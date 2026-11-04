"""NBA rebounds / assists built in steps + a refit spread for points, rebounds and assists (owner 2026-11-03, the threes method;
research/nba_tails*.py, nba_steps.py, kladder_market.py). Step mean: minutes -> team total tonight (the team's and the opponent-
allowed totals, short + long memory, implied points; Poisson) -> his share (per-minute rate, short + long memory, x minutes, normalized
in the team). model.project uses SCALE x mean_new^W x its own mean^(1 - W) (rebounds W .75, assists .5; points keeps its own mean),
drawn with a negative binomial spread of size K0 x mean^G (fit on games as they happened). Walk-forward 2025 -> 2026, 2025-26 -> 2027:
better than the live sims in all 4 held-out halves on the rungs above each player's median (x1e-4 a rung: reb -22 / -26 / -32 / -47,
ast -11 / -25 / -36 / -17, pts -15 / -12 / -29 / -31); the live tails ran hot (reb 13+ 0.81 actual / projected, 16+ 0.70; pts 1-5%
rungs 0.51). vs Kalshi (Nov 2025 - now): the mix with the price beat the price (reb z -2.5, ast z -2.8, pts -0.5); no bet rule.
Parameters: steps_params.json (`python nba/steps.py fit` on the PC; fit 2024-26, as the 2027 test fold)."""
import json
import os

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
PARAMS = os.path.join(HERE, "steps_params.json")
STATS = ("reb", "ast")
LG = {"reb": 44.0, "ast": 26.0}


def feats(fr):
    """Per player-game state before each game (incl. tonight's rows), cached on the Frames."""
    if getattr(fr, "_st", None) is not None:
        return fr._st
    import model as M
    p = fr.p
    pl = p.played.fillna(0)
    q = p[["player_id", "season"]].copy()
    q["min_y"] = p["min"].where(pl == 1, 0.0).fillna(0.0)
    out = pd.DataFrame(index=p.index)
    for c in STATS:
        q[c + "_y"] = p[c].where(pl == 1, 0.0).fillna(0.0)
        for hl, nm in ((15, "s"), (60, "l")):
            r = M.decayed(q, "player_id", [c + "_y", "min_y"], hl, .7)
            out[f"{c}_a{nm}"], out[f"{c}_m{nm}"] = r[c + "_y"], r.min_y
    # team totals for / allowed: every team-game incl. tonight's (no box yet -> weight 0, so it reads the state before it)
    g = fr.games
    tg = pd.concat([g[["game_id", "season", "date", "home", "away"]].rename(columns={"home": "team", "away": "opp"}),
                    g[["game_id", "season", "date", "away", "home"]].rename(columns={"away": "team", "home": "opp"})])
    s = p[pl == 1].assign(**{c: q[c + "_y"] for c in STATS}).groupby(["game_id", "team"])[list(STATS)].sum().reset_index()
    t = tg.merge(s, on=["game_id", "team"], how="left").sort_values(["date", "game_id"]).reset_index(drop=True)
    t["one"] = t[STATS[0]].notna().astype(float); t[list(STATS)] = t[list(STATS)].fillna(0.0)
    keep = []
    for c in STATS:
        for hl, nm in ((10, "s"), (30, "l")):
            o = M.decayed(t, "team", [c, "one"], hl, .5); d = M.decayed(t, "opp", [c, "one"], hl, .5)
            t[f"{c}_off{nm}"] = (o[c] + 3 * LG[c]) / (o.one + 3); t[f"{c}_dfa{nm}"] = (d[c] + 3 * LG[c]) / (d.one + 3)
            keep += [f"{c}_off{nm}", f"{c}_dfa{nm}"]
    k = p[["game_id", "team"]].merge(t[["game_id", "team"] + keep], on=["game_id", "team"], how="left")
    for c in keep:
        out[c] = k[c].to_numpy()
    fr._st = out
    return out


def _team(x, c, b, pts):
    return np.exp(b["const"] + b["os"] * np.log(x[f"{c}_offs"]) + b["ol"] * np.log(x[f"{c}_offl"]) + b["ds"] * np.log(x[f"{c}_dfas"])
                  + b["dl"] * np.log(x[f"{c}_dfal"]) + b["tot"] * np.log(pts.clip(90, 140) / 113))


def mean_new(x, P, c, pts, w_play=None):
    """x: rows with model columns (e_min already team-normalized, pg) + feats(); pts: team points target."""
    S = P[c]; pr = x.pg.map(S["lg_rate"]).fillna(np.mean(list(S["lg_rate"].values()))); kk = 60
    s = (x[f"{c}_as"] + kk * pr) / (x[f"{c}_ms"] + kk); l_ = (x[f"{c}_al"] + kk * pr) / (x[f"{c}_ml"] + kk)
    w = S["w_short"]
    v = np.exp(w * np.log(s.clip(1e-4)) + (1 - w) * np.log(l_.clip(1e-4))) * x.e_min
    wp = pd.Series(1.0 if w_play is None else w_play, index=x.index)
    share = v / (v * wp).groupby([x.game_id, x.team]).transform("sum").clip(lower=1e-6)
    return (_team(x, c, S["team"], pts) * share).to_numpy()


def gamma_size(P, c, mu, v_min):
    """Gamma size for a Poisson-gamma draw that already carries minutes noise of variance v_min (relative): the marginal then
    matches the fitted negative binomial (size K0 x mean^G), and a player's points / rebounds / assists keep moving together."""
    k = P["nb"][c]["K0"] * np.maximum(mu, .01) ** P["nb"][c]["G"]
    inv = (1 / k - v_min) / (1 + v_min)
    return 1 / np.maximum(inv, 1e-4)


def load():
    return json.load(open(PARAMS)) if os.path.exists(PARAMS) else None


def fit():
    """Refit the step means on the 2024-26 seasons (players who played, Vegas lines) -> steps_params.json. W / SCALE / K0 / G come
    from research/nba_tails4.py's 2027 fold (means and spreads fit on the 2025-26 out-of-sample predictions)."""
    import sys
    sys.path.insert(0, os.path.join(HERE, "..", "research"))
    import model as M
    from threes_model import irls
    fr = M.Frames()
    x = fr.p.join(feats(fr))
    tr = x[x.season.between(2024, 2026) & (x.played == 1) & x.vegas_pts.notna()].copy()
    tr["e_min"] = M.team_minutes(tr)
    P = {}
    for c in STATS:
        tr["t_y"] = tr.groupby(["game_id", "team"])[c].transform("sum")
        T = tr.drop_duplicates(["game_id", "team"])
        X = pd.DataFrame(dict(os=np.log(T[f"{c}_offs"]), ol=np.log(T[f"{c}_offl"]), ds=np.log(T[f"{c}_dfas"]), dl=np.log(T[f"{c}_dfal"]),
                              tot=np.log(T.vegas_pts.clip(90, 140) / 113)))
        b, _ = irls(X, T.t_y.to_numpy(float))
        lg = (tr.groupby("pg")[c].sum() / tr.groupby("pg")["min"].sum()).to_dict()
        S = dict(team=b.to_dict(), lg_rate=lg)
        best = None
        for w in (0., .2, .4, .6, .8, 1.):
            S["w_short"] = w
            m = mean_new(tr, {c: S}, c, tr.vegas_pts)
            dev = (m - tr[c] * np.log(np.clip(m, 1e-4, None))).mean()
            best = min(best or (dev, w), (dev, w))
        S["w_short"] = best[1]
        P[c] = S
        print(c, json.dumps(S["team"]), "short weight", S["w_short"])
    P["W"] = {"reb": .75, "ast": .5, "pts": 0.0}
    P["SCALE"] = {"reb": .997, "ast": 1.000, "pts": 1.002}
    P["nb"] = {"reb": dict(K0=1.44, G=1.27), "ast": dict(K0=3.15, G=1.13), "pts": dict(K0=.14, G=1.40)}
    P["fit"] = "2024-26 seasons, 2026-11-03"
    json.dump(P, open(PARAMS, "w"), indent=1)


if __name__ == "__main__":
    fit()
