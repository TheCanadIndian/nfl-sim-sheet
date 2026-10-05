#!/usr/bin/env python3
"""
NBA data from ESPN's public API into nba/nba.db (raw responses cached in nba/raw).

    python nba/fetch.py --seasons 2023 2024 2025 2026     # season = year it ends (2026 = 2025-26)
    python nba/fetch.py --seasons 2027                    # current season: new finals + schedule

Tables
  games    game_id, season, date, start_utc, home, away, home_score, away_score, state,
           game_type (2 regular, 3 playoffs, 1 preseason), spread (home, negative = favored),
           total, line_source
  players  game_id, team, player_id, name, pos, starter, dnp, min, pts, fgm, fga, fg3m, fg3a,
           ftm, fta, reb, oreb, dreb, ast, stl, blk, tov, pf, plus_minus
"""

import argparse
import datetime as dt
import gzip
import json
import os
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import requests

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "nba.db")
RAW = os.path.join(HERE, "raw")
API = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"
H = {"User-Agent": "Mozilla/5.0 (personal NBA projections)"}


def get(url, tries=4):
    for i in range(tries):
        try:
            r = requests.get(url, headers=H, timeout=30)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 404:
                return None
        except (requests.RequestException, ValueError):
            pass
        time.sleep(2 * (i + 1))
    return None


def cached(path, url, final=True):
    if final and os.path.exists(path):
        try:
            return json.load(gzip.open(path, "rt", encoding="utf-8"))
        except (OSError, ValueError, EOFError):
            os.remove(path)
    d = get(url)
    if d is not None and final:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with gzip.open(path + ".tmp", "wt", encoding="utf-8") as f:
            json.dump(d, f)
        os.replace(path + ".tmp", path)
    return d


def season_dates(season):
    """Season S (ends in S): Oct 1 of S-1 through Jun 30 of S."""
    d, end = dt.date(season - 1, 10, 1), min(dt.date(season, 6, 30), dt.date.today() + dt.timedelta(days=21))
    while d <= end:
        yield d
        d += dt.timedelta(days=1)


def schedule(season):
    rows = []
    for d in season_dates(season):
        sb = get(f"{API}/scoreboard?dates={d:%Y%m%d}&limit=50")
        for ev in (sb or {}).get("events", []):
            c = ev["competitions"][0]
            teams = {t["homeAway"]: t for t in c["competitors"]}
            if not all(teams.get(k, {}).get("team", {}).get("abbreviation") for k in ("home", "away")):
                continue                                   # All-Star / undecided playoff slots
            st = ev["status"]["type"]
            stype = ev.get("season", {}).get("type", 2)
            rows.append(dict(game_id=int(ev["id"]), season=season, date=d.isoformat(), start_utc=ev["date"],
                             home=teams["home"]["team"]["abbreviation"], away=teams["away"]["team"]["abbreviation"],
                             home_score=int(teams["home"].get("score") or 0), away_score=int(teams["away"].get("score") or 0),
                             state="FINAL" if st.get("completed") else st.get("name", ""), game_type=int(stype)))
    return pd.DataFrame(rows).drop_duplicates("game_id")


def _num(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return 0.0


def parse_summary(gid, sm):
    """Player box lines and the pregame line from one game summary."""
    out = []
    for team in sm.get("boxscore", {}).get("players", []):
        abbr = team["team"]["abbreviation"]
        st = team["statistics"][0]
        keys = st["keys"]
        for a in st["athletes"]:
            if not (a.get("athlete") or {}).get("id"):
                continue
            v = dict(zip(keys, a.get("stats") or []))
            fg = (v.get("fieldGoalsMade-fieldGoalsAttempted") or "0-0").split("-")
            f3 = (v.get("threePointFieldGoalsMade-threePointFieldGoalsAttempted") or "0-0").split("-")
            ft = (v.get("freeThrowsMade-freeThrowsAttempted") or "0-0").split("-")
            out.append(dict(game_id=gid, team=abbr, player_id=int(a["athlete"]["id"]), name=a["athlete"]["displayName"],
                            pos=(a["athlete"].get("position") or {}).get("abbreviation", ""),
                            starter=int(bool(a.get("starter"))), dnp=int(bool(a.get("didNotPlay"))),
                            min=_num(v.get("minutes")), pts=_num(v.get("points")),
                            fgm=_num(fg[0]), fga=_num(fg[-1]), fg3m=_num(f3[0]), fg3a=_num(f3[-1]), ftm=_num(ft[0]), fta=_num(ft[-1]),
                            reb=_num(v.get("rebounds")), oreb=_num(v.get("offensiveRebounds")), dreb=_num(v.get("defensiveRebounds")),
                            ast=_num(v.get("assists")), stl=_num(v.get("steals")), blk=_num(v.get("blocks")),
                            tov=_num(v.get("turnovers")), pf=_num(v.get("fouls")), plus_minus=_num(v.get("plusMinus"))))
    line = {}
    for p in sm.get("pickcenter", []) or []:
        if p.get("spread") is not None and p.get("overUnder") is not None:
            # ESPN's spread is for the home team when homeTeamOdds.favorite says so; normalise to home
            spread = float(p["spread"])
            hf = (p.get("homeTeamOdds") or {}).get("favorite")
            af = (p.get("awayTeamOdds") or {}).get("favorite")
            if hf is True and spread > 0:
                spread = -spread
            if af is True and spread < 0:
                spread = -spread
            line = dict(spread=spread, total=float(p["overUnder"]), line_source=(p.get("provider") or {}).get("name", ""))
            break
    return out, line


CORE = "https://sports.core.api.espn.com/v2/sports/basketball/leagues/nba/events"


def core_line(season, gid):
    """Pregame spread/total from ESPN's core odds feed (has seasons the summaries lack)."""
    d = cached(os.path.join(RAW, "odds", str(season), f"{gid}.json.gz"), f"{CORE}/{gid}/competitions/{gid}/odds")
    for it in (d or {}).get("items", []):
        if it.get("spread") is None or it.get("overUnder") is None:
            continue
        spread = float(it["spread"])
        if (it.get("homeTeamOdds") or {}).get("favorite") is True and spread > 0:
            spread = -spread
        if (it.get("awayTeamOdds") or {}).get("favorite") is True and spread < 0:
            spread = -spread
        return dict(spread=spread, total=float(it["overUnder"]), line_source=(it.get("provider") or {}).get("name", "") + " (core)")
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seasons", nargs="+", type=int, required=True)
    a = ap.parse_args()
    con = sqlite3.connect(DB)
    for season in a.seasons:
        t0 = time.time()
        g = schedule(season)
        print(f"{season}: {len(g)} games on the schedule ({time.time() - t0:.0f}s)", flush=True)
        if g.empty:
            continue
        done = g[g.state == "FINAL"]
        have = set()
        try:
            have = set(pd.read_sql("SELECT DISTINCT game_id FROM players", con).game_id)
        except Exception:
            pass
        todo = done[~done.game_id.isin(have)]
        files = [(os.path.join(RAW, str(season), f"{gid}.json.gz"), f"{API}/summary?event={gid}") for gid in todo.game_id]
        with ThreadPoolExecutor(max_workers=6) as ex:
            res = list(ex.map(lambda f: cached(*f), files))
        rows, lines = [], {}
        for gid, sm in zip(todo.game_id, res):
            if not sm:
                continue
            pr, ln = parse_summary(int(gid), sm)
            if pr:
                rows += pr
            if ln:
                lines[int(gid)] = ln
        # games whose summary had no line: try the core odds feed
        missing = [int(x) for x in done.game_id if int(x) not in lines]
        if missing:
            try:
                have_ln = set(pd.read_sql(f"SELECT game_id FROM games WHERE season={season} AND spread IS NOT NULL", con).game_id)
            except Exception:
                have_ln = set()
            missing = [x for x in missing if x not in have_ln]
            with ThreadPoolExecutor(max_workers=6) as ex:
                for gid, ln in zip(missing, ex.map(lambda x: core_line(season, x), missing)):
                    if ln:
                        lines[gid] = ln
        # pregame lines for games not yet played (and keep those already stored)
        old = pd.DataFrame()
        try:
            old = pd.read_sql(f"SELECT game_id, spread, total, line_source FROM games WHERE season={season}", con)
        except Exception:
            pass
        ln = pd.DataFrame([dict(game_id=k, **v) for k, v in lines.items()])
        if not old.empty:
            ln = pd.concat([old.dropna(subset=["spread"]), ln]).drop_duplicates("game_id", keep="last")
        g = g.merge(ln, on="game_id", how="left") if not ln.empty else g.assign(spread=None, total=None, line_source=None)
        con.execute(f"DELETE FROM games WHERE season={season}") if _has(con, "games") else None
        g.to_sql("games", con, if_exists="append", index=False)
        if rows:
            p = pd.DataFrame(rows)
            if _has(con, "players"):
                con.execute(f"DELETE FROM players WHERE game_id IN ({','.join(map(str, p.game_id.unique()))})")
            p.to_sql("players", con, if_exists="append", index=False)
        con.commit()
        print(f"{season}: {len(todo)} new final games, {len(rows)} player lines, {len(lines)} lines ({time.time() - t0:.0f}s)", flush=True)


def _has(con, t):
    return con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (t,)).fetchone() is not None


if __name__ == "__main__":
    main()
