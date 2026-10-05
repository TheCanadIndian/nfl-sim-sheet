#!/usr/bin/env python3
"""
Quick check for the frequent NHL task: should a full update run now?
Exit 0 = run, 10 = skip. Prints one line with the reason. Uses one small NHL request.

Runs a full update when:
  - a game starts within the next SOON minutes and hasn't started (picks up start-time
    changes and the official dressed roster, posted ~30 min before puck drop)
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
SOON = 75                 # minutes before puck drop
REFRESH_HOURS = 4
GRADE_AFTER_HOUR = 10


def main():
    now = pd.Timestamp.now(tz="America/New_York")
    state = json.load(open(STATE)) if os.path.exists(STATE) else {}
    last = pd.Timestamp(state["last_full"]) if "last_full" in state else None
    try:
        r = requests.get(f"https://api-web.nhle.com/v1/schedule/{now.date().isoformat()}", timeout=30,
                         headers={"User-Agent": "Mozilla/5.0 (personal NHL projections)"})
        week = r.json().get("gameWeek", [])
    except Exception as e:
        print(f"run: schedule check failed ({type(e).__name__}); running to be safe")
        return 0

    def games_on(day):
        return [g for d in week if d["date"] == day.isoformat() for g in d["games"] if g.get("gameType") in (2, 3)]

    today = games_on(now.date())
    upcoming = [pd.Timestamp(g["startTimeUTC"]).tz_convert("America/New_York") for g in today
                if g.get("gameState") in ("FUT", "PRE")]
    upcoming = [t for t in upcoming if t > now]
    if upcoming:
        nxt = min(upcoming)
        mins = (nxt - now).total_seconds() / 60
        if mins <= SOON:
            print(f"run: game at {nxt:%I:%M %p} starts in {mins:.0f} min")
            return 0
        if last is None or (now - last).total_seconds() > REFRESH_HOURS * 3600:
            print(f"run: next game {nxt:%I:%M %p}; last update {'never' if last is None else f'{last:%I:%M %p}'}")
            return 0
    graded = state.get("graded_for")
    yesterday = (now - pd.Timedelta(days=1)).date().isoformat()
    if now.hour >= GRADE_AFTER_HOUR and graded != yesterday:
        # Grade last night once; the schedule call above only covers this week, which includes yesterday
        # except on the first day of a week, where running once is harmless.
        print(f"run: grade games from {yesterday}")
        return 0
    print(f"skip: {'next game ' + f'{min(upcoming):%I:%M %p}' if upcoming else 'no more games today'}")
    return 10


def mark(graded=False):
    """Called after a full update: remember when it ran (and that last night was graded)."""
    now = pd.Timestamp.now(tz="America/New_York")
    state = json.load(open(STATE)) if os.path.exists(STATE) else {}
    state["last_full"] = now.isoformat()
    if graded or now.hour >= GRADE_AFTER_HOUR:
        state["graded_for"] = (now - pd.Timedelta(days=1)).date().isoformat()
    json.dump(state, open(STATE, "w"))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--mark":
        mark()
        sys.exit(0)
    sys.exit(main())
