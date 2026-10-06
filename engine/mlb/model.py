#!/usr/bin/env python3
"""
MLB home-run model: chance of a home run in each plate appearance, then a batter's chance of 1+ HR
in a game from his expected plate appearances against the starter and the bullpen.

Per-PA logistic model (fit with numpy IRLS) on features known BEFORE each game:
  batter   HR per PA, "power contact" per PA (exit velo 95+ at launch angle 20-40), fly-ball share,
           each shrunk toward league average (pseudo-PAs K_*), current season + W_PREV x last season
  pitcher  HR allowed per batter faced and power contact allowed, shrunk the same way (bullpen = the
           team's relievers pooled)
  platoon  same-handed matchup (switch hitters never same-side)
  park     venue HR factor by batter side, from HR rate there vs the same teams elsewhere, shrunk
  weather  temperature (roof closed / dome = 70F), wind blowing out (+mph) or in (-mph)

    python mlb/model.py backtest            # walk-forward: fit on earlier seasons, score later ones
"""

import json
import os
import sqlite3
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "mlb.db")

W_PREV = 0.6          # last season's weight relative to this season's (to-date) numbers
K_BAT_HR, K_BAT_PC, K_BAT_FB = 250, 150, 120
K_PIT_HR, K_PIT_PC = 500, 300
K_PARK = 1500         # pseudo-PAs pulling park factors to 1
POWER = (95, 20, 40)  # exit velo >=, launch angle between
BASE = ["l_bhr", "l_bpc", "l_bfb", "l_phr", "l_ppc", "same", "l_park", "temp", "wind_out", "post"]
GROUPS = {   # candidate feature groups, each kept only if it improves the walk-forward backtest
    "power": ["l_b_brl", "l_b_hh", "l_b_pull", "l_p_brl", "l_p_hh", "l_p_pull"],
    "k_bb": ["l_b_k", "l_b_bb", "l_p_k", "l_p_bb"],
    "form": ["form"],
    "mix": ["mix"],
    "heat": ["heat"],
    "velo": ["velo"],
}
FEATURES = list(BASE)


def load(con=None, extra=None):
    """All stored PAs (+ optional future rows that already carry the game columns, hr=0)."""
    con = con or sqlite3.connect(DB)
    pa = pd.read_sql("SELECT * FROM pa", con)
    g = pd.read_sql("SELECT game_pk, date, season, type, home, away, venue_id, temp, wind_mph, wind_dir, condition FROM games", con)
    pa = pa.merge(g, on="game_pk")
    if extra is not None and len(extra):
        pa = pd.concat([pa, extra[[c for c in extra.columns if c in pa.columns]]], ignore_index=True)
    pa["bip"] = pa.ev.notna().astype(int)
    pa["pc"] = ((pa.ev >= POWER[0]) & pa.la.between(POWER[1], POWER[2])).astype(int)
    pa["fb"] = (pa.traj == "fly_ball").astype(int)
    pa = pa[~pa.event.isin(["intent_walk"])]
    return pa.sort_values(["date", "game_pk", "idx"]).reset_index(drop=True)


def _to_date(df, key, cols):
    """Season-to-date totals strictly before each date, plus last season's totals, per `key`."""
    day = df.groupby([key, "season", "date"])[cols].sum().reset_index().sort_values([key, "date"])
    cum = day.groupby([key, "season"])[cols].cumsum() - day[cols]
    day[[c + "_cur" for c in cols]] = cum.to_numpy()
    tot = df.groupby([key, "season"])[cols].sum().reset_index()
    tot["season"] += 1
    day = day.merge(tot.rename(columns={c: c + "_prev" for c in cols}), on=[key, "season"], how="left").fillna(0)
    return day.drop(columns=cols)


def logit(p):
    p = np.clip(p, 1e-5, 1 - 1e-5)
    return np.log(p / (1 - p))


def features(pa):
    df = pa.copy()
    df["one"] = 1
    lg = df.groupby("season")[["hr", "pc", "fb", "bip", "one"]].sum()
    lg_hr = (lg.hr / lg.one).to_dict(); lg_pc = (lg.pc / lg.one).to_dict(); lg_fb = (lg.fb / lg.bip).to_dict()
    df["lg_hr"] = df.season.map(lg_hr).fillna(np.mean(list(lg_hr.values())))
    df["lg_pc"] = df.season.map(lg_pc).fillna(np.mean(list(lg_pc.values())))
    df["lg_fb"] = df.season.map(lg_fb).fillna(np.mean(list(lg_fb.values())))

    b = _to_date(df, "batter", ["one", "hr", "pc", "fb", "bip"])
    df = df.merge(b, on=["batter", "season", "date"], how="left")
    n = df.one_cur + W_PREV * df.one_prev
    df["bhr"] = (df.hr_cur + W_PREV * df.hr_prev + K_BAT_HR * df.lg_hr) / (n + K_BAT_HR)
    df["bpc"] = (df.pc_cur + W_PREV * df.pc_prev + K_BAT_PC * df.lg_pc) / (n + K_BAT_PC)
    nb = df.bip_cur + W_PREV * df.bip_prev
    df["bfb"] = (df.fb_cur + W_PREV * df.fb_prev + K_BAT_FB * df.lg_fb) / (nb + K_BAT_FB)
    df = df.drop(columns=[c for c in df.columns if c.endswith(("_cur", "_prev"))])

    # pitchers: starters individually; relievers pooled as the team's bullpen
    df["pkey"] = np.where(df.sp == 1, "P" + df.pitcher.astype(str), "BP" + df.fld_team)
    p = _to_date(df, "pkey", ["one", "hr", "pc"])
    df = df.merge(p, on=["pkey", "season", "date"], how="left")
    n = df.one_cur + W_PREV * df.one_prev
    df["phr"] = (df.hr_cur + W_PREV * df.hr_prev + K_PIT_HR * df.lg_hr) / (n + K_PIT_HR)
    df["ppc"] = (df.pc_cur + W_PREV * df.pc_prev + K_PIT_PC * df.lg_pc) / (n + K_PIT_PC)
    df = df.drop(columns=[c for c in df.columns if c.endswith(("_cur", "_prev"))])

    df["park"] = park_factors(df)
    df["same"] = ((df.bat_side == df.pitch_hand) & (df.bat_side != "S")).astype(int)
    closed = df.condition.fillna("").str.contains("Roof Closed|Dome", case=False)
    df["temp"] = (np.where(closed | df.temp.isna(), 70, df.temp) - 70) / 10
    wd = df.wind_dir.fillna("")
    sign = np.where(wd.str.startswith("Out"), 1, np.where(wd.str.startswith("In"), -1, 0))
    df["wind_out"] = np.where(closed, 0, sign * df.wind_mph.fillna(0)) / 10
    df["post"] = (df.type != "R").astype(int)
    for c in ("bhr", "bpc", "bfb", "phr", "ppc"):
        df["l_" + c] = logit(df[c])
    df["l_park"] = np.log(df.park)
    return df


def park_factors(df):
    """HR factor for each PA's venue and batter side, from games BEFORE that season (prior seasons pooled) +
    this season to date would leak little; keep it simple: prior seasons only, shrunk to 1."""
    side = np.where(df.bat_side == "S", np.where(df.pitch_hand == "R", "L", "R"), df.bat_side)
    d = df.assign(side=side)
    out = np.ones(len(d))
    for s in sorted(d.season.unique()):
        prior = d[d.season < s]
        if prior.empty:
            continue
        team_rate = prior.groupby(["bat_team", "side"]).hr.mean()
        prior = prior.assign(exp=[team_rate.get((t, sd), np.nan) for t, sd in zip(prior.bat_team, prior.side)])
        v = prior.groupby(["venue_id", "side"]).agg(hr=("hr", "sum"), exp=("exp", "sum"), n=("hr", "size"))
        lgr = prior.hr.mean()
        f = ((v.hr + K_PARK * lgr) / (v.exp + K_PARK * lgr)).to_dict()
        m = (d.season == s).to_numpy()
        out[m] = [f.get((vid, sd), 1.0) for vid, sd in zip(d.venue_id[m], d.side[m])]
    return out


def fit(df, l2=1.0, feats=None):
    """Logistic regression by IRLS with a small ridge (no intercept penalty)."""
    feats = feats or FEATURES
    X = np.column_stack([np.ones(len(df))] + [df[c].to_numpy(float) for c in feats])
    y = df.hr.to_numpy(float)
    beta = np.zeros(X.shape[1]); beta[0] = logit(y.mean())
    R = np.eye(X.shape[1]) * l2; R[0, 0] = 0
    for _ in range(25):
        p = 1 / (1 + np.exp(-(X @ beta)))
        W = p * (1 - p)
        g = X.T @ (y - p) - R @ beta
        Hm = (X * W[:, None]).T @ X + R
        step = np.linalg.solve(Hm, g)
        beta += step
        if np.abs(step).max() < 1e-7:
            break
    return dict(zip(["const"] + feats, beta))


def predict(df, coef):
    z = coef["const"] + sum(v * df[c].to_numpy(float) for c, v in coef.items() if c != "const")
    return 1 / (1 + np.exp(-z))


def ll(y, p):
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return float(-(y * np.log(p) + (1 - y) * np.log(1 - p)).mean())


def backtest():
    global FEATURES
    pp = os.path.join(HERE, "params.json")
    if os.path.exists(pp):
        FEATURES = json.load(open(pp))["features"]
    df = full_frame()
    df = df[df.season >= 2025]           # 2024 only feeds 'last season' numbers and park factors
    print(f"{len(df):,} PAs, {df.hr.sum():,} HR (2025-26)\n")
    for test in ("2026 regular", "2026 postseason"):
        if test == "2026 regular":
            tr, te = df[df.season == 2025], df[(df.season == 2026) & (df.type == "R")]
        else:
            tr, te = df[(df.season == 2025) | ((df.season == 2026) & (df.type == "R"))], df[(df.season == 2026) & (df.type != "R")]
        if te.empty:
            continue
        coef = fit(tr)
        te = te.assign(p=predict(te, coef), base_lg=tr.hr.mean(), base_b=te.bhr)
        # per PA
        print(f"=== {test}: {len(te):,} PAs, HR rate {te.hr.mean():.4f}")
        print(f"  per-PA log-loss: model {ll(te.hr, te.p):.5f} | batter HR rate only {ll(te.hr, te.base_b):.5f} | league rate {ll(te.hr, te.base_lg):.5f}")
        # per batter-game: P(1+ HR) over the PAs he actually had
        bg = te.groupby(["game_pk", "batter"]).agg(y=("hr", "max"), p=("p", lambda x: 1 - np.prod(1 - x)),
                                                   b=("base_b", lambda x: 1 - np.prod(1 - x)), n=("hr", "size"))
        print(f"  per batter-game (1+ HR, actual PAs): model {ll(bg.y, bg.p):.5f} | batter rate only {ll(bg.y, bg.b):.5f}  ({len(bg):,} batter-games, {bg.y.mean():.3f} homered)")
        bg["bin"] = pd.cut(bg.p, [0, .06, .09, .12, .15, .2, 1])
        print("  calibration (model chance -> actual):", {str(k): f"{v.p:.3f}->{v.y:.3f} n{int(v.n)}" for k, v in
                                                          bg.groupby("bin", observed=True).agg(p=("p", "mean"), y=("y", "mean"), n=("y", "size")).iterrows()})
        print("  coefficients:", {k: round(v, 3) for k, v in coef.items()}, "\n")


def full_frame(extra_pa=None):
    """Feature frame for every PA. extra_pa: future PAs (today's lineups) -- each gets one placeholder pitch so
    the pitch-level tables have a row for that date (placeholders only count toward later dates)."""
    import features as FX
    con = sqlite3.connect(DB)
    if extra_pa is not None and len(extra_pa):
        extra_pa = extra_pa.copy()
        extra_pa.loc[extra_pa.sp == 0, "pitcher"] = np.where(extra_pa.loc[extra_pa.sp == 0, "fld_team"] == extra_pa.loc[extra_pa.sp == 0, "home"], -1, -2)
    df = features(load(con, extra_pa))
    pitch = pd.read_sql("SELECT game_pk, idx, n, batter, pitcher, bat_side, pitch_hand, ptype, speed, px, pz, sz_top, sz_bot, inplay, ev, la FROM pitch", con)
    pitch = pitch.merge(pd.read_sql("SELECT game_pk, season, date FROM games", con), on="game_pk")
    if extra_pa is not None and len(extra_pa):
        ph = extra_pa[["game_pk", "idx", "batter", "pitcher", "bat_side", "pitch_hand", "season", "date"]].assign(
            n=1, ptype="FF", speed=np.nan, px=0.0, pz=2.5, sz_top=3.4, sz_bot=1.6, inplay=0, ev=np.nan, la=np.nan)
        pitch = pd.concat([pitch, ph], ignore_index=True)
    return FX.attach(df, pitch)


def ablation():
    """Add each feature group to the base set; keep groups that lower per-PA log-loss in BOTH test windows."""
    df = full_frame()
    df = df[df.season >= 2025]
    windows = [("2026 regular", df[df.season == 2025], df[(df.season == 2026) & (df.type == "R")]),
               ("2026 postseason", df[(df.season == 2025) | ((df.season == 2026) & (df.type == "R"))], df[(df.season == 2026) & (df.type != "R")])]
    def score(feats):
        out = []
        for name, tr, te in windows:
            coef = fit(tr, feats=feats)
            out.append(ll(te.hr.to_numpy(float), predict(te, coef)))
        return out
    base = score(BASE)
    print("base", [round(x, 5) for x in base], flush=True)
    kept = list(BASE)
    for g, cols in GROUPS.items():
        s = score(BASE + cols)
        print(f"+{g:6s}", [round(x, 5) for x in s], "gain", [round(b - x, 5) for b, x in zip(base, s)], flush=True)
    for g, cols in GROUPS.items():         # greedy: keep a group if it helps both windows on top of what's kept
        cur = score(kept); s = score(kept + cols)
        if all(x < c for x, c in zip(s, cur)):
            kept += cols
            print(f"keep {g}: {[round(c - x, 5) for c, x in zip(cur, s)]}", flush=True)
    final = score(kept)
    print("FINAL features:", kept)
    print("final log-loss", [round(x, 5) for x in final], "vs base", [round(x, 5) for x in base])
    _, tr, _ = windows[1]
    print("coefficients (fit through 2026 regular season):", {k: round(v, 3) for k, v in fit(tr, feats=kept).items()})


if __name__ == "__main__":
    if sys.argv[1:2] == ["backtest"]:
        backtest()
    elif sys.argv[1:2] == ["ablation"]:
        ablation()
