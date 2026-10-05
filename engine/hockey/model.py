"""
NHL anytime goal scorer model.

    expected goals (player, game) = E[non-PP ice time] x non-PP xG/60
                                  + E[PP ice time]     x PP xG/60,
                                  x finishing (goals per xG, heavily shrunk)
    then a Poisson GLM on top adjusts for the game: opponent xG allowed, opposing
    starter's goals-saved-above-expected, power-play opportunities, home ice,
    back-to-backs. P(anytime goal) = 1 - exp(-lambda).

Everything is as of puck drop (only earlier games count), so the same frames serve
backtests and live projections. xG comes from a logistic model on shot location,
angle, type, rebound, rush, strength and empty net.
"""

import os
import sqlite3
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from boxscore.priors import decayed_sums  # noqa: E402
from boxscore.team_model import wls  # noqa: E402


def poisson_glm(X, y, iters=60):
    """Poisson regression (log link) by iteratively reweighted least squares."""
    beta = np.zeros(X.shape[1])
    beta[0] = np.log(max(y.mean(), 1e-6))
    for _ in range(iters):
        mu = np.exp(np.clip(X @ beta, -20, 5))
        z = X @ beta + (y - mu) / mu
        new = wls(X, z, mu)
        if np.max(np.abs(new - beta)) < 1e-8:
            return new
        beta = new
    return beta

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "nhl.db")
SHOT_TYPES = ["wrist", "snap", "slap", "backhand", "tip-in", "deflected", "wrap-around", "poke", "bat",
              "between-legs", "cradle"]

# Prior settings (tuned in hockey/backtest.py on 2024-25; see README)
TOI_HL = 4.0                    # games: deployment changes fast
RATE_HL, RATE_CARRY = 30.0, 0.7  # games: shot generation is a stable skill
FIN_HL, FIN_CARRY = 120.0, 0.9  # finishing is noisy: long memory, heavy shrink
K_NP_HOURS, K_PP_HOURS = 4.0, 1.5   # pseudo-hours of ice time toward position average
K_FIN_XG = 40.0                 # pseudo expected goals toward league finishing (tuned: 40 beat 10/20)
TEAM_HL, TEAM_CARRY = 15.0, 0.5
K_GOALIE_XG = 60.0
TOI_NORM = "both"  # adopted 2026-10-04 (better log-loss both seasons; fixes live over-projection); "both" / "down": cap a lineup's expected even-strength+PK ice time (norm_base)
NP_TEAM_SEC = 16400.0   # skater-seconds per team-game at even strength + shorthanded (2022-26 average)
GQ_MODE = "xg"     # goalie rating: "xg" = goals allowed per expected goal faced (GSAx);
                   # "sv" = goals allowed vs a league-average save % on shots faced (GSAA)
LG_DAYS, LG_K = 30, 3000.0      # league scoring window and shrinkage (skater-games)
USE_LG_ENV = True
TOI_LONG_HL, TOI_RAMP = 25.0, 5  # long-memory ice time and the games it takes to hand over
TOI_REG_ONLY = True             # expected ice time from regular-season games (playoff minutes run hot)
PP_OPP_ONLY = True
POS = ["C", "L", "R", "D"]
K_POS_GAMES = 20.0              # pseudo-games toward league for xG allowed by position
USE_POS_D = False               # opponent's defense vs position as a goal-model input
USE_H2H = ""                    # "", "goals", "xg", "team": head-to-head vs this opponent as a goal-model input
K_H2H, K_H2H_TEAM = 1.0, 5.0    # pseudo expected goals (player, team)
POS_D_ABS = False              # True: raw rate vs league; False: relative to the team's overall rate


# ------------------------------------------------------------------- xG

def _logit_fit(X, y, iters=30, ridge=1e-3):
    b = np.zeros(X.shape[1])
    for _ in range(iters):
        p = 1 / (1 + np.exp(-X @ b))
        w = np.clip(p * (1 - p), 1e-6, None)
        z = X @ b + (y - p) / w
        A = X.T @ (X * w[:, None]) + ridge * np.eye(X.shape[1])
        nb = np.linalg.solve(A, X.T @ (w * z))
        if np.max(np.abs(nb - b)) < 1e-7:
            return nb
        b = nb
    return b


def xg_design(s):
    d = s.dist.fillna(35).clip(0, 100)
    a = s.angle.fillna(25).clip(0, 90)
    cols = [np.ones(len(s)), np.log1p(d), d / 30, a / 45, (a / 45) ** 2,
            s.rebound, s.rush, s.pp, s.sh, s.empty_net, s.empty_net * np.log1p(d)]
    cols += [(s.shot_type == t).astype(float) for t in SHOT_TYPES]
    return np.column_stack(cols).astype(float)


class XGModel:
    def fit(self, shots):
        u = shots[shots.kind != "blocked-shot"]
        self.b = _logit_fit(xg_design(u), u.goal.to_numpy(float))
        return self

    def predict(self, shots):
        p = 1 / (1 + np.exp(-xg_design(shots) @ self.b))
        return np.where(shots.kind == "blocked-shot", 0.0, p)


# ----------------------------------------------------------------- frames

def load(db=DB):
    con = sqlite3.connect(db)
    games = pd.read_sql("SELECT * FROM games", con).drop_duplicates("game_id")
    games["date"] = pd.to_datetime(games.date)
    shots = pd.read_sql("SELECT * FROM shots", con)
    toi = pd.read_sql("SELECT * FROM toi", con).drop_duplicates(["game_id", "player_id"])
    return games, shots, toi


class Frames:
    """Per player-game and team-game tables with as-of priors. `future` adds unplayed
    games: DataFrame(game_id, date, season, home, away, neutral) plus `future_players`
    DataFrame(game_id, team, player_id, pos, name) and `future_goalies` {(game_id, team): id}."""

    def __init__(self, xg_model, db=DB, future=None, future_players=None, future_goalies=None):
        games, shots, toi = load(db)
        shots["xg"] = xg_model.predict(shots)
        self.games = games
        final = set(toi.game_id)
        g = games[games.game_id.isin(final)][["game_id", "season", "date", "home", "away", "neutral"]]
        if future is not None:
            g = pd.concat([g, future[["game_id", "season", "date", "home", "away", "neutral"]]], ignore_index=True)
        g["date"] = pd.to_datetime(g.date)
        self.g = g

        # ---- player-game
        s = shots[shots.shooter.notna()].copy()
        s["shooter"] = s.shooter.astype("int64")
        s["np"] = 1 - s.pp
        agg = s.assign(ixg_np=s.xg * s.np, ixg_pp=s.xg * s.pp, g_np=s.goal * s.np, g_pp=s.goal * s.pp,
                       sog=s.kind.isin(["shot-on-goal", "goal"]).astype(int)) \
            .groupby(["game_id", "shooter"])[["ixg_np", "ixg_pp", "g_np", "g_pp", "sog"]].sum() \
            .reset_index().rename(columns={"shooter": "player_id"})
        pg = toi.merge(agg, on=["game_id", "player_id"], how="left")
        pg[["ixg_np", "ixg_pp", "g_np", "g_pp", "sog"]] = pg[["ixg_np", "ixg_pp", "g_np", "g_pp", "sog"]].fillna(0)
        pg["np_toi"] = pg.ev_toi + pg.sh_toi
        pg["goals"] = pg.g_np + pg.g_pp
        if future_players is not None:
            pg = pd.concat([pg, future_players.assign(toi=np.nan)], ignore_index=True)
        pg = pg.merge(g[["game_id", "season", "date", "home", "away"]], on="game_id", how="inner")
        pg["opp"] = np.where(pg.team == pg.home, pg.away, pg.home)
        pg["is_home"] = (pg.team == pg.home).astype(float)
        pg["pos_grp"] = np.where(pg.pos == "D", "D", "F")
        pg["F"] = (pg.pos != "D").astype(float)          # after future rows are added
        self.pg = pg.sort_values(["date", "game_id", "team"]).reset_index(drop=True)

        # ---- team-game
        tt = s.groupby(["game_id", "team"]).agg(xgf=("xg", "sum"), gf=("goal", "sum")).reset_index()
        pp_time = toi.groupby(["game_id", "team"]).pp_toi.sum().div(5).rename("pp_time").reset_index()
        tg = g.melt(id_vars=["game_id", "season", "date", "neutral"], value_vars=["home", "away"],
                    var_name="side", value_name="team")
        tg = tg.merge(tt, on=["game_id", "team"], how="left").merge(pp_time, on=["game_id", "team"], how="left")
        opp = tg[["game_id", "team", "xgf", "gf", "pp_time"]].rename(
            columns={"team": "opp", "xgf": "xga", "gf": "ga", "pp_time": "pk_time"})
        tg["opp"] = tg.groupby("game_id").team.transform(lambda x: x.iloc[::-1].values)
        tg = tg.merge(opp, on=["game_id", "opp"], how="left")
        tg["played"] = tg.game_id.isin(final).astype(float)
        tg = tg.sort_values(["date", "game_id", "team"]).reset_index(drop=True)
        tg["b2b"] = tg.groupby("team").date.diff().dt.days.eq(1).astype(float)

        # ---- goalies: starter = goalie in net for the first shot against the team
        gs = shots[shots.goalie.notna() & (shots.kind != "blocked-shot")].copy()
        gs["goalie"] = gs.goalie.astype("int64")
        gs["def_team"] = np.where(gs.is_home == 1, gs.game_id.map(games.set_index("game_id").away),
                                  gs.game_id.map(games.set_index("game_id").home))
        starters = gs.sort_values(["game_id", "t"]).drop_duplicates(["game_id", "def_team"])[["game_id", "def_team", "goalie"]] \
            .rename(columns={"def_team": "team"})
        if future_goalies:
            starters = pd.concat([starters, pd.DataFrame([(gid, t, p) for (gid, t), p in future_goalies.items()],
                                                         columns=["game_id", "team", "goalie"])], ignore_index=True)
        gs["sog"] = gs.kind.isin(["shot-on-goal", "goal"]).astype(int)
        gg = gs.groupby(["game_id", "goalie"]).agg(xg_faced=("xg", "sum"), ga=("goal", "sum"), sa=("sog", "sum")).reset_index()
        self.starters, self.goalie_games = starters, gg
        self.tg = tg
        self._priors()

    # ------------------------------------------------------------- priors
    def _priors(self):
        pg, tg = self.pg.copy(), self.tg.copy()
        played = pg.toi.notna()
        pg["one"] = played.astype(float)
        cols = ["np_toi", "pp_toi", "ixg_np", "ixg_pp", "goals", "one"]
        for c in cols[:-1]:
            pg[c] = pg[c].where(played, 0.0).astype(float)
        pg["xg_all"] = pg.ixg_np + pg.ixg_pp
        lg = pg[played].groupby("pos_grp").apply(lambda x: pd.Series({
            "np_rate": x.ixg_np.sum() / x.np_toi.sum() * 3600, "pp_rate": x.ixg_pp.sum() / max(x.pp_toi.sum(), 1) * 3600,
            "np_toi": x.np_toi.mean(), "pp_toi": x.pp_toi.mean()}))
        self.lg = lg
        b = lg.reindex(pg.pos_grp)
        b.index = pg.index

        gt = self.games.set_index("game_id").game_type
        reg = (pg.game_id.map(gt).fillna(2) == 2) if TOI_REG_ONLY else pd.Series(True, index=pg.index)
        tpg = pg.assign(np_toi=pg.np_toi.where(reg, 0.0), pp_toi=pg.pp_toi.where(reg, 0.0), one=pg.one.where(reg, 0.0))
        t = decayed_sums(tpg, "player_id", ["np_toi", "pp_toi", "one"], TOI_HL, 0.5, change_col="team", change_carry=0.5)
        # Expected ice time: recent games, lightly pulled to a depth-player level for newcomers.
        short_np = (t["_s_np_toi"] + 0.5 * 0.7 * b.np_toi) / (t["_s_one"] + 0.5)
        short_pp = (t["_s_pp_toi"] + 0.5 * 0.3 * b.pp_toi) / (t["_s_one"] + 0.5)
        # Early in a season the last few games (usually last season's final weeks) are a
        # poor guide: lean on long memory until ~TOI_RAMP games into the new season.
        tl = decayed_sums(tpg, "player_id", ["np_toi", "pp_toi", "one"], TOI_LONG_HL, 0.8,
                          change_col="team", change_carry=0.5)
        long_np = (tl["_s_np_toi"] + 0.5 * 0.7 * b.np_toi) / (tl["_s_one"] + 0.5)
        long_pp = (tl["_s_pp_toi"] + 0.5 * 0.3 * b.pp_toi) / (tl["_s_one"] + 0.5)
        n_season = tpg.groupby(["player_id", "season"]).one.cumsum() - tpg.one
        w = np.clip(n_season / TOI_RAMP, 0, 1) if TOI_RAMP > 0 else 1.0
        pg["e_np_toi"] = w * short_np + (1 - w) * long_np
        pg["e_pp_toi"] = w * short_pp + (1 - w) * long_pp
        r = decayed_sums(pg, "player_id", ["np_toi", "pp_toi", "ixg_np", "ixg_pp"], RATE_HL, RATE_CARRY)
        k1, k2 = K_NP_HOURS * 3600, K_PP_HOURS * 3600
        pg["np_rate"] = (r["_s_ixg_np"] + k1 * b.np_rate / 3600) / (r["_s_np_toi"] + k1) * 3600
        pg["pp_rate"] = (r["_s_ixg_pp"] + k2 * b.pp_rate / 3600) / (r["_s_pp_toi"] + k2) * 3600
        f = decayed_sums(pg, "player_id", ["goals", "xg_all"], FIN_HL, FIN_CARRY)
        pg["finish"] = (f["_s_goals"] + K_FIN_XG) / (f["_s_xg_all"] + K_FIN_XG)
        pg["games_prior"] = pg.groupby("player_id").one.cumsum() - pg.one    # NHL games before this one
        pg["base"] = (pg.e_np_toi / 3600 * pg.np_rate + pg.e_pp_toi / 3600 * pg.pp_rate) * pg.finish

        # League scoring environment: goals per skater-game over the previous LG_DAYS days
        # relative to everything before, shrunk. Lets the model follow season-to-season drift.
        daily = pg[played].groupby("date").agg(g=("goals", "sum"), n=("goals", "size")).sort_index()
        cg, cn = daily.g.cumsum(), daily.n.cumsum()
        dates = np.array(sorted(pg.date.unique()), dtype="datetime64[ns]")
        idx_end = np.searchsorted(daily.index.values, dates, side="left")          # games strictly before
        idx_start = np.searchsorted(daily.index.values, dates - np.timedelta64(LG_DAYS, "D"), side="left")
        def csum(c, i):
            return np.where(i > 0, c.values[np.clip(i - 1, 0, None)], 0.0)
        win_g, win_n = csum(cg, idx_end) - csum(cg, idx_start), csum(cn, idx_end) - csum(cn, idx_start)
        all_g, all_n = csum(cg, idx_end), csum(cn, idx_end)
        long_rate = np.where(all_n > 0, all_g / np.maximum(all_n, 1), pg[played].goals.mean())
        recent = (win_g + LG_K * long_rate) / (win_n + LG_K)
        pg["lg_env"] = pd.Series(np.log(recent / long_rate), index=dates).reindex(pg.date).values

        # team context
        tg["one"] = tg.played
        for c in ("xgf", "xga", "gf", "ga", "pp_time", "pk_time"):
            tg[c] = tg[c].where(tg.played == 1, 0.0).fillna(0.0)
        lt = tg[tg.played == 1][["xgf", "xga", "pp_time"]].mean()
        ts = decayed_sums(tg, "team", ["xgf", "xga", "pp_time", "pk_time", "one"], TEAM_HL, TEAM_CARRY)
        K = 5.0
        tg["t_xgf"] = (ts["_s_xgf"] + K * lt.xgf) / (ts["_s_one"] + K) / lt.xgf
        tg["t_xga"] = (ts["_s_xga"] + K * lt.xgf) / (ts["_s_one"] + K) / lt.xgf
        tg["t_pp_drawn"] = (ts["_s_pp_time"] + K * lt.pp_time) / (ts["_s_one"] + K) / lt.pp_time
        tg["t_pp_given"] = (ts["_s_pk_time"] + K * lt.pp_time) / (ts["_s_one"] + K) / lt.pp_time
        o = tg[["game_id", "team", "t_xga", "t_pp_given", "b2b"]].rename(
            columns={"team": "opp", "t_xga": "opp_xga", "t_pp_given": "opp_pp_given", "b2b": "opp_b2b"})
        tg = tg.merge(o, on=["game_id", "opp"], how="left")
        tg = self._pos_defense(pg, tg)

        # goalie quality: goals allowed per xG faced, as of each game
        gg = self.goalie_games.merge(self.g[["game_id", "season", "date"]], on="game_id")
        gg = gg.sort_values(["date", "game_id"]).reset_index(drop=True)
        lg_ga_sa = gg.ga.sum() / max(gg.sa.sum(), 1)              # league goals per shot on goal
        gg["avg_ga"] = gg.sa * lg_ga_sa                            # goals an average goalie allows on those shots
        base_col = "xg_faced" if GQ_MODE == "xg" else "avg_ga"
        gsum = decayed_sums(gg.assign(xg_faced=gg[base_col]), "goalie", ["xg_faced", "ga"], 40.0, 0.7)
        gg["gq"] = (gsum["_s_ga"] + K_GOALIE_XG) / (gsum["_s_xg_faced"] + K_GOALIE_XG)
        self.goalie_table = self._goalie_table(gg, lg_ga_sa)
        # carry each goalie's latest value forward to games he starts (incl. future ones)
        st = self.starters.merge(self.g[["game_id", "date"]], on="game_id")
        allg = pd.concat([gg[["goalie", "date", "gq", "game_id"]],
                          st[~st.game_id.isin(gg.game_id)][["goalie", "date", "game_id"]].assign(gq=np.nan)])
        allg = allg.sort_values(["goalie", "date"])
        last = gg.assign(gq_after=(gsum["_s_ga"] + gg.ga + K_GOALIE_XG) / (gsum["_s_xg_faced"] + gg[base_col] + K_GOALIE_XG))
        st = st.merge(gg[["game_id", "goalie", "gq"]], on=["game_id", "goalie"], how="left")
        miss = st.gq.isna()
        if miss.any():
            latest = last.sort_values("date").groupby("goalie").gq_after.last()
            st.loc[miss, "gq"] = st.loc[miss, "goalie"].map(latest)
        st["gq"] = st.gq.fillna(1.0)
        tg = tg.merge(st[["game_id", "team", "gq"]].rename(columns={"team": "opp", "gq": "opp_gq"}),
                      on=["game_id", "opp"], how="left")
        tg["opp_gq"] = tg.opp_gq.fillna(1.0)
        self.tg = tg

        pg = pg.merge(tg[["game_id", "team", "t_xgf", "t_pp_drawn", "b2b", "opp_xga", "opp_pp_given", "opp_b2b", "opp_gq"]],
                      on=["game_id", "team"], how="left")
        # opponent's defense against this player's position (C / L / R / D), even strength and PK
        pd_cols = tg[["game_id", "team"] + [f"pd_{p}_{s}" for p in POS for s in ("ev", "pk")]] \
            .rename(columns={"team": "opp"})
        pg = pg.merge(pd_cols, on=["game_id", "opp"], how="left")
        p4 = pg.pos.where(pg.pos.isin(POS), "C").to_numpy()
        for s in ("ev", "pk"):
            m = np.column_stack([pg[f"pd_{p}_{s}"].to_numpy(float) for p in POS])
            pg[f"opp_pos_{s}"] = np.nan_to_num(m[np.arange(len(pg)), [POS.index(p) for p in p4]], nan=1.0)
        pg = pg.drop(columns=[c for c in pd_cols.columns if c.startswith("pd_")])
        self.pg = self._h2h(pg)

    def _h2h(self, pg):
        """Head-to-head, as of each game: how the player (and his team) did in earlier meetings
        with this opponent, relative to what his priors expected in those games. Shrunk:
        (actual + K) / (expected + K), K in expected goals."""
        pg = pg.sort_values(["date", "game_id", "team"]).reset_index(drop=True)
        one = pg.one
        pg["_eg"] = pg.base * one                              # expected goals in that game
        pg["_exg"] = (pg.base / pg.finish.clip(lower=.3)) * one   # expected xG
        pg["_xg"] = pg.ixg_np + pg.ixg_pp
        g = pg.groupby(["player_id", "opp"])
        prior = {c: g[c].cumsum() - pg[c] for c in ("goals", "_xg", "_eg", "_exg", "one")}
        pg["h2h_n"] = prior["one"]
        pg["h2h_goals"] = (prior["goals"] + K_H2H) / (prior["_eg"] + K_H2H)
        pg["h2h_xg"] = (prior["_xg"] + K_H2H) / (prior["_exg"] + K_H2H)
        # team: all skaters' goals vs this opponent over the team's expected goals in those games
        t = pg.groupby(["game_id", "date", "team", "opp"], as_index=False)[["goals", "_eg", "one"]].sum()
        t["played"] = (t.one > 0).astype(float)
        t = t.sort_values(["date", "game_id"]).reset_index(drop=True)
        tg = t.groupby(["team", "opp"])
        tp_g = tg.goals.cumsum() - t.goals
        tp_e = tg._eg.cumsum() - t._eg
        t["h2h_team"] = (tp_g + K_H2H_TEAM) / (tp_e + K_H2H_TEAM)
        t["h2h_team_n"] = tg.played.cumsum() - t.played
        pg = pg.merge(t[["game_id", "team", "h2h_team", "h2h_team_n"]], on=["game_id", "team"], how="left")
        return pg.drop(columns=["_eg", "_exg", "_xg"])

    def _goalie_table(self, gg, lg_ga_sa):
        """Per goalie and season: GP, shots against, GA, save %, GSAA (goals saved above an
        average save %), GSAx (goals saved above our expected goals). Regular season + playoffs,
        empty-net goals excluded (no goalie in net)."""
        t = gg.groupby(["goalie", "season"]).agg(gp=("game_id", "nunique"), sa=("sa", "sum"), ga=("ga", "sum"),
                                                xga=("xg_faced", "sum"), avg_ga=("avg_ga", "sum")).reset_index()
        t["sv"] = 1 - t.ga / t.sa.clip(lower=1)
        t["gsaa"] = t.avg_ga - t.ga
        t["gsax"] = t.xga - t.ga
        t.attrs["lg_sv"] = 1 - lg_ga_sa
        return t

    def _pos_defense(self, pg, tg):
        """Each team's xG allowed to centers, left wings, right wings and defensemen, as of each
        game: even strength (incl. 4-on-4 / 3-on-3) and on the penalty kill, relative to league,
        then divided by the team's overall rate so it isolates *which* positions it lets
        through (overall leakiness is already opp_xga). Also kept on self.pos_def for display."""
        played = pg.toi.notna()
        a = pg[played & pg.pos.isin(POS)].groupby(["game_id", "opp", "pos"])[["ixg_np", "ixg_pp", "goals"]].sum()
        a = a.unstack("pos").fillna(0.0)
        a.columns = [f"{c}_{p}" for c, p in a.columns]
        a = a.reset_index().rename(columns={"opp": "team"})
        t = tg.merge(a, on=["game_id", "team"], how="left")
        cols = [c for c in a.columns if c not in ("game_id", "team")]
        for c in cols:
            t[c] = t[c].where(t.played == 1, 0.0).fillna(0.0)
        lgm = t[t.played == 1][cols].mean()
        s = decayed_sums(t, "team", cols + ["one"], TEAM_HL, TEAM_CARRY)
        K = K_POS_GAMES
        for sit, src in (("ev", "ixg_np"), ("pk", "ixg_pp")):
            tot_s = sum(s[f"_s_{src}_{p}"] for p in POS)
            tot_l = sum(lgm[f"{src}_{p}"] for p in POS)
            overall = (tot_s + K * tot_l) / (s["_s_one"] + K) / tot_l
            for p in POS:
                r = (s[f"_s_{src}_{p}"] + K * lgm[f"{src}_{p}"]) / (s["_s_one"] + K) / lgm[f"{src}_{p}"]
                tg[f"pd_{p}_{sit}"] = (r if POS_D_ABS else r / overall).to_numpy()
        # Display table: each team's numbers after its latest played game, relative to league
        # (1.20 = allows 20% more than average). xG at even strength / on the PK, actual goals.
        dec = 0.5 ** (1.0 / TEAM_HL)
        last = t[t.played == 1].groupby("team").tail(1).index
        after = dec * s.loc[last].to_numpy() + t.loc[last, cols + ["one"]].to_numpy()
        a2 = pd.DataFrame(after, columns=cols + ["one"], index=last)
        now = pd.DataFrame({"team": t.loc[last, "team"].to_numpy()}, index=last)
        for p in POS:
            for name, src in (("ev", "ixg_np"), ("pk", "ixg_pp"), ("ga", "goals")):
                now[f"{p}_{name}"] = (a2[f"{src}_{p}"] + K * lgm[f"{src}_{p}"]) / (a2.one + K) / lgm[f"{src}_{p}"]
        cur = t[(t.played == 1) & (t.season == t[t.played == 1].season.max())]
        now["games"] = cur.groupby("team").size().reindex(now.team).fillna(0).astype(int).to_numpy()
        self.pos_now = now.set_index("team")
        self.pos_lg = lgm
        return tg


# ------------------------------------------------------------- goal model

GLM_X = ["log_base", "log_opp_xga", "log_opp_gq", "log_pp_env", "is_home", "b2b", "opp_b2b", "F", "lg_env",
         "log_base_sq", "log_opp_pos", "log_h2h"]
BASE_CURVE = True     # log_base^2 term lets the top end bend (elite scorers were over-projected)


def norm_base(pg):
    """Expected goals with each team's even-strength + shorthanded ice time capped at what a game
    actually has (TOI_NORM: scale the dressed skaters' expected times to NP_TEAM_SEC skater-seconds).
    Only for complete lineups (16+ skaters in the rows given); otherwise the plain base."""
    if not TOI_NORM:
        return pg.base
    key = [pg.game_id, pg.team]
    tot = pg.e_np_toi.groupby(key).transform("sum")
    n = pg.e_np_toi.groupby(key).transform("size")
    s = np.where(n >= 16, NP_TEAM_SEC / tot.clip(lower=1), 1.0)
    if TOI_NORM == "down":
        s = np.minimum(s, 1.0)
    return (pg.e_np_toi * s / 3600 * pg.np_rate + pg.e_pp_toi / 3600 * pg.pp_rate) * pg.finish


def design(pg):
    d = pd.DataFrame(index=pg.index)
    d["log_base"] = np.log(norm_base(pg).clip(lower=1e-3))
    d["log_opp_xga"] = np.log(pg.opp_xga.fillna(1).clip(.5, 2))
    d["log_opp_gq"] = np.log(pg.opp_gq.fillna(1).clip(.6, 1.6))
    pp_share = (pg.e_pp_toi * pg.pp_rate) / (pg.e_np_toi * pg.np_rate + pg.e_pp_toi * pg.pp_rate).clip(lower=1e-6)
    # Own power-play volume is already in the player's PP ice time; the opponent's
    # penalty rate is the new information (more PP chances against undisciplined teams).
    d["log_pp_env"] = pp_share * np.log((pg.opp_pp_given if PP_OPP_ONLY else pg.t_pp_drawn * pg.opp_pp_given)
                                        .fillna(1).clip(.3, 3))
    for c in ("is_home", "b2b", "opp_b2b", "F"):
        d[c] = pg[c].fillna(0).astype(float)
    d["lg_env"] = pg.lg_env.fillna(0).astype(float) if USE_LG_ENV else 0.0
    # opponent's weakness against this position, even-strength and PK parts weighted by where
    # the player's chances come from
    pos_ev = np.log(pg.opp_pos_ev.fillna(1).clip(.6, 1.6))
    pos_pk = np.log(pg.opp_pos_pk.fillna(1).clip(.6, 1.6))
    d["log_opp_pos"] = ((1 - pp_share) * pos_ev + pp_share * pos_pk) if USE_POS_D else 0.0
    h = {"goals": "h2h_goals", "xg": "h2h_xg", "team": "h2h_team"}.get(USE_H2H)
    d["log_h2h"] = np.log(pg[h].fillna(1).clip(.3, 3)) if h and h in pg else 0.0
    c = np.log(0.17)                           # center near the average skater
    d["log_base_sq"] = (d.log_base - c) ** 2 if BASE_CURVE else 0.0
    return np.column_stack([np.ones(len(pg))] + [d[c] for c in GLM_X])


HINGE = np.log(.30 / .70)
# Top-end bend fit on out-of-sample 2024-25 + 2025-26 predictions (players rated 45%+
# scored 3-6 points less often than predicted). Fit on 2024-25 alone (-0.08) it improved
# 2025-26 log-loss and Brier; below 30% nothing changes.
TOP_CAL_C = -0.18


def _hinge_X(p):
    lp = np.log(p / (1 - p))
    return np.column_stack([np.ones(len(p)), lp, np.maximum(lp - HINGE, 0)])


def fit_top_calibration(p, y):
    """logit(p') = logit(p) + c*max(0, logit(p) - logit(.30)): bends only the top end,
    leaves everything below 30% untouched. c chosen by log-loss on out-of-sample predictions."""
    p = np.clip(p, 1e-4, 1 - 1e-4)
    y = np.asarray(y, float)
    best = (np.inf, 0.0)
    for c in np.arange(-0.8, 0.21, 0.02):
        q = apply_top_calibration(p, np.array([0.0, 1.0, c]))
        ll = -(y * np.log(q) + (1 - y) * np.log(1 - q)).mean()
        best = min(best, (ll, c))
    return np.array([0.0, 1.0, best[1]])


def apply_top_calibration(p, abc):
    p = np.clip(p, 1e-4, 1 - 1e-4)
    return 1 / (1 + np.exp(-(_hinge_X(p) @ abc)))


class GoalModel:
    def fit(self, pg):
        x = pg[pg.toi.notna()]
        self.b = poisson_glm(design(x), x.goals.to_numpy(float))
        return self

    def lam(self, pg):
        return np.exp(design(pg) @ self.b)

    def p_goal(self, pg, calibrate=False):
        p = 1 - np.exp(-self.lam(pg))
        return apply_top_calibration(p, np.array([0.0, 1.0, TOP_CAL_C])) if calibrate else p
