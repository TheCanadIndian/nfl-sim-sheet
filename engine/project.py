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
import sgp  # noqa: E402
from boxscore import defout as DO
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


ESPN_BAD = {"out", "doubtful", "injured reserve", "suspension", "physically unable to perform", "pup", "injured"}


def espn_rosters(teams):
    """({team: {normalized name: [status, injury designations...]}}, {team: QB1 normalized name}) from ESPN's live team
    rosters and depth charts, for the teams playing. The weekly roster file lags moves by days (2026 wk 6: Marquez
    Valdes-Scantling, released by LAC, still 'ACT'; Caleb Williams back from injury, still 'INA', so Tyson Bagent was
    projected to start). Teams whose roster doesn't come back complete (under 40 players) are skipped."""
    import requests
    H, out, qb1 = {"User-Agent": "Mozilla/5.0"}, {}, {}
    try:
        T = requests.get("https://site.api.espn.com/apis/site/v2/sports/football/nfl/teams", headers=H, timeout=20).json()
        T = T["sports"][0]["leagues"][0]["teams"]
    except Exception:
        return {}, {}
    for t in T:
        ab = ESPN_TEAM.get(t["team"]["abbreviation"], t["team"]["abbreviation"])
        if ab not in teams:
            continue
        try:
            r = requests.get(f"https://site.api.espn.com/apis/site/v2/sports/football/nfl/teams/{t['team']['id']}/roster", headers=H, timeout=20).json()
        except Exception:
            continue
        names = {_norm(a.get("fullName")): [str((a.get("status") or {}).get("type") or "")] +
                 [str(i.get("status") or "") for i in a.get("injuries", [])]
                 for g in r.get("athletes", []) for a in g.get("items", [])}
        if len(names) >= 40:
            out[ab] = names
        try:
            dc = requests.get(f"https://site.api.espn.com/apis/site/v2/sports/football/nfl/teams/{t['team']['id']}/depthcharts", headers=H, timeout=20).json()
            for d in dc.get("depthchart") or dc.get("items") or []:
                q = (d.get("positions") or {}).get("qb")
                if q and q.get("athletes"):
                    a0 = q["athletes"][0]
                    qb1[ab] = _norm(a0.get("displayName") or a0.get("fullName")); break
        except Exception:
            pass
    return out, qb1


_ESPN_QB = {}        # team -> starting QB's full name from ESPN's official game roster (filled by espn_out)


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
            # the official inactives (posted ~90 min before kickoff): ESPN's game roster marks them didNotPlay.
            # They include late / unlisted scratches the injury feeds miss (e.g. DJ Moore, week 5 2026).
            for c in e.get("competitions", [{}])[0].get("competitors", []):
                team = ESPN_TEAM.get(c["team"]["abbreviation"], c["team"]["abbreviation"])
                try:
                    ro = requests.get(f"https://sports.core.api.espn.com/v2/sports/football/leagues/nfl/events/{e['id']}/competitions/{e['id']}"
                                      f"/competitors/{c['team']['id']}/roster", headers=H, timeout=20).json()
                except Exception:
                    continue
                # the official starting QB (game roster 'starter', position 8 = QB): beats the schedule and depth chart
                # (2026 wk 6: Caleb Williams back from injury, Tanner McKee for Jalen Hurts)
                sq = [x for x in ro.get("entries", []) if x.get("starter") and not x.get("didNotPlay")
                      and str((x.get("position") or {}).get("$ref", "")).split("/positions/")[-1].split("?")[0] == "8"]
                if len(sq) == 1:
                    nm = _espn_name(sq[0])
                    if nm:
                        _ESPN_QB[team] = nm
                dnp = [x for x in ro.get("entries", []) if x.get("didNotPlay")]
                if not 1 <= len(dnp) <= 15:                       # not posted yet (or not the inactives list)
                    continue
                for x in dnp:
                    name = _espn_name(x)
                    if name:
                        out.setdefault((team, _norm(name)), "Out")
    return out


_ESPN_NAMES = {}


def _espn_name(entry):
    """Full name for an ESPN game-roster entry (which only carries the last name): our rosters' espn_id, else ESPN."""
    import requests
    pid = str(entry.get("playerId"))
    if not _ESPN_NAMES:
        try:
            import sqlite3
            con = sqlite3.connect(os.path.join(os.path.dirname(os.path.abspath(__file__)), "nfl.db"))
            _ESPN_NAMES.update({str(i): n for i, n in con.execute("SELECT espn_id, full_name FROM rosters WHERE espn_id IS NOT NULL")})
        except Exception:
            _ESPN_NAMES[""] = ""
    if pid in _ESPN_NAMES:
        return _ESPN_NAMES[pid]
    try:
        a = requests.get(entry["athlete"]["$ref"], headers={"User-Agent": "Mozilla/5.0"}, timeout=20).json()
        return a.get("fullName") or a.get("displayName")
    except Exception:
        return None


def espn_lines(todo):
    """Current spread / total from ESPN's scoreboard for upcoming games: {game_id: (spread_line, total_line)}
    in nflverse convention (spread_line > 0 = home favored; ESPN's spread is the negative of that).
    The nflverse schedule only refreshes lines a few times a day; this keeps re-runs on the live number."""
    import requests
    out, H = {}, {"User-Agent": "Mozilla/5.0"}
    key = {(g.away_team, g.home_team): g.game_id for g in todo.itertuples()}
    for day in sorted({str(d)[:10].replace("-", "") for d in todo.gameday}):
        try:
            sb = requests.get("https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard",
                              params={"dates": day}, headers=H, timeout=20).json()
        except Exception:
            continue
        for e in sb.get("events", []):
            c = e.get("competitions", [{}])[0]
            cs = {x["homeAway"]: ESPN_TEAM.get(x["team"]["abbreviation"], x["team"]["abbreviation"]) for x in c.get("competitors", [])}
            gid = key.get((cs.get("away"), cs.get("home")))
            o = (c.get("odds") or [{}])[0]
            if gid and o.get("spread") is not None and o.get("overUnder") is not None:
                out[gid] = (-float(o["spread"]), float(o["overUnder"]))
    return out


def future_actives(con, todo, season, week, overrides):
    """Who plays for each team: recent snaps + current depth chart, on the active
    roster, minus injury-report Out/Doubtful, plus manual overrides."""
    pos = D.positions(con)
    snaps = D.game_actives(con, pos).merge(
        pd.read_sql("SELECT game_id, season, week FROM games", con), on="game_id")
    # each team's latest roster week: weekly rosters skip a team on its bye, so a league-wide MAX(week)
    # left a team coming off a bye with no active players (Carolina, week 6 2026)
    roster = pd.read_sql(f"""SELECT r.gsis_id AS player_id, r.team, r.position, r.status, r.full_name
                             FROM rosters r JOIN (SELECT team, MAX(week) AS wk FROM rosters WHERE season={season} GROUP BY team) m
                               ON r.team = m.team AND r.week = m.wk
                             WHERE r.season={season}""", con)
    active_roster = roster[roster.status == "ACT"]
    back_ids = set()
    live, espn_qb1 = espn_rosters(set(todo.home_team) | set(todo.away_team))
    healthy = lambda t, n: (lambda v: v is not None and not any(x.lower() in ESPN_BAD for x in v))(live.get(t, {}).get(_norm(n)))
    if live:
        gone = active_roster[active_roster.team.isin(live) & ~pd.Series([_norm(n) in live.get(t, {}) for t, n in zip(active_roster.team, active_roster.full_name)],
                                                                         index=active_roster.index)]
        if len(gone):
            print(f"  not on ESPN's live roster (released / traded / moved), dropped: " + ", ".join(f"{n} ({t})" for t, n in zip(gone.team, gone.full_name)))
        active_roster = active_roster.drop(gone.index)
        # back from injury: inactive / reserve / PUP in the weekly file, but active and healthy on ESPN now
        # only injury-type statuses (INA, RES), only players who actually played for that team this season or last
        # (ESPN lists practice-squad and reserve players as plain 'active', so ESPN alone can't decide)
        played = set(zip(snaps[snaps.season >= season - 1].team, snaps[snaps.season >= season - 1].player_id))
        back = roster[roster.status.isin(["INA", "RES"]) & roster.team.isin(live) & ~roster.player_id.isin(active_roster.player_id)]
        back = back[[(t, p_) in played and healthy(t, n) for t, p_, n in zip(back.team, back.player_id, back.full_name)]]
        if len(back):
            print(f"  back from injury / reserve (active on ESPN now): " + ", ".join(f"{n} ({t})" for t, n in zip(back.team, back.full_name)))
            active_roster = pd.concat([active_roster, back.assign(status="ACT")])
            back_ids = set(back.player_id)            # in the lineup even when the last games and the weekly depth chart predate the return
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
    # ESPN's live roster also marks players Out / Doubtful / Injured Reserve / suspended / PUP: they're out even when the
    # weekly roster and injury files lag (2026 wk 6: Jalen Hurts on IR on ESPN, 'ACT' in the weekly roster)
    if live:
        eb = active_roster[[t in live and not healthy(t, n) and _norm(n) in live[t] for t, n in zip(active_roster.team, active_roster.full_name)]]
        eb = eb[~eb.player_id.isin(inj[inj.report_status.astype(str).str.match(r"(Out|Doubtful)")].player_id)]
        if len(eb):
            st = [next((x for x in live[t][_norm(n)][1:] + live[t][_norm(n)][:1] if x.lower() in ESPN_BAD), "Out") for t, n in zip(eb.team, eb.full_name)]
            inj = pd.concat([inj, pd.DataFrame(dict(team=eb.team, player_id=eb.player_id, full_name=eb.full_name,
                                                    report_status=[f"Out ({x}, ESPN)" for x in st]))])
    out_inj = inj[inj.report_status.astype(str).str.match(r"(Out|Doubtful)")]

    rows, qbs, notes = [], {}, []
    # the schedule file's QB can lag a change (2026 wk 6: it still listed Drew Lock for SEA after Sam Darnold returned
    # and started weeks 3-5). When the latest depth chart's QB1 and the QB who started the team's last game agree on
    # someone else, use them.
    qb1 = depth_qbs.sort_values("pos_rank").drop_duplicates("team").set_index("team").player_id.to_dict()
    pq = pd.read_sql(f"""SELECT season, week, posteam AS team, passer_player_id AS player_id, COUNT(*) AS n FROM plays
                         WHERE qb_dropback = 1 AND passer_player_id IS NOT NULL AND season >= {season - 1}
                         GROUP BY 1, 2, 3, 4""", con)
    pq = pq[(pq.season < season) | (pq.week < week)].sort_values(["season", "week", "n"])
    last_starter = pq.groupby("team").tail(1).set_index("team").player_id.to_dict()
    for g in todo.itertuples():
        for team, qb_id in ((g.home_team, g.home_qb_id), (g.away_team, g.away_qb_id)):
            d1, ls = qb1.get(team), last_starter.get(team)
            if team in _ESPN_QB:                                    # ESPN's official starter, posted ~90 min before kickoff
                m = roster[(roster.team == team) & (roster.full_name.map(_norm) == _norm(_ESPN_QB[team]))]
                if len(m) and m.player_id.iloc[0] != qb_id:
                    notes.append(f"{team}: starting QB -> {m.full_name.iloc[0]} (ESPN official starter; schedule had {qb_id})")
                    qb_id = m.player_id.iloc[0]
                    if qb_id not in set(active_roster.player_id):
                        active_roster = pd.concat([active_roster, m.assign(status="ACT")])
            if d1 is not None and d1 == ls and d1 != qb_id and d1 not in set(out_inj.player_id):
                nm = names.loc[names.player_id == d1, "full_name"]
                notes.append(f"{team}: starting QB -> {nm.iloc[0] if len(nm) else d1} (depth chart QB1 and last game's starter; schedule had {qb_id})")
                qb_id = d1
            ts = snaps[(snaps.team == team) & ((snaps.season < season) | (snaps.week < week))]
            recent = ts.drop_duplicates("game_id").sort_values(["season", "week"]).game_id.iloc[-RECENT_GAMES:]
            cand = set(ts[ts.game_id.isin(recent)].player_id) | set(depth[depth.team == team].player_id) | (back_ids & set(roster[roster.team == team].player_id))
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
# Workhorse RBs: our rushing-yard projections ran low for RBs projected 40+ yds (walk-forward backtest, late 2025 and 2026:
# 52.3% landed in the top half of our range; +3% fixes it without hurting early 2025). Scale their rushing-yard draws.
RB_BIG, RB_BOOST = 40.0, 1.03


def rb_boost(pos, s):
    if pos == "RB" and np.median(s["rush_yds"]) >= RB_BIG:
        s["rush_yds"] = np.round(s["rush_yds"] * RB_BOOST)


# Season-to-date bias fix (research/bias_correction_test.py, 2026 wk 2-5 walk-forward on 7,060 Kalshi rungs: our receiving
# yards / receptions / passing yards ran 4-5 pts low, rushing fine). Each run scales those draws by actual / projected
# so far this season (raw projections: earlier weeks' applied ratios are divided back out), shrunk toward 1 with
# BIAS_K units; receptions by randomized rounding (3 catches x 1.04 -> 4 about 4% x 3 of the time). Log-loss -0.0019 in
# both halves vs raw, no stat worse. Rushing yards untouched.
BIAS_STATS = {"rec": "receptions", "rec_yds": "receiving_yards", "pass_yds": "passing_yards"}
BIAS_K = 3000.0


def season_bias(con, season, week, wdir):
    """{stat: ratio} from the frozen pregame projections of earlier weeks this season vs their box scores."""
    import glob as _g
    pw = pd.read_sql("SELECT player_id, week, receptions, receiving_yards, passing_yards FROM player_week WHERE season = ?",
                     con, params=(int(season),))
    num = {k: 0.0 for k in BIAS_STATS}; den = {k: 0.0 for k in BIAS_STATS}
    for f in sorted(_g.glob(os.path.join(wdir, f"{season}_wk??_players.csv"))):
        w = int(os.path.basename(f)[7:9])
        if w >= int(week):
            continue
        bf = f.replace("_players.csv", "_bias.json")
        used = json.load(open(bf)) if os.path.exists(bf) else {}
        P = pd.read_csv(f, usecols=["player_id", "stat", "mean"])
        a = pw[pw.week == w]
        for k, col in BIAS_STATS.items():
            x = P[P.stat == k].merge(a[["player_id", col]], on="player_id")
            num[k] += x[col].astype(float).sum()
            den[k] += x["mean"].sum() / used.get(k, 1.0)
    return {k: round((num[k] + BIAS_K) / (den[k] + BIAS_K), 4) if den[k] else 1.0 for k in BIAS_STATS}


def apply_bias(res, R, rng):
    pl = res["players"]
    if R.get("rec_yds", 1) != 1:
        pl["rec_yds"] = np.round(pl["rec_yds"] * R["rec_yds"])
    if R.get("rec", 1) != 1:
        pl["rec"] = np.minimum(np.floor(pl["rec"] * R["rec"] + rng.random(pl["rec"].shape)), pl["tgt"])
    q = res.get("qb_line") or {}
    if "pass_yds" in q and R.get("pass_yds", 1) != 1:
        q["pass_yds"] = np.round(q["pass_yds"] * R["pass_yds"])


# RB target split (research/rb_types.py, 1,375 RB games 2025-26): we overstated how different backs are as receivers.
# Early-down backs (<15% of touches targets) beat our target / catch projections (x1.21 / x1.16, 64% above our median),
# receiving backs (30%+) fell short (x0.84 / x0.89, 40%); rushing was right. Each RB's targets, catches and receiving
# yards are scaled by exp(RB_TGT_B * (share - RB_TGT_C)); the 2025-only fit (-0.5) improved 2026 out of sample in 7 of 9
# cells, the 2025-26 fit (-0.9) overcorrected 2025, so the midpoint: about x1.10 at an 8% share, x0.88 at 40%.
RB_TGT_B, RB_TGT_C = -0.7, 0.21


def rb_targets(res, players, rng):
    pl = res["players"]
    for j, p in enumerate(players):
        if p.pos != "RB":
            continue
        t, c = pl["tgt"][:, j], pl["car"][:, j]
        if t.mean() + c.mean() < 1:
            continue
        f = float(np.clip(np.exp(RB_TGT_B * (t.mean() / (t.mean() + c.mean()) - RB_TGT_C)), .8, 1.25))
        u = rng.random(t.shape)
        pl["tgt"][:, j] = np.floor(t * f + u)
        pl["rec"][:, j] = np.minimum(np.floor(pl["rec"][:, j] * f + u), pl["tgt"][:, j])
        pl["rec_yds"][:, j] = np.round(pl["rec_yds"][:, j] * f)


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


def pack(x, n=5000):
    """First n simulations of an array as small ints, base64 (the page's build-your-own parlay slip reads these)."""
    import base64
    a = np.rint(np.asarray(x, float)[:n]).astype(int)
    if a.min() >= 0 and a.max() <= 255:
        return {"t": "u8", "b": base64.b64encode(a.astype(np.uint8).tobytes()).decode()}
    return {"t": "i16", "b": base64.b64encode(np.clip(a, -32768, 32767).astype("<i2").tobytes()).decode()}


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
    ap.add_argument("--sims", type=int, default=50000)   # 50k: steadier first-TD, deep alt-line and same-game-parlay chances (10k is fine for medians)
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
    if not args.played and len(todo):
        todo = todo.copy()
        live = espn_lines(todo)                           # use the current line, not the schedule file's
        for gid, (sl, tl) in live.items():
            row = todo.game_id == gid
            old = todo.loc[row, ["spread_line", "total_line"]].iloc[0]
            if abs((old.spread_line if pd.notna(old.spread_line) else 99) - sl) >= .5 or abs((old.total_line if pd.notna(old.total_line) else 99) - tl) >= .5:
                print(f"  {gid}: line now {sl:+.1f} / {tl:.1f} (ESPN; schedule file had {old.spread_line} / {old.total_line})")
            todo.loc[row, ["spread_line", "total_line"]] = [sl, tl]
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
    sgps, draws, boxtd = {}, {}, {}
    try:
        BIAS = season_bias(con, season, week, os.path.join(args.out, "weeks"))
    except Exception as e:                                       # never block a projection run
        print(f"  bias fix skipped ({type(e).__name__}: {e})"); BIAS = {}
    print(f"  season-to-date bias fix (actual / projected): {BIAS}")
    brng = np.random.default_rng(int(week) * 7919)
    for g in todo.itertuples():
        hi = PL.team_input(fr, g.game_id, g.home_team)
        ai = PL.team_input(fr, g.game_id, g.away_team)
        h, a = simulate_game(model, params, hi, ai, n=args.sims, seed=abs(hash(g.game_id)) % 2**32)
        for res_, t_ in ((h, hi), (a, ai)):
            rb_targets(res_, t_.players, brng)
            apply_bias(res_, BIAS, brng)
        margin = h["points"] - a["points"]
        first = first_td(h, a, hi.team, ai.team, abs(hash(g.game_id)) % 2**32)
        if not args.played:                                     # same-game parlays by game script (sgp.py), both models
            try:
                sgps[g.game_id] = sgp.game_parlays(g.game_id, h, a, hi, ai, g)
            except Exception as e:
                print(f"  {g.game_id}: parlays skipped ({type(e).__name__}: {e})")
        for res, t, opp, sign in ((h, hi, g.away_team, 1), (a, ai, g.home_team, -1)):
            trow.append(dict(
                game_id=g.game_id, team=t.team, opp=opp, home=int(sign == 1),
                vegas_implied=t.row["implied"], points=res["points"].mean(),
                win_prob=np.mean(sign * margin > 0) + 0.5 * np.mean(margin == 0),
                spread_line=float(g.spread_line), total_line=float(g.total_line),     # home margin line, game total line
                p_cover=float(np.mean(sign * margin > sign * g.spread_line)), p_push=float(np.mean(margin == g.spread_line)),
                p_over=float(np.mean(h["points"] + a["points"] > g.total_line)), p_under=float(np.mean(h["points"] + a["points"] < g.total_line)),
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
                rb_boost(p.pos, s)
                box[(t.team, p.name)] = s
                keyrows.append((g.game_id, t.team, p.player_id, p.name))
                s_all = dict(s, tds=pl["rec_td"][:, j] + pl["rush_td"][:, j])
                boxtd[f"{g.game_id}|{t.team}|{p.name}"] = [round(float(pl["rush_td"][:, j].mean()), 3), round(float(pl["rec_td"][:, j].mean()), 3)]
                if not args.played and not args.blind:           # saved simulations for the build-your-own slip
                    draws.setdefault(g.game_id, {"n": 5000, "p": {}, "g": {"margin": pack(margin), "total": pack(h["points"] + a["points"])}})["p"][
                        f"{g.game_id}|{t.team}|{p.name}"] = {st: pack(s_all[st]) for st in DIST_STATS if st in s_all}
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
        players, teams = add_actuals(players, teams, fr, con)
        print_actuals(players, teams)
    players.to_csv(f"{stem}_players.csv", index=False)
    json.dump(boxtd, open(f"{stem}_box.json", "w"), separators=(",", ":"))     # mean rushing / receiving TDs (projected box score)
    json.dump(BIAS, open(f"{stem}_bias.json", "w"))                 # ratios applied this run (later weeks divide them back out)
    teams.to_csv(f"{stem}_teams.csv", index=False)
    with open(f"{stem}_dist.json", "w") as f:
        json.dump(dists, f, separators=(",", ":"))
    if draws:
        ddir = os.path.join(args.out, "draws")
        os.makedirs(ddir, exist_ok=True)
        for gid, dd in draws.items():
            with open(os.path.join(ddir, f"{gid}.json"), "w") as f:
                json.dump(dd, f, separators=(",", ":"))
    if sgps:
        gl = [dict(game_id=r.game_id, gameday=str(r.gameday)[:10], gametime=str(r.gametime or ""), weekday=str(r.weekday),
                   away_team=r.away_team, home_team=r.home_team) for r in todo.itertuples()]
        with open(f"{stem}_sgp.json", "w") as f:
            json.dump(dict(games=sgps, cross=sgp.cross_game(gl, sgps)), f, separators=(",", ":"))
    # As of kickoff for played games; otherwise as of now.
    when = (todo.gameday.min() - pd.Timedelta(hours=1)) if args.played else pd.Timestamp.now()
    payload = matchup_payload(con, fr, todo, keyrows, season, when)
    if not args.played:
        add_defout(payload, con, todo, season, week, players)
        try:
            payload["spot"] = spotlight(con, fr, todo, season, week, keyrows, players, payload)
        except Exception as e:
            print(f"  game summaries skipped ({type(e).__name__}: {e})")
    with open(f"{stem}_matchups.json", "w") as f:
        json.dump(payload, f, separators=(",", ":"))
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
        teams={"season": M.redzone_teams(fr, [season], when),
               "two": M.redzone_teams(fr, [season - 1, season], when)},
        labels={"season": str(season), "two": f"{season - 1}–{str(season)[2:]}"})
    return dict(players=players, dvp=M.dvp_summary(now, faces), rz=rz)


NICK = {"ARI": "Cardinals", "ATL": "Falcons", "BAL": "Ravens", "BUF": "Bills", "CAR": "Panthers", "CHI": "Bears", "CIN": "Bengals",
        "CLE": "Browns", "DAL": "Cowboys", "DEN": "Broncos", "DET": "Lions", "GB": "Packers", "HOU": "Texans", "IND": "Colts",
        "JAX": "Jaguars", "KC": "Chiefs", "LA": "Rams", "LAC": "Chargers", "LV": "Raiders", "MIA": "Dolphins", "MIN": "Vikings",
        "NE": "Patriots", "NO": "Saints", "NYG": "Giants", "NYJ": "Jets", "PHI": "Eagles", "PIT": "Steelers", "SEA": "Seahawks",
        "SF": "49ers", "TB": "Buccaneers", "TEN": "Titans", "WAS": "Commanders"}
ORD = lambda n: {1: "most", 2: "2nd-most", 3: "3rd-most"}.get(n, f"{n}th-most")


def spotlight(con, fr, todo, season, week, keyrows, players, payload):
    """Per game: the players worth a look, all positions, tiered 1-3 with short reasons (usage, snap jumps,
    red-zone work, vacated volume from Out/Doubtful teammates, what the defense allows, missing defenders).
    Display only -- nothing here changes a projection."""
    pg = fr.pg[(fr.pg.season == season) & (fr.pg.week < week)].copy()
    if pg.empty:
        return {}
    team_g = pg.groupby(["team", "game_id"])[["tgt", "car", "att"]].sum().reset_index()
    team_tot = team_g.groupby("team")[["tgt", "car", "att"]].sum()
    team_n = team_g.groupby("team").size()
    ps = pg.groupby(["team", "player_id"]).agg(gp=("game_id", "nunique"), tgt=("tgt", "sum"), car=("car", "sum"), att=("att", "sum"),
                                               pass_yds=("pass_yds", "sum"), rush_yds=("rush_yds", "sum"), rec_yds=("rec_yds", "sum"),
                                               rz_tgt=("rz_tgt", "sum"), rz_car=("rz_car", "sum"), name=("name", "last")).reset_index()
    ps["tgt_sh"] = ps.tgt / ps.team.map(team_tot.tgt).clip(lower=1)
    ps["car_sh"] = ps.car / ps.team.map(team_tot.car).clip(lower=1)
    psi = ps.set_index("player_id")
    # snap share: last game vs the season before it (pfr names -> normalized)
    sn = pd.read_sql("SELECT week, team, player, offense_pct FROM snaps WHERE season = ? AND week < ?", con, params=(season, week))
    sn["k"] = sn.player.map(_norm)
    last_wk = sn.groupby("team").week.max()
    snap = {}
    for (team, k), x in sn.groupby(["team", "k"]):
        lw = x[x.week == last_wk.get(team)]
        prev = x[x.week != last_wk.get(team)]
        if len(lw):
            snap[(team, k)] = (float(lw.offense_pct.iloc[0]), float(prev.offense_pct.mean()) if len(prev) else None)
    # who's out: ESPN game day + the latest injury report
    try:
        eo = espn_out(todo)
    except Exception:
        eo = {}
    inj = pd.read_sql("SELECT team, gsis_id, full_name, report_status, week FROM injuries WHERE season = ?", con, params=(season,))
    inj = inj[inj.week == inj.week.max()] if len(inj) else inj
    out_ids = set(inj[inj.report_status.isin(["Out", "Doubtful"])].gsis_id)
    full = dict(pd.read_sql("SELECT gsis_id, full_name FROM rosters WHERE season = ?", con, params=(season,)).dropna().itertuples(index=False))
    # projections this week, per player
    pv = players.pivot_table(index=["game_id", "team", "player", "player_id", "pos"], columns="stat", values="mean").reset_index()
    pv[[c for c in pv.columns if c not in ("game_id", "team", "player", "player_id", "pos")]] = pv[[c for c in pv.columns if c not in ("game_id", "team", "player", "player_id", "pos")]].fillna(0)
    pos_fp = {}
    pv["fp"] = (pv.get("pass_yds", 0) * .04 + pv.get("pass_td", 0) * 4 + pv.get("rush_yds", 0) * .1 + pv.get("rec", 0) * 1
                + pv.get("rec_yds", 0) * .1 + pv.get("anytime_td", 0) * 6).fillna(0)
    for pos, x in pv.groupby("pos"):
        pos_fp[pos] = x.fp.rank(pct=True)
    pv["pct"] = pd.concat(pos_fp.values()) if pos_fp else 0
    dvp = payload.get("dvp", {})

    def dvp_line(pos, opp, align):
        key = {"WR": (align if align in ("SLOT", "OUT") and align in dvp else "WR"), "TE": "TE", "RB": "RB", "QB": "QB"}.get(pos)
        D = dvp.get(key)
        if not D:
            return None, 99
        col = {"QB": "Pass yds", "RB": "Rush yds", "WR": "Rec yds", "TE": "Rec yds", "SLOT": "Rec yds", "OUT": "Rec yds"}[key]
        i = D["cols"].index(col)
        rows = [r for r in D["rows"] if r.get("season_stats")]
        rows.sort(key=lambda r: -r["season_stats"][i])
        rk = next((n + 1 for n, r in enumerate(rows) if r["team"] == opp), 99)
        lab = {"QB": "QBs", "RB": "RBs", "WR": "WRs", "TE": "TEs", "SLOT": "slot WRs", "OUT": "outside WRs"}[key]
        stat = "passing yards" if key == "QB" else "rushing yards" if key == "RB" else "yards"
        v = next(r["season_stats"][i] for r in rows if r["team"] == opp) if rk < 99 else None
        if rk <= 8:
            return f"{NICK.get(opp, opp)} give up the {ORD(rk)} {stat} to {lab} ({v:.0f}/G)", rk
        if rk >= 25:
            return f"tough matchup: {NICK.get(opp, opp)} allow the {['fewest', '2nd-fewest', '3rd-fewest'][32 - rk] if rk >= 30 else f'{33 - rk}th-fewest'} {stat} to {lab}", rk
        return None, rk

    out = {}
    for g in todo.itertuples():
        cands = []
        for team, opp in ((g.away_team, g.home_team), (g.home_team, g.away_team)):
            # vacated volume: this season's regulars on this team who are Out / Doubtful
            gone = [r for r in ps[ps.team == team].itertuples()
                    if (r.tgt_sh >= .08 or r.car_sh >= .2) and (r.player_id in out_ids or (team, _norm(r.name)) in eo)]
            vac_t = sum(r.tgt_sh for r in gone); vac_c = sum(r.car_sh for r in gone)
            gone_txt = " & ".join(full.get(r.player_id, r.name) for r in gone[:2])
            for r in pv[(pv.game_id == g.game_id) & (pv.team == team)].itertuples():
                pos = r.pos
                if pos not in ("QB", "RB", "WR", "TE"):
                    continue
                if pos == "QB" and getattr(r, "att", 0) < 15:
                    continue
                if pos in ("WR", "TE") and getattr(r, "tgt", 0) < 3:
                    continue
                if pos == "RB" and getattr(r, "car", 0) + getattr(r, "tgt", 0) < 6:
                    continue
                s = psi.loc[r.player_id] if r.player_id in psi.index else None
                if isinstance(s, pd.DataFrame):
                    s = s.iloc[-1]
                ctx = payload["players"].get(f"{g.game_id}|{team}|{r.player}", {})
                label, align = ctx.get("label"), ctx.get("align")
                facts, reasons, boost, change, jump = [], [], 0, 0, 0.0
                gp = int(s.gp) if s is not None else 0
                if pos == "QB" and gp:
                    facts.append(f"{s.att / gp:.0f} attempts, {s.pass_yds / gp:.0f} pass yds a game" + (f" · {s.rush_yds / gp:.0f} rush yds" if s.rush_yds / gp >= 15 else ""))
                elif pos == "RB" and gp:
                    facts.append(f"{s.car / gp:.1f} carries + {s.tgt / gp:.1f} targets a game")
                    if s.rz_car >= 5:
                        facts.append(f"{int(s.rz_car)} red-zone carries"); boost += 1
                elif gp:
                    facts.append(f"{s.tgt / gp:.1f} targets a game")
                    if s.rz_tgt >= 4:
                        facts.append(f"{int(s.rz_tgt)} red-zone targets"); boost += 1
                sn_ = snap.get((team, _norm(r.player)))
                if sn_ and sn_[1] is not None and sn_[0] - sn_[1] >= .15 and pos != "QB":
                    facts.append(f"snaps jumped to {sn_[0]:.0%} last week ({sn_[1]:.0%} on the season)"); boost += 1; change += 1; jump = sn_[0] - sn_[1]
                elif sn_ and sn_[0] >= .8 and pos in ("WR", "TE", "RB"):
                    facts.append(f"on the field {sn_[0]:.0%} of snaps")
                if gp == 0:
                    facts.append("first game of the season for him on this team"); change += 1
                if pos in ("WR", "TE") and vac_t >= .1 and gone_txt:
                    reasons.append(f"{gone_txt} OUT -- {vac_t:.0%} of {NICK.get(team, team)} targets up for grabs"); boost += 1; change += 1
                elif pos == "RB" and vac_c >= .2 and gone_txt:
                    reasons.append(f"{gone_txt} OUT -- {vac_c:.0%} of {NICK.get(team, team)} carries up for grabs"); boost += 1; change += 1
                for t in (ctx.get("tags") or []):
                    if t.get("kind") == "defout" and t.get("dir", 1) > 0:
                        reasons.append(t.get("text", t.get("short", "")).split(":")[0]); boost += 1; change += 1
                        break
                dl, rk = dvp_line(pos, opp, align)
                if dl and rk <= 8:
                    reasons.append(dl); boost += 1
                elif dl and rk >= 25 and not reasons:
                    reasons.append(dl); boost -= 1
                # how much this week's changes matter: projected production for his position, each signal, and the size of a snap jump
                score = .5 * float(r.pct) + .15 * boost + .6 * jump
                if change == 0:                                    # only players affected by a change this week
                    continue
                cands.append(dict(name=r.player, team=team, pos=pos, label=label, facts=facts, reasons=reasons[:3], score=round(score, 3),
                                  proj=dict(tgt=round(float(getattr(r, "tgt", 0) or 0), 1), car=round(float(getattr(r, "car", 0) or 0), 1),
                                            pass_yds=round(float(getattr(r, "pass_yds", 0) or 0)))))
        cands.sort(key=lambda c: -c["score"])
        for c in cands:
            c["tier"] = 1 if c["score"] >= .85 else 2 if c["score"] >= .6 else 3
        out[g.game_id] = cands                                   # [] = no changes this week
    return out



def add_defout(payload, con, todo, season, week, players):
    """Missing defensive starters (injury report + ESPN game day) and flags on the offensive players who
    have historically seen more targets because of them (boxscore/defout.py)."""
    try:
        miss = DO.upcoming(con, todo, season, week, espn_out(todo))
    except Exception as e:                                  # context only: never block a run
        print(f"  defensive absences skipped ({type(e).__name__}: {e})")
        return
    tg = players[players.stat == "tgt"].rename(columns={"mean": "tgt"})
    payload["defout"] = {}
    for g in todo.itertuples():
        for d, o in ((g.home_team, g.away_team), (g.away_team, g.home_team)):
            m = miss.get((g.game_id, d))
            if not m:
                continue
            payload["defout"].setdefault(g.game_id, {})[d] = m
            off = tg[(tg.game_id == g.game_id) & (tg.team == o)][["player", "pos", "tgt"]]
            for name, tags in DO.offense_flags(m, off).items():
                ent = payload["players"].setdefault(f"{g.game_id}|{o}|{name}", dict(label=None, align=None, tags=[]))
                ent["tags"] = tags + list(ent.get("tags") or [])
                print(f"  {o} {name}: " + "; ".join(t["short"] for t in tags))


def first_scorers(plays):
    """{game id: player id of the game's first offensive TD scorer, or None if a defense / special teams scored first}."""
    t = plays[(plays.touchdown == 1)].sort_values(["game_id", "play_id"]).drop_duplicates("game_id")
    out = {}
    for r in t.itertuples():
        off = r.td_team == r.posteam
        out[r.game_id] = (r.receiver_player_id if r.pass_touchdown == 1 else r.rusher_player_id if r.rush_touchdown == 1 else None) if off else None
    return out


def add_actuals(players, teams, fr, con):
    pg = fr.pg.set_index(["game_id", "player_id"])
    gids = sorted(set(players.game_id))
    first = first_scorers(pd.read_sql(f"select game_id, play_id, posteam, td_team, touchdown, pass_touchdown, rush_touchdown, receiver_player_id, rusher_player_id from plays where touchdown = 1 and game_id in ({','.join('?' * len(gids))})", con, params=gids))
    def actual(r):
        if r.stat == "first_td":                       # graded from play-by-play (box scores don't say who scored first)
            return float(first.get(r.game_id) == r.player_id) if r.game_id in first else np.nan
        key = (r.game_id, r.player_id)
        if key not in pg.index:
            return 0.0
        a = pg.loc[key]
        if r.stat == "anytime_td":
            return float(a.rec_td + a.rush_td > 0)
        return float(a[r.stat]) if r.stat in a.index else np.nan
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
