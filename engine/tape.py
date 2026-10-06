#!/usr/bin/env python3
"""
Market tape: every pregame market snapshot the cloud markets job takes (every 5 minutes in the 3
hours before games, 15 otherwise), kept for studies that hourly price history can't answer:
order-book imbalance, exact timing, and whether our flagged prices beat the close.

One gzip file per UTC day, tape/<YYYY-MM-DD>.jsonl.gz, one JSON line per market per snapshot. In
the cloud the files live on the GitHub release "market-tape" (not in the repo, so it doesn't bloat):
each run downloads today's file, appends, and uploads it back.

    python tape.py get 2026-10-05      # download a day's file into tape/
    python tape.py summary 2026-10-05  # snapshots / markets per sport in that file
"""

import gzip
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DIR = os.path.join(HERE, "tape")
RELEASE = "market-tape"
REPO = os.environ.get("GITHUB_REPOSITORY") or "TheCanadIndian/nfl-sim-sheet"


def _gh(*a):
    gh = shutil.which("gh")
    if not gh:
        return 1
    return subprocess.run([gh, *a, "-R", REPO], capture_output=True, text=True, cwd=HERE).returncode


def row(sport, ts, r):
    """Compact line for one market: price, top of book, depth on each side, volume, our chances."""
    b = r.get("book") or {}
    return dict(ts=ts, sp=sport, src=r["source"][0], t=r["ticker"], g=r["game"], k=r["kind"], l=r.get("line"),
                pid=r.get("pid"), yb=r.get("yes_bid"), ya=r.get("yes_ask"), bs=r.get("bid_size"), as_=r.get("ask_size"),
                by1=b.get("ask_1c"), by3=b.get("ask_3c"), bn3=b.get("no_3c"), bb1=b.get("bid_1c"), bb3=b.get("bid_3c"),
                v=r.get("vol"), oi=r.get("oi"), liq=r.get("liq"), p=r.get("ours"), st=r.get("start"))


def record(sport, rows, ts):
    """Append this snapshot's rows to today's file (and push it to the release when in the cloud)."""
    if not rows:
        return
    day = ts[:10]
    os.makedirs(DIR, exist_ok=True)
    name = f"{day}.jsonl.gz"
    path = os.path.join(DIR, name)
    cloud = bool(os.environ.get("GITHUB_ACTIONS"))
    if cloud and not os.path.exists(path):
        _gh("release", "download", RELEASE, "-p", name, "-D", DIR, "--clobber")     # absent on a new day: fine
    with gzip.open(path, "at", encoding="utf-8") as f:            # gzip members concatenate cleanly
        for r in rows:
            f.write(json.dumps(row(sport, ts, r), separators=(",", ":"), default=str) + "\n")
    if cloud:
        rc = _gh("release", "upload", RELEASE, path, "--clobber")
        print(f"tape: {len(rows)} {sport} rows -> release {RELEASE}/{name}" + ("" if rc == 0 else f" (upload failed, rc {rc})"))
    else:
        print(f"tape: {len(rows)} {sport} rows -> {path}")


def main(cmd, day):
    name = f"{day}.jsonl.gz"
    if cmd == "get":
        os.makedirs(DIR, exist_ok=True)
        rc = _gh("release", "download", RELEASE, "-p", name, "-D", DIR, "--clobber")
        print("downloaded" if rc == 0 else "not found", name)
    elif cmd == "summary":
        snaps, n = {}, {}
        with gzip.open(os.path.join(DIR, name), "rt", encoding="utf-8") as f:
            for line in f:
                r = json.loads(line)
                snaps.setdefault(r["sp"], set()).add(r["ts"]); n[r["sp"]] = n.get(r["sp"], 0) + 1
        for sp in snaps:
            print(f"{sp}: {len(snaps[sp])} snapshots, {n[sp]} market rows")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
