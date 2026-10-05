#!/usr/bin/env python3
"""
Project full box scores (team + player distributions) for upcoming games.

    python project.py                       # next unplayed week
    python project.py --season 2026 --week 4
    python project.py --lines lines.csv     # add P(over)/P(under) for prop lines
    python project.py --overrides overrides.csv

Workflow each week:
    python build_nfl_db.py --seasons 2025 2026 --skip ngs_pass ngs_rush ngs_rec player_week
    python project.py --lines lines.csv

overrides.csv (optional) -- news the data feeds don't have yet:
    team,player,action
    GB,Jayden Reed,out           # remove from the game
    DAL,00-0041234,in            # force-include (gsis id or full name)
    NYJ,Tyrod Taylor,starter     # starting QB change

lines.csv (optional):
    player,stat,line
    Josh Allen,pass_yds,245.5
    Bijan Robinson,rush_yds,79.5
    Puka Nacua,rec,6.5
    Derrick Henry,anytime_td,0.5
  stats: pass_yds att cmp pass_td ints rush_yds car rec rec_yds tgt anytime_td

Outputs (projections/):
    <season>_wk<week>_players.csv   one row per player x stat: mean, median, p10..p90
    <season>_wk<week>_teams.csv     team box score + win prob / spread / total from the sim
    <season>_wk<week>_props.csv     if --lines: P(over), P(under), fair American odds
"""

import argparse
import json
import os
import re
import shutil
import sys

import numpy as np
import pandas as pd

import archive
import report
from boxscore import data as D
from boxscore import matchups as M
from boxscore import pipeline as PL
from boxscore.sim import simulate_game
import learned  # noqa: E402
learned.apply("nfl")      # settings adopted by the self-tuning job (learn.py)

PLAYER_STATS = ["att", "cmp", "pass_yds", "pass_td", "ints", "car", "rush_yds",
                "tgt", "rec", "rec_yds", "anytime_td"]
RECENT_GAMES = 3
DEPTH_MAX_RANK = {"QB": 1, "RB": 3, "WR": 5, "TE": 3, "FB": 1}


def kickoff(games):
    """Kickoff as a naive Eastern timestamp (nflverse gametime is ET)."""
    return pd.to_datetime(games.gameday.dt.strftime("%Y-%m-%d") + " " + games.gametime.fillna("13:00"))


def pick_week(games, season, week):
    """Games not yet kicked off. With no week given, the earliest week that still has
    one, so scheduled runs roll to next week once the last game starts."""
    upcoming = games[games.result.isna() & (kickoff(games) > pd.Timestamp.now())]
    if not len(upcoming):
        sys.exit("No upcoming games on the schedule.")
    if season is None:
        season = int(upcoming.season.min())
    if week is None:
        week = int(upcoming[upcoming.season == season].week.min())
    todo = upcoming[(upcoming.season == season) & (upcoming.week == week)]
    return season, week, todo


def load_overrides(path):
    if not path:
        return pd.DataFrame(columns=["team", "player", "action"])
    o = pd.read_csv(path, comment="#", skipinitialspace=True)
    o["action"] = o.action.str.strip().str.lower()
    o["player"] = o.player.str.strip()
    return o


def resolve(o, names):
    """Map override player (gsis id or full name) -> gsis id."""
    if o.player in set(names.player_id):
        return o.player
    hit = names[names.full_name.str.lower() == o.player.lower()]
    if o.team:
        on_team = hit[hit.team == o.team]
        hit = on_team if len(on_team) else hit
    if not len(hit):
        print(f"  ! override: no player named '{o.player}' ({o.team}) -- skipped")
        return None
    return hit.player_id.iloc[0]


ESPN_TEAM = {"WSH": "WAS", "LAR": "LA"}            # ESPN abbreviation -> nflverse


def _norm(n):
    n = re.sub(r"[^a-z ]", "", str(n).lower().replace("-", " "))
    return re.sub(r"\s+(jr|sr|ii|iii|iv|v)$", "", n).strip()


def espn_out(todo):
    """Game-day Out / Doubtful from ESPN for these games: {(team, normalized name): status}. The weekly
    practice report never shows game-day decisions (a Questionable player ruled out at inactives), so
    this catches them. Network trouble just returns {} (the report and overrides still apply)."""
    import requests
    out, H = {}, {"User-Agent": "Mozilla/5.0"}
    want = {(g.away_team, g.home_team) for g in todo.itertuples()}
    for day in sorted({str(d)[:10].replace("-", "") for d in todo.gameday}):
        try:
            sb = requests.get("https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard",
                              params={"dates": day}, headers=H, timeout=20).json()
        except Exception:
            continue
        for e in sb.get("events", []):
            cs = {c["homeAway"]: ESPN_TEAM.get(c["team"]["abbreviation"], c["team"]["abbreviation"])
                  for c in e.get("competitions", [{}])[0].get("competitors", [])}
            if (cs.get("away"), cs.get("home")) not in want:
                continue
            try:
                summ = requests.get("https://site.api.espn.com/apis/site/v2/sports/football/nfl/summary",
                                    params={"event": e["id"]}, headers=H, timeout=20).json()
            except Exception:
                continue
            for t in summ.get("injuries", []):
                team = ESPN_TEAM.get(t.get("team", {}).get("abbreviation"), t.get("team", {}).get("abbreviation"))
                for i in t.get("injuries", []):
                    if i.get("status") in ("Out", "Doubtful"):
                        out[(team, _norm(i.get("athlete", {}).get("displayName")))] = i["status"]
    return out


def future_actives(con, todo, season, week, overrides):
    """Who plays for each team: recent snaps + current depth chart, on the active
    roster, minus injury-report Out/Doubtful, plus manual overrides."""
    pos = D.positions(con)
    snaps = D.game_actives(con, pos).merge(
        pd.read_sql("SELECT game_id, season, week FROM games", con), on="game_id")
    roster = pd.read_sql(f"""SELECT gsis_id AS player_id, team, position, status, full_name
                             FROM rosters WHERE season={season}
                               AND week=(SELECT MAX(week) FROM rosters WHERE season={season})""", con)
    active_roster = roster[roster.status == "ACT"]
    depth = pd.read_sql(f"""SELECT team, gsis_id AS player_id, pos_abb, pos_rank FROM depth_charts
                            WHERE season={season}
                              AND dt=(SELECT MAX(dt) FROM depth_charts WHERE season={season})""", con)
    depth_qbs = depth[depth.pos_abb == "QB"]   # full QB ladder, for replacing an injured starter
    depth = depth[depth.pos_rank <= depth.pos_abb.map(DEPTH_MAX_RANK)]
    inj = pd.read_sql(f"""SELECT team, gsis_id AS player_id, full_name, report_status FROM injuries
                          WHERE season={season} AND week={week}""", con)
    names = roster[["player_id", "team", "full_name"]].dropna()
    game_day = espn_out(todo)                             # late scratches the practice report misses
    if game_day:
        late = names.assign(k=list(zip(names.team, names.full_name.map(_norm))))
        late = late[late.k.isin(game_day.keys())]
        late = late[~late.player_id.isin(inj[inj.report_status.isin(["Out", "Doubtful"])].player_id)]
        inj = pd.concat([inj, pd.DataFrame(dict(team=late.team, player_id=late.player_id, full_name=late.full_name,
                                                report_status=[game_day[k] + " (ESPN game day)" for k in late.k]))])
    out_inj = inj[inj.report_status.astype(str).str.match(r"(Out|Doubtful)")]

    rows, qbs, notes = [], {}, []
    for g in todo.itertuples():
        for team, qb_id in ((g.home_team, g.home_qb_id), (g.away_team, g.away_qb_id)):
            ts = snaps[(snaps.team == team) & ((snaps.season < season) | (snaps.week < week))]
            recent = ts.drop_duplicates("game_id").sort_values(["season", "week"]).game_id.iloc[-RECENT_GAMES:]
            cand = set(ts[ts.game_id.isin(recent)].player_id) | set(depth[depth.team == team].player_id)
            cand &= set(active_roster[active_roster.team == team].player_id)
            dropped = cand & set(out_inj[out_inj.team == team].player_id)
            cand -= dropped
            for pid in dropped:
                r = out_inj[out_inj.player_id == pid].iloc[0]
                notes.append(f"{team}: {r.full_name} {r.report_status} (injury report) -- excluded")
            for o in overrides[overrides.team == team].itertuples():
                pid = resolve(o, names)
                if pid is None:
                    continue
                if o.action == "out":
                    cand.discard(pid)
                elif o.action in ("in", "starter"):
                    cand.add(pid)
                if o.action == "starter":
                    qb_id = pid
                notes.append(f"{team}: override {o.action} {o.player}")
            if pd.isna(qb_id) or qb_id in set(out_inj.player_id):
                dq = depth_qbs[depth_qbs.team == team].sort_values("pos_rank")
                dq = dq[~dq.player_id.isin(out_inj.player_id)]
                if len(dq):
                    new = dq.player_id.iloc[0]
                    name = names.loc[names.player_id == new, "full_name"]
                    notes.append(f"{team}: starting QB -> {name.iloc[0] if len(name) else new} (depth chart)")
                    qb_id = new
                else:
                    notes.append(f"{team}: starting QB is out and no healthy backup on the depth chart "
                                 f"-- add a 'starter' override")
            cand.add(qb_id)
            qbs[(g.game_id, team)] = qb_id
            rows += [(g.game_id, team, pid) for pid in cand]
    return pd.DataFrame(rows, columns=["game_id", "team", "player_id"]), qbs, notes


COUNT_STATS = {"att", "cmp", "pass_td", "ints", "car", "tgt", "rec", "tds"}
DIST_STATS = ["pass_yds", "att", "cmp", "pass_td", "ints", "rush_yds", "car", "tgt", "rec",
              "rec_yds", "tds"]


def compress(x, stat):
    """Compact distribution for pricing any line later.
    Counts: exact probability of each value (offset + pmf). Yards: 1% quantiles."""
    if stat in COUNT_STATS:
        x = np.asarray(x).astype(int)
        lo = int(x.min())
        pmf = np.bincount(x - lo) / len(x)
        return {"o": lo, "p": [round(float(v), 4) for v in pmf]}
    q = np.quantile(x, np.arange(0.005, 1, 0.01))
    return {"q": [round(float(v), 1) for v in q]}


DST_TD_PER_GAME = 0.27   # defense / special-teams TDs per game, both teams (2022-26 play-by-play)


def first_td(h, a, home, away, seed):
    """Chance each listed player scores the game's first TD. In every simulated game each TD
    (both offenses plus defense/special teams at the league rate) is equally likely to come
    first. A team's chance goes to its listed players by their share of its TDs (the sim's
    'unlisted player' bucket keeps anytime-TD odds calibrated but almost never scores first).
    Backtest 2024-26 (618 games): calibrated within ~1 point; top pick scored first 14.1% vs
    15.0% predicted. Returns {team: array over players}."""
    rng = np.random.default_rng(seed + 1)
    n = len(h["points"])
    dst = rng.poisson(DST_TD_PER_GAME, n)
    tot = h["pass_td"] + h["rush_td"] + a["pass_td"] + a["rush_td"] + dst
    inv = np.where(tot > 0, 1.0 / np.maximum(tot, 1), 0.0)
    out = {}
    for res, team in ((h, home), (a, away)):
        team_first = float(((res["pass_td"] + res["rush_td"]) * inv).mean())
        raw = ((res["players"]["rec_td"] + res["players"]["rush_td"]) * inv[:, None]).mean(axis=0)
        out[team] = raw / max(raw.sum(), 1e-12) * team_first
    return out


def summarize(samples):
    q = np.quantile(samples, [.1, .25, .5, .75, .9])
    return dict(mean=samples.mean(), p10=q[0], p25=q[1], median=q[2], p75=q[3], p90=q[4])


def american(p):
    p = min(max(p, 1e-4), 1 - 1e-4)
    return round(-100 * p / (1 - p)) if p >= 0.5 else round(100 * (1 - p) / p)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", type=int)
    ap.add_argument("--week", type=int)
    ap.add_argument("--sims", type=int, default=10000)
    ap.add_argument("--db", default="nfl.db")
    ap.add_argument("--overrides")
    ap.add_argument("--lines")
    ap.add_argument("--out", default="projections")
    ap.add_argument("--games", nargs="+",
                    help="only these games: team codes (e.g. PIT) or game ids")
    ap.add_argument("--played", action="store_true",
                    help="pregame projection of already-played games, compared with the result")
    ap.add_argument("--blind", action="store_true",
                    help="experimental: ignore Vegas lines (market-blind points model); writes to projections/blind")
    ap.add_argument("--archive", action="store_true",
                    help="with --played: store as the week's reconstructed pregame projections")
    args = ap.parse_args()
    if args.blind and args.out == "projections":
        args.out = os.path.join("projections", "blind")

    con = D.connect(args.db)
    games = D.load_games(con)
    if args.played:
        # Pregame projection of games already played: model fit only on games
        # before kickoff, named starters, and the players who suited up.
        if args.season is None or args.week is None:
            sys.exit("--played needs --season and --week (and optionally --games).")
        season, week = args.season, args.week
        todo = games[(games.season == season) & (games.week == week) & games.result.notna()]
    else:
        season, week, todo = pick_week(games, args.season, args.week)
    todo = todo[todo.spread_line.notna() & todo.total_line.notna()]
    if args.games:
        keys = {k.upper() for k in args.games}
        todo = todo[todo.game_id.str.upper().isin(keys) | todo.home_team.isin(keys) | todo.away_team.isin(keys)]
    if not len(todo):
        sys.exit(f"No {'played' if args.played else 'unplayed'} games with Vegas lines for {season} week {week}.")
    print(f"Projecting {season} week {week}: {len(todo)} games, {args.sims:,} sims each"
          + (" (pregame, as of kickoff)" if args.played else ""))

    if args.played:
        fr = PL.Frames(con, market_free=args.blind)
        have = set(fr.df.game_id[fr.df.plays.notna()])
        missing = todo[~todo.game_id.isin(have)]
        for gid in missing.game_id:
            print(f"  {gid}: final score posted but no play-by-play yet -- skipped (rerun later)")
        todo = todo[todo.game_id.isin(have)]
        if not len(todo):
            sys.exit("No played games with play-by-play to project.")
        cutoff = todo.gameday.min()
        train = fr.df.plays.notna() & fr.df.spread_line.notna() & (fr.df.gameday < cutoff) & \
            ~((fr.df.season == fr.df.season.min()) & (fr.df.week < 6))
    else:
        if args.overrides is None:
            weekly = os.path.join("overrides", f"{season}_wk{week:02d}.csv")
            if os.path.exists(weekly):
                args.overrides = weekly
                print(f"  using overrides from {weekly}")
        fa, qbs, notes = future_actives(con, todo, season, week, load_overrides(args.overrides))
        for n in notes:
            print("  " + n)
        fr = PL.Frames(con, future=todo.to_dict("records"), future_actives=fa, future_qbs=qbs,
                       market_free=args.blind)
        train = fr.df.plays.notna() & fr.df.spread_line.notna() & \
            ~((fr.df.season == fr.df.season.min()) & (fr.df.week < 6))
    model, params = PL.fit(fr, train)

    prow, trow, box, dists, keyrows = [], [], {}, {}, []
    for g in todo.itertuples():
        hi = PL.team_input(fr, g.game_id, g.home_team)
        ai = PL.team_input(fr, g.game_id, g.away_team)
        h, a = simulate_game(model, params, hi, ai, n=args.sims, seed=abs(hash(g.game_id)) % 2**32)
        margin = h["points"] - a["points"]
        first = first_td(h, a, hi.team, ai.team, abs(hash(g.game_id)) % 2**32)
        for res, t, opp, sign in ((h, hi, g.away_team, 1), (a, ai, g.home_team, -1)):
            trow.append(dict(
                game_id=g.game_id, team=t.team, opp=opp, home=int(sign == 1),
                vegas_implied=t.row["implied"], points=res["points"].mean(),
                win_prob=np.mean(sign * margin > 0) + 0.5 * np.mean(margin == 0),
                plays=res["plays"].mean(), pass_att=res["att"].mean(), cmp=res["cmp"].mean(),
                pass_yds=res["pass_yds"].mean(), sacks=res["sacks"].mean(), ints=res["ints"].mean(),
                rush_att=res["rush_att"].mean(), rush_yds=res["rush_yds"].mean(),
                pass_td=res["pass_td"].mean(), rush_td=res["rush_td"].mean(), fg=res["fg"].mean(),
            ))
            pl = res["players"]
            for j, p in enumerate(t.players):
                s = dict(tgt=pl["tgt"][:, j], rec=pl["rec"][:, j], rec_yds=pl["rec_yds"][:, j],
                         car=pl["car"][:, j], rush_yds=pl["rush_yds"][:, j],
                         anytime_td=((pl["rec_td"][:, j] + pl["rush_td"][:, j]) > 0).astype(float))
                if j == res["qi"]:
                    s.update(res["qb_line"])
                if max(s["tgt"].mean(), s["car"].mean(), s.get("att", np.zeros(1)).mean()) < 0.5:
                    continue
                box[(t.team, p.name)] = s
                keyrows.append((g.game_id, t.team, p.player_id, p.name))
                s_all = dict(s, tds=pl["rec_td"][:, j] + pl["rush_td"][:, j])
                dists[f"{g.game_id}|{t.team}|{p.name}"] = {
                    stat: compress(s_all[stat], stat) for stat in DIST_STATS if stat in s_all}
                for stat in PLAYER_STATS:
                    if stat in s:
                        prow.append(dict(game_id=g.game_id, team=t.team, opp=opp, player=p.name,
                                         player_id=p.player_id, pos=p.pos, stat=stat,
                                         **summarize(s[stat])))
                q = first[t.team][j]
                prow.append(dict(game_id=g.game_id, team=t.team, opp=opp, player=p.name, player_id=p.player_id,
                                 pos=p.pos, stat="first_td", mean=q, p10=q, p25=q, median=q, p75=q, p90=q))

    os.makedirs(args.out, exist_ok=True)
    stem = os.path.join(args.out, f"{season}_wk{week:02d}")
    players = pd.DataFrame(prow).round(3)
    teams = pd.DataFrame(trow).round(2)
    if args.played:
        stem += ("_" + "_".join(sorted(todo.game_id.str.split("_", n=2).str[2])) if args.games else "") + "_pregame"
        players, teams = add_actuals(players, teams, fr)
        print_actuals(players, teams)
    players.to_csv(f"{stem}_players.csv", index=False)
    teams.to_csv(f"{stem}_teams.csv", index=False)
    with open(f"{stem}_dist.json", "w") as f:
        json.dump(dists, f, separators=(",", ":"))
    # As of kickoff for played games; otherwise as of now.
    when = (todo.gameday.min() - pd.Timedelta(hours=1)) if args.played else pd.Timestamp.now()
    with open(f"{stem}_matchups.json", "w") as f:
        json.dump(matchup_payload(con, fr, todo, keyrows, season, when), f, separators=(",", ":"))
    print_box(teams, players)
    print(f"\nWrote {stem}_players.csv and {stem}_teams.csv")

    if args.lines:
        props = price_lines(pd.read_csv(args.lines, skipinitialspace=True), box)
        props.to_csv(f"{stem}_props.csv", index=False)
        print(f"\n=== PROPS ===\n{props.to_string(index=False)}\nWrote {stem}_props.csv")

    live = not args.played and args.out in ("projections", os.path.join("projections", "blind"))
    nav = [("This week", "index.html", True), ("Results", "results.html")] if live and not args.blind else None
    if args.blind:
        nav = [("This week", "../index.html"), ("Results", "../results.html"), ("Market-blind", "#", True)]
    page = report.build(stem, sims=args.sims, db=args.db, nav=nav,
                        subtitle=("Experimental market-blind build: no Vegas lines. The 'Vegas' figures shown are "
                                  "this model's own points forecast." if args.blind else None))
    print(f"Wrote {page}  (open in a browser)")
    if live:
        shutil.copyfile(page, os.path.join(args.out, "index.html"))
        if not args.blind:
            shutil.copyfile(page, os.path.join(args.out, "latest.html"))
    if live or (args.played and args.archive):
        wk_page = archive.update(stem, season, week, todo.game_id.tolist(),
                                 source="live" if live else "reconstructed", sims=args.sims, db=args.db,
                                 base=archive.BLIND_WEEKS_DIR if args.blind else archive.WEEKS_DIR)
        print(f"Archived to {wk_page}")


def matchup_payload(con, fr, todo, keyrows, season, when):
    """Depth labels, mismatch tags and defense-vs-position tables for the page."""
    keys = pd.DataFrame(keyrows, columns=["game_id", "team", "player_id", "name"])
    ctx = M.player_context(fr, con, keys[["game_id", "team", "player_id"]]).merge(
        keys, on=["game_id", "team", "player_id"])
    players = {f"{r.game_id}|{r.team}|{r.name}": dict(label=r.label, align=r.align, tags=r.tags)
               for r in ctx.itertuples()}
    labels = M.depth_labels(con, fr.tp[["game_id", "team", "gameday"]])
    teams = sorted(fr.tp.team.dropna().unique())
    now = M.defense_now(fr, labels, season, when, teams)
    faces = {**dict(zip(todo.home_team, todo.away_team)), **dict(zip(todo.away_team, todo.home_team))}
    # Red zone: what each defense allows by position, and each player's usage.
    rz = dict(
        defense={"season": M.redzone_defense(fr, [season], when, faces),
                 "two": M.redzone_defense(fr, [season - 1, season], when, faces)},
        usage={"season": M.redzone_usage(fr, keyrows, [season], when),
               "two": M.redzone_usage(fr, keyrows, [season - 1, season], when)},
        labels={"season": str(season), "two": f"{season - 1}–{str(season)[2:]}"})
    return dict(players=players, dvp=M.dvp_summary(now, faces), rz=rz)


def add_actuals(players, teams, fr):
    pg = fr.pg.set_index(["game_id", "player_id"])
    def actual(r):
        key = (r.game_id, r.player_id)
        if key not in pg.index:
            return 0.0
        a = pg.loc[key]
        return float(a.rec_td + a.rush_td > 0) if r.stat == "anytime_td" else float(a[r.stat])
    players["actual"] = [actual(r) for r in players.itertuples()]
    act = fr.df.set_index(["game_id", "team"])
    cols = {"points": "points", "plays": "plays", "pass_att": "att", "cmp": "cmp", "pass_yds": "pass_yds",
            "sacks": "sacks", "ints": "ints", "rush_att": "rush_att", "rush_yds": "rush_yds",
            "pass_td": "pass_td", "rush_td": "rush_td", "fg": "fg"}
    for c, src in cols.items():
        teams[f"actual_{c}"] = [float(act.loc[(g, t), src]) for g, t in zip(teams.game_id, teams.team)]
    return players, teams


def print_actuals(players, teams):
    print("\n=== PREGAME PROJECTION vs RESULT ===")
    for t in teams.itertuples():
        print(f"{t.team:4} points {t.points:5.1f} -> {t.actual_points:3.0f}   pass yds {t.pass_yds:4.0f} -> "
              f"{t.actual_pass_yds:4.0f}   rush yds {t.rush_yds:4.0f} -> {t.actual_rush_yds:4.0f}   win {t.win_prob:.0%}")
    key = players[players.stat.isin(["pass_yds", "rush_yds", "rec_yds", "rec", "anytime_td"])]
    for r in key.itertuples():
        if r.stat == "anytime_td":
            continue
        if r.median < (100 if r.stat == "pass_yds" else 12 if r.stat.endswith("yds") else 1.5):
            continue
        inside = "inside" if r.p10 <= r.actual <= r.p90 else "OUTSIDE"
        print(f"  {r.team:4}{r.player[:22]:23}{r.stat:9} median {r.median:6.1f}  10-90 {r.p10:5.0f}-{r.p90:<5.0f}"
              f" actual {r.actual:5.0f}  {inside}")


def price_lines(lines, box):
    out = []
    by_name = {name.lower(): (team, name) for team, name in box}
    for r in lines.itertuples():
        key = by_name.get(str(r.player).strip().lower())
        stat = r.stat.strip()
        if key is None or stat not in box[key]:
            out.append(dict(player=r.player, stat=stat, line=r.line, note="player/stat not projected"))
            continue
        s = box[key][stat]
        p_over, p_under = np.mean(s > r.line), np.mean(s < r.line)
        out.append(dict(player=key[1], team=key[0], stat=stat, line=r.line,
                        proj_median=np.median(s), proj_mean=round(s.mean(), 2),
                        p_over=round(p_over, 3), p_under=round(p_under, 3),
                        fair_over=american(p_over / max(p_over + p_under, 1e-9)),
                        fair_under=american(p_under / max(p_over + p_under, 1e-9))))
    return pd.DataFrame(out)


def print_box(teams, players):
    wide = players.pivot_table(index=["game_id", "team", "player", "pos"], columns="stat",
                               values="median", aggfunc="first").reset_index()
    mean_td = players[players.stat == "anytime_td"].set_index(["team", "player"])["mean"]
    for gid, tg in teams.groupby("game_id", sort=False):
        print("\n" + "=" * 78)
        for t in tg.itertuples():
            print(f"{t.team:4} {t.points:5.1f} pts (Vegas {t.vegas_implied:4.1f})  win {t.win_prob:5.1%}  "
                  f"plays {t.plays:4.1f}  pass {t.cmp:.0f}/{t.pass_att:.0f} {t.pass_yds:.0f}y  "
                  f"rush {t.rush_att:.0f}-{t.rush_yds:.0f}y")
        print("-" * 78)
        print(f"{'':4}{'player':24}{'pos':4}{'C/Att':>8}{'PaYd':>6}{'Car':>5}{'RuYd':>6}{'Rec':>5}"
              f"{'ReYd':>6}{'TD%':>6}   (medians)")
        for t in tg.team:
            w = wide[(wide.game_id == gid) & (wide.team == t)].copy()
            w["order"] = w.pos.map({"QB": 0, "RB": 1, "WR": 2, "TE": 3})
            w["vol"] = w.get("tgt", 0).fillna(0) + w.get("car", 0).fillna(0)
            for r in w.sort_values(["order", "vol"], ascending=[True, False]).itertuples():
                ca = f"{r.cmp:.0f}/{r.att:.0f}" if not pd.isna(getattr(r, "att", np.nan)) else ""
                py = f"{r.pass_yds:.0f}" if ca else ""
                td = mean_td.get((t, r.player), np.nan)
                print(f"{t:4}{r.player[:23]:24}{r.pos:4}{ca:>8}{py:>6}{r.car:5.0f}{r.rush_yds:6.0f}"
                      f"{r.rec:5.0f}{r.rec_yds:6.0f}{td:6.0%}")


if __name__ == "__main__":
    main()
