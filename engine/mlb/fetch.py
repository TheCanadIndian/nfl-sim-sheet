#!/usr/bin/env python3
"""
MLB data from the official stats API (statsapi.mlb.com, public): every plate appearance with batter,
pitcher, handedness, result and batted-ball data, plus park, weather and lineup slot.

    python mlb/fetch.py --seasons 2024 2025 2026     # regular season + postseason, finished games only

mlb/mlb.db tables:
  games    one row per game: date, season, type (R regular, F/D/L/W postseason), teams, venue + coordinates,
           weather (temp, wind, roof), score
  pa       one row per plate appearance: batter / pitcher (+ sides), batting slot, whether the pitcher
           started, event, home run flag, exit velocity / launch angle / distance / trajectory / spray (hx, hy)
  pitch    one row per pitch: type, velocity, movement, location (px, pz and the batter's zone top/bottom),
           count, call code, swing / in-play flags, exit velo / launch angle if put in play
  players  id, name, bats, throws
Games already stored are skipped, so reruns only fetch new finals.
"""

import argparse
import datetime as dt
import os
import re
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "mlb.db")
API = "https://statsapi.mlb.com/api"
H = {"User-Agent": "Mozilla/5.0 (personal sports projections)", "Accept-Encoding": "gzip"}
TYPES = "R,F,D,L,W"           # regular season, wild card, division series, LCS, World Series

SCHEMA = """
CREATE TABLE IF NOT EXISTS games (game_pk INTEGER PRIMARY KEY, date TEXT, season INTEGER, type TEXT, home TEXT, away TEXT,
  home_id INTEGER, away_id INTEGER, venue_id INTEGER, venue TEXT, lat REAL, lon REAL, temp REAL, wind_mph REAL, wind_dir TEXT,
  condition TEXT, home_score INTEGER, away_score INTEGER);
CREATE TABLE IF NOT EXISTS pa (game_pk INTEGER, idx INTEGER, inning INTEGER, top INTEGER, bat_team TEXT, fld_team TEXT,
  batter INTEGER, bat_side TEXT, pitcher INTEGER, pitch_hand TEXT, slot INTEGER, sp INTEGER, event TEXT, hr INTEGER,
  ev REAL, la REAL, dist REAL, traj TEXT, hx REAL, hy REAL, PRIMARY KEY (game_pk, idx));
CREATE TABLE IF NOT EXISTS pitch (game_pk INTEGER, idx INTEGER, n INTEGER, batter INTEGER, pitcher INTEGER, bat_side TEXT,
  pitch_hand TEXT, ptype TEXT, speed REAL, pfx_x REAL, pfx_z REAL, px REAL, pz REAL, sz_top REAL, sz_bot REAL, zone INTEGER,
  balls INTEGER, strikes INTEGER, code TEXT, swing INTEGER, inplay INTEGER, ev REAL, la REAL, PRIMARY KEY (game_pk, idx, n));
CREATE TABLE IF NOT EXISTS players (id INTEGER PRIMARY KEY, name TEXT, bats TEXT, throws TEXT, pos TEXT);
"""


def get(url, **params):
    for _ in range(4):
        try:
            r = requests.get(url, params=params, headers=H, timeout=40)
            if r.ok:
                return r.json()
        except requests.RequestException:
            pass
    return None


def schedule(season):
    d = get(f"{API}/v1/schedule", sportId=1, season=season, gameType=TYPES)
    out = []
    for day in (d or {}).get("dates", []):
        for g in day["games"]:
            if g["status"]["abstractGameState"] == "Final" and g["status"].get("detailedState") != "Postponed":
                out.append(g["gamePk"])
    return sorted(set(out))


def parse(feed):
    gd, ld = feed["gameData"], feed["liveData"]
    w = gd.get("weather") or {}
    m = re.match(r"(\d+)\s*mph,?\s*(.*)", w.get("wind") or "")
    loc = (gd.get("venue", {}).get("location") or {}).get("defaultCoordinates") or {}
    home, away = gd["teams"]["home"], gd["teams"]["away"]
    ls = ld.get("linescore", {}).get("teams", {})
    game = dict(game_pk=gd["game"]["pk"], date=gd["datetime"]["officialDate"], season=int(gd["game"]["season"]), type=gd["game"]["type"],
                home=home["abbreviation"], away=away["abbreviation"], home_id=home["id"], away_id=away["id"],
                venue_id=gd["venue"]["id"], venue=gd["venue"]["name"], lat=loc.get("latitude"), lon=loc.get("longitude"),
                temp=float(w["temp"]) if str(w.get("temp", "")).isdigit() else None,
                wind_mph=float(m.group(1)) if m else None, wind_dir=m.group(2).strip() if m else None, condition=w.get("condition"),
                home_score=ls.get("home", {}).get("runs"), away_score=ls.get("away", {}).get("runs"))
    box = ld["boxscore"]["teams"]
    slot, starter = {}, {}
    for side in ("home", "away"):
        for k, p in box[side]["players"].items():
            bo = p.get("battingOrder")
            if bo:
                slot[p["person"]["id"]] = int(bo) // 100           # 100 -> 1st, 101 -> sub in 1st slot
        if box[side].get("pitchers"):
            starter[side] = box[side]["pitchers"][0]
    rows, pitches = [], []
    SWING = {"S", "W", "F", "T", "L", "M", "O", "X", "D", "E", "Q", "R"}      # swinging strike / foul / in play codes
    for i, p in enumerate(ld["plays"]["allPlays"]):
        res, mu, ab = p.get("result", {}), p["matchup"], p["about"]
        if res.get("type") != "atBat" or not res.get("eventType"):
            continue
        top = ab["isTopInning"]
        hit = next((e["hitData"] for e in reversed(p.get("playEvents", [])) if e.get("hitData")), {}) or {}
        b, pi = mu["batter"]["id"], mu["pitcher"]["id"]
        rows.append(dict(game_pk=game["game_pk"], idx=i, inning=ab["inning"], top=int(top),
                         bat_team=game["away"] if top else game["home"], fld_team=game["home"] if top else game["away"],
                         batter=b, bat_side=mu.get("batSide", {}).get("code"), pitcher=pi, pitch_hand=mu.get("pitchHand", {}).get("code"),
                         slot=slot.get(b), sp=int(pi == starter.get("home" if top else "away")), event=res["eventType"],
                         hr=int(res["eventType"] == "home_run"), ev=hit.get("launchSpeed"), la=hit.get("launchAngle"),
                         dist=hit.get("totalDistance"), traj=hit.get("trajectory"),
                         hx=(hit.get("coordinates") or {}).get("coordX"), hy=(hit.get("coordinates") or {}).get("coordY")))
        balls = strikes = 0
        for e in p.get("playEvents", []):
            if not e.get("isPitch"):
                continue
            det, pd_ = e.get("details", {}), e.get("pitchData", {}) or {}
            co, code = pd_.get("coordinates", {}) or {}, (det.get("code") or "").lstrip("*")
            h = e.get("hitData") or {}
            pitches.append(dict(game_pk=game["game_pk"], idx=i, n=e.get("pitchNumber"), batter=b, pitcher=pi,
                                bat_side=rows[-1]["bat_side"], pitch_hand=rows[-1]["pitch_hand"], ptype=(det.get("type") or {}).get("code"),
                                speed=pd_.get("startSpeed"), pfx_x=co.get("pfxX"), pfx_z=co.get("pfxZ"), px=co.get("pX"), pz=co.get("pZ"),
                                sz_top=pd_.get("strikeZoneTop"), sz_bot=pd_.get("strikeZoneBottom"), zone=pd_.get("zone"),
                                balls=balls, strikes=strikes, code=code, swing=int(code in SWING), inplay=int(bool(det.get("isInPlay"))),
                                ev=h.get("launchSpeed"), la=h.get("launchAngle")))
            c = e.get("count") or {}
            balls, strikes = c.get("balls", balls), c.get("strikes", strikes)
    players = [dict(id=v["id"], name=v.get("fullName"), bats=(v.get("batSide") or {}).get("code"), throws=(v.get("pitchHand") or {}).get("code"),
                    pos=(v.get("primaryPosition") or {}).get("abbreviation")) for v in gd.get("players", {}).values()]
    return game, rows, players, pitches


def connect():
    con = sqlite3.connect(DB)
    con.executescript(SCHEMA)
    return con


def store(con, game, rows, players, pitches=()):
    con.execute(f"INSERT OR REPLACE INTO games ({','.join(game)}) VALUES ({','.join('?' * len(game))})", list(game.values()))
    if rows:
        cols = list(rows[0])
        con.executemany(f"INSERT OR REPLACE INTO pa ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", [list(r.values()) for r in rows])
    if pitches:
        cols = list(pitches[0])
        con.executemany(f"INSERT OR REPLACE INTO pitch ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", [list(r.values()) for r in pitches])
    con.executemany("INSERT OR REPLACE INTO players (id, name, bats, throws, pos) VALUES (?,?,?,?,?)",
                    [(p["id"], p["name"], p["bats"], p["throws"], p["pos"]) for p in players])


def main(seasons, workers=8):
    con = connect()
    have = {r[0] for r in con.execute("SELECT game_pk FROM games")}
    todo = []
    for s in seasons:
        pks = [pk for pk in schedule(s) if pk not in have]
        print(f"{s}: {len(pks)} new final games", flush=True)
        todo += pks
    done = 0
    with ThreadPoolExecutor(workers) as ex:
        for feed in ex.map(lambda pk: get(f"{API}/v1.1/game/{pk}/feed/live"), todo):
            if feed:
                try:
                    store(con, *parse(feed))
                except (KeyError, TypeError) as e:
                    print("  skip:", type(e).__name__, e)
            done += 1
            if done % 250 == 0:
                con.commit()
                print(f"  {done}/{len(todo)}", flush=True)
    con.commit()
    n = con.execute("SELECT COUNT(*), SUM(hr) FROM pa").fetchone()
    print(f"mlb.db: {con.execute('SELECT COUNT(*) FROM games').fetchone()[0]} games, {n[0]:,} plate appearances, {n[1]:,} home runs")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--seasons", nargs="+", type=int, default=[dt.date.today().year])
    a = ap.parse_args()
    main(a.seasons)
