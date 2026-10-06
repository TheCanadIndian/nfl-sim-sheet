#!/usr/bin/env python3
"""
Prediction-market prices and liquidity (Kalshi and Polymarket public data) matched to our
projections, saved so the market can be graded against our models.

    python markets.py nfl | nhl | nba        # current slate for that sport
    python markets.py live                   # quick re-quote of every sport with a game in the next 12 h
                                             # (cloud, every 15 min; exit code 10 = nothing to do)

Kalshi: game winner, total, anytime / first scorers, player yards / receptions / points /
rebounds / assists / threes. Polymarket: game markets only (moneyline, spreads, totals).
For each matched market:
  market:  bid / ask for the YES outcome, sizes, volume, open interest (Kalshi, contracts) or
           liquidity / volume (Polymarket, dollars)
  ours:    our chance for the same outcome (both NFL/NBA models where available)
  edge:    our chance minus the price you'd pay, after the taker fee, for YES and NO
  book:    every matched market: dollars within 1/3/5 cents of the best ask and bid, totals,
           top levels; no_3c = dollars to buy NO (the under side) within 3 cents
Also writes <site>/markets/live_<sport>.json, which the model pages read for live prices.
Only games that haven't started are refreshed, so each game keeps its last PREGAME price
(markets trade during games too). markets/<sport>_<date>.json; graded by grade_markets.py.
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
SITE = os.environ.get("SIM_SITE") or os.path.join(HERE, "site")
KAL = "https://api.elections.kalshi.com/trade-api/v2"
GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
H = {"User-Agent": "Mozilla/5.0 (personal sports projections)"}
FEE = {"kalshi": 0.07, "polymarket": 0.0}      # taker fee coefficient x p x (1-p); Polymarket sports: none assumed
SD_TOTAL = {"nfl": 13.5, "nba": 17.0}          # game total spread around our mean (normal approx)
SD_MARGIN = {"nfl": 13.5, "nba": 12.5}

SERIES = {
    "nfl": {"KXNFLGAME": "win", "KXNFLTOTAL": "total", "KXNFLTD": "anytime_td", "KXNFLFIRSTTD": "first_td",
            "KXNFLRECYDS": "rec_yds", "KXNFLRSHYDS": "rush_yds", "KXNFLPASSYDS": "pass_yds", "KXNFLREC": "rec"},
    "nhl": {"KXNHLGAME": "win", "KXNHLTOTAL": "total", "KXNHLGOAL": "goal", "KXNHLFIRSTGOAL": "first_goal"},
    "nba": {"KXNBAGAME": "win", "KXNBATOTAL": "total", "KXNBAPTS": "pts", "KXNBAREB": "reb", "KXNBAAST": "ast", "KXNBA3PT": "fg3m"},
}
POLY_SERIES = {"nfl": 12185, "nhl": 10346, "nba": 10345}
POLY_KINDS = {"moneyline": "win", "totals": "total", "spreads": "spread"}
ALIAS = {   # market team codes that differ from ours (nflverse / NHL API / ESPN)
    "nfl": {"LAR": "LA", "JAC": "JAX", "WSH": "WAS", "LVR": "LV"},
    "nhl": {"SJ": "SJS", "LA": "LAK", "TB": "TBL", "NJ": "NJD", "VGS": "VGK", "LV": "VGK", "MON": "MTL", "UTAH": "UTA", "WAS": "WSH", "CLB": "CBJ"},
    "nba": {"NYK": "NY", "GSW": "GS", "SAS": "SA", "NOP": "NO", "UTA": "UTAH", "WAS": "WSH", "PHO": "PHX", "BRK": "BKN", "CHO": "CHA"},
}
MON = {m: i for i, m in enumerate(["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], 1)}
LONGSHOT_KINDS = {"first_td", "first_goal", "anytime_td", "goal"}


def norm(s):
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"\b(jr|sr|ii|iii|iv)\b\.?", "", s)
    return re.sub(r"[^a-z]", "", s)


def fnum(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def norm_cdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def get(url, params=None, tries=5):
    for i in range(tries):                      # public APIs rate-limit bursts: back off and retry
        try:
            r = requests.get(url, params=params, headers=H, timeout=25)
            if r.status_code == 200:
                return r.json()
        except (requests.RequestException, ValueError):
            pass
        time.sleep(0.6 * (i + 1))
    return None


def code(sport, c):
    c = c.upper()
    return ALIAS[sport].get(c, c)


# ------------------------------------------------------------------ order books

def summarize_book(bids, asks):
    """bids/asks: [(price, contracts)] for the YES outcome. Dollars = price x contracts."""
    bids, asks = sorted(bids, reverse=True), sorted(asks)
    if not bids and not asks:
        return dict(empty=True)
    ba, bb = (asks[0][0] if asks else None), (bids[0][0] if bids else None)

    def within(lv, ref, w, up):
        return round(sum(p * q for p, q in lv if ref is not None and (p <= ref + w + 1e-9 if up else p >= ref - w - 1e-9)), 2)
    return dict(best_ask=ba, best_bid=bb, spread=None if ba is None or bb is None else round(ba - bb, 4),
                ask_1c=within(asks, ba, .01, True), ask_3c=within(asks, ba, .03, True), ask_5c=within(asks, ba, .05, True),
                bid_1c=within(bids, bb, .01, False), bid_3c=within(bids, bb, .03, False),
                no_3c=round(sum((1 - p) * q for p, q in bids if bb is not None and p >= bb - .03 - 1e-9), 2),   # cost of buying NO
                ask_total=round(sum(p * q for p, q in asks), 2), bid_total=round(sum(p * q for p, q in bids), 2),
                asks=[[p, round(q)] for p, q in asks[:5]], bids=[[p, round(q)] for p, q in bids[:5]])


def kalshi_book(ticker):
    d = get(f"{KAL}/markets/{ticker}/orderbook")
    if d is None:
        return None
    ob = d.get("orderbook_fp") or {}
    bids = [(float(p), float(q)) for p, q in ob.get("yes_dollars") or []]
    asks = [(round(1 - float(p), 4), float(q)) for p, q in ob.get("no_dollars") or []]   # YES asks = complement of NO bids
    return summarize_book(bids, asks)


def poly_book(token):
    d = get(f"{CLOB}/book", {"token_id": token})
    if d is None:
        return None
    return summarize_book([(float(x["price"]), float(x["size"])) for x in d.get("bids") or []],
                          [(float(x["price"]), float(x["size"])) for x in d.get("asks") or []])


# ------------------------------------------------------------------ our numbers per sport

def p_over(d, line):
    if not d:
        return None
    if "p" in d:
        k = d["o"] + np.arange(len(d["p"]))
        return float(np.asarray(d["p"])[k > line].sum())
    return float((np.asarray(d["q"]) > line).mean())


def ours_nfl(stem=None):
    if stem is None:
        stems = sorted(glob.glob(os.path.join(HERE, "projections", "20??_wk??_players.csv")))
        if not stems:
            return None
        stem = stems[-1][:-len("_players.csv")]
    dbp = os.path.join(HERE, "nfl.db")
    if os.path.exists(dbp):
        con = sqlite3.connect(dbp)
        sched = pd.read_sql("SELECT game_id, gameday, gametime, home_team, away_team FROM games", con).set_index("game_id")
    else:                                                  # quick cloud re-quote: kickoffs from the published page
        sched = nfl_sched_from_page()
    models = {}
    d = os.path.dirname(stem)
    bstem = (os.path.join(os.path.dirname(d), "blind", "weeks", os.path.basename(stem)) if os.path.basename(d) == "weeks"
             else os.path.join(d, "blind", os.path.basename(stem)))
    for name, st in (("vegas", stem), ("blind", bstem)):
        if os.path.exists(st + "_players.csv"):
            models[name] = dict(players=pd.read_csv(st + "_players.csv"), teams=pd.read_csv(st + "_teams.csv"),
                                dist=json.load(open(st + "_dist.json")) if os.path.exists(st + "_dist.json") else {})
    games = []
    for gid in models["vegas"]["teams"].game_id.unique():
        if gid in sched.index:
            r = sched.loc[gid]
            start = pd.Timestamp(f"{r.gameday} {r.gametime or '13:00'}", tz="America/New_York")
            games.append(dict(id=gid, date=str(r.gameday)[:10], home=r.home_team, away=r.away_team, start=start))
    return dict(models=models, games=games)


def nfl_sched_from_page():
    pg = os.path.join(SITE, "nfl", "index.html")
    m = re.search(r'<script type="application/json" id="data">(.*?)</script>', open(pg, encoding="utf-8").read(), re.S) if os.path.exists(pg) else None
    games = json.loads(m.group(1).replace(r"<\/", "</"))["games"] if m else []
    return pd.DataFrame([dict(game_id=g["id"], gameday=g["sort"][:10], gametime=g["sort"][11:16], home_team=g["home"], away_team=g["away"])
                         for g in games], columns=["game_id", "gameday", "gametime", "home_team", "away_team"]).set_index("game_id")


def ours_nhl(path=None):
    fs = [path] if path else sorted(glob.glob(os.path.join(HERE, "hockey", "projections", "????-??-??.json")))
    if not fs:
        return None
    js = json.load(open(fs[-1]))
    date = os.path.basename(fs[-1])[:10]
    games = [dict(id=int(g["id"]), date=date, home=g["home"]["team"], away=g["away"]["team"], ml=g.get("ml"),
                  start=pd.Timestamp(g["start_utc"])) for g in js["games"]]
    return dict(games=games, players=pd.read_csv(fs[-1].replace(".json", ".csv")))


def ours_nba():
    fs = sorted(glob.glob(os.path.join(HERE, "nba", "projections", "????-??-??.json")))
    if not fs:
        return None
    js = json.load(open(fs[-1]))
    games = [dict(id=int(g["id"]), date=js["date"], home=g["home"], away=g["away"], models=g["models"],
                  start=pd.Timestamp(g["start_utc"])) for g in js["games"]]
    return dict(games=games, players=js["players"])


def team_view(sport, g, O):
    """{model: (home_pts, away_pts, home_win)} for game-level markets."""
    out = {}
    if sport == "nfl":
        for mod, M in O["models"].items():
            T = M["teams"][M["teams"].game_id == g["id"]].set_index("team")
            if g["home"] in T.index and g["away"] in T.index:
                out[mod] = (float(T.loc[g["home"], "points"]), float(T.loc[g["away"], "points"]), float(T.loc[g["home"], "win_prob"]))
    elif sport == "nba":
        for mod, v in (g.get("models") or {}).items():
            out[mod] = (v["home_pts"], v["away_pts"], v["home_win"])
    return out


def game_prob(sport, kind, g, O, team=None, line=None, over=True):
    """Our chance for a game-level outcome: team wins / covers `line` (team's handicap), or total over `line`."""
    out = {}
    if sport == "nhl":
        ml = g.get("ml")
        if not ml:
            return out
        if kind == "win" and team:
            out["model"] = ml["home"] if team == g["home"] else ml["away"]
        elif kind == "total" and line is not None and str(line) in ml["totals"]:
            p = ml["totals"][str(line)]
            out["model"] = p if over else 1 - p
        elif kind == "spread" and team and line is not None and abs(abs(line) - 1.5) < 1e-9:
            home = team == g["home"]
            if line < 0:
                out["model"] = ml["home_pl"] if home else ml["away_pl"]
            else:
                out["model"] = 1 - (ml["away_pl"] if home else ml["home_pl"])
        return out
    for mod, (hp, ap, hw) in team_view(sport, g, O).items():
        if kind == "win" and team:
            out[mod] = hw if team == g["home"] else 1 - hw
        elif kind == "total" and line is not None:
            p = 1 - norm_cdf((line - (hp + ap)) / SD_TOTAL[sport])
            out[mod] = p if over else 1 - p
        elif kind == "spread" and team and line is not None:
            m = (hp - ap) if team == g["home"] else (ap - hp)        # this team's expected margin
            out[mod] = 1 - norm_cdf((-line - m) / SD_MARGIN[sport])   # covers: margin > -line
    return out


def player_prob(sport, kind, name, line, g, O):
    """({model: p}, player_id) for a player market."""
    out, pid = {}, None
    if sport == "nfl":
        for mod, M in O["models"].items():
            P = M["players"][M["players"].game_id == g["id"]]
            pm = P[P.player.map(norm) == norm(name)]
            if pm.empty:
                continue
            pl = pm.iloc[0]
            pid = pl.player_id
            if kind in ("anytime_td", "first_td"):
                x = pm[pm.stat == kind]
                if len(x):
                    out[mod] = float(x["mean"].iloc[0])
            elif line is not None:
                p = p_over(M["dist"].get(f"{g['id']}|{pl.team}|{pl.player}", {}).get(kind), line)
                if p is not None:
                    out[mod] = p
    elif sport == "nhl":
        P = O["players"][O["players"].game_id == g["id"]]
        pm = P[P.name.map(norm) == norm(name)]
        if len(pm):
            pid = int(pm.player_id.iloc[0])
            if kind == "goal" and (line is None or line < 1):
                out["model"] = float(pm.p_goal.iloc[0])
            elif kind == "first_goal" and "p_first" in pm:
                out["model"] = float(pm.p_first.iloc[0])
    else:
        for p in O["players"]:
            if p["g"] == g["id"] and norm(p["n"]) == norm(name):
                pid = p["id"]
                for mod in ("vegas", "blind"):
                    v = p_over((p.get("d") or {}).get(mod, {}).get(kind), line)
                    if v is not None:
                        out[mod] = v
                break
    return out, pid


def make_row(source, sport, g, kind, ticker, title, line, ours, yb, ya, extra):
    p = ours.get("vegas", ours.get("model"))
    row = dict(source=source, ticker=ticker, game=g["id"], away=g["away"], home=g["home"], kind=kind, title=title,
               line=line, yes_bid=yb, yes_ask=ya, ours=ours, **extra)
    f = FEE[source]
    na = None if yb is None else round(1 - yb, 4)          # buying NO = selling YES at the bid
    row["no_ask"] = na
    if p is not None and ya and 0 < ya < 1:
        row["edge_yes"] = round(p - ya - f * ya * (1 - ya), 4)
    if p is not None and na and 0 < na < 1:
        row["edge_no"] = round((1 - p) - na - f * na * (1 - na), 4)
    row["longshot"] = kind in LONGSHOT_KINDS or (ya or 1) <= 0.25
    return row


# ------------------------------------------------------------------ Kalshi

def event_parts(ev):
    m = re.match(r"[A-Z0-9]+-(\d{2})([A-Z]{3})(\d{2})([A-Z]+)$", ev)
    if not m:
        return None, None
    y, mon, d, teams = m.groups()
    return dt.date(2000 + int(y), MON[mon], int(d)), teams


def kalshi_game(sport, teams, games):
    inv = {}
    for k, v in ALIAS[sport].items():
        inv.setdefault(v, set()).add(k)
    for g in games:
        for a in {g["away"]} | inv.get(g["away"], set()):
            for h in {g["home"]} | inv.get(g["home"], set()):
                if teams == a + h:
                    return g
    return None


def kalshi_series(ticker, status="open", window=None):
    out, cursor = [], None
    for _ in range(40):
        p = {"series_ticker": ticker, "status": status, "limit": 1000}
        if window:
            p["min_close_ts"], p["max_close_ts"] = window
        if cursor:
            p["cursor"] = cursor
        d = get(f"{KAL}/markets", p)
        if not d:
            break
        out += d.get("markets", [])
        cursor = d.get("cursor")
        if not cursor:
            break
    return out


def kalshi_rows(sport, O, games, settled=None, kinds=None):
    """settled=(min_close_ts, max_close_ts): use settled markets (backfill) instead of open ones."""
    series = [s for s in SERIES[sport] if kinds is None or SERIES[sport][s] in kinds]
    with ThreadPoolExecutor(max_workers=4) as ex:
        allm = dict(zip(series, ex.map(lambda t: kalshi_series(t, "settled" if settled else "open", settled), series)))
    rows, n = [], 0
    for series, ms in allm.items():
        kind = SERIES[sport][series]
        n += len(ms)
        for m in ms:
            if kind == "anytime_td" and not m["ticker"].endswith("-1"):
                continue                                    # KXNFLTD is a 1+/2+/3+ ladder; anytime = the 1+ rung
            day, teams = event_parts(m.get("event_ticker", ""))
            if not day:
                continue
            g = kalshi_game(sport, teams, [x for x in games if x["date"] == day.isoformat()])
            if not g:
                continue
            line = fnum(m.get("floor_strike"))
            pid = None
            if kind in ("win", "total"):
                team = code(sport, m["ticker"].rsplit("-", 1)[-1]) if kind == "win" else None
                ours = game_prob(sport, kind, g, O, team=team if team in (g["home"], g["away"]) else None, line=line)
            else:
                ours, pid = player_prob(sport, kind, m.get("title", "").split(":")[0].strip(), line, g, O)
            if not ours:
                continue
            rows.append(make_row("kalshi", sport, g, kind, m["ticker"], m.get("title"), line, ours,
                                 fnum(m.get("yes_bid_dollars")), fnum(m.get("yes_ask_dollars")),
                                 dict(pid=None if pid is None else str(pid), last=fnum(m.get("last_price_dollars")),
                                      ask_size=fnum(m.get("yes_ask_size_fp")), bid_size=fnum(m.get("yes_bid_size_fp")),
                                      oi=fnum(m.get("open_interest_fp")), vol=fnum(m.get("volume_fp")),
                                      vol24=fnum(m.get("volume_24h_fp")), units="contracts")))
    return rows, n


# ------------------------------------------------------------------ Polymarket

def poly_rows(sport, O, games):
    evs, off = [], 0
    while off < 1000:
        d = get(f"{GAMMA}/events", {"series_id": POLY_SERIES[sport], "closed": "false", "limit": 100, "offset": off})
        if not d:
            break
        evs += d
        if len(d) < 100:
            break
        off += 100
    rows, n = [], 0
    for e in evs:
        m = re.match(r"[a-z]+-([a-z]+)-([a-z]+)-(\d{4}-\d{2}-\d{2})", e.get("slug", ""))
        if not m:
            continue
        a, h, day = code(sport, m.group(1)), code(sport, m.group(2)), m.group(3)
        d0 = dt.date.fromisoformat(day)           # slug dates can be the UTC date (late games roll over)
        g = next((x for x in games if x["away"] == a and x["home"] == h
                  and abs((dt.date.fromisoformat(x["date"]) - d0).days) <= 1), None)
        if not g:
            continue
        mkts = e.get("markets", [])
        n += len(mkts)
        ml = next((x for x in mkts if x.get("sportsMarketType") == "moneyline"), None)
        nick = {}
        if ml:
            o = json.loads(ml.get("outcomes") or "[]")
            if len(o) == 2:
                nick = {o[0]: g["away"], o[1]: g["home"]}
        for x in mkts:
            kind = POLY_KINDS.get(x.get("sportsMarketType"))
            if not kind or x.get("closed"):
                continue
            outs = json.loads(x.get("outcomes") or "[]")
            toks = json.loads(x.get("clobTokenIds") or "[]")
            if len(outs) != 2 or len(toks) != 2:
                continue
            line = fnum(x.get("line"))
            if kind == "win":
                team = nick.get(outs[0])
                ours = game_prob(sport, kind, g, O, team=team)
                title = f"{outs[0]} win ({team})"
            elif kind == "total":
                ours = game_prob(sport, kind, g, O, line=line, over=outs[0].lower().startswith("over"))
                title = f"{x.get('question')}: {outs[0]}"
            else:
                team = nick.get(outs[0])
                ours = game_prob(sport, kind, g, O, team=team, line=line)
                title = f"{outs[0]} {line:+g}" if line is not None else x.get("question")
            if not ours:
                continue
            rows.append(make_row("polymarket", sport, g, kind, toks[0], title, line, ours, fnum(x.get("bestBid")), fnum(x.get("bestAsk")),
                                 dict(liq=fnum(x.get("liquidityNum")), vol=fnum(x.get("volumeNum")), vol24=fnum(x.get("volume24hr")),
                                      last=fnum(x.get("lastTradePrice")), units="dollars", outcome=outs[0],
                                      team_side=None if kind == "total" else ("home" if nick.get(outs[0]) == g["home"] else "away" if nick.get(outs[0]) == g["away"] else None))))
    return rows, n


# ------------------------------------------------------------------ main

def run(sport):
    O = {"nfl": ours_nfl, "nhl": ours_nhl, "nba": ours_nba}[sport]()
    if not O:
        print(f"{sport}: no projections on disk")
        return
    now = pd.Timestamp.now(tz="UTC")
    upcoming = [g for g in O["games"] if pd.Timestamp(g["start"]).tz_convert("UTC") > now]
    krows, kn = kalshi_rows(sport, O, upcoming)
    prows, pn = poly_rows(sport, O, upcoming)
    rows = krows + prows
    flag = rows                                             # every market: the pages show both sides' liquidity
    with ThreadPoolExecutor(max_workers=4) as ex:
        books = ex.map(lambda r: kalshi_book(r["ticker"]) if r["source"] == "kalshi" else poly_book(r["ticker"]), flag)
        for r, b in zip(flag, books):
            r["book"] = b
    for g in upcoming:
        for r in rows:
            if r["game"] == g["id"]:
                r["start"] = pd.Timestamp(g["start"]).tz_convert("UTC").isoformat()
    os.makedirs(OUT, exist_ok=True)
    date = min((g["date"] for g in O["games"]), default=dt.date.today().isoformat())
    path = os.path.join(OUT, f"{sport}_{date}.json")
    fresh = {g["id"] for g in upcoming}
    old = json.load(open(path)).get("markets", []) if os.path.exists(path) else []
    keep = [r for r in old if r["game"] not in fresh]          # started games keep their last pregame snapshot
    snap = dict(sport=sport, fetched=dt.datetime.now().isoformat(timespec="minutes"), markets=keep + rows)
    if TAPE:                                                # cloud markets job: keep every snapshot (tape.py)
        try:
            import tape
            tape.record(sport, rows, dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"))
        except Exception as e:
            print("tape skipped:", type(e).__name__, e)
    json.dump(snap, open(path, "w"), separators=(",", ":"), default=str)
    e3 = sum(1 for r in rows if max(r.get("edge_yes", -1), r.get("edge_no", -1)) >= .03)
    print(f"{sport}: Kalshi {kn} open / {len(krows)} matched, Polymarket {pn} / {len(prows)} matched, "
          f"{len(upcoming)} games not started, {e3} with a 3%+ edge -> {path}")
    write_live(sport)


def weak_kinds(sport):
    """Market types where the market price has beaten our model on settled games (shown as 'market ahead')."""
    gp = os.path.join(OUT, "grades.json")
    if not os.path.exists(gp):
        return []
    return sorted({g["kind"] for g in json.load(open(gp)).get("groups", [])
                   if g["sport"] == sport and g["kind"] != "all" and g["n"] >= 30 and g["model_ll"] > g["market_ll"] + .005})


def write_live(sport):
    """Compact copy of the latest pregame snapshot for the model pages: <site>/markets/live_<sport>.json."""
    fs = [f for f in sorted(glob.glob(os.path.join(OUT, f"{sport}_????-??-??.json")))
          if not str(json.load(open(f)).get("fetched", "")).startswith("backfill")]
    if not fs or not os.path.isdir(SITE):
        return
    snap = json.load(open(fs[-1]))
    rows = []
    tagger = None
    if sport == "nfl":                                     # prop patterns (patterns.py): same rules as the grader
        try:
            import patterns as PT
            low, ctxs = PT.ladders(snap.get("markets", [])), {}

            def tagger(r):
                stem = PT.week_tables(r["game"])
                if stem is None:
                    return []
                if stem not in ctxs:
                    ctxs[stem] = PT.context(pd.read_csv(stem + "_players.csv"), pd.read_csv(stem + "_teams.csv"))
                yb, ya = r.get("yes_bid"), r.get("yes_ask")
                mid = (yb + ya) / 2 if yb and ya else ya
                return PT.tag_nfl(r["kind"], r.get("line"), mid, r.get("pid"), r["game"], ctxs[stem], low.get((r["game"], str(r.get("pid")), r["kind"])))
        except Exception as e:
            print("patterns skipped:", type(e).__name__, e)
    for r in snap.get("markets", []):
        b = r.get("book") or {}
        if b.get("empty"):
            by = bn = 0
        else:
            by = b.get("ask_3c", (r.get("ask_size") or 0) * (r.get("yes_ask") or 0) if r.get("ask_size") is not None else None)
            bn = b.get("no_3c", (r.get("bid_size") or 0) * (1 - (r.get("yes_bid") or 0)) if r.get("bid_size") is not None else None)
        name = (r.get("title") or "").split(":")[0].strip() if r.get("pid") is not None else None
        rows.append(dict(s=r["source"], t=r["ticker"], g=r["game"], a=r.get("away"), h=r.get("home"), ls=bool(r.get("longshot")), k=r["kind"], l=r.get("line"), ti=r.get("title"), o=r.get("outcome"),
                         pid=r.get("pid"), n=name, yb=r.get("yes_bid"), ya=r.get("yes_ask"), p=r["ours"],
                         by=None if by is None else round(by), bn=None if bn is None else round(bn), start=r.get("start")))
        if tagger:
            pats = tagger(r)
            if pats:
                rows[-1]["pat"] = pats
    os.makedirs(os.path.join(SITE, "markets"), exist_ok=True)
    f = snap.get("fetched")
    try:
        f = dt.datetime.fromisoformat(f).astimezone().isoformat(timespec="minutes")    # naive local time -> with offset
    except (TypeError, ValueError):
        pass
    pp = os.path.join(OUT, "patterns.json")
    extra = json.load(open(pp)) if sport == "nfl" and os.path.exists(pp) else {}
    json.dump(dict(sport=sport, fetched=f, weak=weak_kinds(sport), rows=rows, **extra),
              open(os.path.join(SITE, "markets", f"live_{sport}.json"), "w"), separators=(",", ":"), default=str)


TAPE = False


def live(hours=12):
    """Re-quote each sport that has a game starting within `hours`; 0 if anything ran, 10 if not.
    Also writes <site>/markets/schedule.json (upcoming starts) for the Cloudflare Worker's pacing."""
    global TAPE
    TAPE = True
    ran = False
    now = pd.Timestamp.now(tz="UTC")
    starts = []
    for sport, f in (("nfl", ours_nfl), ("nhl", ours_nhl), ("nba", ours_nba)):
        try:
            for g in (f() or {}).get("games", []):
                t = pd.Timestamp(g["start"]).tz_convert("UTC") if pd.Timestamp(g["start"]).tzinfo else pd.Timestamp(g["start"], tz="UTC")
                if now - pd.Timedelta(hours=4) < t < now + pd.Timedelta(days=8):
                    starts.append(dict(sport=sport, game=str(g["id"]), start=t.isoformat()))
        except Exception:
            pass
    if os.path.isdir(SITE):
        os.makedirs(os.path.join(SITE, "markets"), exist_ok=True)
        json.dump(dict(games=sorted(starts, key=lambda x: x["start"])),        # no timestamp: only changes when games do
                  open(os.path.join(SITE, "markets", "schedule.json"), "w"), separators=(",", ":"))
    for sport, f in (("nfl", ours_nfl), ("nhl", ours_nhl), ("nba", ours_nba)):
        try:
            O = f()
        except Exception as e:                             # one sport's missing files must not stop the others
            print(f"{sport}: skipped ({type(e).__name__}: {e})")
            continue
        soon = [g for g in (O or {}).get("games", []) if now < pd.Timestamp(g["start"]).tz_convert("UTC") <= now + pd.Timedelta(hours=hours)]
        if soon:
            run(sport)
            ran = True
        else:
            print(f"{sport}: no game in the next {hours} h")
    return 0 if ran else 10


def pregame_quote(series, ticker, start):
    """Last hourly bid/ask before `start` from Kalshi's price history."""
    st = int(pd.Timestamp(start).tz_convert("UTC").timestamp())
    d = get(f"{KAL}/series/{series}/markets/{ticker}/candlesticks", {"start_ts": st - 3 * 86400, "end_ts": st, "period_interval": 60})
    best = None
    for c in (d or {}).get("candlesticks", []):
        if c.get("end_period_ts", 0) <= st:
            b, a = fnum((c.get("yes_bid") or {}).get("close_dollars")), fnum((c.get("yes_ask") or {}).get("close_dollars"))
            if a and 0 < a < 1:
                best = (b, a)
    return best


def backfill(sport, kinds=None):
    """Past slates: settled Kalshi markets priced at the last hour before each game, vs our frozen
    pregame projections. Writes markets/<sport>_<date>.json for slates that don't have one yet."""
    if sport == "nhl":
        slates = [(os.path.basename(f)[:10], ours_nhl(f)) for f in sorted(glob.glob(os.path.join(HERE, "hockey", "projections", "????-??-??.json")))]
    else:
        slates = [(None, ours_nfl(f[:-len("_players.csv")])) for f in sorted(glob.glob(os.path.join(HERE, "projections", "weeks", "20??_wk??_players.csv")))]
    for date, O in slates:
        if not O or not O["games"]:
            continue
        date = date or min(g["date"] for g in O["games"])
        path = os.path.join(OUT, f"{sport}_{date}.json")
        if os.path.exists(path):
            continue
        starts = [pd.Timestamp(g["start"]).tz_convert("UTC") for g in O["games"]]
        if max(starts) > pd.Timestamp.now(tz="UTC"):
            continue                                        # slate not finished yet
        win = (int(min(starts).timestamp()), int(max(starts).timestamp()) + 4 * 86400)
        rows, n = kalshi_rows(sport, O, O["games"], settled=win, kinds=kinds)
        series_of = {k: s for s, k in SERIES[sport].items()}
        start_of = {g["id"]: g["start"] for g in O["games"]}

        def quote(r):
            return pregame_quote(series_of[r["kind"]], r["ticker"], start_of[r["game"]])
        with ThreadPoolExecutor(max_workers=3) as ex:
            qs = list(ex.map(quote, rows))
        out = []
        for r, q in zip(rows, qs):
            if not q:
                continue
            g = next(x for x in O["games"] if x["id"] == r["game"])
            nr = make_row("kalshi", sport, g, r["kind"], r["ticker"], r["title"], r["line"], r["ours"], q[0], q[1],
                          dict(pid=r.get("pid"), vol=r.get("vol"), oi=r.get("oi"), units="contracts", backfill=True))
            out.append(nr)
        if not out:
            continue                                        # no settled markets (e.g. before Kalshi listed them)
        os.makedirs(OUT, exist_ok=True)
        json.dump(dict(sport=sport, fetched="backfill (last hourly price before start)", markets=out), open(path, "w"), separators=(",", ":"), default=str)
        print(f"{sport} {date}: {n} settled markets, {len(out)} matched with a pregame price -> {path}", flush=True)


if __name__ == "__main__":
    if sys.argv[1:2] == ["backfill"]:
        sp = sys.argv[2]
        backfill(sp, kinds={"win", "total", "anytime_td", "first_td"} if sp == "nfl" else None)
    elif sys.argv[1:2] == ["live"]:
        sys.exit(live())
    else:
        for s in (sys.argv[1:] or ["nfl", "nhl", "nba"]):
            run(s)
