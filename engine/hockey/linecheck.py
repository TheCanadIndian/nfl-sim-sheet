#!/usr/bin/env python3
"""
How accurate were the pregame positions, lines and power-play units on the goal sheet?

For every graded game, rebuild the real lines from the NHL shift charts: at even strength
(5 skaters a side), each forward's two most frequent forward linemates and each
defenseman's most frequent partner. Compare with the pregame sheet (hockey/projections/
<date>.csv: line F1-F4 / D1-D3 from the reported lines, pp_role PP1/PP2, pos).

    python hockey/linecheck.py            # prints a report, writes projections/linecheck.json
"""

import glob
import gzip
import json
import os
import sqlite3
import sys

import numpy as np
import pandas as pd
import requests

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "projections")
RAW = os.path.join(HERE, "raw", "shifts")
DB = os.path.join(HERE, "nhl.db")


def shifts(gid):
    """Shift chart for one game, cached on disk."""
    os.makedirs(RAW, exist_ok=True)
    path = os.path.join(RAW, f"{gid}.json.gz")
    if os.path.exists(path):
        return json.load(gzip.open(path))
    r = requests.get(f"https://api.nhle.com/stats/rest/en/shiftcharts?cayenneExp=gameId={gid}", timeout=30,
                     headers={"User-Agent": "Mozilla/5.0 (personal NHL projections)"})
    d = r.json().get("data", [])
    if d:
        json.dump(d, gzip.open(path, "wt"))
    return d


def _sec(t):
    m, s = str(t).split(":")
    return int(m) * 60 + int(s)


def actual_lines(gid, pos):
    """{player_id: dict(team, ev_mates={id: seconds}, ev_toi)} from even-strength overlap."""
    sh = [x for x in shifts(gid) if x.get("typeCode") == 517 and x.get("duration")]
    if not sh:
        return None
    rows = []
    for x in sh:
        a = (x["period"] - 1) * 1200 + _sec(x["startTime"])
        b = (x["period"] - 1) * 1200 + _sec(x["endTime"])
        if b > a:
            rows.append((x["playerId"], x["teamAbbrev"], a, b))
    end = max(b for *_, b in rows)
    teams = sorted({t for _, t, _, _ in rows})
    on = {t: [set() for _ in range(end + 1)] for t in teams}
    for pid, t, a, b in rows:
        if pos.get(pid) in (None, "G"):       # skaters only (goalies aren't in the skater ice-time table)
            continue
        for s in range(a, b):
            on[t][s].add(pid)
    ev = [len(on[teams[0]][s]) == 5 and len(on[teams[1]][s]) == 5 for s in range(end + 1)] if len(teams) == 2 else []
    out = {}
    for t in teams:
        for s in range(end + 1):
            if not ev or not ev[s]:
                continue
            cur = on[t][s]
            for p in cur:
                d = out.setdefault(p, dict(team=t, ev_toi=0, mates={}))
                d["ev_toi"] += 1
                for q in cur:
                    if q != p:
                        d["mates"][q] = d["mates"].get(q, 0) + 1
    return out


def check():
    con = sqlite3.connect(DB)
    toi = pd.read_sql("SELECT game_id, player_id, team, pos, pp_toi FROM toi", con)
    played = set(toi.game_id)
    rows, games, pos_rows, pp_rows = [], 0, [], []
    for f in sorted(glob.glob(os.path.join(OUT, "????-??-??.csv"))):
        sheet = pd.read_csv(f)
        if "line" not in sheet:
            continue
        for gid, x in sheet.groupby("game_id"):
            if gid not in played:
                continue
            gt = toi[toi.game_id == gid]
            gpos = dict(zip(gt.player_id, gt.pos))
            act = actual_lines(int(gid), gpos)
            if not act:
                continue
            games += 1
            x = x[x.player_id.notna()].copy()
            x["player_id"] = x.player_id.astype(int)
            # positions: sheet vs the game's official position code
            for r in x.itertuples():
                if r.player_id in gpos:
                    pos_rows.append(dict(sheet=r.pos, game=gpos[r.player_id], ok=r.pos == gpos[r.player_id]))
            for team, y in x.groupby("team"):
                listed = y[y.line.notna() & (y.line != "")]
                src = y.src.iloc[0] if "src" in y else ""
                # actual line order: forwards ranked by even-strength ice time, grouped greedily into trios
                fw = [p for p, d in act.items() if d["team"] == team and gpos.get(p) in ("C", "L", "R")]
                dm = [p for p, d in act.items() if d["team"] == team and gpos.get(p) == "D"]
                for grp, size, label in ((fw, 3, "F"), (dm, 2, "D")):
                    left = sorted(grp, key=lambda p: -act[p]["ev_toi"])
                    real = {}
                    n = 0
                    while left:
                        n += 1
                        lead = left.pop(0)
                        mates = sorted([q for q in left], key=lambda q: -act[lead]["mates"].get(q, 0))[:size - 1]
                        for p in [lead] + mates:
                            real[p] = f"{label}{n}"
                            if p in left:
                                left.remove(p)
                    for r in listed[listed.line.str.startswith(label)].itertuples():
                        if r.player_id not in act:
                            continue
                        mates_listed = set(listed[(listed.line == r.line) & (listed.player_id != r.player_id)].player_id)
                        top = [q for q, _ in sorted(act[r.player_id]["mates"].items(), key=lambda kv: -kv[1])
                               if q in set(grp)][:size - 1]
                        rows.append(dict(game_id=gid, team=team, src=src, kind="forward" if label == "F" else "defense",
                                         name=r.name, listed=r.line, actual=real.get(r.player_id),
                                         same_line_no=real.get(r.player_id) == r.line,
                                         mates_right=len(mates_listed & set(top)), mates_total=len(mates_listed)))
                # power play: listed PP1 vs the five skaters with the most PP time
                ppt = gt[gt.team == team].copy()
                ppt["pp_s"] = ppt.pp_toi.map(lambda v: _sec(v) if isinstance(v, str) and ":" in v else float(v or 0))
                real_pp1 = set(ppt.sort_values("pp_s", ascending=False).head(5).player_id)
                lp = set(y[y.pp_role == "PP1"].player_id)
                if lp and ppt.pp_s.sum() > 0:
                    pp_rows.append(dict(src=src, listed=len(lp), right=len(lp & real_pp1)))
    return pd.DataFrame(rows), pd.DataFrame(pos_rows), pd.DataFrame(pp_rows), games


def main():
    r, p, pp, games = check()
    out = dict(games=games)
    print(f"graded games with shift charts: {games}")
    if len(p):
        out["positions"] = dict(n=int(len(p)), right=round(float(p.ok.mean()), 4))
        print(f"\npositions matching the game's official position: {p.ok.mean():.1%} of {len(p)}")
        bad = p[~p.ok]
        if len(bad):
            print(bad.groupby(["sheet", "game"]).size().rename("players").to_string())
    if len(r):
        out["lines"] = []
        for (kind, src), x in r.groupby(["kind", "src"]):
            d = dict(kind=kind, src=src, players=int(len(x)),
                     mates=round(float(x.mates_right.sum() / max(x.mates_total.sum(), 1)), 3),
                     same_line=round(float(x.same_line_no.mean()), 3))
            out["lines"].append(d)
            print(f"\n{kind}s, source {src}: {len(x)} players · listed linemates who really were his most "
                  f"frequent linemates: {d['mates']:.1%} · same line number: {d['same_line']:.1%}")
        worst = r[r.mates_right < r.mates_total].groupby(["game_id", "team", "src"]).size().sort_values(ascending=False).head(8)
        print("\nmost line changes vs the sheet (players with a wrong linemate):"); print(worst.to_string())
    if len(pp):
        out["pp1"] = []
        for src, x in pp.groupby("src"):
            d = dict(src=src, teams=int(len(x)), right=round(float(x.right.sum() / x.listed.sum()), 3))
            out["pp1"].append(d)
            print(f"\nPP1, source {src}: {d['right']:.1%} of listed PP1 players were among the team's top-5 PP ice time ({len(x)} team-games)")
    json.dump(out, open(os.path.join(OUT, "linecheck.json"), "w"))


if __name__ == "__main__":
    main()
