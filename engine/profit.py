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
LAB = {"rec yds": "rec_yds", "rush yds": "rush_yds", "pass yds": "pass_yds", "receptions": "rec"}


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
            out.append(dict(date=date, sport="nfl", cat="NFL ladder candidates (experimental)", src="kalshi", kind=k, title=r.get("title"),
                            bet="YES", cost=round(cy, 4), q=round(mid(r), 4), won=int(y), pnl=round(y / cy - 1, 4)))
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
                                bet="YES", cost=round(cy, 4), q=round(mid(r), 4), won=int(y), pnl=round(y / cy - 1, 4)))
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
WPIN = {"nhl": {"goal": .2}, "nba": {"pts": .1, "reb": .1, "ast": .1, "fg3m": .1},
        "nfl": {"rec": .25, "rec_yds": .25, "rush_yds": .25, "pass_yds": .25, "anytime_td": 0.0}}


def pin_rule(date, sport, cat, src, k, r, p, fy, cy, cn, ya, na, need, sides, R, GM):
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
    return [dict(date=date, sport=sport, cat=f"{cat} · Pinnacle-anchored", src=src, kind=k, title=r.get("title"), bet=bet, cost=round(cost, 4),
                 q=round(q if bet == "YES" else 1 - q, 4), won=int(hit), pnl=round(hit / cost - 1, 4), clv=round(fair - cost, 4))]


def market_rows(sport):
    import grade_markets as GM
    R = {"nfl": GM.nfl_results, "nhl": GM.nhl_results, "nba": GM.nba_results, "mlb": GM.mlb_results}[sport]()
    rows = []
    PP, PL = pin_close(sport)
    for f in sorted(glob.glob(os.path.join(OUT, f"{sport}_20??-??-??.json"))):
        date = os.path.basename(f)[len(sport) + 1:len(sport) + 11]
        mk = json.load(open(f)).get("markets", [])
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
            elif sport == "nhl" and k == "goal":
                if (r.get("line") or .5) >= 1 or rank.get((r["game"], r.get("pid")), 99) > 3:
                    continue
                cat, q, need, sides = "NHL anytime goals (top 3)", .5 * p + .5 * m, 0.0, ("YES",)
            elif k in LINE_KINDS:
                cat, q, need, sides = f"{sport.upper()} game lines", .5 * p + .5 * m, .03, ("YES", "NO")
            elif k in PROP_KINDS:
                cat, q, need, sides = f"{sport.upper()} player props", .5 * p + .5 * m, .05, ("YES", "NO")
            else:
                continue                                   # first scorer etc.: not something the site recommends
            fy = close_fair(sport, r, PP, PL)                      # Pinnacle's fair closing chance (YES side)
            if fy is not None and k in WPIN.get(sport, {}):          # the Pinnacle-anchored rule judges every quote on its own
                rows += pin_rule(date, sport, cat, src, k, r, p, fy, cy, cn, ya, na, need, sides, R, GM)
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
    parts = b.split(" ", 2)
    if len(parts) == 3 and parts[0] in ("over", "under") and parts[2] in LAB:
        return dict(kind=LAB[parts[2]], side="o" if parts[0] == "over" else "u", line=float(parts[1]))
    return None


def tags():
    import parlays as PL
    site = os.environ.get("SIM_SITE") or os.path.join(HERE, "site")
    fp = os.path.join(OUT, "profit_tags.json")
    log = json.load(open(fp)) if os.path.exists(fp) else {}
    now = dt.datetime.now(dt.timezone.utc)
    starts = _starts()
    for sport in ("nfl", "nhl"):
        try:
            FL = json.load(open(os.path.join(site, "markets", f"flags_{sport}.json")))
            PR = json.load(open(os.path.join(site, "markets", f"props_{sport}.json")))
        except (OSError, ValueError):
            continue
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
                g = PL.nhl_game(e["gid"]) if sport == "nhl" else PL.nfl_game(e["gid"], PL.et_date(e["start"]))
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
    c = None if clv is None else np.asarray([x for x in clv if x is not None], float)
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


def page_data():
    rows = []                                          # (sport, category, date, pnl, clv)
    for f in glob.glob(os.path.join(OUT, "profit_*.json")):
        if f.endswith("profit_tags.json"):
            continue
        for r in json.load(open(f)).get("rows", []):
            rows.append(dict(sport=r["sport"], cat=f"{r['cat']} ({'Kalshi' if r['src'] == 'kalshi' else 'Polymarket'})", date=r["date"], pnl=r["pnl"], clv=r.get("clv")))
    tp = os.path.join(OUT, "profit_tags.json")
    for e in (json.load(open(tp)) if os.path.exists(tp) else {}).values():
        if e.get("res") not in ("W", "L"):
            continue
        d_ = dec(e["o"])
        clv = (1 / dec(e["close"]) - 1 / d_) if e.get("close") is not None else None
        rows.append(dict(sport=e["sport"], cat=f"{e['sport'].upper()} Value tags", date=(e.get("start") or e["first"])[:10],
                         pnl=(d_ - 1) if e["res"] == "W" else -1.0, clv=clv))
    for f in glob.glob(os.path.join(HERE, "parlays", "*", "*.json")):
        d = json.load(open(f))
        for p in d.get("parlays", []):
            if p.get("res") not in ("W", "L"):
                continue
            pay = p.get("paid") or p.get("d")
            if not pay:
                continue
            rows.append(dict(sport=d["sport"], cat=f"{d['sport'].upper()} parlays: {p['card']}", date=d["date"],
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
    return dict(overall=dict(**o, roi30=o30["roi"] if o30 else None, n30=o30["n"] if o30 else 0), sports=sports)


PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Profit</title>__FONTS__<style>__CSS__
.rec{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:6px 0 14px}
.rec div{background:var(--sunk);border-radius:8px;padding:10px 12px}.rec b{display:block;font-family:var(--display);font-size:26px;line-height:1.1}.rec span{font-size:12px;color:var(--muted)}.rec b span{font-size:inherit}.rec b span.rp{color:var(--good)}.rec b span.rn{color:var(--bad)}
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
function render(){
  const O = D.overall;
  if (!O){ document.getElementById('main').innerHTML = '<section class="panel"><p class="note">Nothing graded yet.</p></section>'; return; }
  const tiles = `<div class="rec"><div><b>${un(O.units)}</b><span>${O.n} bets · all sports</span></div><div><b>${pc(O.roi)}</b><span>return per bet ± ${O.moe != null ? (O.moe * 100).toFixed(1) : '–'}%</span></div><div><b>${pc(O.roi30)}</b><span>last 30 days (${O.n30} bets)</span></div><div><b>${D.sports.reduce((n, s) => n + s.cats.filter(c => c.verdict === 'proven').length, 0)}</b><span>proven profitable bet types</span></div></div>`;
  const sp = s => `<section class="panel"><div class="sph"><h2>${s.sport.toUpperCase()}</h2><span>${un(s.units)} · ${pc(s.roi)} per bet · ${s.n} bets</span></div>${spark(s.curve)}
    <div class="tw"><table class="t pf"><thead><tr><th class="l">Bet type</th><th>Verdict</th><th>Record</th><th>Return</th><th>Beat close</th><th>Last 30 d</th></tr></thead><tbody>
    ${s.cats.map(c => `<tr><td class="l"><b>${esc(c.cat)}</b><small>${c.n} bets · ${un(c.units)}</small></td><td>${vd(c.verdict)}</td><td>${c.won}-${c.n - c.won}</td>
      <td>${pc(c.roi)}<small>± ${c.moe != null ? (c.moe * 100).toFixed(0) : '–'}%</small></td><td>${c.clv != null ? `${pc(c.clv, 1)}<small>${Math.round(c.clv_beat * 100)}% of ${c.clv_n}</small>` : '<span class="muted">–</span>'}</td>
      <td>${c.n30 ? pc(c.roi30, 0) + `<small>${c.n30} bets</small>` : '<span class="muted">–</span>'}</td></tr>`).join('')}</tbody></table></div></section>`;
  document.getElementById('main').innerHTML = `<section class="panel"><div class="phead"><h2>Overall</h2></div>${tiles}</section>` + D.sports.map(sp).join('');
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
