#!/usr/bin/env python3
"""
Walk-forward backtest of the anytime-goal model.

    python hockey/nhl_backtest.py

Test season S uses an xG model and goal model fit only on seasons before S;
2022-23 is history only. Every skater who played is graded (props void if a player
is scratched, so only players who dressed count). Compared with a baseline: the
player's recency-weighted goals per game, shrunk toward his position.
"""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import model as M  # noqa: E402
from model import decayed_sums  # noqa: E402

TESTS = [20242025, 20252026]
FIRST = 20222023


def baseline(pg):
    x = pg.copy()
    x["one"] = x.toi.notna().astype(float)
    x["gl"] = x.goals.where(x.toi.notna(), 0).astype(float)
    s = decayed_sums(x, "player_id", ["gl", "one"], 30.0, 0.7)
    lg = x[x.one == 1].groupby("pos_grp").gl.mean().reindex(x.pos_grp).values
    rate = (s["_s_gl"] + 10 * lg) / (s["_s_one"] + 10)
    return 1 - np.exp(-rate)


def scores(y, p):
    p = np.clip(p, 1e-4, 1 - 1e-4)
    return dict(logloss=float(-(y * np.log(p) + (1 - y) * np.log(1 - p)).mean()),
                brier=float(((p - y) ** 2).mean()), mean_p=float(p.mean()), rate=float(y.mean()))


def calib(y, p):
    b = pd.cut(p, [0, .05, .1, .15, .2, .25, .3, .35, .4, .5, 1])
    t = pd.DataFrame({"y": y, "p": p, "b": b}).groupby("b", observed=True).agg(n=("y", "size"), pred=("p", "mean"), act=("y", "mean"))
    return t


def top_hits(df, col, k=3):
    t = df.sort_values(col, ascending=False).groupby("game_id").head(k)
    return float(t.scored.mean()), float(t[col].mean())


def run(verbose=True):
    games, shots, _ = M.load()
    season_of = games.set_index("game_id").season
    shots["season"] = shots.game_id.map(season_of)
    out = []
    for S in TESTS:
        xg = M.XGModel().fit(shots[(shots.season < S)])
        fr = M.Frames(xg)
        pg = fr.pg[fr.pg.toi.notna()].copy()
        train = pg[(pg.season > FIRST) & (pg.season < S)]
        gm = M.GoalModel().fit(train)
        test = pg[pg.season == S].copy()
        test["p_model"] = gm.p_goal(test)
        test["p_base"] = baseline(fr.pg).reindex(test.index).values if False else baseline(fr.pg)[test.index]
        test["scored"] = (test.goals > 0).astype(float)
        out.append(test)
        if verbose:
            print(f"\n=== {S} (fit on seasons before it) ===  coefficients:",
                  {k: round(v, 3) for k, v in zip(["const"] + M.GLM_X, gm.b)})
            for name in ("p_model", "p_base"):
                print(f"  {name:8}", {k: round(v, 5) for k, v in scores(test.scored.values, test[name].values).items()})
            print("  calibration (model):")
            print(calib(test.scored.values, test.p_model.values).round(3).to_string())
            for name in ("p_model", "p_base"):
                hit, pr = top_hits(test, name)
                print(f"  top-3 per game by {name}: scored {hit:.1%} (predicted {pr:.1%})")
    return pd.concat(out)


if __name__ == "__main__":
    run()
