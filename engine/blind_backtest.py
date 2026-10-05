#!/usr/bin/env python3
"""
Compare the market-blind build with the Vegas-anchored model on the same games
(walk-forward: 2025 fit on 2022-24, 2026 on 2022-25). Player-games are matched so
both are scored on exactly the same set.

    python blind_backtest.py
"""

import json

import backtest as BT
import tune
from boxscore import data as D
from boxscore import pipeline as PL

BT.STATS["anytime_td"] = (["QB", "RB", "WR", "TE"], 0.0)
split = tune.CONFIRM
con = D.connect()
out = {}
for name, blind in (("vegas", False), ("blind", True)):
    fr = PL.Frames(con, market_free=blind)
    r, tr = BT.run(fr, split["train"], split["test"], sims=3000, verbose=False)
    m = tune.metrics(r)
    pts = tr[tr.stat == "points"]
    m["team_pts_mae"] = float((pts.actual - pts["mean"]).abs().mean())
    m["team_pts_in80"] = float(pts.pit.between(.1, .9).mean())
    for s in ("pass_yds", "rush_yds"):
        t = tr[tr.stat == s]
        m[f"team_{s}_mae"] = float((t.actual - t["mean"]).abs().mean())
    out[name] = m
    print(name, "done", flush=True)

keys = ["team_pts_mae", "team_pts_in80", "team_pass_yds_mae", "team_rush_yds_mae", "in80", "in50",
        "mae_pass_yds", "qs_pass_yds", "mae_att", "mae_rec_yds", "qs_rec_yds", "mae_rush_yds", "qs_rush_yds",
        "mae_rec", "mae_car", "td_logloss", "td_cal_err"]
print(f"\n{'metric':20}{'vegas':>12}{'blind':>12}{'change':>9}")
for k in keys:
    a, b = out["vegas"][k], out["blind"][k]
    print(f"{k:20}{a:12.4f}{b:12.4f}{(b / a - 1) * 100:+8.1f}%")
json.dump(out, open("blind_backtest.json", "w"), indent=1)
