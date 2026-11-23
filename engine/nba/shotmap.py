"""NBA shot maps (owner 2026-11-23: 'I want to create an NBA shot map per player per team' -> 'defense and offense heatmaps
like in nhl' -> 'have the player list next to the shotmap'). Display only, nothing tracked: this season's field-goal attempts
(nba.db `shots`, shots.py; regular season + playoffs) -> markets/shotmap_nba.json, read by the NBA page's game view 'Shot map'.

  grid     17 x 12 cells of 3 ft: x 0-50 across (rim at 25), y from the baseline (-4) to 31 (rim at 1.25; the top of the arc is 25), basket at the top
  teams    where each team shoots (off) and where its opponents shoot against it (dfn): its share of attempts in each cell over
           the league's share there, x100, lightly smoothed and pulled toward average with 300 pseudo-shots (about 3.5 games);
           cells where the league hardly shoots are left blank. Plus each zone's share of attempts and FG% (offense, and what the
           defense allows) and a few volume chips with ranks.
  players  every listed player's attempts this season (any team): zone mix, FG% by zone and his last 300 attempts packed two
           characters each (base64 of ((y + 4) x 51 + x) x 2 + made; y over 35 left out, over 31 not plotted).
Zones (ESPN coordinates, threes from ESPN's points attempted): rim = within 4 ft of the basket; paint = the rest of the 16-ft lane
up to the free-throw line; mid = other twos; corner 3 = threes below the arc's break (10 ft above the rim); above-break 3 = the
rest. Heaves (y over 40) are left out.
"""
import json
import os
import sqlite3

import numpy as np
import pandas as pd

COLS, ROWS = 17, 12
RIM_Y = 1.25
A = 300.0                                   # pseudo-shots toward the league's shot mix
ZONES = ("rim", "paint", "mid", "c3", "ab3")
B64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
N_DOTS = 300


def zone_of(x, y, three):
    d = np.hypot(x - 25, y - RIM_Y)
    return np.where(three == 1, np.where(y <= RIM_Y + 8.95, "c3", "ab3"),
                    np.where(d <= 4, "rim", np.where((np.abs(x - 25) <= 8) & (y <= RIM_Y + 13.75), "paint", "mid")))


def _smooth(g):
    k = np.array([1.0, 2.0, 1.0]) / 4
    for _ in range(2):
        for ax in (0, 1):
            g = np.apply_along_axis(lambda v: np.convolve(np.pad(v, 1, mode="edge"), k, mode="valid"), ax, g)
    return g


def _grid(df):
    g = np.zeros((ROWS, COLS))
    m = df[df.y <= 31]
    np.add.at(g, (((m.y.clip(lower=-4) + 4) // 3).astype(int).values, (m.x.clip(0, 50) // 3).astype(int).values), 1.0)
    return _smooth(g)


def _pack(df):
    m = df[df.y <= 35]
    v = (((m.y.clip(lower=-4) + 4) * 51 + m.x.clip(0, 50)) * 2 + m.made).astype(int)
    return "".join(B64[i // 64] + B64[i % 64] for i in v)


def _zones(df):
    out = {}
    n = len(df)
    for z in ZONES:
        s = df[df.zone == z]
        out[z] = [round(len(s) / n, 3) if n else 0, round(float(s.made.mean()), 3) if len(s) else None, int(len(s))]
    return out


def build(db, season, pids=()):
    con = sqlite3.connect(db)
    s = pd.read_sql(f"""SELECT s.*, g.date, g.home, g.away FROM shots s JOIN games g USING(game_id)
                        WHERE g.season = {int(season)} AND g.game_type IN (2, 3) AND s.y <= 40""", con)
    if s.empty:
        return None
    s["opp"] = np.where(s.team == s.home, s.away, s.home)
    s["zone"] = zone_of(s.x.values, s.y.values, s.three.values)
    s["pts"] = s.made * np.where(s.three == 1, 3, 2)
    lg_n = len(s)
    lg = _grid(s) / lg_n
    mask = lg < .25 * lg.mean()
    rel = lambda df: [[None if mask[i, j] else int(round(v * 100)) for j, v in enumerate(row)]
                      for i, row in enumerate((_grid(df) + A * lg) / ((len(df) + A) * np.where(lg > 0, lg, 1)))]
    teams, summ = {}, {}
    for t in sorted(set(s.team) & set(s.opp)):
        f, a = s[s.team == t], s[s.opp == t]
        ng = max(f.game_id.nunique(), 1)
        summ[t] = dict(fga=len(f) / ng, p3=f.three.mean(), rim=(f.zone == "rim").mean(), pps=f.pts.mean(),
                       a3=a.three.mean(), arim=(a.zone == "rim").mean(), apps=a.pts.mean())
        teams[t] = dict(n=int(ng), off=rel(f), dfn=rel(a), zo=_zones(f), zd=_zones(a),
                        fga=round(summ[t]["fga"], 1), p3=round(summ[t]["p3"], 3), rim=round(summ[t]["rim"], 3), pps=round(summ[t]["pps"], 3),
                        a3=round(summ[t]["a3"], 3), arim=round(summ[t]["arim"], 3), apps=round(summ[t]["apps"], 3))
    rk = lambda k, t: 1 + sum(1 for u in summ if summ[u][k] > summ[t][k])          # 1 = most
    for t in teams:
        teams[t]["rk"] = {k: rk(k, t) for k in ("fga", "p3", "rim", "pps", "a3", "arim", "apps")}
    players = {}
    s = s.sort_values(["date", "game_id"], kind="stable")
    for pid, x in s[s.player_id.isin({int(p) for p in pids})].groupby("player_id", sort=False):
        players[str(int(pid))] = dict(n=int(len(x)), gp=int(x.game_id.nunique()), fg=round(float(x.made.mean()), 3), z=_zones(x), s=_pack(x.tail(N_DOTS)))
    return dict(season=int(season), through=str(s.date.max())[:10], games=int(s.game_id.nunique()), shots=int(lg_n), cols=COLS, rows=ROWS,
                lg=dict(d=[[None if mask[i, j] else round(float(lg[i, j]) * 1000, 2) for j in range(COLS)] for i in range(ROWS)], z=_zones(s),
                        pps=round(float(s.pts.mean()), 3)),
                teams=teams, players=players)


def write(payload, db, season):
    try:
        import parlays as PL
        out = build(db, season, [p["id"] for p in payload.get("players", []) if p.get("id") is not None])
        if not out:
            print("shot maps: no shots stored yet")
            return
        # no timestamp: the file changes only when the shots or the listed players do (a live-branch file, publish.LIVE_JSON)
        json.dump(out, open(os.path.join(PL.SITE, "markets", "shotmap_nba.json"), "w"), separators=(",", ":"))
        print(f"shot maps: {out['games']} games, {out['shots']:,} shots, {len(out['teams'])} teams, {len(out['players'])} players")
    except Exception as e:                                    # noqa: BLE001 -- display only
        print(f"shot maps: skipped ({type(e).__name__}: {e})")
