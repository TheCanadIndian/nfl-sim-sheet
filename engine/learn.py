#!/usr/bin/env python3
"""
Self-tuning: each run tries small changes to a few model settings, scores them on recent
seasons with the walk-forward backtest (every game projected only from data before it), and
adopts at most ONE change, and only if it improves BOTH test windows and keeps calibration.

    python learn.py nfl            # ~25-35 min
    python learn.py nhl            # ~15-25 min
    python learn.py nfl --dry-run  # score candidates, change nothing

Guardrails
- Small steps inside fixed bounds (TUNABLE); one change per run, so every change is checked
  against the latest data before the next one.
- Two windows must both improve (NFL: last completed season and this season to date once it
  has 48+ games, else the two last completed seasons; NHL the same with 400+ games).
- NFL: every player is graded (no props floor; floors create fake position biases), the
  average change across target/catch/yardage/carry quantile scores and TD log-loss must
  improve by MIN_GAIN, no single stat may get worse by more than 1%, and the 80% range must
  still hold 76-84% of results. NHL: anytime-goal log-loss must improve by MIN_GAIN_NHL.
- The current setting is always a candidate, so a change that stops helping can be reversed
  by a later run (it would need to win, like any other move).
Adopted values go to model_params.json (applied by learned.py); every run is appended to
learning_log.json and shown on the site's Model updates page.
"""

import argparse
import datetime as dt
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(HERE, "learning_log.json")
sys.path.insert(0, HERE)
import learned  # noqa: E402

# (module.attr, step, lo, hi, description)
TUNABLE = {
    "nfl": [
        ("boxscore.priors.K_TGT", 5, 10, 60, "target-share shrinkage (games of evidence toward role average)"),
        ("boxscore.priors.K_CAR", 5, 5, 40, "carry-share shrinkage"),
        ("boxscore.priors.K_CATCH", 10, 20, 150, "catch-rate shrinkage"),
        ("boxscore.priors.K_YPR", 10, 10, 80, "yards-per-catch shrinkage"),
        ("boxscore.priors.K_YPC", 20, 40, 200, "yards-per-carry shrinkage"),
        ("boxscore.priors.K_REC_TD", 5, 5, 60, "receiving-TD share shrinkage"),
        ("boxscore.priors.K_RUSH_TD", 5, 5, 50, "rushing-TD share shrinkage"),
        ("boxscore.priors.ROOKIE_USAGE", .05, 0, .3, "early-round rookie usage boost"),
        ("boxscore.sim.TD_GAMMA", .05, .6, 1.0, "TD share compression"),
        ("boxscore.sim.QB_TD_MULT", .2, 1.0, 4.0, "QB goal-line weight"),
        ("boxscore.pipeline.FUNNEL_BETA", .1, 0, .6, "defense target-funnel strength"),
    ],
    "nhl": [
        ("model.K_FIN_XG", 10, 10, 100, "finishing shrinkage (expected goals of evidence)"),
        ("model.RATE_HL", 10, 10, 80, "shot-rate memory (games)"),
        ("model.TOI_HL", 1, 2, 10, "ice-time memory (games)"),
        ("model.K_NP_HOURS", 1, 1, 10, "even-strength rate shrinkage (hours)"),
        ("model.K_PP_HOURS", .5, .5, 5, "power-play rate shrinkage (hours)"),
        ("model.K_GOALIE_XG", 20, 20, 200, "goalie rating shrinkage"),
        ("model.TEAM_HL", 5, 5, 40, "team defense memory (games)"),
    ],
}
PER_RUN = {"nfl": 4, "nhl": 4}     # settings tried per run (rotating), each up and down
MIN_GAIN = 0.0015                  # NFL: average relative improvement required in each window (0.15%)
MIN_GAIN_NHL = 0.00004             # NHL: log-loss improvement required in each window
NFL_STATS = ["tgt", "rec", "rec_yds", "car", "rush_yds", "pass_yds"]


def _log():
    return json.load(open(LOG)) if os.path.exists(LOG) else []


def _rotation(sport):
    """Which settings to try this run: continue round-robin from the last run."""
    names = [t[0] for t in TUNABLE[sport]]
    runs = [r for r in _log() if r["sport"] == sport]
    start = runs[-1].get("next", 0) if runs else 0
    pick = [names[(start + i) % len(names)] for i in range(PER_RUN[sport])]
    return pick, (start + PER_RUN[sport]) % len(names)


def _value(key):
    import importlib
    mod, attr = key.rsplit(".", 1)
    return getattr(importlib.import_module(mod), attr)


def _candidates(sport, keys):
    spec = {t[0]: t for t in TUNABLE[sport]}
    out = []
    for k in keys:
        _, step, lo, hi, _ = spec[k]
        v = _value(k)
        for nv in (v - step, v + step):
            nv = round(nv, 4)
            if lo - 1e-9 <= nv <= hi + 1e-9:
                out.append({k: nv})
    return out


# ------------------------------------------------------------------ NFL

def _nfl_windows():
    from boxscore import data as D
    con = D.connect(os.path.join(HERE, "nfl.db"))
    import pandas as pd
    g = pd.read_sql("SELECT season, COUNT(*) n FROM games WHERE result IS NOT NULL AND game_type='REG' GROUP BY 1", con)
    n = dict(zip(g.season, g.n))
    cur = max(n)
    return [cur - 1, cur] if n.get(cur, 0) >= 48 else [cur - 2, cur - 1]


def _nfl_eval(cfg, windows, frames_cache):
    import importlib
    import backtest as BT
    import tune as T
    from boxscore import data as D
    from boxscore import pipeline as PL
    for k in list(BT.STATS):
        BT.STATS[k] = (BT.STATS[k][0], 0.0)              # grade every player (no props floor)
    for k, v in cfg.items():
        mod, attr = k.rsplit(".", 1)
        setattr(importlib.import_module(mod), attr, v)
    pkey = tuple((k, _value(k)) for k, *_ in TUNABLE["nfl"] if k.startswith("boxscore.priors"))
    if pkey not in frames_cache:
        frames_cache.clear()
        frames_cache[pkey] = PL.Frames(D.connect(os.path.join(HERE, "nfl.db")))
    r, _ = BT.run(frames_cache[pkey], [2022, windows[0] - 1], windows, sims=2000, verbose=False)
    out = {}
    for s in windows:
        x = r[r.season == s]
        T.REF.pop("keys", None)                        # score each window on its own rows
        m = T.metrics(x)
        out[s] = {**{f"qs_{st}": m[f"qs_{st}"] for st in NFL_STATS}, "td_logloss": m["td_logloss"], "in80": m["in80"]}
    return out


def _nfl_gain(base, new):
    """Average relative change (negative = better), worst single change, coverage."""
    keys = [f"qs_{st}" for st in NFL_STATS] + ["td_logloss"]
    rel = [new[k] / base[k] - 1 for k in keys]
    return sum(rel) / len(rel), max(rel), new["in80"]


# ------------------------------------------------------------------ NHL

def _nhl_windows():
    sys.path.insert(0, os.path.join(HERE, "hockey"))
    import sqlite3
    con = sqlite3.connect(os.path.join(HERE, "hockey", "nhl.db"))
    rows = con.execute("SELECT season, COUNT(*) FROM games WHERE state IN ('OFF','FINAL') AND game_type=2 GROUP BY 1").fetchall()
    n = dict(rows)
    cur = max(n)
    return [cur - 10001, cur] if n.get(cur, 0) >= 400 else [cur - 20002, cur - 10001]


def _nhl_eval(cfg, windows):
    sys.path.insert(0, os.path.join(HERE, "hockey"))
    import model as M
    import tune as HT          # hockey/tune.py (hockey dir first on the path)
    out = {}
    for S in windows:
        sc = HT.evaluate({k.split(".", 1)[1]: v for k, v in cfg.items()}, S)
        out[S] = {"logloss": sc["logloss"], "mean_p": sc["mean_p"], "rate": sc["rate"]}
    return out


# ------------------------------------------------------------------ main

def run(sport, dry=False):
    t0 = time.time()
    if sport == "nhl":
        sys.path.insert(0, os.path.join(HERE, "hockey"))
    learned.apply(sport)
    keys, nxt = _rotation(sport)
    if sport == "nfl":
        sys.path.insert(0, HERE)
        windows = _nfl_windows()
        cache = {}
        current = {k: _value(k) for k, *_ in TUNABLE["nfl"]}
        base = _nfl_eval({}, windows, cache)
    else:
        sys.path.insert(0, os.path.join(HERE, "hockey"))
        if "tune" in sys.modules:
            del sys.modules["tune"]
        windows = _nhl_windows()
        current = {k: _value(k) for k, *_ in TUNABLE["nhl"]}
        base = _nhl_eval({}, windows)
    print(f"{sport}: windows {windows}; trying {keys}", flush=True)
    results = []
    for cfg in _candidates(sport, keys):
        full = {**current, **cfg}
        if sport == "nfl":
            sc = _nfl_eval(full, windows, cache)
            g = {s: _nfl_gain(base[s], sc[s]) for s in windows}
            ok = all(g[s][0] <= -MIN_GAIN and g[s][1] <= .01 and .76 <= g[s][2] <= .84 for s in windows)
            score = max(g[s][0] for s in windows)
            row = dict(change=cfg, ok=ok, score=round(score, 5),
                       windows={str(s): dict(avg_change=round(g[s][0], 5), worst=round(g[s][1], 5), in80=round(g[s][2], 3)) for s in windows})
        else:
            sc = _nhl_eval(full, windows)
            d = {s: sc[s]["logloss"] - base[s]["logloss"] for s in windows}
            ok = all(d[s] <= -MIN_GAIN_NHL for s in windows)
            score = max(d.values())
            row = dict(change=cfg, ok=ok, score=round(score, 6),
                       windows={str(s): dict(logloss=round(sc[s]["logloss"], 5), change=round(d[s], 6)) for s in windows})
        results.append(row)
        print(json.dumps(row), flush=True)
        # restore current values before the next candidate
        import importlib
        for k, v in current.items():
            mod, attr = k.rsplit(".", 1)
            setattr(importlib.import_module(mod), attr, v)
    winners = sorted([r for r in results if r["ok"]], key=lambda r: r["score"])
    adopted = None
    if winners and not dry:
        adopted = winners[0]
        params = learned.load()
        params.setdefault(sport, {}).update(adopted["change"])
        learned.save(params)
    spec = {t[0]: t[4] for t in TUNABLE[sport]}
    entry = dict(date=dt.datetime.now().isoformat(timespec="minutes"), sport=sport, windows=windows,
                 tried=[dict(r, what=spec[next(iter(r["change"]))], was=current[next(iter(r["change"]))]) for r in results],
                 adopted=None if not adopted else dict(change=adopted["change"], was={k: current[k] for k in adopted["change"]},
                                                       what=spec[next(iter(adopted["change"]))], windows=adopted["windows"]),
                 baseline={str(k): v for k, v in base.items()}, next=nxt, minutes=round((time.time() - t0) / 60, 1), dry=dry)
    if not dry:
        log = _log()
        log.append(entry)
        json.dump(log, open(LOG, "w"), indent=1)
    print("adopted:", entry["adopted"] or "nothing (no candidate improved both windows)")
    return entry


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("sport", choices=["nfl", "nhl"])
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    run(a.sport, a.dry_run)
