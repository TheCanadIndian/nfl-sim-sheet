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
import math
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


# Weight on our number when the market half is Pinnacle's no-vig line (research/blend_weights.py: 2025-26 closing props, best weight 0-0.4 by half; about 0.2 NHL goals, 0.1 NBA, 0.25 NFL yardage / catches, 0 anytime TD). Without a Pinnacle line the 50/50 with the exchanges tested on Kalshi stays.
WPIN = {"nhl": {"goal": .2}, "nba": {"pts": .05, "reb": .05, "ast": .05, "fg3m": .05},
        "nfl": {"rec": .25, "rec_yds": .25, "rush_yds": .25, "pass_yds": .25, "td": 0.0}}


# Sharp consensus (research/consensus_test.py): the log-odds mean of Pinnacle's, Novig's and ProphetX's no-vig at the same line beat Pinnacle alone on NBA props 2025-26 (-0.0023 log-loss, z -2.5, all 4 stats) and NFL 2026 (-0.0021, z -2.3), flat on NFL 2025; tight Kalshi added nothing beyond Novig, so Kalshi stays out. Leave-one-out: a bet at a book is judged against the consensus without that book.
SHARP_SRC = ("Novig", "ProphetX")      # two-way no-vig, alongside Pinnacle (e.pin)


def _lg(p):
    p = min(max(p, 1e-4), 1 - 1e-4)
    return math.log(p / (1 - p))


def cons_of(parts, exclude=None):
    """Log-odds mean of the sources' over chances, without `exclude` (the book being bet) when others remain."""
    xs = [v for k, v in parts.items() if k != exclude] or list(parts.values())
    return 1 / (1 + math.exp(-sum(_lg(v) for v in xs) / len(xs)))


def apply_consensus(sport, now):
    """Turn each published prop's sharp reference (e.pin) into the consensus of Pinnacle, Novig and ProphetX at its line,
    keeping the parts (the page drops the book being bet) and Pinnacle's own number (pn); then re-judge Value tags on it."""
    fp, ff = os.path.join(M.SITE, "markets", f"props_{sport}.json"), os.path.join(M.SITE, "markets", f"flags_{sport}.json")
    try:
        PR = json.load(open(fp))
    except (OSError, ValueError):
        return
    n = 0
    for gid, g in PR.get("props", {}).items():
        for nm, kinds in g.items():
            for kind, e in kinds.items():
                el = e.get("l", .5)
                parts = {}
                pin = e.get("pin")
                if pin and pin.get("o") is not None and abs(float(pin.get("l", .5)) - float(el)) < 1e-9:
                    parts["Pinnacle"] = pin.get("pn", pin["o"])
                for bk in SHARP_SRC:
                    o, u = (e.get("o") or {}).get(bk), (e.get("u") or {}).get(bk)
                    if o and u:
                        a_, b_ = 1 / dec(o[0]), 1 / dec(u[0])
                        parts[bk] = a_ / (a_ + b_)
                if "Pinnacle" not in parts and "Novig" not in parts:
                    continue
                c = cons_of(parts)
                if pin and "Pinnacle" in parts:
                    pin.update(pn=round(parts["Pinnacle"], 4), o=round(c, 4), parts={k: round(v, 4) for k, v in parts.items()})
                    dl = _lg(c) - _lg(parts["Pinnacle"])              # alt lines carried from Pinnacle move with the consensus
                    for x in (e.get("a") or {}).values():
                        if x.get("pin") is not None and "pin_p" not in x:
                            x["pin_p"] = x["pin"]
                            x["pin"] = round(1 / (1 + math.exp(-(_lg(x["pin"]) + dl))), 4)
                else:
                    e["pin"] = dict(l=float(el), o=round(c, 4), w=2, parts={k: round(v, 4) for k, v in parts.items()})
                n += len(parts) > 1
    # alt lines (and a books' main line that differs from Pinnacle's): the books' own two-way prices at that number beat
    # carrying Pinnacle over by our distribution, which breaks when we sit far from the market (2026 wk 6: Cyrus Allen
    # receiving yards, carried fair ~100% at 11.5 vs 48% at 12.5). 2+ books posting both sides: their no-vig median;
    # 1 book: averaged with the carried number; none: the carried number only within 15% of Pinnacle's line.
    for gid, g in PR.get("props", {}).items():
        for nm, kinds in g.items():
            for kind, e in kinds.items():
                pin = e.get("pin") or {}
                pl = pin.get("pl", pin.get("l"))
                spots = [(float(l), x, False) for l, x in (e.get("a") or {}).items()]
                if pin.get("w") == 3 and e.get("l") is not None:
                    spots.append((float(e["l"]), e, True))
                for line, x, is_main in spots:
                    o, u = x.get("o") or {}, x.get("u") or {}
                    nv = sorted(1 / dec(o[k][0]) / (1 / dec(o[k][0]) + 1 / dec(u[k][0])) for k in set(o) & set(u)
                                if k not in ("Pinnacle", "Kalshi") and isinstance(o[k], list) and isinstance(u[k], list))
                    carried = (pin.get("o") if is_main else x.get("pin"))
                    near = pl is not None and abs(line - float(pl)) <= max(1.0, .15 * abs(float(pl)))
                    if len(nv) >= 2:
                        fair = nv[len(nv) // 2] if len(nv) % 2 else (nv[len(nv) // 2 - 1] + nv[len(nv) // 2]) / 2
                    elif len(nv) == 1 and carried is not None:
                        fair = 1 / (1 + math.exp(-(_lg(nv[0]) + _lg(carried)) / 2))
                    elif len(nv) == 1:
                        fair = nv[0]
                    else:
                        fair = carried if near else None
                    if is_main:
                        if fair is None:
                            e.pop("pin", None)
                        else:                                         # the carried number no longer feeds the consensus
                            pin.update(o=round(fair, 4), parts={"Books": round(fair, 4)})
                            if not near:
                                pin.pop("pn", None)
                    else:
                        if fair is None:
                            x.pop("pin", None)
                        else:
                            x["pin"] = round(fair, 4)
                # a fair over chance can't rise with the line: pool adjacent out-of-order lines (isotonic, falling)
                lad = sorted([(float(e["l"]), e["pin"], "o")] if e.get("pin") and e.get("l") is not None and e["pin"].get("l") == e.get("l") else [], key=lambda t: t[0])
                lad += [(float(l), x, "pin") for l, x in (e.get("a") or {}).items() if x.get("pin") is not None]
                lad.sort(key=lambda t: t[0])
                if len(lad) >= 2:
                    blocks = []
                    for i, (_, obj, key) in enumerate(lad):
                        blocks.append([obj[key], 1, [i]])
                        while len(blocks) > 1 and blocks[-2][0] < blocks[-1][0]:
                            v2, n2, ix2 = blocks.pop(); v1, n1, ix1 = blocks.pop()
                            blocks.append([(v1 * n1 + v2 * n2) / (n1 + n2), n1 + n2, ix1 + ix2])
                    for v, _, ix in blocks:
                        for i in ix:
                            lad[i][1][lad[i][2]] = round(v, 4)
    json.dump(PR, open(fp, "w"), separators=(",", ":"))
    try:
        FL = json.load(open(ff))
    except (OSError, ValueError):                             # no Value-tag file (NBA): one for the LC tags
        FL = dict(sport=sport, fetched=now.isoformat() if hasattr(now, "isoformat") else str(now), tags={})
    if FL:
        LABK = {"rec yds": "rec_yds", "rush yds": "rush_yds", "pass yds": "pass_yds", "receptions": "rec"}
        for gid, tags in list(FL.get("tags", {}).items()):
            for nm, v in list(tags.items()):
                if not isinstance(v, dict) or v.get("p") is None:
                    continue
                b = str(v.get("b") or "")
                if b == "goal":
                    kind, side = "goal", "over"
                elif b == "TD":
                    kind, side = "td", "over"
                else:
                    sp = b.split(" ", 2)
                    if len(sp) < 3 or sp[2] not in LABK:
                        continue
                    kind, side = LABK[sp[2]], sp[0]
                e = PR.get("props", {}).get(gid, {}).get(nm, {}).get(kind)
                parts = ((e or {}).get("pin") or {}).get("parts")
                if not parts:
                    continue
                po = cons_of(parts, exclude=v.get("k"))
                rejudge(FL, gid, nm, v, WPIN.get(sport, {}).get(kind, .5), po if side == "over" else 1 - po)
    if FL is not None:
        try:
            FL["lc"] = lc_tags(sport, PR)
        except Exception as e:                                                  # noqa: BLE001
            print(f"sharp {sport}: liquidity-consensus tags failed ({type(e).__name__}: {e})")
        try:
            FL["off"] = off_market(sport, PR)
            print(f"sharp {sport}: {len(FL['off'])} off-market prices")
        except Exception as e:                                                  # noqa: BLE001
            print(f"sharp {sport}: off-market scan failed ({type(e).__name__}: {e})")
        json.dump(FL, open(ff, "w"), separators=(",", ":"))
        print(f"sharp {sport}: {sum(len(v) for v in FL.get('lc', {}).values())} liquidity-consensus tags")
    print(f"sharp {sport}: consensus reference on {n} props")


# Liquidity-consensus tags (owner 2026-10-21: "apply tags to the props when they beat our consensus weight"; experimental,
# graded as "LC tags" on /profit.html). Fair over chance = the sharp consensus (Pinnacle / Novig / ProphetX, without the
# book being bet) and Kalshi's mid at the same line in log-odds, Kalshi weighted 2 x depth / (depth + $200) (depth =
# dollars to buy within 3c; research/pm_liquidity_consensus.py: deep Kalshi out-predicted the consensus Oct 18-21, z -1.5).
# Chance = WPIN weight on ours, the rest that fair. Tag: the best over price at any book except Kalshi / Pinnacle beats
# the chance by 5-40%. Overs only (the owner plays overs); never on Kalshi itself (its price is in the fair).
LC_KIND = {"anytime_td": "td"}


def lc_tags(sport, PR):
    """{gid: {name: tag}} -- the owner's books only (Hard Rock, Kalshi, Novig; he bets Hard Rock / Kalshi most). The fair
    for a bet never includes the book being bet: at Kalshi it's the consensus with Polymarket (depth-weighted), at a
    sportsbook the consensus with Kalshi and Polymarket (each weighted 2 x depth / (depth + $200))."""
    try:
        L = json.load(open(os.path.join(M.SITE, "markets", f"live_{sport}.json")))
    except (OSError, ValueError):
        return {}
    pm = {}                                                   # (gid, name, kind, line) -> {source: (mid, depth, row)}
    for r in L.get("rows", []):
        if r.get("s") not in ("kalshi", "polymarket") or not r.get("n") or r.get("ya") is None or not r.get("yb") or r["ya"] - r["yb"] > .035 or not r.get("by"):
            continue
        k = LC_KIND.get(r.get("k"), r.get("k"))
        key = (str(r.get("g")), jsnorm(r["n"]), k, round(float(r["l"] if r.get("l") is not None else .5), 2))
        pm.setdefault(key, {})[r["s"]] = ((r["ya"] + r["yb"]) / 2, float(r["by"]), r)
    out = {}
    for gid, g in PR.get("props", {}).items():
        for nm, kinds in g.items():
            for kind, e in kinds.items():
                pin, line = e.get("pin") or {}, round(float(e["l"] if e.get("l") is not None else .5), 2)
                parts = pin.get("parts")
                if not parts or e.get("p") is None or (pin.get("l") is not None and abs(float(pin["l"]) - line) > 1e-9):
                    continue
                P = pm.get((str(gid), nm, kind, line), {})
                if not P:
                    continue
                w = WPIN.get(sport, {}).get(kind, .5)
                def fair(skip):
                    lo, wt = _lg(cons_of(parts, exclude=skip)), 1.0
                    for src, (mid, dep, _) in P.items():
                        if src != skip.lower():
                            wk = 2 * dep / (dep + 200); lo += wk * _lg(mid); wt += wk
                    return 1 / (1 + math.exp(-lo / wt))
                cands = []
                for bk in ("Hard Rock", "Novig"):                     # sportsbook / exchange prices from the props feed
                    v = (e.get("o") or {}).get(bk)
                    if isinstance(v, list) and v[0] is not None:
                        f = fair(bk); q = w * e["p"] + (1 - w) * f
                        cands.append(dict(k=bk, o=v[0], u=v[1] if len(v) > 1 else None, m=round(f, 4), q=round(q, 4), ev=round(q * dec(v[0]) - 1, 4)))
                if "kalshi" in P:                                      # Kalshi: its ask plus the 7% fee
                    mid, dep, r = P["kalshi"]
                    f = fair("Kalshi"); q = w * e["p"] + (1 - w) * f
                    cost = r["ya"] + .07 * r["ya"] * (1 - r["ya"])
                    o = -round(100 * cost / (1 - cost)) if cost >= .5 else round(100 * (1 - cost) / cost)
                    cands.append(dict(k="Kalshi", o=o, u=None, m=round(f, 4), q=round(q, 4), ev=round(q / cost - 1, 4)))
                cands = [c for c in cands if .05 <= c["ev"] <= .40]
                if cands:
                    best = max(cands, key=lambda c: c["ev"])
                    best.update(p=round(e["p"], 4), b="TD" if kind == "td" else "goal" if kind == "goal" else f"over {line:g} {LC_LAB.get(kind, kind)}",
                                all={c["k"]: [c["o"], round(c["ev"], 4)] for c in cands})
                    out.setdefault(gid, {})[nm] = best
    return out


LC_LAB = {"rec_yds": "rec yds", "rush_yds": "rush yds", "pass_yds": "pass yds", "rec": "receptions", "pts": "points", "reb": "rebounds",
          "ast": "assists", "fg3m": "threes"}


# Off-market prices (owner 2026-10-21: "any props way off market, alt props across all books and prediction markets").
# Every over price -- every book, Kalshi, Polymarket, main and alt lines -- against the fair at that exact line: main line
# = the sharp consensus; alt line = the books' own two-way fair there; a rung no book prices two-way = the main fair
# carried along our projection (same log-odds gap between our chance and the fair as at the main line). Flagged when the
# price's implied chance (Kalshi with its fee) is 8+ pts under the fair, or 35%+ under it and 4+ pts (longshots).
# Overs only (the owner plays overs). Tracked on /profit.html as "Off-market prices (experimental)": a price this far
# off is sometimes a stale or wrong line, so check it before betting.
OFF_GAP, OFF_REL, OFF_MIN = .08, .35, .04


def off_market(sport, PR):
    try:
        L = json.load(open(os.path.join(M.SITE, "markets", f"live_{sport}.json")))
    except (OSError, ValueError):
        L = {}
    pm = {}
    for r in L.get("rows", []):
        if r.get("s") in ("kalshi", "polymarket") and r.get("n") and r.get("ya") and 0 < r["ya"] < 1 and r.get("l", 0) is not None:
            k = LC_KIND.get(r.get("k"), r.get("k"))
            pm.setdefault((str(r.get("g")), jsnorm(r["n"]), k), []).append(r)
    out = []
    for gid, g in PR.get("props", {}).items():
        for nm, kinds in g.items():
            for kind, e in kinds.items():
                pin = e.get("pin") or {}
                ml = round(float(e["l"] if e.get("l") is not None else .5), 2)
                fm = pin.get("o") if pin.get("o") is not None and (pin.get("l") is None or abs(float(pin["l"]) - ml) < 1e-9) else None
                if fm is None:
                    continue
                gap = _lg(fm) - _lg(e["p"]) if e.get("p") and 0 < e["p"] < 1 else None
                ladder = {ml: dict(fair=fm, carried=False, prices=e.get("o") or {})}
                for al, x in (e.get("a") or {}).items():
                    f = x.get("pin")
                    carried = f is None
                    if f is None and gap is not None and x.get("p") and 0 < x["p"] < 1:
                        f = 1 / (1 + math.exp(-(_lg(x["p"]) + gap)))
                    if f is not None:
                        ladder[round(float(al), 2)] = dict(fair=f, carried=carried, prices=x.get("o") or {})
                for r in pm.get((str(gid), nm, kind), []):
                    ln = round(float(r["l"] if r.get("l") is not None else .5), 2)
                    if ln not in ladder:
                        po = (r.get("p") or {}).get("vegas")
                        if gap is None or not po or not 0 < po < 1:
                            continue
                        ladder[ln] = dict(fair=1 / (1 + math.exp(-(_lg(po) + gap))), carried=True, prices={})
                    src = "Kalshi" if r["s"] == "kalshi" else "Polymarket"
                    cost = r["ya"] + (.07 * r["ya"] * (1 - r["ya"]) if r["s"] == "kalshi" else 0)
                    o = -round(100 * cost / (1 - cost)) if cost >= .5 else round(100 * (1 - cost) / cost)
                    ladder[ln]["prices"] = dict(ladder[ln]["prices"], **{src: [o, None, round(float(r.get("by") or 0))]})
                for ln, spot in ladder.items():
                    f = spot["fair"]
                    if not .03 <= f <= .97:
                        continue
                    for bk, v in spot["prices"].items():
                        if bk == "Pinnacle" or not isinstance(v, list) or v[0] is None:
                            continue
                        imp = 1 / dec(v[0])
                        d = f - imp
                        if d >= OFF_GAP or (f / imp - 1 >= OFF_REL and d >= OFF_MIN):
                            out.append(dict(g=gid, n=nm, k=kind, l=ln, main=ln == ml, bk=bk, o=v[0], u=v[1] if len(v) > 1 else None,
                                            dep=v[2] if len(v) > 2 else None, imp=round(imp, 4), fair=round(f, 4), carried=spot["carried"],
                                            ev=round(f / imp - 1, 4), b="TD" if kind == "td" else "goal" if kind == "goal" else f"over {ln:g} {LC_LAB.get(kind, kind)}"))
    out.sort(key=lambda x: -(x["fair"] - x["imp"]))
    return out[:80]


def rejudge(FL, gid, nm, v, w, pin):
    """A Value tag with Pinnacle's no-vig as the market half at weight w on ours: keep it only if it still clears the bar."""
    v["m"], v["pin"], v["wp"] = round(pin, 4), round(pin, 4), w
    if v.get("p") is None or v.get("o") is None:
        return
    if (w * v["p"] + (1 - w) * pin) * dec(v["o"]) - 1 < FL.get("min_ev", .05):
        FL["tags"][gid].pop(nm, None)


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
    # NFL yardage / catch props: only the side our median is on, our number 10+ pts from the market, and on exchanges a tight (3c or less), 15c+ market. Kalshi 2026 wk 1-5: the plain 5%-edge rule lost 5% in both halves; this cut it to about breakeven (-0.6% / +7.7%) by dropping far-side ladder rungs and thin markets.
    if blend and bet.startswith(("over ", "under ")) and (p < .5 or (nv is not None and abs(p - nv) < .10)):
        chk = True
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
HR_MKT = {"nhl": ("icehockey_nhl", "player_goal_scorer_anytime", "goal", 6), "nfl": ("americanfootball_nfl", "player_anytime_td", "td", 6)}
HR_REFRESH = 60                       # minutes: re-pull a game's Hard Rock prices this often (paid plan)


def stale(c, now, minutes):
    """True when a cached pull is missing or older than `minutes` (refresh within the credit pacing)."""
    return not c or now - pd.Timestamp(c.get("fetched", "2000-01-01")).tz_convert("UTC") >= pd.Timedelta(minutes=minutes)
HR_CACHE = os.path.join(M.OUT, "hr_props_{}.json")
MORNING_HOUR, FAST = 8, 30           # owner 2026-10-23: one early-morning pull for early value, then every 30 min


OPEN_PROBE_MIN = 60                    # minutes between opening checks of a game we have no prices for (empty answers are free)
OPEN_H = {"nfl": 120, "nhl": 36}     # Hard Rock over / unders: how far ahead the opening watch looks


def wanted(games, cache, now, window_h, base_min, bst, slow_h=None, fast_min=None, open_h=None):
    """Games due a paid prop pull: inside a game's window (last window_h hours) every FAST minutes while today's credits
    are on pace (books.tight: 75% of the day's allowance spent -> back to base_min, so the pulls nearest each game are
    never the ones the budget cuts); outside it, one pull of every game later today from MORNING_HOUR ET on.
    slow_h: a wider window pulled every base_min before the 30-minute one starts (NBA: every 2 h from 8 h out, every
    30 min in the last 4 h; owner 2026-10-23, 12-game nights). An empty answer (the book hasn't posted yet) stores
    nothing and is free, so it simply retries next run.
    open_h (owner 2026-10-26: "figure out when books first post lines and do a capture then"): games starting within open_h
    hours that we have never received prices for are asked hourly (OPEN_PROBE_MIN) -- free while the book hasn't posted --
    so the first answer with prices is the opening capture (research/opening_lines.py: NBA props were only ever seen from
    our 8 AM pull, Hard Rock NFL only inside 6 h; openers already 3%+ under the sharp fair beat the close 57% of the time)."""
    et = now.tz_convert("America/New_York")
    morning = et.normalize() + pd.Timedelta(hours=MORNING_HOUR)
    every = base_min if B.tight(bst, now) else (fast_min or FAST)
    out = {}
    probe = bst.setdefault("open_probe", {})
    for ga, g in games.items():
        st = pd.Timestamp(g["start"]).tz_convert("UTC")
        if st <= now:
            continue
        c = cache.get(str(g["id"]))
        if open_h and (not c or not c.get("props")) and st <= now + pd.Timedelta(hours=open_h):
            last = probe.get(str(g["id"]))
            if not last or now - pd.Timestamp(last) >= pd.Timedelta(minutes=OPEN_PROBE_MIN):
                probe[str(g["id"])] = now.isoformat()
                out[ga] = g
            continue
        if st <= now + pd.Timedelta(hours=window_h):
            if stale(c, now, every):
                out[ga] = g
        elif slow_h and st <= now + pd.Timedelta(hours=slow_h):
            if stale(c, now, base_min):
                out[ga] = g
        elif st.tz_convert("America/New_York").date() == et.date() and et >= morning:
            if not c or pd.Timestamp(c.get("fetched", "2000-01-01")).tz_convert("America/New_York") < morning:
                out[ga] = g
    return out


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
            if now < pd.Timestamp(g["start"]).tz_convert("UTC") <= now + pd.Timedelta(hours=24)}
    try:
        cache = json.load(open(HR_CACHE.format(sport)))
    except (OSError, ValueError):
        cache = {}
    bst = B.load_state()
    todo = wanted(soon, cache, now, hours, HR_REFRESH, bst)
    if not todo:
        return
    r = requests.get(f"https://api.the-odds-api.com/v4/sports/{key}/events", params=dict(apiKey=k), timeout=30)   # free
    if r.status_code != 200:
        return
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
PIN_HOURS = 24.0                      # games within a day of kickoff (paid plan; was one pull 3.5 h out)
PIN_REFRESH = 120                     # minutes between pulls of a game (Pinnacle's lines sharpen toward kickoff)
PIN_CACHE = os.path.join(M.OUT, "pin_props_nfl.json")
PIN_NFL_LOG = os.path.join(M.OUT, "pin_nfl_history.json")   # every pull kept (closing-line grading in profit.py)


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
    todo = {ga: g for ga, g in soon.items() if stale(cache.get(str(g["id"])), now, PIN_REFRESH)}
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
                    if kind == "td" and side in ("yes", "no"):          # Pinnacle posts both sides: two-way de-vig, not the one-way curve
                        side, o = ("over" if side == "yes" else "under"), dict(o, point=.5)
                    if not kind or not nm or side not in ("over", "under") or o.get("point") is None:
                        continue
                    x = P.setdefault(jsnorm(nm), {}).setdefault(kind, [float(o["point"]), None, None])
                    if abs(x[0] - float(o["point"])) < 1e-9:
                        x[1 if side == "over" else 2] = int(o["price"])
        if P:
            cache[str(g["id"])] = dict(fetched=now.isoformat(), start=str(g["start"]), props=P)
            try:
                nlog = json.load(open(PIN_NFL_LOG))
            except (OSError, ValueError):
                nlog = []
            nlog.append(dict(gid=str(g["id"]), fetched=now.isoformat(), start=str(g["start"]), props=P))
            json.dump(nlog[-4000:], open(PIN_NFL_LOG, "w"), separators=(",", ":"))
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
                            rejudge(FL, gid, nm, v, WPIN["nfl"].get(kind, .5), pin_nv(mine, other, k))
        json.dump(FL, open(ff, "w"), separators=(",", ":"))
    except (OSError, ValueError):
        pass
    print(f"pinnacle nfl: de-vig attached to {n} props")


# ---------------------------------------------------------------- Pinnacle anytime goals for the NHL (The Odds API)
PIN_NHL_HOURS = 8.0                   # games within 8 hours of puck drop (paid plan; 1 credit a pull when Pinnacle has it)
PIN_NHL_REFRESH = 60                  # minutes between pulls; every pull is logged, so the drift to puck drop can be graded
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
    todo = {ga: g for ga, g in soon.items() if stale(cache.get(str(g["id"])), now, PIN_NHL_REFRESH)}
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
                    rejudge(FL, gid, nm, v, WPIN["nhl"]["goal"], pin_nv(yes, no, 1.05))
        json.dump(FL, open(ff, "w"), separators=(",", ":"))
    except (OSError, ValueError):
        pass
    print(f"pinnacle nhl: no-vig attached to {n} anytime-goal props")


# ---------------------------------------------------------------- Hard Rock over/under props (The Odds API)
HR_OU = {"nfl": ("americanfootball_nfl", {"player_reception_yds": "rec_yds", "player_receptions": "rec", "player_rush_yds": "rush_yds",
                                          "player_pass_yds": "pass_yds"}),
         "nhl": ("icehockey_nhl", {"player_shots_on_goal": "sog"})}
HR_OU_CACHE = os.path.join(M.OUT, "hr_ou_{}.json")


def hr_ou_pull(sport, now):
    """Hard Rock's over/under player props (NFL yardage and catches, NHL shots on goal), games within 6 h, hourly; about
    1 credit per market per game. Cache: {gid: {fetched, start, props: {player: {kind: {line: {o: [odds, link], u: [...]}}}}}}."""
    k = B.key()
    if not k or sport not in HR_OU:
        return
    key, mkts = HR_OU[sport]
    O = (M.ours_nhl if sport == "nhl" else M.ours_nfl)() or {}
    soon = {(g["away"], g["home"]): g for g in O.get("games", [])
            if now < pd.Timestamp(g["start"]).tz_convert("UTC") <= now + pd.Timedelta(hours=OPEN_H[sport])}   # NFL 5 days / NHL 36 h: the opening watch
    f = HR_OU_CACHE.format(sport)
    try:
        cache = json.load(open(f))
    except (OSError, ValueError):
        cache = {}
    bst = B.load_state()
    todo = wanted(soon, cache, now, 6, HR_REFRESH, bst, open_h=OPEN_H[sport])
    if todo:
        r = requests.get(f"https://api.the-odds-api.com/v4/sports/{key}/events", params=dict(apiKey=k), timeout=30)   # free
        for e in (r.json() if r.status_code == 200 else []):
            g = todo.get((B.NAMES[sport].get(e["away_team"]), B.NAMES[sport].get(e["home_team"])))
            if not g:
                continue
            if not B.budget_ok(bst, now):
                print(f"hard rock {sport} o/u: credit budget used for today"); break
            q = requests.get(f"https://api.the-odds-api.com/v4/sports/{key}/events/{e['id']}/odds", timeout=30,
                             params=dict(apiKey=k, bookmakers="hardrockbet", markets=",".join(mkts), oddsFormat="american", includeLinks="true"))
            used = int(q.headers.get("x-requests-last") or 0)
            bst["spent_today"] = bst.get("spent_today", 0) + used
            if q.headers.get("x-requests-remaining"):
                bst["remaining"] = int(float(q.headers["x-requests-remaining"]))
            if q.status_code != 200:
                continue
            P = {}
            for b in q.json().get("bookmakers", []):
                for m in b.get("markets", []):
                    kind = mkts.get(m["key"])
                    for o in m.get("outcomes", []):
                        side = {"over": "o", "under": "u"}.get((o.get("name") or "").lower())
                        if kind and side and o.get("point") is not None and o.get("description"):
                            P.setdefault(jsnorm(o["description"]), {}).setdefault(kind, {}).setdefault(f"{float(o['point']):g}", {})[side] = \
                                [int(o["price"]), _link(o.get("link") or b.get("link"))]
            cache[str(g["id"])] = dict(fetched=now.isoformat(), start=str(g["start"]), props=P)
            print(f"hard rock {sport} o/u: {g['away']} @ {g['home']}: {len(P)} players ({used} credits, {bst.get('remaining')} left)")
        json.dump(bst, open(B.STATE, "w"), indent=1, default=str)
    cache = {gid: c for gid, c in cache.items() if pd.Timestamp(c["start"]).tz_convert("UTC") > now - pd.Timedelta(hours=6)}
    os.makedirs(M.OUT, exist_ok=True)
    json.dump(cache, open(f, "w"), separators=(",", ":"))


# NFL alternate lines (owner 2026-10-25: "can we pull alt lines for nfl as well"): Hard Rock + Novig alternate ladders for
# receiving yards, catches, rushing and passing yards. One Odds API call per game (4 alternate markets, both books in it:
# 4 credits); the NBA alt schedule -- one morning pull, then hourly in the last 2 h (sharp.wanted) -- about 150 credits on a
# full Sunday (folding them into the 30-min Hard Rock pull would have cost ~700). Merged onto each player's ladder.
NFL_ALT_MKT = {"player_reception_yds_alternate": "rec_yds", "player_receptions_alternate": "rec", "player_rush_yds_alternate": "rush_yds",
               "player_pass_yds_alternate": "pass_yds"}
NFL_ALT_CACHE = os.path.join(M.OUT, "oa_props_nfl_alt.json")


def nfl_alt_pull(now):
    """Cache: {gid: {fetched, start, props: {player: {kind: {line: {o|u: {book: [odds, link]}}}}}}}."""
    k = B.key()
    O = M.ours_nfl() or {}
    if not k or not O:
        return
    games = {(g["away"], g["home"]): g for g in O.get("games", [])
             if now < pd.Timestamp(g["start"]).tz_convert("UTC") <= now + pd.Timedelta(hours=24)}
    try:
        cache = json.load(open(NFL_ALT_CACHE))
    except (OSError, ValueError):
        cache = {}
    bst = B.load_state()
    todo = wanted(games, cache, now, 2.0, 60, bst, fast_min=60)
    if todo:
        r = requests.get("https://api.the-odds-api.com/v4/sports/americanfootball_nfl/events", params=dict(apiKey=k), timeout=30)   # free
        for e in (r.json() if r.status_code == 200 else []):
            g = todo.get((B.NAMES["nfl"].get(e["away_team"]), B.NAMES["nfl"].get(e["home_team"])))
            if not g:
                continue
            if not B.budget_ok(bst, now):
                print("odds api nfl alt lines: credit budget used for today"); break
            q = requests.get(f"https://api.the-odds-api.com/v4/sports/americanfootball_nfl/events/{e['id']}/odds", timeout=30,
                             params=dict(apiKey=k, bookmakers="hardrockbet,novig", markets=",".join(NFL_ALT_MKT), oddsFormat="american", includeLinks="true"))
            used = int(q.headers.get("x-requests-last") or 0)
            bst["spent_today"] = bst.get("spent_today", 0) + used
            if q.headers.get("x-requests-remaining"):
                bst["remaining"] = int(float(q.headers["x-requests-remaining"]))
            if q.status_code != 200:
                continue
            P = {}
            for b in q.json().get("bookmakers", []):
                for m in b.get("markets", []):
                    kind = NFL_ALT_MKT.get(m["key"])
                    for o in m.get("outcomes", []):
                        side = {"over": "o", "under": "u"}.get((o.get("name") or "").lower())
                        if not kind or not side or o.get("point") is None or not o.get("description"):
                            continue
                        P.setdefault(jsnorm(o["description"]), {}).setdefault(kind, {}).setdefault(f"{float(o['point']):g}", {}) \
                            .setdefault(side, {})[NBA_SHORT.get(b["key"], b["key"])] = [int(o["price"]), _link(o.get("link") or b.get("link"))]
            cache[str(g["id"])] = dict(fetched=now.isoformat(), start=str(g["start"]), props=P)
            print(f"odds api nfl alt lines: {g['away']} @ {g['home']}: {len(P)} players ({used} credits, {bst.get('remaining')} left)")
        json.dump(bst, open(B.STATE, "w"), indent=1, default=str)
    cache = {gid: c for gid, c in cache.items() if pd.Timestamp(c["start"]).tz_convert("UTC") > now - pd.Timedelta(hours=6)}
    os.makedirs(M.OUT, exist_ok=True)
    json.dump(cache, open(NFL_ALT_CACHE, "w"), separators=(",", ":"))


def nfl_alt_merge(now):
    """Each alt price onto the published props: the main line when it matches, else that line's alt entry; our chance at
    a new rung from the player's saved distribution (like hr_ou_merge)."""
    try:
        cache = json.load(open(NFL_ALT_CACHE))
        fp = os.path.join(M.SITE, "markets", "props_nfl.json")
        PR = json.load(open(fp))
    except (OSError, ValueError):
        return
    _, by = nfl_ours()
    dist = {(str(g_), jsnorm(me["name"])): me["d"] for (g_, _n), me in (by or {}).items()}
    n = 0
    for gid, c in cache.items():
        if pd.Timestamp(c["start"]).tz_convert("UTC") <= now:
            continue
        G = PR.get("props", {}).get(gid, {})
        for nm, kinds in c["props"].items():
            pl = G.get(nm)
            if not pl:
                continue
            for kind, lines in kinds.items():
                e = pl.get(kind)
                if not e:
                    continue
                for line, sides in lines.items():
                    tgt = e if e.get("l") is not None and abs(float(e["l"]) - float(line)) < 1e-9 else e.setdefault("a", {}).setdefault(line, {})
                    for sd, books in sides.items():
                        for bk, v in books.items():
                            tgt.setdefault(sd, {})[bk] = v
                            n += 1
                    if tgt.get("p") is None:
                        d = dist.get((gid, nm), {}).get(kind)
                        po = p_over(d, float(line)) if d else None
                        if po is not None:
                            tgt["p"] = round(po, 4)
    json.dump(PR, open(fp, "w"), separators=(",", ":"))
    print(f"odds api nfl alt lines: merged {n} prices")


def hr_ou_merge(sport, now):
    """Put Hard Rock's over/under prices on the published props: the main line when it matches, else that line's alt entry
    (NHL shots: each line is its own kind, sog05 / sog15 / ...)."""
    try:
        cache = json.load(open(HR_OU_CACHE.format(sport)))
    except (OSError, ValueError):
        return
    fp = os.path.join(M.SITE, "markets", f"props_{sport}.json")
    try:
        PR = json.load(open(fp))
    except (OSError, ValueError):
        return
    n = 0
    dist = {}
    if sport == "nfl":
        _, by = nfl_ours()
        dist = {(str(g_), jsnorm(me["name"])): me["d"] for (g_, _n), me in (by or {}).items()}
    for gid, c in cache.items():
        if pd.Timestamp(c["start"]).tz_convert("UTC") <= now:
            continue
        G = PR.get("props", {}).get(gid, {})
        for nm, kinds in c["props"].items():
            pl = G.get(nm)
            if not pl:
                continue
            for kind, lines in kinds.items():
                for line, sides in lines.items():
                    if kind == "sog":
                        e = pl.get(f"sog{int(round(float(line) * 10)):02d}")
                        tgt = e
                    else:
                        e = pl.get(kind)
                        if not e:
                            continue
                        tgt = e if e.get("l") is not None and abs(float(e["l"]) - float(line)) < 1e-9 else \
                            e.setdefault("a", {}).setdefault(line, {})
                    if tgt is None:
                        continue
                    for sd, v in sides.items():
                        tgt.setdefault(sd, {})["Hard Rock"] = v
                    if tgt.get("p") is None and kind != "sog":
                        d = dist.get((gid, nm), {}).get(kind)
                        po = p_over(d, float(line)) if d else None
                        if po is not None:
                            tgt["p"] = round(po, 4)
                    n += 1
    json.dump(PR, open(fp, "w"), separators=(",", ":"))
    print(f"hard rock {sport} o/u: merged {n} prices")


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


# ---------------------------------------------------------------- NBA player props (The Odds API; Unabated has none yet)
NBA_MKT = {"player_points": "pts", "player_rebounds": "reb", "player_assists": "ast", "player_threes": "fg3m"}
NBA_BOOKS = ["pinnacle", "novig", "hardrockbet", "draftkings", "fanduel", "betmgm", "williamhill_us", "fanatics", "espnbet", "betrivers"]
NBA_SHORT = {"draftkings": "DK", "fanduel": "FD", "betmgm": "MGM", "williamhill_us": "Caesars", "fanatics": "Fanatics", "novig": "Novig",
             "hardrockbet": "Hard Rock", "espnbet": "ESPN BET", "betrivers": "BetRivers", "pinnacle": "Pinnacle"}
NBA_HOURS, NBA_REFRESH = 8.0, 120       # games within 8 h of tip-off, re-pulled every 2 h (4 credits a game: one per market)
NBA_FAST_HOURS = 4.0                    # ... and every 30 min in the last 4 h (sharp.wanted; a morning pull before that)
NBA_CACHE = os.path.join(M.OUT, "oa_props_nba.json")


def _link(u):
    """A bet link we can publish: direct and complete (links with a {state} / {wagerAmount} placeholder are dropped)."""
    return u if u and str(u).startswith("http") and "{" not in str(u) else None


def oa_nba(now):
    """NBA points / rebounds / assists / threes from ten books (one region's cost), Pinnacle de-vigged as the reference
    (multiplicative; the 2025-26 study found methods within 0.001 log-loss). Published as props_nba.json for the page:
    main line = the line most books price on both sides; our over chance from the With-Vegas distribution."""
    k = B.key()
    O = M.ours_nba() or {}
    if not k or not O:
        return
    games = {(g["away"], g["home"]): g for g in O.get("games", [])
             if now < pd.Timestamp(g["start"]).tz_convert("UTC") <= now + pd.Timedelta(hours=36)}   # 36 h: the opening watch below
    try:
        cache = json.load(open(NBA_CACHE))
    except (OSError, ValueError):
        cache = {}
    bst = B.load_state()
    todo = wanted(games, cache, now, NBA_FAST_HOURS, NBA_REFRESH, bst, slow_h=NBA_HOURS, open_h=36)
    if todo:
        r = requests.get("https://api.the-odds-api.com/v4/sports/basketball_nba/events", params=dict(apiKey=k), timeout=30)   # free
        for e in (r.json() if r.status_code == 200 else []):
            g = todo.get((B.NAMES["nba"].get(e["away_team"]), B.NAMES["nba"].get(e["home_team"])))
            if not g:
                continue
            if not B.budget_ok(bst, now):
                print("odds api nba props: credit budget used for today"); break
            q = requests.get(f"https://api.the-odds-api.com/v4/sports/basketball_nba/events/{e['id']}/odds", timeout=30,
                             params=dict(apiKey=k, bookmakers=",".join(NBA_BOOKS), markets=",".join(NBA_MKT), oddsFormat="american", includeLinks="true"))
            used = int(q.headers.get("x-requests-last") or 0)
            bst["spent_today"] = bst.get("spent_today", 0) + used
            if q.headers.get("x-requests-remaining"):
                bst["remaining"] = int(float(q.headers["x-requests-remaining"]))
            if q.status_code != 200:
                continue
            P = {}                                   # {player: {kind: {line: {o|u: {book: [odds, link]}}}}}
            for b in q.json().get("bookmakers", []):
                for m in b.get("markets", []):
                    kind = NBA_MKT.get(m["key"])
                    for o in m.get("outcomes", []):
                        side = {"over": "o", "under": "u"}.get((o.get("name") or "").lower())
                        if not kind or not side or o.get("point") is None or not o.get("description"):
                            continue
                        P.setdefault(jsnorm(o["description"]), {}).setdefault(kind, {}).setdefault(f"{float(o['point']):g}", {}) \
                         .setdefault(side, {})[NBA_SHORT.get(b["key"], b["key"])] = [int(o["price"]), _link(o.get("link") or b.get("link"))]
            if P:
                cache[str(g["id"])] = dict(fetched=now.isoformat(), start=str(g["start"]), props=P)
                print(f"odds api nba props: {g['away']} @ {g['home']}: {len(P)} players ({used} credits, {bst.get('remaining')} left)")
            else:
                print(f"odds api nba props: {g['away']} @ {g['home']}: not posted yet")
        json.dump(bst, open(B.STATE, "w"), indent=1, default=str)
    cache = {gid: c for gid, c in cache.items() if pd.Timestamp(c["start"]).tz_convert("UTC") > now - pd.Timedelta(hours=6)}
    os.makedirs(M.OUT, exist_ok=True)
    json.dump(cache, open(NBA_CACHE, "w"), separators=(",", ":"))
    # publish: one main line per player-stat
    dist = {(int(p["g"]), jsnorm(p["n"])): p.get("d", {}).get("vegas", {}) for p in O.get("players", [])}
    try:                                                   # Hard Rock's alternate lines (oa_nba_alt) join each player's ladder
        alt = json.load(open(NBA_ALT_CACHE))
    except (OSError, ValueError):
        alt = {}
    out = {}
    for gid, c in cache.items():
        if pd.Timestamp(c["start"]).tz_convert("UTC") <= now:
            continue
        A = (alt.get(gid) or {}).get("props", {})
        for nm, kinds in c["props"].items():
            for kind, lines in kinds.items():
                lines = {l: {sd: dict(v) for sd, v in x.items()} for l, x in lines.items()}
                for l, x in ((A.get(nm) or {}).get(kind) or {}).items():
                    for sd, bk in x.items():
                        lines.setdefault(l, {}).setdefault(sd, {}).update(bk)
                two = {l: x for l, x in lines.items() if x.get("o") and x.get("u")}
                if not two:
                    continue
                main = max(two, key=lambda l: len(set(two[l]["o"]) & set(two[l]["u"])))
                x = two[main]
                e = dict(l=float(main), o=x["o"], u=x["u"])
                d = dist.get((int(gid), nm), {}).get(kind)
                po = M.p_over(d, float(main)) if d else None
                if po is not None:
                    e["p"] = round(po, 4)
                pn = (x["o"].get("Pinnacle"), x["u"].get("Pinnacle"))
                if pn[0] and pn[1]:
                    a_, b_ = 1 / dec(pn[0][0]), 1 / dec(pn[1][0])
                    e["pin"] = dict(l=float(main), o=round(a_ / (a_ + b_), 4), w=2)
                alts = {l: dict(o=v.get("o", {}), u=v.get("u", {}), **({"p": round(M.p_over(d, float(l)), 4)} if d and M.p_over(d, float(l)) is not None else {}))
                        for l, v in lines.items() if l != main}
                if alts:
                    e["a"] = alts
                out.setdefault(gid, {}).setdefault(nm, {})[kind] = e
    if out:
        PROPS["nba"] = out
        write_props("nba", now)
        print(f"odds api nba props: published {sum(len(v) for v in out.values())} players")


NBA_ALT_MKT = {"player_points_alternate": "pts", "player_rebounds_alternate": "reb", "player_assists_alternate": "ast",
               "player_threes_alternate": "fg3m"}
NBA_ALT_CACHE = os.path.join(M.OUT, "oa_props_nba_alt.json")


def oa_nba_alt(now):
    """Hard Rock's NBA alternate lines (owner 2026-10-23: compare Kalshi ladder rungs with Hard Rock alt props and Novig
    ladders; Novig's ladder already comes in the main markets). 4 credits a game (4 alternate markets, one book): the
    morning pull, then hourly in the last 2 h before tip (sharp.wanted) -- alt prices move less than main lines.
    Cache: {gid: {fetched, start, props: {player: {kind: {line: {o|u: {book: [odds, link]}}}}}}}, merged by oa_nba."""
    k = B.key()
    O = M.ours_nba() or {}
    if not k or not O:
        return
    games = {(g["away"], g["home"]): g for g in O.get("games", [])
             if now < pd.Timestamp(g["start"]).tz_convert("UTC") <= now + pd.Timedelta(hours=24)}
    try:
        cache = json.load(open(NBA_ALT_CACHE))
    except (OSError, ValueError):
        cache = {}
    bst = B.load_state()
    todo = wanted(games, cache, now, 2.0, 60, bst, fast_min=60)
    if todo:
        r = requests.get("https://api.the-odds-api.com/v4/sports/basketball_nba/events", params=dict(apiKey=k), timeout=30)   # free
        for e in (r.json() if r.status_code == 200 else []):
            g = todo.get((B.NAMES["nba"].get(e["away_team"]), B.NAMES["nba"].get(e["home_team"])))
            if not g:
                continue
            if not B.budget_ok(bst, now):
                print("odds api nba alt lines: credit budget used for today"); break
            q = requests.get(f"https://api.the-odds-api.com/v4/sports/basketball_nba/events/{e['id']}/odds", timeout=30,
                             params=dict(apiKey=k, bookmakers="hardrockbet", markets=",".join(NBA_ALT_MKT), oddsFormat="american", includeLinks="true"))
            used = int(q.headers.get("x-requests-last") or 0)
            bst["spent_today"] = bst.get("spent_today", 0) + used
            if q.headers.get("x-requests-remaining"):
                bst["remaining"] = int(float(q.headers["x-requests-remaining"]))
            if q.status_code != 200:
                continue
            P = {}
            for b in q.json().get("bookmakers", []):
                for m in b.get("markets", []):
                    kind = NBA_ALT_MKT.get(m["key"])
                    for o in m.get("outcomes", []):
                        side = {"over": "o", "under": "u"}.get((o.get("name") or "").lower())
                        if not kind or not side or o.get("point") is None or not o.get("description"):
                            continue
                        P.setdefault(jsnorm(o["description"]), {}).setdefault(kind, {}).setdefault(f"{float(o['point']):g}", {})                          .setdefault(side, {})[NBA_SHORT.get(b["key"], b["key"])] = [int(o["price"]), _link(o.get("link") or b.get("link"))]
            if P:
                cache[str(g["id"])] = dict(fetched=now.isoformat(), start=str(g["start"]), props=P)
                print(f"odds api nba alt lines: {g['away']} @ {g['home']}: {len(P)} players ({used} credits, {bst.get('remaining')} left)")
            else:
                print(f"odds api nba alt lines: {g['away']} @ {g['home']}: not posted yet")
        json.dump(bst, open(B.STATE, "w"), indent=1, default=str)
    cache = {gid: c for gid, c in cache.items() if pd.Timestamp(c["start"]).tz_convert("UTC") > now - pd.Timedelta(hours=6)}
    os.makedirs(M.OUT, exist_ok=True)
    json.dump(cache, open(NBA_ALT_CACHE, "w"), separators=(",", ":"))


# NBA double-double / triple-double (owner 2026-10-27: "I would like those lines pulled"). Yes prices from ten books in one
# region (2 credits a game: two markets), the alt-line schedule (morning pull, then hourly in the last 2 h). Published on
# their own (markets/ddtd_nba.json, the screener and watchlist read it) so the card builders and Profit rows, never tested
# on these, don't pick them up. Fair = log-odds mean of the two-way books' no-vig (Kalshi, ESPN BET post Yes / No; leave-one-
# out on the page); ours = share of the saved sims (nba/draws, 1,000 games with every player's full line) with 2+ / 3+ of
# pts / reb / ast / stl / blk at 10+. Kalshi's Yes price is shown with its 7% fee in.
DDTD_MKT = {"player_double_double": "dd", "player_triple_double": "tdbl"}
DDTD_BOOKS = ["novig", "hardrockbet", "kalshi", "draftkings", "fanduel", "betmgm", "williamhill_us", "espnbet", "fanatics", "betrivers"]
DDTD_CACHE = os.path.join(M.OUT, "oa_ddtd_nba.json")


def _ddtd_ours(gid):
    import base64
    try:
        D = json.load(open(os.path.join(M.SITE, "nba", "draws", f"{gid}.json")))
    except (OSError, ValueError):
        return {}
    out = {}
    for key, st in D.get("p", {}).items():
        a = [np.frombuffer(base64.b64decode(st[k]["b"]), dtype=np.uint8) for k in ("pts", "reb", "ast", "stl", "blk") if k in st]
        if len(a) < 3:
            continue
        c = sum((x >= 10).astype(int) for x in a)
        out[jsnorm(key.split("|")[-1])] = dict(dd=round(float((c >= 2).mean()), 4), tdbl=round(float((c >= 3).mean()), 4))
    return out


def oa_nba_ddtd(now):
    k = B.key()
    O = M.ours_nba() or {}
    if not k or not O:
        return
    games = {(g["away"], g["home"]): g for g in O.get("games", [])
             if now < pd.Timestamp(g["start"]).tz_convert("UTC") <= now + pd.Timedelta(hours=24)}
    try:
        cache = json.load(open(DDTD_CACHE))
    except (OSError, ValueError):
        cache = {}
    bst = B.load_state()
    todo = wanted(games, cache, now, 2.0, 60, bst, fast_min=60)
    if todo:
        r = requests.get("https://api.the-odds-api.com/v4/sports/basketball_nba/events", params=dict(apiKey=k), timeout=30)   # free
        for e in (r.json() if r.status_code == 200 else []):
            g = todo.get((B.NAMES["nba"].get(e["away_team"]), B.NAMES["nba"].get(e["home_team"])))
            if not g:
                continue
            if not B.budget_ok(bst, now):
                print("odds api nba dd / td: credit budget used for today"); break
            q = requests.get(f"https://api.the-odds-api.com/v4/sports/basketball_nba/events/{e['id']}/odds", timeout=30,
                             params=dict(apiKey=k, bookmakers=",".join(DDTD_BOOKS), markets=",".join(DDTD_MKT), oddsFormat="american", includeLinks="true"))
            used = int(q.headers.get("x-requests-last") or 0)
            bst["spent_today"] = bst.get("spent_today", 0) + used
            if q.headers.get("x-requests-remaining"):
                bst["remaining"] = int(float(q.headers["x-requests-remaining"]))
            if q.status_code != 200:
                continue
            P = {}                                   # {player: {dd|tdbl: {o|u: {book: [odds, link]}}}}
            for b in q.json().get("bookmakers", []):
                for m in b.get("markets", []):
                    kind = DDTD_MKT.get(m["key"])
                    for o in m.get("outcomes", []):
                        side = {"yes": "o", "no": "u"}.get((o.get("name") or "").lower())
                        if not kind or not side or not o.get("description"):
                            continue
                        bk = "Kalshi" if b["key"] == "kalshi" else NBA_SHORT.get(b["key"], b["key"])
                        P.setdefault(jsnorm(o["description"]), {}).setdefault(kind, {}).setdefault(side, {})[bk] = [int(o["price"]), _link(o.get("link") or b.get("link"))]
            if P:
                cache[str(g["id"])] = dict(fetched=now.isoformat(), start=str(g["start"]), props=P)
                print(f"odds api nba dd / td: {g['away']} @ {g['home']}: {len(P)} players ({used} credits, {bst.get('remaining')} left)")
            else:
                print(f"odds api nba dd / td: {g['away']} @ {g['home']}: not posted yet")
        json.dump(bst, open(B.STATE, "w"), indent=1, default=str)
    cache = {gid: c for gid, c in cache.items() if pd.Timestamp(c["start"]).tz_convert("UTC") > now - pd.Timedelta(hours=6)}
    os.makedirs(M.OUT, exist_ok=True)
    json.dump(cache, open(DDTD_CACHE, "w"), separators=(",", ":"))
    fee = lambda o: (lambda q: round(-100 * q / (1 - q)) if q >= .5 else round(100 * (1 - q) / q))((lambda a: a + .07 * a * (1 - a))(1 / dec(o)))
    out = {}
    for gid, c in cache.items():
        if pd.Timestamp(c["start"]).tz_convert("UTC") <= now:
            continue
        ours = _ddtd_ours(gid)
        for nm, kinds in c["props"].items():
            for kind, x in kinds.items():
                yes, no = dict(x.get("o") or {}), dict(x.get("u") or {})
                if not yes:
                    continue
                # fair: the median Yes price across every book (3+), less the vig share the two-way books show for this
                # player (their no-vig / Yes implied). One book's two-way no-vig alone overstated longshots (a Kalshi bet left
                # only ESPN BET: Queta +944 'edge +59%').
                ratio = [(lambda a_, b_: (a_ / (a_ + b_)) / a_)(1 / dec(yes[bk][0]), 1 / dec(no[bk][0])) for bk in yes if bk in no]
                imp = sorted(1 / dec(v[0]) for v in yes.values())
                if "Kalshi" in yes:
                    yes["Kalshi"] = [fee(yes["Kalshi"][0]), yes["Kalshi"][1]]
                e = dict(o=yes, u=no)
                if len(imp) >= 3 and ratio:
                    med = lambda a: (sorted(a)[(len(a) - 1) // 2] + sorted(a)[len(a) // 2]) / 2
                    e["pin"] = dict(o=round(min(.99, med(imp) * med(ratio)), 4), w=len(imp))
                if (ours.get(nm) or {}).get(kind) is not None:
                    e["p"] = ours[nm][kind]
                out.setdefault(gid, {}).setdefault(nm, {})[kind] = e
    json.dump(dict(sport="nba", fetched=now.tz_convert("America/New_York").isoformat(timespec="minutes"), partner=U.PARTNER,
                   props=out), open(os.path.join(M.SITE, "markets", "ddtd_nba.json"), "w"), separators=(",", ":"))
    print(f"odds api nba dd / td: published {sum(len(v) for v in out.values())} players")


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
    if sport == "nhl":
        for g in P.values():
            for kinds in g.values():
                e = kinds.get("goal")
                if e:
                    f4 = four_book(e.get("o"))
                    if f4:
                        e["f4"] = f4
                    else:
                        e.pop("f4", None)
    json.dump(dict(sport=sport, fetched=now.tz_convert("America/New_York").isoformat(timespec="minutes"), partner=U.PARTNER, props=P),
              open(fp, "w"), separators=(",", ":"))


# NHL anytime-goal fair without Pinnacle (owner 2026-10-31, research/nhl_atg_book_weights.py + nhl_atg_fourbook.py; 4,548
# player-games Oct 12-30): Kalshi, FanDuel, Fanatics and DraftKings were sharper than the all-book average in both halves
# (Caesars, theScore, Polymarket worse in both; fitted weights unstable). Each book's hold removed by its own logit shift,
# averaged in logit; beat the exchanges' median / 1.03 in both halves (-5.3e-4 log-loss, z -1.0; that one ran ~7% high).
F4_SHIFT = {"Kalshi": -.162, "FD": -.133, "Fanatics": -.178, "DK": -.163}


def four_book(o):
    """{o: fair, parts: {book: hold-removed chance}} from a goal entry's Yes prices, or None."""
    parts = {}
    for b, s in F4_SHIFT.items():
        v = (o or {}).get(b)
        if isinstance(v, list) and v and v[0] is not None:
            q = 1 / dec(v[0])
            parts[b] = round(1 / (1 + math.exp(-(math.log(q / (1 - q)) + s))), 4)
    if not parts:
        return None
    z = sum(math.log(p / (1 - p)) for p in parts.values()) / len(parts)
    return dict(o=round(1 / (1 + math.exp(-z)), 4), parts=parts)


# ---------------------------------------------------------------- Pinnacle / consensus line moves (tracked)
MOVES_STATE = os.path.join(M.OUT, "pin_moves_{}.json")
PRICE_UP, BOTH_UP = .03, .02         # no-vig over chance: +3 pts at the same line; +2 pts more at the new line
RUNG_EDGE = (.10, .60)               # higher rungs of a moved market: our book's price 10%+ over the carried fair (60%+ = stale)
OWN_BOOKS = ("Hard Rock", "Novig", "Kalshi")


def _nv(o, u):
    a, b = 1 / dec(o), 1 / dec(u)
    return a / (a + b)


def pin_moves(sport, now):
    """The owner's line-move signal (2026-10-23: "I want the Pinnacle baseline to move up... and when it moves up I want
    the odds to drop on it. Doesn't need both, but both is a strong signal... track using only Pinnacle and our consensus
    weight, NBA and NFL"). Per player prop, from its first sighting:
      line   the line moved up (Pinnacle's own two-way line; or the consensus line)
      price  same line, the over's no-vig chance up PRICE_UP+
      both   the line moved up AND the over shortened BOTH_UP+ more at the new line
    Each first firing goes to flags_<sport>.json "moves" with the best over price at the owner's books on the books'
    main line, plus up to two higher rungs of that market priced 10%+ over the carried fair at the owner's books (sig
    'rung', owner: "grabbing 9+ assists if it was an outlier on our books"); profit.tags logs and grades them ('<SPORT> Pinnacle / Consensus move: ...' rows, experimental).
    State: markets/pin_moves_<sport>.json {g|n|k: {start, pin: {...}, cons: {...}, fired: [src:sig]}}."""
    fp = os.path.join(M.SITE, "markets", f"props_{sport}.json")
    try:
        PR = json.load(open(fp))
    except (OSError, ValueError):
        return
    pinc = {}
    if sport == "nfl":
        try:
            pinc = json.load(open(PIN_CACHE))
        except (OSError, ValueError):
            pinc = {}
    sf = MOVES_STATE.format(sport)
    try:
        S = json.load(open(sf))
    except (OSError, ValueError):
        S = {}
    O = (M.ours_nba if sport == "nba" else M.ours_nfl)() or {}
    start = {str(g["id"]): str(g["start"]) for g in O.get("games", [])}
    LAB = dict(LC_LAB, td="TD")
    moves = []
    for gid, G in (PR.get("props") or {}).items():
        st = start.get(str(gid))
        if not st or pd.Timestamp(st).tz_convert("UTC") <= now:
            continue
        for nm, kinds in G.items():
            for k, e in kinds.items():
                if k not in LAB or not e.get("o"):
                    continue
                # Pinnacle's own line now: NBA from the Pinnacle prices on the published lines, NFL from its own pull
                pl = po = None
                if sport == "nba":
                    cand = [(e.get("l"), e.get("o"), e.get("u"))] + [(float(l), a.get("o"), a.get("u")) for l, a in (e.get("a") or {}).items()]
                    two = [(l, _nv(o["Pinnacle"][0], u["Pinnacle"][0])) for l, o, u in cand if l is not None and o and u and "Pinnacle" in o and "Pinnacle" in u]
                    if two:
                        pl, po = min(two, key=lambda x: abs(x[1] - .5))
                else:
                    x = ((pinc.get(str(gid)) or {}).get("props") or {}).get(nm, {}).get(k)
                    if x and x[1] is not None and x[2] is not None:
                        pl, po = float(x[0]), _nv(x[1], x[2])
                pin_ = e.get("pin") or {}
                cl, co = (e.get("l"), pin_.get("o")) if pin_.get("o") is not None and pin_.get("l") == e.get("l") else (None, None)
                key = f"{gid}|{nm}|{k}"
                rec = S.setdefault(key, dict(start=st, fired=[]))
                for src, l, o in (("pin", pl, po), ("cons", cl, co)):
                    if l is None or o is None:
                        continue
                    r = rec.setdefault(src, dict(open_l=l, open_o=round(o, 4), open_t=now.isoformat(), at={}))
                    r.setdefault("at", {}).setdefault(f"{l:g}", round(o, 4))     # first chance seen at each line
                    r["last_l"], r["last_o"] = l, round(o, 4)
                    sig = None
                    if l > r["open_l"] and o - r["at"][f"{l:g}"] >= BOTH_UP:
                        sig = "both"
                    elif l > r["open_l"]:
                        sig = "line"
                    elif l == r["open_l"] and o - r["open_o"] >= PRICE_UP:
                        sig = "price"
                    if not sig:
                        continue
                    tag = f"{src}:{sig}"
                    # best over price at the owner's books on the books' main line (a lagging book still on the lower line is the point)
                    best = max(((b, v[0]) for b, v in e["o"].items() if b in OWN_BOOKS and v and v[0] is not None), key=lambda x: dec(x[1]), default=None)
                    if best is None:
                        continue
                    bet = "TD" if k == "td" else f"over {e['l']:g} {LAB[k]}"
                    moves.append(dict(g=str(gid), n=nm, k=k, b=bet, bk=best[0], o=best[1], src=src, sig=sig, from_l=r["open_l"], to_l=l,
                                      d=round(o - r["open_o"], 4), cons=co, start=st))
                    if tag not in rec["fired"]:
                        rec["fired"].append(tag)
                    # owner 2026-10-23: also the higher outliers of the same market ("grabbing 9+ assists if it was an
                    # outlier on our books"): alt rungs above the main line whose price at the owner's books beats the
                    # fair carried to that rung (apply_consensus `pin`, already reflecting the move) by RUNG_EDGE; best two
                    rungs = []
                    for la, a in (e.get("a") or {}).items():
                        fa = a.get("pin")
                        if fa is None or float(la) <= (e.get("l") or 0):
                            continue
                        bb = max(((b, v[0]) for b, v in (a.get("o") or {}).items() if b in OWN_BOOKS and v and v[0] is not None), key=lambda x: dec(x[1]), default=None)
                        if bb and RUNG_EDGE[0] <= fa * dec(bb[1]) - 1 <= RUNG_EDGE[1]:
                            rungs.append((fa * dec(bb[1]) - 1, float(la), bb, fa))
                    for ev, la, bb, fa in sorted(rungs, reverse=True)[:2]:
                        moves.append(dict(g=str(gid), n=nm, k=k, b=f"over {la:g} {LAB[k]}", bk=bb[0], o=bb[1], src=src, sig="rung", trig=sig,
                                          from_l=r["open_l"], to_l=l, fair=round(fa, 4), ev=round(ev, 3), start=st))
    S = {k: v for k, v in S.items() if pd.Timestamp(v["start"]).tz_convert("UTC") > now - pd.Timedelta(hours=12)}
    json.dump(S, open(sf, "w"), separators=(",", ":"))
    ff = os.path.join(M.SITE, "markets", f"flags_{sport}.json")
    try:
        FL = json.load(open(ff))
    except (OSError, ValueError):
        FL = dict(sport=sport, fetched=now.tz_convert("America/New_York").isoformat(timespec="minutes"), tags={})
    FL["moves"] = moves
    json.dump(FL, open(ff, "w"), separators=(",", ":"))
    print(f"line moves {sport}: {len(S)} props tracked, {len(moves)} live signals "
          f"({', '.join(f'{t} {sum(1 for m in moves if f"{m['src']}:{m['sig']}" == t)}' for t in sorted({f'{m['src']}:{m['sig']}' for m in moves}))})")


KNEE_RATIO = 2.0                     # the drop out of the rung is 2x+ the drop into it ...
KNEE_DROP = .06                      # ... and 6+ pts of implied chance
KNEE_Q = (.15, .65)                  # the rung itself priced about -185..+570 (achievable, overs)


def ladder_knees(sport, now):
    """Ladder knees (owner 2026-10-24: "track odds changes within the ladder... 7 rbs at +180, 8 at +220, 9 at +500 --
    I'm looking to likely take 8"): at each of the owner's books, three consecutive rungs (equal line gaps) of one
    player-stat ladder, the middle one where moving up into it costs little chance and the next step costs a lot:
    drop in = q(prev) - q(rung), drop out = q(rung) - q(next) (q = the book's implied chance; Kalshi with its 7% fee),
    drop out >= KNEE_RATIO x drop in and >= KNEE_DROP, q(rung) in KNEE_Q. The strongest knee per player-stat ->
    flags_<sport>.json "knees" (with the fair / our chance at that rung for context); profit.tags logs and grades
    each first sighting ('<SPORT> Ladder knee (experimental)'). Shape only: no edge is claimed."""
    fp = os.path.join(M.SITE, "markets", f"props_{sport}.json")
    try:
        PR = json.load(open(fp))
    except (OSError, ValueError):
        return
    O = (M.ours_nba if sport == "nba" else M.ours_nfl)() or {}
    start = {str(g["id"]): str(g["start"]) for g in O.get("games", [])}
    # Kalshi's ladders live in live_<sport>.json (asks / bids), not the props file: rungs with a spread of 3c or
    # less only (research/kalshi_liquidity_rule.py: 4c+ spreads lost 38%; wide quotes would make fake knees)
    kal = {}
    try:
        for r in json.load(open(os.path.join(M.SITE, "markets", f"live_{sport}.json"))).get("rows", []):
            if r.get("s") == "kalshi" and r.get("k") in LC_LAB and r.get("l") is not None and r.get("n") and r.get("ya") and r.get("yb") is not None \
                    and r["ya"] - r["yb"] <= .03 and .01 < r["ya"] < .99:
                kal.setdefault((str(r["g"]), jsnorm(r["n"]), r["k"]), {})[float(r["l"])] = (r["ya"], (r.get("p") or {}).get("vegas"))
    except (OSError, ValueError):
        pass
    am = lambda q: round(-100 * q / (1 - q)) if q >= .5 else round(100 * (1 - q) / q)
    keys = {(str(g), nm, k) for g, G in (PR.get("props") or {}).items() for nm, ks in G.items() for k in ks} | set(kal)
    knees = []
    for gid, nm, k in sorted(keys):
        st = start.get(gid)
        if k not in LC_LAB or not st or pd.Timestamp(st).tz_convert("UTC") <= now:
            continue
        e = ((PR.get("props") or {}).get(gid) or {}).get(nm, {}).get(k) or {}
        rungs = {}                                            # line -> (book prices, fair, ours)
        if e.get("l") is not None and e.get("o"):
            pin_ = e.get("pin") or {}
            rungs[float(e["l"])] = (e["o"], pin_.get("o") if pin_.get("l") == e.get("l") else None, e.get("p"))
        for la, a in (e.get("a") or {}).items():
            if a.get("o"):
                rungs.setdefault(float(la), (a["o"], a.get("pin"), a.get("p")))
        for la, (ya, ours) in (kal.get((gid, nm, k)) or {}).items():
            rungs.setdefault(la, ({}, None, ours))
        best = None
        for bk in OWN_BOOKS:
            L = []
            if bk == "Kalshi":                                # the ask plus Kalshi's 7% fee; stored price is fee-in
                for l, (ya, _) in sorted((kal.get((gid, nm, k)) or {}).items()):
                    q = ya + .07 * ya * (1 - ya)
                    L.append((l, am(q), q))
            for l in sorted(rungs) if bk != "Kalshi" else []:
                v = rungs[l][0].get(bk)
                if v and v[0] is not None:
                    L.append((l, v[0], 1 / dec(v[0])))
            for i in range(1, len(L) - 1):
                (l0, o0, q0), (l1, o1, q1), (l2, o2, q2) = L[i - 1], L[i], L[i + 1]
                g0, g1 = l1 - l0, l2 - l1                         # line gaps (yardage ladders are uneven: 9.5, 19.5, 24.5 ...)
                if max(g0, g1) > 2.5 * min(g0, g1):
                    continue
                din, dout = q0 - q1, q1 - q2
                # per unit of line (a yard, a rebound): the step out costs KNEE_RATIO x the step in
                if din <= 0 or dout < KNEE_DROP or dout / g1 < KNEE_RATIO * din / g0 or not KNEE_Q[0] <= q1 <= KNEE_Q[1]:
                    continue
                score = (dout / g1) / (din / g0)
                if best is None or score > best[0]:
                    best = (score, bk, (l0, o0), (l1, o1), (l2, o2), din, dout, rungs[l1][1], rungs[l1][2])
        if best:
            score, bk, prev, mid, nxt, din, dout, fair, ours = best
            knees.append(dict(g=str(gid), n=nm, k=k, l=mid[0], b=f"over {mid[0]:g} {LC_LAB[k]}", bk=bk, o=mid[1],
                              prev=list(prev), next=list(nxt), din=round(din, 4), dout=round(dout, 4), ratio=round(min(score, 99), 2),
                              fair=None if fair is None else round(float(fair), 4), p=None if ours is None else round(float(ours), 4), start=st))
    ff = os.path.join(M.SITE, "markets", f"flags_{sport}.json")
    try:
        FL = json.load(open(ff))
    except (OSError, ValueError):
        FL = dict(sport=sport, fetched=now.tz_convert("America/New_York").isoformat(timespec="minutes"), tags={})
    FL["knees"] = knees
    json.dump(FL, open(ff, "w"), separators=(",", ":"))
    print(f"ladder knees {sport}: {len(knees)} ({', '.join(f'{b} {sum(1 for x in knees if x['bk'] == b)}' for b in OWN_BOOKS)})")


def main(argv):
    now = pd.Timestamp.now(tz="UTC")
    if not argv or "nba" in argv:
        try:
            oa_nba_alt(now)                          # Hard Rock alt lines first, so oa_nba publishes them on the ladders
        except Exception as e:                       # noqa: BLE001
            print("odds api nba alt lines skipped:", type(e).__name__, e)
        try:
            oa_nba(now)
        except Exception as e:                       # never break the markets run
            print(f"odds api nba props: skipped ({type(e).__name__}: {e})")
        try:
            oa_nba_ddtd(now)
        except Exception as e:                       # noqa: BLE001
            print(f"odds api nba dd / td: skipped ({type(e).__name__}: {e})")
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
            hr_ou_pull(sport, now)
            hr_ou_merge(sport, now)
            if sport == "nfl":
                try:
                    nfl_alt_pull(now)
                    nfl_alt_merge(now)
                except Exception as e:                       # noqa: BLE001 -- the main lines still publish
                    print(f"odds api nfl alt lines: skipped ({type(e).__name__}: {e})")
            if sport == "nfl":
                pin_pull(now)
                pin_merge(now)
            if sport == "nhl":
                pin_pull_nhl(now)
                pin_merge_nhl(now)
        except Exception as e:
            print(f"hard rock {sport}: skipped ({type(e).__name__}: {e})")
    for sport in ("nhl", "nfl", "nba"):                         # sharp consensus last, after every merge
        if argv and sport not in argv:
            continue
        try:
            apply_consensus(sport, now)
        except Exception as e:
            print(f"sharp {sport}: consensus skipped ({type(e).__name__}: {e})")
        if sport in ("nba", "nfl"):
            try:
                pin_moves(sport, now)
            except Exception as e:                       # noqa: BLE001 -- tracking only
                print(f"line moves {sport}: skipped ({type(e).__name__}: {e})")
            try:
                ladder_knees(sport, now)
            except Exception as e:                       # noqa: BLE001 -- tracking only
                print(f"ladder knees {sport}: skipped ({type(e).__name__}: {e})")
    os.makedirs(M.OUT, exist_ok=True)
    json.dump(st, open(STATE, "w"), indent=1)


if __name__ == "__main__":
    main(sys.argv[1:])
