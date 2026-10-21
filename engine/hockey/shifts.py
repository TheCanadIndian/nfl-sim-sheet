#!/usr/bin/env python3
"""
NHL shifts (who was on the ice when) for every game in hockey/nhl.db -> hockey/shifts.db, then each shot attempt in
nhl.db's `shots` gets its on-ice skaters (`shots_on`: game_id, event_id, team_on, opp_on as comma lists). Phase 1 of the
NHL play-level simulator (owner 2026-10-21: "done for hockey ... really focusing on ATGs and shots").
PC only (large; *.db is gitignored). Source: api.nhle.com/stats/rest/en/shiftcharts (public, no key).

    python hockey/shifts.py pull      # every finished game not yet pulled (4 at a time, resumable)
    python hockey/shifts.py onice     # attach on-ice skaters to every shot attempt
"""
import os
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "nhl.db")
SH = os.path.join(HERE, "shifts.db")
URL = "https://api.nhle.com/stats/rest/en/shiftcharts?cayenneExp=gameId={}"


def mmss(s):
    try:
        m, x = str(s).split(":")
        return int(m) * 60 + int(x)
    except ValueError:
        return None


def fetch(gid):
    for i in range(4):
        try:
            r = requests.get(URL.format(gid), timeout=30, headers={"User-Agent": "Mozilla/5.0"})
            if r.ok:
                return gid, r.json().get("data", [])
        except (requests.RequestException, ValueError):
            pass
        time.sleep(2 * (i + 1))
    return gid, None


def pull():
    con = sqlite3.connect(SH)
    con.execute("CREATE TABLE IF NOT EXISTS shifts (game_id, player_id, team, period, start, end, type)")
    con.execute("CREATE TABLE IF NOT EXISTS pulled (game_id PRIMARY KEY, n)")
    have = {r[0] for r in con.execute("SELECT game_id FROM pulled")}
    games = [r[0] for r in sqlite3.connect(DB).execute("SELECT game_id FROM games WHERE state IN ('OFF', 'FINAL') ORDER BY date")]
    todo = [g for g in games if g not in have]
    print(f"{len(todo)} games to pull", flush=True)
    done = 0
    with ThreadPoolExecutor(4) as ex:
        for gid, data in ex.map(fetch, todo):
            if data is None:
                continue
            rows = [(gid, d["playerId"], d.get("teamAbbrev"), d.get("period"), mmss(d.get("startTime")), mmss(d.get("endTime")), d.get("typeCode"))
                    for d in data if d.get("typeCode") == 517]               # 517 = a shift (505 = goal events)
            con.executemany("INSERT INTO shifts VALUES (?,?,?,?,?,?,?)", rows)
            con.execute("INSERT OR REPLACE INTO pulled VALUES (?,?)", (gid, len(rows)))
            done += 1
            if done % 250 == 0:
                con.commit(); print(f"  {done}/{len(todo)}", flush=True)
    con.execute("CREATE INDEX IF NOT EXISTS sh_game ON shifts(game_id)")
    con.commit()
    print(f"shifts: {done} games pulled; {con.execute('SELECT COUNT(*) FROM shifts').fetchone()[0]:,} shifts")


def onice():
    import numpy as np
    import pandas as pd
    con = sqlite3.connect(SH)
    con.execute(f"ATTACH DATABASE '{DB}' AS nhl")
    con.execute("DROP TABLE IF EXISTS shots_on")
    con.execute("CREATE TABLE shots_on (game_id, event_id, team_on, opp_on, goalie_on, n_team, n_opp)")
    pos = dict(con.execute("SELECT player_id, pos FROM nhl.rosters GROUP BY player_id"))
    SHF = pd.read_sql("SELECT game_id, player_id, team, period, start, end FROM shifts", con)
    SHT = pd.read_sql("SELECT game_id, event_id, period, t, team, kind FROM nhl.shots", con)
    sg, tg = SHF.groupby("game_id"), SHT.groupby("game_id")
    n, out = 0, []
    for gid, X in tg:
        if gid not in sg.groups:
            continue
        S = sg.get_group(gid)
        per, st, en = S.period.to_numpy(), S.start.to_numpy(float), S.end.to_numpy(float)
        team, pid = S.team.to_numpy(), S.player_id.to_numpy()
        isg = np.array([pos.get(p) == "G" for p in pid])
        xp, xt, xtm = X.period.to_numpy(), X.t.to_numpy(float), X.team.to_numpy()

        ts = np.where(xt < 1200, xt, xt - 1200 * (xp - 1))
        on = (per[None, :] == xp[:, None]) & (st[None, :] < ts[:, None]) & (en[None, :] >= ts[:, None])
        for k, ev in enumerate(X.event_id.to_numpy()):
            m = on[k]
            mine = m & (team == xtm[k]); oth = m & (team != xtm[k])
            a = pid[mine & ~isg]; b = pid[oth & ~isg]; g = pid[oth & isg]
            out.append((int(gid), int(ev), ",".join(map(str, a)), ",".join(map(str, b)), ",".join(map(str, g)), int(len(a)), int(len(b))))
        n += 1
        if n % 1000 == 0:
            con.executemany("INSERT INTO shots_on VALUES (?,?,?,?,?,?,?)", out); out = []; con.commit(); print(f"  {n} games", flush=True)
    con.executemany("INSERT INTO shots_on VALUES (?,?,?,?,?,?,?)", out)
    con.execute("CREATE INDEX IF NOT EXISTS so_game ON shots_on(game_id, event_id)")
    con.commit()
    print(f"on-ice skaters attached for {n} games")


if __name__ == "__main__":
    {"pull": pull, "onice": onice}[sys.argv[1] if len(sys.argv) > 1 else "pull"]()
