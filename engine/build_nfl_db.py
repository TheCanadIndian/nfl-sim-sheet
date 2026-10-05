#!/usr/bin/env python3
"""
build_nfl_db.py — build a local SQLite database of NFL play-by-play, scheme
charting, snap counts, and per-team weekly aggregates from nflverse.

WHY THIS EXISTS
    Play-by-play and personnel/scheme data are not available from any page
    that can be scraped reliably. nflverse publishes them as parquet releases,
    free, updated within hours of each game.

USAGE
    pip install pandas pyarrow requests
    python build_nfl_db.py --seasons 2024 2025 2026 --db nfl.db

    Re-run it every Tuesday. It replaces each season's rows, so re-running
    is safe and idempotent.

VERIFY BEFORE TRUSTING
    The release URLs below are written from memory and nflverse occasionally
    moves things. The script reports exactly which datasets loaded and which
    failed rather than failing silently. If a dataset 404s, check
    https://github.com/nflverse/nflverse-data/releases for the current path
    and update BASE/FILES.
"""

import argparse
import sqlite3
import sys
import io
import warnings

try:
    import pandas as pd
    import requests
except ImportError:
    sys.exit("Missing deps. Run: pip install pandas pyarrow requests")

warnings.filterwarnings("ignore")

BASE = "https://github.com/nflverse/nflverse-data/releases/download"

# dataset -> (release_tag, filename_template, table_name, season_scoped)
FILES = {
    "pbp":         ("pbp",          "play_by_play_{s}.parquet",   "plays",        True),
    "player_week": ("player_stats", "player_stats_{s}.parquet",   "player_week",  True),
    "roster":      ("rosters",      "roster_{s}.parquet",         "rosters",      True),
    "snaps":       ("snap_counts",  "snap_counts_{s}.parquet",    "snaps",        True),
    "ftn":         ("ftn_charting", "ftn_charting_{s}.parquet",   "charting",     True),
    "ngs_pass":    ("nextgen_stats","ngs_{s}_passing.parquet",    "ngs_passing",  True),
    "ngs_rush":    ("nextgen_stats","ngs_{s}_rushing.parquet",    "ngs_rushing",  True),
    "ngs_rec":     ("nextgen_stats","ngs_{s}_receiving.parquet",  "ngs_receiving",True),
    "injuries":    ("injuries",     "injuries_{s}.parquet",       "injuries",     True),
    "depth":       ("depth_charts", "depth_charts_{s}.parquet",   "depth_charts", True),
}
DEPTH_POS = ("QB", "RB", "WR", "TE", "FB")
SCHEDULE_URL = "https://github.com/nflverse/nfldata/raw/master/data/games.csv"

# Keep PBP to the columns that actually drive a projection model. The full
# file is ~380 columns; this is the useful subset.
PBP_COLS = [
    "game_id","season","week","posteam","defteam","home_team","away_team",
    "game_date","game_seconds_remaining","qtr","down","ydstogo","yardline_100",
    "play_type","pass","rush","qb_dropback","qb_scramble","shotgun","no_huddle",
    "pass_length","pass_location","air_yards","yards_after_catch","run_location",
    "run_gap","yards_gained","touchdown","pass_touchdown","rush_touchdown",
    "interception","fumble_lost","sack","complete_pass","incomplete_pass",
    "penalty","first_down","third_down_converted","fourth_down_converted",
    "score_differential","posteam_score","defteam_score","wp","epa","wpa",
    "success","cpoe","passer_player_id","passer_player_name",
    "rusher_player_id","rusher_player_name","receiver_player_id",
    "receiver_player_name","drive","series_result","goal_to_go",
    # needed by the box-score model (boxscore/)
    "pass_attempt","rush_attempt","two_point_attempt","field_goal_result",
    "fumbled_1_player_id","fumbled_1_team","td_team","qb_kneel","qb_spike",
    "play_id",  # joins FTN charting (blitzers, box count) to plays
]


HEADERS = {"User-Agent": "nfl-model-builder/1.0 (python-requests)"}


def fetch_parquet(url):
    r = requests.get(url, headers=HEADERS, timeout=180, allow_redirects=True)
    r.raise_for_status()
    return pd.read_parquet(io.BytesIO(r.content))


def load_dataset(con, key, season, report):
    tag, tmpl, table, _ = FILES[key]
    url = f"{BASE}/{tag}/{tmpl.format(s=season)}"
    try:
        df = fetch_parquet(url)
    except Exception as e:
        report.append((f"{key} {season}", "FAILED", f"{type(e).__name__}: {str(e)[:90]}"))
        return

    if key == "pbp":
        keep = [c for c in PBP_COLS if c in df.columns]
        missing = [c for c in PBP_COLS if c not in df.columns]
        df = df[keep]
        if missing:
            report.append((f"pbp {season} cols", "NOTE",
                           f"{len(missing)} expected cols absent: {', '.join(missing[:6])}"))

    if key == "depth":
        # ~550k daily rows per season; the box-score model only needs skill positions.
        df = df[df["pos_abb"].isin(DEPTH_POS)]

    if "season" not in df.columns:
        df["season"] = season

    if table_exists(con, table):
        con.execute(f"DELETE FROM {table} WHERE season = ?", (season,))
        # Columns added to PBP_COLS (or upstream) since the table was created.
        have = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
        for col in df.columns:
            if col not in have:
                con.execute(f'ALTER TABLE {table} ADD COLUMN "{col}"')
    df.to_sql(table, con, if_exists="append", index=False)
    report.append((f"{key} {season}", "OK", f"{len(df):,} rows -> {table}"))


def table_exists(con, name):
    q = "SELECT name FROM sqlite_master WHERE type='table' AND name=?"
    return con.execute(q, (name,)).fetchone() is not None


def load_schedule(con, seasons, report):
    try:
        r = requests.get(SCHEDULE_URL, headers=HEADERS, timeout=120)
        r.raise_for_status()
        df = pd.read_csv(io.StringIO(r.text))
    except Exception as e:
        report.append(("schedule", "FAILED", f"{type(e).__name__}: {str(e)[:90]}"))
        return
    df = df[df["season"].isin(seasons)]
    if table_exists(con, "games"):
        con.execute("DELETE FROM games WHERE season IN (%s)" %
                    ",".join("?" * len(seasons)), seasons)
    df.to_sql("games", con, if_exists="append", index=False)
    report.append(("schedule", "OK", f"{len(df):,} games -> games"))


def build_team_week(con, report):
    """Per-team, per-game aggregates. This is the table the model reads."""
    if not table_exists(con, "plays"):
        report.append(("team_week", "SKIPPED", "no plays table"))
        return
    con.execute("DROP TABLE IF EXISTS team_week")
    con.execute("""
        CREATE TABLE team_week AS
        SELECT
            season, week, game_id, posteam AS team, defteam AS opponent,
            COUNT(*)                                         AS plays,
            SUM(qb_dropback)                                 AS dropbacks,
            SUM(rush)                                        AS rushes,
            ROUND(1.0*SUM(qb_dropback)/NULLIF(COUNT(*),0),4) AS pass_rate,
            SUM(sack)                                        AS sacks_taken,
            ROUND(1.0*SUM(sack)/NULLIF(SUM(qb_dropback),0),4)AS sack_rate,
            SUM(shotgun)                                     AS shotgun_plays,
            ROUND(1.0*SUM(shotgun)/NULLIF(COUNT(*),0),4)     AS shotgun_rate,
            SUM(no_huddle)                                   AS no_huddle_plays,
            ROUND(1.0*SUM(no_huddle)/NULLIF(COUNT(*),0),4)   AS no_huddle_rate,
            SUM(yards_gained)                                AS yards,
            ROUND(AVG(epa),4)                                AS epa_per_play,
            ROUND(AVG(success),4)                            AS success_rate,
            ROUND(AVG(CASE WHEN qb_dropback=1 THEN epa END),4) AS pass_epa,
            ROUND(AVG(CASE WHEN rush=1 THEN epa END),4)        AS rush_epa,
            ROUND(AVG(air_yards),2)                          AS avg_air_yards,
            SUM(touchdown)                                   AS touchdowns,
            SUM(CASE WHEN yardline_100<=20 THEN 1 ELSE 0 END) AS red_zone_plays,
            SUM(CASE WHEN yardline_100<=20 AND touchdown=1 THEN 1 ELSE 0 END) AS red_zone_tds
        FROM plays
        WHERE posteam IS NOT NULL AND play_type IN ('pass','run')
        GROUP BY season, week, game_id, posteam, defteam
    """)
    n = con.execute("SELECT COUNT(*) FROM team_week").fetchone()[0]
    report.append(("team_week", "OK", f"{n:,} team-games"))


def build_neutral_script(con, report):
    """Neutral-situation pass rate: 1st-3rd qtr, win prob 20-80%, not garbage time.
       This is the number to feed a projection model, not raw season pass rate."""
    if not table_exists(con, "plays"):
        return
    con.execute("DROP TABLE IF EXISTS team_neutral")
    con.execute("""
        CREATE TABLE team_neutral AS
        SELECT season, posteam AS team,
               COUNT(*) AS neutral_plays,
               ROUND(1.0*SUM(qb_dropback)/NULLIF(COUNT(*),0),4) AS neutral_pass_rate,
               ROUND(AVG(epa),4) AS neutral_epa
        FROM plays
        WHERE posteam IS NOT NULL
          AND play_type IN ('pass','run')
          AND qtr <= 3
          AND wp BETWEEN 0.20 AND 0.80
        GROUP BY season, posteam
    """)
    n = con.execute("SELECT COUNT(*) FROM team_neutral").fetchone()[0]
    report.append(("team_neutral", "OK", f"{n:,} team-seasons"))


def build_usage_shares(con, report):
    """Target share and carry share per player per game. THE layer that broke
       in Week 1 2026: a share is not a per-game rate."""
    if not table_exists(con, "plays"):
        return
    con.execute("DROP TABLE IF EXISTS player_shares")
    con.execute("""
        CREATE TABLE player_shares AS
        WITH tgt AS (
            SELECT season, week, posteam AS team,
                   receiver_player_id AS player_id,
                   receiver_player_name AS player,
                   COUNT(*) AS targets
            FROM plays
            WHERE receiver_player_id IS NOT NULL AND play_type='pass'
            GROUP BY season, week, posteam, receiver_player_id, receiver_player_name
        ),
        team_tgt AS (
            SELECT season, week, posteam AS team, COUNT(*) AS team_targets
            FROM plays WHERE receiver_player_id IS NOT NULL AND play_type='pass'
            GROUP BY season, week, posteam
        ),
        car AS (
            SELECT season, week, posteam AS team,
                   rusher_player_id AS player_id,
                   rusher_player_name AS player,
                   COUNT(*) AS carries
            FROM plays
            WHERE rusher_player_id IS NOT NULL AND play_type='run'
            GROUP BY season, week, posteam, rusher_player_id, rusher_player_name
        ),
        team_car AS (
            SELECT season, week, posteam AS team, COUNT(*) AS team_carries
            FROM plays WHERE rusher_player_id IS NOT NULL AND play_type='run'
            GROUP BY season, week, posteam
        ),
        keys AS (
            SELECT season, week, team, player_id, player FROM tgt
            UNION
            SELECT season, week, team, player_id, player FROM car
        )
        SELECT
            k.season, k.week, k.team, k.player_id, k.player,
            COALESCE(t.targets,0) AS targets,
            COALESCE(c.carries,0) AS carries,
            ROUND(1.0*COALESCE(t.targets,0)/NULLIF(tt.team_targets,0),4) AS target_share,
            ROUND(1.0*COALESCE(c.carries,0)/NULLIF(tc.team_carries,0),4) AS carry_share
        FROM keys k
        LEFT JOIN tgt t ON t.season=k.season AND t.week=k.week
             AND t.team=k.team AND t.player_id=k.player_id
        LEFT JOIN car c ON c.season=k.season AND c.week=k.week
             AND c.team=k.team AND c.player_id=k.player_id
        LEFT JOIN team_tgt tt ON tt.season=k.season AND tt.week=k.week AND tt.team=k.team
        LEFT JOIN team_car tc ON tc.season=k.season AND tc.week=k.week AND tc.team=k.team
    """)
    n = con.execute("SELECT COUNT(*) FROM player_shares").fetchone()[0]
    report.append(("player_shares", "OK", f"{n:,} player-games"))


def build_indexes(con):
    idx = [
        "CREATE INDEX IF NOT EXISTS ix_plays_game  ON plays(game_id)",
        "CREATE INDEX IF NOT EXISTS ix_plays_team  ON plays(season, week, posteam)",
        "CREATE INDEX IF NOT EXISTS ix_plays_def   ON plays(season, week, defteam)",
        "CREATE INDEX IF NOT EXISTS ix_tw_team     ON team_week(season, week, team)",
        "CREATE INDEX IF NOT EXISTS ix_shares_team ON player_shares(season, week, team)",
        "CREATE INDEX IF NOT EXISTS ix_snaps_game  ON snaps(game_id)",
    ]
    for s in idx:
        try:
            con.execute(s)
        except sqlite3.OperationalError:
            pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seasons", nargs="+", type=int, default=[2024, 2025, 2026])
    ap.add_argument("--db", default="nfl.db")
    ap.add_argument("--skip", nargs="*", default=[],
                    help="dataset keys to skip, e.g. --skip ngs_pass ngs_rush")
    args = ap.parse_args()

    con = sqlite3.connect(args.db)
    report = []

    print(f"Building {args.db} for seasons {args.seasons}\n")
    load_schedule(con, args.seasons, report)
    for season in args.seasons:
        for key in FILES:
            if key in args.skip:
                continue
            print(f"  fetching {key} {season} ...", flush=True)
            load_dataset(con, key, season, report)

    print("\nFetching game-time weather (Open-Meteo) ...")
    try:
        from boxscore import weather
        n = weather.update(con, args.seasons, verbose=False)
        report.append(("weather", "OK", f"{n} games updated"))
    except Exception as e:
        report.append(("weather", "FAILED", f"{type(e).__name__}: {str(e)[:90]}"))

    print("\nBuilding derived tables ...")
    build_team_week(con, report)
    build_neutral_script(con, report)
    build_usage_shares(con, report)
    build_indexes(con)
    con.commit()

    print("\n" + "=" * 74)
    print(f"{'DATASET':22}{'STATUS':10}DETAIL")
    print("=" * 74)
    ok = fail = 0
    for name, status, detail in report:
        print(f"{name:22}{status:10}{detail}")
        ok += status == "OK"
        fail += status == "FAILED"
    print("=" * 74)
    print(f"{ok} loaded, {fail} failed.")
    if fail:
        print("\nFailed datasets are usually a moved release path. Check")
        print("https://github.com/nflverse/nflverse-data/releases and update FILES.")

    print("\nTables in database:")
    for (t,) in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
        n = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        print(f"  {t:20} {n:>10,} rows")
    con.close()


if __name__ == "__main__":
    main()
