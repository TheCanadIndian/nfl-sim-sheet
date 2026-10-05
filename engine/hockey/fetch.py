#!/usr/bin/env python3
"""
Download NHL data from the free public NHL API into hockey/nhl.db.

    python hockey/fetch.py --seasons 20222023 20232024 20242025 20252026
    python hockey/fetch.py --seasons 20262027          # current season (new games only)

Per game: play-by-play (every shot attempt with location, type, shooter, goalie and
on-ice situation) and per-player ice time split into even strength / power play /
shorthanded. Raw responses are cached under hockey/raw/ so nothing is fetched twice.
"""

import argparse
import datetime as dt
import gzip
import json
import os
import sqlite3
import sys
import time

import pandas as pd
import requests

HERE = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(HERE, "raw")
DB = os.path.join(HERE, "nhl.db")
WEB = "https://api-web.nhle.com/v1"
STATS = "https://api.nhle.com/stats/rest/en"
S = requests.Session()
S.headers["User-Agent"] = "Mozilla/5.0 (personal NHL projections)"


def get(url, tries=5, pause=0.25):
    for i in range(tries):
        try:
            r = S.get(url, timeout=40)
            if r.status_code == 404:
                return None
            r.raise_for_status()
            time.sleep(pause)
            return r.json()
        except Exception:
            time.sleep(2 + 3 * i)
    return None


def cached(path, url):
    if os.path.exists(path):
        try:
            with gzip.open(path, "rt", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, EOFError, ValueError):
            os.remove(path)                  # partial file from an interrupted run
    data = get(url)
    if data is not None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with gzip.open(tmp, "wt", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, path)
    return data


def _game_files(season, gid):
    return [(os.path.join(RAW, str(season), f"{gid}_pbp.json.gz"), f"{WEB}/gamecenter/{gid}/play-by-play"),
            (os.path.join(RAW, str(season), f"{gid}_toi.json.gz"),
             f"{STATS}/skater/timeonice?isAggregate=false&isGame=true&limit=100&start=0&cayenneExp=gameId={gid}")]


# ------------------------------------------------------------------ schedule

def season_games(season):
    """All regular-season and playoff games for a season (e.g. 20242025)."""
    y = int(str(season)[:4])
    day, end = dt.date(y, 9, 20), dt.date(y + 1, 6, 30)
    rows, seen = [], set()
    while day <= end:
        s = get(f"{WEB}/schedule/{day.isoformat()}")
        if s is None:
            day += dt.timedelta(days=7)
            continue
        for d in s.get("gameWeek", []):
            for g in d["games"]:
                if g["id"] in seen or g.get("gameType") not in (2, 3) or g.get("season") != int(season):
                    continue
                seen.add(g["id"])
                rows.append(dict(
                    game_id=g["id"], season=int(season), game_type=g["gameType"], date=d["date"],
                    start_utc=g.get("startTimeUTC"), away=g["awayTeam"]["abbrev"], home=g["homeTeam"]["abbrev"],
                    away_score=g["awayTeam"].get("score"), home_score=g["homeTeam"].get("score"),
                    state=g.get("gameState"), venue=(g.get("venue") or {}).get("default"),
                    neutral=int(bool(g.get("neutralSite")))))
        nxt = s.get("nextStartDate")
        day = dt.date.fromisoformat(nxt) if nxt else day + dt.timedelta(days=7)
    return pd.DataFrame(rows)


# ------------------------------------------------------------------ parsing

def _secs(t):
    m, s = t.split(":")
    return int(m) * 60 + int(s)


def parse_pbp(pbp):
    """Shot attempts + goals, with the shooter's strength situation and attacking net."""
    gid = pbp["id"]
    home_id, away_id = pbp["homeTeam"]["id"], pbp["awayTeam"]["id"]
    abbr = {home_id: pbp["homeTeam"]["abbrev"], away_id: pbp["awayTeam"]["abbrev"]}
    rows, prev = [], None
    for p in pbp.get("plays", []):
        per = p["periodDescriptor"]["number"]
        ptype = p["periodDescriptor"].get("periodType")
        t = _secs(p["timeInPeriod"]) + (per - 1) * 1200
        kind = p["typeDescKey"]
        d = p.get("details", {}) or {}
        if kind in ("shot-on-goal", "goal", "missed-shot", "blocked-shot") and ptype != "SO":
            team = d.get("eventOwnerTeamId")
            if kind == "blocked-shot":        # owner is the blocking team; shooter is the other one
                team = home_id if team == away_id else away_id
            is_home = team == home_id
            sc = p.get("situationCode") or "1551"
            a_g, a_s, h_s, h_g = int(sc[0]), int(sc[1]), int(sc[2]), int(sc[3])
            own_sk, opp_sk = (h_s, a_s) if is_home else (a_s, h_s)
            opp_goalie_in = (a_g if is_home else h_g) == 1
            side = p.get("homeTeamDefendingSide")
            x, y = d.get("xCoord"), d.get("yCoord")
            if x is not None and side:
                net_x = 89 if (is_home and side == "left") or (not is_home and side == "right") else -89
                dist = ((net_x - x) ** 2 + (y or 0) ** 2) ** 0.5
                angle = abs(__import__("math").degrees(__import__("math").atan2(abs(y or 0), abs(net_x - x) or 0.1)))
            else:
                dist = angle = None
            rebound = prev is not None and prev["team"] == team and prev["kind"] in (
                "shot-on-goal", "missed-shot", "blocked-shot") and 0 <= t - prev["t"] <= 3
            rush = prev is not None and prev["kind"] in ("takeaway", "giveaway", "faceoff", "hit") \
                and prev.get("zone") in ("N", "D") and 0 <= t - prev["t"] <= 4
            rows.append(dict(
                game_id=gid, event_id=p["eventId"], period=per, t=t, team=abbr.get(team), is_home=int(is_home),
                kind=kind, goal=int(kind == "goal"),
                shooter=d.get("scoringPlayerId") if kind == "goal" else d.get("shootingPlayerId"),
                assist1=d.get("assist1PlayerId"), assist2=d.get("assist2PlayerId"), goalie=d.get("goalieInNetId"),
                shot_type=d.get("shotType"), x=x, y=y, dist=dist, angle=angle,
                own_sk=own_sk, opp_sk=opp_sk, empty_net=int(not opp_goalie_in),
                pp=int(own_sk > opp_sk), sh=int(own_sk < opp_sk), rebound=int(rebound), rush=int(rush)))
        prev = dict(t=t, kind=kind, team=d.get("eventOwnerTeamId") if kind != "blocked-shot" else None,
                    zone=d.get("zoneCode"))
        if kind in ("shot-on-goal", "missed-shot") and d.get("eventOwnerTeamId"):
            prev["team"] = d["eventOwnerTeamId"]
    roster = [dict(game_id=gid, player_id=r["playerId"], team=abbr.get(r["teamId"]), pos=r["positionCode"],
                   name=f"{r['firstName']['default']} {r['lastName']['default']}") for r in pbp.get("rosterSpots", [])]
    return rows, roster


def parse_toi(toi, gid):
    return [dict(game_id=gid, player_id=r["playerId"], team=r["teamAbbrev"], pos=r["positionCode"],
                 toi=r["timeOnIce"], ev_toi=r["evTimeOnIce"], pp_toi=r["ppTimeOnIce"], sh_toi=r["shTimeOnIce"],
                 name=r["skaterFullName"]) for r in toi.get("data", [])]


# ------------------------------------------------------------------ main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seasons", nargs="+", required=True)
    a = ap.parse_args()
    con = sqlite3.connect(DB)
    for season in a.seasons:
        games = season_games(season)
        print(f"{season}: {len(games)} games on the schedule", flush=True)
        if games.empty:
            continue
        con.execute("DELETE FROM games WHERE season=?", (int(season),)) if _has(con, "games") else None
        games.to_sql("games", con, if_exists="append", index=False)
        done = games[games.state.isin(["OFF", "FINAL"])]
        have = set()
        if _has(con, "shots"):
            have = set(pd.read_sql(f"SELECT DISTINCT game_id FROM toi WHERE game_id IN ({','.join(map(str, done.game_id)) or 0})", con).game_id)
        todo = done[~done.game_id.isin(have)]
        # Download in parallel (4 at a time) into the cache, then parse from disk.
        from concurrent.futures import ThreadPoolExecutor
        jobs = [f for gid in todo.game_id for f in _game_files(season, gid) if not os.path.exists(f[0])]
        with ThreadPoolExecutor(max_workers=4) as ex:
            for i, _ in enumerate(ex.map(lambda f: cached(*f), jobs), 1):
                if i % 400 == 0:
                    print(f"  {season}: downloaded {i}/{len(jobs)} files", flush=True)
        shots, rosters, tois = [], [], []
        for i, gid in enumerate(todo.game_id, 1):
            (pp, pu), (tp, tu) = _game_files(season, gid)
            pbp, toi = cached(pp, pu), cached(tp, tu)
            if pbp is None or toi is None:
                continue
            if not toi.get("data"):
                # Ice-time stats not posted yet: don't keep the empty file, and don't load the
                # game's shots without them (that used to re-add the shots on every run).
                os.remove(tp)
                continue
            s, r = parse_pbp(pbp)
            shots += s
            rosters += r
            tois += parse_toi(toi, gid)
            if i % 100 == 0 or i == len(todo):
                _flush(con, shots, rosters, tois)
                shots, rosters, tois = [], [], []
                print(f"  {season}: {i}/{len(todo)} games", flush=True)
    con.close()


def _has(con, t):
    return con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (t,)).fetchone() is not None


def _flush(con, shots, rosters, tois):
    if not tois:
        return
    if _has(con, "shots") and "assist2" not in [r[1] for r in con.execute("PRAGMA table_info(shots)")]:
        con.execute("ALTER TABLE shots ADD COLUMN assist2 INTEGER")   # older databases; see backfill_assists()
    ids = sorted({t["game_id"] for t in tois} | {x["game_id"] for x in shots})
    for t in ("shots", "rosters", "toi"):
        if _has(con, t):
            con.execute(f"DELETE FROM {t} WHERE game_id IN ({','.join(map(str, ids))})")
    pd.DataFrame(shots).to_sql("shots", con, if_exists="append", index=False)
    pd.DataFrame(rosters).to_sql("rosters", con, if_exists="append", index=False)
    pd.DataFrame(tois).to_sql("toi", con, if_exists="append", index=False)
    con.commit()


def backfill_assists():
    """Fill shots.assist2 from the cached play-by-play files (added after the first download)."""
    import glob
    import gzip
    con = sqlite3.connect(DB)
    if "assist2" not in [r[1] for r in con.execute("PRAGMA table_info(shots)")]:
        con.execute("ALTER TABLE shots ADD COLUMN assist2 INTEGER")
    con.execute("CREATE INDEX IF NOT EXISTS shots_game_event ON shots(game_id, event_id)")
    rows = []
    for f in glob.glob(os.path.join(RAW, "*", "*_pbp.json.gz")):
        pbp = json.load(gzip.open(f))
        rows += [(p["details"].get("assist2PlayerId"), pbp["id"], p["eventId"]) for p in pbp.get("plays", [])
                 if p["typeDescKey"] == "goal" and (p.get("details") or {}).get("assist2PlayerId")]
    con.executemany("UPDATE shots SET assist2=? WHERE game_id=? AND event_id=?", rows)
    con.commit()
    print(f"assist2 filled on {len(rows)} goals")


if __name__ == "__main__":
    if sys.argv[1:] == ["--backfill-assists"]:
        backfill_assists()
    else:
        main()
