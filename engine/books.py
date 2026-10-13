#!/usr/bin/env python3
"""
Sportsbook prices for game lines (moneyline, spread, total), matched to our games and published for
the model pages: <site>/markets/books_<sport>.json. Each price is [American odds, bet link] (Unabated:
a Gambly link with our partner code) or plain odds (The Odds API backup, no links).

Primary: Unabated free API (unabated.py): 8 sportsbooks, no credit limit, pulled at most every 20 min
for sports with a game within 24 h. Backup when Unabated fails: The Odds API (below).

    python books.py            # cloud, after markets.py live: pull each sport with a game soon, within the credit budget
    python books.py nfl        # one sport now (still budget-checked)

Key: env ODDS_API_KEY (GitHub secret), else private/oddsapi_key.txt (this PC; never published).
Credits: one pull = 3 credits (3 markets x up to 10 named books). The free plan has 500 a month,
so pulls are paced: a sport is pulled only when one of its games starts within PULL_WINDOW hours,
it wasn't pulled in the last MIN_GAP hours, and today's spend stays within what is left for the
month divided by the days left. State: markets/books_state.json.
The Odds API terms allow showing its odds on our own site (not reselling them as a data feed).
"""

import datetime as dt
import json
import os
import sys

import pandas as pd
import requests

import markets as M
import unabated as U

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(M.OUT, "books_state.json")
API = "https://api.the-odds-api.com/v4/sports/{}/odds"
KEYS = {"nfl": "americanfootball_nfl", "nhl": "icehockey_nhl", "nba": "basketball_nba"}
OURS = {"nfl": M.ours_nfl, "nhl": M.ours_nhl, "nba": M.ours_nba}
BOOKS = ["hardrockbet", "betrivers", "espnbet", "fliff", "ballybet", "betparx", "pinnacle"]   # books Unabated doesn't carry (+ Pinnacle: the sharp reference, not bettable from the US)
SHORT = {"hardrockbet": "Hard Rock", "novig": "Novig", "draftkings": "DraftKings", "fanduel": "FanDuel", "betmgm": "BetMGM",
         "williamhill_us": "Caesars", "espnbet": "ESPN BET", "betrivers": "BetRivers", "fliff": "Fliff", "ballybet": "Bally Bet", "betparx": "betPARX", "pinnacle": "Pinnacle"}
COST = 3
PULL_WINDOW, MIN_GAP = 8, 8       # hours (The Odds API: credit-limited; leaves room for Hard Rock props in sharp.py)
UA_WINDOW, UA_GAP = 24, 20        # Unabated (primary): games within 24 h, at most every 20 minutes
OA_FRESH = 3                      # hours an Odds API price is shown (pulled far less often)

NAMES = {
    "nfl": {"Arizona Cardinals": "ARI", "Atlanta Falcons": "ATL", "Baltimore Ravens": "BAL", "Buffalo Bills": "BUF", "Carolina Panthers": "CAR",
            "Chicago Bears": "CHI", "Cincinnati Bengals": "CIN", "Cleveland Browns": "CLE", "Dallas Cowboys": "DAL", "Denver Broncos": "DEN",
            "Detroit Lions": "DET", "Green Bay Packers": "GB", "Houston Texans": "HOU", "Indianapolis Colts": "IND", "Jacksonville Jaguars": "JAX",
            "Kansas City Chiefs": "KC", "Las Vegas Raiders": "LV", "Los Angeles Chargers": "LAC", "Los Angeles Rams": "LA", "Miami Dolphins": "MIA",
            "Minnesota Vikings": "MIN", "New England Patriots": "NE", "New Orleans Saints": "NO", "New York Giants": "NYG", "New York Jets": "NYJ",
            "Philadelphia Eagles": "PHI", "Pittsburgh Steelers": "PIT", "San Francisco 49ers": "SF", "Seattle Seahawks": "SEA",
            "Tampa Bay Buccaneers": "TB", "Tennessee Titans": "TEN", "Washington Commanders": "WAS"},
    "nhl": {"Anaheim Ducks": "ANA", "Boston Bruins": "BOS", "Buffalo Sabres": "BUF", "Calgary Flames": "CGY", "Carolina Hurricanes": "CAR",
            "Chicago Blackhawks": "CHI", "Colorado Avalanche": "COL", "Columbus Blue Jackets": "CBJ", "Dallas Stars": "DAL", "Detroit Red Wings": "DET",
            "Edmonton Oilers": "EDM", "Florida Panthers": "FLA", "Los Angeles Kings": "LAK", "Minnesota Wild": "MIN", "Montréal Canadiens": "MTL",
            "Montreal Canadiens": "MTL", "Nashville Predators": "NSH", "New Jersey Devils": "NJD", "New York Islanders": "NYI", "New York Rangers": "NYR",
            "Ottawa Senators": "OTT", "Philadelphia Flyers": "PHI", "Pittsburgh Penguins": "PIT", "San Jose Sharks": "SJS", "Seattle Kraken": "SEA",
            "St Louis Blues": "STL", "St. Louis Blues": "STL", "Tampa Bay Lightning": "TBL", "Toronto Maple Leafs": "TOR", "Utah Mammoth": "UTA",
            "Utah Hockey Club": "UTA", "Vancouver Canucks": "VAN", "Vegas Golden Knights": "VGK", "Washington Capitals": "WSH", "Winnipeg Jets": "WPG"},
    "nba": {"Atlanta Hawks": "ATL", "Boston Celtics": "BOS", "Brooklyn Nets": "BKN", "Charlotte Hornets": "CHA", "Chicago Bulls": "CHI",
            "Cleveland Cavaliers": "CLE", "Dallas Mavericks": "DAL", "Denver Nuggets": "DEN", "Detroit Pistons": "DET", "Golden State Warriors": "GS",
            "Houston Rockets": "HOU", "Indiana Pacers": "IND", "Los Angeles Clippers": "LAC", "Los Angeles Lakers": "LAL", "Memphis Grizzlies": "MEM",
            "Miami Heat": "MIA", "Milwaukee Bucks": "MIL", "Minnesota Timberwolves": "MIN", "New Orleans Pelicans": "NO", "New York Knicks": "NY",
            "Oklahoma City Thunder": "OKC", "Orlando Magic": "ORL", "Philadelphia 76ers": "PHI", "Phoenix Suns": "PHX", "Portland Trail Blazers": "POR",
            "Sacramento Kings": "SAC", "San Antonio Spurs": "SA", "Toronto Raptors": "TOR", "Utah Jazz": "UTAH", "Washington Wizards": "WSH"},
}


def key():
    k = os.environ.get("ODDS_API_KEY")
    p = os.path.join(HERE, "private", "oddsapi_key.txt")
    if not k and os.path.exists(p):
        k = open(p).read().strip()
    return k


def load_state():
    try:
        return json.load(open(STATE))
    except (OSError, ValueError):
        return {}


def budget_ok(st, now):
    """Pace the month: today's spend may not pass (credits left at the start of today) / (days left incl. today)."""
    day = now.strftime("%Y-%m-%d")
    if st.get("day") != day:
        st.update(day=day, spent_today=0, left_at_day_start=st.get("remaining", 500) if st.get("month") == now.strftime("%Y-%m") else 500)
    st["month"] = now.strftime("%Y-%m")
    nxt = (now.replace(day=1) + pd.DateOffset(months=1)).date()
    days_left = (nxt - now.date()).days
    allow = max(COST, st["left_at_day_start"] / max(days_left, 1))
    return st["spent_today"] + COST <= allow and st.get("remaining", 500) >= COST


def tidy(events, sport, games):
    """{our game id: {ml: {home|away: {book: odds}}, spread: {home line: {home|away: {...}}}, total: {line: {over|under: {...}}}}}"""
    names, out = NAMES[sport], {}
    for e in events:
        h, a = names.get(e["home_team"]), names.get(e["away_team"])
        t = pd.Timestamp(e["commence_time"])
        g = next((x for x in games if x["home"] == h and x["away"] == a
                  and abs((pd.Timestamp(x["start"]).tz_convert("UTC") - t).total_seconds()) < 36 * 3600), None)
        if not g:
            continue
        G = out.setdefault(str(g["id"]), {"ml": {}, "spread": {}, "total": {}})
        for b in e.get("bookmakers", []):
            bk = b["key"]
            for m in b.get("markets", []):
                for o in m.get("outcomes", []):
                    side = {e["home_team"]: "home", e["away_team"]: "away"}.get(o["name"], o["name"].lower())
                    if m["key"] == "h2h":
                        G["ml"].setdefault(side, {})[bk] = o["price"]
                    elif m["key"] == "spreads" and o.get("point") is not None:
                        hl = o["point"] if side == "home" else -o["point"]           # keyed by the home team's handicap
                        hl = hl + 0.0 if hl else 0.0                                 # no "-0" keys
                        G["spread"].setdefault(f"{hl:g}", {}).setdefault(side, {})[bk] = o["price"]
                    elif m["key"] == "totals" and o.get("point") is not None:
                        G["total"].setdefault(f"{o['point']:g}", {}).setdefault(side, {})[bk] = o["price"]
    return out


def tidy_ua(df, sport, games):
    """Same shape as tidy(), from Unabated rows; each price is [American odds, Gambly link with our partner code]."""
    out = {}
    for ev, x in df[df.bt.isin([1, 2, 3])].groupby("event"):
        a, h = M.code(sport, str(x.away.iloc[0] or "")), M.code(sport, str(x.home.iloc[0] or ""))
        t = pd.Timestamp(x.start.iloc[0])
        g = next((y for y in games if y["home"] == h and y["away"] == a
                  and abs((pd.Timestamp(y["start"]).tz_convert("UTC") - t).total_seconds()) < 36 * 3600), None)
        if not g:
            continue
        G = out.setdefault(str(g["id"]), {"ml": {}, "spread": {}, "total": {}})
        for r in x.itertuples():
            bk, v = U.SHORT[r.book], [r.price, r.url]
            if r.bt == 1 and r.side_index in (0, 1):
                G["ml"].setdefault("away" if r.side_index == 0 else "home", {})[bk] = v
            elif r.bt == 2 and r.side_index in (0, 1) and pd.notna(r.points):
                side = "away" if r.side_index == 0 else "home"
                hl = r.points if side == "home" else -r.points                  # keyed by the home team's handicap
                hl = hl + 0.0 if hl else 0.0
                G["spread"].setdefault(f"{hl:g}", {}).setdefault(side, {})[bk] = v
            elif r.bt == 3 and r.side_index in (0, 1) and pd.notna(r.points):
                G["total"].setdefault(f"{r.points:g}", {}).setdefault("over" if r.side_index == 0 else "under", {})[bk] = v
    return out


def write(sport, G, now, source):
    # free-tier condition: every published Gambly link carries our partner code
    def links(o):
        if isinstance(o, list):
            yield from ([o[1]] if len(o) == 2 and isinstance(o[1], str) else [])
        elif isinstance(o, dict):
            for v in o.values():
                yield from links(v)
    bad = [u for u in links(G) if f"utm_campaign={U.PARTNER}" not in u]
    if bad:
        raise RuntimeError(f"{len(bad)} links without the partner code: not published")
    os.makedirs(os.path.join(M.SITE, "markets"), exist_ok=True)
    json.dump(dict(sport=sport, fetched=now.tz_convert("America/New_York").isoformat(timespec="minutes"), source=source, games=G),
              open(os.path.join(M.SITE, "markets", f"books_{sport}.json"), "w"), separators=(",", ":"))


def cache(sport, src, G=None, now=None):
    """Last pull per source (engine state): markets/books_<src>_<sport>.json."""
    f = os.path.join(M.OUT, f"books_{src}_{sport}.json")
    if G is not None:
        os.makedirs(M.OUT, exist_ok=True)
        json.dump(dict(fetched=now.isoformat(), games=G), open(f, "w"), separators=(",", ":"))
    try:
        return json.load(open(f))
    except (OSError, ValueError):
        return None


def pull_ua(sport, st, now, games, force):
    last = st.get("ua_last", {}).get(sport)
    if not force and last and now - pd.Timestamp(last) < pd.Timedelta(minutes=UA_GAP):
        return False
    df = U.board(sport, "straight", [1, 2, 3], kalshi=True)
    G = tidy_ua(df, sport, games) if df is not None and not df.empty else {}
    if not G:
        print(f"books {sport}: Unabated returned no matching games"); return False
    cache(sport, "ua", G, now)
    st.setdefault("ua_last", {})[sport] = now.isoformat()
    print(f"books {sport}: {len(G)} games from Unabated")
    return True


def pull_oa(sport, st, now, games, force):
    """The Odds API: only the books Unabated lacks (Hard Rock and others); credit-paced."""
    k = key()
    if not k:
        return False
    soon = [g for g in games if pd.Timestamp(g["start"]).tz_convert("UTC") <= now + pd.Timedelta(hours=PULL_WINDOW)]
    last = st.get("last", {}).get(sport)
    if not force and (not soon or (last and now - pd.Timestamp(last) < pd.Timedelta(hours=MIN_GAP))):
        return False
    if not budget_ok(st, now):
        print(f"books {sport}: Odds API daily credit budget used ({st.get('spent_today')} today, {st.get('remaining')} left this month)"); return False
    r = requests.get(API.format(KEYS[sport]), timeout=30, params=dict(apiKey=k, bookmakers=",".join(BOOKS), markets="h2h,spreads,totals",
                                                                    oddsFormat="american"))
    used = int(r.headers.get("x-requests-last") or COST)
    st["spent_today"] = st.get("spent_today", 0) + used
    if r.headers.get("x-requests-remaining"):
        st["remaining"] = int(float(r.headers["x-requests-remaining"]))
    if r.status_code != 200:
        print(f"books {sport}: Odds API HTTP {r.status_code} {r.text[:200]}"); return False
    st.setdefault("last", {})[sport] = now.isoformat()
    G = tidy(r.json(), sport, games)
    cache(sport, "oa", G, now)
    print(f"books {sport}: {len(G)} games from The Odds API, {used} credits, {st.get('remaining')} left")
    return True


def merge(sport, now):
    """Unabated prices (with bet links) plus Odds API books it lacks, each only while fresh."""
    ua, oa = cache(sport, "ua"), cache(sport, "oa")
    fresh = lambda c, h: c and now - pd.Timestamp(c["fetched"]) < pd.Timedelta(hours=h)
    G = json.loads(json.dumps(ua["games"])) if fresh(ua, 2) else {}
    if fresh(oa, OA_FRESH):
        for gid, g in oa["games"].items():
            T = G.setdefault(gid, {"ml": {}, "spread": {}, "total": {}})
            for kind, v in g.items():
                for k1, v1 in v.items():
                    dst = T[kind].setdefault(k1, {})
                    for k2, v2 in (v1.items() if kind != "ml" else [(None, v1)]):
                        d = dst.setdefault(k2, {}) if kind != "ml" else dst
                        for bk, price in v2.items():
                            d.setdefault(SHORT.get(bk, bk), [price, None])
    if not G:
        return False
    times = [pd.Timestamp(c["fetched"]) for c, h in ((ua, 2), (oa, OA_FRESH)) if fresh(c, h)]
    write(sport, G, min(times), "unabated" if fresh(ua, 2) else "the-odds-api")
    return True


def pull(sport, st, now, force=False):
    O = OURS[sport]()
    games = [g for g in (O or {}).get("games", []) if pd.Timestamp(g["start"]).tz_convert("UTC") > now - pd.Timedelta(hours=1)]
    if not force and not any(pd.Timestamp(g["start"]).tz_convert("UTC") <= now + pd.Timedelta(hours=UA_WINDOW) for g in games):
        print(f"books {sport}: no game in the next {UA_WINDOW} h"); return False
    got = False
    if U.key():
        try:
            got = pull_ua(sport, st, now, games, force)
        except Exception as e:
            print(f"books {sport}: Unabated failed ({type(e).__name__}: {e})")
    try:
        got = pull_oa(sport, st, now, games, force) or got
    except Exception as e:
        print(f"books {sport}: Odds API failed ({type(e).__name__}: {e})")
    return merge(sport, now)                   # rebuild from the latest pulls even when neither source was due


def main(argv):
    now = pd.Timestamp.now(tz="UTC")
    st = load_state()
    sports = [a for a in argv if a in KEYS] or list(KEYS)
    # soonest first so a tight budget goes to the next games
    def first(s):
        try:
            return min(pd.Timestamp(g["start"]).tz_convert("UTC") for g in OURS[s]()["games"] if pd.Timestamp(g["start"]).tz_convert("UTC") > now)
        except Exception:
            return pd.Timestamp.max.tz_localize("UTC")
    for s in sorted(sports, key=first):
        try:
            pull(s, st, now, force=bool(argv))
        except Exception as e:                       # never break the markets run
            print(f"books {s}: skipped ({type(e).__name__}: {e})")
    os.makedirs(M.OUT, exist_ok=True)
    json.dump(st, open(STATE, "w"), indent=1, default=str)


if __name__ == "__main__":
    main(sys.argv[1:])
