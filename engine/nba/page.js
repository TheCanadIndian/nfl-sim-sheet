const D = JSON.parse(document.getElementById('data').textContent);
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const f0 = v => v == null ? '–' : Math.round(v).toString();
const f1 = v => v == null ? '–' : (Math.round(v*10)/10).toFixed(1);
const pct = p => p == null ? '–' : Math.round(p*100) + '%';
const american = p => { p = Math.min(Math.max(p,1e-4),1-1e-4); const o = p>=.5 ? -100*p/(1-p) : 100*(1-p)/p; return (o>0?'+':'') + Math.round(o); };
const [MEAN, P10, P25, MED, P75, P90] = [0,1,2,3,4,5];
const LAB = {min:'Minutes', pts:'Points', reb:'Rebounds', ast:'Assists', fg3m:'Threes', pra:'Pts+Reb+Ast', stl:'Steals', blk:'Blocks', tov:'Turnovers'};
const COLS = ['min','pts','reb','ast','fg3m','pra'];
const state = {view: ['#board','#dvp','#parlays'].includes(location.hash) ? location.hash.slice(1) : 'games', game: D.games[0]?.id, gm: {}, bstat: 'pts', dstat: 'pts', open: null, ostat: 'pts', oline: null, okey: null};
document.getElementById('sub').textContent = `${D.games.length} games · ${D.date} · built ${D.generated}`;
const shadeP = p => p >= .65 ? 'g3' : p >= .56 ? 'g2' : p >= .51 ? 'g1' : p <= .35 ? 'r3' : p <= .44 ? 'r2' : p <= .49 ? 'r1' : '';
const shadeR = x => x >= 1.12 ? 'g3' : x >= 1.07 ? 'g2' : x >= 1.03 ? 'g1' : x <= 1/1.12 ? 'r3' : x <= 1/1.07 ? 'r2' : x <= 1/1.03 ? 'r1' : '';
const mode = g => state.gm[g] || 'vegas';
// matchup gradient: a continuous tint from red (this defense allows less to the position) through clear to green
// (allows more), full strength at ±15% per minute vs the league
const MGT = v => v == null ? 0 : Math.max(-1, Math.min(1, Math.log(v) / Math.log(1.15)));
const MG = v => { const t = MGT(v), a = Math.round(Math.abs(t) * 46); return a < 4 ? '' : `background:color-mix(in srgb,var(${t > 0 ? '--good' : '--bad'}) ${a}%,transparent)`; };
const mgKey = (lo, hi) => `<span class="mgkey"><span>${lo}</span><i></i><span>${hi}</span></span>`;
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
// why a flagged price might hit: the NBA path reasons for the stats of the player's Value / LC / off-market prices
function whyNBA(p, gid){
  if (!(window.MKT && MKT.tagStats)) return '';
  const sts = MKT.tagStats(gid, p.n).filter(s => ['pts', 'reb', 'ast', 'fg3m'].includes(s)); if (!sts.length) return '';
  const g = D.games.find(x => String(x.id) === String(gid)); if (!g) return '';
  const R = new Set();
  for (const st of sts){ const F = nbaFactors(g, p, st); for (const [k, v] of Object.entries(F)) if (v) R.add(k === 'matchup' ? `matchup (${v})` : k === 'rotation' ? v : k === 'game' ? v : k); }
  return R.size ? `<span class="why" title="Path reasons behind this flagged price (the Path longshot rules)">why: <b>${esc([...R].join(' · '))}</b></span>`
    : `<span class="why none" title="None of the path reasons (usage, matchup, rotation, game) applies: the price has no clear reason behind it">no clear reason</span>`;
}
function chips(p, g){
  let h = '';
  if (p.st && p.st !== 'Active') h += `<span class="tag q" title="${esc(p.st + (p.note ? ': ' + p.note : ''))}">${p.st === 'Day-To-Day' ? 'DTD' : p.st === 'Questionable' ? 'Q' : esc(p.st)}</span>`;
  if (p.lu){ const [mv, src] = p.lu.split('|'), off = src === 'official';
    h += mv === 'in' ? `<span class="lean g2" title="${off ? 'In tonight’s official starting five' : 'Projected to start tonight (our lineup from ESPN’s depth chart, minus anyone out)'}; came off the bench last game. Starting raises his minutes about 20% (2025-26 test), ${off ? 'fully applied' : 'applied at 70% until the official lineup posts'}.">▲ starts</span>`
      : `<span class="lean r2" title="${off ? 'Not in tonight’s official starting five' : 'Projected to come off the bench tonight'}; started last game. Coming off the bench cuts his minutes about 13% (2025-26 test), ${off ? 'fully applied' : 'applied at 70% until the official lineup posts'}.">▼ bench</span>`; }
  if (p.trend >= 3) h += `<span class="lean g2" title="Minutes trending up: last few games ${f1(p.trend)} above his longer average">▲ min</span>`;
  else if (p.trend <= -3) h += `<span class="lean r2" title="Minutes trending down: last few games ${f1(-p.trend)} below his longer average">▼ min</span>`;
  const l = lean(p, 'pts');
  if (l && Math.abs(l.edge) >= .03) h += `<span class="lean ${shadeP(.5 + l.edge * 2)}" title="Market-blind model ${l.edge > 0 ? 'higher' : 'lower'} on points at ${l.L}">${l.edge > 0 ? '▲' : '▼'} blind</span>`;
  if (p.gp === 0) h += '<span class="tag" style="color:var(--muted)" title="No NBA games yet: league-average role assumed">new</span>';
  if (window.MKT && MKT.lcFlag) h += MKT.lcFlag(g, p.n) + (MKT.offFlag ? MKT.offFlag(g, p.n) : '') + whyNBA(p, g);
  if (p.ar) h += `<span class="tag arch" title="Playstyle archetype from his per-minute production (points, threes, free-throw rate, assists, rebounds, blocks). Points and threes matchups use what this defense allows to this archetype as well as to his position.">${esc(p.ar)}</span>`;
  return h;
}
function cell(p, st, g){
  const s = p.s[mode(g)] && p.s[mode(g)][st]; if (!s) return '<td>–</td>';
  const dp = st !== 'min' && DVP[p.o + '|' + p.pg] && DVP[p.o + '|' + p.pg][st];
  const da = p.va && p.va[st], dv = dp && da ? Math.sqrt(dp * da) : dp;      // points / threes: position and archetype, as the model blends them
  const pct_ = v => `${Math.abs(Math.round((v - 1) * 100))}% ${v >= 1 ? 'more' : 'less'}`;
  const tip = dv ? (da ? `${esc(p.o)} matchup: ${pct_(dv)} ${LAB[st].toLowerCase()} per minute (to ${p.pg}s ${pct_(dp)}; to ${esc(p.ar || 'his archetype')}s ${pct_(da)})`
    : `${esc(p.o)} allows ${pct_(dv)} ${LAB[st].toLowerCase()} per minute to ${p.pg}s`) : '';
  return `<td class="cell"${dv ? ` style="${MG(dv)}" title="${tip}"` : ''}><b>${st === 'min' ? f0(s[MED]) : f1(s[MED])}</b><small>${f0(s[P10])}–${f0(s[P90])}</small></td>`;
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
    <div id="sl-res">${readout(d, L, p, st)}</div>
    ${window.MKT ? MKT.player(g, p.id, p.n, [st], m, true) : ''}</div>`;
}
const SLIP_LAB = {pts: 'points', reb: 'rebounds', ast: 'assists', fg3m: 'threes', stl: 'steals', blk: 'blocks', tov: 'turnovers', pra: 'pts+reb+ast'};
function slipBtns(p, st, L, r){
  if (!window.SLIP || !p) return '';
  const g = D.games.find(x => x.id === p.g), m = mode(p.g);
  const leg = (side, prob) => ({id: `nba|${p.g}|${p.k}|${st}|${side}|${L}|${m}`, sport: 'nba', g: p.g, glab: g ? `${g.away} @ ${g.home}` : '', lab: `${p.n} ${side} ${L} ${SLIP_LAB[st] || st}`,
    p: prob, side, stat: st, line: L, pk: p.k, model: m, draws: `nba/draws/${p.g}.json`});
  return `<div class="small muted" style="display:flex;gap:6px;align-items:center;flex-wrap:wrap;margin-top:6px">My parlay: ${SLIP.btn(leg('over', r.over), 'Over ' + L)}${SLIP.btn(leg('under', r.under), 'Under ' + L)}</div>`;
}
function readout(d, L, p, st){
  const r = probs(d, L), np = Math.max(r.over + r.under, 1e-9), ov = r.over/np, un = r.under/np, so = shadeP(ov), su = shadeP(un);
  return `<div class="sl-out"><div class="sl-side over"><span>Over ${L}</span><b class="${so}">${pct(r.over)}</b><em>${american(ov)}</em></div>
    <div class="sl-meter"><i class="f-${so||'n'}" style="width:${r.over*100}%"></i><i class="f-${su||'n'}" style="width:${r.under*100}%"></i></div>
    <div class="sl-side under"><span>Under ${L}</span><b class="${su}">${pct(r.under)}</b><em>${american(un)}</em></div></div>${slipBtns(p, st, L, r)}`;
}
// The matchup the model uses for a player-stat: defense vs position, blended 50/50 (log terms) with defense vs
// archetype for points, threes and steals. {v, dp, da}; v = per-minute multiplier vs league (1.12 = +12%).
const PG_PL = {G: 'guards', F: 'forwards', C: 'centers'};
const ARCH_PL = {'Interior big': 'interior bigs', '3-and-D role player': '3-and-D players', 'Spot-up shooter': 'spot-up shooters', 'Rim protector': 'rim protectors',
                 'Primary creator': 'primary creators', 'Versatile forward': 'versatile forwards', 'Pass-first guard': 'pass-first guards', 'Scoring guard/wing': 'scoring guards/wings'};
const SL = {pts: 'Pts', reb: 'Reb', ast: 'Ast', fg3m: '3PM'};
// only where he has real volume (a % on a tiny number is noise): projected median points 8+, rebounds 3+, assists 2+, threes 1+
const MX_MIN = {pts: 8, reb: 3, ast: 2, fg3m: 1};
const vol = (p, st) => { const s = p.s.vegas && p.s.vegas[st]; return !!(s && s[MED] >= MX_MIN[st]); };
function mx(p, st){
  const dp = (DVP[p.o + '|' + p.pg] || {})[st]; if (dp == null) return null;
  const da = p.va && p.va[st];
  return {v: da ? Math.sqrt(dp * da) : dp, dp, da};
}
const sgn = v => (v >= 1 ? '+' : '−') + Math.abs(Math.round((v - 1) * 100)) + '%';
function mxWhy(p, st, m){
  return m.da ? `${esc(p.o)} matchup for ${KLAB[st]}: ${sgn(m.v)} per minute (vs ${PG_PL[p.pg]} ${sgn(m.dp)}, vs ${ARCH_PL[p.ar] || 'his archetype'} ${sgn(m.da)}; the model blends the two)`
              : `${esc(p.o)} allows ${sgn(m.v)} ${KLAB[st]} per minute to ${PG_PL[p.pg]}`;
}
// one or two big matchups (8%+ either way) under a player's name
// 3-point attempts, rebound share and assist share (owner 2026-10-23), from his games this season (last season's with
// fewer than 3): REB% = his share of the rebounds grabbed while he's on the floor (both teams, team rebounds left out);
// AST% = his assists over his teammates' made baskets while he's on the floor. Counted from the play-by-play lineups;
// on 2025-26 they match Basketball-Reference's TRB% / AST% (275 players: median gap 0.1 / 0.25 pts). NBA's tracking
// 'chances' (rebounds near him, passes into shots) need player tracking we don't have.
function usageLine(p){
  const x = p.x; if (!x) return '';
  const p1 = v => (v * 100).toFixed(1), yr = x.season ? ` (${x.season - 1}-${String(x.season).slice(2)}, ${x.gp} games)` : '';
  const L = [];
  if (x.fg3a >= 1) L.push(`<span class="usl" title="3-point attempts a game${esc(yr)}${x.fg3a10 != null ? `; last 10 games ${f1(x.fg3a10)}` : ''}">3PA <b>${f1(x.fg3a)}</b>${x.fg3a10 != null && Math.abs(x.fg3a10 - x.fg3a) >= 1 ? ` <i>L10 ${f1(x.fg3a10)}</i>` : ''}</span>`);
  if (x.reb >= 2 && x.rch) L.push(`<span class="usl" title="REB% = his share of the rebounds grabbed while he's on the floor: ${f1(x.reb)} of ${f1(x.rch)} a game${esc(yr)}. Same as Basketball-Reference's TRB%.">Reb <b>${f1(x.reb)}</b> <i>REB% ${p1(x.rr)}</i></span>`);
  if (x.ast >= 1.5 && x.ach) L.push(`<span class="usl" title="AST% = his assists over his teammates' made baskets while he's on the floor: ${f1(x.ast)} of ${f1(x.ach)} a game${esc(yr)}. Same as Basketball-Reference's AST%.">Ast <b>${f1(x.ast)}</b> <i>AST% ${p1(x.ar)}</i></span>`);
  return L.length ? `<div class="usw">${L.join('')}</div>` : '';
}
function mxLine(p){
  const L = ['pts', 'reb', 'ast', 'fg3m'].filter(st => vol(p, st)).map(st => ({st, m: mx(p, st)})).filter(x => x.m && Math.abs(x.m.v - 1) >= .08)
    .sort((a, b) => Math.abs(b.m.v - 1) - Math.abs(a.m.v - 1)).slice(0, 2);
  if (!L.length) return '';
  return `<div class="mxw">${L.map(x => `<span class="mxl ${x.m.v > 1 ? 'up' : 'dn'}" title="${mxWhy(p, x.st, x.m)}">${SL[x.st]} ${sgn(x.m.v)} <i>vs ${esc(x.m.da && x.m.da !== x.m.dp ? (ARCH_PL[p.ar] || PG_PL[p.pg]) : PG_PL[p.pg])}</i></span>`).join('')}</div>`;
}
// Where this game is soft / tough: players in the rotation (20+ projected minutes) grouped by team, archetype and
// stat; the biggest blended matchups, 8%+ either way
function softStrip(g){
  const G = {};
  for (const p of D.players.filter(x => x.g === g.id && x.st !== 'Out')){
    const mins = p.s.vegas && p.s.vegas.min && p.s.vegas.min[MED]; if (!(mins >= 20)) continue;
    for (const st of ['pts', 'reb', 'ast', 'fg3m']){
      const m = vol(p, st) ? mx(p, st) : null; if (!m) continue;
      const lab = m.da ? (ARCH_PL[p.ar] || PG_PL[p.pg]) : PG_PL[p.pg], k = `${p.t}|${lab}|${st}`;
      (G[k] = G[k] || {t: p.t, o: p.o, lab, st, v: [], ps: []}).v.push(m.v); G[k].ps.push(p.n.split(' ').slice(-1)[0]);
    }
  }
  const all = Object.values(G).map(x => ({...x, m: x.v.reduce((s, v) => s + v, 0) / x.v.length}));
  const soft = all.filter(x => x.m >= 1.08).sort((a, b) => b.m - a.m).slice(0, 3), tough = all.filter(x => x.m <= .92).sort((a, b) => a.m - b.m).slice(0, 2);
  if (!soft.length && !tough.length) return '';
  const chip = (x, up) => `<span class="mxs ${up ? 'up' : 'dn'}" title="${esc(x.o)} defense, per minute vs league: ${esc(x.ps.join(', '))}"><b>${esc(x.t)} ${esc(x.lab)}</b> ${sgn(x.m)} ${KLAB[x.st]} <i>${esc(x.ps.slice(0, 3).join(', '))}</i></span>`;
  return `<div class="mxstrip">${soft.length ? `<span class="eyebrow">Where this game is soft</span>${soft.map(x => chip(x, true)).join('')}` : ''}${tough.length ? `<span class="eyebrow">Tough</span>${tough.map(x => chip(x, false)).join('')}` : ''}</div>`;
}
function teamTable(g, team){
  const ps = D.players.filter(p => p.g === g.id && p.t === team).sort((a,b) => (b.s[mode(g.id)]?.min?.[MED] ?? 0) - (a.s[mode(g.id)]?.min?.[MED] ?? 0));
  const rows = ps.map(p => `<tr class="${state.open === p.k ? 'opened' : ''}"><td class="l name"><button class="plink${state.open === p.k ? ' open' : ''}" data-open="${esc(p.k)}"><span class="caret">${state.open === p.k ? '▾' : '▸'}</span>${esc(p.n)}</button>${chips(p, g.id)}${mxLine(p)}${usageLine(p)}</td><td class="l"><span class="pos">${esc(p.pos)}${p.start ? '' : ''}</span></td>${COLS.map(c => cell(p, c, g.id)).join('')}</tr>`
    + (state.open === p.k ? `<tr class="xrow"><td colspan="${COLS.length + 2}">${slider(p, g.id)}</td></tr>` : '')).join('');
  const outs = (g.out && g.out[team]) || [];
  const outLine = outs.length ? `<p class="muted small" style="margin:6px 0 0"><b style="color:var(--r2)">Out:</b> ${outs.map(o => `${esc(o.n)}${o.note ? ` <span title="${esc(o.note)}">(${esc(o.st)})</span>` : ''}`).join(', ')}. Their minutes are shared among the players below.</p>` : '';
  return outLine + `<div class="tw"><table class="t"><thead><tr><th class="l">${esc(team)}</th><th class="l">Pos</th>${COLS.map(c => `<th>${c === 'fg3m' ? '3PM' : c === 'pra' ? 'PRA' : c.toUpperCase()}</th>`).join('')}</tr></thead><tbody>${rows}</tbody></table></div>`;
}
// game legs for the parlay slip: normal around the projected margin (SD 12.5, nba/project.py MARGIN_SD) and total
// (SD 18.5, approximate). They multiply with player legs (no joint game simulation saved for games).
const MSD = 12.5, TSD = 18.5;
function ncdf(z){ const t = 1 / (1 + .2316419 * Math.abs(z)), d = .3989423 * Math.exp(-z * z / 2), q = d * t * (.3193815 + t * (-.3565638 + t * (1.781478 + t * (-1.821256 + t * 1.330274)))); return z > 0 ? 1 - q : q; }
// one tappable chip per bet option (adds it to the parlay slip; ✓ once added)
function betChip(L, lab, p, bk){                         // bk: best sportsbook price line (mkt.js MKT.bk), if any
  const on = window.SLIP && SLIP.has(L.id);
  const odd = p == null ? '' : american(p);
  return `<button type="button" class="bet${on ? ' on' : ''}"${window.SLIP ? ` data-slip="${esc(JSON.stringify(L))}" title="${on ? 'Remove from' : 'Add to'} my parlay"` : ' disabled'}><span>${esc(lab)}</span><b>${pct(p)}</b><small>${odd}</small><i>${on ? '✓' : '+'}</i>${bk || ''}</button>`;
}
const BK = (...a) => window.MKT && MKT.bk ? MKT.bk(...a) : '';
const betRow = (lab, chips) => `<div class="betrow"><span class="eyebrow">${lab}</span><div class="bets">${chips.join('')}</div></div>`;
function nbaGameBets(g){
  const M = g.models[mode(g.id)], m = M.home_pts - M.away_pts, tot = M.home_pts + M.away_pts, gl = `${g.away} @ ${g.home}`;
  const leg = (key, lab, p) => ({id: `nba|${g.id}|${key}|${mode(g.id)}`, sport: 'nba', g: g.id, glab: gl, lab: `${lab} (${gl})`, p, side: 'over', stat: key});
  const rows = [betRow('Moneyline', [betChip(leg('ml_away', `${g.away} ML`, 1 - M.home_win), g.away, 1 - M.home_win, BK(g.id, 'ml', 'away', null, 1 - M.home_win)), betChip(leg('ml_home', `${g.home} ML`, M.home_win), g.home, M.home_win, BK(g.id, 'ml', 'home', null, M.home_win))])];
  if (g.spread != null){ const L = -g.spread, ph = 1 - ncdf((L - m) / MSD);
    const a = `${g.away} ${g.spread > 0 ? '−' : '+'}${Math.abs(g.spread)}`, h = `${g.home} ${g.spread > 0 ? '+' : '−'}${Math.abs(g.spread)}`;
    rows.push(betRow('Spread', [betChip(leg('sp_away', a, 1 - ph), a, 1 - ph, BK(g.id, 'spread', 'away', g.spread, 1 - ph)), betChip(leg('sp_home', h, ph), h, ph, BK(g.id, 'spread', 'home', g.spread, ph))])); }
  if (g.total != null){ const po = 1 - ncdf((g.total - tot) / TSD);
    rows.push(betRow(`Total ${g.total}`, [betChip(leg('over', `Over ${g.total}`, po), 'Over', po, BK(g.id, 'total', 'over', g.total, po)), betChip(leg('under', `Under ${g.total}`, 1 - po), 'Under', 1 - po, BK(g.id, 'total', 'under', g.total, 1 - po))])); }
  return `<div class="betgrid" style="margin:8px 0">${rows.join('')}${window.MKT && MKT.bkNote ? MKT.bkNote(g.id) : ''}</div>`;
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
    ${nbaGameBets(g)}
    ${window.MKT ? MKT.game(g.id, mode(g.id)) : ''}</section>`;
  const tables = `<section class="panel"><div class="phead"><h2>Players</h2><span class="muted small" style="display:flex;flex-wrap:wrap;gap:4px 10px;align-items:center">Median, with 8-in-10 range below. Tap a name to slide a line. ${mgKey('tough matchup', 'soft matchup')}</span></div>${softStrip(g)}${teamTable(g, g.away)}${teamTable(g, g.home)}</section>`;
  return `<div class="games"><nav class="rail" aria-label="Games">${rail}</nav><div class="detail">${head}${tables}</div></div>`;
}
function board(){
  const st = state.bstat, rows = D.players.filter(p => p.s.vegas && p.s.vegas[st]).sort((a,b) => b.s[mode(a.g)][st][MED] - a.s[mode(b.g)][st][MED] || 0);
  rows.sort((a,b) => b.s[mode(b.g)][st][MED] - a.s[mode(a.g)][st][MED]);
  return `<section class="panel"><div class="phead"><div class="seg">${COLS.map(c => `<button data-bstat="${c}" aria-pressed="${c === st}">${LAB[c]}</button>`).join('')}</div><span class="muted small">Uses each game's selected model</span></div>
  <div class="tw"><table class="t"><thead><tr><th>#</th><th class="l">Player</th><th class="l">Game</th>${COLS.map(c => `<th>${c === 'fg3m' ? '3PM' : c === 'pra' ? 'PRA' : c.toUpperCase()}</th>`).join('')}</tr></thead><tbody>
  ${rows.slice(0, 150).map((p,i) => `<tr><td class="muted">${i+1}</td><td class="l name">${esc(p.n)}${chips(p, p.g)}</td><td class="l">${esc(p.t)} <span class="small muted">vs ${esc(p.o)}</span></td>${COLS.map(c => cell(p, c, p.g)).join('')}</tr>`).join('')}</tbody></table></div></section>`;
}
// By archetype: what each defense allows per minute to each playstyle (points, threes: the stats where it beat position)
const ARCH_SHORT = {'Interior big': 'Interior big', '3-and-D role player': '3-and-D', 'Spot-up shooter': 'Spot-up', 'Rim protector': 'Rim protector',
                    'Primary creator': 'Creator', 'Versatile forward': 'Forward', 'Pass-first guard': 'Pass-first', 'Scoring guard/wing': 'Scorer'};
const ARCH_ORDER = ['Primary creator', 'Scoring guard/wing', 'Pass-first guard', 'Spot-up shooter', '3-and-D role player', 'Versatile forward', 'Interior big', 'Rim protector'];
function dvaView(){
  const st = state.dstat, names = D.arch || [], used = (D.dva_used || ['pts', 'fg3m']).includes(st);
  const idx = ARCH_ORDER.map(n => names.indexOf(n)).filter(i => i >= 0);
  const V = {}; (D.dva || []).forEach(r => { V[r.team + '|' + r.a] = r; });
  const teams = [...new Set((D.dva || []).map(r => r.team))].sort();
  const today = new Set(D.games.flatMap(g => [g.home, g.away]));
  const rows = teams.map(t => ({t, v: idx.map(a => (V[t + '|' + a] || {})[st])}));
  const col = state.dsort != null && idx.includes(+state.dsort) ? idx.indexOf(+state.dsort) : null;
  rows.sort((a, b) => col != null ? (b.v[col] ?? 0) - (a.v[col] ?? 0) : b.v.reduce((s, x) => s + (x ?? 1), 0) - a.v.reduce((s, x) => s + (x ?? 1), 0));
  const rel = v => v == null ? '–' : (v >= 1 ? '+' : '−') + Math.abs(Math.round((v-1)*100)) + '%';
  const ex = a => { const ps = [...new Set(D.players.filter(p => p.ar === names[a]).map(p => p.n))].slice(0, 3); return ps.length ? ` e.g. ${ps.join(', ')}` : ''; };
  return `<div class="tw"><table class="t dva"><thead><tr><th class="l">Defense</th>${idx.map(a => `<th><button type="button" class="dvs${col != null && idx[col] === a ? ' on' : ''}" data-dsort="${a}" title="${esc(names[a])}:${esc(ex(a))}. Tap to sort.">${esc(ARCH_SHORT[names[a]] || names[a])}</button></th>`).join('')}</tr></thead><tbody>
  ${rows.map(r => `<tr><td class="l">${today.has(r.t) ? `<b>${esc(r.t)}</b>` : esc(r.t)}</td>${r.v.map(v => `<td class="hm" style="${MG(v)}">${rel(v)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>
  <p class="note">${used ? `<b>Used by the model</b> for ${LAB[st].toLowerCase()}: blended with the position number, it projected games better than position alone in every half of the last two seasons.` : `<b>Context only</b> for ${LAB[st].toLowerCase()}: the archetype number didn’t project ${LAB[st].toLowerCase()} better than position, so the model uses the position number (By position).`} What each defense allows per minute to each playstyle, against that playstyle’s league average. Archetypes come from a player’s own production (points, threes, free-throw rate, assists, rebounds, blocks), so two guards can land in different columns. Tap a column to sort.</p>`;
}
function dvpView(){
  const st = state.dstat, teams = [...new Set((D.dvp || []).map(r => r.team))].sort();
  const today = new Set(D.games.flatMap(g => [g.home, g.away]));
  const rows = teams.map(t => ({t, v: ['G','F','C'].map(pg => (DVP[t + '|' + pg] || {})[st])})).sort((a,b) => (b.v[0]+b.v[1]+b.v[2]) - (a.v[0]+a.v[1]+a.v[2]));
  const rel = v => v == null ? '–' : (v >= 1 ? '+' : '−') + Math.abs(Math.round((v-1)*100)) + '%';
  const byA = state.dgrp === 'arch' && (D.dva || []).length;
  const grp = (D.dva || []).length ? `<div class="seg" role="group" aria-label="Group by">${[['pos', 'By position'], ['arch', 'By archetype']].map(([k, l]) => `<button data-dgrp="${k}" aria-pressed="${(byA ? 'arch' : 'pos') === k}">${l}</button>`).join('')}</div>` : '';
  const stats = ['pts', 'reb', 'ast', 'fg3m', 'stl', 'blk'].filter(c => byA ? (D.dva || []).some(r => r[c] != null) : (D.dvp || []).some(r => r[c] != null));
  const head = `<section class="panel"><div class="phead" style="flex-wrap:wrap;gap:8px 12px">${grp}<div class="seg">${stats.map(c => `<button data-dstat="${c}" aria-pressed="${c === st}">${LAB[c]}${byA && (D.dva_used || []).includes(c) ? ' <small title="Used by the model">●</small>' : ''}</button>`).join('')}</div><span class="muted small" style="display:flex;flex-wrap:wrap;gap:4px 10px;align-items:center">Per minute allowed vs league average · bold = plays on this slate ${mgKey('allows less', 'allows more')}</span></div>`;
  if (byA) return head + dvaView() + '</section>';
  return head + `
  <div class="tw"><table class="t"><thead><tr><th class="l">Defense</th><th>Guards</th><th>Forwards</th><th>Centers</th></tr></thead><tbody>
  ${rows.map(r => `<tr><td class="l">${today.has(r.t) ? `<b>${esc(r.t)}</b>` : esc(r.t)}</td>${r.v.map(v => `<td class="hm" style="${MG(v)}">${rel(v)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>
  <p class="note">The deeper the green, the more that defense gives up to the position (good for the player); the deeper the red, the less (full color at ±15%). Recent games count most; last season carries over early on; small samples are pulled toward average.</p></section>`;
}
/* ---------------- Parlays tab: ladder candidates + parlays by payout ---------------- */
const KLAB = {pts: 'points', reb: 'rebounds', ast: 'assists', fg3m: 'threes'};
// Ladder edges (experimental): higher Kalshi rungs (2+ above the rung priced nearest 50/50) where our chance beats
// the price, Kalshi's fee included, by 5%+, for players projected 20+ minutes. Why not "soft matchup" alone: in the
// 2024-26 backtest (226,064 player-stat games) players facing soft defenses beat our top-10% projection no more often
// than others (8.3-9.3% vs 8.7-9.1%), so the model already prices the matchup; the edge vs the price is what's left.
// The soft-matchup chip stays as context. Both versions are tracked on the Profit page.
const LEDGE = .05, kcost = y => y + .07 * y * (1 - y);
function ladderCard(){
  const now = Date.now(), cards = []; let anyLadder = 0;
  for (const g of D.games){
    if (g.start_utc && new Date(g.start_utc) <= now) continue;
    for (const p of D.players.filter(x => x.g === g.id)){
      const mins = p.s.vegas && p.s.vegas.min && p.s.vegas.min[MED];
      if (!(mins >= 20) || p.st === 'Out') continue;
      const all = window.MKT && MKT.ladder ? MKT.ladder(g.id, p.id, p.n, Object.keys(KLAB)) : [];
      if (all.length) anyLadder++;
      // each rung at Kalshi (fee included) and at Hard Rock / Novig for the same number (owner 2026-10-23); the edge
      // shown and ranked is the best of the three
      const dec = o => o > 0 ? 1 + o / 100 : 1 + 100 / -o;
      const rungs = all.map(u => { if (u.p == null) return {...u, edge: null};
        const opts = [{b: 'Kalshi', price: u.price, e: u.p / kcost(u.ya) - 1}, ...Object.entries(u.bk || {}).map(([b, v]) => ({b, price: (v.o > 0 ? '+' : '') + v.o, u: v.u, e: u.p * dec(v.o) - 1}))];
        const best = opts.reduce((a, c) => c.e > a.e ? c : a);
        return {...u, opts, best, edge: best.e}; }).filter(u => u.edge != null && u.edge >= LEDGE)
        .sort((a, b) => b.edge - a.edge);
      if (!rungs.length) continue;
      const R = DVP[p.o + '|' + p.pg] || {};
      cards.push({g, p, R, sts: [...new Set(rungs.map(u => u.k))].filter(st => (mx(p, st) || {}).v >= 1.07), rungs, best: rungs[0].edge});
    }
  }
  cards.sort((a, b) => b.best - a.best);
  const soft = c => c.sts.map(st => { const m = mx(c.p, st);
    return `<span class="ladc soft" style="${MG(m.v)}" title="${mxWhy(c.p, st, m)} (context only: the model already uses it)">${esc(c.p.o)} ${sgn(m.v)} ${KLAB[st]} vs ${esc(m.da ? (ARCH_PL[c.p.ar] || PG_PL[c.p.pg]) : PG_PL[c.p.pg])}</span>`; }).join('');
  const row = c => `<div class="lad"><div class="ladh"><b>${esc(c.p.n)}</b> <span class="small muted">${esc(c.p.t)} vs ${esc(c.p.o)} · ${esc(c.p.pos)} · ${f0(c.p.s.vegas.min[MED])} min</span>${soft(c)}${c.p.trend >= 3 ? '<span class="ladc">▲ minutes</span>' : ''}</div>
    <div class="ladr">${c.rungs.slice(0, 3).map(u => `<span class="lru" title="${Math.round(u.l + .5)}+ ${KLAB[u.k]}: our chance ${Math.round(u.p * 100)}% · Kalshi ${Math.round(u.mid * 100)}% (${Math.round(kcost(u.ya) * 100)}% with the fee)${u.opts.filter(o => o.b !== 'Kalshi').map(o => ` · ${o.b} ${o.price}`).join('')}${u.opts.length === 1 ? ' · no Hard Rock / Novig price at this number' : ''}"><b>${Math.round(u.l + .5)}+</b> ${KLAB[u.k]} ${u.opts.map(o => { const tx = `${o.b === 'Kalshi' ? 'K' : o.b === 'Hard Rock' ? 'HR' : 'NV'} ${o.price}`;
      return o === u.best ? (o.u ? `<a class="lbk best" href="${esc(o.u)}" target="_blank" rel="noopener">${tx} ↗</a>` : `<i class="lbk best">${tx}</i>`) : `<i class="lbk">${tx}</i>`; }).join('')} <b class="ledge">+${Math.round(u.edge * 100)}%</b></span>`).join('')}</div></div>`;
  const empty = !anyLadder ? 'Kalshi’s NBA ladders are too thin to read yet (wide gaps between bid and ask); rungs show here once quotes tighten, usually on game day.'
    : !cards.length ? 'No higher rung beats its price by 5%+ right now. Prices move through the day; this updates with them.' : '';
  return `<section class="panel"><div class="phead"><h2>Ladder edges <span class="exp3">experimental</span></h2><span class="small muted">${cards.length} player${cards.length === 1 ? '' : 's'}</span></div>
    ${empty ? `<p class="note" style="margin:0 0 6px">${empty}</p>` : ''}
    <details class="how"><summary>What this is</summary><p class="note">Higher Kalshi rungs (two or more lines above the one priced near 50/50) where our chance beats the best price by 5% or more, for players projected for 20+ minutes. Each rung shows Kalshi (K, fee included), Hard Rock’s alt line (HR) and Novig’s ladder (NV) at the same number; the bold one is the best price and its edge is the one shown (tap it to open the bet when the book gives a link). Hard Rock’s alt lines refresh in the morning and hourly in the last two hours before tip. The green chip marks a soft matchup for that stat, as context: in our 2024-26 backtest players facing soft defenses beat our high projections no more often than anyone else, because the model already counts the matchup. Untested on NBA prices; every flagged rung is tracked on the Profit page.</p></details>
    ${cards.length ? `<div class="ladsw" role="group" aria-label="Ladder edges view">${[['game', 'By game'], ['edge', 'Best first']].map(([k, l]) => `<button type="button" data-ladv="${k}" aria-pressed="${LADV === k}">${l}</button>`).join('')}</div>` : ''}
    ${LADV === 'game' ? byGame(cards, row) : `<div class="ladg">${cards.slice(0, 8).map(row).join('')}</div>${cards.length > 8 ? `<details class="ladw"><summary>${cards.length - 8} more</summary><div class="ladg">${cards.slice(8).map(row).join('')}</div></details>` : ''}`}</section>`;
}
// Ladder edges grouped by game (owner 2026-10-23: "let me see ladder edges by game"): games in start order, each game's
// players best edge first, five shown and the rest folded; the view is remembered on this device (nba-ladv).
let LADV = 'game';
try { LADV = localStorage.getItem('nba-ladv') || 'game'; } catch (e) {}
function byGame(cards, row){
  const by = new Map();
  for (const c of cards) (by.get(c.g.id) || by.set(c.g.id, []).get(c.g.id)).push(c);
  const games = D.games.filter(g => by.has(g.id)).sort((a, b) => new Date(a.start_utc) - new Date(b.start_utc));
  return games.map(g => { const L = by.get(g.id).sort((a, b) => b.best - a.best);
    return `<div class="ladgm"><div class="ladgh"><b>${esc(g.away)} @ ${esc(g.home)}</b> <span class="small muted">${esc(g.start || '')} · ${L.length} player${L.length === 1 ? '' : 's'} · best +${Math.round(L[0].best * 100)}%</span></div>
      <div class="ladg">${L.slice(0, 5).map(row).join('')}</div>${L.length > 5 ? `<details class="ladw"><summary>${L.length - 5} more in this game</summary><div class="ladg">${L.slice(5).map(row).join('')}</div></details>` : ''}</div>`; }).join('');
}
document.addEventListener('click', e => { const b = e.target.closest('[data-ladv]'); if (!b) return;
  LADV = b.dataset.ladv; try { localStorage.setItem('nba-ladv', LADV); } catch (x) {} render(); });
function tierParlays(){
  if (!(window.MKT && MKT.tierCard)) return '<section class="panel"><div class="empty">Loading prices…</div></section>';
  const gameLegs = [];
  for (const g of D.games){
    const M = g.models.vegas; if (!M || !g.has_line) continue;
    const m = M.home_pts - M.away_pts, tot = M.home_pts + M.away_pts, ph = 1 - ncdf((-g.spread - m) / MSD), po = 1 - ncdf((g.total - tot) / TSD);
    const sp = v => (v > 0 ? '+' : v < 0 ? '−' : '') + Math.abs(v);
    gameLegs.push({gid: g.id, kind: 'ml', side: 'away', p: 1 - M.home_win, lab: `${g.away} moneyline`}, {gid: g.id, kind: 'ml', side: 'home', p: M.home_win, lab: `${g.home} moneyline`},
                  {gid: g.id, kind: 'spread', side: 'away', line: g.spread, p: 1 - ph, lab: `${g.away} ${sp(-g.spread)}`}, {gid: g.id, kind: 'spread', side: 'home', line: g.spread, p: ph, lab: `${g.home} ${sp(g.spread)}`},
                  {gid: g.id, kind: 'total', side: 'over', line: g.total, p: po, lab: `${g.away}-${g.home} over ${g.total}`}, {gid: g.id, kind: 'total', side: 'under', line: g.total, p: 1 - po, lab: `${g.away}-${g.home} under ${g.total}`});
  }
  const card = MKT.tierCard({sport: 'nba', overOnly: true, title: 'Parlays by payout (your books)', games: D.games.map(g => ({id: g.id, label: `${g.away} @ ${g.home}`, start: g.start_utc})),
                       gameLegs, propKinds: KLAB, kalshiKinds: KLAB, minEdge: .05});
  return card || `<section class="panel"><div class="phead"><h2>Parlays by payout (your books)</h2></div><p class="note">No parlay with an edge on every leg yet. Legs are player props at your books (Kalshi ladders, plus Novig and Hard Rock once they post) and game lines: our chance blended 50/50 with the market (Pinnacle’s no-vig line when it has one), only the side we lean, our number 10+ points from the market, and a 5%+ edge after fees. Props mostly post on game day.</p></section>`;
}
// ---------------- NBA longshots (owner 2026-10-20: "similar parlays for the NBA as the NFL") ----------------
// Path longshots: one leg per game, 2-3 games, each leg needs 3+ of 5 reasons -- usage (30+ projected minutes or a
// team top-3 scorer), matchup (the model's blended position x archetype number 6%+ for that stat), rotation (a teammate
// out or minutes trending up), game (the market's game lines favor it: total 228+ or a spread within 5, so starters
// play full minutes; NBA sims have no shared game state, so the game factor comes from the lines), price (our
// market-anchored chance beats the price at the owner's books by 5%+). Legs: rungs at about +100..+500 with our chance 20-45%
// and no more than 2 pts under the market. Ladder longshots: achievable Kalshi rungs (our chance 20-45%) for players
// with a 6%+ matchup or a Value tag, our projection backing the rung, 24+ minutes. Both tracked on the Profit page.
const pct1 = p => p == null ? '–' : p < .1 ? (Math.round(p * 1000) / 10).toFixed(1) + '%' : Math.round(p * 100) + '%';
const NKL = {pts: 'pts', reb: 'reb', ast: 'ast', fg3m: 'threes'};
const ordN = n => { const s = ['th','st','nd','rd'], v = n % 100; return n + (s[(v - 20) % 10] || s[v] || s[0]); };
function nbaFactors(g, p, st){
  const team = D.players.filter(x => x.g === g.id && x.t === p.t);
  const med = (x, s_) => x.s.vegas && x.s.vegas[s_] ? x.s.vegas[s_][MED] : 0;
  const top3 = team.slice().sort((a, b) => med(b, 'pts') - med(a, 'pts')).slice(0, 3).includes(p);
  const mins = med(p, 'min'), m = mx(p, st), outs = ((g.out || {})[p.t] || []).filter(o => o.m >= 20);   // a rotation player out (20+ usual minutes)
  const close = g.has_line && Math.abs(g.spread) <= 5, fast = g.has_line && g.total >= 228;
  return {
    usage: mins >= 30 || top3 ? (mins >= 30 ? `${f0(mins)} min` : 'top-3 scorer') : null,
    matchup: m && m.v >= 1.06 ? `${esc(p.o)} ${sgn(m.v)} vs ${m.da ? (ARCH_PL[p.ar] || PG_PL[p.pg]) : PG_PL[p.pg]}` : null,
    rotation: outs.length && mins >= 24 ? `${outs[0].n.split(' ').slice(-1)[0]} out (${f0(outs[0].m)} min)` : p.trend >= 3 ? 'minutes up' : null,
    game: fast ? `total ${g.total}` : close ? `close game (${g.spread > 0 ? '+' : ''}${g.spread})` : null,
  };
}
function nbaPathLegs(g){
  if (!(window.MKT && MKT.altLadder)) return [];
  const out = [];
  for (const p of D.players.filter(x => x.g === g.id && x.st !== 'Out')){
    const mins = p.s.vegas && p.s.vegas.min && p.s.vegas.min[MED]; if (!(mins >= 20)) continue;
    const tk = MKT.tagKind ? MKT.tagKind(g.id, p.n) : null;
    for (const st of ['pts', 'reb', 'ast', 'fg3m']){
      if (!vol(p, st)) continue;
      const F0 = nbaFactors(g, p, st);
      // never our number alone: a rung no book prices two-way takes the main line's sharp fair carried up the ladder by
      // our distribution's shape (same log-odds gap); no market reference at all -> no leg
      const R = MKT.altLadder(g.id, p.n, st), main = R.find(r_ => r_.main);
      const lg = v => Math.log(v / (1 - v)), sg = v => 1 / (1 + Math.exp(-v));
      const gap = main && main.fair != null && main.p > 0 && main.p < 1 ? lg(main.fair) - lg(main.p) : null;
      for (const u of R){
        if (!u.best) continue;
        const dO = u.best.o > 0 ? 1 + u.best.o / 100 : 1 + 100 / -u.best.o;
        if (dO < 2.0 || dO > 6) continue;                                          // achievable legs: about +100 to +500
        let fair = u.fair; if (fair == null && gap != null && u.p > 0 && u.p < 1) fair = sg(lg(u.p) + gap);
        const mk = fair ?? u.mid, w = fair != null ? (main ? main.w : u.w) : .5;
        if (mk == null) continue;
        const q = u.p == null ? mk : mk == null ? u.p : w * u.p + (1 - w) * mk;
        if (q == null || q < .20 || q > .45 || (u.p != null && mk != null && u.p < mk - .02)) continue;   // achievable: 20-45%
        const ev = q * dO - 1, F = {...F0, price: ev >= .05 ? `+${Math.round(ev * 100)}% vs our chance` : tk === st ? 'Value tag' : null};
        const nf = Object.values(F).filter(Boolean).length; if (nf < 3) continue;
        out.push({g, p, st, u, q, dO, ev, F, nf, sc: nf * 10 + q * dO});
      }
    }
  }
  const best = {};                                       // the player's best rung
  for (const L of out) if (!best[L.p.k] || L.sc > best[L.p.k].sc) best[L.p.k] = L;
  return Object.values(best).sort((a, b) => b.sc - a.sc);
}
function nbaCombos(per, maxLegs){
  const combos = [], walk = (i, pk) => { if (pk.length >= 2) combos.push(pk); if (pk.length === maxLegs) return;
    for (let j = i; j < per.length; j++) for (const L of per[j]) walk(j + 1, [...pk, L]); };
  walk(0, []);
  return combos;
}
function nbaPick(combos, val, rank, n, k, used){
  const o = [];
  for (const x of combos.filter(c => c.length === n).map(val).filter(x => x.ok).sort(rank)){
    if (o.length >= k) break; if (x.c.some(L => (used[L.p.k] || 0) >= 2)) continue;
    x.c.forEach(L => { used[L.p.k] = (used[L.p.k] || 0) + 1; }); o.push(x); }
  return o;
}
const nbaLeg = (L, extra) => ({gid: String(L.g.id), start: L.g.start_utc, kind: L.st, side: 'o', line: L.u.l, name: MKT.norm(L.p.n),
  lab: `${L.p.n} ${Math.ceil(L.u.l)}+ ${NKL[L.st]}`, p: +L.q.toFixed(4), price: L.u.best ? L.u.best.o : L.price, book: L.u.best ? L.u.best.k : 'Kalshi', ...(extra || {})});
function nbaPathLongshots(){
  const now = Date.now(), per = [];
  for (const g of D.games){ if (g.start_utc && new Date(g.start_utc) <= now) continue; const L = nbaPathLegs(g); if (L.length) per.push(L.slice(0, 3)); }
  if (per.length < 2) return '';
  const val = c => { const p = c.reduce((s, L) => s * L.q, 1), d = c.reduce((s, L) => s * L.dO, 1); return {c, p, d, nf: c.reduce((s, L) => s + L.nf, 0), ok: p * d - 1 >= 0}; };
  const used = {}, rank = (a, b) => b.nf / b.c.length - a.nf / a.c.length || b.p * b.d - a.p * a.d, combos = nbaCombos(per, 3);
  const P = [...nbaPick(combos, val, rank, 2, 2, used), ...nbaPick(combos, val, rank, 3, 2, used)];
  if (!P.length) return '';
  if (MKT.reg) P.forEach(x => MKT.reg('Path longshots', `${x.c.length}-leg`, x.c.map(L => nbaLeg(L, {f: Object.keys(L.F).filter(k => L.F[k])})), x.p, x.d));
  const chip = (k, t) => t ? `<span class="ladc${k === 'matchup' || k === 'price' ? ' soft' : ''}">${k} · ${esc(t)}</span>` : '';
  const legHtml = L => `<li><b>${esc(L.p.n)} ${Math.ceil(L.u.l)}+ ${NKL[L.st]}</b><span>${L.u.best.u ? `<a href="${esc(L.u.best.u)}" target="_blank" rel="noopener">${esc(L.u.best.name)} ${L.u.best.o > 0 ? '+' : ''}${L.u.best.o}</a>` : `${esc(L.u.best.name)} ${L.u.best.o > 0 ? '+' : ''}${L.u.best.o}`}</span></li>
    <li class="lwhy"><span class="ladc">${esc(L.p.t)} vs ${esc(L.p.o)}</span>${Object.entries(L.F).map(([k, t]) => chip(k, t)).join('')}<span class="ladc">us ${pct(L.q)}</span></li>`;
  const card = x => `<div class="dcp"><div class="dch"><span class="slot ls">${x.c.length} games</span>${x.nf} of ${x.c.length * 5} reasons</div><ul>${x.c.map(legHtml).join('')}</ul>
    <div class="dcs"><span>pays <b>${x.d.toFixed(0)}x</b></span><span>$5 → <b>$${(5 * x.d).toFixed(0)}</b></span><span>hits <b>${pct1(x.p)}</b></span><span class="${x.p * x.d - 1 >= .05 ? 'good' : 'muted'}">edge ${x.p * x.d - 1 >= 0 ? '+' : '−'}${Math.abs(Math.round((x.p * x.d - 1) * 100))}%</span></div></div>`;
  return `<section class="panel"><div class="phead"><h2>Path longshots</h2><span class="small muted">usage · matchups · rotations · game lines · odds</span></div>
    <details class="how"><summary>How these are built</summary><p class="note">Each leg needs at least three of five reasons: <b>usage</b> (30+ projected minutes or one of his team’s top three scorers), <b>matchup</b> (the defense allows 6%+ more of that stat to his position and archetype, the number the model uses), <b>rotation</b> (a teammate who plays 20+ minutes is out, or his minutes are trending up), <b>game</b> (a high total, 228+, or a close spread, so starters play full minutes), and <b>price</b> (our market-anchored chance beats the best price at your books by 5%+, or a Value tag). Each rung sits inside our projection’s realistic range (our chance 20-45%, paying about +100 to +500) and no more than 2 points under the market’s. One leg per game, so books pay the legs multiplied. <b>Hits</b> uses each leg’s market-anchored chance. Tracked on the Profit page.</p></details>
    <div class="dcg">${P.map(card).join('')}</div></section>`;
}
function nbaLadderLongshots(){
  if (!(window.MKT && MKT.ladder)) return '';
  const now = Date.now(), best = {};
  for (const g of D.games){
    if (g.start_utc && new Date(g.start_utc) <= now) continue;
    for (const p of D.players.filter(x => x.g === g.id && x.st !== 'Out')){
      const mins = p.s.vegas && p.s.vegas.min && p.s.vegas.min[MED]; if (!(mins >= 24)) continue;
      const tk = MKT.tagKind ? MKT.tagKind(g.id, p.n) : null;
      for (const u of MKT.ladder(g.id, p.id, p.n, Object.keys(NKL))){
        if (!(u.mid >= .15 && u.mid <= .45) || !(u.yb > 0) || u.ya - u.yb > .05) continue;           // achievable: our chance 20-45%
        if (u.p == null || u.p < .20 || u.p > .45 || u.p < u.mid - .02 || !vol(p, u.k)) continue;
        const m = mx(p, u.k), soft = m && m.v >= 1.06; if (!soft && tk !== u.k) continue;
        const sc = (soft ? 2 : 0) + (tk === u.k ? 2 : 0), L = {g, p, st: u.k, u: {...u, best: null}, q: u.mid, cost: u.cost, price: amO(u.cost), sc, m, tag: tk === u.k};
        if (!best[g.id] || sc > best[g.id].sc || (sc === best[g.id].sc && Math.abs(u.p - .30) < Math.abs(best[g.id].u.p - .30))) best[g.id] = L;
      }
    }
  }
  const per = Object.values(best).map(L => [L]); if (per.length < 2) return '';
  const val = c => ({c, p: c.reduce((s, L) => s * L.q, 1), d: c.reduce((s, L) => s / L.cost, 1), nf: c.reduce((s, L) => s + L.sc, 0), ok: c.reduce((s, L) => s * L.q, 1) >= .004});
  const used = {}, rank = (a, b) => b.nf - a.nf || b.p - a.p, combos = nbaCombos(per, 3);
  const P = [...nbaPick(combos, val, rank, 2, 3, used), ...nbaPick(combos, val, rank, 3, 2, used)];
  if (!P.length) return '';
  if (MKT.reg) P.forEach(x => MKT.reg('Ladder longshots (experimental)', `${x.c.length}-leg`, x.c.map(L => nbaLeg(L)), x.p, x.d));
  const card = x => `<div class="dcp"><div class="dch"><span class="slot ls">${x.c.length} legs</span>Kalshi</div><ul>${x.c.map(L => `<li><b>${esc(L.p.n)} ${Math.round(L.u.l + .5)}+ ${NKL[L.st]}</b><span>${esc(L.p.t)} vs ${esc(L.p.o)} · ${L.u.price}</span></li>
      <li class="lwhy">${L.m && L.m.v >= 1.06 ? `<span class="ladc soft">${esc(L.p.o)} ${sgn(L.m.v)} vs ${esc(L.m.da ? (ARCH_PL[L.p.ar] || PG_PL[L.p.pg]) : PG_PL[L.p.pg])}</span>` : ''}${L.tag ? '<span class="ladc soft">Value tag</span>' : ''}<span class="ladc">proj ${f1(L.p.s.vegas[L.st][MED])} · us ${pct(L.u.p)} vs ${pct(L.u.mid)}</span></li>`).join('')}</ul>
      <div class="dcs"><span>pays <b>${x.d.toFixed(0)}x</b></span><span>$5 → <b>$${(5 * x.d).toFixed(0)}</b></span><span>market chance <b>${pct1(x.p)}</b></span></div></div>`;
  return `<section class="panel"><div class="phead"><h2>Ladder longshots <span class="exp3">experimental</span></h2><span class="small muted">achievable Kalshi rungs · one per game</span></div>
    <details class="how"><summary>What this is</summary><p class="note">Rungs inside our projection’s realistic range (our chance 20-45%, about +120 to +400) on a player’s Kalshi ladder, for players with a soft matchup (the defense allows 6%+ more of that stat to his position and archetype) or a Value tag, where our projection backs the rung (no more than 2 points under Kalshi’s) and he plays 24+ minutes. One per game, two or three games. The chance shown is Kalshi’s own; the NFL version of this idea is a test too, and both are tracked on the Profit page.</p></details>
    <div class="dcg">${P.map(card).join('')}</div></section>`;
}
const amO = q => q >= .5 ? -Math.round(100 * q / (1 - q)) : Math.round(100 * (1 - q) / q);
// Learning builder candidates (builder.py, owner 2026-10-22): in freeze mode, every rotation player's main-line over at
// the owner's books with its path reasons (nbaFactors: usage, matchup, rotation, game); fits with 2+ of them.
function learnCandsNBA(){
  const now = Date.now();
  for (const g of D.games){
    if (g.start_utc && new Date(g.start_utc) <= now) continue;
    for (const p of D.players.filter(x => x.g === g.id && x.st !== 'Out')){
      const mins = p.s.vegas && p.s.vegas.min && p.s.vegas.min[MED]; if (!(mins >= 20)) continue;
      for (const st of ['pts', 'reb', 'ast', 'fg3m']){
        if (!vol(p, st)) continue;
        const F0 = nbaFactors(g, p, st), f = Object.keys(F0).filter(k => F0[k]);
        MKT.cand(g.id, g.start_utc, p.n, st, null, null, f, f.length >= 2);
      }
    }
  }
}
function parlaysView(){
  if (window.MKT && MKT.FREEZE && MKT.cand) try { learnCandsNBA(); } catch (e) {}
  return (window.MKT && MKT.dailyCard ? MKT.dailyCard() : '') + nbaPathLongshots() + ladderCard() + nbaLadderLongshots() + tierParlays();
}
function render(){
  for (const b of document.querySelectorAll('.tabs button')) b.setAttribute('aria-selected', b.dataset.view === state.view);
  document.getElementById('main').innerHTML = state.view === 'board' ? board() : state.view === 'dvp' ? dvpView() : state.view === 'parlays' ? parlaysView() : gameView();
  const cur = document.querySelector('.rail .game-btn[aria-current="true"]');
  if (cur){ const r = cur.parentElement, rr = r.getBoundingClientRect(), cr = cur.getBoundingClientRect(); if (r.scrollWidth > r.clientWidth && (cr.left < rr.left || cr.right > rr.right)) r.scrollLeft += cr.left - rr.left - 12; }
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
  else if (t.dataset.dgrp){ state.dgrp = t.dataset.dgrp; render(); }
  else if (t.dataset.dsort != null){ state.dsort = state.dsort === t.dataset.dsort ? null : t.dataset.dsort; render(); }
});
document.addEventListener('input', e => {
  if (e.target.id !== 'sl-in') return;
  const box = e.target.closest('.slider'), p = D.players.find(x => x.k === box.dataset.sk), g = state.game;
  state.oline = +e.target.value; document.getElementById('sl-val').textContent = state.oline;
  document.getElementById('sl-res').innerHTML = readout(p.d[mode(p.g)][state.ostat], state.oline, p, state.ostat);
});
render();
