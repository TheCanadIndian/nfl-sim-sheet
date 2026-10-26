#!/usr/bin/env python3
"""
Intraday odds history for the watchlist charts (owner 2026-10-26: "create a line graph to show how the various book odds
move"). Every markets run appends, for each upcoming game, every over price at every book at every line (props file main
+ alt lines; Kalshi's ladder rungs from live_<sport>.json, ask + 7% fee) and the sharp fair at that line -- only when a
value changed since the last entry. -> site markets/hist_<sport>.json (served beside props_<sport>.json):
    {gid: {"s": start, "p": {"<name>|<kind>|<line>": {"b": {book: [[t, odds], ...]}, "f": [[t, fair], ...]}}}}
t = minutes since the epoch (UTC). Games are dropped 6 h after they start.

    python oddshist.py               # append the current snapshot (markets.yml)
    python oddshist.py backfill 30   # rebuild from the last N hours of committed props snapshots (PC, git history)
"""
import datetime as dt
import json
import os
import subprocess
import sys

import markets as M

SPORTS = ("nba", "nfl", "nhl")
KEEP_AFTER_START = 6 * 60                      # minutes


def fee_odds(ya):
    q = ya + .07 * ya * (1 - ya)
    return round(-100 * q / (1 - q)) if q >= .5 else round(100 * (1 - q) / q)


def snap(P, L, starts):
    """{gid: {key: ({book: odds}, fair)}} for games not started at the snapshot."""
    out = {}
    for gid, G in (P.get("props") or {}).items():
        for nm, kinds in G.items():
            for k, e in kinds.items():
                lines = []
                if e.get("o"):                                        # no line (anytime goal / TD): stored at 0.5
                    pin = e.get("pin") or {}
                    l0 = float(e["l"]) if e.get("l") is not None else .5
                    lines.append((l0, e["o"], pin.get("o") if pin.get("o") is not None and pin.get("l", e.get("l")) == e.get("l") else None))
                for la, a in (e.get("a") or {}).items():
                    if a.get("o"):
                        lines.append((float(la), a["o"], a.get("pin")))
                for l, o, fair in lines:
                    bk = {b: v[0] for b, v in o.items() if isinstance(v, list) and v and v[0] is not None and b != "Pinnacle"}
                    if bk:
                        out.setdefault(str(gid), {})[f"{nm}|{k}|{l:g}"] = (bk, fair)
    for r in (L or {}).get("rows", []):
        if r.get("s") != "kalshi" or r.get("l") is None or not r.get("n") or not r.get("ya") or r.get("yb") is None or not .01 < r["ya"] < .99:
            continue
        if r["ya"] - r["yb"] > .04:
            continue
        key = f"{jsnorm(r['n'])}|{r['k']}|{float(r['l']):g}"
        G = out.setdefault(str(r["g"]), {})
        bk, fair = G.get(key, ({}, None))
        bk = dict(bk, Kalshi=fee_odds(r["ya"]))
        G[key] = (bk, fair)
    return out


def jsnorm(s):
    import re
    import unicodedata
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z ]", "", s)
    s = re.sub(r"\b(jr|sr|ii|iii|iv)\b", "", s)
    return re.sub(r"\s+", " ", s).strip()


def starts_of(sport):
    try:
        S = json.load(open(os.path.join(M.SITE, "markets", "schedule.json"))).get("games", [])
    except (OSError, ValueError):
        S = []
    return {str(g["game"]): g["start"] for g in S if g.get("sport") == sport}


def merge(H, S, t, starts):
    for gid, G in S.items():
        st = starts.get(gid)
        if st and int(dt.datetime.fromisoformat(st).timestamp() // 60) <= t:
            continue                                                  # started: keep the pregame history only
        g = H.setdefault(gid, {"s": st, "p": {}})
        g["s"] = st or g.get("s")
        for key, (bk, fair) in G.items():
            e = g["p"].setdefault(key, {"b": {}, "f": []})
            for b, o in bk.items():
                h = e["b"].setdefault(b, [])
                if not h or h[-1][1] != o:
                    h.append([t, o])
            if fair is not None:
                fr = round(float(fair), 3)
                if not e["f"] or e["f"][-1][1] != fr:
                    e["f"].append([t, fr])


def prune(H, t):
    for gid in list(H):
        st = H[gid].get("s")
        if st and int(dt.datetime.fromisoformat(st).timestamp() // 60) + KEEP_AFTER_START < t:
            del H[gid]


def path(sport):
    return os.path.join(M.SITE, "markets", f"hist_{sport}.json")


def update():
    t = int(dt.datetime.now(dt.timezone.utc).timestamp() // 60)
    for sport in SPORTS:
        try:
            P = json.load(open(os.path.join(M.SITE, "markets", f"props_{sport}.json")))
        except (OSError, ValueError):
            continue
        try:
            L = json.load(open(os.path.join(M.SITE, "markets", f"live_{sport}.json")))
        except (OSError, ValueError):
            L = {}
        try:
            H = json.load(open(path(sport)))
        except (OSError, ValueError):
            H = {}
        starts = starts_of(sport)
        merge(H, snap(P, L, starts), t, starts)
        prune(H, t)
        json.dump(H, open(path(sport), "w"), separators=(",", ":"))
        print(f"odds history {sport}: {len(H)} games, {sum(len(g['p']) for g in H.values())} lines")


def backfill(hours):
    """Rebuild from the committed snapshots of the last `hours` (run on the PC: the cloud checkout has no history)."""
    git = lambda *a: subprocess.run(["git", "-C", M.SITE, *a], capture_output=True, text=True, encoding="utf-8").stdout
    since = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hours)).isoformat()
    for sport in SPORTS:
        starts = starts_of(sport)
        H = {}
        commits = [l.split() for l in git("log", f"--since={since}", "--format=%H %cI", "--", f"markets/props_{sport}.json").split("\n") if l.strip()][::-1]
        for h, ts in commits:
            t = int(dt.datetime.fromisoformat(ts).timestamp() // 60)
            try:
                P = json.loads(git("show", f"{h}:markets/props_{sport}.json") or "{}")
                L = json.loads(git("show", f"{h}:markets/live_{sport}.json") or "{}")
            except ValueError:
                continue
            merge(H, snap(P, L, starts), t, starts)
        now = int(dt.datetime.now(dt.timezone.utc).timestamp() // 60)
        prune(H, now)
        json.dump(H, open(path(sport), "w"), separators=(",", ":"))
        print(f"odds history {sport}: backfilled {len(commits)} snapshots -> {len(H)} games, {sum(len(g['p']) for g in H.values())} lines, "
              f"{os.path.getsize(path(sport)) // 1024} KB")


if __name__ == "__main__":
    if sys.argv[1:2] == ["backfill"]:
        backfill(float(sys.argv[2]) if len(sys.argv) > 2 else 30)
    else:
        update()
