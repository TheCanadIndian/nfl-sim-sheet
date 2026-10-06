#!/usr/bin/env python3
"""
Statcast bat tracking from Baseball Savant (public CSV), one row per tracked swing, into mlb.db table `swing`:
bat speed, swing length, attack angle, attack direction, swing-path tilt, contact point vs the batter, with the
pitch's location and the batted-ball result.

    python mlb/savant.py --seasons 2025 2026      # days already stored are skipped
"""

import argparse
import io
import os
import sqlite3
import time

import pandas as pd
import requests

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "mlb.db")
URL = "https://baseballsavant.mlb.com/statcast_search/csv"
H = {"User-Agent": "Mozilla/5.0 (personal sports projections)"}
KEEP = {"game_pk": "game_pk", "game_date": "date", "batter": "batter", "pitcher": "pitcher", "stand": "stand", "p_throws": "p_throws",
        "pitch_type": "ptype", "plate_x": "px", "plate_z": "pz", "sz_top": "sz_top", "sz_bot": "sz_bot", "description": "description",
        "events": "events", "launch_speed": "ev", "launch_angle": "la", "bat_speed": "bat_speed", "swing_length": "swing_length",
        "attack_angle": "attack_angle", "attack_direction": "attack_direction", "swing_path_tilt": "tilt",
        "intercept_ball_minus_batter_pos_x_inches": "icpt_x", "intercept_ball_minus_batter_pos_y_inches": "icpt_y"}


def day(date):
    p = {"all": "true", "hfGT": "R|F|D|L|W|", "player_type": "batter", "game_date_gt": date, "game_date_lt": date, "type": "details"}
    for i in range(4):
        try:
            r = requests.get(URL, params=p, headers=H, timeout=180)
            if r.ok:
                d = pd.read_csv(io.StringIO(r.text), low_memory=False)
                if "bat_speed" not in d.columns:
                    return pd.DataFrame()
                d = d[d.bat_speed.notna()]
                return d[[c for c in KEEP if c in d.columns]].rename(columns=KEEP)
        except (requests.RequestException, pd.errors.ParserError):
            pass
        time.sleep(5 * (i + 1))
    return None


def main(seasons):
    con = sqlite3.connect(DB)
    con.execute("CREATE TABLE IF NOT EXISTS swing_days (date TEXT PRIMARY KEY, n INTEGER)")
    have = {r[0] for r in con.execute("SELECT date FROM swing_days")}
    dates = [r[0] for r in con.execute(f"SELECT DISTINCT date FROM games WHERE season IN ({','.join(map(str, seasons))}) ORDER BY date")]
    todo = [d for d in dates if d not in have]
    print(f"{len(todo)} days to fetch", flush=True)
    for i, d in enumerate(todo):
        x = day(d)
        if x is None:
            print("  failed", d, flush=True)
            continue
        if len(x):
            x.to_sql("swing", con, if_exists="append", index=False)
        con.execute("INSERT OR REPLACE INTO swing_days VALUES (?, ?)", (d, len(x)))
        con.commit()
        if i % 20 == 0:
            print(f"  {i}/{len(todo)} {d}: {len(x)} swings", flush=True)
        time.sleep(1.0)
    n = con.execute("SELECT COUNT(*) FROM swing").fetchone()[0]
    print(f"swing table: {n:,} tracked swings")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--seasons", nargs="+", type=int, default=[2026])
    main(ap.parse_args().seasons)
