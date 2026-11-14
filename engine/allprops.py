#!/usr/bin/env python3
"""
Every prop, not just our bets (owner 2026-11-09: 'collect all the props that win per archetype per odds range ... lets us know
what type of player has what type of props mispriced' -> 'yes add it'). research/arch_market.py was the first read.

1) capture (oddshist.update calls it as each game starts): every over at Hard Rock / Novig / Kalshi (fee in), main + alt
   lines, its last pregame price + the last sharp fair -> markets/allprops_pending_<sport>.json {gid: {s, r: [[name, kind,
   line, book, odds, fair]]}} (NBA pts / reb / ast / 3PM; NFL rec yds / catches / rush yds / pass yds / anytime TD).
2) grade (markets.yml, before the Profit page): finished games settled from box scores (parlays.game_for / settle), counted
   into markets/allprops_<sport>.json {days: {date: {"group|stat|band": [n, won, priced, fair]}}} (group = NBA archetype /
   NFL role at grading; band = profit.ODDS_BANDS index; priced = sum of 1 / payout); pending games dropped after 3 days.
   Only these small daily counts are kept (90 days).
3) profit.feed_data reads the last 30 days into the 'straights by archetype / role' tables: every prop's won vs priced, and
   'vs price-mates' = won vs what every other archetype's props at the same stat x odds range won per unit priced (the
   longshot / vig effect taken out: research/arch_market.py found raw cells look steady only because of it).

    python allprops.py               # grade (markets.yml)
    python allprops.py backfill      # PC: seed the counts from research/pull_timing_<sport>.pkl (Hard Rock / Novig (+ Kalshi NFL))
"""
import datetime as dt
import json
import os
import sys

import markets as M

SPORTS = ("nba", "nfl")
OWN = ("Hard Rock", "Novig", "Kalshi")
KINDS = {"nba": ("pts", "reb", "ast", "fg3m"), "nfl": ("rec_yds", "rec", "rush_yds", "pass_yds", "td")}
KEEP_DAYS = 90


def _p(name):
    return os.path.join(M.SITE, "markets", name)


def _load(fp, dflt):
    try:
        return json.load(open(fp))
    except (OSError, ValueError):
        return dflt


def capture(sport, H, t):
    """H = oddshist history for the sport; games started at minute t and not captured yet -> pending."""
    if sport not in SPORTS:
        return 0
    fp = _p(f"allprops_pending_{sport}.json")
    P = _load(fp, {})
    n = 0
    for gid, g in H.items():
        st = g.get("s")
        if not st or g.get("cap") or int(dt.datetime.fromisoformat(st).timestamp() // 60) > t:
            continue
        rows = []
        for key, e in g["p"].items():
            nm, k, l = key.rsplit("|", 2)
            if k not in KINDS[sport]:
                continue
            fair = e["f"][-1][1] if e.get("f") else None
            for b, h in e["b"].items():
                if b in OWN and h:
                    rows.append([nm, k, float(l), b, h[-1][1], fair])
        g["cap"] = True
        if rows:
            P[gid] = dict(s=st, r=rows)
            n += len(rows)
    if n:
        json.dump(P, open(fp, "w"), separators=(",", ":"))
    return n


def _dec(o):
    return 1 + o / 100 if o > 0 else 1 + 100 / abs(o)


def _band(d):
    from profit import ODDS_BANDS
    if d < 2:
        return 0
    am = round((d - 1) * 100)
    return next(i for i, (lo, hi, _) in enumerate(ODDS_BANDS) if lo <= am <= hi)


def _groups(sport):
    return _load(os.path.join(M.SITE, "markets", "arch_nba.json" if sport == "nba" else "role_nfl.json"), {})


def _add(A, day, grp, kind, d, y, fair):
    from profit import ST_LAB
    st = ST_LAB.get(kind, kind)
    key = f"{grp}|{st}|{_band(d)}"
    c = A.setdefault("days", {}).setdefault(day, {}).setdefault(key, [0, 0, 0.0, 0.0])
    c[0] += 1; c[1] += y; c[2] = round(c[2] + 1 / d, 4); c[3] = round(c[3] + (fair if fair is not None else 1 / d / 1.045), 4)


def _add_dr(A, day, gr, kind, main, d, y, fair):
    """NBA defense rating (nba/defrate.py, owner 2026-11-14 'show all'): every over at your books by tonight's grade
    -> A['drate'][day]['grade|stat|main or alt|band'] = [n, won, priced, fair, paid] (paid = payouts of the winners, so the
    return is paid / n - 1); the Profit page's 'NBA defense rating' (Breakdowns) reads it."""
    from profit import ST_LAB
    key = f"{gr}|{ST_LAB.get(kind, kind)}|{'main' if main else 'alt'}|{_band(d)}"
    c = A.setdefault("drate", {}).setdefault(day, {}).setdefault(key, [0, 0, 0.0, 0.0, 0.0])
    c[0] += 1; c[1] += y; c[2] = round(c[2] + 1 / d, 4); c[3] = round(c[3] + (fair if fair is not None else 1 / d / 1.045), 4); c[4] = round(c[4] + d * y, 4)


def _et_day(st):
    from zoneinfo import ZoneInfo
    return dt.datetime.fromisoformat(st).astimezone(ZoneInfo("America/New_York")).date().isoformat()


def grade():
    import parlays as PL
    from parlays import jsnorm
    now = dt.datetime.now(dt.timezone.utc)
    for sport in SPORTS:
        fp = _p(f"allprops_pending_{sport}.json")
        P = _load(fp, {})
        if not P:
            continue
        A = _load(_p(f"allprops_{sport}.json"), {})
        G = _groups(sport)
        DRT = _load(_p("drate_nba.json"), {}).get("games", {}) if sport == "nba" else {}
        BL = _load(_p("boxlines_nba.json"), {}) if sport == "nba" else {}
        done = 0
        for gid in list(P):
            st = dt.datetime.fromisoformat(P[gid]["s"])
            if st > now - dt.timedelta(hours=3):
                continue
            try:
                g = PL.game_for(sport, gid, P[gid]["s"])
            except Exception:                                                   # noqa: BLE001
                g = None
            if not g:
                if st < now - dt.timedelta(days=3):
                    del P[gid]
                continue
            day = _et_day(P[gid]["s"])
            for nm, k, l, b, o, fair in P[gid]["r"]:
                res = PL.settle(dict(kind=k, side="o", line=l, name=nm), g)
                if res not in ("W", "L"):
                    continue
                _add(A, day, G.get(jsnorm(nm)) or "unknown", k, _dec(o), int(res == "W"), fair)
                x = ((DRT.get(str(gid)) or {}).get("p") or {}).get(jsnorm(nm), {}).get(k)
                if x:
                    ml = ((BL.get(str(gid)) or {}).get("p") or {}).get(jsnorm(nm), {}).get(k)
                    _add_dr(A, day, x[1], k, ml is not None and abs(float(ml) - float(l)) < .01, _dec(o), int(res == "W"), fair)
            del P[gid]; done += 1
        cut = (now.date() - dt.timedelta(days=KEEP_DAYS)).isoformat()
        A["days"] = {d: v for d, v in (A.get("days") or {}).items() if d >= cut}
        if A.get("drate"):
            A["drate"] = {d: v for d, v in A["drate"].items() if d >= cut}
        json.dump(A, open(_p(f"allprops_{sport}.json"), "w"), separators=(",", ":"), sort_keys=True)
        json.dump(P, open(fp, "w"), separators=(",", ":"))
        print(f"allprops {sport}: graded {done} games, {len(P)} pending, {len(A['days'])} days kept")


def backfill():
    """PC: last pregame price of every over in research/pull_timing_<sport>.pkl (already graded) -> the daily counts."""
    import pandas as pd
    from parlays import jsnorm
    for sport in SPORTS:
        fp = os.path.join(os.path.dirname(os.path.abspath(__file__)), "research", f"pull_timing_{sport}.pkl")
        if not os.path.exists(fp):
            continue
        D = pd.read_pickle(fp)
        D = D[(D.t < D.start) & D.bk.isin(OWN) & D.k.isin(KINDS[sport])].sort_values("t").groupby(["g", "n", "k", "l", "bk"]).tail(1)
        A = _load(_p(f"allprops_{sport}.json"), {})
        G = _groups(sport)
        have = set((A.get("days") or {}))
        D["day"] = D.start.dt.tz_convert("America/New_York").dt.date.astype(str)
        D = D[~D.day.isin(have)]
        for r in D.itertuples():
            _add(A, r.day, G.get(jsnorm(r.n)) or "unknown", r.k, _dec(r.o), int(r.y), None if pd.isna(r.fair) else float(r.fair))
        json.dump(A, open(_p(f"allprops_{sport}.json"), "w"), separators=(",", ":"), sort_keys=True)
        print(f"allprops {sport}: backfilled {len(D):,} prices, {D.day.nunique()} days")


if __name__ == "__main__":
    backfill() if sys.argv[1:2] == ["backfill"] else grade()
