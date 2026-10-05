"""
Game simulation for NHL moneylines, puck lines and totals.

Each team's expected goals = sum of its dressed skaters' expected goals (the anytime-goal
model). A game is simulated as:
  1. regulation goals (excluding empty-netters): Poisson for each team, scaled by REG_SCALE,
     with extra regulation ties (TIE_INFLATE: teams protect leads / chase ties late);
  2. empty-net goals: a team leading by 1 or 2 at the end of regulation adds one with
     probability EN_P1 / EN_P2;
  3. ties go to overtime / shootout; the home team wins it with probability OT_HOME.
     The winner gets +1 goal in the final score (books count the shootout winner that way).
Parameters are fit in fit() on earlier seasons; see README "Moneylines".
"""

import numpy as np
from scipy.stats import poisson

# OT / empty-net from 2022-24 results; REG_SCALE and TIE_INFLATE fit on 2024-25 out-of-sample
# expected goals, tested on 2025-26 (README "Moneylines").
PARAMS = dict(REG_SCALE=0.940, TIE_INFLATE=0.20, EN_P1=0.453, EN_P2=0.631, OT_HOME=0.535)
KMAX = 15


def outcome(lam_home, lam_away, P=None):
    """Exact score distribution. Returns dict with home win prob (incl. OT/SO), puck lines,
    totals and the final-score matrix."""
    P = P or PARAMS
    k = np.arange(KMAX)
    ph = poisson.pmf(k, lam_home * P["REG_SCALE"])
    pa = poisson.pmf(k, lam_away * P["REG_SCALE"])
    M = np.outer(ph, pa)                                  # regulation, non-empty-net goals
    tie = np.trace(M)
    d = np.diag_indices(KMAX)
    M = M * (1 - tie * (1 + P["TIE_INFLATE"])) / (1 - tie)
    M[d] = np.diag(np.outer(ph, pa)) * (1 + P["TIE_INFLATE"])
    F = np.zeros((KMAX + 2, KMAX + 2))                    # final score
    for h in range(KMAX):
        for a in range(KMAX):
            p = M[h, a]
            if not p:
                continue
            m = h - a
            if m == 0:
                F[h + 1, a] += p * P["OT_HOME"]
                F[h, a + 1] += p * (1 - P["OT_HOME"])
                continue
            en = P["EN_P1"] if abs(m) == 1 else P["EN_P2"] if abs(m) == 2 else 0.0
            F[h, a] += p * (1 - en)
            if m > 0:
                F[h + 1, a] += p * en
            else:
                F[h, a + 1] += p * en
    hh, aa = np.indices(F.shape)
    margin, total = hh - aa, hh + aa
    reg_tie = float(np.trace(M))
    return dict(
        home_win=float(F[margin > 0].sum()), away_win=float(F[margin < 0].sum()),
        reg_tie=reg_tie,
        home_m15=float(F[margin >= 2].sum()), away_m15=float(F[margin <= -2].sum()),   # -1.5 puck lines
        totals={t: float(F[total > t].sum()) for t in (4.5, 5.5, 6.5, 7.5)},
        exp_total=float((F * total).sum()), F=F,
    )


def american(p):
    p = min(max(p, 1e-4), 1 - 1e-4)
    return round(-100 * p / (1 - p)) if p >= .5 else round(100 * (1 - p) / p)


def actual_games(con):
    """Per game: regulation non-EN goals, EN goals, whether it went to OT/SO, final winner."""
    import pandas as pd
    g = pd.read_sql("SELECT game_id, season, home, away, home_score, away_score, game_type FROM games "
                    "WHERE state IN ('OFF', 'FINAL')", con)
    s = pd.read_sql("SELECT game_id, team, period, empty_net FROM shots WHERE goal = 1", con)
    s["reg"] = s.period <= 3
    agg = s.groupby(["game_id", "team"]).agg(reg_nen=("reg", lambda r: int((r & (s.loc[r.index, "empty_net"] == 0)).sum())),
                                              en=("reg", lambda r: int((r & (s.loc[r.index, "empty_net"] == 1)).sum())))
    agg = agg.reset_index()
    for side in ("home", "away"):
        x = agg.rename(columns={"team": side, "reg_nen": f"{side}_reg", "en": f"{side}_en"})
        g = g.merge(x, on=["game_id", side], how="left")
    for c in ("home_reg", "away_reg", "home_en", "away_en"):
        g[c] = g[c].fillna(0).astype(int)
    g["reg_tie"] = (g.home_reg + g.home_en) == (g.away_reg + g.away_en)
    g["home_won"] = (g.home_score > g.away_score).astype(int)
    return g


def fit(con, seasons):
    """Fit OT/EN/tie parameters from actual results in `seasons` (REG_SCALE is fit separately
    against model expected goals in the backtest)."""
    g = actual_games(con)
    g = g[g.season.isin(seasons) & (g.game_type == 2)]
    P = dict(PARAMS)
    P["OT_HOME"] = float(g[g.reg_tie].home_won.mean())
    lead = g.home_reg - g.away_reg
    for k, name in ((1, "EN_P1"), (2, "EN_P2")):
        x = g[lead.abs() == k]
        leader_en = np.where(lead[lead.abs() == k] > 0, x.home_en, x.away_en)
        P[name] = float((leader_en > 0).mean())
    return P, g


if __name__ == "__main__":
    import sqlite3
    import sys
    import os
    con = sqlite3.connect(os.path.join(os.path.dirname(os.path.abspath(__file__)), "nhl.db"))
    if sys.argv[1:] == ["fit"]:
        P, g = fit(con, [20222023, 20232024, 20242025])
        print(P, "games", len(g), "regulation ties", round(g.reg_tie.mean(), 3))
