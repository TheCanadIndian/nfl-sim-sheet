"""NFL field map (owner 2026-11-23: 'I want a heat map for nfl like the nhl shot map or nba shot map').

Where each offense throws (depth x direction) and runs (lane), what each defense allows there (all targets, and targets to
outside WRs / slot WRs / TEs / RBs), and every player's targets and carries this season -> markets/fieldmap_nfl.json (a
live-branch file, publish.LIVE_JSON), read by nfl_page.js's 'Field map' game view. Raw counts only: the page turns them into
shares / rates against the league (pulled toward it on small samples). Display only: no number on the page uses it.

    python fieldmap.py [--season 2026] [--db nfl.db]
"""
import argparse
import json
import os
import sqlite3

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
# air-yard bands, bottom (behind the line) to top; nest inside the old matchup map's behind / short 1-9 / medium 10-19 / deep 20+
ROWS = [(-99, 0), (1, 4), (5, 9), (10, 14), (15, 19), (20, 29), (30, 99)]
COLS = ["left", "middle", "right"]
LANES = [("left", "end"), ("left", "tackle"), ("left", "guard"), ("middle", None), ("right", "guard"), ("right", "tackle"), ("right", "end")]
GRPS = ["OUT", "SLOT", "TE", "RB"]
B64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
MIN_LOOKS = 5                    # players listed: 5+ targets or 5+ carries this season


def _site():
    return os.environ.get("SIM_SITE") or os.path.join(HERE, "site")


def _enc(v):
    return B64[v // 64] + B64[v % 64]


def load(db, season):
    con = sqlite3.connect(db)
    pl = pd.read_sql(f"""SELECT game_id, week, game_date, posteam, defteam, play_type, qb_dropback, qb_scramble, qb_kneel, sack, two_point_attempt,
                                pass_location, air_yards, run_location, run_gap, receiver_player_id, receiver_player_name,
                                rusher_player_id, rusher_player_name, complete_pass, yards_gained, success
                         FROM plays WHERE season = {int(season)}""", con)
    pos = pd.read_sql(f"SELECT gsis_id, position, week FROM rosters WHERE season = {int(season)} AND gsis_id IS NOT NULL", con)
    games = pd.read_sql(f"SELECT game_id, gameday, home_team, away_team FROM games WHERE season = {int(season)}", con)
    try:
        from boxscore.matchups import depth_labels
        tg = pd.concat([games.rename(columns={"home_team": "team"})[["game_id", "team", "gameday"]],
                        games.rename(columns={"away_team": "team"})[["game_id", "team", "gameday"]]])
        lab = depth_labels(con, tg)
    except Exception as e:                                                      # noqa: BLE001
        print("  field map: no depth labels:", e)
        lab = pd.DataFrame(columns=["game_id", "team", "player_id", "align"])
    con.close()
    for c in ("qb_dropback", "qb_scramble", "qb_kneel", "sack", "two_point_attempt", "complete_pass", "success", "yards_gained"):
        pl[c] = pl[c].fillna(0)
    pos = pos.sort_values("week").drop_duplicates("gsis_id", keep="last").set_index("gsis_id").position.replace({"FB": "RB", "HB": "RB"})
    return pl, pos, lab


def build(db, season):
    pl, pos, lab = load(db, season)
    if pl.empty:
        return None
    # targets: a thrown pass with a receiver, a direction and a depth
    t = pl[(pl.play_type == "pass") & (pl.sack != 1) & (pl.two_point_attempt != 1) & pl.receiver_player_id.notna()
           & pl.pass_location.isin(COLS) & pl.air_yards.notna()].copy()
    t["row"] = 0
    for i, (lo, hi) in enumerate(ROWS):
        t.loc[t.air_yards.between(lo, hi), "row"] = i
    t["cell"] = t.row * 3 + t.pass_location.map({c: i for i, c in enumerate(COLS)})
    t["ay"] = t.air_yards.round().clip(-10, 60).astype(int)
    # who was targeted: position, and for WRs the depth-chart alignment for that game (X / Z outside, slot)
    al = lab[lab["align"].notna()].drop_duplicates(["game_id", "player_id"]).set_index(["game_id", "player_id"])["align"]
    t["pos"] = t.receiver_player_id.map(pos)
    t["al"] = [al.get((g, p)) for g, p in zip(t.game_id, t.receiver_player_id)]
    t["grp"] = np.where(t.pos == "WR", np.where(t.al == "Slot", "SLOT", np.where(t.al.isin(["X", "Z"]), "OUT", None)),
                        np.where(t.pos.isin(["TE", "RB"]), t.pos, None))
    # carries: designed runs (no scrambles / kneels) with a lane
    r = pl[(pl.play_type == "run") & (pl.qb_scramble != 1) & (pl.qb_kneel != 1) & (pl.two_point_attempt != 1)
           & pl.rusher_player_id.notna() & pl.run_location.isin(COLS)].copy()
    lane = {(a, b): i for i, (a, b) in enumerate(LANES)}
    r["cell"] = [lane.get((a, None if a == "middle" else (b if isinstance(b, str) else "?")), -1) for a, b in zip(r.run_location, r.run_gap)]
    r = r[r.cell >= 0]

    def cells(df, n, by=None):                                              # [[targets / carries, catches, yards, successes] a cell]
        if by is None:
            g = df.groupby("cell").agg(n=("cell", "size"), c=("complete_pass", "sum"), y=("yards_gained", "sum"), s=("success", "sum"))
            return [[int(g.n.get(i, 0)), int(g.c.get(i, 0)), int(g.y.get(i, 0)), int(g.s.get(i, 0))] for i in range(n)]
        g = df.groupby([by, "cell"]).agg(n=("cell", "size"), c=("complete_pass", "sum"), y=("yards_gained", "sum"), s=("success", "sum"))
        out = {}
        for (k, i), x in g.iterrows():
            out.setdefault(k, [[0, 0, 0, 0] for _ in range(n)])[int(i)] = [int(x.n), int(x.c), int(x.y), int(x.s)]
        return out

    for d in (t, r):
        d.loc[:, "complete_pass"] = d.complete_pass.astype(int)
    teams = sorted(set(pl.posteam.dropna()) | set(pl.defteam.dropna()))
    op, dp, orr, dr = cells(t, 21, "posteam"), cells(t, 21, "defteam"), cells(r, 7, "posteam"), cells(r, 7, "defteam")
    tg = t[t.grp.notna()]
    dg = {k: cells(tg[tg.grp == k], 21, "defteam") for k in GRPS}
    ngames = pl.groupby("posteam").game_id.nunique()
    # pass rush: sacks a dropback (the line's part of the 'holes' table)
    dbk = pl[(pl.qb_dropback == 1) & (pl.two_point_attempt != 1)]
    sko, skd = dbk.groupby("posteam").sack.agg(["size", "sum"]), dbk.groupby("defteam").sack.agg(["size", "sum"])
    skv = lambda g, tm: [int(g["size"].get(tm, 0)), int(g["sum"].get(tm, 0))]

    # chips: per game volume, depth, yards (offense and allowed), ranked 1 = most
    def side(col):
        a = t.groupby(col)
        b = r.groupby(col)
        return pd.DataFrame(dict(tpg=a.size() / ngames, adot=a.air_yards.mean(), deep=a.apply(lambda x: (x.air_yards >= 20).mean()),
                                 scr=a.apply(lambda x: (x.air_yards <= 0).mean()), ypt=a.yards_gained.mean(), cr=a.complete_pass.mean(),
                                 cpg=b.size() / ngames, ypc=b.yards_gained.mean(), out=b.apply(lambda x: x.cell.isin([0, 6]).mean()),
                                 sk=dbk.groupby(col).sack.mean()))
    ko, kd = side("posteam"), side("defteam")
    rko, rkd = ko.rank(ascending=False, method="min"), kd.rank(ascending=False, method="min")
    rnd = {"tpg": 1, "adot": 1, "deep": 3, "scr": 3, "ypt": 2, "cr": 3, "cpg": 1, "ypc": 2, "out": 3, "sk": 4}

    def chips(k, R, tm):
        if tm not in k.index:
            return None
        x = k.loc[tm]
        return {c: [round(float(x[c]), rnd[c]), int(R.loc[tm, c])] for c in rnd if pd.notna(x[c])}
    T = {}
    blank21, blank7 = [[0, 0, 0, 0]] * 21, [[0, 0, 0, 0]] * 7
    for tm in teams:
        T[tm] = dict(n=int(ngames.get(tm, 0)), o=dict(p=op.get(tm, blank21), r=orr.get(tm, blank7), sk=skv(sko, tm)),
                     d=dict(p=dp.get(tm, blank21), r=dr.get(tm, blank7), sk=skv(skd, tm)),
                     g={k: dg[k].get(tm, blank21) for k in GRPS}, ko=chips(ko, rko, tm), kd=chips(kd, rkd, tm))
    lg = dict(p=cells(t, 21), r=cells(r, 7), g={k: cells(tg[tg.grp == k], 21) for k in GRPS}, sk=[int(len(dbk)), int(dbk.sack.sum())])

    # players: 5+ targets or carries this season; every target packed 2 chars ((air yards + 10) x 3 + direction) x 2 + caught
    P = {}
    tc, rc = t.groupby("receiver_player_id"), r.groupby("rusher_player_id")
    looks = t.receiver_player_id.value_counts().add(r.rusher_player_id.value_counts(), fill_value=0)
    for pid in looks.index:
        nt = int(len(tc.get_group(pid))) if pid in tc.groups else 0
        nc = int(len(rc.get_group(pid))) if pid in rc.groups else 0
        if nt < MIN_LOOKS and nc < MIN_LOOKS:
            continue
        x = tc.get_group(pid).sort_values(["week"]) if nt else None
        y = rc.get_group(pid) if nc else None
        gp = len(set(x.game_id if nt else []) | set(y.game_id if nc else []))
        name = (x.receiver_player_name.iloc[-1] if nt else y.rusher_player_name.iloc[-1])
        e = dict(n=name, gp=gp, pos=pos.get(pid))
        if nt:
            col = x.pass_location.map({c: i for i, c in enumerate(COLS)}).astype(int)
            e["t"] = "".join(_enc(((a + 10) * 3 + c) * 2 + int(k)) for a, c, k in zip(x.ay, col, x.complete_pass))
            e["ty"] = int(x.yards_gained.sum())
        if nc:
            g = y.groupby("cell").yards_gained.agg(["size", "sum"])
            e["r"] = [[int(g["size"].get(i, 0)), int(g["sum"].get(i, 0))] for i in range(7)]
        P[pid] = e
    dates = pd.to_datetime(pl.game_date.dropna())
    return dict(season=int(season), through=dates.max().strftime("%b %-d") if os.name != "nt" else dates.max().strftime("%b %#d"),
                games=int(pl.game_id.nunique()), targets=int(len(t)), carries=int(len(r)), lg=lg, teams=T, players=P)


def write(db="nfl.db", season=None):
    if season is None:
        con = sqlite3.connect(db)
        season = int(con.execute("SELECT MAX(season) FROM plays").fetchone()[0])
        con.close()
    d = build(db, season)
    if not d:
        print("  field map: no plays for", season)
        return None
    fn = os.path.join(_site(), "markets", "fieldmap_nfl.json")
    os.makedirs(os.path.dirname(fn), exist_ok=True)
    with open(fn, "w", encoding="utf-8") as f:
        json.dump(d, f, separators=(",", ":"))
    print(f"  field map: {d['games']} games, {d['targets']:,} targets, {d['carries']:,} carries, {len(d['players'])} players -> {fn} "
          f"({os.path.getsize(fn) / 1024:.0f} KB)")
    return fn


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", type=int)
    ap.add_argument("--db", default="nfl.db")
    a = ap.parse_args()
    write(a.db, a.season)
