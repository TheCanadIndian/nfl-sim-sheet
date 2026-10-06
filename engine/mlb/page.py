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
.btnx{padding:8px 14px;border-radius:8px;border:1px solid var(--faint);background:var(--surface);color:var(--ink);font-weight:600;cursor:pointer}
</style>
<div class="wrap">
  <div class="bar1"><span class="ttl">Home run matchups · <span id="gname"></span></span><span class="muted" id="dt"></span></div>
  <div class="games" id="games"></div>
  <div class="tabs2" id="tabs"></div>
  <main id="main" class="blk"></main>
  <p class="note2">HR% is the model's chance of 1+ home run (tested: beats season HR rates, well calibrated). Matchup = percentile of his per-PA HR chance vs this starter among all 2026 PAs. Ceiling = 90th-percentile exit velocity. Zone Fit, Mix Fit and HR Form are context: in testing they did not improve the HR prediction. Cell colors = league percentile (green good for the hitter).</p>
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
const COLS = [
  ['hr', 'HR%', p => pc(p.p_hr), p => heat(ranks[p.batter + '|' + p.game_pk])],
  ['fair', 'Fair', p => odds(p.fair), () => ''],
  ['kalshi', 'Kalshi', p => window.MKT && MKT.has() ? MKT.cell(p.game_pk, p.batter, p.name, 'hr') : '–', () => ''],
  ['matchup', 'Matchup', p => H(p.batter).matchup ?? '–', p => heat(H(p.batter).matchup)],
  ['ceiling', 'Ceiling', p => H(p.batter).season?.ev90 ?? '–', p => heat(H(p.batter).pct?.ev90)],
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
const SORTS = {default: 'Lineup order', hr: 'HR%', matchup: 'Matchup', ceiling: 'Ceiling', xwoba: 'xwOBA', brl: 'Brl/BIP%', pullbrl: 'PullBrl%', fb: 'FB%', form: 'HR form'};
const sortVal = {hr: p => p.p_hr, matchup: p => H(p.batter).matchup ?? -1, ceiling: p => H(p.batter).season?.ev90 ?? 0, xwoba: p => H(p.batter).season?.xwoba ?? 0,
  brl: p => H(p.batter).season?.brl_bip ?? 0, pullbrl: p => H(p.batter).season?.pullbrl ?? 0, fb: p => H(p.batter).season?.fb ?? 0, form: p => H(p.batter).form?.pct ?? -1};
function num(v){ return v == null ? '–' : v.toLocaleString(); }
function fit(v){ return v == null ? '–' : (v >= 0 ? '+' : '') + (v * 100).toFixed(1) + '%'; }
function fitp(v){ return v == null ? null : 50 + Math.max(-50, Math.min(50, v * 500)); }
function form(f){ return f ? `${f.pct}%<span class="arrow">${f.trend === 'up' ? '↑' : f.trend === 'down' ? '↓' : '→'}</span>` : '–'; }
function table(ps){
  return `<div class="wrapx"><table class="h"><thead><tr><th class="l">Hitter</th>${COLS.map(c => `<th>${c[1]}</th>`).join('')}</tr></thead><tbody>
    ${ps.map(p => `<tr><td class="l nm">${esc(p.name)}<small>${esc(p.bats)}HB</small></td>${COLS.map(c => `<td style="${c[3](p)}">${c[2](p)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
}
function sorted(ps){ return st.sort === 'default' ? ps.slice().sort((a, b) => a.slot - b.slot) : ps.slice().sort((a, b) => sortVal[st.sort](b) - sortVal[st.sort](a)); }
const sortCtl = () => `<label class="ctl">Sort by <select id="sort">${Object.entries(SORTS).map(([k, v]) => `<option value="${k}"${st.sort === k ? ' selected' : ''}>${v}</option>`).join('')}</select></label>`;

function summary(){
  const ps = P.slice().sort((a, b) => b.p_hr - a.p_hr).slice(0, 30);
  return `<section class="blk"><div class="blkh"><h2>Slate summary · most likely to homer</h2></div>
    <div class="wrapx"><table class="h"><thead><tr><th class="l">#</th><th class="l">Hitter</th><th class="l">Game</th><th class="l">vs</th>${COLS.slice(0, 8).map(c => `<th>${c[1]}</th>`).join('')}</tr></thead><tbody>
    ${ps.map((p, i) => `<tr><td class="l muted">${i + 1}</td><td class="l nm">${esc(p.name)}<small>${esc(p.bats)}HB</small></td><td class="l muted">${esc(p.team)}</td><td class="l muted">${esc(p.sp_name || 'TBD')}</td>${COLS.slice(0, 8).map(c => `<td style="${c[3](p)}">${c[2](p)}</td>`).join('')}</tr>`).join('')}</tbody></table></div></section>`;
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
    const W = 260, sc = v => W / 2 + v * 5.5;
    const pts = S_.arsenal.map(a => `<circle cx="${sc(a.hbreak)}" cy="${W - sc(a.vbreak)}" r="${5 + a.usage * 30}" fill="var(--sel)" fill-opacity=".55"/><text x="${sc(a.hbreak) + 8}" y="${W - sc(a.vbreak) + 4}" fill="currentColor" font-size="11">${esc(a.code)}</text>`).join('');
    body = `<div style="display:flex;flex-wrap:wrap;gap:16px;align-items:flex-start"><svg class="mv" width="${W}" height="${W}" viewBox="0 0 ${W} ${W}"><line x1="${W/2}" y1="0" x2="${W/2}" y2="${W}" stroke="var(--faint)"/><line x1="0" y1="${W/2}" x2="${W}" y2="${W/2}" stroke="var(--faint)"/>${pts}</svg>
      <div class="wrapx"><table class="h"><thead><tr><th class="l">Pitch</th><th>Velo</th><th>H break (in)</th><th>V break (in)</th></tr></thead><tbody>
      ${S_.arsenal.map(a => `<tr><td class="l">${esc(a.pitch)}</td><td>${a.velo}</td><td>${a.hbreak}</td><td>${a.vbreak}</td></tr>`).join('')}</tbody></table></div></div>
      <p class="note2">Movement chart: catcher's view, induced break in inches; dot size = usage.</p>`;
  }
  return `<div class="sp"><div class="blkh"><div><span class="who">${esc(team)} starter</span><h3>${esc(name)}</h3></div><div class="tabs2">${tb}</div></div>${body}</div>`;
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
document.addEventListener('change', e => { if (e.target.id === 'sort'){ st.sort = e.target.value; render(); } else if (e.target.id === 'hsel'){ st.hitter = +e.target.value; render(); } });
render();
</script>"""


def write(payload):
    os.makedirs(OUT, exist_ok=True)
    html = TEMPLATE.replace("__FONTS__", report.FONTS).replace("__CSS__", report.BASE_CSS).replace("__DATE__", payload["date"]) \
        .replace("__DATA__", json.dumps(payload, separators=(",", ":"), default=str).replace("</", "<\\/"))
    for f in (f"{payload['date']}.html", "index.html"):
        open(os.path.join(OUT, f), "w", encoding="utf-8").write(html)
    print("Wrote", os.path.join(OUT, "index.html"))
