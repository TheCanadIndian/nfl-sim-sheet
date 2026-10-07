#!/usr/bin/env python3
"""
NHL EDGE puck/player tracking (public api-web.nhle.com) per skater-season, cached under hockey/raw/edge/<season>/.

    python hockey/edge.py --seasons 20232024 20242025 20252026

Kept per skater-season (regular season): offensive-zone time share, shots on goal and goals by area
(high-danger slot / crease vs the rest), top and average shot speed, top skating speed and speed bursts.
Used walk-forward: a season's EDGE numbers only describe the NEXT season's games.
"""

import argparse
import gzip
import json
import os
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import requests

HERE = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(HERE, "raw", "edge")
API = "https://api-web.nhle.com/v1/edge"
H = {"User-Agent": "Mozilla/5.0 (personal NHL projections)"}
KINDS = ("skater-detail", "skater-shot-location-detail", "skater-zone-time")


def fetch(pid, season, kind):
    path = os.path.join(RAW, str(season), f"{pid}_{kind}.json.gz")
    if os.path.exists(path):
        return True
    for i in range(3):
        try:
            r = requests.get(f"{API}/{kind}/{pid}/{season}/2", headers=H, timeout=30)
            if r.status_code == 404:
                return False
            if r.ok:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with gzip.open(path, "wt", encoding="utf-8") as f:
                    f.write(r.text)
                return True
        except requests.RequestException:
            pass
        time.sleep(2 * (i + 1))
    return False


def table(seasons):
    """One row per (player_id, season) with the kept EDGE numbers."""
    rows = []
    for s in seasons:
        d = os.path.join(RAW, str(s))
        if not os.path.isdir(d):
            continue
        pids = {f.split("_")[0] for f in os.listdir(d)}
        for pid in pids:
            r = dict(player_id=int(pid), season=int(s))
            try:
                z = json.load(gzip.open(os.path.join(d, f"{pid}_skater-zone-time.json.gz")))
                for x in z.get("zoneTimeDetails", []):
                    if x.get("strengthCode") == "all":
                        r["oz_time"] = x.get("offensiveZonePctg")
                    if x.get("strengthCode") == "es":
                        r["oz_time_es"] = x.get("offensiveZonePctg")
            except (OSError, ValueError):
                pass
            try:
                sl = json.load(gzip.open(os.path.join(d, f"{pid}_skater-shot-location-detail.json.gz")))
                det = sl.get("shotLocationDetails", [])
                tot = sum(x.get("sog", 0) for x in det) or 0
                hd_areas = {"Crease", "Low Slot", "L Circle", "R Circle", "High Slot"}
                hd = [x for x in det if x.get("area") in {"Crease", "Low Slot"}]
                sl_ = [x for x in det if x.get("area") in hd_areas]
                r["sog_edge"] = tot
                r["hd_share"] = sum(x.get("sog", 0) for x in hd) / tot if tot else None
                r["slot_share"] = sum(x.get("sog", 0) for x in sl_) / tot if tot else None
                g = sum(x.get("goals", 0) for x in hd)
                r["hd_shpct"] = g / sum(x.get("sog", 0) for x in hd) if hd and sum(x.get("sog", 0) for x in hd) else None
            except (OSError, ValueError):
                pass
            try:
                sd = json.load(gzip.open(os.path.join(d, f"{pid}_skater-detail.json.gz")))
                def dig(o, *ks):
                    for k in ks:
                        o = (o or {}).get(k) if isinstance(o, dict) else None
                    return o
                r["top_shot_speed"] = dig(sd, "topShotSpeed", "imperial")
                r["top_skate_speed"] = dig(sd, "skatingSpeed", "speedMax", "imperial")
                r["bursts_20"] = dig(sd, "skatingSpeed", "burstsOver20", "value")
            except (OSError, ValueError):
                pass
            rows.append(r)
    return pd.DataFrame(rows)


def main(seasons, min_games=10):
    con = sqlite3.connect(os.path.join(HERE, "nhl.db"))
    jobs = []
    for s in seasons:
        q = """SELECT t.player_id, COUNT(*) n FROM toi t JOIN games g USING(game_id)
               WHERE g.season=? AND g.game_type=2 AND t.pos != 'G' GROUP BY t.player_id HAVING n >= ?"""
        for pid, _ in con.execute(q, (s, min_games)):
            for k in KINDS:
                jobs.append((pid, s, k))
    print(len(jobs), "EDGE requests", flush=True)
    with ThreadPoolExecutor(3) as ex:
        for i, ok in enumerate(ex.map(lambda j: fetch(*j), jobs)):
            if i % 1500 == 0:
                print(i, flush=True)
    t = table(seasons)
    print(t.describe().T[["count", "mean"]].round(3).to_string())


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--seasons", nargs="+", type=int, default=[20232024, 20242025, 20252026])
    main(ap.parse_args().seasons)
