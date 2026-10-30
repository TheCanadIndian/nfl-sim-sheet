#!/usr/bin/env python3
"""
Anytime goal scorer projections for upcoming NHL games.

    python hockey/fetch.py --seasons 20262027        # refresh results/schedule first
    python hockey/project.py                         # next date with games
    python hockey/project.py --date 2026-10-01 --overrides hockey/overrides.csv

Lineups: each team's current roster (so offseason moves count), dressing the 12
forwards and 6 defensemen with the most expected ice time. Starting goalie: the
team's most recent starter on the current roster. Both can be changed in an
overrides file (news the API doesn't have: scratches, injuries, confirmed starters):

    team,player,action
    TOR,Auston Matthews,out          # remove from the lineup
    TOR,Easton Cowan,in              # force into the lineup
    BOS,Joonas Korpisalo,goalie      # starting goalie

Writes hockey/projections/<date>.csv and .html (players ranked by P(goal)).
"""

import argparse
import datetime as dt
import json
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(1, os.path.dirname(HERE))
import fetch as F  # noqa: E402
import lines as L  # noqa: E402
import gamesim as GS  # noqa: E402
import model as M  # noqa: E402
import learned  # noqa: E402
learned.apply("nhl")      # settings adopted by the self-tuning job (learn.py)
import report  # noqa: E402

OUT = os.path.join(HERE, "projections")
FIT_SEASONS = 3          # goal model: the last 3 completed seasons plus this season's games so far
                         # (rolling; in-season refit tested 2026-10-05: better at 5 of 6 checkpoints)
# How far a listed power-play unit moves a player's expected PP time away from his own
# recent history (0 = ignore the unit, 1 = use the unit's typical time). Not backtestable
# (no historical line reports), so kept moderate and tracked on the results page.
LINE_PP_BLEND = 0.5
NO_UNIT_KEEP = 0.4      # share of his historical PP time kept when he's on neither unit


def american(p):
    p = min(max(p, 1e-4), 1 - 1e-4)
    return round(-100 * p / (1 - p)) if p >= .5 else round(100 * (1 - p) / p)


def et(start_utc):
    t = pd.Timestamp(start_utc).tz_convert("America/New_York")
    return t.strftime("%a %b %d · %I:%M %p").replace(" 0", " ") + " ET"


def rosters(teams):
    out, goalies = [], []
    for t in teams:
        r = F.get(f"{F.WEB}/roster/{t}/current") or {}
        for grp in ("forwards", "defensemen"):
            for p in r.get(grp, []):
                out.append(dict(team=t, player_id=p["id"], pos=p["positionCode"],
                                name=f"{p['firstName']['default']} {p['lastName']['default']}"))
        for p in r.get("goalies", []):
            goalies.append(dict(team=t, player_id=p["id"], name=f"{p['firstName']['default']} {p['lastName']['default']}"))
    return pd.DataFrame(out), pd.DataFrame(goalies)


def load_overrides(path):
    if not path or not os.path.exists(path):
        return pd.DataFrame(columns=["team", "player", "action"])
    o = pd.read_csv(path, comment="#", skipinitialspace=True)
    o["action"] = o.action.str.strip().str.lower()
    return o


PAIR_MIN_PAYOUT = 15.0   # user rule (Oct 2026): 2-leg parlays only if they pay 15x or more
PAIRS_PER_NIGHT = 5
# Demotions vs the team's previous game (owner's call, 2026-10-17, on early evidence): in 2026-27 Oct 1-16, forwards
# moved down a line scored 3.6 pts and players dropped a power-play unit 5.1 pts below our chance (n 161 / 89),
# against 1.5 pts for players who didn't move; not yet significant (z about -1.2). Applied: about half of the
# excess, as multipliers on the goal rate (they stack). {} turns it off. Recheck around 2026-12-01.
DEMOTE = {"line": 0.94, "pp": 0.88}


SLATES = (("Early slate", 0, 17), ("Middle slate", 17, 21), ("Late slate", 21, 24))   # Eastern start hour


def slates(games):
    out = {}
    for g in games:
        if not g.get("start_utc"):
            continue
        h = pd.Timestamp(g["start_utc"]).tz_convert("America/New_York").hour
        lab = next(l for l, a, b in SLATES if a <= h < b)
        out.setdefault(lab, []).append(g)
    return {l: out[l] for l, _, _ in SLATES if l in out}


def pairs_15x(games, n=PAIRS_PER_NIGHT):
    """The likeliest anytime-goal pairs from DIFFERENT games whose fair payout is 15x+ (joint chance <= 1/15), no
    player used twice. Backtests: pairs like these hit about their implied rate (5-8% depending on which near-identical pair
    gets picked -- dozens sit at ~6.7% each night), so treat them as fairly priced; same-team / same-game pairs did worse."""
    import itertools
    P = [dict(gid=g["id"], game=f'{g["away"]["team"]} @ {g["home"]["team"]}', team=s["team"], id=p["id"], name=p["name"], p=p["p"])
         for g in games for s in (g["away"], g["home"]) for p in s["players"]]
    P = sorted([x for x in P if x["p"] >= .08], key=lambda x: -x["p"])       # every plausible leg (a top-90 cut left big slates with no pair under 1/15)
    cands = sorted(((a["p"] * b["p"], a, b) for a, b in itertools.combinations(P, 2)
                    if a["gid"] != b["gid"] and a["p"] * b["p"] <= 1 / PAIR_MIN_PAYOUT), key=lambda z: -z[0])
    out, used = [], set()
    for pj, a, b in cands:
        if a["id"] in used or b["id"] in used:
            continue
        out.append(dict(p=round(pj, 4), payout=round(1 / pj, 1), legs=[a, b]))
        used |= {a["id"], b["id"]}
        if len(out) >= n:
            break
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date")
    ap.add_argument("--overrides", default=os.path.join(HERE, "overrides.csv"))
    a = ap.parse_args()

    games, shots, _ = M.load()
    # Only games that haven't started: a game's projection freezes at its last run before puck drop.
    start = pd.to_datetime(games.start_utc, utc=True)
    upcoming = games[~games.state.isin(["OFF", "FINAL"]) & games.game_type.isin([2, 3])
                     & (start > pd.Timestamp.now(tz="UTC"))].copy()
    if a.date:
        todo = upcoming[upcoming.date == pd.Timestamp(a.date)]
    else:
        todo = upcoming[upcoming.date == upcoming.date.min()]
    if todo.empty:
        sys.exit("No upcoming games for that date.")
    date = todo.date.iloc[0].date().isoformat()
    teams = sorted(set(todo.home) | set(todo.away))
    print(f"{date}: {len(todo)} games; loading rosters for {len(teams)} teams")

    skaters, goalies = rosters(teams)
    ov = load_overrides(a.overrides)
    name_to_id = {(r.team, r.name.lower()): r.player_id for r in pd.concat([skaters, goalies]).itertuples()}

    # Lineup sources: official NHL roster > Daily Faceoff lines > ice-time estimate.
    slugs = L.team_slugs()
    tl = {t: (L.team_lines(t, slugs[t], skaters, goalies) if t in slugs else None) for t in teams}
    # Players on a line report but not on the roster page (fresh call-ups): look them up and re-read the lines.
    found = []
    for t, d in tl.items():
        for nm in (d or {}).get("unmatched", []):
            fp = L.find_player(nm, t)
            if fp and fp["pos"] != "G":
                found.append(dict(team=t, **fp))
    if found:
        skaters = pd.concat([skaters, pd.DataFrame(found)[["team", "player_id", "pos", "name"]]], ignore_index=True)
        for t in {f["team"] for f in found}:
            tl[t] = L.team_lines(t, slugs[t], skaters, goalies)
        print("call-ups added from line reports:", ", ".join(f"{f['name']} ({f['team']})" for f in found))
    sg = L.starting_goalies(goalies)
    # Game roster from the NHL feed: dressed lineup once scratches post (complete), otherwise
    # just who is eligible (used to drop players who aren't on the game roster at all).
    official, game_ro = {}, {}
    for g in todo.itertuples():
        for t, d in L.game_roster(g.game_id).items():
            game_ro[(g.game_id, t)] = d
            if d["complete"]:
                official[(g.game_id, t)] = d["ids"]
    # Call-ups on the game roster but not yet on the team's roster page: add them so they
    # can be projected (they'd otherwise be missing from the sheet).
    have = set(zip(skaters.team, skaters.player_id.astype(int)))
    extra = [dict(team=t, player_id=pid, name=d["info"][pid][0], pos=d["info"][pid][1])
             for (gid, t), d in game_ro.items() for pid in d["ids"] if (t, pid) not in have]
    if extra:
        ex = pd.DataFrame(extra).drop_duplicates(["team", "player_id"])
        skaters = pd.concat([skaters, ex[[c for c in skaters.columns if c in ex.columns]]], ignore_index=True)
        print("added from game rosters:", ", ".join(f"{r.name} ({r.team})" for r in ex.itertuples()))

    fut_players = []
    for g in todo.itertuples():
        for t in (g.home, g.away):
            fut_players.append(skaters[skaters.team == t].assign(game_id=g.game_id))
    fut_players = pd.concat(fut_players, ignore_index=True)

    # Starting goalie: latest starter on the current roster, unless overridden.
    xg_model = M.XGModel().fit(shots)
    probe = M.Frames(xg_model)
    recent_start = probe.starters.merge(probe.g[["game_id", "date"]], on="game_id").sort_values("date")
    fut_goalies, goalie_status = {}, {}
    for g in todo.itertuples():
        for t in (g.home, g.away):
            ids = set(goalies[goalies.team == t].player_id)
            hist = recent_start[recent_start.goalie.isin(ids)]
            pick = int(hist.goalie.iloc[-1]) if len(hist) else (next(iter(ids)) if ids else None)
            goalie_status[(g.game_id, t)] = "last starter"
            if t in sg:
                pick, goalie_status[(g.game_id, t)] = sg[t][0], str(sg[t][1]).lower()
            elif tl.get(t) and tl[t]["fresh"] and tl[t]["goalie"]:
                pick, goalie_status[(g.game_id, t)] = tl[t]["goalie"], "projected"
            for o in ov[(ov.team == t) & (ov.action == "goalie")].itertuples():
                if (t, str(o.player).strip().lower()) in name_to_id:
                    pick, goalie_status[(g.game_id, t)] = name_to_id[(t, str(o.player).strip().lower())], "override"
            if pick is not None:
                fut_goalies[(g.game_id, t)] = pick

    future = todo.assign(season=todo.season.astype(int))
    fr = M.Frames(xg_model, future=future, future_players=fut_players, future_goalies=fut_goalies)
    cur = int(todo.season.iloc[0])
    fit_seasons = [cur - 10001 * k for k in range(FIT_SEASONS, -1, -1)]   # e.g. 2023-24 ... 2026-27
    gm = M.GoalModel().fit(fr.pg[fr.pg.toi.notna() & fr.pg.season.isin(fit_seasons)])

    pg = fr.pg[fr.pg.game_id.isin(todo.game_id)].copy()
    pg["e_toi"] = pg.e_np_toi + pg.e_pp_toi
    # games since his last goal (owner 2026-10-27, research/player_trends_nhl.py: the market overprices players who just
    # scored and underprices droughts; 3+ games dry +21% on 173 live legs at the owner's books). Chip + Profit tracking only.
    played = fr.pg[fr.pg.toi.notna() & (fr.pg.toi > 0)].sort_values("date")
    dry = {}
    for pid, h in played.groupby("player_id"):
        gl = h.goals.to_numpy()
        if len(gl) >= 5:
            nz = np.nonzero(gl > 0)[0]
            dry[int(pid)] = int(len(gl) - 1 - nz[-1]) if len(nz) else int(len(gl))
    # Dress 12 F + 6 D by expected ice time, then apply overrides.
    outs = {(o.team, str(o.player).strip().lower()) for o in ov[ov.action == "out"].itertuples()}
    ins = {(o.team, str(o.player).strip().lower()) for o in ov[ov.action == "in"].itertuples()}
    pg["key"] = list(zip(pg.team, pg.name.str.lower()))
    pg = pg[~pg.key.isin(outs)]
    pg["forced"] = pg.key.isin(ins)
    pg["player_id"] = pg.player_id.astype("int64")
    pg["line"], pg["unit"], pg["src"] = "", "", "estimate"
    lineup_note = {}
    keep = pd.Series(False, index=pg.index)
    for (gid, team), x in pg.groupby(["game_id", "team"]):
        d = tl.get(team)
        listed = {pid for pid, v in d["players"].items() if v["line"]} if d and d["fresh"] else set()
        if d and d["fresh"]:
            for pid, v in d["players"].items():
                m = x.index[x.player_id == pid]
                pg.loc[m, "line"], pg.loc[m, "unit"] = v["line"] or "", v["pp"] or ""
        if (gid, team) in official:
            dressed, src = official[(gid, team)], "official"
            sc = game_ro[(gid, team)]["scratches"]
            lineup_note[(gid, team)] = "Official NHL roster" + (f" ({len(sc)} healthy scratches removed)" if sc else "")
        elif len(listed) >= 16:
            dressed, src = listed - d["out"], "lines"
            gr = game_ro.get((gid, team))
            if gr and gr["ids"]:
                dressed = dressed & (gr["ids"] | set())      # drop listed players not on the game roster / scratched
            when = d["updated"].tz_convert("America/New_York").strftime("%b %d, %I:%M %p").replace(" 0", " ")
            lineup_note[(gid, team)] = f"Lines: {d['source']}, updated {when} ET"
        else:
            dressed, src = None, "estimate"
            lineup_note[(gid, team)] = "Estimated from ice time (no current line report)"
        if dressed is None:
            y = x.sort_values("e_toi", ascending=False)
            gr = game_ro.get((gid, team))
            if gr and gr["ids"]:
                y = y[y.player_id.isin(gr["ids"])]
            rank = y.groupby("pos_grp").cumcount()
            ok = ((y.pos_grp == "F") & (rank < 12)) | ((y.pos_grp == "D") & (rank < 6))
            keep.loc[ok.index[ok]] = True
        else:
            keep.loc[x.index[x.player_id.isin(dressed)]] = True
            gr = game_ro.get((gid, team))
            if src == "lines":
                # Report short (a listed player is injured/scratched or the report lists 17): fill to
                # 12 F + 6 D by expected ice time, from the game roster when posted, else from the
                # team roster minus players the report lists as out.
                elig = gr["ids"] if gr and gr["ids"] else set(x.player_id) - set(d["out"])
                short = 18 - int(x.player_id.isin(dressed).sum())        # 11 F / 7 D lineups are fine
                for grp, need in (("F", 12), ("D", 6)) if short > 0 else ():
                    have_n = int((x.player_id.isin(dressed) & (x.pos_grp == grp)).sum())
                    pool = x[(x.pos_grp == grp) & x.player_id.isin(elig) & ~x.player_id.isin(dressed)]
                    add = pool.sort_values("e_toi", ascending=False).head(min(max(need - have_n, 0), short))
                    keep.loc[add.index] = True
                    short -= len(add)
                    if len(add):
                        lineup_note[(gid, team)] += " · added (report short): " + ", ".join(add.name)
        pg.loc[x.index, "src"] = src
    pg = pg[keep | pg.forced].copy()

    # Opponent missing a regular defenseman (owner 2026-10-30, research/nhl_goal_methods_absd.py: a D who played 3+ of
    # his team's last 5 games this season and isn't dressed tonight; 6th+ game only -- summer departures add nothing).
    # Players facing that team scored 17% more than the 2025-26 closing price implied (relative), 9% live. Profit tracking only.
    d_out = {}
    cur_hist = fr.pg[fr.pg.toi.notna() & (fr.pg.season == int(todo.season.iloc[0]))]
    for (gid, team), x in pg.groupby(["game_id", "team"]):
        h = cur_hist[cur_hist.team == team]
        gids = h.drop_duplicates("game_id").sort_values("date").game_id.tolist()
        if len(gids) < 5:
            continue
        last5 = h[h.game_id.isin(gids[-5:]) & (h.pos == "D")]
        n = last5.groupby("player_id").size()
        regs = last5[last5.player_id.isin(n.index[n >= 3])].groupby("player_id").agg(m=("np_toi", "mean"), name=("name", "last"))
        miss = regs[~regs.index.isin(x.player_id)]
        dressed_d = float(x[x.pos == "D"].e_np_toi.sum())
        if len(miss) and miss.m.sum() / (miss.m.sum() + dressed_d) > .1:
            d_out[(gid, team)] = list(miss.sort_values("m", ascending=False).name)

    # Power-play units: move expected PP time toward the listed unit's typical time.
    hist = fr.pg[fr.pg.toi.notna() & (fr.pg.season == fr.pg.season[fr.pg.toi.notna()].max())]
    r = hist.groupby(["game_id", "team"]).pp_toi.rank(ascending=False, method="first")
    team_pp = hist.groupby(["game_id", "team"]).pp_toi.transform("sum") / 5
    share1 = float((hist.pp_toi[r <= 5] / team_pp[r <= 5].clip(lower=1)).mean())
    share2 = float((hist.pp_toi[(r > 5) & (r <= 10)] / team_pp[(r > 5) & (r <= 10)].clip(lower=1)).mean())
    lg_pp = float(team_pp.groupby([hist.game_id, hist.team]).first().mean())
    has_units = pg.groupby(["game_id", "team"]).unit.transform(lambda u: (u != "").sum() >= 8)
    unit_time = np.select([pg.unit == "PP1", pg.unit == "PP2"], [share1, share2], np.nan) * lg_pp * pg.t_pp_drawn.fillna(1)
    blended = np.where(np.isnan(unit_time), pg.e_pp_toi * NO_UNIT_KEEP,
                       (1 - LINE_PP_BLEND) * pg.e_pp_toi + LINE_PP_BLEND * unit_time)
    pg["e_pp_hist"] = pg.e_pp_toi
    pg["e_pp_toi"] = np.where(has_units, blended, pg.e_pp_toi)
    pg["base"] = (pg.e_np_toi / 3600 * pg.np_rate + pg.e_pp_toi / 3600 * pg.pp_rate) * pg.finish

    pg["lam"] = gm.lam(pg)
    pg["p_goal"] = gm.p_goal(pg, calibrate=True)
    pg["fair"] = pg.p_goal.map(american)
    # First goal of the game: every dressed skater's goals arrive at a steady rate, so
    # P(first) = his rate / all skaters' rate x P(at least one goal). Backtest 2024-26
    # (2,792 games): predicted 2.77% per skater vs 2.78% actual, calibrated by bucket.
    lam_c = -np.log(1 - pg.p_goal.clip(upper=.999))
    tot = lam_c.groupby(pg.game_id).transform("sum")
    pg["p_first"] = lam_c / tot * (1 - np.exp(-tot))
    pg["fair_first"] = pg.p_first.map(american)
    # usage ranking (display only; P(goal) and its tiers stay as above): live expected goals x exp(beta . usage),
    # usage from this season's shift charts (usage.py). Backtest 2025-26: top 3 caught 1.046 scorers/game vs 1.032 (refit 2026-10-21).
    pg["u_mult"], pg["u_rank"] = 1.0, np.nan
    try:
        import sqlite3
        import usage as UG
        ucon = sqlite3.connect(M.DB)
        season = int(todo.season.iloc[0])
        rows = UG.update(ucon, season)
        sids = {g for (g,) in ucon.execute("SELECT game_id FROM games WHERE season = ?", (season,))}
        pg["u_mult"] = UG.scores(rows, sids, pg.player_id.astype(int)).to_numpy()
        pg["u_rank"] = (lam_c * pg.u_mult).groupby(pg.game_id).rank(ascending=False, method="first")
        print(f"usage ranking: {rows[rows.game_id.isin(sids)].game_id.nunique()} games of shift charts this season")
    except Exception as e:
        print("usage ranking skipped:", type(e).__name__, e)
    # shots on goal (sog.py): expected shots and the chance of going over each common line
    try:
        import sqlite3
        import sog as SOG
        sg = SOG.project(sqlite3.connect(M.DB), pg[["game_id", "team", "opp", "player_id", "pos", "e_np_toi", "e_pp_toi"]], todo)
        for c in [c for c in sg.columns if c.startswith("sog")]:
            pg[c] = sg[c].to_numpy()
    except Exception as e:
        print("shots on goal skipped:", type(e).__name__, e)
    pp_rank = pg.groupby(["game_id", "team"]).e_pp_toi.rank(ascending=False, method="first")
    pg["pp_role"] = np.where(has_units, pg.unit, np.select(
        [(pp_rank <= 5) & (pg.e_pp_toi >= 45), (pp_rank <= 10) & (pg.e_pp_toi >= 20)], ["PP1", "PP2"], ""))
    pg["why"] = reasons(pg)
    # lineup changes vs each team's previous game; demotions cut the goal chance (DEMOTE)
    lchg, lside = lineup_changes(games, todo, pg)
    ppr = lambda v: {"PP1": 1, "PP2": 2}.get(v if isinstance(v, str) else "", 3)
    def demote(gid, pid, pos, pp_now):
        m = 1.0
        for k, before in lchg.get((gid, int(pid)), []):
            if k == "down" and pos != "D":
                m *= DEMOTE.get("line", 1.0)
            elif k == "pp" and ppr(pp_now) > ppr(before):
                m *= DEMOTE.get("pp", 1.0)
        return m
    pg["dm"] = [demote(g_, p_, po, pp_) for g_, p_, po, pp_ in zip(pg.game_id, pg.player_id, pg.pos, pg.pp_role)]
    if (pg.dm < 1).any():
        lam_c = -np.log(1 - pg.p_goal.clip(upper=.999)) * pg.dm
        pg["lam"] = pg.lam * pg.dm
        pg["p_goal"] = 1 - np.exp(-lam_c)
        pg["fair"] = pg.p_goal.map(american)
        tot = lam_c.groupby(pg.game_id).transform("sum")
        pg["p_first"] = lam_c / tot * (1 - np.exp(-tot))
        pg["fair_first"] = pg.p_first.map(american)
        if pg.u_rank.notna().any():
            pg["u_rank"] = (lam_c * pg.u_mult).groupby(pg.game_id).rank(ascending=False, method="first")
        print(f"demotions: {(pg.dm < 1).sum()} players' goal chances cut")
    # same-game combos (shiftsim.py, game-state sim on these chances): each game's top 3 by usage rank gets the sims' lift
    # for goal + team wins, goal + game over 5.5, 3+ shots + goal (2025-26 backtest: married beat legs multiplied)
    lifts = {}
    if "sog" in pg:
        try:
            import sqlite3
            import shiftsim as SSIM
            pg["sog_ppf"] = SSIM.sog_pp_share(sqlite3.connect(M.DB), pg)
            for k, g in enumerate(todo.itertuples()):
                x = pg[pg.game_id == g.game_id].assign(lam=lambda d: -np.log(1 - d.p_goal.clip(upper=.999)))
                h, a_ = x[x.team == g.home], x[x.team == g.away]
                if len(h) < 15 or len(a_) < 15 or x.sog.isna().any():
                    continue
                r_ = x.assign(rk=x.groupby("team").p_goal.rank(ascending=False, method="first") if x.u_rank.isna().any() else x.u_rank)
                who = set(r_[r_.rk <= 3].player_id.astype(int))
                for pid, v in SSIM.game_lifts(h, a_, who, seed=k).items():
                    lifts[(g.game_id, pid)] = v
            print(f"same-game lifts: {len(lifts)} players")
        except Exception as e:
            print("same-game lifts skipped:", type(e).__name__, e)
    os.makedirs(OUT, exist_ok=True)
    cols = ["game_id", "team", "opp", "player_id", "name", "pos", "p_goal", "fair", "lam", "e_np_toi", "e_pp_toi", "pp_role",
            "np_rate", "pp_rate", "finish", "games_prior", "line", "src", "e_pp_hist", "p_first"]
    # Merge into the date's saved list: games projected now replace their old rows;
    # games that already started keep their frozen pregame rows.
    csv_path = os.path.join(OUT, f"{date}.csv")
    new_rows = pg[cols].copy()
    if os.path.exists(csv_path):
        old = pd.read_csv(csv_path)
        new_rows = pd.concat([old[~old.game_id.isin(todo.game_id)], new_rows], ignore_index=True)
    new_rows.sort_values("p_goal", ascending=False).round(4).to_csv(csv_path, index=False)

    gname = {r.player_id: r.name for r in goalies.itertuples()}
    gt = fr.goalie_table
    cur_season = int(todo.season.iloc[0])

    def gstats(pid, season):
        x = gt[(gt.goalie == pid) & (gt.season == season)]
        if x.empty:
            return None
        r = x.iloc[0]
        return dict(gp=int(r.gp), sa=int(r.sa), ga=int(r.ga), sv=round(float(r.sv), 3),
                    gsaa=round(float(r.gsaa), 1), gsax=round(float(r.gsax), 1))
    prev = cur_season - 10001
    gq_now = fr.tg.set_index(["game_id", "opp"]).opp_gq.to_dict()
    h2h = head_to_head(games, shots, set(zip(pg.player_id.astype(int), pg.opp)))
    tg = fr.tg.set_index(["game_id", "team"])
    payload = dict(date=date, generated=dt.datetime.now().strftime("%b %d, %Y %I:%M %p").replace(" 0", " "),
                   coef={k: round(float(v), 3) for k, v in zip(["const"] + M.GLM_X, gm.b)}, games=[])
    for g in todo.sort_values("start_utc").itertuples():
        side = {}
        for t in (g.away, g.home):
            x = pg[(pg.game_id == g.game_id) & (pg.team == t)].sort_values("p_goal", ascending=False)
            side[t] = dict(
                team=t, exp_goals=round(float(x.lam.sum()), 2),
                goalie=gname.get(fut_goalies.get((g.game_id, t)), "unknown"), goalie_id=None if fut_goalies.get((g.game_id, t)) is None else int(fut_goalies[(g.game_id, t)]),
                goalie_status=goalie_status.get((g.game_id, t), ""), lineup=lineup_note.get((g.game_id, t), ""),
                lchg=lside.get((g.game_id, t)),
                dout=d_out.get((g.game_id, t)),
                gstats=dict(now=gstats(fut_goalies.get((g.game_id, t)), cur_season),
                            last=gstats(fut_goalies.get((g.game_id, t)), prev),
                            rating=round(float(gq_now.get((g.game_id, g.home if t == g.away else g.away), 1.0)), 3)),
                players=[dict(id=int(r.player_id), name=r.name, pos=r.pos, p=round(float(r.p_goal), 4), fair=int(r.fair),
                              pf=round(float(r.p_first), 4), fair_first=int(r.fair_first), why=r.why,
                              lam=round(float(r.lam), 3), toi=round(float(r.e_np_toi + r.e_pp_toi) / 60, 1),
                              pp=r.pp_role, line=r.line, new=bool(r.games_prior < 1), chg=lchg.get((g.game_id, int(r.player_id))),
                              cut=round(1 - float(r.dm), 3) if r.dm < 1 else None,
                              h2h=h2h.get((int(r.player_id), r.opp)),
                              ur=None if pd.isna(r.u_rank) else int(r.u_rank), um=round(float(r.u_mult), 3),
                              sog=round(float(r.sog), 2) if "sog" in x and pd.notna(r.sog) else None,
                              so=[round(float(getattr(r, f"sog_o{k}")), 3) for k in ("05", "15", "25", "35", "45")] if "sog" in x and pd.notna(r.sog) else None,
                              sg=lifts.get((g.game_id, int(r.player_id))), dry=dry.get(int(r.player_id)))
                         for r in x.itertuples()])
        lam_t = {t: float(-np.log(1 - pg[(pg.game_id == g.game_id) & (pg.team == t)].p_goal.clip(upper=.999)).sum())
                 for t in (g.away, g.home)}
        o = GS.outcome(lam_t[g.home], lam_t[g.away])
        ml = dict(home=round(o["home_win"], 4), away=round(o["away_win"], 4),
                  home_fair=GS.american(o["home_win"]), away_fair=GS.american(o["away_win"]),
                  home_pl=round(o["home_m15"], 4), away_pl=round(o["away_m15"], 4),
                  home_pl_fair=GS.american(o["home_m15"]), away_pl_fair=GS.american(o["away_m15"]),
                  away_pl_plus=round(1 - o["home_m15"], 4), home_pl_plus=round(1 - o["away_m15"], 4),
                  ot=round(o["reg_tie"], 4), total=round(o["exp_total"], 2),
                  totals={str(k): round(v, 4) for k, v in o["totals"].items()})
        payload["games"].append(dict(id=int(g.game_id), start=et(g.start_utc), start_utc=g.start_utc,
                                     away=side[g.away], home=side[g.home], ml=ml))
    payload["posdef"] = pos_defense(fr)
    sm = shot_maps(games, shots, teams, list(fut_goalies.values()), players=pg.player_id.dropna().astype(int).unique())
    osm = (json.load(open(os.path.join(OUT, f"{date}.json"))) if os.path.exists(os.path.join(OUT, f"{date}.json")) else {}).get("shotmap") or {}
    payload["shotmap"] = dict(sm, teams={**osm.get("teams", {}), **sm["teams"]}, goalies={**osm.get("goalies", {}), **sm["goalies"]},
                              shooters={**osm.get("shooters", {}), **sm["shooters"]})
    payload["lg_sv"] = round(float(gt.attrs.get("lg_sv", .9)), 3)
    starters = sorted({p for p in fut_goalies.values() if p is not None})
    payload["goalies"] = [dict(name=gname.get(p, str(p)), team=next((t for (gid, t), q in fut_goalies.items() if q == p), ""),
                               now=gstats(p, cur_season), last=gstats(p, prev)) for p in starters]
    json_path = os.path.join(OUT, f"{date}.json")
    old = json.load(open(json_path)) if os.path.exists(json_path) else {}
    if old:
        kept = [x for x in old["games"] if x["id"] not in set(todo.game_id.astype(int))]
        payload["games"] = sorted(kept + payload["games"], key=lambda x: x.get("start_utc") or "")
    # nightly 2-leg pairs paying 15x+ (frozen once the night's first game starts, so the record is pregame)
    # split by slate (early / middle / late, Eastern start time); each slate freezes at its own first puck drop
    now = pd.Timestamp.now(tz="UTC")
    payload["pairs"] = []
    for sl, gs in slates(payload["games"]).items():
        first = min(pd.Timestamp(x["start_utc"]) for x in gs)
        olds = [x for x in (old.get("pairs") or []) if x.get("slate") == sl]
        if first <= now and olds:
            payload["pairs"] += olds
        else:
            payload["pairs"] += [dict(x, slate=sl, slate_start=first.tz_convert("America/New_York").strftime("%I:%M %p").lstrip("0")) for x in pairs_15x(gs)]
    payload["slates"] = {sl: len(gs) for sl, gs in slates(payload["games"]).items()}
    rp = os.path.join(OUT, "pairs_record.json")
    payload["pairs_record"] = json.load(open(rp)) if os.path.exists(rp) else None
    json.dump(payload, open(json_path, "w"), separators=(",", ":"))
    try:                                                    # profit.tags logs goal overs for 3+ games dry (experimental)
        import parlays as PL
        dp = os.path.join(PL.SITE, "markets", "dry_nhl.json")
        json.dump(dict(generated=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                       games={str(x["id"]): {"start": x.get("start_utc"), "dry": {PL.jsnorm(q["name"]): q["dry"] for s_ in (x["away"], x["home"])
                                                                                   for q in s_["players"] if q.get("dry") is not None}}
                              for x in payload["games"]}), open(dp, "w"), separators=(",", ":"))
    except Exception as e:                                  # noqa: BLE001
        print(f"dry_nhl.json: skipped ({type(e).__name__}: {e})")
    try:                                                    # profit.tags logs goal overs vs a team missing a regular D (experimental)
        import parlays as PL
        json.dump(dict(generated=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                       games={str(x["id"]): {"start": x.get("start_utc"),
                                             "out": {s_["team"]: s_["dout"] for s_ in (x["away"], x["home"]) if s_.get("dout")},
                                             "vs": [PL.jsnorm(q["name"]) for s_, o_ in ((x["away"], x["home"]), (x["home"], x["away"]))
                                                    if o_.get("dout") for q in s_["players"]]}
                              for x in payload["games"]}), open(os.path.join(PL.SITE, "markets", "dout_nhl.json"), "w"), separators=(",", ":"))
    except Exception as e:                                  # noqa: BLE001
        print(f"dout_nhl.json: skipped ({type(e).__name__}: {e})")
    html = TEMPLATE.replace("__FONTS__", report.FONTS).replace("__CSS__", report.BASE_CSS) \
        .replace("__DATA__", json.dumps(payload, separators=(",", ":")).replace("</", "<\\/")).replace("__DATE__", date)
    path = os.path.join(OUT, f"{date}.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    with open(os.path.join(OUT, "index.html"), "w", encoding="utf-8") as f:
        f.write(html)
    top = pg.sort_values("p_goal", ascending=False).head(15)
    print(top[["team", "opp", "name", "pos", "p_goal", "fair", "pp_role"]].to_string(index=False))
    print(f"\nWrote {path}")


def reasons(pg):
    """Up to three short, data-backed reasons a player's goal chance is high (for the
    highlighted picks): shot rate, power play, ice time, opponent defense and goalie."""
    f = pg.pos != "D"
    rate = (pg.e_np_toi * pg.np_rate + pg.e_pp_toi * pg.pp_rate) / (pg.e_np_toi + pg.e_pp_toi).clip(lower=1)
    hi_rate = rate >= rate[f].quantile(.9) if f.any() else rate > 9e9
    toi_rank = pg.groupby(["game_id", "team"]).e_np_toi.rank(ascending=False, method="first")
    out = []
    for i in pg.index:
        r, w = pg.loc[i], []
        if hi_rate[i]:
            w.append("elite shot volume")
        if r.opp_gq >= 1.05:
            w.append("weak goalie")
        if r.opp_xga >= 1.07:
            w.append("leaky defense")
        if r.get("opp_pp_given", 1) >= 1.15 and r.get("pp_role") in ("PP1", "PP2"):
            w.append("opponent takes penalties")
        if r.finish >= 1.12:
            w.append("proven finisher")
        if r.pos != "D" and toi_rank[i] <= 2 and len(w) < 2:
            w.append("top minutes")
        if r.opp_b2b == 1:
            w.append("opponent on back-to-back")
        out.append(w[:3])
    return out


def head_to_head(games, shots, pairs):
    """Goals / assists / points for each (player_id, opponent) in `pairs`, over every game in the
    database (2022-23 on, regular season and playoffs), plus the player's overall points per game
    for comparison and his last 5 meetings. Shootout goals don't count."""
    import sqlite3
    con = sqlite3.connect(M.DB)
    toi = pd.read_sql("SELECT DISTINCT game_id, player_id, team FROM toi", con)
    g = games[["game_id", "date", "home", "away", "game_type"]]
    toi = toi.merge(g, on="game_id")
    toi["opp"] = np.where(toi.team == toi.home, toi.away, toi.home)
    goals = shots[shots.goal == 1]
    gl = goals.groupby(["game_id", "shooter"]).size().rename("g")
    a_cols = [c for c in ("assist1", "assist2") if c in goals]
    ast = pd.concat([goals[["game_id", c]].rename(columns={c: "pid"}) for c in a_cols]).dropna()
    al = ast.groupby(["game_id", "pid"]).size().rename("a")
    toi = toi.join(gl, on=["game_id", "player_id"]).join(al, on=["game_id", "player_id"])
    toi[["g", "a"]] = toi[["g", "a"]].fillna(0).astype(int)
    pids = {p for p, _ in pairs}
    toi = toi[toi.player_id.isin(pids)]
    overall = toi.groupby("player_id").agg(gp=("g", "size"), p=("g", "sum"), a=("a", "sum"))
    overall["ppg"] = (overall.p + overall.a) / overall.gp
    out = {}
    for (pid, opp), x in toi[toi.opp.isin({o for _, o in pairs})].groupby(["player_id", "opp"]):
        if (pid, opp) not in pairs:
            continue
        x = x.sort_values("date")
        out[(pid, opp)] = dict(gp=int(len(x)), g=int(x.g.sum()), a=int(x.a.sum()), pts=int(x.g.sum() + x.a.sum()),
                               ppg_all=round(float(overall.ppg.get(pid, 0)), 2),
                               last=[[d.strftime("%b %d, %Y").replace(" 0", " "), int(gg), int(aa), int(gt == 3)]
                                     for d, gg, aa, gt in zip(x.date, x.g, x.a, x.game_type)][::-1][:5])
    return out


def lineup_changes(games, todo, pg):
    """Tonight's lineup vs each team's previous game. Line and power-play moves come from that game's frozen
    pregame rows (projections/<date>.csv); who dressed comes from the game's ice-time records. Returns
    ({(game_id, player_id): [[kind, before], ...]}, {(game_id, team): dict(prev=label, out=[...])}).
    kinds: "in" (didn't dress last game), "up" / "down" (forward line or D pair), "pp" (power-play unit changed)."""
    import glob, sqlite3
    con = sqlite3.connect(M.DB)
    done = games[games.state.isin(["OFF", "FINAL"]) & games.game_type.isin([2, 3])]
    toi = pd.read_sql("SELECT game_id, player_id, team, name, pos FROM toi", con).drop_duplicates(["game_id", "player_id"])
    cols = ["game_id", "team", "player_id", "line", "pp_role"]
    frames = [pd.read_csv(f, usecols=lambda c: c in cols).reindex(columns=cols)      # older files have no line column
              for f in sorted(glob.glob(os.path.join(OUT, "20??-??-??.csv")))[-40:]]
    old = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=["game_id", "team", "player_id", "line", "pp_role"])
    norm = lambda v: v if isinstance(v, str) and v else None
    chg, side = {}, {}
    for g in todo.itertuples():
        for t in (g.home, g.away):
            prev = done[((done.home == t) | (done.away == t)) & (done.date < g.date)].sort_values("date").tail(1)
            if prev.empty:
                continue
            pv = prev.iloc[0]
            dressed = toi[(toi.game_id == pv.game_id) & (toi.team == t)]
            if dressed.empty:
                continue                                   # last game's records not in yet
            was = old[(old.game_id == pv.game_id) & (old.team == t)].drop_duplicates("player_id", keep="last").set_index("player_id")
            now = pg[(pg.game_id == g.game_id) & (pg.team == t)]
            ids_now, ids_was = set(now.player_id.astype(int)), set(dressed.player_id.astype(int))
            for r in now.itertuples():
                pid, c = int(r.player_id), []
                if pid not in ids_was:
                    c.append(["in", None])
                elif pid in was.index:
                    l0, l1 = norm(was.at[pid, "line"]), norm(r.line)
                    if l0 and l1 and l0 != l1 and l0[0] == l1[0]:
                        c.append(["up" if l1 < l0 else "down", l0])
                    p0, p1 = norm(was.at[pid, "pp_role"]), norm(r.pp_role)
                    if p0 != p1:
                        c.append(["pp", p0])
                if c:
                    chg[(g.game_id, pid)] = c
            opp = pv.away if pv.home == t else pv.home
            out = [dict(name=d.name, pos=d.pos, line=norm(was.at[int(d.player_id), "line"]) if int(d.player_id) in was.index else None)
                   for d in dressed[~dressed.player_id.astype(int).isin(ids_now)].itertuples()]
            side[(g.game_id, t)] = dict(prev=f"{'vs' if pv.home == t else 'at'} {opp} {pd.Timestamp(pv.date).strftime('%b %d').replace(' 0', ' ')}",
                                        known=bool(len(was)), out=out)
    return chg, side


def shot_maps(games, shots, teams, goalie_ids, n_games=30, players=(), n_shots=50):
    """Where each team shoots from (last `n_games`) and where it allows shots, relative to the league,
    on a 5 ft grid of the attacking zone (net at the top: 17 lateral x 15 depth cells, from 10 ft behind
    the goal line to the blue line). Unblocked, non-empty-net shots; lightly smoothed and pulled toward
    average with 12 pseudo-games. Plus each starting goalie's save % by zone over the last two seasons.
    Display only: in 2024-26 tests neither the zone overlay nor zone save % predicted goals beyond the
    model's own team and goalie ratings."""
    done = games[games.state.isin(["OFF", "FINAL"]) & games.game_type.isin([2, 3]) & games.game_id.isin(shots.game_id)][["game_id", "date", "home", "away"]]
    s = shots[shots.game_id.isin(done.game_id) & (shots.kind != "blocked-shot") & (shots.empty_net == 0)
              & shots.x.notna() & shots.dist.notna()].merge(done, on="game_id")
    x, y = s.x.astype(float), s.y.fillna(0).astype(float)
    pos = (np.hypot(89 - x, y) - s.dist).abs() <= (np.hypot(-89 - x, y) - s.dist).abs()     # attacking the +89 net
    s["d"] = np.where(pos, 89 - x, x + 89)
    s["l"] = np.where(pos, -y, y)
    s["opp"] = np.where(s.team == s.home, s.away, s.home)
    s = s[(s.d >= -10) & (s.d < 65) & (s.l.abs() < 42.5)]
    s["r"] = ((s.d + 10) // 5).astype(int)
    s["c"] = ((s.l + 42.5) // 5).astype(int)
    R, C = 15, 17
    lg_s = s[s.date >= s.date.max() - pd.Timedelta(days=365)]
    lg_games = lg_s.groupby("game_id").ngroups * 2                       # team-games
    def grid(df):
        g = np.zeros((R, C))
        np.add.at(g, (df.r.values, df.c.values), 1.0)
        k = np.array([1.0, 2.0, 1.0]); k /= k.sum()
        for _ in range(2):                                                # two passes ~ a 5x5 binomial blur
            for ax in (0, 1):
                g = np.apply_along_axis(lambda v: np.convolve(np.pad(v, 1, mode="edge"), k, mode="valid"), ax, g)
        return g
    lg = grid(lg_s) / max(lg_games, 1)
    mask = lg < .02                                                       # cells where hardly anyone shoots
    inner = (s.dist <= 22) & (s.angle <= 50)
    s["inner"] = inner
    K = 12.0
    def rel(df, ng):
        g = (grid(df) + K * lg) / ((ng + K) * np.where(lg > 0, lg, 1))
        return [[None if mask[i, j] else int(round(g[i, j] * 100)) for j in range(C)] for i in range(R)]
    out, summ = {}, {}
    all_teams = sorted(set(done.home) | set(done.away))
    for t in all_teams:
        last = done[(done.home == t) | (done.away == t)].sort_values("date").tail(n_games).game_id
        f, a = s[(s.team == t) & s.game_id.isin(last)], s[(s.opp == t) & s.game_id.isin(last)]
        n = max(len(last), 1)
        summ[t] = dict(n=int(len(last)), f=len(f) / n, a=len(a) / n, fi=float(f.inner.mean()) if len(f) else None,
                       ai=float(a.inner.mean()) if len(a) else None, fdf=f, adf=a)
    cur = {t for t in all_teams if t in set(teams)}
    rk = lambda key, t, desc=True: 1 + sum(1 for u in summ if u in set(games[games.season == games.season.max()].home)
                                           and summ[u][key] is not None and (summ[u][key] > summ[t][key] if desc else summ[u][key] < summ[t][key]))
    for t in cur:
        m = summ[t]
        out[t] = dict(n=m["n"], off=rel(m["fdf"], m["n"]), dfn=rel(m["adf"], m["n"]),
                      f=round(m["f"], 1), a=round(m["a"], 1), fi=round(m["fi"], 3), ai=round(m["ai"], 3),
                      f_rk=rk("f", t), a_rk=rk("a", t), fi_rk=rk("fi", t), ai_rk=rk("ai", t))
    # goalies: save % by zone, last two seasons (shots on goal only)
    seas = sorted(games.season.unique())[-2:]
    sg = shots[shots.game_id.isin(games[games.season.isin(seas)].game_id) & shots.kind.isin(["shot-on-goal", "goal"])
               & (shots.empty_net == 0) & shots.goalie.notna() & shots.dist.notna()].copy()
    ii = (sg.dist <= 22) & (sg.angle <= 50)
    hh = ~ii & (sg.dist <= 45) & (sg.angle <= 35)
    sg["zone"] = np.where(ii, "inner", np.where(hh, "high", "perim"))
    lz = sg.groupby("zone").goal.agg(["size", "sum"])
    gz = {}
    for gid in {int(g) for g in goalie_ids if g is not None}:
        x_ = sg[sg.goalie == gid].groupby("zone").goal.agg(["size", "sum"])
        gz[str(gid)] = {z: dict(n=int(x_.loc[z, "size"]), sv=round(float(1 - x_.loc[z, "sum"] / x_.loc[z, "size"]), 3)) for z in x_.index if x_.loc[z, "size"] >= 20}
    # shooters: each listed player's last `n_shots` unblocked attempts (any team), their zone mix vs his
    # position group's and his usual shot types
    s["zone"] = np.where(s.inner, "inner", np.where(~s.inner & (s.dist <= 45) & (s.angle <= 35), "high", "perim"))
    ros = pd.read_sql("SELECT DISTINCT player_id, pos FROM rosters", __import__("sqlite3").connect(M.DB)).drop_duplicates("player_id")
    pos = dict(zip(ros.player_id.astype("int64"), ros.pos))
    s["grp"] = s.shooter.map(lambda x: None if x != x else "D" if pos.get(int(x)) == "D" else "F")    # a shot with no shooter recorded
    rec = s[s.date >= s.date.max() - pd.Timedelta(days=365)]
    pz = {gp: {z: round(float(v), 3) for z, v in x.zone.value_counts(normalize=True).items()} for gp, x in rec.groupby("grp")}
    by = s[s.shooter.isin({int(x) for x in players})].sort_values(["date", "t"])
    sh = {}
    for pid, x in by.groupby("shooter"):
        x = x.tail(n_shots)
        if len(x) < 8:
            continue
        ty = x.shot_type.value_counts(normalize=True)
        sh[str(int(pid))] = dict(n=int(len(x)), g=int(x.goal.sum()), grp="D" if pos.get(int(pid)) == "D" else "F",
                                 z={z: round(float(v), 3) for z, v in x.zone.value_counts(normalize=True).items()},
                                 ty=[[k, round(float(v), 2)] for k, v in ty.head(2).items()], dist=round(float(x.dist.mean()), 1),
                                 xy=[v for d_, l_, gl in zip(x.d, x.l, x.goal) for v in (int(round(l_)), int(round(d_)), int(gl))])
    return dict(teams=out, goalies=gz, shooters=sh, pos_zone=pz, lg_zone={z: round(float(1 - r["sum"] / r["size"]), 3) for z, r in lz.iterrows()},
                lg_density=[[None if mask[i, j] else round(float(lg[i, j]), 3) for j in range(C)] for i in range(R)])


def pos_defense(fr):
    """Each current team's defense by opposing position, relative to the league (1.10 = allows
    10% more): xG allowed at even strength and on the PK, and actual goals allowed. Recency-
    weighted and pulled toward average; each column is re-centered on this season's teams."""
    now = fr.pos_now
    sched = fr.games[(fr.games.season == fr.games.season.max()) & (fr.games.game_type == 2)]
    now = now[now.index.isin(set(sched.home) | set(sched.away))].copy()
    cols = [c for c in now.columns if c != "games"]
    now[cols] = now[cols] / now[cols].mean()
    out = []
    for t, r in now.sort_index().iterrows():
        out.append(dict(team=t, games=int(r.games), **{f"{p}": dict(ev=round(float(r[f"{p}_ev"]), 3),
                    pk=round(float(r[f"{p}_pk"]), 3), ga=round(float(r[f"{p}_ga"]), 3)) for p in M.POS}))
    return out


TEMPLATE = r"""<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Goals & Shots __DATE__</title>
__FONTS__<style>
__CSS__
.gcard{display:grid;gap:14px}
.ladc{display:inline-block;border:1px solid var(--faint);border-radius:999px;padding:0 8px;font-size:11.5px;color:var(--muted);white-space:nowrap}
.smw{display:grid;gap:26px;grid-template-columns:minmax(0,1fr)}@media(min-width:900px){.smw{grid-template-columns:1fr 1fr}}
.smb{display:grid;gap:10px;align-content:start;min-width:0}.smb h3{margin:0}.smb h3 small{font-family:var(--body);font-weight:400;font-size:13px;color:var(--muted);letter-spacing:0}
.smchips{display:flex;flex-wrap:wrap;gap:5px}
.smsvg{width:100%;max-width:400px;display:block;margin:0 auto;background:var(--sunk);border-radius:10px}
.smsvg .rink path,.smsvg .rink rect,.smsvg .rink circle,.smsvg .rink line,.smsvg .rink polygon{stroke:var(--muted);stroke-width:1;opacity:.55}
.smsvg .rink .gl{stroke:#e5484d;opacity:.6}.smsvg .rink .bl{stroke:#4c8dff;stroke-width:3;opacity:.6}.smsvg .rink .hp{stroke-dasharray:4 3;opacity:.7}.smsvg .rink .dot{fill:var(--muted)}
.smkey{display:flex;flex-wrap:wrap;gap:6px 16px;font-size:12.5px;margin-bottom:12px}.smkey i{display:inline-block;width:11px;height:11px;border-radius:2px;margin-right:5px;vertical-align:-1px}
.smlist{margin:0;padding-left:0;list-style:none;display:grid;gap:4px;font-size:13.5px}.smlist b.up{color:#ff7a5c}.smlist b.dn{color:#5aa2ff}.smlist b{display:inline-block;min-width:44px}
.smd{fill:#fff;fill-opacity:.85;stroke:#0b0f14;stroke-width:.8}.smd.g{fill:#F2C14E;fill-opacity:1}
.smsh summary{cursor:pointer;font-weight:600;font-size:14px}.smsh[open] summary{margin-bottom:8px}
.smzk{display:flex;flex-wrap:wrap;gap:4px 12px;font-size:12px;margin-bottom:6px}.smzk i,.smd-k{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:4px;vertical-align:-1px}
.smd-k.g{background:#F2C14E;border-radius:50%;margin:0 0 0 2px}
.zi{background:#ff6347}.zh{background:#f2a65a}.zp{background:color-mix(in srgb,var(--muted) 55%,transparent)}
.smpl{display:grid;gap:2px}.smp{display:grid;grid-template-columns:minmax(0,1.3fr) minmax(60px,1fr) auto;gap:10px;align-items:center;text-align:left;background:none;border:1px solid transparent;border-radius:7px;padding:6px 8px;color:var(--ink);cursor:pointer;font:inherit;font-size:13px}
.smp:hover{background:var(--sunk)}.smp.fit{background:color-mix(in srgb,#ff6347 9%,transparent)}
.smfit{display:inline-block;border:1px solid color-mix(in srgb,#ff6347 60%,transparent);color:#ff8a6e;border-radius:999px;padding:0 7px;font-size:11.5px;font-weight:600;background:none;font-family:inherit;cursor:pointer;white-space:nowrap}
.smgood{display:flex;flex-wrap:wrap;gap:5px 6px;align-items:center;font-size:13px}.smgood .smfit{font-size:12.5px;padding:2px 9px}.smp[aria-pressed="true"]{border-color:var(--accent);background:var(--sunk)}
.smpn{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.smpn small{color:var(--muted)}
.smzb{display:flex;height:8px;border-radius:4px;overflow:hidden;gap:1px}.smzb i{display:block}
.smps{font-size:12px;color:var(--muted);white-space:nowrap}.smps b{color:var(--ink)}.smps b.up{color:#ff7a5c}.smps b.dn{color:#5aa2ff}
.smsel{font-size:13px;display:flex;flex-wrap:wrap;gap:4px 8px;align-items:center}.smx{background:none;border:1px solid var(--faint);border-radius:999px;color:var(--muted);font:inherit;font-size:12px;padding:1px 9px;cursor:pointer}
@media(max-width:520px){.smp{grid-template-columns:minmax(0,1fr) 70px;}.smps{grid-column:1/-1}}
.smgl{display:flex;flex-wrap:wrap;gap:4px 12px;font-size:13px;align-items:baseline}.smg b{font-weight:600}
.gh{display:flex;flex-wrap:wrap;justify-content:space-between;align-items:baseline;gap:8px}
.sides{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,1080px),1fr));gap:18px}
@media (max-width:1500px){.sides .mkc{display:none}}   /* Best odds already shows the exchange prices */
.sides .lp{display:inline-block;margin-left:6px;font-size:11px;font-weight:600;color:var(--muted);border:1px solid var(--faint);border-radius:4px;padding:0 4px;vertical-align:1px;white-space:nowrap}
.sides td.kp .kpv{white-space:nowrap}.sides td.kp .kpv small,.sides .fo{font-size:12px;font-weight:500;color:var(--muted);margin-left:5px}
.sides td.kp .pbar{margin-top:4px;min-width:0;width:100%}
@media (max-width:640px){.sides .lp,.sides td.kp .dk{display:none}}
.sidehead{display:flex;justify-content:space-between;align-items:baseline;gap:8px;border-bottom:1px solid var(--faint);padding-bottom:6px}
.sidehead .code{font-size:24px}
.pp{font-size:11px;font-weight:600;color:var(--accent);border:1px solid currentColor;border-radius:4px;padding:0 4px;margin-left:6px}
.newp{font-size:11px;color:var(--muted);margin-left:6px}
.src{margin:6px 0 2px}
.sides .pbar{min-width:60px}
.gjw{position:sticky;top:var(--tbh,0px);z-index:19;display:flex;align-items:center;gap:4px;background:var(--bg)}
.gjump{flex:1;min-width:0;display:flex;gap:6px;overflow-x:auto;padding:8px 0;scrollbar-width:none;-webkit-overflow-scrolling:touch;scroll-behavior:smooth}
.gjw.ml .gjump{-webkit-mask-image:linear-gradient(90deg,transparent,#000 28px);mask-image:linear-gradient(90deg,transparent,#000 28px)}
.gjw.mr .gjump{-webkit-mask-image:linear-gradient(90deg,#000 calc(100% - 28px),transparent);mask-image:linear-gradient(90deg,#000 calc(100% - 28px),transparent)}
.gjw.ml.mr .gjump{-webkit-mask-image:linear-gradient(90deg,transparent,#000 28px,#000 calc(100% - 28px),transparent);mask-image:linear-gradient(90deg,transparent,#000 28px,#000 calc(100% - 28px),transparent)}
.gjarr{display:none}
@media (hover:hover) and (pointer:fine){.gjarr{display:grid;place-items:center;flex:0 0 30px;height:30px;border:1px solid var(--faint);border-radius:999px;background:var(--sunk);color:var(--ink);font-size:18px;line-height:1;cursor:pointer;padding:0}
.gjarr:hover:not(:disabled){border-color:var(--accent)}.gjarr:disabled{opacity:.3;cursor:default}}
.gjump::-webkit-scrollbar{display:none}
.gjump a{flex:0 0 auto;display:flex;gap:6px;align-items:baseline;border:1px solid var(--faint);border-radius:999px;padding:5px 11px;color:var(--ink);text-decoration:none;font-family:var(--display);font-weight:600;font-size:15px;letter-spacing:.02em}
.gjump a small{font-family:var(--body);font-weight:400;font-size:11.5px;color:var(--muted);letter-spacing:0}
.gjump a:hover{border-color:var(--accent)}
.gcard{scroll-margin-top:calc(var(--tbh,0px) + 56px)}
.urtop{color:var(--accent);font-weight:700}
.urup{color:var(--good)}
.urdn{color:var(--muted)}
.mlrow{display:flex;flex-wrap:wrap;gap:8px}
.betgrid{display:grid;gap:8px}
.betrow{display:grid;grid-template-columns:86px 1fr;gap:8px;align-items:center}
.bets{display:flex;flex-wrap:wrap;gap:6px}
.bet{display:inline-flex;align-items:baseline;gap:6px;border:1px solid var(--faint);background:var(--sunk);color:var(--ink);border-radius:8px;padding:6px 10px;cursor:pointer;font:inherit;font-size:13.5px}
.bet b{font-family:var(--display);font-size:17px;font-weight:700}
.bet small{color:var(--muted);font-size:12px}
.bet i{font-style:normal;color:var(--accent);font-weight:700;margin-left:2px}
.bet.on{border-color:var(--accent);background:rgba(54,211,153,.12)}
.bet:disabled{cursor:default}
.bet:disabled i{display:none}
@media (max-width:520px){.betrow{grid-template-columns:1fr;gap:4px}.bets{display:grid;grid-template-columns:1fr 1fr;gap:6px}.bet{justify-content:space-between;gap:4px;padding:5px 8px;font-size:12.5px;min-width:0}.bet b{font-size:15px}.bet small{display:none}.bet span{white-space:nowrap}}
.mlc{display:grid;gap:1px;background:var(--sunk);border-radius:7px;padding:7px 12px;min-width:104px}
.mlc b{font-family:var(--display);font-size:20px;font-weight:700;line-height:1.1}
.mlc .muted{font-size:12.5px}
tr.pick td{background:color-mix(in srgb,var(--accent) 9%,transparent)}
tr.pick td:first-child,tr.best td:first-child{box-shadow:inset 3px 0 0 var(--accent)}
.star{color:var(--accent);margin-right:5px;cursor:default}
.why{display:block;font-size:11.5px;font-weight:400;color:var(--muted);margin-top:1px;white-space:normal;line-height:1.3}
.picks-key{display:flex;flex-wrap:wrap;gap:14px;align-items:center;font-size:12.5px;color:var(--muted);margin-bottom:16px}
.picks-key b{color:var(--ink);font-weight:600}
.sides .t td,.sides .t th{padding-left:6px;padding-right:6px}
.h2h{white-space:nowrap;cursor:default}
.tg3{display:inline-block;margin-left:6px;padding:0 6px;border-radius:9px;font-size:10.5px;font-weight:700;line-height:16px;color:var(--good);border:1px solid color-mix(in srgb,var(--good) 55%,transparent);background:color-mix(in srgb,var(--good) 10%,transparent);white-space:nowrap;cursor:help;vertical-align:1px}
@media (max-width:640px){.sgc td.pay::after{content:none}.sgc td.pay b::after{content:" fair";font-weight:400;color:var(--muted)}}
.sgl{display:inline-block;font-size:12px;font-weight:700;color:var(--good);border:1px solid color-mix(in srgb,var(--good) 45%,transparent);border-radius:999px;padding:1px 8px;white-space:nowrap}
.exp3{font-family:var(--body);font-size:10.5px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;color:#F2C14E;border:1px solid currentColor;border-radius:4px;padding:1px 5px;margin-left:6px;vertical-align:3px}
.h2h b{font-weight:600}
.h2h .hot{color:var(--good)}
.mu{font-size:11px;font-weight:600;border-radius:4px;padding:0 4px;margin-left:6px;border:1px solid currentColor}
.mu.soft{color:var(--good)} .mu.tough{color:var(--bad)}
.pdline{margin:2px 0 4px}
.lchg{display:inline-block;margin-left:6px;border:1px solid var(--faint);border-radius:999px;padding:0 7px;font-size:11px;font-weight:600;white-space:nowrap;vertical-align:1px}
.lchg.up{color:var(--good);border-color:color-mix(in srgb,var(--good) 55%,transparent)}.lchg.dn{color:#e5a03a;border-color:color-mix(in srgb,#e5a03a 55%,transparent)}
.lchg.in{color:#7fb2ff;border-color:color-mix(in srgb,#7fb2ff 55%,transparent)}
.lchg.dry{color:var(--muted);border-color:var(--faint)}
.lchgl{margin:2px 0 4px}.lchgl b{font-weight:600}
.pdline b{font-weight:600}
.pdline .up{color:var(--good)} .pdline .dn{color:var(--bad)}
.hm{font-weight:500}
.hm.g1{background:color-mix(in srgb,var(--g1) 12%,transparent)} .hm.g2{background:color-mix(in srgb,var(--g2) 22%,transparent)} .hm.g3{background:color-mix(in srgb,var(--g3) 34%,transparent)}
.hm.r1{background:color-mix(in srgb,var(--r1) 12%,transparent)} .hm.r2{background:color-mix(in srgb,var(--r2) 22%,transparent)} .hm.r3{background:color-mix(in srgb,var(--r3) 34%,transparent)}
.t th.sort{cursor:pointer;text-decoration:underline dotted;text-underline-offset:3px}
.t th.gs{text-align:center;border-bottom:0;padding-bottom:0}
</style>
<div class="wrap">
  <header>
    <div><div class="eyebrow" id="sub"></div><h1>Goals &amp; Shots</h1></div>
    <div class="tabs" role="tablist" aria-label="View">
      <button role="tab" data-view="games">Games</button>
      <button role="tab" data-view="plays">Plays</button>
      <button role="tab" data-view="board">All players</button>
      <button role="tab" data-view="posdef">Defense vs position</button>
      <button role="tab" data-view="goalies">Goalies</button>
    </div>
  </header>
  <main id="main"></main>
  <footer>Chance of scoring at least one goal (regulation or overtime; shootouts don't count), from expected ice time at even strength and on the power play, each player's shot quality (expected goals per 60), finishing skill (heavily regressed), the opponent's defense, the opposing starting goalie, power-play opportunities, home ice and back-to-backs. No betting lines are used. Fair odds are no-vig. Lineups come from the official NHL game roster once the dressed lineup posts (healthy scratches removed; call-ups added), otherwise from the latest reported line combinations (Daily Faceoff), otherwise from expected ice time; each team shows its source. L1–L4 are forward lines, D1–D3 defense pairs, PP1/PP2 power-play units. Backtest (2024–25, 2025–26): calibrated within about 1 point from 5% to 35%, slightly high above 35%. 1st goal = chance to score the game's first goal (regulation or overtime): the player's share of all dressed skaters' expected goals times the chance anyone scores; backtest 2024–26: calibrated (2.77% predicted vs 2.78% actual per skater). H2H G-A-P = goals-assists-points against tonight's opponent since 2022–23 (regular season and playoffs; hover for the last 5 meetings; (n) = games played; green = at least 0.25 points per game above his rate against everyone, in 3+ games). It is shown for reference only: in backtests, head-to-head history did not predict goals beyond what the model already uses.</footer>
</div>
<div id="tip" hidden></div>
<script type="application/json" id="data">__DATA__</script>
<script>
__JS__</script>
"""
# The page script lives in hockey/page.js (owner 2026-10-23: scripts out of Python strings, so they can be
# syntax-checked and quotes / backslashes need no care); pasted in at build so each dated page stays self-contained.
TEMPLATE = TEMPLATE.replace("__JS__", open(os.path.join(HERE, "page.js"), encoding="utf-8").read())


if __name__ == "__main__":
    main()
