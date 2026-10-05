"""
Weekly archive of pregame projections.

project.py only projects games that haven't kicked off, so each run's files lose
games as the week goes on. This keeps, per week, every game's LAST pregame
projection in projections/weeks/<season>_wk<NN>_{players,teams}.csv (+ dist,
matchups, meta) and rebuilds a frozen page projections/weeks/<season>_wk<NN>.html.
results.py grades these files once games are final.
"""

import datetime as dt
import json
import os

import pandas as pd

import report

WEEKS_DIR = os.path.join("projections", "weeks")
BLIND_WEEKS_DIR = os.path.join("projections", "blind", "weeks")     # market-blind build, same layout


def week_stem(season, week, base=WEEKS_DIR):
    return os.path.join(base, f"{season}_wk{week:02d}")


def _merge_csv(path, new, game_ids):
    new = new.drop(columns=[c for c in new.columns if c == "actual" or c.startswith("actual_")])
    if os.path.exists(path):
        old = pd.read_csv(path)
        new = pd.concat([old[~old.game_id.isin(game_ids)], new], ignore_index=True)
    new.to_csv(path, index=False)


def _load(path, default):
    return json.load(open(path)) if os.path.exists(path) else default


def update(stem, season, week, game_ids, source="live", sims=None, db="nfl.db", base=WEEKS_DIR):
    """Merge a run's output (stem_*) into the week's archive and rebuild its page."""
    os.makedirs(base, exist_ok=True)
    ws = week_stem(season, week, base)
    meta = _load(f"{ws}_meta.json", {})
    game_ids = set(game_ids)
    if source == "reconstructed":
        # Never replace a projection that was actually made before kickoff.
        game_ids -= {g for g, m in meta.items() if m["source"] == "live"}
    if not game_ids:
        return build_page(season, week, sims=sims, db=db, base=base)
    keep = lambda df: df[df.game_id.isin(game_ids)]
    _merge_csv(f"{ws}_players.csv", keep(pd.read_csv(f"{stem}_players.csv")), game_ids)
    _merge_csv(f"{ws}_teams.csv", keep(pd.read_csv(f"{stem}_teams.csv")), game_ids)

    dist = {k: v for k, v in _load(f"{ws}_dist.json", {}).items() if k.split("|")[0] not in game_ids}
    dist.update({k: v for k, v in _load(f"{stem}_dist.json", {}).items() if k.split("|")[0] in game_ids})
    json.dump(dist, open(f"{ws}_dist.json", "w"), separators=(",", ":"))

    m_old = _load(f"{ws}_matchups.json", {"players": {}, "dvp": {}})
    m_new = _load(f"{stem}_matchups.json", {"players": {}, "dvp": {}})
    players = {k: v for k, v in m_old["players"].items() if k.split("|")[0] not in game_ids}
    players.update({k: v for k, v in m_new["players"].items() if k.split("|")[0] in game_ids})
    json.dump({"players": players, "dvp": m_new.get("dvp") or m_old.get("dvp", {}),
               "rz": m_new.get("rz") or m_old.get("rz")},
              open(f"{ws}_matchups.json", "w"), separators=(",", ":"))

    stamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    for g in game_ids:
        meta[g] = {"source": source, "projected_at": stamp}
    json.dump(meta, open(f"{ws}_meta.json", "w"), indent=1)

    return build_page(season, week, sims=sims, db=db, base=base)


def build_page(season, week, sims=None, db="nfl.db", base=WEEKS_DIR):
    ws = week_stem(season, week, base)
    meta = _load(f"{ws}_meta.json", {})
    recon = sum(1 for v in meta.values() if v["source"] == "reconstructed")
    note = "Final pregame projections for each game (last run before kickoff)."
    if base == BLIND_WEEKS_DIR:
        note = "Market-blind build (no Vegas lines). " + note
    if recon:
        note += (f" {recon} game{'s' if recon != 1 else ''} reconstructed afterwards from data available"
                 " at kickoff, because the live projection wasn't saved.")
    return report.build(ws, sims=sims, db=db, subtitle=note,
                        nav=[("This week", "../index.html"), ("Results", "../results.html")])
