#!/usr/bin/env python3
"""
Profit page: is every model making money? One record of what the site recommends, graded at the prices shown.

    python profit.py rows <sport>   # in each sport's run: grade that sport's prediction-market picks under the site's
                                    # own rules -> markets/profit_<sport>.json
    python profit.py tags           # every 15 min (markets job): log each Value tag's first price and its last price
                                    # before the game, then settle tags from box scores -> markets/profit_tags.json
    python profit.py page           # build /profit.html from the above + the parlay records (parlays.py)

Sources and rules (what the site actually tells you to bet):
  - Prediction markets (Kalshi with its fee, Polymarket), last pregame quote: chance = half ours, half the market mid.
      NFL / NBA props: the side with a 5%+ edge.  NFL / NHL / NBA game lines (win, total, spread): 3%+.
      NHL anytime goals: our top 3 per game, any edge (YES).  MLB home runs: the VALUE rule, ours 6%+ over the ask.
  - Value tags (NFL / NHL, at the tagged book): also closing-line value (CLV) = the book's last price before the game
    vs the price when the tag first appeared; beating the close is the fastest sign of a real edge.
  - Parlays: every card's record from parlays.py.
Flat 1-unit stakes. Verdict per row: Proven (95% range of the return above 0, 30+ bets), Losing (95% range below 0),
Promising (beats the closing line, z >= 2), Too early (< 30 bets), otherwise Unproven.
"""
import datetime as dt
import glob
import json
import math
import os
import sys
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
OUT = os.path.join(HERE, "markets")
ET = ZoneInfo("America/New_York")
FEE = {"kalshi": .07, "polymarket": 0.0}
PROP_KINDS = {"anytime_td", "rec", "rec_yds", "rush_yds", "pass_yds", "pts", "reb", "ast", "fg3m"}
LINE_KINDS = {"win", "total", "spread"}
LAB = {"rec yds": "rec_yds", "rush yds": "rush_yds", "pass yds": "pass_yds", "receptions": "rec", "points": "pts", "rebounds": "reb",
       "assists": "ast", "threes": "fg3m"}


def dec(o):
    o = float(o)
    return 1 + (o / 100 if o > 0 else 100 / -o)


# ---------------------------------------------------------------- prediction-market picks (each sport's run)
def _soft(week):
    """{(game, pid)} for players whose opponent was top 8 most generous to their position, as frozen before that week."""
    import pandas as pd
    for stem in (f"projections/weeks/2026_wk{week:02d}", f"projections/2026_wk{week:02d}"):
        if os.path.exists(stem + "_players.csv") and os.path.exists(stem + "_matchups.json"):
            break
    else:
        return set()
    M = json.load(open(stem + "_matchups.json"))
    soft = {pos: {r["team"] for r in t.get("rows", []) if r.get("rank", 99) <= 8} for pos, t in M.get("dvp", {}).items()}
    P = pd.read_csv(stem + "_players.csv", usecols=["game_id", "player_id", "pos", "opp"]).drop_duplicates()
    return {(g, str(p)) for g, p, pos, o in zip(P.game_id, P.player_id, P.pos, P.opp) if o in soft.get(pos, set())}


def ladder_rows(date, mk, R, GM):
    """Ladder candidates (experimental): YES on Kalshi rungs 2+ above the ~50/50 rung for players facing a soft defense."""
    by = {}
    for r in mk:
        if r.get("source") == "kalshi" and r.get("kind") in ("rec", "rec_yds", "rush_yds", "pass_yds") and r.get("line") is not None                 and r.get("yes_ask") and r.get("yes_bid") is not None:
            by.setdefault((r["game"], str(r.get("pid")), r["kind"]), {})[float(r["line"])] = r      # last quote wins
    soft, out = {}, []
    for (g, pid, k), L in by.items():
        wk = int(str(g)[5:7]) if str(g)[5:7].isdigit() else None
        if wk is None:
            continue
        if wk not in soft:
            soft[wk] = _soft(wk)
        if (g, pid) not in soft[wk]:
            continue
        rs = [L[x] for x in sorted(L)]
        mid = lambda r: (r["yes_ask"] + r["yes_bid"]) / 2
        i0 = min(range(len(rs)), key=lambda i: abs(mid(rs[i]) - .5))
        for r in rs[i0 + 2:]:
            y = GM.outcome("nfl", r, R)
            if y is None:
                continue
            ya = r["yes_ask"]; cy = ya + FEE["kalshi"] * ya * (1 - ya)
            po = (r.get("ours") or {}).get("vegas")                      # our chance: the claimed edge is po / cost - 1
            out.append(dict(date=date, sport="nfl", cat="NFL ladder candidates (experimental)", src="kalshi", kind=k, title=r.get("title"),
                            bet="YES", cost=round(cy, 4), q=round(mid(r), 4), p=None if po is None else round(po, 4), won=int(y), pnl=round(y / cy - 1, 4)))
    return out


def nba_ladder_rows(date, mk, R, GM):
    """NBA ladders (experimental), YES on Kalshi pts/reb/ast/3pm rungs 2+ above the ~50/50 rung, two versions:
    'candidates' = his stat faced a soft matchup (dvp >= 1.07 for his position) in that night's frozen page data;
    'edge' (what the page shows) = a 20+ minute player and our chance beats the price with the fee by 5%+."""
    f = os.path.join(HERE, "nba", "projections", f"{date}.json")
    if not os.path.exists(f):
        return []
    P = json.load(open(f, encoding="utf-8"))
    dv = {(r["team"], r["pg"]): r for r in P.get("dvp", [])}
    soft = {}
    for p in P.get("players", []):
        mins = ((p.get("s") or {}).get("vegas") or {}).get("min")
        if not mins or mins[3] < 20:
            continue
        r = dv.get((p["o"], p.get("pg")))
        for st in ("pts", "reb", "ast", "fg3m"):
            if r and (r.get(st) or 0) >= 1.07:
                soft[(str(p["g"]), str(p["id"]), st)] = True
    by = {}
    for r in mk:
        if r.get("source") == "kalshi" and r.get("kind") in ("pts", "reb", "ast", "fg3m") and r.get("line") is not None \
                and r.get("yes_ask") and r.get("yes_bid") is not None:
            by.setdefault((str(r["game"]), str(r.get("pid")), r["kind"]), {})[float(r["line"])] = r
    play = {(str(p["g"]), str(p["id"])) for p in P.get("players", [])
            if (((p.get("s") or {}).get("vegas") or {}).get("min") or [0] * 4)[3] >= 20}
    out = []
    for key, L in by.items():
        rs = [L[x] for x in sorted(L) if L[x]["yes_ask"] - L[x]["yes_bid"] <= .10]       # wide quotes left out, as on the page
        if not rs:
            continue
        mid = lambda r: (r["yes_ask"] + r["yes_bid"]) / 2
        i0 = min(range(len(rs)), key=lambda i: abs(mid(rs[i]) - .5))
        for r in rs[i0 + 2:]:
            if mid(r) >= mid(rs[i0]):
                continue
            ya = r["yes_ask"]; cy = ya + FEE["kalshi"] * ya * (1 - ya)
            p = (r.get("ours") or {}).get("vegas")
            cats = (["NBA ladder candidates (experimental)"] if key in soft else []) + \
                   (["NBA ladder edge (experimental)"] if key[:2] in play and p is not None and p / cy - 1 >= .05 else [])
            if not cats:
                continue
            y = GM.outcome("nba", r, R)
            if y is None:
                continue
            for cat in cats:
                out.append(dict(date=date, sport="nba", cat=cat, src="kalshi", kind=r["kind"], title=r.get("title"),
                                bet="YES", cost=round(cy, 4), q=round(mid(r), 4), p=None if p is None else round(p, 4), won=int(y), pnl=round(y / cy - 1, 4)))
    return out


import re as _re
import unicodedata as _ud


def _norm(s):
    """Same as sharp.jsnorm / mkt.js norm()."""
    s = _ud.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().lower()
    s = _re.sub(r"[^a-z ]", "", s)
    s = _re.sub(r"\b(jr|sr|ii|iii|iv)\b", "", s)
    return _re.sub(r"\s+", " ", s).strip()


def pin_close(sport):
    """Pinnacle's last logged price before each game: (props {gid: props}, lines {gid: entry}), from
    markets/pin_<sport>_history.json (player props) and markets/pin_lines_<sport>.json (game lines)."""
    def last(f):
        try:
            log = json.load(open(os.path.join(OUT, f)))
        except (OSError, ValueError):
            return {}
        out = {}
        ts = lambda x: pd.Timestamp(x).tz_convert("UTC") if pd.Timestamp(x).tzinfo else pd.Timestamp(x).tz_localize("UTC")
        for e in log:
            f_ = ts(e["fetched"])
            if e.get("start") and f_ > ts(e["start"]):
                continue
            g = str(e["gid"])
            if g not in out or f_ > out[g]["_t"]:
                out[g] = dict(e, _t=f_)
        return out
    return last(f"pin_{sport}_history.json"), last(f"pin_lines_{sport}.json")


def _nv2(a, b):
    """Two-way multiplicative de-vig of American odds a / b: the a side's fair chance."""
    if a is None or b is None:
        return None
    x, y = 1 / dec(a), 1 / dec(b)
    return x / (x + y)


PIN_PROP = {"rec": "rec", "rec_yds": "rec_yds", "rush_yds": "rush_yds", "pass_yds": "pass_yds", "anytime_td": "td"}


def close_fair(sport, r, PP, PL):
    """Pinnacle's fair (no-vig) closing chance that the YES side of market row r wins, or None if it can't be matched
    exactly (same player and line). Anytime TD is one-way: power de-vig with k = 1.04."""
    gid, k, line = str(r["game"]), r.get("kind"), r.get("line")
    name = _norm(str(r.get("title") or "").split(":")[0])
    if sport == "nhl" and k == "goal" and (line or .5) < 1:
        x = ((PP.get(gid) or {}).get("props") or {}).get(name)
        return _nv2(x[0], x[1]) if x else None
    if sport == "nfl" and k in PIN_PROP:
        x = (((PP.get(gid) or {}).get("props") or {}).get(name) or {}).get(PIN_PROP[k])
        if not x:
            return None
        if k == "anytime_td":
            return min(.99, (1 / dec(x[1])) ** 1.04) if x[1] is not None else None
        return _nv2(x[1], x[2]) if line is not None and abs(float(x[0]) - float(line)) < 1e-9 else None
    L = PL.get(gid)
    if not L:
        return None
    if k == "win":
        team = str(r.get("ticker") or "").rsplit("-", 1)[-1]
        side = "home" if team == r.get("home") else "away" if team == r.get("away") else None
        m = L.get("ml") or {}
        return _nv2(m.get(side), m.get("away" if side == "home" else "home")) if side else None
    if k == "total" and line is not None and "over" in str(r.get("title") or "").lower():
        t = (L.get("total") or {}).get(f"{float(line):g}") or {}
        return _nv2(t.get("over"), t.get("under"))
    return None


# Weight on ours when the market is Pinnacle's no-vig (research/blend_weights.py; same table as mkt.js / sharp.py)
WPIN = {"nhl": {"goal": .2}, "nba": {"pts": .05, "reb": .05, "ast": .05, "fg3m": .05},
        "nfl": {"rec": .25, "rec_yds": .25, "rush_yds": .25, "pass_yds": .25, "anytime_td": 0.0}}


CLOSE_MKT = {"nfl": {"rec_yds": "player_reception_yds", "rec": "player_receptions", "rush_yds": "player_rush_yds",
                     "pass_yds": "player_pass_yds"},
             "nhl": {"goal": "player_goal_scorer_anytime"},
             "nba": {"pts": "player_points", "reb": "player_rebounds", "ast": "player_assists", "fg3m": "player_threes"}}


def close_cons(sport, r, CL):
    """Consensus fair chance (YES side) at the close from markets/close_<sport>.json: log-odds mean of the two-way no-vig of
    Pinnacle, Novig and ProphetX at the same player and line. None unless 2+ of them price it."""
    e = CL.get(str(r["game"]))
    mk = CLOSE_MKT.get(sport, {}).get(r.get("kind"))
    if not e or not mk or not e.get("props"):
        return None
    name, line = _norm(str(r.get("title") or "").split(":")[0]), float(r.get("line") if r.get("line") is not None else .5)
    lg = []
    for bk in ("pinnacle", "novig", "prophetx"):
        s_ = {}
        for nm, side, pt, price in (e["props"].get(mk) or {}).get(bk, []):
            if _norm(nm) == name and price is not None and abs(float(pt if pt is not None else .5) - line) < 1e-9:
                s_[(side or "").lower()] = 1 / dec(price)
        a_, b_ = s_.get("over", s_.get("yes")), s_.get("under", s_.get("no"))
        if a_ and b_:
            q = a_ / (a_ + b_); lg.append(math.log(q / (1 - q)))
    return 1 / (1 + math.exp(-sum(lg) / len(lg))) if len(lg) >= 2 else None


def experimental(date, sport, src, k, r, fy, cy, cn, R, GM):
    """Tracked ideas from the 2026-10-18 research, graded on the same Kalshi quotes against the sharp closing line:
    - NFL anytime TD: Kalshi YES when its price (fee in) is 5%+ under the sharp fair chance (historical: 18 of 85 props, +40%).
    - NFL yardage / catches: NO when both models sit 5+ pts under the sharp line (2025: unders beat the market by 6.4 pts,
      z 2.4, n 348; the NBA showed the opposite)."""
    if sport != "nfl" or fy is None:
        return []
    o = r.get("ours") or {}
    bet = cat = None
    if k == "anytime_td" and fy / cy - 1 >= .05:
        bet, cost, fair, cat = "YES", cy, fy, "NFL anytime TD: Kalshi under the sharp line (experimental)"
    elif k in ("rec", "rec_yds", "rush_yds", "pass_yds") and cn and o.get("vegas") is not None and o.get("blind") is not None \
            and o["vegas"] <= fy - .05 and o["blind"] <= fy - .05:
        bet, cost, fair, cat = "NO", cn, 1 - fy, "NFL props: both models 5+ pts under the sharp line (experimental)"
    if not bet:
        return []
    y = GM.outcome(sport, r, R)
    if y is None:
        return []
    hit = y if bet == "YES" else 1 - y
    return [dict(date=date, sport=sport, cat=cat, src=src, kind=k, title=r.get("title"), bet=bet, cost=round(cost, 4),
                 q=round(fair, 4), won=int(hit), pnl=round(hit / cost - 1, 4), clv=round(fair - cost, 4))]


def pin_rule(date, sport, cat, src, k, r, p, fy, cy, cn, ya, na, need, sides, R, GM, tag="Pinnacle-anchored"):
    """The Pinnacle-anchored version of a rule, on the same quote: chance = w * ours + (1 - w) * Pinnacle's no-vig (its
    last pregame price), same edge bar. Tracked as '<category> · Pinnacle-anchored' next to the current rule."""
    w = WPIN[sport][k]
    q = w * p + (1 - w) * fy
    bet = "YES" if "YES" in sides and q / cy - 1 > need else "NO" if "NO" in sides and cn and (1 - q) / cn - 1 > need else None
    if not bet:
        return []
    y = GM.outcome(sport, r, R)
    if y is None:
        return []
    hit, cost = (y, cy) if bet == "YES" else (1 - y, cn)
    fair = fy if bet == "YES" else 1 - fy
    return [dict(date=date, sport=sport, cat=f"{cat} · {tag}", src=src, kind=k, title=r.get("title"), bet=bet, cost=round(cost, 4),
                 q=round(q if bet == "YES" else 1 - q, 4), won=int(hit), pnl=round(hit / cost - 1, 4), clv=round(fair - cost, 4))]


def market_rows(sport):
    import grade_markets as GM
    R = {"nfl": GM.nfl_results, "nhl": GM.nhl_results, "nba": GM.nba_results, "mlb": GM.mlb_results}[sport]()
    rows = []
    PP, PL = pin_close(sport)
    try:
        CL = {str(e["gid"]): e for e in json.load(open(os.path.join(OUT, f"close_{sport}.json")))}
    except (OSError, ValueError):
        CL = {}
    files = sorted(glob.glob(os.path.join(OUT, f"{sport}_20??-??-??.json")))
    # one quote per market: a market can sit in several daily files (Thursday's file carries Sunday's games at Thursday's
    # price); only its last file counts (fix 2026-10-21: NFL had 6,156 markets graded 2+ times, partly at stale prices)
    last = {}
    for f in files:
        for r in json.load(open(f)).get("markets", []):
            if r.get("ticker"):
                last[r["ticker"]] = f
    for f in files:
        date = os.path.basename(f)[len(sport) + 1:len(sport) + 11]
        mk = [r for r in json.load(open(f)).get("markets", []) if not r.get("ticker") or last.get(r["ticker"]) == f]
        # NHL: our top 3 per game among the listed scorers
        rank = {}
        if sport == "nhl":
            gl = [r for r in mk if r.get("kind") == "goal" and (r.get("line") or .5) < 1 and r.get("source") == "kalshi"]
            by = {}
            for r in gl:
                o = r["ours"].get("model")
                if o is not None:
                    by.setdefault(r["game"], []).append((o, r.get("pid")))
            for g, xs in by.items():
                for i, (_, pid) in enumerate(sorted(xs, key=lambda t: -t[0])):
                    rank[(g, pid)] = i + 1
        for r in mk:
            k, src = r.get("kind"), r.get("source")
            p = r["ours"].get("vegas", r["ours"].get("model"))
            ya, yb, na = r.get("yes_ask"), r.get("yes_bid"), r.get("no_ask")
            if p is None or not ya or src not in FEE:
                continue
            m = (ya + yb) / 2 if yb is not None and yb > 0 else ya
            fee = FEE[src]
            cy = ya + fee * ya * (1 - ya)
            cn = na + fee * na * (1 - na) if na else None
            if sport == "mlb":
                if k != "hr" or (r.get("line") or .5) >= 1:
                    continue
                cat, q, need, sides = "MLB home runs (VALUE)", p, .06, ("YES",)
                # twin (2026-10-21, research/mlb_hr_anchor.py: Aug-Oct 2026 the blend's log-loss .3455 ~ Kalshi .3452 and the
                # 50/50 mix .3447 beat both; VALUE +21% on 98 bets, 25/75-anchored 5%+ edge +16% on 45, neither proven):
                # the market-anchored version, graded on the same quotes so next season decides
                qa = .25 * p + .75 * m
                if qa / cy - 1 >= .05:
                    ya_ = GM.outcome(sport, r, R)
                    if ya_ is not None:
                        rows.append(dict(date=date, sport=sport, cat="MLB home runs (VALUE) · market-anchored", src=src, kind=k, title=r.get("title"),
                                         bet="YES", cost=round(cy, 4), q=round(qa, 4), won=int(ya_), pnl=round(ya_ / cy - 1, 4), clv=None))
            elif sport == "nhl" and k == "goal":
                if (r.get("line") or .5) >= 1 or rank.get((r["game"], r.get("pid")), 99) > 3:
                    continue
                cat, q, need, sides = "NHL anytime goals (top 3)", .5 * p + .5 * m, 0.0, ("YES",)
            elif k in LINE_KINDS:
                cat, q, need, sides = f"{sport.upper()} game lines", .5 * p + .5 * m, .03, ("YES", "NO")
            elif k in PROP_KINDS:
                # props judged like mkt.js pq since 2026-10-21: our weight by Kalshi depth (10% at $1,000+ within 3c, 25% at
                # $100+, else 50%), no edge at a 4c+ spread; the old 50/50 rule is graded beside it
                dep = float((r.get("book") or {}).get("ask_3c") or 0) if r.get("book") else 1000.0
                wk = .10 if dep >= 1000 else .25 if dep >= 100 else .5
                if (ya - (yb or 0)) <= .035 and yb:
                    q50 = .5 * p + .5 * m
                    if q50 / cy - 1 > .05 and (sport != "nfl" or k not in ("rec", "rec_yds", "rush_yds", "pass_yds") or (p >= .5 and abs(p - m) >= .10 and cy >= .15)):
                        yo = GM.outcome(sport, r, R)
                        if yo is not None:
                            rows.append(dict(date=date, sport=sport, cat=f"{sport.upper()} player props · old 50/50", src=src, kind=k, title=r.get("title"),
                                             bet="YES", cost=round(cy, 4), q=round(q50, 4), won=int(yo), pnl=round(yo / cy - 1, 4), clv=None))
                    cat, q, need, sides = f"{sport.upper()} player props", wk * p + (1 - wk) * m, .05, ("YES", "NO")
                else:
                    continue
            else:
                continue                                   # first scorer etc.: not something the site recommends
            fy = close_fair(sport, r, PP, PL)                      # Pinnacle's fair closing chance (YES side)
            if fy is not None and k in WPIN.get(sport, {}):          # the Pinnacle-anchored rule judges every quote on its own
                rows += pin_rule(date, sport, cat, src, k, r, p, fy, cy, cn, ya, na, need, sides, R, GM)
            rows += experimental(date, sport, src, k, r, fy, cy, cn, R, GM)
            fc = close_cons(sport, r, CL) if k in WPIN.get(sport, {}) else None
            if fc is not None:                                         # and its consensus twin (Pinnacle + Novig + ProphetX)
                rows += pin_rule(date, sport, cat, src, k, r, p, fc, cy, cn, ya, na, need, sides, R, GM, tag="consensus-anchored")
                # liquidity consensus (owner 2026-10-21; research/pm_liquidity_consensus.py, Oct 18-21, 1,050 quotes: Kalshi's
                # deep mid -0.0026 log-loss vs the consensus, z -1.5; NBA / NFL for Kalshi, NHL against): the consensus and
                # Kalshi's mid in log-odds, Kalshi weighted 2 x depth / (depth + $200) (depth = dollars to buy within 3c)
                dep = float((r.get("book") or {}).get("ask_3c") or 0)
                if dep > 0 and yb and ya - yb <= .035:
                    wl = 2 * dep / (dep + 200)
                    lo = lambda v: math.log(min(max(v, 1e-4), 1 - 1e-4) / (1 - min(max(v, 1e-4), 1 - 1e-4)))
                    flc = 1 / (1 + math.exp(-(lo(fc) + wl * lo(m)) / (1 + wl)))
                    rows += pin_rule(date, sport, cat, src, k, r, p, flc, cy, cn, ya, na, need, sides, R, GM, tag="liquidity consensus")
            bet = None
            # NFL yardage / catch props: the side our median is on, our number 10+ pts from the mid, a tight 15c+ market
            nflp = sport == "nfl" and k in ("rec", "rec_yds", "rush_yds", "pass_yds")
            ok = lambda ps, c_: not nflp or (ps >= .5 and abs(p - m) >= .10 and (ya - (yb or ya)) <= .035 and c_ >= .15)
            if "YES" in sides and q / cy - 1 > need and ok(p, ya):
                bet, cost, win = "YES", cy, q
            elif "NO" in sides and cn and (1 - q) / cn - 1 > need and ok(1 - p, na):
                bet, cost, win = "NO", cn, 1 - q
            if not bet:
                continue
            y = GM.outcome(sport, r, R)
            if y is None:
                continue
            hit = y if bet == "YES" else 1 - y
            fair = None if fy is None else (fy if bet == "YES" else 1 - fy)
            rows.append(dict(date=date, sport=sport, cat=cat, src=src, kind=k, title=r.get("title"), bet=bet, cost=round(cost, 4),
                             q=round(win, 4), won=int(hit), pnl=round(hit / cost - 1, 4),
                             clv=None if fair is None else round(fair - cost, 4)))
        if sport == "nfl":
            rows += ladder_rows(date, mk, R, GM)
        if sport == "nba":
            rows += nba_ladder_rows(date, mk, R, GM)
    json.dump(dict(sport=sport, rows=rows), open(os.path.join(OUT, f"profit_{sport}.json"), "w"), separators=(",", ":"))
    print(f"profit {sport}: {len(rows)} graded market picks")


# ---------------------------------------------------------------- Value tags: price when tagged, closing price, result
def _starts():
    try:
        s = json.load(open(os.path.join(os.environ.get("SIM_SITE") or os.path.join(HERE, "site"), "markets", "schedule.json")))
        return {str(g["game"]): g["start"] for g in s.get("games", [])}
    except (OSError, ValueError):
        return {}


def _tag_leg(sport, b):
    if sport == "nhl":
        return dict(kind="goal", side="o", line=.5) if b == "goal" else None
    if b == "TD":
        return dict(kind="td", side="o", line=.5)
    if b in ("double-double", "triple-double"):
        return dict(kind="dd" if b == "double-double" else "tdbl", side="o", line=.5)
    parts = b.split(" ", 2)
    if len(parts) == 3 and parts[0] in ("over", "under") and parts[2] in LAB:
        return dict(kind=LAB[parts[2]], side="o" if parts[0] == "over" else "u", line=float(parts[1]))
    return None


# ---------------------------------------------------------------- book-weighted fair (the old Experimental tab)
# Tracked in the background since 2026-10-18 (the tab was removed): fair = the owner's book weights over each book's no-vig
# chance, the book being bet left out (two-way multiplicative; one-way power, each book's exponent from its own two-way
# pairs on the slate). A bet = a main-line prop side at Novig / Kalshi / Hard Rock with a 2%+ edge vs that fair, fair 8%+,
# 2+ weighted books. First sighting's price is the bet; the book's last pregame price is the close. Surfaced on the
# Profit page only once it reads Proven or Promising.
W_PROPS = {"Novig": 7, "Kalshi": 7, "DK": 1.4, "ProphetX": 1.4, "Pinnacle": 1.4, "FD": 1, "Caesars": .5, "Fliff": .5, "bet365": .3, "Hard Rock": .1}
WF_KINDS = {"goal", "td", "rec", "rec_yds", "rush_yds", "pass_yds"}
WF_BOOKS = ("Novig", "Kalshi", "Hard Rock")
WF_CAT = "Book-weighted fair (old Experimental tab)"


def _wfair_bets(PR, every=False):
    """[(gid, name, kind, side, line, book, odds, fair)] for the current props file (every: all sides, edge or not)."""
    P0 = lambda v: v[0] if isinstance(v, list) else v
    mk = []
    for gid, pl in (PR.get("props") or {}).items():
        for nm, kinds in pl.items():
            for k, e in kinds.items():
                if k not in WF_KINDS or not e.get("o"):
                    continue
                mk.append((gid, nm, k, "o", e.get("l"), e.get("o") or {}, e.get("u") or {}))
                if e.get("u"):
                    mk.append((gid, nm, k, "u", e.get("l"), e.get("u") or {}, e.get("o") or {}))
    ks = {}
    for *_, A, B in mk:
        for b, v in A.items():
            if b in B and P0(v) is not None and P0(B[b]) is not None:
                a, c = 1 / dec(P0(v)), 1 / dec(P0(B[b]))
                if a + c > 1:
                    lo, hi = 1.0, 3.0
                    for _ in range(40):
                        kk = (lo + hi) / 2
                        lo, hi = (kk, hi) if a ** kk + c ** kk > 1 else (lo, kk)
                    ks.setdefault(b, []).append((lo + hi) / 2)
    K = {b: float(np.median(v)) for b, v in ks.items()}
    out = []
    for gid, nm, k, sd, line, A, B in mk:
        ch = {}
        for b, v in A.items():
            if P0(v) is None:
                continue
            a = 1 / dec(P0(v))
            ch[b] = a / (a + 1 / dec(P0(B[b]))) if b in B and P0(B[b]) is not None else min(a ** K.get(b, 1.05), .999)
        for b in WF_BOOKS:
            if b not in A or P0(A[b]) is None:
                continue
            ws = [(W_PROPS[x], q) for x, q in ch.items() if x != b and x in W_PROPS]
            if len(ws) < 2:
                continue
            f = sum(w * q for w, q in ws) / sum(w for w, _ in ws)
            if every or (f >= .08 and f * dec(P0(A[b])) - 1 >= .02):
                out.append((gid, nm, k, sd, line, b, P0(A[b]), round(f, 4)))
    return out


def wfair(sport, PR, starts, now, PL):
    fp = os.path.join(OUT, "profit_wfair.json")
    try:
        log = json.load(open(fp))
    except (OSError, ValueError):
        log = {}
    for gid, nm, k, sd, line, b, o, f in _wfair_bets(PR):
        st = starts.get(str(gid))
        if st and PL.when(st) <= now:
            continue
        key = f"{sport}|{gid}|{nm}|{k}|{sd}|{line}|{b}"
        e = log.setdefault(key, dict(sport=sport, gid=gid, name=nm, kind=k, side=sd, line=line, book=b, o=o, f=f, first=now.isoformat(), start=st))
    # every run until the start: the book's latest price and the weighted fair now (the close)
    cur = {f"{sport}|{gid}|{nm}|{k}|{sd}|{line}|{b}": (o, f) for gid, nm, k, sd, line, b, o, f in _wfair_bets(PR, every=True)}
    for key, e in log.items():
        if key in cur and e.get("res") is None and not (e.get("start") and PL.when(e["start"]) <= now):
            e["close"], e["close_f"] = cur[key]
    for key, e in log.items():
        if e["sport"] != sport or e.get("res") is not None or not e.get("start") or PL.when(e["start"]) > now - dt.timedelta(hours=3):
            continue
        try:
            g = PL.nhl_game(e["gid"]) if sport == "nhl" else PL.nfl_game(e["gid"], PL.et_date(e["start"]))
        except Exception:                                                       # noqa: BLE001
            g = None
        if g:
            e["res"] = PL.settle(dict(kind=e["kind"], side=e["side"], line=e["line"] if e["line"] is not None else .5, name=e["name"]), g)
    json.dump(log, open(fp, "w"), separators=(",", ":"))
    done = [e for e in log.values() if e["sport"] == sport and e.get("res") in ("W", "L")]
    print(f"book-weighted fair ({sport}): {sum(1 for e in log.values() if e['sport'] == sport)} logged, {len(done)} settled, "
          f"{sum((dec(e['o']) - 1) if e['res'] == 'W' else -1 for e in done):+.1f}u")


DRY_MIN = 3                          # games without a goal (research/player_trends_nhl.py)
MOVE_SIG = {"line": "line up", "price": "odds drop", "both": "line up + odds drop", "rung": "higher rung outlier"}


def tag_label(e):
    """Profit-page category for a logged tag."""
    if e.get("src") == "move":
        return f"{'Pinnacle' if e.get('msrc') == 'pin' else 'Consensus'} move: {MOVE_SIG.get(e.get('sig'), e.get('sig'))} (experimental)"
    if e.get("src") == "knee":
        return "Ladder knee (experimental)"
    if e.get("src") == "open":
        return "Early opener value (experimental)"
    if e.get("src") == "dry":
        return "Goal drought 3+ games (experimental)"
    if e.get("src") == "dout":
        return "Goal vs a team missing a regular D (experimental)"
    if e.get("src") == "ddtd":
        w = "Double-double" if e.get("bet") == "double-double" else "Triple-double"
        return f"{w}: every Yes at your books (baseline)" if e.get("grp") == "all" else f"{w} edge 5%+ (experimental)"
    return "LC tags (experimental)" if e.get("src") == "lc" else "Off-market prices (experimental)" if e.get("src") == "off" else "Value tags"


def _et_day(ts):
    """The Eastern date of a start time (2026-10-24 fix: UTC starts were cut to their UTC date, so every game from 8 PM
    ET on -- and Sunday / Monday night NFL -- was filed under the next day on the Profit page)."""
    try:
        t = dt.datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return str(ts)[:10]
    from zoneinfo import ZoneInfo
    et = ZoneInfo("America/New_York")
    return (t if t.tzinfo else t.replace(tzinfo=et)).astimezone(et).date().isoformat()


def tags():
    import parlays as PL
    site = os.environ.get("SIM_SITE") or os.path.join(HERE, "site")
    fp = os.path.join(OUT, "profit_tags.json")
    log = json.load(open(fp)) if os.path.exists(fp) else {}
    now = dt.datetime.now(dt.timezone.utc)
    starts = _starts()
    for sport in ("nfl", "nhl", "nba"):
        try:
            FL = json.load(open(os.path.join(site, "markets", f"flags_{sport}.json")))
            PR = json.load(open(os.path.join(site, "markets", f"props_{sport}.json")))
        except (OSError, ValueError):
            continue
        try:
            wfair(sport, PR, starts, now, PL)
        except Exception as e:                                                  # noqa: BLE001
            print(f"book-weighted fair ({sport}): failed ({type(e).__name__}: {e})")
        # every live tag: first sighting keeps its price; later runs refresh the book's latest price (the close)
        for gid, tg in FL.get("tags", {}).items():
            st = starts.get(str(gid))
            if st and PL.when(st) <= now:
                continue
            for nm, v in tg.items():
                if not isinstance(v, dict) or v.get("o") is None:
                    continue
                key = f"{sport}|{gid}|{nm}|{v['b']}"
                e = log.setdefault(key, dict(sport=sport, gid=gid, name=nm, bet=v["b"], book=v["k"], o=v["o"], first=now.isoformat(),
                                             p=v.get("p"), m=v.get("m"), start=st))
                e.setdefault("start", st)
        # off-market prices (sharp.off_market): each first sighting, graded like tags
        for x in FL.get("off") or []:
            st = starts.get(str(x["g"]))
            if (st and PL.when(st) <= now) or x.get("o") is None:
                continue
            e = log.setdefault(f"off|{sport}|{x['g']}|{x['n']}|{x['b']}|{x['bk']}", dict(sport=sport, src="off", gid=x["g"], name=x["n"], bet=x["b"], book=x["bk"],
                                                                                    o=x["o"], first=now.isoformat(), p=x.get("fair"), m=x.get("fair"), start=st))
            e.setdefault("start", st)
        # Pinnacle / consensus line moves (sharp.pin_moves; owner 2026-10-23): each first firing, graded like tags
        for x in FL.get("moves") or []:
            st = starts.get(str(x["g"])) or x.get("start")
            if (st and PL.when(st) <= now) or x.get("o") is None:
                continue
            e = log.setdefault(f"move|{sport}|{x['g']}|{x['n']}|{x['b']}|{x['src']}|{x['sig']}", dict(sport=sport, src="move", msrc=x["src"], sig=x["sig"],
                               gid=x["g"], name=x["n"], bet=x["b"], book=x["bk"], o=x["o"], first=now.isoformat(), p=x.get("fair") or x.get("cons"),
                               m=x.get("fair") or x.get("cons"), start=st, from_l=x.get("from_l"), to_l=x.get("to_l")))
            e.setdefault("start", st)
        # ladder knees (sharp.ladder_knees; owner 2026-10-24): each first sighting at its book's price, graded like tags
        for x in FL.get("knees") or []:
            st = starts.get(str(x["g"])) or x.get("start")
            if (st and PL.when(st) <= now) or x.get("o") is None:
                continue
            e = log.setdefault(f"knee|{sport}|{x['g']}|{x['n']}|{x['b']}|{x['bk']}", dict(sport=sport, src="knee", gid=x["g"], name=x["n"], bet=x["b"],
                               book=x["bk"], o=x["o"], first=now.isoformat(), p=x.get("fair") or x.get("p"), m=x.get("fair"), start=st,
                               prev=x.get("prev"), next=x.get("next"), ratio=x.get("ratio")))
            e.setdefault("start", st)
        # early-value openers (oddshist.py; owner 2026-10-26): a book's first price at a line already 3-40% under the sharp fair
        for x in FL.get("openers") or []:
            st = starts.get(str(x["g"])) or x.get("start")
            if (st and PL.when(st) <= now) or x.get("o") is None:
                continue
            e = log.setdefault(f"open|{sport}|{x['g']}|{x['n']}|{x['b']}|{x['bk']}", dict(sport=sport, src="open", gid=x["g"], name=x["n"], bet=x["b"],
                               book=x["bk"], o=x["o"], first=now.isoformat(), p=x.get("fair"), m=x.get("fair"), start=st))
            e.setdefault("start", st)
        # NHL goal droughts (owner 2026-10-27, research/player_trends_nhl.py): every anytime-goal over at the owner's books for a
        # player 3+ games without a goal, at its first price (best of Hard Rock / Novig / Kalshi), graded like tags
        if sport == "nhl":
            try:
                DRY = json.load(open(os.path.join(site, "markets", "dry_nhl.json"))).get("games", {})
            except (OSError, ValueError):
                DRY = {}
            for gid, x in DRY.items():
                st = starts.get(str(gid)) or x.get("start")
                if st and PL.when(st) <= now:
                    continue
                for nm, n in (x.get("dry") or {}).items():
                    if n < DRY_MIN:
                        continue
                    e_ = (((PR.get("props") or {}).get(str(gid)) or {}).get(nm) or {}).get("goal") or {}
                    bo = [(dec(v[0]), b, v[0]) for b, v in (e_.get("o") or {}).items() if b in ("Hard Rock", "Novig", "Kalshi") and isinstance(v, list) and v and v[0] is not None]
                    if not bo:
                        continue
                    _, bk, o = max(bo)
                    fair = (e_.get("pin") or {}).get("o")
                    if fair is None:                                  # as in the study: only props the sharp books price
                        continue
                    e = log.setdefault(f"dry|{sport}|{gid}|{nm}", dict(sport=sport, src="dry", gid=gid, name=nm, bet="goal", book=bk, o=o,
                                                                     first=now.isoformat(), p=fair, m=fair, start=st, dry=n))
                    e.setdefault("start", st)
        # NHL opponent missing a regular defenseman (owner 2026-10-30, research/nhl_goal_methods_absd.py): every anytime-goal
        # over at the owner's books for a player whose opponent is missing one (hockey/project.py -> markets/dout_nhl.json),
        # first price, best of Hard Rock / Novig / Kalshi, sharp-priced props only (as in the study)
        if sport == "nhl":
            try:
                DO = json.load(open(os.path.join(site, "markets", "dout_nhl.json"))).get("games", {})
            except (OSError, ValueError):
                DO = {}
            for gid, x in DO.items():
                st = starts.get(str(gid)) or x.get("start")
                if st and PL.when(st) <= now:
                    continue
                for nm in x.get("vs") or []:
                    e_ = (((PR.get("props") or {}).get(str(gid)) or {}).get(nm) or {}).get("goal") or {}
                    bo = [(dec(v[0]), b, v[0]) for b, v in (e_.get("o") or {}).items() if b in ("Hard Rock", "Novig", "Kalshi") and isinstance(v, list) and v and v[0] is not None]
                    fair = (e_.get("pin") or {}).get("o")
                    if not bo or fair is None:
                        continue
                    _, bk, o = max(bo)
                    e = log.setdefault(f"dout|{sport}|{gid}|{nm}", dict(sport=sport, src="dout", gid=gid, name=nm, bet="goal", book=bk, o=o,
                                                                      first=now.isoformat(), p=fair, m=fair, start=st,
                                                                      dout=", ".join(v for vs in (x.get("out") or {}).values() for v in vs)))
                    e.setdefault("start", st)
        # NBA double / triple-doubles (owner 2026-10-27: 'yes' to grading them; sharp.oa_nba_ddtd -> markets/ddtd_nba.json):
        # every Yes at the owner's best book, its last pregame price (baseline, kept off the bets feed), and each first
        # sighting with an edge 5-40% (chance = 5% ours + 95% the books' fair, the screener's NBA weight)
        if sport == "nba":
            try:
                DT = json.load(open(os.path.join(site, "markets", "ddtd_nba.json"))).get("props", {})
            except (OSError, ValueError):
                DT = {}
            for gid, ps in DT.items():
                st = starts.get(str(gid))
                if not st or PL.when(st) <= now:
                    continue
                for nm, ks in ps.items():
                    for kind, x in ks.items():
                        fair = (x.get("pin") or {}).get("o")
                        bo = [(dec(v[0]), b, v[0]) for b, v in (x.get("o") or {}).items() if b in ("Hard Rock", "Novig", "Kalshi") and isinstance(v, list) and v and v[0] is not None]
                        if fair is None or not bo:
                            continue
                        _, bk, o = max(bo)
                        bet = "double-double" if kind == "dd" else "triple-double"
                        q = .05 * x["p"] + .95 * fair if x.get("p") is not None else fair
                        e = log.setdefault(f"ddtd|all|{gid}|{nm}|{kind}", dict(sport=sport, src="ddtd", grp="all", gid=gid, name=nm, bet=bet, first=now.isoformat(), start=st))
                        e.update(book=bk, o=o, p=round(q, 4), m=fair)                    # latest pregame price
                        if .05 <= q * dec(o) - 1 <= .40:
                            log.setdefault(f"ddtd|edge|{gid}|{nm}|{kind}", dict(sport=sport, src="ddtd", grp="edge", gid=gid, name=nm, bet=bet, book=bk, o=o,
                                                                                first=now.isoformat(), p=round(q, 4), m=fair, ours=x.get("p"), start=st))
        # liquidity-consensus tags (sharp.lc_tags): logged and graded the same way, as their own category
        for gid, tg in (FL.get("lc") or {}).items():
            st = starts.get(str(gid))
            if st and PL.when(st) <= now:
                continue
            for nm, v in tg.items():
                if not isinstance(v, dict) or v.get("o") is None:
                    continue
                e = log.setdefault(f"lc|{sport}|{gid}|{nm}|{v['b']}", dict(sport=sport, src="lc", gid=gid, name=nm, bet=v["b"], book=v["k"], o=v["o"],
                                                                         first=now.isoformat(), p=v.get("q"), m=v.get("m"), start=st))
                e.setdefault("start", st)
        # closing price: the tagged book's current price for that bet, until the game starts
        for key, e in log.items():
            if e["sport"] != sport or e.get("res") is not None:
                continue
            if e.get("start") and PL.when(e["start"]) <= now:
                continue
            L = _tag_leg(sport, e["bet"])
            kind = {"td": "td", "goal": "goal"}.get(L["kind"], L["kind"]) if L else None
            x = (((PR.get("props") or {}).get(str(e["gid"])) or {}).get(e["name"]) or {}).get(kind) if kind else None
            if x:
                book = (x.get("o" if L["side"] == "o" else "u") or {}).get(e["book"])
                if book:
                    e["close"] = book[0]
                    e["close_t"] = now.isoformat()
        # settle started tags from box scores
        for key, e in log.items():
            if e["sport"] != sport or e.get("res") is not None or not e.get("start") or PL.when(e["start"]) > now - dt.timedelta(hours=3):
                continue
            L = _tag_leg(sport, e["bet"])
            if not L:
                e["res"] = "V"
                continue
            try:
                g = PL.game_for(sport, e["gid"], e["start"])
            except Exception:                                                   # noqa: BLE001
                g = None
            if g:
                e["res"] = PL.settle(dict(L, name=e["name"]), g)
    json.dump(log, open(fp, "w"), separators=(",", ":"))
    print(f"profit tags: {len(log)} logged, {sum(1 for e in log.values() if e.get('res') in ('W', 'L'))} settled")


# ---------------------------------------------------------------- page
def _stats(pnl, clv=None):
    n = len(pnl)
    if not n:
        return None
    a = np.asarray(pnl, float)
    roi, sd = float(a.mean()), float(a.std(ddof=1)) if n > 1 else 0.0
    moe = 1.96 * sd / math.sqrt(n) if n > 1 else None
    out = dict(n=n, won=int((a > 0).sum()), units=round(float(a.sum()), 2), roi=round(roi, 4), moe=None if moe is None else round(moe, 4))
    c = None if clv is None else np.asarray([x for x in clv if x is not None and x == x], float)    # x == x drops NaN
    if c is not None and len(c):
        out.update(clv=round(float(c.mean()), 4), clv_beat=round(float((c > 0).mean()), 3), clv_n=int(len(c)),
                   clv_z=round(float(c.mean() / (c.std(ddof=1) / math.sqrt(len(c)))), 2) if len(c) > 1 and c.std(ddof=1) > 0 else None)
    if n < 30:
        v = "too early"
    elif moe is not None and roi - moe > 0:
        v = "proven"
    elif moe is not None and roi + moe < 0:
        v = "losing"
    elif out.get("clv_z") is not None and out["clv"] > 0 and out["clv_z"] >= 2:
        v = "promising"
    else:
        v = "unproven"
    out["verdict"] = v
    return out


TRACKED = ["NHL anytime goals (top 3)", "NFL player props", "NBA player props", "MLB home runs (VALUE)"]
TRACK_SINCE = "Oct 18"


def page_data():
    rows = []                                          # (sport, category, date, pnl, clv)
    for f in glob.glob(os.path.join(OUT, "profit_*.json")):
        if f.endswith(("profit_tags.json", "profit_wfair.json")):
            continue
        try:
            rs = json.load(open(f)).get("rows", [])
        except (OSError, ValueError):
            print(f"profit page: skipped {os.path.basename(f)}"); continue
        for r in rs:
            rows.append(dict(sport=r["sport"], cat=f"{r['cat']} ({'Kalshi' if r['src'] == 'kalshi' else 'Polymarket'})", date=r["date"], pnl=r["pnl"], clv=r.get("clv")))
    tp = os.path.join(OUT, "profit_tags.json")
    for e in (json.load(open(tp)) if os.path.exists(tp) else {}).values():
        if e.get("res") not in ("W", "L"):
            continue
        d_ = dec(e["o"])
        clv = (1 / dec(e["close"]) - 1 / d_) if e.get("close") is not None else None
        rows.append(dict(sport=e["sport"], cat=f"{e['sport'].upper()} {tag_label(e)}", date=_et_day(e.get("start") or e["first"]),
                         pnl=(d_ - 1) if e["res"] == "W" else -1.0, clv=clv))
    # book-weighted fair: graded in the background, shown only once it reads Proven or Promising
    try:
        wl = [e for e in json.load(open(os.path.join(OUT, "profit_wfair.json"))).values() if e.get("res") in ("W", "L")]
    except (OSError, ValueError):
        wl = []
    for sp_ in {e["sport"] for e in wl}:
        y = [e for e in wl if e["sport"] == sp_]
        pnl = [(dec(e["o"]) - 1) if e["res"] == "W" else -1.0 for e in y]
        clv = [(e["close_f"] - 1 / dec(e["o"])) if e.get("close_f") is not None else None for e in y]
        st = _stats(pnl, clv)
        print(f"book-weighted fair ({sp_}): {st['n']} bets, {st['roi']:+.1%}, verdict {st['verdict']}")
        if st["verdict"] in ("proven", "promising"):
            for e, p_, c_ in zip(y, pnl, clv):
                rows.append(dict(sport=sp_, cat=WF_CAT, date=_et_day(e.get("start") or e["first"]), pnl=p_, clv=c_))
    for f in glob.glob(os.path.join(HERE, "parlays", "*", "????-??-??.json")):
        if os.path.basename(os.path.dirname(f)) == "learn":           # the learning builder's own files (builder.py)
            continue
        try:
            d = json.load(open(f))
        except (OSError, ValueError) as e:              # one bad file must not take the whole page down
            print(f"profit page: skipped {os.path.basename(f)} ({type(e).__name__})"); continue
        for p in d.get("parlays", []):
            if p.get("res") not in ("W", "L"):
                continue
            pay = p.get("paid") or p.get("d")
            if not pay:
                continue
            rows.append(dict(sport=d["sport"], cat=f"{d['sport'].upper()} parlays: {p['card']}", date=d["date"],
                             pnl=(pay - 1) if p["res"] == "W" else -1.0, clv=None))
            if p.get("pick"):                               # the daily card (parlays.pick_card): 4 a day, $5 each
                rows.append(dict(sport=d["sport"], cat=f"{d['sport'].upper()} Today's card ($5 a parlay)", date=d["date"],
                                 pnl=(pay - 1) if p["res"] == "W" else -1.0, clv=None))
    if not rows:
        return dict(rows=[], overall=None, sports=[])
    df = pd.DataFrame(rows)
    today = dt.datetime.now(ET).date().isoformat()
    last30 = (dt.date.fromisoformat(today) - dt.timedelta(days=30)).isoformat()
    sports = []
    for sp, x in df.groupby("sport"):
        cats = []
        for c, y in x.groupby("cat"):
            s = _stats(y.pnl.values, y.clv.values if y.clv.notna().any() else None)
            s30 = _stats(y[y.date >= last30].pnl.values)
            cats.append(dict(cat=c, **s, roi30=s30["roi"] if s30 else None, n30=s30["n"] if s30 else 0))
        cats.sort(key=lambda c: ({"proven": 0, "promising": 1, "unproven": 2, "too early": 3, "losing": 4}[c["verdict"]], -c["n"]))
        curve = x.sort_values("date").groupby("date").pnl.sum().cumsum().round(2)
        sports.append(dict(sport=sp, **_stats(x.pnl.values), cats=cats, curve=[[d, float(v)] for d, v in curve.items()]))
    order = {"nfl": 0, "nhl": 1, "nba": 2, "mlb": 3}
    sports.sort(key=lambda s: order.get(s["sport"], 9))
    o = _stats(df.pnl.values)
    o30 = _stats(df[df.date >= last30].pnl.values)
    # rules under test (2026-10-18): each current rule next to its Pinnacle- and consensus-anchored twins, always listed
    track = []
    for base in TRACKED:
        for tag in (("", " · market-anchored") if base.startswith("MLB") else ("", " · Pinnacle-anchored", " · consensus-anchored", " · liquidity consensus")):
            y = df[df.cat == f"{base}{tag} (Kalshi)"]
            st = _stats(y.pnl.values, y.clv.values if len(y) and y.clv.notna().any() else None) if len(y) else None
            track.append(dict(base=base, rule=tag.strip(" ·") or "current rule", n=int(len(y)),
                              roi=st["roi"] if st else None, moe=st["moe"] if st else None,
                              clv=st.get("clv") if st else None, clv_n=st.get("clv_n") if st else 0))
    return dict(overall=dict(**o, roi30=o30["roi"] if o30 else None, n30=o30["n"] if o30 else 0), sports=sports, track=track, since=TRACK_SINCE, feed=feed_data(), edges=edge_bands())


# ---------------------------------------------------------------- what actually worked (owner 2026-10-21)
# The bets themselves, not just rates: every graded straight bet the site recommends (Kalshi rule picks on the current
# rule, Value tags) and every parlay worth tracking (Today's card, multisport, path / ladder / primetime, Value-tag
# parlays), last 14 days, with each leg's result and the owner's stakes ($10 straight, $5 parlay).
FEED_DAYS, STRAIGHT, PARLAY = 14, 10.0, 5.0
# owner 2026-10-21: "I don't want to see under legs since I don't play them. I want to see the experimental ladder
# candidates and parlays along with the value legs" -> YES / over bets only (rule picks incl. the experimental ladders,
# Value tags), every priced parlay with no under leg (same-game cards excluded: no book pays their product)


def _plays(cat, bet):
    return "·" not in cat and str(bet).upper() == "YES"


def _over_leg(L):
    return str(L.get("side", "o")).lower() not in ("u", "under") and not str(L.get("lab", "")).lower().startswith(("under ", "no "))


EDGE_BANDS = [(-9, 0, "under 0%"), (0, .05, "0-5%"), (.05, .10, "5-10%"), (.10, .20, "10-20%"), (.20, .40, "20-40%"), (.40, .80, "40-80%"), (.80, 99, "80%+")]
EDGE_CATS = ("ladder candidates", "ladder edge")


def edge_bands():
    """Results by claimed edge (owner 2026-10-23: "out of all the ladder candidates and pinnacle movement show the edge
    percentage that they have hit in"): ladder rules (claimed edge = our chance / Kalshi cost with fee - 1; rows carry
    `p` since 2026-10-23, rebuilt for every graded quote) and the Pinnacle / consensus move signals (profit_tags src
    'move': the fair at logging x the payout - 1). Per band: bets, hit rate, our average chance, return per $1."""
    out = []
    def table(cat, xs):
        bands = []
        for lo, hi, lab in EDGE_BANDS:
            x = [r for r in xs if lo <= r[0] < hi]
            if x:
                n = len(x)
                bands.append(dict(lab=lab, n=n, hit=round(sum(r[1] for r in x) / n, 4), ours=round(sum(r[2] for r in x) / n, 4), roi=round(sum(r[3] for r in x) / n, 4)))
        if bands:
            n = len(xs)
            out.append(dict(cat=cat, n=n, hit=round(sum(r[1] for r in xs) / n, 4), roi=round(sum(r[3] for r in xs) / n, 4), bands=bands))
    for f in sorted(glob.glob(os.path.join(OUT, "profit_*.json"))):
        if f.endswith(("profit_tags.json", "profit_wfair.json")):
            continue
        try:
            rs = json.load(open(f)).get("rows", [])
        except (OSError, ValueError):
            continue
        by = {}
        for r in rs:
            if any(k in r["cat"] for k in EDGE_CATS) and r.get("won") is not None and r.get("p") is not None and r.get("cost"):
                by.setdefault(r["cat"], []).append((r["p"] / r["cost"] - 1, r["won"], r["p"], r["pnl"]))
        for cat, xs in by.items():
            table(cat, xs)
    tp = os.path.join(OUT, "profit_tags.json")
    by = {}
    for e in (json.load(open(tp)) if os.path.exists(tp) else {}).values():
        if e.get("src") != "move" or e.get("res") not in ("W", "L") or e.get("p") is None or e.get("o") is None:
            continue
        d = 1 + (e["o"] / 100 if e["o"] > 0 else 100 / -e["o"])
        by.setdefault(f"{e['sport'].upper()} {tag_label(e)}", []).append((e["p"] * d - 1, int(e["res"] == "W"), e["p"], (d - 1) if e["res"] == "W" else -1.0))
    for cat, xs in sorted(by.items()):
        table(cat, xs)
    return out


def feed_data():
    import glob as _g
    since = (dt.datetime.now(ET).date() - dt.timedelta(days=FEED_DAYS)).isoformat()
    since30 = (dt.datetime.now(ET).date() - dt.timedelta(days=30)).isoformat()
    items, seen = [], set()
    for f in _g.glob(os.path.join(OUT, "profit_*.json")):
        if f.endswith(("profit_tags.json", "profit_wfair.json")):
            continue
        try:
            rs = json.load(open(f)).get("rows", [])
        except (OSError, ValueError):
            continue
        for r in rs:
            if r["date"] < since30 or not _plays(r["cat"], r.get("bet")):
                continue
            k = (r["date"], r.get("title"), r.get("bet"))
            if k in seen:
                continue
            seen.add(k)
            items.append(dict(date=r["date"], sport=r["sport"], t="bet", cat=r["cat"], title=f"{r.get('title') or ''} {r.get('bet') or ''}".strip(),
                              book="Kalshi" if r.get("src") == "kalshi" else "Polymarket", pay=round(1 / r["cost"], 2) if r.get("cost") else None,
                              q=r.get("q"), res="W" if r.get("won") else "L", net=round(STRAIGHT * r["pnl"], 2)))
    tp = os.path.join(OUT, "profit_tags.json")
    for e in (json.load(open(tp)) if os.path.exists(tp) else {}).values():
        d0 = _et_day(e.get("start") or e["first"])
        if e.get("res") not in ("W", "L") or d0 < since30 or str(e.get("bet", "")).lower().startswith("under") or e.get("grp") == "all":
            continue
        dd = dec(e["o"])
        items.append(dict(date=d0, sport=e["sport"], t="bet", cat=f"{e['sport'].upper()} {tag_label(e)}", title=f"{e['name'].title()} {e['bet']}",
                          book=e.get("book"), pay=round(dd, 2), q=e.get("p"), res=e["res"], net=round(STRAIGHT * ((dd - 1) if e["res"] == "W" else -1), 2)))
    legs = []
    for f in _g.glob(os.path.join(HERE, "parlays", "*", "????-??-??.json")):
        if os.path.basename(os.path.dirname(f)) == "learn":           # the learning builder's own files (builder.py)
            continue
        try:
            d = json.load(open(f))
        except (OSError, ValueError):
            continue
        for q in d.get("parlays", []):
            if q.get("res") not in ("W", "L") or str(q.get("card", "")).startswith("Same-game") or not all(_over_leg(L) for L in q["legs"]):
                continue
            pay = q.get("paid") or q.get("d")
            if not pay:
                continue
            for L in q["legs"]:                                  # leg-level hit rates (each leg counted once per day)
                if L.get("res") in ("W", "L"):
                    legs.append(dict(sport=L.get("sport") or d["sport"], kind=L.get("kind"), p=L.get("p"), y=L["res"] == "W", date=d["date"],
                                     key=f"{d['date']}|{L.get('gid')}|{L.get('name') or L.get('lab')}|{L.get('kind')}|{L.get('line')}", f=L.get("f")))
            if d["date"] < since30:
                continue
            items.append(dict(date=d["date"], sport=d["sport"], t="parlay", cat="Today’s card" if q.get("pick") else q["card"], tier=q.get("tier"),
                              legs=[dict(lab=L.get("lab"), res=L.get("res"), sport=L.get("sport")) for L in q["legs"]], pay=round(pay, 2), q=q.get("p"),
                              res=q["res"], net=round((STRAIGHT if q.get("tier") == "straight" else PARLAY) * ((pay - 1) if q["res"] == "W" else -1), 2)))
    items.sort(key=lambda x: (x["date"], x["res"] == "W", x["net"]), reverse=True)
    # your bets, last 30 days (the record at the top of the page)
    staked = sum(STRAIGHT if x["t"] == "bet" or x.get("tier") == "straight" else PARLAY for x in items)
    you = dict(n=len(items), w=sum(x["res"] == "W" for x in items), net=round(sum(x["net"] for x in items), 2), staked=staked)
    # what's working: each bet type over the last 30 days, in dollars at the owner's stakes
    by = {}
    for x in items:
        if x["date"] >= since30:
            b = by.setdefault((x["sport"], x["cat"]), dict(sport=x["sport"], cat=x["cat"], n=0, w=0, net=0.0))
            b["n"] += 1; b["w"] += x["res"] == "W"; b["net"] += x["net"]
    types = sorted((dict(b, net=round(b["net"], 2)) for b in by.values() if b["n"] >= 5), key=lambda b: -b["net"])
    # parlay legs: hit rate vs our chance, by sport and stat (and by path reason)
    uniq = {L["key"]: L for L in legs}.values()
    lt = {}
    for L in uniq:
        k = (L["sport"], {"anytime_td": "td"}.get(L["kind"], L["kind"]))
        t = lt.setdefault(k, dict(sport=k[0], kind=k[1], n=0, hit=0, exp=0.0)); t["n"] += 1; t["hit"] += L["y"]; t["exp"] += L["p"] or 0
        for r in L.get("f") or []:
            t = lt.setdefault(("reason", r), dict(sport="path", kind=r, n=0, hit=0, exp=0.0)); t["n"] += 1; t["hit"] += L["y"]; t["exp"] += L["p"] or 0
    legt = sorted((dict(t, exp=round(t["exp"], 1)) for t in lt.values() if t["n"] >= 5), key=lambda t: (t["sport"] == "path", t["sport"], -t["n"]))
    # every graded bet of the window (a cap cut older days' losers: they sort after winners), compact keys
    items = [x for x in items if x["date"] >= since]          # the day-by-day list: last 14 days
    return dict(items=items, you=you, types=types, legs=legt, days=FEED_DAYS, stake=dict(straight=STRAIGHT, parlay=PARLAY))


PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Profit</title>__FONTS__<style>__CSS__
.rec{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:6px 0 14px}
.trk{display:grid;gap:12px;grid-template-columns:repeat(auto-fit,minmax(280px,1fr))}.trk .tb{border:1px solid var(--faint);border-radius:10px;padding:10px 12px}
.trk h3{margin:0 0 6px;font-size:15px}.trk table{width:100%;border-collapse:collapse;font-size:13px}.trk td{padding:4px 0;border-top:1px solid var(--faint)}
.trk td.r{text-align:right;white-space:nowrap}.trk .wait{color:var(--muted)}.trk .tag3{display:inline-block;font-size:10.5px;font-weight:700;letter-spacing:.05em;text-transform:uppercase;color:#F2C14E;border:1px solid currentColor;border-radius:4px;padding:0 5px;margin-left:6px}
.rec div{background:var(--sunk);border-radius:8px;padding:10px 12px}.rec b{display:block;font-family:var(--display);font-size:26px;line-height:1.1}.rec span{font-size:12px;color:var(--muted)}.rec b span{font-size:inherit}.rec b span.rp{color:var(--good)}.rec b span.rn{color:var(--bad)}
.wk{display:grid;gap:10px;grid-template-columns:repeat(auto-fit,minmax(260px,1fr))}.wk .tb{border:1px solid var(--faint);border-radius:10px;padding:10px 12px}
.wk h3{margin:0 0 6px;font-size:14px}.wk li{display:flex;gap:8px;align-items:baseline;padding:3px 0;border-top:1px solid var(--faint);font-size:13.5px}.wk ul{list-style:none;margin:0;padding:0}
.wk li .n{margin-left:auto;white-space:nowrap;font-weight:700}.wk li small{color:var(--muted)}
.fsw{display:flex;flex-wrap:wrap;gap:6px;margin:0 0 10px}.fsw button{border:1px solid var(--faint);background:none;color:var(--ink);border-radius:999px;padding:4px 12px;font:600 13px var(--body);cursor:pointer}
.fsw button[aria-pressed="true"]{background:var(--ink);color:var(--bg);border-color:var(--ink)}
.fd h3{display:inline;font-size:13px;color:var(--muted);margin:0;font-family:var(--body);letter-spacing:.04em;text-transform:uppercase}.fday{border-top:1px solid var(--faint);padding:8px 0}.fgrp{border-top:1px solid var(--faint);padding:6px 0 6px 14px}.fgrp>summary{display:flex;align-items:baseline;gap:8px;cursor:pointer;font-size:13.5px}.fgrp>summary .gl{flex:1}.fgrp>summary .net{font-weight:800;white-space:nowrap}.fgrp .fi{padding-left:4px}.fgrp .c{font-size:11.5px;color:var(--muted)}.fday>summary{cursor:pointer;list-style-position:outside}.fday .fi:first-of-type{border-top:0}
.fi{display:grid;grid-template-columns:22px 1fr auto;gap:2px 8px;align-items:baseline;padding:6px 0;border-top:1px solid var(--faint);font-size:13.5px}
.fi .m{font-weight:800}.fi.W .m{color:var(--good)}.fi.L .m{color:var(--bad)}.fi.L{opacity:.8}.fi .c{font-size:11.5px;color:var(--muted)}
.fi .net{font-weight:800;white-space:nowrap;text-align:right}.fi.W .net{color:var(--good)}.fi.L .net{color:var(--muted)}
.fi .lg{grid-column:2/4;display:flex;flex-wrap:wrap;gap:2px 10px;font-size:12.5px}.fi .lg span.W{color:var(--good)}.fi .lg span.L{color:var(--bad)}.fi .lg span.V{color:var(--muted)}
.lt table{width:100%;border-collapse:collapse;font-size:13px}.lt td,.lt th{padding:4px 6px;border-top:1px solid var(--faint);text-align:right}.lt td:first-child,.lt th:first-child{text-align:left}
.rp{color:var(--good)}.rn{color:var(--bad)}
.vd{display:inline-block;border-radius:999px;padding:1px 9px;font-size:11.5px;font-weight:700;white-space:nowrap}
.vd.proven{background:color-mix(in srgb,var(--good) 20%,transparent);color:var(--good)}.vd.promising{background:color-mix(in srgb,#6CB4FF 22%,transparent);color:#6CB4FF}
.vd.unproven,.vd.too{background:var(--sunk);color:var(--muted)}.vd.losing{background:color-mix(in srgb,var(--bad) 18%,transparent);color:var(--bad)}
.pf td.l{white-space:normal}.pf small{display:block;color:var(--muted);font-size:11.5px}
.spark{width:100%;height:56px;display:block;margin:4px 0 10px}
.sph{display:flex;flex-wrap:wrap;align-items:baseline;gap:6px 16px}.sph h2{margin-right:auto}
@media (max-width:640px){.pf th:nth-child(3),.pf td:nth-child(3),.pf th:nth-child(5),.pf td:nth-child(5){display:none}}
</style></head><body><div class="wrap">
<header><div><div class="eyebrow">All sports</div><h1>Profit</h1></div></header>
<main id="main"></main>
<footer>Everything the site recommends, graded at the price it showed, 1 unit per bet. Prediction-market picks follow the site's rules on the last pregame quote (Kalshi includes its fee); Value tags use the tagged book's price when the tag first appeared, and Beat close compares it with that book's last price before the game; for prediction-market picks Beat close is Pinnacle's fair (no-vig) closing chance minus the price paid, fee included, when Pinnacle priced the same bet (positive = better than the sharpest closing line, the quickest sign of a real edge); parlays are every card on the Plays / Parlays tabs, frozen before their first game. Verdicts: Proven = the 95% range of the return is above zero (30+ bets); Losing = below zero; Promising = beats the closing line by a clear margin; Too early = fewer than 30 bets.</footer>
</div><script>
const D = __DATA__;
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
const pc = (v, d = 1) => v == null ? '–' : `<span class="${v >= 0 ? 'rp' : 'rn'}">${v >= 0 ? '+' : ''}${(v * 100).toFixed(d)}%</span>`;
const un = v => v == null ? '–' : `<span class="${v >= 0 ? 'rp' : 'rn'}">${v >= 0 ? '+' : ''}${v.toFixed(1)}u</span>`;
const VL = {proven: 'Proven', promising: 'Promising', unproven: 'Unproven', 'too early': 'Too early', losing: 'Losing'};
const vd = v => `<span class="vd ${v === 'too early' ? 'too' : v}">${VL[v]}</span>`;
function spark(c){
  if (!c || c.length < 2) return '';
  const ys = c.map(p => p[1]), lo = Math.min(0, ...ys), hi = Math.max(0, ...ys), W = 600, H = 56, X = i => i / (c.length - 1) * W, Y = v => H - 4 - (v - lo) / ((hi - lo) || 1) * (H - 8);
  const last = ys[ys.length - 1];
  return `<svg class="spark" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="Units over time"><line x1="0" x2="${W}" y1="${Y(0)}" y2="${Y(0)}" stroke="var(--faint)" stroke-dasharray="4 4"/><polyline fill="none" stroke="${last >= 0 ? 'var(--good)' : 'var(--bad)'}" stroke-width="2.5" points="${c.map((p, i) => X(i) + ',' + Y(p[1])).join(' ')}"/></svg>`;
}

// ---- what actually worked
const FE = D.feed || {items: [], types: [], legs: []};
let FF = {w: true, s: 'all'}; try { Object.assign(FF, JSON.parse(localStorage.getItem('simfeed-v1') || '{}')); } catch (e) {}
const usd = v => `<span class="${v >= 0 ? 'rp' : 'rn'}">${v >= 0 ? '+' : '−'}$${Math.abs(v).toFixed(2)}</span>`;
const dayL = d => new Date(d + 'T12:00:00').toLocaleDateString([], {weekday: 'short', month: 'short', day: 'numeric'});
function working(){
  const T = FE.types || []; if (!T.length) return '';
  const up = T.filter(t => t.net > 0).slice(0, 4), dn = T.filter(t => t.net < 0).slice(-4).reverse();
  const lab = t => t.cat.toUpperCase().startsWith(t.sport.toUpperCase()) ? esc(t.cat) : `${esc(t.sport.toUpperCase())} ${esc(t.cat)}`;
  const li = t => `<li><span><b>${lab(t)}</b><small> · won ${t.w} of ${t.n}</small></span><span class="n">${usd(t.net)}</span></li>`;
  return `<section class="panel"><div class="phead"><h2>What’s working</h2><span class="small muted">last 30 days · $${FE.stake.straight} a straight bet, $${FE.stake.parlay} a parlay</span></div>
    <div class="wk"><div class="tb"><h3>Making money</h3><ul>${up.map(li).join('') || '<li class="muted">Nothing in profit yet.</li>'}</ul></div>
    <div class="tb"><h3>Losing money</h3><ul>${dn.map(li).join('') || '<li class="muted">Nothing losing.</li>'}</ul></div></div></section>`;
}
function you(){
  const Y = FE.you; if (!Y || !Y.n) return '';
  return `<section class="panel"><div class="phead"><h2>Your bets</h2><span class="small muted">last 30 days · overs, ladders and parlays you’d play · $${FE.stake.straight} straight, $${FE.stake.parlay} parlay</span></div>
    <div class="rec"><div><b>${usd(Y.net)}</b><span>net</span></div><div><b>${Y.w}-${Y.n - Y.w}</b><span>won-lost</span></div><div><b>${pc(Y.net / Y.staked)}</b><span>return on $${Math.round(Y.staked).toLocaleString()} staked</span></div></div></section>`;
}
function feed(){
  const sports = [...new Set(FE.items.map(x => x.sport))];
  const A = FE.items.filter(x => FF.s === 'all' || x.sport === FF.s), I = A.filter(x => !FF.w || x.res === 'W');   // totals always count every bet
  const sw = `<div class="fsw"><button data-fw="1" aria-pressed="${FF.w}" title="Inside each bet type, list only the winners">Winners only</button>${['all', ...sports].map(k => `<button data-fs="${k}" aria-pressed="${FF.s === k}">${k === 'all' ? 'All sports' : k.toUpperCase()}</button>`).join('')}</div>`;
  const days = [...new Set(I.map(x => x.date))];
  const tip = x => esc(`${x.t === 'bet' ? x.cat + ' · ' + (x.book || '') : x.cat}${x.q ? ' · our chance ' + Math.round(x.q * 100) + '%' : ''}`);
  const row = x => `<div class="fi ${x.res}" title="${tip(x)}"><span class="m">${x.res === 'W' ? '✓' : '✗'}</span><span><b>${x.t === 'parlay' ? `${esc(x.cat)}${x.tier ? ` · ${esc(x.tier)}` : ''}` : esc(x.title)}</b>
      <span class="c"> · ${esc(x.sport.toUpperCase())}${x.pay ? ` · ${x.pay.toFixed(x.pay < 10 ? 2 : 0)}x` : ''}</span></span>
      <span class="net">${usd(x.net)}</span>${x.t === 'parlay' ? `<span class="lg">${x.legs.map(L => `<span class="${L.res || ''}">${L.res === 'W' ? '✓' : L.res === 'L' ? '✗' : '·'} ${esc(L.lab)}${L.sport ? ` <small>${esc(L.sport.toUpperCase())}</small>` : ''}</span>`).join('')}</span>` : ''}</div>`;
  const tot = A.reduce((s, x) => s + x.net, 0), w = A.filter(x => x.res === 'W').length;
  return `<section class="panel fd"><div class="phead"><h2>Results</h2><span class="small muted">last ${FE.days} days · ${w} of ${A.length} won · net ${usd(tot)}</span></div>${sw}
    ${days.map((d, i) => { const XA = A.filter(x => x.date === d), n = XA.reduce((s, x) => s + x.net, 0);
      // one line per bet type that day (record and net), its bets inside
      const G = {}; XA.forEach(x => { const k = x.sport + '|' + x.cat; (G[k] = G[k] || []).push(x); });
      const grp = Object.values(G).sort((a, b) => b.reduce((s, x) => s + x.net, 0) - a.reduce((s, x) => s + x.net, 0)).map(L => {
        const w = L.filter(x => x.res === 'W'), nn = L.reduce((s, x) => s + x.net, 0), show = FF.w ? w : L;
        const lab = L[0].cat.toUpperCase().startsWith(L[0].sport.toUpperCase()) ? L[0].cat : `${L[0].sport.toUpperCase()} ${L[0].cat}`;
        return `<details class="fgrp"><summary><span class="gl"><b>${esc(lab)}</b> <span class="c">won ${w.length} of ${L.length}</span></span><span class="net">${usd(nn)}</span></summary>${show.map(row).join('') || '<p class="small muted" style="margin:4px 0">No winners.</p>'}</details>`; }).join('');
      return `<details class="fday"${i === 0 ? ' open' : ''}><summary><h3>${dayL(d)} · ${XA.filter(x => x.res === 'W').length}-${XA.filter(x => x.res === 'L').length} · net ${usd(n)}</h3></summary>${grp}</details>`; }).join('') || '<p class="note">Nothing graded in this view yet.</p>'}</section>`;
}
function edgeTable(){
  const E = D.edges || []; if (!E.length) return '';
  const pc0 = v => (v >= 0 ? '+' : '−') + Math.abs(Math.round(v * 100)) + '%';
  const tb = c => `<div class="tb"><h3>${esc(c.cat)}</h3><p class="small muted" style="margin:0 0 4px">${c.n} bets · hit ${Math.round(c.hit * 100)}% · return ${pc0(c.roi)}</p><table><thead><tr><th>Claimed edge</th><th>Bets</th><th>Hit</th><th>Ours</th><th>Return</th></tr></thead><tbody>${c.bands.map(b => `<tr><td>${esc(b.lab)}</td><td>${b.n}</td><td>${(b.hit * 100).toFixed(1)}%</td><td class="muted">${(b.ours * 100).toFixed(1)}%</td><td class="${b.roi >= 0 ? 'rp' : 'rn'}">${pc0(b.roi)}${b.n < 30 ? ' <small class="muted">few</small>' : ''}</td></tr>`).join('')}</tbody></table></div>`;
  return `<section class="panel lt"><details><summary style="cursor:pointer"><h2 style="display:inline">Results by claimed edge</h2> <span class="small muted">ladder candidates · ladder edges · Pinnacle / consensus moves</span></summary>
    <p class="note" style="margin:8px 0">Each bet grouped by the edge we claimed when it was flagged (our chance against the price; Kalshi with its fee). Hit = how often it won; Ours = the chance we gave it on average. If the big claimed edges hit no more often than the small ones, the model is overconfident there. Fewer than 30 bets in a band is noise.</p>
    <div class="trk">${E.map(tb).join('')}</div></details></section>`;
}
function legTable(){
  const L = FE.legs || []; if (!L.length) return '';
  const KL = {rec_yds: 'receiving yds', rush_yds: 'rushing yds', pass_yds: 'passing yds', rec: 'catches', td: 'anytime TD', goal: 'anytime goal', sog: 'shots on goal', pts: 'points', reb: 'rebounds', ast: 'assists', fg3m: 'threes', ml: 'moneyline', spread: 'spread', total: 'total'};
  const tr = t => `<tr><td>${t.sport === 'path' ? `reason: <b>${esc(t.kind)}</b>` : `<b>${esc(t.sport.toUpperCase())}</b> ${esc(KL[t.kind] || t.kind)}`}</td><td>${t.n}</td><td>${t.hit} <small class="muted">(${Math.round(t.hit / t.n * 100)}%)</small></td><td>${t.exp}</td><td class="${t.hit - t.exp >= 0 ? 'rp' : 'rn'}">${t.hit - t.exp >= 0 ? '+' : '−'}${Math.abs(t.hit - t.exp).toFixed(1)}</td></tr>`;
  return `<section class="panel lt"><details><summary style="cursor:pointer"><h2 style="display:inline">Which parlay legs hit</h2> <span class="small muted">by leg type and reason</span></summary>
    <div class="tw" style="margin-top:8px"><table><thead><tr><th>Leg type</th><th>Legs</th><th>Hit</th><th>Expected</th><th>vs expected</th></tr></thead><tbody>${L.map(tr).join('')}</tbody></table></div>
    <p class="note" style="margin:6px 0 0">Expected = the sum of our chances for those legs. Hitting more than expected means that kind of leg has been underpriced; reasons are the path factors (usage, matchup, rotation, script / game, price) each leg had.</p></details></section>`;
}
document.addEventListener('click', e => { const b = e.target.closest('button[data-fw],button[data-fs]'); if (!b) return;
  if (b.dataset.fw) FF.w = !FF.w; else FF.s = b.dataset.fs;
  try { localStorage.setItem('simfeed-v1', JSON.stringify(FF)); } catch (x) {} render(); });
function render(){
  const O = D.overall;
  if (!O){ document.getElementById('main').innerHTML = '<section class="panel"><p class="note">Nothing graded yet.</p></section>'; return; }
  const tiles = `<div class="rec"><div><b>${un(O.units)}</b><span>${O.n} bets · all sports</span></div><div><b>${pc(O.roi)}</b><span>return per bet ± ${O.moe != null ? (O.moe * 100).toFixed(1) : '–'}%</span></div><div><b>${pc(O.roi30)}</b><span>last 30 days (${O.n30} bets)</span></div><div><b>${D.sports.reduce((n, s) => n + s.cats.filter(c => c.verdict === 'proven').length, 0)}</b><span>proven profitable bet types</span></div></div>`;
  const sp = s => `<section class="panel"><div class="sph"><h2>${s.sport.toUpperCase()}</h2><span>${un(s.units)} · ${pc(s.roi)} per bet · ${s.n} bets</span></div>${spark(s.curve)}
    <div class="tw"><table class="t pf"><thead><tr><th class="l">Bet type</th><th>Verdict</th><th>Record</th><th>Return</th><th>Beat close</th><th>Last 30 d</th></tr></thead><tbody>
    ${s.cats.map(c => `<tr><td class="l"><b>${esc(c.cat)}</b><small>${c.n} bets · ${un(c.units)}</small></td><td>${vd(c.verdict)}</td><td>${c.won}-${c.n - c.won}</td>
      <td>${pc(c.roi)}<small>± ${c.moe != null ? (c.moe * 100).toFixed(0) : '–'}%</small></td><td>${c.clv != null ? `${pc(c.clv, 1)}<small>${Math.round(c.clv_beat * 100)}% of ${c.clv_n}</small>` : '<span class="muted">–</span>'}</td>
      <td>${c.n30 ? pc(c.roi30, 0) + `<small>${c.n30} bets</small>` : '<span class="muted">–</span>'}</td></tr>`).join('')}</tbody></table></div></section>`;
  const T = D.track || [], by = {}; T.forEach(t => (by[t.base] = by[t.base] || []).push(t));
  const tcell = t => !t.n ? '<span class="wait">waiting for results</span>'
    : t.n < 30 ? `<span class="wait" title="Fewer than 30 bets: too early to read">${pc(t.roi)} · too early</span>` : `${pc(t.roi)}<small> ± ${t.moe != null ? (t.moe * 100).toFixed(0) : '–'}%</small>`;
  const ccell = t => t.clv != null ? `${pc(t.clv, 1)}<small> on ${t.clv_n}</small>` : '<span class="wait">–</span>';
  const track = T.length ? `<section class="panel"><div class="phead"><h2>New rules being tested <span class="tag3">since ${esc(D.since || '')}</span></h2></div>
    <p class="note" style="margin:0 0 10px">Each current rule is graded next to two versions that use the sharp market as the main input: our number weighted 10-25% against Pinnacle’s no-vig line, or against the consensus of Pinnacle, Novig and ProphetX. Same Kalshi quotes, same edge bar. Beat close = the sharp closing chance minus the price paid (positive = real value); it settles in weeks, the return takes months.</p>
    <div class="trk">${Object.entries(by).map(([b, L]) => `<div class="tb"><h3>${esc(b)}</h3><table><tbody>${L.map(t => `<tr><td>${esc(t.rule)}<small style="display:block;color:var(--muted)">${t.n} bets</small></td><td class="r">${tcell(t)}</td><td class="r">${ccell(t)}</td></tr>`).join('')}</tbody></table></div>`).join('')}</div></section>` : '';
  const all = `<details class="panel"><summary style="cursor:pointer"><h2 style="display:inline">Every tracked rule</h2> <span class="small muted">all bets incl. unders and the test versions</span></summary>
    <section class="panel"><div class="phead"><h2>Overall</h2></div>${tiles}</section>${track}${D.sports.map(sp).join('')}</details>`;
  document.getElementById('main').innerHTML = you() + working() + feed() + legTable() + edgeTable() + all;
}
render();
</script></body></html>"""


def page():
    import importlib.util
    import report
    spec = importlib.util.spec_from_file_location("sim_site", os.path.join(HERE, "site.py"))
    S = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(S)
    d = page_data()
    s = (PAGE.replace("__FONTS__", report.FONTS).replace("__CSS__", report.BASE_CSS).replace("__DATA__", json.dumps(d, separators=(",", ":"))))
    src = os.path.join(OUT, "profit_page.html")
    open(src, "w", encoding="utf-8").write(s)
    site = os.environ.get("SIM_SITE") or os.path.join(HERE, "site")
    S.inject(src, os.path.join(site, "profit.html"), 0, "profit")
    o = d.get("overall")
    print("profit page:", f"{o['units']:+.1f}u over {o['n']} bets" if o else "empty")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "page"
    try:
        if cmd == "rows":
            market_rows(sys.argv[2])
        elif cmd == "tags":
            tags()
        elif cmd == "page":
            page()
    except Exception as e:                                                      # noqa: BLE001
        print(f"profit {cmd}: failed ({type(e).__name__}: {e})")
