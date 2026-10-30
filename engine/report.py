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
    btd = json.load(open(f"{b}_box.json")) if os.path.exists(f"{b}_box.json") else {}
    tr = {}
    for r in teams.to_dict("records"):
        tr.setdefault(r["game_id"], {})[r["team"]] = {k: (round(v, 3) if isinstance(v, float) else v) for k, v in r.items()}
    pl = []
    for (gid, team, opp, name, pid, pos), d in players.groupby(["game_id", "team", "opp", "player", "player_id", "pos"], sort=False):
        key = f"{gid}|{team}|{name}"
        mp = match["players"].get(key, {})
        pl.append(dict(k=key, g=gid, t=team, o=opp, n=name, id=pid, pos=pos, d=bd.get(key, {}), lab=mp.get("label"), tags=mp.get("tags", []), tdm=btd.get(key),
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
    btd = json.load(open(f"{stem}_box.json")) if os.path.exists(f"{stem}_box.json") else {}     # mean rushing / receiving TDs
    for (gid, team, opp, name, pid, pos), d in players.groupby(
            ["game_id", "team", "opp", "player", "player_id", "pos"], sort=False):
        key = f"{gid}|{team}|{name}"
        mp = match["players"].get(key, {})
        plist.append(dict(k=key, g=gid, t=team, o=opp, n=name, id=pid, pos=pos, d=dists.get(key, {}),
                          lab=mp.get("label"), tags=mp.get("tags", []), tdm=btd.get(key),
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
.wrap{max-width:1560px;margin:0 auto;display:grid;gap:22px;grid-template-columns:minmax(0,1fr)}
button,input,select{font:inherit;color:inherit}
:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.eyebrow{font-size:12px;letter-spacing:.09em;text-transform:uppercase;color:var(--muted);font-weight:500}
h1{font-family:var(--display);font-weight:700;font-size:44px;line-height:1;margin:6px 0 0;letter-spacing:.005em;text-transform:uppercase;text-wrap:balance}
h2{font-family:var(--display);font-weight:600;font-size:22px;line-height:1.1;margin:0;text-transform:uppercase;letter-spacing:.02em}
header{display:flex;flex-wrap:wrap;align-items:flex-end;justify-content:space-between;gap:16px}
.tabs{display:flex;gap:4px;background:var(--sunk);padding:4px;border-radius:8px;max-width:100%;overflow-x:auto;scrollbar-width:none}
.tabs::-webkit-scrollbar{display:none}
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
.nbxgrid{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:20px}.nbxteam h3{margin:4px 0 6px;font-family:var(--display);font-size:18px}
.ladsw{display:flex;gap:6px;margin:4px 0 8px}.ladsw button{border:1px solid var(--faint);background:none;color:var(--ink);border-radius:999px;padding:4px 12px;font:600 13px var(--body);cursor:pointer}.ladsw button[aria-pressed="true"]{background:var(--ink);color:var(--bg);border-color:var(--ink)}
.ladgm{border-top:1px solid var(--faint);padding:10px 0 2px}.ladgh{font-size:14.5px;margin-bottom:2px}.ladgm .lad:first-child{border-top:0}
table.nbx{margin-bottom:10px}.bxo{background:rgba(52,199,123,.18);color:var(--good);border-radius:4px;padding:0 4px}.bxos{display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin:0 0 12px}.bxoc{border:1px solid var(--faint);border-radius:999px;padding:2px 9px;font-size:12.5px;white-space:nowrap}.bxoc b{color:var(--good)}.bxoc i{font-style:normal;color:var(--muted)}table.nbx td,table.nbx th{text-align:right;white-space:nowrap}table.nbx .l{text-align:left;white-space:normal}table.nbx th.l{color:var(--muted)}table.nbx .bxp{font-weight:700}
@media(max-width:820px){.nbxgrid{grid-template-columns:minmax(0,1fr)}}
@media(max-width:560px){table.nbx td,table.nbx th{padding-left:5px;padding-right:5px}}
table.t{width:100%;border-collapse:collapse;font-size:14px}
.t th{font-size:11px;letter-spacing:.07em;text-transform:uppercase;color:var(--muted);font-weight:500;text-align:right;padding:6px 8px;border-bottom:1px solid var(--faint);white-space:nowrap;vertical-align:bottom}
.t td{padding:7px 8px;border-bottom:1px solid var(--faint);text-align:right;white-space:nowrap}
.t th.l,.t td.l{text-align:left}
.t td.name{font-weight:500;width:1%;padding-right:10px}   /* shrink to the longest name so the position tag sits beside it */
.t td.name small{display:block;font-weight:400;color:var(--muted);font-size:12px}
.t .grp td{border-bottom:0;padding-top:16px;text-align:left;font-family:var(--display);font-weight:600;font-size:17px;letter-spacing:.04em}
.t td.big{font-weight:600}
.t th.sc{width:32%;min-width:130px}
.tag.arch{display:inline-block;font-size:11px;font-weight:600;color:var(--muted);border:1px solid var(--faint);border-radius:4px;padding:0 6px;margin:3px 4px 0 0;cursor:help}
.mxw{display:flex;flex-wrap:wrap;gap:3px 6px;margin:2px 0 0 18px}.mxl{font-size:11px;font-weight:700;white-space:nowrap;cursor:help}.mxl i{font-style:normal;font-weight:500;color:var(--muted)}.mxl.up{color:var(--good)}.mxl.dn{color:var(--bad)}
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
.dch .good,.dcs .good{color:var(--good)}.dcp li.pth{margin-top:4px}.dcp li.pth:first-child{margin-top:0}.psc{font-size:12px;color:var(--muted);margin-left:0!important}.psc i{font-style:normal;margin-left:4px}.dch .bad{color:var(--bad)}
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
.sck{margin:2px 0;justify-self:start;width:fit-content}.dcp li.lwhy{gap:4px;flex-wrap:wrap;margin:-2px 0 2px 0}.dcp li.lwhy span{margin-left:0;font-size:11px}.scc .ladh b{font-size:15px}.ladc.up{color:var(--good);border-color:color-mix(in srgb,var(--good) 50%,transparent)}.ladc.dn{color:var(--bad);border-color:color-mix(in srgb,var(--bad) 50%,transparent)}
.sct{width:100%;border-collapse:collapse;font-size:13px;font-variant-numeric:tabular-nums}.sct th{font-size:11px;font-weight:600;color:var(--muted);text-align:right;padding:2px 4px}.sct th:first-child,.sct td:first-child{text-align:left}
.sct td{text-align:right;padding:4px;border-top:1px solid var(--faint);white-space:nowrap}.sct caption{text-align:left;font-size:12px;color:var(--muted);padding-bottom:2px}
.sct td a{color:inherit}.sct small{display:block;font-size:10.5px;color:var(--muted)}.sct .mainl{font-size:10px;color:var(--muted);margin-left:4px}
.offm{display:inline-block;font-size:10px;font-weight:700;color:#F2C14E;border:1px solid currentColor;border-radius:4px;padding:0 4px;margin-top:2px}
.offbox{display:grid;gap:6px;margin-top:10px}.offl{display:flex;flex-wrap:wrap;gap:6px}
.offc{border:1px solid var(--faint);border-radius:999px;padding:3px 10px;font-size:12.5px}.offc a{color:inherit;text-decoration:none}.offc i{font-style:normal;color:var(--muted);margin-left:4px}
.offc.cheap{border-color:color-mix(in srgb,var(--good) 45%,transparent)}.offc.cheap b{color:var(--good)}.offc.rich b{color:var(--bad)}.offc.mine{border-width:2px}
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
/* Touchdowns tab: red-zone usage table (nfl_page.js tdBoard) */
.tdbar{display:flex;flex-wrap:wrap;gap:8px 14px;align-items:center;margin:0 0 10px}
.tdw{position:relative}
.tdt{min-width:1040px}
.tdt td,.tdt th{padding-inline:4px}
.tdany{display:inline-flex;flex-direction:column;align-items:center;gap:2px}
.tdt th,.tdt td{text-align:center;vertical-align:middle}
.tdt th{text-transform:none;letter-spacing:0;font-size:11.5px}
.tdt .gh th{text-transform:none;letter-spacing:0;font-size:12.5px;color:var(--ink);font-weight:600;border-bottom:1px solid var(--faint);padding-bottom:4px}
.tdt .gs{border-left:1px solid var(--faint)}
.tdt th .tds{all:unset;cursor:pointer;display:inline-flex;align-items:center;gap:4px;line-height:1.2;white-space:normal;max-width:84px;justify-content:center;text-align:center}
.tdt th .tds:focus-visible{outline:2px solid var(--accent);outline-offset:2px;border-radius:3px}
.tdt th .tds .ar{font-size:9px;opacity:.45}
.tdt th .tds.on{color:var(--ink)}.tdt th .tds.on .ar{opacity:1;color:var(--accent)}
.tdt .tdname{position:sticky;left:0;z-index:2;background:var(--surface);text-align:left;min-width:180px;max-width:230px;width:1%;box-shadow:1px 0 0 var(--faint)}
.tdt thead .tdname{z-index:3}
.tdp{display:flex;gap:10px;align-items:center}
.tdpn{display:flex;flex-direction:column;align-items:flex-start;gap:1px;min-width:0}
.tdpn .plink{font-weight:600}
.tdsub{font-size:11.5px;color:var(--muted);white-space:nowrap}
.tchip{flex:0 0 auto;display:inline-flex;align-items:center;justify-content:center;width:34px;height:30px;border-radius:7px;font-size:10.5px;font-weight:700;letter-spacing:.02em;box-shadow:inset 0 0 0 1px rgba(255,255,255,.14)}
.tdt td.hv{padding-inline:4px}
.hc{display:block;min-width:38px;padding:5px 0;border-radius:6px;font-weight:600;background:rgba(46,158,91,var(--a,.12));color:var(--ink)}
.hc.z{background:rgba(214,69,69,.16);color:var(--bad)}
.ring{position:relative;display:inline-grid;place-items:center;width:38px;height:38px}
.ring svg{position:absolute;inset:0}
.ring b{position:relative;font-size:10.5px}
.sb{display:inline-flex;flex-direction:column;align-items:flex-start;gap:4px;min-width:56px}
.sb > span{font-weight:600;font-size:12.5px}
.sb i{position:relative;display:block;width:100%;height:4px;background:var(--sunk);border-radius:2px}
.sb i b{position:absolute;top:0;bottom:0;border-radius:2px}
.sb i s{position:absolute;left:50%;top:-2px;bottom:-2px;width:1px;background:var(--muted);opacity:.5}
.sb i.lf b{left:0;background:#a8822f}.sb i.lf b.mu{background:var(--muted)}
.sb .good{color:var(--good)}.sb .bad{color:var(--bad)}
.tdr{display:inline-flex;flex-direction:column;align-items:center;gap:3px}
.tdr .pill{display:inline-block;padding:2px 8px;border-radius:6px;font-weight:700;font-size:12.5px;box-shadow:inset 0 0 0 1px var(--faint)}
.tdr .pill.good{color:var(--good);box-shadow:inset 0 0 0 1px var(--good)}.tdr .pill.bad{color:var(--bad);box-shadow:inset 0 0 0 1px var(--bad)}
.tdc{display:inline-flex;flex-direction:column;align-items:flex-start;gap:2px}
.tdc .dots{font-size:9px;letter-spacing:1px;color:#a8822f}
.xv{font-variant-numeric:tabular-nums}
@media (max-width:640px){.tdt .tdname{min-width:150px}.tdsub{white-space:normal}.tchip{width:30px;height:26px;font-size:9.5px}}
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
__JS__</script>
"""
# The page script lives in nfl_page.js (owner 2026-10-23: scripts out of Python strings, so they can be syntax-checked
# and quotes need no care); pasted in at build so each week's page stays self-contained.
TEMPLATE = TEMPLATE.replace("__JS__", open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "nfl_page.js"), encoding="utf-8").read())



BASE_CSS = TEMPLATE.split("<style>", 1)[1].split("</style>", 1)[0]
FONTS = TEMPLATE.split("<title>", 1)[1].split("<style>", 1)[0].split("\n", 1)[1]


if __name__ == "__main__":
    main()
