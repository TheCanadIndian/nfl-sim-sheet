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
                              so=[round(float(getattr(r, f"sog_o{k}")), 3) for k in ("05", "15", "25", "35", "45")] if "sog" in x and pd.notna(r.sog) else None)
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
    s["grp"] = s.shooter.map(lambda x: "D" if pos.get(int(x)) == "D" else "F" if x == x else None)
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
.exp3{font-family:var(--body);font-size:10.5px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;color:#F2C14E;border:1px solid currentColor;border-radius:4px;padding:1px 5px;margin-left:6px;vertical-align:3px}
.h2h b{font-weight:600}
.h2h .hot{color:var(--good)}
.mu{font-size:11px;font-weight:600;border-radius:4px;padding:0 4px;margin-left:6px;border:1px solid currentColor}
.mu.soft{color:var(--good)} .mu.tough{color:var(--bad)}
.pdline{margin:2px 0 4px}
.lchg{display:inline-block;margin-left:6px;border:1px solid var(--faint);border-radius:999px;padding:0 7px;font-size:11px;font-weight:600;white-space:nowrap;vertical-align:1px}
.lchg.up{color:var(--good);border-color:color-mix(in srgb,var(--good) 55%,transparent)}.lchg.dn{color:#e5a03a;border-color:color-mix(in srgb,#e5a03a 55%,transparent)}
.lchg.in{color:#7fb2ff;border-color:color-mix(in srgb,#7fb2ff 55%,transparent)}
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
const D = JSON.parse(document.getElementById('data').textContent);
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const pct = p => (p*100).toFixed(1) + '%';
const odds = o => (o > 0 ? '+' : '') + o;
if (location.hash === '#books') location.replace('markets.html#books');          // Book odds moved to the Markets page
const state = {view: ['#board', '#posdef', '#goalies', '#shots', '#plays'].includes(location.hash) ? location.hash.slice(1) : 'games', sort: 'team', dir: 1};
// Highlights: tonight's top 5 by goal chance (backtest 2024-26: scored 38%, average skater 15%)
// and the best option in each game (38%). The model's probability already includes every factor
// that tested as useful, so the ranking is the selection rule; reasons explain it.
const ALLP = D.games.flatMap(g => [g.away, g.home].flatMap(s => s.players.map(p => ({p, g: g.id}))));
const TOP5 = new Set(ALLP.slice().sort((a, b) => b.p.p - a.p.p).slice(0, 5).map(x => x.p));
const BEST = new Set(D.games.map(g => [...g.away.players, ...g.home.players].sort((a, b) => b.p - a.p)[0]).filter(Boolean));
const rowCls = p => TOP5.has(p) ? 'pick' : BEST.has(p) ? 'best' : '';
function pickMark(p){
  if (TOP5.has(p)) {
    const tip = `<b>Top 5 tonight</b><br>${(p.p*100).toFixed(1)}% to score${p.why && p.why.length ? '<br>' + p.why.map(esc).join(' · ') : ''}<br><span style="opacity:.8">In backtests the night's top 5 scored 38% of the time (average skater 15%).</span>`;
    return `<span class="star" tabindex="0" data-tip="${esc(tip)}" aria-label="Top 5 tonight">★</span>`;
  }
  return '';
}
const whyLine = p => TOP5.has(p) && p.why && p.why.length ? `<span class="why">${p.why.map(esc).join(' · ')}</span>` : '';
const PD = Object.fromEntries((D.posdef || []).map(r => [r.team, r]));
const PNAME = {C: 'C', L: 'LW', R: 'RW', D: 'D'};
// degree shades: 1 slight, 2 moderate, 3 strong (green = good for the shooter, red = bad)
const hshade = v => v >= 1.20 ? 'g3' : v >= 1.12 ? 'g2' : v >= 1.05 ? 'g1' : v <= 1/1.2 ? 'r3' : v <= 1/1.12 ? 'r2' : v <= 1/1.05 ? 'r1' : '';
const rel = v => { const n = Math.round((v - 1) * 100); return (n >= 0 ? '+' : '−') + Math.abs(n) + '%'; };
// Matchup tag: opponent's even-strength xG allowed to the player's position, 10%+ off average.
// Triple green: green head-to-head (3+ meetings, +0.25 pts/game over his overall rate), opposing goalie GSAA -1.5 or worse
// (this season once he has 3 games, else last season), and the opponent allowing 5%+ more expected goals to his position.
// Backtest 2024-26: all three together scored 12-20% above the model's chance (~340 player-games a season; not proven);
// one or two greens: nothing consistent. Experimental and tracked (Parlays results).
function gGreen(o){ const g = o && o.gstats; if (!g) return false; const x = g.now && g.now.gp >= 3 ? g.now : (g.last || g.now); return !!x && x.gsaa != null && x.gsaa <= -1.5; }
function tripleGreen(p, oppSide){
  const h = p.h2h, hot = !!(h && h.gp >= 3 && h.pts / h.gp >= h.ppg_all + .25);
  const r = PD[oppSide.team], k = PNAME[p.pos] ? p.pos : 'C', dv = !!(r && r[k] && r[k].ev >= 1.05);
  return hot && dv && gGreen(oppSide);
}
const TG3_TIP = 'Triple green: strong head-to-head vs this opponent, a struggling opposing goalie (GSAA -1.5 or worse) and a defense that allows more to his position. In 2024-26 backtests players with all three scored 12-20% more often than our chance (small sample: experimental, tracked on the Parlays results tab).';
function mtag(pos, opp){
  const r = PD[opp], k = PNAME[pos] ? pos : 'C';
  if (!r) return '';
  const v = r[k].ev;
  if (v >= 1.10) return `<span class="mu soft ${hshade(v)}" title="${esc(opp)} allows ${rel(v)} expected goals to ${PNAME[k]} at even strength vs league average">soft vs ${PNAME[k]} ${rel(v)}</span>`;
  if (v <= 0.90) return `<span class="mu tough ${hshade(v)}" title="${esc(opp)} allows ${rel(v)} expected goals to ${PNAME[k]} at even strength vs league average">tough vs ${PNAME[k]} ${rel(v)}</span>`;
  return '';
}
// Head-to-head vs tonight's opponent: games, goals-assists-points; tooltip lists the last 5 meetings.
function h2hCell(h, opp){
  if (!h || !h.gp) return '<span class="muted small">—</span>';
  const ppg = h.pts / h.gp, hot = h.gp >= 3 && ppg >= h.ppg_all + 0.25;
  const tip = `<b>vs ${esc(opp)} since 2022–23</b><br>${h.gp} GP · ${h.g} G · ${h.a} A · ${h.pts} P (${ppg.toFixed(2)} per game; ${h.ppg_all.toFixed(2)} vs everyone)<br>` +
    h.last.map(([d, g, a, po]) => `${esc(d)}${po ? ' (playoffs)' : ''}: ${g} G, ${a} A`).join('<br>');
  return `<span class="h2h" tabindex="0" data-tip="${esc(tip)}"><b class="${hot ? 'hot' : ''}">${h.g}-${h.a}-${h.pts}</b> <span class="small muted">(${h.gp})</span></span>`;
}
// Opposing goalie: save % and GSAA (goals saved above an average save %), this season and last.
const sv3 = v => v == null ? '–' : v.toFixed(3).replace(/^0/, '');
const sgn = v => v == null ? '–' : (v >= 0 ? '+' : '−') + Math.abs(v).toFixed(1);
function gcell(x, lab){ return x ? `${lab}: <b>${sv3(x.sv)}</b> SV% · GSAA <b class="${x.gsaa >= 8 ? 'r3' : x.gsaa >= 4 ? 'r2' : x.gsaa >= 1.5 ? 'r1' : x.gsaa <= -8 ? 'g3' : x.gsaa <= -4 ? 'g2' : x.gsaa <= -1.5 ? 'g1' : ''}">${sgn(x.gsaa)}</b> <span class="muted">(${x.gp} GP)</span>` : `${lab}: <span class="muted">no games</span>`; }
function goalieLine(opp){
  const g = opp.gstats; if (!g) return '';
  return `<div class="small muted pdline">${esc(opp.goalie)}: ${gcell(g.now, 'this season')} · ${gcell(g.last, 'last season')}</div>`;
}
function pdline(opp){
  const r = PD[opp];
  if (!r) return '';
  const cell = k => { const v = r[k].ev; return `${PNAME[k]} <b class="${hshade(v)}">${rel(v)}</b>`; };
  return `<div class="small muted pdline">${esc(opp)} defense vs position (xG allowed, even strength): ${['C','L','R','D'].map(cell).join(' · ')}</div>`;
}
document.getElementById('sub').textContent = `${D.games.length} games · ${D.date} · built ${D.generated}`;
const bar = (p, max) => `<div class="pbar" role="img" aria-label="${pct(p)}"><i style="width:${Math.min(p/max,1)*100}%"></i></div>`;
// build-your-own parlay (slip.js): the goal model treats players' goals as independent, so goal legs multiply
function nhlSlip(p, team){
  if (!window.SLIP) return '';
  const g = D.games.find(x => x.home.team === team || x.away.team === team); if (!g) return '';
  const lam = -Math.log(1 - Math.min(p.p, .999)), p2 = 1 - Math.exp(-lam) * (1 + lam);
  const leg = (n, prob) => ({id: `nhl|${g.id}|${p.id}|goal${n}`, sport: 'nhl', g: g.id, glab: `${g.away.team} @ ${g.home.team}`, lab: n === 1 ? `${p.name} to score` : `${p.name} 2+ goals`, p: prob, side: 'over', stat: 'goals', line: n - .5});
  return SLIP.btn(leg(1, p.p), 'ATG') + (p2 >= .02 ? SLIP.btn(leg(2, p2), '2+') : '');
}
// usage rank (display only): rank in the game by live expected goals x usage multiplier; the model's own rank for comparison
const MRANK = {};
D.games.forEach(g => [...g.away.players, ...g.home.players].sort((a, b) => b.p - a.p).forEach((p, i) => { MRANK[g.id + '|' + p.id] = i + 1; }));
const UTIP = "Rank in this game after a usage adjustment: shot share while on the ice, shift length and count, 6-on-5 and late-game ice time (from this season's shift charts). Backtest 2025-26: the usage top 3 caught 1.045 scorers per game vs 1.032 for the plain ranking. P(goal) and fair odds are unchanged.";
function urCell(p, gid){
  if (p.ur == null) return '<span class="muted">–</span>';
  const m = MRANK[gid + '|' + p.id], d = m ? m - p.ur : 0;
  return `<span class="${p.ur <= 3 ? 'urtop' : ''}" title="Usage-adjusted rank ${p.ur} (model rank ${m}); usage multiplier ${p.um}">#${p.ur}</span>${d ? ` <span class="small ${d > 0 ? 'urup' : 'urdn'}">${d > 0 ? '▲' : '▼'}${Math.abs(d)}</span>` : ''}`;
}
// lineup changes vs the team's previous game (lineup_changes in project.py)
const LN = l => l ? l.replace('F', 'L') : '';
function chgChips(p){
  if (!p.chg) return '';
  const cut = p.cut ? ` · −${Math.round(p.cut * 100)}%` : '', cutT = p.cut ? ` Our goal chance is cut ${Math.round(p.cut * 100)}% for this (early 2026-27 evidence: demoted players scored below our chance; not yet proven).` : '';
  return p.chg.map(([k, b]) => k === 'in' ? (p.new ? '' : `<span class="lchg in" title="Didn’t play last game">back in</span>`)
    : k === 'up' ? `<span class="lchg up" title="Moved up: ${LN(b)} last game, ${LN(p.line)} tonight">▲ ${LN(b)} → ${LN(p.line)}</span>`
    : k === 'down' ? `<span class="lchg dn" title="Moved down: ${LN(b)} last game, ${LN(p.line)} tonight.${p.pos !== 'D' ? cutT : ''}">▼ ${LN(b)} → ${LN(p.line)}${p.pos !== 'D' ? cut : ''}</span>`
    : !b ? `<span class="lchg up" title="Not on a power-play unit last game">▲ new ${esc(p.pp)}</span>`
    : !p.pp ? `<span class="lchg dn" title="Was on ${esc(b)} last game.${cutT}">▼ off ${esc(b)}${cut}</span>`
    : `<span class="lchg ${p.pp < b ? 'up' : 'dn'}" title="${esc(b)} last game, ${esc(p.pp)} tonight.${p.pp < b ? '' : cutT}">${p.pp < b ? '▲' : '▼'} ${esc(b)} → ${esc(p.pp)}${p.pp < b ? '' : cut}</span>`).join('');
}
function chgLine(s){
  const c = s.lchg; if (!c) return '';
  const n = (s.players || []).filter(p => p.chg && p.chg.some(([k]) => k !== 'in' || !p.new)).length + (s.players || []).filter(p => p.new).length;
  const out = c.out.length ? `<b>Out:</b> ${c.out.map(o => `${esc(o.name)}${o.line ? ` <span class="muted">(${LN(o.line)})</span>` : ''}`).join(', ')}` : 'nobody out';
  return `<div class="small lchgl"><span class="muted">Since last game (${esc(c.prev)}):</span> ${n ? `${n} change${n === 1 ? '' : 's'} marked` : 'same lineup'}${c.known ? '' : ' <span class="muted">(no saved lines for that game; only who’s in and out)</span>'} · ${out}</div>`;
}
function side(s, opp, gid){
  const max = .5, mk = window.MKT && MKT.has(), bo = !!(window.MKT && MKT.hasProps && MKT.hasProps(gid));
  return `<div><div class="sidehead"><span class="code">${esc(s.team)}</span><span class="small muted">${s.exp_goals} expected goals · vs ${esc(opp.goalie)}${opp.goalie_status ? ` (${esc(opp.goalie_status)})` : ''}</span></div>
    ${s.lineup ? `<div class="small muted src">${esc(s.lineup)}</div>` : ''}${chgLine(s)}${goalieLine(opp)}${pdline(opp.team)}
    <div class="tw"><table class="t pt"><thead><tr><th class="l">Player</th><th style="min-width:96px" title="Chance to score, and the fair (no-vig) odds">Goal · fair</th><th title="${UTIP}">Usage</th><th title="Chance to score the game's first goal, and its fair odds">1st goal · fair</th><th>TOI</th><th class="l" title="Expected shots on goal, and the chance of 2+ and 3+ (shots model: calibrated within ~1 pt on 2024-26)">Shots</th><th class="l" title="Goals-assists-points vs this opponent since 2022-23">H2H G-A-P</th>${bo ? '<th class="l pcd" title="Best anytime-goal price at your sportsbooks (choose them under the game bets). Green = our chance beats it by 5%+">Best odds</th>' : ''}${mk ? '<th class="l mkc" title="Kalshi / Polymarket anytime-goal price, our edge, and dollars to buy YES / NO within 3¢">Market</th>' : ''}</tr></thead><tbody>
    ${gamePlayers(s.players, gid).map(p => `<tr class="${rowCls(p)}"><td class="l name">${pickMark(p)}${esc(p.name)}${nhlSlip(p, s.team)}${window.MKT && MKT.flag ? MKT.flag(gid, p.name) + (MKT.lcFlag ? MKT.lcFlag(gid, p.name) : '') : ''}${p.pp ? `<span class="pp">${p.pp}</span>` : ''}${chgChips(p)}<span class="lp">${esc(p.line ? p.line.replace('F', 'L') + ' · ' + p.pos : p.pos)}</span><span class="mmeta">${esc(p.line ? p.line.replace('F', 'L') + ' · ' + p.pos : p.pos)} · fair ${odds(p.fair)}${p.ur ? ` · usage #${p.ur}${(() => { const m = MRANK[gid + '|' + p.id], d = m ? m - p.ur : 0; return d ? ` ${d > 0 ? '▲' : '▼'}${Math.abs(d)}` : ''; })()}` : ''}${p.pf != null ? ` · 1st goal ${pct(p.pf)}` : ''}</span>${bo ? `<span class="pcm">${MKT.prop(gid, p.name, 'goal')}</span>` : ''}${p.new ? '<span class="newp">no NHL history</span>' : ''}${tripleGreen(p, opp) ? `<span class="tg3" title="${esc(TG3_TIP)}">Triple green</span>` : ''}${mtag(p.pos, opp.team)}${whyLine(p)}</td><td class="big kp"><div class="kpv">${pct(p.p)}<small class="dk">${odds(p.fair)}</small></div><div class="dk">${bar(p.p, max)}</div>${p.sog != null ? `<small class="kps">${p.sog.toFixed(1)} shots<br>3+ ${pct(p.so[2])}</small>` : ''}</td><td>${urCell(p, gid)}</td><td style="white-space:nowrap">${p.pf != null ? `${pct(p.pf)}<span class="fo">${p.fair_first != null ? odds(p.fair_first) : ''}</span>` : '–'}</td><td class="muted">${p.toi}</td><td class="l">${p.sog != null ? `<b>${p.sog.toFixed(1)}</b> <small class="muted">2+ ${pct(p.so[1])} · 3+ ${pct(p.so[2])}</small>` : '<span class="muted">–</span>'}</td><td class="l">${h2hCell(p.h2h, opp.team)}</td>${bo ? `<td class="l pcd">${MKT.prop(gid, p.name, 'goal')}</td>` : ''}${mk ? `<td class="l mkc">${MKT.cell(gid, p.id, p.name, 'goal')}</td>` : ''}</tr>`).join('')}
    ${state.bval && !gamePlayers(s.players, gid).length ? '<tr><td colspan="9" class="l name muted small">No value plays on this team at your books.</td></tr>' : ''}
    </tbody></table></div></div>`;
}
function picksKey(){
  const top = ALLP.filter(x => TOP5.has(x.p)).sort((a, b) => b.p.p - a.p.p).map(x => `<b>${esc(x.p.name)}</b> ${(x.p.p*100).toFixed(0)}%`);
  return `<div class="picks-key"><span><span class="star">★</span>Top 5 tonight: ${top.join(' · ')}</span><span><span style="display:inline-block;width:3px;height:12px;background:var(--accent);vertical-align:-1px;margin-right:6px"></span>best option in each game</span></div>`;
}
// Moneyline / puck line / total from the game simulation (gamesim.py).
function mlBlock(g){
  const m = g.ml; if (!m) return '';
  const o = p => p == null ? '–' : odds(Math.abs(p - .5) < 1e-9 ? -100 : (p >= .5 ? -Math.round(100 * p / (1 - p)) : Math.round(100 * (1 - p) / p)));
  const gl = `${g.away.team} @ ${g.home.team}`;
  // each option is one tappable chip that adds it to the parlay slip (game legs multiply with goal legs)
  const BK = (...a) => window.MKT && MKT.bk ? MKT.bk(g.id, ...a) : '';
  const bkOf = {ml_away: ['ml', 'away'], ml_home: ['ml', 'home'], pl_away_m: ['spread', 'away', 1.5], pl_home_m: ['spread', 'home', -1.5],
                pl_away_p: ['spread', 'away', -1.5], pl_home_p: ['spread', 'home', 1.5]};      // spread line = home team's handicap
  const chip = (key, lab, p, full) => {
    const bk = key.startsWith('over') ? BK('total', 'over', key.slice(4), p) : key.startsWith('under') ? BK('total', 'under', key.slice(5), p)
             : bkOf[key] ? BK(bkOf[key][0], bkOf[key][1], bkOf[key][2], p) : '';
    const L = {id: `nhl|${g.id}|${key}`, sport: 'nhl', g: g.id, glab: gl, lab: `${full || lab} (${gl})`, p, side: 'over', stat: key};
    const on = window.SLIP && SLIP.has(L.id);
    return `<button type="button" class="bet${on ? ' on' : ''}"${window.SLIP ? ` data-slip="${esc(JSON.stringify(L))}" title="${on ? 'Remove from' : 'Add to'} my parlay"` : ' disabled'}><span>${esc(lab)}</span><b>${pct(p)}</b><small>${o(p)}</small><i>${on ? '✓' : '+'}</i>${bk}</button>`;
  };
  const row = (lab, chips) => `<div class="betrow"><span class="eyebrow">${lab}</span><div class="bets">${chips.join('')}</div></div>`;
  const A = g.away.team, H = g.home.team;
  const rows = [row('Moneyline', [chip('ml_away', A, m.away), chip('ml_home', H, m.home)]),
                row('Puck line', [chip('pl_away_m', A + ' −1.5', m.away_pl), chip('pl_home_m', H + ' −1.5', m.home_pl)]
                  .concat(m.away_pl_plus != null ? [chip('pl_away_p', A + ' +1.5', m.away_pl_plus), chip('pl_home_p', H + ' +1.5', m.home_pl_plus)] : []))];
  for (const k of ['5.5', '6.5']) if (m.totals[k] != null)
    rows.push(row('Total ' + k, [chip('over' + k, 'Over', m.totals[k], 'Over ' + k), chip('under' + k, 'Under', 1 - m.totals[k], 'Under ' + k)]));
  return `<div class="betgrid">${rows.join('')}<div class="small muted">Projected total ${m.total.toFixed(1)} goals · overtime ${pct(m.ot)}</div>${window.MKT && MKT.bkNote ? MKT.bkNote(g.id) : ''}</div>`;
}
// Anytime-goal parlays priced at the viewer's books: legs only from our top 3 per game (usage-adjusted rank, the one slice
// that has held up vs Kalshi), one leg per game, games not started. Ranked by payout vs our fair odds.
function atgParlays(){
  if (!(window.MKT && MKT.propBest)) return '';
  const now = Date.now(), dec = o => o > 0 ? 1 + o / 100 : 1 + 100 / -o, am = o => (o > 0 ? '+' : '') + o;
  const legs = [];
  for (const g of D.games){
    if (g.start_utc && new Date(g.start_utc) <= now) continue;
    for (const side of [g.away, g.home]) for (const p of side.players){
      if (!(p.ur && p.ur <= 3) || p.p < .30) continue;              // our top 3, and 30%+ (shorter droughts, same calibration)
      const b = MKT.propBest(g.id, p.name, 'goal'); if (!b) continue;
      const m = MKT.propMarket ? MKT.propMarket(g.id, p.name, 'goal') : null;
      // 50/50 ours and the exchanges (Kalshi 2026: 3,822 graded legs, top-3 legs +10%); 20/80 with Pinnacle when pulled (2025-26 closing props)
      const q = MKT.blend ? MKT.blend(g.id, p.name, 'goal', p.p) : m != null ? .5 * p.p + .5 * m : p.p;
      const ev = q * dec(b.o) - 1;
      if (ev <= 0) continue;                                         // every leg needs its own edge
      legs.push({g, p, team: side.team, b, q, m, ev});
    }
  }
  if (legs.length < 2) return '';
  const byGame = {}; legs.forEach(L => (byGame[L.g.id] = byGame[L.g.id] || []).push(L));
  const gids = Object.keys(byGame), combos = [];
  const walk = (i, pick, k) => {
    if (pick.length === k){ const p = pick.reduce((a, L) => a * L.q, 1), d = pick.reduce((a, L) => a * dec(L.b.o), 1); combos.push({legs: pick, p, d, ev: p * d - 1}); return; }
    for (let j = i; j < gids.length; j++) for (const L of byGame[gids[j]]) walk(j + 1, [...pick, L], k);
  };
  walk(0, [], 2); if (gids.length >= 3) walk(0, [], 3);
  if (!combos.length) return '';
  const top = k => combos.filter(c => c.legs.length === k).sort((a, b) => b.ev - a.ev).slice(0, 4);
  const legSlip = L => ({id: `nhl|${L.g.id}|${L.p.id}|goal1`, sport: 'nhl', g: L.g.id, glab: `${L.g.away.team} @ ${L.g.home.team}`, lab: `${L.p.name} to score`, p: L.p.p, side: 'over', stat: 'goals', line: .5});
  const row = c => `<tr><td class="l">${c.legs.map(L => `<b>${esc(L.p.name)}</b> <span class="small muted" title="Our chance ${pct(L.p.p)}${L.m != null ? ', exchanges ' + pct(L.m) + ', blended ' + pct(L.q) : ' (no exchange price to blend)'}">${esc(L.team)} · ${pct(L.q)} · ${L.b.u ? `<a href="${esc(L.b.u)}" target="_blank" rel="noopener">${am(L.b.o)} ${esc(L.b.name)} ↗</a>` : `${am(L.b.o)} ${esc(L.b.name)}`}${L.b.mine ? '' : ' (not at your books)'}</span>`).join('<br>')}</td>
    <td class="big">${pct(c.p)}</td><td class="pay">${c.d.toFixed(1)}x</td><td class="edge ${c.ev >= .1 ? 'pos-d' : c.ev < 0 ? 'neg-d' : ''}" style="font-weight:700">${c.ev >= 0 ? '+' : ''}${(c.ev * 100).toFixed(0)}%</td>
    <td class="l add">${window.SLIP ? SLIP.allBtn(c.legs.map(legSlip), '+ add parlay') : ''}</td></tr>`;
  // round robin: the 3 best-edge legs from different games, bet as all three 2-leg parlays (backtest 2024-26: longest
  // dry spell 7-9 nights vs 20-23 for a single nightly 2-leg, same long-run return)
  const rr = [], seen = new Set();
  for (const L of legs.slice().sort((a, b) => b.ev - a.ev)) if (!seen.has(L.g.id) && rr.length < 3){ seen.add(L.g.id); rr.push(L); }
  const rrPairs = rr.length === 3 ? [[rr[0], rr[1]], [rr[0], rr[2]], [rr[1], rr[2]]].map(pk => {
    const p = pk[0].q * pk[1].q, d = dec(pk[0].b.o) * dec(pk[1].b.o); return {legs: pk, p, d, ev: p * d - 1}; }) : [];
  const rrBlock = rrPairs.length ? `<div class="rr"><div class="eyebrow">Round robin · fewer dry spells</div>
    <p class="small muted" style="margin:4px 0 8px">Bet all three 2-leg parlays below (same stake each). At least one hits whenever 2 of the 3 score: ${pct(rr[0].q * rr[1].q + rr[0].q * rr[2].q + rr[1].q * rr[2].q - 2 * rr[0].q * rr[1].q * rr[2].q)} by our blended chances; in the 2024-26 backtest the longest stretch with no winner was 7-9 nights, vs 20-23 for one 2-leg a night.</p>
    <div class="tw"><table class="t pairs atgp"><thead><tr><th class="l">2-leg</th><th>Chance</th><th>Pays</th><th>Edge</th><th class="l">My parlay</th></tr></thead><tbody>${rrPairs.map(row).join('')}</tbody></table></div></div>` : '';
  if (window.MKT && MKT.reg){                                                    // freeze mode: record for the Results page
    const lg = L => ({gid: String(L.g.id), start: L.g.start_utc, kind: 'goal', side: 'o', line: .5, name: MKT.norm(L.p.name), lab: `${L.p.name} to score`, p: +L.q.toFixed(4), price: L.b.o, book: L.b.k});
    top(2).forEach(c => MKT.reg('Anytime-goal parlays', 'Best 2-leg', c.legs.map(lg), c.p, c.d));
    top(3).forEach(c => MKT.reg('Anytime-goal parlays', 'Best 3-leg', c.legs.map(lg), c.p, c.d));
    rrPairs.forEach(c => MKT.reg('Anytime-goal parlays', 'Round robin', c.legs.map(lg), c.p, c.d));
  }
  const mine = MKT.myBooks();
  return `<section class="panel"><div class="phead"><h2>Anytime-goal parlays at your books</h2><span class="small muted">${mine.length ? 'Priced at ' + esc(mine.join(', ')) : 'Priced at the best book (choose yours under any game’s bets)'}</span></div>
    <details class="how"${innerWidth > 640 ? ' open' : ''}><summary>How these are picked</summary><p class="note">Legs: our top 3 scorers in each game (usage-adjusted rank; on Kalshi those roughly broke even while everyone else lost) who are 30%+ to score. Each leg's chance is half our number, half the exchanges' price: on Kalshi this season (3,822 graded legs) that blend was more accurate than our number alone, and our top-3 picks returned +10% (vs +6% leaning 75% on our model). Each leg must beat its own price. One player per game, so legs are independent; parlays multiply the books' margin, so the edge column matters more than the payout. Edge = our chance × the payout − 1. Backtest (2024-26, the likeliest such parlays each night): 2-leg hit 15.6-16.2% vs 15-17% predicted, 3-leg 6.7-7.2% vs 6-7%, so a 2-leg needs to pay about 6.3x and a 3-leg about 14x just to break even; the nightly top 2-leg once went 24 nights without hitting. Treat these as small stakes.</p></details>
    ${rrBlock}
    ${[2, 3].map(k => top(k).length ? `<div class="eyebrow" style="margin-top:10px">Best ${k}-leg parlays</div><div class="tw"><table class="t pairs atgp"><thead><tr><th class="l">Legs</th><th>Chance</th><th>Pays</th><th>Edge</th><th class="l">My parlay</th></tr></thead><tbody>${top(k).map(row).join('')}</tbody></table></div>` : '').join('')}</section>`;
}
// parlays by payout tier (mkt.js tierCard): anytime goals from our top 3 per game (30%+), plus game lines
function tierParlays(){
  if (!(window.MKT && MKT.tierCard)) return '';
  const gameLegs = [], top = {};
  for (const g of D.games){
    for (const s of [g.away, g.home]) for (const p of s.players) if (p.ur && p.ur <= 3 && p.p >= .30) top[g.id + '|' + MKT.norm(p.name)] = 1;
    const m = g.ml; if (!m) continue;
    const A = g.away.team, H = g.home.team;
    gameLegs.push({gid: g.id, kind: 'ml', side: 'away', p: m.away, lab: `${A} moneyline`}, {gid: g.id, kind: 'ml', side: 'home', p: m.home, lab: `${H} moneyline`},
                  {gid: g.id, kind: 'spread', side: 'away', line: 1.5, p: m.away_pl, lab: `${A} −1.5`}, {gid: g.id, kind: 'spread', side: 'home', line: -1.5, p: m.home_pl, lab: `${H} −1.5`},
                  {gid: g.id, kind: 'spread', side: 'away', line: -1.5, p: m.away_pl_plus, lab: `${A} +1.5`}, {gid: g.id, kind: 'spread', side: 'home', line: 1.5, p: m.home_pl_plus, lab: `${H} +1.5`});
    for (const k of ['5.5', '6.5']) if (m.totals[k] != null)
      gameLegs.push({gid: g.id, kind: 'total', side: 'over', line: +k, p: m.totals[k], lab: `${A}-${H} over ${k}`}, {gid: g.id, kind: 'total', side: 'under', line: +k, p: 1 - m.totals[k], lab: `${A}-${H} under ${k}`});
  }
  const games = D.games.map(g => ({id: g.id, label: `${g.away.team} @ ${g.home.team}`, start: g.start_utc}));
  const shots = {sog05: 'shots', sog15: 'shots', sog25: 'shots', sog35: 'shots', sog45: 'shots'};
  // anytime goals only from our top 3 (30%+); shots from anyone (the shots model is calibrated across the board)
  const all = MKT.tierCard({sport: 'nhl', title: 'Parlays by payout (your books)', games, gameLegs, wOurs: {goal: .5},
                            propKinds: {goal: 'to score', ...shots}, propOk: (gid, nm, k) => k !== 'goal' || !!top[gid + '|' + nm]});
  // shots + moneyline: priced at the best book anywhere, every combo has at least one of each
  const sml = MKT.tierCard({sport: 'nhl', title: 'Shots + moneyline parlays', games, gameLegs: gameLegs.filter(L => L.kind === 'ml'),
                            propKinds: shots, overOnly: true, anyBook: true, need: ['ml', 'shots']});
  const body = h => h.replace(/<h2>[^<]*<\/h2>/, '');
  return body(all) === body(sml) ? sml : all + sml;                 // same picks (no goal prices yet): show one card
}
// Plays tab: every parlay card in one place
// each card folds under its title; folded cards are remembered on this device
let FOLD = {};
try { FOLD = JSON.parse(localStorage.getItem('nhl-plays-fold') || '{}'); } catch (e) {}
function foldable(html){
  return html.split(/(?=<section class="panel")/).filter(x => x.trim()).map(sec => {
    const m = sec.match(/<h2>([\s\S]*?)<\/h2>/); if (!m) return sec;
    const key = m[1].replace(/<[^>]+>/g, '').trim();
    const body = sec.replace(m[0], '');
    return `<details class="pfold" data-fold="${esc(key)}"${FOLD[key] ? '' : ' open'}><summary><h2>${m[1]}</h2><span class="chev" aria-hidden="true">▾</span></summary>${body}</details>`;
  }).join('');
}
// Triple-green longshots (experimental): two triple-green players from different games, each 15-35% to score, whose fair
// payout is 12x+; the likeliest six. Backtest 2024-26: such pairs hit ~16 times vs ~10 expected (pairs share players, so
// weaker evidence than it looks). Shown and tracked, not recommended.
function tripleCard(){
  const legs = [], now = Date.now(), dec = o => o > 0 ? 1 + o / 100 : 1 + 100 / -o, am = o => (o > 0 ? '+' : '') + o;
  for (const g of D.games){
    if (g.start_utc && new Date(g.start_utc) <= now) continue;
    for (const [s, o] of [[g.away, g.home], [g.home, g.away]]) for (const p of s.players)
      if (tripleGreen(p, o)) legs.push({g, p, team: s.team, b: window.MKT && MKT.propBest ? MKT.propBest(g.id, p.name, 'goal') : null});
  }
  if (!legs.length) return '';
  const pairs = [];
  for (let i = 0; i < legs.length; i++) for (let j = i + 1; j < legs.length; j++){
    const a = legs[i], c = legs[j];
    if (a.g.id === c.g.id || a.p.p < .15 || a.p.p > .35 || c.p.p < .15 || c.p.p > .35) continue;
    const pr = a.p.p * c.p.p; if (pr > 1 / 12) continue;
    pairs.push({legs: [a, c], p: pr, d: a.b && c.b ? dec(a.b.o) * dec(c.b.o) : 1 / pr});
  }
  pairs.sort((x, y) => y.p - x.p); const top = pairs.slice(0, 6);
  if (window.MKT && MKT.reg) top.forEach(c => MKT.reg('Triple-green longshots (experimental)', '2-leg', c.legs.map(L => ({gid: String(L.g.id), start: L.g.start_utc, kind: 'goal', side: 'o', line: .5,
    name: MKT.norm(L.p.name), lab: `${L.p.name} to score`, p: L.p.p, price: L.b ? L.b.o : null, book: L.b ? L.b.k : null})), c.p, c.d));
  const price = L => L.b ? (L.b.u ? `<a href="${esc(L.b.u)}" target="_blank" rel="noopener">${am(L.b.o)} ${esc(L.b.name)} ↗</a>` : `${am(L.b.o)} ${esc(L.b.name)}`) : '<span class="muted">no price yet</span>';
  const who = legs.map(L => `<b>${esc(L.p.name)}</b> <span class="small muted">${esc(L.team)} · ${pct(L.p.p)} · ${price(L)}</span>`).join('<br>');
  const rows = top.map(c => `<tr><td class="l">${c.legs.map(L => `<b>${esc(L.p.name)}</b> <span class="small muted">${esc(L.team)} · ${pct(L.p.p)} · ${price(L)}</span>`).join('<br>')}</td>
    <td class="big">${pct(c.p)}</td><td class="pay">${c.d.toFixed(1)}x</td><td class="l add">${window.SLIP ? SLIP.allBtn(c.legs.map(L => ({id: `nhl|${L.g.id}|${L.p.id}|goal1`, sport: 'nhl', g: L.g.id, glab: `${L.g.away.team} @ ${L.g.home.team}`, lab: `${L.p.name} to score`, p: L.p.p, side: 'over', stat: 'goals', line: .5})), '+ add pair') : ''}</td></tr>`).join('');
  return `<section class="panel"><div class="phead"><h2>Triple-green longshots <span class="exp3">experimental</span></h2><span class="small muted">${legs.length} triple-green player${legs.length === 1 ? '' : 's'} tonight</span></div>
    <details class="how"><summary>What this is</summary><p class="note">Players with all three green flags: a strong head-to-head record against tonight's opponent (3+ meetings, 0.25+ points a game above his usual rate), an opposing goalie struggling this season (GSAA −1.5 or worse) and a defense that allows more expected goals to his position. One or two of these on their own predicted nothing in our 2024-26 tests, but players with all three scored 12-20% more often than our chance in both seasons, and pairs of them hit about 16 times vs 10 expected. That is a small sample (about 340 player-games a season), so this card is tracked on the Parlays results tab to see if it holds up, not a recommendation. Chances shown are our model's, unadjusted.</p></details>
    <div class="eyebrow" style="margin-top:8px">Tonight's triple-green players</div><p style="margin:6px 0 10px;line-height:1.7">${who}</p>
    ${top.length ? `<div class="eyebrow">Pairs paying 12x+ (fair)</div><div class="tw"><table class="t pairs atgp"><thead><tr><th class="l">Two-man</th><th>Chance</th><th>Pays</th><th class="l">My parlay</th></tr></thead><tbody>${rows}</tbody></table></div>` : '<p class="small muted">No pair from two different games in the 15-35% range tonight.</p>'}</section>`;
}
function playsView(){
  const h = (window.MKT && MKT.dailyCard ? MKT.dailyCard() : '') + foldable(atgParlays() + tierParlays() + pairsCard() + tripleCard());
  return h || '<section class="panel"><div class="empty">No plays yet: they appear once sportsbook prices for tonight’s games are in.</div></section>';
}
// Longshot two-mans priced at the viewer's books: legs 15-35% to score, chance blended with the exchanges', every
// leg beating its price, two different games, paying 12x+ at the book. (Backtest 2024-26: adding shot / xG / DvP signals
// to the pick made these pairs hit LESS often -- 5.4% vs 6.0% and 5.1% vs 7.3% -- so the goal chance stays the pick.)
function pricedLongshots(){
  if (!(window.MKT && MKT.propBest && MKT.myBooks().length)) return [];
  const now = Date.now(), dec = o => o > 0 ? 1 + o / 100 : 1 + 100 / -o, legs = [];
  for (const g of D.games){
    if (g.start_utc && new Date(g.start_utc) <= now) continue;
    for (const side of [g.away, g.home]) for (const p of side.players){
      if (p.p < .15 || p.p > .35 || !(p.ur && p.ur <= 3)) continue;  // our top 3 per game only: everyone else has lost on Kalshi
      const b = MKT.propBest(g.id, p.name, 'goal'); if (!b || !b.mine) continue;
      const m = MKT.propMarket ? MKT.propMarket(g.id, p.name, 'goal') : null, q = MKT.blend ? MKT.blend(g.id, p.name, 'goal', p.p) : m != null ? .5 * p.p + .5 * m : p.p;
      if (q * dec(b.o) - 1 <= 0) continue;
      legs.push({g, p, team: side.team, b, q, m});
    }
  }
  const out = [];
  for (let i = 0; i < legs.length; i++) for (let j = i + 1; j < legs.length; j++){
    const a = legs[i], c = legs[j]; if (a.g.id === c.g.id) continue;
    const d = dec(a.b.o) * dec(c.b.o); if (d < 12) continue;
    out.push({legs: [a, c], p: a.q * c.q, d});
  }
  return out.sort((x, y) => y.p - x.p).slice(0, 6);
}
function pairsCard(){
  const P = D.pairs || [];
  const L2 = pricedLongshots(), am = o => (o > 0 ? '+' : '') + o;
  const pricedRows = L2.map(c => `<tr><td class="l">${c.legs.map(L => `<b>${esc(L.p.name)}</b> <span class="small muted" title="Our chance ${pct(L.p.p)}${L.m != null ? ', exchanges ' + pct(L.m) : ''}">${esc(L.team)} · ${pct(L.q)} · ${L.b.u ? `<a href="${esc(L.b.u)}" target="_blank" rel="noopener">${am(L.b.o)} ${esc(L.b.name)} ↗</a>` : `${am(L.b.o)} ${esc(L.b.name)}`}</span>`).join('<br>')}</td>
      <td class="big">${pct(c.p)}</td><td class="pay">${c.d.toFixed(1)}x</td><td class="edge ${c.p * c.d - 1 >= .1 ? 'pos-d' : ''}" style="font-weight:700">+${((c.p * c.d - 1) * 100).toFixed(0)}%</td>
      <td class="l add">${window.SLIP ? SLIP.allBtn(c.legs.map(L => ({id: `nhl|${L.g.id}|${L.p.id}|goal1`, sport: 'nhl', g: L.g.id, glab: `${L.g.away.team} @ ${L.g.home.team}`, lab: `${L.p.name} to score`, p: L.q, side: 'over', stat: 'goals', line: .5})), '+ add pair') : ''}</td></tr>`).join('');
  const priced = L2.length ? `<div class="tw"><table class="t pairs atgp"><thead><tr><th class="l">Two-man</th><th>Chance</th><th>Pays</th><th>Edge</th><th class="l">My parlay</th></tr></thead><tbody>${pricedRows}</tbody></table></div>` : '';
  if (window.MKT && MKT.reg){
    const gs = Object.fromEntries(D.games.map(g => [String(g.id), g.start_utc]));
    L2.forEach(c => MKT.reg('Longshot two-mans', '12x+', c.legs.map(L => ({gid: String(L.g.id), start: L.g.start_utc, kind: 'goal', side: 'o', line: .5, name: MKT.norm(L.p.name), lab: `${L.p.name} to score`, p: +L.q.toFixed(4), price: L.b.o, book: L.b.k})), c.p, c.d));
    P.forEach(x => MKT.reg('2-leg pairs paying 15x+', x.slate || 'Tonight', x.legs.map(L => ({gid: String(L.gid), start: gs[String(L.gid)], kind: 'goal', side: 'o', line: .5, name: MKT.norm(L.name), lab: `${L.name} to score`, p: L.p, price: null, book: null})), x.p, x.payout));
  }
  if (!P.length && !priced) return '';
  const R = D.pairs_record, rec = R && R.n ? `Record since ${esc(R.since)}: <b>${R.hits}-${R.n - R.hits}</b> (${(R.hits / R.n * 100).toFixed(1)}% vs ${(R.pred * 100).toFixed(1)}% implied) · return if paid our fair odds ${R.ret >= 0 ? '+' : ''}${(R.ret * 100).toFixed(0)}%` : 'Record: tracking starts with tonight\'s pairs.';
  const legSlip = L => ({id: `nhl|${L.gid}|${L.id}|goal1`, sport: 'nhl', g: L.gid, glab: L.game, lab: `${L.name} to score`, p: L.p, side: 'over', stat: 'goals', line: .5});
  const order = ['Early slate', 'Middle slate', 'Late slate'], by = {};
  P.forEach(x => (by[x.slate || 'Tonight'] = by[x.slate || 'Tonight'] || []).push(x));
  const rowsFor = list => list.map(x => `<tr><td class="l">${x.legs.map(L => `<b>${esc(L.name)}</b> <span class="small muted">${esc(L.team)} · ${pct(L.p)}</span>`).join(' + ')}</td><td class="big">${pct(x.p)}</td><td class="pay">${x.payout}x</td><td class="l add">${window.SLIP ? SLIP.allBtn(x.legs.map(legSlip), '+ add pair') : ''}</td></tr>`).join('');
  const singles = Object.entries(D.slates || {}).filter(([sl, n]) => n < 2 && !by[sl]).map(([sl]) => `<tr class="slate"><td class="l muted" colspan="4"><b>${esc(sl)}</b>: one game only, no different-game pair possible</td></tr>`).join('');
  const body = [...order.filter(k => by[k]), ...Object.keys(by).filter(k => !order.includes(k))].map(k => `<tr class="slate"><td class="l" colspan="4"><b>${esc(k)}</b> <span class="small muted">first puck drop ${esc(by[k][0].slate_start || '')} ET · ${by[k].length} pair${by[k].length === 1 ? '' : 's'}</span></td></tr>` + rowsFor(by[k])).join('') + singles;
  const tracked = P.length ? `<details${priced ? '' : ' open'}><summary class="small muted" style="cursor:pointer;margin:10px 0 6px">Tracked 15x pairs (fair odds) · ${rec}</summary>
    <div class="tw"><table class="t pairs"><thead><tr><th class="l">Pair</th><th>Chance</th><th>Fair payout</th><th class="l">My parlay</th></tr></thead><tbody>
    ${body}
    </tbody></table></div></details>` : '';
  return `<section class="panel"><div class="phead"><h2>Longshot two-mans</h2><span class="small muted">${priced ? 'Priced at ' + esc(MKT.myBooks().join(', ')) + ' · pays 12x+' : 'waiting for prices at your books'}</span></div>
    <details class="how"${innerWidth > 640 ? ' open' : ''}><summary>How these are picked</summary><p class="note">Two anytime-goal legs from different games, each from our top 3 in its game and 15-35% to score (on Kalshi this season, legs outside our top 3 have lost money at every weighting), with each chance half ours and half the exchanges' and each beating its price at your books; the pairs paying 12x+ that are most likely to hit. We tested adding shot volume, expected-goal trends and defense-vs-position on top of our goal chance (2024-26): pairs picked that way hit less often (5.4% vs 6.0%, 5.1% vs 7.3%), because the goal model already uses shot quality, the opponent and the goalie. Expect about 1 hit in 12-15.</p></details>
    ${priced}${tracked}</section>`;
  return `<section class="panel"><div class="phead"><h2>2-leg pairs paying 15x+</h2><span class="small muted">${rec}</span></div>
    <details class="how"${innerWidth > 640 ? ' open' : ''}><summary>How these work</summary><p class="note">The likeliest anytime-goal pairs from different games whose fair payout is 15x or more (only take one if your book pays at least that). These are fairly priced, not a proven edge: in backtests, pairs built this way hit about their implied 1 in 15 (anywhere from 5% to 8% depending on exactly which near-identical pair was picked). Both legs come from the same slate (early before 5 PM ET, middle 5-9 PM, late 9 PM+), and each slate's pairs freeze at its first puck drop and are graded on the Results page.</p></details>
    <div class="tw"><table class="t pairs"><thead><tr><th class="l">Pair</th><th>Chance</th><th>Fair payout</th><th class="l">My parlay</th></tr></thead><tbody>
    ${body}
    </tbody></table></div></section>`;
}
function jumpBar(){
  if (D.games.length < 2) return '';
  const t = g => (g.start || '').split('·').pop().replace(' ET', '').trim();
  return `<div class="gjw"><button type="button" class="gjarr" data-gj="-1" aria-label="Earlier games">‹</button><nav class="gjump" aria-label="Jump to game">${D.games.map(g => `<a href="#g${g.id}">${esc(g.away.team)} @ ${esc(g.home.team)}<small>${esc(t(g))}</small></a>`).join('')}</nav><button type="button" class="gjarr" data-gj="1" aria-label="Later games">›</button></div>`;
}
// game strip: arrows and the mouse wheel scroll it sideways on desktop; fades / disabled arrows show where more games are
function gjSync(){
  for (const w of document.querySelectorAll('.gjw')){
    const n = w.querySelector('.gjump'); if (!n) continue;
    const l = n.scrollLeft > 2, r = n.scrollLeft + n.clientWidth < n.scrollWidth - 2;
    w.classList.toggle('ml', l); w.classList.toggle('mr', r);
    const [a, b] = w.querySelectorAll('.gjarr'); if (a) a.disabled = !l; if (b) b.disabled = !r;
  }
}
document.addEventListener('click', e => { const b = e.target.closest && e.target.closest('[data-gj]'); if (!b) return;
  const n = b.parentElement.querySelector('.gjump'); n.scrollBy({left: +b.dataset.gj * n.clientWidth * .7}); });
document.addEventListener('wheel', e => { const n = e.target.closest && e.target.closest('.gjump'); if (!n || Math.abs(e.deltaX) > Math.abs(e.deltaY)) return;
  if (n.scrollWidth <= n.clientWidth) return; e.preventDefault(); n.style.scrollBehavior = 'auto'; n.scrollLeft += e.deltaY; n.style.scrollBehavior = ''; }, {passive: false});
document.addEventListener('scroll', e => { if (e.target.classList && e.target.classList.contains('gjump')) gjSync(); }, true);
addEventListener('resize', gjSync);
new MutationObserver(() => requestAnimationFrame(gjSync)).observe(document.body, {childList: true, subtree: true});
// the All players sort / Value-only chips, shared with the Games tab (same state, so the choice carries over)
function playerChips(){
  return `<div class="pchips"><div class="seg" role="group" aria-label="Sort players"><button data-bsort="p" aria-pressed="${state.bsort !== 'pf' && state.bsort !== 'u'}">Anytime goal</button><button data-bsort="pf" aria-pressed="${state.bsort === 'pf'}">First goal</button><button data-bsort="u" aria-pressed="${state.bsort === 'u'}" title="${UTIP}">Usage rank</button></div><div class="seg" role="group" aria-label="Filter"><button data-bval="1" aria-pressed="${!!state.bval}" title="Only players whose best anytime-goal price at your books beats our chance by 5%+ (top-3 usage players; same rule as the green prices)">Value only</button></div></div>`;
}
function gamePlayers(list, gid){
  let x = list.slice();
  if (state.bval) x = x.filter(p => isValue({...p, gid}));
  return x.sort((a, b) => state.bsort === 'pf' ? (b.pf ?? 0) - (a.pf ?? 0) : state.bsort === 'u' ? ulam(b) - ulam(a) : b.p - a.p);
}
// Games: NFL-style rail of games + one game at a time, with tabs (Projections / Shot map)
const UI = (() => { try { return JSON.parse(localStorage.getItem('nhl-ui') || '{}'); } catch (e) { return {}; } })();
const saveUI = () => { try { localStorage.setItem('nhl-ui', JSON.stringify({gtab: state.gtab, smode: state.smode})); } catch (e) {} };
state.gtab = UI.gtab === 'map' ? 'map' : 'proj'; state.smode = ['ov', 'off', 'def'].includes(UI.smode) ? UI.smode : 'ov';
{ const m = /^#g(\d+)$/.exec(location.hash), up = D.games.find(g => !g.start_utc || new Date(g.start_utc) > Date.now());
  state.game = m && D.games.some(g => g.id === +m[1]) ? +m[1] : (up || D.games[0] || {}).id; }
function gameList(){
  return D.games.map(g => { const hw = g.ml ? g.ml.home : .5, fav = hw >= .5 ? g.home.team : g.away.team;
    return `<button class="game-btn" data-game="${g.id}" aria-current="${g.id === state.game}">
      <span class="m"><span class="teams">${esc(g.away.team)} @ ${esc(g.home.team)}</span><span class="line">${esc(String(g.start).replace(/^\w+ \w+ \d+ · /, '').replace(/ ET$/, ''))}</span></span>
      ${g.ml ? `<span class="line">${esc(fav)} ${Math.round(Math.max(hw, 1 - hw) * 100)}% · total ${g.ml.total.toFixed(1)}</span>
      <span class="mini" aria-hidden="true"><i style="flex:${1 - hw};background:var(--away)"></i><i style="flex:${hw};background:var(--accent)"></i></span>` : ''}
    </button>`; }).join('');
}
function gameCard(g){
  return `<section class="panel gcard" id="g${g.id}"><div class="gh"><h2>${esc(g.away.team)} at ${esc(g.home.team)}</h2><span class="small muted">${esc(g.start)} · goalies ${esc(g.away.goalie)} / ${esc(g.home.goalie)}</span></div>
    ${mlBlock(g)}${window.MKT ? MKT.game(g.id) : ''}<div class="sides">${side(g.away, g.home, g.id)}${side(g.home, g.away, g.id)}</div></section>`;
}
function games(){
  const g = D.games.find(x => x.id === state.game);
  if (!g) return '<div class="empty">No games.</div>';
  const gsw = `<div class="seg gsw" role="group" aria-label="Game view">${[['proj', 'Projections'], ['map', 'Shot map']].map(([k, l]) =>
    `<button data-gtab="${k}" aria-pressed="${state.gtab === k}">${l}</button>`).join('')}</div>`;
  const body = state.gtab === 'map' ? shotMapPanel(g) : picksKey() + playerChips() + gameCard(g);
  return `<div class="games"><nav class="rail" aria-label="Games">${gameList()}</nav><div class="detail">${gsw}${body}</div></div>`;
}
/* ---------------- shot map: where each team shoots vs where the other allows (display only) ---------------- */
const SM_ZONE = [['inner', 'Net front & low slot'], ['high', 'High slot'], ['perim', 'Outside']];
const ordn = n => { const s_ = ['th', 'st', 'nd', 'rd'], v = n % 100; return n + (s_[(v - 20) % 10] || s_[v] || s_[0]); };
function smRegion(r, c){
  const d = r * 5 - 7.5, l = Math.abs(c * 5 - 40);
  if (d < 0) return 'behind the net';
  if (d <= 12 && l <= 10) return 'net front';
  if (d <= 32 && l <= 12) return 'slot';
  if (d <= 50 && l <= 15) return 'high slot';
  if (d >= 50) return 'the points';
  if (d <= 40 && l <= 32) return 'the circles';
  return 'sharp angles';
}
function smFill(v, w){
  if (v == null) return 'none';
  return v >= 1 ? `rgba(255,99,71,${(Math.min(1, (v - 1) / .3) * .95 * w).toFixed(3)})` : `rgba(70,150,255,${(Math.min(1, (1 - v) / .25) * .85 * w).toFixed(3)})`;
}
function smSvg(vals, tipf, dots){
  const DN = D.shotmap.lg_density, dmax = Math.max(...DN.flat().filter(x => x != null));
  const P = (l, d) => `${(127.5 + l * 3).toFixed(1)},${((d + 10) * 3).toFixed(1)}`;
  let cells = '';
  for (let r = 0; r < 15; r++) for (let c = 0; c < 17; c++){
    const v = vals[r][c]; if (v == null) continue;
    const w = Math.max(.5, Math.min(1, Math.sqrt(DN[r][c] / dmax) * 1.3));
    cells += `<rect x="${c * 15}" y="${r * 15}" width="15" height="15" fill="${smFill(v, w)}" data-tip="${esc(tipf(r, c))}"/>`;
  }
  return `<svg class="smsvg" viewBox="-2 -2 259 229" role="img" aria-label="Shot map">
    <defs><filter id="smblur" x="-5%" y="-5%" width="110%" height="110%"><feGaussianBlur stdDeviation="6"/></filter><clipPath id="smclip"><path d="M0,225 L0,84 Q0,0 84,0 L171,0 Q255,0 255,84 L255,225 Z"/></clipPath></defs>
    <g clip-path="url(#smclip)"><g filter="url(#smblur)"${dots ? ' opacity=".35"' : ''}>${cells}</g>${dots || ''}</g>
    <g class="rink" fill="none">
      <path d="M0,225 L0,84 Q0,0 84,0 L171,0 Q255,0 255,84 L255,225"/>
      <line x1="0" y1="30" x2="255" y2="30" class="gl"/><line x1="0" y1="222" x2="255" y2="222" class="bl"/>
      <rect x="118.5" y="20" width="18" height="10" rx="2"/><path d="M109.5,30 A18,18 0 0 0 145.5,30" class="cr"/>
      <circle cx="61.5" cy="90" r="45"/><circle cx="193.5" cy="90" r="45"/><circle cx="61.5" cy="90" r="2" class="dot"/><circle cx="193.5" cy="90" r="2" class="dot"/>
      <polygon points="${P(-3, 0)} ${P(-22, 20)} ${P(-22, 35)} ${P(22, 35)} ${P(22, 20)} ${P(3, 0)}" class="hp"/>
    </g></svg>`;
}
function smBlock(off, def){
  const SM = D.shotmap, O = SM.teams[off.team], F = SM.teams[def.team];
  if (!O || !F) return `<div class="smb"><h3>${esc(off.team)} attack</h3><div class="empty">No shot data yet.</div></div>`;
  const ov = O.off.map((row, r) => row.map((v, c) => v == null || F.dfn[r][c] == null ? null : v * F.dfn[r][c] / 1e4));
  const vals = state.smode === 'off' ? O.off.map(row => row.map(v => v == null ? null : v / 100))
    : state.smode === 'def' ? F.dfn.map(row => row.map(v => v == null ? null : v / 100)) : ov;
  const pc = v => (v >= 1 ? '+' : '−') + Math.abs(Math.round((v - 1) * 100)) + '%';
  const tipf = (r, c) => `<b>${smRegion(r, c)}</b><br>${esc(off.team)} shoots here ${pc(O.off[r][c] / 100)} vs average<br>${esc(def.team)} allows ${pc(F.dfn[r][c] / 100)}<br>Together: ${pc(ov[r][c])}`;
  // areas where the two meet, weighted by how often the league shoots there
  const DN = SM.lg_density, agg = {};
  O.off.forEach((row, r) => row.forEach((v, c) => { if (ov[r][c] == null) return; const k = smRegion(r, c), a = agg[k] = agg[k] || {w: 0, v: 0}; a.w += DN[r][c]; a.v += DN[r][c] * ov[r][c]; }));
  const reg = Object.entries(agg).filter(([, a]) => a.w > .4).map(([k, a]) => ({k, v: a.v / a.w}));
  const hot = reg.filter(x => x.v >= 1.08).sort((a, b) => b.v - a.v).slice(0, 2), cold = reg.filter(x => x.v <= .92).sort((a, b) => a.v - b.v).slice(0, 1);
  const G = def.goalie_id != null && SM.goalies[def.goalie_id], LZ = SM.lg_zone || {};
  const sv = x => x.toFixed(3).replace(/^0/, '');
  const gz = G ? SM_ZONE.filter(([z]) => G[z]).map(([z, lab]) => { const d = G[z].sv - LZ[z];
      return `<span class="smg" tabindex="0" data-tip="${esc(def.goalie)}: ${sv(G[z].sv)} save % on ${G[z].n} shots from here (league ${sv(LZ[z])}), last two seasons">${lab} <b class="${d <= -.03 ? 'g2' : d <= -.012 ? 'g1' : d >= .03 ? 'r2' : d >= .012 ? 'r1' : ''}">${sv(G[z].sv)}</b></span>`; }).join('') : '';
  // shooters: tap one to plot his last shots on the map
  const SH = SM.shooters || {}, PZ = SM.pos_zone || {};
  const pl = (off.players || []).filter(p => SH[p.id]).sort((a, b) => (b.sog ?? 0) - (a.sog ?? 0));
  const selId = (state.smp || {})[off.team], sel = pl.find(p => p.id === selId), S = sel && SH[sel.id];
  let dots = '';
  if (S){ for (let i = 0; i < S.xy.length; i += 3){ const [l, d, gl] = [S.xy[i], S.xy[i + 1], S.xy[i + 2]];
    if (d < -10 || d > 64) continue;
    dots += `<circle cx="${(127.5 + l * 3).toFixed(1)}" cy="${((d + 10) * 3).toFixed(1)}" r="${gl ? 5 : 3.2}" class="${gl ? 'smd g' : 'smd'}"/>`; } }
  const zb = (z, grp) => { const a = PZ[grp] || {}; return `<span class="smzb" aria-hidden="true">${[['inner', 'zi'], ['high', 'zh'], ['perim', 'zp']].map(([k, c]) => `<i class="${c}" style="flex:${z[k] || 0}"></i>`).join('')}</span>`; };
  const TY = {'tip-in': 'tips', 'wrap-around': 'wraps', 'between-legs': 'between-legs', deflected: 'deflections'};
  const tyl = t => TY[t] || t;
  // fit: how much more than average the opponent allows shots where this player shoots (his last shots)
  const fit = id => { const x = SH[id]; let t = 0, n = 0;
    for (let i = 0; i < x.xy.length; i += 3){ const r = Math.floor((x.xy[i + 1] + 10) / 5), c = Math.floor((x.xy[i] + 42.5) / 5);
      const v = F.dfn[r] && F.dfn[r][c]; if (v != null){ t += v / 100; n++; } }
    return n >= 8 ? t / n : null; };
  pl.forEach(p => { p._fit = fit(p.id); });
  const good = pl.filter(p => p._fit != null && p._fit >= 1.08).sort((a, b) => b._fit - a._fit);
  const fitTip = p => `${esc(def.team)} allows ${Math.round((p._fit - 1) * 100)}% more shots than average in the spots ${esc(p.name)} shoots from (his last ${SH[p.id].n} shots). Tested on 2024-26: this mostly reflects how many shots ${esc(def.team)} allows overall, which our shots projection already uses; the location part didn’t predict goals or extra shots.`;
  const srow = p => { const x = SH[p.id], a = (PZ[x.grp] || {}).inner || .25, d = (x.z.inner || 0) - a, gd = p._fit != null && p._fit >= 1.08;
    return `<button type="button" class="smp${gd ? ' fit' : ''}" data-smp="${esc(off.team)}:${p.id}" aria-pressed="${p.id === selId}">
      <span class="smpn"><b>${esc(p.name)}</b> <small>${esc(p.pos)}</small>${gd ? ` <span class="smfit" data-tip="${fitTip(p)}">fit +${Math.round((p._fit - 1) * 100)}%</span>` : ''}</span>${zb(x.z, x.grp)}
      <span class="smps"><b class="${d >= .08 ? 'up' : d <= -.08 ? 'dn' : ''}">${Math.round((x.z.inner || 0) * 100)}%</b> in close · ${esc(tyl(x.ty[0][0]))} ${Math.round(x.ty[0][1] * 100)}%</span></button>`; };
  const goodLine = good.length ? `<div class="smgood"><span class="small muted">Shoots where ${esc(def.team)} is soft:</span>${good.slice(0, 4).map(p =>
      `<button type="button" class="smfit" data-smp="${esc(off.team)}:${p.id}" data-tip="${fitTip(p)}">${esc(p.name)} +${Math.round((p._fit - 1) * 100)}%</button>`).join('')}</div>` : '';
  const shooters = pl.length ? `${goodLine}<details class="smsh"${selId ? ' open' : ''}><summary>Shooters <span class="small muted">· tap one to see his last shots</span></summary>
      <div class="smzk"><span><i class="zi"></i>in close</span><span><i class="zh"></i>high slot</span><span><i class="zp"></i>outside</span><span class="muted">${esc(off.team)} forwards average ${Math.round(((PZ.F || {}).inner || 0) * 100)}% in close, D ${Math.round(((PZ.D || {}).inner || 0) * 100)}%</span></div>
      <div class="smpl">${pl.filter((p, i) => i < 12 || good.includes(p)).map(srow).join('')}</div></details>` : '';
  const selNote = S ? `<div class="smsel"><b>${esc(sel.name)}</b>: last ${S.n} shots, <i class="smd-k g"></i>${S.g} goal${S.g === 1 ? '' : 's'} · average ${S.dist} ft out · ${S.ty.map(t => `${esc(tyl(t[0]))} ${Math.round(t[1] * 100)}%`).join(', ')} <button type="button" class="smx" data-smp="${esc(off.team)}:0">clear</button></div>` : '';
  return `<div class="smb"><h3>${esc(off.team)} attack <small>vs ${esc(def.team)} defense</small></h3>
    <div class="smchips"><span class="ladc">${esc(off.team)} ${O.f} shots/gm (${ordn(O.f_rk)})</span><span class="ladc">${Math.round(O.fi * 100)}% from in close (${ordn(O.fi_rk)})</span>
      <span class="ladc">${esc(def.team)} allows ${F.a}/gm (${ordn(F.a_rk)} most)</span><span class="ladc">${Math.round(F.ai * 100)}% in close (${ordn(F.ai_rk)} most)</span></div>
    ${selNote}${smSvg(vals, tipf, dots)}
    <ul class="smlist">${hot.map(x => `<li><b class="up">${pc(x.v)}</b> ${x.k}: ${esc(off.team)} goes there and ${esc(def.team)} gives it up</li>`).join('')}${cold.map(x => `<li><b class="dn">${pc(x.v)}</b> ${x.k}: little room for ${esc(off.team)}</li>`).join('')}${!hot.length && !cold.length ? '<li class="muted">No area stands out.</li>' : ''}</ul>
    ${gz ? `<div class="smgl"><span class="small muted">${esc(def.goalie)} save %:</span>${gz}</div>` : ''}${shooters}</div>`;
}
function shotMapPanel(g){
  if (!D.shotmap) return '<section class="panel"><div class="empty">No shot maps on this page yet.</div></section>';
  const seg = `<div class="seg" role="group" aria-label="Map view">${[['ov', 'Overlay'], ['off', 'Where they shoot'], ['def', 'Where it’s allowed']].map(([k, l]) =>
    `<button data-smode="${k}" aria-pressed="${state.smode === k}">${l}</button>`).join('')}</div>`;
  const n = (D.shotmap.teams[g.home.team] || {}).n || 30;
  return `<section class="panel"><div class="phead"><h2>${esc(g.away.team)} at ${esc(g.home.team)}: shot map</h2>${seg}</div>
    <div class="smkey"><span><i style="background:rgba(255,99,71,.8)"></i>more than average</span><span><i style="background:rgba(70,150,255,.6)"></i>less</span><span class="muted">net at the top · last ${n} games · faded where few shots happen · tap a square</span></div>
    <div class="smw">${smBlock(g.away, g.home)}${smBlock(g.home, g.away)}</div>
    <p class="note">Overlay = where this team shoots more than average times where the other team allows more than average (unblocked shots, empty nets left out). Goalie save % by zone is from the last two seasons. Display only: in our 2024-26 tests neither the overlay nor zone save % predicted goals beyond what the model already uses (it rates every shot by location through expected goals, and goalies by goals saved above expected), so the maps don’t change any number on the page.</p></section>`;
}
const ulam = p => -Math.log(1 - Math.min(p.p, .999)) * (p.um ?? 1);
// value play: the best anytime-goal price at the viewer's books beats our chance by 5%+ (not a low-trust pick)
function isValue(p){
  const b = window.MKT && MKT.propBest ? MKT.propBest(p.gid, p.name, 'goal') : null;
  if (!b || !b.mine || b.w) return false;
  const d = b.o > 0 ? 1 + b.o / 100 : 1 + 100 / -b.o;
  return p.p * d - 1 >= .05;
}
function board(){
  const rows = D.games.flatMap(g => [[g.away, g.home], [g.home, g.away]].flatMap(([s, o]) => s.players.map(p => ({...p, orig: p, team: s.team, opp: o.team, gid: g.id}))))
    .filter(p => !state.bval || isValue(p))
    .sort((a,b) => state.bsort === 'pf' ? (b.pf ?? 0) - (a.pf ?? 0) : state.bsort === 'u' ? ulam(b) - ulam(a) : b.p - a.p);
  const seg = `<div class="phead"><div class="seg" role="group" aria-label="Sort by"><button data-bsort="p" aria-pressed="${state.bsort !== 'pf'}">Anytime goal</button><button data-bsort="pf" aria-pressed="${state.bsort === 'pf'}">First goal</button><button data-bsort="u" aria-pressed="${state.bsort === 'u'}" title="${UTIP}">Usage rank</button></div><div class="seg" role="group" aria-label="Filter"><button data-bval="1" aria-pressed="${!!state.bval}" title="Only players whose best anytime-goal price at your books beats our chance by 5%+ (top-3 usage players; same rule as the green prices)">Value only</button></div><span class="small muted">First goal = chance to score the game's first goal (regulation or overtime)</span></div>`;
  const bv = p => state.bsort === 'pf' ? (p.pf ?? 0) : p.p, bmax = state.bsort === 'pf' ? .12 : .5;
  return `<section class="panel">${seg}<div class="tw"><table class="t pt"><thead><tr><th>#</th><th class="l">Player</th><th class="l">Team</th><th class="l">Pos</th><th class="l" style="min-width:140px">Chance</th><th>P(goal)</th><th>Fair</th><th>1st goal</th><th>Fair</th><th>Exp. goals</th><th title="${UTIP}">Usage rank</th><th>TOI</th><th class="l">H2H G-A-P</th><th class="l pcd" title="Best anytime-goal price at your books; green = our chance beats it by 5%+">Best odds</th></tr></thead><tbody>
    ${rows.map((p,i) => `<tr class="${rowCls(p.orig)}"><td class="muted">${i+1}</td><td class="l name">${pickMark(p.orig)}${esc(p.name)}${nhlSlip(p.orig, p.team)}${p.pp ? `<span class="pp">${p.pp}</span>` : ''}<span class="mmeta">${esc(p.team)} vs ${esc(p.opp)} · ${esc(p.pos)}${p.ur ? ` · usage #${p.ur}${(() => { const m = MRANK[p.gid + '|' + p.id], d = m ? m - p.ur : 0; return d ? ` ${d > 0 ? '▲' : '▼'}${Math.abs(d)}` : ''; })()}` : ''}${p.pf != null ? ` · 1st goal ${pct(p.pf)}` : ''}</span>${window.MKT && MKT.prop ? `<span class="pcm">${MKT.prop(p.gid, p.name, 'goal')}</span>` : ''}${mtag(p.pos, p.opp)}${whyLine(p.orig)}</td><td class="l">${esc(p.team)} <span class="small muted">vs ${esc(p.opp)}</span></td><td class="l"><span class="pos">${esc(p.pos)}</span></td><td class="l">${bar(bv(p), bmax)}</td><td class="${state.bsort === 'pf' ? '' : 'big'} kp">${pct(p.p)}${p.sog != null ? `<small class="kps">${p.sog.toFixed(1)} shots</small>` : ''}</td><td>${odds(p.fair)}</td><td class="${state.bsort === 'pf' ? 'big' : ''}">${p.pf != null ? pct(p.pf) : '–'}</td><td>${p.fair_first != null ? odds(p.fair_first) : '–'}</td><td>${p.lam.toFixed(2)}</td><td>${urCell(p.orig, p.gid)}</td><td class="muted">${p.toi}</td><td class="l">${h2hCell(p.h2h, p.opp)}</td><td class="l pcd">${window.MKT && MKT.prop ? MKT.prop(p.gid, p.name, 'goal') : ''}</td></tr>`).join('')}${!rows.length ? `<tr><td colspan="14" class="l muted">${state.bval ? 'No value plays at your books right now (prices appear once books post tonight’s anytime-goal odds; choose your books under any game’s bets).' : ''}</td></tr>` : ''}
    </tbody></table></div></section>`;
}
function posdef(){
  const rows = (D.posdef || []).slice();
  const val = (r, k) => k === 'team' ? r.team : k.split('.').reduce((o, x) => o[x], r);
  rows.sort((a, b) => { const x = val(a, state.sort), y = val(b, state.sort); return (x < y ? -1 : x > y ? 1 : 0) * state.dir; });
  const today = new Set(D.games.flatMap(g => [g.away.team, g.home.team]));
  const hm = v => `hm ${hshade(v)}`;
  const th = (k, label) => `<th class="sort" data-sort="${k}" aria-sort="${state.sort === k ? (state.dir > 0 ? 'ascending' : 'descending') : 'none'}">${label}</th>`;
  const P = ['C','L','R','D'];
  return `<section class="panel"><div class="phead"><h2>Defense vs position</h2><span class="small muted">Click a column to sort · bold teams play today</span></div>
  <div class="tw"><table class="t"><thead>
  <tr><th></th><th class="gs" colspan="4">xG allowed, even strength</th><th class="gs" colspan="4">xG allowed, penalty kill</th><th class="gs" colspan="4">Goals allowed</th><th></th></tr>
  <tr><th class="l sort" data-sort="team">Team</th>${['ev','pk','ga'].map(s => P.map(p => th(p + '.' + s, PNAME[p])).join('')).join('')}<th>Games this season</th></tr></thead><tbody>
  ${rows.map(r => `<tr><td class="l">${today.has(r.team) ? `<b>${esc(r.team)}</b>` : esc(r.team)}</td>${['ev','pk','ga'].map(s => P.map(p => `<td class="${hm(r[p][s])}">${rel(r[p][s])}</td>`).join('')).join('')}<td class="muted">${r.games}</td></tr>`).join('')}
  </tbody></table></div>
  <p class="small muted">How much each defense allows to opposing centers, left wings, right wings and defensemen, compared with the average team (+15% = 15% more). Shooter positions are the NHL's roster listings. Recent games count most, carried over from last season early on, and small samples are pulled toward average. Expected goals (xG) is the steadier measure; actual goals are noisier. Green = soft spot for that position, red = tough. On the Games tab, players facing a 10%+ soft or tough even-strength matchup get a tag. This is context, not part of the probabilities: in backtests (2024–25 and 2025–26) it added nothing beyond each opponent's overall defense, which the model already uses.</p></section>`;
}
// shots on goal (sog.py): every skater by expected shots, with the chance of 2+ / 3+ / 4+
function shotsView(){
  const rows = [];
  for (const g of D.games) for (const [me, op] of [[g.away, g.home], [g.home, g.away]]) for (const p of me.players)
    if (p.sog != null) rows.push({p, team: me.team, opp: op.team, gid: g.id, start: g.start});
  if (!rows.length) return '<section class="panel"><div class="empty">Shots on goal appear with tonight’s projections.</div></section>';
  rows.sort((a, b) => b.p.sog - a.p.sog);
  const t = state.shotsAll ? rows : rows.slice(0, 60);
  return `<section class="panel"><div class="phead"><h2>Shots on goal</h2><span class="small muted">expected shots and the chance of each line going over</span></div>
    <details class="how"${innerWidth > 640 ? ' open' : ''}><summary>How these work</summary><p class="note">Each skater's shots per 60 minutes (recent form counts a little; few games are pulled toward his position's average) times tonight's expected ice time, adjusted for how many shots the opponent allows, power-play time, home ice and back-to-backs. Shot counts spread out more than a simple average suggests, so the chances use a wider (negative binomial) curve. Checked on games the model never saw (2024-25 and 2025-26): over 1.5 / 2.5 / 3.5 shots were predicted 42.2 / 21.5 / 10.1% and happened 42.7 / 21.9 / 10.2%.</p></details>
    <div class="tw"><table class="t pt"><thead><tr><th class="l">Player</th><th>Expected</th><th>2+ (over 1.5)</th><th>3+ (over 2.5)</th><th>4+ (over 3.5)</th><th class="l">Game</th></tr></thead><tbody>
    ${t.map(r => `<tr><td class="l name">${esc(r.p.name)} <span class="small muted">${esc(r.team)} · ${esc(r.p.pos)}${r.p.pp ? ' · ' + esc(r.p.pp) : ''}</span>
      <span class="mmeta">2+ <b>${pct(r.p.so[1])}</b> · 3+ <b>${pct(r.p.so[2])}</b> · 4+ <b>${pct(r.p.so[3])}</b> · vs ${esc(r.opp)}</span></td>
      <td class="big kp">${r.p.sog.toFixed(1)}</td><td>${pct(r.p.so[1])}</td><td>${pct(r.p.so[2])}</td><td>${pct(r.p.so[3])}</td><td class="l small muted">vs ${esc(r.opp)} · ${esc(r.start)}</td></tr>`).join('')}
    </tbody></table></div>${rows.length > 60 && !state.shotsAll ? `<p><button type="button" class="bkset" data-shotsall="1">Show all ${rows.length} skaters</button></p>` : ''}</section>`;
}
function goaliesView(){
  const rows = (D.goalies || []).slice().sort((a, b) => ((b.now || b.last || {}).gsaa ?? -99) - ((a.now || a.last || {}).gsaa ?? -99));
  const c = x => x ? `<td>${x.gp}</td><td>${x.sa}</td><td>${x.ga}</td><td class="big">${sv3(x.sv)}</td><td class="big">${sgn(x.gsaa)}</td><td>${sgn(x.gsax)}</td>` : '<td colspan="6" class="muted small">no games</td>';
  return `<section class="panel"><div class="phead"><h2>Tonight's starting goalies</h2><span class="small muted">league save % ${sv3(D.lg_sv)}</span></div>
  <div class="tw"><table class="t"><thead><tr><th class="l"></th><th class="l"></th><th class="gs" colspan="6">This season</th><th class="gs" colspan="6">Last season</th></tr>
  <tr><th class="l">Goalie</th><th class="l">Team</th>${['GP','SA','GA','SV%','GSAA','GSAx'].map(h => `<th>${h}</th>`).join('').repeat(2)}</tr></thead><tbody>
  ${rows.map(r => `<tr><td class="l name">${esc(r.name)}</td><td class="l">${esc(r.team)}</td>${c(r.now)}${c(r.last)}</tr>`).join('')}</tbody></table></div>
  <p class="small muted">SV% = saves ÷ shots on goal. GSAA = goals saved above average: goals a league-average save % would have allowed on his shots, minus what he allowed (+ is good). GSAx = goals saved above expected: same idea using our shot-quality (expected goals) model, so it credits goalies who face harder shots. Empty-net goals excluded; regular season and playoffs. The goal model rates goalies on GSAx (recent games weighted, pulled toward average); in backtests a save-%/GSAA rating was no better (2024–25) or slightly worse (2025–26).</p></section>`;
}
function render(){
  for (const b of document.querySelectorAll('.tabs button')) b.setAttribute('aria-selected', b.dataset.view === state.view);
  document.getElementById('main').innerHTML = state.view === 'board' ? board() : state.view === 'posdef' ? posdef() : state.view === 'goalies' ? goaliesView() : state.view === 'shots' ? shotsView() : state.view === 'plays' ? playsView()
    : state.view === 'books' ? (window.MKT && MKT.oddsView ? MKT.oddsView(D.games.map(g => ({id: g.id, label: `${g.away.team} @ ${g.home.team}`, away: g.away.team, home: g.home.team}))) : '<div class="empty">Loading prices…</div>')
 : games();
}
document.addEventListener('click', e => {
  const b = e.target.closest('button[data-view]');
  if (b){ state.view = b.dataset.view; history.replaceState(null, '', state.view === 'games' ? location.pathname : '#' + state.view); render(); return; }
  const gb = e.target.closest('[data-game]');
  if (gb){ state.game = +gb.dataset.game; history.replaceState(null, '', '#g' + state.game); render(); return; }
  const gt = e.target.closest('[data-gtab]');
  if (gt){ state.gtab = gt.dataset.gtab; saveUI(); render(); return; }
  const sp = e.target.closest('[data-smp]');
  if (sp){ const [t, id] = sp.dataset.smp.split(':'); state.smp = state.smp || {}; state.smp[t] = state.smp[t] === +id || +id === 0 ? null : +id; render(); return; }
  const sm = e.target.closest('[data-smode]');
  if (sm){ state.smode = sm.dataset.smode; saveUI(); render(); return; }
  if (e.target.closest('[data-shotsall]')){ state.shotsAll = true; render(); return; }
  if (e.target.closest('details.pfold > summary')){
    const d = e.target.closest('details.pfold');
    setTimeout(() => { FOLD[d.dataset.fold] = !d.open; try { localStorage.setItem('nhl-plays-fold', JSON.stringify(FOLD)); } catch (e2) {} }, 0);
    return;
  }
  if (e.target.closest('[data-bval]')){ state.bval = !state.bval; render(); return; }
  const bs = e.target.closest('button[data-bsort]');
  if (bs){ state.bsort = bs.dataset.bsort; render(); return; }
  const h = e.target.closest('th[data-sort]');
  if (h){ const k = h.dataset.sort; state.dir = state.sort === k ? -state.dir : (k === 'team' ? 1 : -1); state.sort = k; render(); }
});
render();
const tip = document.getElementById('tip');
function showTip(el, x, y){ tip.innerHTML = el.dataset.tip; tip.hidden = false; const r = tip.getBoundingClientRect();
  tip.style.left = Math.min(Math.max(8, x - r.width/2), innerWidth - r.width - 8) + 'px'; tip.style.top = (y - r.height - 12 < 8 ? y + 18 : y - r.height - 12) + 'px'; }
document.addEventListener('pointermove', e => { const el = e.target.closest('[data-tip]'); if (el) showTip(el, e.clientX, e.clientY); else tip.hidden = true; });
document.addEventListener('focusin', e => { const el = e.target.closest('[data-tip]'); if (el){ const r = el.getBoundingClientRect(); showTip(el, r.left + r.width/2, r.top); } });
</script>
"""

if __name__ == "__main__":
    main()
