"""Print a short brief of the upcoming week for the scheduled news check."""

import os
import sqlite3
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from boxscore import data as D  # noqa: E402
from project import kickoff, pick_week  # noqa: E402

con = D.connect("nfl.db")
games = D.load_games(con)
season, week, todo = pick_week(games, None, None)
print(f"NOW: {pd.Timestamp.now():%a %Y-%m-%d %H:%M} ET")
print(f"TARGET: {season} week {week}  (overrides file: overrides/{season}_wk{week:02d}.csv)\n")

pos = D.positions(con).set_index("player_id").full_name
print("GAMES NOT YET KICKED OFF (named starting QBs from the schedule feed):")
todo = todo.assign(_k=kickoff(todo)).sort_values("_k")
for g, k in zip(todo.itertuples(), todo._k):
    aq, hq = pos.get(g.away_qb_id, g.away_qb_name), pos.get(g.home_qb_id, g.home_qb_name)
    print(f"  {k:%a %H:%M}  {g.away_team} ({aq}) @ {g.home_team} ({hq})")

teams = set(todo.home_team) | set(todo.away_team)
inj = pd.read_sql(f"""SELECT team, full_name, position, report_status FROM injuries
                      WHERE season={season} AND week={week} AND report_status IS NOT NULL
                        AND position IN ('QB','RB','WR','TE')""", sqlite3.connect("nfl.db"))
inj = inj[inj.team.isin(teams)].sort_values(["report_status", "team"])
print("\nOFFICIAL INJURY REPORT (skill positions):")
for r in inj.itertuples():
    print(f"  {r.report_status:12} {r.team:4} {r.position:3} {r.full_name}")

path = f"overrides/{season}_wk{week:02d}.csv"
print(f"\nCURRENT OVERRIDES ({path}):")
print(open(path).read() if os.path.exists(path) else "  (none yet)")
