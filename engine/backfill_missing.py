#!/usr/bin/env python3
"""
Add finished games that never got an archived projection (e.g. a game whose
play-by-play posted after the last run, or weeks before archiving existed) to
projections/weeks as reconstructed pregame projections. Games with a live
pregame projection are never touched.

    python backfill_missing.py            # current season
    python backfill_missing.py --season 2025
"""

import argparse
import json
import os
import subprocess
import sys

from boxscore import data as D

ap = argparse.ArgumentParser()
ap.add_argument("--season", type=int)
ap.add_argument("--blind", action="store_true", help="the market-blind archive")
args = ap.parse_args()

con = D.connect()
games = D.load_games(con)
season = args.season or int(games[games.result.isna()].season.min())
have_pbp = set(r[0] for r in con.execute("SELECT DISTINCT game_id FROM plays WHERE season=?", (season,)))
done = games[(games.season == season) & games.result.notna() & games.game_id.isin(have_pbp)]

for week, g in done.groupby("week"):
    base = os.path.join("projections", "blind", "weeks") if args.blind else os.path.join("projections", "weeks")
    meta_path = os.path.join(base, f"{season}_wk{week:02d}_meta.json")
    archived = set(json.load(open(meta_path))) if os.path.exists(meta_path) else set()
    missing = sorted(set(g.game_id) - archived)
    if not missing:
        continue
    print(f"{season} week {week}: backfilling {len(missing)} game(s): {', '.join(missing)}")
    cmd = [sys.executable, "project.py", "--season", str(season), "--week", str(week),
           "--played", "--archive", "--sims", "5000", "--games", *missing] + (["--blind"] if args.blind else [])
    subprocess.run(cmd, check=False, stdout=subprocess.DEVNULL)
