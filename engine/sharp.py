#!/usr/bin/env python3
"""
Value tags for the model pages: player props where a sportsbook pays clearly more than our fair price.
<site>/markets/flags_<sport>.json: game id -> player -> one bet. Each run replaces the file; no history kept.

Two sources, tried in this order:
  Unabated (unabated.py): tried every run, at most every UA_GAP minutes (its props request often fails on
    their side). Its terms allow showing prices, so a tag also carries price, book and a Gambly bet link
    with our partner code. Attempts / successes are counted in markets/sharp_state.json.
  SharpAPI (fallback, at most every MIN_GAP hours): publishes ONLY our inference (the bet), never its data:
    no odds, no book names, no book chances.

    python sharp.py            # cloud, after books.py: each sport with a game in the next 12 h
    python sharp.py nfl        # one sport now

Rules (same checks as the private comparison page, private/sharp_compare.py, but stricter):
  our chance x the best book's payout - 1 >= MIN_EV; not 'check news' (we're 20+ points off the book's own
  no-vig chance, or 2x its implied chance on one-sided bets: usually news the model lacks);
  NHL goals: only our top 3 in the game (outside it, our picks lost on Kalshi this season);
  NFL yards / receptions: main lines priced on both sides only.
Keys: env UNABATED_API_KEY / SHARPAPI_KEY (GitHub secrets), else private/*_key.txt (this PC).
"""

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
import unabated as U     # noqa: E402  (preferred props source)

API = "https://api.sharpapi.io/api/v1"
STATE = os.path.join(M.OUT, "sharp_state.json")
MIN_EV, WINDOW, MIN_GAP, PER_GAME = .05, 12, 3, 5          # hours
UA_GAP = 20                                                   # minutes between Unabated props tries
NFL_PROP = {"player_receiving_yards": ("rec_yds", "rec yds"), "player_rushing_yards": ("rush_yds", "rush yds"),
            "player_passing_yards": ("pass_yds", "pass yds"), "player_receptions": ("rec", "receptions")}
UA_NFL = {216: ("td", "TD"), 15: ("rec", "receptions"), 16: ("rec_yds", "rec yds"), 12: ("rush_yds", "rush yds"), 14: ("pass_yds", "pass yds")}


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
    """SharpAPI: all pages of one league + market (free tier: 12 requests a minute, 200 rows a page)."""
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


# ---------------------------------------------------------------- our numbers
def nhl_ours(now):
    """({(away, home, team, name): our numbers}, date) for today's NHL games not yet started."""
    date = now.tz_convert("America/New_York").date().isoformat()
    f = os.path.join(HERE, "hockey", "projections", f"{date}.json")
    if not os.path.exists(f):
        return None, date
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
    return pl, date


def nfl_ours():
    """({(away, home): game id}, {(game id, name): our numbers}) for the latest NFL week."""
    fs = sorted(glob.glob(os.path.join(HERE, "projections", "20??_wk??_players.csv")))
    if not fs:
        return None, None
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
    return games, by


# ---------------------------------------------------------------- SharpAPI rows: (gid, player, bet, ev, check, None)
def nhl(now):
    pl, date = nhl_ours(now)
    if pl is None:
        return None
    out = []
    for r in pull(key(), "nhl", "anytime_goal_scorer"):
        a, h = B.NAMES["nhl"].get(r["away_team"]), B.NAMES["nhl"].get(r["home_team"])
        if pd.Timestamp(r["event_start_time"]).tz_convert("America/New_York").date().isoformat() != date:
            continue
        nm = norm(r.get("player_name") or r["selection"])
        me = pl.get((a, h, a, nm)) or pl.get((a, h, h, nm))
        if not me or me["rank"] > 3:
            continue
        ev, chk = judge(me["p"], r["odds_american"])
        out.append((me["gid"], me["name"], "goal", ev, chk, None))
    return out


def nfl(now):
    games, by = nfl_ours()
    if games is None:
        return None
    out = []
    for mk in ("anytime_touchdown_scorer", *NFL_PROP):
        rows = pull(key(), "nfl", mk)
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
                    out.append((gid, me["name"], "TD", ev, chk, None))
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
            out.append((gid, me["name"], f"{sd} {float(r['line']):g} {lab}", ev, chk, None))
    return out


# ---------------------------------------------------------------- Unabated rows: extra = price, book, bet link
def ua_nv(df):
    """{(book, event, bet type, person, line, side): the book's no-vig chance} where it prices both sides."""
    out = {}
    for (bk, ev, bt, pid, pts), x in df.groupby(["book", "event", "bt", "person", "points"], dropna=False):
        if set(x.side_index) >= {0, 1}:
            q = {si: 1 / dec(pr) for si, pr in zip(x.side_index, x.price)}
            t = q[0] + q[1]
            for si in (0, 1):
                out[(bk, ev, bt, pid, pts, si)] = q[si] / t
    return out


def ua_row(NV, gid, me, bet, p, r):
    ev, chk = judge(p, r.price, NV.get((r.book, r.event, r.bt, r.person, r.points, r.side_index)))
    return (gid, me["name"], bet, ev, chk, dict(o=int(r.price), k=U.SHORT[r.book], u=r.url, p=round(float(p), 4)))


def ua_nhl(now):
    pl, date = nhl_ours(now)
    if pl is None:
        return None
    df = U.board("nhl", "props", [278], kalshi=True)
    if df is None or df.empty:
        return False
    df = df[pd.to_datetime(df.start, utc=True) > now]
    NV, out = ua_nv(df), []
    for r in df[(df.side_index == 0) & (df.points == .5)].itertuples():           # side 0 = over 0.5 goals = scores
        if pd.Timestamp(r.start).tz_convert("America/New_York").date().isoformat() != date:
            continue
        a, h = M.code("nhl", str(r.away)), M.code("nhl", str(r.home))
        me = pl.get((a, h, a, norm(r.name))) or pl.get((a, h, h, norm(r.name)))
        if me and me["rank"] <= 3:
            out.append(ua_row(NV, me["gid"], me, "goal", me["p"], r))
    return out


def ua_nfl(now):
    games, by = nfl_ours()
    if games is None:
        return None
    df = U.board("nfl", "props", list(UA_NFL), kalshi=True)
    if df is None or df.empty:
        return False
    df = df[(pd.to_datetime(df.start, utc=True) > now) & df.side_index.isin([0, 1])]
    NV, out = ua_nv(df), []
    for r in df.itertuples():
        gid = games.get((M.code("nfl", str(r.away)), M.code("nfl", str(r.home))))
        me = by.get((gid, norm(r.name))) if gid else None
        if not me or pd.isna(r.points):
            continue
        st, lab = UA_NFL[r.bt]
        if st == "td":
            if r.side_index == 0 and r.points == .5 and me["td"] is not None:
                out.append(ua_row(NV, gid, me, "TD", me["td"], r))
            continue
        if NV.get((r.book, r.event, r.bt, r.person, r.points, r.side_index)) is None:
            continue                                                    # one-sided / ladder rung: no clean comparison
        po = p_over(me["d"].get(st), float(r.points))
        if po is not None:
            sd = "over" if r.side_index == 0 else "under"
            out.append(ua_row(NV, gid, me, f"{sd} {float(r.points):g} {lab}", po if sd == "over" else 1 - po, r))
    return out


def tags(rows):
    """Best price per bet; a player gets one tag (his best bet clearing MIN_EV without a news flag), at most
    PER_GAME a game. 'Under 0.5' bets are skipped (thin roles the model overrates).
    SharpAPI rows publish only the bet; Unabated rows also carry price, book and the bet link."""
    best, alls = {}, {}
    for gid, name, bet, ev, chk, extra in rows:
        if bet.startswith("under 0.5"):
            continue
        kk = (str(gid), name, bet)
        if extra and not chk:                                   # every book's price for this bet (the page can prefer the viewer's books)
            alls.setdefault(kk, {})[extra["k"]] = [extra["o"], extra["u"]]
        if kk not in best or ev > best[kk][0]:
            best[kk] = (ev, chk, extra)
    top = {}
    for (gid, name, bet), (ev, chk, extra) in best.items():
        if ev >= MIN_EV and not chk and ((gid, name) not in top or ev > top[(gid, name)][0]):
            top[(gid, name)] = (ev, bet, extra)
    out = {}
    for (gid, name), (ev, bet, extra) in sorted(top.items(), key=lambda kv: -kv[1][0]):
        if len(out.get(gid, {})) < PER_GAME:
            out.setdefault(gid, {})[jsnorm(name)] = dict(b=bet, **extra, all=alls.get((gid, name, bet), {})) if extra else bet
    return out


def main(argv):
    now = pd.Timestamp.now(tz="UTC")
    try:
        st = json.load(open(STATE))
    except (OSError, ValueError):
        st = {}
    for sport, fn, ufn, ours in (("nhl", nhl, ua_nhl, M.ours_nhl), ("nfl", nfl, ua_nfl, M.ours_nfl)):
        if argv and sport not in argv:
            continue
        try:
            O = ours() or {}
            soon = any(now < pd.Timestamp(g["start"]).tz_convert("UTC") <= now + pd.Timedelta(hours=WINDOW) for g in O.get("games", []))
            if not soon and not argv:
                print(f"sharp {sport}: no game in the next {WINDOW} h"); continue
            rows, src = None, None
            ul = st.get("ua_" + sport)
            if U.key() and (argv or not ul or now - pd.Timestamp(ul) >= pd.Timedelta(minutes=UA_GAP)):
                tries, ok = st.setdefault("ua_attempts", {}), st.setdefault("ua_ok", {})
                tries[sport] = tries.get(sport, 0) + 1
                try:
                    r = ufn(now)
                except Exception as e:
                    print(f"sharp {sport}: Unabated error ({type(e).__name__}: {e})"); r = False
                if r is None:
                    print(f"sharp {sport}: no projections"); continue
                if r:
                    rows, src = r, "unabated"
                    st["ua_" + sport] = now.isoformat()
                    ok[sport] = ok.get(sport, 0) + 1
                else:
                    print(f"sharp {sport}: Unabated props unavailable ({ok.get(sport, 0)} of {tries[sport]} tries have worked)")
            if rows is None:
                ufresh = ul and now - pd.Timestamp(ul) < pd.Timedelta(minutes=UA_GAP)
                last = st.get(sport)
                if not key() or ufresh or (not argv and last and now - pd.Timestamp(last) < pd.Timedelta(hours=MIN_GAP)):
                    print(f"sharp {sport}: not due"); continue
                rows, src = fn(now), "sharpapi"
                if rows is None:
                    print(f"sharp {sport}: no projections"); continue
                st[sport] = now.isoformat()
            T = tags(rows)
            bad = [v["u"] for g in T.values() for v in g.values() if isinstance(v, dict) and f"utm_campaign={U.PARTNER}" not in (v.get("u") or "")]
            if bad:
                raise RuntimeError(f"{len(bad)} links without the partner code: not published")
            os.makedirs(os.path.join(M.SITE, "markets"), exist_ok=True)
            json.dump(dict(sport=sport, fetched=now.tz_convert("America/New_York").isoformat(timespec="minutes"), min_ev=MIN_EV, source=src, tags=T),
                      open(os.path.join(M.SITE, "markets", f"flags_{sport}.json"), "w"), separators=(",", ":"))
            print(f"sharp {sport}: {src}, {len(rows)} priced bets, {sum(len(g) for g in T.values())} tagged")
        except Exception as e:                                   # never break the markets run
            print(f"sharp {sport}: skipped ({type(e).__name__}: {e})")
    os.makedirs(M.OUT, exist_ok=True)
    json.dump(st, open(STATE, "w"), indent=1)


if __name__ == "__main__":
    main(sys.argv[1:])
