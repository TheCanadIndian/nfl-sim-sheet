#!/usr/bin/env python3
"""
Unabated free odds API (data.unabated.com): sportsbook game lines and player props with Gambly bet links.
Free tier terms: odds may be shown publicly as long as every Gambly link we publish carries our partner code
(PARTNER below). One request every 5 seconds per key, odds delayed 15+ seconds.

Used by books.py (game-line prices under the bet chips) and value.py (Value tags on player props).
Key: env UNABATED_API_KEY (GitHub secret), else private/unabated_key.txt (this PC; never published).
"""

import json
import os
import re
import tempfile
import time
import unicodedata

import pandas as pd
import requests

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = "https://data.unabated.com"
PARTNER = "thecanadindian"
CACHE = os.path.join(tempfile.gettempdir(), "unabated_cache")       # not committed (the cloud refetches)
LEAGUE = {"nfl": "nfl", "nhl": "nhl", "nba": "nba", "mlb": "mlb"}
# books whose price is a real sportsbook bet (DFS pick'em, Canadian / duplicate feeds and fee-charging
# prediction markets are left out of 'best price': Kalshi and Polymarket have their own box on the pages)
BOOKS = {1: "DraftKings", 2: "FanDuel", 4: "BetMGM", 20: "Caesars", 36: "theScore", 86: "Fanatics", 89: "Novig", 66: "ProphetX"}
SHORT = {1: "DK", 2: "FD", 4: "MGM", 20: "Caesars", 36: "theScore", 86: "Fanatics", 89: "Novig", 66: "ProphetX", 105: "Kalshi"}
KALSHI = 105        # included on request, priced after its taker fee (see kalshi_price)
_last = [0.0]


def key():
    k = os.environ.get("UNABATED_API_KEY")
    p = os.path.join(HERE, "private", "unabated_key.txt")
    return k or (open(p).read().strip() if os.path.exists(p) else None)


def get(path, params=None, tries=8):
    """GET with the free tier's pacing (5 s between requests) and retries (the props board errors now and then)."""
    k = key()
    if not k:
        raise RuntimeError("no UNABATED_API_KEY")
    for i in range(tries):
        wait = 5.3 - (time.time() - _last[0])
        if wait > 0:
            time.sleep(wait)
        try:
            r = requests.get(BASE + path, params=params, headers={"X-Api-Key": k}, timeout=60)
            _last[0] = time.time()
            d = r.json() if r.status_code == 200 else None
            if d and d.get("success", True) and "data" in d:
                return d["data"]
        except (requests.RequestException, ValueError):
            _last[0] = time.time()
    return None


def link(url):
    """Gambly deep link with our partner code (required on every published link)."""
    if not url:
        return None
    return url + ("&" if "?" in url else "?") + f"utm_campaign={PARTNER}"


def norm(s):
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b\.?", "", s)
    return re.sub(r"[^a-z]", "", s)


def lookup(sport, what):
    """Teams {id: abbreviation} / players {id: (name, teamId)}, cached for a day."""
    os.makedirs(CACHE, exist_ok=True)
    f = os.path.join(CACHE, f"{sport}_{what}.json")
    if os.path.exists(f) and time.time() - os.path.getmtime(f) < 86400:
        return {int(k): v for k, v in json.load(open(f)).items()}
    d = get(f"/{'team' if what == 'teams' else 'player'}/{LEAGUE[sport]}")
    if d is None:
        return {int(k): v for k, v in json.load(open(f)).items()} if os.path.exists(f) else {}
    out = ({x["id"]: x["abbreviation"] for x in d} if what == "teams"
           else {x["id"]: (f"{x.get('firstName', '')} {x.get('lastName', '')}".strip(), x.get("teamId")) for x in d})
    json.dump(out, open(f, "w"))
    return out


def kalshi_price(c):
    """American odds a Kalshi buyer really gets: contract price c plus the 7% x c x (1 - c) taker fee."""
    q = c + .07 * c * (1 - c)
    if not 0 < q < 1:
        return None
    return int(round(-100 * q / (1 - q))) if q >= .5 else int(round(100 * (1 - q) / q))


def board(sport, market_type, bet_types=None, kalshi=False):
    """Flat pregame rows: one per (market, side, book) for books in BOOKS.
    Fields: event, start, away, home (Unabated abbreviations), bt, person, name, team, side_index, points, book, price, opener, liq, url."""
    params = [("betTypeId", b) for b in (bet_types or [])]
    d = get(f"/market/{LEAGUE[sport]}/{market_type}/odds", params or None)
    if not d:
        return None
    teams = lookup(sport, "teams")
    people = lookup(sport, "players") if market_type == "props" else {}
    rows = []
    for lg in d.get("odds", {}).values():
        pre = lg.get("periodTypes", {}).get("game", {}).get("pregame", {})
        for m in pre.values():
            if m.get("statusId") not in (1, None) or m.get("isLive"):
                continue
            et = {v["sideIndex"]: v["teamId"] for v in (m.get("eventTeams") or {}).values()}
            away, home = teams.get(et.get(0)), teams.get(et.get(1))
            for sd in (m.get("sides") or {}).values():
                pid = sd.get("personId")
                nm, tm = people.get(pid, (None, None)) if pid else (None, None)
                for src, L in (sd.get("marketSourceLines") or {}).items():
                    src = int(src)
                    if (src not in BOOKS and not (kalshi and src == KALSHI)) or L.get("disabled") or L.get("statusId") != 1 or L.get("price") is None:
                        continue
                    price = int(L["price"])
                    if src == KALSHI:
                        if L.get("sourceFormat") != 4 or L.get("sourcePrice") is None:
                            continue
                        price = kalshi_price(float(L["sourcePrice"]))
                        if price is None:
                            continue
                    rows.append(dict(event=m["eventId"], start=m.get("eventStart"), away=away, home=home, bt=m.get("betTypeId"),
                                     person=pid, name=nm, team=teams.get(tm) if tm else None, side_index=sd.get("sideIndex"),
                                     team_side=sd.get("teamId"), points=L.get("points"), book=src, price=price,
                                     opener=L.get("openerPrice"), liq=L.get("liquidity"), url=link(L.get("deepLink"))))
    return pd.DataFrame(rows)
