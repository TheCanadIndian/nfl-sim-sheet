const D = JSON.parse(document.getElementById('data').textContent);
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const f0 = v => v == null ? '–' : Math.round(v).toString();
const f1 = v => v == null ? '–' : (Math.round(v*10)/10).toFixed(1);
const pct = p => p == null ? '–' : Math.round(p*100) + '%';
const american = p => { p = Math.min(Math.max(p,1e-4),1-1e-4); const o = p>=.5 ? -100*p/(1-p) : 100*(1-p)/p; return (o>0?'+':'') + Math.round(o); };
const [MEAN, P10, P25, MED, P75, P90] = [0,1,2,3,4,5];
const LAB = {min:'Minutes', pts:'Points', reb:'Rebounds', ast:'Assists', fg3m:'Threes', pra:'Pts+Reb+Ast', stl:'Steals', blk:'Blocks', tov:'Turnovers'};
const COLS = ['min','pts','reb','ast','fg3m','pra'];
const state = {view: ['#board','#dvp','#parlays','#ladders'].includes(location.hash) ? location.hash.slice(1) : 'games', game: D.games[0]?.id, gm: {}, bstat: 'pts', dstat: 'pts', open: null, ostat: 'pts', oline: null, okey: null};
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
  if (p.tl){                                               // teammate links (teamlink.py, owner 2026-11-06): a 24+ min teammate out tonight
    const w = esc((p.tl.w || []).join(', '));
    if (p.tl.f != null && p.tl.f >= .25) h += `<span class="lean r2" title="${w} out tonight: ${Math.round(p.tl.f * 100)}% of his assisted baskets this season came from ${w}. Scorers this dependent on an out teammate went under the market on points / threes (Kalshi Jan–Nov 2026: 4.8 pts below the price on 691, every period). Tracked on the Profit page, not a rule.">▼ feeder out</span>`;
    const am = p.s[mode(g)] && p.s[mode(g)].ast ? p.s[mode(g)].ast[MED] : 0;   // only players with assist volume (props exist)
    if (p.tl.a != null && p.tl.a < .15 && am >= 2) h += `<span class="lean g2" title="${w} out tonight; only ${Math.round(p.tl.a * 100)}% of his assists this season went to ${w}. Passers like this beat the market on assists overs (Kalshi Jan–Nov 2026: +3.8 pts over the price on 759, every period, about +2% after Kalshi’s fee). Tracked on the Profit page, not a rule.">▲ ast free</span>`;
  }
  const l = lean(p, 'pts');
  if (l && Math.abs(l.edge) >= .03) h += `<span class="lean ${shadeP(.5 + l.edge * 2)}" title="Market-blind model ${l.edge > 0 ? 'higher' : 'lower'} on points at ${l.L}">${l.edge > 0 ? '▲' : '▼'} blind</span>`;
  if (p.gp === 0) h += '<span class="tag" style="color:var(--muted)" title="No NBA games yet: league-average role assumed">new</span>';
  if (window.MKT && MKT.lcFlag) h += MKT.lcFlag(g, p.n) + (MKT.offFlag ? MKT.offFlag(g, p.n) : '') + (MKT.moveFlag ? MKT.moveFlag(g, p.n) : '') + whyNBA(p, g);
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
// Projected box score (owner 2026-10-24: "full box scores for every game... I want x person 27 points 6 rebounds 2
// assists... make sure NBA has steals and blocks"): each player's simulated averages (p.bx[mode]), integer columns by
// largest remainder within the team (team totals = the sum of the averages), points rebuilt from rounded makes.
let PVIEW = 'proj';
try { PVIEW = localStorage.getItem('nba-pview') || 'proj'; } catch (e) {}
document.addEventListener('click', e => { const b = e.target.closest('[data-pview]'); if (!b) return; PVIEW = b.dataset.pview;
  try { localStorage.setItem('nba-pview', PVIEW); } catch (x) {} render(); });
function lrRound(vals){
  const tot = Math.round(vals.reduce((a, b) => a + b, 0)), fl = vals.map(v => Math.floor(v));
  let rem = tot - fl.reduce((a, b) => a + b, 0);
  vals.map((v, i) => [v - Math.floor(v), i]).sort((a, b) => b[0] - a[0]).forEach(([, i]) => { if (rem > 0){ fl[i]++; rem--; } });
  return fl;
}
function boxTable(g, team){
  const md = mode(g.id);
  // bx = 'if he plays' means; team totals hold only weighted by his chance to play (pp). Box = the rotation, each stat
  // scaled so it adds up to the team's expected total (240 min, the projected points, ...)
  const all = D.players.filter(p => p.g === g.id && p.t === team && p.bx && p.bx[md] && p.st !== 'Out');
  const pp = p => p.pp == null ? 1 : p.pp, KS = ['min', 'pts', 'reb', 'ast', 'stl', 'blk', 'tov', 'fga', 'fta', 'fg3m'];
  // the 11 with the most expected minutes (research/nba_box_backtest.py, 2024-26: best MAE for players who played; ~10.7 play)
  const ps = all.slice().sort((a, b) => pp(b) * b.bx[md].min - pp(a) * a.bx[md].min).slice(0, 11).sort((a, b) => b.bx[md].min - a.bx[md].min);
  if (!ps.length) return '';
  const sc = {};
  for (const k of KS) { const want = all.reduce((a, p) => a + pp(p) * p.bx[md][k], 0), have = ps.reduce((a, p) => a + p.bx[md][k], 0); sc[k] = have > 0 ? want / have : 1; }
  const X = ps.map(p => { const b = {}; for (const k of KS) b[k] = p.bx[md][k] * sc[k];
    const f3 = p.sh ? p.sh[0] : .36, ft = p.sh ? p.sh[1] : .78;
    const ftm = b.fta * ft, fg3a = Math.min(b.fga, b.fg3m / Math.max(f3, .15)), fg2m = Math.max(0, (b.pts - 3 * b.fg3m - ftm) / 2);
    return {min: b.min, reb: b.reb, ast: b.ast, stl: b.stl, blk: b.blk, tov: b.tov, fg2m, fg3m: b.fg3m, ftm, fga: Math.max(b.fga, fg2m + b.fg3m), fg3a: Math.max(fg3a, b.fg3m), fta: Math.max(b.fta, ftm)}; });
  const R = {};
  for (const k of ['min', 'reb', 'ast', 'stl', 'blk', 'tov', 'fg2m', 'fg3m', 'ftm', 'fga', 'fg3a', 'fta']) R[k] = lrRound(X.map(x => x[k]));
  const rows = ps.map((p, i) => { const fgm = R.fg2m[i] + R.fg3m[i];
    return {p, start: i < 5, min: R.min[i], pts: 2 * R.fg2m[i] + 3 * R.fg3m[i] + R.ftm[i], reb: R.reb[i], ast: R.ast[i], stl: R.stl[i], blk: R.blk[i], tov: R.tov[i],
            fgm, fga: Math.max(R.fga[i], fgm), f3m: R.fg3m[i], f3a: Math.max(R.fg3a[i], R.fg3m[i]), ftm: R.ftm[i], fta: Math.max(R.fta[i], R.ftm[i])}; });
  const T = k => rows.reduce((a, r) => a + r[k], 0);
  // owner 2026-10-24: highlight box numbers above the books' main line (an over lean); strip lists them with the line
  const LN = (r, k) => window.MKT && MKT.propLine ? MKT.propLine(g.id, r.p.n, k) : null;
  const ov = (r, k, v) => { const l = LN(r, k); return l != null && v > l ? `<span class="bxo" title="above the books’ line ${l}">${v}</span>` : v; };
  const over = rows.flatMap(r => [['pts', r.pts, 'PTS'], ['reb', r.reb, 'REB'], ['ast', r.ast, 'AST'], ['fg3m', r.f3m, '3PM']].map(([k, v, lab]) => { const l = LN(r, k); return l != null && v > l ? `<span class="bxoc">${esc(r.p.n.replace(/\s+(Jr\.?|Sr\.?|II|III|IV|V)$/, '').split(' ').slice(-1)[0])} ${lab} <b>${v}</b> <i>line ${l}</i></span>` : ''; }).filter(Boolean));
  const q = p => p.st && p.st !== 'Active' ? ` <span class="tag q" title="${esc(p.st)}">${p.st === 'Questionable' ? 'Q' : p.st === 'Day-To-Day' ? 'DTD' : esc(p.st)}</span>` : '';
  const tr = (r, i) => `${i === 5 ? '<tr class="bxsep"><td class="l" colspan="11">Bench</td></tr>' : ''}<tr><td class="l"><b>${esc(r.p.n)}</b> <span class="small muted">${esc(r.p.pos)}</span>${q(r.p)}</td><td>${r.min}</td><td class="bxp">${ov(r, 'pts', r.pts)}</td><td>${ov(r, 'reb', r.reb)}</td><td>${ov(r, 'ast', r.ast)}</td><td>${r.stl}</td><td>${r.blk}</td><td>${r.tov}</td><td>${r.fgm}-${r.fga}</td><td>${ov(r, 'fg3m', r.f3m)}-${r.f3a}</td><td>${r.ftm}-${r.fta}</td></tr>`;
  return `<div class="tw"><table class="t bx"><thead><tr><th class="l">${esc(team)} · starters</th><th>MIN</th><th>PTS</th><th>REB</th><th>AST</th><th>STL</th><th>BLK</th><th>TOV</th><th>FG</th><th>3P</th><th>FT</th></tr></thead><tbody>
    ${rows.map(tr).join('')}<tr class="bxt"><td class="l"><b>Team</b></td><td>${T('min')}</td><td class="bxp">${T('pts')}</td><td>${T('reb')}</td><td>${T('ast')}</td><td>${T('stl')}</td><td>${T('blk')}</td><td>${T('tov')}</td><td>${T('fgm')}-${T('fga')}</td><td>${T('f3m')}-${T('f3a')}</td><td>${T('ftm')}-${T('fta')}</td></tr></tbody></table></div>${over.length ? `<div class="bxos"><span class="eyebrow">${esc(team)} above the books’ line</span>${over.join('')}</div>` : ''}`;
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
  const pv = `<span class="gmsw" role="group" aria-label="Players view"><button data-pview="proj" aria-pressed="${PVIEW === 'proj'}">Projections</button><button data-pview="box" aria-pressed="${PVIEW === 'box'}">Box score</button></span>`;
  const tables = PVIEW === 'box'
    ? `<section class="panel"><div class="phead"><h2>Projected box score <span class="exp3">highly experimental</span></h2>${pv}<span class="muted small">${mode(g.id) === 'vegas' ? 'With Vegas' : 'Market-blind'} · each player’s simulated average, rounded so team totals hold</span></div>${boxTable(g, g.away)}${boxTable(g, g.home)}
      <p class="note" style="margin:8px 0 0">One number per stat: the average of each player’s simulated games (the most accurate single guess, and averages add up to the team score). The 11 players with the most expected minutes (about how many play); the rest of the bench’s expected share goes to them so totals match the projected score. Tested on 2024-26 games: points off by 4.4 a player on average. Starters are the five with the most projected minutes; Q = questionable. Highlighted = above the books’ main line for that prop (an over lean; the chips list the line). Points come from his makes (2 × twos + 3 × threes + free throws); shooting splits use his 3P% and FT% this season and last, pulled toward league average on small samples. <a href="boxes.html">How accurate it has been, graded every night →</a></p></section>`
    : `<section class="panel"><div class="phead"><h2>Players</h2>${pv}<span class="muted small" style="display:flex;flex-wrap:wrap;gap:4px 10px;align-items:center">Median, with 8-in-10 range below. Tap a name to slide a line. ${mgKey('tough matchup', 'soft matchup')}</span></div>${softStrip(g)}${teamTable(g, g.away)}${teamTable(g, g.home)}</section>`;
  return `<div class="games"><nav class="rail" aria-label="Games">${rail}</nav><div class="detail">${head}${fbPanel(g)}${tables}</div></div>`;
}
// First basket (owner 2026-11-07: 'build the model and show me who is projected and if they have value'): nba/firstbasket.py
// -> markets/fb_model_nba.json (our chance: tip records of the projected jumpers x points share, opening-shot share, first-FG
// share, jumper; research/nba_fb_leads.py) against every book's first-basket price (markets/fb_nba.json, sharp.fb_nba).
// Value = our chance x the price - 1, 10%+ green. Experimental: the model beat the old flat one in every half of 4 seasons, but
// its value against real prices is untested (tracked on the Profit page).
let FBM = null, FBP = null;
try { fetch('../markets/fb_model_nba.json?t=' + Date.now(), {cache: 'no-store'}).then(r => r.ok ? r.json() : null).then(d => { if (d && d.games){ FBM = d.games; render(); } }).catch(() => {}); } catch (e) {}
try { fetch('../markets/fb_nba.json?t=' + Date.now(), {cache: 'no-store'}).then(r => r.ok ? r.json() : null).then(d => { if (d && d.games){ FBP = d.games; render(); } }).catch(() => {}); } catch (e) {}
const FBK = {FD: 'FD', Bovada: 'BV', DK: 'DK', MGM: 'MGM', Caesars: 'CZR', 'ESPN BET': 'ESPN', Fanatics: 'FAN', 'Hard Rock': 'HR', BetRivers: 'BR', BetOnline: 'BOL', Novig: 'NV', Kalshi: 'K'};
let FBALL = false;
document.addEventListener('click', e => { const b = e.target.closest('[data-fball]'); if (!b) return; FBALL = !FBALL; render(); });
function fbPanel(g){
  const Mg = FBM && FBM[String(g.id)];
  if (!Mg) return '';
  const Pg = (FBP && FBP[String(g.id)] && FBP[String(g.id)].players) || {};
  const dec = o => o > 0 ? 1 + o / 100 : 1 + 100 / -o, so = o => (o > 0 ? '+' : '') + o;
  const fo = p => p >= .5 ? Math.round(-100 * p / (1 - p)) : Math.round(100 * (1 - p) / p);
  const nrm = v => String(v || '').normalize('NFKD').replace(/[\u0300-\u036f]/g, '').toLowerCase().replace(/[^a-z ]/g, '').replace(/\b(jr|sr|ii|iii|iv)\b/g, '').replace(/\s+/g, ' ').trim();
  const team = {}; D.players.filter(x => x.g === g.id).forEach(x => { team[nrm(x.n)] = [x.t, x.n]; });
  const R = Object.entries(Mg).filter(([, x]) => x.r).map(([nm, x]) => {
    const o = (Pg[nm] && Pg[nm].o) || {}, bks = Object.entries(o).sort((a, b) => dec(b[1]) - dec(a[1]));
    const best = bks[0], hr = o['Hard Rock'], own = ['Hard Rock', 'Novig', 'Kalshi'].filter(b => o[b] != null).sort((a, b) => dec(o[b]) - dec(o[a]))[0];
    return {nm, x, t: (team[nm] || [])[0] || '', n: (team[nm] || [])[1] || nm, best, own: own ? [own, o[own]] : null, ev: best ? x.p * dec(best[1]) - 1 : null, evo: own ? x.p * dec(o[own]) - 1 : null, all: bks};
  }).sort((a, b) => b.x.p - a.x.p);
  if (!R.length) return '';
  const nv = R.filter(r => r.ev != null && r.ev >= .10).length;
  const show = FBALL ? R : R.slice(0, 6);
  const evc = v => v == null ? '<span class="muted">–</span>' : `<b class="${v >= .10 ? 'fbv' : v >= 0 ? '' : 'muted'}">${v >= 0 ? '+' : '−'}${Math.abs(Math.round(v * 100))}%</b>`;
  const tags = r => `${r.x.j ? '<span class="tag" title="Projected to take the opening tip (most frequent jumper among tonight’s five)">jumper</span>' : ''}${r.x.s1 >= .3 ? `<span class="tag" title="Took his team’s first shot in ${Math.round(r.x.s1 * 100)}% of his starts (decayed, shrunk): the opening play runs through him">opens ${Math.round(r.x.s1 * 100)}%</span>` : ''}${r.x.c ? '<span class="tag">C</span>' : ''}`;
  return `<section class="panel"><div class="phead"><h2>First basket <span class="exp3">experimental</span></h2><span class="muted small">our chance vs every book · ${nv ? `<b class="fbv">${nv} with value</b>` : 'no value at 10%+'}</span></div>
    <div class="tw"><table class="t fbt"><thead><tr><th class="l">Player</th><th>Ours</th><th title="Our chance x the best price - 1; green at 10%+">Value</th><th>Best price</th><th title="Best of Hard Rock / Novig / Kalshi, and its value">Your books</th><th>Fair</th></tr></thead><tbody>
    ${show.map(r => `<tr><td class="l name"><b>${esc(r.n)}</b> <span class="small muted">${esc(r.t)}</span> ${tags(r)}</td><td><b>${(r.x.p * 100).toFixed(1)}%</b></td><td>${evc(r.ev)}</td>
      <td title="${esc(r.all.map(([b, v]) => `${b} ${so(v)}`).join(' · '))}">${r.best ? `${so(r.best[1])} <span class="small muted">${esc(FBK[r.best[0]] || r.best[0])}</span>` : '<span class="muted">not posted</span>'}</td>
      <td>${r.own ? `${so(r.own[1])} <span class="small muted">${esc(FBK[r.own[0]] || r.own[0])}</span> ${evc(r.evo)}` : '<span class="muted">–</span>'}</td><td class="muted">${so(fo(r.x.p))}</td></tr>`).join('')}</tbody></table></div>
    ${R.length > 6 ? `<button class="flagbtn" data-fball="1" aria-pressed="${FBALL}" style="margin-top:6px">${FBALL ? 'Show top 6' : `All ${R.length} starters`}</button>` : ''}
    <details class="how"><summary>How this works</summary><p class="note">Our chance that he makes the game’s first field goal: who wins the opening tip (each projected jumper’s tip record), then inside his team’s five his share of the scoring, how often he takes his team’s first shot (opening plays repeat), how often he makes it, and whether he jumps. In 5,746 games since 2023 this beat a plain scoring-share model in every half of every season. Value = our chance × the price − 1 (green at 10%+). Prices from every book we pull; Hard Rock / Novig / Kalshi are your books. Untested against real prices: tracked on the Profit page, not a rule.</p></details></section>`;
}
let BPIN = false;                                 // board filter: only players Pinnacle has moved (sharp.pin_moves)
function board(){
  const st = state.bstat, rows = D.players.filter(p => p.s.vegas && p.s.vegas[st] && (!BPIN || (window.MKT && MKT.pinMoves && MKT.pinMoves(p.g, p.n).some(m => m.src === 'pin'))))
    .sort((a,b) => b.s[mode(a.g)][st][MED] - a.s[mode(b.g)][st][MED] || 0);
  rows.sort((a,b) => b.s[mode(b.g)][st][MED] - a.s[mode(a.g)][st][MED]);
  return `<section class="panel"><div class="phead"><div class="seg">${COLS.map(c => `<button data-bstat="${c}" aria-pressed="${c === st}">${LAB[c]}</button>`).join('')}</div><button class="flagbtn" data-bpin="1" aria-pressed="${BPIN}" title="Players whose prop Pinnacle has moved: line up and / or the over shortened (tracked, experimental)">Pinnacle moved</button><span class="muted small">${BPIN ? rows.length + ' players · ' : ''}Uses each game's selected model</span></div>
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
const LEDGE = .05;
const ladPrice = u => { const opts = [{b: 'Kalshi', price: u.price, pay: 1 / u.cost}, ...Object.entries(u.bk || {}).map(([b, v]) => ({b, price: (v.o > 0 ? '+' : '') + v.o, u: v.u, pay: v.o > 0 ? 1 + v.o / 100 : 1 + 100 / -v.o}))];
  const best = opts.reduce((a, c) => c.pay > a.pay ? c : a); return {...u, opts, best, edge: u.p != null ? u.p * best.pay - 1 : null}; };
// one rung; its number is a dropdown of this rung and the ones below it, to check their prices (owner 2026-11-01, as on the
// NFL page). Rungs outside the card's flagged set show our chance, no edge: the 5% rule only ever covered the flagged rungs
const LADSEL = {}, LADC = {};
function lruChip(c, u0){
  const key = `${c.p.id}|${u0.k}|${u0.l}`, lo = c.all.filter(x => x.k === u0.k && x.l <= u0.l).sort((a, b) => b.l - a.l);
  LADC[key] = {c, u0};
  const u = lo.find(x => x.l === LADSEL[key]) || u0, flagged = c.rungs.some(x => x.k === u.k && x.l === u.l), b = u.best, kc = u.cost;
  const n = l => Math.round(l + .5), tx = o => `${o.b === 'Kalshi' ? 'K' : o.b === 'Hard Rock' ? 'HR' : 'NV'} ${o.price}`;
  // multi-book fair (MKT.multiFair, leaving out the best book) + the tracked Novig check: Novig's price 10%+ under the
  // fair from 2+ other sources (30% ours) -- research/ladder_multibook.py, Novig +73% on 120, both halves, a lead (owner 2026-11-03)
  const mf = window.MKT && MKT.multiFair ? MKT.multiFair(c.g.id, c.p.id, c.p.n, u.k, u.l, b.b) : null;
  const nvo = u.opts.find(o => o.b === 'Novig'), mfn = nvo && window.MKT && MKT.multiFair ? MKT.multiFair(c.g.id, c.p.id, c.p.n, u.k, u.l, 'Novig') : null;
  const nvq = mfn && mfn.n >= 2 ? (u.p != null ? .3 * u.p + .7 * mfn.f : mfn.f) : null, nvd = nvo ? +String(nvo.price).replace('+', '') : null;
  const nve = nvq != null && nvd ? nvq * (nvd > 0 ? 1 + nvd / 100 : 1 + 100 / -nvd) - 1 : null;
  const mfTxt = mf ? ` · fair from ${Object.keys(mf.parts).length} source${Object.keys(mf.parts).length === 1 ? '' : 's'} ${Math.round(mf.f * 100)}% (${Object.entries(mf.parts).map(([k, v]) => `${k} ${Math.round(v * 100)}%`).join(', ')}, without ${b.b})` : '';
  const num = lo.length > 1 ? `<select class="lsel" data-lrk="${esc(key)}" aria-label="Change rung">${lo.map(x => `<option value="${x.l}"${x.l === u.l ? ' selected' : ''}>${n(x.l)}+</option>`).join('')}</select>` : `<b>${n(u.l)}+</b>`;
  return `<span class="lru" title="${n(u.l)}+ ${KLAB[u.k]}: our chance ${u.p != null ? Math.round(u.p * 100) + '%' : '–'} · Kalshi ${Math.round(u.mid * 100)}% (${Math.round(kc * 100)}% with the fee)${u.opts.filter(o => o.b !== 'Kalshi').map(o => ` · ${o.b} ${o.price}`).join('')}${u.opts.length === 1 ? ' · no Hard Rock / Novig price at this number' : ''}${mfTxt}${nve != null && nve >= .10 && nve <= .40 ? ` · Novig ${Math.round(nve * 100)}% under the other books’ fair: tracked, not proven` : ''}">${num}${aStar(c.p, u)} ${KLAB[u.k]} ${u.opts.map(o => o === b ? (o.u ? `<a class="lbk best" href="${esc(o.u)}" target="_blank" rel="noopener">${tx(o)} ↗</a>` : `<i class="lbk best">${tx(o)}</i>`) : `<i class="lbk">${tx(o)}</i>`).join('')}${flagged ? ` <b class="ledge">+${Math.round(u.edge * 100)}%</b>` : u.p != null ? ` <i class="lbk">ours ${Math.round(u.p * 100)}%</i>` : ''}${ladRungFit(u)}${mf ? ` <i class="lbk">fair ${Math.round(mf.f * 100)}%</i>` : ''}${nve != null && nve >= .10 && nve <= .40 ? ` <b class="lnv">NV +${Math.round(nve * 100)}% vs books</b>` : ''}${window.MKT && MKT.statMove ? MKT.statMove(c.g.id, c.p.n, u.k) : ''}${window.WATCH ? ' ' + WATCH.both({sport: 'nba', gid: String(c.g.id), name: c.p.n, kind: u.k, line: u.l, book: b.b, o: b.b === 'Kalshi' ? Math.round(kc >= .5 ? -100 * kc / (1 - kc) : 100 * (1 - kc) / kc) : Number(String(b.price).replace('+', '')), glab: `${c.g.away} @ ${c.g.home}`, start: c.g.start_utc || null, lab: `${c.p.n} ${n(u.l)}+ ${KLAB[u.k]}`}) : ''}${rungSlip(c.g, c.p, u)}</span>`;
}
document.addEventListener('change', e => { const t = e.target.closest('[data-lrk]'); if (!t) return;
  const k = t.dataset.lrk, x = LADC[k]; LADSEL[k] = +t.value; const w = document.createElement('div'); w.innerHTML = lruChip(x.c, x.u0); t.closest('.lru').replaceWith(w.firstElementChild); });
// a rung in a ladder category, for the screener (freeze mode only)
const regL = (g, p, u, cat) => { if (window.MKT && MKT.regLad) MKT.regLad({gid: String(g.id), start: g.start_utc, name: MKT.norm(p.n), kind: u.k, line: u.l, cat, ya: u.ya, p: u.p == null ? null : +u.p.toFixed(4)}); };
// Game picker above the ladders (owner 2026-11-01: 'scroll between game selections without having to scroll down on the
// whole slate'): pinned under the top bar, one tap shows that game with every player unfolded; the tap keeps the picker where it was
let LADG = '';
function ladRail(cards, games){
  const n = {}; cards.forEach(c => { n[c.g.id] = (n[c.g.id] || 0) + 1; });
  const G = games.filter(g => n[g.id]);
  if (G.length < 2) return '';
  return `<div class="lgr" role="group" aria-label="Pick a game">${[['', 'All', cards.length], ...G.map(g => [String(g.id), `${g.away} @ ${g.home}`, n[g.id]])].map(([id, t, k]) => `<button type="button" data-lgm="${esc(id)}" aria-pressed="${LADG === id}">${esc(t)} <i>${k}</i></button>`).join('')}</div>`;
}
document.addEventListener('click', e => { const b = e.target.closest('[data-lgm]'); if (!b) return;
  const r0 = b.closest('.lgr'), left = r0.scrollLeft, y = r0.getBoundingClientRect().top;
  LADG = b.dataset.lgm; render();
  const r = document.querySelector('.lgr'); if (!r) return;
  r.scrollLeft = left; window.scrollBy(0, r.parentElement.getBoundingClientRect().top - y); });
let LADM = '';
// ★ filter (owner 2026-11-07: 'filter by star on the ladder edge'): only rungs with a ★ (aStar); remembered on this device
let LADS = false;
try { LADS = localStorage.getItem('nba-lads') === '1'; } catch (e) {}
document.addEventListener('click', e => { const b = e.target.closest('[data-lads]'); if (!b) return;
  LADS = !LADS; try { localStorage.setItem('nba-lads', LADS ? '1' : '0'); } catch (x) {} render(); });
document.addEventListener('click', e => { const b = e.target.closest('[data-lmf]'); if (!b) return; LADM = b.dataset.lmf; render(); });
const ladMv = (c, u) => window.MKT && MKT.consKind ? MKT.consKind(c.g.id, c.p.n, u.k) : null;
// Selection tags (owner 2026-11-04: 'dont skip anything but add tags for all these criteria'; research/nba_ladder_factors.py +
// nba_ladder_drill.py: every Kalshi NBA rung Jan 2026 - Nov 2, inside our chance 15%+ over the ask + fee, by period). Display only:
// nothing is filtered or tracked differently. Green = up in every period there, red = down.
const LFIT = {
  k15: 'Our chance beats Kalshi’s ask + fee by 15%+: +7.6% on 2,331 rungs (40%+ edges +8.9%); edges 5-15% lost 7%. Picked Jan-Apr, held in the playoffs and 2026-27.',
  fg3m: 'Threes inside the 15%+ slice: +37% on 230, up in every period.',
  away: 'Away player inside the 15%+ slice: +12% on 1,646, up in every period (home +4%).',
  line: 'Game line 10+ inside the 15%+ slice: +16% on 989, up in every period.',
  t230: 'Game total 230+ inside the 15%+ slice: +18% on 1,458, up in every period.',
  t220: 'Game total under 220 inside the 15%+ slice: -10% on 773, down in every period -- low-scoring games cap the volume high rungs need.',
  m2432: 'Projected 24-32 minutes inside the 15%+ slice: +11% on 2,374, up in every period.',
  m24: 'Projected under 24 minutes inside the 15%+ slice: about flat overall, down in the playoffs and 2026-27.',
  start: 'Starter (tonight’s five, else started his last game) inside the 15%+ slice: +9% on 2,669, up in every period (bench +5%).',
};
const ltag = (cls, txt, tip) => `<span class="ladc ${cls}" title="${esc(tip)} Tracked study, not a tested bet rule.">${txt}</span>`;
function ladFit(c){
  const g = c.g, p = c.p, mins = p.s.vegas && p.s.vegas.min && p.s.vegas.min[MED], t = [];
  const starter = p.lu ? p.lu.startsWith('in') : p.ls === 1;
  if (starter) t.push(ltag('up', 'starter', LFIT.start));
  if (p.t === g.away) t.push(ltag('up', 'away', LFIT.away));
  if (mins >= 24 && mins <= 32) t.push(ltag('up', '24-32 min', LFIT.m2432));
  else if (mins < 24) t.push(ltag('dn', 'under 24 min', LFIT.m24));
  if (g.has_line && g.total != null){
    if (g.total >= 230) t.push(ltag('up', `O/U ${g.total}`, LFIT.t230));
    else if (g.total < 220) t.push(ltag('dn', `O/U ${g.total}`, LFIT.t220));
  }
  if (g.has_line && g.spread != null && Math.abs(g.spread) >= 10) t.push(ltag('up', `line ${Math.abs(g.spread)}`, LFIT.line));
  return t.join('');
}
// per rung: our chance 15%+ over Kalshi's ask + fee (the slice the study starts from), threes
// ★ = this player's archetype, this stat and this rung's best price fell in an archetype -> bet type -> odds range that made
// money on the Profit page (NBA straights by archetype, last 30 days, 20+ bets, won more than priced; owner 2026-11-07:
// 'a star to the profitable markets in the ladder edges'). Picked after the fact: a lead, not a rule.
let AODDS = null;
try { fetch('../markets/arch_odds_nba.json?t=' + Date.now(), {cache: 'no-store'}).then(r => r.ok ? r.json() : null).then(d => { if (d && d.cells){ AODDS = d.cells; render(); } }).catch(() => {}); } catch (e) {}
const ST_NM = {pts: 'Points', reb: 'Rebounds', ast: 'Assists', fg3m: 'Threes'};
const OBANDS = [[-99999, 99, 'minus money'], [100, 150, '+100–150'], [151, 350, '+151–350'], [351, 600, '+351–600'], [601, 800, '+601–800'],
                [801, 1200, '+801–1200'], [1201, 1500, '+1201–1500'], [1501, 2500, '+1501–2500'], [2501, 10000, '+2501–10000'], [10001, 1e9, 'over +10000']];
function aStar(p, u){
  if (!AODDS || !p.ar || !u.best) return '';
  const am = u.best.pay < 2 ? 0 : Math.round((u.best.pay - 1) * 100), band = (OBANDS.find(([lo, hi]) => am >= lo && am <= hi) || [])[2];
  const c = AODDS.find(x => x.a === p.ar && x.s === ST_NM[u.k] && x.b === (am === 0 ? 'minus money' : band));
  if (!c || c.n < 20 || c.r <= 0 || c.w <= c.e) return '';
  return ` <b class="lstar" title="${esc(`${p.ar} · ${ST_NM[u.k].toLowerCase()} · ${c.b}: ${c.w} won vs ${c.e.toFixed(1)} priced on ${c.n} straights, +${Math.round(c.r * 100)}% (Profit page, last 30 days). Picked after the fact: a lead, not a rule.`)}">★</b>`;
}
const ladRungFit = u => (u.p != null && u.cost && u.p / u.cost - 1 >= .15 ? ` <b class="lfit" title="${esc(LFIT.k15)}">15%+ K</b>` : '') + (u.k === 'fg3m' ? ` <b class="lfit" title="${esc(LFIT.fg3m)}">3PM</b>` : '');
// rungs shown on a player row (top 3 of the flagged set, by the move / star filters)
const ladShown = c => (LADM ? c.rungs.filter(u => ladMv(c, u) === LADM) : c.rungs).filter(u => !LADS || aStar(c.p, u)).slice(0, 3);
// every rung of his ladder with a ★: the flagged ones first, then the rest of the ladder (lower rungs in the dropdown)
const starRungs = c => [...c.rungs, ...(c.all || []).filter(u => !c.rungs.some(r => r.k === u.k && r.l === u.l))].filter(u => aStar(c.p, u));
// owner 2026-11-07: 'if not immediately visible provide the leg that the star would pop up on' -- when none of the shown rungs has a
// ★ but another rung of his ladder does, show that rung under the row (best first: flagged before the rest)
function starElse(c){
  const shown = ladShown(c);
  if (shown.some(u => aStar(c.p, u))) return '';
  const u = starRungs(c).find(x => !shown.some(y => y.k === x.k && y.l === x.l));
  return u ? `<div class="lstarx"><span class="small muted">★ on another rung:</span> ${lruChip(c, u)}</div>` : '';
}
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
      const rungs = all.map(ladPrice).filter(u => u.edge != null && u.edge >= LEDGE).sort((a, b) => b.edge - a.edge);
      if (!rungs.length) continue;
      rungs.forEach(u => regL(g, p, u, 'Ladder edge'));
      const R = DVP[p.o + '|' + p.pg] || {};
      cards.push({g, p, R, sts: [...new Set(rungs.map(u => u.k))].filter(st => (mx(p, st) || {}).v >= 1.07), rungs, all: window.MKT && MKT.ladderAll ? MKT.ladderAll(g.id, p.id, p.n, Object.keys(KLAB)).map(ladPrice) : rungs, best: rungs[0].edge});
    }
  }
  cards.sort((a, b) => b.best - a.best);
  const soft = c => c.sts.map(st => { const m = mx(c.p, st);
    return `<span class="ladc soft" style="${MG(m.v)}" title="${mxWhy(c.p, st, m)} (context only: the model already uses it)">${esc(c.p.o)} ${sgn(m.v)} ${KLAB[st]} vs ${esc(m.da ? (ARCH_PL[c.p.ar] || PG_PL[c.p.pg]) : PG_PL[c.p.pg])}</span>`; }).join('');
  const row = c => `<div class="lad"><div class="ladh"><b>${esc(c.p.n)}</b> <span class="small muted">${esc(c.p.t)} vs ${esc(c.p.o)} · ${esc(c.p.pos)} · ${f0(c.p.s.vegas.min[MED])} min</span>${soft(c)}${ladFit(c)}</div><div class="ladtg">${chips(c.p, c.g)}</div>
    <div class="ladr">${ladShown(c).map(u => lruChip(c, u)).join('')}</div>${starElse(c)}</div>`;
  if (LADM && !cards.some(c => c.rungs.some(u => ladMv(c, u) === LADM))) LADM = '';
  const nStar = cards.filter(c => starRungs(c).length).length;
  const cm = (LADM ? cards.filter(c => c.rungs.some(u => ladMv(c, u) === LADM)) : cards).filter(c => !LADS || starRungs(c).some(u => !LADM || ladMv(c, u) === LADM));
  if (!cm.some(c => String(c.g.id) === LADG)) LADG = '';
  const vis = LADG ? cm.filter(c => String(c.g.id) === LADG) : cm, lim = LADG ? 999 : 8;
  const empty = !anyLadder ? 'Kalshi’s NBA ladders are too thin to read yet (wide gaps between bid and ask); rungs show here once quotes tighten, usually on game day.'
    : !cards.length ? 'No higher rung beats its price by 5%+ right now. Prices move through the day; this updates with them.'
    : LADS && !cm.length ? 'No ladder edge has a ★ right now. Tap ★ only again to see them all.' : '';
  if (empty && !cards.length) return `<section class="panel pempty"><div class="phead"><h2>Ladder edges <span class="exp3">experimental</span></h2></div><p class="note">${!anyLadder ? 'No Kalshi ladders tight enough yet · usually fill in on game day.' : 'No rung beats its price by 5%+ right now · updates through the day.'}</p></section>`;
  return `<section class="panel"><div class="phead"><h2>Ladder edges <span class="exp3">experimental</span></h2><span class="small muted">${cards.length} player${cards.length === 1 ? '' : 's'}</span></div>
    ${empty ? `<p class="note" style="margin:0 0 6px">${empty}</p>` : ''}
    <details class="how"><summary>What this is</summary><p class="note">Higher Kalshi rungs (two or more lines above the one priced near 50/50) where our chance beats the best price by 5% or more, for players projected for 20+ minutes. Each rung shows Kalshi (K, fee included), Hard Rock’s alt line (HR) and Novig’s ladder (NV) at the same number; the bold one is the best price and its edge is the one shown (tap it to open the bet when the book gives a link). Tap a rung’s number to switch to a lower rung and check its prices; rungs outside the flagged set show our chance, with no edge claimed. Hard Rock’s alt lines refresh in the morning and hourly in the last two hours before tip. The green chip marks a soft matchup for that stat, as context: in our 2024-26 backtest players facing soft defenses beat our high projections no more often than anyone else, because the model already counts the matchup. Untested on NBA prices; every flagged rung is tracked on the Profit page. <b>Tags</b> come from a study of every Kalshi NBA rung since January (research only, nothing is filtered): green = up in every period among rungs where our chance beats Kalshi’s ask + fee by 15%+ (the <b>15%+ K</b> tag: starter, away, 24-32 projected minutes, game total 230+, line 10+, threes); red = down (game total under 220, under 24 minutes). Every rung here is already two or more above the 50/50 rung. Hover a tag for its record.</p></details>
    ${cards.length ? `<div class="ladsw" role="group" aria-label="Ladder edges view">${[['game', 'By game'], ['edge', 'Best first']].map(([k, l]) => `<button type="button" data-ladv="${k}" aria-pressed="${LADV === k}">${l}</button>`).join('')}</div><div class="ladsw" role="group" aria-label="Star filter"><button type="button" data-lads="1" aria-pressed="${LADS}" title="Only rungs whose archetype, stat and odds range made money in NBA straights by archetype on the Profit page (last 30 days, 20+ bets). Picked after the fact: a lead, not a rule.">★ only <i>${nStar}</i></button></div>${window.MKT && MKT.consBar ? MKT.consBar(cards, LADM) : ''}` : ''}
    <div class="ladbody">${ladRail(cm, [...D.games].sort((a, b) => new Date(a.start_utc) - new Date(b.start_utc)))}${LADV === 'game' ? byGame(vis, row) : `<div class="ladg">${vis.slice(0, lim).map(row).join('')}</div>${vis.length > lim ? `<details class="ladw"><summary>${vis.length - lim} more</summary><div class="ladg">${vis.slice(lim).map(row).join('')}</div></details>` : ''}`}</div></section>`;
}
// Threes board (owner 2026-11-09: 'a place to see all the recommended threes mispricing across the board for the day'):
// every Kalshi 3PM rung on today's slate (spread 5c or less, mid 3-97c) priced by the tracked threes rule (profit.py,
// research/threes_market.py): Kalshi's mid recalibrated (logit -.0794 + 1.0023 logit mid), moved .75 of the way to our
// threes (nba/threes.py) in log-odds. The pick = the player's rung with the best edge at Kalshi's ask + fee, 5-60% (what the
// Profit row 'NBA threes: one rung per player' grades); Hard Rock / Novig at the same number shown, with their edge on that chance.
const THR_W = .75, lgt = v => Math.log(v / (1 - v)), sgm = z => 1 / (1 + Math.exp(-z)), clp = (v, a, b) => Math.min(Math.max(v, a), b);
function threesBoard(){
  if (!(window.MKT && MKT.ladderAll)) return '';
  const now = Date.now(), rows = [];
  for (const g of D.games){
    if (g.start_utc && new Date(g.start_utc) <= now) continue;
    for (const p of D.players.filter(x => x.g === g.id && x.st !== 'Out')){
      const R = MKT.ladderAll(g.id, p.id, p.n, ['fg3m']).filter(u => u.p != null && u.ya - u.yb <= .05 && u.mid >= .03 && u.mid <= .97 && u.ya < .97)
        .map(u => { const mk = -.0794 + 1.0023 * lgt(clp(u.mid, .005, .995)), q = sgm(mk + THR_W * (lgt(clp(u.p, .002, .998)) - mk));
          const v = ladPrice(u), ek = q / u.cost - 1, eb = q * v.best.pay - 1;
          // every book (owner 2026-11-11: 'for NBA threes ladders take into account all books'): the other books' prices at this
          // number, and their fair (MKT.multiFair: each book's hold taken out, Kalshi's mid, the carried sharp fair; leaving out
          // the book you'd bet). agree = the rule's edge clears 5% AND your best price is no worse than every other book's fair
          const ob = Object.entries(MKT.booksAt(g.id, p.n, 'fg3m', u.l, true) || {}).filter(([b]) => b !== 'Hard Rock' && b !== 'Novig')
            .map(([b, x]) => ({b, o: x.o, pay: x.o > 0 ? 1 + x.o / 100 : 1 + 100 / -x.o})).sort((a, b) => b.pay - a.pay);
          const mf = MKT.multiFair ? MKT.multiFair(g.id, p.id, p.n, 'fg3m', u.l, v.best.b) : null, ef = mf ? mf.f * v.best.pay - 1 : null;
          return {...v, q, ek, eb, ob, mf, ef, agree: Math.max(ek, eb) >= .05 && Math.max(ek, eb) <= .6 && ef != null && ef >= 0 && mf.n >= 2}; }).sort((a, b) => a.l - b.l);
      if (!R.length) continue;
      const pick = R.filter(u => u.ek >= .05 && u.ek <= .6).sort((a, b) => b.ek - a.ek)[0] || null;
      const bk = R.filter(u => u.eb >= .05 && u.eb <= .6).sort((a, b) => b.eb - a.eb)[0] || null;
      rows.push({g, p, R, pick, bk, agree: R.some(u => u.agree), best: pick ? pick.ek : bk ? bk.eb : Math.max(...R.map(u => u.eb))});
    }
  }
  if (!rows.length) return `<section class="panel pempty"><div class="phead"><h2>Threes board <span class="exp3">experimental</span></h2></div><p class="note">No Kalshi threes ladders tight enough yet · they usually fill in on game day.</p></section>`;
  rows.sort((a, b) => (!!b.pick - !!a.pick) || (!!b.bk - !!a.bk) || (b.agree - a.agree) || b.best - a.best);
  const n = l => Math.round(l + .5), tx = o => `${o.b === 'Kalshi' ? 'K' : o.b === 'Hard Rock' ? 'HR' : 'NV'} ${o.price}`;
  const BKS = {DK: 'DK', FD: 'FD', MGM: 'MGM', Caesars: 'CZR', Fanatics: 'FAN', 'ESPN BET': 'ESPN', BetRivers: 'BR'};
  const chip = (r, u) => { const pk = r.pick === u, e = Math.max(u.ek, u.eb);
    return `<span class="lru${pk ? ' thpk' : ''}" title="${n(u.l)}+ threes: threes-rule chance ${Math.round(u.q * 100)}% (ours ${Math.round(u.p * 100)}%, Kalshi mid ${Math.round(u.mid * 100)}%) · Kalshi ${Math.round(u.cost * 100)}% with the fee → edge ${u.ek >= 0 ? '+' : '−'}${Math.abs(Math.round(u.ek * 100))}%${u.opts.filter(o => o.b !== 'Kalshi').map(o => ` · ${o.b} ${o.price}`).join('')}${u.ob.map(o => ` · ${o.b} ${o.o > 0 ? '+' : ''}${o.o}`).join('')}${u.mf ? ` · every other book’s fair ${Math.round(u.mf.f * 100)}% from ${Object.keys(u.mf.parts).join(', ')}${u.ef != null ? ` → ${u.best.b} ${u.ef >= 0 ? '+' : '−'}${Math.abs(Math.round(u.ef * 100))}% vs it` : ''}` : ' · no other book prices this number'}${pk ? ' · the pick the Profit page grades' : ''}${u.agree ? ' · ✓ the other books agree' : ''}"><b>${n(u.l)}+</b>${pk ? ' <b class="thtag">pick</b>' : ''}${u.agree ? ' <b class="thok">✓ books</b>' : ''} ${u.opts.map(o => o === u.best ? (o.u ? `<a class="lbk best" href="${esc(o.u)}" target="_blank" rel="noopener">${tx(o)} ↗</a>` : `<i class="lbk best">${tx(o)}</i>`) : `<i class="lbk">${tx(o)}</i>`).join('')}${u.ob.length ? ` <i class="lbk thob">${esc(BKS[u.ob[0].b] || u.ob[0].b)} ${u.ob[0].o > 0 ? '+' : ''}${u.ob[0].o}${u.ob.length > 1 ? ` <small>+${u.ob.length - 1}</small>` : ''}</i>` : ''} <i class="lbk">${Math.round(u.q * 100)}%</i>${u.mf ? ` <i class="lbk thmf">books ${Math.round(u.mf.f * 100)}%</i>` : ''}${e >= .05 && e <= .6 ? ` <b class="ledge">+${Math.round(e * 100)}%${u.ek < .05 ? ` ${u.best.b === 'Hard Rock' ? 'HR' : 'NV'}` : ''}</b>` : ''}${window.WATCH ? ' ' + WATCH.both({sport: 'nba', gid: String(r.g.id), name: r.p.n, kind: 'fg3m', line: u.l, book: u.best.b, o: u.best.b === 'Kalshi' ? Math.round(u.cost >= .5 ? -100 * u.cost / (1 - u.cost) : 100 * (1 - u.cost) / u.cost) : Number(String(u.best.price).replace('+', '')), glab: `${r.g.away} @ ${r.g.home}`, start: r.g.start_utc || null, lab: `${r.p.n} ${n(u.l)}+ threes`}) : ''}${rungSlip(r.g, r.p, u, u.q)}</span>`; };
  const med = p => p.s.vegas && p.s.vegas.fg3m ? p.s.vegas.fg3m[MED] : null;
  const row = r => `<div class="lad"><div class="ladh"><b>${esc(r.p.n)}</b> <span class="small muted">${esc(r.p.t)} vs ${esc(r.p.o)} · ${esc(r.g.start || '')}${med(r.p) != null ? ` · our median ${f0(med(r.p))} 3PM` : ''}</span>${r.p.ar ? ` <span class="ladc">${esc(r.p.ar)}</span>` : ''}</div>
    <div class="ladr">${(THA ? r.R : r.R.filter(u => u === r.pick || u === r.bk || u.agree || Math.max(u.ek, u.eb) >= .05 || Math.abs(u.mid - .5) < .2).slice(0, 4)).map(u => chip(r, u)).join('')}</div></div>`;
  const P = rows.filter(r => r.pick), B = rows.filter(r => !r.pick && r.bk), rest = rows.filter(r => !r.pick && !r.bk);
  return `<section class="panel"><div class="phead"><h2>Threes board <span class="exp3">experimental</span></h2><span class="small muted">${P.length} pick${P.length === 1 ? '' : 's'} · ${B.length} more at Hard Rock / Novig · ${rows.filter(r => r.agree).length} where every book agrees · ${rows.length} players</span></div>
    <details class="how"><summary>What this is</summary><p class="note">Every Kalshi threes rung on today’s slate, priced by the tracked threes rule: Kalshi’s own price (slightly recalibrated) moved three-quarters of the way to our threes model, which beat the price on Kalshi’s 3PM ladders since last November in every period (not proven: the edge was small and one test). <b>Pick</b> = each player’s rung with the best edge at Kalshi’s ask with the fee, 5–60%: that is what the Profit row ‘NBA threes: one rung per player, 5%+ edge’ grades. Hard Rock (HR) and Novig (NV) at the same number are shown on the same chance; the bold price is the best and its edge is shown (a book name after the edge = only that book clears 5%). The % chip is the rule’s chance. <b>Every book</b>: the next price (e.g. DK +250, with +N more books) is the best other sportsbook at that number, for reference only; <b>books N%</b> is every other book’s fair at that number (each book’s margin taken out, Kalshi’s mid, the sharp fair carried up; the book you would bet left out). <b>✓ books</b> = the rule clears 5% and your best price is no worse than that fair from 2+ sources. Not tested: in the Nov 11 books study a broad fair like this flagged fewer false edges than the sharp three alone. Hover a rung for every book’s price.</p></details>
    <div class="ladsw" role="group" aria-label="Rungs shown"><button type="button" data-tha="1" aria-pressed="${THA}">Every rung</button></div>
    ${P.length ? `<div class="ladg">${P.map(row).join('')}</div>` : '<p class="note">No rung clears 5% at Kalshi right now · updates with prices.</p>'}
    ${B.length ? `<details class="ladw"${P.length ? '' : ' open'}><summary>${B.length} more where only Hard Rock / Novig clear 5%</summary><div class="ladg">${B.map(row).join('')}</div></details>` : ''}
    ${rest.length ? `<details class="ladw"><summary>${rest.length} other player${rest.length === 1 ? '' : 's'} with a threes ladder</summary><div class="ladg">${rest.map(row).join('')}</div></details>` : ''}</section>`;
}
let THA = false;
try { THA = localStorage.getItem('nba-tha') === '1'; } catch (e) {}
document.addEventListener('click', e => { const b = e.target.closest('[data-tha]'); if (!b) return;
  THA = !THA; try { localStorage.setItem('nba-tha', THA ? '1' : '0'); } catch (x) {} render(); });
// Ladder edges grouped by game (owner 2026-10-23: "let me see ladder edges by game"): games in start order, each game's
// players best edge first, five shown and the rest folded; the view is remembered on this device (nba-ladv).
let LADV = 'game';
try { LADV = localStorage.getItem('nba-ladv') || 'game'; } catch (e) {}
function byGame(cards, row){
  const by = new Map();
  for (const c of cards) (by.get(c.g.id) || by.set(c.g.id, []).get(c.g.id)).push(c);
  const games = D.games.filter(g => by.has(g.id)).sort((a, b) => new Date(a.start_utc) - new Date(b.start_utc));
  const cap = LADG ? 999 : 5;
  return games.map(g => { const L = by.get(g.id).sort((a, b) => b.best - a.best);
    return `<div class="ladgm"><div class="ladgh"><b>${esc(g.away)} @ ${esc(g.home)}</b> <span class="small muted">${esc(g.start || '')} · ${L.length} player${L.length === 1 ? '' : 's'} · best +${Math.round(L[0].best * 100)}%</span></div>
      <div class="ladg">${L.slice(0, cap).map(row).join('')}</div>${L.length > cap ? `<details class="ladw"><summary>${L.length - cap} more in this game</summary><div class="ladg">${L.slice(cap).map(row).join('')}</div></details>` : ''}</div>`; }).join('');
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
  return card || `<section class="panel pempty"><div class="phead"><h2>Parlays by payout (your books)</h2></div><p class="note">No parlay with an edge on every leg yet · props mostly post on game day.</p></section>`;
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
        if (MKT.legOk && !MKT.legOk({kind: st, price: u.best.o}, 'nba')) continue;  // the one leg rule (25-55%, no points)
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
  for (const g of D.games){ if (g.start_utc && new Date(g.start_utc) <= now) continue; const L = nbaPathLegs(g); if (L.length) per.push(L.slice(0, 3));
    if (MKT.regPath) L.forEach(x => MKT.regPath({gid: String(g.id), start: g.start_utc, name: MKT.norm(x.p.n), kind: x.st, line: x.u.l, p: +x.q.toFixed(4), price: x.u.best.o, book: x.u.best.k,
      f: Object.keys(x.F).filter(k => x.F[k]), why: Object.entries(x.F).filter(([, t]) => t).map(([k, t]) => `${k} · ${String(t).replace(/<[^>]*>/g, '').replace(/&amp;/g, '&')}`)})); }
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
        if (MKT.legOk && !MKT.legOk({kind: u.k, price: u.cost}, 'nba')) continue;   // the one leg rule
        const m = mx(p, u.k), soft = m && m.v >= 1.06; if (!soft && tk !== u.k) continue;
        regL(g, p, u, 'Ladder longshot');
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
  if (window.MKT && MKT.FREEZE) try { ladderCard(); } catch (e) {}      // the freeze loads #parlays: ladder rungs still register for the screener
  return `<div class="pstack">${(window.MKT && MKT.dailyCard ? MKT.dailyCard() : '') + nbaPathLongshots() + nbaLadderLongshots() + tierParlays()}</div>`;
}
// Ladders tab (owner 2026-11-11: 'move the ladder edge ... and threes out side of the parlays tab and give them their own tab')
function laddersView(){ return `<div class="pstack">${ladderCard() + threesBoard()}</div>`; }
// + on every ladder / threes rung (owner 2026-11-11: 'make all bets in there able to be added to the parlay builder even if i
// switch to a different leg'): the leg is the rung shown (a dropdown switch re-renders the chip with its own +); another rung of
// the same player-stat replaces it in the slip. Prices: every book at that number (Kalshi with its fee) for 'Each book pays'
const amK = c => Math.round(c >= .5 ? -100 * c / (1 - c) : 100 * (1 - c) / c);
function rungSlip(g, p, u, prob){
  if (!window.SLIP || !u) return '';
  const px = {};
  for (const o of u.opts || []) px[o.b] = o.b === 'Kalshi' ? amK(u.cost) : Number(String(o.price).replace('+', ''));
  const b = (u.best && u.best.b) || 'Kalshi';
  return ' ' + SLIP.btn({id: `nba|${g.id}|${p.k}|${u.k}|over|${u.l}|vegas`, sport: 'nba', g: g.id, glab: `${g.away} @ ${g.home}`, lab: `${p.n} ${Math.round(u.l + .5)}+ ${SLIP_LAB[u.k] || u.k}`,
    p: prob != null ? prob : u.p, side: 'over', stat: u.k, line: u.l, pk: p.k, model: 'vegas', draws: `nba/draws/${g.id}.json`, book: b, o: px[b], px});
}
function render(){
  for (const b of document.querySelectorAll('.tabs button')) b.setAttribute('aria-selected', b.dataset.view === state.view);
  document.getElementById('main').innerHTML = state.view === 'board' ? board() : state.view === 'dvp' ? dvpView() : state.view === 'parlays' ? parlaysView() : state.view === 'ladders' ? laddersView() : gameView();
  const cur = document.querySelector('.rail .game-btn[aria-current="true"]');
  if (cur){ const r = cur.parentElement, rr = r.getBoundingClientRect(), cr = cur.getBoundingClientRect(); if (r.scrollWidth > r.clientWidth && (cr.left < rr.left || cr.right > rr.right)) r.scrollLeft += cr.left - rr.left - 12; if (r.scrollHeight > r.clientHeight && (cr.top < rr.top || cr.bottom > rr.bottom - 64)) r.scrollTop += cr.top - rr.top - (r.clientHeight - cr.height) / 2; }
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

document.addEventListener('click', e => { const b = e.target.closest('[data-bpin]'); if (!b) return; BPIN = !BPIN; render(); });
