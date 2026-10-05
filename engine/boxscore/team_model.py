"""
Team-level conditional model.

Game script comes first: z = (points - implied) / SD_PTS for each team, drawn
jointly for both teams from historical pairs. Everything else is regressed on
priors + Vegas + (z, z_opp), so volume, efficiency and touchdowns all move
together the way they do in real games (a team that falls behind throws more;
a team that scores more gains more yards).

Rates are modeled relative to the rolling league level (lg_*), so league-wide
drift in pace and passing carries straight through instead of being pulled
back to the training-period average.

Rates are fit by weighted least squares. For simulation we keep only the
*extra* game-level variance: residual variance minus the play-sampling
variance the Monte Carlo already produces.
"""

import json

import numpy as np
import pandas as pd


def wls(X, y, w):
    sw = np.sqrt(w)
    beta, *_ = np.linalg.lstsq(X * sw[:, None], y * sw, rcond=None)
    return beta


# Each spec: target = num/den - lg, weight = den, relative features.
# "r_" features are prepared in prepare() as (prior - league level).
RATE_SPECS = {
    "plays": dict(num="plays", den="one", lg="lg_plays_pg",
                  x=["r_off_plays_pg", "r_def_plays_pg", "spread", "total_line", "z", "z_opp"]),
    "pass_rate": dict(num="dropbacks", den="plays", lg="lg_pass_rate",
                      x=["r_off_neutral_pr", "r_def_neutral_pr", "r_off_pass_rate", "spread",
                         "total_line", "z", "z_opp"]),
    "sack_rate": dict(num="sacks", den="dropbacks", lg="lg_sack_rate",
                      x=["r_q_sack_rate", "r_off_sack_rate", "r_def_sack_rate", "z", "z_opp"]),
    "scr_rate": dict(num="scrambles", den="nonsack_db", lg="lg_scr_rate",
                     x=["r_q_scr_rate", "r_off_scr_rate", "r_def_scr_rate", "z"]),
    # Efficiency is conditioned on realized volume (att / designed_runs): in real
    # games volume and efficiency are negatively linked, and leaving that out
    # overstates the spread of team yardage.
    "cmp_pct": dict(num="cmp", den="att", lg="lg_cmp_pct",
                    x=["r_q_cmp_pct", "r_off_cmp_pct", "r_def_cmp_pct", "implied", "z", "z_opp", "att"]),
    "ypa": dict(num="pass_yds", den="att", lg="lg_ypa",
                x=["r_q_ypa", "r_off_ypa", "r_def_ypa", "implied", "z", "z_opp", "att"]),
    "ypc": dict(num="designed_rush_yds", den="designed_runs", lg="lg_ypc",
                x=["r_off_ypc", "r_def_ypc", "implied", "z", "z_opp", "designed_runs"]),
    "int_rate": dict(num="ints", den="att", lg="lg_int_rate",
                     x=["r_q_int_rate", "r_off_int_rate", "r_def_int_rate", "spread", "z", "z_opp"]),
}
BINOMIAL = {"pass_rate", "sack_rate", "scr_rate", "cmp_pct", "int_rate"}
RELATIVE = {  # r_<col> = <col> - <league col>
    "off_plays_pg": "lg_plays_pg", "def_plays_pg": "lg_plays_pg",
    "off_neutral_pr": "lg_neutral_pr", "def_neutral_pr": "lg_neutral_pr",
    "off_pass_rate": "lg_pass_rate",
    "q_sack_rate": "lg_sack_rate", "off_sack_rate": "lg_sack_rate", "def_sack_rate": "lg_sack_rate",
    "q_scr_rate": "lg_scr_rate", "off_scr_rate": "lg_scr_rate", "def_scr_rate": "lg_scr_rate",
    "q_cmp_pct": "lg_cmp_pct", "off_cmp_pct": "lg_cmp_pct", "def_cmp_pct": "lg_cmp_pct",
    "q_ypa": "lg_ypa", "off_ypa": "lg_ypa", "def_ypa": "lg_ypa",
    "off_ypc": "lg_ypc", "def_ypc": "lg_ypc",
    "q_int_rate": "lg_int_rate", "off_int_rate": "lg_int_rate", "def_int_rate": "lg_int_rate",
}
# Scoring: TDs | script, then FGs | script + TDs, then pass/rush split of TDs.
TD_X = ["implied", "z"]
FG_X = ["implied", "z", "off_td"]
PASS_TD_FRAC_X = ["off_pass_td_frac", "r_off_pass_rate", "z", "z_opp"]

# Game-time weather (boxscore/weather.py): wind mph outdoors, degrees below 45F / 10, rain.
# Off: tested 2026-09-28 (README tuning log). Vegas totals already price weather; adding it
# was worse on 2024 and roughly flat on 2025-26. Kept for context on the page.
USE_WEATHER = False
WEATHER_X = ["w_wind", "w_cold", "w_rain"]
WEATHER_TARGETS = {"pass_rate", "sack_rate", "cmp_pct", "ypa", "ypc", "int_rate", "fg", "pass_td_frac"}


def feature_cols(name):
    base = {"off_td": TD_X, "fg": FG_X, "pass_td_frac": PASS_TD_FRAC_X}.get(name) or RATE_SPECS[name]["x"]
    return list(base) + (WEATHER_X if USE_WEATHER and name in WEATHER_TARGETS else [])


def design(df, cols):
    return np.column_stack([np.ones(len(df))] + [df[c].to_numpy(float) for c in cols])


def add_script(tg, sd_pts):
    tg = tg.copy()
    tg["z"] = (tg.points - tg.implied) / sd_pts
    opp = tg[["game_id", "team", "z"]].rename(columns={"team": "opp", "z": "z_opp"})
    return tg.merge(opp, on=["game_id", "opp"], how="left")


def prepare(tp, qb_priors):
    """tp: team priors frame; qb_priors: starter QB q_* columns keyed by (game_id, team)."""
    df = tp.merge(qb_priors, on=["game_id", "team"], how="left")
    df["nonsack_db"] = df.dropbacks - df.sacks
    df["one"] = 1.0
    for col, lg in RELATIVE.items():
        df[f"r_{col}"] = df[col] - df[lg]
    return df


class TeamModel:
    def fit(self, df, play_var):
        """df: prepared training frame; play_var: per-play yard variances for ypa/ypc."""
        df = df.dropna(subset=["spread", "total_line", "q_ypa"])
        self.sd_pts = float((df.points - df.implied).std())
        df = add_script(df.drop(columns=[c for c in ("z", "z_opp") if c in df]), self.sd_pts)
        df = df.dropna(subset=["z_opp"])
        self.coef, self.extra_sd = {}, {}
        self.x = {n: feature_cols(n) for n in [*RATE_SPECS, "off_td", "fg", "pass_td_frac"]}
        for c in WEATHER_X:
            if c not in df:
                df[c] = 0.0
            df[c] = df[c].fillna(0.0)

        for name, s in RATE_SPECS.items():
            d = df[df[s["den"]] > 0]
            X = design(d, self.x[name])
            lg = d[s["lg"]].to_numpy(float)
            y = (d[s["num"]] / d[s["den"]]).to_numpy(float) - lg
            w = d[s["den"]].to_numpy(float)
            b = wls(X, y, w)
            resid = y - X @ b
            resid_var = np.average(resid ** 2, weights=w)
            pred = X @ b + lg
            if name == "plays":
                samp = 0.0
            elif name in BINOMIAL:
                samp = np.average(pred * (1 - pred) / w, weights=w)
            else:
                samp = np.average(play_var[name] / w, weights=w)
            self.coef[name] = b.tolist()
            self.extra_sd[name] = float(np.sqrt(max(resid_var - samp, 0.0)))

        X = design(df, self.x["off_td"])
        b = wls(X, df.off_td.to_numpy(float), np.ones(len(df)))
        self.coef["off_td"], self.extra_sd["off_td"] = b.tolist(), float(np.std(df.off_td - X @ b))
        X = design(df, self.x["fg"])
        b = wls(X, df.fg.to_numpy(float), np.ones(len(df)))
        self.coef["fg"], self.extra_sd["fg"] = b.tolist(), float(np.std(df.fg - X @ b))
        d = df[df.off_td > 0]
        X = design(d, self.x["pass_td_frac"])
        self.coef["pass_td_frac"] = wls(X, (d.pass_td / d.off_td).to_numpy(float),
                                        d.off_td.to_numpy(float)).tolist()
        # Points not from offensive TDs or FGs: XP/2pt + defensive/special-teams scores.
        self.other_pts_per_td = float((df.points - 6 * df.off_td - 3 * df.fg).sum() / df.off_td.sum())

        self.z_pairs = df[["z", "z_opp"]].to_numpy().round(4).tolist()
        return self

    # -------------------------------------------------------------- predict
    def predict(self, row, z, z_opp):
        """row: priors for one team-game; z, z_opp: arrays (n_sims,). Returns conditional means."""
        n = len(z)
        feats = dict(row)
        for c in WEATHER_X:
            if feats.get(c) is None or feats[c] != feats[c]:     # missing / NaN -> neutral
                feats[c] = 0.0
        X = getattr(self, "x", None) or {nm: feature_cols(nm) for nm in [*RATE_SPECS, "off_td", "fg", "pass_td_frac"]}

        def lin(name, cols, extra=None):
            b = np.asarray(self.coef[name])
            out = np.full(n, b[0])
            for coef, c in zip(b[1:], cols):
                if c == "z":
                    v = z
                elif c == "z_opp":
                    v = z_opp
                elif extra is not None and c in extra:
                    v = extra[c]
                else:
                    v = float(feats[c])
                out = out + coef * v
            return out

        m = {}
        for name, s in RATE_SPECS.items():
            lg = float(feats[s["lg"]])
            cols = X[name]
            if "att" in cols or "designed_runs" in cols:
                # Needs realized volume from the sim: return a function of it.
                m[name] = (lambda name, cols, lg: lambda **vol: lin(name, cols, vol) + lg)(name, cols, lg)
            else:
                m[name] = lin(name, cols) + lg
        m["off_td"] = lin("off_td", X["off_td"])
        m["pass_td_frac"] = lin("pass_td_frac", X["pass_td_frac"])
        m["_fg"] = lambda off_td: lin("fg", X["fg"], {"off_td": off_td})
        return m

    def save(self, path):
        with open(path, "w") as f:
            json.dump(self.__dict__, f)

    @classmethod
    def load(cls, path):
        m = cls()
        with open(path) as f:
            m.__dict__.update(json.load(f))
        return m
