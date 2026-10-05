#!/usr/bin/env python3
"""
Grade archived pregame projections (projections/weeks/) against final box scores
and build projections/results.html: a week-by-week review plus season-to-date
diagnostics of what to improve.

    python results.py

Grading rules
  - Only props-style lines are graded (same volume floors as backtest.py).
  - A player with no offensive snaps and no stats is treated as void (props would be).
  - Where the actual landed in the projected distribution (its percentile) comes
    from the saved simulation distribution, so "inside the 80% range" is exact.
  - "Limited" = played under half his usual snap share (previous 3 games):
    usually an in-game injury or benching, which pregame projections can't see.
"""

import datetime as dt
import glob
import json
import os
import re
import sqlite3
import zlib

import numpy as np
import pandas as pd

import report
from boxscore import data as D

WEEKS_DIR = os.path.join("projections", "weeks")
BLIND_WEEKS_DIR = os.path.join("projections", "blind", "weeks")
CMP_STATS = ["pass_yds", "att", "cmp", "rush_yds", "car", "rec", "rec_yds", "tgt"]
OUT = os.path.join("projections", "results.html")
MIN_MEAN = {"pass_yds": 100, "att": 15, "cmp": 10, "pass_td": 0.5, "ints": 0.3, "rush_yds": 15,
            "car": 5, "rec": 1.5, "rec_yds": 15, "tgt": 2.5, "anytime_td": 0.1}
SHOW = ["pass_yds", "rush_yds", "rec", "rec_yds", "anytime_td"]      # per-game review tables
LABELS = {"pass_yds": "Passing yards", "att": "Pass attempts", "cmp": "Completions",
          "pass_td": "Passing TDs", "ints": "Interceptions", "rush_yds": "Rushing yards",
          "car": "Carries", "rec": "Receptions", "rec_yds": "Receiving yards", "tgt": "Targets",
          "anytime_td": "Anytime TD"}
POS_STAT = {"QB": "pass_yds", "RB": "rush_yds", "WR": "rec_yds", "TE": "rec_yds"}


# ------------------------------------------------------------------ actuals

def load_actuals(con):
    games = D.load_games(con)
    plays = D.load_plays(con)
    pg = D.player_games(plays, D.load_kneels(con))
    pg["anytime_td"] = ((pg.rec_td + pg.rush_td) > 0).astype(float)
    tg = D.add_field_goals(D.team_games(plays, games), con)
    pos = D.positions(con)
    sn = pd.read_sql("SELECT game_id, pfr_player_id AS pfr_id, offense_pct FROM snaps", con)
    sn = sn.merge(pos[["player_id", "pfr_id"]].dropna(), on="pfr_id")
    sn = sn.merge(games[["game_id", "gameday"]], on="game_id").sort_values("gameday")
    sn["usual"] = sn.groupby("player_id").offense_pct.transform(
        lambda s: s.shift(1).rolling(3, min_periods=1).mean())
    return games, pg, tg, sn.set_index(["game_id", "player_id"])[["offense_pct", "usual"]]


def pit(d, y, key=""):
    """Percentile of the actual within the projected distribution. Ties (common for
    counts like 0 INTs) are spread uniformly across the tied mass, seeded by `key`
    so the page is reproducible; that keeps coverage honest for discrete stats."""
    if d is None:
        return None
    if "p" in d:
        k = d["o"] + np.arange(len(d["p"]))
        p = np.asarray(d["p"])
        lo, hi = p[k < y].sum(), p[k <= y].sum()
    else:
        q = np.asarray(d["q"])
        lo, hi = (q < y).mean(), (q <= y).mean()
    u = np.random.default_rng(zlib.crc32(key.encode())).random()
    return float(lo + u * (hi - lo))


# ------------------------------------------------------------------ grading

def grade_week(path_stem, act):
    games, pg, tg, snaps = act
    season, week = map(int, re.search(r"(\d{4})_wk(\d+)", path_stem).groups())
    pl = pd.read_csv(f"{path_stem}_players.csv")
    tm = pd.read_csv(f"{path_stem}_teams.csv")
    dist = json.load(open(f"{path_stem}_dist.json")) if os.path.exists(f"{path_stem}_dist.json") else {}
    mt = json.load(open(f"{path_stem}_matchups.json"))["players"] if os.path.exists(f"{path_stem}_matchups.json") else {}
    meta = json.load(open(f"{path_stem}_meta.json")) if os.path.exists(f"{path_stem}_meta.json") else {}
    final = set(tg.game_id)
    pgi = pg.set_index(["game_id", "player_id"])
    tgi = tg.set_index(["game_id", "team"])
    wide = pl.pivot_table(index=["game_id", "player_id"], columns="stat", values="mean", aggfunc="first")

    rows, void = [], 0
    for r in pl.itertuples():
        if r.game_id not in final or r.stat not in MIN_MEAN or r.mean < MIN_MEAN[r.stat]:
            continue
        key2 = (r.game_id, r.player_id)
        played = key2 in pgi.index
        sn = snaps.loc[key2] if key2 in snaps.index else None
        if isinstance(sn, pd.DataFrame):
            sn = sn.iloc[0]
        if not played and (sn is None or not sn.offense_pct > 0):
            void += 1
            continue
        a = pgi.loc[key2] if played else None
        if isinstance(a, pd.DataFrame):
            a = a.iloc[0]
        y = float(a[r.stat]) if played else 0.0
        k = f"{r.game_id}|{r.team}|{r.player}"
        d = dist.get(k, {}).get("tds" if r.stat == "anytime_td" else r.stat)
        m = mt.get(k, {})
        usual = None if sn is None or pd.isna(sn.usual) else float(sn.usual)
        snap = None if sn is None or pd.isna(sn.offense_pct) else float(sn.offense_pct)
        extra = wide.loc[key2] if key2 in wide.index else {}
        rows.append(dict(
            season=season, week=week, game_id=r.game_id, team=r.team, opp=r.opp, player=r.player,
            player_id=r.player_id, pos=r.pos, label=m.get("label"), stat=r.stat, mean=r.mean,
            p10=r.p10, p25=r.p25, median=r.median, p75=r.p75, p90=r.p90, actual=y,
            pit=(None if r.stat == "anytime_td" else pit(d, y, f"{k}|{r.stat}")),
            source=meta.get(r.game_id, {}).get("source", "live"),
            tag=(m.get("tags") or [None])[0],
            snap=snap, usual=usual,
            limited=bool(usual is not None and snap is not None and usual >= 0.35 and snap < 0.5 * usual),
            proj_tgt=_g(extra, "tgt"), proj_car=_g(extra, "car"), proj_rec_yds=_g(extra, "rec_yds"),
            proj_rush_yds=_g(extra, "rush_yds"),
            act_tgt=float(a["tgt"]) if played else 0.0, act_car=float(a["car"]) if played else 0.0,
            act_rec_yds=float(a["rec_yds"]) if played else 0.0,
            act_rush_yds=float(a["rush_yds"]) if played else 0.0,
            rk=bool(DRAFT.get(r.player_id, (None, None))[0] == season
                    and (DRAFT.get(r.player_id, (None, None))[1] or 999) <= 64),
        ))
    g = pd.DataFrame(rows)

    trows = []
    for gid, t2 in tm.groupby("game_id", sort=False):
        home = t2[t2.home == 1].iloc[0]
        away = t2[t2.home == 0].iloc[0]
        done = gid in final
        def act_of(team, col):
            return float(tgi.loc[(gid, team), col]) if done and (gid, team) in tgi.index else None
        trows.append(dict(
            game_id=gid, away=away.team, home=home.team, source=meta.get(gid, {}).get("source", "live"),
            final=done,
            proj=[round(float(away.points), 1), round(float(home.points), 1)],
            vegas=[round(float(away.vegas_implied), 1), round(float(home.vegas_implied), 1)],
            win=[round(float(away.win_prob), 3), round(float(home.win_prob), 3)],
            score=[act_of(away.team, "points"), act_of(home.team, "points")],
            proj_att=[round(float(away.pass_att), 1), round(float(home.pass_att), 1)],
            att=[act_of(away.team, "att"), act_of(home.team, "att")],
        ))
    return season, week, g, trows, void


def _g(s, k):
    try:
        v = s[k]
        return None if pd.isna(v) else float(v)
    except (KeyError, TypeError):
        return None


# ------------------------------------------------------------- first TD scorer

FIRST = {}   # game_id -> who scored the game's first TD; filled by build()


def load_first_tds(con):
    """First touchdown of each game from play-by-play: scorer id (receiver on a pass TD,
    rusher on a run) or a defense/special-teams TD."""
    t = pd.read_sql("""SELECT game_id, play_id, qtr, game_seconds_remaining, pass_touchdown, rush_touchdown,
                              td_team, receiver_player_id, receiver_player_name, rusher_player_id, rusher_player_name
                       FROM plays WHERE touchdown = 1""", con)
    t = t.sort_values(["game_id", "qtr", "game_seconds_remaining", "play_id"], ascending=[True, True, False, True])
    out = {}
    for r in t.drop_duplicates("game_id").itertuples():
        if r.pass_touchdown == 1:
            out[r.game_id] = dict(pid=r.receiver_player_id, name=r.receiver_player_name, team=r.td_team, kind="player")
        elif r.rush_touchdown == 1:
            out[r.game_id] = dict(pid=r.rusher_player_id, name=r.rusher_player_name, team=r.td_team, kind="player")
        else:
            out[r.game_id] = dict(pid=None, name=None, team=r.td_team, kind="dst")
    return out


def grade_first(stem, final):
    """Per game: who scored the first TD, the chance we gave him, his rank among our
    first-TD chances, and our top pick. Returns (games, [(p, hit) for every listed player])."""
    pl = pd.read_csv(f"{stem}_players.csv")
    pl = pl[pl.stat == "first_td"]
    games, cal = [], []
    for gid, x in pl.groupby("game_id", sort=False):
        if gid not in final:
            continue
        x = x.sort_values("mean", ascending=False).reset_index(drop=True)
        f = FIRST.get(gid)
        hit = x.player_id == (f or {}).get("pid")
        cal += list(zip(x["mean"].round(4), hit.astype(int)))
        top = x.iloc[0]
        row = dict(game_id=gid, top=top.player, top_team=top.team, top_p=round(float(top["mean"]), 4),
                   top_hit=bool(hit.iloc[0]), top3_hit=bool(hit.iloc[:3].any()),
                   top3_p=round(float(x["mean"].iloc[:3].sum()), 4), n=int(len(x)))
        if f is None:
            row.update(kind="none", scorer=None)
        elif f["kind"] == "dst":
            row.update(kind="dst", scorer=f"{f['team']} defense / special teams")
        elif hit.any():
            i = int(hit.idxmax())
            row.update(kind="player", scorer=x.player[i], team=x.team[i], p=round(float(x["mean"][i]), 4), rank=i + 1)
        else:
            row.update(kind="unlisted", scorer=f"{f['name']} ({f['team']})")
        games.append(row)
    return games, cal


def first_summary(games, cal):
    if not games:
        return None
    c = pd.DataFrame(cal, columns=["p", "hit"])
    b = pd.cut(c.p, [0, .03, .06, .10, .15, 1])
    buckets = [dict(bucket=f"{int(i.left * 100)}–{int(i.right * 100)}%", n=int(len(y)), pred=_r(y.p.mean()), act=_r(y.hit.mean()))
               for i, y in c.groupby(b, observed=True)]
    g = pd.DataFrame(games)
    return dict(games=int(len(g)), top_hits=int(g.top_hit.sum()), top_exp=_r(g.top_p.sum(), 1),
                top3_hits=int(g.top3_hit.sum()), top3_exp=_r(g.top3_p.sum(), 1),
                listed_pred=_r(c.p.sum(), 1), listed_act=int(c.hit.sum()), dst=int((g.kind == "dst").sum()),
                buckets=buckets)


# ------------------------------------------------------------- diagnostics

def summarize(g):
    x = g[g.stat != "anytime_td"].dropna(subset=["pit"])
    td = g[g.stat == "anytime_td"]
    return dict(
        n=int(len(x)),
        in80=_r(x.pit.between(.1, .9).mean()), in50=_r(x.pit.between(.25, .75).mean()),
        over=_r((x.pit > .5).mean()),
        td_exp=_r(td["mean"].sum(), 1), td_act=int(td.actual.sum()), td_n=int(len(td)),
        limited=int(g[g.stat.isin(["pass_yds", "rush_yds", "rec_yds"])].limited.sum()),
    )


def _r(v, d=3):
    return None if v is None or pd.isna(v) else round(float(v), d)


def by_stat(g, min_n=25):
    out = []
    for (stat, pos), x in g[g.stat != "anytime_td"].groupby(["stat", "pos"]):
        x = x.dropna(subset=["pit"])
        n = len(x)
        if n < min_n:
            continue
        m = x["mean"].mean()
        bias = (x.actual.mean() - m) / m
        se = (x.actual - x["mean"]).std() / np.sqrt(n) / m
        in80, in50 = x.pit.between(.1, .9).mean(), x.pit.between(.25, .75).mean()
        cse = np.sqrt(.16 / n)
        verdict = "ok"
        if abs(bias) >= .05 and abs(bias) >= 2 * se:
            verdict = "high" if bias < 0 else "low"          # projections too high / too low
        elif in80 < .8 - max(.05, 2 * cse):
            verdict = "narrow"
        elif in80 > .8 + max(.05, 2 * cse):
            verdict = "wide"
        out.append(dict(stat=stat, pos=pos, n=n, proj=_r(m, 2), actual=_r(x.actual.mean(), 2),
                        bias=_r(bias), se=_r(se), in80=_r(in80), in50=_r(in50),
                        mae=_r((x.actual - x["median"]).abs().mean(), 2), verdict=verdict))
    order = list(MIN_MEAN)
    return sorted(out, key=lambda r: (order.index(r["stat"]), r["pos"]))


def decompose(g, stat, vol, proj_vol, proj_tot, act_vol, act_tot):
    """Split each player's miss into volume (touches) vs efficiency (yards per touch)."""
    x = g[(g.stat == stat)].dropna(subset=[proj_vol, proj_tot])
    x = x[x[proj_vol] > 0]
    if len(x) < 20:
        return None
    ypu = x[proj_tot] / x[proj_vol]
    v = (x[act_vol] - x[proj_vol]) * ypu
    e = x[act_tot] - x[act_vol] * ypu
    return dict(stat=stat, unit=vol, one={"targets": "target", "carries": "carry"}[vol], n=int(len(x)), vol_share=_r(v.abs().sum() / (v.abs().sum() + e.abs().sum())),
                vol_mean=_r(v.mean(), 1), eff_mean=_r(e.mean(), 1))


def td_buckets(g):
    x = g[g.stat == "anytime_td"]
    if x.empty:
        return []
    b = pd.cut(x["mean"], [0, .15, .25, .35, .45, .6, 1])
    t = x.groupby(b, observed=True).agg(n=("actual", "size"), pred=("mean", "mean"), act=("actual", "mean"))
    return [dict(bucket=f"{int(i.left*100)}–{int(i.right*100)}%", n=int(r.n), pred=_r(r.pred), act=_r(r.act))
            for i, r in t.iterrows()]


def team_check(trows):
    t = [x for x in trows if x["final"] and None not in x["score"]]
    if not t:
        return None
    mae_m = np.mean([abs(p - s) for x in t for p, s in zip(x["proj"], x["score"])])
    mae_v = np.mean([abs(p - s) for x in t for p, s in zip(x["vegas"], x["score"])])
    brier = np.mean([(x["win"][1] - (x["score"][1] > x["score"][0])) ** 2 for x in t if x["score"][0] != x["score"][1]])
    fav = np.mean([(x["win"][1] > .5) == (x["score"][1] > x["score"][0]) for x in t if x["score"][0] != x["score"][1]])
    return dict(n=len(t), mae_model=_r(mae_m, 2), mae_vegas=_r(mae_v, 2), brier=_r(brier), fav_won=_r(fav))


def flag_check(g):
    x = g[g.tag.notna()].copy()
    x = x[x.apply(lambda r: r.stat == POS_STAT.get(r.pos) or (r.tag["kind"] == "deep" and r.stat == "rec_yds"), axis=1)]
    x = x.dropna(subset=["pit"])
    out = {}
    for d, name in ((1, "favorable"), (-1, "tough")):
        y = x[x.tag.map(lambda t: t["dir"]) == d]
        out[name] = dict(n=int(len(y)), beat=_r((y.pit > .5).mean()) if len(y) else None)
    return out


def misses(g, trows, top=12):
    tt = {x["game_id"]: x for x in trows}
    x = g[g.stat.isin(["pass_yds", "rush_yds", "rec_yds", "rec"])].dropna(subset=["pit"])
    x = x[(x.pit <= .03) | (x.pit >= .97)].copy()
    if x.empty:
        return []
    x["ext"] = (x.pit - .5).abs()
    x["gap"] = (x.actual - x["median"]).abs() / x["median"].clip(lower=1)
    x = x.sort_values(["ext", "gap"], ascending=False).head(top)
    out = []
    for r in x.itertuples():
        reasons = []
        if r.limited:
            reasons.append(f"played {r.snap:.0%} of snaps (usually {r.usual:.0%})")
        if r.stat in ("rec_yds", "rec") and r.proj_tgt:
            if abs(r.act_tgt - r.proj_tgt) >= max(3, .5 * r.proj_tgt):
                reasons.append(f"{r.act_tgt:.0f} target{'' if r.act_tgt == 1 else 's'} (projected {r.proj_tgt:.1f})")
            elif r.stat == "rec_yds" and r.act_tgt > 0:
                reasons.append(f"{r.act_rec_yds / r.act_tgt:.1f} yds/target (projected {r.proj_rec_yds / r.proj_tgt:.1f})")
        if r.stat == "rush_yds" and r.proj_car:
            if abs(r.act_car - r.proj_car) >= max(4, .4 * r.proj_car):
                reasons.append(f"{r.act_car:.0f} carr{'y' if r.act_car == 1 else 'ies'} (projected {r.proj_car:.1f})")
            elif r.act_car > 0:
                reasons.append(f"{r.act_rush_yds / r.act_car:.1f} yds/carry (projected {r.proj_rush_yds / r.proj_car:.1f})")
        t = tt.get(r.game_id)
        if t and r.stat == "pass_yds" and t["final"]:
            i = 0 if t["away"] == r.team else 1
            if t["att"][i] is not None and abs(t["att"][i] - t["proj_att"][i]) >= 8:
                reasons.append(f"team threw {t['att'][i]:.0f} times (projected {t['proj_att'][i]:.0f})")
        out.append(dict(week=int(r.week), game_id=r.game_id, team=r.team, opp=r.opp, player=r.player,
                        label=r.label, stat=r.stat, median=_r(r.median, 1), p10=_r(r.p10, 1), p90=_r(r.p90, 1),
                        actual=_r(r.actual, 1), dir=1 if r.pit >= .5 else -1,
                        reasons=reasons or ["outlier game: volume and role were as expected"]))
    return out


def suggestions(g, stats, dec, td, team, flags, miss_all):
    s = []
    for r in stats:
        name = f"{r['pos']} {LABELS[r['stat']].lower()}"
        if r["verdict"] in ("high", "low"):
            s.append(dict(level="fix", title=f"{name} projected too {r['verdict']} by {abs(r['bias']):.0%}",
                          detail=f"Average projection {r['proj']}, actual {r['actual']} over {r['n']} lines "
                                 f"(±{r['se']:.0%}). Check the {'efficiency' if r['stat'].endswith('yds') else 'volume'} "
                                 f"inputs for {r['pos']}s and whether a league-wide shift is lagging."))
        elif r["verdict"] == "narrow":
            s.append(dict(level="fix", title=f"{name} ranges too narrow",
                          detail=f"Only {r['in80']:.0%} of results landed in the 80% range ({r['n']} lines). "
                                 "Widen the game-to-game variance for this stat."))
        elif r["verdict"] == "wide":
            s.append(dict(level="watch", title=f"{name} ranges wider than needed",
                          detail=f"{r['in80']:.0%} landed in the 80% range ({r['n']} lines); "
                                 "prices on alternate lines are too conservative."))
    for d in dec:
        if d and d["vol_share"] >= .6:
            what = "targets" if d["unit"] == "targets" else "carries"
            s.append(dict(level="watch",
                          title=f"{LABELS[d['stat']]} misses come mostly from {what} ({d['vol_share']:.0%})",
                          detail=f"Volume, not yards per {what[:-1]}, drives most of the error. The share/role model "
                                 "(depth changes, injuries to teammates, game plan) is the bigger lever than efficiency."))
    x = g[g.stat == "anytime_td"]
    if len(x) >= 60:
        exp, act = x["mean"].sum(), x.actual.sum()
        se = np.sqrt((x["mean"] * (1 - x["mean"])).sum())
        if abs(act - exp) > 2 * se:
            s.append(dict(level="fix", title=f"Anytime-TD chances {'too low' if act > exp else 'too high'}",
                          detail=f"Expected {exp:.0f} scorers among {len(x)} graded players, {act:.0f} scored (±{se:.0f})."))
    if team and team["n"] >= 30 and team["mae_model"] > team["mae_vegas"] + .3:
        s.append(dict(level="watch", title="Team scoring less accurate than the Vegas lines alone",
                      detail=f"Average points error {team['mae_model']} vs {team['mae_vegas']} for Vegas implied totals "
                             f"over {team['n']} team-games."))
    lim = [m for m in miss_all if any("snaps" in r for r in m["reasons"])]
    if len(miss_all) >= 10 and len(lim) / len(miss_all) >= .25:
        s.append(dict(level="watch", title=f"{len(lim) / len(miss_all):.0%} of the biggest misses were early exits",
                      detail="Players who left injured or lost snaps mid-game. Pregame projections can't see these; "
                             "they're why ranges have long lower tails."))
    for name in ("favorable", "tough"):
        f = flags.get(name, {})
        if f.get("n", 0) >= 40 and f["beat"] is not None:
            se = np.sqrt(.25 / f["n"])
            if abs(f["beat"] - .5) < max(.06, 2 * se):
                continue
            agrees = f["beat"] > .5 if name == "favorable" else f["beat"] < .5
            if agrees:
                s.append(dict(level="watch", title=f"{name.title()} matchup flags are predicting results",
                              detail=f"{f['beat']:.0%} of {name}-flagged players beat their median ({f['n']} lines). "
                                     "If this holds, give defense-vs-position more weight in the model."))
            else:
                s.append(dict(level="watch", title=f"{name.title()} matchups may be over-adjusted",
                              detail=f"Only {f['beat']:.0%} of {name}-flagged players beat their median ({f['n']} lines, "
                                     f"±{se:.0%}); with no edge it would be 50%. The projection may already lean too far "
                                     "toward the matchup: test a smaller defense funnel (FUNNEL_BETA) in the backtest."
                              if name == "favorable" else
                              f"{f['beat']:.0%} of tough-flagged players beat their median ({f['n']} lines, ±{se:.0%}). "
                              "The projections may be marking these players down too much."))
    if not s:
        s.append(dict(level="ok", title="No clear problems at this sample size",
                      detail="Every stat with enough lines is within normal error of its target."))
    order = {"fix": 0, "watch": 1, "ok": 2}
    return sorted(s, key=lambda t: order[t["level"]])


def by_slot(g):
    """Receiving yards by depth-chart slot (WR1, TE1, RB1...): actual vs projected, and how often
    the result beat the median and the middle half."""
    x = g[g.stat == "rec_yds"].dropna(subset=["pit"]).copy()
    x["slot"] = x.label.fillna("").str.extract(r"^([A-Z]+\d)")[0]
    out = []
    for slot in ("WR1", "WR2", "WR3", "TE1", "TE2", "RB1", "RB2"):
        y = x[x.slot == slot]
        if len(y) < 10:
            continue
        out.append(dict(slot=slot, n=int(len(y)), proj=_r(y["mean"].mean(), 1), actual=_r(y.actual.mean(), 1),
                        bias=_r(y.actual.sum() / y["mean"].sum() - 1), over=_r((y.actual > y["median"]).mean()),
                        above=_r((y.pit > .75).mean())))
    return out


def diagnostics(g, trows):
    stats = by_stat(g)
    dec = [decompose(g, "rec_yds", "targets", "proj_tgt", "proj_rec_yds", "act_tgt", "act_rec_yds"),
           decompose(g, "rush_yds", "carries", "proj_car", "proj_rush_yds", "act_car", "act_rush_yds")]
    td = td_buckets(g)
    team = team_check(trows)
    flags = flag_check(g)
    miss = misses(g, trows, top=25)
    return dict(summary=summarize(g), stats=stats, slots=by_slot(g), dec=[d for d in dec if d], td=td, team=team,
                flags=flags, misses=miss, suggestions=suggestions(g, stats, dec, td, team, flags, miss))


# ----------------------------------------------------------------- output

def review_rows(g):
    x = g[g.stat.isin(SHOW)]
    cols = ["game_id", "team", "opp", "player", "player_id", "pos", "label", "stat", "mean", "p10", "p25", "median", "p75",
            "p90", "actual", "pit", "limited", "source", "rk"]
    x = x[cols].copy()
    for c in ["mean", "p10", "p25", "median", "p75", "p90", "actual", "pit"]:
        x[c] = x[c].astype(float).round(3)
    return json.loads(x.to_json(orient="records"))


def compare(gm, gb, tm, tb):
    """Main vs market-blind on the same player-games (and the same team-games)."""
    key = ["game_id", "player_id", "stat"]
    m = gm.merge(gb[key + ["mean", "median", "pit", "p10", "p90"]], on=key, suffixes=("", "_b"))
    if m.empty:
        return None
    out = dict(n=int(len(m)), stats=[], weeks=[])
    for stat in CMP_STATS + ["anytime_td"]:
        x = m[m.stat == stat]
        if len(x) < 20:
            continue
        if stat == "anytime_td":
            pa, pb = x["mean"].clip(1e-4, 1 - 1e-4), x.mean_b.clip(1e-4, 1 - 1e-4)
            ll = lambda p: float(-(x.actual * np.log(p) + (1 - x.actual) * np.log(1 - p)).mean())
            out["stats"].append(dict(stat=stat, n=int(len(x)), main=round(ll(pa), 4), blind=round(ll(pb), 4),
                                     metric="log-loss"))
            continue
        x = x.dropna(subset=["pit", "pit_b"])
        out["stats"].append(dict(stat=stat, n=int(len(x)),
                                 main=round(float((x.actual - x["median"]).abs().mean()), 2),
                                 blind=round(float((x.actual - x.median_b).abs().mean()), 2),
                                 in80_main=round(float(x.pit.between(.1, .9).mean()), 3),
                                 in80_blind=round(float(x.pit_b.between(.1, .9).mean()), 3), metric="median error"))
    tmap = {t["game_id"]: t for t in tb}
    pts = [(p, pb, sc) for t in tm if t["final"] and t["game_id"] in tmap and None not in t["score"]
           for p, pb, sc in zip(t["proj"], tmap[t["game_id"]]["proj"], t["score"])]
    if pts:
        a = np.array(pts, dtype=float)
        out["points"] = dict(n=len(a), main=round(float(np.abs(a[:, 0] - a[:, 2]).mean()), 2),
                             blind=round(float(np.abs(a[:, 1] - a[:, 2]).mean()), 2))
    y = m[m.stat.isin(["pass_yds", "rush_yds", "rec_yds"])].dropna(subset=["pit", "pit_b"])
    for w, x in y.groupby("week"):
        em, eb = (x.actual - x["median"]).abs().mean(), (x.actual - x.median_b).abs().mean()
        out["weeks"].append(dict(week=int(w), n=int(len(x)), main=round(float(em), 2), blind=round(float(eb), 2),
                                 in80_main=round(float(x.pit.between(.1, .9).mean()), 3),
                                 in80_blind=round(float(x.pit_b.between(.1, .9).mean()), 3)))
    return out


def _blind_page(stem):
    b = os.path.basename(stem)
    return f"blind/weeks/{b}.html" if os.path.exists(os.path.join(BLIND_WEEKS_DIR, f"{b}.html")) else None


def _grade_dir(base, act):
    out = {}
    for stem in sorted({p[:-len("_players.csv")] for p in glob.glob(os.path.join(base, "*_players.csv"))}):
        season, week, g, trows, void = grade_week(stem, act)
        out.setdefault(season, []).append((week, stem, g, trows, void))
    return out


def grade_model(base, act, page_dir, other_page):
    """Grade one model's archive. Returns {season: {"weeks": ..., "diag": ...}} plus raw frames."""
    seasons, raw = {}, {}
    for stem in sorted({p[:-len("_players.csv")] for p in glob.glob(os.path.join(base, "*_players.csv"))}):
        season, week, g, trows, void = grade_week(stem, act)
        b = os.path.basename(stem)
        S = seasons.setdefault(season, {"weeks": {}})
        R = raw.setdefault(season, {"g": [], "t": []})
        links = dict(page=f"{page_dir}/{b}.html", other_page=other_page(stem))
        fg, fc = grade_first(stem, set(act[2].game_id))
        R.setdefault("fg", []).extend(fg)
        R.setdefault("fc", []).extend(fc)
        links["first"] = fg
        if g.empty:
            S["weeks"][week] = dict(games=trows, rows=[], summary=None, misses=[], void=void, **links)
            continue
        S["weeks"][week] = dict(games=trows, rows=review_rows(g), summary=summarize(g),
                                misses=misses(g, trows), void=void, **links)
        R["g"].append(g)
        R["t"] += trows
    frames = {}
    for season, R in raw.items():
        g = pd.concat(R["g"]) if R["g"] else pd.DataFrame()
        seasons[season]["diag"] = diagnostics(g, R["t"]) if len(g) else None
        seasons[season]["first"] = first_summary(R.get("fg", []), R.get("fc", []))
        frames[season] = (g, R["t"])
    return seasons, frames


def _vegas_page(stem):
    b = os.path.basename(stem)
    return f"weeks/{b}.html" if os.path.exists(os.path.join(WEEKS_DIR, f"{b}.html")) else None


DRAFT = {}   # player_id -> (rookie season, draft pick); filled by build()


def build(db="nfl.db", out=OUT):
    con = sqlite3.connect(db)
    act = load_actuals(con)
    from boxscore import data as BD
    FIRST.update(load_first_tds(con))
    pos = BD.positions(con)
    DRAFT.update({p: (None if pd.isna(y) else int(y), None if pd.isna(n) else int(n))
                  for p, y, n in zip(pos.player_id, pos.rookie_year, pos.draft_number)})
    vegas, vf = grade_model(WEEKS_DIR, act, "weeks", _blind_page)
    blind, bf = grade_model(BLIND_WEEKS_DIR, act, "blind/weeks", _vegas_page)
    for season, S in vegas.items():
        if season in vf and season in bf and len(vf[season][0]) and len(bf[season][0]):
            S["compare"] = compare(vf[season][0], bf[season][0], vf[season][1], bf[season][1])
    data = dict(generated=dt.datetime.now().strftime("%b %d, %Y %I:%M %p").replace(" 0", " "),
                labels=LABELS, models={"vegas": vegas, "blind": blind},
                nav=[["This week", "index.html"], ["Results", "results.html", True]])
    payload = json.dumps(_clean(data), separators=(",", ":"), default=_json,
                         allow_nan=False).replace("</", "<\\/")
    html = TEMPLATE.replace("__FONTS__", report.FONTS).replace("__CSS__", report.BASE_CSS) \
        .replace("__DATA__", payload)
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    return out


def _clean(o):
    """NaN is not valid JSON (the page's JSON.parse would fail): turn it into null."""
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, (float, np.floating)) and not np.isfinite(o):
        return None
    return o


def _json(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return None if np.isnan(o) else float(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    raise TypeError(type(o))


TEMPLATE = r"""<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Sim Sheet Results</title>
__FONTS__<style>
__CSS__
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:10px}
.tile{background:var(--sunk);border-radius:8px;padding:12px 14px;display:grid;gap:2px}
.tile .v{font-family:var(--display);font-weight:700;font-size:34px;line-height:1}
.tile .s{font-size:12.5px;color:var(--muted)}
.strip .a{position:absolute;top:4px;width:12px;height:12px;border-radius:50%;background:var(--ink);box-shadow:0 0 0 2px var(--surface);transform:translateX(-6px)}
.strip .a.out{background:var(--bad)}
.res{font-size:12px;font-weight:600;white-space:nowrap}
.res.out{color:var(--bad)}
.res.in{color:var(--muted);font-weight:500}
.sug{display:grid;gap:10px;margin:0;padding:0;list-style:none}
.sug li{display:grid;gap:2px;padding:10px 12px;border-radius:8px;background:var(--sunk)}
.sug b{font-weight:600}
.lvl{display:inline-block;font-size:11px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;margin-right:8px}
.lvl.fix{color:var(--bad)} .lvl.watch{color:var(--away)} .lvl.ok{color:var(--good)}
.verdict{font-size:12px;font-weight:600}
.verdict.ok{color:var(--muted);font-weight:500}
.verdict.bad{color:var(--bad)}
.split{display:flex;height:12px;border-radius:2px;overflow:hidden;min-width:110px;gap:2px}
.split i{display:block}
.recon{font-size:11px;color:var(--muted);border:1px dashed var(--faint);border-radius:4px;padding:0 5px;white-space:nowrap}
.gscore{font-family:var(--display);font-weight:600;font-size:17px}
.linkbtns{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
.lbtn{display:inline-block;padding:6px 12px;border-radius:7px;border:1px solid var(--faint);color:var(--ink);text-decoration:none;font-weight:600;font-size:13.5px;background:var(--sunk)}
.lbtn:hover{border-color:var(--accent)}
a{color:var(--accent)}
</style>

<div class="wrap">
  <nav class="sitenav" id="sitenav" aria-label="Site"></nav>
  <header>
    <div>
      <div class="eyebrow" id="sub"></div>
      <h1>Results</h1>
    </div>
    <div class="tabs" role="tablist" aria-label="View">
      <button role="tab" data-rview="week">Week review</button>
      <button role="tab" data-rview="improve">What to improve</button>
      <button role="tab" data-rview="beat">Above the range</button>
      <button role="tab" data-rview="compare">Compare models</button>
    </div>
  </header>
  <div class="controls" id="controls"></div>
  <main id="main"></main>
  <footer>Each week's projections are the last ones made before kickoff. Weeks marked "reconstructed" were rebuilt afterwards from data available at kickoff, because the live projections weren't saved then. Players who didn't play are left out, as their props would be voided. A calibrated model puts about 80% of results inside its 80% range and 50% inside the middle half.</footer>
</div>
<div id="tip" hidden></div>
<script type="application/json" id="data">__DATA__</script>
<script>
const D = JSON.parse(document.getElementById('data').textContent);
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const f0 = v => v == null ? '–' : Math.round(v).toString();
const f1 = v => v == null ? '–' : (Math.round(v*10)/10).toFixed(1);
const pct = p => p == null ? '–' : Math.round(p*100) + '%';
const spct = p => p == null ? '–' : (p >= 0 ? '+' : '−') + Math.abs(Math.round(p*100)) + '%';
const niceCeil = v => { if (v<=0) return 1; const e = 10**Math.floor(Math.log10(v)); for (const m of [1,1.2,1.5,2,2.5,3,4,5,6,8,10]) if (m*e >= v - 1e-9) return m*e; };
const L = D.labels;
const MODELS = {vegas: 'With Vegas', blind: 'Market-blind'};
const state = {view:'week', model:'vegas', season: null, week: null, game: null};
const seasons = () => Object.keys(D.models[state.model]).map(Number).sort((a,b) => b - a);
state.season = seasons()[0];
try { const s = JSON.parse(localStorage.getItem('simsheet-results') || '{}'); if (s.view) state.view = s.view; } catch (e) {}
if (location.hash === '#improve') state.view = 'improve';
{ const h = new URLSearchParams(location.hash.slice(1));
  if (h.get('model') in MODELS) state.model = h.get('model');
  if (h.get('week')) { const [ss, ww] = h.get('week').split('-').map(Number); if (ss) state.season = ss; if (ww) state.week = ww; } }
if (location.hash === '#compare') state.view = 'compare';
if (location.hash === '#beat') state.view = 'beat';
state.bstat = 'all'; state.bmin = 3;
const S = () => D.models[state.model][state.season] || D.models[state.model][seasons()[0]];
const weeks = () => Object.keys(S().weeks).map(Number).sort((a,b) => a - b);
function pickDefaults(){
  const graded = weeks().filter(w => S().weeks[w].summary);
  if (!weeks().includes(state.week)) state.week = graded.length ? graded[graded.length - 1] : weeks()[0];
  const W = S().weeks[state.week];
  if (!W.games.some(g => g.game_id === state.game)) state.game = (W.games.find(g => g.final) || W.games[0] || {}).game_id;
}
document.getElementById('sub').textContent = `Projections graded against final box scores · built ${D.generated}`;
document.getElementById('sitenav').innerHTML = D.nav.map(([l, h, c]) => `<a href="${esc(h)}"${c ? ' aria-current="page"' : ''}>${esc(l)}</a>`).join('');

function tiles(sm, void_n){
  if (!sm) return '<p class="note">No graded games yet this week.</p>';
  const t = (v, label, sub) => `<div class="tile"><span class="eyebrow">${label}</span><span class="v">${v}</span><span class="s">${sub}</span></div>`;
  return `<div class="tiles">
    ${t(sm.n, 'Lines graded', void_n ? `${void_n} voided (player didn't play)` : 'props-style lines')}
    ${t(pct(sm.in80), 'Inside 80% range', 'target 80%')}
    ${t(pct(sm.in50), 'Inside middle half', 'target 50%')}
    ${t(pct(sm.over), 'Beat the median', 'target about 50%')}
    ${t(`${sm.td_act} <span class="s">of ${f1(sm.td_exp)}</span>`, 'TD scorers', `actual vs expected among ${sm.td_n} players`)}
  </div>`;
}

function gameBtn(g){
  const sc = g.final ? `${f0(g.score[0])}–${f0(g.score[1])}` : 'awaiting stats';
  return `<button class="game-btn" data-game="${esc(g.game_id)}" aria-current="${g.game_id===state.game}">
    <span class="m"><span class="teams">${esc(g.away)} @ ${esc(g.home)}</span><span class="line">${sc}</span></span>
    <span class="line">proj ${f1(g.proj[0])}–${f1(g.proj[1])}${g.source === 'reconstructed' ? ' · reconstructed' : ''}</span></button>`;
}

function resultCell(r){
  if (r.stat === 'anytime_td') return r.actual > 0 ? '<span class="res">Scored</span>' : '<span class="res in">No TD</span>';
  if (r.pit == null) return '';
  if (r.pit >= .9) return `<span class="res out">▲ above range</span>`;
  if (r.pit <= .1) return `<span class="res out">▼ below range</span>`;
  return `<span class="res in">in range</span>`;
}
function strip(r, lo, hi){
  const x = v => ((v - lo) / (hi - lo)) * 100, out = r.pit != null && (r.pit > .9 || r.pit < .1);
  return `<div class="strip" tabindex="0" data-tip="${esc(`<b>${r.player}</b><br>Projected median ${f1(r.median)} (8 in 10: ${f1(r.p10)}–${f1(r.p90)})<br>Actual ${f1(r.actual)}${r.pit != null ? ` · ${Math.round(r.pit*100)}th percentile` : ''}`)}">
    <i class="w" style="left:${x(r.p10)}%;width:${Math.max(x(r.p90)-x(r.p10),.5)}%"></i>
    <i class="b" style="left:${x(r.p25)}%;width:${Math.max(x(r.p75)-x(r.p25),.5)}%"></i>
    <i class="m" style="left:${x(r.median)}%"></i>
    <i class="a${out ? ' out' : ''}" style="left:${Math.min(100, Math.max(0, x(r.actual)))}%"></i></div>`;
}

function gameTable(W, g){
  const rows = W.rows.filter(r => r.game_id === g.game_id);
  if (!rows.length) return `<p class="note">${g.final ? 'No props-style lines for this game.' : 'Final play-by-play not published yet. Re-run results.py after it posts.'}</p>`;
  let html = '';
  for (const stat of ['pass_yds','rush_yds','rec_yds','rec','anytime_td']){
    const rs = rows.filter(r => r.stat === stat).sort((a,b) => b.median - a.median || b.mean - a.mean);
    if (!rs.length) continue;
    if (stat === 'anytime_td'){
      html += `<tr class="grp"><td colspan="6">${L[stat]}</td></tr>` + rs.map(r => `<tr><td class="l name">${esc(r.player)} <span class="small muted">${esc(r.team)}</span></td><td class="l"><span class="pos">${esc(r.label || r.pos)}</span></td><td class="l"><div class="pbar"><i style="width:${r.mean*100}%"></i></div></td><td>${pct(r.mean)}</td><td class="big">${r.actual > 0 ? 'Yes' : 'No'}</td><td class="l">${resultCell(r)}</td></tr>`).join('');
      continue;
    }
    const hi = niceCeil(Math.max(...rs.map(r => Math.max(r.p90, r.actual)))), lo = Math.min(0, ...rs.map(r => Math.min(r.p10, r.actual)));
    html += `<tr class="grp"><td colspan="6">${L[stat]}</td></tr>` + rs.map(r => `<tr><td class="l name">${esc(r.player)} <span class="small muted">${esc(r.team)}</span>${r.limited ? ' <span class="recon" title="Played under half his usual snaps">limited snaps</span>' : ''}</td><td class="l"><span class="pos">${esc(r.label || r.pos)}</span></td><td class="l">${strip(r, lo, hi)}</td><td>${f1(r.median)}</td><td class="big">${f1(r.actual)}</td><td class="l">${resultCell(r)}</td></tr>`).join('');
  }
  return `<div class="tw"><table class="t"><thead><tr><th class="l">Player</th><th class="l">Role</th><th class="sc l">Projected range · <span style="text-transform:none">● actual</span></th><th>Median</th><th>Actual</th><th class="l">Result</th></tr></thead><tbody>${html}</tbody></table></div>`;
}

function missList(ms){
  if (!ms || !ms.length) return '<p class="note">No results outside the 3rd–97th percentile.</p>';
  return `<ul class="sug">${ms.map(m => `<li><span><b>${esc(m.player)}</b> <span class="small muted">${esc(m.team)} vs ${esc(m.opp)} · week ${m.week}${m.label ? ' · ' + esc(m.label) : ''}</span></span>
    <span>${L[m.stat]}: <b>${f1(m.actual)}</b> vs median ${f1(m.median)} <span class="muted small">(8 in 10: ${f1(m.p10)}–${f1(m.p90)})</span> <span class="res out">${m.dir > 0 ? '▲' : '▼'}</span></span>
    <span class="small muted">${m.reasons.map(esc).join(' · ')}</span></li>`).join('')}</ul>`;
}

// First TD scorer grading. Backtest (2024-26, 618 games) for reference until live weeks build up.
const FIRST_BT = 'Backtest 2024–26 (618 games): top pick scored first 14.1% of the time (predicted 15.0%); one of the top 3 did 34.8% (35.8%).';
const pct1 = p => p == null ? '–' : (p < .1 ? (Math.round(p*1000)/10).toFixed(1) : Math.round(p*100)) + '%';
function firstSummaryLine(fs){
  if (!fs) return `<p class="note" style="margin:0">No graded games with first-TD projections yet (they started in week 4, 2026). ${FIRST_BT}</p>`;
  return `<p style="margin:0">Top pick scored first in <b>${fs.top_hits} of ${fs.games}</b> games (expected ${f1(fs.top_exp)}); one of the top 3 in <b>${fs.top3_hits}</b> (expected ${f1(fs.top3_exp)}). Listed players scored the first TD in ${fs.listed_act} games (expected ${f1(fs.listed_pred)}); defense or special teams in ${fs.dst}.</p><p class="note" style="margin:0">${FIRST_BT}</p>`;
}
function firstPanel(W, label){
  const F = W.first || [];
  if (!F.length) return '';
  const name = id => { const g = W.games.find(x => x.game_id === id); return g ? `${esc(g.away)} @ ${esc(g.home)}` : esc(id); };
  const who = r => r.kind === 'player' ? `<b>${esc(r.scorer)}</b> <span class="small muted">${esc(r.team)}</span>`
    : r.kind === 'none' ? '<span class="muted">No touchdown</span>' : `<span>${esc(r.scorer)}</span>`;
  const ours = r => r.kind === 'player' ? `${pct1(r.p)} <span class="small muted">(#${r.rank} of ${r.n})</span>` : '<span class="small muted">not a listed player</span>';
  const rows = F.map(r => `<tr><td class="l">${name(r.game_id)}</td><td class="l">${who(r)}</td><td>${ours(r)}</td><td class="l">${esc(r.top)} <span class="small muted">${esc(r.top_team)} · ${pct1(r.top_p)}</span></td><td class="l">${r.top_hit ? '<span class="res">✓ hit</span>' : r.top3_hit ? '<span class="res in">top 3</span>' : '<span class="res in">–</span>'}</td></tr>`).join('');
  const hits = F.filter(r => r.top_hit).length, exp = F.reduce((a, r) => a + r.top_p, 0);
  return `<section class="panel"><div class="phead"><h2>First TD scorer${label}</h2><span class="small muted">top pick hit ${hits} of ${F.length} (expected ${f1(exp)})</span></div>
    <div class="tw"><table class="t"><thead><tr><th class="l">Game</th><th class="l">Scored first</th><th>Our chance</th><th class="l">Our top pick</th><th class="l">Result</th></tr></thead><tbody>${rows}</tbody></table></div></section>`;
}

function renderWeek(){
  pickDefaults();
  const W = S().weeks[state.week], g = W.games.find(x => x.game_id === state.game);
  const detail = g ? `<section class="panel">
      <div class="phead"><h2>${esc(g.away)} at ${esc(g.home)}</h2>${g.source === 'reconstructed' ? '<span class="recon" title="Rebuilt afterwards from data available at kickoff">reconstructed projection</span>' : '<span class="small muted">live pregame projection</span>'}</div>
      <div class="tiles">
        <div class="tile"><span class="eyebrow">${esc(g.away)}</span><span class="v">${f0(g.score[0])}</span><span class="s">projected ${f1(g.proj[0])} · ${state.model === 'blind' ? 'model forecast' : 'Vegas'} ${f1(g.vegas[0])} · win ${pct(g.win[0])}</span></div>
        <div class="tile"><span class="eyebrow">${esc(g.home)}</span><span class="v">${f0(g.score[1])}</span><span class="s">projected ${f1(g.proj[1])} · ${state.model === 'blind' ? 'model forecast' : 'Vegas'} ${f1(g.vegas[1])} · win ${pct(g.win[1])}</span></div>
      </div>
      <div class="key"><span><i class="kw"></i>8 in 10 games</span><span><i class="kb"></i>middle half</span><span><i class="km"></i>median</span><span>● actual (orange when outside the 8-in-10 range)</span></div>
      ${gameTable(W, g)}
    </section>` : '';
  return `<section class="panel"><div class="phead"><h2>Week ${state.week}, ${state.season} · ${MODELS[state.model]}</h2><span class="linkbtns"><span class="small muted">This week's projections:</span>${[[state.model, W.page], [state.model === 'vegas' ? 'blind' : 'vegas', W.other_page]].filter(x => x[1]).map(([m, h]) => `<a class="lbtn" href="${esc(h)}">${MODELS[m]}</a>`).join('')}</span></div>
      ${tiles(W.summary, W.void)}</section>
    <div class="games"><nav class="rail" aria-label="Games">${W.games.map(gameBtn).join('')}</nav><div class="detail">${detail}
      ${firstPanel(W, ' this week')}
      <section class="panel"><div class="phead"><h2>Biggest misses this week</h2><span class="small muted">results outside the 3rd–97th percentile</span></div>${missList(W.misses)}</section>
    </div></div>`;
}

const VERDICT = {ok: 'on target', high: 'projections too high', low: 'projections too low', narrow: 'ranges too narrow', wide: 'ranges too wide'};
function renderImprove(){
  const dg = S().diag;
  if (!dg) return '<section class="panel"><p class="note">No graded games for this season yet.</p></section>';
  const sm = dg.summary;
  const stats = dg.stats.map(r => `<tr><td class="l">${esc(L[r.stat])}</td><td class="l"><span class="pos">${r.pos}</span></td><td>${r.n}</td><td>${f1(r.proj)}</td><td>${f1(r.actual)}</td><td>${spct(r.bias)} <span class="small muted">±${pct(r.se)}</span></td><td>${pct(r.in80)}</td><td>${pct(r.in50)}</td><td>${f1(r.mae)}</td><td class="l"><span class="verdict ${r.verdict === 'ok' ? 'ok' : 'bad'}">${VERDICT[r.verdict]}</span></td></tr>`).join('');
  const dec = dg.dec.map(d => `<tr><td class="l">${esc(L[d.stat])}</td><td>${d.n}</td><td class="l"><div class="split" role="img" aria-label="${pct(d.vol_share)} volume"><i style="flex:${d.vol_share};background:var(--accent)"></i><i style="flex:${1-d.vol_share};background:var(--away)"></i></div></td><td>${pct(d.vol_share)} ${esc(d.unit)}</td><td>${pct(1-d.vol_share)} yds/${esc(d.one)}</td></tr>`).join('');
  const td = dg.td.map(b => `<tr><td class="l">${b.bucket}</td><td>${b.n}</td><td>${pct(b.pred)}</td><td class="big">${pct(b.act)}</td></tr>`).join('');
  const tm = dg.team, fl = dg.flags;
  return `<section class="panel"><div class="phead"><h2>What to improve · ${state.season} · ${MODELS[state.model]}</h2><span class="small muted">${sm.n} graded lines · ${sm.limited} early exits</span></div>
      ${tiles(sm, 0)}
      <ul class="sug">${dg.suggestions.map(s => `<li><span><span class="lvl ${s.level}">${s.level === 'fix' ? 'Fix' : s.level === 'watch' ? 'Watch' : 'OK'}</span><b>${esc(s.title)}</b></span><span class="small muted">${esc(s.detail)}</span></li>`).join('')}</ul></section>
    <section class="panel"><div class="phead"><h2>Calibration by stat</h2><span class="small muted">bias = actual vs projected average; target 80% / 50% coverage</span></div>
      <div class="tw"><table class="t"><thead><tr><th class="l">Stat</th><th class="l">Pos</th><th>Lines</th><th>Proj avg</th><th>Actual avg</th><th>Bias</th><th>In 80%</th><th>In 50%</th><th>Median error</th><th class="l">Verdict</th></tr></thead><tbody>${stats}</tbody></table></div></section>
    ${dg.slots && dg.slots.length ? `<section class="panel"><div class="phead"><h2>Receiving yards by depth chart</h2><span class="small muted">is a role group running high or low?</span></div>
      <div class="tw"><table class="t"><thead><tr><th class="l">Slot</th><th>Lines</th><th>Proj avg</th><th>Actual avg</th><th>Bias</th><th>Beat median</th><th>Above middle half</th></tr></thead><tbody>${dg.slots.map(r => `<tr><td class="l"><span class="pos">${r.slot}</span></td><td>${r.n}</td><td>${f1(r.proj)}</td><td>${f1(r.actual)}</td><td class="big">${spct(r.bias)}</td><td>${pct(r.over)}</td><td>${pct(r.above)}</td></tr>`).join('')}</tbody></table></div>
      <p class="note">Calibrated: bias near 0, about 50% beat the median and 25% finish above the middle half. Early-season swings are normal: in weeks 1–3, WR1s ran 12% under projection in 2024 and 6% under in 2025, then evened out by midseason.</p></section>` : ''}
    <div class="two" style="grid-template-columns:repeat(auto-fit,minmax(320px,1fr))">
      <section class="panel"><div class="phead"><h2>Where yardage misses come from</h2></div>
        <div class="tw"><table class="t"><thead><tr><th class="l">Stat</th><th>Lines</th><th class="l">Volume vs efficiency</th><th>Volume</th><th>Efficiency</th></tr></thead><tbody>${dec}</tbody></table></div>
        <p class="note">Each miss is split into getting more or fewer touches than projected (volume) and gaining more or fewer yards per touch (efficiency).</p></section>
      <section class="panel"><div class="phead"><h2>Anytime TD calibration</h2></div>
        <div class="tw"><table class="t"><thead><tr><th class="l">Projected chance</th><th>Players</th><th>Projected</th><th>Scored</th></tr></thead><tbody>${td}</tbody></table></div></section>
    </div>
    <div class="two" style="grid-template-columns:repeat(auto-fit,minmax(320px,1fr))">
      <section class="panel"><div class="phead"><h2>Team scoring</h2></div>${tm ? `<p style="margin:0">Average points error <b>${f1(tm.mae_model)}</b> for the model vs <b>${f1(tm.mae_vegas)}</b> for Vegas implied totals, over ${tm.n} games. Favorites won ${pct(tm.fav_won)}; win-probability Brier score ${tm.brier}.</p>` : '<p class="note">No final games.</p>'}</section>
      <section class="panel"><div class="phead"><h2>Matchup flags check</h2></div><p style="margin:0">Favorable-flagged players beat their median <b>${pct(fl.favorable.beat)}</b> of the time (${fl.favorable.n} lines); tough-flagged players <b>${pct(fl.tough.beat)}</b> (${fl.tough.n}). No edge would be about 50% for both.</p></section>
    </div>
    <section class="panel"><div class="phead"><h2>First TD scorer · ${state.season}</h2></div>${firstSummaryLine(S().first)}
      ${S().first && S().first.buckets.length ? `<div class="tw"><table class="t"><thead><tr><th class="l">Our first-TD chance</th><th>Players</th><th>Predicted</th><th>Scored first</th></tr></thead><tbody>${S().first.buckets.map(b => `<tr><td class="l">${b.bucket}</td><td>${b.n}</td><td>${pct1(b.pred)}</td><td class="big">${pct1(b.act)}</td></tr>`).join('')}</tbody></table></div>` : ''}</section>
    <section class="panel"><div class="phead"><h2>Biggest misses this season</h2><span class="small muted">results outside the 3rd–97th percentile</span></div>${missList(dg.misses)}</section>`;
}

function renderCompare(){
  const c = (D.models.vegas[state.season] || {}).compare;
  if (!c) return '<section class="panel"><p class="note">No market-blind projections graded for this season yet.</p></section>';
  const win = (a, b, lowerBetter = true) => a === b ? 'tie' : ((a < b) === lowerBetter ? 'main' : 'blind');
  const cell = (v, w, who) => `<td class="${w === who ? 'big' : 'muted'}">${v}${w === who ? ' ✓' : ''}</td>`;
  const rows = c.stats.map(r => { const w = win(r.main, r.blind);
    return `<tr><td class="l">${esc(L[r.stat])}</td><td class="l small muted">${r.metric}</td><td>${r.n}</td>${cell(r.metric === 'log-loss' ? r.main.toFixed(4) : f1(r.main), w, 'main')}${cell(r.metric === 'log-loss' ? r.blind.toFixed(4) : f1(r.blind), w, 'blind')}<td>${r.in80_main == null ? '–' : pct(r.in80_main)}</td><td>${r.in80_blind == null ? '–' : pct(r.in80_blind)}</td></tr>`; }).join('');
  const pts = c.points ? `<p style="margin:0">Team points error: <b>${f1(c.points.main)}</b> with Vegas vs <b>${f1(c.points.blind)}</b> market-blind, over ${c.points.n} team-games.</p>` : '';
  const wk = c.weeks.map(r => { const w = win(r.main, r.blind);
    return `<tr><td class="l">Week ${r.week}</td><td>${r.n}</td>${cell(f1(r.main), w, 'main')}${cell(f1(r.blind), w, 'blind')}<td>${pct(r.in80_main)}</td><td>${pct(r.in80_blind)}</td></tr>`; }).join('');
  const wins = c.weeks.reduce((a, r) => (a[win(r.main, r.blind)]++, a), {main: 0, blind: 0, tie: 0});
  return `<section class="panel"><div class="phead"><h2>Vegas-anchored vs market-blind · ${state.season}</h2><span class="small muted">${c.n} matched player-lines</span></div>
      ${pts}
      <div class="tw"><table class="t"><thead><tr><th class="l">Stat</th><th class="l">Measure</th><th>Lines</th><th>With Vegas</th><th>Market-blind</th><th>In 80% (Vegas)</th><th>In 80% (blind)</th></tr></thead><tbody>${rows}</tbody></table></div>
      <p class="note">Both models are scored on exactly the same player-games. Lower error and lower log-loss are better; ✓ marks the better model. A calibrated model has about 80% of results inside its 80% range.</p></section>
    <section class="panel"><div class="phead"><h2>Week by week (passing, rushing and receiving yards)</h2><span class="small muted">Vegas better ${wins.main} · blind better ${wins.blind}${wins.tie ? ' · tied ' + wins.tie : ''}</span></div>
      <div class="tw"><table class="t"><thead><tr><th class="l">Week</th><th>Lines</th><th>Error with Vegas</th><th>Error blind</th><th>In 80% (Vegas)</th><th>In 80% (blind)</th></tr></thead><tbody>${wk}</tbody></table></div></section>`;
}

// "Above the range": who finished above the projected middle half (above the 75th percentile).
const BSTATS = {all: 'All yardage + catches', pass_yds: L.pass_yds, rush_yds: L.rush_yds, rec_yds: L.rec_yds, rec: L.rec};
function renderBeat(){
  pickDefaults();
  const ok = r => r.stat !== 'anytime_td' && r.pit != null && (state.bstat === 'all' || r.stat === state.bstat);
  const all = weeks().flatMap(w => S().weeks[w].rows.map(r => ({...r, week: w}))).filter(ok);
  const by = {};
  for (const r of all){
    const p = by[r.player_id] ||= {player: r.player, team: r.team, pos: r.label || r.pos, rk: r.rk, n: 0, above: 0, top: 0, below: 0, pit: 0, weeks: new Set()};
    p.n++; p.pit += r.pit; p.weeks.add(r.week); p.team = r.team;
    if (r.pit > .75) p.above++; if (r.pit > .9) p.top++; if (r.pit < .25) p.below++;
  }
  const players = Object.values(by).filter(p => p.weeks.size >= state.bmin)
    .sort((a, b) => b.above / b.n - a.above / a.n || b.above - a.above).slice(0, 60);
  const rate = p => p.above / p.n;
  const rateBar = v => `<div class="pbar" style="min-width:120px" role="img" aria-label="${pct(v)}"><i style="width:${Math.min(v, 1) * 100}%;${v >= .4 ? '' : 'opacity:.55'}"></i><b style="position:absolute;left:25%;top:-3px;bottom:-3px;width:1px;background:var(--ink)"></b></div>`;
  const lb = players.map(p => `<tr><td class="l name">${esc(p.player)}${p.rk ? ' <span class="recon" title="Rookie drafted in the top 64">early-round rookie</span>' : ''}</td><td class="l">${esc(p.team)}</td><td class="l"><span class="pos">${esc(p.pos)}</span></td><td>${p.weeks.size}</td><td>${p.n}</td><td class="big">${p.above}</td><td class="l">${rateBar(rate(p))}</td><td>${pct(rate(p))}</td><td>${p.top}</td><td>${p.below}</td><td>${Math.round(p.pit / p.n * 100)}</td></tr>`).join('');
  const W = S().weeks[state.week];
  const wkAll = (W ? W.rows : []).filter(ok);
  const wg = {};
  for (const r of wkAll){
    const p = wg[r.player_id] ||= {r, lines: [], n: 0};
    p.n++;
    if (r.pit > .75) p.lines.push(r);
  }
  const wk = Object.values(wg).filter(p => p.lines.length).map(p => ({...p, best: Math.max(...p.lines.map(x => x.pit))}))
    .sort((a, b) => b.lines.length / b.n - a.lines.length / a.n || b.best - a.best);
  const chip = x => `<span style="white-space:nowrap;margin-right:14px">${esc(L[x.stat])} <b>${f1(x.actual)}</b> <span class="small muted">(middle half ${f1(x.p25)}–${f1(x.p75)}, ${Math.round(x.pit * 100)}th)</span>${x.pit > .9 ? ' <span class="res out">▲</span>' : ''}</span>`;
  const wkRows = wk.map(p => `<tr><td class="l name">${esc(p.r.player)}${p.r.rk ? ' <span class="recon">early-round rookie</span>' : ''}</td><td class="l">${esc(p.r.team)} <span class="small muted">vs ${esc(p.r.opp || '')}</span></td><td class="l"><span class="pos">${esc(p.r.label || p.r.pos)}</span></td><td>${p.lines.length} of ${p.n}</td><td class="l" style="white-space:normal">${p.lines.sort((a, b) => b.pit - a.pit).map(chip).join('')}</td></tr>`).join('');
  const statSeg = `<div class="seg" role="group" aria-label="Stat">${Object.entries(BSTATS).map(([k, l]) => `<button data-bstat="${k}" aria-pressed="${k === state.bstat}">${esc(l)}</button>`).join('')}</div>`;
  const minSeg = `<div class="seg" role="group" aria-label="Minimum games">${[1, 3, 6, 10].map(m =>`<button data-bmin="${m}" aria-pressed="${m === state.bmin}">${m}+ games</button>`).join('')}</div>`;
  return `<section class="panel"><div class="phead"><h2>Above the middle half · ${state.season} · ${MODELS[state.model]}</h2><div class="controls">${statSeg}${minSeg}</div></div>
      <p class="note" style="margin:0">The middle half is the projected 25th–75th percentile range. A player should finish above it about 1 game-line in 4 (25%, the black tick). Sorted by how often they did.</p>
      ${players.length ? `<div class="tw"><table class="t"><thead><tr><th class="l">Player</th><th class="l">Team</th><th class="l">Role</th><th>Games</th><th>Lines</th><th>Above middle half</th><th class="l">Rate (tick = 25% expected)</th><th>Rate</th><th>Above 80% range</th><th>Below middle half</th><th>Avg percentile</th></tr></thead><tbody>${lb}</tbody></table></div>` : '<p class="note">No players with enough graded games yet.</p>'}
      <p class="note">What the backtest (2024–2026, every player-game) says about using this: a veteran's past rate of beating the range does <b>not</b> predict his next game. Hot streaks and early-season leaders came back to 25%. Early-round rookies (top-64 picks) were the exception: they finished above the middle half about 31% of the time and below it about 20%, in both 2024 and 2025–26, because the model was slow to credit their role. As of Sep 30, 2026 the model gives them 15% more projected targets and carries, which cut that gap from +14% to +4% in 2025–26 testing. Weeks graded before then used the earlier model.</p></section>
    <section class="panel"><div class="phead"><h2>Week ${state.week}: finished above the middle half</h2><span class="small muted">${wk.length} players · pick another week above</span></div>
      ${wk.length ? `<div class="tw"><table class="t"><thead><tr><th class="l">Player</th><th class="l">Game</th><th class="l">Role</th><th>Lines above</th><th class="l">Above the middle half (▲ = above the 80% range too)</th></tr></thead><tbody>${wkRows}</tbody></table></div>` : '<p class="note">None graded this week yet.</p>'}</section>`;
}

function render(){
  for (const b of document.querySelectorAll('.tabs button')) b.setAttribute('aria-selected', b.dataset.rview === state.view);
  pickDefaults();
  if (!seasons().includes(state.season)) state.season = seasons()[0];
  const modelSeg = state.view === 'compare' ? '' : `<div class="seg" role="group" aria-label="Model">${Object.entries(MODELS).map(([k, l]) => `<button data-model="${k}" aria-pressed="${k===state.model}">${l}</button>`).join('')}</div>`;
  const seasonSeg = `<div class="seg" role="group" aria-label="Season">${seasons().map(s => `<button data-season="${s}" aria-pressed="${s===state.season}">${s}</button>`).join('')}</div>`;
  const weekSeg = state.view === 'week' || state.view === 'beat' ? `<div class="seg" role="group" aria-label="Week">${weeks().map(w => `<button data-week="${w}" aria-pressed="${w===state.week}">${w > 18 ? ['WC','DIV','CONF','SB'][w-19] || 'Wk ' + w : 'Wk ' + w}</button>`).join('')}</div>` : '';
  document.getElementById('controls').innerHTML = modelSeg + seasonSeg + weekSeg;
  document.getElementById('main').innerHTML = state.view === 'improve' ? renderImprove()
    : state.view === 'compare' ? renderCompare() : state.view === 'beat' ? renderBeat() : renderWeek();
  try { localStorage.setItem('simsheet-results', JSON.stringify({view: state.view})); } catch (e) {}
}
document.addEventListener('click', e => {
  const t = e.target.closest('button'); if (!t) return;
  if (t.dataset.rview){ state.view = t.dataset.rview; render(); }
  else if (t.dataset.model){ state.model = t.dataset.model; state.game = null; render(); }
  else if (t.dataset.season){ state.season = +t.dataset.season; state.week = null; render(); }
  else if (t.dataset.week){ state.week = +t.dataset.week; state.game = null; render(); }
  else if (t.dataset.game){ state.game = t.dataset.game; render(); }
  else if (t.dataset.bstat){ state.bstat = t.dataset.bstat; render(); }
  else if (t.dataset.bmin){ state.bmin = +t.dataset.bmin; render(); }
});
const tip = document.getElementById('tip');
function showTip(el, x, y){ tip.innerHTML = el.dataset.tip; tip.hidden = false; const r = tip.getBoundingClientRect();
  tip.style.left = Math.min(Math.max(8, x - r.width/2), innerWidth - r.width - 8) + 'px'; tip.style.top = (y - r.height - 12 < 8 ? y + 18 : y - r.height - 12) + 'px'; }
document.addEventListener('pointermove', e => { const el = e.target.closest('[data-tip]'); if (el) showTip(el, e.clientX, e.clientY); else tip.hidden = true; });
document.addEventListener('focusin', e => { const el = e.target.closest('[data-tip]'); if (el){ const r = el.getBoundingClientRect(); showTip(el, r.left + r.width/2, r.top); } });
document.addEventListener('focusout', () => { tip.hidden = true; });
render();
</script>
"""


if __name__ == "__main__":
    print("Wrote", build())
