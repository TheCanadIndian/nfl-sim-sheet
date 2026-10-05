"""NBA slate page (same look and site navigation as NFL/NHL)."""

import json

import pandas as pd
import os
import sys

sys.path.insert(1, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import report  # noqa: E402

Q = ["mean", "p10", "p25", "median", "p75", "p90"]


def player_payload(tab, meta, dists):
    m = meta.set_index(["game_id", "team", "player_id"])
    out = []
    for (gid, team, pid), x in tab.groupby(["game_id", "team", "player_id"], sort=False):
        r = x.iloc[0]
        k = f"{gid}|{team}|{r['name']}"
        mm = m.loc[(gid, team, pid)] if (gid, team, pid) in m.index else None
        s = {mode: {row.stat: [round(float(getattr(row, q)), 2) for q in Q] for row in y.itertuples()}
             for mode, y in x.groupby("model")}
        out.append(dict(k=k, g=int(gid), t=team, o=r["opp"], n=r["name"], id=int(pid), pos=r["pos"],
                        pg=None if mm is None else mm.pg, st=None if mm is None else mm.status,
                        note=None if mm is None else (mm.note or ""), start=None if mm is None else bool(mm.starter == 1),
                        trend=None if mm is None or pd.isna(mm.min_trend) else round(float(mm.min_trend), 1),
                        gp=None if mm is None else int(mm.games_prior or 0), s=s,
                        d={mode: dists[mode].get(k, {}) for mode in ("vegas", "blind")}))
    return out


def render(payload):
    data = json.dumps(payload, separators=(",", ":")).replace("</", "<\\/")
    return TEMPLATE.replace("__FONTS__", report.FONTS).replace("__CSS__", report.BASE_CSS).replace("__DATA__", data)


TEMPLATE = r"""<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>NBA Sim Sheet</title>
__FONTS__<style>
__CSS__
.gmsw{display:inline-flex;background:var(--sunk);border-radius:8px;padding:3px;margin-right:auto;margin-left:6px}
.gmsw button{border:0;background:none;padding:4px 11px;border-radius:6px;cursor:pointer;font-weight:600;font-size:12.5px;color:var(--muted)}
.gmsw button[aria-pressed="true"]{background:var(--surface);color:var(--ink);box-shadow:0 1px 2px rgba(0,0,0,.12)}
.cell b{font-weight:600}
.cell small{display:block;color:var(--muted);font-size:11.5px}
.tag{font-size:10.5px;font-weight:700;border-radius:4px;padding:0 4px;margin-left:5px;border:1px solid currentColor;white-space:nowrap;cursor:help}
.tag.q{color:#F2C14E} .tag.out{color:var(--r2)}
.lean{font-size:11px;font-weight:700;margin-left:6px;white-space:nowrap;cursor:help}
.g1{color:var(--g1)!important} .g2{color:var(--g2)!important} .g3{color:var(--g3)!important}
.r1{color:var(--r1)!important} .r2{color:var(--r2)!important} .r3{color:var(--r3)!important}
.hm.g1{background:color-mix(in srgb,var(--g1) 12%,transparent)} .hm.g2{background:color-mix(in srgb,var(--g2) 22%,transparent)} .hm.g3{background:color-mix(in srgb,var(--g3) 34%,transparent)}
.hm.r1{background:color-mix(in srgb,var(--r1) 12%,transparent)} .hm.r2{background:color-mix(in srgb,var(--r2) 22%,transparent)} .hm.r3{background:color-mix(in srgb,var(--r3) 34%,transparent)}
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
.sl-side{display:grid}
.sl-side span{font-size:12px;color:var(--muted);text-transform:uppercase;letter-spacing:.07em;font-weight:600}
.sl-side b{font-family:var(--display);font-size:34px;line-height:1;color:var(--ink)}
.sl-side em{font-style:normal;color:var(--muted);font-size:14px}
.sl-side.under{text-align:right}
.sl-meter{height:12px;border-radius:6px;overflow:hidden;display:flex;gap:2px;background:var(--sunk)}
.sl-meter i{display:block;height:100%}
.sl-meter .f-g1{background:var(--g1)} .sl-meter .f-g2{background:var(--g2)} .sl-meter .f-g3{background:var(--g3)}
.sl-meter .f-r1{background:var(--r1)} .sl-meter .f-r2{background:var(--r2)} .sl-meter .f-r3{background:var(--r3)} .sl-meter .f-n{background:var(--whisker)}
.noline{font-size:12.5px;color:#F2C14E}
</style>
<div class="wrap">
  <header>
    <div><div class="eyebrow" id="sub"></div><h1>NBA Sim Sheet</h1></div>
    <div class="tabs" role="tablist" aria-label="View">
      <button role="tab" data-view="games">Games</button>
      <button role="tab" data-view="board">Player board</button>
      <button role="tab" data-view="dvp">Defense vs position</button>
    </div>
  </header>
  <main id="main"></main>
  <footer>Each game is simulated thousands of times. Minutes come from recent games (with a trend toward the latest), the current roster and the injury report; players ruled out are removed and their minutes go to teammates by role. Per-minute production is recency-weighted and pulled toward position averages for small samples; the opponent's defense vs the player's position and the game's pace adjust it. <b>With Vegas</b> scales each team's players to the points implied by the spread and total; <b>Market-blind</b> uses each team's own scoring and defense instead. Medians are the coin-flip number; ranges show 8 in 10 games. Tap a name to slide any line. Q / DTD = questionable / day-to-day (projected to play).</footer>
</div>
<div id="tip" hidden></div>
<script type="application/json" id="data">__DATA__</script>
<script>
const D = JSON.parse(document.getElementById('data').textContent);
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const f0 = v => v == null ? '–' : Math.round(v).toString();
const f1 = v => v == null ? '–' : (Math.round(v*10)/10).toFixed(1);
const pct = p => p == null ? '–' : Math.round(p*100) + '%';
const american = p => { p = Math.min(Math.max(p,1e-4),1-1e-4); const o = p>=.5 ? -100*p/(1-p) : 100*(1-p)/p; return (o>0?'+':'') + Math.round(o); };
const [MEAN, P10, P25, MED, P75, P90] = [0,1,2,3,4,5];
const LAB = {min:'Minutes', pts:'Points', reb:'Rebounds', ast:'Assists', fg3m:'Threes', pra:'Pts+Reb+Ast', stl:'Steals', blk:'Blocks', tov:'Turnovers'};
const COLS = ['min','pts','reb','ast','fg3m','pra'];
const state = {view: ['#board','#dvp'].includes(location.hash) ? location.hash.slice(1) : 'games', game: D.games[0]?.id, gm: {}, bstat: 'pts', dstat: 'pts', open: null, ostat: 'pts', oline: null, okey: null};
document.getElementById('sub').textContent = `${D.games.length} games · ${D.date} · built ${D.generated}`;
const shadeP = p => p >= .65 ? 'g3' : p >= .56 ? 'g2' : p >= .51 ? 'g1' : p <= .35 ? 'r3' : p <= .44 ? 'r2' : p <= .49 ? 'r1' : '';
const shadeR = x => x >= 1.12 ? 'g3' : x >= 1.07 ? 'g2' : x >= 1.03 ? 'g1' : x <= 1/1.12 ? 'r3' : x <= 1/1.07 ? 'r2' : x <= 1/1.03 ? 'r1' : '';
const mode = g => state.gm[g] || 'vegas';
const DVP = {}; (D.dvp || []).forEach(r => { DVP[r.team + '|' + r.pg] = r; });
function probs(d, L){
  if (d.p){ let o = 0, u = 0; d.p.forEach((v,i) => { const k = d.o + i; if (k > L) o += v; else if (k < L) u += v; }); return {over:o, under:u, push:Math.max(0,1-o-u)}; }
  const n = d.q.length; return {over: d.q.filter(v => v > L).length / n, under: d.q.filter(v => v < L).length / n, push: 0};
}
function dmed(d){ if (d.p){ let c = 0; for (let i = 0; i < d.p.length; i++){ c += d.p[i]; if (c >= .5) return d.o + i; } return d.o + d.p.length - 1; } return (d.q[49]+d.q[50])/2; }
// market-blind lean on a stat: blind model's chance of beating the Vegas median vs the Vegas model's own
function lean(p, st){
  const dv = p.d.vegas[st], db = p.d.blind[st]; if (!dv || !db) return null;
  const L = Math.floor(dmed(dv)) + .5, a = probs(dv, L), b = probs(db, L);
  return {edge: b.over - a.over, L};
}
function chips(p, g){
  let h = '';
  if (p.st && p.st !== 'Active') h += `<span class="tag q" title="${esc(p.st + (p.note ? ': ' + p.note : ''))}">${p.st === 'Day-To-Day' ? 'DTD' : p.st === 'Questionable' ? 'Q' : esc(p.st)}</span>`;
  if (p.trend >= 3) h += `<span class="lean g2" title="Minutes trending up: last few games ${f1(p.trend)} above his longer average">▲ min</span>`;
  else if (p.trend <= -3) h += `<span class="lean r2" title="Minutes trending down: last few games ${f1(-p.trend)} below his longer average">▼ min</span>`;
  const l = lean(p, 'pts');
  if (l && Math.abs(l.edge) >= .03) h += `<span class="lean ${shadeP(.5 + l.edge * 2)}" title="Market-blind model ${l.edge > 0 ? 'higher' : 'lower'} on points at ${l.L}">${l.edge > 0 ? '▲' : '▼'} blind</span>`;
  if (p.gp === 0) h += '<span class="tag" style="color:var(--muted)" title="No NBA games yet: league-average role assumed">new</span>';
  return h;
}
function cell(p, st, g){
  const s = p.s[mode(g)] && p.s[mode(g)][st]; if (!s) return '<td>–</td>';
  const dv = st !== 'min' && DVP[p.o + '|' + p.pg] && DVP[p.o + '|' + p.pg][st];
  const cls = dv ? shadeR(dv) : '';
  return `<td class="cell"><b class="${cls}" ${dv ? `title="${esc(p.o)} allows ${Math.round((dv-1)*100)}% ${dv >= 1 ? 'more' : 'less'} ${LAB[st].toLowerCase()} per minute to ${p.pg}s"` : ''}>${st === 'min' ? f0(s[MED]) : f1(s[MED])}</b><small>${f0(s[P10])}–${f0(s[P90])}</small></td>`;
}
function slider(p, g){
  const m = mode(g), stats = COLS.concat(['stl','blk','tov']).filter(s => p.d[m][s]);
  const st = stats.includes(state.ostat) ? state.ostat : 'pts'; state.ostat = st;
  const d = p.d[m][st];
  if (state.oline == null || state.okey !== p.k + st + m){ state.oline = Math.floor(dmed(d)) + .5; state.okey = p.k + st + m; }
  let lo, hi; if (d.p){ lo = d.o + .5; hi = d.o + d.p.length - 1.5; } else { lo = Math.floor(d.q[2]) + .5; hi = Math.ceil(d.q[96]) + .5; }
  hi = Math.max(hi, lo + 1); const L = Math.min(Math.max(state.oline, lo), hi);
  const s = p.s[m][st];
  return `<div class="slider" data-sk="${esc(p.k)}"><div class="sl-top"><div class="seg">${stats.map(x => `<button data-ostat="${x}" aria-pressed="${x===st}">${LAB[x]}</button>`).join('')}</div><span class="muted small">Median <b>${f1(s[MED])}</b> · 8 in 10 games ${f1(s[P10])}–${f1(s[P90])} · ${m === 'vegas' ? 'With Vegas' : 'Market-blind'}</span></div>
    <div class="sl-row"><label for="sl-in">Line</label><button class="sl-step" data-ostep="-1">−</button><input id="sl-in" type="range" min="${lo}" max="${hi}" step="1" value="${L}"><button class="sl-step" data-ostep="1">+</button><b class="sl-val" id="sl-val">${L}</b></div>
    <div id="sl-res">${readout(d, L)}</div>
    ${window.MKT ? MKT.player(g, p.id, p.n, [st], m, true) : ''}</div>`;
}
function readout(d, L){
  const r = probs(d, L), np = Math.max(r.over + r.under, 1e-9), ov = r.over/np, un = r.under/np, so = shadeP(ov), su = shadeP(un);
  return `<div class="sl-out"><div class="sl-side over"><span>Over ${L}</span><b class="${so}">${pct(r.over)}</b><em>${american(ov)}</em></div>
    <div class="sl-meter"><i class="f-${so||'n'}" style="width:${r.over*100}%"></i><i class="f-${su||'n'}" style="width:${r.under*100}%"></i></div>
    <div class="sl-side under"><span>Under ${L}</span><b class="${su}">${pct(r.under)}</b><em>${american(un)}</em></div></div>`;
}
function teamTable(g, team){
  const ps = D.players.filter(p => p.g === g.id && p.t === team).sort((a,b) => (b.s[mode(g.id)]?.min?.[MED] ?? 0) - (a.s[mode(g.id)]?.min?.[MED] ?? 0));
  const rows = ps.map(p => `<tr class="${state.open === p.k ? 'opened' : ''}"><td class="l name"><button class="plink${state.open === p.k ? ' open' : ''}" data-open="${esc(p.k)}"><span class="caret">${state.open === p.k ? '▾' : '▸'}</span>${esc(p.n)}</button>${chips(p, g.id)}</td><td class="l"><span class="pos">${esc(p.pos)}${p.start ? '' : ''}</span></td>${COLS.map(c => cell(p, c, g.id)).join('')}</tr>`
    + (state.open === p.k ? `<tr class="xrow"><td colspan="${COLS.length + 2}">${slider(p, g.id)}</td></tr>` : '')).join('');
  const outs = (g.out && g.out[team]) || [];
  const outLine = outs.length ? `<p class="muted small" style="margin:6px 0 0"><b style="color:var(--r2)">Out:</b> ${outs.map(o => `${esc(o.n)}${o.note ? ` <span title="${esc(o.note)}">(${esc(o.st)})</span>` : ''}`).join(', ')}. Their minutes are shared among the players below.</p>` : '';
  return outLine + `<div class="tw"><table class="t"><thead><tr><th class="l">${esc(team)}</th><th class="l">Pos</th>${COLS.map(c => `<th>${c === 'fg3m' ? '3PM' : c === 'pra' ? 'PRA' : c.toUpperCase()}</th>`).join('')}</tr></thead><tbody>${rows}</tbody></table></div>`;
}
function gameView(){
  const g = D.games.find(x => x.id === state.game) || D.games[0]; if (!g) return '<div class="empty">No games.</div>';
  const M = g.models[mode(g.id)], hw = M.home_win;
  const rail = D.games.map(x => `<button class="game-btn" data-game="${x.id}" aria-current="${x.id === g.id}"><span class="m"><span class="teams">${esc(x.away)} @ ${esc(x.home)}</span><span class="line">${esc(x.start.split('·')[1] || '')}</span></span><span class="line">${x.has_line ? `${esc(x.home)} ${x.spread > 0 ? '+' : ''}${x.spread} · O/U ${x.total}` : 'no line yet'}</span></button>`).join('');
  const sw = `<span class="gmsw" role="group"><button data-gmodel="vegas" aria-pressed="${mode(g.id) === 'vegas'}">With Vegas</button><button data-gmodel="blind" aria-pressed="${mode(g.id) === 'blind'}">Market-blind</button></span>`;
  const head = `<section class="panel"><div class="phead"><h2>${esc(g.away)} at ${esc(g.home)}</h2>${sw}<span class="muted small">${esc(g.start)}${g.has_line ? ` · ${esc(g.home)} ${g.spread > 0 ? '+' : ''}${g.spread} · O/U ${g.total}` : ''}</span></div>
    ${!g.has_line && mode(g.id) === 'vegas' ? '<p class="noline" style="margin:0">No betting line posted yet: With Vegas uses the market-blind team forecast until it is.</p>' : ''}
    <div class="score"><div class="side away"><span class="code">${esc(g.away)}</span><span class="pts">${f1(M.away_pts)}</span><span class="muted small">win ${pct(1 - hw)}</span></div><span class="at">@</span><div class="side home"><span class="code">${esc(g.home)}</span><span class="pts">${f1(M.home_pts)}</span><span class="muted small">win ${pct(hw)}</span></div></div>
    <div class="winbar"><div class="bar"><i style="flex:${1 - hw};background:var(--away)"></i><i style="flex:${hw};background:var(--accent)"></i></div></div>
    ${window.MKT ? MKT.game(g.id, mode(g.id)) : ''}</section>`;
  const tables = `<section class="panel"><div class="phead"><h2>Players</h2><span class="muted small">Median, with 8-in-10 range below. Color = matchup vs this defense. Tap a name to slide a line.</span></div>${teamTable(g, g.away)}${teamTable(g, g.home)}</section>`;
  return `<div class="games"><nav class="rail" aria-label="Games">${rail}</nav><div class="detail">${head}${tables}</div></div>`;
}
function board(){
  const st = state.bstat, rows = D.players.filter(p => p.s.vegas && p.s.vegas[st]).sort((a,b) => b.s[mode(a.g)][st][MED] - a.s[mode(b.g)][st][MED] || 0);
  rows.sort((a,b) => b.s[mode(b.g)][st][MED] - a.s[mode(a.g)][st][MED]);
  return `<section class="panel"><div class="phead"><div class="seg">${COLS.map(c => `<button data-bstat="${c}" aria-pressed="${c === st}">${LAB[c]}</button>`).join('')}</div><span class="muted small">Uses each game's selected model</span></div>
  <div class="tw"><table class="t"><thead><tr><th>#</th><th class="l">Player</th><th class="l">Game</th>${COLS.map(c => `<th>${c === 'fg3m' ? '3PM' : c === 'pra' ? 'PRA' : c.toUpperCase()}</th>`).join('')}</tr></thead><tbody>
  ${rows.slice(0, 150).map((p,i) => `<tr><td class="muted">${i+1}</td><td class="l name">${esc(p.n)}${chips(p, p.g)}</td><td class="l">${esc(p.t)} <span class="small muted">vs ${esc(p.o)}</span></td>${COLS.map(c => cell(p, c, p.g)).join('')}</tr>`).join('')}</tbody></table></div></section>`;
}
function dvpView(){
  const st = state.dstat, teams = [...new Set((D.dvp || []).map(r => r.team))].sort();
  const today = new Set(D.games.flatMap(g => [g.home, g.away]));
  const rows = teams.map(t => ({t, v: ['G','F','C'].map(pg => (DVP[t + '|' + pg] || {})[st])})).sort((a,b) => (b.v[0]+b.v[1]+b.v[2]) - (a.v[0]+a.v[1]+a.v[2]));
  const rel = v => v == null ? '–' : (v >= 1 ? '+' : '−') + Math.abs(Math.round((v-1)*100)) + '%';
  return `<section class="panel"><div class="phead"><div class="seg">${['pts','reb','ast','fg3m'].map(c => `<button data-dstat="${c}" aria-pressed="${c === st}">${LAB[c]}</button>`).join('')}</div><span class="muted small">Per minute allowed vs league average · bold = plays on this slate</span></div>
  <div class="tw"><table class="t"><thead><tr><th class="l">Defense</th><th>Guards</th><th>Forwards</th><th>Centers</th></tr></thead><tbody>
  ${rows.map(r => `<tr><td class="l">${today.has(r.t) ? `<b>${esc(r.t)}</b>` : esc(r.t)}</td>${r.v.map(v => `<td class="hm ${v ? shadeR(v) : ''}">${rel(v)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>
  <p class="note">Green = gives up more to that position (good for the player), red = less. Recent games count most; last season carries over early on; small samples are pulled toward average.</p></section>`;
}
function render(){
  for (const b of document.querySelectorAll('.tabs button')) b.setAttribute('aria-selected', b.dataset.view === state.view);
  document.getElementById('main').innerHTML = state.view === 'board' ? board() : state.view === 'dvp' ? dvpView() : gameView();
}
document.addEventListener('click', e => {
  const t = e.target.closest('button'); if (!t) return;
  if (t.dataset.view){ state.view = t.dataset.view; render(); }
  else if (t.dataset.game){ state.game = +t.dataset.game; state.open = null; render(); }
  else if (t.dataset.gmodel){ state.gm[state.game] = t.dataset.gmodel; state.okey = null; render(); }
  else if (t.dataset.open){ state.open = state.open === t.dataset.open ? null : t.dataset.open; state.okey = null; render(); }
  else if (t.dataset.ostat){ state.ostat = t.dataset.ostat; state.okey = null; render(); }
  else if (t.dataset.ostep){ const i = document.getElementById('sl-in'); if (i){ i.value = Math.min(+i.max, Math.max(+i.min, +i.value + (+t.dataset.ostep))); i.dispatchEvent(new Event('input', {bubbles: true})); } }
  else if (t.dataset.bstat){ state.bstat = t.dataset.bstat; render(); }
  else if (t.dataset.dstat){ state.dstat = t.dataset.dstat; render(); }
});
document.addEventListener('input', e => {
  if (e.target.id !== 'sl-in') return;
  const box = e.target.closest('.slider'), p = D.players.find(x => x.k === box.dataset.sk), g = state.game;
  state.oline = +e.target.value; document.getElementById('sl-val').textContent = state.oline;
  document.getElementById('sl-res').innerHTML = readout(p.d[mode(p.g)][state.ostat], state.oline);
});
render();
</script>
"""
