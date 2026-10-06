#!/usr/bin/env python3
"""
HR blend: a batter-game model stacked on top of the per-PA model, built to RANK hitters within a game.

Inputs per batter-game, all known before first pitch (from his first PA row, i.e. vs the starter):
  model    logit of the per-PA HR chance vs the starter (model.py) and expected PAs (lineup slot, home/away)
  batter   HR rate, power contact, fly-ball, barrel, hard-hit, pulled-air, K, BB rates; form; zone / mix fit
  pitcher  HR / power / barrel / hard-hit / pulled-air allowed, K rate, fastball velocity
  setting  platoon, park HR factor, temperature, wind, postseason
  mixes    power x pitcher velocity, fly-ball x temperature, pull x wind, fly-ball x park, power x platoon
Fit: gradient-boosted trees (sklearn) on every batter-game before the date. Output p_blend; within each game
the hitters are ranked by it (top 3 = the game's picks).

    python mlb/blend.py evaluate      # train on games before 2026-08-01, score the ranking Aug-Oct 2026
"""

import json
import os
import sqlite3
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import model as M          # noqa: E402

RAW = ["l_bhr", "l_bpc", "l_bfb", "l_b_brl", "l_b_hh", "l_b_pull", "l_b_k", "l_b_bb", "l_phr", "l_ppc", "l_p_brl", "l_p_hh",
       "l_p_pull", "l_p_k", "velo", "same", "l_park", "temp", "wind_out", "form", "mix", "heat", "post"]
SWING = ["la_at_sp", "plane_fit", "pa_", "pbz"]
FEATS = RAW + ["l_p1", "log_epa", "x_brl_velo", "x_fb_temp", "x_pull_wind", "x_fb_park", "x_brl_same"] + SWING
# swing plane (2026-10-06): hitter's launch angle ~ a + bz*height + bx*plate x, fit walk-forward from balls in play
# (ridge toward a league plane), evaluated at the starter's average location vs his side. Adding it lifted the
# Aug-Oct 2026 top-3 capture 0.515 -> 0.533 HR hitters/game and log-loss .33965 -> .33872, both halves.
MOM = ["n", "z", "x", "zz", "xx", "zx", "y", "zy", "xy"]
LG_PLANE = np.array([12.0, 15.0, 0.0])
PARAMS = dict(max_iter=350, learning_rate=.035, max_leaf_nodes=15, min_samples_leaf=250, l2_regularization=1.0, random_state=0)


def epa_table(df):
    h = df.dropna(subset=["slot"]).groupby(["game_pk", "top", "slot"]).size().rename("k").reset_index()
    h["home"] = 1 - h.top
    return h.groupby(["home", "slot"]).k.mean().to_dict()


def batter_games(df, coef, ek):
    """One row per batter-game: first-PA features (vs the starter) + outcome."""
    x = df.copy()
    x["p"] = M.predict(x, coef)
    x["home"] = 1 - x.top
    first = x.sort_values("idx").groupby(["game_pk", "batter"]).first()
    agg = x.groupby(["game_pk", "batter"]).agg(y=("hr", "max"), n_pa=("hr", "size"))
    out = first[RAW + ["p", "slot", "home", "date", "season", "type", "bat_team", "fld_team", "sp", "pitcher", "bat_side", "pitch_hand"]].join(agg).reset_index()
    out["side"] = np.where(out.bat_side == "S", np.where(out.pitch_hand == "R", "L", "R"), out.bat_side)
    return add_mixes(out, ek)


def add_mixes(d, ek):
    d = d.copy()
    d["l_p1"] = np.log(np.clip(d.p, 1e-5, 1) / np.clip(1 - d.p, 1e-5, 1))
    d["e_pa"] = [ek.get((h, s), 4.2) for h, s in zip(d.home, d.slot)]
    d["log_epa"] = np.log(d.e_pa)
    z = lambda c: (d[c] - d[c].mean()) / (d[c].std() or 1)
    d["x_brl_velo"] = z("l_b_brl") * -d.velo
    d["x_fb_temp"] = z("l_bfb") * d.temp
    d["x_pull_wind"] = z("l_b_pull") * d.wind_out
    d["x_fb_park"] = z("l_bfb") * d.l_park * 10
    d["x_brl_same"] = z("l_b_brl") * (1 - 2 * d.same)
    return d


def swing_tables(con, first_season, extra=None):
    """Walk-forward swing planes per (batter, season, date) and starters' average location per (pitcher, side,
    season, date). extra: DataFrame(batter, pitcher, side, season, date) for future rows (today's games)."""
    p = pd.read_sql("""SELECT t.batter, t.pitcher, t.bat_side, t.pitch_hand, t.px, t.pz, t.sz_top, t.sz_bot, t.inplay, t.la, g.date, g.season
                       FROM pitch t JOIN games g USING(game_pk) WHERE g.season >= ?""", con, params=(first_season - 1,))
    p["zn"] = (p.pz - p.sz_bot) / (p.sz_top - p.sz_bot)
    p["side"] = np.where(p.bat_side == "S", np.where(p.pitch_hand == "R", "L", "R"), p.bat_side)
    c = p[(p.inplay == 1) & p.la.notna() & p.px.notna() & p.zn.notna()].copy()
    c["n"] = 1.0; c["z"] = c.zn; c["x"] = c.px; c["zz"] = c.zn ** 2; c["xx"] = c.px ** 2; c["zx"] = c.zn * c.px
    c["y"] = c.la; c["zy"] = c.zn * c.la; c["xy"] = c.px * c.la
    if extra is not None and len(extra):
        c = pd.concat([c, extra[["batter", "season", "date"]].assign(**{m: 0.0 for m in MOM})], ignore_index=True)
    day = c.groupby(["batter", "season", "date"])[MOM].sum().reset_index().sort_values(["batter", "date"])
    cum = day.groupby(["batter", "season"])[MOM].cumsum() - day[MOM]
    tot = c.groupby(["batter", "season"])[MOM].sum().reset_index(); tot["season"] += 1
    day = day[["batter", "season", "date"]].join(cum).merge(tot.rename(columns={m: m + "_p" for m in MOM}), on=["batter", "season"], how="left").fillna(0)
    for m in MOM:
        day[m] = day[m] + .6 * day[m + "_p"]
    n, z, x, zz, xx, zx, y, zy, xy = (day[m].to_numpy() for m in MOM)
    A = np.stack([np.stack([n, z, x], -1), np.stack([z, zz, zx], -1), np.stack([x, zx, xx], -1)], 1) + np.eye(3) * 30
    bvec = np.stack([y, zy, xy], -1) + 30 * LG_PLANE
    B = np.linalg.solve(A, bvec[..., None])[..., 0]
    day["pa_"], day["pbz"], day["pbx"] = B[:, 0], B[:, 1], B[:, 2]
    q = p[p.px.notna() & p.zn.notna()][["pitcher", "side", "season", "date", "zn", "px"]]
    if extra is not None and len(extra):
        q = pd.concat([q, extra[["pitcher", "side", "season", "date"]].assign(zn=np.nan, px=np.nan)], ignore_index=True)
    loc = q.groupby(["pitcher", "side", "season", "date"]).agg(sz=("zn", "sum"), sx=("px", "sum"), k=("zn", "count")).reset_index().sort_values(["pitcher", "side", "date"])
    cl = loc.groupby(["pitcher", "side", "season"])[["sz", "sx", "k"]].cumsum() - loc[["sz", "sx", "k"]]
    loc[["sz", "sx", "k"]] = cl.to_numpy()
    loc["mz"] = (loc.sz + 50 * .48) / (loc.k + 50); loc["mx"] = loc.sx / (loc.k + 50)
    return day[["batter", "season", "date", "pa_", "pbz", "pbx"]], loc[["pitcher", "side", "season", "date", "mz", "mx"]]


def attach_swing(d, tables):
    day, loc = tables
    d = d.merge(day, on=["batter", "season", "date"], how="left").merge(loc, on=["pitcher", "side", "season", "date"], how="left")
    for c_, v in (("pa_", 12.0), ("pbz", 15.0), ("pbx", 0.0), ("mz", .48), ("mx", 0.0)):
        d[c_] = d[c_].fillna(v)
    d["la_at_sp"] = d.pa_ + d.pbz * d.mz + d.pbx * d.mx
    d["plane_fit"] = -(d.la_at_sp - 22).abs()
    return d


def fit(train):
    from sklearn.ensemble import HistGradientBoostingClassifier
    gb = HistGradientBoostingClassifier(**PARAMS)
    gb.fit(train[FEATS], train.y)
    return gb


def evaluate():
    feats = json.load(open(os.path.join(HERE, "params.json")))["features"]
    df = M.full_frame()
    df = df[df.season >= 2025]
    cut = "2026-08-01"
    coef = M.fit(df[df.date < cut], feats=feats)
    ek = epa_table(df[df.date < cut])
    bg = batter_games(df, coef, ek)
    bg = bg[bg.slot.notna() & (bg.n_pa >= 2)]                 # starters (a sub inherits a slot but bats once or twice)
    tr, te = bg[bg.date < cut], bg[(bg.date >= cut)].copy()
    gb = fit(tr)
    te["blend"] = gb.predict_proba(te[FEATS])[:, 1]
    te["model"] = 1 - (1 - te.p) ** te.e_pa
    te["season_rate"] = te.l_bhr                               # batter HR rate only (shrunk, to date)
    from sklearn.linear_model import LogisticRegression
    lr = LogisticRegression(C=.5, max_iter=2000).fit((tr[FEATS] - tr[FEATS].mean()) / tr[FEATS].std(), tr.y)
    te["blend_lr"] = lr.predict_proba((te[FEATS] - tr[FEATS].mean()) / tr[FEATS].std())[:, 1]
    ll = lambda y, p: float(-(y * np.log(np.clip(p, 1e-6, 1)) + (1 - y) * np.log(np.clip(1 - p, 1e-6, 1))).mean())
    print(f"test: {te.game_pk.nunique():,} games, {len(te):,} starting hitters, {te.y.sum():,} homered "
          f"({te.groupby('game_pk').y.sum().mean():.2f} HR hitters per game)\n")
    print("log-loss: blend (trees) %.5f | blend (logistic) %.5f | model %.5f" % (ll(te.y, te.blend), ll(te.y, te.blend_lr), ll(te.y, te.model)))
    print("\nRanking all hitters in each game, top 3 (both teams):")
    for col in ("blend", "blend_lr", "model", "season_rate"):
        r = te.assign(rk=te.groupby("game_pk")[col].rank(ascending=False, method="first"))
        top = r[r.rk <= 3].groupby("game_pk").y.sum()
        games = te.groupby("game_pk").y.sum()
        print(f"  {col:12s} top-3 HR hitters per game {top.mean():.3f} | games with 1+ of top 3 homering {(top >= 1).mean():.3f} | "
              f"2+ {(top >= 2).mean():.3f} | share of all HR hitters caught {top.sum() / games.sum():.3f}")
    rnd = 3 * te.y.mean()
    print(f"  (random 3 hitters: {rnd:.3f} per game)")
    # top-1 / top-5 for the blend
    r = te.assign(rk=te.groupby("game_pk").blend.rank(ascending=False, method="first"))
    for k in (1, 2, 3, 5):
        top = r[r.rk <= k].groupby("game_pk").y.sum()
        print(f"  blend top-{k}: {top.mean():.3f} HR hitters per game, 1+ in {(top >= 1).mean():.3f} of games, hit rate {r[r.rk <= k].y.mean():.3f}")
    # are the picks just the stars?
    r["team_rank"] = r.groupby(["game_pk", "bat_team"]).l_bhr.rank(ascending=False, method="first")
    t3 = r[r.rk <= 3]
    print(f"\n  blend top-3 picks that are NOT their team's best HR hitter (by season rate): {(t3.team_rank > 1).mean():.2f}; avg lineup slot {t3.slot.mean():.1f}")
    te.to_pickle(os.path.join(os.environ.get("TEMP", "."), "mlb_blend_eval.pkl"))


if __name__ == "__main__":
    if sys.argv[1:2] == ["evaluate"]:
        evaluate()
