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
UA_WINDOW = {"nhl": 12, "nfl": 24 * 7}                        # hours ahead to try Unabated (NFL props are posted all week)
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


def judge(p, odds, nv=None, blend=False):
    """(EV, check) for our chance p at these odds. blend: judge the edge on (ours + market no-vig) / 2 (NFL props:
    on Kalshi 2026 that blend with a 5%+ edge returned +8.4%, our projection alone lost)."""
    q = (p + nv) / 2 if blend and nv is not None else p
    ev, imp = q * dec(odds) - 1, 1 / dec(odds)
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
            pl[(a, h, team, norm(p["name"]))] = dict(gid=g["id"], name=p["name"], p=p["p"], rank=i + 1, so=p.get("so"))
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
            ev, chk = judge(po if sd == "over" else 1 - po, r["odds_american"], nv, blend=True)
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


def ua_row(NV, gid, me, bet, p, r, blend=False):
    nv = NV.get((r.book, r.event, r.bt, r.person, r.points, r.side_index))
    ev, chk = judge(p, r.price, nv, blend=blend)
    x = dict(o=int(r.price), k=U.SHORT[r.book], u=r.url, p=round(float(p), 4))
    if blend and nv is not None:
        x["m"] = round(float(nv), 4)                      # the page re-checks the viewer's books on the same blend
    return (gid, me["name"], bet, ev, chk, x)


PROPS = {}          # sport -> every matched player's prices (published as props_<sport>.json when Unabated works)


def gpath(url):
    """'book/line/points' from a Gambly deep link; the page rebuilds the link with our partner code."""
    m = re.search(r"/deeplink/odds/(\d+/\d+/[^?]+)", url or "")
    return m.group(1) if m else None


def add_price(P, gid, name, kind, side, r, p=None, line=None, warn=False):
    """warn: we don't trust our edge here (NHL outside our top 3, or 'check news'): the page won't highlight it."""
    e = P.setdefault(str(gid), {}).setdefault(jsnorm(name), {}).setdefault(kind, {})
    if warn:
        e["w"] = 1
    if p is not None:
        e["p"] = round(float(p), 4)
    if line is not None:
        e["l"] = float(line)
    gp = gpath(r.url)
    if gp:
        e.setdefault(side, {})[U.SHORT[r.book]] = [int(r.price), gp]


def add_alt(P, gid, name, kind, side, r, po):
    """Alternate line for a prop: e.a = {line: {p: our over chance, o: {book: [odds, path]}, u: {...}}}."""
    gp = gpath(r.url)
    if not gp:
        return
    a = P.setdefault(str(gid), {}).setdefault(jsnorm(name), {}).setdefault(kind, {}).setdefault("a", {}).setdefault(f"{float(r.points):g}", {})
    if po is not None:
        a["p"] = round(float(po), 4)
    a.setdefault(side, {})[U.SHORT[r.book]] = [int(r.price), gp]


def ua_nhl(now):
    pl, date = nhl_ours(now)
    if pl is None:
        return None
    P = {}
    # shots on goal (bet type 86): every line 0.5-4.5, both sides, with our shots model's chance (hockey/sog.py)
    sg = U.board("nhl", "props", [86], kalshi=True, tries=3)
    if sg is not None and not sg.empty:
        sg = sg[(pd.to_datetime(sg.start, utc=True) > now) & sg.side_index.isin([0, 1]) & sg.points.isin([.5, 1.5, 2.5, 3.5, 4.5])]
        for r in sg.itertuples():
            a, h = M.code("nhl", str(r.away)), M.code("nhl", str(r.home))
            me = pl.get((a, h, a, norm(r.name))) or pl.get((a, h, h, norm(r.name)))
            if not me or not me.get("so"):
                continue
            po = me["so"][int(r.points)]                                              # so = P(over 0.5, 1.5, ... 4.5)
            add_price(P, me["gid"], me["name"], f"sog{int(r.points * 10):02d}", "o" if r.side_index == 0 else "u", r, p=po, line=r.points)
        print(f"sharp nhl: Unabated shots on goal, {sg.person.nunique()} players")
    df = U.board("nhl", "props", [278], kalshi=True)
    if df is None or df.empty:
        if P:
            PROPS["nhl"] = P
            return []                                                                 # shots only: publish prices, no goal tags
        return False
    df = df[pd.to_datetime(df.start, utc=True) > now]
    NV, out = ua_nv(df), []
    # the exchanges' price for each scorer (median yes price, ~3% margin off), blended 50/50 with ours as on the page:
    # on Kalshi 2026 (3,822 graded legs) that blend beat our number alone; our top-3 legs returned +10% (vs +6% at 75/25)
    ex = df[(df.side_index == 0) & (df.points == .5) & df.book.isin([89, 66, 105] + list(U.PMS))]
    EXM = (ex.assign(q=1 / ex.price.map(dec)).groupby(["event", "person"]).q.median() / 1.03).to_dict()
    for r in df[(df.side_index == 0) & (df.points == .5)].itertuples():           # side 0 = over 0.5 goals = scores
        if pd.Timestamp(r.start).tz_convert("America/New_York").date().isoformat() != date:
            continue
        a, h = M.code("nhl", str(r.away)), M.code("nhl", str(r.home))
        me = pl.get((a, h, a, norm(r.name))) or pl.get((a, h, h, norm(r.name)))
        if not me:
            continue
        add_price(P, me["gid"], me["name"], "goal", "o", r, p=me["p"], warn=me["rank"] > 3)
        if me["rank"] <= 3:
            m = EXM.get((r.event, r.person))
            nv = {(r.book, r.event, r.bt, r.person, r.points, r.side_index): m} if m is not None else {}
            out.append(ua_row(nv, me["gid"], me, "goal", me["p"], r, blend=True))
    PROPS["nhl"] = P
    return out


def ua_nfl(now):
    games, by = nfl_ours()
    if games is None:
        return None
    # one stat per request: the big all-stats board fails far more often; keep whatever comes back
    parts = [U.board("nfl", "props", [bt], kalshi=True, tries=3) for bt in UA_NFL]
    parts = [x for x in parts if x is not None and not x.empty]
    if not parts:
        return False
    df = pd.concat(parts, ignore_index=True)
    print(f"sharp nfl: Unabated returned {', '.join(sorted({UA_NFL[b][1] for b in df.bt.unique()}))}")
    df = df[(pd.to_datetime(df.start, utc=True) > now) & df.side_index.isin([0, 1])]
    NV, out, P = ua_nv(df), [], {}
    # each player's main line for a stat: the number the most books price on both sides
    two = df.groupby(["event", "bt", "person", "points", "book"]).side_index.nunique().reset_index()
    two = two[two.side_index == 2].groupby(["event", "bt", "person", "points"]).size().reset_index(name="n")
    main = {(e, b, pp): pts for e, b, pp, pts in two.sort_values("n").drop_duplicates(["event", "bt", "person"], keep="last")[["event", "bt", "person", "points"]].values}
    for r in df.itertuples():
        gid = games.get((M.code("nfl", str(r.away)), M.code("nfl", str(r.home))))
        me = by.get((gid, norm(r.name))) if gid else None
        if not me or pd.isna(r.points):
            continue
        st, lab = UA_NFL[r.bt]
        if st == "td":
            if r.side_index == 0 and r.points == .5 and me["td"] is not None:
                add_price(P, gid, me["name"], "td", "o", r, p=me["td"], warn=judge(me["td"], r.price)[1])
                out.append(ua_row(NV, gid, me, "TD", me["td"], r))
            continue
        if main.get((r.event, r.bt, r.person)) not in (None, r.points):   # alt line / ladder rung: shown with Pinnacle's anchored price
            add_alt(P, gid, me["name"], st, "o" if r.side_index == 0 else "u", r, p_over(me["d"].get(st), float(r.points)))
        if NV.get((r.book, r.event, r.bt, r.person, r.points, r.side_index)) is None:
            continue                                                    # one-sided / ladder rung: no clean comparison
        po = p_over(me["d"].get(st), float(r.points))
        if po is not None:
            if main.get((r.event, r.bt, r.person)) == r.points:
                nv = NV.get((r.book, r.event, r.bt, r.person, r.points, r.side_index))
                add_price(P, gid, me["name"], st, "o" if r.side_index == 0 else "u", r, p=po, line=r.points,
                          warn=judge(po if r.side_index == 0 else 1 - po, r.price, nv)[1])
            sd = "over" if r.side_index == 0 else "under"
            out.append(ua_row(NV, gid, me, f"{sd} {float(r.points):g} {lab}", po if sd == "over" else 1 - po, r, blend=True))
    PROPS["nfl"] = P
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


# ---------------------------------------------------------------- Hard Rock props (The Odds API; Unabated's free feed lacks Hard Rock)
HR_MKT = {"nhl": ("icehockey_nhl", "player_goal_scorer_anytime", "goal", 2), "nfl": ("americanfootball_nfl", "player_anytime_td", "td", 3)}
HR_CACHE = os.path.join(M.OUT, "hr_props_{}.json")


def hr_pull(sport, now, st):
    """Hard Rock's anytime-goal (NHL) / anytime-TD (NFL) prices for each game starting within HR_MKT[..][3] hours.
    1 credit per game when Hard Rock has posted the market (an empty answer is free, so we simply retry next run);
    shares books.py's monthly credit pacing. Cache: markets/hr_props_<sport>.json {gid: {player: [odds, link]}}."""
    k = B.key()
    if not k:
        return
    key, mkt, kind, hours = HR_MKT[sport]
    O = (M.ours_nhl if sport == "nhl" else M.ours_nfl)() or {}
    soon = {(g["away"], g["home"]): g for g in O.get("games", [])
            if now < pd.Timestamp(g["start"]).tz_convert("UTC") <= now + pd.Timedelta(hours=hours)}
    try:
        cache = json.load(open(HR_CACHE.format(sport)))
    except (OSError, ValueError):
        cache = {}
    todo = {ga: g for ga, g in soon.items() if str(g["id"]) not in cache}
    if not todo:
        return
    r = requests.get(f"https://api.the-odds-api.com/v4/sports/{key}/events", params=dict(apiKey=k), timeout=30)   # free
    if r.status_code != 200:
        return
    bst = B.load_state()
    for e in r.json():
        g = todo.get((B.NAMES[sport].get(e["away_team"]), B.NAMES[sport].get(e["home_team"])))
        if not g:
            continue
        if not B.budget_ok(bst, now):
            print(f"hard rock {sport}: credit budget used for today"); break
        q = requests.get(f"https://api.the-odds-api.com/v4/sports/{key}/events/{e['id']}/odds", timeout=30,
                         params=dict(apiKey=k, bookmakers="hardrockbet", markets=mkt, oddsFormat="american", includeLinks="true"))
        used = int(q.headers.get("x-requests-last") or 0)
        bst["spent_today"] = bst.get("spent_today", 0) + used
        if q.headers.get("x-requests-remaining"):
            bst["remaining"] = int(float(q.headers["x-requests-remaining"]))
        if q.status_code != 200:
            continue
        P = {}
        for b in q.json().get("bookmakers", []):
            for m in b.get("markets", []):
                for o in m.get("outcomes", []):
                    if (o.get("name") or "").lower() in ("yes", "over") and o.get("description"):
                        P[jsnorm(o["description"])] = [int(o["price"]), o.get("link")]
        if P:
            cache[str(g["id"])] = dict(fetched=now.isoformat(), start=str(g["start"]), kind=kind, prices=P)
            print(f"hard rock {sport}: {g['away']} @ {g['home']}: {len(P)} prices ({used} credits, {bst.get('remaining')} left)")
        else:
            print(f"hard rock {sport}: {g['away']} @ {g['home']}: not posted yet (free, will retry)")
    json.dump(bst, open(B.STATE, "w"), indent=1, default=str)
    # keep only games that haven't started
    cache = {gid: c for gid, c in cache.items() if pd.Timestamp(c["start"]).tz_convert("UTC") > now - pd.Timedelta(hours=6)}
    os.makedirs(M.OUT, exist_ok=True)
    json.dump(cache, open(HR_CACHE.format(sport), "w"), separators=(",", ":"))


# ---------------------------------------------------------------- Pinnacle de-vig for NFL props (The Odds API)
PIN_MKT = {"player_reception_yds": "rec_yds", "player_rush_yds": "rush_yds", "player_pass_yds": "pass_yds", "player_receptions": "rec",
           "player_anytime_td": "td"}       # anytime TD is one-way (Yes only)
PIN_K = 1.04                          # power exponent when a game has no two-way lines to solve it from
PIN_HOURS = 3.5                       # one pull per game this close to kickoff (Pinnacle's lines are sharpest late)
PIN_CACHE = os.path.join(M.OUT, "pin_props_nfl.json")


def pin_pull(now):
    """Pinnacle over/under for NFL yardage and catch props, once per game ~3.5 h before kickoff. 1 credit per market
    with data (an empty answer is free, retried next run); shares books.py's monthly credit pacing.
    Cache: {gid: {player: {kind: [line, over odds, under odds]}}}."""
    k = B.key()
    if not k:
        return
    O = M.ours_nfl() or {}
    soon = {(g["away"], g["home"]): g for g in O.get("games", [])
            if now < pd.Timestamp(g["start"]).tz_convert("UTC") <= now + pd.Timedelta(hours=PIN_HOURS)}
    try:
        cache = json.load(open(PIN_CACHE))
    except (OSError, ValueError):
        cache = {}
    todo = {ga: g for ga, g in soon.items() if str(g["id"]) not in cache}
    if not todo:
        return
    r = requests.get("https://api.the-odds-api.com/v4/sports/americanfootball_nfl/events", params=dict(apiKey=k), timeout=30)
    if r.status_code != 200:
        return
    bst = B.load_state()
    for e in r.json():
        g = todo.get((B.NAMES["nfl"].get(e["away_team"]), B.NAMES["nfl"].get(e["home_team"])))
        if not g:
            continue
        if not B.budget_ok(bst, now):
            print("pinnacle nfl: credit budget used for today"); break
        q = requests.get(f"https://api.the-odds-api.com/v4/sports/americanfootball_nfl/events/{e['id']}/odds", timeout=30,
                         params=dict(apiKey=k, bookmakers="pinnacle", markets=",".join(PIN_MKT), oddsFormat="american"))
        used = int(q.headers.get("x-requests-last") or 0)
        bst["spent_today"] = bst.get("spent_today", 0) + used
        if q.headers.get("x-requests-remaining"):
            bst["remaining"] = int(float(q.headers["x-requests-remaining"]))
        if q.status_code != 200:
            continue
        P = {}
        for b in q.json().get("bookmakers", []):
            for m in b.get("markets", []):
                kind = PIN_MKT.get(m["key"])
                for o in m.get("outcomes", []):
                    nm, side = o.get("description"), (o.get("name") or "").lower()
                    if kind == "td" and side == "yes":
                        side, o = "over", dict(o, point=.5)
                    if not kind or not nm or side not in ("over", "under") or o.get("point") is None:
                        continue
                    x = P.setdefault(jsnorm(nm), {}).setdefault(kind, [float(o["point"]), None, None])
                    if abs(x[0] - float(o["point"])) < 1e-9:
                        x[1 if side == "over" else 2] = int(o["price"])
        if P:
            cache[str(g["id"])] = dict(fetched=now.isoformat(), start=str(g["start"]), props=P)
            print(f"pinnacle nfl: {g['away']} @ {g['home']}: {len(P)} players ({used} credits, {bst.get('remaining')} left)")
        else:
            print(f"pinnacle nfl: {g['away']} @ {g['home']}: not posted yet (free, will retry)")
    json.dump(bst, open(B.STATE, "w"), indent=1, default=str)
    cache = {gid: c for gid, c in cache.items() if pd.Timestamp(c["start"]).tz_convert("UTC") > now - pd.Timedelta(hours=6)}
    os.makedirs(M.OUT, exist_ok=True)
    json.dump(cache, open(PIN_CACHE, "w"), separators=(",", ":"))


def p_over_s(dist, x):
    """Our over chance at any number x, interpolated between half-points so a shift moves it smoothly."""
    if not dist:
        return None
    lo = np.floor(x - .5) + .5
    a, b = p_over(dist, lo), p_over(dist, lo + 1)
    return a + (b - a) * (x - lo)


def pin_anchor(dist, pl, q):
    """Shift our distribution so its over chance at Pinnacle's line pl equals Pinnacle's no-vig q; returns the shift s
    (Pinnacle-anchored over chance at any line L = p_over_s(dist, L + s)), or None without a usable distribution."""
    if not dist or q is None or not .01 < q < .99:
        return None
    lo, hi = -400.0, 400.0
    if p_over_s(dist, pl + lo) < q or p_over_s(dist, pl + hi) > q:
        return None
    for _ in range(60):
        s = (lo + hi) / 2
        lo, hi = (s, hi) if p_over_s(dist, pl + s) > q else (lo, s)
    return (lo + hi) / 2


def pin_k(props):
    """Power de-vig exponent for one game: the k that makes Pinnacle's two-way lines sum to 1 (p_over^k + p_under^k = 1),
    median over the game's two-way props. One-way prices in that game are then de-vigged as p^k."""
    ks = []
    for kinds in props.values():
        for line, o, u in kinds.values():
            if o is None or u is None:
                continue
            a, b = 1 / dec(o), 1 / dec(u)
            if a + b <= 1:
                continue
            lo, hi = 1.0, 3.0
            for _ in range(50):
                k = (lo + hi) / 2
                lo, hi = (k, hi) if a ** k + b ** k > 1 else (lo, k)
            ks.append((lo + hi) / 2)
    return float(np.median(ks)) if ks else PIN_K


def pin_nv(mine, other, k):
    """Pinnacle no-vig chance for one side: multiplicative when both sides are posted, power (p^k) when one-way."""
    if mine is None:
        return None
    a = 1 / dec(mine)
    if other is None:
        return min(a ** k, .999)
    b = 1 / dec(other)
    return a / (a + b)


def pin_merge(now):
    """Put Pinnacle's no-vig chance on the published NFL props (props_nfl.json: e.pin = {l, o}) where its line matches,
    and on matching Value tags (flags_nfl.json: m), so the page judges NFL props against the sharp book."""
    try:
        cache = json.load(open(PIN_CACHE))
    except (OSError, ValueError):
        return
    cache = {gid: c for gid, c in cache.items() if pd.Timestamp(c["start"]).tz_convert("UTC") > now}
    if not cache:
        return
    fp, ff = os.path.join(M.SITE, "markets", "props_nfl.json"), os.path.join(M.SITE, "markets", "flags_nfl.json")
    n = 0
    _, by = nfl_ours()
    dist = {}
    for (gid, _n), me in (by or {}).items():
        dist[(str(gid), jsnorm(me["name"]))] = me["d"]
    try:
        PR = json.load(open(fp))
        for gid, c in cache.items():
            k = pin_k(c["props"])
            for nm, kinds in c["props"].items():
                for kind, (line, o, u) in kinds.items():
                    e = PR.get("props", {}).get(gid, {}).get(nm, {}).get(kind)
                    if not e or o is None and u is None:
                        continue
                    el = e.get("l", .5 if kind == "td" else None)
                    po = pin_nv(o, u, k) if o is not None else 1 - pin_nv(u, None, k)
                    if el is not None and abs(float(el) - line) < 1e-9:
                        e["pin"] = dict(l=line, o=round(po, 4), w=1 if o is None or u is None else 2)
                        n += 1
                    if kind == "td":
                        continue
                    # other numbers (the books' main line when it differs, and every alt line): Pinnacle's price carried
                    # over by our distribution's shape (w 3 = anchored; pl = Pinnacle's own line)
                    s = pin_anchor(dist.get((gid, nm), {}).get(kind), line, po)
                    if s is None:
                        continue
                    d = dist[(gid, nm)][kind]
                    if el is not None and abs(float(el) - line) >= 1e-9:
                        e["pin"] = dict(l=float(el), o=round(p_over_s(d, float(el) + s), 4), w=3, pl=line, po=round(po, 4))
                        n += 1
                    for al, x in (e.get("a") or {}).items():
                        x["pin"] = round(p_over_s(d, float(al) + s), 4)
        json.dump(PR, open(fp, "w"), separators=(",", ":"))
    except (OSError, ValueError):
        pass
    try:
        FL = json.load(open(ff))
        LAB = {"rec_yds": "rec yds", "rush_yds": "rush yds", "pass_yds": "pass yds", "rec": "receptions"}
        for gid, c in cache.items():
            k = pin_k(c["props"])
            for nm, kinds in c["props"].items():
                v = FL.get("tags", {}).get(gid, {}).get(nm)
                if not isinstance(v, dict):
                    continue
                for kind, (line, o, u) in kinds.items():
                    for sd, mine in (("over", o), ("under", u)):
                        other = u if sd == "over" else o
                        bet = "TD" if kind == "td" else f"{sd} {line:g} {LAB[kind]}"
                        if mine is not None and v.get("b") == bet:
                            v["pin"] = round(pin_nv(mine, other, k), 4)   # shown next to our tag; the tag's own rule is unchanged
        json.dump(FL, open(ff, "w"), separators=(",", ":"))
    except (OSError, ValueError):
        pass
    print(f"pinnacle nfl: de-vig attached to {n} props")


# ---------------------------------------------------------------- Pinnacle anytime goals for the NHL (The Odds API)
PIN_NHL_HOURS = 2.0                   # one pull per game in its last 2 hours before puck drop (1 credit when Pinnacle has it)
PIN_NHL_CACHE = os.path.join(M.OUT, "pin_props_nhl.json")
PIN_NHL_LOG = os.path.join(M.OUT, "pin_nhl_history.json")   # every pull kept, so Pinnacle can be graded against goals later


def pin_pull_nhl(now):
    """Pinnacle's two-way anytime-goal market (Yes / No, ~11 main scorers a game) for NHL games starting within 2 hours,
    once per game, inside books.py's monthly credit pacing. Cache: {gid: {start, props: {player: [yes, no]}}}."""
    k = B.key()
    if not k:
        return
    O = M.ours_nhl() or {}
    soon = {(g["away"], g["home"]): g for g in O.get("games", [])
            if now < pd.Timestamp(g["start"]).tz_convert("UTC") <= now + pd.Timedelta(hours=PIN_NHL_HOURS)}
    try:
        cache = json.load(open(PIN_NHL_CACHE))
    except (OSError, ValueError):
        cache = {}
    todo = {ga: g for ga, g in soon.items() if str(g["id"]) not in cache}
    if not todo:
        return
    r = requests.get("https://api.the-odds-api.com/v4/sports/icehockey_nhl/events", params=dict(apiKey=k), timeout=30)
    if r.status_code != 200:
        return
    try:
        log = json.load(open(PIN_NHL_LOG))
    except (OSError, ValueError):
        log = []
    bst = B.load_state()
    for e in r.json():
        g = todo.get((B.NAMES["nhl"].get(e["away_team"]), B.NAMES["nhl"].get(e["home_team"])))
        if not g:
            continue
        if not B.budget_ok(bst, now):
            print("pinnacle nhl: credit budget used for today"); break
        q = requests.get(f"https://api.the-odds-api.com/v4/sports/icehockey_nhl/events/{e['id']}/odds", timeout=30,
                         params=dict(apiKey=k, bookmakers="pinnacle", markets="player_goal_scorer_anytime", oddsFormat="american"))
        used = int(q.headers.get("x-requests-last") or 0)
        bst["spent_today"] = bst.get("spent_today", 0) + used
        if q.headers.get("x-requests-remaining"):
            bst["remaining"] = int(float(q.headers["x-requests-remaining"]))
        if q.status_code != 200:
            continue
        P = {}
        for b in q.json().get("bookmakers", []):
            for m in b.get("markets", []):
                for o in m.get("outcomes", []):
                    nm, side = o.get("description"), (o.get("name") or "").lower()
                    if nm and side in ("yes", "no"):
                        P.setdefault(jsnorm(nm), [None, None])[0 if side == "yes" else 1] = int(o["price"])
        if P:
            cache[str(g["id"])] = dict(fetched=now.isoformat(), start=str(g["start"]), props=P)
            log.append(dict(gid=int(g["id"]), fetched=now.isoformat(), start=str(g["start"]), props=P))
            print(f"pinnacle nhl: {g['away']} @ {g['home']}: {len(P)} players ({used} credits, {bst.get('remaining')} left)")
        else:
            print(f"pinnacle nhl: {g['away']} @ {g['home']}: not posted yet (free, will retry)")
    json.dump(bst, open(B.STATE, "w"), indent=1, default=str)
    cache = {gid: c for gid, c in cache.items() if pd.Timestamp(c["start"]).tz_convert("UTC") > now - pd.Timedelta(hours=6)}
    os.makedirs(M.OUT, exist_ok=True)
    json.dump(cache, open(PIN_NHL_CACHE, "w"), separators=(",", ":"))
    json.dump(log[-3000:], open(PIN_NHL_LOG, "w"), separators=(",", ":"))


def pin_merge_nhl(now):
    """Pinnacle's no-vig scoring chance on the published NHL anytime-goal props (props_nhl.json: goal.pin = {l: .5, o, w})
    and on matching Value tags (flags_nhl.json: m, the market half of the 50/50 blend, and pin for display)."""
    try:
        cache = json.load(open(PIN_NHL_CACHE))
    except (OSError, ValueError):
        return
    cache = {gid: c for gid, c in cache.items() if pd.Timestamp(c["start"]).tz_convert("UTC") > now}
    if not cache:
        return
    fp, ff = os.path.join(M.SITE, "markets", "props_nhl.json"), os.path.join(M.SITE, "markets", "flags_nhl.json")
    n = 0
    try:
        PR = json.load(open(fp))
        for gid, c in cache.items():
            for nm, (yes, no) in c["props"].items():
                nv = pin_nv(yes, no, 1.05) if yes is not None else None
                if nv is None:
                    continue
                e = PR.get("props", {}).get(gid, {}).get(nm, {}).get("goal")
                if e is not None:
                    e["pin"] = dict(l=.5, o=round(nv, 4), w=2 if no is not None else 1)
                    n += 1
        json.dump(PR, open(fp, "w"), separators=(",", ":"))
    except (OSError, ValueError):
        pass
    try:
        FL = json.load(open(ff))
        for gid, c in cache.items():
            for nm, (yes, no) in c["props"].items():
                v = FL.get("tags", {}).get(gid, {}).get(nm)
                if isinstance(v, dict) and v.get("b") == "goal" and yes is not None:
                    nv = pin_nv(yes, no, 1.05)
                    v["m"] = round(nv, 4)                      # Pinnacle replaces the exchange median as the market half
                    v["pin"] = round(nv, 4)
        json.dump(FL, open(ff, "w"), separators=(",", ":"))
    except (OSError, ValueError):
        pass
    print(f"pinnacle nhl: no-vig attached to {n} anytime-goal props")


def hr_merge(sport, now):
    """Add Hard Rock's prices to the published prices (props_<sport>.json) and Value tags (flags_<sport>.json)."""
    try:
        cache = json.load(open(HR_CACHE.format(sport)))
    except (OSError, ValueError):
        return
    cache = {gid: c for gid, c in cache.items() if pd.Timestamp(c["start"]).tz_convert("UTC") > now}
    if not cache:
        return
    # our chance + trust per player
    ours = {}
    if sport == "nhl":
        pl, _ = nhl_ours(now)
        for v in (pl or {}).values():
            ours[(str(v["gid"]), jsnorm(v["name"]))] = (v["p"], v["rank"] > 3)
    else:
        _, by = nfl_ours()
        for (gid, _n), v in (by or {}).items():
            if v["td"] is not None:
                ours[(str(gid), jsnorm(v["name"]))] = (v["td"], None)
    site = os.path.join(M.SITE, "markets")
    os.makedirs(site, exist_ok=True)
    fp, ff = os.path.join(site, f"props_{sport}.json"), os.path.join(site, f"flags_{sport}.json")
    stamp = now.tz_convert("America/New_York").isoformat(timespec="minutes")
    try:
        PR = json.load(open(fp))
        if now - pd.Timestamp(PR["fetched"]) > pd.Timedelta(hours=12):
            raise ValueError
    except (OSError, ValueError, KeyError):
        PR = dict(sport=sport, fetched=stamp, partner=U.PARTNER, props={})
    try:
        FL = json.load(open(ff))
    except (OSError, ValueError):
        FL = dict(sport=sport, fetched=stamp, min_ev=MIN_EV, source="hardrock", tags={})
    nt = 0
    for gid, c in cache.items():
        kind, bet = c["kind"], ("goal" if c["kind"] == "goal" else "TD")
        for nm, (o, link) in c["prices"].items():
            me = ours.get((gid, nm))
            if not me:
                continue
            p, warn = me
            if warn is None:
                warn = judge(p, o)[1]
            e = PR["props"].setdefault(gid, {}).setdefault(nm, {}).setdefault(kind, {})
            e.setdefault("p", round(float(p), 4))
            if warn:
                e["w"] = 1
            e.setdefault("o", {})["Hard Rock"] = [o, link]
            if warn or p * dec(o) - 1 < MIN_EV:
                continue
            tg = FL["tags"].setdefault(gid, {})
            v = tg.get(nm)
            if isinstance(v, dict):
                v.setdefault("all", {})["Hard Rock"] = [o, link]
                if dec(o) > dec(v["o"]):
                    v.update(o=o, k="Hard Rock", u=link)
            elif len(tg) < PER_GAME or nm in tg:
                tg[nm] = dict(b=bet, o=o, k="Hard Rock", u=link, p=round(float(p), 4), all={"Hard Rock": [o, link]})
                nt += 1
    if not FL.get("fetched") or now - pd.Timestamp(FL["fetched"]) > pd.Timedelta(hours=12):
        FL["fetched"] = stamp
    json.dump(PR, open(fp, "w"), separators=(",", ":"))
    json.dump(FL, open(ff, "w"), separators=(",", ":"))
    print(f"hard rock {sport}: merged {sum(len(c['prices']) for c in cache.values())} prices, {nt} new Value tags")


def write_props(sport, now):
    """Publish PROPS[sport] as <site>/markets/props_<sport>.json, keeping kinds this pull didn't get (< 12 h old)."""
    fp = os.path.join(M.SITE, "markets", f"props_{sport}.json")
    os.makedirs(os.path.dirname(fp), exist_ok=True)
    P = PROPS[sport]
    try:
        old = json.load(open(fp))
        if now - pd.Timestamp(old["fetched"]) < pd.Timedelta(hours=12):
            got = {k for g in P.values() for pl in g.values() for k in pl}
            for gid, g in old.get("props", {}).items():
                for nm, kinds in g.items():
                    for k, e in kinds.items():
                        if k not in got:
                            P.setdefault(gid, {}).setdefault(nm, {}).setdefault(k, e)
    except (OSError, ValueError, KeyError):
        pass
    json.dump(dict(sport=sport, fetched=now.tz_convert("America/New_York").isoformat(timespec="minutes"), partner=U.PARTNER, props=P),
              open(fp, "w"), separators=(",", ":"))


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
            within = lambda h: any(now < pd.Timestamp(g["start"]).tz_convert("UTC") <= now + pd.Timedelta(hours=h) for g in O.get("games", []))
            soon, soon_ua = within(WINDOW), within(UA_WINDOW[sport])
            if not soon_ua and not argv:
                print(f"sharp {sport}: no game in the next {UA_WINDOW[sport]} h"); continue
            rows, src = None, None
            ul = st.get("ua_" + sport)
            if U.key() and (argv or not ul or now - pd.Timestamp(ul) >= pd.Timedelta(minutes=UA_GAP)):     # Unabated: free, try often
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
                elif r == [] and PROPS.get(sport):                    # only shot prices came back: publish them, tags fall back below
                    write_props(sport, now)
                    print(f"sharp {sport}: Unabated shots only (goal props unavailable)")
                else:
                    print(f"sharp {sport}: Unabated props unavailable ({ok.get(sport, 0)} of {tries[sport]} tries have worked)")
            if rows is None:
                ufresh = ul and now - pd.Timestamp(ul) < pd.Timedelta(minutes=UA_GAP)
                last = st.get(sport)
                if not key() or ufresh or (not argv and (not soon or (last and now - pd.Timestamp(last) < pd.Timedelta(hours=MIN_GAP)))):
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
            if src == "unabated" and PROPS.get(sport):
                write_props(sport, now)
            print(f"sharp {sport}: {src}, {len(rows)} priced bets, {sum(len(g) for g in T.values())} tagged"
                  + (f", prices for {sum(len(g) for g in PROPS[sport].values())} players" if src == "unabated" and PROPS.get(sport) else ""))
        except Exception as e:                                   # never break the markets run
            print(f"sharp {sport}: skipped ({type(e).__name__}: {e})")
    for sport in ("nhl", "nfl"):                                # Hard Rock props, then merged into the published files
        if argv and sport not in argv:
            continue
        try:
            hr_pull(sport, now, st)
            hr_merge(sport, now)
            if sport == "nfl":
                pin_pull(now)
                pin_merge(now)
            if sport == "nhl":
                pin_pull_nhl(now)
                pin_merge_nhl(now)
        except Exception as e:
            print(f"hard rock {sport}: skipped ({type(e).__name__}: {e})")
    os.makedirs(M.OUT, exist_ok=True)
    json.dump(st, open(STATE, "w"), indent=1)


if __name__ == "__main__":
    main(sys.argv[1:])
