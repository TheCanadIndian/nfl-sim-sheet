#!/usr/bin/env python3
"""
Value tags for the model pages: player props where a major sportsbook (DraftKings / FanDuel, via SharpAPI)
pays clearly more than our fair price. Publishes ONLY our inference, never the book's data:
<site>/markets/flags_<sport>.json holds game id -> player -> one bet (e.g. 'over 4.5 receptions') and nothing
else: no odds, no book names, no book chances. Each run replaces the file; no history is kept.

    python sharp.py            # cloud, after books.py: each sport with a game in the next 12 h, at most every 3 h
    python sharp.py nfl        # one sport now

Rules (same checks as the private comparison page, private/sharp_compare.py, but stricter):
  our chance x the best book's payout - 1 >= MIN_EV; not 'check news' (we're 20+ points off the book's own
  no-vig chance, or 2x its implied chance on one-sided bets: usually news the model lacks);
  NHL goals: only our top 3 in the game (outside it, our picks lost on Kalshi this season);
  NFL yards / receptions: main lines priced on both sides only.
Key: env SHARPAPI_KEY (GitHub secret), else private/sharpapi_key.txt (this PC).
"""

import datetime as dt
import glob
import json
import os
import re
import sys
import time
import unicodedata

import numpy as np
import pandas as pd
import requests

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import books as B        # noqa: E402  (team-name tables)
import markets as M      # noqa: E402  (site / state folders)

API = "https://api.sharpapi.io/api/v1"
STATE = os.path.join(M.OUT, "sharp_state.json")
MIN_EV, WINDOW, MIN_GAP, PER_GAME = .05, 12, 3, 5          # hours
NFL_PROP = {"player_receiving_yards": ("rec_yds", "rec yds"), "player_rushing_yards": ("rush_yds", "rush yds"),
            "player_passing_yards": ("pass_yds", "pass yds"), "player_receptions": ("rec", "receptions")}


def key():
    k = os.environ.get("SHARPAPI_KEY")
    p = os.path.join(HERE, "private", "sharpapi_key.txt")
    return k or (open(p).read().strip() if os.path.exists(p) else None)


def norm(s):
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b\.?", "", s)
    return re.sub(r"[^a-z]", "", s)


def jsnorm(s):
    """Same as mkt.js norm(): the page looks players up by this."""
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z ]", "", s)
    s = re.sub(r"\b(jr|sr|ii|iii|iv)\b", "", s)
    return re.sub(r"\s+", " ", s).strip()


def dec(a):
    a = float(a)
    return 1 + (a / 100 if a > 0 else 100 / -a)


_last = [0.0]


def pull(k, league, market):
    """All pages of one league + market (free tier: 12 requests a minute, 200 rows a page)."""
    rows, cursor = [], None
    for _ in range(12):
        wait = 5.2 - (time.time() - _last[0])
        if wait > 0:
            time.sleep(wait)
        prm = {"league": league, "market_type": market, "limit": 200}
        if cursor:
            prm["cursor"] = cursor
        r = requests.get(API + "/odds", headers={"X-API-Key": k}, params=prm, timeout=30)
        _last[0] = time.time()
        if r.status_code == 429:
            time.sleep(15); continue
        if r.status_code != 200:
            print(f"sharp {league} {market}: HTTP {r.status_code}"); break
        d = r.json()
        rows += d.get("data", [])
        pg = d.get("pagination", {})
        if not pg.get("has_more"):
            break
        cursor = pg.get("next_cursor")
    # the market filter is a prefix match; keep exact, pregame, live prices only
    return [x for x in rows if x.get("market_type") == market and not x.get("is_live") and x.get("is_active", True)
            and not x.get("is_stale_pregame_price")]


def two_sided(rows):
    g = {}
    for r in rows:
        if not r.get("is_main_line") or r.get("is_alternate_line"):
            continue
        sd = (r.get("selection_type") or "").lower()
        if sd not in ("over", "under"):
            continue
        g.setdefault((r["sportsbook"], r["event_id"], r["market_type"], r.get("player_name"), r.get("line")), {})[sd] = 1 / dec(r["odds_american"])
    out = {}
    for kk, v in g.items():
        if len(v) == 2:
            t = sum(v.values())
            for sd, q in v.items():
                out[kk + (sd,)] = q / t
    return out


def judge(p, odds, nv=None):
    """(EV, check) for our chance p at these odds."""
    ev, imp = p * dec(odds) - 1, 1 / dec(odds)
    check = (nv is not None and abs(p - nv) >= .20) or (nv is None and p >= 2 * imp and p - imp >= .08)
    return ev, check


def p_over(dist, line):
    if not dist:
        return None
    if "p" in dist:
        kk = np.arange(len(dist["p"])) + dist["o"]
        return float(np.sum(np.array(dist["p"])[kk > line]))
    return float((np.array(dist["q"]) > line).mean())


# ---------------------------------------------------------------- per sport: [(gid, player, bet label, ev, check)]
def nhl(k, now):
    date = now.tz_convert("America/New_York").date().isoformat()
    f = os.path.join(HERE, "hockey", "projections", f"{date}.json")
    if not os.path.exists(f):
        return None
    d = json.load(open(f))
    pl = {}
    for g in d["games"]:
        a, h = g["away"]["team"], g["home"]["team"]
        if pd.Timestamp(g["start_utc"]) <= now:
            continue                                                    # started: no pregame tag
        allp = g["away"]["players"] + g["home"]["players"]
        allp = sorted(allp, key=lambda p: -(p.get("um") or 1) * -np.log(1 - min(p["p"], .999)))   # usage-adjusted rank, as on the page
        for i, p in enumerate(allp):
            team = a if p in g["away"]["players"] else h
            pl[(a, h, team, norm(p["name"]))] = dict(gid=g["id"], name=p["name"], p=p["p"], rank=i + 1)
    out = []
    for r in pull(k, "nhl", "anytime_goal_scorer"):
        a, h = B.NAMES["nhl"].get(r["away_team"]), B.NAMES["nhl"].get(r["home_team"])
        if pd.Timestamp(r["event_start_time"]).tz_convert("America/New_York").date().isoformat() != date:
            continue
        nm = norm(r.get("player_name") or r["selection"])
        me = pl.get((a, h, a, nm)) or pl.get((a, h, h, nm))
        if not me or me["rank"] > 3:
            continue
        ev, chk = judge(me["p"], r["odds_american"])
        out.append((me["gid"], me["name"], "goal", ev, chk))
    return out


def nfl(k, now):
    fs = sorted(glob.glob(os.path.join(HERE, "projections", "20??_wk??_players.csv")))
    if not fs:
        return None
    stem = fs[-1][:-len("_players.csv")]
    P = pd.read_csv(fs[-1])
    dists = json.load(open(stem + "_dist.json")) if os.path.exists(stem + "_dist.json") else {}
    T = pd.read_csv(stem + "_teams.csv")
    games = {}
    for gid, x in T.groupby("game_id"):
        games[(x[x.home == 0].team.iloc[0], x[x.home == 1].team.iloc[0])] = gid
    by = {}
    for (gid, team, name), x in P.groupby(["game_id", "team", "player"]):
        td = x[x.stat == "anytime_td"]["mean"]
        by[(gid, norm(name))] = dict(name=name, d=dists.get(f"{gid}|{team}|{name}", {}), td=float(td.iloc[0]) if len(td) else None)
    out = []
    for mk in ("anytime_touchdown_scorer", *NFL_PROP):
        rows = pull(k, "nfl", mk)
        NV = two_sided(rows)
        for r in rows:
            a, h = B.NAMES["nfl"].get(r["away_team"]), B.NAMES["nfl"].get(r["home_team"])
            t = pd.Timestamp(r["event_start_time"])
            gid = games.get((a, h))
            if gid is None or t <= now or (t - now).days > 7 or r.get("is_alternate_line"):
                continue
            me = by.get((gid, norm(r.get("player_name") or re.sub(r"\s+(Over|Under)$", "", r["selection"]))))
            if not me:
                continue
            if mk == "anytime_touchdown_scorer":
                if me["td"] is not None:
                    ev, chk = judge(me["td"], r["odds_american"])
                    out.append((gid, me["name"], "TD", ev, chk))
                continue
            st, lab = NFL_PROP[mk]
            sd = (r.get("selection_type") or "").lower()
            nv = NV.get((r["sportsbook"], r["event_id"], mk, r.get("player_name"), r.get("line"), sd))
            if nv is None or not r.get("is_main_line") or r.get("line") is None:
                continue
            po = p_over(me["d"].get(st), float(r["line"]))
            if po is None:
                continue
            ev, chk = judge(po if sd == "over" else 1 - po, r["odds_american"], nv)
            out.append((gid, me["name"], f"{sd} {float(r['line']):g} {lab}", ev, chk))
    return out


def tags(rows):
    """Best price per bet; a player gets one tag (his best bet clearing MIN_EV without a news flag), at most
    PER_GAME a game. 'Under 0.5' bets are skipped (thin roles the model overrates). Output carries no book data."""
    best = {}
    for gid, name, bet, ev, chk in rows:
        if bet.startswith("under 0.5"):
            continue
        kk = (str(gid), name, bet)
        if kk not in best or ev > best[kk][0]:
            best[kk] = (ev, chk)
    top = {}
    for (gid, name, bet), (ev, chk) in best.items():
        if ev >= MIN_EV and not chk and ((gid, name) not in top or ev > top[(gid, name)][0]):
            top[(gid, name)] = (ev, bet)
    out = {}
    for (gid, name), (ev, bet) in sorted(top.items(), key=lambda kv: -kv[1][0]):
        if len(out.get(gid, {})) < PER_GAME:
            out.setdefault(gid, {})[jsnorm(name)] = bet
    return out


def main(argv):
    k = key()
    if not k:
        print("sharp: no SHARPAPI_KEY"); return
    now = pd.Timestamp.now(tz="UTC")
    try:
        st = json.load(open(STATE))
    except (OSError, ValueError):
        st = {}
    for sport, fn, ours in (("nhl", nhl, M.ours_nhl), ("nfl", nfl, M.ours_nfl)):
        if argv and sport not in argv:
            continue
        try:
            O = ours() or {}
            soon = any(now < pd.Timestamp(g["start"]).tz_convert("UTC") <= now + pd.Timedelta(hours=WINDOW) for g in O.get("games", []))
            last = st.get(sport)
            if not argv and (not soon or (last and now - pd.Timestamp(last) < pd.Timedelta(hours=MIN_GAP))):
                print(f"sharp {sport}: not due"); continue
            rows = fn(k, now)
            if rows is None:
                print(f"sharp {sport}: no projections"); continue
            T = tags(rows)
            os.makedirs(os.path.join(M.SITE, "markets"), exist_ok=True)
            json.dump(dict(sport=sport, fetched=now.tz_convert("America/New_York").isoformat(timespec="minutes"), min_ev=MIN_EV, tags=T),
                      open(os.path.join(M.SITE, "markets", f"flags_{sport}.json"), "w"), separators=(",", ":"))
            st[sport] = now.isoformat()
            print(f"sharp {sport}: {len(rows)} priced bets, {sum(len(g) for g in T.values())} tagged")
        except Exception as e:                                   # never break the markets run
            print(f"sharp {sport}: skipped ({type(e).__name__}: {e})")
    os.makedirs(M.OUT, exist_ok=True)
    json.dump(st, open(STATE, "w"), indent=1)


if __name__ == "__main__":
    main(sys.argv[1:])
