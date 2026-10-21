#!/usr/bin/env python3
"""
Shared game state for the NBA's saved draws (phase 3 of the play-level sims, 2026-10-21). The projections' own draws
are independent per player; these add what moves a whole game or team together, keeping every player's mean:
  - team factors for 7 stats, both teams (pace, a shooting night, the other side's rebounds ...): covariance of team-game
    log(actual / projected) totals minus independent player noise (2025 walk-forward; corr_params.json),
  - blowout minutes: each team's top 5 by projected minutes play ~2.5% more in close games and 4-7% less at 25+ points
    (bench the reverse), by the simulated margin (pass 1), then the same random numbers again (pass 2).
Used only for the saved draws behind the build-your-own slip / same-game pricing; the page's ranges stay the model's.
research/nba_corr.py (2026 season, ~1,300 games): log-loss better in both halves for teammates both over, points over +
game over the total, opposing stars both over, opposing bigs both over on rebounds; points over + team wins mixed;
single legs unchanged. Effects are small (team scoring swings ~5%; lifts ~1.01-1.02).
"""
import json
import os

import numpy as np
from scipy import stats as SS

import model as M

HERE = os.path.dirname(os.path.abspath(__file__))
P = json.load(open(os.path.join(HERE, "corr_params.json")))
ST = P["stats"]


def draws(out, rows, n=1000, seed=0):
    """Correlated draws for ONE game's players (out from model.project, rows the matching Frames rows, same order).
    Returns {stat: players x n} for ST plus 'pra'."""
    rng = np.random.default_rng(seed)
    S = np.array(P["cov"])
    home = rows.is_home.to_numpy().astype(bool)
    em = out.e_min.to_numpy(float)
    sd = rows.min_sd.to_numpy(float)
    mult = np.exp(rng.multivariate_normal(np.zeros(len(S)), S, n, method="eigh") - np.diag(S) / 2)
    z = rng.normal(0, 1, (len(out), n))
    top = np.zeros(len(out), bool)
    teams = out.team.to_numpy()
    for t in np.unique(teams):
        idx = np.where(teams == t)[0]
        top[idx[np.argsort(-em[idx])[:5]]] = True
    mu = {c: out["mu_" + c].to_numpy(float) for c in ST}
    G, U = {}, {}
    for c in ST:
        k = M.DISP[c] * np.clip(mu[c] / M.DISP_REF.get(c, 1e9), 1, None) ** M.DISP_GAMMA
        G[c] = rng.gamma(k[:, None] * np.ones((1, n)), 1 / k[:, None])
        U[c] = rng.uniform(size=(len(out), n))
    side = np.where(home, 0, len(ST))

    def run(mfac):
        m = np.clip(em[:, None] * mfac + z * sd[:, None], 0, 48)
        fac = np.where(em[:, None] > 0, m / np.maximum(em[:, None], 1e-6), 0)
        return {c: SS.poisson.ppf(U[c], np.maximum(mu[c][:, None] * fac * mult[:, side + i].T * G[c], 1e-9))
                for i, c in enumerate(ST)}
    o = run(np.ones((len(out), 1)))
    if home.any() and (~home).any():
        marg = o["pts"][home].sum(0) - o["pts"][~home].sum(0)
        b = np.clip(np.searchsorted(P["buckets"], np.abs(marg), side="right") - 1, 0, len(P["starters"]) - 1)
        hs, hb = np.array(P["starters"])[b], np.array(P["bench"])[b]
        o = run(np.where(top[:, None], hs[None, :] / hs.mean(), hb[None, :] / hb.mean()))
    o["pra"] = o["pts"] + o["reb"] + o["ast"]
    return o
