#!/usr/bin/env python3
"""
Quick check: should a full NBA update run now? (owner 2026-10-23: "NBA should be on the same schedule as the other
sports" -- the NHL's rules, hockey/should_run.py.) Exit 0 = run, 10 = skip. Prints one line with the reason.
One small ESPN request. markets.yml (every ~15 min) runs this and starts nba.yml when it says run.

Runs a full update when:
  - a game tips within the next SOON minutes and hasn't started (late scratches, starters)
  - there's a game later today and the last full update is older than REFRESH_HOURS
  - it's after 10 AM, there were games yesterday, and today's grading hasn't run yet
"""

import datetime as dt
import json
import os
import sys

import pandas as pd
import requests

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, ".last_run.json")
API = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"
SOON = 75                 # minutes before tip-off
REFRESH_HOURS = 4
GRADE_AFTER_HOUR = 10


def games_on(day):
    r = requests.get(f"{API}/scoreboard?dates={day:%Y%m%d}&limit=50", timeout=30, headers={"User-Agent": "Mozilla/5.0"})
    return r.json().get("events", [])


def main():
    now = pd.Timestamp.now(tz="America/New_York")
    state = json.load(open(STATE)) if os.path.exists(STATE) else {}
    last = pd.Timestamp(state["last_full"]) if "last_full" in state else None
    try:
        today = games_on(now.date())
        yday = games_on((now - pd.Timedelta(days=1)).date())
    except Exception as e:                                            # noqa: BLE001
        print(f"run: schedule check failed ({type(e).__name__}); running to be safe")
        return 0
    upcoming = [pd.Timestamp(e["date"]).tz_convert("America/New_York") for e in today
                if ((e.get("status") or {}).get("type") or {}).get("state") == "pre"]
    upcoming = [t for t in upcoming if t > now]
    if upcoming:
        nxt = min(upcoming)
        mins = (nxt - now).total_seconds() / 60
        if mins <= SOON:
            print(f"run: game at {nxt:%I:%M %p} tips in {mins:.0f} min")
            return 0
        if last is None or (now - last).total_seconds() > REFRESH_HOURS * 3600:
            print(f"run: next game {nxt:%I:%M %p}; last update {'never' if last is None else f'{last:%I:%M %p}'}")
            return 0
    yesterday = (now - pd.Timedelta(days=1)).date().isoformat()
    if yday and now.hour >= GRADE_AFTER_HOUR and state.get("graded_for") != yesterday:
        print(f"run: grade games from {yesterday}")
        return 0
    print(f"skip: {'next game ' + f'{min(upcoming):%I:%M %p}' if upcoming else 'no more games today'}")
    return 10


def mark():
    """Called after a full update: remember when it ran (and that last night was graded)."""
    now = pd.Timestamp.now(tz="America/New_York")
    state = json.load(open(STATE)) if os.path.exists(STATE) else {}
    state["last_full"] = now.isoformat()
    if now.hour >= GRADE_AFTER_HOUR:
        state["graded_for"] = (now - pd.Timedelta(days=1)).date().isoformat()
    json.dump(state, open(STATE, "w"))


def soon(minutes):
    """Exit 0 when a game tips within `minutes` (nba.yml then starts a markets run so the NBA card re-picks from the
    fresh lineups right away and can lock; owner 2026-10-28: lock only after lineups and injuries are confirmed)."""
    now = pd.Timestamp.now(tz="UTC")
    try:
        ts = [pd.Timestamp(e["date"]) for e in games_on(pd.Timestamp.now(tz="America/New_York").date())
              if ((e.get("status") or {}).get("type") or {}).get("state") == "pre"]
    except Exception:                                                 # noqa: BLE001
        return 10
    return 0 if any(0 < (t - now).total_seconds() / 60 <= minutes for t in ts) else 10


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "--soon":
        sys.exit(soon(float(sys.argv[2])))
    if len(sys.argv) > 1 and sys.argv[1] == "--mark":
        mark()
        sys.exit(0)
    sys.exit(main())
