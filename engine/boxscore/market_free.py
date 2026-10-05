"""
Market-blind game model (experimental): predicts each team's points from football
information only, so the box-score model can run without Vegas lines.

Inputs, all as of kickoff:
  - opponent-adjusted team strength: EPA per play and points, offense and defense
    (each game's result is adjusted for the opponent's prior rating, then
    recency-weighted like the other team priors)
  - starting QB efficiency (relative to league), pace
  - home field (neutral sites excluded), rest days, short week, off a bye
  - kickoff weather (wind, cold, rain) - priced by Vegas normally, so it matters here
Target: team points minus the rolling league average. Linear model fit walk-forward
(season S is predicted by a model fit on earlier seasons only).

apply(fr) swaps Vegas spread/total/implied in fr.df for these predictions and keeps
the originals as vegas_spread / vegas_total / vegas_implied for comparison.
"""

import numpy as np
import pandas as pd

from . import priors as P
from . import weather as W
from .team_model import wls

ADJ_HL, ADJ_CARRY = 6.0, 0.5
K_GAMES = 3.0
FEATURES = ["adj_off_epa", "adj_def_epa_opp", "adj_off_pts", "adj_def_pts_opp",
            "r_off_plays_pg", "r_def_plays_pg", "r_q_ypa", "r_q_int_rate", "r_q_sack_rate",
            "home", "rest_diff", "short_week", "off_bye", "w_wind", "w_cold", "w_rain"]


def _decayed_mean(df, by, val, weight, hl, carry, k, base):
    d = df.assign(_wv=df[val].fillna(0) * df[weight].fillna(0), _w=df[weight].fillna(0))
    s = P.decayed_sums(d, by, ["_wv", "_w"], hl, carry)
    return (s["_s__wv"] + k * base) / (s["_s__w"] + k)


def features(fr, con):
    """One row per (game_id, team) with the market-free features, as of kickoff."""
    d = fr.df.copy().sort_values(["gameday", "game_id", "team"]).reset_index(drop=True)
    played = d.plays.notna()
    lg_epa = (d.epa * d.plays)[played].sum() / d.plays[played].sum()
    lg_pts = d.points[played].mean()

    # Opponent-adjusted game results. def_epa_pp on a row = what the opponent's defense
    # allowed before this game; the opponent's own offense prior comes from its row.
    opp = d[["game_id", "team", "off_epa_pp", "epa"]].rename(
        columns={"team": "opp", "off_epa_pp": "opp_off_epa_prior", "epa": "opp_epa"})
    d = d.merge(opp, on=["game_id", "opp"], how="left")
    d["g_off_epa"] = d.epa - (d.def_epa_pp - lg_epa)
    d["g_def_epa"] = d.opp_epa - (d.opp_off_epa_prior - lg_epa)       # allowed, adjusted
    d["one"] = played.astype(float)
    ppg = P.decayed_sums(d.assign(pts=d.points.fillna(0), opts=d.opp_points.fillna(0)),
                         "team", ["pts", "opts", "one"], ADJ_HL, ADJ_CARRY)
    d["off_ppg_prior"] = (ppg["_s_pts"] + K_GAMES * lg_pts) / (ppg["_s_one"] + K_GAMES)
    d["def_ppg_prior"] = (ppg["_s_opts"] + K_GAMES * lg_pts) / (ppg["_s_one"] + K_GAMES)
    opp_ppg = d[["game_id", "team", "off_ppg_prior", "def_ppg_prior"]].rename(
        columns={"team": "opp", "off_ppg_prior": "opp_off_ppg", "def_ppg_prior": "opp_def_ppg"})
    d = d.merge(opp_ppg, on=["game_id", "opp"], how="left")
    d["g_off_pts"] = d.points - (d.opp_def_ppg - lg_pts)
    d["g_def_pts"] = d.opp_points - (d.opp_off_ppg - lg_pts)

    d["adj_off_epa"] = _decayed_mean(d, "team", "g_off_epa", "plays", ADJ_HL, ADJ_CARRY, 200, lg_epa) - lg_epa
    d["adj_def_epa"] = _decayed_mean(d, "team", "g_def_epa", "plays", ADJ_HL, ADJ_CARRY, 200, lg_epa) - lg_epa
    d["adj_off_pts"] = _decayed_mean(d, "team", "g_off_pts", "one", ADJ_HL, ADJ_CARRY, K_GAMES, lg_pts) - lg_pts
    d["adj_def_pts"] = _decayed_mean(d, "team", "g_def_pts", "one", ADJ_HL, ADJ_CARRY, K_GAMES, lg_pts) - lg_pts
    oppr = d[["game_id", "team", "adj_def_epa", "adj_def_pts"]].rename(
        columns={"team": "opp", "adj_def_epa": "adj_def_epa_opp", "adj_def_pts": "adj_def_pts_opp"})
    d = d.merge(oppr, on=["game_id", "opp"], how="left")

    g = pd.read_sql("SELECT game_id, location, home_rest, away_rest FROM games", con)
    d = d.merge(g, on="game_id", how="left")
    neutral = d.location.eq("Neutral")
    d["home"] = np.where(neutral, 0.0, np.where(d.is_home == 1, 1.0, -1.0))
    rest = np.where(d.is_home == 1, d.home_rest, d.away_rest).astype(float)
    orest = np.where(d.is_home == 1, d.away_rest, d.home_rest).astype(float)
    d["rest_diff"] = np.clip(np.nan_to_num(rest - orest), -7, 7)
    d["short_week"] = (np.nan_to_num(rest, nan=7) <= 5).astype(float)
    d["off_bye"] = (np.nan_to_num(rest, nan=7) >= 12).astype(float)
    for c in ("w_wind", "w_cold", "w_rain"):
        if c not in d:
            d[c] = 0.0
    d["lg_ppg"] = lg_pts
    for c in FEATURES:
        d[c] = d[c].astype(float).fillna(0.0)
    return d


class PointsModel:
    def fit(self, d):
        d = d[d.points.notna()]
        X = np.column_stack([np.ones(len(d))] + [d[c] for c in FEATURES])
        self.b = wls(X, (d.points - d.lg_ppg).to_numpy(float), np.ones(len(d)))
        self.sd = float(np.std(d.points - d.lg_ppg - X @ self.b))
        return self

    def predict(self, d):
        X = np.column_stack([np.ones(len(d))] + [d[c] for c in FEATURES])
        return X @ self.b + d.lg_ppg.to_numpy(float)


def walkforward_points(d, first_fit_week=4):
    """Predict each season with a model fit on earlier seasons (earliest: fit on itself)."""
    pred = pd.Series(np.nan, index=d.index)
    train_ok = d.points.notna() & ~((d.season == d.season.min()) & (d.week < first_fit_week))
    models = {}
    for s in sorted(d.season.unique()):
        tr = d[train_ok & (d.season < s)]
        if len(tr) < 300:
            tr = d[train_ok & (d.season == s)]
        models[s] = PointsModel().fit(tr)
        m = d.season == s
        pred[m] = models[s].predict(d[m])
    return pred, models


def apply(fr, con):
    """Replace Vegas lines in fr.df with market-free predicted points (in place)."""
    d = features(fr, con)
    d["mf_points"], fr.mf_models = walkforward_points(d)
    opp = d[["game_id", "team", "mf_points"]].rename(columns={"team": "opp", "mf_points": "mf_opp_points"})
    d = d.merge(opp, on=["game_id", "opp"], how="left")
    key = ["game_id", "team"]
    mf = d.set_index(key)[["mf_points", "mf_opp_points"]]
    df = fr.df.set_index(key)
    for c in ("spread", "total_line", "implied", "spread_line"):
        df[f"vegas_{c}"] = df[c]
    df["implied"] = mf.mf_points.reindex(df.index).values
    opp_pts = mf.mf_opp_points.reindex(df.index).values
    df["spread"] = df.implied - opp_pts
    df["total_line"] = df.implied + opp_pts
    df["spread_line"] = np.where(df.is_home == 1, df.spread, -df.spread)
    df["log_implied"] = np.log(df.implied.clip(lower=3))
    fr.df = df.reset_index()
    fr.mf_features = d
    return fr
