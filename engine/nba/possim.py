#!/usr/bin/env python3
"""
NBA possession simulator, phase 2 (owner 2026-10-21). Every possession of a game, n games at once (numpy across sims):

  floor     five per team drawn each possession by systematic sampling, so every player's on-court share equals his
            expected minutes / 48 exactly (the box-score model's minutes); in the 4th quarter with 6 min or less left and a
            20+ margin, the starters (top 5 by minutes) sit -- garbage time, the blowout correlation the box model lacks
  ender     who ends the possession: the five on the floor, weighted by usage per minute (FGA + 0.44 FTA + TOV)
  outcome   turnover (his share; a steal 55% of the time, credited by steal rate) / shooting foul (2 free throws at his
            FT%) / a shot: zone from his shot map (rim, paint, mid, corner 3, above-break 3), made at his zone FG% x the
            defense's zone FG% allowed (vs league)
  assists   a make is assisted at the league rate for its zone, credited to a teammate on the floor by assist rate
  rebounds  a miss: offensive at the offense's / defense's rebound rates, credited by rebound rate; the offense keeps it
  pace      possessions and a team scoring factor solved so each team's mean points = its Vegas implied total
All rates are as of the game (earlier games only, recency-weighted, last season carried at 0.6, shrunk to position).
Shot zones come from nba/plays.db (nba/plays.py).

    python nba/possim.py backtest [n_games]   # 2026 regular season vs the box-score model (MAE, log score)
"""
import os
import sqlite3
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import model as M  # noqa: E402

PLAYS = os.path.join(HERE, "plays.db")
ZONES = ["rim", "paint", "mid", "c3", "ab3"]
PTS_Z = np.array([2, 2, 2, 3, 3])
AST_Z = np.array([.55, .42, .45, .90, .82])            # league share of makes assisted by zone (2024-26 plays)
STEAL_SHARE, OREB_LG, HL, CARRY = .55, .26, 25.0, .6
TEAM_REB = .12                                             # share of rebounds credited to the team, not a player


def zone_table():
    con = sqlite3.connect(PLAYS)
    s = pd.read_sql("SELECT game_id, p1, x, y, att, made, type FROM plays WHERE shot = 1 AND att IN (2, 3) AND p1 IS NOT NULL", con)
    d = np.hypot((s.x - 25.0), (s.y - 1.5))
    z = np.where(s.att == 3, np.where(s.y.fillna(20) <= 9, "c3", "ab3"),
                 np.where(d.isna(), np.where(s.type.str.contains("Layup|Dunk|Tip", na=False), "rim", "mid"),
                          np.where(d < 4.5, "rim", np.where(d < 11, "paint", "mid"))))
    s["z"] = z
    t = s.assign(one=1).pivot_table(index=["game_id", "p1"], columns="z", values=["one", "made"], aggfunc="sum", fill_value=0)
    t.columns = [f"{'a' if a == 'one' else 'm'}_{b}" for a, b in t.columns]
    t = t.reset_index().rename(columns={"p1": "player_id"})
    t["player_id"] = t.player_id.astype(int)
    for zz in ZONES:
        for c in ("a_", "m_"):
            if c + zz not in t:
                t[c + zz] = 0
    return t


class Sim:
    def __init__(self, fr):
        """fr: model.Frames. Adds as-of shot-zone, usage, rebound, assist, steal and defense rates to fr.p."""
        p = fr.p
        zt = zone_table()
        p = p.merge(zt, on=["game_id", "player_id"], how="left")
        cols = ["min_x"] + [f"{c}{z}" for z in ZONES for c in ("a_", "m_")] + ["fta_x", "ftm_x", "tov_x", "oreb_x", "dreb_x", "ast_x", "stl_x"]
        pl = p.played.fillna(0) == 1
        for c in ("ftm", "oreb", "dreb", "ast", "stl", "tov", "fta"):
            p[c + "_x"] = p[c].where(pl, 0).fillna(0).astype(float) if c in p else 0.0
        for z in ZONES:
            for c in ("a_", "m_"):
                p[c + z] = p[c + z].where(pl, 0).fillna(0).astype(float)
        D = M.decayed(p, "player_id", cols, HL, CARRY)
        lg = D.sum()
        lgz_fg = {z: (p["m_" + z].sum() + 1) / (p["a_" + z].sum() + 1) for z in ZONES}
        mins = D.min_x + 1e-6
        by_pg = p[pl].groupby("pg")
        fga = sum(D["a_" + z] for z in ZONES)
        use = fga + .44 * D.fta_x + D.tov_x
        pg_use = (by_pg.fga.sum() + .44 * by_pg.fta.sum() + by_pg.tov.sum()) / by_pg["min"].sum()
        p["s_use"] = (use + 150 * p.pg.map(pg_use)) / (mins + 150)
        p["s_tov"] = (D.tov_x + 20 * .12) / (use + 20)
        p["s_ft"] = (.44 * D.fta_x + 20 * .10) / (use + 20)
        pg_zs = {g: np.array([x["a_" + z].sum() for z in ZONES], float) for g, x in p[pl].groupby("pg")}
        for g in pg_zs:
            pg_zs[g] = pg_zs[g] / pg_zs[g].sum()
        Z = np.stack([D["a_" + z].to_numpy() for z in ZONES], 1)
        prior = np.stack([pg_zs.get(g, pg_zs["G"]) for g in p.pg], 0)
        self.zshare = (Z + 30 * prior) / (Z.sum(1, keepdims=True) + 30)
        self.zfg = np.stack([((D["m_" + z] + 40 * lgz_fg[z]) / (D["a_" + z] + 40)).to_numpy() for z in ZONES], 1)
        p["s_ftp"] = (D.ftm_x + 15 * .78) / (D.fta_x + 15)
        for c, pri in (("oreb", 400), ("dreb", 400), ("ast", 300), ("stl", 400)):
            rate = p[pl][c].sum() / p[pl]["min"].sum()
            pgr = (by_pg[c].sum() / by_pg["min"].sum())
            p["s_" + c] = (D[c + "_x"] + pri * p.pg.map(pgr)) / (mins + pri)
        # defense: zone FG% allowed (by the opponent team), vs league, shrunk
        dz = p[["game_id", "opp", "season", "date"] + [f"{c}{z}" for z in ZONES for c in ("a_", "m_")]].groupby(["game_id", "opp", "season", "date"]).sum().reset_index().sort_values("date")
        DD = M.decayed(dz, "opp", [f"{c}{z}" for z in ZONES for c in ("a_", "m_")], 15.0, CARRY)
        for z in ZONES:
            dz["d_" + z] = ((DD["m_" + z] + 150 * lgz_fg[z]) / (DD["a_" + z] + 150)) / lgz_fg[z]
        key = p[["game_id", "opp"]].merge(dz[["game_id", "opp"] + [f"d_{z}" for z in ZONES]], on=["game_id", "opp"], how="left")
        self.dadj = key[[f"d_{z}" for z in ZONES]].fillna(1.0).to_numpy()
        self.p = p
        self.idx = {i: k for k, i in enumerate(p.index)}

    # ------------------------------------------------------------ one game
    def game(self, rows, n=1000, seed=1, target=None):
        """rows: this game's player rows from self.p (index preserved) with e_min (team-normalized). target: {team: pts}."""
        rng = np.random.default_rng(seed)
        teams = list(dict.fromkeys(rows.team))
        T = {}
        for t in teams:
            x = rows[rows.team == t]
            ii = np.array([self.idx[i] for i in x.index])
            em = x.e_min.to_numpy(float)
            pi = np.clip(em / em.sum() * 5, 0, .985)
            for _ in range(10):                                        # re-spread what the cap cut so the floor holds 5
                pi = np.clip(pi * 5 / pi.sum(), 0, .985)
            st = np.argsort(-em)[:5]
            pg_ = pi.copy(); pg_[st] = .05
            for _ in range(10):
                pg_ = np.clip(pg_ * 5 / pg_.sum(), 0, .985)
            P = self.p.loc[x.index]
            T[t] = dict(n=len(x), pi=pi, pig=pg_, use=P.s_use.to_numpy(), tov=P.s_tov.to_numpy(), ft=P.s_ft.to_numpy(), ftp=P.s_ftp.to_numpy(),
                        zs=self.zshare[ii], zfg=self.zfg[ii], dadj=self.dadj[ii].mean(0), oreb=P.s_oreb.to_numpy(), dreb=P.s_dreb.to_numpy(),
                        ast=P.s_ast.to_numpy(), stl=P.s_stl.to_numpy(), idx=x.index)
        if len(teams) != 2:
            return None
        a, b = teams
        kap = {a: 1.0, b: 1.0}
        npos = 100
        if target:
            for _ in range(2):                                         # pilot runs: possessions and scoring factors to hit Vegas
                pts = self._run(T, a, b, 300, npos, kap, rng, stats=False)
                ppp = {t: pts[t].mean() / npos for t in (a, b)}
                npos = int(round((target[a] + target[b]) / (ppp[a] + ppp[b])))
                for t in (a, b):
                    kap[t] *= np.clip((target[t] / npos) / ppp[t], .8, 1.25) ** .9
        return self._run(T, a, b, n, npos, kap, rng, stats=True)

    def _run(self, T, a, b, n, npos, kap, rng, stats):
        S = {t: {k: np.zeros((n, T[t]["n"])) for k in ("pts", "reb", "ast", "fg3m", "stl", "tov", "min")} for t in (a, b)}
        score = {a: np.zeros(n), b: np.zeros(n)}
        steps = 2 * npos
        dt = 48.0 / steps
        def floor(t, garb):
            pi = np.where(garb[:, None], T[t]["pig"][None, :], T[t]["pi"][None, :])
            C = np.cumsum(pi, 1); lo = C - pi
            u = rng.random((n, 1))
            return (np.floor(C - u) - np.floor(lo - u)) >= 1
        def pick(w):                                                   # one column per row of weights (n x m), -1 if none
            s = w.sum(1); r = rng.random(n) * s
            j = (np.cumsum(w, 1) < r[:, None]).sum(1)
            return np.where(s > 0, np.minimum(j, w.shape[1] - 1), -1)
        ar = np.arange(n)
        for k in range(steps):
            off, de = (a, b) if k % 2 == 0 else (b, a)
            garb = (k / steps >= .875) & (np.abs(score[a] - score[b]) >= 20)
            fo, fd = floor(off, garb), floor(de, garb)
            if stats:
                S[off]["min"] += fo * dt; S[de]["min"] += fd * dt
            O, Dd = T[off], T[de]
            live = np.ones(n, bool)
            for _ in range(4):                                         # a possession: up to 3 offensive rebounds
                if not live.any():
                    break
                e = pick(fo * O["use"][None, :] * live[:, None])
                ok = e >= 0
                r = rng.random(n)
                pt, pf = O["tov"][e], O["ft"][e]
                is_tov = ok & (r < pt)
                is_ft = ok & ~is_tov & (r < pt + pf)
                is_sh = ok & ~is_tov & ~is_ft
                if stats:
                    np.add.at(S[off]["tov"], (ar[is_tov], e[is_tov]), 1)
                    stl_ev = is_tov & (rng.random(n) < STEAL_SHARE)
                    s_ = pick(fd * Dd["stl"][None, :] * stl_ev[:, None])
                    m_ = s_ >= 0
                    np.add.at(S[de]["stl"], (ar[m_], s_[m_]), 1)
                fr_ = rng.random((n, 2)) < O["ftp"][e][:, None]
                ftm = fr_.sum(1) * is_ft
                ft_miss = is_ft & ~fr_[:, 1]                              # a missed last free throw is a live rebound
                score[off] += ftm
                if stats:
                    np.add.at(S[off]["pts"], (ar[is_ft], e[is_ft]), ftm[is_ft])
                zc = (np.cumsum(O["zs"][e], 1) < rng.random(n)[:, None]).sum(1).clip(0, 4)
                pm = np.clip(O["zfg"][e, zc] * Dd["dadj"][zc] * kap[off], .02, .95)
                made = is_sh & (rng.random(n) < pm)
                pts = PTS_Z[zc] * made
                score[off] += pts
                if stats:
                    np.add.at(S[off]["pts"], (ar[made], e[made]), pts[made])
                    three = made & (zc >= 3)
                    np.add.at(S[off]["fg3m"], (ar[three], e[three]), 1)
                    asd = made & (rng.random(n) < AST_Z[zc])
                    wa = fo * O["ast"][None, :] * asd[:, None]
                    wa[ar, np.maximum(e, 0)] = 0
                    q = pick(wa); mq = q >= 0
                    np.add.at(S[off]["ast"], (ar[mq], q[mq]), 1)
                miss = (is_sh & ~made) | ft_miss
                orb = miss & (rng.random(n) < np.where(ft_miss, .14, OREB_LG))
                if stats:
                    cr = rng.random(n) >= TEAM_REB                          # credited to a player (not a team rebound)
                    ro = pick(fo * O["oreb"][None, :] * (orb & cr)[:, None]); mo = ro >= 0
                    np.add.at(S[off]["reb"], (ar[mo], ro[mo]), 1)
                    drb = miss & ~orb
                    rd = pick(fd * Dd["dreb"][None, :] * (drb & cr)[:, None]); md = rd >= 0
                    np.add.at(S[de]["reb"], (ar[md], rd[md]), 1)
                live = orb
        if not stats:
            return score
        return S, score, T


def backtest(n_games=400):
    fr = M.Frames()
    sim = Sim(fr)
    p = sim.p
    test = p[(p.season == 2026) & (p.played == 1) & p.vegas_pts.notna()].copy()
    test = test[test.game_id.isin(fr.games[fr.games.game_type == 2].game_id)]
    gids = sorted(test.game_id.unique())[:n_games]
    test = test[test.game_id.isin(gids)]
    test["e_min"] = M.team_minutes(test)
    base, _ = M.project(fr, test, mode="vegas", n=500, seed=1)
    base.index = test.index
    out = []
    for i, g in enumerate(gids):
        x = test[test.game_id == g]
        tgt = x.groupby("team").vegas_pts.first().to_dict()
        r = sim.game(x, n=800, seed=i, target=tgt)
        if r is None:
            continue
        S, score, T = r
        for t in T:
            for j, ix in enumerate(T[t]["idx"]):
                out.append(dict(ix=ix, **{f"sim_{k}": S[t][k][:, j] for k in ("pts", "reb", "ast", "fg3m", "min")}))
        if i % 50 == 0:
            print(f"  {i}/{len(gids)} games", flush=True)
    R = pd.DataFrame(out).set_index("ix")
    X = test.join(R, how="inner")
    print(f"\n{len(X)} player-games, {X.game_id.nunique()} games (2026 regular season)")
    print(f"{'stat':6} {'actual':>7} {'box':>7} {'poss':>7} {'MAE box':>8} {'MAE poss':>9} {'logsc box':>10} {'logsc poss':>11}")
    from scipy.stats import nbinom
    for c in ("min", "pts", "reb", "ast", "fg3m"):
        y = X[c].to_numpy(float)
        sm = np.stack(X[f"sim_{c}"].to_numpy())
        mu_b = base.loc[X.index, "mu_" + c if c != "min" else "mu_min"].to_numpy()
        mu_s = sm.mean(1)
        ls_s = np.log(np.clip([(np.round(s_) == round(v)).mean() for s_, v in zip(sm, y)], 1e-3, 1)).mean() if c != "min" else float("nan")
        # box model's distribution: negative binomial with its dispersion (as in model.project)
        if c != "min":
            k = M.DISP[c] * np.clip(mu_b / M.DISP_REF.get(c, 1e9), 1, None) ** M.DISP_GAMMA
            ls_b = np.log(np.clip(nbinom.pmf(np.round(y), k, k / (k + mu_b)), 1e-3, 1)).mean()
        else:
            ls_b = float("nan")
        print(f"{c:6} {y.mean():7.2f} {mu_b.mean():7.2f} {mu_s.mean():7.2f} {np.abs(y - mu_b).mean():8.3f} {np.abs(y - mu_s).mean():9.3f} {ls_b:10.4f} {ls_s:11.4f}")
    # game-level correlation the box model lacks: starters' minutes in blowouts
    X["margin"] = X.groupby("game_id").apply(lambda g: 0).reindex(X.game_id).to_numpy() if False else 0
    X.to_pickle(os.path.join(os.environ.get("TEMP", "."), "possim_bt.pkl"))


if __name__ == "__main__":
    if sys.argv[1:2] == ["backtest"]:
        backtest(int(sys.argv[2]) if len(sys.argv) > 2 else 400)
