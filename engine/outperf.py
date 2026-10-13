#!/usr/bin/env python3
"""
Receiving-yards 'fade' flags, tracked forward (not shown on the site yet).

Backtests (2025-26, walk-forward rows in backtest_rows.csv) found receivers our model slightly overrates: a hot
yards-per-target streak, a target-share spike, high yards per air yard over the last 3 games, veterans, low Vegas team
totals; and slightly underrates high-snap receivers. outperf_params.json holds a small ridge fit of
(PIT - 0.5) on those signals. A shift of -3 PIT points or more = UNDER flag, +3 or more = OVER flag.

    python outperf.py          # flag this week's receivers (games not started; started games keep their flags)
    python outperf.py grade    # grade past flags vs our median, Kalshi's 50/50 rung and the books' main line

Writes projections/<week>_outperf.csv and markets/outperf_record.json. Cloud: after each NFL run.
"""

import glob
import json
import os
import sqlite3
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
SITE = os.environ.get("SIM_SITE") or os.path.join(HERE, "site")
P = json.load(open(os.path.join(HERE, "outperf_params.json")))
CUT = .03


def features(con, rows, season, week):
    """Signals from games before `week` (same definitions as the backtest study)."""
    pw = pd.read_sql("SELECT player_id, season, week, targets, receiving_yards, receiving_air_yards, target_share FROM player_week "
                     "WHERE (season_type = 'REG' OR season_type IS NULL) AND season * 100 + week < ?", con, params=(season * 100 + week,))
    pw = pw.sort_values(["player_id", "season", "week"])
    ro = pd.read_sql("SELECT gsis_id, pfr_id, years_exp, season FROM rosters", con)
    sn = pd.read_sql("SELECT season, week, pfr_player_id, offense_pct FROM snaps WHERE game_type = 'REG' AND season * 100 + week < ?",
                     con, params=(season * 100 + week,))
    sn = sn.merge(ro[["gsis_id", "pfr_id"]].drop_duplicates("pfr_id"), left_on="pfr_player_id", right_on="pfr_id")
    last = ro.sort_values("season").drop_duplicates("gsis_id", keep="last").set_index("gsis_id")
    yx, LAST = last.years_exp, last.season.to_dict()          # years in the league, advanced to this season
    PW, SN = dict(list(pw.groupby("player_id"))), dict(list(sn.sort_values(["season", "week"]).groupby("gsis_id")))
    out = []
    for r in rows.itertuples():
        f = {}
        h = PW.get(r.player_id)
        if h is not None and len(h) >= 3:
            l3, base = h.tail(3), h.tail(10)
            tg, btg = l3.targets.sum(), base.targets.sum()
            f["yds_per_tgt_l3_vs_base"] = (l3.receiving_yards.sum() / tg if tg else np.nan) - (base.receiving_yards.sum() / btg if btg else np.nan)
            f["tgt_share_trend"] = l3.target_share.mean() - base.target_share.mean()
            ay = l3.receiving_air_yards.sum()
            f["racr_l3"] = l3.receiving_yards.sum() / ay if ay > 0 else np.nan
        s = SN.get(r.player_id)
        if s is not None and len(s) >= 2:
            f["snap_l2"] = s.tail(2).offense_pct.mean()
        y = yx.get(r.player_id)
        f["years_exp"] = y + (season - LAST.get(r.player_id, season)) if y is not None and not pd.isna(y) else np.nan
        f["team_total"] = r.team_total
        out.append(f)
    return pd.DataFrame(out, index=rows.index)


def shift(F):
    z = sum(np.clip(((F[k] - mu) / (sd or 1)).fillna(0), -2.5, 2.5) * b for k, (mu, sd, b) in P.items())
    return np.clip(z, -.08, .08)


def lines():
    """{(game, player_id): Kalshi 50/50 rung} and {(game, normalized name): books' main line} for receiving yards."""
    k, b = {}, {}
    try:
        L = json.load(open(os.path.join(SITE, "markets", "live_nfl.json")))
        for r in L.get("rows", []):
            if r.get("k") == "rec_yds" and r.get("s") == "kalshi" and r.get("pid") and r.get("ya") is not None:
                mid = (r["ya"] + (r.get("yb") or r["ya"])) / 2
                cur = k.get((r["g"], r["pid"]))
                if cur is None or abs(mid - .5) < cur[0]:
                    k[(r["g"], r["pid"])] = (abs(mid - .5), r["l"])
    except (OSError, ValueError):
        pass
    try:
        R = json.load(open(os.path.join(SITE, "markets", "props_nfl.json")))
        for gid, g in R.get("props", {}).items():
            for nm, kinds in g.items():
                if "rec_yds" in kinds and kinds["rec_yds"].get("l") is not None:
                    b[(gid, nm)] = kinds["rec_yds"]["l"]
    except (OSError, ValueError):
        pass
    return {key: v[1] for key, v in k.items()}, b


def flag():
    fs = sorted(glob.glob(os.path.join(HERE, "projections", "20??_wk??_players.csv")))
    if not fs:
        return
    stem = fs[-1][:-len("_players.csv")]
    season, week = int(os.path.basename(stem)[:4]), int(os.path.basename(stem)[7:9])
    pl = pd.read_csv(stem + "_players.csv")
    tm = pd.read_csv(stem + "_teams.csv")
    rows = pl[(pl.stat == "rec_yds") & pl.pos.isin(["WR", "TE", "RB"]) & (pl["mean"] >= 15)].copy()
    rows = rows.merge(tm[["game_id", "team", "vegas_implied"]].rename(columns={"vegas_implied": "team_total"}), on=["game_id", "team"], how="left")
    con = sqlite3.connect(os.path.join(HERE, "nfl.db"))
    started = set(pd.read_sql("SELECT game_id FROM games WHERE gameday || ' ' || COALESCE(gametime, '13:00') <= ?", con,
                              params=(pd.Timestamp(os.environ.get("OUTPERF_NOW") or pd.Timestamp.now(tz="America/New_York")).strftime("%Y-%m-%d %H:%M"),)).game_id)
    rows = rows[~rows.game_id.isin(started)]
    out_path = stem + "_outperf.csv"
    old = pd.read_csv(out_path) if os.path.exists(out_path) else pd.DataFrame()
    keep = old[old.game_id.isin(started)] if len(old) else old
    if rows.empty:
        return
    F = features(con, rows, season, week)
    rows = rows.join(F[[c for c in F.columns if c != "team_total"]])
    rows["shift"] = shift(rows)
    rows["flag"] = np.where(rows["shift"] <= -CUT, "under", np.where(rows["shift"] >= CUT, "over", ""))
    import sharp
    kal, bk = lines()
    rows["kalshi_line"] = [kal.get((g, p)) for g, p in zip(rows.game_id, rows.player_id)]
    rows["book_line"] = [bk.get((g, sharp.jsnorm(n))) for g, n in zip(rows.game_id, rows.player)]
    rows["season"], rows["week"] = season, week
    cols = ["season", "week", "game_id", "team", "player", "player_id", "pos", "median", "p10", "p90", "shift", "flag", "kalshi_line", "book_line", *P]
    out = pd.concat([keep, rows[cols]], ignore_index=True)
    out.to_csv(out_path, index=False)
    print(f"outperf: {len(rows)} receivers, {int((rows.flag == 'under').sum())} under / {int((rows.flag == 'over').sum())} over flags -> {os.path.basename(out_path)}")


def grade():
    fs = sorted(glob.glob(os.path.join(HERE, "projections", "20??_wk??_outperf.csv")))
    if not fs:
        return
    x = pd.concat([pd.read_csv(f) for f in fs], ignore_index=True)
    con = sqlite3.connect(os.path.join(HERE, "nfl.db"))
    act = pd.read_sql("SELECT player_id, season, week, receiving_yards FROM player_week", con)
    x = x.merge(act, on=["player_id", "season", "week"], how="inner")
    rec = {}
    for fl in ("under", "over"):
        z = x[x.flag == fl]
        sgn = -1 if fl == "under" else 1
        res = {"n": int(len(z))}
        for lab, col in (("vs_our_median", "median"), ("vs_kalshi", "kalshi_line"), ("vs_books", "book_line")):
            y = z.dropna(subset=[col])
            y = y[y.receiving_yards != y[col]]
            res[lab] = dict(n=int(len(y)), hit=round(float((np.sign(y.receiving_yards - y[col]) == sgn).mean()), 3) if len(y) else None)
        rec[fl] = res
    allz = x.dropna(subset=["kalshi_line"])
    rec["all_receivers_under_kalshi"] = round(float((allz.receiving_yards < allz.kalshi_line).mean()), 3) if len(allz) else None
    rec["weeks"] = sorted({f"{s}-{w:02d}" for s, w in zip(x.season, x.week)})
    os.makedirs(os.path.join(HERE, "markets"), exist_ok=True)
    json.dump(rec, open(os.path.join(HERE, "markets", "outperf_record.json"), "w"), indent=1)
    print("outperf record:", json.dumps(rec))


if __name__ == "__main__":
    (grade if sys.argv[1:] == ["grade"] else flag)()
