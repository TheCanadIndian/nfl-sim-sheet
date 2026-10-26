#!/usr/bin/env python3
"""
Learning parlay builder -- EXPERIMENTAL until its own graded record is consistent (owner 2026-10-22: "build out a self
improving parlay builder. Look for consistent returns on value props. They should fit script or our new paths";
"label the builder as experimental until it can produce consistent results").

Why it starts from nothing: research/value_segments.py (Oct 12-22, 6,129 graded over prices at the owner's books) found
no kind of value prop with consistent returns yet (all 5-40% edges -13.7% on 305; none positive in both halves with
n >= 30). So the builder learns as it goes, from an unbiased record:

  1. log  every freeze, the sport pages register every over they could bet at Hard Rock / Novig / Kalshi (MKT.cand:
          anchored chance, best price, edge, the path / script reasons f, and whether it fits) -> parlays/learn/<sport>/<date>.json,
          each candidate's last version before its game starts (picked or not, so the record isn't only our picks)
  2. grade from box scores (parlays.game_for / settle)
  3. learn segments (sport x stat, book, stat x edge band, stat x price band, each reason, fits / not, reason count): return at the
          price, clustered z by game, and each half (by date). Proven = n >= 30, positive in both halves, z >= 1;
          Dropped = n >= 30, negative in both halves, z <= -1; otherwise Learning / Watching. Seeded with the study's
          graded prices (no reasons): parlays/learn/seed.json.
  4. build "Learning parlays": legs that fit a script / path, edge 5-40%, in no Dropped segment, one per game, a leg
          in one parlay; scored by edge, plus a bonus per Proven segment it belongs to. Up to 3 a sport a day (two 2-leg,
          one 3-leg). They lock with the day's card (parlays.pick_lock) and are graded like every parlay.
The builder's own record decides the label: Proven when n >= 60 graded parlays, positive in both halves, z >= 1.

    python builder.py grade learn    # (markets.yml, before parlays.py freeze)
"""
import datetime as dt
import glob
import json
import math
import os
import sys

import parlays as PL

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "parlays", "learn")
STATE = os.path.join(OUT, "state.json")
SEED = os.path.join(OUT, "seed.json")
CARD = "Learning parlays (experimental)"
EDGE = (.05, .40)
SEG_N, SEG_Z = 30, 1.0
BUILD_N, BUILD_Z = 60, 1.0
PER_SPORT = 3
STAT = {"td": "anytime TD", "rec": "receptions", "rec_yds": "rec yds", "rush_yds": "rush yds", "pass_yds": "pass yds", "pts": "points",
        "reb": "rebounds", "ast": "assists", "fg3m": "threes", "goal": "goals", "sog15": "2+ shots", "sog25": "3+ shots", "sog": "shots"}


def _key(c):
    return f"{c['gid']}|{c['name']}|{c['kind']}"


# ---------------------------------------------------------------- 1. log
def save_cands(sport, cands, now):
    """Merge one freeze's candidates: games not started yet take the latest version; started games keep theirs."""
    by = {}
    for c in cands or []:
        if not c.get("start"):
            continue
        by.setdefault(PL.et_date(c["start"]), []).append(c)
    for date, cs in by.items():
        fp = os.path.join(OUT, sport, f"{date}.json")
        old = json.load(open(fp)) if os.path.exists(fp) else {"sport": sport, "date": date, "cands": {}}
        C = old["cands"]
        for c in cs:
            k = _key(c)
            if k in C and PL.when(C[k]["start"]) <= now:
                continue
            C[k] = dict(c, seen=now.isoformat(timespec="seconds"))
        os.makedirs(os.path.dirname(fp), exist_ok=True)
        json.dump(old, open(fp, "w"), separators=(",", ":"))


# ---------------------------------------------------------------- 2. grade
def grade():
    now = dt.datetime.now(dt.timezone.utc)
    for fp in sorted(glob.glob(os.path.join(OUT, "*", "*.json"))):
        d = json.load(open(fp))
        if d.get("graded"):
            continue
        sport, games, waiting = d["sport"], {}, 0
        for c in d["cands"].values():
            if c.get("res") or PL.when(c["start"]) > now - dt.timedelta(hours=3):
                waiting += 0 if c.get("res") else 1
                continue
            if c["gid"] not in games:
                try:
                    games[c["gid"]] = PL.game_for(sport, c["gid"], c["start"])
                except Exception:                                              # noqa: BLE001
                    games[c["gid"]] = None
            g = games[c["gid"]]
            if not g:
                waiting += 1
                continue
            kind = "sog" if c["kind"].startswith("sog") else c["kind"]
            c["res"] = PL.settle(dict(kind=kind, side="o", line=c.get("line", .5), name=c["name"]), g)
        if waiting == 0 or (now.date() - dt.date.fromisoformat(d["date"])).days >= 3:
            d["graded"] = True
        json.dump(d, open(fp, "w"), separators=(",", ":"))


# ---------------------------------------------------------------- 3. learn
def band_edge(e):
    return "edge 5-10%" if e < .10 else "edge 10-20%" if e < .20 else "edge 20-40%" if e <= .40 else "edge 40%+"


def band_price(o):
    o = float(o)
    return "favorite" if o <= -150 else "coin flip" if o < 150 else "plus money" if o < 400 else "longshot"


def features(sport, r):
    sp = sport.upper()
    st = STAT.get(r["kind"], r["kind"])                           # bands per stat: goals evidence never blocks shots
    F = [sp, f"{sp} · {st}", f"{sp} · {r['book']}", f"{sp} · {st} · {band_edge(r['edge'])}", f"{sp} · {st} · {band_price(r['price'])}"]
    if r.get("f") is not None:
        F += [f"{sp} · fits a path" if r.get("fit") else f"{sp} · no path"] + [f"{sp} · {x}" for x in r["f"]]
        F.append(f"{sp} · {len(r['f'])} reason{'s' if len(r['f']) != 1 else ''}")
    return F


def graded_rows():
    rows = []
    for fp in glob.glob(os.path.join(OUT, "*", "*.json")):
        d = json.load(open(fp))
        for c in d["cands"].values():
            if c.get("res") in ("W", "L") and EDGE[0] <= c["edge"] <= EDGE[1]:
                rows.append(dict(c, sport=d["sport"], date=d["date"], y=c["res"] == "W"))
    if os.path.exists(SEED):
        for r in json.load(open(SEED)):
            if EDGE[0] <= r["edge"] <= EDGE[1]:
                rows.append(dict(r, f=None))
    return rows


def seg_stats(rows):
    S = {}
    for r in rows:
        ret = (PL.dec(r["price"]) - 1) if r["y"] else -1.0
        for f in features(r["sport"], r):
            S.setdefault(f, []).append((r["date"], r["gid"], ret))
    out = []
    for seg, xs in S.items():
        n = len(xs)
        days = sorted({x[0] for x in xs})
        cut = days[len(days) // 2 - 1] if len(days) > 1 else days[0]
        h1 = [x[2] for x in xs if x[0] <= cut]
        h2 = [x[2] for x in xs if x[0] > cut]
        g = {}
        for _, gid, r in xs:
            g[gid] = g.get(gid, 0) + r
        mean = sum(x[2] for x in xs) / n
        vals = list(g.values())
        var = (sum((v - sum(vals) / len(vals)) ** 2 for v in vals) / (len(vals) - 1)) if len(vals) > 1 else 0
        se = math.sqrt(len(vals) * var) / n if var > 0 else None
        z = mean / se if se else 0.0
        m1 = sum(h1) / len(h1) if h1 else None
        m2 = sum(h2) / len(h2) if h2 else None
        st = ("Proven" if n >= SEG_N and m1 is not None and m2 is not None and m1 > 0 and m2 > 0 and z >= SEG_Z else
              "Dropped" if n >= SEG_N and m1 is not None and m2 is not None and m1 < 0 and m2 < 0 and z <= -SEG_Z else
              "Watching" if n >= SEG_N else "Learning")
        out.append(dict(seg=seg, n=n, ret=round(mean, 4), h1=None if m1 is None else round(m1, 4), h2=None if m2 is None else round(m2, 4),
                        z=round(z, 2), status=st))
    return sorted(out, key=lambda s: (-s["n"]))


def builder_record(card=CARD):
    ps = []
    for fp in glob.glob(os.path.join(PL.OUT, "*", "*.json")):
        if os.sep + "learn" + os.sep in fp:
            continue
        d = json.load(open(fp))
        for p in d.get("parlays", []):
            if p.get("card") == card and p.get("res") in ("W", "L"):
                ps.append((d["date"], (((p.get("paid") or p.get("d") or 1) - 1) if p["res"] == "W" else -1.0), p.get("p") or 0))
    if not ps:
        return dict(n=0, status="Experimental")
    ps.sort()
    n = len(ps)
    rets = [x[1] for x in ps]
    mean = sum(rets) / n
    half = n // 2
    m1 = sum(rets[:half]) / half if half else None
    m2 = sum(rets[half:]) / (n - half) if n - half else None
    sd = math.sqrt(sum((r - mean) ** 2 for r in rets) / (n - 1)) if n > 1 else 0
    z = mean / (sd / math.sqrt(n)) if sd > 0 else 0
    ok = n >= BUILD_N and m1 is not None and m1 > 0 and m2 > 0 and z >= BUILD_Z
    return dict(n=n, w=sum(1 for x in ps if x[1] > -1), ret=round(mean, 4), h1=None if m1 is None else round(m1, 4),
                h2=None if m2 is None else round(m2, 4), z=round(z, 2), exp=round(sum(x[2] for x in ps), 2),
                net=round(sum(rets) * PL.CARD_STAKE, 2), status="Proven" if ok else "Experimental")


def learn():
    old = json.load(open(STATE)) if os.path.exists(STATE) else {}
    rows = graded_rows()
    segs = seg_stats(rows)
    prev = {s["seg"]: s["status"] for s in old.get("segments", [])}
    today = dt.datetime.now(PL.ET).date().isoformat()
    changes = old.get("changes", [])
    for s in segs:
        if prev.get(s["seg"]) and prev[s["seg"]] != s["status"] and s["status"] in ("Proven", "Dropped") or (
                prev.get(s["seg"]) in ("Proven", "Dropped") and s["status"] != prev[s["seg"]]):
            changes.append(dict(date=today, seg=s["seg"], was=prev[s["seg"]], now=s["status"], n=s["n"], ret=s["ret"]))
    st = dict(updated=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), rows=len(rows),
              live_rows=sum(1 for r in rows if r.get("f") is not None), segments=segs, changes=changes[-60:], builder=builder_record(),
              confluence=builder_record(CONF_CARD), moveknee=builder_record(MK_CARD))
    os.makedirs(OUT, exist_ok=True)
    json.dump(st, open(STATE, "w"), indent=0)
    print(f"builder: {len(rows)} graded value props ({st['live_rows']} with reasons), {sum(s['status'] == 'Proven' for s in segs)} proven, "
          f"{sum(s['status'] == 'Dropped' for s in segs)} dropped segments; builder {st['builder']}")
    return st


# ---------------------------------------------------------------- 4. build
def build(sport, cands, now):
    """Today's Learning parlays from this freeze's candidates (parlays.freeze adds them to the page's parlays)."""
    st = json.load(open(STATE)) if os.path.exists(STATE) else {"segments": []}
    status = {s["seg"]: s["status"] for s in st["segments"]}
    legs = []
    for c in cands or []:
        if not c.get("fit") or not (EDGE[0] <= c["edge"] <= EDGE[1]) or not c.get("start") or PL.when(c["start"]) <= now:
            continue
        F = features(sport, c)
        if any(status.get(f) == "Dropped" for f in F[1:]):              # the bare sport segment informs, never blocks
            continue
        bonus = sum(1 for f in F if status.get(f) == "Proven")
        legs.append(dict(c, score=c["edge"] * (1 + .5 * bonus), proven=[f for f in F if status.get(f) == "Proven"]))
    legs.sort(key=lambda c: -c["score"])
    best = {}
    for c in legs:                                                   # one leg per game: its best
        best.setdefault(c["gid"], c)
    pool = list(best.values())
    out, used = [], set()

    def mk(ls, tier):
        kind = lambda c: "sog" if c["kind"].startswith("sog") else c["kind"]
        lab = lambda c: f"{c['lab']} {'anytime TD' if c['kind'] == 'td' else 'to score' if c['kind'] == 'goal' else (STAT.get(c['kind'], c['kind']) if c['kind'].startswith('sog') else 'over ' + str(c['line']) + ' ' + STAT.get(c['kind'], c['kind']))}"
        L = [dict(gid=c["gid"], start=c["start"], kind=kind(c), side="o", line=c["line"], name=c["name"], lab=lab(c), p=c["q"],
                  price=c["price"], book=c["book"], f=c["f"], sport=sport, edge=c["edge"]) for c in ls]
        p = math.prod(c["q"] for c in ls)
        d = math.prod(PL.dec(c["price"]) for c in ls)
        return dict(card=CARD, tier=tier, legs=L, p=round(p, 5), d=round(d, 3))
    avail = lambda: [c for c in pool if c["gid"] not in used]
    for size in (2, 2, 3):
        if len(out) >= PER_SPORT:
            break
        a = avail()
        if len(a) < size:
            continue
        ls = a[:size]
        out.append(mk(ls, f"{size} legs"))
        used.update(c["gid"] for c in ls)
    return out


# ---------------------------------------------------------------- 5. confluence parlays
# Owner 2026-10-25: "I do want to go for higher odds parlays... take confluence into account... multiple things agreeing
# leading to an increased leg hit rate"; run alongside Today's card (not on it) until its record says otherwise.
# Why the rules (research/confluence_study.py, 2,117 graded overs at the owner's books Oct 22-25; parlay legs Oct 8-25):
# every over landed near its price (-2%); 5+ agreeing signals hit 53.6% vs 48.3% priced (+11%, n 56), 2+ market signals
# 51% vs 47% (+8%, n 82) -- the right direction, not proven (halves split). Legs priced under 25% hit 10.5% vs 19.9% priced
# and claimed edges 15%+ hit 24% vs 40% claimed, so neither goes in; the payout comes from more near-fair legs.
CONF_CARD = "Confluence parlays (experimental)"
CONF_Q = (.25, .55)                  # leg price as implied chance: about -122 .. +300
CONF_EDGE = (0.0, .15)               # market-anchored edge at the price
CONF_VOTES = 4                       # agreeing signals a leg needs (owner 2026-10-25, research/confluence_replay.py Oct 22-25:
                                     # legs hit 38% at 3+, 42% at 4+, 50% at 5+; 4+ keeps enough parlays)
CONF_LONG = (20, 100)                # the longshot's payout


def signals(sport):
    """Live market signals per bet from the markets run (flags_<sport>.json): {(gid, name, kind, line): {votes}} for
    Value / LC / knees at that line, and {(gid, name, kind): {votes}} for Pinnacle / consensus moves up (any line)."""
    try:
        FL = json.load(open(os.path.join(PL.SITE, "markets", f"flags_{sport}.json")))
    except (OSError, ValueError):
        return {}, {}
    rev = {v: k for k, v in STAT.items()}
    at, prop = {}, {}

    def bet(b):                                                       # 'over 9.5 rebounds' -> (kind, line)
        if b == "TD":
            return "td", .5
        if b == "goal":
            return "goal", .5
        p = str(b).split(" ", 2)
        if len(p) == 3 and p[0] == "over" and p[2] in rev:
            return rev[p[2]], float(p[1])
        return None
    for src in ("tags", "lc"):
        for gid, tg in (FL.get(src) or {}).items():
            for nm, v in tg.items():
                kl = bet(v.get("b")) if isinstance(v, dict) else None
                if kl:
                    at.setdefault((str(gid), nm, kl[0], kl[1]), set()).add("value" if src == "tags" else "lc")
    for x in FL.get("knees") or []:
        at.setdefault((str(x["g"]), x["n"], x["k"], float(x["l"])), set()).add("knee")
    for m in FL.get("moves") or []:
        if m.get("sig") in ("line", "price", "both"):
            prop.setdefault((str(m["g"]), m["n"], m["k"]), set()).add("pinnacle up" if m.get("src") == "pin" else "consensus up")
    return at, prop


def build_confluence(sport, cands, now):
    """Confluence parlays from this freeze's candidates: legs where 3+ things agree, one per game; a 2-leg, a 3-leg and
    a 4-5 leg longshot (CONF_LONG). Each leg's shown chance is the market's fair (our model is one vote, not the chance)."""
    st = json.load(open(STATE)) if os.path.exists(STATE) else {"segments": []}
    status = {s["seg"]: s["status"] for s in st["segments"]}
    at, prop = signals(sport)
    SP = sport.upper()
    w = lambda v: 0 if status.get(f"{SP} · {v}") == "Dropped" else 2 if status.get(f"{SP} · {v}") == "Proven" else 1
    legs = []
    for c in cands or []:
        if not c.get("start") or PL.when(c["start"]) <= now or c.get("price") is None:
            continue
        q = 1 / PL.dec(c["price"])
        if not CONF_Q[0] <= q <= CONF_Q[1] or not CONF_EDGE[0] <= (c.get("edge") or -1) <= CONF_EDGE[1]:
            continue
        F = features(sport, c)
        if any(status.get(f) == "Dropped" for f in F[1:5]):            # a dropped stat / book / band never plays
            continue
        line = float(c["line"]) if c.get("line") is not None else .5
        votes = [x for x in (c.get("f") or []) if x != "price"]
        if c.get("p") is not None and c.get("m") is not None and c["p"] > c["m"]:
            votes.append("our model above fair")
        kind = "sog" if c["kind"].startswith("sog") else c["kind"]
        votes += sorted(at.get((str(c["gid"]), c["name"], kind, line), set()) | prop.get((str(c["gid"]), c["name"], kind), set()))
        score = sum(w(v) for v in votes)
        if score < CONF_VOTES:
            continue
        legs.append(dict(c, votes=votes, score=score, fair=c.get("m") if c.get("m") is not None else c.get("q")))
    # market signals rank first (research/confluence_study.py: 2+ market signals lift 1.08, model reasons alone ~1.0)
    MK = ("value", "lc", "knee", "pinnacle up", "consensus up")
    legs.sort(key=lambda c: (-(c["score"] + .5 * sum(v in MK for v in c["votes"])), -(c.get("edge") or 0)))
    best = {}
    for c in legs:
        best.setdefault(c["gid"], c)
    pool = list(best.values())

    def mk(ls, tier):
        lab = lambda c: f"{c['lab']} {'anytime TD' if c['kind'] == 'td' else 'to score' if c['kind'] == 'goal' else (STAT.get(c['kind'], c['kind']) if c['kind'].startswith('sog') else 'over ' + str(c['line']) + ' ' + STAT.get(c['kind'], c['kind']))}"
        L = [dict(gid=c["gid"], start=c["start"], kind="sog" if c["kind"].startswith("sog") else c["kind"], side="o", line=c["line"], name=c["name"],
                  lab=lab(c), p=round(c["fair"], 4), price=c["price"], book=c["book"], f=c.get("f"), v=c["votes"], sport=sport, edge=c.get("edge")) for c in ls]
        return dict(card=CONF_CARD, tier=tier, legs=L, p=round(math.prod(c["fair"] for c in ls), 5), d=round(math.prod(PL.dec(c["price"]) for c in ls), 3))
    out = []
    if len(pool) >= 2:
        out.append(mk(pool[:2], "2 legs"))
    if len(pool) >= 5:
        out.append(mk(pool[2:5], "3 legs"))
    elif len(pool) >= 3 and not out:
        out.append(mk(pool[:3], "3 legs"))
    ls = []                                                          # longshot: the strongest legs again (a leg in at most 2)
    for c in pool:
        ls.append(c)
        d = math.prod(PL.dec(x["price"]) for x in ls)
        if len(ls) >= 4 and d >= CONF_LONG[0]:
            break
        if len(ls) == 5:
            break
    if len(ls) >= 4 and CONF_LONG[0] <= math.prod(PL.dec(x["price"]) for x in ls) <= CONF_LONG[1]:
        out.append(mk(ls, f"{len(ls)}-leg longshot"))
    return out


# ---------------------------------------------------------------- 6. NBA sharp move parlays
# Owner 2026-10-25: "create parlays in nba based on our confluence up and down markers. When we see the baseline increase
# for a player and/or their odds drop, look and find a higher priced knee..." -- built that way first (a ladder knee above
# the main line with soft matchup / Value / LC / 5%+ edge), but research/moveknee_replay.py found 0 legs Oct 22-25: above
# the main line each step costs less than the last, so knees there almost never exist (3 of 202 moved props), and the
# confluence tags rarely coincide with a move. On the same 135 moved props: main-line over +6% (Pinnacle moves +24% on 37),
# ONE RUNG ABOVE THE MAIN LINE +13% (37.3% vs 32.1% priced), two above -17%, and requiring confluence made every rung worse.
# Owner: "change the card" -> trigger = a Pinnacle / consensus move up (line up, odds drop, or both); leg = the first rung
# above the main line at the owner's best price (Hard Rock / Novig, Kalshi with fee); no confluence filter (shown as context).
# Pinnacle moves rank first, then line up + odds drop, then edge. One leg per game; EXPERIMENTAL, tracked, never Today's card.
MK_CARD = "Sharp move parlays (experimental)"
MK_WPIN = .05                        # NBA weight on our number (mkt.js / screener WPIN for NBA props)


def build_moveknee(sport, cands, now):
    return moveknee_parlays(moveknee_legs(sport, cands, now))


def moveknee_legs(sport, cands, now):
    """[(score, leg)]: each moved prop's first rung above the main line at the owner's best price (one per prop)."""
    if sport != "nba":
        return []
    import sharp as SH
    try:
        PR = json.load(open(os.path.join(PL.SITE, "markets", "props_nba.json")))
        FL = json.load(open(os.path.join(PL.SITE, "markets", "flags_nba.json")))
    except (OSError, ValueError):
        return []
    try:
        LV = json.load(open(os.path.join(PL.SITE, "markets", "live_nba.json"))).get("rows", [])
    except (OSError, ValueError):
        LV = []
    C = {(str(c["gid"]), c["name"], c["kind"]): c for c in cands or [] if c.get("start")}
    moved = {}
    for m in FL.get("moves") or []:
        if m.get("sig") in ("line", "price", "both"):
            moved.setdefault((str(m["g"]), m["n"], m["k"]), set()).add((m.get("src"), m["sig"]))
    tagged = lambda src, g, n, k: any(isinstance(v, dict) and str(v.get("b", "")).endswith(" " + STAT.get(k, k)) for nm, v in ((FL.get(src) or {}).get(g) or {}).items() if nm == n)
    kal = {}
    for r in LV:
        if r.get("s") == "kalshi" and r.get("k") in SH.LC_LAB and r.get("l") is not None and r.get("n") and r.get("ya") and r.get("yb") is not None                 and r["ya"] - r["yb"] <= .03 and .01 < r["ya"] < .99:
            kal.setdefault((str(r["g"]), SH.jsnorm(r["n"]), r["k"]), {})[float(r["l"])] = (r["ya"], (r.get("p") or {}).get("vegas"))
    lg = lambda x: math.log(x / (1 - x))
    legs = []
    for key, mv in moved.items():
        gid, nm, k = key
        c = C.get(key)
        e = ((PR.get("props") or {}).get(gid) or {}).get(nm, {}).get(k)
        if not c or not e or e.get("l") is None or PL.when(c["start"]) <= now:
            continue
        main, pmain = float(e["l"]), e.get("p")
        mfair = (e.get("pin") or {}).get("o") if (e.get("pin") or {}).get("l") == e.get("l") else None
        rungs = {float(la): a for la, a in (e.get("a") or {}).items()}
        prices = {}                                                   # line -> [(book, american)] at the owner's books
        for l, a in rungs.items():
            for bk in ("Hard Rock", "Novig"):
                v = (a.get("o") or {}).get(bk)
                if v and v[0] is not None:
                    prices.setdefault(l, []).append((bk, v[0]))
        for l, (ya, _) in (kal.get(key) or {}).items():
            q = ya + .07 * ya * (1 - ya)
            prices.setdefault(l, []).append(("Kalshi", round(-100 * q / (1 - q)) if q >= .5 else round(100 * (1 - q) / q)))
        above = sorted(l for l in prices if l > main)
        if not above:
            continue
        l1 = above[0]
        bk, o1 = max(prices[l1], key=lambda x: PL.dec(x[1]))
        a = rungs.get(l1) or {}
        ours = a.get("p") if a.get("p") is not None else (kal.get(key) or {}).get(l1, (None, None))[1]
        fair = a.get("pin")
        if fair is None:                                              # the books' two-way no-vig at that rung (2+ books)
            ro, ru = a.get("o") or {}, a.get("u") or {}
            two = sorted((1 / SH.dec(ro[b][0])) / ((1 / SH.dec(ro[b][0])) + (1 / SH.dec(ru[b][0]))) for b in ro if b in ru and ro[b] and ru[b] and ro[b][0] is not None and ru[b][0] is not None)
            fair = two[len(two) // 2] if len(two) >= 2 else None
        if fair is None and mfair is not None and pmain and ours and 0 < pmain < 1 and 0 < ours < 1:
            fair = 1 / (1 + math.exp(-(lg(mfair) + lg(ours) - lg(pmain))))     # the main fair carried by our distribution (≈)
        if fair is None:
            continue                                                  # never our number alone
        chance = MK_WPIN * ours + (1 - MK_WPIN) * fair if ours is not None else fair
        edge = chance * PL.dec(o1) - 1
        if edge > .40:
            continue                                                  # 40%+ claimed edges are stale quotes / model error
        conf = (["soft matchup"] if "matchup" in (c.get("f") or []) else []) + (["Value"] if tagged("tags", gid, nm, k) else []) + (["LC"] if tagged("lc", gid, nm, k) else [])
        why = sorted(f"{'Pinnacle' if s_ == 'pin' else 'consensus'} {'line up' if g_ == 'line' else 'odds drop' if g_ == 'price' else 'line up + odds drop'}" for s_, g_ in mv)
        score = (int(any(s_ == "pin" for s_, _ in mv)), int(any(g_ == "both" for _, g_ in mv) or len({s_ for s_, _ in mv}) > 1), edge)
        legs.append((score, dict(gid=gid, start=c["start"], kind=k, side="o", line=l1, name=nm,
                                 lab=f"{c.get('lab') or nm.title()} over {l1:g} {STAT.get(k, k)}", p=round(chance, 4), fair=round(fair, 4),
                                 price=o1, book=bk, sport=sport, edge=round(edge, 4),
                                 v=why + [f"one rung above the main line ({main:g})"] + conf)))
    return legs


def moveknee_parlays(legs):
    """One leg per game (its strongest), then a 2-leg, a 3-leg and a second 2-leg from the next legs (a leg in at most 2)."""
    legs = sorted(legs, key=lambda x: tuple(-v for v in x[0]))
    per_game = {}
    for sc, L in legs:
        per_game.setdefault(L["gid"], L)
    pool = list(per_game.values())
    mk = lambda ls, tier: dict(card=MK_CARD, tier=tier, legs=ls, p=round(math.prod(L["fair"] for L in ls), 5), d=round(math.prod(PL.dec(L["price"]) for L in ls), 3))
    out = []
    if len(pool) >= 2:
        out.append(mk(pool[:2], "2 legs"))
    if len(pool) >= 3:
        out.append(mk(pool[:3], "3 legs"))
    if len(pool) >= 5:
        out.append(mk(pool[2:4], "2 legs"))
    return out


if __name__ == "__main__":
    for step in sys.argv[1:] or ["grade", "learn"]:
        try:
            {"grade": grade, "learn": learn}[step]()
        except Exception as e:                                                 # noqa: BLE001
            print(f"builder {step}: failed ({type(e).__name__}: {e})")
            PL.fail_note(f"builder {step}: {type(e).__name__}: {e}")
