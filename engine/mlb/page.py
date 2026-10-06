"""MLB home-run page: slate summary + per-game matchup tables, rolling form, pitcher / hitter zones, exports."""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import report  # noqa: E402

OUT = os.path.join(HERE, "projections")

TEMPLATE = r"""<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>MLB home runs __DATE__</title>
__FONTS__
<style>__CSS__
.bar1{display:flex;flex-wrap:wrap;justify-content:space-between;align-items:baseline;gap:8px}
.bar1 .ttl{font-family:var(--display);font-size:20px;letter-spacing:.06em;text-transform:uppercase;color:var(--muted)}
.games{display:flex;gap:10px;overflow-x:auto;padding-bottom:4px}
.gbtn{flex:0 0 auto;min-width:150px;display:grid;gap:2px;justify-items:center;padding:12px 16px;border-radius:12px;border:1px solid var(--faint);background:var(--surface);color:var(--ink);cursor:pointer}
.gbtn[aria-pressed="true"]{border-color:var(--sel);background:color-mix(in srgb,var(--sel) 12%,var(--surface))}
.gbtn b{font-family:var(--display);font-size:20px;letter-spacing:.04em}
.gbtn span{font-size:13px;color:var(--muted)}
.tabs2{display:flex;flex-wrap:wrap;gap:8px}
.tabs2 button{padding:8px 16px;border-radius:999px;border:1px solid var(--faint);background:none;color:var(--ink);font-weight:600;cursor:pointer}
.tabs2 button[aria-pressed="true"]{background:color-mix(in srgb,var(--sel) 18%,transparent);border-color:var(--sel)}
.blk{display:grid;gap:10px}
.blkh{display:flex;flex-wrap:wrap;justify-content:space-between;align-items:center;gap:8px}
.blkh h2{font-size:22px}
.ctl{display:flex;align-items:center;gap:8px;color:var(--muted);font-size:13px}
.ctl select{background:var(--surface);color:var(--ink);border:1px solid var(--faint);border-radius:8px;padding:6px 10px}
table.h{border-collapse:separate;border-spacing:0;width:100%;font-size:13.5px;font-variant-numeric:tabular-nums}
table.h th{font-size:10.5px;letter-spacing:.07em;text-transform:uppercase;color:var(--muted);font-weight:600;text-align:right;padding:8px 8px;white-space:nowrap;position:sticky;top:0;background:var(--surface)}
table.h th.l,table.h td.l{text-align:left}
table.h td{padding:9px 8px;text-align:right;border-top:1px solid var(--faint);white-space:nowrap}
table.h td.nm{font-weight:600;color:var(--good)}
table.h td.nm small{color:var(--muted);font-weight:600;margin-left:3px;font-size:11px}
table.h tr:hover td{filter:brightness(1.12)}
.wrapx{overflow-x:auto;border:1px solid var(--faint);border-radius:10px}
.note2{color:var(--muted);font-size:12.5px;margin:0}
.sp{border:1px solid var(--faint);border-radius:12px;padding:14px;display:grid;gap:10px;background:var(--surface)}
.sp h3{margin:0;font-size:20px;font-weight:600}
.sp .who{color:var(--muted);font-size:13px}
.grids{display:flex;flex-wrap:wrap;gap:18px}
.grid5{display:grid;grid-template-columns:repeat(5,44px);grid-template-rows:repeat(5,44px);gap:2px;position:relative}
.grid5 div{display:flex;align-items:center;justify-content:center;font-size:11px;font-weight:600;border-radius:3px;color:#fff}
.grid5 .inz{outline:1px solid rgba(255,255,255,.55)}
.gcap{font-size:12px;color:var(--muted);text-align:center;margin-top:4px}
.mv{background:var(--sunk);border-radius:8px}
.arrow{font-size:11px;margin-left:3px}
.rk{display:inline-block;min-width:26px;font-weight:700;color:#f6c84c}
.val{display:inline-block;margin-left:6px;padding:0 6px;border-radius:9px;font-size:10.5px;font-weight:800;background:#1f9d55;color:#fff;letter-spacing:.03em}
.picks{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,330px),1fr));gap:12px}
.pick{border:1px solid var(--faint);border-radius:12px;padding:12px 14px;background:var(--surface);display:grid;gap:6px}
.pick h3{margin:0;font-size:16px;font-family:var(--display);letter-spacing:.04em}
.pick .r{display:grid;grid-template-columns:28px 1fr auto auto;gap:8px;align-items:center;font-size:14px}
.pick .r small{color:var(--muted)}
.pf{margin:0;font-size:14px} .pf b{color:#6cb4ff}
.btnx{padding:8px 14px;border-radius:8px;border:1px solid var(--faint);background:var(--surface);color:var(--ink);font-weight:600;cursor:pointer}
</style>
<div class="wrap">
  <div class="bar1"><span class="ttl">Home run matchups · <span id="gname"></span></span><span class="muted" id="dt"></span></div>
  <div class="games" id="games"></div>
  <div class="tabs2" id="tabs"></div>
  <main id="main" class="blk"></main>
  <p class="note2">HR% is the blend's chance of 1+ home run (stacked on the per-PA model; tested Aug-Oct 2026: more accurate than the model, and its top 3 per game held 0.52 HR hitters vs 0.33 for random). Model% = the per-PA model alone. VALUE = blend beats Kalshi's ask by 6%+ after the fee. Matchup = percentile of his per-PA HR chance vs this starter among all 2026 PAs. Ceiling = 90th-percentile exit velocity. Zone Fit, Mix Fit and HR Form are context: in testing they did not improve the HR prediction. Cell colors = league percentile (green good for the hitter).</p>
</div>
<script type="application/json" id="data">__DATA__</script>
<script>
const D = JSON.parse(document.getElementById('data').textContent);
const SC = D.scout || {hitters: {}, pitchers: {}};
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const pc = v => v == null ? '–' : (v * 100).toFixed(1) + '%';
const f3 = v => v == null ? '–' : v.toFixed(3).replace(/^0/, '');
const odds = o => (o > 0 ? '+' : '') + o;
// percentile -> background (red 0 .. amber 50 .. green 100), like a scouting heat table
function heat(p, invert){
  if (p == null) return '';
  if (invert) p = 100 - p;
  const t = Math.max(0, Math.min(100, p)) / 100;
  const c = t < .5 ? [[181, 71, 63], [199, 154, 58]] : [[199, 154, 58], [46, 158, 100]];
  const u = t < .5 ? t / .5 : (t - .5) / .5;
  const rgb = c[0].map((a, i) => Math.round(a + (c[1][i] - a) * u));
  return `background:rgba(${rgb.join(',')},.78);color:#fff`;
}
let st = {game: 'summary', tab: 'matchup', sort: 'default', hitter: null, sp: {}};
try { Object.assign(st, JSON.parse(localStorage.getItem('mlb-view') || '{}')); } catch (e) {}
const G = D.games, P = D.players;
const H = b => SC.hitters[String(b)] || {};
const ranks = {}; P.slice().sort((a, b) => b.p_hr - a.p_hr).forEach((p, i) => ranks[p.batter + '|' + p.game_pk] = 100 - i / Math.max(P.length - 1, 1) * 100);

function games(){
  const now = Date.now();
  const btn = (id, top, sub) => `<button class="gbtn" data-game="${id}" aria-pressed="${st.game == id}"><b>${top}</b><span>${sub}</span></button>`;
  return btn('summary', 'Slate', 'Summary') + G.map(g => { const t = new Date(g.start_utc);
    return btn(g.id, `${esc(g.away)} @ ${esc(g.home)}`, `${t.toLocaleTimeString([], {hour: 'numeric', minute: '2-digit'})} · ${t > now ? 'Scheduled' : 'Started'}`); }).join('');
}
// Kalshi 1+ HR ask for this hitter (live), and our edge after the fee; "value" = blend beats the ask by 6%+
function kask(p){ if (!window.MKT || !MKT.has()) return null; const r = MKT.rows().find(r => String(r.g) === String(p.game_pk) && r.k === 'hr' && (r.l == null || r.l < 1) && r.s === 'kalshi' && (String(r.pid) === String(p.batter))); return r && r.ya ? r.ya : null; }
function edge(p){ const a = kask(p); return a == null ? null : p.p_hr - a - .07 * a * (1 - a); }
// data-confidence traffic light: weighted PAs (hitters) / batters faced (pitchers) = this season + 0.6 x last
const LIGHT = {green: ['#2fbf71', 'enough data'], yellow: ['#f2c14e', 'building: numbers still lean partly on league averages'], red: ['#e5534b', 'thin data: numbers lean mostly on league averages']};
const dot = (o, what) => { if (!o || !o.light) return ''; const [c, t] = LIGHT[o.light];
  return `<span title="${o.data_n} weighted ${what} of data (this season + 0.6 x last) -- ${t}" style="display:inline-block;width:9px;height:9px;border-radius:50%;background:${c};margin-right:6px;vertical-align:1px"></span>`; };
const star = p => p.game_rank <= 3 ? `<span class="rk" title="Top ${p.game_rank} pick in this game (blend)">★${p.game_rank}</span>` : `<span class="rk muted">${p.game_rank ?? ''}</span>`;
const valueChip = p => { const e = edge(p); return e != null && e >= .06 ? `<span class="val" title="Blend beats Kalshi's ask by ${(e * 100).toFixed(1)} pts after the fee. Aug-Oct 2026 backtest: 6%+ edges returned ~+50% on stake (about 200 bets).">VALUE +${(e * 100).toFixed(0)}</span>` : ''; };
// swing-plane fit: his predicted launch angle at this starter's usual location (Aug-Oct 2026 backtest)
const PLANE = [[5, -.44, 'poor: pitcher works below his band'], [10, -.15, 'weak'], [15, -.02, 'neutral'], [20, .13, 'good'], [25, .19, 'very good'], [99, .19, 'excellent (rare; small sample)']];
const planeOf = p => { if (p.plane_la == null) return null; const b = PLANE.find(x => p.plane_la < x[0]); return {la: p.plane_la, eff: b[1], txt: b[2]}; };
const SLOPE = {steeper: ['steeper plane than most', .30], average: ['average plane', 0], flatter: ['flatter plane than most', -.13]};
const planeCell = p => { const f = planeOf(p); return f ? `${Math.round(f.la)}° <small>${f.eff >= 0 ? '+' : ''}${Math.round(f.eff * 100)}%</small>` : '–'; };
const planeHeat = p => { const f = planeOf(p); return f ? heat(50 + f.eff * 150) : ''; };
const COLS = [
  ['rank', 'Game rank', p => star(p), () => ''],
  ['hr', 'HR%', p => pc(p.p_hr) + valueChip(p), p => heat(ranks[p.batter + '|' + p.game_pk])],
  ['model', 'Model%', p => pc(p.p_model ?? p.p_hr), () => 'color:var(--muted)'],
  ['fair', 'Fair', p => odds(p.fair), () => ''],
  ['kalshi', 'Kalshi', p => window.MKT && MKT.has() ? MKT.cell(p.game_pk, p.batter, p.name, 'hr') : '–', () => ''],
  ['matchup', 'Matchup', p => H(p.batter).matchup ?? '–', p => heat(H(p.batter).matchup)],
  ['ceiling', 'Ceiling', p => H(p.batter).season?.ev90 ?? '–', p => heat(H(p.batter).pct?.ev90)],
  ['plane', 'Plane fit', p => planeCell(p), p => planeHeat(p)],
  ['zone', 'Zone fit', p => fit(H(p.batter).zone_fit), p => heat(fitp(H(p.batter).zone_fit))],
  ['mix', 'Mix fit', p => fit(H(p.batter).mix_fit), p => heat(fitp(H(p.batter).mix_fit))],
  ['form', 'HR form', p => form(H(p.batter).form), p => H(p.batter).form ? heat(H(p.batter).form.pct) : 'color:var(--muted)'],
  ['pit', 'Pit', p => num(H(p.batter).season?.pit), () => ''],
  ['bip', 'BIP', p => num(H(p.batter).season?.bip), () => ''],
  ['iso', 'ISO', p => f3(H(p.batter).season?.iso), p => heat(H(p.batter).pct?.iso)],
  ['xwoba', 'xwOBA', p => f3(H(p.batter).season?.xwoba), p => heat(H(p.batter).pct?.xwoba)],
  ['xwobacon', 'xwOBAcon', p => f3(H(p.batter).season?.xwobacon), p => heat(H(p.batter).pct?.xwobacon)],
  ['swstr', 'SwStr%', p => pc(H(p.batter).season?.swstr), p => heat(H(p.batter).pct?.swstr, true)],
  ['pullbrl', 'PullBrl%', p => pc(H(p.batter).season?.pullbrl), p => heat(H(p.batter).pct?.pullbrl)],
  ['brl', 'Brl/BIP%', p => pc(H(p.batter).season?.brl_bip), p => heat(H(p.batter).pct?.brl_bip)],
  ['sweet', 'Sweet%', p => pc(H(p.batter).season?.sweet), p => heat(H(p.batter).pct?.sweet)],
  ['fb', 'FB%', p => pc(H(p.batter).season?.fb), p => heat(H(p.batter).pct?.fb)],
  ['hh', 'HardHit%', p => pc(H(p.batter).season?.hardhit), p => heat(H(p.batter).pct?.hardhit)],
  ['hrs', 'HR', p => num(H(p.batter).season?.hr), () => ''],
];
const SORTS = {default: 'Lineup order', rank: 'Game rank', hr: 'HR%', plane: 'Plane fit', matchup: 'Matchup', ceiling: 'Ceiling', xwoba: 'xwOBA', brl: 'Brl/BIP%', pullbrl: 'PullBrl%', fb: 'FB%', form: 'HR form'};
const sortVal = {rank: p => -p.game_rank, plane: p => p.plane_la ?? -99, hr: p => p.p_hr, matchup: p => H(p.batter).matchup ?? -1, ceiling: p => H(p.batter).season?.ev90 ?? 0, xwoba: p => H(p.batter).season?.xwoba ?? 0,
  brl: p => H(p.batter).season?.brl_bip ?? 0, pullbrl: p => H(p.batter).season?.pullbrl ?? 0, fb: p => H(p.batter).season?.fb ?? 0, form: p => H(p.batter).form?.pct ?? -1};
function num(v){ return v == null ? '–' : v.toLocaleString(); }
function fit(v){ return v == null ? '–' : (v >= 0 ? '+' : '') + (v * 100).toFixed(1) + '%'; }
function fitp(v){ return v == null ? null : 50 + Math.max(-50, Math.min(50, v * 500)); }
function form(f){ return f ? `${f.pct}%<span class="arrow">${f.trend === 'up' ? '↑' : f.trend === 'down' ? '↓' : '→'}</span>` : '–'; }
function table(ps){
  return `<div class="wrapx"><table class="h"><thead><tr><th class="l">Hitter</th>${COLS.map(c => `<th>${c[1]}</th>`).join('')}</tr></thead><tbody>
    ${ps.map(p => `<tr><td class="l nm">${dot(H(p.batter), 'PAs')}${esc(p.name)}<small>${esc(p.bats)}HB</small></td>${COLS.map(c => `<td style="${c[3](p)}">${c[2](p)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>
    <p class="note2">Data light: <span style="color:#2fbf71">●</span> 300+ weighted PAs (reliable) · <span style="color:#f2c14e">●</span> 120-300 (building) · <span style="color:#e5534b">●</span> under 120 (thin: leans on league averages).</p>`;
}
function sorted(ps){ return st.sort === 'default' ? ps.slice().sort((a, b) => a.slot - b.slot) : ps.slice().sort((a, b) => sortVal[st.sort](b) - sortVal[st.sort](a)); }
const sortCtl = () => `<label class="ctl">Sort by <select id="sort">${Object.entries(SORTS).map(([k, v]) => `<option value="${k}"${st.sort === k ? ' selected' : ''}>${v}</option>`).join('')}</select></label>`;

function summary(){
  const ps = P.slice().sort((a, b) => b.p_hr - a.p_hr).slice(0, 30);
  const picks = G.map(g => { const top = P.filter(p => p.game_pk === g.id && p.game_rank <= 3).sort((a, b) => a.game_rank - b.game_rank);
    return `<div class="pick"><h3>${esc(g.away)} @ ${esc(g.home)} · top 3</h3>${top.map(p => `<div class="r"><span class="rk">★${p.game_rank}</span><span>${dot(H(p.batter), 'PAs')}${esc(p.name)} <small>${esc(p.team)} · ${esc(p.lineup === 'posted' ? '#' + p.slot : 'proj #' + p.slot)} · vs ${esc(p.sp_name || 'TBD')}</small></span><b>${pc(p.p_hr)}</b><span>${valueChip(p) || (kask(p) != null ? `<small>${Math.round(kask(p) * 100)}¢</small>` : '')}</span></div>`).join('')}</div>`; }).join('');
  return `<section class="blk"><div class="blkh"><h2>Game picks · top 3 in each game</h2></div><div class="picks">${picks}</div>
    <p class="note2">Ranked by the blend across both lineups. Aug-Oct 2026 backtest: a game's top 3 held 0.52 of its home-run hitters on average (random 3: 0.33); 44% of games had at least one of the three homer.</p></section>
    <section class="blk"><div class="blkh"><h2>Slate summary · most likely to homer</h2></div>
    <div class="wrapx"><table class="h"><thead><tr><th class="l">#</th><th class="l">Hitter</th><th class="l">Game</th><th class="l">vs</th>${COLS.slice(0, 8).map(c => `<th>${c[1]}</th>`).join('')}</tr></thead><tbody>
    ${ps.map((p, i) => `<tr><td class="l muted">${i + 1}</td><td class="l nm">${dot(H(p.batter), 'PAs')}${esc(p.name)}<small>${esc(p.bats)}HB</small></td><td class="l muted">${esc(p.team)}</td><td class="l muted">${esc(p.sp_name || 'TBD')}</td>${COLS.slice(0, 8).map(c => `<td style="${c[3](p)}">${c[2](p)}</td>`).join('')}</tr>`).join('')}</tbody></table></div></section>`;
}
function spCard(sid, name, team){
  const S_ = SC.pitchers[String(sid)]; if (!S_) return `<div class="sp"><h3>${esc(name || 'TBD')}</h3><p class="note2">No 2026 data.</p></div>`;
  const tab = st.sp[sid] || 'summary';
  const tb = ['summary', 'arsenal', 'counts', 'shape'].map(k => `<button data-sptab="${k}" data-sid="${sid}" aria-pressed="${tab === k}">${{summary: 'Summary', arsenal: 'Arsenal', counts: 'Count usage', shape: 'Pitch shape'}[k]}</button>`).join('');
  let body = '';
  if (tab === 'summary'){
    const M = [['xwoba', 'xwOBA', f3, true], ['csw', 'CSW%', pc, false], ['swstr', 'SwStr%', pc, false], ['brl_bip', 'Brl/BIP%', pc, true], ['fb', 'FB%', pc, true], ['hardhit', 'HardHit%', pc, true]];
    body = `<div class="wrapx"><table class="h"><thead><tr><th class="l">Split</th><th>BF</th>${M.map(m => `<th>${m[1]}</th>`).join('')}</tr></thead><tbody>
      ${Object.entries(S_.splits).map(([k, v]) => `<tr><td class="l">${k}</td><td>${v.pa}</td>${M.map(m => `<td style="${heat(S_.pct[k][m[0]], m[3])}">${m[2](v[m[0]])}</td>`).join('')}</tr>`).join('')}</tbody></table></div>
      <p class="note2">Colors: league percentile among 2026 pitchers (green = good for the pitcher). ${S_.starts} starts.</p>`;
  } else if (tab === 'arsenal'){
    body = `<div class="wrapx"><table class="h"><thead><tr><th class="l">Pitch</th><th>Usage</th><th>vs RHH</th><th>vs LHH</th><th>Velo</th><th>Whiff%</th><th>xwOBAcon</th></tr></thead><tbody>
      ${S_.arsenal.map(a => `<tr><td class="l">${esc(a.pitch)}</td><td>${pc(a.usage)}</td><td>${pc(a.vs_r)}</td><td>${pc(a.vs_l)}</td><td>${a.velo}</td><td>${pc(a.whiff)}</td><td>${f3(a.xwobacon)}</td></tr>`).join('')}</tbody></table></div>`;
  } else if (tab === 'counts'){
    const pitches = [...new Set(Object.values(S_.counts).flatMap(o => Object.keys(o)))];
    const lab = {even: 'Even', ahead: 'Pitcher ahead', behind: 'Pitcher behind', two_strikes: 'Two strikes'};
    body = `<div class="wrapx"><table class="h"><thead><tr><th class="l">Count</th>${pitches.map(x => `<th>${esc(x)}</th>`).join('')}</tr></thead><tbody>
      ${Object.keys(lab).filter(k => S_.counts[k]).map(k => `<tr><td class="l">${lab[k]}</td>${pitches.map(x => `<td style="${heat((S_.counts[k][x] || 0) * 200)}">${pc(S_.counts[k][x] || 0)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
  } else {
    const opp = P.filter(p => String(p.sp_id) === String(sid)).sort((a, b) => a.slot - b.slot);
    const ov = (st.ov || {})[sid];
    const pick = `<label class="ctl">Overlay batter <select data-ov="${sid}"><option value="">None</option>${opp.map(p => `<option value="${p.batter}"${String(ov) === String(p.batter) ? ' selected' : ''}>${esc(p.name)} (${esc(p.bats)})</option>`).join('')}</select></label>`;
    body = pick + shapeBoxes(S_, opp.find(p => String(p.batter) === String(ov))) + oldShape(S_);
  }
  return `<div class="sp"><div class="blkh"><div><span class="who">${esc(team)} starter</span><h3>${dot(S_, 'batters faced')}${esc(name)}</h3></div><div class="tabs2">${tb}</div></div>${body}</div>`;
}
const PCOL = {FF: '#e5534b', SI: '#f0883e', FC: '#c58c5a', SL: '#f2c14e', ST: '#e3b341', SV: '#d29922', CU: '#58a6ff', KC: '#4e8ee6', CS: '#79c0ff', CH: '#3fb950', FS: '#2ea043', FO: '#56d364', SC: '#a371f7', KN: '#a371f7'};
// catcher's view: x in feet (+ = 1B side / catcher's right), height in units of the zone; RHH box on the left, LHH on the right
function shapeBoxes(S_, bat){
  const W = 300, Hh = 340, X = v => W / 2 + v * 80, Z = z => 250 - z * 140;
  const XE = [-1.33, -0.83, -0.28, 0.28, 0.83, 1.33], ZE = [-0.33, 0, 1 / 3, 2 / 3, 1, 1.33];
  const bside = bat ? (bat.bats === 'S' ? (bat.sp_hand === 'R' ? 'L' : 'R') : bat.bats) : null;
  const overlay = side => {
    if (!bat || side !== bside) return '';
    const h = H(bat.batter), z = h.zones || [];
    const cells = z.map((c, i) => { const r = Math.floor(i / 5), k = i % 5; if (c.xcon == null) return '';
      const t = Math.max(0, Math.min(1, (c.xcon - .2) / .4)), col = t > .5 ? `rgba(46,158,100,${(t - .5) * 1.1})` : `rgba(181,71,63,${(.5 - t) * 1.1})`;
      return `<rect x="${X(XE[k])}" y="${Z(ZE[5 - r])}" width="${X(XE[k + 1]) - X(XE[k])}" height="${Z(ZE[4 - r]) - Z(ZE[5 - r])}" fill="${col}"><title>xwOBA on contact ${c.xcon}</title></rect>`; }).join('');
    let plane = '';
    const s = h.swing;
    if (s && Math.abs(s.bz) > 1e-3){
      const zAt = (la, x) => (la - s.a - s.bx * x) / s.bz;            // height where predicted launch angle = la
      const pts = la => [-1.2, 1.2].map(x => `${X(x)},${Z(Math.max(-.3, Math.min(1.3, zAt(la, x))))}`);
      const band = [...pts(15), ...pts(30).reverse()].join(' ');
      plane = `<polygon points="${band}" fill="rgba(108,180,255,.18)" stroke="none"/><polyline points="${pts(22).join(' ')}" fill="none" stroke="#6cb4ff" stroke-width="2.5" stroke-dasharray="6 4"/>
        <text x="${X(-1.25)}" y="${Z(Math.max(-.3, Math.min(1.3, zAt(22, -1.2)))) - 6}" fill="#6cb4ff" font-size="10.5" font-weight="700">swing plane (est.)</text>`;
    }
    return cells + plane;
  };
  const one = side => {
    const pts = S_.arsenal.filter(a => a.by_side && a.by_side[side]).map(a => ({a, s: a.by_side[side]}));
    const box = side === 'R' ? `<rect x="${X(-1.95)}" y="${Z(1.55)}" width="${80 * .95}" height="${140 * 1.9}" fill="none" stroke="var(--muted)" stroke-dasharray="4 3"/><text x="${X(-1.48)}" y="${Z(1.62)}" text-anchor="middle" fill="var(--muted)" font-size="11">RHH</text>`
      : `<rect x="${X(1.0)}" y="${Z(1.55)}" width="${80 * .95}" height="${140 * 1.9}" fill="none" stroke="var(--muted)" stroke-dasharray="4 3"/><text x="${X(1.48)}" y="${Z(1.62)}" text-anchor="middle" fill="var(--muted)" font-size="11">LHH</text>`;
    const zone = `<rect x="${X(-0.83)}" y="${Z(1)}" width="${80 * 1.66}" height="${140}" fill="none" stroke="rgba(255,255,255,.7)" stroke-width="1.5"/>
      <path d="M${X(-0.71)} ${Z(-0.32)} L${X(0.71)} ${Z(-0.32)} L${X(0.71)} ${Z(-0.38)} L0 0 Z" fill="none"/>
      <polygon points="${X(-0.71)},${Z(-0.3)} ${X(0.71)},${Z(-0.3)} ${X(0.71)},${Z(-0.38)} ${X(0)},${Z(-0.46)} ${X(-0.71)},${Z(-0.38)}" fill="var(--sunk)" stroke="var(--muted)"/>`;
    const arrows = pts.map(({a, s}) => { const x1 = X(s.px), y1 = Z(s.zn), x0 = X(s.px - s.hb / 12 * .9), y0 = Z(s.zn - s.vb / 12 * .45 + .15), c = PCOL[a.code] || 'var(--sel)';
      return `<line x1="${x0}" y1="${y0}" x2="${x1}" y2="${y1}" stroke="${c}" stroke-width="2.2" marker-end="url(#ah${side}${a.code})" opacity=".9"/>
        <defs><marker id="ah${side}${a.code}" markerWidth="7" markerHeight="7" refX="5" refY="3.5" orient="auto"><path d="M0,0 L7,3.5 L0,7 Z" fill="${c}"/></marker></defs>
        <circle cx="${x1}" cy="${y1}" r="${5 + s.usage * 28}" fill="${c}" fill-opacity=".45" stroke="${c}"/><text x="${x1 + 9}" y="${y1 - 8}" fill="${c}" font-size="11" font-weight="700">${esc(a.code)} ${Math.round(s.usage * 100)}%</text>`; }).join('');
    if (bat && side !== bside) return '';
    return `<div><svg class="mv" width="${W}" height="${Hh}" viewBox="0 0 ${W} ${Hh}">${box}${overlay(side)}${zone}${arrows}</svg><div class="gcap">${bat ? esc(bat.name) + ' vs this arsenal' : `vs ${side === 'R' ? 'right' : 'left'}-handed hitters`} (catcher's view)</div></div>`;
  };
  return `<div class="grids">${one('R')}${one('L')}</div>
    ${bat ? (() => { const f = planeOf(bat), sl = SLOPE[bat.plane_slope]; return f ? `<p class="pf"><b>Plane fit: ${Math.round(f.la)}° at his usual spots</b> · ${f.txt} (${f.eff >= 0 ? '+' : ''}${Math.round(f.eff * 100)}% HR vs average)${sl ? ` · ${sl[0]} (${sl[1] >= 0 ? '+' : ''}${Math.round(sl[1] * 100)}% HR vs an average plane)` : ''}</p>` : ''; })() : ''}
    <p class="note2">Dot = where each pitch ends up on average vs that side (size = how often he throws it to them); arrow = its break into that spot (exaggerated for visibility). Dashed box = where that hitter stands.${bat ? ` Overlay: ${esc(bat.name)}'s damage by zone (green = he does damage there, red = weak) and his estimated swing plane: the blue band is where his contact tends to come off at home-run launch angles (15-30 deg; dashed line ~22 deg), fit from ${H(bat.batter).swing ? H(bat.batter).swing.n : 0} balls in play (no bat-tracking data, so this is an estimate from where pitches were and how he lifted them).` : ''}</p>`;
}
function oldShape(S_){
    const W = 260, sc = v => W / 2 + v * 5.5;
    const pts = S_.arsenal.map(a => `<circle cx="${sc(a.hbreak)}" cy="${W - sc(a.vbreak)}" r="${5 + a.usage * 30}" fill="var(--sel)" fill-opacity=".55"/><text x="${sc(a.hbreak) + 8}" y="${W - sc(a.vbreak) + 4}" fill="currentColor" font-size="11">${esc(a.code)}</text>`).join('');
    return `<div style="display:flex;flex-wrap:wrap;gap:16px;align-items:flex-start"><svg class="mv" width="${W}" height="${W}" viewBox="0 0 ${W} ${W}"><line x1="${W/2}" y1="0" x2="${W/2}" y2="${W}" stroke="var(--faint)"/><line x1="0" y1="${W/2}" x2="${W}" y2="${W/2}" stroke="var(--faint)"/>${pts}</svg>
      <div class="wrapx"><table class="h"><thead><tr><th class="l">Pitch</th><th>Velo</th><th>H break (in)</th><th>V break (in)</th></tr></thead><tbody>
      ${S_.arsenal.map(a => `<tr><td class="l">${esc(a.pitch)}</td><td>${a.velo}</td><td>${a.hbreak}</td><td>${a.vbreak}</td></tr>`).join('')}</tbody></table></div></div>
      <p class="note2">Movement chart: catcher's view, induced break in inches; dot size = usage.</p>`;
}
function matchup(g){
  const side = (t, spn, spid, spteam) => { const ps = P.filter(p => p.game_pk === g.id && p.team === t); if (!ps.length) return '';
    return `<section class="blk"><div class="blkh"><h2>${esc(t)} vs ${esc(spn || 'TBD')}</h2>${sortCtl()}</div>${table(sorted(ps))}
      <p class="note2">${esc(ps[0].lineup === 'posted' ? 'Posted lineup.' : 'Projected lineup (last game’s order) until the real one posts.')}</p></section>`; };
  const any = P.find(p => p.game_pk === g.id) || {};
  const awaySp = P.find(p => p.game_pk === g.id && p.team === g.home)?.sp_id, homeSp = P.find(p => p.game_pk === g.id && p.team === g.away)?.sp_id;
  return `<p class="note2">${esc(g.series || '')} · ${esc(g.venue)} · park HR factor ${any.park != null ? (any.park >= 1 ? '+' : '') + Math.round((any.park - 1) * 100) + '%' : '–'} · ${any.temp != null ? any.temp + '°F' : 'temp n/a'} · wind out ${any.wind_out ?? 'n/a'} mph</p>
    <section class="blk"><h2>Starters</h2>${spCard(awaySp, g.sp_away, g.away)}${spCard(homeSp, g.sp_home, g.home)}</section>
    ${side(g.away, g.sp_home)}${side(g.home, g.sp_away)}`;
}
function rolling(g){
  const ps = P.filter(p => g === null || p.game_pk === g.id).sort((a, b) => b.p_hr - a.p_hr);
  const W = ['7', '14', '30'];
  const cell = (v, s, f) => { if (!v || v.pa === 0) return '<td class="muted">–</td>'; const r = f(v), b = f(s); const up = b && r != null ? r - b : 0;
    return `<td>${r == null ? '–' : (typeof r === 'number' && r < 1 ? pc(r) : r)}<span class="arrow" style="color:${up > 0 ? 'var(--g2)' : up < 0 ? 'var(--r2)' : 'var(--muted)'}">${up > 0 ? '↑' : up < 0 ? '↓' : ''}</span></td>`; };
  return `<section class="blk"><h2>Rolling form</h2><div class="wrapx"><table class="h"><thead><tr><th class="l">Hitter</th>${W.map(w => `<th>${w}d PA</th><th>${w}d HR</th><th>${w}d Brl/BIP</th><th>${w}d HardHit</th>`).join('')}<th>Season Brl/BIP</th></tr></thead><tbody>
    ${ps.map(p => { const h = H(p.batter); const s = h.season || {};
      return `<tr><td class="l nm">${esc(p.name)}<small>${esc(p.team)}</small></td>${W.map(w => { const v = (h.rolling || {})[w] || {};
        return `<td class="muted">${v.pa ?? 0}</td><td>${v.hr ?? 0}</td>${cell(v, s, x => x.brl_bip)}${cell(v, s, x => x.hardhit)}`; }).join('')}<td>${pc(s.brl_bip)}</td></tr>`; }).join('')}</tbody></table></div>
    <p class="note2">Arrows compare the window with his season rate. Recent form did not improve HR predictions in testing; use it as context.</p></section>`;
}
function grid(vals, color, label, fmt){
  return `<div><div class="grid5">${vals.map((v, i) => { const r = Math.floor(i / 5), c = i % 5, inz = r >= 1 && r <= 3 && c >= 1 && c <= 3;
    return `<div class="${inz ? 'inz' : ''}" style="${color(v)}">${fmt(v)}</div>`; }).join('')}</div><div class="gcap">${label}</div></div>`;
}
function pitcherZones(g){
  const one = (sid, name) => { const S_ = SC.pitchers[String(sid)]; if (!S_ || !S_.zones.R) return '';
    const mx = Math.max(...S_.zones.R, ...S_.zones.L, .01);
    const col = v => heat(v / mx * 100);
    return `<div class="sp"><h3>${esc(name)}</h3><div class="grids">${grid(S_.zones.R, col, 'vs right-handed hitters', v => Math.round(v * 100))}${grid(S_.zones.L, col, 'vs left-handed hitters', v => Math.round(v * 100))}</div>
      <p class="note2">% of his pitches in each spot (catcher's view; outlined = strike zone, scaled to each hitter's zone).</p></div>`; };
  const sps = [...new Set(P.filter(p => p.game_pk === g.id).map(p => p.sp_id + '|' + p.sp_name))].map(s => s.split('|'));
  return `<section class="blk">${sps.map(([sid, n]) => one(sid, n)).join('')}</section>`;
}
function hitterZones(g){
  const ps = P.filter(p => p.game_pk === g.id).sort((a, b) => a.team.localeCompare(b.team) || a.slot - b.slot);
  if (!ps.length) return '';
  const cur = ps.find(p => p.batter == st.hitter) || ps[0];
  const h = H(cur.batter), z = h.zones || [];
  const sel = `<label class="ctl">Hitter <select id="hsel">${ps.map(p => `<option value="${p.batter}"${p.batter === cur.batter ? ' selected' : ''}>${esc(p.team)} · ${esc(p.name)}</option>`).join('')}</select></label>`;
  if (!z.length) return `<section class="blk">${sel}<p class="note2">No 2026 pitch data.</p></section>`;
  const sp = SC.pitchers[String(cur.sp_id)], side = cur.bats === 'S' ? (cur.sp_hand === 'R' ? 'L' : 'R') : cur.bats;
  const loc = sp && sp.zones && sp.zones[side];
  const mx = loc ? Math.max(...loc, .01) : 1;
  return `<section class="blk"><div class="blkh"><h2>${esc(cur.name)} · damage and whiffs</h2>${sel}</div><div class="grids">
    ${grid(z.map(c => c.xcon), v => v == null ? 'background:var(--sunk);color:var(--muted)' : heat((v - .2) / .4 * 100), 'xwOBA on contact', v => v == null ? '' : f3(v))}
    ${grid(z.map(c => c.whiff), v => v == null ? 'background:var(--sunk);color:var(--muted)' : heat(100 - v * 200), 'whiff % of swings', v => v == null ? '' : Math.round(v * 100))}
    ${grid(z.map(c => c.hr), v => v ? heat(50 + v * 15) : 'background:var(--sunk);color:var(--muted)', 'home runs (2026)', v => v || '')}
    ${loc ? grid(loc, v => heat(v / mx * 100), `${esc(cur.sp_name)}'s locations vs ${side}HH`, v => Math.round(v * 100)) : ''}</div>
    <p class="note2">Catcher's view; outlined = strike zone. Zone fit (${fit(h.zone_fit)}) compares where he does damage with where this starter throws.</p></section>`;
}
function csv(rows){ return rows.map(r => r.map(v => `"${String(v ?? '').replace(/"/g, '""')}"`).join(',')).join('\n'); }
function download(name, rows){ const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob([csv(rows)], {type: 'text/csv'})); a.download = name; a.click(); }
function exportsView(){
  return `<section class="blk"><h2>Exports</h2><p class="note2">Download the tables as CSV.</p><div class="tabs2">
    <button class="btnx" data-exp="slate">Slate (all hitters)</button><button class="btnx" data-exp="pitchers">Starters</button></div></section>`;
}
function doExport(k){
  if (k === 'slate'){
    const keys = ['pa', 'pit', 'bip', 'hr', 'iso', 'xwoba', 'xwobacon', 'swstr', 'pullbrl', 'brl_bip', 'sweet', 'hardhit', 'ev90'];
    download(`mlb_hr_${D.date}.csv`, [['hitter', 'team', 'bats', 'slot', 'vs', 'hr_pct', 'fair', 'exp_pa', 'matchup', 'zone_fit', 'mix_fit', 'form_pct', ...keys]].concat(
      P.map(p => { const h = H(p.batter), s = h.season || {}; return [p.name, p.team, p.bats, p.slot, p.sp_name, p.p_hr, p.fair, p.exp_pa, h.matchup, h.zone_fit, h.mix_fit, h.form?.pct, ...keys.map(k => s[k])]; })));
  } else {
    const rows = [['pitcher', 'split', 'bf', 'xwoba', 'csw', 'swstr', 'brl_bip', 'fb', 'hardhit']];
    G.forEach(g => [[g.sp_away], [g.sp_home]].forEach(([n]) => { const p = P.find(x => x.game_pk === g.id && x.sp_name === n); const S_ = p && SC.pitchers[String(p.sp_id)];
      if (S_) Object.entries(S_.splits).forEach(([k, v]) => rows.push([n, k, v.pa, v.xwoba, v.csw, v.swstr, v.brl_bip, v.fb, v.hardhit])); }));
    download(`mlb_starters_${D.date}.csv`, rows);
  }
}
function render(){
  try { localStorage.setItem('mlb-view', JSON.stringify({game: st.game, tab: st.tab, sort: st.sort})); } catch (e) {}
  if (st.game !== 'summary' && !G.find(g => g.id == st.game)) st.game = 'summary';
  const g = G.find(x => x.id == st.game) || null;
  document.getElementById('games').innerHTML = games();
  document.getElementById('gname').textContent = g ? `${g.away} @ ${g.home}` : 'slate';
  document.getElementById('dt').textContent = D.date;
  const T = g ? [['matchup', 'Matchup'], ['rolling', 'Rolling'], ['pzones', 'Pitcher zones'], ['hzones', 'Hitter zones'], ['exports', 'Exports']] : [['matchup', 'Summary'], ['rolling', 'Rolling'], ['exports', 'Exports']];
  if (!T.find(t => t[0] === st.tab)) st.tab = 'matchup';
  document.getElementById('tabs').innerHTML = T.map(([k, l]) => `<button data-tab="${k}" aria-pressed="${st.tab === k}">${l}</button>`).join('');
  const M = document.getElementById('main');
  M.innerHTML = st.tab === 'exports' ? exportsView() : st.tab === 'rolling' ? rolling(g) : !g ? summary()
    : st.tab === 'pzones' ? pitcherZones(g) : st.tab === 'hzones' ? hitterZones(g) : matchup(g);
}
document.addEventListener('click', e => {
  const b = e.target.closest('button'); if (!b) return;
  if (b.dataset.game){ st.game = b.dataset.game === 'summary' ? 'summary' : +b.dataset.game; render(); }
  else if (b.dataset.tab){ st.tab = b.dataset.tab; render(); }
  else if (b.dataset.sptab){ st.sp[b.dataset.sid] = b.dataset.sptab; render(); }
  else if (b.dataset.exp) doExport(b.dataset.exp);
});
document.addEventListener('change', e => { if (e.target.dataset && e.target.dataset.ov){ st.ov = st.ov || {}; st.ov[e.target.dataset.ov] = e.target.value; render(); return; }
  if (e.target.id === 'sort'){ st.sort = e.target.value; render(); } else if (e.target.id === 'hsel'){ st.hitter = +e.target.value; render(); } });
render();
</script>"""


def write(payload):
    os.makedirs(OUT, exist_ok=True)
    html = TEMPLATE.replace("__FONTS__", report.FONTS).replace("__CSS__", report.BASE_CSS).replace("__DATE__", payload["date"]) \
        .replace("__DATA__", json.dumps(payload, separators=(",", ":"), default=str).replace("</", "<\\/"))
    for f in (f"{payload['date']}.html", "index.html"):
        open(os.path.join(OUT, f), "w", encoding="utf-8").write(html)
    print("Wrote", os.path.join(OUT, "index.html"))
