#!/usr/bin/env python3
"""
Shots on goal per skater, for props and parlays.

    expected shots = exp(b0 + b1 log(long rate x expected TOI) + b2 log(form) + b3 log(opponent) + b4 home + b5 b2b + b6 PP share)
      long rate  shots on goal per 60 over his last 60 games, shrunk toward his position (3 hours of prior)
      form       last 10 games' rate vs the long rate
      attempts   (2026-10-30) att_ratio = log(attempt rate / SOG rate) over the last 60 games (attempts incl. missed and
                 blocked, shrunk the same way); att_form = last-10 attempt rate vs the last-60 attempt rate
                 (research/nhl_sog_attempts.py: better in all 6 test halves 2024-27; with it the SOG form term ~0)
      opponent   shots the opponent allowed per game over its last 25, vs the league
      ice time   the goal model's expected TOI for tonight
    then a negative binomial (sog_params.json alpha) for P(over 0.5 / 1.5 / 2.5 / 3.5 / 4.5).

Fit on 2024-25 + 2025-26 (sog_params.json). Walk-forward checks (fit earlier, test later): over 1.5 / 2.5 / 3.5
predicted 42.2 / 21.5 / 10.1% vs actual 42.7 / 21.9 / 10.2% in 2025-26; within ~1 pt in late 2024-25.
"""

import json
import os

import numpy as np
import pandas as pd
from scipy import stats

HERE = os.path.dirname(os.path.abspath(__file__))
P = json.load(open(os.path.join(HERE, "sog_params.json")))
LINES = (0.5, 1.5, 2.5, 3.5, 4.5)


def p_over(lam, line):
    n = 1 / P["alpha"]
    return 1 - stats.nbinom.cdf(int(np.floor(line)), n, n / (n + lam))


def project(con, players, todo):
    """players: game_id, team, opp, player_id, pos, e_np_toi, e_pp_toi (seconds). todo: game_id, home, away, start_utc.
    Returns players + sog (expected shots) and sog_o{line} (chance of going over each line)."""
    day = pd.Timestamp(todo.start_utc.min()).tz_convert("America/New_York").strftime("%Y-%m-%d")
    g = pd.read_sql("SELECT game_id, date, home, away FROM games WHERE state IN ('OFF','FINAL') AND date < ?", con, params=(day,))
    t = pd.read_sql("SELECT game_id, player_id, team, pos, toi, pp_toi FROM toi", con)
    t = t[t.game_id.isin(g.game_id)]
    s = pd.read_sql("SELECT game_id, shooter AS player_id, SUM(kind IN ('shot-on-goal','goal')) AS sog, COUNT(*) AS att FROM shots "
                    "WHERE shooter IS NOT NULL GROUP BY 1, 2", con)
    h = t.merge(g, on="game_id").merge(s, on=["game_id", "player_id"], how="left")
    h = h[h.pos != "G"]
    h["sog"] = h.sog.fillna(0)
    h["att"] = h.att.fillna(0)
    h["toi_min"] = h.toi / 60
    h = h.sort_values(["date", "game_id"])
    # player history
    H = {pid: x for pid, x in h.groupby("player_id")}
    # opponent shots allowed (team level), last 25 games
    h["opp"] = np.where(h.team == h.home, h.away, h.home)
    ta = h.groupby(["game_id", "date", "opp"]).sog.sum().reset_index()
    allow = ta.groupby("opp").sog.apply(lambda v: v.tail(25).mean())
    league = allow.mean()
    last_game = pd.concat([g[["date", "home"]].rename(columns={"home": "team"}), g[["date", "away"]].rename(columns={"away": "team"})]).groupby("team").date.max()
    yday = (pd.Timestamp(day) - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    home_of = dict(zip(todo.game_id, todo.home))
    out = []
    c, A = P["coef"], P["prior_hours"]
    for r in players.itertuples():
        pos2 = "D" if r.pos == "D" else "F"
        m = P["pos_rate"][pos2]
        x = H.get(int(r.player_id))
        x60 = x.tail(60) if x is not None else None
        x10 = x.tail(10) if x is not None else None
        sog60, toi60 = (x60.sog.sum(), x60.toi_min.sum()) if x60 is not None else (0, 0)
        sog10, toi10 = (x10.sog.sum(), x10.toi_min.sum()) if x10 is not None else (0, 0)
        r_long = (sog60 + A * m) / (toi60 / 60 + A)
        r_short = (sog10 + 1.0 * r_long) / (toi10 / 60 + 1.0)
        att60, att10 = (x60.att.sum(), x10.att.sum()) if x60 is not None else (0, 0)
        ra_long = (att60 + A * m * P.get("att_per_sog", 2.05)) / (toi60 / 60 + A)
        ra_short = (att10 + 1.0 * ra_long) / (toi10 / 60 + 1.0)
        pp_share = (x10.pp_toi.sum() / 60 / toi10) if x10 is not None and toi10 > 0 else 0.0
        toi_exp = (r.e_np_toi + r.e_pp_toi) / 60
        opp_f = allow.get(r.opp, league) / league if league else 1.0
        home = float(home_of.get(r.game_id) == r.team)
        b2b = float(last_game.get(r.team) == yday)
        z = (P["intercept"] + c["base"] * np.log(max(r_long * toi_exp / 60, 1e-3)) + c["form"] * np.log(r_short / r_long)
             + c["opp"] * np.log(max(opp_f, .5)) + c["home"] * home + c["b2b"] * b2b + c["pp"] * pp_share
             + c.get("att_ratio", 0) * np.log(ra_long / r_long) + c.get("att_form", 0) * np.log(ra_short / ra_long))
        lam = float(np.exp(z))
        out.append(dict(sog=lam, **{f"sog_o{str(l).replace('.', '')}": float(p_over(lam, l)) for l in LINES}))
    return players.reset_index(drop=True).join(pd.DataFrame(out))
