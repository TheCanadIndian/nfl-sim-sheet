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


def builder_record():
    ps = []
    for fp in glob.glob(os.path.join(PL.OUT, "*", "*.json")):
        if os.sep + "learn" + os.sep in fp:
            continue
        d = json.load(open(fp))
        for p in d.get("parlays", []):
            if p.get("card") == CARD and p.get("res") in ("W", "L"):
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
              live_rows=sum(1 for r in rows if r.get("f") is not None), segments=segs, changes=changes[-60:], builder=builder_record())
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
        lab = lambda c: f"{c['lab']} {'anytime TD' if c['kind'] == 'td' else 'to score' if c['kind'] == 'goal' else 'over ' + str(c['line']) + ' ' + STAT.get(c['kind'], c['kind'])}"
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


if __name__ == "__main__":
    for step in sys.argv[1:] or ["grade", "learn"]:
        try:
            {"grade": grade, "learn": learn}[step]()
        except Exception as e:                                                 # noqa: BLE001
            print(f"builder {step}: failed ({type(e).__name__}: {e})")
