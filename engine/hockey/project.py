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
    pp_rank = pg.groupby(["game_id", "team"]).e_pp_toi.rank(ascending=False, method="first")
    pg["pp_role"] = np.where(has_units, pg.unit, np.select(
        [(pp_rank <= 5) & (pg.e_pp_toi >= 45), (pp_rank <= 10) & (pg.e_pp_toi >= 20)], ["PP1", "PP2"], ""))
    pg["why"] = reasons(pg)
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
                goalie=gname.get(fut_goalies.get((g.game_id, t)), "unknown"),
                goalie_status=goalie_status.get((g.game_id, t), ""), lineup=lineup_note.get((g.game_id, t), ""),
                gstats=dict(now=gstats(fut_goalies.get((g.game_id, t)), cur_season),
                            last=gstats(fut_goalies.get((g.game_id, t)), prev),
                            rating=round(float(gq_now.get((g.game_id, g.home if t == g.away else g.away), 1.0)), 3)),
                players=[dict(name=r.name, pos=r.pos, p=round(float(r.p_goal), 4), fair=int(r.fair),
                              pf=round(float(r.p_first), 4), fair_first=int(r.fair_first), why=r.why,
                              lam=round(float(r.lam), 3), toi=round(float(r.e_np_toi + r.e_pp_toi) / 60, 1),
                              pp=r.pp_role, line=r.line, new=bool(r.games_prior < 1),
                              h2h=h2h.get((int(r.player_id), r.opp))) for r in x.itertuples()])
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
    payload["lg_sv"] = round(float(gt.attrs.get("lg_sv", .9)), 3)
    starters = sorted({p for p in fut_goalies.values() if p is not None})
    payload["goalies"] = [dict(name=gname.get(p, str(p)), team=next((t for (gid, t), q in fut_goalies.items() if q == p), ""),
                               now=gstats(p, cur_season), last=gstats(p, prev)) for p in starters]
    json_path = os.path.join(OUT, f"{date}.json")
    if os.path.exists(json_path):
        kept = [x for x in json.load(open(json_path))["games"] if x["id"] not in set(todo.game_id.astype(int))]
        payload["games"] = sorted(kept + payload["games"], key=lambda x: x.get("start_utc") or "")
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
<title>Goal Sheet __DATE__</title>
__FONTS__<style>
__CSS__
.gcard{display:grid;gap:14px}
.gh{display:flex;flex-wrap:wrap;justify-content:space-between;align-items:baseline;gap:8px}
.sides{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,740px),1fr));gap:18px}
.sidehead{display:flex;justify-content:space-between;align-items:baseline;gap:8px;border-bottom:1px solid var(--faint);padding-bottom:6px}
.sidehead .code{font-size:24px}
.pp{font-size:11px;font-weight:600;color:var(--accent);border:1px solid currentColor;border-radius:4px;padding:0 4px;margin-left:6px}
.newp{font-size:11px;color:var(--muted);margin-left:6px}
.src{margin:6px 0 2px}
.sides .pbar{min-width:60px}
.mlrow{display:flex;flex-wrap:wrap;gap:8px}
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
.h2h b{font-weight:600}
.h2h .hot{color:var(--good)}
.mu{font-size:11px;font-weight:600;border-radius:4px;padding:0 4px;margin-left:6px;border:1px solid currentColor}
.mu.soft{color:var(--good)} .mu.tough{color:var(--bad)}
.pdline{margin:2px 0 4px}
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
    <div><div class="eyebrow" id="sub"></div><h1>Anytime Goal Sheet</h1></div>
    <div class="tabs" role="tablist" aria-label="View">
      <button role="tab" data-view="games">Games</button>
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
const state = {view: ['#board', '#posdef', '#goalies'].includes(location.hash) ? location.hash.slice(1) : 'games', sort: 'team', dir: 1};
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
function side(s, opp){
  const max = .5;
  return `<div><div class="sidehead"><span class="code">${esc(s.team)}</span><span class="small muted">${s.exp_goals} expected goals · vs ${esc(opp.goalie)}${opp.goalie_status ? ` (${esc(opp.goalie_status)})` : ''}</span></div>
    ${s.lineup ? `<div class="small muted src">${esc(s.lineup)}</div>` : ''}${goalieLine(opp)}${pdline(opp.team)}
    <div class="tw"><table class="t"><thead><tr><th class="l">Player</th><th class="l">Pos</th><th class="l" style="min-width:70px">Chance</th><th>P(goal)</th><th>Fair</th><th title="Chance to score the game's first goal">1st goal</th><th>Fair</th><th>TOI</th><th class="l" title="Goals-assists-points vs this opponent since 2022-23">H2H G-A-P</th></tr></thead><tbody>
    ${s.players.map(p => `<tr class="${rowCls(p)}"><td class="l name">${pickMark(p)}${esc(p.name)}${p.pp ? `<span class="pp">${p.pp}</span>` : ''}${p.new ? '<span class="newp">no NHL history</span>' : ''}${mtag(p.pos, opp.team)}${whyLine(p)}</td><td class="l"><span class="pos">${esc(p.line ? p.line.replace('F', 'L') + ' · ' + p.pos : p.pos)}</span></td><td class="l">${bar(p.p, max)}</td><td class="big">${pct(p.p)}</td><td>${odds(p.fair)}</td><td>${p.pf != null ? pct(p.pf) : '–'}</td><td class="muted">${p.fair_first != null ? odds(p.fair_first) : '–'}</td><td class="muted">${p.toi}</td><td class="l">${h2hCell(p.h2h, opp.team)}</td></tr>`).join('')}
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
  const fav = m.home >= m.away ? g.home.team : g.away.team;
  const line = (lab, p) => `<span class="mlc"><span class="eyebrow">${lab}</span><b>${pct(p)}</b><span class="muted">${o(p)}</span></span>`;
  const tline = Object.entries(m.totals).filter(([k]) => k === '5.5' || k === '6.5').map(([k, p]) => `<span class="mlc"><span class="eyebrow">O/U ${k}</span><b>${pct(p)} / ${pct(1 - p)}</b><span class="muted">${o(p)} / ${o(1 - p)}</span></span>`).join('');
  return `<div class="mlrow">${line(g.away.team + ' ML', m.away)}${line(g.home.team + ' ML', m.home)}${line(g.away.team + ' −1.5', m.away_pl)}${line(g.home.team + ' −1.5', m.home_pl)}${tline}<span class="mlc"><span class="eyebrow">Proj total</span><b>${m.total.toFixed(1)}</b><span class="muted">OT ${pct(m.ot)}</span></span></div>`;
}
function games(){
  return picksKey() + D.games.map(g => `<section class="panel gcard"><div class="gh"><h2>${esc(g.away.team)} at ${esc(g.home.team)}</h2><span class="small muted">${esc(g.start)} · goalies ${esc(g.away.goalie)} / ${esc(g.home.goalie)}</span></div>
    ${mlBlock(g)}<div class="sides">${side(g.away, g.home)}${side(g.home, g.away)}</div></section>`).join('');
}
function board(){
  const rows = D.games.flatMap(g => [[g.away, g.home], [g.home, g.away]].flatMap(([s, o]) => s.players.map(p => ({...p, orig: p, team: s.team, opp: o.team}))))
    .sort((a,b) => state.bsort === 'pf' ? (b.pf ?? 0) - (a.pf ?? 0) : b.p - a.p);
  const seg = `<div class="phead"><div class="seg" role="group" aria-label="Sort by"><button data-bsort="p" aria-pressed="${state.bsort !== 'pf'}">Anytime goal</button><button data-bsort="pf" aria-pressed="${state.bsort === 'pf'}">First goal</button></div><span class="small muted">First goal = chance to score the game's first goal (regulation or overtime)</span></div>`;
  const bv = p => state.bsort === 'pf' ? (p.pf ?? 0) : p.p, bmax = state.bsort === 'pf' ? .12 : .5;
  return `<section class="panel">${seg}<div class="tw"><table class="t"><thead><tr><th>#</th><th class="l">Player</th><th class="l">Team</th><th class="l">Pos</th><th class="l" style="min-width:140px">Chance</th><th>P(goal)</th><th>Fair</th><th>1st goal</th><th>Fair</th><th>Exp. goals</th><th>TOI</th><th class="l">H2H G-A-P</th></tr></thead><tbody>
    ${rows.map((p,i) => `<tr class="${rowCls(p.orig)}"><td class="muted">${i+1}</td><td class="l name">${pickMark(p.orig)}${esc(p.name)}${p.pp ? `<span class="pp">${p.pp}</span>` : ''}${mtag(p.pos, p.opp)}${whyLine(p.orig)}</td><td class="l">${esc(p.team)} <span class="small muted">vs ${esc(p.opp)}</span></td><td class="l"><span class="pos">${esc(p.pos)}</span></td><td class="l">${bar(bv(p), bmax)}</td><td class="${state.bsort === 'pf' ? '' : 'big'}">${pct(p.p)}</td><td>${odds(p.fair)}</td><td class="${state.bsort === 'pf' ? 'big' : ''}">${p.pf != null ? pct(p.pf) : '–'}</td><td>${p.fair_first != null ? odds(p.fair_first) : '–'}</td><td>${p.lam.toFixed(2)}</td><td class="muted">${p.toi}</td><td class="l">${h2hCell(p.h2h, p.opp)}</td></tr>`).join('')}
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
  document.getElementById('main').innerHTML = state.view === 'board' ? board() : state.view === 'posdef' ? posdef() : state.view === 'goalies' ? goaliesView() : games();
}
document.addEventListener('click', e => {
  const b = e.target.closest('button[data-view]');
  if (b){ state.view = b.dataset.view; history.replaceState(null, '', state.view === 'games' ? location.pathname : '#' + state.view); render(); return; }
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
