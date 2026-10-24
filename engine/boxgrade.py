#!/usr/bin/env python3
"""
Projected box score accuracy, graded every night (owner 2026-10-24: "for both we are going to grade for accuracy every
night"). Rebuilds each game's box exactly as the page drew it at tip-off / kickoff (the frozen pregame data: NBA
nba/projections/<date>.json players' bx / pp / sh; NFL projections/weeks/<wk>_players.csv means + _box.json TDs, With
Vegas model) and grades it against the real box score. Ports of nba/page.js `boxTable` and nfl_page.js `boxPanel`:
keep them in step.

    python boxgrade.py nba     # -> nba/projections/boxes.html  (site: nba/boxes.html)
    python boxgrade.py nfl     # -> projections/boxes.html      (site: nfl/boxes.html)
"""
import datetime as dt
import glob
import json
import math
import os
import sqlite3
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import report  # noqa: E402

NIGHTS_SHOWN = 14                       # nights / weeks with per-game detail


def jround(x):                          # JS Math.round
    return int(math.floor(x + 0.5))


def lr(vals):
    """Largest-remainder rounding (page.js lrRound): integers summing to the rounded total."""
    v = [float(x) for x in vals]
    fl = [math.floor(x) for x in v]
    rem = jround(sum(v)) - sum(fl)
    for i in sorted(range(len(v)), key=lambda i: -(v[i] - fl[i])):
        if rem <= 0:
            break
        fl[i] += 1
        rem -= 1
    return fl


# ------------------------------------------------------------------ NBA

NBA_KS = ["min", "pts", "reb", "ast", "fg3m", "stl", "blk", "tov"]
NBA_WITHIN = {"pts": 5, "reb": 2, "ast": 2}


def nba_box(players, md="vegas"):
    """One team's box (nba/page.js boxTable): the 11 with the most expected minutes, scaled to the team's expected totals."""
    al = [p for p in players if p.get("bx") and (p["bx"] or {}).get(md) and p.get("st") != "Out"]
    pp = lambda p: 1.0 if p.get("pp") is None else float(p["pp"])
    ps = sorted(al, key=lambda p: -pp(p) * p["bx"][md]["min"])[:11]
    ps = sorted(ps, key=lambda p: -p["bx"][md]["min"])
    if not ps:
        return []
    KS = ["min", "pts", "reb", "ast", "stl", "blk", "tov", "fga", "fta", "fg3m"]
    sc = {}
    for k in KS:
        want = sum(pp(p) * p["bx"][md][k] for p in al)
        have = sum(p["bx"][md][k] for p in ps)
        sc[k] = want / have if have > 0 else 1.0
    X = []
    for p in ps:
        b = {k: p["bx"][md][k] * sc[k] for k in KS}
        ft = p["sh"][1] if p.get("sh") else .78
        ftm = b["fta"] * ft
        fg2m = max(0.0, (b["pts"] - 3 * b["fg3m"] - ftm) / 2)
        X.append(dict(min=b["min"], reb=b["reb"], ast=b["ast"], stl=b["stl"], blk=b["blk"], tov=b["tov"], fg2m=fg2m, fg3m=b["fg3m"], ftm=ftm))
    R = {k: lr([x[k] for x in X]) for k in ("min", "reb", "ast", "stl", "blk", "tov", "fg2m", "fg3m", "ftm")}
    return [dict(id=int(p["id"]), n=p["n"], t=p["t"], start=i < 5, min=R["min"][i], pts=2 * R["fg2m"][i] + 3 * R["fg3m"][i] + R["ftm"][i],
                 reb=R["reb"][i], ast=R["ast"][i], fg3m=R["fg3m"][i], stl=R["stl"][i], blk=R["blk"][i], tov=R["tov"][i]) for i, p in enumerate(ps)]


def grade_nba():
    db = os.path.join(HERE, "nba", "nba.db")
    con = sqlite3.connect(db)
    final = set(pd.read_sql("SELECT game_id FROM games WHERE state='FINAL'", con).game_id.astype(int))
    act = pd.read_sql("SELECT game_id, player_id, team, dnp, min, pts, reb, ast, fg3m, stl, blk, tov FROM players", con)
    act = act.fillna({k: 0 for k in NBA_KS})
    A = {(int(g), int(p)): r for g, p, r in zip(act.game_id, act.player_id, act.to_dict("records"))}
    tpts = act.groupby(["game_id", "team"]).pts.sum().to_dict()
    nights = []
    for f in sorted(glob.glob(os.path.join(HERE, "nba", "projections", "????-??-??.json"))):
        D = json.load(open(f, encoding="utf-8"))
        games = []
        for g in D.get("games", []):
            gid = int(g["id"])
            if gid not in final:
                continue
            rows, teams = [], []
            for t in (g["away"], g["home"]):
                bx = nba_box([p for p in D["players"] if int(p["g"]) == gid and p["t"] == t])
                if not bx:
                    continue
                for r in bx:
                    a = A.get((gid, r["id"]))
                    played = a is not None and a["dnp"] == 0 and (a["min"] or 0) > 0
                    r["a"] = {k: (float(a[k]) if played else 0.0) for k in NBA_KS}
                    r["dnp"] = not played
                    rows.append(r)
                teams.append(dict(t=t, box=sum(r["pts"] for r in bx), act=float(tpts.get((gid, t), 0))))
            if rows and len(teams) == 2:
                games.append(dict(id=gid, label=f"{g['away']} @ {g['home']}", rows=rows, teams=teams))
        if games:
            nights.append(dict(key=os.path.basename(f)[:10], label=os.path.basename(f)[:10], games=games))
    return nights


# ------------------------------------------------------------------ NFL

NFL_GROUPS = {"pass": ["cmp", "att", "pass_yds", "pass_td", "ints"], "rush": ["car", "rush_yds", "rush_td"],
              "rec": ["rec", "tgt", "rec_yds", "rec_td"]}
NFL_WITHIN = {"pass_yds": 30, "rush_yds": 15, "rec_yds": 15}


def nfl_box(P, tdm):
    """One team's box (nfl_page.js boxPanel). P: {player_id: {name, pos, s: {stat: mean}}}; tdm: {player_id: [rush, rec]}."""
    m = lambda i, k: P[i]["s"].get(k, 0.0)
    td = lambda i, j: (tdm.get(i) or [0, 0])[j]
    qbs = sorted([i for i in P if "att" in P[i]["s"]], key=lambda i: -m(i, "att"))
    rec = sorted([i for i in P if m(i, "tgt") >= 1], key=lambda i: -m(i, "rec_yds"))
    ru = sorted([i for i in P if m(i, "car") >= 1], key=lambda i: -m(i, "rush_yds"))
    rc, tg, ry, rt = (lr([f(i) for i in rec]) for f in (lambda i: m(i, "rec"), lambda i: m(i, "tgt"), lambda i: m(i, "rec_yds"), lambda i: td(i, 1)))
    ca, uy, ut = (lr([f(i) for i in ru]) for f in (lambda i: m(i, "car"), lambda i: m(i, "rush_yds"), lambda i: td(i, 0)))
    out = {}
    row = lambda i: out.setdefault(i, dict(id=i, n=P[i]["name"], pos=P[i]["pos"], b={}))
    if qbs:
        q = qbs[0]
        c = sum(rc)
        row(q)["b"].update(cmp=c, att=max(jround(m(q, "att")), c), pass_yds=sum(ry), pass_td=sum(rt), ints=jround(m(q, "ints")))
    for k, i in enumerate(ru):
        row(i)["b"].update(car=ca[k], rush_yds=uy[k], rush_td=ut[k])
    for k, i in enumerate(rec):
        row(i)["b"].update(rec=rc[k], tgt=max(tg[k], rc[k]), rec_yds=ry[k], rec_td=rt[k])
    return list(out.values())


def grade_nfl():
    from boxscore import data as BD
    con = sqlite3.connect(os.path.join(HERE, "nfl.db"))
    games = BD.load_games(con)
    final = set(games[games.result.notna()].game_id) if "result" in games else set(games.game_id)
    pg = BD.player_games(BD.load_plays(con), BD.load_kneels(con)).reset_index()
    A = {(g, p): r for g, p, r in zip(pg.game_id, pg.player_id, pg.to_dict("records"))}
    weeks = []
    for f in sorted(glob.glob(os.path.join(HERE, "projections", "weeks", "20??_wk??_players.csv"))):
        stem = f[:-len("_players.csv")]
        if not os.path.exists(stem + "_box.json"):
            continue                                            # weeks before the box score existed
        btd = json.load(open(stem + "_box.json"))
        d = pd.read_csv(f)
        gl = []
        for gid, x in d.groupby("game_id", sort=False):
            if gid not in final or not any(k.startswith(gid + "|") for k in btd):
                continue
            rows, teams = [], []
            for t, y in x.groupby("team", sort=False):
                P = {}
                for r in y.itertuples():
                    P.setdefault(r.player_id, dict(name=r.player, pos=r.pos, s={}))["s"][r.stat] = float(r.mean)
                tdm = {pid: btd.get(f"{gid}|{t}|{P[pid]['name']}") for pid in P}
                bx = nfl_box(P, tdm)
                for r in bx:
                    a = A.get((gid, r["id"]))
                    r["a"] = {k: (float(a.get(k) or 0) if a is not None else 0.0) for k in r["b"]}
                    r["dnp"] = a is None
                    r["t"] = t
                    rows.append(r)
                qb = [r for r in bx if "pass_yds" in r["b"]]
                teams.append(dict(t=t, box=qb[0]["b"]["pass_yds"] if qb else 0,
                                  act=float(pg[(pg.game_id == gid) & (pg.team == t)].pass_yds.sum()) if "team" in pg else 0.0))
            if rows:
                h = gid.split("_")
                gl.append(dict(id=gid, label=f"{h[2]} @ {h[3]}" if len(h) >= 4 else gid, rows=rows, teams=teams))
        if gl:
            b = os.path.basename(stem)
            weeks.append(dict(key=b, label=f"{b[:4]} week {int(b[-2:])}", games=gl))
    return weeks


# ------------------------------------------------------------------ summary + page

def summarize(nights, sport):
    if sport == "nba":
        stats, within = NBA_KS, NBA_WITHIN
    else:
        stats, within = [k for g in NFL_GROUPS.values() for k in g], NFL_WITHIN

    def summ(games):
        rows = [r for g in games for r in g["rows"]]
        out = dict(games=len(games), players=len(rows), dnp=sum(r["dnp"] for r in rows), mae={}, n={}, within={}, bias={})
        for k in stats:
            pr = [(r["b"][k] if sport == "nfl" else r[k], r["a"][k]) for r in rows if (k in r["b"] if sport == "nfl" else True)]
            if not pr:
                continue
            e = np.array([a - p for p, a in pr], float)
            out["mae"][k], out["n"][k], out["bias"][k] = round(float(np.abs(e).mean()), 2), len(pr), round(float(e.mean()), 2)
            if k in within:
                out["within"][k] = round(float((np.abs(e) <= within[k]).mean()), 3)
        te = [abs(t["box"] - t["act"]) for g in games for t in g["teams"]]
        out["team"] = round(float(np.mean(te)), 1) if te else None
        return out
    allg = [g for n in nights for g in n["games"]]
    return dict(season=summ(allg) if allg else None, nights=[dict(key=n["key"], label=n["label"], s=summ(n["games"]),
                games=n["games"] if i < NIGHTS_SHOWN else None) for i, n in enumerate(reversed(nights))])


def build(sport):
    nights = grade_nba() if sport == "nba" else grade_nfl()
    data = summarize(nights, sport)
    data.update(sport=sport, generated=dt.datetime.now().strftime("%b %d, %Y %I:%M %p").replace(" 0", " "),
                within=NBA_WITHIN if sport == "nba" else NFL_WITHIN)
    html = TEMPLATE.replace("__FONTS__", report.FONTS).replace("__CSS__", report.BASE_CSS) \
        .replace("__TITLE__", ("NBA" if sport == "nba" else "NFL") + " Box Score Accuracy") \
        .replace("__DATA__", json.dumps(data, separators=(",", ":")).replace("</", "<\\/"))
    out = os.path.join(HERE, "nba", "projections", "boxes.html") if sport == "nba" else os.path.join(HERE, "projections", "boxes.html")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    open(out, "w", encoding="utf-8").write(html)
    s = data["season"]
    print(f"{sport} box scores graded: {len(nights)} {'nights' if sport == 'nba' else 'weeks'}"
          + (f", {s['players']} player rows, MAE {s['mae']}" if s else ", none final yet"))
    return out


TEMPLATE = r"""<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>__TITLE__</title>
__FONTS__<style>
__CSS__
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}
.tile{background:var(--sunk);border-radius:8px;padding:12px 14px;display:grid;gap:2px}
.tile .v{font-family:var(--display);font-weight:700;font-size:30px;line-height:1}
.tile .s{font-size:12.5px;color:var(--muted)}
.exp3{font-family:var(--body);font-size:10.5px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;color:#F2C14E;border:1px solid currentColor;border-radius:4px;padding:1px 5px;margin-left:6px;vertical-align:3px}
table.ga td,table.ga th{text-align:right;white-space:nowrap}table.ga .l{text-align:left;white-space:normal}
.pa{font-weight:700}.pa i{font-style:normal;color:var(--muted);font-weight:400}
.hit{color:var(--good)}.miss{color:var(--bad)}
details.gm{border-top:1px solid var(--faint);padding:8px 0}details.gm summary{cursor:pointer;font-weight:600}
@media(max-width:560px){table.ga td,table.ga th{padding-left:4px;padding-right:4px}}
</style>
<div class="wrap">
  <header><div><div class="eyebrow" id="sub"></div><h1>__TITLE__ <span class="exp3">highly experimental</span></h1></div></header>
  <main id="main"></main>
  <footer>Each game’s projected box score is frozen at tip-off / kickoff exactly as the page showed it (With Vegas) and graded against the real box score after the game. Error = how far the box’s number was from what happened, on average per player. A player the box listed who didn’t play counts as 0.</footer>
</div>
<script type="application/json" id="data">__DATA__</script>
<script>
const D = JSON.parse(document.getElementById('data').textContent);
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const pct = p => p == null ? '–' : Math.round(p * 100) + '%';
const NBA = D.sport === 'nba';
const LAB = NBA ? {min:'MIN', pts:'PTS', reb:'REB', ast:'AST', fg3m:'3PM', stl:'STL', blk:'BLK', tov:'TOV'}
  : {cmp:'CMP', att:'ATT', pass_yds:'PASS YDS', pass_td:'PASS TD', ints:'INT', car:'CAR', rush_yds:'RUSH YDS', rush_td:'RUSH TD', rec:'REC', tgt:'TGT', rec_yds:'REC YDS', rec_td:'REC TD'};
const KS = Object.keys(LAB);
const unit = NBA ? 'night' : 'week';
document.getElementById('sub').textContent = `Built ${D.generated} · graded every ${unit}`;
const errRow = (lab, s) => `<tr><td class="l">${lab}</td><td>${s.games}</td><td>${s.players}</td>${KS.map(k => `<td>${s.mae[k] == null ? '–' : s.mae[k]}</td>`).join('')}<td>${s.team == null ? '–' : s.team}</td></tr>`;
const errHead = `<tr><th class="l">${NBA ? 'Night' : 'Week'}</th><th>Games</th><th>Players</th>${KS.map(k => `<th>${LAB[k]}</th>`).join('')}<th>${NBA ? 'Team pts' : 'Team pass yds'}</th></tr>`;
function tiles(s){
  const W = D.within, T = [];
  if (NBA){
    T.push([s.mae.pts, 'points off per player']); T.push([pct(s.within.pts), `points within ${W.pts}`]);
    T.push([pct(s.within.reb), `rebounds within ${W.reb}`]); T.push([pct(s.within.ast), `assists within ${W.ast}`]); T.push([s.team, 'team score off by']);
  } else {
    T.push([s.mae.pass_yds, 'passing yards off per QB']); T.push([s.mae.rec_yds, 'receiving yards off per player']);
    T.push([pct(s.within.rec_yds), `receiving yards within ${W.rec_yds}`]); T.push([s.mae.rush_yds, 'rushing yards off per player']); T.push([s.mae.rec, 'catches off per player']);
  }
  return `<div class="tiles">${T.map(([v, l]) => `<div class="tile"><span class="v">${v == null ? '–' : v}</span><span class="s">${l}</span></div>`).join('')}</div>`;
}
const cell = (p, a, w) => { const d = Math.abs(a - p), cls = w != null ? (d <= w ? 'hit' : '') : ''; return `<td class="pa"><span class="${cls}">${p}</span> <i>${Math.round(a * 10) / 10}</i></td>`; };
function gameTable(g){
  if (NBA){
    const C = ['min', 'pts', 'reb', 'ast', 'fg3m', 'stl', 'blk', 'tov'];
    const sides = g.teams.map(T => { const rs = g.rows.filter(r => r.t === T.t);
      return `<tr class="bxsep"><td class="l" colspan="${C.length + 1}"><b>${esc(T.t)}</b> <span class="muted small">box ${T.box} · actual ${T.act}</span></td></tr>` + rs.map(r => `<tr><td class="l">${esc(r.n)}${r.dnp ? ' <span class="small miss">DNP</span>' : ''}</td>${C.map(k => cell(r[k], r.a[k], D.within[k])).join('')}</tr>`).join(''); }).join('');
    return `<div class="tw"><table class="t ga"><thead><tr><th class="l">Box <i class="muted">actual</i></th>${C.map(k => `<th>${LAB[k]}</th>`).join('')}</tr></thead><tbody>${sides}</tbody></table></div>`;
  }
  const C = [['pass_yds', 'PASS'], ['car', 'CAR'], ['rush_yds', 'RUSH'], ['rec', 'REC'], ['rec_yds', 'REC YDS'], ['td', 'TD']];
  const v = (r, k, which) => k === 'td' ? ['pass_td', 'rush_td', 'rec_td'].filter(x => x in r.b && (x !== 'pass_td')).reduce((s, x) => s + (which === 'b' ? r.b[x] : r.a[x]), 0) : (which === 'b' ? r.b[k] : r.a[k]);
  const sides = g.teams.map(T => { const rs = g.rows.filter(r => r.t === T.t);
    return `<tr class="bxsep"><td class="l" colspan="${C.length + 1}"><b>${esc(T.t)}</b> <span class="muted small">pass yds box ${T.box} · actual ${T.act}</span></td></tr>` + rs.map(r => `<tr><td class="l">${esc(r.n)} <span class="small muted">${esc(r.pos)}</span>${r.dnp ? ' <span class="small miss">DNP</span>' : ''}</td>${C.map(([k]) => (k === 'td' || k in r.b) ? cell(v(r, k, 'b'), v(r, k, 'a'), D.within[k]) : '<td class="muted">·</td>').join('')}</tr>`).join(''); }).join('');
  return `<div class="tw"><table class="t ga"><thead><tr><th class="l">Box <i class="muted">actual</i></th>${C.map(([, l]) => `<th>${l}</th>`).join('')}</tr></thead><tbody>${sides}</tbody></table></div>`;
}
const main = document.getElementById('main');
if (!D.season) main.innerHTML = `<section class="panel"><p class="note" style="margin:0">No graded box scores yet. The first ones appear after the first ${unit} with projected box scores is final.</p></section>`;
else main.innerHTML = `<section class="panel"><div class="phead"><h2>So far</h2><span class="small muted">${D.season.games} games · ${D.season.players} player lines${D.season.dnp ? ` · ${D.season.dnp} listed but didn’t play` : ''}</span></div>${tiles(D.season)}
  <div class="tw"><table class="t"><thead>${errHead}</thead><tbody>${errRow('All', D.season)}${D.nights.map(n => errRow(esc(n.label), n.s)).join('')}</tbody></table></div>
  <p class="note" style="margin:0">Numbers in the table are the average miss per player (box vs actual). In the game tables, each cell is the box’s number with what happened beside it${Object.keys(D.within).length ? `; green = within ${Object.entries(D.within).map(([k, w]) => `${w} ${LAB[k].toLowerCase()}`).join(', ')}` : ''}.</p></section>`
  + D.nights.filter(n => n.games).map((n, i) => `<section class="panel"><div class="phead"><h2>${esc(n.label)}</h2><span class="small muted">${n.s.games} games${n.s.mae.pts != null ? ` · points off ${n.s.mae.pts} a player` : n.s.mae.rec_yds != null ? ` · receiving yards off ${n.s.mae.rec_yds}` : ''}</span></div>
    ${n.games.map(g => `<details class="gm"${i === 0 ? ' open' : ''}><summary>${esc(g.label)} <span class="muted small">${g.teams.map(T => `${esc(T.t)} ${T.box} → ${T.act}`).join(' · ')}</span></summary>${gameTable(g)}</details>`).join('')}</section>`).join('');
</script>
"""

if __name__ == "__main__":
    for sp in (sys.argv[1:] or ["nba", "nfl"]):
        print("Wrote", build(sp))
