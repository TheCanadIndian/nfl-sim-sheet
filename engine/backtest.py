#!/usr/bin/env python3
"""
Out-of-sample backtest: fit on earlier seasons, simulate every later game
using only information available at kickoff (priors, Vegas lines, and the
players who were active), then grade the distributions against actuals.

    python backtest.py --train 2022 2024 --test 2025 2026 --sims 3000

What "accurate" means for props: the full distribution must be calibrated.
  - PIT coverage: actual falls inside the 50% interval ~50% of the time, 80% ~80%
  - P(actual > median) ~ 50%  (no over/under bias)
  - MAE of the median vs a naive trailing-average baseline
  - anytime-TD probabilities calibrated by bucket
"""

import argparse
import time
import zlib

import numpy as np
import pandas as pd

from boxscore import data as D
from boxscore import pipeline as PL
from boxscore.sim import simulate_game

STATS = {  # stat -> (positions graded, min projected mean to be "prop-worthy")
    "pass_yds": (["QB"], 100), "att": (["QB"], 15), "cmp": (["QB"], 10),
    "pass_td": (["QB"], 0.5), "ints": (["QB"], 0.3),
    "rush_yds": (["QB", "RB", "WR"], 15), "car": (["RB", "QB"], 5),
    "rec": (["WR", "TE", "RB"], 1.5), "rec_yds": (["WR", "TE", "RB"], 15),
    "tgt": (["WR", "TE", "RB"], 2.5),
    "anytime_td": (["QB", "RB", "WR", "TE"], 0.1),
}


def player_samples(res, t):
    P, pl = t.players, res["players"]
    out = {}
    for j, p in enumerate(P):
        s = dict(rec=pl["rec"][:, j], rec_yds=pl["rec_yds"][:, j], tgt=pl["tgt"][:, j],
                 car=pl["car"][:, j], rush_yds=pl["rush_yds"][:, j],
                 anytime_td=((pl["rec_td"][:, j] + pl["rush_td"][:, j]) > 0).astype(float))
        if j == res["qi"]:
            s.update(res["qb_line"])
        out[p.player_id] = (p, s)
    return out


def rpit(samples, y, rng):
    """Randomized PIT for discrete-ish outcomes."""
    lo = np.mean(samples < y)
    hi = np.mean(samples <= y)
    return lo + rng.uniform() * (hi - lo)


def run(fr, train_span, test, sims=3000, verbose=True):
    """Walk-forward backtest. Returns (player rows, team rows) DataFrames."""
    df = fr.df
    first = train_span[0]
    actual = fr.pg.set_index(["game_id", "player_id"])
    tests = df[df.season.isin(test) & df.plays.notna() & df.spread_line.notna()]
    games = tests.drop_duplicates("game_id")
    rng = np.random.default_rng(7)
    rows, team_rows = [], []
    t0 = time.time()
    fitted = {}
    for g in games.itertuples():
        # Walk forward: each test season uses a model fit on all earlier seasons.
        if g.season not in fitted:
            last = max(train_span[1], g.season - 1)
            train = df.season.between(first, last) & ~((df.season == first) & (df.week < 6))
            fitted[g.season] = PL.fit(fr, train)
            if verbose:
                print(f"season {g.season}: fit on {first}-{last}")
        model, params = fitted[g.season]
        home, away = (g.team, g.opp) if g.is_home else (g.opp, g.team)
        try:
            hi = PL.team_input(fr, g.game_id, home)
            ai = PL.team_input(fr, g.game_id, away)
        except (StopIteration, IndexError):
            continue
        # Stable seed per game so two runs differ only by the model settings.
        h, a = simulate_game(model, params, hi, ai, n=sims, seed=zlib.crc32(g.game_id.encode()))
        for res, t in ((h, hi), (a, ai)):
            trow = df[(df.game_id == g.game_id) & (df.team == t.team)].iloc[0]
            for stat in ("points", "plays", "pass_yds", "rush_yds", "pass_td", "rush_td"):
                act = trow[stat]
                team_rows.append(dict(game_id=g.game_id, team=t.team, stat=stat,
                                      mean=res[stat].mean(), actual=act,
                                      pit=rpit(res[stat], act, rng)))
            for pid, (p, s) in player_samples(res, t).items():
                key = (g.game_id, pid)
                a_row = actual.loc[key] if key in actual.index else None
                for stat, (poss, min_mean) in STATS.items():
                    if p.pos not in poss or stat not in s:
                        continue
                    smp = s[stat]
                    if smp.mean() < min_mean:
                        continue
                    if a_row is None:
                        y = 0.0
                    elif stat == "anytime_td":
                        y = float(a_row.rec_td + a_row.rush_td > 0)
                    else:
                        y = float(a_row[stat])
                    rows.append(dict(
                        game_id=g.game_id, season=g.season, week=g.week, team=t.team,
                        player_id=pid, name=p.name, pos=p.pos, stat=stat, actual=y,
                        mean=smp.mean(), median=np.median(smp),
                        q10=np.quantile(smp, .1), q25=np.quantile(smp, .25),
                        q75=np.quantile(smp, .75), q90=np.quantile(smp, .9),
                        p_over_median=np.mean(smp > np.median(smp)),
                        pit=rpit(smp, y, rng),
                    ))
    if verbose:
        print(f"simulated {len(games)} games in {time.time() - t0:.0f}s")
    return pd.DataFrame(rows), pd.DataFrame(team_rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--train", nargs=2, type=int, default=[2022, 2024])
    ap.add_argument("--test", nargs="+", type=int, default=[2025, 2026])
    ap.add_argument("--sims", type=int, default=3000)
    ap.add_argument("--db", default="nfl.db")
    ap.add_argument("--out", default="backtest_rows.csv")
    args = ap.parse_args()

    con = D.connect(args.db)
    fr = PL.Frames(con)
    r, tr = run(fr, args.train, args.test, args.sims)
    r = add_baseline(r, fr)
    r.to_csv(args.out, index=False)
    report(r, tr)


def add_baseline(r, fr):
    """Naive trailing baseline: player's average over his previous 5 games (any team)."""
    pg = fr.pg.merge(fr.games[["game_id", "gameday"]], on="game_id").sort_values("gameday")
    pg["anytime_td"] = ((pg.rec_td + pg.rush_td) > 0).astype(float)
    cols = [s for s in STATS]
    base = pg.groupby("player_id")[cols].transform(lambda s: s.shift(1).rolling(5, min_periods=1).mean())
    base = pd.concat([pg[["game_id", "player_id"]], base.add_prefix("base_")], axis=1)
    long = base.melt(id_vars=["game_id", "player_id"], var_name="stat", value_name="baseline")
    long["stat"] = long.stat.str[5:]
    return r.merge(long, on=["game_id", "player_id", "stat"], how="left")


def report(r, tr):
    print("\n=== TEAM LEVEL (out of sample) ===")
    print(f"{'stat':10}{'n':>6}{'mean':>9}{'actual':>9}{'MAE':>8}{'in50%':>8}{'in80%':>8}")
    for stat, d in tr.groupby("stat", sort=False):
        pit = d.pit
        print(f"{stat:10}{len(d):6d}{d['mean'].mean():9.2f}{d.actual.mean():9.2f}"
              f"{(d['mean'] - d.actual).abs().mean():8.2f}"
              f"{np.mean(pit.between(.25, .75)):8.1%}{np.mean(pit.between(.1, .9)):8.1%}")

    print("\n=== PLAYER PROPS (out of sample) ===")
    print("Calibrated model: in50% ~ 50%, in80% ~ 80%, over_med ~ 50%, PIT<.5 ~ 50%")
    print(f"{'stat':11}{'n':>6}{'proj':>8}{'actual':>8}{'MAE_med':>9}{'MAE_base':>9}"
          f"{'in50%':>7}{'in80%':>7}{'actual>med':>11}")
    for stat in STATS:
        d = r[r.stat == stat]
        if not len(d) or stat == "anytime_td":
            continue
        db = d.dropna(subset=["baseline"])
        print(f"{stat:11}{len(d):6d}{d['mean'].mean():8.1f}{d.actual.mean():8.1f}"
              f"{(d['median'] - d.actual).abs().mean():9.2f}"
              f"{(db.baseline - db.actual).abs().mean():9.2f}"
              f"{np.mean(d.pit.between(.25, .75)):7.1%}{np.mean(d.pit.between(.1, .9)):7.1%}"
              f"{np.mean(d.actual > d['median']):11.1%}")

    d = r[r.stat == "anytime_td"].copy()
    if len(d):
        print("\n=== ANYTIME TD calibration ===")
        d["bucket"] = pd.cut(d["mean"], [0, .15, .25, .35, .45, .6, 1])
        cal = d.groupby("bucket", observed=True).agg(n=("actual", "size"), predicted=("mean", "mean"),
                                                     actual=("actual", "mean"))
        print(cal.round(3).to_string())
        brier = np.mean((d["mean"] - d.actual) ** 2)
        brier_b = np.mean((d.baseline.fillna(d.actual.mean()) - d.actual) ** 2)
        print(f"Brier: model {brier:.4f}   trailing-5 baseline {brier_b:.4f}")


if __name__ == "__main__":
    main()
