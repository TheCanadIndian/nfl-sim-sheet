"""
Glue: load data -> priors -> fit team model + sim params -> build sim inputs.
"""

import numpy as np
import pandas as pd

from . import data as D
from . import priors as P
from . import weather as W
from .sim import Player, SimParams, TeamInput
from .team_model import TeamModel, prepare

FUNNEL_BETA = 0.25
# Position corrections (tuned in tune.py; 1.0 = off). Backtest 2024-26 showed RB targets and
# receiving yards over-projected and QB rushing yards under-projected every season.
RB_TGT_MULT = 1.0
RB_YPR_MULT = 1.0
QB_YPC_MULT = 1.0     # designed QB runs
QB_SCR_MULT = 1.0     # scramble yards
QB_COLS = ["q_cmp_pct", "q_ypa", "q_int_rate", "q_sack_rate", "q_scr_rate", "q_scr_ypc", "q_att_prior"]


class Frames:
    """All derived tables. `future` = list of dicts {game_id, season, week, gameday, home_team,
    away_team, spread_line, total_line}; `future_actives` = DataFrame(game_id, team, player_id)
    and `future_qbs` = {(game_id, team): player_id}."""

    def __init__(self, con, future=None, future_actives=None, future_qbs=None, market_free=False):
        self.games = D.load_games(con)
        self.plays = D.load_plays(con)
        self.pos = D.positions(con)
        tg = D.add_field_goals(D.team_games(self.plays, self.games), con)
        self.kneels = D.load_kneels(con)
        self.pg = D.player_games(self.plays, self.kneels)
        actives = D.game_actives(con, self.pos)
        tg = tg.merge(self.kneels.groupby(["game_id", "team"]).kneels.sum().reset_index(),
                      on=["game_id", "team"], how="left").fillna({"kneels": 0})

        # Historical starters: the schedule's named starting QB (known at kickoff);
        # fall back to most dropbacks where the schedule lacks it.
        g = self.games
        named = pd.concat([
            g[["game_id", "home_team", "home_qb_id"]].set_axis(["game_id", "team", "player_id"], axis=1),
            g[["game_id", "away_team", "away_qb_id"]].set_axis(["game_id", "team", "player_id"], axis=1),
        ]).dropna()
        st = self.pg.sort_values("dropbacks", ascending=False).drop_duplicates(["game_id", "team"])
        st = st[st.dropbacks > 0][["game_id", "team", "player_id"]]
        future_st = pd.DataFrame([(g, t, p) for (g, t), p in (future_qbs or {}).items()],
                                 columns=["game_id", "team", "player_id"])
        # Caller-supplied starters (overrides) win over the schedule's.
        starters = pd.concat([future_st, named, st]).drop_duplicates(["game_id", "team"])
        if future:
            tg = pd.concat([tg, _future_team_rows(future)], ignore_index=True)
            actives = pd.concat([actives, future_actives], ignore_index=True)
        # Make sure starters appear in the player frame even if snaps lack them.
        actives = pd.concat([actives, starters])

        wx = W.features(con)[["game_id", "w_wind", "w_cold", "w_rain"]]
        tg = tg.drop(columns=[c for c in wx.columns if c != "game_id" and c in tg]) \
            .merge(wx, on="game_id", how="left")
        tg[["w_wind", "w_cold", "w_rain"]] = tg[["w_wind", "w_cold", "w_rain"]].fillna(0.0)
        self.means = P.league_means(tg.dropna(subset=["plays"]))
        self.tp = P.team_priors(tg.sort_values(["gameday", "game_id", "team"]), self.means)
        pf = P.player_frame(self.pg, actives, self.tp, self.pos)
        self.base = P.position_baselines(pf.dropna(subset=["team_att"]))
        self.pp = P.player_priors(pf, self.base)
        self.starters = starters
        qb = self.pp.merge(starters, on=["game_id", "team", "player_id"])[["game_id", "team"] + QB_COLS]
        self.df = prepare(self.tp, qb.drop_duplicates(["game_id", "team"]))
        self.dpos = P.defense_position_priors(self.pg, self.pos, self.tp).set_index(["game_id", "team"])
        if market_free:
            # Experimental: replace Vegas spread/total with the market-blind points model.
            from . import market_free as MF
            MF.apply(self, con)


def _future_team_rows(future):
    rows = []
    for g in future:
        for home in (True, False):
            spread = g["spread_line"] if home else -g["spread_line"]
            rows.append(dict(
                game_id=g["game_id"], season=g["season"], week=g["week"],
                gameday=pd.Timestamp(g["gameday"]),
                team=g["home_team"] if home else g["away_team"],
                opp=g["away_team"] if home else g["home_team"],
                is_home=int(home), spread=spread, total_line=g["total_line"],
                spread_line=g["spread_line"], implied=g["total_line"] / 2 + spread / 2,
            ))
    return pd.DataFrame(rows)


# ------------------------------------------------------------------ fitting

def play_variances(plays):
    att = plays[plays.is_att == 1]
    runs = plays[plays.is_designed_run == 1]
    return {"ypa": float(att.pass_yds.var()), "ypc": float(runs.rush_yds.var())}


def unit_dists(plays, pos, max_n=40000, seed=0):
    rng = np.random.default_rng(seed)
    posmap = pos.set_index("player_id").pos
    out = {}
    rec = plays[(plays.is_att == 1) & (plays.complete_pass == 1)]
    rpos = rec.receiver_player_id.map(posmap)
    for ps in ("WR", "TE", "RB"):
        out[f"rec_{ps}"] = rec.pass_yds[rpos == ps].to_numpy(float)
    run = plays[plays.is_designed_run == 1]
    upos = run.rusher_player_id.map(posmap)
    out["rush_RB"] = run.rush_yds[upos == "RB"].to_numpy(float)
    out["rush_QB"] = run.rush_yds[upos == "QB"].to_numpy(float)
    out["rush_WR"] = run.rush_yds[upos.isin(["WR", "TE"])].to_numpy(float)
    out["scramble"] = plays.yards_gained[plays.is_scramble == 1].to_numpy(float)
    for k, v in out.items():
        if len(v) > max_n:
            v = rng.choice(v, max_n, replace=False)
        out[k] = v / v.mean()
    return out


def dirichlet_alpha(pp, share_col, num, den, min_games=4):
    d = pp[(pp.p_games >= min_games) & (pp[den] >= 10)]
    p = d[share_col].clip(1e-4, 0.9999)
    y = d[num] / d[den]
    excess = ((y - p) ** 2 - p * (1 - p) / d[den]).mean() / (p * (1 - p)).mean()
    return float(1 / max(excess, 1e-3) - 1)


def fit(fr: Frames, train_mask):
    df = fr.df[train_mask & fr.df.plays.notna()]
    model = TeamModel().fit(df, play_variances(fr.plays))
    train_games = set(df.game_id)
    pp = fr.pp[fr.pp.game_id.isin(train_games)]
    tg = df
    params = SimParams(
        alpha_tgt=dirichlet_alpha(pp, "p_tgt_share", "tgt", "team_att"),
        alpha_car=dirichlet_alpha(pp, "p_car_share", "designed_car", "team_designed_runs"),
        no_target_frac=float(1 - fr.pg[fr.pg.game_id.isin(train_games)].tgt.sum() / tg.att.sum()),
        kneel_win=float(tg[tg.points > tg.opp_points].kneels.mean()),
        kneel_lose=float(tg[tg.points <= tg.opp_points].kneels.mean()),
        starter_share=starter_shares(fr, train_games),
        dists=unit_dists(fr.plays[fr.plays.game_id.isin(train_games)], fr.pos),
    )
    # Most recent 150 mid/late-season training games (priors are mature by week 6).
    calib = df[(df.week >= 6) & df.spread_line.notna()].sort_values("gameday") \
        .drop_duplicates("game_id").game_id.iloc[-150:].tolist()
    params.pass_shrink, params.rush_shrink = calibrate_shrink(fr, model, params, calib)
    return model, params


def calibrate_shrink(fr, model, params, game_ids, n=1000):
    """Pick shrink factors so simulated team yardage variance matches actual.
    With T' = E + lam*S (E = volume x game efficiency, S = sampling residual):
    Var(T') = Var(E) + 2 lam Cov(E,S) + lam^2 Var(S); solve against actual MSE."""
    from .sim import simulate_game
    acc = {"pass": np.zeros(4), "rush": np.zeros(4)}   # sum Var(S), 2Cov, Var(E), resid^2
    df = fr.df.set_index(["game_id", "team"])
    for gid in game_ids:
        teams = df.loc[gid]
        home = teams.index[teams.is_home == 1][0]
        away = teams.index[teams.is_home == 0][0]
        try:
            hi, ai = team_input(fr, gid, home), team_input(fr, gid, away)
        except (StopIteration, IndexError):
            continue
        h, a = simulate_game(model, params, hi, ai, n=n, seed=abs(hash(gid)) % 2**32)
        for res, t in ((h, home), (a, away)):
            act = teams.loc[t]
            for kind, T, E, y in (("pass", res["pass_yds"], res["pass_exp"], act.pass_yds),
                                  ("rush", res["designed_rush_yds"], res["rush_exp"], act.designed_rush_yds)):
                S = T - E
                acc[kind] += [S.var(), 2 * np.cov(E, S)[0, 1], E.var(), (y - T.mean()) ** 2]
    out = []
    for kind in ("pass", "rush"):
        a, b, c0, mse = acc[kind]
        roots = np.roots([a, b, c0 - mse])
        roots = [r.real for r in roots if abs(r.imag) < 1e-9 and 0 < r.real <= 1.5]
        out.append(float(np.clip(max(roots), 0.3, 1.0)) if roots else 1.0)
    return tuple(out)


def starter_shares(fr, games):
    st = fr.starters[fr.starters.game_id.isin(games)]
    m = st.merge(fr.pg[["game_id", "team", "player_id", "dropbacks"]],
                 on=["game_id", "team", "player_id"], how="left") \
        .merge(fr.df[["game_id", "team", "dropbacks"]], on=["game_id", "team"], suffixes=("", "_team"))
    m = m[m.dropbacks_team > 0]
    return (m.dropbacks.fillna(0) / m.dropbacks_team).clip(0, 1).to_numpy()


# ------------------------------------------------------------ sim inputs

def team_input(fr: Frames, game_id, team, overrides=None):
    row = fr.df[(fr.df.game_id == game_id) & (fr.df.team == team)].iloc[0].to_dict()
    pl = fr.pp[(fr.pp.game_id == game_id) & (fr.pp.team == team)]
    if overrides is not None:
        pl = overrides(pl)
    qb_id = fr.starters[(fr.starters.game_id == game_id) & (fr.starters.team == team)].player_id
    if not len(qb_id):
        qb_id = pl[pl.pos == "QB"].sort_values("q_att_prior", ascending=False).player_id
    qb_id = qb_id.iloc[0]
    # Opponent's target funnel by receiver position (e.g. defenses that feed TEs).
    # Strength tuned on 2023-24 share prediction; yards-per-target funnels were
    # tested and hurt, so only volume is adjusted.
    funnel = fr.dpos.loc[(game_id, team)] if (game_id, team) in fr.dpos.index else None
    players = []
    for r in pl.itertuples():
        f = 1.0
        if funnel is not None and r.pos in P.FUNNEL_POS:
            f = float(funnel[f"share_{r.pos}"]) ** FUNNEL_BETA
        players.append(Player(
            player_id=r.player_id, name=r.name if isinstance(r.name, str) else r.player_id, pos=r.pos,
            tgt_share=0.0 if r.player_id == qb_id else r.p_tgt_share * f * (RB_TGT_MULT if r.pos == "RB" else 1.0),
            car_share=r.p_car_share,
            rz_tgt_share=r.p_rz_tgt_share, rz_car_share=r.p_rz_car_share,
            catch_rate=r.p_catch_rate, ypr=r.p_ypr * (RB_YPR_MULT if r.pos == "RB" else 1.0),
            ypc=r.p_ypc * (QB_YPC_MULT if r.pos == "QB" else 1.0),
            rec_td_share=0.0 if r.player_id == qb_id else r.p_rec_td_share,
            rush_td_share=r.p_rush_td_share,
        ))
    # Non-starting QBs don't throw or run designed plays in the sim.
    for p in players:
        if p.pos == "QB" and p.player_id != qb_id:
            p.car_share = p.tgt_share = p.rec_td_share = p.rush_td_share = 0.0
    qb = next(p for p in players if p.player_id == qb_id)
    for c in QB_COLS:
        if pd.isna(row.get(c)):
            row[c] = pl.loc[pl.player_id == qb_id, c].iloc[0]
    return TeamInput(team=team, row=row, qb=qb, qb_scr_ypc=float(row["q_scr_ypc"]) * QB_SCR_MULT, players=players)
