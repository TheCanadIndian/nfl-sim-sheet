#!/usr/bin/env python3
"""
Grade the MLB home-run sheets: every frozen pregame projection (mlb/projections/<date>.json) against what
happened (mlb.db, refreshed by fetch.py). Writes mlb/projections/results.html (+ results.json).

    python mlb/results.py

Scored only for batters who came to the plate. Reports: calibration by chance bucket, log-loss vs a
"season HR rate" baseline (same PAs), the slate's top 5 each night, and the running record.
"""

import glob
import json
import os
import sqlite3
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import report  # noqa: E402

OUT = os.path.join(HERE, "projections")


def load():
    con = sqlite3.connect(os.path.join(HERE, "mlb.db"))
    hr = pd.read_sql("SELECT game_pk, batter, SUM(hr) hr, COUNT(*) pa FROM pa GROUP BY game_pk, batter", con)
    done = set(r[0] for r in con.execute("SELECT game_pk FROM games"))
    rows = []
    for f in sorted(glob.glob(os.path.join(OUT, "????-??-??.json"))):
        js = json.load(open(f))
        for p in js.get("players", []):
            if p["game_pk"] in done and p.get("lineup") != "bench":          # bettable = starters
                rows.append(dict(date=js["date"], game_pk=p["game_pk"], batter=p["batter"], name=p["name"], team=p["team"],
                                 p=p["p_hr"], slot=p["slot"], lineup=p.get("lineup")))
    d = pd.DataFrame(rows)
    if d.empty:
        return d
    d = d.merge(hr, on=["game_pk", "batter"], how="inner")          # batters who actually batted
    d["y"] = (d.hr > 0).astype(int)
    d["rank"] = d.groupby("date").p.rank(ascending=False, method="first")
    d["grank"] = d.groupby("game_pk").p.rank(ascending=False, method="first")      # the game's picks
    return d


def main():
    d = load()
    os.makedirs(OUT, exist_ok=True)
    if d.empty:
        summary = dict(n=0)
    else:
        ll = lambda y, p: float(-(y * np.log(np.clip(p, 1e-6, 1)) + (1 - y) * np.log(np.clip(1 - p, 1e-6, 1))).mean())
        d["bin"] = pd.cut(d.p, [0, .06, .09, .12, .15, .2, 1], labels=["<6%", "6-9%", "9-12%", "12-15%", "15-20%", "20%+"])
        cal = d.groupby("bin", observed=True).agg(n=("y", "size"), predicted=("p", "mean"), actual=("y", "mean")).reset_index()
        top = d[d["rank"] <= 5]
        nights = d.groupby("date").agg(n=("y", "size"), hrs=("y", "sum"), expected=("p", "sum")).reset_index()
        tops = top.groupby("date").apply(lambda x: x.sort_values("rank")[["name", "team", "p", "y"]].to_dict("records")).to_dict()
        g3 = d[d.grank <= 3].groupby("game_pk").y.sum()
        pergame = dict(games=int(len(g3)), caught=round(float(g3.mean()), 3), any=round(float((g3 >= 1).mean()), 3),
                       random=round(float(3 * d.y.mean()), 3))
        summary = dict(pergame=pergame, n=int(len(d)), homered=int(d.y.sum()), expected=round(float(d.p.sum()), 1), logloss=round(ll(d.y, d.p), 4),
                       top5=dict(n=int(len(top)), hit=int(top.y.sum()), expected=round(float(top.p.sum()), 2)),
                       calibration=cal.assign(bin=cal.bin.astype(str)).round(3).to_dict("records"),
                       nights=nights.round(2).to_dict("records")[::-1], tops={k: v for k, v in tops.items()})
    json.dump(summary, open(os.path.join(OUT, "results.json"), "w"), default=str)
    html = PAGE.replace("__FONTS__", report.FONTS).replace("__CSS__", report.BASE_CSS) \
        .replace("__DATA__", json.dumps(summary, default=str).replace("</", "<\\/"))
    open(os.path.join(OUT, "results.html"), "w", encoding="utf-8").write(html)
    print(f"MLB results: {summary.get('n', 0)} batter-games graded" + (f", {summary['homered']} homered vs {summary['expected']} expected" if summary.get("n") else ""))


PAGE = r"""<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>MLB home run results</title>__FONTS__<style>__CSS__</style>
<div class="wrap"><header class="hero" style="display:grid;gap:6px"><h1>Home run results</h1>
<p class="sub" style="color:var(--muted);max-width:70ch;margin:0">Every pregame home-run chance, frozen at first pitch, against what happened. Only hitters who batted count.</p></header>
<main id="main"></main></div>
<script type="application/json" id="data">__DATA__</script>
<script>
const S = JSON.parse(document.getElementById('data').textContent), pct = p => (p * 100).toFixed(1) + '%';
const m = document.getElementById('main');
if (!S.n) m.innerHTML = '<section class="panel"><p class="muted">Nothing graded yet. Results appear the morning after each slate.</p></section>';
else m.innerHTML = `<section class="panel" style="display:grid;gap:10px"><h2>Running record</h2>
  <p>${S.n} hitter-games graded: <b>${S.homered}</b> homered vs <b>${S.expected}</b> expected. Top 5 of each slate: <b>${S.top5.hit}</b> of ${S.top5.n} homered (${S.top5.expected} expected).</p>
  ${S.pergame ? `<p>Top 3 in each game (${S.pergame.games} games): <b>${S.pergame.caught}</b> home-run hitters per game (random 3: ${S.pergame.random}); at least one of the three homered in <b>${Math.round(S.pergame.any * 100)}%</b> of games.</p>` : ''}
  <div class="tw"><table class="t"><thead><tr><th class="l">Our chance</th><th>Hitter-games</th><th>Predicted</th><th>Actually homered</th></tr></thead><tbody>
  ${S.calibration.map(r => `<tr><td class="l">${r.bin}</td><td>${r.n}</td><td>${pct(r.predicted)}</td><td class="big">${pct(r.actual)}</td></tr>`).join('')}</tbody></table></div></section>
  <section class="panel" style="display:grid;gap:10px"><h2>By night</h2><div class="tw"><table class="t"><thead><tr><th class="l">Date</th><th>Hitters</th><th>HR hitters</th><th>Expected</th><th class="l">Top 5</th></tr></thead><tbody>
  ${S.nights.map(r => `<tr><td class="l">${r.date}</td><td>${r.n}</td><td class="big">${r.hrs}</td><td>${r.expected}</td><td class="l small">${(S.tops[r.date] || []).map(t => `${t.y ? '✅' : '·'} ${t.name} ${pct(t.p)}`).join(' &nbsp; ')}</td></tr>`).join('')}</tbody></table></div></section>`;
</script>"""

if __name__ == "__main__":
    main()
