#!/usr/bin/env python3
"""
Shadow tests: candidate model changes that FAILED their backtest bar, graded live beside the real projections without
changing anything the site shows or bets (owner 2026-10-25: "both", after research/nfl_qb_volume_bias.py and
research/nhl_goal_recal.py failed). A shadow is promoted only if it beats the live numbers in both halves of its live
record with enough games (then re-tested on history before any switch).

  NFL  QB pass attempts / completions, season-to-date scale (project.season_bias form, K = 1000): week w's frozen
       projection (mean and quantiles) x (actual + K) / (projected + K) over this season's earlier weeks. Scored by
       quantile (pinball) loss over p10..p90, mean abs error and the share above the median.
  NHL  anytime-goal chances (hockey/projections/<date>.csv, frozen at puck drop), three shadows:
         recal     logit(p) = -0.026 + 0.993 logit(p)        (fit on 2025-26)
         high      above 20%: logit shrink x0.978 toward 20%  (fit on 2025-26)
         season    p x (goals + 200) / (expected + 200) over this season's earlier nights
       Scored by log-loss, goals expected vs scored.

    python shadow.py          # -> projections/shadow.html (site: shadow.html), markets/shadow.json
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
NFL_K, NHL_K = 1000.0, 200.0
QS = {"p10": .1, "p25": .25, "median": .5, "p75": .75, "p90": .9}


def halves(df, key):
    ks = sorted(df[key].unique())
    if len(ks) < 2:
        return None
    cut = ks[len(ks) // 2 - 1]
    return df[key] <= cut


def nfl(season):
    con = sqlite3.connect(os.path.join(HERE, "nfl.db"))
    pw = pd.read_sql("SELECT player_id, week, attempts, completions FROM player_week WHERE season = ?", con, params=(season,))
    F = []
    for f in sorted(glob.glob(os.path.join(HERE, "projections", "weeks", f"{season}_wk??_players.csv"))):
        P = pd.read_csv(f)
        F.append(P[P.stat.isin(["att", "cmp"]) & (P.pos == "QB")].assign(week=int(os.path.basename(f)[7:9])))
    if not F:
        return None
    P = pd.concat(F).merge(pw, on=["player_id", "week"], how="inner")
    P["y"] = np.where(P.stat == "att", P.attempts, P.completions).astype(float)
    P = P[P.y >= 5]
    pin = lambda q, y, t: np.where(y >= q, t * (y - q), (1 - t) * (q - y))
    rows = []
    for k, z in P.groupby("stat"):
        for w, x in z.groupby("week"):
            prev = z[z.week < w]
            r = (prev.y.sum() + NFL_K) / (prev["mean"].sum() + NFL_K) if len(prev) else 1.0
            for lab, sc in (("live", 1.0), ("shadow", r)):
                rows.append(pd.DataFrame(dict(stat=k, week=w, model=lab, ratio=sc, ae=(x["mean"] * sc - x.y).abs(),
                                              pin=sum(pin(x[q] * sc, x.y, t) for q, t in QS.items()) / len(QS),
                                              over=(x.y > x["median"] * sc).astype(float))))
    T = pd.concat(rows)
    T = T[T.week >= 2]
    out = []
    for k, z in T.groupby("stat"):
        h = halves(z, "week")
        L, Sh = z[z.model == "live"], z[z.model == "shadow"]
        hl = [] if h is None else [float(Sh[h[z.model == "shadow"].values].pin.mean() - L[h[z.model == "live"].values].pin.mean()),
                                   float(Sh[~h[z.model == "shadow"].values].pin.mean() - L[~h[z.model == "live"].values].pin.mean())]
        out.append(dict(stat=k, n=int(len(L)), weeks=[int(z.week.min()), int(z.week.max())], live_pin=round(float(L.pin.mean()), 4),
                        shadow_pin=round(float(Sh.pin.mean()), 4), live_mae=round(float(L.ae.mean()), 2), shadow_mae=round(float(Sh.ae.mean()), 2),
                        live_over=round(float(L.over.mean()), 3), shadow_over=round(float(Sh.over.mean()), 3),
                        ratio=round(float(Sh[Sh.week == Sh.week.max()].ratio.mean()), 4), halves=[round(x, 4) for x in hl]))
    return out


def nhl():
    sys.path.insert(0, os.path.join(HERE, "hockey"))
    import results as HR
    G = HR.grade()[0]
    if G.empty:
        return None
    G = G.sort_values("date").reset_index(drop=True)
    lg = lambda p: np.log(np.clip(p, 1e-4, 1 - 1e-4) / (1 - np.clip(p, 1e-4, 1 - 1e-4)))
    sg = lambda z: 1 / (1 + np.exp(-z))
    p = G.p.to_numpy()
    V = {"live": p,
         "recal": sg(-0.026 + 0.993 * lg(p)),
         "high": np.where(p > .2, sg(lg(.2) + 0.978 * (lg(p) - lg(.2))), p)}
    sc = np.ones(len(G))
    for d in G.date.unique():                                    # season-to-date scale from earlier nights only
        prev = G[G.date < d]
        sc[(G.date == d).to_numpy()] = (prev.scored.sum() + NHL_K) / (prev.p.sum() + NHL_K) if len(prev) else 1.0
    V["season"] = np.clip(p * sc, 1e-4, .95)
    y = G.scored.to_numpy(float)
    ll = lambda q, m: float(-np.mean(y[m] * np.log(np.clip(q[m], 1e-6, 1)) + (1 - y[m]) * np.log(np.clip(1 - q[m], 1e-6, 1))))
    h = halves(G, "date")
    h = h.to_numpy() if h is not None else np.ones(len(G), bool)
    allm = np.ones(len(G), bool)
    out = []
    for lab, q in V.items():
        out.append(dict(model=lab, n=int(len(G)), nights=int(G.date.nunique()), exp=round(float(q.sum()), 1), act=int(y.sum()),
                        ll=round(ll(q, allm), 5), d=round(ll(q, allm) - ll(p, allm), 5),
                        halves=[round(ll(q, h) - ll(p, h), 5), round(ll(q, ~h) - ll(p, ~h), 5)],
                        scale=round(float(sc[-1]), 4) if lab == "season" else None))
    return out


def build():
    season = dt.date.today().year if dt.date.today().month >= 8 else dt.date.today().year - 1
    data = dict(generated=dt.datetime.now().strftime("%b %d, %Y %I:%M %p").replace(" 0", " "))
    for nm, fn in (("nfl", lambda: nfl(season)), ("nhl", nhl)):
        try:
            data[nm] = fn()
        except Exception as e:                                         # noqa: BLE001 -- a shadow never breaks a run
            print(f"shadow {nm}: skipped ({type(e).__name__}: {e})")
            data[nm] = None
    os.makedirs(os.path.join(HERE, "markets"), exist_ok=True)
    json.dump(data, open(os.path.join(HERE, "markets", "shadow.json"), "w"), indent=1)
    import report
    src = os.path.join(HERE, "projections", "shadow.html")
    open(src, "w", encoding="utf-8").write(PAGE.replace("__FONTS__", report.FONTS).replace("__CSS__", report.BASE_CSS)
                                           .replace("__DATA__", json.dumps(data, separators=(",", ":")).replace("</", "<\\/")))
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("sim_site", os.path.join(HERE, "site.py"))
        S = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(S)
        S.inject(src, os.path.join(S.SITE, "shadow.html"), 0, "shadow")
    except Exception as e:                                             # noqa: BLE001
        print(f"shadow page: not published ({type(e).__name__}: {e})")
    print("shadow:", json.dumps({k: v for k, v in data.items() if k != "generated"})[:600])


PAGE = r"""<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Shadow Tests</title>
__FONTS__<style>
__CSS__
.vd{font-weight:700}.vd.good{color:var(--good)}.vd.bad{color:var(--bad)}
</style>
<div class="wrap">
  <header><div><div class="eyebrow" id="sub"></div><h1>Shadow tests</h1></div></header>
  <main id="main"></main>
  <footer>Model changes that didn’t pass their backtest, graded live beside the real projections. Nothing here changes what the site shows or bets. A shadow is switched on only if it beats the live numbers in both halves of its live record, and then passes a re-test on past seasons.</footer>
</div>
<script type="application/json" id="data">__DATA__</script>
<script>
const D = JSON.parse(document.getElementById('data').textContent);
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const sg = v => (v >= 0 ? '+' : '−') + Math.abs(v).toFixed(4);
const verdict = h => !h || h.length < 2 ? '<span class="vd">too early</span>' : h[0] < 0 && h[1] < 0 ? '<span class="vd good">shadow ahead in both halves</span>' : h[0] > 0 && h[1] > 0 ? '<span class="vd bad">live ahead in both halves</span>' : '<span class="vd">split</span>';
document.getElementById('sub').textContent = `Built ${D.generated}`;
let h = '';
if (D.nfl && D.nfl.length) h += `<section class="panel"><div class="phead"><h2>NFL · QB attempts and completions</h2><span class="small muted">season-to-date scale (actual ÷ projected so far, shrunk)</span></div>
  <div class="tw"><table class="t"><thead><tr><th class="l">Stat</th><th>QB games</th><th>Weeks</th><th>Scale now</th><th>Range score live → shadow</th><th>Avg miss live → shadow</th><th>Above median live → shadow</th><th>Halves (shadow − live)</th><th class="l">Verdict</th></tr></thead><tbody>
  ${D.nfl.map(r => `<tr><td class="l">${r.stat === 'att' ? 'Attempts' : 'Completions'}</td><td>${r.n}</td><td>${r.weeks[0]}-${r.weeks[1]}</td><td>×${r.ratio.toFixed(3)}</td><td>${r.live_pin} → ${r.shadow_pin}</td><td>${r.live_mae} → ${r.shadow_mae}</td><td>${Math.round(r.live_over * 100)}% → ${Math.round(r.shadow_over * 100)}%</td><td>${(r.halves || []).map(sg).join(' / ')}</td><td class="l">${verdict(r.halves)}</td></tr>`).join('')}</tbody></table></div>
  <p class="note" style="margin:0">Range score = quantile loss over the 10th-90th percentiles (lower is better). Backtest: better in 2026 weeks 2-5, worse in 2025, so not adopted.</p></section>`;
if (D.nhl && D.nhl.length){ const L = {live: 'Live model', recal: 'Recalibrated (fit 2025-26)', high: 'High-end shrink (above 20%)', season: 'Season-to-date scale'};
  h += `<section class="panel"><div class="phead"><h2>NHL · anytime-goal chances</h2><span class="small muted">${D.nhl[0].nights} nights · ${D.nhl[0].n} skater-games</span></div>
  <div class="tw"><table class="t"><thead><tr><th class="l">Version</th><th>Goals expected</th><th>Scored</th><th>Log-loss</th><th>vs live</th><th>Halves (vs live)</th><th class="l">Verdict</th></tr></thead><tbody>
  ${D.nhl.map(r => `<tr><td class="l">${L[r.model]}${r.scale ? ` <span class="small muted">×${r.scale.toFixed(3)} now</span>` : ''}</td><td>${r.exp}</td><td>${r.act}</td><td>${r.ll.toFixed(5)}</td><td>${r.model === 'live' ? '–' : sg(r.d)}</td><td>${r.model === 'live' ? '–' : r.halves.map(sg).join(' / ')}</td><td class="l">${r.model === 'live' ? '' : verdict(r.halves)}</td></tr>`).join('')}</tbody></table></div>
  <p class="note" style="margin:0">Lower log-loss is better. Backtest (2024-25 / 2025-26 / this season): the model was within 1-3% of the goals scored over full seasons and none of these beat it out of sample.</p></section>`; }
document.getElementById('main').innerHTML = h || '<section class="panel"><p class="note" style="margin:0">No graded games yet.</p></section>';
</script>
"""

if __name__ == "__main__":
    build()
