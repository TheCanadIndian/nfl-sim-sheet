"""
NBA player box-score model.

    minutes  = recency-weighted minutes (short and long memory, blended by games played this
               season), adjusted for teammates who are out
    rate/min = recency-weighted per-minute production, shrunk toward the position average
    team     = Vegas: implied team points from spread + total (players scaled so the team adds
               up); blind: the team's own offense vs the opponent's defense (points per game,
               recency-weighted), league average otherwise
    opponent = defense vs position (G / F / C): what each defense allows per minute to the
               position, relative to league, shrunk; for points and threes also defense vs archetype
               (8 playstyles from each player's rates as of tip-off, nba/archetypes.json), blended 50/50
               in log terms with the position number
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
# Preseason role (edge audit fix A, owner 2026-11-11; research/edge_audit_nba_preseason.py): the biggest NBA ladder edges came
# from opening-night minutes built on last season's role (Tre Jones / Jrue Holiday projected 30-35, off the bench in preseason,
# played 13-21). For a player's first PRE_K regular-season games, e_min is blended in log scale with his preseason minutes (2+
# preseason games, his last two counted double): weight PRE_A (PRE_A_NEW on a new team), fading to 0 by game PRE_K. Fit on the
# 2024-25 opening only; on the 2025-26 and 2026-27 openings (unseen) minutes MSE 48.2 -> 45.8 / 47.1 -> 44.8, rung log-loss
# pts -12 / -14e-4, reb -39 / -40, ast -7 / 0, nothing worse.
USE_PRESEASON, PRE_A, PRE_A_NEW, PRE_K = True, 0.2, 0.4, 5
# Edge audit fix F (owner 2026-11-11): a player on a new team with under 2 preseason games (Collin Sexton) played 0.926 / 0.857 /
# 0.860 of his projected minutes in his first 5 games (2024-25 / 2025-26 / 2026-27); his e_min x PRE_NT_NOPRE (fit 2024-25),
# fading to 1 by game PRE_K. Unseen 2025-26 / 2026-27: their minutes MSE 77.3 -> 72.6 / 56.6 -> 49.3, every opening row better.
PRE_NT_NOPRE = 0.926
RATE_HL = 25.0                                    # per-minute production memory (games)
CARRY = 0.6                                       # last season's weight entering a new season
K_SCALE = 0.05    # multiplies K_MIN; tuned on 2024-25 (1.0 under-projected stars ~11%, over bench ~12%)
K_MIN = {"pts": 250, "reb": 250, "ast": 250, "fg3m": 400, "stl": 500, "blk": 500, "tov": 400, "fga": 250, "fta": 300}
TEAM_HL, K_TEAM = 15.0, 6.0
DVP_HL, K_DVP = 20.0, 600.0                       # minutes of evidence toward neutral
USE_DVP = True
# Within-team tilt (2026-10-21, research: per-stat gamma fit on 2025 by Poisson deviance, 2026 better in both halves):
# points +0.05 (top-2 scorers' bias +3.5% -> +1.1%), assists +0.025, steals -0.10, blocks -0.05; turnovers mixed (off),
# rebounds / threes fit 0.
TILT = {"pts": 0.05, "ast": 0.025, "stl": -0.10, "blk": -0.05}
DVP_BETA = 0.5                                    # how much of the matchup to apply (tuned in backtest)
# Defense vs archetype (owner's idea 2026-10-19; research/nba_archetypes.py): players in the same position play very
# differently, and defenses give different things to each style. 8 archetypes from per-minute style (k-means on 2023-24).
# Walk-forward 2025 + 2026 regular seasons, Poisson deviance vs the current defense vs position: points better in all 4
# season halves (z -4.0 overall; k 10: -3.8), threes in all 4 (z -3.3; k 10: -3.7); rebounds and assists no better
# (z -0.1 / +0.1), so they keep the position number.
USE_DVA = True
DVA_STATS = ("pts", "fg3m", "stl")              # stats the model blends with the archetype number
DVA_ALL = ("pts", "reb", "ast", "fg3m", "stl", "blk")   # computed for every one (the page shows them all)
# Steals and blocks had no matchup at all until 2026-10-20 (research/nba_arch_stlblk.py, same walk-forward test): the
# position matchup improved both in all 4 season halves (steals z -2.1..-4.4, blocks -1.2..-4.5); for steals the
# position x archetype blend was best (z -2.7..-4.8), for blocks position alone (the blend was worse in 2 halves).
MATCH_STATS = ("pts", "reb", "ast", "fg3m", "stl", "blk")
ARCH = os.path.join(HERE, "archetypes.json")
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


def preseason(g, p):
    """Each player's preseason minutes per season: weighted mean over preseason games he played (his last two count double)."""
    pg = g[g.game_type == 1][["game_id", "season", "date"]]
    x = p.merge(pg, on="game_id")
    x = x[(x.dnp == 0) & (x["min"] > 0)].sort_values("date")
    if x.empty:
        return pd.DataFrame(columns=["season", "player_id", "pre_min", "n_pre"])
    x["w"] = np.where(x.groupby(["season", "player_id"]).date.rank(ascending=False, method="first") <= 2, 2.0, 1.0)
    x["wm"] = x.w * x["min"]
    a = x.groupby(["season", "player_id"]).agg(wm=("wm", "sum"), w=("w", "sum"), n_pre=("min", "size")).reset_index()
    a["pre_min"] = a.wm / a.w
    return a[["season", "player_id", "pre_min", "n_pre"]]


def preseason_blend(p, pre, n_season):
    """e_min blended with preseason minutes for a player's first PRE_K games (see USE_PRESEASON)."""
    x = p[["player_id", "season", "team", "date"]].copy()
    x["n_season"] = n_season.to_numpy()
    x = x.merge(pre, on=["season", "player_id"], how="left")
    x.index = p.index
    o = x.sort_values(["player_id", "date"])
    prev = o.groupby("player_id").team.shift(1)
    first = o.groupby(["player_id", "season"]).cumcount() == 0
    nt = pd.Series(np.where(first & prev.notna() & (prev != o.team), 1.0, np.nan), index=o.index)
    new_team = nt.groupby([o.player_id, o.season]).transform("max").fillna(0).reindex(p.index)
    e = p.e_min.astype(float).clip(lower=1).to_numpy()
    pm = x.pre_min.astype(float).fillna(1).clip(lower=1).to_numpy()
    ok = (x.n_pre.fillna(0) >= 2).to_numpy()
    fade = np.clip(1 - x.n_season.to_numpy() / PRE_K, 0, 1)
    a = np.where(new_team.to_numpy() == 1, PRE_A_NEW, PRE_A) * fade
    out = np.where(ok & (a > 0), np.exp((1 - a) * np.log(e) + a * np.log(pm)), p.e_min.to_numpy())
    nt0 = (new_team.to_numpy() == 1) & ~ok & (fade > 0)                       # new team, no preseason to read (fix F)
    out = np.where(nt0, out * (1 - (1 - PRE_NT_NOPRE) * fade), out)
    return pd.Series(out, index=p.index).clip(0, 44)


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
        self.pre = preseason(g, p)
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
        if USE_PRESEASON and getattr(self, "pre", None) is not None and len(self.pre):
            p["e_min"] = preseason_blend(p, self.pre, n_season)
        p["min_trend"] = (short - long_).where(n_season >= 3)        # only once this season has a few games
        p["games_prior"] = p.groupby("player_id").one.cumsum() - p.one
        # how often he gets into the game when he's in uniform (box-score rows incl. DNP-coach's decision)
        dressed = p.dnp.notna().astype(float)
        pp = decayed(p.assign(dressed=dressed), "player_id", ["one", "dressed"], 10.0, CARRY)
        raw = ((pp.one + 0.2) / (pp.dressed + 1.0)).clip(0.05, 1.0)
        # calibrated to AVAILABLE players (research/nba_pplay_cal.py, 2026-10-24): 'dressed' counts injury DNPs too, but the
        # live page already drops Out players, so the raw rate ran low (0.935 -> played 99.1%) and the live minutes model,
        # spreading 240 minutes by it, put every 'if he plays' stat ~10% high (actual / projected 0.89-0.91, 2025 and 2026).
        # logit(p) = a + b logit(raw), fit on each season and tested on the other: ratio -> 0.96-0.99, deviance better on
        # every stat in all 4 halves (pts MAE 4.68 -> 4.52). Only live projections (team_minutes / project live=True) use it.
        lr = np.log(raw / (1 - raw).clip(lower=1e-4))
        p["p_play"] = (1 / (1 + np.exp(-(P_PLAY_CAL[0] + P_PLAY_CAL[1] * lr)))).clip(0.05, 1.0)
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
        x = p[p.one == 1].groupby(["game_id", "opp", "pg"])[["min_x"] + [c + "_x" for c in MATCH_STATS]].sum().reset_index()
        x = x.merge(self.games[["game_id", "season", "date"]], on="game_id").sort_values(["date", "game_id"]).reset_index(drop=True)
        dv = decayed(x, ["opp", "pg"], ["min_x"] + [c + "_x" for c in MATCH_STATS], DVP_HL, CARRY)
        for c in MATCH_STATS:
            base = x.pg.map(lg[c])
            x["dvp_" + c] = ((dv[c + "_x"] + K_DVP * base) / (dv.min_x + K_DVP)) / base
        # archetype as of each game, and defense vs archetype (same method as defense vs position)
        self.arch = None
        if USE_DVA and os.path.exists(ARCH):
            import json
            A = json.load(open(ARCH))
            X = p[[c for c in A["cols"] if c.startswith("r_")]].copy()
            X["ft_rate"] = p.r_fta / p.r_fga.clip(lower=1e-3)
            X["three_share"] = p.r_fg3m / p.r_fga.clip(lower=1e-3)
            Z = ((X[A["cols"]].to_numpy() - np.array(A["mu"])) / np.array(A["sd"]))
            Z = np.nan_to_num(Z)
            p["arch"] = ((Z[:, None, :] - np.array(A["centers"])[None]) ** 2).sum(-1).argmin(1)
            self.arch = A
            pa = p[p.one == 1]
            lga = {c: pa.groupby("arch")[c + "_x"].sum() / pa.groupby("arch").min_x.sum() for c in DVA_ALL}
            xa = pa.groupby(["game_id", "opp", "arch"])[["min_x"] + [c + "_x" for c in DVA_ALL]].sum().reset_index()
            xa = xa.merge(self.games[["game_id", "season", "date"]], on="game_id").sort_values(["date", "game_id"]).reset_index(drop=True)
            da = decayed(xa, ["opp", "arch"], ["min_x"] + [c + "_x" for c in DVA_ALL], DVP_HL, CARRY)
            for c in DVA_ALL:
                base = xa.arch.map(lga[c])
                xa["dva_" + c] = ((da[c + "_x"] + K_DVP * base) / (da.min_x + K_DVP)) / base
            self.dva_hist = xa[["date", "opp", "arch"] + [f"dva_{c}" for c in DVA_ALL]]
            ka = p[["date", "opp", "arch"]].copy()
            ka["_i"] = np.arange(len(p))
            ma = pd.merge_asof(ka.sort_values("date"), self.dva_hist.sort_values("date"), on="date", by=["opp", "arch"],
                               allow_exact_matches=False).sort_values("_i")
            for c in DVA_ALL:
                p["dva_" + c] = ma["dva_" + c].fillna(1.0).to_numpy()
        # latest value before each game for every (defense, position), including future games
        dvp_cols = [f"dvp_{c}" for c in MATCH_STATS]
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


USE_THREES = True       # threes.py second opinion blended into fg3m (2026-11-03)
USE_STEPS = True        # steps.py: rebounds / assists step means blended in + refit pts / reb / ast spread (2026-11-03)
MIN_NORM = True        # each team's expected minutes add up to a full game (48 x 5)
TEAM_MIN = 241.0       # 240 + a little overtime
MIN_CAP = 42.0
P_PLAY_CAL = (0.48, 1.27)                 # logit calibration of p_play to available players (see _priors)


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
        if USE_DVP and c in MATCH_STATS:
            mt = x["dvp_" + c].to_numpy()
            if USE_DVA and c in DVA_STATS and "dva_" + c in x:
                mt = np.sqrt(mt * x["dva_" + c].fillna(1.0).to_numpy())      # position and archetype, 50/50 in log terms
            r = r * mt ** DVP_BETA
        mu[c] = r * em
    # within-team tilt (TILT): stars' per-minute rates were shrunk too evenly toward the team (2025-26 walk-forward: top-2
    # scorers +3% actual vs projected, the deep bench -6%); each player x (his projection / team average)^gamma, team total kept
    w_t = x.p_play.to_numpy() if live and "p_play" in x else np.ones(len(x))
    keys = [x.game_id.to_numpy(), x.team.to_numpy()]
    for c, gm in TILT.items():
        v = pd.Series(np.maximum(mu[c], 1e-6), index=x.index)
        wv = pd.Series(w_t, index=x.index)
        avg = (v * wv).groupby(keys).transform("sum") / wv.groupby(keys).transform("sum").clip(lower=1e-9)
        t = v * (v / avg) ** gm
        mu[c] = (t * (v * wv).groupby(keys).transform("sum") / (t * wv).groupby(keys).transform("sum").clip(lower=1e-9)).to_numpy()
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
    # threes: geometric mean with the step model (threes.py; walk-forward better in all 4 held-out halves, tails no longer hot)
    th = None
    if USE_THREES:
        try:
            import threes as TH
            P = TH.load()
            F = TH.feats(fr)
            if P is not None and x.index.isin(F.index).all():
                xx = x.assign(e_min=em).join(F[[c for c in F.columns if c not in x.columns]])
                new = TH.mean_new(xx, P, target, x.p_play.to_numpy() if live and "p_play" in x else None)
                mu["fg3m"] = P["SCALE"] * np.sqrt(np.maximum(new, .01) * np.maximum(mu["fg3m"], .01))
                th = P
        except Exception as e:                                   # noqa: BLE001 -- never block the projection
            print("threes model skipped:", type(e).__name__, e)
    # rebounds / assists: geometric blend with the step model; pts / reb / ast spread refit (steps.py; walk-forward better in all
    # 4 held-out halves on the rungs above the median, the old tails ran hot)
    sp = None
    if USE_STEPS:
        try:
            import steps as STP
            P2 = STP.load()
            F2 = STP.feats(fr)
            if P2 is not None and x.index.isin(F2.index).all():
                xx = x.assign(e_min=em).join(F2[[c for c in F2.columns if c not in x.columns]])
                wp = x.p_play.to_numpy() if live and "p_play" in x else None
                for c in STP.STATS:
                    new = np.maximum(STP.mean_new(xx, P2, c, target, wp), .05)
                    w = P2["W"][c]
                    mu[c] = P2["SCALE"][c] * np.exp(w * np.log(new) + (1 - w) * np.log(np.maximum(mu[c], .05)))
                sp = P2
        except Exception as e:                                   # noqa: BLE001 -- never block the projection
            print("steps model skipped:", type(e).__name__, e)
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
    v_min = fac.var(axis=1)
    for c in STATS:
        if c == "fg3m" and th is not None:
            # fitted on games as they happened, so its spread already holds minutes variation: no minutes factor here
            m3 = np.maximum(mu[c], .01)[:, None]; k = th["K0"] * m3 ** th["G"]
            sims[c] = rng.negative_binomial(np.broadcast_to(k, (len(x), n)), np.broadcast_to(k / (k + m3), (len(x), n)))
            continue
        lam = np.maximum(mu[c][:, None] * fac, 1e-6)
        if sp is not None and c in sp["nb"]:
            # steps.py spread: gamma size net of the minutes noise, so the marginal is the fitted one and pts / reb / ast still share minutes
            k = STP.gamma_size(sp, c, mu[c], v_min)[:, None]
            sims[c] = rng.negative_binomial(np.broadcast_to(k, lam.shape), k / (k + lam))
            continue
        # dispersion grows with the projection: stars' lines are steadier relative to size
        k = DISP[c] * np.clip(mu[c][:, None] / DISP_REF.get(c, 1e9), 1, None) ** DISP_GAMMA
        sims[c] = rng.negative_binomial(k, k / (k + lam))
    sims["pra"] = sims["pts"] + sims["reb"] + sims["ast"]
    out["mu_pra"] = mu["pts"] + mu["reb"] + mu["ast"]
    out["mu_min"] = em
    return out, sims
