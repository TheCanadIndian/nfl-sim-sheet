#!/usr/bin/env python3
"""
Plus-money slate -- EXPERIMENTAL (owner 2026-10-27: "a slate that can include straight bets, everything has to be plus money
250 or higher. Max 10 bets, straights about 3-4, the rest parlays... parlay legs also 250 or higher. Use all the methods
available, any sport... create [a model] to help improve the selection process... tracked and displayed"; judged on profit
over time; a learning model, not an LLM).

  1. candidates  every over at the owner's books (best of Hard Rock / Novig / Kalshi) priced +250 to +1000, main and alt
                 lines, NBA / NHL / NFL, that the sharp books price (fair at that line) -> logged each freeze
                 (parlays/slate/cands/<date>.json, last version before the start) and graded nightly
  2. the model   P(over) = sigmoid(logit(fair) + b . x): the market's fair is the base and the model learns what to add:
                 our number's gap to the fair, the price vs the fair, NHL goal drought (3+ games without a goal) / scored
                 last game, Value or LC tag, a Pinnacle / consensus move up, alt line, sport, anytime TD / goal, price band.
                 Seeded with research/parlay_reasoning*.py's 13,768 graded +250 legs (Oct 12-26, parlays/slate/seed.json:
                 features + result only), refit every night on seed + graded candidates (live rows weigh 2x), ridge toward 0.
  3. the slate   legs with model edge 5-60% (q x payout - 1); straights = the 4 best (one per game, $10); parlays = 2-leg
                 (legs from different games, best combined edge), $5, up to 10 bets in all, a leg in at most 2 bets.
                 Each bet locks 60 min before its first game; the rest re-pick every markets run.
Graded like every parlay (parlays.grade: a straight is a 1-leg entry); Profit rows 'SLATE parlays: Plus-money slate ...'.

    python slate.py grade learn      # (markets.yml, before parlays.py freeze)
"""
import datetime as dt
import glob
import json
import math
import os
import sys

import numpy as np

import parlays as PL

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(PL.OUT, "slate")
MODEL = os.path.join(OUT, "model.json")
SEED = os.path.join(OUT, "seed.json")
CARD_S, CARD_P = "Plus-money slate · straight", "Plus-money slate · parlay"
ODDS = (250, 1000)                   # every leg's American price (owner: +250 or higher; +1000 cap: the NBA alt-line sink)
EDGE = (.05, .60)
N_STRAIGHT, N_MAX = 4, 10
BOOKS = ("Hard Rock", "Novig", "Kalshi")
LOCK_MIN = 60
FEATS = ["gap", "pvf", "dry3", "hot0", "tag", "move", "alt", "nba", "nhl", "td", "goal", "far"]
LIVE_W, RIDGE = 2.0, 2.0
lg = lambda p: math.log(min(max(p, 1e-4), 1 - 1e-4) / (1 - min(max(p, 1e-4), 1 - 1e-4)))


def feats(sport, kind, fair, ours, imp, alt, dry=None, tag=False, move=False):
    """The model's inputs for one leg (same for the seed, the log and the build)."""
    return dict(gap=(lg(ours) - lg(fair)) if ours is not None else 0.0, pvf=lg(imp) - lg(fair),
                dry3=float(sport == "nhl" and kind == "goal" and dry is not None and dry >= 3),
                hot0=float(sport == "nhl" and kind == "goal" and dry == 0), tag=float(bool(tag)), move=float(bool(move)),
                alt=float(bool(alt)), nba=float(sport == "nba"), nhl=float(sport == "nhl"), td=float(kind == "td"), goal=float(kind == "goal"),
                far=float(imp < 1 / 7))                                   # +600 or longer


# ---------------------------------------------------------------- model
def fit(rows):
    """Ridge logistic with the market as offset: rows = [(fair, x dict, y, weight)]."""
    X = np.array([[r[1][f] for f in FEATS] for r in rows], float)
    off = np.array([lg(r[0]) for r in rows]); y = np.array([r[2] for r in rows], float); w = np.array([r[3] for r in rows], float)
    X = np.column_stack([np.ones(len(X)), X])
    b = np.zeros(X.shape[1]); R = RIDGE * np.eye(X.shape[1]); R[0, 0] = .01
    for _ in range(40):
        p = 1 / (1 + np.exp(-(off + X @ b)))
        H = X.T @ (X * (w * p * (1 - p))[:, None]) + R
        b = b + np.linalg.solve(H, X.T @ (w * (y - p)) - R @ b)
    se = np.sqrt(np.diag(np.linalg.inv(H)))
    return dict(zip(["const"] + FEATS, map(float, b))), dict(zip(["const"] + FEATS, map(float, se)))


def chance(m, fair, x):
    z = lg(fair) + m["const"] + sum(m[f] * x[f] for f in FEATS)
    return 1 / (1 + math.exp(-z))


def load_model():
    try:
        return json.load(open(MODEL))
    except (OSError, ValueError):
        return None


# ---------------------------------------------------------------- 1. candidates
def _signals(sport):
    """(gid, name) with a Value / LC tag, and props a Pinnacle / consensus move pushed up, from flags_<sport>.json."""
    try:
        FL = json.load(open(os.path.join(PL.SITE, "markets", f"flags_{sport}.json")))
    except (OSError, ValueError):
        return set(), set()
    tag = {(str(g), n) for k in ("tags", "lc") for g, T in (FL.get(k) or {}).items() for n, v in T.items() if isinstance(v, dict)}
    mv = {(str(x.get("g")), x.get("n")) for x in FL.get("moves") or []}
    return tag, mv


def candidates(now):
    try:
        S = json.load(open(os.path.join(PL.SITE, "markets", "schedule.json")))
    except (OSError, ValueError):
        S = {"games": []}
    try:
        DRY = json.load(open(os.path.join(PL.SITE, "markets", "dry_nhl.json"))).get("games", {})
    except (OSError, ValueError):
        DRY = {}
    m = load_model()
    out = []
    for sport in ("nba", "nhl", "nfl"):
        try:
            P = json.load(open(os.path.join(PL.SITE, "markets", f"props_{sport}.json")))
        except (OSError, ValueError):
            continue
        start = {str(g["game"]): g["start"] for g in S.get("games", []) if g.get("sport") == sport}
        tag, mv = _signals(sport)
        for gid, G in (P.get("props") or {}).items():
            st = start.get(str(gid))
            if not st or PL.when(st) <= now:
                continue
            for nm, ks in G.items():
                for k, e in ks.items():
                    ml = e.get("l") if e.get("l") is not None else .5
                    pin = e.get("pin") or {}
                    lines = [(ml, e.get("o") or {}, pin.get("o") if pin.get("o") is not None and pin.get("l", ml) == ml else None, e.get("p"))]
                    for l, a in (e.get("a") or {}).items():
                        try:
                            if float(l) != ml:
                                lines.append((float(l), a.get("o") or {}, a.get("pin"), a.get("p")))
                        except ValueError:
                            pass
                    for l, o, fair, ours in lines:
                        if fair is None or not 0 < fair < 1:
                            continue
                        bo = [(PL.dec(v[0]), b, v[0]) for b, v in o.items() if b in BOOKS and isinstance(v, list) and v and v[0] is not None]
                        if not bo:
                            continue
                        d, book, price = max(bo)
                        if not ODDS[0] <= price <= ODDS[1]:
                            continue
                        kind = "sog" if k.startswith("sog") else k
                        dry = ((DRY.get(str(gid)) or {}).get("dry") or {}).get(nm) if sport == "nhl" else None
                        x = feats(sport, kind, fair, ours, 1 / d, l != ml, dry, (str(gid), nm) in tag, (str(gid), nm) in mv)
                        q = chance(m["coef"], fair, x) if m else fair
                        out.append(dict(sport=sport, gid=str(gid), start=st, name=nm, kind=kind, k=k, line=l, main=l == ml, price=price, book=book,
                                        fair=round(fair, 4), p=None if ours is None else round(ours, 4), x=x, q=round(q, 4), edge=round(q * d - 1, 4),
                                        dry=dry))
    return out


def log_cands(cands, now):
    by = {}
    for c in cands:
        by.setdefault(PL.et_date(c["start"]), []).append(c)
    for date, cs in by.items():
        fp = os.path.join(OUT, "cands", f"{date}.json")
        old = json.load(open(fp)) if os.path.exists(fp) else {"date": date, "cands": {}}
        C = old["cands"]
        for c in cs:
            key = f"{c['sport']}|{c['gid']}|{c['name']}|{c['k']}|{c['line']}"
            if key in C and PL.when(C[key]["start"]) <= now:
                continue
            C[key] = dict({k: c[k] for k in ("sport", "gid", "start", "name", "kind", "line", "price", "book", "fair", "p", "x", "q")}, seen=now.isoformat(timespec="seconds"))
        os.makedirs(os.path.dirname(fp), exist_ok=True)
        json.dump(old, open(fp, "w"), separators=(",", ":"))


# ---------------------------------------------------------------- 2. grade + learn
def grade():
    now = dt.datetime.now(dt.timezone.utc)
    for fp in sorted(glob.glob(os.path.join(OUT, "cands", "*.json"))):
        d = json.load(open(fp))
        if d.get("graded"):
            continue
        games, pending = {}, 0
        for c in d["cands"].values():
            if c.get("res") in ("W", "L", "P", "V"):
                continue
            if PL.when(c["start"]) > now - dt.timedelta(hours=3):
                pending += 1
                continue
            key = (c["sport"], c["gid"])
            if key not in games:
                try:
                    games[key] = PL.game_for(c["sport"], c["gid"], c["start"])
                except Exception:                                               # noqa: BLE001
                    games[key] = None
            g = games[key]
            if not g:
                pending += 1
                continue
            c["res"] = PL.settle(dict(kind=c["kind"], side="o", line=c["line"], name=c["name"]), g)
        d["graded"] = pending == 0 and all(c.get("res") for c in d["cands"].values())
        json.dump(d, open(fp, "w"), separators=(",", ":"))
    print(f"slate: graded candidate files {len(glob.glob(os.path.join(OUT, 'cands', '*.json')))}")


def learn():
    seed = json.load(open(SEED)) if os.path.exists(SEED) else []
    live = []
    for fp in sorted(glob.glob(os.path.join(OUT, "cands", "*.json"))):
        for c in json.load(open(fp))["cands"].values():
            if c.get("res") in ("W", "L"):
                live.append((c["fair"], c["x"], int(c["res"] == "W"), LIVE_W, os.path.basename(fp)[:10], c["price"]))
    rows = [(r["f"], r["x"], r["y"], 1.0) for r in seed] + [r[:4] for r in live]
    if len(rows) < 200:
        print("slate: too few rows to fit"); return
    coef, se = fit(rows)
    old = load_model() or {}
    # the model's own live record: log-loss vs the market fair, and what its 5%+ edges returned (each half of the live days)
    rec = {}
    if live:
        days = sorted({r[4] for r in live}); half = set(days[:len(days) // 2])
        ll = lambda y, q: -(y * math.log(max(q, 1e-6)) + (1 - y) * math.log(max(1 - q, 1e-6)))
        qs = [chance(old.get("coef") or coef, r[0], r[1]) for r in live]      # yesterday's model, not refit on these rows
        ret = [(r[2] * PL.dec(r[5]) - 1, r[4] in half) for r, q in zip(live, qs) if q * PL.dec(r[5]) - 1 >= EDGE[0]]
        rec = dict(n=len(live), ll_model=round(sum(ll(r[2], q) for r, q in zip(live, qs)) / len(live), 4),
                   ll_market=round(sum(ll(r[2], r[0]) for r in live) / len(live), 4), picks=len(ret),
                   picks_ret=round(sum(x for x, _ in ret) / len(ret), 4) if ret else None,
                   h1=round(np.mean([x for x, h in ret if h]), 4) if any(h for _, h in ret) else None,
                   h2=round(np.mean([x for x, h in ret if not h]), 4) if any(not h for _, h in ret) else None)
    st = dict(updated=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), n_seed=len(seed), n_live=len(live),
              coef={k: round(v, 4) for k, v in coef.items()}, se={k: round(v, 4) for k, v in se.items()}, live=rec,
              history=(old.get("history") or [])[-59:] + [dict(date=dt.datetime.now(PL.ET).date().isoformat(), n_live=len(live), **{k: round(v, 3) for k, v in coef.items()})],
              record=record())
    os.makedirs(OUT, exist_ok=True)
    json.dump(st, open(MODEL, "w"), indent=0)
    print(f"slate model: {len(seed)} seed + {len(live)} live rows; coef " + ", ".join(f"{k} {v:+.2f}" for k, v in coef.items()) + f"; live {rec}")


def record():
    """The slate's own graded record ($10 straights, $5 parlays)."""
    out = {"straight": [], "parlay": []}
    for fp in sorted(glob.glob(os.path.join(OUT, "*.json"))):
        if os.path.basename(fp)[:4].isdigit():
            d = json.load(open(fp))
            for q in d.get("parlays", []):
                if q.get("res") in ("W", "L"):
                    t = "straight" if q["card"] == CARD_S else "parlay"
                    out[t].append((d["date"], ((q.get("paid") or q["d"]) - 1) if q["res"] == "W" else -1.0))
    res = {}
    for t, stake in (("straight", 10.0), ("parlay", 5.0)):
        x = out[t]
        res[t] = dict(n=len(x), w=sum(1 for _, r in x if r > -1), net=round(sum(r for _, r in x) * stake, 2), staked=len(x) * stake)
    days = sorted({d for t in out for d, _ in out[t]})
    res["days"] = len(days)
    res["net"] = round(res["straight"]["net"] + res["parlay"]["net"], 2)
    res["staked"] = res["straight"]["staked"] + res["parlay"]["staked"]
    return res


# ---------------------------------------------------------------- 3. the slate
def build(cands, now):
    """Straights (the 4 best legs, one per game) and 2-leg parlays (different games), up to 10 bets."""
    legs = [c for c in cands if EDGE[0] <= c["edge"] <= EDGE[1]]
    legs.sort(key=lambda c: -c["edge"])
    best = {}
    for c in legs:                                                    # one leg per player
        best.setdefault((c["sport"], c["gid"], c["name"]), c)
    pool = list(best.values())
    lab = lambda c: f"{c['name'].title()} {'anytime TD' if c['kind'] == 'td' else 'to score' if c['kind'] == 'goal' else ('over ' + format(c['line'], 'g') + ' ' + c['kind'].replace('_', ' '))}"
    leg = lambda c: dict(gid=c["gid"], start=c["start"], kind=c["kind"], side="o", line=c["line"], name=c["name"], lab=lab(c), p=c["q"],
                         price=c["price"], book=c["book"], sport=c["sport"], edge=c["edge"], fair=c["fair"], dry=c.get("dry"))
    out, used, games_s = [], {}, set()
    for c in pool:                                                     # straights
        if len([q for q in out if q["card"] == CARD_S]) >= N_STRAIGHT:
            break
        if (c["sport"], c["gid"]) in games_s:
            continue
        games_s.add((c["sport"], c["gid"]))
        out.append(dict(card=CARD_S, tier="straight", legs=[leg(c)], p=c["q"], d=round(PL.dec(c["price"]), 3)))
        used[id(c)] = used.get(id(c), 0) + 1
    pairs = []
    top = pool[:30]
    for i in range(len(top)):
        for j in range(i + 1, len(top)):
            a, b = top[i], top[j]
            if (a["sport"], a["gid"]) == (b["sport"], b["gid"]):
                continue
            pairs.append(((a["q"] * PL.dec(a["price"])) * (b["q"] * PL.dec(b["price"])) - 1, a, b))
    pairs.sort(key=lambda t: -t[0])
    for e, a, b in pairs:
        if len(out) >= N_MAX:
            break
        if used.get(id(a), 0) >= 2 or used.get(id(b), 0) >= 2:
            continue
        used[id(a)] = used.get(id(a), 0) + 1; used[id(b)] = used.get(id(b), 0) + 1
        out.append(dict(card=CARD_P, tier="2 legs", legs=[leg(a), leg(b)], p=round(a["q"] * b["q"], 5), d=round(PL.dec(a["price"]) * PL.dec(b["price"]), 3)))
    return out


def freeze(now):
    """Called by parlays.freeze after the sport pages: log candidates, rebuild today's slate, keep locked bets."""
    cands = candidates(now)
    log_cands(cands, now)
    new = build(cands, now)
    for q in new:
        q["first"] = min(PL.when(L["start"]) for L in q["legs"]).astimezone(dt.timezone.utc).isoformat()
    by = {}
    for q in new:
        by.setdefault(PL.et_date(q["first"]), []).append(q)
    today = dt.datetime.now(PL.ET).date()
    dates = set(by) | {os.path.basename(f)[:10] for f in glob.glob(os.path.join(OUT, "????-??-??.json")) if dt.date.fromisoformat(os.path.basename(f)[:10]) >= today}
    for date in sorted(dates):
        fp = os.path.join(OUT, f"{date}.json")
        old = json.load(open(fp)) if os.path.exists(fp) else None
        if old and old.get("graded"):
            continue
        lock = lambda q: PL.when(q["first"]) - dt.timedelta(minutes=LOCK_MIN)
        locked = [dict(q, locked=True) for q in (old["parlays"] if old else []) if q.get("locked") or now >= lock(q)]
        key = lambda q: tuple(sorted((L["sport"], L["gid"], L["name"], L["line"]) for L in q["legs"]))
        lk = {key(q) for q in locked}
        lgames = {(L["sport"], L["gid"]) for q in locked if q["card"] == CARD_S for L in q["legs"]}
        n_s = sum(1 for q in locked if q["card"] == CARD_S)
        keep = []
        for q in by.get(date, []):
            if now >= lock(q) or key(q) in lk:
                continue
            if q["card"] == CARD_S:
                if n_s >= N_STRAIGHT or (q["legs"][0]["sport"], q["legs"][0]["gid"]) in lgames:
                    continue
                n_s += 1
            keep.append(q)
        ps = locked + keep[:max(0, N_MAX - len(locked))]
        if not ps or (old and ps == old["parlays"]):
            continue
        json.dump(dict(sport="slate", date=date, lock=min(q["first"] for q in ps), frozen_at=now.isoformat(), books=list(BOOKS),
                       parlays=ps, graded=False), open(fp, "w"), indent=0)
        print(f"slate {date}: {len(locked)} locked, {len(ps) - len(locked)} still updating ({len(cands)} candidates)")


if __name__ == "__main__":
    for step in sys.argv[1:] or ["grade", "learn"]:
        try:
            {"grade": grade, "learn": learn}[step]()
        except Exception as e:                                                 # noqa: BLE001
            print(f"slate {step}: failed ({type(e).__name__}: {e})")
            PL.fail_note(f"slate {step}: {type(e).__name__}: {e}")
