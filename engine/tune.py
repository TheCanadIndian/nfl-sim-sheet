#!/usr/bin/env python3
"""
Try model settings with the walk-forward backtest and print one metrics line per setting.

    python tune.py wr        # target-share / catch-rate shrinkage
    python tune.py td        # TD-share compression and QB goal-line weight
    python tune.py exit      # early-exit chance for non-QB skill players
    python tune.py confirm   # chosen settings vs baseline on 2025-26
Tuning runs fit on 2022-23 and score 2024, so 2025-26 stays untouched for confirmation.
"""

import json
import sys

import numpy as np
import pandas as pd

import backtest as BT
from boxscore import data as D
from boxscore import pipeline as PL
from boxscore import priors as P
from boxscore import sim as SIM
from boxscore import team_model as TM
from boxscore import weather as W

TUNE = dict(train=[2022, 2023], test=[2024])
CONFIRM = dict(train=[2022, 2024], test=[2025, 2026])
BASE = dict(K_TGT=20, K_CATCH=40, K_REC_TD=4, K_RUSH_TD=3, TD_GAMMA=0.85, QB_TD_MULT=1.8, P_EXIT=0.0,
            W_SNAP_TGT=0.0, W_SNAP_CAR=0.0, SNAP_HL=1.5, K_RATE_TGT=60, K_RATE_CAR=40, K_YPR=30, K_YPC=80, USE_WEATHER=False,
            ROOKIE_USAGE=0.15, ROOKIE_YPR=0.0, ROOKIE_PICK=64,
            RB_TGT_MULT=1.0, RB_YPR_MULT=1.0, QB_YPC_MULT=1.0, QB_SCR_MULT=1.0,
            LEAGUE_HL=256, TEAM_HL=6.0, TEAM_CARRY=0.45, PLAYER_HL=5.0, PLAYER_CARRY=0.4)
PRIOR_KEYS = {"K_TGT", "K_CATCH", "K_REC_TD", "K_RUSH_TD", "W_SNAP_TGT", "W_SNAP_CAR", "SNAP_HL", "K_RATE_TGT", "K_RATE_CAR", "K_YPR", "K_YPC",
              "ROOKIE_USAGE", "ROOKIE_YPR", "ROOKIE_PICK", "LEAGUE_HL", "TEAM_HL", "TEAM_CARRY", "PLAYER_HL", "PLAYER_CARRY"}
# Grade TD chances for nearly every player so every setting is scored on the same set
# (a 10% floor lets settings change which players count).
BT.STATS["anytime_td"] = (["QB", "RB", "WR", "TE"], 0.0)
if "--all-rows" in sys.argv:
    # Settings that move volume between players change who clears the props floor; grade
    # everyone so every setting is scored on the same rows.
    sys.argv.remove("--all-rows")
    for k, (poss, _) in list(BT.STATS.items()):
        BT.STATS[k] = (poss, 0.0)


REF = {}   # first run's (game, player, stat) keys: every setting is scored on this same set
TAUS = (.1, .25, .5, .75, .9)


def qscore(x):
    """Average pinball loss over the stored quantiles: rewards narrow ranges only if calibrated."""
    tot = 0
    for tau, c in zip(TAUS, ("q10", "q25", "median", "q75", "q90")):
        d = x.actual - x[c]
        tot += np.maximum(tau * d, (tau - 1) * d).mean()
    return tot / len(TAUS)


def metrics(r):
    k = r.game_id + "|" + r.player_id + "|" + r.stat
    if "keys" not in REF:
        REF["keys"] = set(k)
    r = r[k.isin(REF["keys"])]
    y = r[r.stat != "anytime_td"]
    out = dict(n=len(y), in80=y.pit.between(.1, .9).mean(), in50=y.pit.between(.25, .75).mean(),
               below10=(y[y.stat.isin(["rec_yds", "rush_yds", "pass_yds"])].pit < .1).mean(),
               above90=(y[y.stat.isin(["rec_yds", "rush_yds", "pass_yds"])].pit > .9).mean())
    for stat in ("pass_yds", "att", "tgt", "rec", "rec_yds", "car", "rush_yds"):
        x = y[y.stat == stat]
        out[f"mae_{stat}"] = (x.actual - x["median"]).abs().mean()
        out[f"qs_{stat}"] = qscore(x)
        out[f"width_{stat}"] = (x.q90 - x.q10).mean()
    if "wx" not in REF:
        REF["wx"] = W.features(D.connect()).set_index("game_id")
    wind = y.game_id.map(REF["wx"].w_wind).fillna(0)
    for label, mask in (("windy", wind >= 15), ("calm", wind < 15)):
        for stat in ("pass_yds", "rec_yds", "rush_yds"):
            x = y[mask & (y.stat == stat)]
            out[f"{label}_mae_{stat}"] = (x.actual - x["median"]).abs().mean()
            out[f"{label}_bias_{stat}"] = x.actual.mean() / x["mean"].mean() - 1
            out[f"{label}_n_{stat}"] = len(x)
    wr = y[y.pos == "WR"]
    rec, tgt = wr[wr.stat == "rec"], wr[wr.stat == "tgt"]
    out["wr_rec_bias"] = rec.actual.sum() / rec["mean"].sum() - 1
    out["wr_tgt_bias"] = tgt.actual.sum() / tgt["mean"].sum() - 1
    top = rec[rec["mean"] >= 4.4]
    out["wr_top_rec_bias"] = top.actual.sum() / top["mean"].sum() - 1
    m = rec.merge(tgt, on=["game_id", "player_id"], suffixes=("", "_t"))
    out["wr_catch_proj"] = m["mean"].sum() / m["mean_t"].sum()
    out["wr_catch_act"] = m.actual.sum() / m.actual_t.sum()
    if "draft" not in REF:
        REF["draft"] = D.positions(D.connect()).set_index("player_id")
    dr = REF["draft"]
    season = y.game_id.str[:4].astype(int)
    rk = (season == y.player_id.map(dr.rookie_year)) & (y.player_id.map(dr.draft_number) <= 64)
    for label, m in (("rk", rk), ("vet", ~rk)):
        x = y[m & y.stat.isin(["tgt", "rec_yds", "car", "rush_yds"])]
        out[f"{label}_n"] = len(x)
        out[f"{label}_bias"] = x.actual.sum() / x["mean"].sum() - 1
        out[f"{label}_above75"] = (x.pit > .75).mean()
        out[f"{label}_below25"] = (x.pit < .25).mean()
        out[f"{label}_qs"] = sum(qscore(x[x.stat == s]) * (x.stat == s).sum() for s in x.stat.unique()) / max(len(x), 1)
    for pos_, stat in (("RB", "tgt"), ("RB", "rec_yds"), ("RB", "rec"), ("QB", "rush_yds"), ("QB", "car")):
        x = y[(y.pos == pos_) & (y.stat == stat)]
        out[f"{pos_}_{stat}_bias"] = x.actual.sum() / x["mean"].sum() - 1
        out[f"{pos_}_{stat}_qs"] = qscore(x)
    td = r[r.stat == "anytime_td"]
    p = td["mean"].clip(1e-4, 1 - 1e-4)
    out["td_brier"] = ((p - td.actual) ** 2).mean()
    out["td_logloss"] = -(td.actual * np.log(p) + (1 - td.actual) * np.log(1 - p)).mean()
    b = pd.cut(p, [0, .15, .25, .35, .45, .6, 1])
    cal = td.groupby(b, observed=True).agg(n=("actual", "size"), pr=("mean", "mean"), ac=("actual", "mean"))
    out["td_cal_err"] = float((cal.n * (cal.pr - cal.ac).abs()).sum() / cal.n.sum())
    hi = td[p >= .45]
    out["td_hi_pred"], out["td_hi_act"] = hi["mean"].mean(), hi.actual.mean()
    qb = td[td.pos == "QB"]
    out["td_qb_pred"], out["td_qb_act"] = qb["mean"].mean(), qb.actual.mean()
    return {k: round(float(v), 4) if isinstance(v, (float, np.floating)) else v for k, v in out.items()}


_frames = {}


def evaluate(cfg, split, sims=2000):
    cfg = {**BASE, **cfg}
    for k in PRIOR_KEYS:
        setattr(P, k, cfg[k])
    for k in ("TD_GAMMA", "QB_TD_MULT", "P_EXIT"):
        setattr(SIM, k, cfg[k])
    TM.USE_WEATHER = cfg["USE_WEATHER"]
    for k in ("RB_TGT_MULT", "RB_YPR_MULT", "QB_YPC_MULT", "QB_SCR_MULT"):
        setattr(PL, k, cfg[k])
    key = tuple(cfg[k] for k in sorted(PRIOR_KEYS))
    if key not in _frames:
        _frames.clear()
        _frames[key] = PL.Frames(D.connect())
    r, _ = BT.run(_frames[key], split["train"], split["test"], sims=sims, verbose=False)
    m = metrics(r)
    print(json.dumps({"cfg": {k: v for k, v in cfg.items() if v != BASE[k]} or "baseline", **m}), flush=True)
    return m


GRIDS = {
    "wr": [{}, {"K_TGT": 35}, {"K_TGT": 50}, {"K_CATCH": 80}, {"K_CATCH": 150}, {"K_TGT": 35, "K_CATCH": 80}],
    "td": [{}, {"TD_GAMMA": .85}, {"QB_TD_MULT": 1.4}, {"QB_TD_MULT": 1.8}, {"QB_TD_MULT": 2.2},
           {"TD_GAMMA": .85, "QB_TD_MULT": 1.8}],
    "rztd": [{}, {"K_REC_TD": 10, "K_RUSH_TD": 8}, {"K_REC_TD": 20, "K_RUSH_TD": 15}, {"K_REC_TD": 40, "K_RUSH_TD": 30},
             {"K_REC_TD": 80, "K_RUSH_TD": 60}],                  # TD share pulled harder toward red-zone usage
    "weather": [{}, {"USE_WEATHER": True}],
    "eff": [{}, {"K_YPR": 15}, {"K_YPR": 60}, {"K_YPC": 40}, {"K_YPC": 160}],
    "exit": [{}, {"P_EXIT": .02}, {"P_EXIT": .04}, {"P_EXIT": .06}],
    "rookie": [{"ROOKIE_USAGE": 0.0}, {"ROOKIE_USAGE": .08},{"ROOKIE_USAGE": .15}, {"ROOKIE_USAGE": .25},
               {"ROOKIE_USAGE": .15, "ROOKIE_YPR": .04}, {"ROOKIE_USAGE": .15, "ROOKIE_PICK": 32}],
    "pos": [{}, {"RB_TGT_MULT": .94}, {"RB_TGT_MULT": .90}, {"RB_YPR_MULT": .95},
            {"QB_YPC_MULT": 1.10}, {"QB_SCR_MULT": 1.10}, {"QB_YPC_MULT": 1.10, "QB_SCR_MULT": 1.10},
            {"RB_TGT_MULT": .94, "QB_YPC_MULT": 1.10, "QB_SCR_MULT": 1.10}],
    "memory": [{}, {"LEAGUE_HL": 128}, {"LEAGUE_HL": 512}, {"TEAM_CARRY": .3}, {"TEAM_CARRY": .6},
               {"PLAYER_CARRY": .25}, {"PLAYER_CARRY": .55}, {"PLAYER_HL": 3.5}, {"PLAYER_HL": 7.0},
               {"TEAM_HL": 4.0}, {"TEAM_HL": 9.0}],
    "snap": [{}, {"W_SNAP_TGT": .3, "W_SNAP_CAR": .3, "K_RATE_TGT": 15, "K_RATE_CAR": 10},
             {"W_SNAP_TGT": .5, "W_SNAP_CAR": .5, "K_RATE_TGT": 15, "K_RATE_CAR": 10}],
}

if __name__ == "__main__":
    phase = sys.argv[1]
    if phase == "confirm":
        chosen = json.loads(sys.argv[2])
        evaluate(json.loads(sys.argv[3]) if len(sys.argv) > 3 else {}, CONFIRM, sims=3000)
        evaluate(chosen, CONFIRM, sims=3000)
    else:
        for cfg in GRIDS[phase]:
            evaluate(cfg, TUNE)
