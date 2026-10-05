"""
Monte Carlo box-score simulator.

For each simulated game:
  1. draw (z_home, z_away) game script from historical pairs
  2. team model -> plays, pass rate, sack/scramble rates, cmp%, ypa, ypc,
     INT rate, TD and FG means, each with fitted game-level noise
  3. split attempts to receivers (Dirichlet-multinomial on target share),
     completions by player catch rate, yards per catch drawn from the real
     per-position distribution scaled to the player's and the game's level
  4. split designed runs the same way; scrambles go to the starting QB
  5. assign TDs in proportion to receptions/carries x red-zone propensity

Box scores are internally consistent: QB passing yards == sum of receiving
yards, team totals == sum of players.
"""

from dataclasses import dataclass, field

import numpy as np

REC_MAX, CAR_MAX, SCR_MAX = 20, 40, 14

# Tuned by tune.py on 2024 and confirmed on 2025-26 (README "Model tuning log").
TD_GAMMA = 0.85         # <1 compresses players' TD shares toward each other (top-end TD odds were overconfident)
QB_TD_MULT = 1.8        # extra rushing-TD weight for the starting QB (sneaks, goal line)
P_EXIT = 0.0            # per-game chance a non-QB skill player leaves early. Tested: 0.06 fixed 2024's slightly
                        # narrow ranges but over-widened 2025-26, which were already on target; left off.
EXIT_KEEP = (0.05, 0.6)  # share of his normal volume he keeps when that happens


@dataclass
class Player:
    player_id: str
    name: str
    pos: str
    tgt_share: float
    car_share: float
    rz_tgt_share: float
    rz_car_share: float
    catch_rate: float
    ypr: float
    ypc: float
    rec_td_share: float = 0.0
    rush_td_share: float = 0.0


@dataclass
class TeamInput:
    team: str
    row: dict                  # team priors + Vegas features for TeamModel.predict
    qb: Player                 # starting QB
    qb_scr_ypc: float
    players: list = field(default_factory=list)   # skill players incl. the QB


@dataclass
class SimParams:
    alpha_tgt: float
    alpha_car: float
    no_target_frac: float
    kneel_win: float
    kneel_lose: float
    starter_share: np.ndarray  # empirical share of team dropbacks taken by the named starter
    dists: dict                # unit-mean yard samples: rec_WR, rec_TE, rec_RB, rush_RB, rush_QB, rush_WR, scramble
    # Game yardage is not a sum of iid plays (the field is 100 yards; big
    # completions and checkdown-heavy games trade off). Shrink each team's
    # sampled yardage toward volume x game efficiency by these factors, fit on
    # training seasons so team-level spread matches reality.
    pass_shrink: float = 1.0
    rush_shrink: float = 1.0


def _sum_draws(rng, counts, dist, cap):
    """Sum of `counts[i,j]` iid draws from `dist` for each cell. counts: (n, k)."""
    n, k = counts.shape
    c = np.minimum(counts, cap)
    draws = rng.choice(dist, size=(n, k, cap))
    mask = np.arange(cap)[None, None, :] < c[:, :, None]
    return (draws * mask).sum(axis=2)


def _dirichlet_multinomial(rng, totals, shares, alpha, mult=None):
    """totals: (n,), shares: (k,) summing to 1. mult: optional (n, k) per-sim volume
    multipliers (early exits); the lost share goes to everyone else proportionally."""
    a = np.maximum(shares * alpha, 1e-3)
    p = rng.dirichlet(a, size=len(totals))
    if mult is not None:
        p = p * mult
        p = p / p.sum(axis=1, keepdims=True)
    return rng.multinomial(totals.astype(np.int64), p)


def _compress(w, gamma):
    """Pull TD shares toward each other (gamma < 1) keeping their total."""
    if gamma == 1.0 or w.sum() <= 0:
        return w
    c = np.where(w > 0, w, 0) ** gamma
    return c / c.sum() * w.sum()


def _shrink_to(yds, expected, lam):
    """Pull each sim's team total toward `expected` by factor lam, scaling players
    proportionally. Only positive totals are rescaled (a ratio of mixed signs is unstable)."""
    if lam >= 1.0:
        return yds
    tot = yds.sum(axis=1)
    target = expected + lam * (tot - expected)
    ok = (tot > 1) & (target > 0)
    scale = np.where(ok, target / np.where(ok, tot, 1), 1.0)
    return yds * scale[:, None]


def _allocate_tds(rng, tds, weights):
    """tds: (n,), weights: (n,k) nonneg. Rows with zero weight lose their TDs."""
    tot = weights.sum(axis=1, keepdims=True)
    ok = tot[:, 0] > 0
    p = np.where(ok[:, None], weights / np.where(tot > 0, tot, 1), 1.0 / weights.shape[1])
    out = rng.multinomial(np.where(ok, tds, 0).astype(np.int64), p)
    return out


def _team_sim(rng, model, params, t: TeamInput, z, z_opp):
    n = len(z)
    m = model.predict(t.row, z, z_opp)
    sd = model.extra_sd

    def noisy(name, lo, hi, **vol):
        mean = m[name](**vol) if vol else m[name]
        return np.clip(mean + rng.normal(0, sd[name], n), lo, hi)

    plays = np.round(noisy("plays", 35, 95)).astype(int)
    pass_rate = noisy("pass_rate", 0.2, 0.9)
    dropbacks = rng.binomial(plays, pass_rate)
    sacks = rng.binomial(dropbacks, noisy("sack_rate", 0.0, 0.25))
    scrambles = rng.binomial(dropbacks - sacks, noisy("scr_rate", 0.0, 0.25))
    att = dropbacks - sacks - scrambles
    designed = plays - dropbacks
    cmp_t = noisy("cmp_pct", 0.3, 0.9, att=att)
    ypa_t = noisy("ypa", 2.5, 13.0, att=att)
    ypc_t = noisy("ypc", 1.5, 8.0, designed_runs=designed)
    ints = rng.binomial(att, noisy("int_rate", 0.0, 0.12))
    off_td = np.round(noisy("off_td", 0, 10)).astype(int)
    fg = np.clip(np.round(m["_fg"](off_td) + rng.normal(0, sd["fg"], n)), 0, 7).astype(int)
    pass_td = rng.binomial(off_td, np.clip(m["pass_td_frac"], 0.15, 0.9))
    rush_td = off_td - pass_td

    P = t.players
    k = len(P)
    pos = [p.pos for p in P]
    # Early exits (injury, benching): a non-QB player keeps only part of his volume.
    exit_mult = None
    if P_EXIT > 0:
        can_exit = np.array([p.player_id != t.qb.player_id for p in P])
        hit = (rng.random((n, k)) < P_EXIT) & can_exit[None, :]
        exit_mult = np.where(hit, rng.uniform(*EXIT_KEEP, (n, k)), 1.0)

    # ---- receiving
    tgt_share = np.array([p.tgt_share for p in P])
    tgt_share = tgt_share / tgt_share.sum() * (1 - params.no_target_frac)
    shares_nt = np.append(tgt_share, params.no_target_frac)
    tgts = _dirichlet_multinomial(rng, att, shares_nt, params.alpha_tgt,
                                  None if exit_mult is None else np.column_stack([exit_mult, np.ones(n)]))[:, :k]
    cr = np.array([p.catch_rate for p in P])
    exp_cmp = (tgt_share * cr).sum()
    cr_adj = np.clip(cr[None, :] * (cmp_t / exp_cmp)[:, None], 0.02, 0.98)
    recs = rng.binomial(tgts, cr_adj)
    ypr = np.array([p.ypr for p in P])
    exp_ypcomp = (tgt_share * cr * ypr).sum() / exp_cmp
    ypr_adj = ypr[None, :] * ((ypa_t / cmp_t) / exp_ypcomp)[:, None]
    rec_yds = np.zeros((n, k))
    for j, ps in enumerate(pos):
        key = f"rec_{ps}" if f"rec_{ps}" in params.dists else "rec_WR"
        rec_yds[:, j] = _sum_draws(rng, recs[:, [j]], params.dists[key], REC_MAX)[:, 0] * ypr_adj[:, j]
    rec_yds = _shrink_to(rec_yds, att * ypa_t, params.pass_shrink)

    # ---- rushing
    car_share = np.array([p.car_share for p in P])
    car_share = car_share / car_share.sum()
    cars = _dirichlet_multinomial(rng, designed, car_share, params.alpha_car, exit_mult)
    ypc = np.array([p.ypc for p in P])
    ypc_adj = ypc[None, :] * (ypc_t / (car_share * ypc).sum())[:, None]
    rush_yds = np.zeros((n, k))
    for j, ps in enumerate(pos):
        key = {"QB": "rush_QB", "RB": "rush_RB"}.get(ps, "rush_WR")
        rush_yds[:, j] = _sum_draws(rng, cars[:, [j]], params.dists[key], CAR_MAX)[:, 0] * ypc_adj[:, j]
    rush_yds = _shrink_to(rush_yds, designed * ypc_t, params.rush_shrink)
    designed_rush_yds = rush_yds.sum(axis=1)

    qi = next(i for i, p in enumerate(P) if p.player_id == t.qb.player_id)
    scr_yds = _sum_draws(rng, scrambles[:, None], params.dists["scramble"], SCR_MAX)[:, 0] * t.qb_scr_ypc
    cars[:, qi] += scrambles
    rush_yds[:, qi] += scr_yds

    # ---- touchdowns
    # Weight = player's TD share x how much more/less volume he got this sim
    # than expected. Receivers need a catch to score.
    # The last column is "other": share of team TDs historically scored by
    # players outside this list (new arrivals, gadget plays). Without it every
    # TD is forced onto listed players and the lead back is overstated.
    exp_rec = tgt_share * cr * att.mean()
    rec_td_w = _compress(np.array([p.rec_td_share for p in P]), TD_GAMMA)
    rec_w = rec_td_w[None, :] * (recs + 0.5) / (exp_rec[None, :] + 0.5) * (recs > 0)
    rec_w = np.column_stack([rec_w, np.full(n, max(1 - rec_td_w.sum(), 0.0))])
    rec_td = _allocate_tds(rng, pass_td, rec_w)[:, :k]
    exp_car = car_share * designed.mean()
    exp_car[qi] += scrambles.mean()
    rush_td_w = np.array([p.rush_td_share for p in P])
    if QB_TD_MULT != 1.0:
        tot = rush_td_w.sum()
        rush_td_w[qi] *= QB_TD_MULT
        rush_td_w = rush_td_w / max(rush_td_w.sum(), 1e-9) * tot
    rush_td_w = _compress(rush_td_w, TD_GAMMA)
    rush_w = rush_td_w[None, :] * (cars + 0.5) / (exp_car[None, :] + 0.5) * (cars > 0)
    rush_w = np.column_stack([rush_w, np.full(n, max(1 - rush_td_w.sum(), 0.0))])
    rush_td_p = _allocate_tds(rng, rush_td, rush_w)[:, :k]

    pass_yds = rec_yds.sum(axis=1)
    cmp = recs.sum(axis=1)
    tds = pass_td + rush_td
    points = np.round((6 + model.other_pts_per_td) * tds + 3 * fg).astype(int)

    # The named starter doesn't always finish: thin his line by a sampled share
    # of team dropbacks (injury, benching, blowout relief).
    f = rng.choice(params.starter_share, n)
    s_att = rng.binomial(att, f)
    s_cmp = rng.hypergeometric(cmp, np.maximum(att - cmp, 0), np.minimum(s_att, att))
    s_yds = np.where(cmp > 0, pass_yds * s_cmp / np.maximum(cmp, 1), 0.0)
    qb_line = dict(att=s_att, cmp=s_cmp, pass_yds=s_yds,
                   pass_td=rng.binomial(pass_td, f), ints=rng.binomial(ints, f),
                   sacks=rng.binomial(sacks, f))
    qb_car = rng.binomial(cars[:, qi], f)
    rush_yds[:, qi] = np.where(cars[:, qi] > 0, rush_yds[:, qi] * qb_car / np.maximum(cars[:, qi], 1), 0)
    rush_td_p[:, qi] = rng.binomial(rush_td_p[:, qi], f)
    cars[:, qi] = qb_car

    return dict(
        plays=plays, dropbacks=dropbacks, att=att, cmp=cmp, pass_yds=pass_yds,
        pass_td=pass_td, ints=ints, sacks=sacks, rush_att=cars.sum(axis=1),
        rush_yds=rush_yds.sum(axis=1), rush_td=rush_td, fg=fg, points=points,
        players=dict(tgt=tgts, rec=recs, rec_yds=rec_yds, rec_td=rec_td,
                     car=cars, rush_yds=rush_yds, rush_td=rush_td_p),
        qi=qi, qb_line=qb_line,
        pass_exp=att * ypa_t, rush_exp=designed * ypc_t, designed_rush_yds=designed_rush_yds,
    )


def simulate_game(model, params, home: TeamInput, away: TeamInput, n=10000, seed=None):
    rng = np.random.default_rng(seed)
    pairs = np.asarray(model.z_pairs)
    zz = pairs[rng.integers(0, len(pairs), n)]
    h = _team_sim(rng, model, params, home, zz[:, 0], zz[:, 1])
    a = _team_sim(rng, model, params, away, zz[:, 1], zz[:, 0])

    for me, opp, t in ((h, a, home), (a, h, away)):
        win = me["points"] > opp["points"]
        kn = rng.poisson(np.where(win, params.kneel_win, params.kneel_lose))
        qi = me["qi"]
        me["players"]["car"][:, qi] += kn
        me["players"]["rush_yds"][:, qi] -= kn
        me["rush_att"] = me["rush_att"] + kn
        me["rush_yds"] = me["rush_yds"] - kn
    return h, a
