#!/usr/bin/env python3
"""
Walk-forward check of the NBA model: every game projected only from earlier games, graded
against the box score. Players who actually played are graded (props void otherwise).

    python nba/backtest.py                 # seasons ending 2025 and 2026, both models
    python nba/backtest.py --seasons 2026 --mode vegas
"""

import argparse
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import model as M  # noqa: E402

GRADE = ["min", "pts", "reb", "ast", "fg3m", "pra", "stl", "blk", "tov"]


def run(fr, seasons, mode, n=1500, verbose=True):
    p = fr.p
    test = p[p.season.isin(seasons) & (p.played == 1)]
    if mode == "vegas":
        test = test[test.vegas_pts.notna()]
    rng = np.random.default_rng(1)
    rows = []
    for d, x in test.groupby("date"):
        # project every player of these games who played (lineup known), scale per team
        out, sims = M.project(fr, x, mode=mode, n=n, seed=int(d.strftime("%Y%m%d")))
        act = x.assign(pra=x.pts + x.reb + x.ast)
        for c in GRADE:
            s = sims[c] if c != "min" else sims["min"]
            y = act[c].to_numpy(float)
            lo = (s < y[:, None]).mean(1)
            hi = (s <= y[:, None]).mean(1)
            pit = lo + rng.uniform(size=len(y)) * (hi - lo)
            med = np.median(s, axis=1)
            mu = out["mu_" + c].to_numpy() if c != "min" else out.e_min.to_numpy()
            rows.append(pd.DataFrame(dict(date=d, season=x.season.to_numpy(), game_id=x.game_id.to_numpy(), team=x.team.to_numpy(),
                                          player_id=x.player_id.to_numpy(), pg=x.pg.to_numpy(), stat=c, mean=mu, median=med,
                                          actual=y, pit=pit, starter=x.starter.to_numpy())))
    r = pd.concat(rows, ignore_index=True)
    return r


def baseline(fr):
    """Trailing 10-game average of each stat (games played only)."""
    p = fr.p[fr.p.played == 1].sort_values(["date", "game_id"]).copy()
    p["pra"] = p.pts + p.reb + p.ast
    out = p[["game_id", "player_id"]].copy()
    for c in GRADE:
        out[c] = p.groupby("player_id")[c].transform(lambda s: s.shift(1).rolling(10, min_periods=1).mean())
    return out.melt(id_vars=["game_id", "player_id"], var_name="stat", value_name="base")


def report(r, label):
    print(f"\n=== {label} ===  {r.game_id.nunique()} games, {r[r.stat == 'pts'].shape[0]} player-games")
    print(f"{'stat':6}{'proj':>8}{'actual':>8}{'bias':>8}{'MAE':>8}{'base MAE':>10}{'in80':>7}{'in50':>7}{'>med':>7}")
    for c in GRADE:
        x = r[r.stat == c]
        b = x.dropna(subset=["base"])
        print(f"{c:6}{x['mean'].mean():8.2f}{x.actual.mean():8.2f}{x.actual.sum() / x['mean'].sum() - 1:8.1%}"
              f"{(x.actual - x['median']).abs().mean():8.2f}{(b.actual - b.base).abs().mean():10.2f}"
              f"{x.pit.between(.1, .9).mean():7.1%}{x.pit.between(.25, .75).mean():7.1%}{(x.actual > x['median']).mean():7.1%}")


def team_report(fr, seasons):
    t = fr.team[fr.team.season.isin(seasons) & (fr.team.one == 1)]
    v = t.dropna(subset=["vegas_pts"])
    print(f"\nteam points MAE: blind {(t.blind_pts - t.pf).abs().mean():.2f} (n={len(t)}), "
          f"Vegas implied {(v.vegas_pts - v.pf).abs().mean():.2f} vs blind on same games {(v.blind_pts - v.pf).abs().mean():.2f} (n={len(v)})")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--seasons", nargs="+", type=int, default=[2025, 2026])
    ap.add_argument("--mode", choices=["vegas", "blind", "both"], default="both")
    ap.add_argument("--out")
    a = ap.parse_args()
    fr = M.Frames()
    team_report(fr, a.seasons)
    b = baseline(fr)
    for mode in (["vegas", "blind"] if a.mode == "both" else [a.mode]):
        r = run(fr, a.seasons, mode).merge(b, on=["game_id", "player_id", "stat"], how="left")
        for s in a.seasons:
            report(r[r.season == s], f"{mode} {s}")
        if a.out:
            r.to_csv(a.out.replace(".csv", f"_{mode}.csv"), index=False)
