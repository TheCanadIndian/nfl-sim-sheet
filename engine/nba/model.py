"""
NBA player box-score model.

    minutes  = recency-weighted minutes (short and long memory, blended by games played this
               season), adjusted for teammates who are out
    rate/min = recency-weighted per-minute production, shrunk toward the position average
    team     = Vegas: implied team points from spread + total (players scaled so the team adds
               up); blind: the team's own offense vs the opponent's defense (points per game,
               recency-weighted), league average otherwise
    opponent = defense vs position (G / F / C): what each defense allows per minute to the
               position, relative to league, shrunk
    simulate = minutes ~ normal(sd from history), stats | minutes ~ negative binomial

Everything is as of tip-off (only earlier games count), so one frame serves backtests and live.
"""

import os
import sqlite3

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "nba.db")

STATS = ["pts", "reb", "ast", "fg3m", "stl", "blk", "tov", "fga", "fta"]
SHOW = ["min", "pts", "reb", "ast", "fg3m", "stl", "blk", "tov", "pra"]

MIN_HL, MIN_LONG_HL, MIN_RAMP = 4.0, 20.0, 6     # minutes memory (games); games to trust this season
RATE_HL = 25.0                                    # per-minute production memory (games)
CARRY = 0.6                                       # last season's weight entering a new season
K_SCALE = 0.05    # multiplies K_MIN; tuned on 2024-25 (1.0 under-projected stars ~11%, over bench ~12%)
K_MIN = {"pts": 250, "reb": 250, "ast": 250, "fg3m": 400, "stl": 500, "blk": 500, "tov": 400, "fga": 250, "fta": 300}
TEAM_HL, K_TEAM = 15.0, 6.0
DVP_HL, K_DVP = 20.0, 600.0                       # minutes of evidence toward neutral
USE_DVP = True
DVP_BETA = 0.5                                    # how much of the matchup to apply (tuned in backtest)
TEAM_SCALE = "vegas"                              # "vegas" | "blind"
DISP_GAMMA = 1.5    # dispersion grows with projection size; tuned 2024-26 (in80 by tier .77/.82/.83)
DISP_REF = {"pts": 8.0, "fga": 8.0, "reb": 4.0, "ast": 3.0}
DISP = {"pts": 3.0, "reb": 12.0, "ast": 10.0, "fg3m": 9.0, "stl": 40.0, "blk": 15.0, "tov": 25.0, "fga": 3.0, "fta": 8.0}


def pos_group(p):
    p = str(p or "").upper()
    if p.startswith("C"):
        return "C"
    if "F" in p:
        return "F"
    return "G"


def load(db=DB):
    con = sqlite3.connect(db)
    g = pd.read_sql("SELECT * FROM games", con)
    p = pd.read_sql("SELECT * FROM players", con)
    g["date"] = pd.to_datetime(g.date)
    return g, p


def decayed(df, key, cols, hl, carry):
    d = 0.5 ** (1.0 / hl)
    v = df[cols].to_numpy(float)
    se = df.season.to_numpy()
    out = np.zeros_like(v)
    for idx in df.groupby(key, sort=False).indices.values():
        s = np.zeros(v.shape[1]); ps = None
        for i in idx:
            if ps is not None and se[i] != ps:
                s *= carry
            out[i] = s
            s = d * s + np.nan_to_num(v[i])
            ps = se[i]
    return pd.DataFrame(out, columns=cols, index=df.index)


class Frames:
    """Player-game table with as-of priors. `future` = DataFrame(game_id, season, date, home, away,
    spread, total); `future_players` = DataFrame(game_id, team, player_id, name, pos, status)."""

    def __init__(self, db=DB, future=None, future_players=None):
        g, p = load(db)
        g = g[g.game_type.isin([2, 3])]
        if future is not None:
            g = pd.concat([g[~g.game_id.isin(future.game_id)], future.assign(state="SCHEDULED", game_type=2)], ignore_index=True)
            g["date"] = pd.to_datetime(g.date)
        self.games = g
        p = p[p.game_id.isin(g.game_id)].copy()
        p["played"] = ((p.dnp == 0) & (p["min"] > 0)).astype(float)
        if future_players is not None:
            fp = future_players.copy()
            for c in ["min"] + STATS + ["starter", "dnp", "played"]:
                fp[c] = np.nan
            p = pd.concat([p, fp], ignore_index=True)
        p = p.merge(g[["game_id", "season", "date", "home", "away", "spread", "total"]], on="game_id")
        p["opp"] = np.where(p.team == p.home, p.away, p.home)
        p["is_home"] = (p.team == p.home).astype(float)
        p["pg"] = p.pos.map(pos_group)
        self.p = p.sort_values(["date", "game_id"]).reset_index(drop=True)
        self._team()
        self._priors()

    # ------------------------------------------------------------ team strength
    def _team(self):
        g = self.games.copy()
        done = g.state == "FINAL"
        rows = []
        for side, opp in (("home", "away"), ("away", "home")):
            x = g[["game_id", "season", "date", side, opp, f"{side}_score", f"{opp}_score"]].copy()
            x.columns = ["game_id", "season", "date", "team", "opp", "pf", "pa"]
            x["is_home"] = float(side == "home")
            rows.append(x)
        t = pd.concat(rows).sort_values(["date", "game_id"]).reset_index(drop=True)
        t["one"] = t.game_id.isin(g[done].game_id).astype(float)
        t["pf"] = t.pf.where(t.one == 1, 0.0).astype(float)
        t["pa"] = t.pa.where(t.one == 1, 0.0).astype(float)
        lg = t[t.one == 1].pf.mean()
        s = decayed(t, "team", ["pf", "pa", "one"], TEAM_HL, CARRY)
        t["off"] = (s.pf + K_TEAM * lg) / (s.one + K_TEAM)
        t["def"] = (s.pa + K_TEAM * lg) / (s.one + K_TEAM)
        o = t[["game_id", "team", "def"]].rename(columns={"team": "opp", "def": "opp_def"})
        t = t.merge(o, on=["game_id", "opp"])
        home_adv = 1.5
        t["blind_pts"] = t.off + t.opp_def - lg + np.where(t.is_home == 1, home_adv / 2, -home_adv / 2)
        gl = g.set_index("game_id")
        sp, tot = t.game_id.map(gl.spread), t.game_id.map(gl.total)
        t["vegas_pts"] = np.where(t.is_home == 1, (tot - sp) / 2, (tot + sp) / 2)   # spread is the home line
        self.lg_pts = lg
        self.team = t

    # ------------------------------------------------------------ player priors
    def _priors(self):
        p = self.p
        pl = p.played.fillna(0)
        for c in ["min"] + STATS:
            p[c + "_x"] = p[c].where(pl == 1, 0.0).fillna(0.0)
        p["one"] = pl
        s_short = decayed(p, "player_id", ["min_x", "one"], MIN_HL, CARRY)
        s_long = decayed(p, "player_id", ["min_x", "one"], MIN_LONG_HL, CARRY)
        lg_bench = 8.0          # a player with no NBA history starts as a deep-bench role
        short = (s_short.min_x + 1.0 * lg_bench) / (s_short.one + 1.0)
        long_ = (s_long.min_x + 1.0 * lg_bench) / (s_long.one + 1.0)
        n_season = p.groupby(["player_id", "season"]).one.cumsum() - p.one
        w = np.clip(n_season / MIN_RAMP, 0, 1)
        p["e_min"] = (w * short + (1 - w) * long_).clip(0, 44)
        p["min_trend"] = (short - long_).where(n_season >= 3)        # only once this season has a few games
        p["games_prior"] = p.groupby("player_id").one.cumsum() - p.one
        # how often he gets into the game when he's in uniform (box-score rows incl. DNP-coach's decision)
        dressed = p.dnp.notna().astype(float)
        pp = decayed(p.assign(dressed=dressed), "player_id", ["one", "dressed"], 10.0, CARRY)
        p["p_play"] = ((pp.one + 0.2) / (pp.dressed + 1.0)).clip(0.05, 1.0)
        # minutes variability: recent sd of minutes around the player's mean
        sq = decayed(p.assign(m2=p.min_x ** 2), "player_id", ["m2", "min_x", "one"], 15.0, CARRY)
        var = sq.m2 / sq.one.clip(lower=1) - (sq.min_x / sq.one.clip(lower=1)) ** 2
        p["min_sd"] = np.sqrt(var.clip(lower=4)).where(sq.one >= 3, 6.0).clip(3, 10)
        # per-minute rates, shrunk toward position average
        played = p[p.one == 1]
        lg = {c: played.groupby("pg")[c + "_x"].sum() / played.groupby("pg").min_x.sum() for c in STATS}
        r = decayed(p, "player_id", ["min_x"] + [c + "_x" for c in STATS], RATE_HL, CARRY)
        for c in STATS:
            base = p.pg.map(lg[c])
            p["r_" + c] = (r[c + "_x"] + K_SCALE * K_MIN[c] * base) / (r.min_x + K_SCALE * K_MIN[c])
        self.lg_rate = lg
        # defense vs position (per minute allowed, relative to league), as of each game
        x = p[p.one == 1].groupby(["game_id", "opp", "pg"])[["min_x"] + [c + "_x" for c in ("pts", "reb", "ast", "fg3m")]].sum().reset_index()
        x = x.merge(self.games[["game_id", "season", "date"]], on="game_id").sort_values(["date", "game_id"]).reset_index(drop=True)
        dv = decayed(x, ["opp", "pg"], ["min_x", "pts_x", "reb_x", "ast_x", "fg3m_x"], DVP_HL, CARRY)
        for c in ("pts", "reb", "ast", "fg3m"):
            base = x.pg.map(lg[c])
            x["dvp_" + c] = ((dv[c + "_x"] + K_DVP * base) / (dv.min_x + K_DVP)) / base
        # latest value before each game for every (defense, position), including future games
        dvp_cols = [f"dvp_{c}" for c in ("pts", "reb", "ast", "fg3m")]
        self.dvp_hist = x[["date", "opp", "pg"] + dvp_cols]
        key = p[["date", "opp", "pg"]].copy()
        key["_i"] = np.arange(len(p))
        xs = x[["date", "opp", "pg"] + dvp_cols].sort_values("date")
        ks = key.sort_values("date")
        m = pd.merge_asof(ks, xs, on="date", by=["opp", "pg"], allow_exact_matches=False)
        m = m.sort_values("_i")
        for c in dvp_cols:
            p[c] = m[c].fillna(1.0).to_numpy()
        p = p.merge(self.team[["game_id", "team", "blind_pts", "vegas_pts", "off", "opp_def"]], on=["game_id", "team"], how="left")
        self.p = p

    def dvp_now(self, season):
        """Each defense's current per-minute allowed vs league, by position (for display)."""
        x = self.dvp_hist
        last = x.sort_values("date").groupby(["opp", "pg"]).tail(1)
        return last


MIN_NORM = True        # each team's expected minutes add up to a full game (48 x 5)
TEAM_MIN = 241.0       # 240 + a little overtime
MIN_CAP = 42.0


def team_minutes(x, live=False):
    """Scale each team's expected minutes to TEAM_MIN, capping anyone at MIN_CAP. Out players
    aren't in `x`, so their minutes go to whoever is left, in proportion to role.
    live=True: the players given are everyone available, not everyone who played, so each
    counts by how often he actually gets in (p_play); minutes stay 'if he plays'."""
    out = x.e_min.astype(float).copy()
    w_all = x.p_play.astype(float) if live and "p_play" in x else pd.Series(1.0, index=x.index)
    for _, idx in x.groupby(["game_id", "team"]).groups.items():
        m = out.loc[idx].to_numpy()
        w = w_all.loc[idx].to_numpy()
        if len(m) < 7 or (m * w).sum() <= 0:
            continue
        for _ in range(5):
            m = m * (TEAM_MIN / (m * w).sum())
            over = m > MIN_CAP
            if not over.any():
                break
            m[over] = MIN_CAP
        out.loc[idx] = m
    return out


def project(fr, rows, mode="vegas", n=4000, seed=7, live=False):
    """Means and simulated distributions for `rows` (one player-game each) of Frames.p.
    Team scaling: each team's expected points from its players sum to the team target
    (Vegas implied, or blind)."""
    x = rows.copy()
    if MIN_NORM:
        x["e_min"] = team_minutes(x, live)
    em = x.e_min.to_numpy()
    mu = {}
    for c in STATS:
        r = x["r_" + c].to_numpy()
        if USE_DVP and c in ("pts", "reb", "ast", "fg3m"):
            r = r * x["dvp_" + c].to_numpy() ** DVP_BETA
        mu[c] = r * em
    # team scale: points from players add up to the team target; other stats follow pace partially
    target = x.vegas_pts if mode == "vegas" else x.blind_pts
    target = target.fillna(x.blind_pts).fillna(fr.lg_pts)
    w = x.p_play.to_numpy() if live and "p_play" in x else 1.0
    raw = pd.Series(mu["pts"] * w, index=x.index).groupby([x.game_id, x.team]).transform("sum")
    scale = (target / raw.clip(lower=1)).clip(.7, 1.4).to_numpy()
    mu["pts"] = mu["pts"] * scale
    for c in ("fga", "fta", "fg3m"):
        mu[c] = mu[c] * scale
    pace = np.sqrt(scale)
    for c in ("reb", "ast", "stl", "blk", "tov"):
        mu[c] = mu[c] * pace
    out = x[["game_id", "team", "opp", "player_id", "name", "pos", "pg"]].copy()
    out["e_min"] = em
    for c in STATS:
        out["mu_" + c] = mu[c]
    # simulation: minutes jitter shared across a player's stats
    rng = np.random.default_rng(seed)
    sd = x.min_sd.to_numpy()
    sims = {}
    m = np.clip(em[:, None] + rng.normal(0, 1, (len(x), n)) * sd[:, None], 0, 48)
    fac = np.where(em[:, None] > 0, m / np.maximum(em[:, None], 1e-6), 0)
    sims["min"] = m
    for c in STATS:
        lam = np.maximum(mu[c][:, None] * fac, 1e-6)
        # dispersion grows with the projection: stars' lines are steadier relative to size
        k = DISP[c] * np.clip(mu[c][:, None] / DISP_REF.get(c, 1e9), 1, None) ** DISP_GAMMA
        sims[c] = rng.negative_binomial(k, k / (k + lam))
    sims["pra"] = sims["pts"] + sims["reb"] + sims["ast"]
    out["mu_pra"] = mu["pts"] + mu["reb"] + mu["ast"]
    out["mu_min"] = em
    return out, sims
