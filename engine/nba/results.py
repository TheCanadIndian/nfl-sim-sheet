#!/usr/bin/env python3
"""
Grade every saved NBA slate (nba/projections/<date>.csv, frozen at tip-off) against the box
scores, for both models. Players who didn't play are voided (props would be).

    python nba/results.py        # writes nba/projections/results.html
"""

import datetime as dt
import glob
import json
import os
import sqlite3
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(1, os.path.dirname(HERE))
import report  # noqa: E402

OUT = os.path.join(HERE, "projections")
DB = os.path.join(HERE, "nba.db")
STATS = ["min", "pts", "reb", "ast", "fg3m", "pra"]
LAB = {"min": "Minutes", "pts": "Points", "reb": "Rebounds", "ast": "Assists", "fg3m": "Threes", "pra": "Pts+Reb+Ast"}


def grade():
    con = sqlite3.connect(DB)
    box = pd.read_sql("SELECT game_id, player_id, name, team, min, pts, reb, ast, fg3m, dnp FROM players", con)
    box = box[(box.dnp == 0) & (box["min"] > 0)].copy()
    box["pra"] = box.pts + box.reb + box.ast
    final = set(pd.read_sql("SELECT game_id FROM games WHERE state='FINAL'", con).game_id)
    long = box.melt(id_vars=["game_id", "player_id"], value_vars=STATS, var_name="stat", value_name="actual")
    rows = []
    for f in sorted(glob.glob(os.path.join(OUT, "????-??-??.csv"))):
        d = pd.read_csv(f)
        d = d[d.game_id.isin(final) & d.stat.isin(STATS)]
        if d.empty:
            continue
        d["date"] = os.path.basename(f)[:10]
        rows.append(d)
    if not rows:
        return pd.DataFrame(), 0
    d = pd.concat(rows)
    g = d.merge(long, on=["game_id", "player_id", "stat"], how="left")
    void = int(g[g.actual.isna()][["game_id", "player_id"]].drop_duplicates().shape[0])
    return g.dropna(subset=["actual"]), void


def summary(g):
    out = []
    for (model, stat), x in g.groupby(["model", "stat"]):
        out.append(dict(model=model, stat=stat, n=int(len(x)), proj=round(float(x["mean"].mean()), 2), actual=round(float(x.actual.mean()), 2),
                        bias=round(float(x.actual.sum() / max(x["mean"].sum(), 1e-9) - 1), 3),
                        in80=round(float(((x.actual >= x.p10) & (x.actual <= x.p90)).mean()), 3),
                        over=round(float((x.actual > x["median"]).mean()), 3),
                        mae=round(float((x.actual - x["median"]).abs().mean()), 2)))
    return out


def build():
    g, void = grade()
    data = dict(generated=dt.datetime.now().strftime("%b %d, %Y %I:%M %p").replace(" 0", " "), void=void,
                season=summary(g) if len(g) else [], nights=[])
    if len(g):
        con = sqlite3.connect(DB)
        names = pd.read_sql("SELECT DISTINCT player_id, name FROM players", con).drop_duplicates("player_id").set_index("player_id").name
        for date, x in sorted(g.groupby("date"), reverse=True):
            v = x[(x.model == "vegas") & (x.stat == "pts")].copy()
            v["miss"] = v.actual - v["median"]
            big = v.reindex(v.miss.abs().sort_values(ascending=False).index).head(8)
            data["nights"].append(dict(date=date, s=summary(x), games=int(x.game_id.nunique()),
                                       misses=[dict(n=str(names.get(r.player_id, r.name)), t=r.team, med=float(r["median"]), act=float(r.actual),
                                                    p10=float(r.p10), p90=float(r.p90)) for _, r in big.iterrows()]))
    html = TEMPLATE.replace("__FONTS__", report.FONTS).replace("__CSS__", report.BASE_CSS) \
        .replace("__DATA__", json.dumps(data, separators=(",", ":")).replace("</", "<\\/"))
    path = os.path.join(OUT, "results.html")
    os.makedirs(OUT, exist_ok=True)
    open(path, "w", encoding="utf-8").write(html)
    return path


TEMPLATE = r"""<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>NBA Results</title>
__FONTS__<style>
__CSS__
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:10px}
.tile{background:var(--sunk);border-radius:8px;padding:12px 14px;display:grid;gap:2px}
.tile .v{font-family:var(--display);font-weight:700;font-size:30px;line-height:1}
.tile .s{font-size:12.5px;color:var(--muted)}
</style>
<div class="wrap">
  <header><div><div class="eyebrow" id="sub"></div><h1>NBA Results</h1></div></header>
  <main id="main"></main>
  <footer>Every slate's projections are frozen at tip-off and graded against the box score, for both models. Players who didn't play are voided. A calibrated model puts about 80% of results inside its 8-in-10 range and about half above the median (a bit under for counts like threes, where landing exactly on the median is common).</footer>
</div>
<script type="application/json" id="data">__DATA__</script>
<script>
const D = JSON.parse(document.getElementById('data').textContent);
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const pct = p => p == null ? '–' : Math.round(p*100) + '%';
const sp = p => (p >= 0 ? '+' : '−') + Math.abs(Math.round(p*1000)/10).toFixed(1) + '%';
const LAB = {min:'Minutes', pts:'Points', reb:'Rebounds', ast:'Assists', fg3m:'Threes', pra:'Pts+Reb+Ast'};
document.getElementById('sub').textContent = `Built ${D.generated}`;
function table(rows){
  const st = ['min','pts','reb','ast','fg3m','pra'];
  return `<div class="tw"><table class="t"><thead><tr><th class="l">Stat</th><th>Lines</th><th class="l">Model</th><th>Proj avg</th><th>Actual avg</th><th>Bias</th><th>Inside 80% range</th><th>Beat median</th><th>Median error</th></tr></thead><tbody>
  ${st.flatMap(s => ['vegas','blind'].map(m => rows.find(r => r.stat === s && r.model === m)).filter(Boolean).map(r => `<tr><td class="l">${LAB[r.stat]}</td><td>${r.n}</td><td class="l">${r.model === 'vegas' ? 'With Vegas' : 'Market-blind'}</td><td>${r.proj}</td><td>${r.actual}</td><td>${sp(r.bias)}</td><td class="big">${pct(r.in80)}</td><td>${pct(r.over)}</td><td>${r.mae}</td></tr>`)).join('')}</tbody></table></div>`;
}
const main = document.getElementById('main');
if (!D.season.length) main.innerHTML = '<section class="panel"><p class="note" style="margin:0">No graded games yet. Results appear the morning after each slate (the regular season starts October 20).</p></section>';
else main.innerHTML = `<section class="panel"><div class="phead"><h2>Season so far</h2>${D.void ? `<span class="small muted">${D.void} voided (didn't play)</span>` : ''}</div>${table(D.season)}</section>`
  + D.nights.map(n => `<section class="panel"><div class="phead"><h2>${esc(n.date)} <a class="lbtn" href="${esc(n.date)}.html" style="font-size:13px">Pregame sheet</a></h2><span class="small muted">${n.games} games</span></div>${table(n.s)}
    <p class="small" style="margin:0"><b>Biggest points misses (With Vegas):</b> ${n.misses.map(m => `${esc(m.n)} ${esc(m.t)} ${m.act} vs ${m.med} <span class="muted">(${m.p10}–${m.p90})</span>`).join(' · ')}</p></section>`).join('');
</script>
"""

if __name__ == "__main__":
    print("Wrote", build())
