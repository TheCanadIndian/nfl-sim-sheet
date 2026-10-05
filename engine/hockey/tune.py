#!/usr/bin/env python3
"""Tune hockey model settings on 2024-25 (fit 2023-24); confirm on 2025-26.
    python hockey/tune.py grid
    python hockey/tune.py confirm '{"RATE_HL": 60}'
"""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import model as M
import nhl_backtest as B

BASE = {k: getattr(M, k) for k in ("TOI_HL", "RATE_HL", "FIN_HL", "K_NP_HOURS", "K_PP_HOURS", "K_FIN_XG",
                                   "TEAM_HL", "K_GOALIE_XG", "PP_OPP_ONLY", "USE_LG_ENV", "LG_DAYS", "TOI_REG_ONLY", "TOI_RAMP", "BASE_CURVE",
                                   "USE_POS_D", "K_POS_GAMES", "POS_D_ABS",
                                   "USE_H2H", "K_H2H", "K_H2H_TEAM", "GQ_MODE", "TOI_NORM", "NP_TEAM_SEC")}
_games, _shots, _ = M.load()
_shots["season"] = _shots.game_id.map(_games.set_index("game_id").season)
_xg = {}


def evaluate(cfg, S):
    for k, v in {**BASE, **cfg}.items():
        setattr(M, k, v)
    if S not in _xg:
        _xg[S] = M.XGModel().fit(_shots[_shots.season < S])
    fr = M.Frames(_xg[S])
    pg = fr.pg[fr.pg.toi.notna()]
    gm = M.GoalModel().fit(pg[(pg.season > B.FIRST) & (pg.season < S)])
    t = pg[pg.season == S]
    y, p = (t.goals > 0).astype(float).values, gm.p_goal(t)
    sc = B.scores(y, p)
    hi = p >= .35
    sc["hi_pred"], sc["hi_act"] = float(p[hi].mean()), float(y[hi].mean())
    top = p >= .45
    sc["top_pred"], sc["top_act"], sc["top_n"] = float(p[top].mean()), float(y[top].mean()), int(top.sum())
    print(json.dumps({"season": S, "cfg": cfg or "baseline", **{k: round(v, 5) for k, v in sc.items()}}), flush=True)
    return sc


GRID = [{}, {"PP_OPP_ONLY": False}, {"RATE_HL": 15.0}, {"RATE_HL": 60.0}, {"TOI_HL": 2.0}, {"TOI_HL": 8.0},
        {"K_FIN_XG": 10.0}, {"K_FIN_XG": 40.0}, {"K_NP_HOURS": 2.0}, {"K_NP_HOURS": 8.0},
        {"K_PP_HOURS": 0.75}, {"K_PP_HOURS": 3.0}, {"TEAM_HL": 30.0}, {"K_GOALIE_XG": 150.0}]

def recal():
    """Fit logit recalibration on 2024-25 out-of-sample predictions, test on 2025-26."""
    res = {}
    for S in (20242025, 20252026):
        for k, v in BASE.items():
            setattr(M, k, v)
        if S not in _xg:
            _xg[S] = M.XGModel().fit(_shots[_shots.season < S])
        fr = M.Frames(_xg[S])
        pg = fr.pg[fr.pg.toi.notna()]
        gm = M.GoalModel().fit(pg[(pg.season > B.FIRST) & (pg.season < S)])
        t = pg[pg.season == S]
        res[S] = ((t.goals > 0).astype(float).values, np.clip(gm.p_goal(t), 1e-4, 1 - 1e-4))
    y, p = res[20242025]
    ab = M.fit_top_calibration(p, y)
    print("top-end calibration (hinge above p=.30):", np.round(ab, 4))
    y2, p2 = res[20252026]
    q2 = M.apply_top_calibration(p2, ab)
    for name, pp in (("raw", p2), ("recalibrated", q2)):
        sc = B.scores(y2, pp)
        hi = pp >= .35
        print(f"2025-26 {name:13} logloss {sc['logloss']:.5f} brier {sc['brier']:.5f} mean {sc['mean_p']:.4f}/{sc['rate']:.4f} p>=.35 {pp[hi].mean():.3f}/{y2[hi].mean():.3f} (n={hi.sum()})")
        print(B.calib(y2, pp).round(3).to_string())


if __name__ == "__main__":
    if sys.argv[1] == "recal":
        recal(); sys.exit()
    if sys.argv[1] == "grid":
        for c in GRID:
            evaluate(c, 20242025)
    else:
        c = json.loads(sys.argv[2])
        for S in (20242025, 20252026):
            evaluate({}, S)
            evaluate(c, S)
