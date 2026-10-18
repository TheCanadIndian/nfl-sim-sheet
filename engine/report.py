#!/usr/bin/env python3
"""
Render a week's projection CSVs as one self-contained HTML page.

    python report.py projections/2026_wk03          # -> projections/2026_wk03.html
    python report.py --season 2026 --week 3

project.py calls build() automatically after writing the CSVs.
"""

import argparse
import datetime as dt
import json
import os
import re
import sqlite3

import numpy as np
import pandas as pd

Q = ["mean", "p10", "p25", "median", "p75", "p90"]


def _weather(db, game_ids):
    """Forecast/actual kickoff weather per game, as short display text."""
    if not os.path.exists(db):
        return {}
    try:
        w = pd.read_sql("SELECT game_id, temp, wind, precip, indoor, source FROM weather",
                        sqlite3.connect(db))
    except Exception:
        return {}
    out = {}
    for r in w[w.game_id.isin(game_ids)].itertuples():
        if r.indoor:
            out[r.game_id] = dict(text="Indoors", flag=False, src="indoor")
            continue
        parts = [f"{r.temp:.0f}°F", f"wind {r.wind:.0f} mph"]
        if r.precip >= 0.05:
            parts.append(f"rain {r.precip:.2f} in")
        flag = r.wind >= 15 or r.precip >= 0.05 or r.temp <= 32
        out[r.game_id] = dict(text=" · ".join(parts), flag=bool(flag),
                              src="forecast" if r.source == "forecast" else "observed")
    return out


def _clean(o):
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, float) and o != o:
        return None
    if hasattr(o, "item") and not isinstance(o, (str, bytes)):     # numpy scalars
        v = o.item()
        return None if isinstance(v, float) and v != v else v
    return o


def _kickoffs(db, game_ids):
    if not os.path.exists(db):
        return {}
    con = sqlite3.connect(db)
    g = pd.read_sql("SELECT game_id, gameday, gametime FROM games", con)
    out = {}
    for r in g[g.game_id.isin(game_ids)].itertuples():
        try:
            t = dt.datetime.strptime(f"{r.gameday} {r.gametime}", "%Y-%m-%d %H:%M")
            out[r.game_id] = (t.isoformat(), t.strftime("%a %b %d") + " · " +
                              t.strftime("%I:%M %p").lstrip("0") + " ET")
        except (TypeError, ValueError):
            out[r.game_id] = (str(r.gameday), str(r.gameday))
    return out


AGREE_STATS = ["pass_yds", "rush_yds", "rec_yds", "rec"]
AGREE_W = 0.45   # weight on the blind model's edge; fit on 2025, improved 2026 log-loss (README)


def _p_over(d, y):
    if not d:
        return None
    if "p" in d:
        k = d["o"] + np.arange(len(d["p"]))
        return float(np.asarray(d["p"])[k > y].sum())
    return float((np.asarray(d["q"]) > y).mean())


def blind_stem(stem):
    """projections/2026_wk04 -> projections/blind/2026_wk04; projections/weeks/X -> projections/blind/weeks/X."""
    d, b = os.path.dirname(stem), os.path.basename(stem)
    if os.path.basename(d) == "weeks":
        return os.path.join(os.path.dirname(d), "blind", "weeks", b)
    return os.path.join(d, "blind", b)


def alt_model(stem, match):
    """The market-blind version of the same games, for the per-game model switch (teams and
    player summaries; no distributions, to keep the page small)."""
    b = blind_stem(stem)
    if "blind" in os.path.normpath(stem).split(os.sep) or not os.path.exists(f"{b}_players.csv"):
        return None
    teams = pd.read_csv(f"{b}_teams.csv")
    players = pd.read_csv(f"{b}_players.csv")
    bd = json.load(open(f"{b}_dist.json")) if os.path.exists(f"{b}_dist.json") else {}
    tr = {}
    for r in teams.to_dict("records"):
        tr.setdefault(r["game_id"], {})[r["team"]] = {k: (round(v, 3) if isinstance(v, float) else v) for k, v in r.items()}
    pl = []
    for (gid, team, opp, name, pid, pos), d in players.groupby(["game_id", "team", "opp", "player", "player_id", "pos"], sort=False):
        key = f"{gid}|{team}|{name}"
        mp = match["players"].get(key, {})
        pl.append(dict(k=key, g=gid, t=team, o=opp, n=name, id=pid, pos=pos, d=bd.get(key, {}), lab=mp.get("label"), tags=mp.get("tags", []),
                       s={r["stat"]: [round(float(r[q]), 3 if r["stat"] in ("anytime_td", "first_td") else 2) for q in Q]
                          for r in d.to_dict("records")}))
    sp = f"{b}_sgp.json"
    return dict(teams=tr, players=pl, sgp=json.load(open(sp)) if os.path.exists(sp) else None)


# matchup map: where each offense goes (pass depth x direction, run lane) against how this defense does there.
# Plays from this season before this week, plus last season at 0.35 weight; defense rates shrunk toward the league.
PASS_ROWS = [("deep", "Deep (20+ yds)", 20, 99), ("inter", "Intermediate (10-19)", 10, 19), ("short", "Short (1-9)", 1, 9), ("behind", "Screens / behind line", -99, 0)]
RUN_LANES = [("left", "end", "L end"), ("left", "tackle", "L tackle"), ("left", "guard", "L guard"), ("middle", None, "Middle"),
             ("right", "guard", "R guard"), ("right", "tackle", "R tackle"), ("right", "end", "R end")]


def _fm_proj(players):
    """{(game id, team): [(player id, short name, pos, projected targets, projected carries)]} from the projection rows."""
    m = players[players.stat.isin(["tgt", "car"])].pivot_table(index=["game_id", "team", "player_id", "player", "pos"], columns="stat", values="mean").reset_index()
    out = {}
    for r in m.itertuples():
        nm = re.sub(r"\s+(Jr\.?|Sr\.?|II|III|IV|V)$", "", str(r.player)).split(" ")[-1]
        out.setdefault((r.game_id, r.team), []).append((r.player_id, nm, r.pos, float(getattr(r, "tgt", 0) or 0), float(getattr(r, "car", 0) or 0)))
    return out


def field_map(db, games, season, week, k=25, proj=None, kp=15):
    """proj: {(game id, team): [(player id, name, pos, projected targets, projected carries)]} -> each zone also lists who's
    likeliest to get the ball there: projected targets (carries) x his share of targets (carries) in that zone, his own
    history shrunk toward his position's typical spread (kp pseudo-targets)."""
    try:
        con = sqlite3.connect(db)
        pl = pd.read_sql(f"""SELECT season, week, posteam, defteam, pass, rush, qb_scramble, pass_location, air_yards, run_location, run_gap, success,
                                    receiver_player_id, rusher_player_id
                             FROM plays WHERE season >= {season - 1} AND (pass = 1 OR rush = 1) AND two_point_attempt = 0""", con)
        pos = dict(con.execute(f"SELECT gsis_id, position FROM rosters WHERE season >= {season - 1} AND gsis_id IS NOT NULL").fetchall())
    except Exception:                                                           # noqa: BLE001
        return {}
    pl = pl[(pl.season < season) | (pl.week < week)].copy()
    pl["w"] = np.where(pl.season == season, 1.0, .35)
    zone = pd.Series(None, index=pl.index, dtype=object)
    ps = (pl["pass"] == 1) & (pl.qb_scramble != 1) & pl.pass_location.notna() & pl.air_yards.notna()
    for key, _, lo, hi in PASS_ROWS:
        m = ps & pl.air_yards.between(lo, hi)
        zone[m] = key + "|" + pl.loc[m, "pass_location"]
    rs = (pl.rush == 1) & (pl.qb_scramble != 1) & pl.run_location.notna()
    gap = pl.run_gap.where(pl.run_location != "middle")
    zone[rs] = "run|" + pl.loc[rs, "run_location"] + "|" + gap[rs].fillna("")
    zone = zone.str.replace(r"run\|middle\|.*", "run|middle|", regex=True)
    pl["zone"], pl["kind"] = zone, np.where(ps, "pass", np.where(rs, "run", None))
    pl = pl[pl.zone.notna()]
    pl["ws"] = pl.w * pl.success.fillna(0)
    lg = pl.groupby("zone")[["w", "ws"]].sum()
    lg_sr = (lg.ws / lg.w).to_dict()
    off = pl.groupby(["posteam", "zone"])[["w", "ws"]].sum()
    off_tot = pl.groupby(["posteam", "kind"]).w.sum()
    de = pl.groupby(["defteam", "zone"])[["w", "ws"]].sum()
    zones = [f"{r}|{c}" for r, _, _, _ in PASS_ROWS for c in ("left", "middle", "right")] + [f"run|{a}|{b or ''}" for a, b, _ in RUN_LANES]
    # who gets the ball where: each player's (weighted) targets / carries by zone, and each position's typical spread
    tg = pl[(pl.kind == "pass") & pl.receiver_player_id.notna()].assign(pid=lambda x: x.receiver_player_id)
    ca = pl[(pl.kind == "run") & pl.rusher_player_id.notna()].assign(pid=lambda x: x.rusher_player_id)
    spread = {}
    for kind, t in (("pass", tg), ("run", ca)):
        pz = t.groupby(["pid", "zone"]).w.sum()
        t = t.assign(pos=t.pid.map(pos).replace({"FB": "RB", "HB": "RB"}))
        posz = t.groupby(["pos", "zone"]).w.sum()
        spread[kind] = (pz, pz.groupby(level=0).sum(), posz, posz.groupby(level=0).sum())

    def who(team_players, zone_list, kind):
        pz, pn, posz, posn = spread[kind]
        out_ = {z: [] for z in zone_list}
        for pid, name, ppos, tgt, car in team_players:
            vol = tgt if kind == "pass" else car
            if vol < .3:
                continue
            n = pn.get(pid, 0.0)
            ptot = posn.get(ppos, 0.0)
            for z in zone_list:
                own = pz.get((pid, z), 0.0)
                prior = (posz.get((ppos, z), 0.0) / ptot) if ptot else 1 / len(zone_list)
                e = vol * (own + kp * prior) / (n + kp)
                if e >= .2:
                    out_[z].append((name, round(e, 1)))
        return {z: sorted(v, key=lambda t: -t[1])[:3] for z, v in out_.items()}
    out = {}
    for g in games:
        for o, d in ((g["away"], g["home"]), (g["home"], g["away"])):
            cells = {}
            for z in zones:
                kind = "run" if z.startswith("run") else "pass"
                ow = off.loc[(o, z)].w if (o, z) in off.index else 0.0
                tot = off_tot.get((o, kind), 0.0)
                dw, dws = (de.loc[(d, z)].w, de.loc[(d, z)].ws) if (d, z) in de.index else (0.0, 0.0)
                L = lg_sr.get(z)
                if L is None:
                    continue
                da = (dws + k * L) / (dw + k)
                cells[z] = dict(s=round(ow / tot, 3) if tot else 0, da=round(da, 3), lg=round(L, 3), n=round(dw, 1))
            tp = (proj or {}).get((g["id"], o), [])
            if tp:
                for kind, zl in (("pass", [z for z in cells if not z.startswith("run")]), ("run", [z for z in cells if z.startswith("run")])):
                    for z, lst in who(tp, zl, kind).items():
                        cells[z]["who"] = lst
            out.setdefault(g["id"], {})[o] = dict(vs=d, cells=cells)
    return out


def agreement(stem, players, dists):
    """Vegas-anchored vs market-blind projections for the same player lines, with a calibrated
    chance of beating the Vegas-model median: p = P_vegas(over) + AGREE_W x (P_blind(over) - P_vegas(over))."""
    b = blind_stem(stem)
    if "blind" in os.path.normpath(stem).split(os.sep) or not os.path.exists(f"{b}_players.csv") or not os.path.exists(f"{b}_dist.json"):
        return []
    bp = pd.read_csv(f"{b}_players.csv")
    bd = json.load(open(f"{b}_dist.json"))
    v = players[players.stat.isin(AGREE_STATS)]
    m = v.merge(bp[["game_id", "player_id", "stat", "median", "mean"]], on=["game_id", "player_id", "stat"],
                suffixes=("", "_b"))
    out = []
    for r in m.itertuples():
        k = f"{r.game_id}|{r.team}|{r.player}"
        pv = _p_over(dists.get(k, {}).get(r.stat), r.median)
        pb = _p_over(bd.get(k, {}).get(r.stat), r.median)
        if pv is None or pb is None:
            continue
        out.append(dict(k=k, g=r.game_id, t=r.team, o=r.opp, n=r.player, pos=r.pos, stat=r.stat,
                        vmed=round(float(r.median), 1), bmed=round(float(r.median_b), 1),
                        pv=round(pv, 3), pb=round(pb, 3), p=round(pv + AGREE_W * (pb - pv), 3),
                        edge=round(pb - pv, 3)))
    return out


def build(stem, sims=None, db="nfl.db", subtitle=None, nav=None, out=None):
    players = pd.read_csv(f"{stem}_players.csv")
    teams = pd.read_csv(f"{stem}_teams.csv")
    props_path = f"{stem}_props.csv"
    props = pd.read_csv(props_path) if os.path.exists(props_path) else pd.DataFrame()

    season, week = re.match(r"(\d{4})_wk(\d+)", os.path.basename(stem)).groups()
    kick = _kickoffs(db, set(teams.game_id))
    wx = _weather(db, set(teams.game_id))

    games = []
    for gid, tg in teams.groupby("game_id", sort=False):
        home = tg[tg.home == 1].iloc[0]
        away = tg[tg.home == 0].iloc[0]
        games.append(dict(
            id=gid, home=home.team, away=away.team,
            sort=kick.get(gid, ("", ""))[0], kickoff=kick.get(gid, ("", ""))[1], weather=wx.get(gid),
            home_implied=round(float(home.vegas_implied), 2),
            away_implied=round(float(away.vegas_implied), 2),
        ))
    games.sort(key=lambda g: (g["sort"], g["id"]))

    team_rows = {}
    for r in teams.to_dict("records"):
        team_rows.setdefault(r["game_id"], {})[r["team"]] = {
            k: (round(v, 3) if isinstance(v, float) else v) for k, v in r.items()}

    dist_path = f"{stem}_dist.json"
    dists = json.load(open(dist_path)) if os.path.exists(dist_path) else {}
    mpath = f"{stem}_matchups.json"
    match = json.load(open(mpath)) if os.path.exists(mpath) else {"players": {}, "dvp": {}}
    spath = f"{stem}_sgp.json"
    sgp = json.load(open(spath)) if os.path.exists(spath) else None
    plist = []
    for (gid, team, opp, name, pid, pos), d in players.groupby(
            ["game_id", "team", "opp", "player", "player_id", "pos"], sort=False):
        key = f"{gid}|{team}|{name}"
        mp = match["players"].get(key, {})
        plist.append(dict(k=key, g=gid, t=team, o=opp, n=name, id=pid, pos=pos, d=dists.get(key, {}),
                          lab=mp.get("label"), tags=mp.get("tags", []),
                          s={r["stat"]: [round(float(r[q]), 3 if r["stat"] in ("anytime_td", "first_td") else 2) for q in Q]
                             for r in d.to_dict("records")}))

    props_list = [] if props.empty else \
        json.loads(props.astype(object).where(props.notna(), None).to_json(orient="records"))

    data = dict(season=int(season), week=int(week), sims=sims,
                generated=dt.datetime.now().strftime("%b %d, %Y %I:%M %p").replace(" 0", " "),
                games=games, teams=team_rows, players=plist, props=props_list, dvp=match["dvp"],
                rz=match.get("rz"), defout=match.get("defout", {}), agree=agreement(stem, players, dists), alt=alt_model(stem, match),
                sgp=sgp, spot=match.get("spot", {}), fmap=field_map(db, games, int(season), int(week), proj=_fm_proj(players)) if db else {},
                subtitle=subtitle, nav=nav or [])
    # NaN is not valid JSON: one missing value (e.g. a player with no depth label) would
    # stop the page's script entirely. Turn every NaN into null and refuse any that slip by.
    payload = json.dumps(_clean(data), separators=(",", ":"), allow_nan=False).replace("</", "<\\/")
    html = TEMPLATE.replace("__TITLE__", f"{season} Week {int(week)} Sim Sheet").replace("__DATA__", payload)
    out = out or f"{stem}.html"
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("stem", nargs="?", help="e.g. projections/2026_wk03")
    ap.add_argument("--season", type=int)
    ap.add_argument("--week", type=int)
    ap.add_argument("--dir", default="projections")
    ap.add_argument("--sims", type=int)
    ap.add_argument("--db", default="nfl.db")
    a = ap.parse_args()
    stem = a.stem or os.path.join(a.dir, f"{a.season}_wk{a.week:02d}")
    print("Wrote", build(stem, sims=a.sims, db=a.db))


TEMPLATE = r"""<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>__TITLE__</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@500;600;700&family=IBM+Plex+Sans:wght@400;500;600&display=swap">
<style>
:root{
  color-scheme:dark;
  --bg:#0A0F12; --surface:#111A1F; --sunk:#18242B; --ink:#EAF1F4; --muted:#95A6B0;
  --faint:#22313A; --accent:#36D399; --band:rgba(54,211,153,.26); --whisker:#6D808B;
  --away:#6CB4FF; --tip-bg:#EAF1F4; --tip-ink:#0A0F12; --good:#34D27A; --bad:#F04848; --sel:#6CB4FF;
  --g1:#9ED9B6; --g2:#4FD08A; --g3:#1EE06E; --r1:#E9A3A3; --r2:#F26464; --r3:#FF2E2E;
  --c-qb:#B4BCC2; --c-rb:#36D399; --c-wr:#6CB4FF; --c-te:#F2C14E;
  --display:"Barlow Condensed","Arial Narrow",Arial,sans-serif;
  --body:"IBM Plex Sans",system-ui,-apple-system,"Segoe UI",sans-serif;
}
*{box-sizing:border-box}
body{background:var(--bg);color:var(--ink);font:15px/1.5 var(--body);padding-inline:20px;padding-block:28px 56px;font-variant-numeric:tabular-nums}
.wrap{max-width:1560px;margin:0 auto;display:grid;gap:22px}
button,input,select{font:inherit;color:inherit}
:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.eyebrow{font-size:12px;letter-spacing:.09em;text-transform:uppercase;color:var(--muted);font-weight:500}
h1{font-family:var(--display);font-weight:700;font-size:44px;line-height:1;margin:6px 0 0;letter-spacing:.005em;text-transform:uppercase;text-wrap:balance}
h2{font-family:var(--display);font-weight:600;font-size:22px;line-height:1.1;margin:0;text-transform:uppercase;letter-spacing:.02em}
header{display:flex;flex-wrap:wrap;align-items:flex-end;justify-content:space-between;gap:16px}
.tabs{display:flex;gap:4px;background:var(--sunk);padding:4px;border-radius:8px}
.tabs button{border:0;background:none;padding:7px 14px;border-radius:6px;cursor:pointer;font-weight:500;color:var(--muted)}
.tabs button[aria-selected="true"]{background:var(--surface);color:var(--ink);box-shadow:0 1px 2px rgba(0,0,0,.08)}
.muted{color:var(--muted)}
.small{font-size:13px}

/* games view */
.games{display:grid;grid-template-columns:228px minmax(0,1fr);gap:20px;align-items:start}
.rail{display:grid;gap:2px;position:sticky;top:calc(env(safe-area-inset-top,0px) + 64px)}
.game-btn{display:grid;gap:4px;width:100%;text-align:left;border:1px solid transparent;background:none;border-radius:7px;padding:9px 12px;cursor:pointer}
.game-btn:hover{background:var(--sunk)}
.game-btn[aria-current="true"]{background:var(--surface);border-color:var(--faint)}
.game-btn .m{display:flex;justify-content:space-between;align-items:baseline;gap:8px}
.game-btn .teams{font-family:var(--display);font-weight:600;font-size:20px;letter-spacing:.02em}
.game-btn .line{font-size:12.5px;color:var(--muted)}
.mini{display:flex;height:4px;gap:2px}
.mini i{display:block;border-radius:1px}
.detail{display:grid;gap:18px;min-width:0}
.panel{background:var(--surface);border:1px solid var(--faint);border-radius:9px;padding:20px;display:grid;gap:18px;min-width:0}
.score{display:grid;grid-template-columns:1fr auto 1fr;align-items:end;gap:12px}
.side{display:grid;gap:2px}
.side.home{text-align:right}
.code{font-family:var(--display);font-weight:700;font-size:26px;letter-spacing:.03em;line-height:1}
.pts{font-family:var(--display);font-weight:700;font-size:64px;line-height:.9}
.at{font-family:var(--display);font-size:22px;color:var(--muted);padding-bottom:10px}
.winbar{display:grid;gap:6px}
.plink .caret{display:inline-block;width:12px;color:var(--muted);font-size:11px}
.plink.open{color:var(--sel)}
tr.opened td{background:color-mix(in srgb,var(--sel) 8%,transparent)}
tr.xrow td{background:color-mix(in srgb,var(--sel) 5%,transparent);padding:12px 14px 16px;white-space:normal}
.slider{display:grid;gap:12px;max-width:860px}
.sl-top{display:flex;flex-wrap:wrap;gap:8px 14px;align-items:center}
.sl-row{display:flex;align-items:center;gap:10px}
.sl-row label{font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);font-weight:600}
.sl-row input[type=range]{flex:1;accent-color:var(--sel);height:28px;min-width:120px}
.sl-step{border:1px solid var(--faint);background:var(--surface);color:var(--ink);border-radius:6px;width:30px;height:30px;cursor:pointer;font-size:16px}
.sl-val{font-family:var(--display);font-size:26px;min-width:64px;text-align:right}
.sl-out{display:grid;grid-template-columns:auto minmax(80px,1fr) auto;gap:14px;align-items:center}
.sl-side{display:grid;gap:0}
.sl-side span{font-size:12px;color:var(--muted);text-transform:uppercase;letter-spacing:.07em;font-weight:600}
.sl-side b{font-family:var(--display);font-size:34px;line-height:1}
.sl-side em{font-style:normal;color:var(--muted);font-size:14px}
.sl-side.under{text-align:right} .sl-side b{color:var(--ink)}
.sl-meter{height:12px;border-radius:6px;background:color-mix(in srgb,var(--bad) 55%,transparent);overflow:hidden}
.sl-meter i{display:block;height:100%;background:var(--good)}
.lean{font-size:11px;font-weight:700;margin-left:6px;white-space:nowrap;cursor:help}
/* degree shades: text */
.g1{color:var(--g1)!important} .g2{color:var(--g2)!important} .g3{color:var(--g3)!important}
.r1{color:var(--r1)!important} .r2{color:var(--r2)!important} .r3{color:var(--r3)!important}
.g3,.r3{font-weight:700}
/* degree shades: fills and row tints */
.sl-meter{display:flex;gap:2px;background:var(--sunk)!important}
.sl-meter i{display:block;height:100%}
.sl-meter .f-g1{background:var(--g1)} .sl-meter .f-g2{background:var(--g2)} .sl-meter .f-g3{background:var(--g3)}
.sl-meter .f-r1{background:var(--r1)} .sl-meter .f-r2{background:var(--r2)} .sl-meter .f-r3{background:var(--r3)} .sl-meter .f-n{background:var(--whisker)}
tr.bg-g1 td{background:color-mix(in srgb,var(--g1) 6%,transparent)} tr.bg-g2 td{background:color-mix(in srgb,var(--g2) 10%,transparent)} tr.bg-g3 td{background:color-mix(in srgb,var(--g3) 15%,transparent)}
tr.bg-r1 td{background:color-mix(in srgb,var(--r1) 6%,transparent)} tr.bg-r2 td{background:color-mix(in srgb,var(--r2) 10%,transparent)} tr.bg-r3 td{background:color-mix(in srgb,var(--r3) 15%,transparent)}
.gmsw{display:inline-flex;background:var(--sunk);border-radius:8px;padding:3px;margin-right:auto;margin-left:6px}
.gmsw button{border:0;background:none;padding:4px 11px;border-radius:6px;cursor:pointer;font-weight:600;font-size:12.5px;color:var(--muted)}
.gmsw button[aria-pressed="true"]{background:var(--surface);color:var(--ink);box-shadow:0 1px 2px rgba(0,0,0,.12)}
.ag{font-size:12px;font-weight:600;white-space:nowrap}
.ag.up,.ag.up2{color:var(--good)} .ag.dn,.ag.dn2{color:var(--bad)} .ag.ok{color:var(--muted);font-weight:500}
tr.agover td{background:color-mix(in srgb,var(--good) 11%,transparent)}
tr.agunder td{background:color-mix(in srgb,var(--bad) 10%,transparent)}
.pos-d{color:var(--good)} .neg-d{color:var(--bad)}
.agbar{position:relative;height:10px;min-width:120px;background:var(--sunk);border-radius:2px}
.agbar i{position:absolute;top:0;bottom:0;left:0;background:var(--accent);border-radius:2px}
.agbar b{position:absolute;left:50%;top:-3px;bottom:-3px;width:1px;background:var(--ink)}
.ftd{display:grid;gap:2px;border-top:1px solid var(--faint);padding-top:12px}
.ftd-h{display:flex;justify-content:space-between;align-items:baseline;gap:8px;margin-bottom:4px}
.ftd-r{display:grid;grid-template-columns:minmax(0,1fr) 90px 46px 52px 36px;gap:10px;align-items:center;font-size:14px;padding:3px 0}
.ftd-r .n{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ftd-r .pbar{min-width:0;height:8px}
.ftd-r b{text-align:right;font-weight:600} .ftd-r > span:nth-child(4),.ftd-r > span:nth-child(5){text-align:right}
.ftd-r.top .n{font-weight:600}
.star{color:var(--accent);margin-right:5px}
.winbar .bar{display:flex;height:10px;gap:2px}
.winbar .bar i{display:block;border-radius:2px}
.winbar .lbl{display:flex;justify-content:space-between;font-size:13px}
.box{width:100%;border-collapse:collapse}
.box td{padding:6px 4px;border-bottom:1px solid var(--faint)}
.box tr:last-child td{border-bottom:0}
.box td:first-child{text-align:left;font-weight:500}
.box td:nth-child(2){text-align:center;color:var(--muted);font-size:13px}
.box td:last-child{text-align:right;font-weight:500}
.boxhead{display:grid;grid-template-columns:1fr auto 1fr;font-family:var(--display);font-weight:600;font-size:16px;letter-spacing:.04em}
.boxhead span:last-child{text-align:right}
.two{display:grid;grid-template-columns:minmax(0,1.1fr) minmax(0,1fr);gap:22px}
.phead{display:flex;flex-wrap:wrap;justify-content:space-between;align-items:center;gap:12px}
.seg{display:flex;flex-wrap:wrap;gap:4px}
.seg button{border:1px solid var(--faint);background:none;border-radius:999px;padding:4px 12px;cursor:pointer;font-size:13.5px}
.seg button[aria-pressed="true"]{background:var(--ink);color:var(--bg);border-color:var(--ink)}
.key{display:flex;flex-wrap:wrap;gap:16px;font-size:12.5px;color:var(--muted);align-items:center}
.key span{display:inline-flex;align-items:center;gap:6px}
.key .kw{width:22px;height:2px;background:var(--whisker)}
.key .kb{width:22px;height:10px;background:var(--band);border-radius:2px}
.key .km{width:2px;height:14px;background:var(--ink)}

/* tables */
.tw{overflow-x:auto}
table.t{width:100%;border-collapse:collapse;font-size:14px}
.t th{font-size:11px;letter-spacing:.07em;text-transform:uppercase;color:var(--muted);font-weight:500;text-align:right;padding:6px 8px;border-bottom:1px solid var(--faint);white-space:nowrap;vertical-align:bottom}
.t td{padding:7px 8px;border-bottom:1px solid var(--faint);text-align:right;white-space:nowrap}
.t th.l,.t td.l{text-align:left}
.t td.name{font-weight:500;width:1%;padding-right:10px}   /* shrink to the longest name so the position tag sits beside it */
.t td.name small{display:block;font-weight:400;color:var(--muted);font-size:12px}
.t .grp td{border-bottom:0;padding-top:16px;text-align:left;font-family:var(--display);font-weight:600;font-size:17px;letter-spacing:.04em}
.t td.big{font-weight:600}
.t th.sc{width:32%;min-width:130px}
.pos{display:inline-block;font-size:11px;font-weight:600;letter-spacing:.05em;color:var(--muted);border:1px solid var(--faint);border-radius:4px;padding:0 5px}
.strip{position:relative;height:20px;min-width:130px;cursor:default}
.strip .g{position:absolute;top:0;bottom:0;width:1px;background:var(--faint)}
.strip .w{position:absolute;top:9px;height:2px;background:var(--whisker)}
.strip .b{position:absolute;top:4px;height:12px;background:var(--band);border-radius:2px}
.strip .m{position:absolute;top:2px;width:2px;height:16px;background:var(--ink);transform:translateX(-1px)}
.axis{position:relative;height:14px;min-width:130px;font-size:11px;letter-spacing:0;text-transform:none}
.axis span{position:absolute;bottom:0}
.pbar{position:relative;height:10px;min-width:100px;background:var(--sunk);border-radius:2px}
.pbar i{position:absolute;top:0;bottom:0;left:0;background:var(--accent);border-radius:2px}
.dv{position:relative;height:12px;min-width:150px;background:var(--sunk);border-radius:2px}
.dv i{position:absolute;top:0;bottom:0;border-radius:2px}
.dv .c{position:absolute;left:50%;top:-3px;bottom:-3px;width:1px;background:var(--muted)}
.controls{display:flex;flex-wrap:wrap;gap:12px;align-items:center}
.controls label{font-size:13px;color:var(--muted)}
select,input[type=search]{background:var(--surface);border:1px solid var(--faint);border-radius:6px;padding:6px 10px}
input[type=search]{min-width:200px}
.empty{padding:28px 8px;color:var(--muted)}
.betgrid{display:grid;gap:8px}
.betrow{display:grid;grid-template-columns:86px 1fr;gap:8px;align-items:center}
.bets{display:flex;flex-wrap:wrap;gap:6px}
.bet{display:inline-flex;align-items:baseline;gap:6px;border:1px solid var(--faint);background:var(--sunk);color:var(--ink);border-radius:8px;padding:6px 10px;cursor:pointer;font:inherit;font-size:13.5px}
.bet b{font-family:var(--display);font-size:17px;font-weight:700}
.bet small{color:var(--muted);font-size:12px}
.bet i{font-style:normal;color:var(--accent);font-weight:700;margin-left:2px}
.bet.on{border-color:var(--accent);background:rgba(54,211,153,.12)}
.bet:disabled{cursor:default}
.bet:disabled i{display:none}
@media (max-width:520px){.betrow{grid-template-columns:1fr;gap:4px}.bets{display:grid;grid-template-columns:1fr 1fr;gap:6px}.bet{justify-content:space-between;gap:4px;padding:5px 8px;font-size:12.5px;min-width:0}.bet b{font-size:15px}.bet small{display:none}.bet span{white-space:nowrap}}
.sgp-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));gap:14px}
.sgp{border:1px solid var(--faint);border-radius:9px;padding:14px;display:grid;gap:10px;align-content:start;background:var(--bg)}
.sgp h3{font-family:var(--display);font-weight:600;font-size:19px;text-transform:uppercase;letter-spacing:.02em;margin:0;display:flex;flex-wrap:wrap;justify-content:space-between;gap:4px 8px;align-items:baseline}
.sgp h3 small{font-family:var(--body);font-size:12px;text-transform:none;letter-spacing:0;color:var(--muted);font-weight:400}
.sgp ol{margin:0;padding-left:22px;display:grid;gap:3px;font-size:14px}
.sgp li .r{float:right;color:var(--muted);font-size:12.5px;margin-left:8px}
.sgp li.after{list-style:none;margin-left:-22px;border-top:1px dashed var(--faint);padding-top:4px;margin-top:2px;font-size:12px;color:var(--muted)}
.sgp table{width:100%;border-collapse:collapse;font-size:13.5px}
.sgp td,.sgp th{padding:4px 6px;text-align:right;border-top:1px solid var(--faint)}
.sgp th{font-weight:500;color:var(--muted);font-size:12px}
.sgp td:first-child,.sgp th:first-child{text-align:left}
.sgp td:first-child{white-space:nowrap}
.outlook{display:flex;flex-wrap:wrap;gap:6px 18px;align-items:baseline;font-size:13.5px;padding:10px 12px;border:1px solid var(--faint);border-radius:8px;background:var(--sunk)}
.outlook .good{color:var(--good)}
.sgp.exp{border-color:var(--accent);box-shadow:inset 0 0 0 1px var(--band)}
.gbtns{display:flex;flex-wrap:wrap;gap:6px;align-items:center}
.gbtn{font-size:12.5px;border:1px solid var(--faint);border-radius:999px;padding:3px 10px;color:var(--ink);text-decoration:none}
.gbtn:hover{border-color:var(--accent);color:var(--accent)}
.sp-list{display:grid;gap:0}
.exp3{font-family:var(--body);font-size:10.5px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;color:#F2C14E;border:1px solid currentColor;border-radius:4px;padding:1px 5px;margin-left:6px;vertical-align:3px}
.gsh{display:flex;flex-wrap:wrap;align-items:baseline;gap:4px 14px;font-size:15px}.gsh .big{font-size:19px;font-weight:700}
.scsw{margin:4px 0 2px}.scsw button small{opacity:.7;margin-left:5px;font-size:11.5px}
.scgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(330px,1fr));gap:12px}
.scteam{display:flex;align-items:baseline;gap:8px;margin:14px 0 6px}.scteam h3{margin:0;font-size:16px}
.scc{border:1px solid var(--faint);border-radius:10px;padding:10px 12px;background:var(--sunk);display:grid;gap:8px;align-content:start;min-width:0}
.scc .ladh b{font-size:15px}.ladc.up{color:var(--good);border-color:color-mix(in srgb,var(--good) 50%,transparent)}.ladc.dn{color:var(--bad);border-color:color-mix(in srgb,var(--bad) 50%,transparent)}
.sct{width:100%;border-collapse:collapse;font-size:13px;font-variant-numeric:tabular-nums}.sct th{font-size:11px;font-weight:600;color:var(--muted);text-align:right;padding:2px 4px}.sct th:first-child,.sct td:first-child{text-align:left}
.sct td{text-align:right;padding:4px;border-top:1px solid var(--faint);white-space:nowrap}.sct caption{text-align:left;font-size:12px;color:var(--muted);padding-bottom:2px}
.sct td a{color:inherit}.sct small{display:block;font-size:10.5px;color:var(--muted)}.sct .mainl{font-size:10px;color:var(--muted);margin-left:4px}
.sct .e2{color:var(--good);font-weight:700}.sct .e1{color:var(--good)}.sct .neg{color:var(--muted)}.sct .far{opacity:.55}
.lad{border-top:1px solid var(--faint);padding:10px 0}.ladh{display:flex;flex-wrap:wrap;align-items:baseline;gap:4px 8px}
.ladc{display:inline-block;border:1px solid var(--faint);border-radius:999px;padding:0 8px;font-size:11.5px;color:var(--muted);white-space:nowrap}.ladc.soft{color:var(--good);border-color:color-mix(in srgb,var(--good) 50%,transparent)}
.ladr{display:flex;flex-wrap:wrap;gap:6px;margin-top:6px}.ladg{display:grid;column-gap:32px}@media(min-width:900px){.ladg{grid-template-columns:1fr 1fr}}.ladw{border-top:1px solid var(--faint);padding:10px 0 2px}.ladw summary{cursor:pointer;color:var(--muted);font-size:13px}.lad .ladh>*{min-width:0}.lru{border:1px solid var(--faint);border-radius:8px;padding:3px 9px;font-size:13px;background:var(--sunk);white-space:nowrap}.lru i{font-style:normal;color:var(--muted);margin-left:4px}
.fm-off{border-top:1px solid var(--faint);padding-top:14px;margin-top:14px}.fm-off:first-of-type{border-top:0;margin-top:4px;padding-top:0}
.fm-off h3{margin:0 0 10px;font-family:var(--display);font-size:20px;letter-spacing:.02em;text-transform:uppercase}.fm-off h3 small{font-family:var(--body);font-size:13px;color:var(--muted);text-transform:none;letter-spacing:0;font-weight:500}
.fm-wrap{display:grid;grid-template-columns:minmax(0,1.15fr) minmax(0,1fr);gap:18px;align-items:start}
.fm-grid{display:grid;grid-template-columns:auto repeat(3,minmax(0,1fr));gap:5px;align-items:stretch}
.fm-grid .rl{font-size:11px;letter-spacing:.05em;text-transform:uppercase;color:var(--muted);align-self:center;padding-right:4px;max-width:92px;line-height:1.25}
.fm-grid .cl{font-size:11px;letter-spacing:.05em;text-transform:uppercase;color:var(--muted);text-align:center}
.fm-c{border-radius:8px;padding:8px 6px;text-align:center;border:1px solid var(--faint);background:var(--sunk);min-height:58px;display:flex;flex-direction:column;justify-content:center}
.fm-c b{font-family:var(--display);font-size:22px;line-height:1}.fm-c small{display:block;font-size:11.5px;margin-top:3px;color:var(--ink);opacity:.85}
.fm-c.out{outline:2px dashed #F2C14E;outline-offset:-2px}.fm-c .ob{display:block;font-size:10px;font-weight:700;color:#F2C14E;margin-top:2px;letter-spacing:.02em}
.fm-miss{font-size:13px;margin:0 0 10px;padding:8px 10px;border:1px dashed #F2C14E;border-radius:8px;color:var(--ink)}.fm-miss small{display:block;color:var(--muted);font-size:12px;margin-top:2px}
.fm-c .wh{display:block;margin-top:4px;font-size:11px;line-height:1.3;color:var(--ink);opacity:.92;overflow:hidden;text-overflow:ellipsis}.fm-c .wh em{font-style:normal;color:var(--muted)}
.fm-run .fm-c .wh{font-size:10px}
@media (max-width:760px){.fm-c .wh{font-size:10px}.fm-c .wh span:nth-child(n+2){display:none}}
.fm-c.thin{background-image:repeating-linear-gradient(45deg,transparent 0 6px,rgba(127,127,127,.12) 6px 12px)}
.fm-los{grid-column:1/-1;border-top:2px solid var(--accent);margin:2px 0;position:relative}.fm-los span{position:absolute;right:0;top:-9px;font-size:10px;color:var(--accent);background:var(--surface);padding:0 4px;letter-spacing:.06em}
.fm-run{display:grid;grid-template-columns:repeat(7,minmax(0,1fr));gap:4px;margin-top:4px}.fm-run .cl{grid-row:2}
.fm-run .fm-c{min-height:52px;padding:6px 2px}.fm-run .fm-c b{font-size:18px}
.fm-sub{font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);margin:2px 0 6px}
.fm-list{display:grid;gap:6px;margin:0;padding:0;list-style:none;font-size:14px}.fm-list li{line-height:1.4}.fm-list li b{font-weight:700}
.fm-list .up{color:var(--good);font-weight:800}.fm-list .dn{color:var(--bad);font-weight:800}
.fm-key{display:flex;flex-wrap:wrap;gap:6px 14px;font-size:12.5px;color:var(--muted);margin:2px 0 6px}.fm-key i{display:inline-block;width:12px;height:12px;border-radius:3px;vertical-align:-2px;margin-right:5px}
@media (max-width:760px){.fm-wrap{grid-template-columns:minmax(0,1fr)}.fm-run{gap:3px}.fm-run .fm-c b{font-size:15px}.fm-run .fm-c small{font-size:10px}.fm-grid .rl{max-width:64px;font-size:10px}}
.sp-row{display:grid;grid-template-columns:auto 1fr;gap:12px;align-items:start;padding:12px 0;border-top:1px solid var(--faint)}
.sp-row:first-child{border-top:0}
.tierb{font-family:var(--display);font-weight:700;font-size:12px;letter-spacing:.06em;text-transform:uppercase;border-radius:4px;padding:3px 7px;white-space:nowrap;margin-top:3px}
.tierb.t1{background:var(--accent);color:var(--bg)}
.tierb.t2{background:#F2C14E;color:var(--bg)}
.tierb.t3{background:var(--sunk);color:var(--muted);border:1px solid var(--faint)}
.sp-name{display:flex;flex-wrap:wrap;gap:4px 10px;align-items:baseline}
.sp-name b{font-family:var(--display);font-size:21px;font-weight:700;letter-spacing:.01em}
.sp-name span{font-size:12px;color:var(--muted);text-transform:uppercase;letter-spacing:.04em}
.sp-facts{font-size:13.5px;margin-top:3px}
.sp-why{font-size:13.5px;color:#F2C14E;margin-top:2px}
.sp-why.neg{color:var(--r2)}
.gsw{flex-wrap:nowrap;overflow-x:auto;scrollbar-width:none}
.gsw::-webkit-scrollbar{display:none}
.gsw button{white-space:nowrap}
@media (max-width:520px){.sp-row{grid-template-columns:1fr;gap:4px}.tierb{justify-self:start;margin-top:0}.sp-name b{font-size:19px}}
.legsrc.both{color:var(--good);border-color:var(--good)}
.legsrc{font-size:11px;color:var(--muted);border:1px solid var(--faint);border-radius:4px;padding:0 4px;margin-left:4px;white-space:nowrap}
@media (max-width:520px){.sgp-grid{grid-template-columns:1fr}}
.plink{border:0;background:none;padding:0;font:inherit;font-weight:inherit;color:inherit;cursor:pointer;text-align:left;text-decoration:underline;text-decoration-color:var(--faint);text-underline-offset:3px}
.plink:hover{text-decoration-color:var(--accent)}
.fo-res{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}
.fo-side{display:grid;gap:2px;background:var(--sunk);border-radius:8px;padding:14px 16px}
.fo-p{font-family:var(--display);font-weight:700;font-size:52px;line-height:1}
.fo-o{font-family:var(--display);font-weight:600;font-size:24px;color:var(--muted)}
.t tr.cur td{background:var(--sunk)}
#fo-player{background:var(--surface);border:1px solid var(--faint);border-radius:6px;padding:6px 10px;width:min(340px,100%)}
#fo-line{background:var(--surface);border:1px solid var(--faint);border-radius:6px;padding:6px 10px;width:90px}
.mm{display:inline-flex;align-items:center;gap:3px;font-size:11.5px;font-weight:500;border:1px solid currentColor;border-radius:4px;padding:0 5px;margin:2px 4px 0 0;white-space:nowrap;cursor:help}
.mm.up{color:var(--good)} .mm.down{color:var(--bad)} .mm.strong{font-weight:700}
.chips{display:flex;flex-wrap:wrap}
.mgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(96px,1fr));gap:8px}
.mcell{background:var(--sunk);border-radius:7px;padding:8px 10px;display:grid;gap:1px}
.mcell .v{font-family:var(--display);font-weight:700;font-size:24px;line-height:1.1}
.mcell .v.up{color:var(--good)} .mcell .v.down{color:var(--bad)}
.dmiss{margin:0;font-size:13px;color:var(--ink)} .dmiss b{color:var(--r2);font-weight:600}
.mlist{display:grid;gap:6px;margin:0;padding:0;list-style:none}
.mlist li{display:flex;flex-wrap:wrap;gap:4px 10px;align-items:baseline}
.mlist .who{font-weight:600;min-width:170px}
html{-webkit-text-size-adjust:100%;text-size-adjust:100%}
.note{font-size:12.5px;color:var(--muted);margin:0;max-width:75ch}
.mmeta{display:none}
.pchips{display:flex;flex-wrap:wrap;gap:8px;margin:0 0 12px}
details.pfold{margin-bottom:14px}
details.pfold > summary{list-style:none;cursor:pointer;display:flex;justify-content:space-between;align-items:center;gap:10px;padding:14px 18px;border:1px solid var(--faint);border-radius:12px;background:var(--surface)}
details.pfold > summary::-webkit-details-marker{display:none}
details.pfold > summary h2{margin:0}
details.pfold > summary .chev{color:var(--muted);transition:transform .15s}
details.pfold[open] > summary{border-bottom-left-radius:0;border-bottom-right-radius:0;border-bottom-color:transparent}
details.pfold:not([open]) > summary .chev{transform:rotate(-90deg)}
details.pfold > section.panel{border-top-left-radius:0;border-top-right-radius:0;border-top:0;margin-top:0}
.kps{display:none}
.atgp td a{color:var(--accent);text-decoration:none;white-space:nowrap}
.atgp td a:hover{text-decoration:underline}
.rr{border:1px solid var(--faint);border-radius:9px;padding:10px 12px;margin:6px 0 12px;background:var(--bg)}
details.how{font-size:12.5px;color:var(--muted)} details.how summary{cursor:pointer;color:var(--ink);font-size:13px;margin-bottom:4px}
@media (max-width:640px){
  /* player tables become two-line rows: name + chips + a small details line on the left, the key number big on the right */
  .pt,.pt tbody{display:block;width:100%}
  .pt thead{display:none}
  .pt tbody tr{display:grid;grid-template-columns:minmax(0,1fr) auto;column-gap:12px;align-items:center;padding:9px 2px;border-top:1px solid var(--faint)}
  .pt tbody tr.grp,.pt tbody tr.xrow{display:block;padding:0;border:0}
  .pt tbody tr.grp td,.pt tbody tr.xrow td{display:block;padding:10px 2px 4px!important}
  .pt td{display:none;border:0!important;padding:0!important;text-align:left}
  .pt td.name{display:block;grid-column:1;min-width:0;width:auto;white-space:normal}
  .pt td.kp{display:block;grid-column:2;grid-row:1;text-align:right;font-family:var(--display);font-size:22px;font-weight:700;line-height:1}
  .pt td.kp b{font-weight:700}
  .pt td.kp .kps{display:block;font-family:var(--body);font-size:11.5px;font-weight:500;color:var(--muted);margin-top:4px;line-height:1.3}
  .pt .mmeta{display:block;color:var(--muted);font-size:12px;font-weight:400;margin-top:3px;line-height:1.35}
  .pt .mmeta b{color:var(--ink);font-weight:600}
  .tw:has(> .pt){overflow:visible}
  /* pairs: stacked cards */
  .pairs,.pairs tbody{display:block;width:100%}
  .pairs thead{display:none}
  .pairs tbody tr{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:4px 10px;padding:9px 2px;border-top:1px solid var(--faint)}
  .pairs td{border:0!important;padding:0!important;white-space:normal!important}
  .pairs td:first-child{grid-column:1 / -1}
  .pairs td.big{grid-column:1;font-size:14px;text-align:left}
  .pairs td.big::after{content:" chance";color:var(--muted);font-weight:400;font-size:12px}
  .pairs td.pay{grid-column:1;grid-row:3;text-align:left;color:var(--muted);font-size:12.5px}
  .pairs td.pay::after{content:" fair payout"}
  .pairs td.add{grid-column:2;grid-row:2 / span 2;align-self:center}
  .pairs tr.slate td{grid-column:1 / -1}
  .tw:has(> .pairs){overflow:visible}
  .atgp td.pay::after{content:" payout"}
  .atgp td.edge{grid-column:1;text-align:left;font-size:12.5px}
  .atgp td.edge::after{content:" edge";font-weight:400;color:var(--muted)}
  .atgp td.add{grid-row:2 / span 3}
  /* chips wrap instead of running off the edge; Kalshi pattern chips live in the tap-open panel on phones */
  .pt td.name .pat-high_total,.pt td.name .pat-cheap_over,.pt td.name .pat-low_rung,.pt td.name .pat-low_total{display:none}
  .pt td.name .vtag{white-space:normal;line-height:1.35;margin:3px 4px 0 0}
  /* first-TD list: full names, no bar */
  .ftd-r{grid-template-columns:minmax(0,1fr) 46px 52px 36px;gap:8px}
  .ftd-r .pbar{display:none}
  .ftd-r .n{white-space:normal}
  .ftd-r .n .small{display:block}
}
.dvbar{position:relative;height:10px;min-width:150px;background:var(--sunk);border-radius:2px}
.dvbar i{position:absolute;top:0;bottom:0;border-radius:2px}
.dvbar .c{position:absolute;left:50%;top:-3px;bottom:-3px;width:1px;background:var(--muted)}
.flagbtn{border:1px solid var(--faint);background:none;border-radius:999px;padding:4px 12px;cursor:pointer;font-size:13.5px}
.flagbtn[aria-pressed="true"]{background:var(--ink);color:var(--bg);border-color:var(--ink)}
.wx-flag{color:var(--bad);font-weight:600}
.sitenav{display:flex;flex-wrap:wrap;gap:4px 18px;font-size:13.5px}
.sitenav a{color:var(--muted);text-decoration:none;font-weight:500}
.sitenav a:hover{color:var(--ink);text-decoration:underline;text-underline-offset:3px}
.sitenav a[aria-current="page"]{color:var(--ink)}
.mix{display:flex;height:12px;gap:2px;min-width:150px}
.mix i{display:block;border-radius:2px}
.swatch{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:5px;vertical-align:-1px}
.mm.tagn{color:var(--muted);cursor:help}
.t td .rzchips{min-width:240px;max-width:360px;white-space:normal;justify-content:flex-start}
.t td,.t th{padding-inline:6px}
.rzline{font-family:var(--body);font-size:12.5px;font-weight:400;letter-spacing:0;margin-left:12px;display:inline-flex;flex-wrap:wrap;gap:4px 8px;align-items:center;color:var(--muted)}
.rzline .mm{margin:0}
footer{font-size:13px;color:var(--muted);max-width:70ch;line-height:1.55}
#tip{position:fixed;z-index:10;pointer-events:none;background:var(--tip-bg);color:var(--tip-ink);font-size:12.5px;line-height:1.45;padding:7px 10px;border-radius:5px;max-width:260px}
#tip b{font-weight:600}
@media (max-width:980px){.two{grid-template-columns:1fr}}
@media (max-width:1100px) and (min-width:861px){.games{grid-template-columns:1fr}.rail{position:static;display:flex;overflow-x:auto;gap:6px;padding-bottom:4px}.game-btn{min-width:180px;width:auto;flex:0 0 auto}}
@media (max-width:860px){
  .games{grid-template-columns:1fr}
  /* phones: a sticky, swipeable row of compact game chips (several visible at once) */
  .rail{position:sticky;top:var(--tbh,0px);z-index:19;display:flex;overflow-x:auto;gap:6px;padding:8px 0;margin:-8px 0 0;background:var(--bg);scrollbar-width:none;-webkit-overflow-scrolling:touch;scroll-snap-type:x proximity}
  .rail::-webkit-scrollbar{display:none}
  .game-btn{width:auto;flex:0 0 auto;min-width:0;padding:6px 10px;border:1px solid var(--faint);border-radius:999px;scroll-snap-align:start}
  .game-btn[aria-current="true"]{border-color:var(--accent);background:var(--sunk)}
  .game-btn .teams{font-size:15px}
  .game-btn .m{gap:6px}
  .game-btn > .line, .game-btn .mini{display:none}
  .pts{font-size:48px}
  h1{font-size:34px}
}
@media (prefers-reduced-motion:no-preference){.game-btn,.tabs button,.seg button{transition:background .15s,color .15s}}
</style>

<div class="wrap">
  <nav class="sitenav" id="sitenav" aria-label="Site"></nav>
  <header>
    <div>
      <div class="eyebrow" id="sub"></div>
      <h1 id="h1"></h1>
      <p class="note" id="subnote" hidden></p>
    </div>
    <div class="tabs" role="tablist" aria-label="View">
      <button role="tab" id="tab-games" data-view="games">Games</button>
      <button role="tab" id="tab-board" data-view="board">Player board</button>
      <button role="tab" id="tab-dvp" data-view="dvp">Defense vs position</button>
      <button role="tab" id="tab-parlays" data-view="parlays">Parlays</button>
      <button role="tab" id="tab-fair" data-view="fair">Fair odds</button>
      <button role="tab" id="tab-agree" data-view="agree">Model agreement</button>
      <button role="tab" id="tab-props" data-view="props">Your lines</button>
    </div>
  </header>
  <main id="main"></main>
  <footer id="foot"></footer>
</div>
<div id="tip" hidden></div>
<script type="application/json" id="data">__DATA__</script>
<script>
const D = JSON.parse(document.getElementById('data').textContent);
const [MEAN,P10,P25,MED,P75,P90] = [0,1,2,3,4,5];
const STATS = {
  pass_yds:{label:'Passing yards',min:50}, att:{label:'Pass attempts',min:10}, cmp:{label:'Completions',min:5},
  pass_td:{label:'Passing TDs',min:.3,dec:1}, ints:{label:'Interceptions',min:.2,dec:1},
  rush_yds:{label:'Rushing yards',min:5}, car:{label:'Carries',min:1.5},
  rec_yds:{label:'Receiving yards',min:5}, rec:{label:'Receptions',min:.8}, tgt:{label:'Targets',min:1},
  anytime_td:{label:'Anytime TD',min:.04,prob:true},
  first_td:{label:'First TD scorer',min:.01,prob:true},
};
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const f0 = v => Math.round(v).toString();
const f1 = v => (Math.round(v*10)/10).toFixed(1);
const pct = p => Math.round(p*100) + '%';
const pct1 = p => p < .1 ? (Math.round(p*1000)/10).toFixed(1) + '%' : Math.round(p*100) + '%';
const american = p => { p = Math.min(Math.max(p,1e-4),1-1e-4); const o = p>=.5 ? -100*p/(1-p) : 100*(1-p)/p; return (o>0?'+':'') + Math.round(o); };
const niceCeil = v => { if (v<=0) return 1; const e = 10**Math.floor(Math.log10(v)); for (const m of [1,1.2,1.5,2,2.5,3,4,5,6,8,10]) if (m*e >= v - 1e-9) return m*e; };
const fmtTick = v => Math.abs(v) >= 10 || Number.isInteger(v) ? f0(v) : f1(v);

const state = {script:'all', view:'games', game:D.games[0]?.id, group:'receiving', bstat:'rec_yds', bpos:'All', q:'', bflag:false, dpos:'WR', rzscope:'two'};
try { Object.assign(state, JSON.parse(localStorage.getItem('simsheet-ui') || '{}')); } catch (e) {}
if (!D.games.some(g => g.id === state.game)) state.game = D.games[0]?.id;
if (location.hash === '#books') location.replace('markets.html#books');          // Book odds moved to the Markets page
if (['games','board','dvp','fair','props','agree','parlays'].includes(location.hash.slice(1))) state.view = location.hash.slice(1);
if (state.view === 'wfair') state.view = 'games';      // the Experimental tab is gone (graded in the background on the Profit page)
if (state.view === 'props' && !D.props.length) state.view = 'games';
if (state.view === 'rz') { state.view = 'games'; state.group = 'td'; }
const save = () => { try { localStorage.setItem('simsheet-ui', JSON.stringify({view:state.view, gtab:state.gtab, group:state.group, bstat:state.bstat, bpos:state.bpos, fo:state.fo, dpos:state.dpos, rzscope:state.rzscope})); } catch (e) {} };

document.getElementById('h1').textContent = `Week ${D.week} Sim Sheet`;
if (D.subtitle){ const n = document.getElementById('subnote'); n.textContent = D.subtitle; n.hidden = false; }
document.getElementById('sitenav').innerHTML = (D.nav || []).map(([label, href, cur]) => `<a href="${esc(href)}"${cur ? ' aria-current="page"' : ''}>${esc(label)}</a>`).join('');
document.getElementById('sub').textContent = `${D.season} season · ${D.games.length} game${D.games.length===1?'':'s'}` + (D.sims ? ` · ${D.sims.toLocaleString()} simulations each` : '') + ` · built ${D.generated}`;
document.getElementById('foot').innerHTML = `Every number comes from simulated games anchored to the Vegas spread and total. Medians are the 50/50 point: half the simulations land above, half below. Bars show where 8 in 10 simulations landed (10th to 90th percentile), with the darker band holding the middle half. Fair odds are the model's no-vig price. Rebuild this page each time <code>project.py</code> runs.`;
if (!D.props.length) document.getElementById('tab-props').hidden = true;
if (!D.sgp) { document.getElementById('tab-parlays').hidden = true; if (state.view === 'parlays') state.view = 'games'; }

function scaleFor(rows, stat){
  let lo = 0, hi = 0;
  for (const p of rows){ const s = p.s[stat]; if (!s) continue; lo = Math.min(lo, s[P10]); hi = Math.max(hi, s[P90]); }
  lo = lo < 0 ? -niceCeil(-lo) : 0;
  hi = niceCeil(hi);
  return {lo, hi, x: v => ((v - lo) / (hi - lo)) * 100};
}
function ticks(sc){ const mid = (sc.lo + sc.hi) / 2; return sc.lo < 0 ? [sc.lo, 0, sc.hi] : [0, mid, sc.hi]; }
function axis(sc){
  return `<div class="axis">${ticks(sc).map((t,i,a) => `<span style="left:${sc.x(t)}%;transform:translateX(${i===0?'0':i===a.length-1?'-100%':'-50%'})">${fmtTick(t)}</span>`).join('')}</div>`;
}
function strip(s, sc, label){
  const x = sc.x, g = ticks(sc).map(t => `<i class="g" style="left:${x(t)}%"></i>`).join('');
  const tip = `<b>${esc(label)}</b><br>Median ${f1(s[MED])} · mean ${f1(s[MEAN])}<br>Middle half ${f1(s[P25])} to ${f1(s[P75])}<br>8 in 10 games ${f1(s[P10])} to ${f1(s[P90])}`;
  return `<div class="strip" tabindex="0" data-tip="${esc(tip)}" aria-label="${esc(label)}: median ${f1(s[MED])}, 10th to 90th percentile ${f1(s[P10])} to ${f1(s[P90])}">${g}`
    + `<i class="w" style="left:${x(s[P10])}%;width:${Math.max(x(s[P90])-x(s[P10]),.5)}%"></i>`
    + `<i class="b" style="left:${x(s[P25])}%;width:${Math.max(x(s[P75])-x(s[P25]),.5)}%"></i>`
    + `<i class="m" style="left:${x(s[MED])}%"></i></div>`;
}
function pbar(p, max){ return `<div class="pbar" role="img" aria-label="${pct(p)}"><i style="width:${Math.min(p/max,1)*100}%"></i></div>`; }
const posTag = p => `<span class="pos">${esc(p)}</span>`;
const posCell = p => `<span class="pos" title="${p.lab ? 'Depth chart' : 'Not on the current depth chart'}">${esc(p.lab || p.pos)}</span>`;
const chip = t => `<span class="mm ${t.dir > 0 ? 'up' : 'down'}${t.strong ? ' strong' : ''}" title="${esc(t.text)}">${t.dir > 0 ? '▲' : '▼'} ${esc(t.short || t.kind)}</span>`;
// Shade by degree: g3/r3 strong, g2/r2 moderate, g1/r1 slight, '' neutral.
const shadeP = p => p >= .65 ? 'g3' : p >= .56 ? 'g2' : p >= .51 ? 'g1' : p <= .35 ? 'r3' : p <= .44 ? 'r2' : p <= .49 ? 'r1' : '';
const shadeEdge = e => e >= .06 ? 'g3' : e >= .04 ? 'g2' : e >= .02 ? 'g1' : e <= -.06 ? 'r3' : e <= -.04 ? 'r2' : e <= -.02 ? 'r1' : '';
const shadeRank = r => r <= 4 ? 'g3' : r <= 8 ? 'g2' : r <= 12 ? 'g1' : r >= 29 ? 'r3' : r >= 25 ? 'r2' : r >= 21 ? 'r1' : '';
const shadeRatio = x => x >= 1.2 ? 'g3' : x >= 1.12 ? 'g2' : x >= 1.05 ? 'g1' : x <= 1/1.2 ? 'r3' : x <= 1/1.12 ? 'r2' : x <= 1/1.05 ? 'r1' : '';
const chips = p => (p.tags && p.tags.length) ? `<div class="chips">${p.tags.slice(0, 2).map(chip).join('')}</div>` : '';

/* ---------------- games view ---------------- */
function spreadText(g){
  const d = g.home_implied - g.away_implied, fav = d >= 0 ? g.home : g.away;
  const total = g.home_implied + g.away_implied;
  return (Math.abs(d) < .25 ? 'Pick' : `${fav} −${f1(Math.abs(d)).replace('.0','')}`) + ` · O/U ${f1(total).replace('.0','')}`;
}
function gameList(){
  return D.games.map(g => {
    const T = D.teams[g.id], hw = T[g.home].win_prob;
    return `<button class="game-btn" data-game="${esc(g.id)}" aria-current="${g.id===state.game}">
      <span class="m"><span class="teams">${esc(g.away)} @ ${esc(g.home)}</span><span class="line">${esc(g.kickoff.replace(/ \w+ \d+ · /, ' ').replace(/ ET$/, ''))}</span></span>
      <span class="line">${esc(spreadText(g))}</span>
      <span class="mini" aria-hidden="true"><i style="flex:${1-hw};background:var(--away)"></i><i style="flex:${hw};background:var(--accent)"></i></span>
    </button>`;
  }).join('');
}
const GROUPS = {
  passing:{label:'Passing', stat:'pass_yds', keep:p => p.s.att && p.s.att[MEAN] >= 5,
    head:['Cmp / Att','','Median','TD','INT'],
    cells:(p,sc) => [`${f0(p.s.cmp[MED])} / ${f0(p.s.att[MED])}`, strip(p.s.pass_yds, sc, `${p.n} passing yards`), `<b>${f0(p.s.pass_yds[MED])}</b>`, f1(p.s.pass_td[MEAN]), f1(p.s.ints[MEAN])]},
  rushing:{label:'Rushing', stat:'rush_yds', keep:p => p.s.car && p.s.car[MEAN] >= 1,
    head:['Carries','','Median'],
    cells:(p,sc) => [f0(p.s.car[MED]), strip(p.s.rush_yds, sc, `${p.n} rushing yards`), `<b>${f0(p.s.rush_yds[MED])}</b>`]},
  receiving:{label:'Receiving', stat:'rec_yds', keep:p => p.s.tgt && p.s.tgt[MEAN] >= 1,
    head:['Tgt','Rec','','Median'],
    cells:(p,sc) => [f0(p.s.tgt[MED]), f0(p.s.rec[MED]), strip(p.s.rec_yds, sc, `${p.n} receiving yards`), `<b>${f0(p.s.rec_yds[MED])}</b>`]},
  td:{label:'Touchdowns', stat:'anytime_td', keep:p => p.s.anytime_td && p.s.anytime_td[MEAN] >= .03,
    head:['','Anytime','Fair odds','First TD','Fair odds','Red zone'],
    cells:(p,sc) => [pbar(p.s.anytime_td[MEAN], sc.hi), `<b>${pct(p.s.anytime_td[MEAN])}</b>`, american(p.s.anytime_td[MEAN]),
      p.s.first_td ? `<b>${pct1(p.s.first_td[MEAN])}</b>` : '–', p.s.first_td ? american(p.s.first_td[MEAN]) : '–', rzTags(p)]},
};
function playerTable(g){
  const G = GROUPS[state.group];
  const rows = PL(g).filter(p => G.keep(p));
  const by = p => G.stat === 'anytime_td' ? p.s.anytime_td[MEAN] : p.s[G.stat][MED];
  let sc;
  if (G.stat === 'anytime_td') { const m = Math.max(.1, ...rows.map(by)); sc = {hi: Math.min(1, niceCeil(m))}; }
  else sc = scaleFor(rows, G.stat);
  const scIdx = G.head.indexOf('');
  const headCells = G.head.map((h,i) => i === scIdx
    ? `<th class="sc l">${G.stat === 'anytime_td' ? `<div class="axis"><span style="left:0">0%</span><span style="left:100%;transform:translateX(-100%)">${pct(sc.hi)}</span></div>` : axis(sc)}</th>`
    : `<th>${h}</th>`).join('');
  // best price at the viewer's books (mkt.js), the tab's main bet
  const bo = !!(window.MKT && MKT.hasProps && MKT.hasProps(g.id)), bk = {passing: 'pass_yds', rushing: 'rush_yds', receiving: 'rec_yds', td: 'td'}[state.group];
  const ncol = G.head.length + 2 + (bo ? 1 : 0);
  // phones: the key number on the right, the rest in one small line under the name
  const kIdx = {passing: 2, rushing: 2, receiving: 3, td: 1}[state.group];
  const rng = s => s ? ` · usually ${f0(s[P10])}–${f0(s[P90])} yds` : '';
  const mmeta = p => { const s = p.s, m = {
      passing: () => `${f0(s.cmp[MED])}/${f0(s.att[MED])} · ${f1(s.pass_td[MEAN])} TD · ${f1(s.ints[MEAN])} INT${rng(s.pass_yds)}`,
      rushing: () => `${f0(s.car[MED])} carries${rng(s.rush_yds)}`,
      receiving: () => `${f0(s.tgt[MED])} tgt · ${f0(s.rec[MED])} rec${rng(s.rec_yds)}`,
      td: () => `fair ${american(s.anytime_td[MEAN])}${s.first_td ? ` · first TD ${pct1(s.first_td[MEAN])}` : ''}`}[state.group];
    return `<span class="mmeta">${esc(p.lab || p.pos)} · ${m ? m() : ''}</span>`; };
  let body = '';
  for (const team of [g.away, g.home]){
    const tr = rows.filter(p => p.t === team).sort((a,b) => by(b) - by(a));
    const oppTeam = team === g.home ? g.away : g.home;
    body += `<tr class="grp"><td colspan="${ncol}">${esc(team)}${state.group === 'td' ? rzTeamLine(team, oppTeam) : ''}</td></tr>`;
    body += tr.length ? tr.map(p => `<tr class="${state.open === p.k ? 'opened' : ''}">${nameCell(p, GROUP_STAT[state.group], mmeta(p) + (bo ? `<span class="pcm">${MKT.prop(g.id, p.n, bk)}</span>` : ''))}<td class="l">${posCell(p)}</td>${G.cells(p, sc).map((c,i) => `<td class="${i===scIdx?'l':''}${i===kIdx?' kp':''}">${c}</td>`).join('')}${bo ? `<td class="l pcd">${MKT.prop(g.id, p.n, bk)}</td>` : ''}</tr>`
        + (state.open === p.k ? `<tr class="xrow"><td colspan="${ncol}">${sliderPanel(p)}</td></tr>` : '')).join('')
      : `<tr><td colspan="${ncol}" class="l muted small">No players with meaningful volume.</td></tr>`;
  }
  return `<div class="tw"><table class="t pt"><thead><tr><th class="l">Player</th><th class="l">Pos</th>${headCells}${bo ? '<th class="l pcd" title="Best price at your sportsbooks (choose them under the game bets); over/unders show the side we rate better. Green = our chance beats it by 5%+">Best odds</th>' : ''}</tr></thead><tbody>${body}</tbody></table></div>`;
}
// Most likely first TD scorers in this game (backtest 2024-26: calibrated; top pick scored first 14%).
function firstTdBlock(g){
  const ps = PL(g).filter(p => p.s.first_td).sort((a, b) => b.s.first_td[MEAN] - a.s.first_td[MEAN]).slice(0, 5);
  if (!ps.length) return '';
  const hi = Math.max(.15, ps[0].s.first_td[MEAN]);
  return `<div class="ftd"><div class="ftd-h"><span class="eyebrow">Most likely first TD scorer</span><span class="small muted">chance · fair odds · anytime</span></div>
    ${ps.map((p, i) => `<div class="ftd-r${i === 0 ? ' top' : ''}"><span class="n">${i === 0 ? '<span class="star">★</span>' : ''}${esc(p.n)} <span class="small muted">${esc(p.t)} · ${esc(p.lab || p.pos)}</span></span><span class="pbar"><i style="width:${p.s.first_td[MEAN] / hi * 100}%"></i></span><b>${pct1(p.s.first_td[MEAN])}</b><span class="muted">${american(p.s.first_td[MEAN])}</span><span class="small muted">${pct(p.s.anytime_td[MEAN])}</span></div>`).join('')}
  </div>`;
}
// Per-game model switch: "vegas" (this page's model) or "blind" (market-blind numbers embedded as D.alt).
state.gm = state.gm || {};
const isBlind = g => state.gm[g.id] === 'blind' && D.alt && D.alt.teams[g.id];
const TM = g => isBlind(g) ? D.alt.teams[g.id] : D.teams[g.id];
const PL = g => (isBlind(g) ? D.alt.players : D.players).filter(p => p.g === g.id);
function modelSwitch(g){
  if (!D.alt || !D.alt.teams[g.id]) return '';
  const b = !!isBlind(g);
  return `<span class="gmsw" role="group" aria-label="Model for this game"><button data-gmodel="vegas" aria-pressed="${!b}">With Vegas</button><button data-gmodel="blind" aria-pressed="${b}">Market-blind</button></span>`;
}
/* ---------------- parlays (sgp.py: joint simulation, 5-7 legs, no unders, main lines only) ---------------- */
const SRC_TIP = {model: "Our median line (Kalshi has no line for this yet): check your book's number", Vegas: 'Vegas total'};
const srcTag = L => L.src === 'Kalshi' ? '' : `<span class="legsrc" title="${esc(SRC_TIP[L.src] || L.src)}">${L.src === 'model' ? 'our line' : esc(L.src)}</span>`;
// the other model's chance for the same leg (from its saved distributions / win chance)
const LEG_STAT = {rec: 'rec', rec_yds: 'rec_yds', rush_yds: 'rush_yds', pass_yds: 'pass_yds', anytime_td: 'tds'};
let SGP_G = null, SGP_BLIND = false;
function otherP(L){
  const g = SGP_G; if (!g || !D.alt) return null;
  if (L.kind === 'win'){ const T = (SGP_BLIND ? D.teams : D.alt.teams)[g.id]; return T && T[L.team] ? T[L.team].win_prob : null; }
  const st = LEG_STAT[L.kind]; if (!st || L.pid == null) return null;
  const p = (SGP_BLIND ? D.players : D.alt.players).find(x => x.g === g.id && String(x.id) === String(L.pid));
  const d = p && p.d && p.d[st]; if (!d) return null;
  const r = probs(d, L.kind === 'anytime_td' ? .5 : L.line); return r.over;
}
function otherTag(L){
  const q = otherP(L); if (q == null) return '';
  const who = SGP_BLIND ? 'Vegas model' : 'Market-blind';
  return q >= Math.min(.5, L.p) - .02 ? ` <span class="legsrc both" title="${who}: ${pct(q)}. Both models like this leg (the other model has it at least as high, up to 50%).">both ✓</span>`
                 : ` <span class="legsrc" title="${who} has this leg at ${pct(q)}">${SGP_BLIND ? 'vegas' : 'blind'} ${pct(q)}</span>`;
}
const legLine = (L, script, n) => `<li${n ? ` value="${n}"` : ''}>${esc(L.lab)}${srcTag(L)}${otherTag(L)}${L.mean != null ? ` <span class="small muted" title="Projected average vs median">avg ${L.mean} · med ${L.med}</span>` : ''}<span class="r">${pct(L.p)}${script && L.p_script != null && Math.abs(L.p_script - L.p) >= .02 ? ` → ${pct(L.p_script)}` : ''}${L.price ? ` · ${window.MKT && MKT.px ? MKT.px(L.price, 'kalshi') : Math.round(L.price * 100) + '¢'}` : ''}</span></li>`;
// "Build in Gambly": opens Gambly's chat with the parlay typed in (its own ask-Gambly links use these params);
// Gambly turns it into bet slips at the user's books (Hard Rock, Kalshi, Novig, ...). Unofficial: may change.
function gamblyLink(legs, k, game){
  const ls = legs.slice(0, k).map(L => L.lab).join(', ');
  const prompt = `Build a ${k}-leg ${game ? 'same game parlay for ' + game : 'parlay'}: ${ls}`;
  return 'https://gambly.com/chat?' + new URLSearchParams({entry: 'ask-gambly', prompt, autoSubmit: '1'}).toString();
}
// a card's legs as slip legs (player key looked up on this page; game legs read the saved margin / total)
function cardSlipLegs(legs, gid){
  const g = D.games.find(x => x.id === gid); if (!g) return [];
  const pool = (state.gm[gid] === 'blind' && D.alt ? D.alt.players : D.players);
  return legs.map(L => {
    const base = {sport: 'nfl', g: gid, glab: `${g.away} @ ${g.home}`, lab: L.lab, p: L.p, model: state.gm[gid] === 'blind' ? 'blind' : 'vegas', draws: `nfl/draws/${gid}.json`};
    if (L.kind === 'win') return {...base, id: `nfl|${gid}|win|${L.team}`, stat: 'win', home: L.team === g.home, side: 'over'};
    if (L.kind === 'total') return {...base, id: `nfl|${gid}|total|over|${L.line}`, stat: 'total', line: L.line, side: 'over'};
    const p = pool.find(x => x.g === gid && String(x.id) === String(L.pid)); if (!p) return null;
    const stat = L.kind === 'anytime_td' ? 'tds' : L.kind, line = L.kind === 'anytime_td' ? .5 : L.line;
    return {...base, id: `nfl|${gid}|${p.k}|${stat}|over|${line}|${base.model}`, stat, line, side: 'over', pk: p.k};
  }).filter(Boolean);
}
const gamblyBtns = (legs, ks, game) => `<div class="gbtns"><span class="small muted">Build in Gambly:</span>${ks.map(k => `<a class="gbtn" href="${esc(gamblyLink(legs, k, game))}" target="_blank" rel="noopener" title="Opens Gambly's chat with these ${k} legs typed in; pick your book there">${k} legs ↗</a>`).join('')}</div>`;
// Parlay chance = (each leg's market-anchored chance) x the sims' correlation lift (joint / legs multiplied).
// research/sgp_pairs_test.py, 2026 wk 5-6 (8,966 same-game Kalshi pairs): where the sims saw legs rise together
// (lift 1.15+) both hit 19.6% vs 19.6% married, 15.7% multiplying the market, 14.4% sims alone; anti-correlated
// (lift <= 0.87): 7.2% vs 8.5% married, 10.4% market, 5.1% sims. Legs no market prices at their line keep our chance.
const PLAYER_OF = lab => { const m = /^(.*?) (over|under|anytime|to score)\b/i.exec(lab || ''); return m ? m[1] : null; };
function legQ(L, gid){
  const name = PLAYER_OF(L.lab), M = name && window.MKT && MKT.legMarket ? MKT.legMarket(gid, name, L.kind, L.line) : null;
  return M ? {q: M.w * L.p + (1 - M.w) * M.m, src: M.src} : {q: L.p, src: null};
}
function married(legs, gid, pSim, pInd){
  const Q = legs.map(L => legQ(L, gid || L.gid));
  const prod = Q.reduce((s, x) => s * x.q, 1), lift = pInd > 0 ? pSim / pInd : 1;
  return {p: Math.min(prod * lift, ...Q.map(x => x.q)), n: Q.filter(x => x.src).length, of: Q.length};
}
function sgpCard(P){
  const any = P.any ?? P.name === 'Most likely', cut = P.rows.length ? P.rows[0].k : 5;
  const gid = SGP_G && SGP_G.id;
  const rows = P.rows.map(r => { const M = married(P.legs.slice(0, r.k), gid, r.p, r.indep);
    return `<tr><td>${r.k} legs<small class="muted" style="display:block">${M.n}/${M.of} priced</small></td><td><b title="Each leg’s market-anchored chance (${M.n} of ${M.of} legs have a market line) times our sims’ correlation lift">${pct1(M.p)}</b></td><td>${esc(american(M.p))}</td>${any ? '' : `<td>${pct1(r.p_script)}</td>`}<td>${pct1(r.p)}</td><td>${r.kalshi ? american(r.kalshi) : '–'}</td></tr>`; }).join('');
  const legs = P.legs.map((L, i) => (i === cut && i < P.legs.length ? `<li class="after">+ ${P.legs.length - cut === 1 ? 'this' : 'these'} for the longer version${P.legs.length - cut === 1 ? '' : 's'}</li>` : '') + legLine(L, !any, i === cut ? cut + 1 : 0)).join('');
  return `<div class="sgp${P.expected ? ' exp' : ''}"><h3>${esc(P.name)} <small>${P.name === 'Higher lines' ? 'fewer legs, bumped lines' : any ? 'any script' : `happens in ${pct(P.happens)} of sims`}</small></h3>
    <div class="small muted">${esc(P.desc)}</div>
    <ol>${legs}</ol>
    <table><thead><tr><th></th><th>Hits</th><th>Fair</th>${any ? '' : '<th title="Chance the parlay hits if this script happens">In script</th>'}<th title="Our simulated games alone (before the market's leg chances)">Sims only</th><th title="Kalshi asks multiplied (when every leg has one)">Kalshi</th></tr></thead><tbody>${rows}</tbody></table>
    ${gamblyBtns(P.legs, P.rows.map(r => r.k), SGP_G ? `${SGP_G.away} @ ${SGP_G.home}` : '')}
    ${window.SLIP && SGP_G ? `<div class="gbtns"><span class="small muted">My parlay:</span>${P.rows.map(r => SLIP.allBtn(cardSlipLegs(P.legs.slice(0, r.k), SGP_G.id), `+ ${r.k} legs`)).join('')}</div>` : ''}</div>`;
}
function sgpPanel(g){
  SGP_G = g; SGP_BLIND = !!isBlind(g);
  const S = SGP_BLIND ? (D.alt.sgp && D.alt.sgp.games[g.id]) : (D.sgp && D.sgp.games[g.id]);
  if (!S || !S.parlays.length) return '<section class="panel"><div class="empty">No parlays for this game yet: it needs 5+ main-line legs that clear 35%.</div></section>';
  return `<section class="panel" aria-label="Same-game parlays"><div class="phead"><h2>${esc(g.away)} at ${esc(g.home)}: same-game parlays</h2>${modelSwitch(g)}<span class="small muted">${SGP_BLIND ? 'market-blind model' : 'Vegas model'} · leg chance overall → inside the script · Kalshi ask</span></div>
    <p class="note"><b>Hits</b> = each leg's market chance (the sharp consensus or Kalshi at that line, our number weighted in lightly) times how much our simulated games say the legs rise or fall together (a QB's yards and his receivers' rise together). On 8,966 same-game pairs (weeks 5-6) that matched the real hit rate, where multiplying the market's prices missed correlated pairs by about 4 points and our sims alone ran low. <b>Sims only</b> = our simulated games alone; "priced" = legs with a market line. Main lines only (the Kalshi rung nearest 50/50, or our own median line until Kalshi lists one), no unders, every leg 35%+ on its own. Hit chances are calibrated to 2025-26 backtests (335 games). Each leg is adjusted for the player's depth role: backup RBs, QB yards on our own lines, WR2 yards and WR3+ catches ran low and are marked down; RB1 rushing yards and TDs ran high and are marked up. Legs 6 and 7 must be 50%+ alone and a reliable type (a Kalshi line, game leg, RB1 rushing/TD, WR1 or TE1), so some parlays stop at 6. The "Expected" parlay only appears when the model makes a clear close-game or blowout call (in backtests those hit above their shown chance; slight leans didn't). "Higher lines" (3-4 overs set at or above the player's average) backtested right on its numbers. Fair is the no-vig price: a book's parlay is only worth it if it pays more. Switch models above: each leg shows the other model's chance, and "both ✓" when the other model has it at least as high (up to 50%). In the Model agreement backtest, legs where the market-blind model leans the same way hit more often.</p>
    ${outlookBar(S.outlook)}
    <div class="sgp-grid">${S.parlays.map(sgpCard).join('')}</div></section>`;
}
function outlookBar(o){
  if (!o) return '';
  const lean = (p, b) => { const d = Math.round((p - b) * 100); return `<span class="${d >= 3 ? 'good' : 'muted'}">${d >= 0 ? '+' : '−'}${Math.abs(d)} vs the model's typical game</span>`; };
  return `<div class="outlook"><b>${o.expected === 'blowout' ? `Model ${o.slight ? 'slightly leans to' : 'expects'} a ${esc(o.fav)} blowout` : `Model ${o.slight ? 'slightly leans to' : 'expects'} a close game`}</b>
    <span>Decided by 7 or fewer: <b>${pct(o.p_close)}</b> ${lean(o.p_close, o.base_close)}</span>
    <span>Decided by 14+: <b>${pct(o.p_blowout)}</b> ${lean(o.p_blowout, o.base_blowout)} · ${esc(o.fav)} by 14+: ${pct(o.p_fav_blowout)}</span>
    ${D.alt && D.alt.sgp && D.sgp ? otherOutlook() : ''}</div>`;
}
function otherOutlook(){
  const S = (SGP_BLIND ? D.sgp : D.alt.sgp).games[SGP_G.id]; const o = S && S.outlook; if (!o) return '';
  return `<span class="muted">${SGP_BLIND ? 'Vegas model' : 'Market-blind model'}: ${o.expected === 'blowout' ? `${esc(o.fav)} blowout` : 'close game'} (7 or fewer ${pct(o.p_close)}, 14+ ${pct(o.p_blowout)})</span>`;
}
function crossSlipLegs(legs){
  const byGame = {};
  legs.forEach(L => { const g = D.games.find(x => `${x.away} @ ${x.home}` === L.game); if (g) (byGame[g.id] = byGame[g.id] || []).push(L); });
  return Object.entries(byGame).flatMap(([gid, ls]) => cardSlipLegs(ls, gid));
}
function tierParlays(){
  if (!(window.MKT && MKT.tierCard)) return '';
  const gameLegs = [];
  for (const g of D.games){
    const T = D.teams[g.id]; if (!T) continue;
    const H = T[g.home], A = T[g.away]; if (H.spread_line == null) continue;
    const L = H.spread_line, tl = H.total_line, sp = v => (v > 0 ? '+' : v < 0 ? '−' : '') + Math.abs(v);
    gameLegs.push({gid: g.id, kind: 'ml', side: 'away', p: A.win_prob, lab: `${g.away} moneyline`}, {gid: g.id, kind: 'ml', side: 'home', p: H.win_prob, lab: `${g.home} moneyline`},
                  {gid: g.id, kind: 'spread', side: 'away', line: -L, p: A.p_cover, lab: `${g.away} ${sp(L)}`}, {gid: g.id, kind: 'spread', side: 'home', line: -L, p: H.p_cover, lab: `${g.home} ${sp(-L)}`},
                  {gid: g.id, kind: 'total', side: 'over', line: tl, p: H.p_over, lab: `${g.away}-${g.home} over ${tl}`}, {gid: g.id, kind: 'total', side: 'under', line: tl, p: H.p_under, lab: `${g.away}-${g.home} under ${tl}`});
  }
  return MKT.tierCard({sport: 'nfl', title: 'Parlays by payout (your books)', games: D.games.map(g => ({id: g.id, label: `${g.away} @ ${g.home}`, start: g.sort})),
                       gameLegs, propKinds: {td: 'TD', rec_yds: 'rec yds', rush_yds: 'rush yds', pass_yds: 'pass yds', rec: 'receptions'},
                       // Kalshi 2026 (17,517 graded props): 50/50 with the market and a 5%+ edge returned +8.4%; our projection alone lost
                       minEdge: .05});
}
// Ladder candidates (experimental): players facing one of the 8 defenses most generous to their position (defense vs
// position, this season). Kalshi 2026 wk 1-5: their high rungs (2+ above the 50/50 rung) cleared 3-8 pts more often than
// the price said, in both halves (z 1.9 / 2.5 per player); with a 2nd signal (underdog, team total 25+) similar. Tracked
// on the Profit page; not proven.
function ladderCard(){
  if (!D.dvp) return '';
  const KL = {rec: 'catches', rec_yds: 'rec yds', rush_yds: 'rush yds', pass_yds: 'pass yds'};
  const posKey = p => p.pos === 'QB' ? 'QB' : p.pos === 'RB' ? 'RB' : p.pos === 'TE' ? 'TE' : 'WR';
  const now = Date.now(), cards = [];
  for (const g of D.games){
    if (g.sort && new Date(g.sort) <= now) continue;
    for (const p of D.players.filter(x => x.g === g.id)){
      const v = (p.s.tgt && p.s.tgt[MEAN] >= 3) || (p.s.car && p.s.car[MEAN] >= 8) || (p.s.att && p.s.att[MEAN] >= 20);
      if (!v) continue;
      const k = posKey(p), r = dvpRow(k, p.o); if (!r || r.rank > 8) continue;
      const T = (D.teams[g.id] || {})[p.t] || {};
      const extra = [T.win_prob != null && T.win_prob <= .40 ? 'underdog' : null, T.vegas_implied >= 25 ? `team total ${T.vegas_implied}` : null].filter(Boolean);
      const rungs = window.MKT && MKT.ladder ? MKT.ladder(g.id, p.id, p.n) : [];
      cards.push({g, p, r, k, extra, rungs});
    }
  }
  if (!cards.length) return '';
  cards.sort((a, b) => b.extra.length - a.extra.length || a.r.rank - b.r.rank);
  const row = c => `<div class="lad"><div class="ladh"><b>${esc(c.p.n)}</b> <span class="small muted">${esc(c.p.t)} vs ${esc(c.p.o)} · ${esc(c.p.pos)}</span>
      <span class="ladc soft" title="${esc(c.p.o)} gives up the ${ord(c.r.rank)} most to ${c.k === 'WR' ? 'wide receivers' : c.k + 's'} this season (defense vs position)">soft: ${ord(c.r.rank)} most to ${c.k}s</span>${c.extra.map(x => `<span class="ladc">${esc(x)}</span>`).join('')}</div>
    <div class="ladr">${c.rungs.slice(0, 3).map(u => `<span class="lru" title="Kalshi ${Math.round(u.l + .5)}+ ${KL[u.k]}: price ${Math.round(u.mid * 100)}% · our chance ${u.p != null ? Math.round(u.p * 100) + '%' : '–'}"><b>${Math.round(u.l + .5)}+</b> ${KL[u.k]} <i>${u.price}</i></span>`).join('')}</div></div>`;
  const on = cards.filter(c => c.rungs.length), off = cards.filter(c => !c.rungs.length);
  const wait = off.length ? `<details class="ladw"><summary>${on.length ? `${off.length} more` : `${off.length} players`} · Kalshi ladders not posted yet</summary><div class="ladr">${off.map(c =>
      `<span class="lru" title="${esc(c.p.o)}: ${ord(c.r.rank)} most to ${c.k}s${c.extra.length ? ' · ' + c.extra.join(' · ') : ''}"><b>${esc(c.p.n)}</b> <i>${esc(c.p.pos)} vs ${esc(c.p.o)}</i></span>`).join('')}</div></details>` : '';
  return `<section class="panel"><div class="phead"><h2>Ladder candidates <span class="exp3">experimental</span></h2><span class="small muted">${cards.length} player${cards.length === 1 ? '' : 's'}</span></div>
    <details class="how"><summary>What this is</summary><p class="note">Players facing one of the 8 defenses that have given up the most to their position this season, with their higher Kalshi rungs (two or more lines above the one priced near 50/50). In Kalshi’s 2026 markets (weeks 1-5), those higher rungs cleared 3-8 points more often than their price said, in both halves of the sample; a second signal (underdog, or a team total of 25+) pointed the same way. The market seems to price the average game against a bad defense but not the ceiling. That is a small sample from one season, so these are tracked on the Profit page to see if it holds, not a recommendation. Defense rankings early in the season rest on only a few games.</p></details>
    <div class="ladg">${on.slice(0, 8).map(row).join('')}</div>${on.length > 8 ? `<details class="ladw"><summary>${on.length - 8} more with ladders</summary><div class="ladg">${on.slice(8).map(row).join('')}</div></details>` : ''}${wait}</section>`;
}
function renderParlays(){
  const ladder = ladderCard();
  const C = (D.sgp && D.sgp.cross) || [];
  const tiers = tierParlays() + (window.MKT && MKT.tagParlays ? MKT.tagParlays({sport: 'nfl', title: 'Value-tag parlays (higher odds)',
    games: D.games.map(g => ({id: g.id, label: `${g.away} @ ${g.home}`, start: g.sort}))}) : '');
  if (window.MKT && MKT.reg){                                      // freeze mode: record every model parlay for the Results page
    const byLab = Object.fromEntries(D.games.map(g => [`${g.away} @ ${g.home}`, g])), byId = Object.fromEntries(D.games.map(g => [g.id, g]));
    const PK = {anytime_td: 'td', rec: 'rec', rec_yds: 'rec_yds', rush_yds: 'rush_yds', pass_yds: 'pass_yds'};
    const leg = (L, g) => { g = g || {};
      const base = {gid: g.id || null, start: g.sort || null, lab: L.lab, p: L.p, price: L.price, book: L.price ? 'Kalshi' : null};
      if (L.kind === 'win') return {...base, kind: 'ml', side: L.team === g.home ? 'home' : 'away', line: null, name: null};
      if (L.kind === 'total') return {...base, kind: 'total', side: /^under/i.test(L.lab) ? 'under' : 'over', line: L.line, name: null};
      return {...base, kind: PK[L.kind] || L.kind, side: 'o', line: L.kind === 'anytime_td' ? .5 : L.line, name: MKT.norm(L.lab.replace(/ (anytime TD|over .*)$/, ''))}; };
    C.forEach(c => MKT.reg('Cross-game parlays', c.title, c.legs.map(L => leg(L, byLab[L.game])), c.p, c.kalshi ? 1 / c.kalshi : null));
    // same-game parlays on each game's page: every script at every size, both models
    for (const [lab, S] of [['Same-game parlays', D.sgp], ['Same-game parlays (market-blind)', D.alt && D.alt.sgp]]){
      if (!S || !S.games) continue;
      for (const [gid, G] of Object.entries(S.games)){ const g = byId[gid]; if (!g) continue;
        for (const P of G.parlays || []) for (const r of P.rows || []){
          const legs = (P.legs || []).slice(0, r.k).map(L => leg(L, g)); if (legs.length < r.k) continue;
          MKT.reg(lab, `${P.name} · ${r.k} legs`, legs, r.p, r.kalshi ? 1 / r.kalshi : null); } }
    }
  }
  if (!C.length) return ladder + tiers + '<section class="panel"><div class="empty">No cross-game parlays this week yet.</div></section>';
  const day = s => { const d = new Date(String(s).slice(0, 10) + 'T12:00:00'); return isNaN(d) ? s : d.toLocaleDateString([], {weekday: 'short', month: 'short', day: 'numeric'}); };
  const card = c => `<div class="sgp"><h3>${esc(c.title)} <small>${day(c.when)} · ${c.games} game${c.games === 1 ? '' : 's'} · ${c.legs.length} legs</small></h3>
    <ol>${c.legs.map(L => `<li>${esc(L.lab)} <span class="small muted">${esc(L.game)}</span>${srcTag(L)}<span class="r">${pct(L.p)}${L.price ? ` · ${window.MKT && MKT.px ? MKT.px(L.price, 'kalshi') : Math.round(L.price * 100) + '¢'}` : ''}</span></li>`).join('')}</ol>
    ${(() => { const byG = Object.fromEntries(D.games.map(x => [`${x.away} @ ${x.home}`, x.id]));
      const M = married(c.legs.map(L => ({...L, gid: byG[L.game]})), null, c.p, c.legs.reduce((s, L) => s * L.p, 1));
      return `<table><thead><tr><th></th><th>Hits</th><th>Fair</th><th>Sims only</th><th>Kalshi</th></tr></thead><tbody><tr><td>all ${c.legs.length} legs<small class="muted" style="display:block">${M.n}/${M.of} priced</small></td><td><b title="Market-anchored leg chances times our sims’ correlation lift">${pct1(M.p)}</b></td><td>${esc(american(M.p))}</td><td>${pct1(c.p)}</td><td>${c.kalshi ? american(c.kalshi) : '–'}</td></tr></tbody></table>`; })()}
    ${gamblyBtns(c.legs.map(L => ({lab: `${L.lab} (${L.game})`})), [c.legs.length], '')}
    ${window.SLIP ? `<div class="gbtns"><span class="small muted">My parlay:</span>${SLIP.allBtn(crossSlipLegs(c.legs), '+ add parlay')}</div>` : ''}</div>`;
  return ladder + tiers + `<section class="panel"><div class="phead"><h2>Long parlays by kickoff window and day</h2><span class="small muted">each game's strongest legs, stacked</span></div>
    <p class="note">Built from each game's most likely same-game parlay: its first legs (kept together, so their link counts), as many from each game as it takes to reach 5+ legs. The full-day parlay covers every game that day. Games are simulated separately, so across games the chances multiply. These overlap with the per-game parlays on purpose.</p>
    <div class="sgp-grid">${C.map(card).join('')}</div></section>`;
}
/* ---------------- game summary: players worth a look, tiered, with the reasons (display only) ---------------- */
function summaryPanel(g){
  const L = D.spot[g.id] || [];
  const row = c => `<div class="sp-row"><span class="tierb t${c.tier}">Tier ${c.tier}</span><div class="sp-main">
      <div class="sp-name"><b>${esc(c.name)}</b><span>${esc(c.team)} · ${esc(c.pos)}${c.label ? ' · ' + esc(c.label) : ''}</span></div>
      ${c.facts.length ? `<div class="sp-facts">${c.facts.map(esc).join(' · ')}</div>` : ''}
      ${c.reasons.map(r => `<div class="sp-why${/^tough/.test(r) ? ' neg' : ''}">↳ ${esc(r).replace(/ -- /g, ' — ')}</div>`).join('')}</div></div>`;
  return `<section class="panel sp"><div class="phead"><h2>${esc(g.away)} at ${esc(g.home)}: what changed</h2><span class="small muted">${esc(g.kickoff)}</span></div>
    ${L.length ? `<div class="sp-list">${L.map(row).join('')}</div>` : '<div class="empty" style="padding:8px 0">No notable changes for this game: no snap jumps, injuries opening up volume, or missing defenders.</div>'}
    <p class="note">Context only: the projections already include these.</p></section>`;
}
// Matchup map: where each offense goes (number = share of its passes or runs) and how this defense does there
// (color = success it allows vs the league in that spot; striped = few plays, so the color is mostly league average)
const FM_ROWS = [['deep', 'Deep 20+'], ['inter', 'Medium 10-19'], ['short', 'Short 1-9'], ['behind', 'Screens / behind line']];
const FM_COLS = [['left', 'Left'], ['middle', 'Middle'], ['right', 'Right']];
const FM_RUN = [['left|end', 'L end'], ['left|tackle', 'L tackle'], ['left|guard', 'L guard'], ['middle|', 'Middle'], ['right|guard', 'R guard'], ['right|tackle', 'R tackle'], ['right|end', 'R end']];
// areas a missing defensive starter usually covers (boxscore/defout.py roles), for the dashed outline
const FM_COVER = {CB1: ['short|left', 'short|right', 'inter|left', 'inter|right', 'deep|left', 'deep|right'], CB2: ['short|left', 'short|right', 'inter|left', 'inter|right', 'deep|left', 'deep|right'],
  CB3: ['short|middle', 'inter|middle', 'behind|left', 'behind|right'], S: ['deep|left', 'deep|middle', 'deep|right', 'inter|middle'],
  LB: ['short|middle', 'inter|middle', 'behind|middle', 'run|middle|', 'run|left|guard', 'run|right|guard'],
  DL: ['run|middle|', 'run|left|guard', 'run|right|guard', 'run|left|tackle', 'run|right|tackle', 'run|left|end', 'run|right|end']};
function fmZoneName(z){
  const [a, b, c] = z.split('|');
  if (a === 'run') return b === 'middle' ? 'runs up the middle' : `runs off the ${b} ${c}`;
  return `${{deep: 'deep', inter: 'medium', short: 'short', behind: 'screen/behind-the-line'}[a]} ${b} passes`;
}
function fmCell(c, def, off, z, outs){
  if (!c) return '<div class="fm-c"><small class="muted">–</small></div>';
  const ob = (outs || []).filter(o => (FM_COVER[o.role] || []).includes(z)).map(o => o.role);
  const e = c.da - c.lg, a = Math.min(Math.abs(e) / .10, 1) * .55, thin = c.n < 12;
  const bg = Math.abs(e) < .015 ? '' : `background-color:color-mix(in srgb,var(${e > 0 ? '--good' : '--bad'}) ${Math.round(a * 100)}%,var(--sunk))`;
  const W = c.who || [], unit = z.startsWith('run') ? 'carries' : 'targets';
  const wh = W.length ? `<span class="wh">${W.slice(0, 2).map(([n, e], i) => `<span>${i ? ' · ' : ''}${esc(n)} <em>${(+e).toFixed(1)}</em></span>`).join('')}</span>` : '';
  const tip = `${off}: ${Math.round(c.s * 100)}% of its ${z.startsWith('run') ? 'runs' : 'passes'} go here${W.length ? ` (likeliest: ${W.map(([n, e]) => n + ' ' + e + ' ' + unit).join(', ')})` : ''}. ${def} allows success on ${Math.round(c.da * 100)}% of these (league ${Math.round(c.lg * 100)}%)${thin ? '; few plays here, so mostly league average' : ''}.`;
  return `<div class="fm-c${thin ? ' thin' : ''}${ob.length ? ' out' : ''}" style="${bg}" title="${esc(tip + (ob.length ? ' Missing here: ' + ob.join(', ') + '.' : ''))}"><b>${Math.round(c.s * 100)}%</b><small>${Math.abs(e) < .015 ? 'even' : (e > 0 ? '+' : '−') + Math.round(Math.abs(e) * 100) + ' pts'}</small>${wh}${ob.length ? `<span class="ob">${[...new Set(ob)].join(' · ')} out</span>` : ''}</div>`;
}
function mapPanel(g){
  const M = D.fmap[g.id];
  const block = off => {
    const X = M[off]; if (!X) return ''; const C = X.cells, def = X.vs;
    const outs = ((D.defout || {})[g.id] || {})[def] || [];
    const RN = {CB1: 'top corner', CB2: 'No. 2 corner', CB3: 'slot corner', S: 'safety', LB: 'linebacker', DL: 'D-lineman'};
    const miss = outs.length ? `<div class="fm-miss"><b>${esc(def)} missing:</b> ${outs.map(o => `${esc(o.name)} (${esc(RN[o.role] || o.role)}${o.status && o.status !== 'out' ? ', ' + esc(o.status) : ''})`).join(', ')}
      <small>Dashed boxes are where they usually play. In 2022-26 games, a missing starter changed the success allowed in his area by only about 1-2 points (not enough to trust), so the colors stay as they are.</small></div>` : '';
    const grid = `<div class="fm-grid"><span></span>${FM_COLS.map(([, l]) => `<span class="cl">${l}</span>`).join('')}
      ${FM_ROWS.map(([r, lab], i) => `${i === 3 ? '<div class="fm-los"><span>LINE OF SCRIMMAGE</span></div>' : ''}<span class="rl">${lab}</span>${FM_COLS.map(([c]) => fmCell(C[r + '|' + c], def, off, r + '|' + c, outs)).join('')}`).join('')}</div>`;
    const run = `<div class="fm-sub" style="margin-top:14px">Runs, by lane</div><div class="fm-run">${FM_RUN.map(([z]) => fmCell(C['run|' + z], def, off, 'run|' + z, outs)).join('')}${FM_RUN.map(([, l]) => `<span class="cl">${l}</span>`).join('')}</div>`;
    const Z = Object.entries(C).filter(([, c]) => c.s >= .05 && c.n >= 12).map(([z, c]) => ({z, c, e: c.da - c.lg, w: c.s * (c.da - c.lg)}));
    const line = ({z, c, e}) => `<li><b>${esc(fmZoneName(z)[0].toUpperCase() + fmZoneName(z).slice(1))}</b>: ${esc(off)} goes here ${Math.round(c.s * 100)}% of the time; ${esc(def)} allows success on <span class="${e > 0 ? 'up' : 'dn'}">${Math.round(c.da * 100)}%</span> (league ${Math.round(c.lg * 100)}%).${(c.who || []).length ? ` Likeliest: ${c.who.slice(0, 2).map(([n, x]) => `${esc(n)} (${(+x).toFixed(1)} ${z.startsWith('run') ? 'carries' : 'targets'})`).join(', ')}.` : ''}</li>`;
    const best = Z.filter(x => x.e > .02).sort((a, b) => b.w - a.w).slice(0, 3), tough = Z.filter(x => x.e < -.02).sort((a, b) => a.w - b.w).slice(0, 2);
    return `<div class="fm-off"><h3>${esc(off)} offense <small>vs ${esc(def)} defense</small></h3>${miss}<div class="fm-wrap"><div><div class="fm-sub">Passes, by depth and direction</div>${grid}</div>
      <div>${run}<div class="fm-sub" style="margin-top:16px">Where ${esc(off)} should find room</div><ul class="fm-list">${best.map(line).join('') || '<li class="muted">No spot stands out.</li>'}</ul>
      <div class="fm-sub" style="margin-top:12px">Where ${esc(def)} is tough</div><ul class="fm-list">${tough.map(line).join('') || '<li class="muted">No spot stands out.</li>'}</ul></div></div></div>`;
  };
  return `<section class="panel"><div class="phead"><h2>${esc(g.away)} at ${esc(g.home)}: matchup map</h2><span class="small muted">${esc(g.kickoff)}</span></div>
    <div class="fm-key"><span><b style="color:var(--ink)">22%</b> = how often the offense goes there</span><span><i style="background:color-mix(in srgb,var(--good) 55%,var(--sunk))"></i>defense gives up more than average</span><span><i style="background:color-mix(in srgb,var(--bad) 55%,var(--sunk))"></i>defense is tougher than average</span><span><i style="background:var(--sunk);background-image:repeating-linear-gradient(45deg,transparent 0 3px,rgba(127,127,127,.35) 3px 6px)"></i>few plays: mostly league average</span><span><b style="color:var(--ink)">Name 1.7</b> = expected targets (or carries) there this game</span></div>
    ${block(g.away)}${block(g.home)}
    <p class="note">Names: who is likeliest to get the ball in each spot, from this week's projected targets and carries times where his targets and carries have gone (players with few looks lean toward their position's usual spread). Where the ball was thrown, not full route charts, which aren't published during the season. Success = a play that improved the offense's expected points. This season so far (last season counts a third as much); each defense's rates lean toward the league average until it has enough plays in a spot. Context only: the projections already include each defense's overall strength.</p></section>`;
}
// Game script tab: the game lines (who's favored, by how much, the total) and, for the high-usage players and the ones
// facing a soft defense, each prop's over ladder (main line + alt lines + Kalshi rungs) at the viewer's books. Chance
// = ours weighted with the market's fair at that line (MKT.altLadder: sharp consensus / the books' alt-line fair / Kalshi
// mid), the same anchoring as everywhere else; "in script" scales it by how much our saved simulated games say that
// line rises or falls when the chosen script happens (e.g. the favorite pulls away by 10+).
const SCR = {};
const SCR_ROOT = (() => { const el = document.querySelector('script[src*="slip.js"]'); return el ? el.src.replace(/slip\.js.*$/, '') : '../'; })();
function scriptDraws(gid){
  if (SCR[gid] === undefined){
    SCR[gid] = 'load';
    fetch(`${SCR_ROOT}nfl/draws/${gid}.json?t=${Math.floor(Date.now() / 6e5)}`).then(r => r.ok ? r.json() : null).catch(() => null)
      .then(j => { SCR[gid] = j; if (state.gtab === 'script' && state.game === gid) render(); });
  }
  return SCR[gid];
}
function sdecode(a){
  if (!a) return null;
  const bin = atob(a.b), u8 = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) u8[i] = bin.charCodeAt(i);
  return a.t === 'i16' ? new Int16Array(u8.buffer) : u8;
}
const SC_KIND = {pass_yds: 'pass yds', rush_yds: 'rush yds', rec_yds: 'rec yds', rec: 'catches'};
const decO = o => o > 0 ? 1 + o / 100 : 1 + 100 / -o;
const amS = o => (o > 0 ? '+' : '') + o;
function scriptPanel(g){
  SGP_G = g; SGP_BLIND = false;
  const T = D.teams[g.id] || {}, H = T[g.home], A = T[g.away];
  if (!H || H.spread_line == null) return '<section class="panel"><div class="empty">No Vegas lines for this game yet.</div></section>';
  const L = H.spread_line, tl = H.total_line, homeFav = L > 0 || (L === 0 && H.win_prob >= .5);
  const fav = homeFav ? g.home : g.away, dog = homeFav ? g.away : g.home, F = homeFav ? H : A, U = homeFav ? A : H;
  const DW = scriptDraws(g.id), ready = !!(DW && DW !== 'load' && DW.g);
  const M = ready ? sdecode(DW.g.margin) : null, TT = ready ? sdecode(DW.g.total) : null;
  const SC = [['all', 'Expected', () => true, 'every simulated game'],
              ['pull', `${fav} pulls away`, (m) => (homeFav ? m : -m) >= 10, `${fav} wins by 10+`],
              ['close', 'Close game', (m) => Math.abs(m) <= 7, 'decided by 7 or fewer'],
              ['upset', `${dog} wins`, (m) => homeFav ? m < 0 : m > 0, `the underdog ${dog} wins`],
              ['shoot', 'Shootout', (m, t) => t > tl, `over ${tl} total points`],
              ['low', 'Low-scoring', (m, t) => t < tl, `under ${tl} total points`]];
  const sc = SC.find(x => x[0] === state.script) || SC[0];
  const share = f => { if (!M) return null; let c = 0; for (let i = 0; i < M.length; i++) if (f(M[i], TT[i])) c++; return c / M.length; };
  const mask = M && sc[0] !== 'all' ? Array.from(M, (m, i) => sc[2](m, TT[i])) : null, nIn = mask ? mask.filter(Boolean).length : 0;
  const sw = `<div class="seg scsw" role="group" aria-label="Game script">${SC.map(x => { const s_ = x[0] === 'all' ? null : share(x[2]);
    return `<button data-script="${x[0]}" aria-pressed="${sc[0] === x[0]}" title="${esc(x[3])}">${esc(x[1])}${s_ != null ? `<small>${pct(s_)}</small>` : ''}</button>`; }).join('')}</div>`;
  const O = D.sgp && D.sgp.games && D.sgp.games[g.id] && D.sgp.games[g.id].outlook;
  const head = `<div class="gsh"><span class="big">${esc(fav)} −${f1(Math.abs(L)).replace('.0', '')}</span>
      <span>Win: ${esc(fav)} <b>${pct(F.win_prob)}</b> · ${esc(dog)} <b>${pct(U.win_prob)}</b></span>
      <span>Total <b>${tl}</b> · over <b>${pct(H.p_over)}</b></span>
      <span class="muted small">Vegas team totals: ${esc(fav)} ${f1(F.vegas_implied)} · ${esc(dog)} ${f1(U.vegas_implied)}</span></div>`;
  // players: the starting QB, each team's top 3 by targets + carries, and anyone facing a top-8 soft defense for his position
  const posKey = p => p.pos === 'QB' ? 'QB' : p.pos === 'RB' ? 'RB' : p.pos === 'TE' ? 'TE' : 'WR';
  const mu = (p, s_) => p.s[s_] ? p.s[s_][MEAN] : 0;
  const pool = D.players.filter(p => p.g === g.id), cards = {[fav]: [], [dog]: []}, waiting = [];
  const lift = (x, l) => { if (!mask || !x) return 1; let a = 0, b = 0;
    for (let i = 0; i < x.length; i++){ if (x[i] > l){ a++; if (mask[i]) b++; } }
    return ((b + 1) / (nIn + 2)) / ((a + 1) / (x.length + 2)); };
  for (const team of [fav, dog]){
    const mine = pool.filter(p => p.t === team);
    const qb = mine.filter(p => p.pos === 'QB').sort((a, b) => mu(b, 'att') - mu(a, 'att'))[0];
    const top = mine.filter(p => p.pos !== 'QB').sort((a, b) => (mu(b, 'tgt') + mu(b, 'car')) - (mu(a, 'tgt') + mu(a, 'car'))).slice(0, 3);
    for (const p of mine){
      const k = posKey(p), r = dvpRow(k, p.o), soft = !!(r && r.rank <= 8);
      const usage = p === qb || top.includes(p);
      if (!usage && !(soft && (mu(p, 'tgt') >= 3 || mu(p, 'car') >= 8 || mu(p, 'att') >= 20))) continue;
      const stats = p.pos === 'QB' ? ['pass_yds', ...(mu(p, 'car') >= 4 ? ['rush_yds'] : [])]
        : p.pos === 'RB' ? ['rush_yds', ...(mu(p, 'tgt') >= 3 ? ['rec_yds', 'rec'] : [])] : ['rec_yds', 'rec'];
      const ladders = [];
      for (const st of stats){
        const R = window.MKT && MKT.altLadder ? MKT.altLadder(g.id, p.n, st) : [];
        const x = ready && DW.p && DW.p[p.k] ? sdecode(DW.p[p.k][st]) : null;
        const main = R.find(r_ => r_.main);
        // a rung no book prices two-way (Kalshi-only): the main line's sharp fair carried up the ladder by our
        // distribution's shape (same log-odds gap to ours), never Kalshi's own mid (that made fake tail edges)
        const lg = v => Math.log(v / (1 - v)), sgm = v => 1 / (1 + Math.exp(-v));
        const gap = main && main.fair != null && main.p != null && main.p > 0 && main.p < 1 ? lg(main.fair) - lg(main.p) : null;
        const rows = R.filter(r_ => (r_.best || r_.any) && (!main || r_.l >= main.l - 1e-9)).map(r_ => {
          let fair = r_.fair, carried = false;
          if (fair == null && gap != null && r_.p != null && r_.p > 0 && r_.p < 1){ fair = sgm(lg(r_.p) + gap); carried = true; }
          const w = fair != null ? main.w : .5, m = fair ?? r_.mid, p0 = r_.p, q = p0 == null ? m : m == null ? p0 : w * p0 + (1 - w) * m;
          const lf = lift(x, r_.l), qs = q == null ? null : Math.min(.99, q * lf), B = r_.best || r_.any, im = 1 / decO(B.o);
          return {...r_, fair, carried, q, qs, lf, B, mineB: !!r_.best, ev: q != null ? q - im : null, evs: qs != null ? qs - im : null};
        }).slice(0, 5);
        if (rows.length) ladders.push({st, rows, lf: rows[0].lf});
      }
      const c = {p, r, soft, usage, ladders, vol: p.pos === 'QB' ? mu(p, 'att') : mu(p, 'tgt') + mu(p, 'car')};
      if (ladders.length) cards[team].push(c); else waiting.push(c);
    }
    cards[team].sort((a, b) => sc[0] === 'all' ? b.vol - a.vol : Math.max(...b.ladders.map(l => l.lf)) - Math.max(...a.ladders.map(l => l.lf)) || b.vol - a.vol);
  }
  const showS = sc[0] !== 'all' && ready;
  const ecls = e => e == null ? '' : e >= .05 ? 'e2' : e >= .03 ? 'e1' : e < 0 ? 'neg' : '';
  const pts = e => e == null ? '–' : (e >= 0 ? '+' : '−') + Math.abs(Math.round(e * 100));
  const sg = e => e == null ? '–' : (e >= 0 ? '+' : '−') + Math.abs(Math.round(e * 100)) + '%';
  const lad = Lz => `<table class="sct"><caption>${SC_KIND[Lz.st]}${showS ? ` · <span class="${Lz.lf >= 1.05 ? 'good' : ''}">${sg(Lz.lf - 1)} at the main line in this script</span>` : ''}</caption>
      <thead><tr><th>Over</th><th>Best price</th><th title="The market’s no-vig chance at this line">Fair</th><th title="Our chance weighted with the market’s fair">Chance</th>${showS ? '<th title="Chance if this script happens (our simulated games)">In script</th>' : ''}<th title="Chance minus the best price’s implied chance, in points${showS ? ', if the script happens' : ''}">Edge</th></tr></thead>
      <tbody>${Lz.rows.map(u => { const e = showS ? u.evs : u.ev;
        return `<tr class="${u.mineB ? '' : 'far'}"><td><b>${Math.ceil(u.l)}+</b>${u.main ? '<span class="mainl">main</span>' : ''}</td>
        <td>${u.B.u ? `<a href="${esc(u.B.u)}" target="_blank" rel="noopener">${amS(u.B.o)}</a>` : amS(u.B.o)}<small>${esc(u.B.name)}${u.mineB ? '' : ' · not yours'}</small></td>
        <td>${u.fair != null ? (u.carried ? '≈' : '') + pct(u.fair) : u.mid != null ? pct(u.mid) + '<small>Kalshi mid</small>' : '–'}</td>
        <td>${u.q != null ? pct(u.q) : '–'}</td>${showS ? `<td><b>${u.qs != null ? pct(u.qs) : '–'}</b></td>` : ''}<td class="${ecls(e)}">${pts(e)}</td></tr>`; }).join('')}</tbody></table>`;
  const card = c => { const k = posKey(c.p), best = Math.max(...c.ladders.map(l => l.lf));
    const vol = c.p.pos === 'QB' ? `${f0(mu(c.p, 'att'))} pass att` : [mu(c.p, 'tgt') >= 2 ? `${f1(mu(c.p, 'tgt'))} tgt` : null, mu(c.p, 'car') >= 2 ? `${f1(mu(c.p, 'car'))} car` : null].filter(Boolean).join(' · ');
    return `<div class="scc"><div class="ladh"><b>${esc(c.p.n)}</b> <span class="small muted">${esc(c.p.pos)} · vs ${esc(c.p.o)}</span>
      ${c.usage ? `<span class="ladc" title="Projected volume (top 3 on his team by targets + carries, or the starting QB)">${esc(vol)}</span>` : ''}
      ${c.soft ? `<span class="ladc soft" title="${esc(c.p.o)} gives up the ${ord(c.r.rank)} most to ${k === 'WR' ? 'wide receivers' : k + 's'} (defense vs position)">soft: ${ord(c.r.rank)} most to ${k}s</span>` : ''}
      ${showS ? `<span class="ladc ${best >= 1.05 ? 'up' : best <= .95 ? 'dn' : ''}">script ${sg(best - 1)}</span>` : ''}</div>
      ${c.ladders.map(lad).join('')}</div>`; };
  const team = (t, lab) => cards[t].length ? `<div class="scteam"><h3>${esc(t)}</h3><span class="small muted">${lab}</span></div><div class="scgrid">${cards[t].map(card).join('')}</div>` : '';
  const wait = waiting.length ? `<details class="ladw"><summary>${waiting.length} more player${waiting.length === 1 ? '' : 's'} · no lines posted yet</summary><div class="ladr">${waiting.map(c => `<span class="lru"><b>${esc(c.p.n)}</b> <i>${esc(c.p.t)} ${esc(c.p.pos)}</i></span>`).join('')}</div></details>` : '';
  const none = !cards[fav].length && !cards[dog].length;
  return `<section class="panel" aria-label="Game script"><div class="phead"><h2>${esc(g.away)} at ${esc(g.home)}: game script</h2><span class="small muted">${esc(g.kickoff)} · Vegas model</span></div>
    ${head}
    ${O ? outlookBar(O) : ''}
    ${gameBets(g)}
    <div><span class="eyebrow">Props if the game goes this way</span>${sw}
      <div class="small muted">${ready ? (sc[0] === 'all' ? 'All simulated games. Pick a script to see whose lines rise and fall with it.' : `${pct(nIn / M.length)} of our simulated games: ${esc(sc[3])}. Players sorted by how much their lines rise in it.`) : DW === 'load' ? 'Loading simulated games…' : 'Simulated games not available for this game; showing the expected view.'}</div></div>
    ${none ? '<p class="note">No prop lines posted yet for this game’s high-usage or soft-matchup players. Books post them about a day out.</p>' : team(fav, `favored by ${f1(Math.abs(L)).replace('.0', '')}`) + team(dog, 'underdog')}
    ${wait}
    <details class="how"><summary>How to read this</summary><p class="note">Players: each team’s starting QB and top 3 by projected targets + carries, plus anyone facing one of the 8 defenses that give up the most to his position. Each ladder is the main line and the higher alt lines (books’ alt lines and Kalshi’s rungs). <b>Fair</b> = the market’s no-vig chance at that line (the sharp consensus at the main line; the books’ own two-way alt prices for alts; ≈ = the main line’s fair carried up the ladder by our distribution’s shape where only Kalshi prices the rung). <b>Chance</b> = ours weighted with the fair, the same anchoring tested for the rest of the site. <b>In script</b> = that chance scaled by how often the line clears in our simulated games where the script happens vs all of them. <b>Edge</b> = chance minus the best price’s implied chance, in points (green 3+, bold 5+; faded rows: only at other books). This view is for screening: the tested Value-tag rules are on the Markets page.</p></details>
  </section>`;
}
function gameDetail(){
  const g = D.games.find(x => x.id === state.game); if (!g) return '<div class="empty">No games.</div>';
  const gsw = (D.sgp || D.spot) ? `<div class="seg gsw" role="group" aria-label="Game view">${[['proj', 'Projections'], ...(D.sgp ? [['script', 'Game script']] : []), ...(D.spot && g.id in D.spot ? [['sum', 'Summary']] : []), ...(D.fmap && D.fmap[g.id] ? [['map', 'Matchup map']] : []), ...(D.sgp ? [['sgp', 'Same-game parlays']] : [])].map(([k, l]) => `<button data-gtab="${k}" aria-pressed="${(state.gtab || 'proj') === k}">${l}</button>`).join('')}</div>` : '';
  if (D.sgp && state.gtab === 'sgp') return gsw + sgpPanel(g);
  if (D.sgp && state.gtab === 'script') return gsw + scriptPanel(g);
  if (state.gtab === 'sum' && D.spot && g.id in D.spot) return gsw + summaryPanel(g);
  if (state.gtab === 'map' && D.fmap && D.fmap[g.id]) return gsw + mapPanel(g);
  return gsw + gameDetailMain(g);
}
// game bets for the parlay slip: moneyline, spread and total at the Vegas lines (judged in the same saved simulations)
// one tappable chip per bet option (adds it to the parlay slip; ✓ once added)
function betChip(L, lab, p, bk){                         // bk: best sportsbook price line (mkt.js MKT.bk), if any
  const on = window.SLIP && SLIP.has(L.id);
  const odd = p == null ? '' : american(p);
  return `<button type="button" class="bet${on ? ' on' : ''}"${window.SLIP ? ` data-slip="${esc(JSON.stringify(L))}" title="${on ? 'Remove from' : 'Add to'} my parlay"` : ' disabled'}><span>${esc(lab)}</span><b>${pct(p)}</b><small>${odd}</small><i>${on ? '✓' : '+'}</i>${bk || ''}</button>`;
}
const BK = (...a) => window.MKT && MKT.bk ? MKT.bk(...a) : '';
const betRow = (lab, chips) => `<div class="betrow"><span class="eyebrow">${lab}</span><div class="bets">${chips.join('')}</div></div>`;
function gameBets(g){
  const T = D.teams[g.id], H = T[g.home], A = T[g.away]; if (H.spread_line == null) return '';
  const L = H.spread_line, tl = H.total_line, glab = `${g.away} @ ${g.home}`, base = {sport: 'nfl', g: g.id, glab, draws: `nfl/draws/${g.id}.json`, model: 'vegas'};
  const sp = v => (v > 0 ? '+' : v < 0 ? '−' : '') + f1(Math.abs(v)).replace('.0', '');
  const leg = (id, extra, lab) => ({...base, id: `nfl|${g.id}|${id}`, side: 'over', lab, ...extra});
  return `<div class="betgrid">${[
    betRow('Moneyline', [betChip(leg(`win|${g.away}`, {stat: 'win', home: false}, `${g.away} moneyline`), g.away, A.win_prob, BK(g.id, 'ml', 'away', null, A.win_prob)),
                         betChip(leg(`win|${g.home}`, {stat: 'win', home: true}, `${g.home} moneyline`), g.home, H.win_prob, BK(g.id, 'ml', 'home', null, H.win_prob))]),
    betRow('Spread', [betChip(leg(`spread|${g.away}|${L}`, {stat: 'spread', home: false, line: L}, `${g.away} ${sp(L)}`), `${g.away} ${sp(L)}`, A.p_cover, BK(g.id, 'spread', 'away', -L, A.p_cover)),
                      betChip(leg(`spread|${g.home}|${L}`, {stat: 'spread', home: true, line: L}, `${g.home} ${sp(-L)}`), `${g.home} ${sp(-L)}`, H.p_cover, BK(g.id, 'spread', 'home', -L, H.p_cover))]),
    betRow(`Total ${tl}`, [betChip(leg(`total|over|${tl}`, {stat: 'total', line: tl}, `Over ${tl} (${glab})`), 'Over', H.p_over, BK(g.id, 'total', 'over', tl, H.p_over)),
                           betChip(leg(`total|under|${tl}`, {stat: 'total', line: tl, side: 'under'}, `Under ${tl} (${glab})`), 'Under', H.p_under, BK(g.id, 'total', 'under', tl, H.p_under))]),
  ].join('')}${window.MKT && MKT.bkNote ? MKT.bkNote(g.id) : ''}</div>`;
}
function gameDetailMain(g){
  const A = TM(g)[g.away], H = TM(g)[g.home];
  const side = (t, T, cls) => `<div class="side ${cls}"><span class="code">${esc(t)}</span><span class="pts">${f1(T.points)}</span><span class="muted small">Vegas ${f1(D.teams[g.id][t].vegas_implied)}</span></div>`;
  const rows = [
    ['Win probability', T => pct(T.win_prob)], ['Plays', T => f1(T.plays)],
    ['Cmp / Att', T => `${f1(T.cmp)} / ${f1(T.pass_att)}`], ['Passing yards', T => f0(T.pass_yds)],
    ['Sacks taken', T => f1(T.sacks)], ['Interceptions', T => f1(T.ints)],
    ['Rush attempts', T => f1(T.rush_att)], ['Rushing yards', T => f0(T.rush_yds)],
    ['Passing TDs', T => f1(T.pass_td)], ['Rushing TDs', T => f1(T.rush_td)], ['Field goals', T => f1(T.fg)],
  ];
  return `<section class="panel" aria-label="Game summary">
    <div class="phead"><h2>${esc(g.away)} at ${esc(g.home)}</h2>${modelSwitch(g)}<span class="muted small">${esc(g.kickoff)} · ${esc(spreadText(g))}${g.weather ? ` · <span${g.weather.flag ? ' class="wx-flag"' : ''} title="${g.weather.src === 'forecast' ? 'Kickoff forecast (Open-Meteo)' : 'Kickoff conditions'}">${esc(g.weather.text)}</span>` : ''}</span></div>
    <div class="two">
      <div style="display:grid;gap:16px;align-content:start">
        <div class="score">${side(g.away, A, 'away')}<span class="at">@</span>${side(g.home, H, 'home')}</div>
        <div class="winbar"><div class="bar" role="img" aria-label="Win probability ${esc(g.away)} ${pct(A.win_prob)}, ${esc(g.home)} ${pct(H.win_prob)}"><i style="flex:${A.win_prob};background:var(--away)"></i><i style="flex:${H.win_prob};background:var(--accent)"></i></div>
          <div class="lbl"><span>${esc(g.away)} ${pct(A.win_prob)}</span><span class="muted">projected points and win chance</span><span>${esc(g.home)} ${pct(H.win_prob)}</span></div></div>
        ${gameBets(g)}
        ${firstTdBlock(g)}
        ${window.MKT ? MKT.game(g.id, isBlind(g) ? 'blind' : 'vegas') : ''}
        ${isBlind(g) ? '<p class="note" style="margin:0">Market-blind: this game simulated without the Vegas spread or total (its own points forecast). Vegas lines shown for comparison.</p>' : ''}
      </div>
      <div><div class="boxhead"><span>${esc(g.away)}</span><span class="eyebrow">Team box (averages)</span><span>${esc(g.home)}</span></div>
        <table class="box">${rows.map(([l,f]) => `<tr><td>${f(A)}</td><td>${l}</td><td>${f(H)}</td></tr>`).join('')}</table></div>
    </div>
  </section>
  <section class="panel" aria-label="Player projections">
    <div class="phead">
      <div class="seg" role="group" aria-label="Stat group">${Object.entries(GROUPS).map(([k,G]) => `<button data-group="${k}" aria-pressed="${k===state.group}">${G.label}</button>`).join('')}</div>
      ${state.group === 'td' ? `<span class="key">Chance of scoring at least one rushing or receiving TD${D.rz ? ` · red-zone tags from <span class="seg" role="group" aria-label="Seasons" style="display:inline-flex">${['season','two'].map(k => `<button data-rzscope="${k}" aria-pressed="${k===state.rzscope}">${k === 'season' ? D.rz.labels.season + ' only' : D.rz.labels.two}</button>`).join('')}</span>` : ''}</span>` : '<span class="key"><span><i class="kw"></i>8 in 10 games</span><span><i class="kb"></i>middle half</span><span><i class="km"></i>median</span></span>'}
    </div>
    ${playerTable(g)}
    <p class="muted small" style="margin:0">Tap a player's name to slide the line and see the over/under chance, fair odds and live market prices.</p>
  </section>
  ${matchupPanel(g)}`;
}
const ord = n => { const s = ['th','st','nd','rd'], v = n % 100; return n + (s[(v - 20) % 10] || s[v] || s[0]); };
const rankText = r => r <= 16 ? `${ord(r)} most` : `${ord(33 - r)} fewest`;
const signPct = r => { const v = Math.round((r - 1) * 100); return (v >= 0 ? '+' : '−') + Math.abs(v) + '%'; };
const DVP_CELLS = [['QB','QB'],['RB','RB'],['WR','WR'],['SLOT','Slot WR'],['OUT','Outside WR'],['TE','TE']];
function dvpRow(key, team){ const v = D.dvp && D.dvp[key]; return v ? v.rows.find(r => r.team === team) : null; }
function matchupPanel(g){
  if (!D.dvp || !D.dvp.WR) return '';
  const side = (off, def) => {
    const cells = DVP_CELLS.map(([k, lbl]) => { const r = dvpRow(k, def); if (!r) return '';
      const cols = D.dvp[k].cols;
      if (r.season_rank) {
        const cls = shadeRank(r.season_rank);
        const tip = `<b>${esc(def)} vs ${lbl}s, this season</b> (${r.season_games} game${r.season_games === 1 ? '' : 's'})<br>` +
          cols.map((c, i) => `${esc(c)}: ${r.season_stats[i] == null ? '–' : f1(r.season_stats[i])} per game`).join('<br>') +
          `<br>${f1(r.season_ppr)} fantasy pts per game, ${rankText(r.season_rank)} in the league` +
          `<br><span style="opacity:.8">Blended with recent history: ${signPct(r.ratio)} vs average, ${rankText(r.rank)}</span>`;
        return `<div class="mcell" tabindex="0" data-tip="${esc(tip)}"><span class="eyebrow">${lbl}</span><span class="v ${cls}">${ord(r.season_rank)}</span><span class="muted small">${r.season_rank <= 16 ? 'most allowed' : ord(33 - r.season_rank) + ' fewest'} · ${f1(r.season_ppr)} pts/g</span><span class="muted small">blended ${signPct(r.ratio)}</span></div>`;
      }
      const cls = shadeRatio(r.ratio);
      return `<div class="mcell" title="${esc(def)} allows ${f1(r.ppr_adj)} fantasy pts per game to ${lbl}s (adjusted)"><span class="eyebrow">${lbl}</span><span class="v ${cls}">${signPct(r.ratio)}</span><span class="muted small">${rankText(r.rank)}</span></div>`; }).join('');
    const tagged = D.players.filter(p => p.g === g.id && p.t === off && p.tags && p.tags.length)
      .sort((a,b) => b.tags[0].strength - a.tags[0].strength);
    const list = tagged.length ? `<ul class="mlist">${tagged.map(p => `<li><span class="who">${esc(p.n)} <span class="pos">${esc(p.lab || p.pos)}</span></span>${p.tags.map(t => `${chip(t)}<span class="small muted">${esc(t.text)}</span>`).join(' ')}</li>`).join('')}</ul>`
      : `<p class="note">No ${esc(off)} player has a matchup outside the normal range.</p>`;
    const RN = {CB1: 'top CB', CB2: 'No. 2 CB', CB3: 'slot CB', S: 'S', LB: 'LB', DL: 'DL'};
    const dmiss = ((D.defout || {})[g.id] || {})[def] || [];
    const dline = dmiss.length ? `<p class="dmiss"><b>${esc(def)} missing:</b> ${dmiss.map(m => `${esc(m.name)} <span class="muted">(${RN[m.role] || m.role}, ${m.status === 'out' ? 'out' : 'questionable'})</span>`).join(', ')}</p>` : '';
    return `<div style="display:grid;gap:12px;align-content:start"><h3 class="eyebrow" style="margin:0">${esc(off)} offense vs ${esc(def)} defense</h3>${dline}<div class="mgrid">${cells}</div>${list}</div>`;
  };
  return `<section class="panel" aria-label="Matchups">
    <div class="phead"><h2>Matchups</h2><span class="key"><span class="mm up">▲ favorable</span><span class="mm down">▼ tough</span></span></div>
    <div class="two" style="grid-template-columns:repeat(auto-fit,minmax(280px,1fr))">${side(g.away, g.home)}${side(g.home, g.away)}</div>
    <p class="note">Big number: where the defense ranks this season in fantasy points allowed per game to that position (1st = gives up the most; green = top 8, red = bottom 8). Hover for the stats behind it. "Blended" is the steadier number: allowed vs league average, weighted toward recent games, carrying some of last season and adjusted toward average for small samples. The projections already account for the opponent, so these notes explain the numbers. In 2025–26 backtests, flagged players did not beat their projections more often than other players. "Missing" lists the defense's regular starters (60%+ of snaps lately) who are out or questionable; ▲ CB1 out / 2 DBs out / LB out flags mark the receivers offenses have historically fed more in that spot (2022–26), tracked on the Markets page.</p>
  </section>`;
}

function renderDvp(){
  const v = D.dvp && D.dvp[state.dpos];
  if (!v) return '<section class="panel"><div class="empty">No defense-vs-position data in this file. Re-run project.py.</div></section>';
  const seg = Object.entries(D.dvp).map(([k, x]) => `<button data-dpos="${k}" aria-pressed="${k===state.dpos}">${esc(x.label)}</button>`).join('');
  const bySeason = state.drank !== 'blend' && v.rows.some(r => r.season_rank);
  const rows = v.rows.slice().sort((a, b) => bySeason ? (a.season_rank || 99) - (b.season_rank || 99) : a.rank - b.rank);
  const rankSeg = `<div class="seg" role="group" aria-label="Rank by"><button data-drank="season" aria-pressed="${bySeason}">Rank: this season</button><button data-drank="blend" aria-pressed="${!bySeason}">Rank: blended</button></div>`;
  const maxDev = Math.max(.3, ...v.rows.map(r => Math.abs(r.ratio - 1)));
  const bar = r => { const d = (r.ratio - 1) / maxDev * 50, fill = d >= 0 ? `left:50%;width:${d}%;background:var(--good)` : `left:${50+d}%;width:${-d}%;background:var(--bad)`;
    return `<div class="dvbar" role="img" aria-label="${signPct(r.ratio)} vs average"><i style="${fill}"></i><span class="c"></span></div>`; };
  const statHead = v.cols.map(c => `<th>${esc(c)}</th>`).join('');
  const rawHead = `<th>Fantasy pts</th><th>${bySeason ? 'Blended rank' : 'Season rank'}</th>`;
  const body = rows.map(r => `<tr><td class="big">${bySeason ? (r.season_rank || '–') : r.rank}</td><td class="l name">${esc(r.team)}</td><td class="l">${r.faces ? esc(r.faces) : '<span class="small muted">no game</span>'}</td><td>${r.season_games ?? r.games}</td>${(r.season_stats || r.stats).map(x => `<td>${x == null ? '–' : f1(x)}</td>`).join('')}<td>${r.season_ppr == null ? '–' : f1(r.season_ppr)}</td><td class="muted">${bySeason ? r.rank : (r.season_rank || '–')}</td><td class="big">${f1(r.ppr_adj)}</td><td class="l">${bar(r)}</td><td class="big">${signPct(r.ratio)}</td></tr>`).join('');
  return `<section class="panel">
    <div class="phead"><div class="controls"><div class="seg" role="group" aria-label="Position">${seg}</div>${rankSeg}</div>
      <span class="key"><span><i class="kb" style="background:var(--good)"></i>gives up more (favorable for offenses)</span><span><i class="kb" style="background:var(--bad)"></i>gives up less (tough)</span></span></div>
    <div class="tw"><table class="t"><thead><tr><th>${bySeason ? 'Season rank' : 'Blended rank'}</th><th class="l">Defense</th><th class="l">Faces this week</th><th>Games</th>${statHead}${rawHead}<th>Adjusted pts</th><th class="l" style="min-width:150px">vs league average</th><th></th></tr></thead><tbody>${body}</tbody></table></div>
    <p class="note">Stat columns and "Fantasy pts" are this season only, per game allowed, unadjusted${v.adjusted_only ? " (slot and outside use each receiver's depth-chart alignment; games = games with alignment data)" : ''}. "Season rank" ranks those fantasy points (1 = gives up the most). "Adjusted pts" and the blended rank weight recent games more, carry some of last season and pull small samples toward league average: steadier early in the season. Fantasy points are PPR: 1 per catch, 0.1 per rushing or receiving yard, 6 per TD, 0.04 per passing yard, 4 per passing TD, −2 per interception.</p>
  </section>`;
}

/* ---------------- red zone (tags in the Touchdowns table) ---------------- */
function rzUsage(p){ const u = D.rz && D.rz.usage && D.rz.usage[state.rzscope]; return u ? u[p.k] : null; }
function rzOppTag(pos, opp){
  const r = rzDefRow(opp); if (!r || !r.pos[pos]) return '';
  const x = r.pos[pos], cls = x.ratio >= 1.12 ? 'up' : x.ratio <= 1/1.12 ? 'down' : '';
  const tip = `${opp} defense vs ${RZN[pos]} inside the 20: ${x.td} TDs allowed in ${r.games} games (${signPct(x.ratio)} vs league, ${ord(x.rank)} most). ${x.share == null ? '' : pct(x.share) + ' of the TDs they allow in the red zone go to ' + RZN[pos] + '.'}`;
  return `<span class="mm ${cls}"${cls ? '' : ' style="color:var(--muted)"'} title="${esc(tip)}">${cls === 'up' ? '▲ ' : cls === 'down' ? '▼ ' : ''}${esc(opp)} D vs ${pos} ${signPct(x.ratio)}</span>`;
}
function rzTags(p){
  const u = rzUsage(p), t = [];
  if (u){
    const g = `${u.games} game${u.games === 1 ? '' : 's'}`;
    if (u.rz_car_share != null && u.rz_car_share >= .2) t.push(`<span class="mm tagn" title="${u.rz_car} of the team's red-zone carries over ${g}">RZ carries ${pct(u.rz_car_share)}</span>`);
    if (u.rz_tgt_share != null && u.rz_tgt_share >= .1) t.push(`<span class="mm tagn" title="${u.rz_tgt} red-zone targets over ${g}">RZ targets ${pct(u.rz_tgt_share)}</span>`);
    if (u.gl_car >= 2) t.push(`<span class="mm tagn" title="Carries inside the 5 over ${g}">${u.gl_car} carries inside 5</span>`);
    t.push(`<span class="mm tagn" title="Touchdowns scored inside the 20 over ${g}">${u.rz_td} RZ TD${u.rz_td === 1 ? '' : 's'} / ${u.games}g</span>`);
  }
  const opp = rzOppTag(p.pos, p.o);
  if (opp) t.push(opp);
  return t.length ? `<div class="chips rzchips">${t.join('')}</div>` : '<span class="small muted">–</span>';
}
function rzTeamLine(team, opp){
  const r = rzDefRow(opp); if (!r) return '';
  const parts = RZP.map(ps => { const x = r.pos[ps], cls = x.ratio >= 1.12 ? 'up' : x.ratio <= 1/1.12 ? 'down' : '';
    return `<span class="${cls ? 'mm ' + cls : 'small muted'}" title="${x.td} red-zone TDs allowed to ${RZN[ps]} in ${r.games} games">${cls === 'up' ? '▲ ' : cls === 'down' ? '▼ ' : ''}${ps} ${signPct(x.ratio)}</span>`; }).join(' ');
  return `<span class="rzline">vs ${esc(opp)} D in the red zone (TD rate ${pct(r.td_rate)}): ${parts}</span>`;
}
/* ---------------- red zone ---------------- */
const RZP = ['QB','RB','WR','TE'];
const RZC = {QB:'var(--c-qb)', RB:'var(--c-rb)', WR:'var(--c-wr)', TE:'var(--c-te)'};
const RZN = {QB:'QBs', RB:'RBs', WR:'WRs', TE:'TEs'};
const sw = p => `<span class="swatch" style="background:${RZC[p]}"></span>`;
function rzDefRow(team){ const d = D.rz && D.rz.defense[state.rzscope]; return d ? d.rows.find(r => r.team === team) : null; }
function renderGames(){
  return `<div class="games"><nav class="rail" aria-label="Games">${gameList()}</nav><div class="detail">${gameDetail()}</div></div>`;
}

/* ---------------- board view ---------------- */
function renderBoard(){
  const st = STATS[state.bstat], q = state.q.trim().toLowerCase();
  let rows = D.players.filter(p => p.s[state.bstat] && p.s[state.bstat][MEAN] >= st.min && (!state.bflag || (p.tags && p.tags.length))
    && (state.bpos === 'All' || p.pos === state.bpos) && (!q || p.n.toLowerCase().includes(q) || p.t.toLowerCase() === q));
  const key = p => st.prob ? p.s[state.bstat][MEAN] : p.s[state.bstat][MED] + p.s[state.bstat][MEAN]/1e4;
  rows.sort((a,b) => key(b) - key(a));
  const opts = Object.entries(STATS).map(([k,v]) => `<option value="${k}" ${k===state.bstat?'selected':''}>${v.label}</option>`).join('');
  const pos = ['All','QB','RB','WR','TE'].map(p => `<button data-pos="${p}" aria-pressed="${p===state.bpos}">${p}</button>`).join('');
  let table;
  if (!rows.length) table = `<div class="empty">No players match. Clear the search or pick another position.</div>`;
  else if (st.prob){
    const hi = Math.min(1, niceCeil(Math.max(...rows.map(key))));
    table = `<table class="t"><thead><tr><th>#</th><th class="l">Player</th><th class="l">Pos</th><th class="sc l"><div class="axis"><span style="left:0">0%</span><span style="left:100%;transform:translateX(-100%)">${pct(hi)}</span></div></th><th>Chance</th><th>Fair odds</th></tr></thead><tbody>`
      + rows.map((p,i) => `<tr><td class="muted">${i+1}</td>${nameCell(p, st.prob ? 'tds' : state.bstat, `<small>${esc(p.t)} vs ${esc(p.o)}</small>`)}<td class="l">${posCell(p)}</td><td class="l">${pbar(p.s[state.bstat][MEAN], hi)}</td><td class="big">${pct1(p.s[state.bstat][MEAN])}</td><td>${american(p.s[state.bstat][MEAN])}</td></tr>`).join('') + '</tbody></table>';
  } else {
    const sc = scaleFor(rows, state.bstat), d = st.dec ? f1 : f0;
    table = `<table class="t"><thead><tr><th>#</th><th class="l">Player</th><th class="l">Pos</th><th class="sc l">${axis(sc)}</th><th>Median</th><th>Mean</th><th>10th</th><th>90th</th></tr></thead><tbody>`
      + rows.map((p,i) => { const s = p.s[state.bstat]; return `<tr class="${state.open === p.k ? 'opened' : ''}"><td class="muted">${i+1}</td>${nameCell(p, state.bstat === 'anytime_td' ? 'tds' : state.bstat, `<small>${esc(p.t)} vs ${esc(p.o)}</small>`)}<td class="l">${posCell(p)}</td><td class="l">${strip(s, sc, `${p.n} ${st.label.toLowerCase()}`)}</td><td class="big">${d(s[MED])}</td><td>${f1(s[MEAN])}</td><td class="muted">${d(s[P10])}</td><td class="muted">${d(s[P90])}</td></tr>` + (state.open === p.k ? `<tr class="xrow"><td colspan="8">${sliderPanel(p)}</td></tr>` : ''); }).join('') + '</tbody></table>';
  }
  return `<section class="panel">
    <div class="controls">
      <label for="bstat">Stat</label><select id="bstat">${opts}</select>
      <div class="seg" role="group" aria-label="Position">${pos}</div>
      <button class="flagbtn" data-bflag="1" aria-pressed="${state.bflag}">Matchup flags only</button>
      <input type="search" id="bsearch" placeholder="Player name or team code" value="${esc(state.q)}" aria-label="Search players">
      <span class="muted small">${rows.length} player${rows.length===1?'':'s'}</span>
    </div>
    ${st.prob ? '' : '<div class="key"><span><i class="kw"></i>8 in 10 games</span><span><i class="kb"></i>middle half</span><span><i class="km"></i>median</span></div>'}
    <div class="tw">${table}</div>
  </section>`;
}

/* ---------------- fair odds view ---------------- */
const FSTATS = {
  pass_yds:{label:'Passing yards',step:10}, att:{label:'Pass attempts',step:1}, cmp:{label:'Completions',step:1},
  pass_td:{label:'Passing TDs',step:1}, ints:{label:'Interceptions',step:1},
  rush_yds:{label:'Rushing yards',step:5}, car:{label:'Carries',step:1},
  rec_yds:{label:'Receiving yards',step:5}, rec:{label:'Receptions',step:1}, tgt:{label:'Targets',step:1},
  tds:{label:'Touchdowns (rush + rec)',step:1},
};
const GROUP_STAT = {passing:'pass_yds', rushing:'rush_yds', receiving:'rec_yds', td:'tds'};
const byKey = Object.fromEntries(D.players.map(p => [p.k, p]));
const plabel = p => `${p.n} (${p.t} vs ${p.o})`;
function probs(d, L){
  if (d.p){ let over = 0, under = 0; d.p.forEach((v,i) => { const k = d.o + i; if (k > L) over += v; else if (k < L) under += v; }); return {over, under, push: Math.max(0, 1 - over - under)}; }
  const n = d.q.length, over = d.q.filter(v => v > L).length / n, under = d.q.filter(v => v < L).length / n;
  return {over, under, push: Math.max(0, 1 - over - under)};
}
function dmed(d){
  if (d.p){ let c = 0; for (let i = 0; i < d.p.length; i++){ c += d.p[i]; if (c >= .5) return d.o + i; } return d.o + d.p.length - 1; }
  return (d.q[49] + d.q[50]) / 2;
}
const defaultLine = (stat, d) => stat === 'tds' ? .5 : Math.max(.5, Math.floor(dmed(d)) + .5);
function fairStats(p){ return Object.keys(FSTATS).filter(s => p.d[s] && (s !== 'pass_yds' && s !== 'att' && s !== 'cmp' && s !== 'pass_td' && s !== 'ints' || (p.s.att && p.s.att[MEAN] >= 5))); }
function ensureFo(){
  let fo = state.fo;
  if (!fo || !byKey[fo.key] || !byKey[fo.key].d[fo.stat]){
    const top = D.players.filter(p => p.d.rec_yds).sort((a,b) => b.s.rec_yds[MED] - a.s.rec_yds[MED])[0] || D.players.find(p => Object.keys(p.d).length);
    if (!top) return null;
    const stat = top.d.rec_yds ? 'rec_yds' : fairStats(top)[0];
    fo = state.fo = {key: top.k, stat, line: defaultLine(stat, top.d[stat])};
  }
  return fo;
}
function priceBlock(p, stat, L){
  const d = p.d[stat], r = probs(d, L), nopush = Math.max(r.over + r.under, 1e-9);
  const tdName = stat === 'tds' ? (L === .5 ? 'Anytime TD' : L === 1.5 ? '2+ touchdowns' : '') : '';
  const side = (name, pr) => `<div class="fo-side"><span class="eyebrow">${name}</span><span class="fo-p">${pct(pr)}</span><span class="fo-o">${american(pr / nopush)}</span></div>`;
  const s = p.s[stat === 'tds' ? 'anytime_td' : stat];
  const range = stat === 'tds' || !s ? '' : `Median ${f1(s[MED])} · mean ${f1(s[MEAN])} · 8 in 10 games between ${f1(s[P10])} and ${f1(s[P90])}`;
  return `<div class="fo-res">${side(tdName ? tdName + ' · yes' : 'Over ' + L, r.over)}${side(tdName ? tdName + ' · no' : 'Under ' + L, r.under)}</div>
    <p class="muted small" style="margin:0">${r.push >= .005 ? `Push (exactly ${L}) ${pct(r.push)} of games; odds exclude pushes. ` : ''}${range}</p>`;
}
function ladder(p, stat, L){
  const d = p.d[stat], step = FSTATS[stat].step;
  let lines;
  if (stat === 'tds') lines = [.5, 1.5, 2.5];
  else { const c = Math.floor(dmed(d)) + .5; lines = []; for (let k = -4; k <= 4; k++){ const v = c + k*step; if (v > 0) lines.push(v); } }
  if (!lines.includes(L)) lines.push(L);
  lines.sort((a,b) => a - b);
  return `<table class="t"><thead><tr><th class="l">Line</th><th class="l" style="min-width:160px">Chance of over</th><th>Over</th><th>Fair over</th><th>Fair under</th></tr></thead><tbody>`
    + lines.map(v => { const r = probs(d, v), np = Math.max(r.over + r.under, 1e-9);
      return `<tr${v === L ? ' class="cur"' : ''}><td class="l"><button class="plink" data-line="${v}">${v}</button></td><td class="l">${pbar(r.over, 1)}</td><td class="big">${pct(r.over)}</td><td>${american(r.over/np)}</td><td>${american(r.under/np)}</td></tr>`; }).join('')
    + '</tbody></table>';
}
/* ---------------- model agreement ---------------- */
const AG_LABEL = {pass_yds:'Passing yards', rush_yds:'Rushing yards', rec_yds:'Receiving yards', rec:'Receptions'};
function agTag(r){
  const c = shadeEdge(r.edge);
  if (r.edge >= .05) return `<span class="ag ${c}">▲ lean over</span>`;
  if (r.edge >= .02) return `<span class="ag ${c}">▲ slight over</span>`;
  if (r.edge <= -.05) return `<span class="ag ${c}">▼ lean under</span>`;
  if (r.edge <= -.02) return `<span class="ag ${c}">▼ slight under</span>`;
  return '<span class="ag ok">agree</span>';
}
function renderAgree(){
  const A = D.agree || [];
  if (!A.length) return '<section class="panel"><div class="empty">No market-blind projections for this week yet. Run project.py --blind, then rebuild this page.</div></section>';
  state.astat = state.astat || 'all'; state.aflt = state.aflt || 'all'; state.agame = state.agame || 'all';
  state.agroup = state.agroup ?? true;
  const gamesWith = D.games.filter(g => A.some(r => r.g === g.id));
  if (state.agame !== 'all' && !gamesWith.some(g => g.id === state.agame)) state.agame = 'all';
  const rows = A.filter(r => (state.agame === 'all' || r.g === state.agame) && (state.astat === 'all' || r.stat === state.astat)
    && (state.aflt === 'all' || (state.aflt === 'over' ? r.edge >= .02 : state.aflt === 'under' ? r.edge <= -.02 : Math.abs(r.edge) < .02)));
  rows.sort((a, b) => b.p - a.p);
  const seg = (key, opts) => `<div class="seg" role="group">${opts.map(([k, l]) => `<button data-${key}="${k}" aria-pressed="${state[key] === k}">${l}</button>`).join('')}</div>`;
  const nOver = A.filter(r => r.edge >= .02).length, nUnder = A.filter(r => r.edge <= -.02).length;
  const bar = p => { const x = Math.max(0, Math.min(1, (p - .35) / .3)) * 100; return `<div class="agbar" role="img" aria-label="${pct(p)}"><i style="width:${x}%"></i><b></b></div>`; };
  const rowHtml = r => `<tr class="${shadeEdge(r.edge) ? 'bg-' + shadeEdge(r.edge) : ''}"><td class="l name">${esc(r.n)} <span class="small muted">${esc(r.t)} vs ${esc(r.o)}</span></td><td class="l"><span class="pos">${esc(r.pos)}</span></td><td class="l">${AG_LABEL[r.stat]}</td><td>${f1(r.vmed)}</td><td>${f1(r.bmed)}</td><td class="${r.bmed > r.vmed ? 'pos-d' : r.bmed < r.vmed ? 'neg-d' : ''}">${r.bmed >= r.vmed ? '+' : '−'}${f1(Math.abs(r.bmed - r.vmed))}</td><td class="l">${bar(r.p)}</td><td class="big">${pct(r.p)}</td><td class="l">${agTag(r)}</td></tr>`;
  // grouped by game (kickoff order) unless one game is picked or grouping is off
  let body;
  if (state.agame === 'all' && state.agroup) {
    body = gamesWith.map(g => { const rs = rows.filter(r => r.g === g.id); if (!rs.length) return '';
      const nO = rs.filter(r => r.edge >= .02).length, nU = rs.filter(r => r.edge <= -.02).length;
      return `<tr class="grp"><td colspan="9">${esc(g.away)} @ ${esc(g.home)} <span class="small muted" style="font-family:var(--body);font-weight:400;letter-spacing:0">${esc(g.kickoff)} · ${nO} lean over · ${nU} lean under</span></td></tr>` + rs.map(rowHtml).join(''); }).join('');
  } else body = rows.map(rowHtml).join('');
  if (!rows.length) body = '<tr><td colspan="9" class="l muted">No lines match these filters.</td></tr>';
  const gameSeg = `<div class="controls"><label for="agame">Game</label><select id="agame"><option value="all"${state.agame === 'all' ? ' selected' : ''}>All games</option>${gamesWith.map(g => `<option value="${esc(g.id)}"${state.agame === g.id ? ' selected' : ''}>${esc(g.away)} @ ${esc(g.home)} · ${esc(g.kickoff)}</option>`).join('')}</select>${state.agame === 'all' ? `<div class="seg" role="group"><button data-agroup="1" aria-pressed="${state.agroup}">Grouped by game</button><button data-agroup="0" aria-pressed="${!state.agroup}">One list</button></div>` : ''}</div>`;
  return `<section class="panel">${gameSeg}
    <div class="phead"><div class="controls">${seg('astat', [['all','All'], ['pass_yds','Pass yds'], ['rush_yds','Rush yds'], ['rec_yds','Rec yds'], ['rec','Receptions']])}${seg('aflt', [['all','All'], ['over',`Blind higher (${nOver})`], ['under',`Blind lower (${nUnder})`], ['agree','Agree']])}</div></div>
    <div class="tw"><table class="t"><thead><tr><th class="l">Player</th><th class="l">Pos</th><th class="l">Stat</th><th>Vegas model median</th><th>Blind model median</th><th>Gap</th><th class="l" style="min-width:140px">Chance to beat Vegas median</th><th></th><th class="l">Read</th></tr></thead><tbody>${body}</tbody></table></div>
    <p class="note">Both models simulate every player; this compares them on the same line. "Chance to beat Vegas median" starts from the Vegas model's own chance (a little under 50% for catches and other counts, where landing exactly on the median is common) and moves it 45% of the way toward the blind model's chance: the weight that fit 2025 results and improved 2026 out of sample. Green rows (blind model 5+ points more bullish) and red rows (5+ points more bearish) are the strongest leans. History, 2025–26: when the blind model was higher, players beat the Vegas median 49–50% of the time vs 44–48% otherwise; when it was much lower, 33–49%. The edge is real but small: nobody here is better than about 55%. When the models agree, players hit their projection on average.</p>
  </section>`;
}

function renderFair(){
  const fo = ensureFo(); if (!fo) return '<section class="panel"><div class="empty">No player distributions in this file. Re-run project.py to include them.</div></section>';
  const p = byKey[fo.key];
  const opts = D.players.filter(x => Object.keys(x.d).length).map(x => `<option value="${esc(plabel(x))}"></option>`).join('');
  const stats = fairStats(p).map(s => `<option value="${s}" ${s===fo.stat?'selected':''}>${FSTATS[s].label}</option>`).join('');
  return `<section class="panel">
    <div class="controls">
      <label for="fo-player">Player</label><input id="fo-player" list="fo-list" value="${esc(plabel(p))}" autocomplete="off"><datalist id="fo-list">${opts}</datalist>
      <label for="fo-stat">Stat</label><select id="fo-stat">${stats}</select>
      <label for="fo-line">Line</label><input id="fo-line" type="number" step="0.5" min="0" value="${fo.line}">
    </div>
    <div id="fo-result">${priceBlock(p, fo.stat, fo.line)}</div>
  </section>
  <section class="panel"><div class="phead"><h2>Nearby lines</h2><span class="muted small">No-vig prices from the simulations. Click a line to price it above.</span></div>
    <div class="tw" id="fo-ladder">${ladder(p, fo.stat, fo.line)}</div></section>`;
}
function refreshFair(){
  const p = byKey[state.fo.key];
  document.getElementById('fo-result').innerHTML = priceBlock(p, state.fo.stat, state.fo.line);
  document.getElementById('fo-ladder').innerHTML = ladder(p, state.fo.stat, state.fo.line);
  save();
}
function openFair(key, stat){
  const p = byKey[key]; if (!p || !Object.keys(p.d).length) return;
  if (!p.d[stat]) stat = fairStats(p)[0];
  state.fo = {key, stat, line: defaultLine(stat, p.d[stat])};
  state.view = 'fair'; render(); window.scrollTo({top: 0});
}
const nameCell = (p, stat, extra='') => `<td class="l name"><button class="plink${state.open === p.k ? ' open' : ''}" data-price="${esc(p.k)}" data-pstat="${stat}" title="Slide a line for ${esc(p.n)}"><span class="caret">${state.open === p.k ? '▾' : '▸'}</span>${esc(p.n)}</button>${agChip(p, stat)}${window.MKT && MKT.flag ? MKT.flag(p.g, p.n) : ''}${window.MKT && state.gm[p.g] === 'blind' ? MKT.patChips(p.g, p.id, p.n) : ''}${extra}${chips(p)}</td>`;
// Market-blind lean, inline (same rule as the Model agreement tab)
const AGK = Object.fromEntries((D.agree || []).map(r => [r.k + '|' + r.stat, r]));
function agChip(p, stat){
  const r = AGK[p.k + '|' + stat]; if (!r || Math.abs(r.edge) < .02) return '';
  const up = r.edge > 0, tip = `Market-blind model ${up ? 'higher' : 'lower'}: ${f1(r.bmed)} vs ${f1(r.vmed)}. Chance to beat the median: ${pct(r.p)}.`;
  return `<span class="lean ${shadeEdge(r.edge)}" title="${esc(tip)}">${up ? '▲' : '▼'} blind</span>`;
}
// Slider panel: drag the line, see over/under % and fair odds from the simulated distribution.
function sliderRange(d, stat){
  if (stat === 'tds') return {lo: .5, hi: 2.5, step: 1};
  if (d.p){ const lo = d.o, hi = d.o + d.p.length - 1; return {lo: Math.max(.5, lo + .5), hi: Math.max(1.5, hi - .5), step: 1}; }
  const step = FSTATS[stat] && FSTATS[stat].step >= 5 ? 1 : .5;
  return {lo: Math.max(.5, Math.floor(d.q[2]) + .5), hi: Math.ceil(d.q[96]) + .5, step};
}
// build-your-own parlay (slip.js): legs from this page carry the game's saved simulations so same-game legs link
const SLIP_LAB = {pass_yds: 'pass yds', att: 'pass att', cmp: 'completions', pass_td: 'pass TDs', ints: 'INTs', rush_yds: 'rush yds', car: 'carries', rec_yds: 'rec yds', rec: 'receptions', tgt: 'targets', tds: 'TDs'};
const glabel = gid => { const g = D.games.find(x => x.id === gid); return g ? `${g.away} @ ${g.home}` : ''; };
function slipLeg(p, stat, L, side, prob){
  const model = state.gm[p.g] === 'blind' ? 'blind' : 'vegas';
  const lab = stat === 'tds' && L === .5 ? (side === 'over' ? `${p.n} anytime TD` : `${p.n} no TD`) : `${p.n} ${side} ${L} ${SLIP_LAB[stat] || stat}`;
  return {id: `nfl|${p.g}|${p.k}|${stat}|${side}|${L}|${model}`, sport: 'nfl', g: p.g, glab: glabel(p.g), lab, p: prob, side, stat, line: L, pk: p.k, model, draws: `nfl/draws/${p.g}.json`};
}
function sliderReadout(p, stat, L){
  const d = p.d[stat], r = probs(d, L), np = Math.max(r.over + r.under, 1e-9);
  const ov = r.over / np, un = r.under / np;
  const so = shadeP(ov), su = shadeP(un);
  return `<div class="sl-out"><div class="sl-side over"><span>Over ${L}</span><b class="${so}">${pct(r.over)}</b><em>${american(ov)}</em></div>
    <div class="sl-meter" aria-hidden="true"><i class="f-${so || 'n'}" style="width:${r.over * 100}%"></i><i class="f-${su || 'n'}" style="width:${r.under * 100}%"></i></div>
    <div class="sl-side under"><span>Under ${L}</span><b class="${su}">${pct(r.under)}</b><em>${american(un)}</em></div></div>
    ${r.push >= .005 ? `<p class="muted small" style="margin:0">Exactly ${L}: ${pct(r.push)} (a push; odds exclude it)</p>` : ''}
    ${window.SLIP ? `<div class="small muted" style="display:flex;gap:6px;align-items:center;flex-wrap:wrap">My parlay: ${SLIP.btn(slipLeg(p, stat, L, 'over', r.over), 'Over ' + L)}${SLIP.btn(slipLeg(p, stat, L, 'under', r.under), 'Under ' + L)}</div>` : ''}`;
}
const MK_KIND = {rec_yds: ['rec_yds'], rush_yds: ['rush_yds'], pass_yds: ['pass_yds'], rec: ['rec'], tds: ['anytime_td', 'first_td']};
function sliderPanel(p){
  const stats = fairStats(p); if (!stats.length) return '<p class="muted small">No distribution saved for this player.</p>';
  let st = state.ostat && stats.includes(state.ostat) ? state.ostat : stats[0];
  state.ostat = st;
  const d = p.d[st], rg = sliderRange(d, st);
  if (state.oline == null || state.okey !== p.k + st) { state.oline = defaultLine(st, d); state.okey = p.k + st; }
  const L = Math.min(Math.max(state.oline, rg.lo), rg.hi);
  const sum = p.s[st === 'tds' ? 'anytime_td' : st];
  const info = st === 'tds' || !sum ? '' : `Median <b>${f1(sum[MED])}</b> · 8 in 10 games between ${f1(sum[P10])} and ${f1(sum[P90])}`;
  return `<div class="slider" data-sk="${esc(p.k)}">
    <div class="sl-top"><div class="seg" role="group" aria-label="Stat">${stats.map(x => `<button data-ostat="${x}" aria-pressed="${x === st}">${FSTATS[x].label}</button>`).join('')}</div><span class="muted small">${info}</span></div>
    <div class="sl-row"><label for="sl-in">Line</label><button class="sl-step" data-ostep="-1" aria-label="Lower the line">−</button>
      <input id="sl-in" type="range" min="${rg.lo}" max="${rg.hi}" step="${rg.step}" value="${L}" aria-label="Line">
      <button class="sl-step" data-ostep="1" aria-label="Raise the line">+</button><b class="sl-val" id="sl-val">${L}</b></div>
    <div id="sl-res">${sliderReadout(p, st, L)}</div>
    ${window.MKT ? MKT.player(p.g, p.id, p.n, MK_KIND[st] || [], state.gm[p.g] === 'blind' ? 'blind' : 'vegas', true, state.gm[p.g] === 'blind') : ''}</div>`;
}
const playerByKey = k => (D.alt && D.alt.players.find(x => x.k === k && state.gm[x.g] === 'blind')) || byKey[k];

/* ---------------- props view ---------------- */
function renderProps(){
  const slot = {};
  D.games.forEach((g,i) => { slot[g.home] = slot[g.away] = i; });
  const rows = D.props.map((r,i) => [r,i]).sort(([a,i],[b,j]) => (slot[a.team] ?? 1e9) - (slot[b.team] ?? 1e9) || i - j).map(([r]) => r);
  const body = rows.map(r => {
    if (r.p_over == null) return `<tr><td class="l name">${esc(r.player)}</td><td class="l">${esc(STATS[r.stat]?.label || r.stat)}</td><td>${esc(r.line)}</td><td colspan="5" class="l muted">${esc(r.note || 'Not projected')}</td></tr>`;
    const p = r.p_over / Math.max(r.p_over + r.p_under, 1e-9);
    const fill = p >= .5 ? `left:50%;width:${(p-.5)*100}%;background:var(--accent)` : `left:${p*100}%;width:${(.5-p)*100}%;background:var(--away)`;
    const st = STATS[r.stat] || {};
    return `<tr><td class="l name">${esc(r.player)}<small>${esc(r.team)}</small></td><td class="l">${esc(st.label || r.stat)}</td><td class="big">${esc(r.line)}</td><td>${st.prob ? pct(r.proj_mean) : (st.dec ? f1(r.proj_median) : f0(r.proj_median))}</td>
      <td class="l"><div class="dv" role="img" aria-label="Over ${pct(p)}"><i style="${fill}"></i><span class="c"></span></div></td>
      <td class="big">${pct(p)}</td><td>${esc(r.fair_over > 0 ? '+'+r.fair_over : r.fair_over)}</td><td>${esc(r.fair_under > 0 ? '+'+r.fair_under : r.fair_under)}</td></tr>`;
  }).join('');
  return `<section class="panel">
    <div class="phead"><h2>Your lines</h2><span class="key"><span><i class="kb" style="background:var(--accent)"></i>leans over</span><span><i class="kb" style="background:var(--away)"></i>leans under</span></span></div>
    <div class="tw"><table class="t"><thead><tr><th class="l">Player</th><th class="l">Stat</th><th>Line</th><th>Proj</th><th class="l" style="min-width:160px">Under ← 50% → Over</th><th>Over</th><th>Fair over</th><th>Fair under</th></tr></thead><tbody>${body}</tbody></table></div>
  </section>`;
}

/* ---------------- shell ---------------- */
function render(){
  for (const b of document.querySelectorAll('.tabs button')) b.setAttribute('aria-selected', b.dataset.view === state.view);
  const main = document.getElementById('main');
  main.innerHTML = state.view === 'board' ? renderBoard() : state.view === 'props' ? renderProps()
    : state.view === 'books' ? (window.MKT && MKT.oddsView ? MKT.oddsView(D.games.map(g => ({id: g.id, label: `${g.away} @ ${g.home}`, away: g.away, home: g.home}))) : '<div class="empty">Loading prices…</div>')
    : state.view === 'parlays' ? renderParlays() : state.view === 'fair' ? renderFair() : state.view === 'agree' ? renderAgree() : state.view === 'dvp' ? renderDvp() : renderGames();
  save();
  const cur = document.querySelector('.rail .game-btn[aria-current="true"]');
  if (cur){ const r = cur.parentElement, rr = r.getBoundingClientRect(), cr = cur.getBoundingClientRect(); if (r.scrollWidth > r.clientWidth && (cr.left < rr.left || cr.right > rr.right)) r.scrollLeft += cr.left - rr.left - 12; }
}
document.addEventListener('change', e => { if (e.target.id === 'agame'){ state.agame = e.target.value; render(); } });
document.addEventListener('click', e => {
  const t = e.target.closest('button'); if (!t) return;
  if (t.dataset.price){ const k = t.dataset.price; state.open = state.open === k ? null : k;
    state.ostat = {tds: 'tds'}[t.dataset.pstat] || t.dataset.pstat; state.okey = null; render(); }
  else if (t.dataset.ostat){ state.ostat = t.dataset.ostat; state.okey = null; render(); }
  else if (t.dataset.ostep){ const inp = document.getElementById('sl-in'); if (inp){ inp.value = Math.min(+inp.max, Math.max(+inp.min, +inp.value + (+t.dataset.ostep) * (+inp.step))); inp.dispatchEvent(new Event('input', {bubbles: true})); } }
  else if (t.dataset.line){ state.fo.line = +t.dataset.line; document.getElementById('fo-line').value = state.fo.line; refreshFair(); }
  else if (t.dataset.view){ state.view = t.dataset.view; render(); }
  else if (t.dataset.gtab){ state.gtab = t.dataset.gtab; render(); }
  else if (t.dataset.script){ state.script = t.dataset.script; render(); }
  else if (t.dataset.game){ state.game = t.dataset.game; render(); }
  else if (t.dataset.gmodel){ state.gm[state.game] = t.dataset.gmodel; render(); }
  else if (t.dataset.group){ state.group = t.dataset.group; render(); }
  else if (t.dataset.pos){ state.bpos = t.dataset.pos; render(); }
  else if (t.dataset.dpos){ state.dpos = t.dataset.dpos; render(); }
  else if (t.dataset.astat){ state.astat = t.dataset.astat; render(); }
  else if (t.dataset.aflt){ state.aflt = t.dataset.aflt; render(); }
  else if (t.dataset.agroup){ state.agroup = t.dataset.agroup === '1'; render(); }
  else if (t.dataset.drank){ state.drank = t.dataset.drank; render(); }
  else if (t.dataset.rzscope){ state.rzscope = t.dataset.rzscope; render(); }
  else if (t.dataset.bflag){ state.bflag = !state.bflag; render(); }
});
document.addEventListener('change', e => {
  const id = e.target.id;
  if (id === 'bstat'){ state.bstat = e.target.value; render(); }
  else if (id === 'fo-stat'){ const p = byKey[state.fo.key]; state.fo.stat = e.target.value; state.fo.line = defaultLine(state.fo.stat, p.d[state.fo.stat]); render(); }
  else if (id === 'fo-player'){
    const p = D.players.find(x => plabel(x) === e.target.value) || D.players.find(x => x.n.toLowerCase() === e.target.value.trim().toLowerCase());
    if (p) openFair(p.k, state.fo.stat); else e.target.value = plabel(byKey[state.fo.key]);
  }
});
document.addEventListener('input', e => {
  if (e.target.id === 'sl-in'){ const box = e.target.closest('.slider'), p = playerByKey(box.dataset.sk); state.oline = +e.target.value;
    document.getElementById('sl-val').textContent = state.oline; document.getElementById('sl-res').innerHTML = sliderReadout(p, state.ostat, state.oline); return; }
  if (e.target.id === 'fo-line'){ const v = parseFloat(e.target.value); if (!isNaN(v)){ state.fo.line = v; refreshFair(); } return; }
  if (e.target.id === 'fo-player'){ const p = D.players.find(x => plabel(x) === e.target.value); if (p) openFair(p.k, state.fo.stat); return; }
  if (e.target.id !== 'bsearch') return;
  state.q = e.target.value; const pos = e.target.selectionStart; render();
  const s = document.getElementById('bsearch'); s.focus(); s.setSelectionRange(pos, pos);
});
const tip = document.getElementById('tip');
function showTip(el, x, y){
  tip.innerHTML = el.dataset.tip; tip.hidden = false;
  const r = tip.getBoundingClientRect();
  tip.style.left = Math.min(Math.max(8, x - r.width/2), innerWidth - r.width - 8) + 'px';
  tip.style.top = (y - r.height - 12 < 8 ? y + 18 : y - r.height - 12) + 'px';
}
document.addEventListener('pointermove', e => { const el = e.target.closest('[data-tip]'); if (el) showTip(el, e.clientX, e.clientY); else tip.hidden = true; });
document.addEventListener('focusin', e => { const el = e.target.closest('[data-tip]'); if (el){ const r = el.getBoundingClientRect(); showTip(el, r.left + r.width/2, r.top); } });
document.addEventListener('focusout', () => { tip.hidden = true; });
render();
</script>
"""


BASE_CSS = TEMPLATE.split("<style>", 1)[1].split("</style>", 1)[0]
FONTS = TEMPLATE.split("<title>", 1)[1].split("<style>", 1)[0].split("\n", 1)[1]


if __name__ == "__main__":
    main()
