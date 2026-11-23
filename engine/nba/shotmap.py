"""NBA shot maps (owner 2026-11-23: 'I want to create an NBA shot map per player per team' -> 'defense and offense heatmaps
like in nhl' -> 'have the player list next to the shotmap' -> 'the heat map looks fairly blurry can I also look at misses vs
makes' -> 'both offense and defense' -> 'maybe be able to group by similar player archetype when viewing on defense'). Display
only, nothing tracked: this season's field-goal attempts (nba.db `shots`, shots.py; regular season + playoffs) ->
markets/shotmap_nba.json, read by the NBA page's game view 'Shot map'.

  grid     17 x 12 cells of 3 ft: x 0-50 across (rim at 25), y from the baseline (-4) to 31 (rim at 1.25; the top of the arc is
           25), basket at the top; one pass of a 3x3 binomial blur (two until the owner found it blurry)
  teams    (grids packed two base64 characters a cell, row by row, '__' = blank)
           off / dfn = where each team shoots / where its opponents shoot against it: its share of attempts in each cell over
           the league's share there, x100, pulled toward average with 300 pseudo-shots (about 3.5 games); offg / dfng = FG% in
           each cell over the league's FG% there, x100, pulled toward it with 20 pseudo-attempts a cell (makes vs misses);
           cells where the league hardly shoots are blank. Plus each zone's share of attempts and FG% (offense, and what the
           defense allows) and volume chips with ranks.
  arch     for the slate's teams, what the defense allows to each shooter archetype (markets/arch_nba.json, the player's
           latest): attempt shares (a) and FG% (g) per cell against how the whole league's shots by that archetype go (150
           pseudo-shots; 20 pseudo-attempts a cell), and zones (z); archetypes with 1,000+ shots league-wide.
  players  every listed player's attempts this season (any team): zone mix, FG% by zone, archetype and his last 300 attempts
           packed two characters each (base64 of ((y + 4) x 51 + x) x 2 + made; y over 35 left out, over 31 not plotted).
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
A_ARCH = 150.0                              # the same for one archetype's shots (smaller samples)
K_FG = 20.0                                 # pseudo-attempts a cell toward the baseline's FG% there
ZONES = ("rim", "paint", "mid", "c3", "ab3")
B64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
N_DOTS = 300


def zone_of(x, y, three):
    d = np.hypot(x - 25, y - RIM_Y)
    return np.where(three == 1, np.where(y <= RIM_Y + 8.95, "c3", "ab3"),
                    np.where(d <= 4, "rim", np.where((np.abs(x - 25) <= 8) & (y <= RIM_Y + 13.75), "paint", "mid")))


def _smooth(g, passes=1):
    k = np.array([1.0, 2.0, 1.0]) / 4
    for _ in range(passes):
        for ax in (0, 1):
            g = np.apply_along_axis(lambda v: np.convolve(np.pad(v, 1, mode="edge"), k, mode="valid"), ax, g)
    return g


def _grid(df, made=False):
    g = np.zeros((ROWS, COLS))
    m = df[df.y <= 31]
    np.add.at(g, (((m.y.clip(lower=-4) + 4) // 3).astype(int).values, (m.x.clip(0, 50) // 3).astype(int).values),
              m.made.values.astype(float) if made else 1.0)
    return _smooth(g)


def _enc(rows):
    return "".join("__" if v is None else B64[min(int(v), 4094) // 64] + B64[min(int(v), 4094) % 64] for row in rows for v in row)


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


def _base(df):
    """A baseline: attempt share and FG% per cell, and the cells too rare to show."""
    a = _grid(df)
    d = a / max(len(df), 1)
    return d, _grid(df, True) / np.where(a > 0, a, 1), d < .25 * d.mean()


def _rel(df, d, msk, a=A):
    g = (_grid(df) + a * d) / ((len(df) + a) * np.where(d > 0, d, 1))
    return [[None if msk[i, j] else int(round(g[i, j] * 100)) for j in range(COLS)] for i in range(ROWS)]


def _frel(df, fg, msk):
    g = (_grid(df, True) + K_FG * fg) / (_grid(df) + K_FG) / np.where(fg > 0, fg, 1)
    return [[None if msk[i, j] else int(round(g[i, j] * 100)) for j in range(COLS)] for i in range(ROWS)]


def build(db, season, pids=(), arch=None, slate=()):
    """arch: {player_id: archetype}; slate: the teams that get the archetype grids."""
    con = sqlite3.connect(db)
    s = pd.read_sql(f"""SELECT s.*, g.date, g.home, g.away FROM shots s JOIN games g USING(game_id)
                        WHERE g.season = {int(season)} AND g.game_type IN (2, 3) AND s.y <= 40""", con)
    if s.empty:
        return None
    s["opp"] = np.where(s.team == s.home, s.away, s.home)
    s["zone"] = zone_of(s.x.values, s.y.values, s.three.values)
    s["pts"] = s.made * np.where(s.three == 1, 3, 2)
    s["arch"] = s.player_id.map(arch or {})
    lg, lgfg, mask = _base(s)
    teams, summ = {}, {}
    for t in sorted(set(s.team) & set(s.opp)):
        f, a = s[s.team == t], s[s.opp == t]
        ng = max(f.game_id.nunique(), 1)
        summ[t] = dict(fga=len(f) / ng, p3=f.three.mean(), rim=(f.zone == "rim").mean(), pps=f.pts.mean(),
                       a3=a.three.mean(), arim=(a.zone == "rim").mean(), apps=a.pts.mean())
        teams[t] = dict(n=int(ng), off=_enc(_rel(f, lg, mask)), dfn=_enc(_rel(a, lg, mask)), offg=_enc(_frel(f, lgfg, mask)), dfng=_enc(_frel(a, lgfg, mask)),
                        zo=_zones(f), zd=_zones(a),
                        fga=round(summ[t]["fga"], 1), p3=round(summ[t]["p3"], 3), rim=round(summ[t]["rim"], 3), pps=round(summ[t]["pps"], 3),
                        a3=round(summ[t]["a3"], 3), arim=round(summ[t]["arim"], 3), apps=round(summ[t]["apps"], 3))
    rk = lambda k, t: 1 + sum(1 for u in summ if summ[u][k] > summ[t][k])          # 1 = most
    for t in teams:
        teams[t]["rk"] = {k: rk(k, t) for k in ("fga", "p3", "rim", "pps", "a3", "arim", "apps")}
    archs, lga = {}, {}
    for ar, x in s[s.arch.notna()].groupby("arch"):
        if len(x) < 1000:
            continue
        d, fg, msk = _base(x)
        lga[ar] = dict(n=int(len(x)), z=_zones(x))
        for t in [t for t in teams if t in set(slate)]:
            a = x[x.opp == t]
            archs.setdefault(t, {})[ar] = dict(n=int(len(a)), a=_enc(_rel(a, d, msk, A_ARCH)), g=_enc(_frel(a, fg, msk)), z=_zones(a))
    players = {}
    s = s.sort_values(["date", "game_id"], kind="stable")
    for pid, x in s[s.player_id.isin({int(p) for p in pids})].groupby("player_id", sort=False):
        ar = x.arch.iloc[-1]
        players[str(int(pid))] = dict(n=int(len(x)), gp=int(x.game_id.nunique()), fg=round(float(x.made.mean()), 3), z=_zones(x),
                                      s=_pack(x.tail(N_DOTS)), **({"ar": ar} if isinstance(ar, str) else {}))
    return dict(season=int(season), through=str(s.date.max())[:10], games=int(s.game_id.nunique()), shots=int(len(s)), cols=COLS, rows=ROWS,
                lg=dict(d=[[None if mask[i, j] else round(float(lg[i, j]) * 1000, 2) for j in range(COLS)] for i in range(ROWS)],
                        fg=[[None if mask[i, j] else round(float(lgfg[i, j]), 3) for j in range(COLS)] for i in range(ROWS)],
                        z=_zones(s), pps=round(float(s.pts.mean()), 3), arch=lga),
                teams=teams, players=players, arch=archs)


def arch_ids(db, site):
    """{player_id: archetype} from markets/arch_nba.json (normalized names) and nba.db's player names."""
    import parlays as PL
    try:
        names = json.load(open(os.path.join(site, "markets", "arch_nba.json")))
    except (OSError, ValueError):
        return {}
    p = pd.read_sql("SELECT player_id, MAX(name) AS name FROM players WHERE name IS NOT NULL GROUP BY player_id", sqlite3.connect(db))
    return {int(i): names[PL.jsnorm(n)] for i, n in zip(p.player_id, p.name) if PL.jsnorm(n) in names}


def write(payload, db, season):
    try:
        import parlays as PL
        slate = {t for g in payload.get("games", []) for t in (g.get("home"), g.get("away"))}
        out = build(db, season, [p["id"] for p in payload.get("players", []) if p.get("id") is not None], arch_ids(db, PL.SITE), slate)
        if not out:
            print("shot maps: no shots stored yet")
            return
        # no timestamp: the file changes only when the shots or the listed players do (a live-branch file, publish.LIVE_JSON)
        json.dump(out, open(os.path.join(PL.SITE, "markets", "shotmap_nba.json"), "w"), separators=(",", ":"))
        print(f"shot maps: {out['games']} games, {out['shots']:,} shots, {len(out['teams'])} teams, {len(out['players'])} players, "
              f"archetype grids for {len(out['arch'])} teams ({len(out['lg']['arch'])} archetypes)")
    except Exception as e:                                    # noqa: BLE001 -- display only
        print(f"shot maps: skipped ({type(e).__name__}: {e})")
