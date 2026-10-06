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
    return dict(teams=tr, players=pl)


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
.note{font-size:12.5px;color:var(--muted);margin:0;max-width:75ch}
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
@media (max-width:1100px){.games{grid-template-columns:1fr}.rail{position:static;display:flex;overflow-x:auto;gap:6px;padding-bottom:4px}.game-btn{min-width:180px}}
@media (max-width:860px){
  .games{grid-template-columns:1fr}
  .rail{position:static;display:flex;overflow-x:auto;gap:6px;padding-bottom:4px}
  .game-btn{min-width:180px}
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

const state = {view:'games', game:D.games[0]?.id, group:'receiving', bstat:'rec_yds', bpos:'All', q:'', bflag:false, dpos:'WR', rzscope:'two'};
try { Object.assign(state, JSON.parse(localStorage.getItem('simsheet-ui') || '{}')); } catch (e) {}
if (!D.games.some(g => g.id === state.game)) state.game = D.games[0]?.id;
if (['games','board','dvp','fair','props','agree'].includes(location.hash.slice(1))) state.view = location.hash.slice(1);
if (state.view === 'props' && !D.props.length) state.view = 'games';
if (state.view === 'rz') { state.view = 'games'; state.group = 'td'; }
const save = () => { try { localStorage.setItem('simsheet-ui', JSON.stringify({view:state.view, group:state.group, bstat:state.bstat, bpos:state.bpos, fo:state.fo, dpos:state.dpos, rzscope:state.rzscope})); } catch (e) {} };

document.getElementById('h1').textContent = `Week ${D.week} Sim Sheet`;
if (D.subtitle){ const n = document.getElementById('subnote'); n.textContent = D.subtitle; n.hidden = false; }
document.getElementById('sitenav').innerHTML = (D.nav || []).map(([label, href, cur]) => `<a href="${esc(href)}"${cur ? ' aria-current="page"' : ''}>${esc(label)}</a>`).join('');
document.getElementById('sub').textContent = `${D.season} season · ${D.games.length} game${D.games.length===1?'':'s'}` + (D.sims ? ` · ${D.sims.toLocaleString()} simulations each` : '') + ` · built ${D.generated}`;
document.getElementById('foot').innerHTML = `Every number comes from simulated games anchored to the Vegas spread and total. Medians are the 50/50 point: half the simulations land above, half below. Bars show where 8 in 10 simulations landed (10th to 90th percentile), with the darker band holding the middle half. Fair odds are the model's no-vig price. Rebuild this page each time <code>project.py</code> runs.`;
if (!D.props.length) document.getElementById('tab-props').hidden = true;

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
  let body = '';
  for (const team of [g.away, g.home]){
    const tr = rows.filter(p => p.t === team).sort((a,b) => by(b) - by(a));
    const oppTeam = team === g.home ? g.away : g.home;
    body += `<tr class="grp"><td colspan="${G.head.length+2}">${esc(team)}${state.group === 'td' ? rzTeamLine(team, oppTeam) : ''}</td></tr>`;
    body += tr.length ? tr.map(p => `<tr class="${state.open === p.k ? 'opened' : ''}">${nameCell(p, GROUP_STAT[state.group])}<td class="l">${posCell(p)}</td>${G.cells(p, sc).map((c,i) => `<td class="${i===scIdx?'l':''}">${c}</td>`).join('')}</tr>`
        + (state.open === p.k ? `<tr class="xrow"><td colspan="${G.head.length+2}">${sliderPanel(p)}</td></tr>` : '')).join('')
      : `<tr><td colspan="${G.head.length+2}" class="l muted small">No players with meaningful volume.</td></tr>`;
  }
  return `<div class="tw"><table class="t"><thead><tr><th class="l">Player</th><th class="l">Pos</th>${headCells}</tr></thead><tbody>${body}</tbody></table></div>`;
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
function gameDetail(){
  const g = D.games.find(x => x.id === state.game); if (!g) return '<div class="empty">No games.</div>';
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
const nameCell = (p, stat, extra='') => `<td class="l name"><button class="plink${state.open === p.k ? ' open' : ''}" data-price="${esc(p.k)}" data-pstat="${stat}" title="Slide a line for ${esc(p.n)}"><span class="caret">${state.open === p.k ? '▾' : '▸'}</span>${esc(p.n)}</button>${agChip(p, stat)}${window.MKT && state.gm[p.g] === 'blind' ? MKT.patChips(p.g, p.id, p.n) : ''}${extra}${chips(p)}</td>`;
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
function sliderReadout(p, stat, L){
  const d = p.d[stat], r = probs(d, L), np = Math.max(r.over + r.under, 1e-9);
  const ov = r.over / np, un = r.under / np;
  const so = shadeP(ov), su = shadeP(un);
  return `<div class="sl-out"><div class="sl-side over"><span>Over ${L}</span><b class="${so}">${pct(r.over)}</b><em>${american(ov)}</em></div>
    <div class="sl-meter" aria-hidden="true"><i class="f-${so || 'n'}" style="width:${r.over * 100}%"></i><i class="f-${su || 'n'}" style="width:${r.under * 100}%"></i></div>
    <div class="sl-side under"><span>Under ${L}</span><b class="${su}">${pct(r.under)}</b><em>${american(un)}</em></div></div>
    ${r.push >= .005 ? `<p class="muted small" style="margin:0">Exactly ${L}: ${pct(r.push)} (a push; odds exclude it)</p>` : ''}`;
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
    : state.view === 'fair' ? renderFair() : state.view === 'agree' ? renderAgree() : state.view === 'dvp' ? renderDvp() : renderGames();
  save();
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
