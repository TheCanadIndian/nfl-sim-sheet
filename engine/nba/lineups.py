#!/usr/bin/env python3
"""
NBA starting lineups (owner 2026-10-23: "make sure we are considering starting lineups"; "use projected starting
lineups"). Our minutes model doesn't know who starts tonight, and lineup changes are big:
  research (nba/backtest walk-forward, 2025 fit -> 2026 test): a player moving INTO the starting five ran x1.20 his
  projected minutes (23 -> 28), moving OUT x0.87; applying those to 2026 improved minutes / points / rebounds / assists /
  threes deviance in both halves (with the actual starters -- the best case).
Tonight's five per team:
  official   ESPN's game summary lists the starters (posted ~30 min before tip) -> full multiplier
  projected  each position (PG, SG, SF, PF, C) takes the first healthy player in ESPN's depth-chart order, skipping
             anyone Out (ESPN keeps injured starters at the top of its chart, so the chart alone mostly repeats the
             last lineup) -> the multiplier at PROJ_STRENGTH of its effect until lineups_log.json shows how often the
             projection is right; a team whose projected five includes a day-to-day / questionable player ('unsure')
             gets no change until its official lineup posts
Each player's last game (started or not) vs tonight's five: moved in x MOVE_IN, moved out x MOVE_OUT on his
expected minutes (model.team_minutes then rescales the team to 240).
"""
import datetime as dt
import json
import os

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
API = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"
H = {"User-Agent": "Mozilla/5.0 (personal NBA projections)"}
LOG = os.path.join(HERE, "lineups_log.json")
MOVE_IN, MOVE_OUT = 1.197, 0.868          # fit 2025 (relative to unchanged starters), held in 2026
PROJ_STRENGTH = 0.7                       # share of the (log) effect for a projected, unconfirmed lineup
OUT = ("Out", "Suspension", "Injured Reserve")


def _get(url):
    try:
        r = requests.get(url, headers=H, timeout=30)
        return r.json() if r.status_code == 200 else None
    except (requests.RequestException, ValueError):
        return None


def official(gid):
    """{team: [player ids]} when ESPN's summary already lists tonight's starters, else {}."""
    s = _get(f"{API}/summary?event={gid}") or {}
    out = {}
    for t in (s.get("boxscore") or {}).get("players", []):
        ab = (t.get("team") or {}).get("abbreviation")
        ids = [int(a["athlete"]["id"]) for st in t.get("statistics", []) for a in st.get("athletes", []) if a.get("starter")]
        if ab and len(set(ids)) == 5:
            out[ab] = sorted(set(ids))
    return out


def projected(teams, out_ids):
    """{team: ([player ids], unsure)}: first healthy player at each depth-chart position (ESPN), skipping out_ids / ESPN
    Out; unsure = one of the five is day-to-day / questionable (then no change is applied until the official lineup)."""
    idx = _get(f"{API}/teams") or {}
    tid = {t["team"]["abbreviation"]: t["team"]["id"] for t in (idx.get("sports") or [{}])[0].get("leagues", [{}])[0].get("teams", [])}
    out = {}
    for ab in teams:
        if ab not in tid:
            continue
        d = _get(f"{API}/teams/{tid[ab]}/depthcharts") or {}
        pos = ((d.get("depthchart") or [{}])[0]).get("positions", {})
        five, unsure = [], False
        for k in ("pg", "sg", "sf", "pf", "c"):
            for a in (pos.get(k) or {}).get("athletes", []):
                pid = int(a["id"])
                inj = [i.get("status") for i in a.get("injuries") or []]
                if pid in out_ids or any(x in OUT for x in inj) or pid in five:
                    continue
                five.append(pid)
                unsure = unsure or bool(inj)                     # a day-to-day / questionable player in the five
                break
        if len(five) == 5:
            out[ab] = (five, unsure)
    return out


def tonight(games, out_ids):
    """games: [(game_id, home, away)]. Returns {team: (five, 'official' | 'projected')} and logs it."""
    res = {}
    for gid, h, a in games:
        off = official(gid)
        for t in (h, a):
            if t in off:
                res[t] = (off[t], "official")
    proj = projected([t for _, h, a in games for t in (h, a) if t not in res], out_ids)
    for t, (five, unsure) in proj.items():
        res[t] = (five, "unsure" if unsure else "projected")
    try:
        log = json.load(open(LOG)) if os.path.exists(LOG) else {}
        now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="minutes")
        for gid, h, a in games:
            for t in (h, a):
                if t in res:
                    log.setdefault(str(gid), {})[t] = dict(five=res[t][0], src=res[t][1], at=now)
        json.dump(dict(list(log.items())[-400:]), open(LOG, "w"), separators=(",", ":"))
    except (OSError, ValueError):
        pass
    return res


def multipliers(rows, last_start, lineup):
    """rows: DataFrame (team, player_id); last_start: {player_id: 0/1 his last game}; lineup: tonight(...).
    Returns (multiplier array, tag list) -- tag "in|official", "out|projected", ... or None."""
    import numpy as np
    m, tags = [], []
    for t, pid in zip(rows.team, rows.player_id.astype(int)):
        five, src = lineup.get(t, (None, None))
        prev = last_start.get(pid)
        if five is None or prev is None or src == "unsure":        # a questionable starter: wait for the official five
            m.append(1.0); tags.append(None); continue
        now_ = pid in five
        k = 1.0 if src == "official" else PROJ_STRENGTH
        if now_ and not prev:
            m.append(float(np.exp(k * np.log(MOVE_IN)))); tags.append(f"in|{src}")
        elif prev and not now_:
            m.append(float(np.exp(k * np.log(MOVE_OUT)))); tags.append(f"out|{src}")
        else:
            m.append(1.0); tags.append(None)
    return np.array(m), tags
