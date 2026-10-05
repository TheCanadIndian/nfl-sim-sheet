#!/usr/bin/env python3
"""
Prediction-market prices and liquidity (Kalshi public market data) matched to our projections.

    python markets.py nfl | nhl | nba        # current slate for that sport

For every open Kalshi market we also project (game winner, total, anytime / first scorer, player
yards / receptions / points / rebounds / assists / threes) we record:
  market:    yes bid / ask, last price, top-of-book sizes, open interest, 24h and total volume
  ours:      our chance for the same outcome (both models where we have both)
  edge:      our chance minus the price you'd pay (after Kalshi's taker fee), for YES and NO
  depth:     dollars available within 3 cents of the best ask (order book), for markets we flag
Writes markets/<sport>_<date>.json (the page reads it; later snapshots of the same day replace
earlier ones for games that haven't started, so the last pregame price is what gets graded).
"""

import datetime as dt
import glob
import json
import math
import os
import re
import sqlite3
import sys
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd
import requests

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "markets")
API = "https://api.elections.kalshi.com/trade-api/v2"
H = {"User-Agent": "Mozilla/5.0 (personal sports projections)"}
FEE = 0.07          # Kalshi taker fee ~ 0.07 x price x (1 - price) per $1 contract

SERIES = {
    "nfl": {"KXNFLGAME": "win", "KXNFLTOTAL": "total", "KXNFLANYTD": "anytime_td", "KXNFLFIRSTTD": "first_td",
            "KXNFLRECYDS": "rec_yds", "KXNFLRSHYDS": "rush_yds", "KXNFLPASSYDS": "pass_yds", "KXNFLREC": "rec"},
    "nhl": {"KXNHLGAME": "win", "KXNHLTOTAL": "total", "KXNHLGOAL": "goal", "KXNHLFIRSTGOAL": "first_goal"},
    "nba": {"KXNBAGAME": "win", "KXNBATOTAL": "total", "KXNBAPTS": "pts", "KXNBAREB": "reb", "KXNBAAST": "ast", "KXNBA3PT": "fg3m"},
}
# Kalshi team codes that differ from ours (ours: nflverse / NHL API / ESPN)
ALIAS = {
    "nfl": {"LAR": "LA", "JAC": "JAX", "WSH": "WAS", "LVR": "LV"},
    "nhl": {"SJ": "SJS", "LA": "LAK", "TB": "TBL", "NJ": "NJD", "VGS": "VGK", "LV": "VGK", "MON": "MTL", "UTAH": "UTA", "WAS": "WSH", "CLB": "CBJ"},
    "nba": {"NYK": "NY", "GSW": "GS", "SAS": "SA", "NOP": "NO", "UTA": "UTAH", "WAS": "WSH", "PHO": "PHX", "BRK": "BKN", "CHO": "CHA"},
}
MON = {m: i for i, m in enumerate(["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], 1)}


def norm(s):
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"\b(jr|sr|ii|iii|iv)\b\.?", "", s)
    return re.sub(r"[^a-z]", "", s)


def fnum(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def fetch_series(ticker):
    out, cursor = [], None
    for _ in range(20):
        p = {"series_ticker": ticker, "status": "open", "limit": 1000}
        if cursor:
            p["cursor"] = cursor
        try:
            d = requests.get(f"{API}/markets", params=p, headers=H, timeout=30).json()
        except (requests.RequestException, ValueError):
            break
        out += d.get("markets", [])
        cursor = d.get("cursor")
        if not cursor:
            break
    return out


def depth(ticker, best_ask, width=0.03):
    """Dollars you could spend buying YES within `width` of the best ask (YES asks are the
    complement of NO bids in Kalshi's book)."""
    try:
        ob = requests.get(f"{API}/markets/{ticker}/orderbook", headers=H, timeout=20).json().get("orderbook_fp") or {}
    except (requests.RequestException, ValueError):
        return None
    tot = 0.0
    for px, qty in ob.get("no_dollars") or []:
        ask = 1 - float(px)
        if best_ask is not None and ask <= best_ask + width + 1e-9:
            tot += ask * float(qty)
    return round(tot, 2)


LONGSHOT_KINDS = {"first_td", "first_goal", "anytime_td", "goal"}


def is_longshot(r):
    """High-variance markets: scorer markets and any YES priced 25 cents or less (ladder tails)."""
    return r["kind"] in LONGSHOT_KINDS or (r.get("yes_ask") or 1) <= 0.25


def book(ticker):
    """Full order book -> liquidity summary. YES bids are resting buy orders for YES; YES asks are
    the complement of NO bids. Dollars = price x contracts."""
    ob = None
    for i in range(5):                       # public API rate-limits bursts: back off and retry
        try:
            r = requests.get(f"{API}/markets/{ticker}/orderbook", headers=H, timeout=20)
            if r.status_code == 200:
                ob = r.json().get("orderbook_fp") or {}
                break
        except (requests.RequestException, ValueError):
            pass
        time.sleep(0.6 * (i + 1))
    if ob is None:
        return None
    bids = sorted(((float(p), float(q)) for p, q in ob.get("yes_dollars") or []), reverse=True)        # buy YES at p
    asks = sorted(((round(1 - float(p), 4), float(q)) for p, q in ob.get("no_dollars") or []))        # sell YES at p
    if not bids and not asks:
        return dict(empty=True)
    ba, bb = (asks[0][0] if asks else None), (bids[0][0] if bids else None)
    within = lambda lv, ref, w, up: round(sum(p * q for p, q in lv if ref is not None and (p <= ref + w + 1e-9 if up else p >= ref - w - 1e-9)), 2)
    return dict(best_ask=ba, best_bid=bb, spread=None if ba is None or bb is None else round(ba - bb, 4),
                ask_1c=within(asks, ba, .01, True), ask_3c=within(asks, ba, .03, True), ask_5c=within(asks, ba, .05, True),
                bid_1c=within(bids, bb, .01, False), bid_3c=within(bids, bb, .03, False),
                ask_total=round(sum(p * q for p, q in asks), 2), bid_total=round(sum(p * q for p, q in bids), 2),
                asks=[[p, round(q)] for p, q in asks[:5]], bids=[[p, round(q)] for p, q in bids[:5]])


def event_parts(ev):
    """KXNFLGAME-26OCT12BUFLAR -> (date, 'BUFLAR')."""
    m = re.match(r"[A-Z0-9]+-(\d{2})([A-Z]{3})(\d{2})([A-Z]+)$", ev)
    if not m:
        return None, None
    y, mon, d, teams = m.groups()
    return dt.date(2000 + int(y), MON[mon], int(d)), teams


def game_key(sport, teams, games):
    """Match Kalshi's AWAYHOME code string to one of our games (away, home) that day."""
    al = ALIAS[sport]
    inv = {}
    for k, v in al.items():
        inv.setdefault(v, set()).add(k)
    for g in games:
        for a in {g["away"]} | inv.get(g["away"], set()):
            for h in {g["home"]} | inv.get(g["home"], set()):
                if teams == a + h:
                    return g
    return None


def team_of_code(sport, code, g):
    c = ALIAS[sport].get(code, code)
    return c if c in (g["home"], g["away"]) else None


# ------------------------------------------------------------------ our numbers per sport

def p_over(d, line):
    """P(X > line) from a saved distribution: {'o','p'} pmf or {'q'} quantiles."""
    if not d:
        return None
    if "p" in d:
        k = d["o"] + np.arange(len(d["p"]))
        return float(np.asarray(d["p"])[k > line].sum())
    q = np.asarray(d["q"])
    return float((q > line).mean())


def ours_nfl():
    """Current NFL week: games, player distributions (main + blind), TD chances."""
    stems = sorted(glob.glob(os.path.join(HERE, "projections", "20??_wk??_players.csv")))
    if not stems:
        return None
    stem = stems[-1][:-len("_players.csv")]
    con = sqlite3.connect(os.path.join(HERE, "nfl.db"))
    sched = pd.read_sql("SELECT game_id, gameday, home_team, away_team FROM games", con).set_index("game_id")
    models = {}
    for name, st in (("vegas", stem), ("blind", os.path.join(os.path.dirname(stem), "blind", os.path.basename(stem)))):
        if not os.path.exists(st + "_players.csv"):
            continue
        models[name] = dict(players=pd.read_csv(st + "_players.csv"), teams=pd.read_csv(st + "_teams.csv"),
                            dist=json.load(open(st + "_dist.json")) if os.path.exists(st + "_dist.json") else {})
    games = []
    for gid in models["vegas"]["teams"].game_id.unique():
        if gid in sched.index:
            r = sched.loc[gid]
            games.append(dict(id=gid, date=str(r.gameday)[:10], home=r.home_team, away=r.away_team))
    return dict(models=models, games=games)


def ours_nhl():
    fs = sorted(glob.glob(os.path.join(HERE, "hockey", "projections", "????-??-??.json")))
    if not fs:
        return None
    js = json.load(open(fs[-1]))
    date = os.path.basename(fs[-1])[:10]
    csv = pd.read_csv(fs[-1].replace(".json", ".csv"))
    games = [dict(id=int(g["id"]), date=date, home=g["home"]["team"], away=g["away"]["team"], ml=g.get("ml")) for g in js["games"]]
    return dict(games=games, players=csv)


def ours_nba():
    fs = sorted(glob.glob(os.path.join(HERE, "nba", "projections", "????-??-??.json")))
    if not fs:
        return None
    js = json.load(open(fs[-1]))
    games = [dict(id=int(g["id"]), date=js["date"], home=g["home"], away=g["away"], models=g["models"]) for g in js["games"]]
    return dict(games=games, players=js["players"])


def norm_cdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def our_prob(sport, kind, m, g, O):
    """Our chance(s) that this market resolves YES: {'vegas': p, 'blind': p} (blind may be missing)."""
    line = fnum(m.get("floor_strike"))
    title = m.get("title", "")
    name = title.split(":")[0].strip()
    code = m["ticker"].rsplit("-", 1)[-1]
    out = {}
    if sport == "nfl":
        for mod, M in O["models"].items():
            T = M["teams"][M["teams"].game_id == g["id"]].set_index("team")
            if kind == "win":
                t = team_of_code("nfl", code, g)
                if t and t in T.index:
                    out[mod] = float(T.loc[t, "win_prob"])
            elif kind == "total" and line is not None and len(T) == 2:
                mu = float(T.points.sum())
                out[mod] = 1 - norm_cdf((line - mu) / 13.5)          # NFL game totals: sd ~13.5 around the mean
            else:
                P = M["players"][M["players"].game_id == g["id"]]
                pm = P[P.player.map(norm) == norm(name)]
                if pm.empty:
                    continue
                pl = pm.iloc[0]
                key = f"{g['id']}|{pl.team}|{pl.player}"
                if kind == "anytime_td":
                    x = pm[pm.stat == "anytime_td"]
                    if len(x):
                        out[mod] = float(x["mean"].iloc[0])
                elif kind == "first_td":
                    x = pm[pm.stat == "first_td"]
                    if len(x):
                        out[mod] = float(x["mean"].iloc[0])
                elif line is not None:
                    p = p_over(M["dist"].get(key, {}).get(kind), line)
                    if p is not None:
                        out[mod] = p
    elif sport == "nhl":
        if kind == "win":
            t = team_of_code("nhl", code, g)
            if t and g.get("ml"):
                out["model"] = g["ml"]["home"] if t == g["home"] else g["ml"]["away"]
        elif kind == "total" and line is not None and g.get("ml"):
            p = g["ml"]["totals"].get(str(line))
            if p is not None:
                out["model"] = p
        else:
            P = O["players"][O["players"].game_id == g["id"]]
            pm = P[P.name.map(norm) == norm(name)]
            if len(pm):
                if kind == "goal" and (line is None or line < 1):
                    out["model"] = float(pm.p_goal.iloc[0])
                elif kind == "first_goal" and "p_first" in pm:
                    out["model"] = float(pm.p_first.iloc[0])
    else:  # nba
        if kind == "win":
            t = team_of_code("nba", code, g)
            for mod, v in (g.get("models") or {}).items():
                if t:
                    out[mod] = v["home_win"] if t == g["home"] else 1 - v["home_win"]
        elif kind == "total" and line is not None:
            for mod, v in (g.get("models") or {}).items():
                out[mod] = 1 - norm_cdf((line - (v["home_pts"] + v["away_pts"])) / 17.0)   # NBA totals sd ~17
        else:
            for p in O["players"]:
                if p["g"] == g["id"] and norm(p["n"]) == norm(name):
                    for mod in ("vegas", "blind"):
                        v = p_over((p.get("d") or {}).get(mod, {}).get(kind), line)
                        if v is not None:
                            out[mod] = v
                    break
    return out


def run(sport):
    O = {"nfl": ours_nfl, "nhl": ours_nhl, "nba": ours_nba}[sport]()
    if not O:
        print(f"{sport}: no projections on disk")
        return
    games = O["games"]
    with ThreadPoolExecutor(max_workers=4) as ex:
        allm = dict(zip(SERIES[sport], ex.map(fetch_series, SERIES[sport])))
    rows = []
    for series, ms in allm.items():
        kind = SERIES[sport][series]
        for m in ms:
            day, teams = event_parts(m.get("event_ticker", ""))
            if not day:
                continue
            g = game_key(sport, teams, [x for x in games if x["date"] == day.isoformat()])
            if not g:
                continue
            ours = our_prob(sport, kind, m, g, O)
            if not ours:
                continue
            ya, yb, na = fnum(m.get("yes_ask_dollars")), fnum(m.get("yes_bid_dollars")), fnum(m.get("no_ask_dollars"))
            p = ours.get("vegas", ours.get("model"))
            row = dict(ticker=m["ticker"], game=g["id"], away=g["away"], home=g["home"], kind=kind, title=m.get("title"),
                       line=fnum(m.get("floor_strike")), yes_bid=yb, yes_ask=ya, no_ask=na, last=fnum(m.get("last_price_dollars")),
                       ask_size=fnum(m.get("yes_ask_size_fp")), bid_size=fnum(m.get("yes_bid_size_fp")),
                       oi=fnum(m.get("open_interest_fp")), vol=fnum(m.get("volume_fp")), vol24=fnum(m.get("volume_24h_fp")),
                       close=m.get("close_time"), ours=ours)
            if p is not None and ya and 0 < ya < 1:
                row["edge_yes"] = round(p - ya - FEE * ya * (1 - ya), 4)
            if p is not None and na and 0 < na < 1:
                row["edge_no"] = round((1 - p) - na - FEE * na * (1 - na), 4)
            rows.append(row)
    # full order books for the high-variance markets and anything with a meaningful edge
    flag = [r for r in rows if is_longshot(r) or max(r.get("edge_yes", -1), r.get("edge_no", -1)) >= .03]
    for r in rows:
        r["longshot"] = is_longshot(r)
    with ThreadPoolExecutor(max_workers=3) as ex:
        for r, b in zip(flag, ex.map(lambda r: book(r["ticker"]), flag)):
            r["book"] = b
    os.makedirs(OUT, exist_ok=True)
    date = min((g["date"] for g in games), default=dt.date.today().isoformat())
    path = os.path.join(OUT, f"{sport}_{date}.json")
    snap = dict(sport=sport, fetched=dt.datetime.now().isoformat(timespec="minutes"), markets=rows)
    if os.path.exists(path):        # keep earlier snapshots of games that have closed since
        old = json.load(open(path))
        done = {r["game"] for r in rows}
        snap["markets"] = [r for r in old.get("markets", []) if r["game"] not in done] + rows
    json.dump(snap, open(path, "w"), separators=(",", ":"))
    n_edge = sum(1 for r in rows if max(r.get("edge_yes", -1), r.get("edge_no", -1)) >= .03)
    print(f"{sport}: {sum(len(v) for v in allm.values())} open Kalshi markets, {len(rows)} matched to our projections, "
          f"{n_edge} with a 3%+ edge -> {path}")


if __name__ == "__main__":
    for s in (sys.argv[1:] or ["nfl", "nhl", "nba"]):
        run(s)
