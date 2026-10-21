#!/usr/bin/env python3
"""
NBA play-by-play from the cached ESPN game summaries (nba/raw/<season>/<game_id>.json.gz, written by fetch.py) into
nba/plays.db table `plays` -- phase 1 of the possession simulator (owner 2026-10-21). A separate file kept on the PC:
nba.db is restored by every cloud run, so it stays small; the simulator trains here and publishes compact parameter files.

One row per play: type (ESPN id + text), the team, participants (p1 = shooter / rebounder / player entering, p2 =
assister / player leaving / fouled), shot flag, make, points, points attempted, court coordinates (ESPN's: x 0-50 across,
y 0-47 from the baseline; missing for non-shots), the score, seconds left in the period, and the ten players on the
floor (starters, then every substitution) as two comma-separated id lists.

    python nba/plays.py            # parse every cached game not yet in the table
    python nba/plays.py --rebuild  # drop and parse everything
"""
import glob
import gzip
import json
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "nba.db")
PLAYS = os.path.join(HERE, "plays.db")
RAW = os.path.join(HERE, "raw")

COLS = ["game_id", "seq", "period", "sec", "team", "type_id", "type", "shot", "made", "pts", "att", "x", "y", "p1", "p2",
        "home_score", "away_score", "home_on", "away_on"]


def clock_s(v):
    try:
        m, s = str(v).split(":") if ":" in str(v) else ("0", str(v))
        return round(int(m) * 60 + float(s), 1)
    except ValueError:
        return None


def parse(gid, d):
    teams = {str(t["team"]["id"]): t["team"]["abbreviation"] for t in (d.get("boxscore") or {}).get("teams", [])}
    comp = ((d.get("header") or {}).get("competitions") or [{}])[0]
    side = {str(c["team"]["id"]): c["homeAway"] for c in comp.get("competitors", [])}
    on = {"home": [], "away": []}
    for t in (d.get("boxscore") or {}).get("players", []):
        h = side.get(str(t["team"]["id"]))
        for st in t.get("statistics", []):
            for a in st.get("athletes", []):
                if a.get("starter") and h:
                    on[h].append(str(a["athlete"]["id"]))
    plays = d.get("plays", [])
    sub_of = lambda p: (p.get("type") or {}).get("id") == "584"
    parts_of = lambda p: [str(x["athlete"]["id"]) for x in p.get("participants", []) if x.get("athlete")]
    # each period's opening five per team (ESPN logs no between-period changes): players who act in the period, or are
    # subbed out, before being subbed in -- in order of first appearance; topped up from the previous period's end
    by_period = {}
    for p in plays:
        by_period.setdefault((p.get("period") or {}).get("number"), []).append(p)
    opening = {}
    for per, L in by_period.items():
        seen, sub_in = {"home": [], "away": []}, {"home": set(), "away": set()}
        for p in L:
            sd, parts = side.get(str((p.get("team") or {}).get("id") or "")), parts_of(p)
            if not sd or not parts:
                continue
            if sub_of(p):
                if len(parts) >= 2 and parts[1] not in sub_in[sd] and parts[1] not in seen[sd]:
                    seen[sd].append(parts[1])
                sub_in[sd].add(parts[0])
                continue
            cand = [parts[0]] + ([parts[1]] if p.get("shootingPlay") and p.get("scoringPlay") and len(parts) > 1 else [])   # shooter (+ assister)
            for pid in cand:
                if pid not in sub_in[sd] and pid not in seen[sd]:
                    seen[sd].append(pid)
        opening[per] = (seen, sub_in)
    rows, last_per = [], None
    for p in plays:
        per = (p.get("period") or {}).get("number")
        if per != last_per:
            if last_per is not None and per in opening:                          # new period: its inferred opening five
                seen, sub_in = opening[per]
                for sd in ("home", "away"):
                    five = seen[sd][:5]
                    for pid in on[sd]:                                          # top up from who ended the last period
                        if len(five) >= 5:
                            break
                        if pid not in five and pid not in sub_in[sd]:
                            five.append(pid)
                    if len(five) == 5:
                        on[sd] = five
            last_per = per
        tid = str((p.get("team") or {}).get("id") or "")
        parts = parts_of(p)
        typ = (p.get("type") or {})
        if sub_of(p) and len(parts) >= 2 and side.get(tid):                     # substitution: p1 enters for p2
            L = on[side[tid]]
            if parts[1] in L:
                L[L.index(parts[1])] = parts[0]
            elif parts[0] not in L:
                L.append(parts[0])
        c = p.get("coordinate") or {}
        x, y = c.get("x"), c.get("y")
        if x is None or y is None or abs(x) > 1000 or abs(y) > 1000:
            x = y = None
        shot = bool(p.get("shootingPlay"))
        rows.append((int(gid), int(p.get("sequenceNumber") or 0), per, clock_s((p.get("clock") or {}).get("displayValue")),
                     teams.get(tid), int(typ.get("id") or 0), str(typ.get("text") or "").replace("\n", " "), int(shot),
                     int(bool(p.get("scoringPlay")) and shot), int(p.get("scoreValue") or 0), int(p.get("pointsAttempted") or 0), x, y,
                     parts[0] if parts else None, parts[1] if len(parts) > 1 else None, p.get("homeScore"), p.get("awayScore"),
                     ",".join(on["home"][:5]), ",".join(on["away"][:5])))
    return rows


def main():
    con = sqlite3.connect(PLAYS)
    con.execute(f"ATTACH DATABASE '{DB}' AS nba")
    if "--rebuild" in sys.argv:
        con.execute("DROP TABLE IF EXISTS plays")
    con.execute(f"CREATE TABLE IF NOT EXISTS plays ({', '.join(COLS)})")
    con.execute("CREATE INDEX IF NOT EXISTS plays_game ON plays(game_id)")
    have = {r[0] for r in con.execute("SELECT DISTINCT game_id FROM plays")}
    final = {r[0] for r in con.execute("SELECT game_id FROM nba.games WHERE state = 'FINAL'")}
    files = sorted(glob.glob(os.path.join(RAW, "*", "*.json.gz")))
    n = 0
    for f in files:
        gid = int(os.path.basename(f).split(".")[0])
        if gid in have or gid not in final:
            continue
        try:
            d = json.load(gzip.open(f))
        except (OSError, ValueError):
            continue
        rows = parse(gid, d)
        if rows:
            con.executemany(f"INSERT INTO plays VALUES ({', '.join('?' * len(COLS))})", rows)
            n += 1
        if n % 500 == 0 and n:
            con.commit(); print(f"  {n} games parsed", flush=True)
    con.commit()
    tot = con.execute("SELECT COUNT(DISTINCT game_id), COUNT(*), SUM(shot) FROM plays").fetchone()
    print(f"plays: {n} new games; table has {tot[0]:,} games, {tot[1]:,} plays, {tot[2]:,} shots")


if __name__ == "__main__":
    main()
