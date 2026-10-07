#!/usr/bin/env python3
"""
Grade each night's pregame anytime-goal list (hockey/projections/<date>.csv) against who
actually scored, and build hockey/projections/results.html.

    python hockey/results.py

A player counts only if he played (scratched players would void). Goals include
overtime, not shootouts. Backtest reference numbers are shown for comparison.
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
sys.path.insert(0, os.path.dirname(HERE))
import report  # noqa: E402

OUT = os.path.join(HERE, "projections")
DB = os.path.join(HERE, "nhl.db")
BUCKETS = [0, .05, .10, .15, .20, .25, .30, .35, .40, 1]
# hit rate by tier in the walk-forward backtest (2024-25 + 2025-26, 100,502 skater-games): the benchmark
BACKTEST_TIER = {"0–5%": .038, "5–10%": .074, "10–15%": .121, "15–20%": .176, "20–25%": .227, "25–30%": .279, "30–35%": .326, "35–40%": .348, "40–100%": .412}


def actuals():
    con = sqlite3.connect(DB)
    toi = pd.read_sql("SELECT DISTINCT game_id, player_id, name, team FROM toi", con)
    goals = pd.read_sql("SELECT game_id, shooter AS player_id, COUNT(*) AS goals FROM shots "
                        "WHERE goal = 1 AND shooter IS NOT NULL GROUP BY 1, 2", con)
    games = pd.read_sql("SELECT game_id, date, away, home, away_score, home_score FROM games", con)
    # first goal of each game (shootouts are not in the shots table)
    fg = pd.read_sql("SELECT game_id, t, event_id, shooter FROM shots WHERE goal = 1 AND shooter IS NOT NULL", con)
    first = fg.sort_values(["game_id", "t", "event_id"]).drop_duplicates("game_id").set_index("game_id").shooter
    games["first_scorer"] = games.game_id.map(first)
    return toi, goals, games


def grade():
    toi, goals, games = actuals()
    played = set(zip(toi.game_id, toi.player_id))
    final = set(toi.game_id)
    scored = {(r.game_id, int(r.player_id)): int(r.goals) for r in goals.itertuples()}
    by_name = {(r.game_id, str(r.name).lower()): int(r.player_id) for r in toi.itertuples()}
    first = games.set_index("game_id").first_scorer.dropna().astype(int).to_dict()
    rows, void, lineup = [], 0, []
    dressed = toi.groupby(["game_id", "team"]).player_id.apply(set).to_dict()
    for f in sorted(glob.glob(os.path.join(OUT, "????-??-??.csv"))):
        d = pd.read_csv(f)
        date = os.path.basename(f)[:10]
        # Lineup accuracy per team-game: did the listed players play, and who did we miss?
        if "player_id" in d.columns:
            for (gid, team), x in d.groupby(["game_id", "team"]):
                if gid not in final or (gid, team) not in dressed:
                    continue
                listed = set(x.player_id.dropna().astype(int))
                actual = dressed[(gid, team)]
                lineup.append(dict(src=x.src.iloc[0] if "src" in x.columns and pd.notna(x.src.iloc[0]) else "estimate",
                                   listed=len(listed), played=len(listed & actual), missed=len(actual - listed)))
        for r in d.itertuples():
            if r.game_id not in final:
                continue
            pid = int(r.player_id) if "player_id" in d.columns and pd.notna(r.player_id)                 else by_name.get((r.game_id, str(r.name).lower()))
            key = (r.game_id, pid)
            if pid is None or key not in played:
                void += 1
                continue
            g = scored.get(key, 0)
            rows.append(dict(date=date, game_id=int(r.game_id), team=r.team, opp=r.opp, name=r.name, pos=r.pos,
                             p=float(r.p_goal), goals=g, scored=int(g > 0), pp=getattr(r, "pp_role", "") or "",
                             pf=float(getattr(r, "p_first", np.nan)), first=int(first.get(r.game_id) == pid)))
    return pd.DataFrame(rows), void, games, pd.DataFrame(lineup)


def summary(x):
    if x.empty:
        return None
    p = x.p.clip(1e-4, 1 - 1e-4)
    top = x.sort_values("p", ascending=False).groupby("game_id").head(3)
    t5 = x.sort_values("p", ascending=False).groupby("date").head(5)    # the sheet's starred picks
    bg = x.sort_values("p", ascending=False).groupby("game_id").head(1)   # best option in each game
    return dict(n=int(len(x)), exp=round(float(x.p.sum()), 1), act=int(x.scored.sum()),
                logloss=round(float(-(x.scored * np.log(p) + (1 - x.scored) * np.log(1 - p)).mean()), 4),
                brier=round(float(((p - x.scored) ** 2).mean()), 4),
                top3=round(float(top.scored.mean()), 3), top3_pred=round(float(top.p.mean()), 3),
                games=int(x.game_id.nunique()),
                top5_hits=int(t5.scored.sum()), top5_n=int(len(t5)), top5_exp=round(float(t5.p.sum()), 1),
                best_hits=int(bg.scored.sum()), best_n=int(len(bg)), best_exp=round(float(bg.p.sum()), 1))


def first_summary(x):
    """First goal scorer, over games that had first-goal projections."""
    x = x[x.pf.notna()]
    if x.empty:
        return None
    top = x.sort_values("pf", ascending=False).groupby("game_id")
    t1, t3 = top.head(1), top.head(3)
    b = pd.cut(x.pf, [0, .02, .04, .06, .08, 1])
    cal = x.groupby(b, observed=True).agg(n=("pf", "size"), pred=("pf", "mean"), act=("first", "mean"))
    return dict(games=int(x.game_id.nunique()), top_hits=int(t1["first"].sum()), top_exp=round(float(t1.pf.sum()), 1),
                top3_hits=int(t3["first"].sum()), top3_exp=round(float(t3.pf.sum()), 1),
                buckets=[dict(bucket=f"{int(i.left * 100)}–{int(i.right * 100)}%", n=int(r.n), pred=round(float(r.pred), 4),
                              act=round(float(r.act), 4)) for i, r in cal.iterrows()])


def ml_grade(gid, meta, mls, rows):
    """Moneyline / total grade for one game, if a pregame moneyline was saved."""
    m = mls.get(int(gid))
    if not m or pd.isna(meta.home_score):
        return None
    hs, as_ = int(meta.home_score), int(meta.away_score)
    home_won = hs > as_
    fav_home = m["home"] >= m["away"]
    p_fav = max(m["home"], m["away"])
    tot = hs + as_
    rows.append(dict(p=m["home"], y=int(home_won), fav=int(home_won == fav_home), p_fav=p_fav,
                     o55=m["totals"]["5.5"], over=int(tot > 5.5), total=m["total"], act_total=tot))
    return dict(fav=meta.home if fav_home else meta.away, p_fav=round(p_fav, 3), fav_won=bool(home_won == fav_home),
                total=m["total"], act_total=tot)


def ml_summary(rows):
    if not rows:
        return None
    x = pd.DataFrame(rows)
    p = x.p.clip(1e-4, 1 - 1e-4)
    return dict(games=int(len(x)), fav_won=int(x.fav.sum()), fav_exp=round(float(x.p_fav.sum()), 1),
                logloss=round(float(-(x.y * np.log(p) + (1 - x.y) * np.log(1 - p)).mean()), 4),
                total=round(float(x.total.mean()), 2), act_total=round(float(x.act_total.mean()), 2),
                over=int(x.over.sum()), over_exp=round(float(x.o55.sum()), 1))


def build():
    g, void, games, lu = grade()
    data = dict(generated=dt.datetime.now().strftime("%b %d, %Y %I:%M %p").replace(" 0", " "),
                void=void, season=summary(g), nights=[], calib=[], lineup=[],
                first=first_summary(g) if not g.empty else None)
    names = {"official": "Official NHL roster", "lines": "Reported lines", "estimate": "Ice-time estimate"}
    lc = os.path.join(OUT, "linecheck.json")      # written by linecheck.py (shift charts vs the sheet)
    data["linecheck"] = json.load(open(lc)) if os.path.exists(lc) else None
    if not lu.empty:
        for src, x in lu.groupby("src"):
            data["lineup"].append(dict(src=names.get(src, src), teams=int(len(x)),
                                       played=round(float(x.played.sum() / max(x.listed.sum(), 1)), 3),
                                       missed=round(float(x.missed.mean()), 2)))
    if not g.empty:
        b = pd.cut(g.p, BUCKETS)
        cal = g.groupby(b, observed=True).agg(n=("p", "size"), pred=("p", "mean"), act=("scored", "mean"))
        nights = max(g.date.nunique(), 1)
        cal = cal.join(g.groupby(b, observed=True).scored.sum().rename("hits"))
        data["calib"] = [dict(bucket=f"{int(i.left * 100)}–{int(i.right * 100)}%", n=int(r.n), hits=int(r.hits),
                              pred=round(float(r.pred), 3), act=round(float(r.act), 3), per_night=round(float(r.n / nights), 1),
                              bt=BACKTEST_TIER.get(f"{int(i.left * 100)}–{int(i.right * 100)}%")) for i, r in cal.iterrows()]
        gi = games.set_index("game_id")
        mls = {}                                   # pregame moneylines saved with each sheet
        for jf in glob.glob(os.path.join(OUT, "????-??-??.json")):
            for gm in json.load(open(jf)).get("games", []):
                if gm.get("ml"):
                    mls[int(gm["id"])] = gm["ml"]
        mrows = []
        toi_names = {(r.game_id, int(r.player_id)): r.name for r in actuals()[0].itertuples()}
        for date, x in sorted(g.groupby("date"), reverse=True):
            night = dict(date=date, s=summary(x), games=[])
            for gid, y in x.groupby("game_id"):
                meta = gi.loc[gid]
                y = y.sort_values("p", ascending=False)
                fy = None
                if y.pf.notna().any():
                    z = y.sort_values("pf", ascending=False).reset_index(drop=True)
                    hit = z["first"] == 1
                    fy = dict(top=z.name[0], top_team=z.team[0], top_p=round(float(z.pf[0]), 4), top_hit=bool(hit[0]))
                    if hit.any():
                        i = int(hit.idxmax())
                        fy.update(scorer=z.name[i], team=z.team[i], p=round(float(z.pf[i]), 4), rank=i + 1, n=int(len(z)))
                    elif pd.notna(meta.first_scorer):
                        nm = toi_names.get((gid, int(meta.first_scorer)), "unlisted player")
                        fy.update(scorer=nm, team=None, p=None)
                night["games"].append(dict(
                    away=meta.away, home=meta.home, score=[int(meta.away_score or 0), int(meta.home_score or 0)],
                    exp=round(float(y.p.sum()), 1), act=int(y.scored.sum()), first=fy, ml=ml_grade(gid, meta, mls, mrows),
                    top=[dict(name=r.name, team=r.team, p=round(r.p, 3), g=int(r.goals)) for r in y.head(6).itertuples()],
                    surprise=[dict(name=r.name, team=r.team, p=round(r.p, 3), g=int(r.goals))
                              for r in y[(y.scored == 1) & (y.p < .15)].itertuples()]))
            data["nights"].append(night)
        data["ml"] = ml_summary(mrows)
    html = TEMPLATE.replace("__FONTS__", report.FONTS).replace("__CSS__", report.BASE_CSS) \
        .replace("__DATA__", json.dumps(data, separators=(",", ":")).replace("</", "<\\/"))
    path = os.path.join(OUT, "results.html")
    os.makedirs(OUT, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    return path


TEMPLATE = r"""<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Goal Sheet Results</title>
__FONTS__<style>
__CSS__
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:10px}
.tile{background:var(--sunk);border-radius:8px;padding:12px 14px;display:grid;gap:2px}
.tile .v{font-family:var(--display);font-weight:700;font-size:34px;line-height:1}
.tile .s{font-size:12.5px;color:var(--muted)}
.gg{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:14px}
.gcard{background:var(--sunk);border-radius:8px;padding:12px 14px;display:grid;gap:8px}
.gcard h3{margin:0;font-family:var(--display);font-size:20px;letter-spacing:.02em}
.pl{display:grid;grid-template-columns:1fr auto auto;gap:4px 12px;font-size:14px;align-items:baseline}
.hit{color:var(--good);font-weight:700}
.lbtn{display:inline-block;margin-left:10px;padding:4px 10px;border-radius:7px;border:1px solid var(--faint);color:var(--ink);text-decoration:none;font-family:var(--body);font-weight:600;font-size:13px;letter-spacing:0;text-transform:none;background:var(--sunk);vertical-align:middle}
.miss{color:var(--muted)}
</style>
<div class="wrap">
  <header><div><div class="eyebrow" id="sub"></div><h1>Goal Sheet Results</h1></div></header>
  <main id="main"></main>
  <footer>Each night's list is graded against who actually scored (regulation and overtime; shootouts don't count). Only skaters who played are counted. A well-calibrated list has actual scorers close to expected, and players rated around 30% scoring about 30% of the time. Backtest reference (2024–25 and 2025–26): log-loss 0.387–0.391, top 3 per game scored about 35%.</footer>
</div>
<script type="application/json" id="data">__DATA__</script>
<script>
const D = JSON.parse(document.getElementById('data').textContent);
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const pct = p => p == null ? '–' : p < .1 ? (Math.round(p*1000)/10).toFixed(1) + '%' : Math.round(p*100) + '%';
document.getElementById('sub').textContent = `Built ${D.generated}`;
function tiles(s){
  if (!s) return '<p class="note">No graded nights yet. Results appear the morning after each slate.</p>';
  const t = (v, l, sub) => `<div class="tile"><span class="eyebrow">${l}</span><span class="v">${v}</span><span class="s">${sub}</span></div>`;
  return `<div class="tiles">${t(s.act + ` <span class="s">of ${s.exp}</span>`, 'Scorers', 'actual vs expected')}${t(pct(s.top3), 'Top 3 per game scored', `predicted ${pct(s.top3_pred)}`)}${t(`${s.top5_hits} <span class="s">of ${s.top5_n}</span>`, '★ Top 5 picks scored', `expected ${s.top5_exp} · backtest 38%`)}${t(`${s.best_hits} <span class="s">of ${s.best_n}</span>`, 'Best in each game scored', `expected ${s.best_exp} · backtest 38%`)}${t(s.logloss, 'Log-loss', 'lower is better; backtest 0.387–0.391')}${t(s.n, 'Players graded', `${s.games} games`)}</div>`;
}
const pl = p => `<span>${esc(p.name)} <span class="small muted">${esc(p.team)}</span></span><span>${pct(p.p)}</span><span class="${p.g ? 'hit' : 'miss'}">${p.g ? '● ' + (p.g > 1 ? p.g + ' goals' : 'goal') : 'no goal'}</span>`;
// First goal scorer (graded from Oct 1, 2026, when first-goal projections started).
function mlSec(){
  const m = D.ml, bt = 'Backtest 2025–26 (1,312 games): log-loss 0.681 vs 0.696 for always taking the home team; favorites won 54% (predicted 58%). 2024–25: 0.663 vs 0.685.';
  if (!m) return `<p class="note">Moneylines: no graded games yet (started Oct 3, 2026). ${bt}</p>`;
  return `<h3 class="eyebrow" style="margin:6px 0 0">Moneylines and totals</h3><p style="margin:0">Our favorite won <b>${m.fav_won} of ${m.games}</b> (expected ${m.fav_exp}); log-loss ${m.logloss}. Projected total ${m.total} vs actual ${m.act_total}; over 5.5 hit ${m.over} times (expected ${m.over_exp}).</p><p class="note">${bt}</p>`;
}
function firstSec(){
  const f = D.first, bt = 'Backtest 2024–26 (2,792 games): 2.77% predicted vs 2.78% actual per skater, calibrated by bucket.';
  if (!f) return `<p class="note">First goal scorer: no graded games yet (projections started Oct 1, 2026). ${bt}</p>`;
  return `<h3 class="eyebrow" style="margin:6px 0 0">First goal scorer</h3><p style="margin:0">Our top pick scored first in <b>${f.top_hits} of ${f.games}</b> games (expected ${f.top_exp}); one of the top 3 in <b>${f.top3_hits}</b> (expected ${f.top3_exp}).</p>
    <div class="tw"><table class="t"><thead><tr><th class="l">First-goal chance</th><th>Players</th><th>Projected</th><th>Scored first</th></tr></thead><tbody>${f.buckets.map(c => `<tr><td class="l">${c.bucket}</td><td>${c.n}</td><td>${pct(c.pred)}</td><td class="big">${pct(c.act)}</td></tr>`).join('')}</tbody></table></div><p class="note">${bt}</p>`;
}
// Lines, pairs, PP1 and positions on the sheet vs what happened (NHL shift charts, even strength).
function lineCheck(){
  const c = D.linecheck; if (!c || !c.lines) return '';
  const lines = c.lines.reduce((a, r) => { const k = r.kind; a[k] = a[k] || {n: 0, m: 0}; a[k].n += r.players; a[k].m += r.mates * r.players; return a; }, {});
  const pp = (c.pp1 || []).reduce((a, r) => (a.t += r.teams, a.r += r.right * r.teams, a), {t: 0, r: 0});
  const cell = (v, l, sub) => `<div class="tile"><span class="eyebrow">${l}</span><span class="v">${v}</span><span class="s">${sub}</span></div>`;
  return `<div class="tiles" style="margin-top:12px">${lines.forward ? cell(pct(lines.forward.m / lines.forward.n), 'Forward linemates right', `${lines.forward.n} forwards`) : ''}${lines.defense ? cell(pct(lines.defense.m / lines.defense.n), 'D partners right', `${lines.defense.n} defensemen`) : ''}${pp.t ? cell(pct(pp.r / pp.t), 'PP1 right', `${pp.t} team-games`) : ''}${c.positions ? cell(pct(c.positions.right), 'Positions right', `${c.positions.n} players`) : ''}</div>
    <p class="note">Checked against the NHL shift charts over ${c.games} games: a listed linemate counts as right when he was really among the player's most frequent even-strength linemates; PP1 is right when the player was among his team's top five in power-play time; positions are compared with the official game sheet.</p>`;
}
function render(){
  const fair = p => p > 0 ? '+' + Math.round(100 * (1 - p) / p) : '–';
  const cal = D.calib.length ? `<h3 class="eyebrow" style="margin:14px 0 6px">Hit rate by goal-chance tier</h3>
    <p class="small muted" style="margin:0 0 8px">Every dressed skater this season, grouped by his pregame chance. A scorer doesn't have to be a top pick: this shows how often each tier actually scored. Backtest = the same tier over 2024-25 and 2025-26 (100,502 skater-games).</p>
    <div class="tw"><table class="t"><thead><tr><th class="l">Goal chance</th><th>Players</th><th>Per night</th><th>Scored</th><th>Projected</th><th>Hit rate</th><th>Backtest hit rate</th><th>Fair odds</th></tr></thead><tbody>${D.calib.map(c => `<tr><td class="l">${c.bucket}</td><td>${c.n}</td><td class="muted">${c.per_night ?? ''}</td><td>${c.hits ?? ''}</td><td>${pct(c.pred)}</td><td class="big">${pct(c.act)}</td><td class="muted">${c.bt != null ? pct(c.bt) : '–'}</td><td class="muted">${fair(c.pred)}</td></tr>`).join('')}</tbody></table></div>` : '';
  const nights = D.nights.map(n => `<section class="panel"><div class="phead"><h2>${esc(n.date)} <a class="lbtn" href="${esc(n.date)}.html">Pregame goal sheet</a></h2><span class="small muted">${n.s.act} scorers vs ${n.s.exp} expected · ★ top 5: ${n.s.top5_hits} of ${n.s.top5_n} · best in game: ${n.s.best_hits} of ${n.s.best_n} · top 3 per game scored ${pct(n.s.top3)}</span></div>
    <div class="gg">${n.games.map(g => `<div class="gcard"><h3>${esc(g.away)} ${g.score[0]} – ${g.score[1]} ${esc(g.home)}</h3><span class="small muted">${g.act} scorers vs ${g.exp} expected</span>
      <div class="pl">${g.top.map(pl).join('')}</div>
      ${g.ml ? `<span class="small">Moneyline: ${esc(g.ml.fav)} ${pct(g.ml.p_fav)} ${g.ml.fav_won ? '<span class="hit">✓ won</span>' : '<span class="miss">lost</span>'} · total ${g.ml.act_total} (proj ${g.ml.total})</span>` : ''}
      ${g.first ? `<span class="small">First goal: ${g.first.scorer ? `<b>${esc(g.first.scorer)}</b>${g.first.p != null ? ` <span class="muted">${pct(g.first.p)}, #${g.first.rank} of ${g.first.n}</span>` : ' <span class="muted">(not projected)</span>'}` : '<span class="muted">none</span>'} · our pick ${esc(g.first.top)} <span class="muted">${pct(g.first.top_p)}</span>${g.first.top_hit ? ' <span class="hit">✓</span>' : ''}</span>` : ''}
      ${g.surprise.length ? `<span class="small muted">Surprise scorers (under 15%): ${g.surprise.map(p => `${esc(p.name)} ${pct(p.p)}`).join(', ')}</span>` : ''}</div>`).join('')}</div></section>`).join('');
  const lu = D.lineup && D.lineup.length ? `<div class="tw"><table class="t"><thead><tr><th class="l">Lineup source</th><th>Team-games</th><th>Listed players who played</th><th>Dressed but not listed (per team)</th></tr></thead><tbody>${D.lineup.map(r => `<tr><td class="l">${esc(r.src)}</td><td>${r.teams}</td><td class="big">${pct(r.played)}</td><td>${r.missed}</td></tr>`).join('')}</tbody></table></div><p class="note">How well each lineup source predicted who dressed. A perfect lineup lists all 18 skaters who played and misses none.</p>${lineCheck()}` : '';
  document.getElementById('main').innerHTML = `<section class="panel"><div class="phead"><h2>Season so far</h2>${D.void ? `<span class="small muted">${D.void} voided (didn't play)</span>` : ''}</div>${tiles(D.season)}${cal}${mlSec()}${firstSec()}${lu}</section>${nights}`;
}
render();
</script>
"""

if __name__ == "__main__":
    print("Wrote", build())
