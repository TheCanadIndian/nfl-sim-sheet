#!/usr/bin/env python3
"""
Game-day watcher: re-run a sport's model when the facts it used change.

    python watch.py            # check NFL, NBA and MLB games starting in the next 12 hours

Runs inside the cloud markets job (every 5 minutes in the 3 hours before games, 15 otherwise).
For each upcoming game it compares what our latest projections used with ESPN right now:
  - a projected player (real role: NFL 2+ targets / 3+ carries / QB, NBA 12+ minutes) is now
    Out or Doubtful (e.g. a Questionable player ruled out at inactives)
  - the spread or total moved by 1+ point (NFL) / 1.5+ (NBA) from the line the model used
and starts that sport's workflow once. The re-run itself drops the player (project.espn_out / NBA
injury statuses) and uses the new line (project.espn_lines / NBA ESPN odds), so the same change
never fires twice. A run already queued or started in the last 20 minutes blocks a new one.
"""

import datetime as dt
import glob
import json
import os
import re
import shutil
import subprocess
import sys

import pandas as pd
import requests

HERE = os.path.dirname(os.path.abspath(__file__))
H = {"User-Agent": "Mozilla/5.0"}
ESPN = "https://site.api.espn.com/apis/site/v2/sports"
ESPN_TEAM = {"WSH": "WAS", "LAR": "LA"}
HORIZON_H = 12
COOLDOWN_MIN = 20          # MLB uses 12 (lineups trickle in game by game)


def norm(n):
    n = re.sub(r"[^a-z ]", "", str(n).lower().replace("-", " "))
    return re.sub(r"\s+(jr|sr|ii|iii|iv|v)$", "", n).strip()


def get(url, **params):
    try:
        r = requests.get(url, params=params, headers=H, timeout=20)
        return r.json() if r.ok else None
    except Exception:
        return None


def espn_game(path, event_id):
    """(out names by team, spread (ESPN: negative = home favored), total) for one ESPN event."""
    s = get(f"{ESPN}/{path}/summary", event=event_id)
    if not s:
        return {}, None, None
    out = {}
    for t in s.get("injuries", []):
        team = ESPN_TEAM.get(t.get("team", {}).get("abbreviation"), t.get("team", {}).get("abbreviation"))
        for i in t.get("injuries", []):
            if i.get("status") in ("Out", "Doubtful"):
                out.setdefault(team, set()).add(norm(i.get("athlete", {}).get("displayName")))
    pc = (s.get("pickcenter") or [{}])[0]
    sp, tot = pc.get("spread"), pc.get("overUnder")
    return out, (None if sp is None else float(sp)), (None if tot is None else float(tot))


def soon(start):
    t = pd.Timestamp(start).tz_convert("UTC") if pd.Timestamp(start).tzinfo else pd.Timestamp(start, tz="UTC")
    now = pd.Timestamp.now(tz="UTC")
    return now < t <= now + pd.Timedelta(hours=HORIZON_H)


def nfl_checks():
    import markets as M
    O = M.ours_nfl()
    if not O:
        return []
    stems = sorted(glob.glob(os.path.join(HERE, "projections", "20??_wk??_players.csv")))
    P = pd.read_csv(stems[-1]); T = pd.read_csv(stems[-1].replace("_players", "_teams"))
    role = P[((P.stat == "tgt") & (P["mean"] >= 2)) | ((P.stat == "car") & (P["mean"] >= 3)) | ((P.stat == "att") & (P["mean"] >= 10))]
    reasons = []
    games = [g for g in O["games"] if soon(g["start"])]
    events = {}
    for day in sorted({g["date"].replace("-", "") for g in games}):
        for e in (get(f"{ESPN}/football/nfl/scoreboard", dates=day) or {}).get("events", []):
            cs = {x["homeAway"]: ESPN_TEAM.get(x["team"]["abbreviation"], x["team"]["abbreviation"])
                  for x in e.get("competitions", [{}])[0].get("competitors", [])}
            events[(cs.get("away"), cs.get("home"))] = e["id"]
    for g in games:
        eid = events.get((g["away"], g["home"]))
        if not eid:
            continue
        out, sp, tot = espn_game("football/nfl", eid)
        for team in (g["away"], g["home"]):
            mine = role[(role.game_id == g["id"]) & (role.team == team)].player.unique()
            hit = [n for n in mine if norm(n) in out.get(team, set())]
            for n in hit:
                reasons.append(f"{g['away']}@{g['home']}: {n} ({team}) is now Out/Doubtful but still projected")
        tm = T[T.game_id == g["id"]].set_index("team")
        if sp is not None and tot is not None and g["home"] in tm.index and g["away"] in tm.index:
            used_total = tm.loc[g["home"], "vegas_implied"] + tm.loc[g["away"], "vegas_implied"]
            used_spread = tm.loc[g["home"], "vegas_implied"] - tm.loc[g["away"], "vegas_implied"]   # home favored > 0
            if abs(used_total - tot) >= 1 or abs(used_spread - (-sp)) >= 1:
                reasons.append(f"{g['away']}@{g['home']}: line moved to {g['home']} {sp:+.1f} / {tot:.1f} "
                               f"(model used {-used_spread:+.1f} / {used_total:.1f})")
    return reasons


def nba_checks():
    fs = sorted(glob.glob(os.path.join(HERE, "nba", "projections", "????-??-??.json")))
    if not fs:
        return []
    js = json.load(open(fs[-1]))
    reasons = []
    for g in js["games"]:
        if not soon(g["start_utc"]):
            continue
        out, sp, tot = espn_game("basketball/nba", g["id"])
        for p in js["players"]:
            if p["g"] != g["id"] or p.get("st") != "Active":
                continue
            mins = (((p.get("s") or {}).get("vegas") or {}).get("min") or [0])[0]
            if mins >= 12 and norm(p["n"]) in out.get(p["t"], set()):
                reasons.append(f"{g['away']}@{g['home']}: {p['n']} ({p['t']}, {mins:.0f} min projected) is now Out/Doubtful")
        if g.get("has_line") and sp is not None and tot is not None:
            if abs(g["spread"] - sp) >= 1.5 or abs(g["total"] - tot) >= 1.5:
                reasons.append(f"{g['away']}@{g['home']}: line moved to {g['home']} {sp:+.1f} / {tot:.1f} "
                               f"(model used {g['spread']:+.1f} / {g['total']:.1f})")
        elif not g.get("has_line") and sp is not None:
            reasons.append(f"{g['away']}@{g['home']}: a line is now posted ({g['home']} {sp:+.1f} / {tot})")
    return reasons


def mlb_checks():
    """MLB: a lineup was posted since our sheet (we used a projected one) or a probable starter changed."""
    fs = sorted(glob.glob(os.path.join(HERE, "mlb", "projections", "????-??-??.json")))
    if not fs:
        return []
    js = json.load(open(fs[-1]))
    ours = {}
    for p in js.get("players", []):
        g = ours.setdefault(p["game_pk"], dict(projected=set(), sp={}))
        if p.get("lineup") != "posted":
            g["projected"].add(p["team"])
        g["sp"][p["team"]] = p.get("sp_id")                  # the starter this team faces
    reasons = []
    d = get("https://statsapi.mlb.com/api/v1/schedule", sportId=1, date=js["date"], hydrate="probablePitcher,lineups,team") or {}
    for day in d.get("dates", []):
        for g in day["games"]:
            if not soon(g["gameDate"]) or g["gamePk"] not in ours:
                continue
            o, lu = ours[g["gamePk"]], g.get("lineups") or {}
            for side in ("away", "home"):
                team = g["teams"][side]["team"]["abbreviation"]
                opp = "home" if side == "away" else "away"
                if team in o["projected"] and lu.get(f"{side}Players"):
                    reasons.append(f"{team}: lineup posted (sheet used a projected one)")
                sp_new = (g["teams"][opp].get("probablePitcher") or {}).get("id")
                if sp_new and o["sp"].get(team) and sp_new != o["sp"][team]:
                    reasons.append(f"{team}: opposing starter changed to {(g['teams'][opp].get('probablePitcher') or {}).get('fullName')}")
    return reasons


def busy(workflow, cooldown=COOLDOWN_MIN):
    """True if this workflow is queued / running, or started in the last `cooldown` minutes."""
    gh = shutil.which("gh")
    if not gh:
        return False
    r = subprocess.run([gh, "run", "list", "--workflow", workflow, "--limit", "3", "--json", "status,createdAt"],
                       capture_output=True, text=True)
    try:
        runs = json.loads(r.stdout or "[]")
    except ValueError:
        return False
    now = pd.Timestamp.now(tz="UTC")
    return any(x["status"] in ("queued", "in_progress", "waiting", "pending") or
               now - pd.Timestamp(x["createdAt"]) < pd.Timedelta(minutes=cooldown) for x in runs)


def main():
    for sport, check, wf, args in (("nfl", nfl_checks, "nfl.yml", ["-f", "mode=nfl"]), ("nba", nba_checks, "nba.yml", []), ("mlb", mlb_checks, "mlb.yml", [])):
        try:
            reasons = check()
        except Exception as e:                              # one sport's trouble never blocks the other
            print(f"{sport}: watch skipped ({type(e).__name__}: {e})")
            continue
        if not reasons:
            print(f"{sport}: nothing changed")
            continue
        for r in reasons:
            print(f"{sport}: {r}")
        if not os.environ.get("GITHUB_ACTIONS"):
            print(f"{sport}: (local run: not starting {wf})")
        elif busy(wf, 12 if sport == "mlb" else COOLDOWN_MIN):
            print(f"{sport}: {wf} ran or is running in the last {COOLDOWN_MIN} min -- not starting another")
        else:
            subprocess.run([shutil.which("gh"), "workflow", "run", wf, *args], check=False)
            print(f"{sport}: started {wf}")
        with open(os.path.join(HERE, "markets", "watch_log.txt"), "a", encoding="utf-8") as f:
            for r in reasons:
                f.write(f"{dt.datetime.now().isoformat(timespec='minutes')} {sport}: {r}\n")


if __name__ == "__main__":
    main()
