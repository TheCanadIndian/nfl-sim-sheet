"""NBA threes, second opinion built in steps (owner 2026-11-02, after KSplit's thread; research/threes_model.py + threes_variants.py):
minutes -> team 3PA tonight (team's and the opponent-allowed 3PA, implied points) -> his share of it -> 3P% pulled to his history (long 3P%,
FT%, last-20 form, opponent 3P% allowed) = mean_new. model.project uses sqrt(mean_new x its own mean) x SCALE, drawn from a negative
binomial with size K0 x mean^G. Walk-forward 2025 / 2026 / 2027: better than the old fg3m in all 4 held-out halves on deviance and nearly
every rung 1+..8+; the old tails ran hot (actual / projected at 5+..8+ 0.87 / 0.70 / 0.58 / 0.50; blend 1.03 / 0.94 / 0.92 / 0.98).
Parameters: threes_params.json (`python nba/threes.py fit` on the PC; fit 2024-26, as the 2027 test fold)."""
import json
import os

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
PARAMS = os.path.join(HERE, "threes_params.json")
logit = lambda p: np.log(p / (1 - p))
sig = lambda x: 1 / (1 + np.exp(-x))


def feats(fr):
    """Per player-game state before each game (incl. tonight's rows), cached on the Frames."""
    if getattr(fr, "_th", None) is not None:
        return fr._th
    import model as M
    p = fr.p
    pl = p.played.fillna(0)
    q = p[["player_id", "season"]].copy()
    for c in ("fg3a", "fg3m", "ftm", "fta", "min"):
        q[c] = p[c].where(pl == 1, 0.0).fillna(0.0)
    out = pd.DataFrame(index=p.index)
    for hl, nm in ((15, "s"), (60, "l")):
        r = M.decayed(q, "player_id", ["fg3a", "min"], hl, .7)
        out["a_" + nm], out["m_" + nm] = r.fg3a, r["min"]
    r = M.decayed(q, "player_id", ["fg3m", "fg3a", "ftm", "fta"], 400, 1.0)
    out["s_m"], out["s_a"], out["ft_m"], out["ft_a"] = r.fg3m, r.fg3a, r.ftm, r.fta
    r = M.decayed(q, "player_id", ["fg3m", "fg3a"], 20, 1.0)
    out["h_m"], out["h_a"] = r.fg3m, r.fg3a
    # team 3PA for / allowed: every team-game incl. tonight's (no box yet -> weight 0, so it reads the state before it)
    g = fr.games
    tg = pd.concat([g[["game_id", "season", "date", "home", "away"]].rename(columns={"home": "team", "away": "opp"}),
                    g[["game_id", "season", "date", "away", "home"]].rename(columns={"away": "team", "home": "opp"})])
    s = p[pl == 1].assign(a=q.fg3a, m=q.fg3m).groupby(["game_id", "team"])[["a", "m"]].sum().reset_index()
    t = tg.merge(s, on=["game_id", "team"], how="left").sort_values(["date", "game_id"]).reset_index(drop=True)
    t["one"] = t.a.notna().astype(float); t[["a", "m"]] = t[["a", "m"]].fillna(0.0)
    for hl, nm in ((10, "s"), (30, "l")):
        o = M.decayed(t, "team", ["a", "one"], hl, .5); d = M.decayed(t, "opp", ["a", "one"], hl, .5)
        t["off_" + nm] = (o.a + 3 * 35) / (o.one + 3); t["dfa_" + nm] = (d.a + 3 * 35) / (d.one + 3)
    d = M.decayed(t, "opp", ["a", "m"], 30, .5); t["df_pct"] = (d.m + 100 * .36) / (d.a + 100)
    k = p[["game_id", "team"]].merge(t[["game_id", "team", "off_s", "off_l", "dfa_s", "dfa_l", "df_pct"]], on=["game_id", "team"], how="left")
    for c in ("off_s", "off_l", "dfa_s", "dfa_l", "df_pct"):
        out[c] = k[c].to_numpy()
    fr._th = out
    return out


def _team(x, b, pts):
    lp = np.log(pts.clip(90, 140) / 113)
    return np.exp(b["const"] + b["os"] * np.log(x.off_s) + b["ol"] * np.log(x.off_l) + b["ds"] * np.log(x.dfa_s) + b["dl"] * np.log(x.dfa_l) + b["tot"] * lp)


def _shoot(x, P):
    pos = x.pg.map(P["pos3"]).fillna(.35); k = P["k"]
    ours = ((x.s_m + k * pos) / (x.s_a + k)).clip(.1, .55)
    ft = ((x.ft_m + 40 * .77) / (x.ft_a + 40)).clip(.4, .95)
    hot = ((x.h_m + 60 * ours) / (x.h_a + 60)).clip(.1, .55)
    c = P["shoot"]
    z = c["const"] + c["l3"] * logit(ours) + c["lft"] * logit(ft) + c["hot"] * (logit(hot) - logit(ours)) \
        + c["opp"] * (logit(x.df_pct.clip(.3, .42)) - logit(.36)) + c["home"] * (x.is_home - .5)
    return sig(z)


def mean_new(x, P, pts, w_play=None):
    """x: rows with model columns (e_min already team-normalized, pg, is_home) + feats(); pts: team points target."""
    lg = x.pg.map(P["lg_rate"]).fillna(.1); kk = 60
    s = (x.a_s + kk * lg) / (x.m_s + kk); l = (x.a_l + kk * lg) / (x.m_l + kk)
    w = P["w_short"]
    r = np.exp(w * np.log(s.clip(1e-4)) + (1 - w) * np.log(l.clip(1e-4)))
    v = r * x.e_min
    wp = pd.Series(1.0 if w_play is None else w_play, index=x.index)
    share = v / (v * wp).groupby([x.game_id, x.team]).transform("sum").clip(lower=1e-6)
    return (_team(x, P["team"], pts) * share * _shoot(x, P)).to_numpy()


def load():
    return json.load(open(PARAMS)) if os.path.exists(PARAMS) else None


def fit():
    """Refit on the 2024-26 seasons (players who played, Vegas lines) -> threes_params.json. The blend's SCALE / K0 / G come
    from research/threes_variants.py's 2027 fold (seed + live mean on 2025-26 test predictions)."""
    import sys
    sys.path.insert(0, os.path.join(HERE, "..", "research"))
    import model as M
    import threes_model as R
    fr = M.Frames()
    x = fr.p.join(feats(fr))
    tr = x[x.season.between(2024, 2026) & (x.played == 1) & x.vegas_pts.notna()].copy()
    tr["e_min"] = M.team_minutes(tr)
    tr["fg3a_y"], tr["fg3m_y"], tr["min_y"] = tr.fg3a, tr.fg3m, tr["min"]
    tr["t3a"] = tr.groupby(["game_id", "team"]).fg3a.transform("sum")
    T = tr.drop_duplicates(["game_id", "team"])
    b, _ = R.irls(pd.DataFrame(dict(os=np.log(T.off_s), ol=np.log(T.off_l), ds=np.log(T.dfa_s), dl=np.log(T.dfa_l),
                                     tot=np.log(T.vegas_pts.clip(90, 140) / 113))), T.t3a.to_numpy(float))
    lg = (tr.groupby("pg").fg3a.sum() / tr.groupby("pg")["min"].sum()).to_dict()
    pos3 = (tr.groupby("pg").fg3m.sum() / tr.groupby("pg").fg3a.sum()).to_dict()
    P = dict(team=b.to_dict(), lg_rate=lg, pos3=pos3)
    best = None
    for w in (0., .2, .4, .6, .8, 1.):
        P["w_short"] = w
        P["shoot"] = dict(const=0, l3=1, lft=0, hot=0, opp=0, home=0); P["k"] = 100
        a = mean_new(tr, P, tr.vegas_pts) / _shoot(tr, P).to_numpy()
        dev = (a - tr.fg3a * np.log(np.clip(a, 1e-4, None))).mean()
        best = min(best or (dev, w), (dev, w))
    P["w_short"] = best[1]
    Y = tr[tr.fg3a > 0]; bl = None
    for k in (100, 200, 400, 800):
        c, ll = R.irls(R.shoot_x(Y, pd.Series(pos3), k), Y.fg3m.to_numpy(float), Y.fg3a.to_numpy(float))
        if bl is None or ll > bl[0]:
            bl = (ll, k, c)
    P["k"], P["shoot"] = bl[1], bl[2].to_dict()
    P.update(SCALE=0.985, K0=4.032, G=1.476, fit="2024-26 seasons, 2026-11-03")
    json.dump(P, open(PARAMS, "w"), indent=1)
    print(json.dumps(P, indent=1))


if __name__ == "__main__":
    fit()
