#!/usr/bin/env python3
"""
NHL game-state simulator (phase 2 of the play-level sims; owner 2026-10-21: "done for hockey ... really focusing on
ATGs and shots"; no animated view). Built on the lesson of the NBA possession sim (a sim from scratch lost to the box
model): player rates stay the live models' (anytime goal `lam`, shots on goal `sog`); the sim adds the shared game
state, so goals, shots and the score come out of the same simulated games.

Each game, in 20-second steps:
  - penalties -> power plays (each team's expected PP time = its skaters' expected PP minutes / 5; a PP goal ends it),
  - goals and shots on goal at each team's rate for the state: even strength x score effects (trailing teams shoot
    more, period 3 stronger), power play, shorthanded,
  - the empty-net window (last 3 min, down 1 or 2): league rates for the trailing (6-on-5) and leading (empty net) team,
  - overtime (3-on-3, first goal wins), then a shootout (no player goal; winner +1 on the final score).
Team rates are scaled so a team's simulated mean goals = the sum of its players' expected goals (and the same for shots).
Goals and shots are then shared out to players by their even-strength / power-play weights, times a per-player
"big night" factor (gamma, mean 1) shared by his goals and shots.

    python hockey/shiftsim.py fit        # league rates from 2023-24 + 2024-25 -> shiftsim_params.json
"""
import json
import os
import sqlite3
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "nhl.db")
PFILE = os.path.join(HERE, "shiftsim_params.json")
DT, REG, ENW = 20, 3600, 3420          # step (s), regulation (s), empty-net window start
NB = REG // DT
FIT_SEASONS = (20232024, 20242025)


def _bucket(d):
    return np.clip(d, -2, 2) + 2       # team-view score diff -> 0..4 (down 2+, down 1, tied, up 1, up 2+)


def fit(con, seasons=FIT_SEASONS):
    q = ",".join(map(str, seasons))
    g = pd.read_sql(f"SELECT game_id, season, home, away, home_score, away_score FROM games WHERE game_type = 2 "
                    f"AND state IN ('OFF', 'FINAL') AND season IN ({q})", con)
    s = pd.read_sql("SELECT game_id, period, t, team, kind, goal, empty_net, own_sk, opp_sk, pp, sh FROM shots "
                    "WHERE kind IN ('shot-on-goal', 'goal')", con)
    s = s[s.game_id.isin(g.game_id)].merge(g[["game_id", "season", "home"]], on="game_id")
    s["h"] = (s.team == s.home).astype(int)
    gi = {gid: i for i, gid in enumerate(g.game_id)}
    s["gi"] = s.game_id.map(gi)
    G = len(g)
    reg = s[s.t < REG]
    b = (reg.t // DT).to_numpy()
    # goals / shots per game x bin x side
    gl = np.zeros((G, NB, 2)); sg = np.zeros((G, NB, 2))
    np.add.at(sg, (reg.gi.to_numpy(), b, reg.h.to_numpy()), 1)
    gg = reg[reg.goal == 1]
    np.add.at(gl, (gg.gi.to_numpy(), (gg.t // DT).to_numpy(), gg.h.to_numpy()), 1)
    # home-view diff at each bin's start
    cum = np.cumsum(gl, axis=1) - gl
    dh = (cum[:, :, 1] - cum[:, :, 0]).astype(int)
    p3 = (np.arange(NB) * DT >= 2400)[None, :].repeat(G, 0)
    enw = (np.arange(NB) * DT >= ENW)[None, :] & np.isin(np.abs(dh), (1, 2))
    # team-season rates (per second, outside the empty-net window) for the expected counts
    ts = {}
    for side, col in ((1, "home"), (0, "away")):
        for (team, season), idx in g.groupby([col, "season"]).groups.items():
            ii = [gi[x] for x in g.loc[idx, "game_id"]]
            m = ~enw[ii]
            t = ts.setdefault((team, season), np.zeros(3))
            t += [m.sum() * DT, sg[ii, :, side][m].sum(), gl[ii, :, side][m].sum()]
    rate = {k: (v[1] / v[0], v[2] / v[0]) for k, v in ts.items()}
    exp_s = np.zeros((G, 2)); exp_g = np.zeros((G, 2))
    for side, col in ((1, "home"), (0, "away")):
        r = np.array([rate[(t, se)] for t, se in zip(g[col], g.season)])
        exp_s[:, side], exp_g[:, side] = r[:, 0], r[:, 1]
    mS = np.zeros((5, 2)); mG = np.zeros((5, 2)); eS = np.zeros((5, 2)); eG = np.zeros((5, 2))
    for side in (0, 1):
        view = dh if side == 1 else -dh
        bk = _bucket(view)
        m = ~enw
        for k in range(5):
            for p in (0, 1):
                sel = m & (bk == k) & (p3 == bool(p))
                mS[k, p] += sg[:, :, side][sel].sum(); mG[k, p] += gl[:, :, side][sel].sum()
                eS[k, p] += (sel * exp_s[:, side][:, None]).sum() * DT
                eG[k, p] += (sel * exp_g[:, side][:, None]).sum() * DT
    score_sog, score_goal = mS / eS, mG / eG
    # empty-net window: league rates per second by deficit (1, 2) and minute left (3 buckets), trailing / leading side
    en = {}
    for dfc in (1, 2):
        for mb in range(3):
            lo, hi = (ENW + 60 * mb) // DT, (ENW + 60 * (mb + 1)) // DT
            T = 0; c = np.zeros(4)
            for side in (0, 1):
                view = dh if side == 1 else -dh
                sel = np.zeros_like(enw); sel[:, lo:hi] = view[:, lo:hi] == -dfc      # this side trails by dfc
                T += sel.sum() * DT
                c += [gl[:, :, side][sel].sum(), gl[:, :, 1 - side][sel].sum(), sg[:, :, side][sel].sum(), sg[:, :, 1 - side][sel].sum()]
            en[f"{dfc}_{mb}"] = dict(trail_goal=float(c[0] / T), lead_goal=float(c[1] / T), trail_sog=float(c[2] / T), lead_sog=float(c[3] / T), sec=int(T))
    # shorthanded goals / shots per PK second (league)
    toi = pd.read_sql(f"SELECT game_id, team, pp_toi FROM toi WHERE game_id IN (SELECT game_id FROM games WHERE game_type = 2 AND season IN ({q}))", con)
    pk_sec = toi.pp_toi.sum() / 5
    shs = s[(s.own_sk < s.opp_sk) & (s.opp_sk <= 5) & (s.t < REG)]
    # overtime: tied after regulation
    tied = g[dh[:, -1] + (gl[:, -1, 1] - gl[:, -1, 0]) == 0]
    p4 = s[(s.period == 4) & s.game_id.isin(tied.game_id)]
    p4g = p4[p4.goal == 1].groupby("game_id").t.min()
    p_ot = len(p4g) / len(tied)
    ot_sec = ((p4g - 3600).sum() + (len(tied) - len(p4g)) * 300)
    so = tied[~tied.game_id.isin(p4g.index)]
    P = dict(
        fit=f"regular seasons {seasons}", dt=DT,
        score_sog=score_sog.round(4).tolist(),          # [down2+, down1, tied, up1, up2+] x [P1-2, P3]
        # goals: sqrt of the shot effect (trailing teams shoot more, convert a bit less). The raw goal-rate ratios are
        # distorted by high-event games spending more time not tied (tied < every other state); kept for reference.
        score_goal=np.sqrt(score_sog).round(4).tolist(), score_goal_raw=score_goal.round(4).tolist(),
        en=en,
        sh_goal=float((shs.goal == 1).sum() / pk_sec), sh_sog=float(len(shs) / pk_sec),
        ot_goal=float(-np.log(1 - p_ot) / 300), ot_sog=float(len(p4) / ot_sec / 2), p_ot_goal=float(p_ot),
        so_home=float((so.home_score > so.away_score).mean()),
        league_goals=float(gl.sum() / G / 2), league_sog=float(sg.sum() / G / 2),
        pen_len=112.0, frailty=0.06,
    )
    json.dump(P, open(PFILE, "w"), indent=1)
    return P


def _alloc(rng, n, W):
    """Multinomial split of counts n (N,) over players by weights W (N, P), as a chain of binomials."""
    N, Pn = W.shape
    out = np.zeros((N, Pn), np.int16)
    rem = n.astype(np.int64)
    wrem = W.sum(1)
    for j in range(Pn - 1):
        p = np.clip(np.where(wrem > 1e-12, W[:, j] / np.maximum(wrem, 1e-12), 0), 0, 1)
        x = rng.binomial(rem, p)
        out[:, j] = x
        rem = rem - x
        wrem = wrem - W[:, j]
    out[:, -1] = rem
    return out


class Team:
    """One side's inputs: per-player expected goals split even strength / power play, the same for shots on goal, and
    the team's expected PP seconds."""

    def __init__(self, g_np, g_pp, s_np, s_pp, pp_sec):
        self.g_np, self.g_pp = np.asarray(g_np, float), np.asarray(g_pp, float)
        self.s_np, self.s_pp = np.maximum(np.asarray(s_np, float), self.g_np), np.maximum(np.asarray(s_pp, float), self.g_pp)
        self.pp_sec = float(np.clip(pp_sec, 60, 900))
        self.goals, self.sog = self.g_np.sum() + self.g_pp.sum(), self.s_np.sum() + self.s_pp.sum()


def _run(rng, P, H, A, n, scale):
    """Team-level game flow. scale (calibration multipliers): [even-strength goals h, a, PP goals h, a, even-strength
    shots h, a, PP shots h, a]."""
    sg, ss = np.array(P["score_goal"]), np.array(P["score_sog"])
    lg = P["league_goals"]
    out = {}
    T = [H, A]
    ev_sec = [REG - H.pp_sec - A.pp_sec] * 2
    r_ev_g = [T[i].g_np.sum() / ev_sec[i] * scale[i] for i in (0, 1)]
    r_pp_g = [T[i].g_pp.sum() / T[i].pp_sec * scale[2 + i] for i in (0, 1)]
    r_ev_s = [T[i].s_np.sum() / ev_sec[i] * scale[4 + i] for i in (0, 1)]
    r_pp_s = [T[i].s_pp.sum() / T[i].pp_sec * scale[6 + i] for i in (0, 1)]
    pen = [T[1 - i].pp_sec / P["pen_len"] / REG for i in (0, 1)]       # penalties taken by side i per second
    strength = [np.sqrt(max(T[i].goals, .3) / lg) for i in (0, 1)]
    score = np.zeros((n, 2), np.int16)
    pp_left = np.zeros((n, 2))                                         # side i on the power play, seconds left
    G = {k: np.zeros((n, 2), np.int16) for k in ("ev", "pp")}
    S = {k: np.zeros((n, 2)) for k in ("ev", "pp")}                    # non-goal shot intensity
    for b in range(NB):
        t = b * DT
        p3 = int(t >= 2400)
        free = (pp_left[:, 0] <= 0) & (pp_left[:, 1] <= 0)
        for i in (0, 1):                                               # side i takes a penalty -> other side's PP
            hit = free & (rng.random(n) < pen[i] * DT)
            pp_left[hit, 1 - i] = P["pen_len"] * 120 / 112
        d = score[:, 0] - score[:, 1]
        in_en = (t >= ENW) & np.isin(np.abs(d), (1, 2))
        mb = min((t - ENW) // 60, 2) if t >= ENW else 0
        for i in (0, 1):
            view = d if i == 0 else -d
            onpp, onpk = pp_left[:, i] > 0, pp_left[:, 1 - i] > 0
            rg = np.where(onpp, r_pp_g[i], np.where(onpk, P["sh_goal"], r_ev_g[i] * sg[_bucket(view), p3]))
            rs = np.where(onpp, r_pp_s[i], np.where(onpk, P["sh_sog"], r_ev_s[i] * ss[_bucket(view), p3]))
            if t >= ENW:
                for dfc in (1, 2):
                    e = P["en"][f"{dfc}_{mb}"]
                    tr, ld = in_en & (view == -dfc), in_en & (view == dfc)
                    rg = np.where(tr, e["trail_goal"] * strength[i], np.where(ld, e["lead_goal"] * strength[i], rg))
                    rs = np.where(tr, e["trail_sog"] * strength[i], np.where(ld, e["lead_sog"] * strength[i], rs))
            k = rng.poisson(rg * DT)
            cat_pp = onpp & ~in_en
            G["pp"][:, i] += np.where(cat_pp, k, 0).astype(np.int16)
            G["ev"][:, i] += np.where(cat_pp, 0, k).astype(np.int16)
            ng = np.maximum(rs - rg, 0) * DT
            S["pp"][:, i] += np.where(cat_pp, ng, 0)
            S["ev"][:, i] += np.where(cat_pp, 0, ng)
            score[:, i] += k.astype(np.int16)
            pp_left[(k > 0) & onpp, i] = 0                             # a PP goal ends the power play
        pp_left = np.maximum(pp_left - DT, 0)
    reg_tie = score[:, 0] == score[:, 1]
    # overtime: first goal wins, 3-on-3 (team share by strength); else a shootout (no player goal)
    r_ot = P["ot_goal"]
    wh = strength[0] ** 2 / (strength[0] ** 2 + strength[1] ** 2)
    t_goal = rng.exponential(1 / r_ot, n)
    ot_goal = reg_tie & (t_goal < 300)
    ot_home = ot_goal & (rng.random(n) < wh)
    ot_away = ot_goal & ~ot_home
    ot_len = np.where(reg_tie, np.minimum(t_goal, 300), 0)
    G["ev"][:, 0] += ot_home; G["ev"][:, 1] += ot_away
    for i in (0, 1):
        S["ev"][:, i] += np.maximum(P["ot_sog"] - r_ot / 2, 0) * ot_len
    final = score.astype(int).copy()
    final[:, 0] += ot_home; final[:, 1] += ot_away
    so = reg_tie & ~ot_goal
    so_home = so & (rng.random(n) < P["so_home"])
    final[:, 0] += so_home; final[:, 1] += so & ~so_home
    out.update(G=G, S=S, final=final, reg_tie=reg_tie)
    return out


def simulate(H, A, n=10000, seed=0, P=None, calibrate=True):
    """Returns dict: home / away player goals and shots on goal (n x players), final score (n x 2, shootout winner +1),
    reg_tie. Team means are calibrated to the players' expected goals / shots."""
    P = P or json.load(open(PFILE))
    rng = np.random.default_rng(seed)
    scale = np.ones(8)
    tgt = np.array([H.g_np.sum(), A.g_np.sum(), H.g_pp.sum(), A.g_pp.sum(), H.s_np.sum(), A.s_np.sum(), H.s_pp.sum(), A.s_pp.sum()])
    if calibrate:                     # each team's even-strength / PP goal and shot means -> its players' sums
        for _ in range(3):
            o = _run(rng, P, H, A, 3000, scale)
            ge, gp = o["G"]["ev"].mean(0), o["G"]["pp"].mean(0)
            got = np.r_[ge, gp, ge + o["S"]["ev"].mean(0), gp + o["S"]["pp"].mean(0)]
            scale *= np.where(tgt > 1e-6, tgt / np.maximum(got, 1e-6), 1)
    o = _run(rng, P, H, A, n, scale)
    res = dict(final=o["final"], reg_tie=o["reg_tie"])
    for i, (side, Tm) in enumerate((("home", H), ("away", A))):
        k = len(Tm.g_np)
        a = P["frailty"]
        f = rng.gamma(1 / a, a, (n, k)) if a > 0 else np.ones((n, k))
        g = _alloc(rng, o["G"]["ev"][:, i], Tm.g_np * f) + _alloc(rng, o["G"]["pp"][:, i], (Tm.g_pp if Tm.g_pp.sum() > 0 else Tm.g_np) * f)
        ng = rng.poisson(o["S"]["ev"][:, i])
        npp = rng.poisson(o["S"]["pp"][:, i])
        wn, wp = np.maximum(Tm.s_np - Tm.g_np, 1e-9), np.maximum(Tm.s_pp - Tm.g_pp, 1e-9)
        s = g + _alloc(rng, ng, wn * f) + _alloc(rng, npp, wp * f)
        res[side] = dict(goals=g, sog=s)
    return res


def sog_pp_share(con, rows):
    """Each player's power-play share of his shots tonight: his even-strength / PP shot rates over his last 60 games
    (shrunk toward the league: 3 h / 1 h of prior) times tonight's expected ice time. rows: player_id, e_np_toi, e_pp_toi."""
    ids = sorted({int(x) for x in rows.player_id.dropna()})
    if not ids:
        return np.zeros(len(rows))
    q = ",".join(map(str, ids))
    t = pd.read_sql(f"SELECT t.game_id, t.player_id, t.toi, t.pp_toi, g.date FROM toi t JOIN games g USING (game_id) "
                    f"WHERE t.player_id IN ({q}) AND t.toi > 0", con).sort_values("date")
    t = t.groupby("player_id").tail(60)
    sh = pd.read_sql(f"SELECT game_id, shooter AS player_id, pp FROM shots WHERE kind IN ('shot-on-goal', 'goal') AND shooter IN ({q})", con)
    sh = sh.merge(t[["game_id", "player_id"]], on=["game_id", "player_id"])
    sh["player_id"] = sh.player_id.astype("int64")
    c = sh.groupby("player_id").pp.agg(["sum", "count"])
    tm = t.groupby("player_id")[["toi", "pp_toi"]].sum()
    d = tm.join(c, how="left").fillna(0)
    lg_np = (d["count"] - d["sum"]).sum() / max((d.toi - d.pp_toi).sum() / 3600, 1)
    lg_pp = d["sum"].sum() / max(d.pp_toi.sum() / 3600, 1)
    rnp = ((d["count"] - d["sum"]) + 3 * lg_np) / ((d.toi - d.pp_toi) / 3600 + 3)
    rpp = (d["sum"] + 1 * lg_pp) / (d.pp_toi / 3600 + 1)
    pid = rows.player_id.astype("int64")
    a_np = rows.e_np_toi.to_numpy() * pid.map(rnp).fillna(lg_np).to_numpy()
    a_pp = rows.e_pp_toi.to_numpy() * pid.map(rpp).fillna(lg_pp).to_numpy()
    return np.where(a_np + a_pp > 0, a_pp / np.maximum(a_np + a_pp, 1e-9), 0)


COMBOS = ("win", "o55", "s3")    # goal + his team wins, goal + game over 5.5, 3+ shots + goal (tested 2025-26, see CLAUDE.md)


def game_lifts(home, away, who, n=10000, seed=0, P=None):
    """home / away: DataFrames (player_id, lam, e_np_toi, e_pp_toi, np_rate, pp_rate, sog, sog_ppf). who: player ids to
    report. Returns {player_id: {combo: lift}} with lift = P(both) / (P(a) P(b)) in the simulated games."""
    T = []
    for x in (home, away):
        bn, bp = x.e_np_toi / 3600 * x.np_rate, x.e_pp_toi / 3600 * x.pp_rate
        gpp = (bp / (bn + bp)).fillna(0).to_numpy()
        lam, sg, sp = x.lam.to_numpy(), x.sog.fillna(0).to_numpy(), x.sog_ppf.fillna(0).to_numpy()
        T.append(Team(lam * (1 - gpp), lam * gpp, sg * (1 - sp), sg * sp, x.e_pp_toi.sum() / 5))
    r = simulate(T[0], T[1], n=n, seed=seed, P=P)
    f = r["final"]
    win = {"home": f[:, 0] > f[:, 1], "away": f[:, 1] > f[:, 0]}
    o55 = f.sum(1) > 5.5
    out = {}
    for nm, x in (("home", home), ("away", away)):
        G, S = r[nm]["goals"] >= 1, r[nm]["sog"] > 2.5
        for j, pid in enumerate(x.player_id.astype(int)):
            if pid not in who:
                continue
            g = G[:, j]
            lift = lambda b: float((g & b).mean() / max(g.mean() * b.mean(), 1e-9))
            out[pid] = dict(win=round(lift(win[nm]), 3), o55=round(lift(o55), 3), s3=round(lift(S[:, j]), 3))
    return out


if __name__ == "__main__":
    if (sys.argv[1] if len(sys.argv) > 1 else "") == "fit":
        P = fit(sqlite3.connect(DB))
        print(json.dumps({k: v for k, v in P.items() if k != "en"}, indent=1))
        for k, v in P["en"].items():
            print(k, {a: round(b, 5) for a, b in v.items()})
