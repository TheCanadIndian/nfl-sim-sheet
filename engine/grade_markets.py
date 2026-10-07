#!/usr/bin/env python3
"""
Grade the prediction markets against our models on the last PREGAME snapshot of every game
(markets/<sport>_<date>.json from markets.py).

    python grade_markets.py [nfl|nhl|nba]   # writes markets/grades.json (shown on /markets.html)

With a sport, only that sport is regraded and the others are kept (each cloud run has only its own database).

For each settled market: did YES happen? Compare
  - accuracy: log-loss and Brier of our chance vs the market's mid price (lower is better)
  - flagged bets: buying the side with a 3%+ edge at the listed price (after fee), 1 contract each:
    how many, how many won, profit per $1 staked
by sport, source and market type.
"""

import glob
import json
import math
import os
import sqlite3
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "markets")
sys.path.insert(0, HERE)
FEE = {"kalshi": 0.07, "polymarket": 0.0}


def nfl_results():
    from boxscore import data as D
    import results as R
    con = D.connect(os.path.join(HERE, "nfl.db"))
    g = pd.read_sql("SELECT game_id, home_team home, away_team away, home_score, away_score FROM games WHERE result IS NOT NULL", con)
    pg = D.player_games(D.load_plays(con), D.load_kneels(con))
    pg["anytime_td"] = ((pg.rec_td + pg.rush_td) > 0).astype(float)
    first = R.load_first_tds(con)
    return dict(games=g.set_index("game_id"), players=pg.set_index(["game_id", "player_id"]), first=first)


def nhl_results():
    con = sqlite3.connect(os.path.join(HERE, "hockey", "nhl.db"))
    g = pd.read_sql("SELECT game_id, home, away, home_score, away_score FROM games WHERE state IN ('OFF','FINAL')", con)
    s = pd.read_sql("SELECT game_id, t, event_id, shooter FROM shots WHERE goal = 1 AND shooter IS NOT NULL", con)
    goals = s.groupby(["game_id", "shooter"]).size()
    first = s.sort_values(["game_id", "t", "event_id"]).drop_duplicates("game_id").set_index("game_id").shooter
    played = pd.read_sql("SELECT DISTINCT game_id, player_id FROM toi", con)
    return dict(games=g.set_index("game_id"), goals=goals, first=first, played=set(zip(played.game_id, played.player_id)))


def nba_results():
    con = sqlite3.connect(os.path.join(HERE, "nba", "nba.db"))
    g = pd.read_sql("SELECT game_id, home, away, home_score, away_score FROM games WHERE state = 'FINAL'", con)
    p = pd.read_sql("SELECT game_id, player_id, min, pts, reb, ast, fg3m, dnp FROM players", con)
    return dict(games=g.set_index("game_id"), players=p.set_index(["game_id", "player_id"]))


def mlb_results():
    con = sqlite3.connect(os.path.join(HERE, "mlb", "mlb.db"))
    hr = pd.read_sql("SELECT game_pk, batter, SUM(hr) hr FROM pa GROUP BY game_pk, batter", con)
    return dict(games=pd.read_sql("SELECT game_pk game_id, home_score, away_score, home, away FROM games", con).set_index("game_id"),
                hr=hr.set_index(["game_pk", "batter"]).hr.to_dict())


def outcome(sport, r, R):
    """1 / 0 if the market's YES happened, None if not settled or void (player didn't play)."""
    G = R["games"]
    gid = r["game"] if sport == "nfl" else int(r["game"])
    if sport == "mlb":
        if gid not in G.index or r.get("pid") is None:
            return None
        k = (gid, int(r["pid"]))
        if k not in R["hr"]:
            return None                                    # didn't bat: void
        return float(R["hr"][k] > (r.get("line") if r.get("line") is not None else 0.5))
    if gid not in G.index or pd.isna(G.loc[gid, "home_score"]):
        return None
    hs, as_ = float(G.loc[gid, "home_score"]), float(G.loc[gid, "away_score"])
    k, line = r["kind"], r.get("line")
    if k == "win":
        if r["source"] == "kalshi":
            team = r["ticker"].rsplit("-", 1)[-1]
        else:
            team = r["title"].split("(")[-1].rstrip(")")
        import markets as MK
        team = MK.code(sport, team)
        home = team == G.loc[gid, "home"]
        return float((hs > as_) if home else (as_ > hs))
    if k == "total":
        over = not str(r.get("outcome", "Over")).lower().startswith("under")
        return float((hs + as_ > line) == over)
    if k == "spread":
        side = r.get("team_side")                          # which team the YES outcome is (home / away)
        if side is None:
            return None
        margin = (hs - as_) if side == "home" else (as_ - hs)
        return float(margin + line > 0)
    pid = r.get("pid")
    if pid is None:
        return None
    if sport == "nfl":
        if k == "first_td":
            f = R["first"].get(gid)
            return None if f is None else float(f.get("pid") == pid)
        key = (gid, pid)
        if key not in R["players"].index:
            return None                                   # didn't play: void
        v = R["players"].loc[key]
        v = v.iloc[0] if isinstance(v, pd.DataFrame) else v
        if k == "anytime_td":
            return float(v.anytime_td)
        return float(v[k] > line)
    if sport == "nhl":
        pid = int(pid)
        if (gid, pid) not in R["played"]:
            return None
        if k == "first_goal":
            return float(R["first"].get(gid) == pid)
        return float(R["goals"].get((gid, pid), 0) > (line if line is not None else 0.5))
    key = (gid, int(pid))
    if key not in R["players"].index:
        return None
    v = R["players"].loc[key]
    v = v.iloc[0] if isinstance(v, pd.DataFrame) else v
    if v.dnp == 1 or not v["min"] > 0:
        return None
    return float(v[k] > line)


def mid(r):
    b, a = r.get("yes_bid"), r.get("yes_ask")
    if b is not None and a is not None and 0 < a <= 1 and b > 0:
        return (a + b) / 2
    return a if a is not None and 0 < a < 1 else r.get("last")


def ll(y, p):
    p = np.clip(np.asarray(p, float), 0.01, 0.99)
    return float(-(y * np.log(p) + (1 - y) * np.log(1 - p)).mean())


def main(only=None):
    loaders = {"nfl": nfl_results, "nhl": nhl_results, "nba": nba_results, "mlb": mlb_results}
    gp = os.path.join(OUT, "grades.json")
    old = json.load(open(gp)) if os.path.exists(gp) else {}
    rows, done = [], set()
    for sport in ([only] if only else ("nfl", "nhl", "nba", "mlb")):
        files = sorted(glob.glob(os.path.join(OUT, f"{sport}_*.json")))
        if not files:
            continue
        try:
            R = loaders[sport]()
        except Exception as e:                         # a missing database must not stop the others
            print(f"{sport}: results unavailable ({type(e).__name__})")
            continue
        done.add(sport)
        for f in files:
            for r in json.load(open(f)).get("markets", []):
                p = r["ours"].get("vegas", r["ours"].get("model"))
                m = mid(r)
                if p is None or m is None:
                    continue
                y = outcome(sport, r, R)
                if y is None:
                    continue
                fee = FEE[r["source"]]
                bet, pnl, cost = None, 0.0, 0.0
                if (r.get("edge_yes") or -1) >= .03 and r.get("yes_ask"):
                    a = r["yes_ask"]; bet, cost = "YES", a + fee * a * (1 - a); pnl = y - cost
                elif (r.get("edge_no") or -1) >= .03 and r.get("no_ask"):
                    a = r["no_ask"]; bet, cost = "NO", a + fee * a * (1 - a); pnl = (1 - y) - cost
                fl = (r.get("flow") or {}).get("imb")
                ey = r.get("edge_yes")
                tag = None
                if fl is not None and ey is not None and ey >= .03:
                    tag = "value + NO flow" if fl <= -.2 else "value, YES-heavy flow"
                crowd = fl is not None and fl >= .6 and r["kind"] in ("anytime_td", "goal")
                ya = r.get("yes_ask"); ycost = (ya + fee * ya * (1 - ya)) if ya else None
                rows.append(dict(flowtag=tag, crowd=crowd, ycost=ycost, sport=sport, source=r["source"], kind=r["kind"], longshot=bool(r.get("longshot")), y=y, p=p, m=m,
                                 bet=bet, pnl=pnl, cost=cost, title=r["title"], game=f"{r['away']} @ {r['home']}"))
    keep = [g for g in old.get("groups", []) if g["sport"] not in done]          # sports not regraded this run
    out = dict(groups=keep, n=len(rows) + sum(g["n"] for g in keep if g["kind"] == "all"))
    if rows:
        d = pd.DataFrame(rows)
        for keys, x in [((s, src, "all"), x) for (s, src), x in d.groupby(["sport", "source"])] + \
                       [((s, src, k), x) for (s, src, k), x in d.groupby(["sport", "source", "kind"])]:
            b = x[x.bet.notna()]
            out["groups"].append(dict(sport=keys[0], source=keys[1], kind=keys[2], n=int(len(x)), hit=round(float(x.y.mean()), 3),
                                      model_ll=round(ll(x.y, x.p), 4), market_ll=round(ll(x.y, x.m), 4),
                                      model_brier=round(float(((x.p - x.y) ** 2).mean()), 4), market_brier=round(float(((x.m - x.y) ** 2).mean()), 4),
                                      bets=int(len(b)), bets_won=int((b.pnl > 0).sum()),
                                      roi=None if b.empty else round(float(b.pnl.sum() / b.cost.sum()), 3)))
        fr = {}
        for (sp_, lab), x in [((s_, t_), x) for (s_, t_), x in d[d.flowtag.notna() & d.ycost.notna()].groupby(["sport", "flowtag"])] +                 [((s_, "crowded YES (buying YES)"), x) for s_, x in d[d.crowd & d.ycost.notna()].groupby("sport")]:
            fr.setdefault(sp_, {})[lab] = dict(bets=int(len(x)), won=int(x.y.sum()), roi=round(float((x.y - x.ycost).sum() / x.ycost.sum()), 3))
        keep_fr = {k: v for k, v in old.get("flow", {}).items() if k not in done}
        out["flow"] = {**keep_fr, **fr}
        out["recent"] = [r for r in old.get("recent", []) if r["sport"] not in done] + d[d.bet.notna()].tail(40).to_dict("records")
    json.dump(out, open(gp, "w"), separators=(",", ":"))
    print(f"graded {out['n']} settled markets -> markets/grades.json")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
