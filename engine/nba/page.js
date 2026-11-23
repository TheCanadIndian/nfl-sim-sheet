const D = JSON.parse(document.getElementById('data').textContent);
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const f0 = v => v == null ? '–' : Math.round(v).toString();
const f1 = v => v == null ? '–' : (Math.round(v*10)/10).toFixed(1);
const pct = p => p == null ? '–' : Math.round(p*100) + '%';
const american = p => { p = Math.min(Math.max(p,1e-4),1-1e-4); const o = p>=.5 ? -100*p/(1-p) : 100*(1-p)/p; return (o>0?'+':'') + Math.round(o); };
const [MEAN, P10, P25, MED, P75, P90] = [0,1,2,3,4,5];
const LAB = {min:'Minutes', pts:'Points', reb:'Rebounds', ast:'Assists', fg3m:'Threes', pra:'Pts+Reb+Ast', stl:'Steals', blk:'Blocks', tov:'Turnovers'};
const COLS = ['min','pts','reb','ast','fg3m','pra'];
const state = {view: ['#board','#dvp','#parlays','#ladders','#mu'].includes(location.hash) ? location.hash.slice(1) : 'games', game: D.games[0]?.id, gm: {}, bstat: 'pts', dstat: 'pts', open: null, ostat: 'pts', oline: null, okey: null};
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
// next man up chips: players whose ROLE changes because a regular (20+ min) is out -- moving into the starting five, or not a starter
// and gaining 4+ minutes and 20%+ over his usual role -- the top 3 a team (the model spreads the rest of the minutes in proportion,
// so established starters' bumps are just that scaling and stay unchipped)
const lastNm = n => { const w = String(n || '').replace(/\s+(Jr\.?|Sr\.?|II|III|IV|V)$/i, '').trim().split(/\s+/); return w[w.length - 1] || n; };
let NMOK = null;
function nmOk(p){
  if (!NMOK){ NMOK = new Set(); const by = {};
    for (const q of D.players){ if (!q.nm || !Array.isArray(q.nm)) continue;
      const gm_ = D.games.find(x => String(x.id) === String(q.g));
      if (!(gm_ && gm_.out && (gm_.out[q.t] || []).some(o => (o.m || 0) >= 20))) continue;
      const [gn, us] = q.nm, mv = q.lu && q.lu.startsWith('in');
      if (!(mv || (q.ls !== 1 && gn >= 4 && gn / Math.max(us, 1) >= .2))) continue;
      (by[q.g + '|' + q.t] = by[q.g + '|' + q.t] || []).push(q); }
    for (const L of Object.values(by)) L.sort((a, b) => b.nm[0] - a.nm[0]).slice(0, 3).forEach(q => NMOK.add(q.k)); }
  return NMOK.has(p.k);
}
function chips(p, g){
  if (g && typeof g === 'object') g = g.id;                // the ladder cards pass the game itself (crashed the Ladders tab, 2026-11-16)
  let h = '';
  if (p.st && p.st !== 'Active') h += `<span class="tag q" title="${esc(p.st + (p.note ? ': ' + p.note : ''))}">${p.st === 'Day-To-Day' ? 'DTD' : p.st === 'Questionable' ? 'Q' : esc(p.st)}</span>`;
  if (p.lu && !(p.lu.startsWith('in') && nmOk(p))){ const [mv, src] = p.lu.split('|'), off = src === 'official';     // a move into the five because someone's out: the next-man-up chip says it
    h += mv === 'in' ? `<span class="lean g2" title="${off ? 'In tonight’s official starting five' : 'Projected to start tonight (our lineup from ESPN’s depth chart, minus anyone out)'}; came off the bench last game. Starting raises his minutes about 20% (2025-26 test), ${off ? 'fully applied' : 'applied at 70% until the official lineup posts'}.">▲ starts</span>`
      : `<span class="lean r2" title="${off ? 'Not in tonight’s official starting five' : 'Projected to come off the bench tonight'}; started last game. Coming off the bench cuts his minutes about 13% (2025-26 test), ${off ? 'fully applied' : 'applied at 70% until the official lineup posts'}.">▼ bench</span>`; }
  if (nmOk(p)){                                            // next man up (owner 2026-11-16): a role change because a regular teammate is out
    const gm_ = D.games.find(x => String(x.id) === String(g)), outs = (gm_ && gm_.out ? gm_.out[p.t] || [] : []).filter(o => (o.m || 0) >= 20).sort((a, b) => (b.m || 0) - (a.m || 0));
    const who = outs.length ? lastNm(outs[0].n) + (outs.length > 1 ? ` +${outs.length - 1}` : '') : 'teammate', mv = p.lu && p.lu.startsWith('in');
    h += `<span class="lean g2" title="Next man up: ${esc(outs.map(o => `${o.n} (${Math.round(o.m)} min a game)`).join(', '))} out tonight. ${mv ? (p.lu.endsWith('official') ? 'In tonight’s official starting five; ' : 'Projected to start tonight (applied at 70% until the official lineup posts); ') : ''}he projects ${f1(p.nm[1] + p.nm[0])} minutes vs ${f1(p.nm[1])} in his usual role (+${f1(p.nm[0])}). His points / rebounds / assists rise with the minutes (per-minute rates held steady when teammates sat, 2023-27 test). Already in his projection.">▲ ${mv ? 'starts ' : ''}+${f1(p.nm[0])} min · ${esc(who)} out</span>`; }
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
// defense rating (nba/defrate.py, owner 2026-11-14: 'lets build a rating system for the defense' -> 'show all'): how far tonight's
// defense moves his over away from the market price, in points at a 50/50 line, from the defense findings that held up against the
// market (DvP, opponent rest / back-to-back / travel, the opponent's usual rim protector or 3-and-D starter out). Grades T tough
// (<= -3), t slightly tough, n neutral, s slightly soft, S soft (+3+). Unseen tests: tough ran 3-5 pts under the price; no grade
// beats the price after the vig -- a reason to skip an over, not to bet one.
const DRG = {T: ['tough', 'drg-tough'], t: ['slightly tough', 'drg-stough'], n: ['neutral', 'drg-neutral'], s: ['slightly soft', 'drg-ssoft'], S: ['soft', 'drg-soft']};   // distinct names: the page renders in quirks mode, where class names ignore case
function drWhy(p, st){
  const g = D.games.find(x => x.id === p.g), dd = g && g.dd ? g.dd[p.o] : null, m = mx(p, st), R = [];
  if (m) R.push(`DvP ${sgn(m.v)} ${KLAB[st]} vs ${m.da && m.da !== m.dp ? (ARCH_PL[p.ar] || PG_PL[p.pg]) : PG_PL[p.pg]}${m.v <= .92 ? ' (tough)' : ''}`);
  if (dd){
    if (dd.r != null && dd.r >= 3) R.push(`${p.o} rested ${Math.round(dd.r)} days`);
    if (dd.r != null && dd.r < 1.6) R.push(`${p.o} on the 2nd night of a back-to-back`);
    if (dd.mi != null && dd.mi >= 1000) R.push(`${p.o} flew ${dd.mi.toLocaleString()} mi`);
    if (dd.rim && dd.rim.length) R.push(`${p.o} rim protector out (${dd.rim.join(', ')}; the market over-adjusts)`);
    if (dd.d3 && dd.d3.length) R.push(`${p.o} 3-and-D starter out (${dd.d3.join(', ')}; the market over-adjusts)`);
  }
  return R;
}
function drChip(p, st, lab){
  const x = p.dr && p.dr[st]; if (!x) return '';
  const [v, gr] = x, G = DRG[gr] || DRG.n, pv = `${v > 0 ? '+' : v < 0 ? '−' : ''}${Math.abs(v).toFixed(1)}`;
  const tip = `Defense rating, ${KLAB[st]} overs: ${G[0]}, ${pv} pts vs the market price (at a 50/50 line). Why: ${drWhy(p, st).join(' · ') || 'nothing unusual'}. Built from the defense findings that held up against the market and tested on data it never saw: tough matchups ran 3-5 pts under the price; no grade beat the price after the vig, so read it as a reason to skip an over, not to bet one. Experimental.`;
  return lab === 'card' ? `<span class="drc ${G[1]}" title="${esc(tip)}">D · <i>${SL[st]}</i> ${G[0]} <b>${pv}</b></span>`
    : `<span class="drc ${G[1]}" title="${esc(tip)}"><i>${SL[st]}</i> <b>${pv}</b></span>`;
}
function drLine(p){
  if (!p.dr) return '';
  const L = ['pts', 'reb', 'ast', 'fg3m'].filter(st => p.dr[st] && vol(p, st));
  return L.length ? `<div class="drw"><span class="drh" title="Defense rating for his overs tonight: points vs the market price. Red = tough (overs ran 3-5 pts under the price on unseen data), outlined = slight, grey = neutral, green = soft. Hover a chip for why.">D rating</span>${L.map(st => drChip(p, st)).join('')}</div>` : '';
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
  const rows = ps.map(p => `<tr class="${state.open === p.k ? 'opened' : ''}"><td class="l name"><button class="plink${state.open === p.k ? ' open' : ''}" data-open="${esc(p.k)}"><span class="caret">${state.open === p.k ? '▾' : '▸'}</span>${esc(p.n)}</button>${chips(p, g.id)}${mxLine(p)}${drLine(p)}${usageLine(p)}</td><td class="l"><span class="pos">${esc(p.pos)}${p.start ? '' : ''}</span></td>${COLS.map(c => cell(p, c, g.id)).join('')}</tr>`
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
  const over = rows.flatMap(r => [['pts', r.pts, 'PTS'], ['reb', r.reb, 'REB'], ['ast', r.ast, 'AST'], ['fg3m', r.f3m, '3PM']].map(([k, v, lab]) => { const l = LN(r, k); return l != null && v > l ? `<span class="bxoc">${esc(r.p.n.replace(/\s+(Jr\.?|Sr\.?|II|III|IV|V)$/, '').split(' ').slice(-1)[0])} ${lab} <b>${v}</b> <i>line ${l}</i>${ladBoxTags(g, r.p, k)}</span>` : ''; }).filter(Boolean));
  const q = p => p.st && p.st !== 'Active' ? ` <span class="tag q" title="${esc(p.st)}">${p.st === 'Questionable' ? 'Q' : p.st === 'Day-To-Day' ? 'DTD' : esc(p.st)}</span>` : '';
  const tr = (r, i) => `${i === 5 ? '<tr class="bxsep"><td class="l" colspan="11">Bench</td></tr>' : ''}<tr><td class="l"><b>${esc(r.p.n)}</b> <span class="small muted">${esc(r.p.pos)}</span>${q(r.p)}</td><td>${r.min}</td><td class="bxp">${ov(r, 'pts', r.pts)}</td><td>${ov(r, 'reb', r.reb)}</td><td>${ov(r, 'ast', r.ast)}</td><td>${r.stl}</td><td>${r.blk}</td><td>${r.tov}</td><td>${r.fgm}-${r.fga}</td><td>${ov(r, 'fg3m', r.f3m)}-${r.f3a}</td><td>${r.ftm}-${r.fta}</td></tr>`;
  return `<div class="tw"><table class="t bx"><thead><tr><th class="l">${esc(team)} · starters</th><th>MIN</th><th>PTS</th><th>REB</th><th>AST</th><th>STL</th><th>BLK</th><th>TOV</th><th>FG</th><th>3P</th><th>FT</th></tr></thead><tbody>
    ${rows.map(tr).join('')}<tr class="bxt"><td class="l"><b>Team</b></td><td>${T('min')}</td><td class="bxp">${T('pts')}</td><td>${T('reb')}</td><td>${T('ast')}</td><td>${T('stl')}</td><td>${T('blk')}</td><td>${T('tov')}</td><td>${T('fgm')}-${T('fga')}</td><td>${T('f3m')}-${T('f3a')}</td><td>${T('ftm')}-${T('fta')}</td></tr></tbody></table></div>${over.length ? `<div class="bxos"><span class="eyebrow">${esc(team)} above the books’ line</span>${over.join('')}</div>` : ''}`;
}
/* ---------------- shot maps (owner 2026-11-23: 'NBA shot map per player per team' -> 'defense and offense heatmaps like in nhl'
   -> 'have the player list next to the shotmap so I dont have to scroll up and down' -> 'the heat map looks fairly blurry can I also
   look at misses vs makes' -> 'both offense and defense' -> 'maybe be able to group by similar player archetype when viewing on
   defense'): nba/shotmap.py -> markets/shotmap_nba.json, loaded the first time the Shot map view opens. This season's attempts;
   display only (no number on the page uses it). ---------------- */
let NSM = null, NSM_ST = '';
function nsmLoad(){
  if (NSM || NSM_ST) return; NSM_ST = 'loading';
  try { fetch('../markets/shotmap_nba.json?t=' + Date.now(), {cache: 'no-store'}).then(r => r.ok ? r.json() : null)
    .then(d => { NSM = d && d.teams ? d : null; NSM_ST = NSM ? 'ok' : 'none'; render(); }).catch(() => { NSM_ST = 'none'; render(); }); } catch (e) { NSM_ST = 'none'; }
}
const nsLS = (k, ok, dflt) => { try { const v = localStorage.getItem(k); return ok.includes(v) ? v : dflt; } catch (e) { return dflt; } };
let NSMODE = nsLS('nba-smode', ['ov', 'off', 'def'], 'ov');   // overlay / where they shoot / where it's allowed
let NSMET = nsLS('nba-smet', ['att', 'fg'], 'att');           // attempts / makes vs misses
let NSAR = '', NSDF = 'all';                                  // defense view: shooter archetype ('' = all); a player's dots: all / made / miss
let NSPV = nsLS('nba-spv', ['spots', 'dots'], 'spots');      // a picked player: his spots (one circle a square) or every shot
const NSMP = {}, NSMC = {};                                   // team -> picked player id / tapped cell 'r:c'
const NSZ = [['rim', 'At the rim', '#c92a2a'], ['paint', 'Paint', '#f08c00'], ['mid', 'Mid-range', '#ffe066'], ['c3', 'Corner 3', '#99e9f2'], ['ab3', 'Above-break 3', '#4c6ef5']];
const NSB64 = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_';
const nsX = x => ((x + .5) * 6).toFixed(1), nsY = y => ((y + 4.5) * 6).toFixed(1);     // ft -> svg (6 px a foot; basket at the top)
const nsPc = v => (v >= 1 ? '+' : '−') + Math.abs(Math.round((v - 1) * 100)) + '%';
const nsOrd = n => { const s_ = ['th', 'st', 'nd', 'rd'], v = n % 100; return n + (s_[(v - 20) % 10] || s_[v] || s_[0]); };
const nsF = v => v == null ? '–' : (v * 100).toFixed(0) + '%';
const NSDC = new Map();
function nsDec(s){                                             // a packed grid -> 12 x 17 ratios (null = blank)
  if (!NSDC.has(s)){ const g = [];
    for (let r = 0; r < 12; r++){ const row = []; for (let c = 0; c < 17; c++){ const i = (r * 17 + c) * 2, v = NSB64.indexOf(s[i]) * 64 + NSB64.indexOf(s[i + 1]); row.push(v >= 4095 || v < 0 ? null : v / 100); } g.push(row); }
    NSDC.set(s, g); }
  return NSDC.get(s);
}
const nsLast = n => { const w = String(n || '').replace(/\s+(Jr\.?|Sr\.?|II|III|IV|V)$/i, '').trim().split(/\s+/); return w[w.length - 1] || n; };
const nsArLab = a => a === 'Scoring guard/wing' ? 'scoring guards / wings' : a === '3-and-D role player' ? '3-and-D role players' : a.toLowerCase() + 's';
function nsZoneAt(r, c){                                       // the zone a cell's centre falls in (shotmap.zone_of)
  const x = c * 3 + 1, y = r * 3 - 3, dx = x - 25, d = Math.hypot(dx, y - 1.25);
  if (Math.abs(dx) >= 22 && y <= 10.2) return 'c3';
  if (d >= 23.75) return 'ab3';
  return d <= 4 ? 'rim' : Math.abs(dx) <= 8 && y <= 15 ? 'paint' : 'mid';
}
function nsFill(v, w){
  if (v == null) return 'none';
  if (NSMET === 'fg') return v >= 1 ? `rgba(52,210,122,${(Math.min(1, (v - 1) / .25) * .95 * w).toFixed(3)})` : `rgba(240,72,72,${(Math.min(1, (1 - v) / .25) * .9 * w).toFixed(3)})`;
  return v >= 1 ? `rgba(255,99,71,${(Math.min(1, (v - 1) / .4) * .95 * w).toFixed(3)})` : `rgba(70,150,255,${(Math.min(1, (1 - v) / .3) * .85 * w).toFixed(3)})`;
}
function nsDots(s){
  let out = '';
  for (let i = 0; i + 1 < s.length; i += 2){
    let v = NSB64.indexOf(s[i]) * 64 + NSB64.indexOf(s[i + 1]); const made = v % 2; v = (v - made) / 2;
    const x = v % 51, y = Math.floor(v / 51) - 4;
    if (y > 31 || (NSDF === 'made' && !made) || (NSDF === 'miss' && made)) continue;
    out += made ? `<circle cx="${nsX(x)}" cy="${nsY(y)}" r="2.9" class="nsm-dm"/>` : `<circle cx="${nsX(x)}" cy="${nsY(y)}" r="2.1" class="nsm-dx"/>`;
  }
  return out;
}
// a picked player's spots (owner 2026-11-23: 'make the NBA player shot overlay easier to visualize on the heat map right now I can barely
// tell if the player shoots in a good location'): his last 300 attempts grouped by square, one circle each, sized by how often he shoots
// there and FILLED with the map's colour at that square (strong colour = the map rates his spot strongly; a pale circle = about average),
// the map faded behind; `fit` = the map's value averaged over his attempts (how favourable his spots are on the map shown)
const nsSpFill = v => { if (v == null) return 'rgba(255,255,255,.10)'; const fg = NSMET === 'fg', d = v - 1, a = Math.min(1, Math.abs(d) / (fg ? .15 : .25));
  return `rgba(${d >= 0 ? (fg ? '52,210,122' : '255,99,71') : (fg ? '240,72,72' : '70,150,255')},${(.1 + .9 * a).toFixed(2)})`; };
function nsSpots(s, vals){
  const cell = {};
  for (let i = 0; i + 1 < s.length; i += 2){
    let v = NSB64.indexOf(s[i]) * 64 + NSB64.indexOf(s[i + 1]); const made = v % 2; v = (v - made) / 2;
    const x = v % 51, y = Math.floor(v / 51) - 4; if (y > 31) continue;
    const r = Math.floor((y + 4) / 3), c = Math.min(16, Math.floor(x / 3)), k = r + ':' + c;
    const e = cell[k] = cell[k] || {r, c, n: 0, m: 0}; e.n++; e.m += made;
  }
  const C = Object.values(cell), mx = Math.max(1, ...C.map(x => x.n));
  let svg = '', w = 0, sv = 0, up = 0, dn = 0;
  for (const x of C.sort((a, b) => b.n - a.n)){
    const v = vals[x.r] && vals[x.r][x.c];
    svg += `<circle cx="${x.c * 18 + 9}" cy="${x.r * 18 + 9}" r="${(3 + 6.5 * Math.sqrt(x.n / mx)).toFixed(1)}" class="nsm-sp" style="fill:${nsSpFill(v)}"/>`;
    if (v != null){ w += x.n; sv += x.n * v; if (v >= 1.05) up += x.n; else if (v <= .95) dn += x.n; }
  }
  return {svg, cell, fit: w ? sv / w : null, up: w ? up / w : 0, dn: w ? dn / w : 0};
}
function nsSvg(team, vals, dots, pick, dim){
  const DN = NSM.lg.d, dmax = Math.max(...DN.flat().filter(x => x != null));
  let cells = '', hits = '';
  for (let r = 0; r < 12; r++) for (let c = 0; c < 17; c++){
    const v = vals[r] && vals[r][c]; if (v == null) continue;
    const w = DN[r][c] == null ? .45 : Math.max(.45, Math.min(1, Math.sqrt(DN[r][c] / dmax) * 1.4));
    cells += `<rect x="${c * 18 + 1}" y="${r * 18 + 1}" width="16" height="16" rx="2.5" fill="${nsFill(v, w)}"/>`;      // sharp tiles (owner: 'fairly blurry')
    hits += `<rect x="${c * 18}" y="${r * 18}" width="18" height="18" class="nsm-hit${pick === r + ':' + c ? ' on' : ''}" data-nsc="${esc(team)}:${r}:${c}"/>`;
  }
  const R = 23.75 * 6, cx = nsX(25), cy = nsY(1.25);
  return `<svg class="nsm-svg" viewBox="0 0 306 216" role="img" aria-label="${esc(team)} shot map">
    <g${dim ? ' opacity=".35"' : ''}>${cells}</g>
    <g class="nsm-court" fill="none">
      <rect x="0.5" y="${nsY(-4)}" width="305" height="${(216 - nsY(-4) - .5).toFixed(1)}"/>
      <rect x="${nsX(17)}" y="${nsY(-4)}" width="${16 * 6}" height="${19 * 6}"/>
      <circle cx="${cx}" cy="${nsY(15)}" r="36"/>
      <path d="M${(+cx - 24).toFixed(1)},${cy} A24,24 0 0 0 ${(+cx + 24).toFixed(1)},${cy}"/>
      <line x1="${nsX(22)}" y1="${nsY(0)}" x2="${nsX(28)}" y2="${nsY(0)}" class="bb"/><circle cx="${cx}" cy="${cy}" r="4.5" class="hoop"/>
      <path d="M${nsX(3)},${nsY(-4)} L${nsX(3)},${nsY(10.2)} A${R},${R} 0 0 0 ${nsX(47)},${nsY(10.2)} L${nsX(47)},${nsY(-4)}" class="arc"/>
    </g>${dots ? `<g>${dots}</g>` : ''}<g>${hits}</g></svg>`;
}
const nsBar = z => `<span class="nsm-zb" aria-hidden="true">${NSZ.map(([k, , col]) => `<i style="flex:${(z[k] || [0])[0]};background:${col}"></i>`).join('')}</span>`;
// a zone's FG% pulled toward a baseline (60 attempts) over the baseline
const nsZfg = (z, base) => z[1] == null || base == null ? 1 : ((z[1] * z[2] + base * 60) / (z[2] + 60)) / base;
// a player's zones vs what tonight's defense allows there: the defense's FG% by zone pulled toward the league (60 shots), x 2 or 3,
// weighted by his zone mix, over the league's points per shot on the same mix. Untested; display only.
function nsFit(pz, def){
  const L = NSM.lg.z; let a = 0, b = 0;
  for (const [k] of NSZ){
    const w = (pz[k] || [0])[0], m = k === 'c3' || k === 'ab3' ? 3 : 2, lf = L[k][1];
    a += w * lf * nsZfg(def.zd[k] || [0, null, 0], lf) * m; b += w * lf * m;
  }
  return b > 0 ? a / b : null;
}
function nsBlock(off, def, g){
  const O = NSM.teams[off], F = NSM.teams[def], L = NSM.lg.z, fg = NSMET === 'fg';
  if (!O || !F) return `<div class="nsm-b"><h3>${esc(off)} offense <small>vs ${esc(def)} defense</small></h3><div class="empty">No shot data yet.</div></div>`;
  // the defense side: every shooter, or one archetype (defense view only) against how that archetype shoots league-wide
  const AR = NSMODE === 'def' && NSAR && NSM.arch && NSM.arch[def] ? NSM.arch[def][NSAR] : null, AL = AR ? NSM.lg.arch[NSAR].z : L;
  const offG = nsDec(fg ? O.offg : O.off), defG = nsDec(AR ? (fg ? AR.g : AR.a) : fg ? F.dfng : F.dfn);
  const ovG = offG.map((row, r) => row.map((v, c) => v == null || defG[r][c] == null ? null : v * defG[r][c]));
  const vals = NSMODE === 'off' ? offG : NSMODE === 'def' ? defG : ovG;
  const who = AR ? nsArLab(NSAR) : 'opponents';
  // players: tonight's listed players for the team with shots this season, most attempts a game first (one archetype when picked)
  const P = NSM.players || {};
  const all = D.players.filter(p => p.g === g.id && p.t === off && P[p.id]).map(p => ({p, x: P[p.id], fit: nsFit(P[p.id].z, F)}))
    .sort((a, b) => b.x.n / b.x.gp - a.x.n / a.x.gp);
  const same = AR ? all.filter(r => r.x.ar === NSAR) : all, pl = same.length ? same : all;
  const sel = all.find(r => String(r.p.id) === String(NSMP[off])), S = sel && sel.x;
  const SP = S && NSPV === 'spots' ? nsSpots(S.s, vals) : null, dots = S ? (SP ? SP.svg : nsDots(S.s)) : '';
  // his spots on both measures (the summary line reports attempts and makes whichever map is shown)
  const gridFor = m => { const o = nsDec(m ? O.offg : O.off), d = nsDec(AR ? (m ? AR.g : AR.a) : m ? F.dfng : F.dfn);
    return NSMODE === 'off' ? o : NSMODE === 'def' ? d : o.map((row, r) => row.map((v, c) => v == null || d[r][c] == null ? null : v * d[r][c])); };
  const SP2 = SP ? nsSpots(S.s, gridFor(!fg)) : null, fitA = SP ? (fg ? SP2.fit : SP.fit) : null, fitF = SP ? (fg ? SP.fit : SP2.fit) : null;
  // tapped square
  const cc = NSMC[off], [cr, ccl] = cc ? cc.split(':').map(Number) : [];
  const lfg = (r, c) => NSM.lg.fg && NSM.lg.fg[r] && NSM.lg.fg[r][c];
  const cellInfo = cc != null && vals[cr] && vals[cr][ccl] != null ? (() => { const zl = NSZ.find(z => z[0] === nsZoneAt(cr, ccl))[1], o = offG[cr][ccl], d = defG[cr][ccl], lf = lfg(cr, ccl);
    const txt = fg ? `${lf != null ? `league ${nsF(lf)} here · ` : ''}${esc(off)} makes ${o != null ? (lf != null && !AR ? `${nsF(lf * o)} (${nsPc(o)})` : nsPc(o)) : '–'} · ${esc(def)} lets ${who} make ${d != null ? (lf != null && !AR ? `${nsF(lf * d)} (${nsPc(d)})` : nsPc(d)) : '–'}`
      : `${esc(off)} shoots here ${o != null ? nsPc(o) : '–'} vs average · ${esc(def)} allows ${who} ${d != null ? nsPc(d) : '–'}${NSMODE === 'ov' && ovG[cr][ccl] != null ? ` · together ${nsPc(ovG[cr][ccl])}` : ''}`;
    const me = SP && SP.cell[cr + ':' + ccl], lf0 = lfg(cr, ccl), meTxt = SP ? ` · <b>${esc(nsLast(sel.p.n))}</b> ${me ? `${me.m} of ${me.n} here${lf0 != null ? ` (league ${nsF(lf0)})` : ''}` : 'no shots here'}` : '';
    return `<div class="nsm-ci"><b>${esc(zl)}</b> · ${txt}${meTxt} <button type="button" class="nsm-x" data-nsc="${esc(off)}:x">close</button></div>`; })()
    : '<div class="nsm-ci muted">Tap a square on the court for its numbers.</div>';
  // zones that stand out: attempts = offense share x defense share over the league's; makes = offense FG% x defense FG% allowed over the
  // league's (each zone pulled toward the league with 60 attempts). With an archetype picked: the defense against that archetype only.
  const DZ = AR ? AR.z : F.zd;
  const zr = NSZ.map(([k, lab]) => { const oA = O.zo[k][0] / L[k][0], dA = DZ[k][0] / (AL[k][0] || 1), oF = nsZfg(O.zo[k], L[k][1]), dF = nsZfg(DZ[k], AL[k][1]);
    return {lab, v: AR ? (fg ? dF : dA) : NSMODE === 'off' ? (fg ? oF : oA) : NSMODE === 'def' ? (fg ? dF : dA) : (fg ? oF * dF : oA * dA)}; });
  const hot = zr.filter(x => x.v >= (fg ? 1.05 : 1.08)).sort((a, b) => b.v - a.v).slice(0, 2), cold = zr.filter(x => x.v <= (fg ? .95 : .92)).sort((a, b) => a.v - b.v).slice(0, 1);
  const say = (x, up) => AR ? (fg ? `${esc(def)} lets ${who} ${up ? 'make more' : 'make fewer'} than they do league-wide` : `${who} get ${up ? 'more' : 'fewer'} of their shots here against ${esc(def)}`)
    : NSMODE === 'off' ? (fg ? `${esc(off)} ${up ? 'shoots it well' : 'misses more than average'}` : `${esc(off)} ${up ? 'goes there a lot' : 'rarely goes there'}`)
    : NSMODE === 'def' ? (fg ? `${esc(def)} ${up ? 'lets them fall' : 'contests it well'}` : `${esc(def)} ${up ? 'gives it up' : 'takes it away'}`)
    : fg ? (up ? `${esc(off)} makes them and ${esc(def)} lets them fall` : `${esc(off)} misses there or ${esc(def)} contests it`) : (up ? `${esc(off)} goes there and ${esc(def)} gives it up` : `little of it for ${esc(off)} tonight`);
  const shr = (z, base) => { const v = z[0] / (base[0] || 1); return `<b class="${v >= 1.15 ? 'up' : v <= .87 ? 'dn' : ''}">${nsF(z[0])}</b>`; };
  const fgc = (z, base, defn) => { if (z[1] == null) return '<span class="muted">–</span>'; const d = z[1] - base[1], n = z[2] >= 40;
    return `<span class="${n && d >= .03 ? (defn ? 'bad' : 'good') : n && d <= -.03 ? (defn ? 'good' : 'bad') : ''}" title="${Math.round(z[1] * z[2])} of ${z[2]} (baseline ${nsF(base[1])})">${nsF(z[1])}</span>`; };
  // zone table: the columns of the current view (overlay: both sides); with an archetype picked, the defense against it vs how
  // that archetype shoots league-wide
  const showO = NSMODE !== 'def', showD = NSMODE !== 'off';
  const ztab = `<div class="tw"><table class="t nsm-zt"><thead><tr><th class="l">Zone</th><th title="${AR ? `How the league’s ${esc(nsArLab(NSAR))} shoot: share and FG%` : 'Share of the league’s attempts from here, and its FG%'}">${AR ? 'League <small>type</small>' : 'League'}</th>${showO ? `<th title="${esc(off)}’s share of its attempts from here">${esc(off)} shoots</th><th title="${esc(off)}’s FG% from here">FG%</th>` : ''}${showD ? `<th title="Share of ${esc(who)}’ attempts against ${esc(def)} from here">${esc(def)} allows</th><th title="${esc(who)}’ FG% against ${esc(def)} from here">FG%</th>` : ''}</tr></thead><tbody>
    ${NSZ.map(([k, lab, col]) => `<tr><td class="l"><i class="nsm-sw" style="background:${col}"></i>${lab}</td><td class="muted">${nsF(AL[k][0])} <small>${nsF(AL[k][1])}</small></td>${showO ? `<td>${shr(O.zo[k], L[k])}</td><td>${fgc(O.zo[k], L[k])}</td>` : ''}${showD ? `<td>${shr(DZ[k], AL[k])}</td><td>${fgc(DZ[k], AL[k], true)}</td>` : ''}</tr>`).join('')}</tbody></table></div>`;
  const arTag = a => a ? `<span class="nsm-ar">${esc(a)}</span>` : '';
  const prow = r => { const x = r.x, fit = r.fit, on = sel && r === sel;
    const fc = fit != null && Math.abs(fit - 1) >= .05 ? `<span class="nsm-fit ${fit > 1 ? 'up' : 'dn'}" title="${esc(def)} allows ${nsPc(fit)} points per shot vs average in the zones ${esc(r.p.n)} shoots from (its FG% by zone, pulled toward average). Display only, untested.">${nsPc(fit)} pts/shot</span>` : '';
    return `<button type="button" class="nsm-p" data-nsmp="${esc(off)}:${r.p.id}" aria-pressed="${!!on}">
      <span class="nsm-pn"><b>${esc(r.p.n)}</b> <small>${esc(r.p.pos || '')}</small>${fc}</span>${nsBar(x.z)}
      <span class="nsm-ps">${(x.n / x.gp).toFixed(1)} FGA · rim ${nsF(x.z.rim[0])} · 3PT ${nsF(x.z.c3[0] + x.z.ab3[0])} · FG ${nsF(x.fg)}${x.ar ? ` · ${esc(x.ar)}` : ''}</span></button>`; };
  const df = ['all', 'made', 'miss'].map(k => `<button type="button" data-nsdf="${k}" aria-pressed="${NSDF === k}">${k === 'all' ? 'All' : k === 'made' ? 'Makes' : 'Misses'}</button>`).join('');
  const pv2 = ['spots', 'dots'].map(k => `<button type="button" data-nspv="${k}" aria-pressed="${NSPV === k}">${k === 'spots' ? 'His spots' : 'Every shot'}</button>`).join('');
  // how favourable his spots are (higher = better for his side in every view but 'Where they shoot'): attempts and makes both
  const fpc = f => f == null ? '–' : `<b class="${NSMODE === 'off' ? '' : f >= 1.03 ? 'good' : f <= .97 ? 'bad' : ''}">${nsPc(f)}</b>`;
  const fitSay = () => NSMODE === 'def' ? `From his spots ${esc(def)} allows ${esc(who)} ${fpc(fitA)} shots and lets them make ${fpc(fitF)} vs average`
    : NSMODE === 'off' ? `From his spots ${esc(off)} shoots ${fpc(fitA)} and makes ${fpc(fitF)} vs average`
    : `His spots tonight: ${fpc(fitA)} on attempts (${esc(off)} likes them, ${esc(def)} gives them up), ${fpc(fitF)} on makes`;
  const fitLine = SP && (fitA != null || fitF != null) ? `<div class="nsm-fitl">${fitSay()} · on this map ${Math.round(SP.up * 100)}% of his shots come from squares above average, ${Math.round(SP.dn * 100)}% below</div>` : '';
  const selNote = S ? `<div class="nsm-sel"><b>${esc(sel.p.n)}</b>${arTag(S.ar)} · last ${Math.min(S.n, 300)} of ${S.n} shots in ${S.gp} games <span class="gmsw nsm-df" role="group" aria-label="Show">${pv2}</span>${SP ? ' <span class="nsm-spk">circle size = how often he shoots there · colour = the map at that spot (pale = about average)</span>' : ` <span class="gmsw nsm-df" role="group" aria-label="Shots shown">${df}</span> <i class="nsm-k m"></i>made <i class="nsm-k x"></i>missed`}${fitLine}
      <span class="nsm-pz">${NSZ.map(([k, lab, col]) => { const z = S.z[k]; if (!z[2]) return ''; const d = z[1] - L[k][1];
        return `<span title="${Math.round(z[1] * z[2])} of ${z[2]} (league ${nsF(L[k][1])})"><i class="nsm-sw" style="background:${col}"></i>${lab} ${nsF(z[0])} · <b class="${z[2] >= 15 && d >= .04 ? 'good' : z[2] >= 15 && d <= -.04 ? 'bad' : ''}">${nsF(z[1])}</b></span>`; }).join('')}</span>
      <button type="button" class="nsm-x" data-nsmp="${esc(off)}:0">clear</button></div>` : '';
  const arNote = AR ? `<div class="nsm-arn">${esc(def)} vs ${esc(nsArLab(NSAR))}: ${AR.n} shots faced${AR.n < 120 ? ' (a small sample: the map stays close to average)' : ''}, against how the league’s ${esc(nsArLab(NSAR))} shoot</div>` : '';
  const chips = `<div class="nsm-chips"><span class="ladc">${esc(off)} ${O.fga} FGA/gm (${nsOrd(O.rk.fga)})</span><span class="ladc">${nsF(O.p3)} threes (${nsOrd(O.rk.p3)} most)</span><span class="ladc">${nsF(O.rim)} at the rim (${nsOrd(O.rk.rim)})</span><span class="ladc">${O.pps.toFixed(2)} pts/shot (${nsOrd(O.rk.pps)})</span>
    <span class="ladc">${esc(def)} allows ${nsF(F.a3)} threes (${nsOrd(F.rk.a3)} most)</span><span class="ladc">${nsF(F.arim)} at the rim (${nsOrd(F.rk.arim)} most)</span><span class="ladc">${F.apps.toFixed(2)} pts/shot (${nsOrd(F.rk.apps)} most)</span></div>`;
  const plHead = AR ? (same.length ? `${esc(off)} ${esc(nsArLab(NSAR))}` : `${esc(off)} shooters <small>no ${esc(nsArLab(NSAR))} tonight</small>`) : `${esc(off)} shooters`;
  return `<div class="nsm-b"><h3>${esc(off)} offense <small>vs ${esc(def)} defense</small></h3>${chips}
    <div class="nsm-row"><div class="nsm-mapc">${arNote}${selNote}${nsSvg(off, vals, dots, cc, !!S)}${cellInfo}
      <ul class="nsm-list">${hot.map(x => `<li><b class="up">${nsPc(x.v)}</b> ${x.lab}: ${say(x, true)}</li>`).join('')}${cold.map(x => `<li><b class="dn">${nsPc(x.v)}</b> ${x.lab}: ${say(x, false)}</li>`).join('')}${!hot.length && !cold.length ? '<li class="muted">No zone stands out.</li>' : ''}</ul>
      ${ztab}</div>
      <div class="nsm-pc"><div class="nsm-ph">${plHead} <small>tap one to plot his shots</small></div><div class="nsm-pl">${pl.length ? pl.map(prow).join('') : '<div class="muted small">No listed player has shots this season.</div>'}</div></div></div></div>`;
}
function nsmPanel(g, pv){
  nsmLoad();
  if (!NSM) return `<section class="panel"><div class="phead"><h2>Shot map</h2>${pv}</div><div class="empty">${NSM_ST === 'none' ? 'No shot maps yet: they appear after the next NBA run.' : 'Loading shot maps…'}</div></section>`;
  const seg = `<div class="gmsw nsm-seg" role="group" aria-label="Map view">${[['ov', 'Overlay'], ['off', 'Where they shoot'], ['def', 'Where it’s allowed']].map(([k, l]) =>
    `<button data-nsmode="${k}" aria-pressed="${NSMODE === k}">${l}</button>`).join('')}</div>`;
  const met = `<div class="gmsw nsm-seg" role="group" aria-label="Measure">${[['att', 'Attempts'], ['fg', 'Makes vs misses']].map(([k, l]) =>
    `<button data-nsmet="${k}" aria-pressed="${NSMET === k}">${l}</button>`).join('')}</div>`;
  const ars = Object.entries(NSM.lg.arch || {}).sort((a, b) => b[1].n - a[1].n).map(([a]) => a);
  const arRow = NSMODE === 'def' && ars.length ? `<div class="nsm-arr"><span class="small muted">Shooter type:</span><button type="button" data-nsar="" aria-pressed="${!NSAR}">All</button>${ars.map(a =>
    `<button type="button" data-nsar="${esc(a)}" aria-pressed="${NSAR === a}">${esc(a)}</button>`).join('')}</div>` : '';
  const key = NSMET === 'fg' ? `<span><i style="background:rgba(52,210,122,.85)"></i>makes more than average</span><span><i style="background:rgba(240,72,72,.8)"></i>misses more</span>`
    : `<span><i style="background:rgba(255,99,71,.85)"></i>more attempts than average</span><span><i style="background:rgba(70,150,255,.7)"></i>fewer</span>`;
  return `<section class="panel"><div class="phead"><h2>Shot map</h2>${pv}<span class="muted small">this season · ${NSM.games} games through ${esc(NSM.through)}</span></div>
    <div class="nsm-top">${seg}${met}<div class="nsm-key">${key}<span class="muted">basket at the top · faded where few shots happen</span></div></div>${arRow}
    ${nsBlock(g.away, g.home, g)}${nsBlock(g.home, g.away, g)}
    <p class="note" style="margin:8px 0 0"><b>Attempts:</b> where they shoot = each team’s share of its field-goal attempts in each 3-ft square over the league’s share there; where it’s allowed = the same for opponents’ attempts against the defense; overlay = the two multiplied (red where this offense likes to shoot and this defense gives those shots up). <b>Makes vs misses:</b> FG% in each square over the league’s FG% there (offense, and what the defense allows; overlay = both), pulled toward the league’s on small samples (green = more makes, red = more misses). <b>Shooter type</b> (Where it’s allowed): what this defense allows to one playstyle archetype, against how that archetype shoots league-wide (each player’s latest archetype); the shooter list then shows tonight’s players of that type. This season’s regular-season shots, lightly smoothed; zone FG% is colored at 40+ attempts and 3+ points from its baseline (green = good for the shooter’s side). Tap a player to see his spots: his last 300 attempts (any team) grouped by square, each circle sized by how often he shoots there and filled with the map’s colour at that square (a big, strongly coloured circle = he shoots a lot where the map is strong; pale = about average), plus a line saying how favourable his spots are on the map shown; tap a square for his makes there. ‘Every shot’ plots each attempt instead (All / Makes / Misses). Display only: no number on the page uses these maps, and none of it has been tested as a betting signal; the projections already rate each team’s defense by position and playstyle.</p></section>`;
}
document.addEventListener('click', e => {
  const t = e.target.closest('[data-nsmode],[data-nsmet],[data-nsar],[data-nsdf],[data-nspv],[data-nsmp],[data-nsc]'); if (!t) return;
  if (t.dataset.nsmode){ NSMODE = t.dataset.nsmode; try { localStorage.setItem('nba-smode', NSMODE); } catch (x) {} }
  else if (t.dataset.nsmet){ NSMET = t.dataset.nsmet; try { localStorage.setItem('nba-smet', NSMET); } catch (x) {} }
  else if (t.dataset.nsar != null){ NSAR = t.dataset.nsar; }
  else if (t.dataset.nsdf){ NSDF = t.dataset.nsdf; }
  else if (t.dataset.nspv){ NSPV = t.dataset.nspv; try { localStorage.setItem('nba-spv', NSPV); } catch (x) {} }
  else if (t.dataset.nsmp){ const [tm, id] = t.dataset.nsmp.split(':'); NSMP[tm] = id === '0' || NSMP[tm] === id ? null : id; }
  else if (t.dataset.nsc){ const [tm, r, c] = t.dataset.nsc.split(':'); NSMC[tm] = r === 'x' || NSMC[tm] === r + ':' + c ? null : r + ':' + c; }
  render();
});
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
  const pv = `<span class="gmsw" role="group" aria-label="Players view"><button data-pview="proj" aria-pressed="${PVIEW === 'proj'}">Projections</button><button data-pview="box" aria-pressed="${PVIEW === 'box'}">Box score</button><button data-pview="map" aria-pressed="${PVIEW === 'map'}">Shot map</button></span>`;
  const tables = PVIEW === 'map' ? nsmPanel(g, pv) : PVIEW === 'box'
    ? `<section class="panel"><div class="phead"><h2>Projected box score <span class="exp3">highly experimental</span></h2>${pv}<span class="muted small">${mode(g.id) === 'vegas' ? 'With Vegas' : 'Market-blind'} · each player’s simulated average, rounded so team totals hold</span></div>${boxTable(g, g.away)}${boxTable(g, g.home)}
      <p class="note" style="margin:8px 0 0">One number per stat: the average of each player’s simulated games (the most accurate single guess, and averages add up to the team score). The 11 players with the most expected minutes (about how many play); the rest of the bench’s expected share goes to them so totals match the projected score. Tested on 2024-26 games: points off by 4.4 a player on average. Starters are the five with the most projected minutes; Q = questionable. Highlighted = above the books’ main line for that prop (an over lean; the chips list the line). On those chips, <b>Ladder</b> = that stat is on Ladder edges (Ladders tab); then <b>15%+ K</b> = a Kalshi rung where our chance beats the ask + fee by 15%+, and <b>NV +N%</b> = a Novig rung 10%+ under the other books’ fair (the rung number follows; both tracked, not proven). Points come from his makes (2 × twos + 3 × threes + free throws); shooting splits use his 3P% and FT% this season and last, pulled toward league average on small samples. <a href="boxes.html">How accurate it has been, graded every night →</a></p></section>`
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
// Edge audit fix J (owner 2026-11-11; research/edge_audit_nba_anchor.py): after the opening week our NBA ladder claims of 20%+ won 25
// vs 28.9 priced vs 42.3 claimed -- the biggest claims were our minutes misses. The card's chance is market-anchored: logit p = .049
// + .749 logit(Kalshi mid) + .233 logit(ours) (fit Jan - Apr 2026 on the Kalshi archive; unseen playoffs + 2026-27 log-loss .4969
// vs Kalshi .4983 vs ours .5086; current model Nov 1 - 10 .2725 vs .2737 / .2761; claims then calibrated). `praw` keeps ours.
const LADA = [.049, .749, .233], lgA = v => Math.log(v / (1 - v)), sgA = z => 1 / (1 + Math.exp(-z)), clA = v => Math.min(Math.max(v, .002), .998);
const ladAnchor = u => u.p == null || u.mid == null ? u : {...u, praw: u.p, p: sgA(LADA[0] + LADA[1] * lgA(clA(u.mid)) + LADA[2] * lgA(clA(u.p)))};
const ladPrice = u => { const opts = [{b: 'Kalshi', price: u.price, pay: 1 / u.cost}, ...Object.entries(u.bk || {}).map(([b, v]) => ({b, price: (v.o > 0 ? '+' : '') + v.o, u: v.u, pay: v.o > 0 ? 1 + v.o / 100 : 1 + 100 / -v.o}))];
  const best = opts.reduce((a, c) => c.pay > a.pay ? c : a); return {...u, opts, best, edge: u.p != null ? u.p * best.pay - 1 : null}; };
// one rung; its number is a dropdown of this rung and the ones below it, to check their prices (owner 2026-11-01, as on the
// NFL page). Rungs outside the card's flagged set show our chance, no edge: the 5% rule only ever covered the flagged rungs
const LADSEL = {}, LADC = {};
// the tracked Novig check (profit.tags src 'mbook'): Novig's price vs the other books' fair (2+ sources, 30% our raw number)
function nvEdge(g, p, u){
  const nvo = (u.opts || []).find(o => o.b === 'Novig'), mfn = nvo && window.MKT && MKT.multiFair ? MKT.multiFair(g.id, p.id, p.n, u.k, u.l, 'Novig') : null;
  const pr = u.praw != null ? u.praw : u.p;                     // the tracked Novig rule was built on our raw number
  const nvq = mfn && mfn.n >= 2 ? (pr != null ? .3 * pr + .7 * mfn.f : mfn.f) : null, nvd = nvo ? +String(nvo.price).replace('+', '') : null;
  return nvq != null && nvd ? nvq * (nvd > 0 ? 1 + nvd / 100 : 1 + 100 / -nvd) - 1 : null;
}
const nvOk = e => e != null && e >= .10 && e <= .40;
const k15Ok = u => (u.praw ?? u.p) != null && u.cost && (u.praw ?? u.p) / u.cost - 1 >= .15;
// 15%+ K pick (owner 2026-11-19: show each player's 15%+ K rung wherever it sits on his ladder, sorted first; after the ladder
// rules since Nov 11: this one 150 won vs 138.1 priced on 587, +15% (+-15, 8 nights) -- the card's own rule -1%, its anchored
// twin -38% on 104). Exactly the Profit row 'NBA study pick' (profit.nba_ladder_rows): every Kalshi pts / reb / ast / 3PM rung,
// spread 5c or less, mid 3-97c, ask under 97c, our own chance 15-40% over the ask + fee, the stat's biggest; no minutes cut and
// any rung height (405 of those 587 picks sat on rungs the card never showed). ke = that edge; anchored / priced like the card.
function k15Picks(g, p){
  if (!(window.MKT && MKT.ladderAll) || p.st === 'Out') return [];
  const best = {};
  for (const u of MKT.ladderAll(g.id, p.id, p.n, Object.keys(KLAB))){
    if (u.p == null || u.ya - u.yb > .05 || u.mid < .03 || u.mid > .97 || u.ya >= .97) continue;
    const e = u.p / u.cost - 1;
    if (e >= .15 && e <= .40 && (!best[u.k] || e > best[u.k].ke)) best[u.k] = {...u, ke: e};
  }
  return Object.values(best).map(ladAnchor).map(ladPrice).sort((a, b) => b.ke - a.ke);
}
const k15Of = (c, u) => (c.k15 || []).find(x => x.k === u.k && x.l === u.l);
// his average a game for the rung's stat (owner 2026-11-20: 'for the ladder candidates i want to see the avg for the player
// stat'): this season's games played (last season's when he has under 3; nba/project.py stat_avgs -> av), last 10 in the tooltip
function ladAvg(p, k){
  const a = p.av && p.av[k]; if (!a || a[0] == null) return '';
  const yr = `${p.av.season - 1}-${String(p.av.season).slice(2)}`;
  return ` <i class="lavg" title="${esc(`${KLAB[k]} a game: ${f1(a[0])} over his ${p.av.gp} game${p.av.gp === 1 ? '' : 's'} ${p.av.cur ? 'this season' : `last season (${yr}; under 3 games this season)`}; last 10 games ${f1(a[1])}. Context only.`)}">avg <b>${f1(a[0])}</b></i>`;
}
// Box score (owner 2026-11-18: 'when a player is above their market projection show if they are a ladder candidate and if so if
// they are k15%+ or novig 10%+'): his Ladder edges rungs for that stat (the card's rule: 20+ min, 2+ above the 50/50 rung, 5%+
// edge); if any, his ladder's best 15%+ K rung and best Novig-under-the-books rung (any rung, as the card's dropdown shows them)
function ladInfo(g, p, k){
  const mins = p.s && p.s.vegas && p.s.vegas.min && p.s.vegas.min[MED];
  if (!(mins >= 20) || p.st === 'Out' || !window.MKT || !MKT.ladder) return null;
  const rungs = MKT.ladder(g.id, p.id, p.n, [k]).map(ladAnchor).map(ladPrice).filter(u => u.edge != null && u.edge >= LEDGE);
  if (!rungs.length) return null;
  const all = MKT.ladderAll ? MKT.ladderAll(g.id, p.id, p.n, [k]).map(ladAnchor).map(ladPrice) : rungs;
  const k15 = k15Picks(g, p).find(u => u.k === k) || null;           // the tracked pick (same as the card's)
  const nv = all.map(u => [u, nvEdge(g, p, u)]).filter(([, e]) => nvOk(e)).sort((a, b) => b[1] - a[1])[0] || null;
  return {n: rungs.length, top: rungs[0], k15, nv};
}
function ladBoxTags(g, p, k){
  const L = ladInfo(g, p, k); if (!L) return '';
  const n = l => Math.round(l + .5), K = KLAB[k];
  return ` <b class="bxl" title="${esc(`On Ladder edges (Ladders tab): ${L.n} ${K} rung${L.n === 1 ? '' : 's'} 2+ above the 50/50 rung beat the best price by 5%+; best ${n(L.top.l)}+ at +${Math.round(L.top.edge * 100)}%. Experimental.`)}">Ladder</b>`
    + (L.k15 ? ` <b class="bxl k" title="${esc(`${n(L.k15.l)}+ ${K}: our chance ${Math.round((L.k15.praw ?? L.k15.p) * 100)}% vs Kalshi’s ask + fee ${Math.round(L.k15.cost * 100)}%. ${LFIT.k15} Not proven.`)}">15%+ K ${n(L.k15.l)}+</b>` : '')
    + (L.nv ? ` <b class="bxl nv" title="${esc(`${n(L.nv[0].l)}+ ${K}: Novig ${Math.round(L.nv[1] * 100)}% under the other books’ fair (2+ sources, 30% ours). Tracked lead (Novig rungs +73% on 120 in the study), not proven.`)}">NV +${Math.round(L.nv[1] * 100)}% ${n(L.nv[0].l)}+</b>` : '');
}
function lruChip(c, u0){
  const key = `${c.p.id}|${u0.k}|${u0.l}`, lo = c.all.filter(x => x.k === u0.k && x.l <= u0.l).sort((a, b) => b.l - a.l);
  LADC[key] = {c, u0};
  const u = lo.find(x => x.l === LADSEL[key]) || u0, kp = k15Of(c, u), flagged = !kp && c.rungs.some(x => x.k === u.k && x.l === u.l), b = u.best, kc = u.cost;
  const n = l => Math.round(l + .5), tx = o => `${o.b === 'Kalshi' ? 'K' : o.b === 'Hard Rock' ? 'HR' : 'NV'} ${o.price}`;
  // multi-book fair (MKT.multiFair, leaving out the best book) + the tracked Novig check: Novig's price 10%+ under the
  // fair from 2+ other sources (30% ours) -- research/ladder_multibook.py, Novig +73% on 120, both halves, a lead (owner 2026-11-03)
  const mf = window.MKT && MKT.multiFair ? MKT.multiFair(c.g.id, c.p.id, c.p.n, u.k, u.l, b.b) : null;
  const nve = nvEdge(c.g, c.p, u);
  const mfTxt = mf ? ` · fair from ${Object.keys(mf.parts).length} source${Object.keys(mf.parts).length === 1 ? '' : 's'} ${Math.round(mf.f * 100)}% (${Object.entries(mf.parts).map(([k, v]) => `${k} ${Math.round(v * 100)}%`).join(', ')}, without ${b.b})` : '';
  const num = lo.length > 1 ? `<select class="lsel" data-lrk="${esc(key)}" aria-label="Change rung">${lo.map(x => `<option value="${x.l}"${x.l === u.l ? ' selected' : ''}>${n(x.l)}+</option>`).join('')}</select>` : `<b>${n(u.l)}+</b>`;
  const kpTxt = kp ? ` · 15%+ K pick: our own chance ${Math.round(kp.praw * 100)}% vs Kalshi’s ask + fee ${Math.round(kp.cost * 100)}% = +${Math.round(kp.ke * 100)}%, the one rung of his ${KLAB[u.k]} ladder the Profit page grades (‘NBA study pick’); the one ladder rule ahead since Nov 11, not proven` : '';
  return `<span class="lru${kp ? ' thpk' : ''}" title="${n(u.l)}+ ${KLAB[u.k]}: chance ${u.p != null ? Math.round(u.p * 100) + '%' : '–'}${u.praw != null ? ` (23% our ${Math.round(u.praw * 100)}% + 77% Kalshi’s mid: our own number ran high on these rungs)` : ''} · Kalshi ${Math.round(u.mid * 100)}% (${Math.round(kc * 100)}% with the fee)${u.opts.filter(o => o.b !== 'Kalshi').map(o => ` · ${o.b} ${o.price}`).join('')}${u.opts.length === 1 ? ' · no Hard Rock / Novig price at this number' : ''}${mfTxt}${nve != null && nve >= .10 && nve <= .40 ? ` · Novig ${Math.round(nve * 100)}% under the other books’ fair: tracked, not proven` : ''}${kpTxt}">${num}${kp ? ' <b class="thtag">15%+ K pick</b>' : ''}${aStar(c.p, u)}${muDot(c.p, u.k)} ${KLAB[u.k]}${ladAvg(c.p, u.k)} ${u.opts.map(o => o === b ? (o.u ? `<a class="lbk best" href="${esc(o.u)}" target="_blank" rel="noopener">${tx(o)} ↗</a>` : `<i class="lbk best">${tx(o)}</i>`) : `<i class="lbk">${tx(o)}</i>`).join('')}${kp ? ` <b class="ledge">+${Math.round(kp.ke * 100)}% K</b>` : flagged ? ` <b class="ledge">+${Math.round(u.edge * 100)}%</b>` : u.p != null ? ` <i class="lbk">chance ${Math.round(u.p * 100)}%</i>` : ''}${ladRungFit(u, kp)}${mf ? ` <i class="lbk">fair ${Math.round(mf.f * 100)}%</i>` : ''}${nve != null && nve >= .10 && nve <= .40 ? ` <b class="lnv">NV +${Math.round(nve * 100)}% vs books</b>` : ''}${window.MKT && MKT.statMove ? MKT.statMove(c.g.id, c.p.n, u.k) : ''}${window.WATCH ? ' ' + WATCH.both({sport: 'nba', gid: String(c.g.id), name: c.p.n, kind: u.k, line: u.l, book: b.b, o: b.b === 'Kalshi' ? Math.round(kc >= .5 ? -100 * kc / (1 - kc) : 100 * (1 - kc) / kc) : Number(String(b.price).replace('+', '')), glab: `${c.g.away} @ ${c.g.home}`, start: c.g.start_utc || null, lab: `${c.p.n} ${n(u.l)}+ ${KLAB[u.k]}`}) : ''}${rungSlip(c.g, c.p, u)}</span>`;
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
const ladRungFit = (u, kp) => (!kp && (u.praw ?? u.p) != null && u.cost && (u.praw ?? u.p) / u.cost - 1 >= .15 ? ` <b class="lfit" title="${esc(LFIT.k15)}">15%+ K</b>` : '') + (u.k === 'fg3m' ? ` <b class="lfit" title="${esc(LFIT.fg3m)}">3PM</b>` : '');
// rungs shown on a player row (top 3 of the flagged set, by the move / star filters)
// 15%+ K picks first (each stat's one), then up to 3 flagged rungs that aren't a pick
const ladFilt = (c, L) => (LADM ? L.filter(u => ladMv(c, u) === LADM) : L).filter(u => !LADS || aStar(c.p, u));
const ladRest = c => c.rungs.filter(u => !k15Of(c, u));
const ladPool = c => [...(c.k15 || []), ...ladRest(c)];
const ladShown = c => [...ladFilt(c, c.k15 || []), ...ladFilt(c, ladRest(c)).slice(0, 3)];
const ladCmp = (a, b) => ((b.kb != null) - (a.kb != null)) || ((b.kb ?? b.best) - (a.kb ?? a.best));
// every rung of his ladder with a ★: the flagged ones first, then the rest of the ladder (lower rungs in the dropdown)
const starRungs = c => { const P = ladPool(c); return [...P, ...(c.all || []).filter(u => !P.some(r => r.k === u.k && r.l === u.l))].filter(u => aStar(c.p, u)); };
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
      if (p.st === 'Out') continue;
      const k15 = k15Picks(g, p);                               // any minutes, any rung (the tracked rule)
      const all = (mins >= 20 && window.MKT && MKT.ladder ? MKT.ladder(g.id, p.id, p.n, Object.keys(KLAB)) : []).map(ladAnchor);
      if (all.length || k15.length) anyLadder++;
      // each rung at Kalshi (fee included) and at Hard Rock / Novig for the same number (owner 2026-10-23); the edge
      // shown and ranked is the best of the three
      const rungs = all.map(ladPrice).filter(u => u.edge != null && u.edge >= LEDGE).sort((a, b) => b.edge - a.edge);
      if (!rungs.length && !k15.length) continue;
      rungs.forEach(u => regL(g, p, u, 'Ladder edge'));
      const R = DVP[p.o + '|' + p.pg] || {};
      cards.push({g, p, R, sts: [...new Set([...k15, ...rungs].map(u => u.k))].filter(st => (mx(p, st) || {}).v >= 1.07), rungs, k15, all: window.MKT && MKT.ladderAll ? MKT.ladderAll(g.id, p.id, p.n, Object.keys(KLAB)).map(ladAnchor).map(ladPrice) : rungs,
        best: rungs.length ? rungs[0].edge : null, kb: k15.length ? k15[0].ke : null});
    }
  }
  cards.sort(ladCmp);
  const soft = c => c.sts.map(st => { const m = mx(c.p, st);
    return `<span class="ladc soft" style="${MG(m.v)}" title="${mxWhy(c.p, st, m)} (context only: the model already uses it)">${esc(c.p.o)} ${sgn(m.v)} ${KLAB[st]} vs ${esc(m.da ? (ARCH_PL[c.p.ar] || PG_PL[c.p.pg]) : PG_PL[c.p.pg])}${MU[c.p.k] && MU[c.p.k].s[st] && MU[c.p.k].s[st][2] ? ` <b title="His team’s greenest ${KLAB[st]} matchup tonight (Matchups tab)">· best on ${esc(c.p.t)}</b>` : ''}</span>`; }).join('');
  const row = c => `<div class="lad"><div class="ladh"><b>${esc(c.p.n)}</b> <span class="small muted">${esc(c.p.t)} vs ${esc(c.p.o)} · ${esc(c.p.pos)}${c.p.s && c.p.s.vegas && c.p.s.vegas.min ? ` · ${f0(c.p.s.vegas.min[MED])} min` : ''}</span>${soft(c)}${muTags(c.p, [...new Set(ladPool(c).map(u => u.k))], c.sts)}${ladFit(c)}${[...new Set(ladPool(c).map(u => u.k))].map(st => drChip(c.p, st, 'card')).join('')}</div><div class="ladtg">${chips(c.p, c.g)}</div>
    <div class="ladr">${ladShown(c).map(u => lruChip(c, u)).join('')}</div>${starElse(c)}</div>`;
  if (LADM && !cards.some(c => ladPool(c).some(u => ladMv(c, u) === LADM))) LADM = '';
  const nStar = cards.filter(c => starRungs(c).length).length, nK = cards.filter(c => c.kb != null).length;
  const cm = (LADM ? cards.filter(c => ladPool(c).some(u => ladMv(c, u) === LADM)) : cards).filter(c => !LADS || starRungs(c).some(u => !LADM || ladMv(c, u) === LADM));
  if (!cm.some(c => String(c.g.id) === LADG)) LADG = '';
  const vis = LADG ? cm.filter(c => String(c.g.id) === LADG) : cm, lim = LADG ? 999 : 8;
  const empty = !anyLadder ? 'Kalshi’s NBA ladders are too thin to read yet (wide gaps between bid and ask); rungs show here once quotes tighten, usually on game day.'
    : !cards.length ? 'No higher rung beats its price by 5%+ right now. Prices move through the day; this updates with them.'
    : LADS && !cm.length ? 'No ladder edge has a ★ right now. Tap ★ only again to see them all.' : '';
  if (empty && !cards.length) return `<section class="panel pempty"><div class="phead"><h2>Ladder edges <span class="exp3">experimental</span></h2></div><p class="note">${!anyLadder ? 'No Kalshi ladders tight enough yet · usually fill in on game day.' : 'No rung beats its price by 5%+ right now · updates through the day.'}</p></section>`;
  return `<section class="panel"><div class="phead"><h2>Ladder edges <span class="exp3">experimental</span></h2><span class="small muted">${cards.length} player${cards.length === 1 ? '' : 's'}${nK ? ` · ${nK} with a 15%+ K pick` : ''}</span></div>
    ${empty ? `<p class="note" style="margin:0 0 6px">${empty}</p>` : ''}
    <details class="how"><summary>What this is</summary><p class="note"><b>15%+ K pick</b> (green-edged rung, first in its row; players with one come first): for each stat, the one Kalshi rung where our own chance beats Kalshi’s ask + fee by 15-40% (spread 5¢ or less), wherever it sits on his ladder and at any minutes; its edge is ours vs Kalshi with the fee. It is exactly what the Profit page grades as ‘NBA study pick’, the one ladder rule ahead since the Nov 11 model fixes (150 won vs 138 priced on 587, +15% through Nov 18, 8 nights; not proven). Below it: higher Kalshi rungs (two or more lines above the one priced near 50/50) where our chance beats the best price by 5% or more, for players projected for 20+ minutes (that rule was about break-even over the same nights). Each rung shows Kalshi (K, fee included), Hard Rock’s alt line (HR) and Novig’s ladder (NV) at the same number; the bold one is the best price and its edge is the one shown (tap it to open the bet when the book gives a link). Tap a rung’s number to switch to a lower rung and check its prices; rungs outside the flagged set show our chance, with no edge claimed. Hard Rock’s alt lines refresh in the morning and hourly in the last two hours before tip. The green chip marks a soft matchup for that stat, as context: in our 2024-26 backtest players facing soft defenses beat our high projections no more often than anyone else, because the model already counts the matchup. Untested on NBA prices; every flagged rung is tracked on the Profit page. <b>Tags</b> come from a study of every Kalshi NBA rung since January (research only, nothing is filtered): green = up in every period among rungs where our chance beats Kalshi’s ask + fee by 15%+ (the <b>15%+ K</b> tag: starter, away, 24-32 projected minutes, game total 230+, line 10+, threes); red = down (game total under 220, under 24 minutes). Every rung here is already two or more above the 50/50 rung. Hover a tag for its record.</p></details>
    ${cards.length ? `<div class="ladsw" role="group" aria-label="Ladder edges view">${[['game', 'By game'], ['edge', 'Best first']].map(([k, l]) => `<button type="button" data-ladv="${k}" aria-pressed="${LADV === k}">${l}</button>`).join('')}</div><div class="ladsw" role="group" aria-label="Star filter"><button type="button" data-lads="1" aria-pressed="${LADS}" title="Only rungs whose archetype, stat and odds range made money in NBA straights by archetype on the Profit page (last 30 days, 20+ bets). Picked after the fact: a lead, not a rule.">★ only <i>${nStar}</i></button></div>${window.MKT && MKT.consBar ? MKT.consBar(cards.map(c => ({...c, rungs: ladPool(c)})), LADM) : ''}` : ''}
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
  const row = r => `<div class="lad"><div class="ladh"><b>${esc(r.p.n)}</b> <span class="small muted">${esc(r.p.t)} vs ${esc(r.p.o)} · ${esc(r.g.start || '')}${med(r.p) != null ? ` · our median ${f0(med(r.p))} 3PM` : ''}</span>${drChip(r.p, 'fg3m', 'card')}${muTags(r.p, ['fg3m'])}${r.p.ar ? ` <span class="ladc">${esc(r.p.ar)}</span>` : ''}</div>
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
  return games.map(g => { const L = by.get(g.id).sort(ladCmp), nk = L.filter(c => c.kb != null).length, be = Math.max(...L.map(c => c.best ?? -9));
    return `<div class="ladgm"><div class="ladgh"><b>${esc(g.away)} @ ${esc(g.home)}</b> <span class="small muted">${esc(g.start || '')} · ${L.length} player${L.length === 1 ? '' : 's'}${nk ? ` · ${nk} 15%+ K` : ''}${be > -9 ? ` · best +${Math.round(be * 100)}%` : ''}</span></div>
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
/* ---------------- Matchups tab (owner 2026-11-21) ---------------- */
// From a PDF the owner made with our data for the Nov 20 slate: 'Create a tab under nba that shows this for all slates from now
// on. Make it exportable as a pdf from the page and make them easier to see when using ladders tab or screener'. One calculation,
// nba/matchups.py -> D.mu (also the screener's tags via site.py ctx_nba): each player-stat's sheet cell = what tonight's defense
// allows per minute vs the league to his position, blended with his archetype for points / threes (the number that shades his stat
// cell), in fixed bands; best = his team's greenest for that stat; chip = the D rating; dis = green cell with a negative chip (or the
// reverse). Players with 20+ projected minutes and real volume in the stat. Display only: nothing tracked.
const MU = (D.mu && D.mu.p) || {}, ST4 = ['pts', 'reb', 'ast', 'fg3m'];
const MUL = {3: 'Deep green', 2: 'Green', 1: 'Light green', 0: 'Neutral', '-1': 'Light red', '-2': 'Red', '-3': 'Deep red'};
const muCls = lv => lv >= 0 ? `mul${lv}` : `muln${-lv}`;
const muPct = v => `${v >= 0 ? '+' : '−'}${Math.abs(v).toFixed(1)}%`;
const muChip = c => c == null ? '' : `${c > 0 ? '+' : c < 0 ? '−' : ''}${Math.abs(c).toFixed(1)}`;
let MUV = 'game'; try { MUV = localStorage.getItem('nba-muv') || 'game'; } catch (e) {}
const muGames = () => [...D.games].sort((a, b) => new Date(a.start_utc) - new Date(b.start_utc));
function muRows(){
  const R = [], last = n => { const w = String(n).replace(/\s+(jr\.?|sr\.?|ii|iii|iv)$/i, '').split(' '); return w[w.length - 1]; };
  for (const p of D.players){
    const x = MU[p.k], g = x && D.games.find(z => z.id === p.g); if (!g) continue;
    for (const st of ST4){ const c = x.s[st]; if (c) R.push({p, g, st, m: x.m, v: c[0], lv: c[1], best: !!c[2], chip: c[3], dis: c[4]}); }
  }
  const cnt = {}; for (const n of new Set(R.map(r => r.p.n))){ const l = last(n); cnt[l] = (cnt[l] || 0) + 1; }
  for (const r of R){ const l = last(r.p.n); r.sn = (cnt[l] > 1 ? r.p.n[0] + '. ' : '') + l + (r.m < 24 ? '*' : ''); }
  return R;
}
const muWhy = r => `${r.p.n} (${r.p.t}): ${SL[r.st]} ${muPct(r.v)} per minute vs the league against ${r.p.o} (${MUL[r.lv].toLowerCase()})${r.chip != null ? ` · D rating ${muChip(r.chip)}` : ''} · ${f0(r.m)} projected min`;
function muByGame(R){
  const cell = (g, t, st) => {
    const B = R.filter(r => r.g.id === g.id && r.p.t === t && r.st === st && r.best).sort((a, b) => b.v - a.v);
    if (!B.length) return `<div class="muc mul0"><i>${SL[st]}</i><span class="muted">–</span></div>`;
    return `<div class="muc ${muCls(B[0].lv)}" title="${MUL[B[0].lv]}"><i>${SL[st]}</i>${B.map(r => `<span class="mun" title="${esc(muWhy(r))}">${esc(r.sn)}</span>`).join(' / ')}</div>`;
  };
  return `<div class="mugh"><span>Game</span><span>Team</span>${ST4.map(st => `<span>${SL[st]}</span>`).join('')}</div>` + muGames().map(g =>
    `<div class="mugg"><div class="mugl">${esc(g.away)} @ ${esc(g.home)}<small>${esc(g.start || '')}</small></div><div class="mugt">${[g.away, g.home].map(t =>
      `<div class="mur"><b>${esc(t)}</b>${ST4.map(st => cell(g, t, st)).join('')}</div>`).join('')}</div></div>`).join('');
}
function muTable(B, chip){
  if (!B.length) return '<p class="note">None on this slate.</p>';
  return `<table class="mut"><thead><tr><th>Player</th><th>Team</th><th>Game</th>${chip ? '<th class="n">D chip</th>' : ''}<th>Cell</th></tr></thead><tbody>${B.map(r =>
    `<tr title="${esc(muWhy(r))}"><td>${esc(r.sn)}</td><td>${esc(r.p.t)}</td><td>${esc(r.g.away)} @ ${esc(r.g.home)}</td>${chip ? `<td class="n">${muChip(r.chip)}</td>` : ''}<td class="c ${muCls(r.lv)}">${MUL[r.lv]} <span class="mup">${muPct(r.v)}</span></td></tr>`).join('')}</tbody></table>`;
}
const muByStat = (R, pick, chip) => `<div class="mus">${ST4.map(st => `<div><h3>${LAB[st]}</h3>${muTable(R.filter(r => r.st === st && pick(r)).sort((a, b) => b.lv - a.lv || b.v - a.v), chip)}</div>`).join('')}</div>`;
function muDisGame(R){
  const B = R.filter(r => r.dis), order = new Map(muGames().map((g, i) => [g.id, i]));
  if (!B.length) return '<p class="note">None on this slate: no green cell has a slightly tough or tough D rating.</p>';
  B.sort((a, b) => order.get(a.g.id) - order.get(b.g.id) || a.p.t.localeCompare(b.p.t) || a.sn.localeCompare(b.sn) || ST4.indexOf(a.st) - ST4.indexOf(b.st));
  return `<div class="tw"><table class="mut"><thead><tr><th>Game</th><th>Team</th><th>Player</th><th>Stat</th><th class="n">D chip</th><th>Cell</th></tr></thead><tbody>${B.map((r, i) =>
    `<tr title="${esc(muWhy(r))}"><td>${i && B[i - 1].g.id === r.g.id ? '' : `<b>${esc(r.g.away)} @ ${esc(r.g.home)}</b>`}</td><td>${esc(r.p.t)}</td><td>${esc(r.sn)}</td><td>${SL[r.st]}</td><td class="n">${muChip(r.chip)}</td><td class="c ${muCls(r.lv)}">${MUL[r.lv]} <span class="mup">${muPct(r.v)}</span></td></tr>`).join('')}</tbody></table></div>`;
}
// Heatmap view (owner 2026-11-23: 'can the matchup maps be like heatmaps as well?'): every eligible player x the four stats, each cell
// shaded on a continuous scale by the same matchup number (full colour at +/-15%), the team's best for that stat ringed, a cell the D
// rating disagrees with marked; players by projected minutes. Display only.
function muHeat(R){
  const shade = v => { const a = Math.min(1, Math.abs(v) / 15), w = Math.round(8 + a * 62);
    return Math.abs(v) < .5 ? '' : `background:color-mix(in srgb,var(${v > 0 ? '--good' : '--bad'}) ${w}%,transparent)${a >= .75 ? ';color:#fff' : ''}`; };
  const team = (g, t) => {
    const by = new Map(); for (const r of R) if (r.g.id === g.id && r.p.t === t){ if (!by.has(r.p.k)) by.set(r.p.k, {r, c: {}}); by.get(r.p.k).c[r.st] = r; }
    const rows = [...by.values()].sort((a, b) => b.r.m - a.r.m);
    if (!rows.length) return `<div class="muht-w"><div class="muht-h">${esc(t)}</div><p class="note">No eligible players.</p></div>`;
    return `<div class="muht-w"><div class="muht-h">${esc(t)} <small>vs ${esc(rows[0].r.p.o)} defense</small></div><table class="muht"><thead><tr><th class="l">Player</th>${ST4.map(st => `<th>${SL[st]}</th>`).join('')}</tr></thead><tbody>${rows.map(({r, c}) =>
      `<tr><td class="l"><b>${esc(r.sn)}</b><small>${f0(r.m)}m</small></td>${ST4.map(st => { const x = c[st]; if (!x) return '<td class="na">–</td>';
        const bst = x.best && x.lv >= 1, n = Math.round(x.v);
        return `<td class="${bst ? 'best' : ''}${x.dis ? ' dis' : ''}" style="${shade(x.v)}" title="${esc(muWhy(x))}${bst ? ' · his team’s best for this stat' : ''}${x.dis ? ' · the D rating disagrees' : ''}">${n > 0 ? '+' : n < 0 ? '−' : ''}${Math.abs(n)}<i>%</i></td>`; }).join('')}</tr>`).join('')}</tbody></table></div>`;
  };
  return `<div class="muhk"><span class="muhg" aria-hidden="true"></span><span class="small muted">tough −15% … neutral … +15% soft · <span class="muhk-b">ringed</span> = team’s best (light green or better) · <span class="muhk-d">dot</span> = D rating disagrees · m = projected minutes</span></div>
    <div class="muhm">${muGames().map(g => `<div class="muhm-g"><div class="muhm-gh">${esc(g.away)} @ ${esc(g.home)} <small>${esc(g.start || '')}</small></div><div class="muhm-t">${team(g, g.away)}${team(g, g.home)}</div></div>`).join('')}</div>`;
}
function muView(){
  if (!D.mu) return `<section class="panel"><div class="phead"><h2>Matchups</h2></div><p class="note">Matchup charts start with pages built from Nov 21, 2026. Open today’s page for them.</p></section>`;
  setTimeout(() => muLoad().catch(() => {}), 0);                    // the PDF libraries, ready before the tap
  const R = muRows(), nd = R.filter(r => r.dis).length;
  const key = `<span class="mukey">${[3, 2, 1, 0, -1, -2, -3].map(lv => `<span class="${muCls(lv)}">${MUL[lv]}</span>`).join('')}</span>`;
  const seg = `<div class="seg" role="group" aria-label="Group by">${[['game', 'By game'], ['stat', 'By stat'], ['heat', 'Heatmap']].map(([k, l]) => `<button type="button" data-muv="${k}" aria-pressed="${MUV === k}">${l}</button>`).join('')}</div>`;
  return `<section class="panel"><div class="phead"><h2>${MUV === 'heat' ? 'Matchup heatmap, every player' : 'Best matchup per stat, each team'}</h2><span class="small muted">${D.games.length} games · ${esc(D.date)}</span></div>
    <div class="mubar">${seg}${MUV === 'heat' ? '' : key}<button type="button" class="mupdf" data-mupdf="1" title="Downloads all four charts as a PDF (the same layout as the shared one)">⤓ Download PDF</button><a class="mupdf" href="../screener.html#nba-shortlist" title="The screener’s NBA shortlist: every NBA longshot (+350, Novig / Kalshi) and study pick, sorted by this sheet’s matchup cell and the D rating, with Copy list">Shortlist ↗</a></div>
    <details class="how"><summary>How to read this</summary><p class="note"><b>Cell</b> = what tonight’s defense allows per minute against the league average, to his position (and his archetype for points and threes): the same number that shades his stat cell on the sheet. Bands: deep green +10% or more, green +5 to +10%, light green +2 to +5%, neutral within 2%, and the same steps in red. Each team’s cell lists the players with its greenest matchup for that stat (ties all listed); only players with 20+ projected minutes and real volume in the stat (8+ points, 3+ rebounds, 2+ assists, 1+ three projected), so big men with no threes don’t show up green on threes. * = under 24 projected minutes. The model already uses these matchups, so a green cell is context, not an edge; the same tags show on the Ladders tab and in the screener (‘Best matchups’). Display only.</p></details>
    ${MUV === 'stat' ? muByStat(R, r => r.best) : MUV === 'heat' ? muHeat(R) : muByGame(R)}</section>
    <section class="panel"><div class="phead"><h2>Where the cell disagrees with the D rating</h2><span class="small muted">${nd} player-stat${nd === 1 ? '' : 's'}</span></div>
    <p class="note" style="margin:0 0 8px">Green or deeper on the sheet while the D rating chip is −1.0 or lower, its slightly tough / tough grades (and the reverse: red with a chip of +1.0 or more; chips between are the rating’s neutral band). The rating adds the defense’s rest, travel and missing defenders to the same matchup and was tested against the market on unseen games: tough ratings ran 3-5 pts under the price, and no grade beat the price after the vig. When they disagree, the chip is the tested one: a reason to check before betting the over.</p>
    ${MUV === 'stat' ? muByStat(R, r => r.dis, true) : muDisGame(R)}</section>${muBetsView()}`;
}
// Best bets (owner 2026-11-21: 'At the end of the sheets could you make a best bet list as well'): the matchup sheet is a filter,
// not an edge (no matchup level beat the price in our tests), so the list starts from the one ladder rule ahead since the Nov 11
// model fixes -- the 15%+ K pick (k15Picks, Profit row 'NBA study pick': our own chance 15-40% over Kalshi's ask + fee, the stat's
// biggest rung) -- and keeps the picks whose stat is his team's best matchup (light green or better) with a D rating above -1.0
// (not slightly tough / tough). Games not started; live prices. Display only: not proven, nothing new tracked.
function muBets(){
  const now = Date.now(), out = [];
  for (const g of D.games){
    if (g.start_utc && new Date(g.start_utc) <= now) continue;
    for (const p of D.players.filter(x => x.g === g.id && MU[x.k])){
      for (const u of k15Picks(g, p)){
        const c = MU[p.k].s[u.k];
        if (!c || !c[2] || c[1] < 1 || (c[3] != null && c[3] <= -1)) continue;
        const ob = b => (u.opts || []).find(o => o.b === b), nve = nvEdge(g, p, u);
        out.push({g, p, u, c, ke: u.ke, kal: ob('Kalshi'), hr: ob('Hard Rock'), nv: ob('Novig'), nvok: nvOk(nve), nve});
      }
    }
  }
  out.sort((a, b) => b.ke - a.ke);
  for (const r of out) r.mates = [...new Set(out.filter(x => x.p.k !== r.p.k && x.p.t === r.p.t && x.g.id === r.g.id).map(x => x.p.n.replace(/\s+(jr\.?|sr\.?|ii|iii|iv)$/i, '').split(' ').slice(-1)[0]))];
  return out;
}
const muRung = r => `${Math.round(r.u.l + .5)}+ ${KLAB[r.u.k]}`;
function muBetsView(){
  const B = muBets();
  const body = !B.length ? `<p class="note">${window.MKT && MKT.ladderAll ? 'No 15%+ K pick lines up with a best matchup right now. Picks move with Kalshi’s prices and lineups; this updates through the day.' : 'Loading Kalshi prices…'}</p>`
    : `<div class="tw"><table class="mut mubt"><thead><tr><th>Bet</th><th>Game</th><th>Kalshi</th><th>Hard Rock</th><th>Novig</th><th class="n">Edge</th><th>Matchup</th><th class="n">D chip</th></tr></thead><tbody>${B.map(r =>
      `<tr><td><b>${esc(r.p.n)}</b> ${esc(muRung(r))}${r.mates.length ? ` <span class="mutag dis" title="Same team as ${esc(r.mates.join(', '))}: teammates’ overs pull slightly against each other, so take one, or keep them out of the same parlay">same team</span>` : ''}</td>
        <td>${esc(r.p.t)} · ${esc(r.g.away)} @ ${esc(r.g.home)}<small class="muted"> ${esc(r.g.start || '')}</small></td>
        <td>${r.kal ? `<b>${esc(r.kal.price)}</b>` : '–'}</td><td>${r.hr ? (r.hr.u ? `<a href="${esc(r.hr.u)}" target="_blank" rel="noopener">${esc(r.hr.price)} ↗</a>` : esc(r.hr.price)) : '–'}</td>
        <td>${r.nv ? esc(r.nv.price) : '–'}${r.nvok ? ` <span class="mutag mul2" title="Novig ${Math.round(r.nve * 100)}% under the other books’ fair at this number (2+ sources, 30% ours): the tracked Novig check, a lead">NV +${Math.round(r.nve * 100)}%</span>` : ''}</td>
        <td class="n"><b>+${Math.round(r.ke * 100)}%</b></td><td class="c ${muCls(r.c[1])}">${MUL[r.c[1]]} <span class="mup">${muPct(r.c[0])}</span></td><td class="n">${r.c[3] != null ? muChip(r.c[3]) : '–'}</td></tr>`).join('')}</tbody></table></div>`;
  return `<section class="panel"><div class="phead"><h2>Best bets <span class="exp3">experimental</span></h2><span class="small muted">${B.length} pick${B.length === 1 ? '' : 's'} · overs</span></div>
    <p class="note" style="margin:0 0 8px">A matchup alone isn’t an edge (the market already prices it), so this list starts from the one ladder rule that is ahead since the Nov 11 model fixes, the <b>15%+ K pick</b> (Ladders tab: our chance 15-40% over Kalshi’s ask + fee, one rung per stat; graded on the Profit page as ‘NBA study pick’, not proven), and keeps the picks whose stat is his team’s <b>best matchup</b> on the sheet (light green or better) with a <b>D rating above −1.0</b>. Edge = our chance vs Kalshi with the fee. Longshot rungs: most lose on any one night. Prices and picks move until lineups are confirmed (about 30 minutes before tip), so check again then.</p>
    ${body}</section>`;
}
// PDF export: jsPDF + autoTable, loaded only when the button is tapped (pinned versions); if they can't load, the print dialog opens
const MU_RGB = {3: [46, 158, 91], 2: [143, 213, 166], 1: [214, 240, 221], 0: [229, 231, 235], '-1': [250, 221, 221], '-2': [240, 163, 163], '-3': [214, 80, 80]};
const muAsc = s => String(s).normalize('NFD').replace(/[̀-ͯ]/g, '').replace(/[’‘]/g, "'").replace(/[−–]/g, '-').replace(/[^\x20-\x7E]/g, '');
const muReady = () => !!(window.jspdf && window.jspdf.jsPDF && window.jspdf.jsPDF.API.autoTable);
let MU_LOADING = null;
function muLoad(){
  if (muReady()) return Promise.resolve();
  if (MU_LOADING) return MU_LOADING;
  const add = src => new Promise((res, rej) => { const s = document.createElement('script'); s.src = src; s.onload = res; s.onerror = rej; document.head.appendChild(s); });
  MU_LOADING = add('https://cdnjs.cloudflare.com/ajax/libs/jspdf/2.5.1/jspdf.umd.min.js').then(() => add('https://cdnjs.cloudflare.com/ajax/libs/jspdf-autotable/3.8.2/jspdf.plugin.autotable.min.js'))
    .catch(e => { MU_LOADING = null; throw e; });
  return MU_LOADING;
}
// phones (owner 2026-11-23: 'The pdf download wasn't working on mobile'): a download started after the libraries load has lost the
// tap, and in-app browsers ignore downloads -- so the libraries load as the tab opens (muView), the PDF is built in the same tap and
// goes to the phone's share sheet (Save to Files, send) when it offers one; otherwise an 'Open the PDF' link appears (a fresh tap works)
const muTouch = () => { try { return matchMedia('(pointer: coarse)').matches; } catch (e) { return false; } };
async function muOut(doc, name, btn){
  if (!muTouch()){ doc.save(name); return; }
  const blob = doc.output('blob');
  try { const f = new File([blob], name, {type: 'application/pdf'});
    if (navigator.canShare && navigator.canShare({files: [f]})){ await navigator.share({files: [f], title: name}); return; } }
  catch (e) { if (e && e.name === 'AbortError') return; }
  const url = URL.createObjectURL(blob), old = document.getElementById('mupdf-open'); if (old) old.remove();
  btn.insertAdjacentHTML('afterend', `<a id="mupdf-open" class="mupdf" href="${url}" target="_blank" rel="noopener" download="${name}">Open the PDF</a>`);
}
async function muPdf(btn){
  const t0 = btn.textContent; btn.disabled = true; btn.textContent = 'Building PDF…';
  try {
    if (!muReady()) await muLoad();                                 // already loaded with the tab: no wait, so the tap still counts
    const doc = new window.jspdf.jsPDF({unit: 'pt', format: 'letter'}), W = doc.internal.pageSize.getWidth(), H = doc.internal.pageSize.getHeight();
    const R = muRows(), G = muGames(), gl = g => `${g.away} @ ${g.home}`, fill = lv => ({fillColor: MU_RGB[lv]});
    const sty = {theme: 'grid', styles: {fontSize: 8, cellPadding: 3, textColor: [20, 20, 20], lineColor: [200, 204, 210], lineWidth: .5, valign: 'middle'},
      headStyles: {fillColor: [31, 41, 55], textColor: 255, fontStyle: 'bold'}, margin: {left: 40, right: 40, top: 40}};
    const title = (t, sub, first) => {
      if (!first) doc.addPage();
      doc.setFont('helvetica', 'bold'); doc.setFontSize(16); doc.setTextColor(0); doc.text(muAsc(t), W / 2, 46, {align: 'center'});
      doc.setFont('helvetica', 'normal'); doc.setFontSize(8.5); doc.setTextColor(90);
      const L = doc.splitTextToSize(muAsc(sub), W - 80); doc.text(L, 40, 64); doc.setTextColor(0); return 64 + L.length * 10 + 6;
    };
    const base = `NBA ${D.date} (${G.length} games), built ${D.generated}. Cell = what tonight's defense allows per minute vs the league to his position (and archetype for points / threes): the shading on the sim sheet. Deep green +10%+, green +5-10%, light green +2-5%, neutral within 2% (reds the same). Only players with 20+ projected min and real volume in the stat; * = under 24 min.`;
    const sub = (t, y) => { if (y > H - 90){ doc.addPage(); y = 50; } doc.setFont('helvetica', 'bolditalic'); doc.setFontSize(11); doc.text(t, 40, y); return y + 6; };
    // Chart 1: best matchup per stat per team, by game
    let y = title('Chart 1 - Best Matchup per Stat per Team (by game)', base, true);
    const b1 = [];
    for (const g of G) [g.away, g.home].forEach((t, i) => {
      const row = i ? [] : [{content: muAsc(gl(g)), rowSpan: 2, styles: {fontStyle: 'bold'}}];
      row.push({content: muAsc(t), styles: {fontStyle: 'bold'}});
      for (const st of ST4){
        const B = R.filter(r => r.g.id === g.id && r.p.t === t && r.st === st && r.best).sort((a, c) => c.v - a.v);
        row.push(B.length ? {content: muAsc(B.map(r => r.sn).join(' / ')), styles: fill(B[0].lv)} : {content: '-', styles: fill(0)});
      }
      b1.push(row);
    });
    doc.autoTable({...sty, startY: y, head: [['Game', 'Team', 'Pts', 'Reb', 'Ast', '3PM']], body: b1, columnStyles: {0: {cellWidth: 72}, 1: {cellWidth: 38}}});
    // Chart 2: cell vs D-rating chip disagreements, by game
    const D2 = R.filter(r => r.dis), ord = new Map(G.map((g, i) => [g.id, i]));
    D2.sort((a, c) => ord.get(a.g.id) - ord.get(c.g.id) || a.p.t.localeCompare(c.p.t) || a.sn.localeCompare(c.sn) || ST4.indexOf(a.st) - ST4.indexOf(c.st));
    y = title('Chart 2 - Where the Cell Color Disagrees with the D-Rating Chip', `Cells that are green or deeper while the player's D-rating chip is -1.0 or lower (its slightly tough / tough grades), and red or deeper with a chip of +1.0 or more. The D rating adds the defense's rest, travel and missing defenders and was tested against the market (tough ran 3-5 pts under the price). 20+ min; * = under 24 min.`);
    if (D2.length) doc.autoTable({...sty, startY: y, head: [['Game', 'Team', 'Player', 'Stat', 'Chip', 'Cell']],
      body: D2.map((r, i) => [{content: i && D2[i - 1].g.id === r.g.id ? '' : muAsc(gl(r.g)), styles: {fontStyle: 'bold'}}, r.p.t, muAsc(r.sn), SL[r.st], muAsc(muChip(r.chip)), {content: `${MUL[r.lv]} ${muAsc(muPct(r.v))}`, styles: fill(r.lv)}])});
    else { doc.setFontSize(10); doc.text('No disagreements on this slate.', 40, y + 12); }
    // Charts 3 and 4: the same grouped by stat, greenest first
    const byStat = (pick, chip) => {
      for (const st of ST4){
        const B = R.filter(r => r.st === st && pick(r)).sort((a, c) => c.lv - a.lv || c.v - a.v); if (!B.length) continue;
        y = sub(SL[st], y + 14);
        doc.autoTable({...sty, startY: y, head: [[`${SL[st]} - Player`, 'Team', 'Game', ...(chip ? ['Chip'] : []), 'Cell']],
          body: B.map(r => [muAsc(r.sn), r.p.t, muAsc(gl(r.g)), ...(chip ? [muAsc(muChip(r.chip))] : []), {content: `${MUL[r.lv]} ${muAsc(muPct(r.v))}`, styles: fill(r.lv)}])});
        y = doc.lastAutoTable.finalY;
      }
    };
    y = title('Chart 3 - Best Matchups (Chart 1 players) Grouped by Stat', base + ' Sorted greenest first within each stat.'); y -= 8; byStat(r => r.best);
    y = title('Chart 4 - Cell vs. Chip Disagreements Grouped by Stat', 'Same players as Chart 2, grouped by stat and sorted by how green the cell is. 20+ min; * = under 24 min.'); y -= 8;
    if (D2.length) byStat(r => r.dis, true); else { doc.setFontSize(10); doc.text('No disagreements on this slate.', 40, y + 12); }
    // Best bets: the 15%+ K picks whose stat is his team's best matchup with a D rating above -1.0 (muBets)
    const BB = muBets();
    y = title('Best Bets - 15%+ K Picks on a Best Matchup', `Overs. Starts from the one ladder rule ahead since the Nov 11 model fixes, the 15%+ K pick (our chance 15-40% over Kalshi's ask + fee, one rung per stat; 'NBA study pick' on the Profit page, not proven), and keeps picks whose stat is his team's best matchup on the sheet (light green or better) with a D rating above -1.0. Edge = our chance vs Kalshi with the fee. Prices as of ${new Date().toLocaleString()}; they move until lineups are confirmed. Longshot rungs: most lose on any one night.`);
    if (BB.length) doc.autoTable({...sty, startY: y, head: [['Bet', 'Team', 'Game', 'Kalshi', 'Hard Rock', 'Novig', 'Edge', 'Matchup', 'D chip']],
      body: BB.map(r => [muAsc(`${r.p.n} ${muRung(r)}${r.mates.length ? ' (same team as ' + r.mates.join(', ') + ')' : ''}`), r.p.t, muAsc(`${gl(r.g)} ${r.g.start || ''}`), r.kal ? r.kal.price : '-', r.hr ? r.hr.price : '-', r.nv ? r.nv.price + (r.nvok ? ` (NV +${Math.round(r.nve * 100)}%)` : '') : '-',
        `+${Math.round(r.ke * 100)}%`, {content: `${MUL[r.c[1]]} ${muAsc(muPct(r.c[0]))}`, styles: fill(r.c[1])}, r.c[3] != null ? muAsc(muChip(r.c[3])) : '-'])});
    else { doc.setFontSize(10); doc.text('No 15%+ K pick lines up with a best matchup right now.', 40, y + 12); }
    const n = doc.internal.getNumberOfPages();
    for (let i = 1; i <= n; i++){ doc.setPage(i); doc.setFontSize(7.5); doc.setTextColor(130); doc.text(`Sim Sheet - NBA matchup charts - ${D.date} - page ${i} of ${n} - display only, not a bet rule`, W / 2, H - 22, {align: 'center'}); }
    await muOut(doc, `NBA_matchup_charts_${D.date}.pdf`, btn);
  } catch (e) { console.error(e); window.print(); }
  finally { btn.disabled = false; btn.textContent = t0; }
}
// tags for the Ladders tab: the stat's cell when it is his team's best (light green or better), and a warning when the cell
// disagrees with the D rating
function muTags(p, sts, noBest){
  const x = MU[p.k]; if (!x) return '';
  return sts.map(st => { const c = x.s[st]; if (!c) return ''; const [v, lv, best, chip, dis] = c; let h = '';
    if (best && lv >= 1 && !(noBest || []).includes(st)) h += `<span class="mutag ${muCls(lv)}" title="${esc(`Best ${LAB[st].toLowerCase()} matchup on ${p.t} tonight: ${MUL[lv].toLowerCase()}, ${muPct(v)} per minute vs the league against ${p.o} (the sheet's cell; Matchups tab). The model already uses it: context, not an edge.`)}">Best ${SL[st]} matchup ${muPct(v)}</span>`;
    if (dis) h += `<span class="mutag dis" title="${esc(`${SL[st]} cell ${MUL[lv].toLowerCase()} (${muPct(v)}) but the D rating is ${muChip(chip)}: the rating adds rest, travel and missing defenders and was tested against the market. Check before betting the over.`)}">${dis > 0 ? 'Green' : 'Red'} cell · D ${muChip(chip)}</span>`;
    return h; }).join('');
}
// a dot on each ladder rung: the stat's cell color (ringed when it is his team's best)
function muDot(p, st){
  const c = MU[p.k] && MU[p.k].s[st]; if (!c || !c[1]) return '';
  return `<i class="mud ${muCls(c[1])}${c[2] && c[1] >= 1 ? ' best' : ''}" title="${esc(`Matchup cell: ${MUL[c[1]].toLowerCase()} ${muPct(c[0])}${c[2] && c[1] >= 1 ? `, his team's best for ${LAB[st].toLowerCase()}` : ''}${c[4] ? ` · D rating ${muChip(c[3])} disagrees` : ''} (Matchups tab)`)}"></i>`;
}
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
  const main = document.getElementById('main');
  try { main.innerHTML = state.view === 'board' ? board() : state.view === 'dvp' ? dvpView() : state.view === 'parlays' ? parlaysView() : state.view === 'ladders' ? laddersView() : state.view === 'mu' ? muView() : gameView(); }
  catch (e) { console.error(e); main.innerHTML = `<div class="empty">This view hit an error (${esc(String(e && e.message || e))}). The other tabs still work; it is fixed on the next page build.</div>`; }
  // shot map player lists (a strip on phones, a scrolling column on desktop): keep the picked player in view after a redraw
  for (const pl of document.querySelectorAll('.nsm-pl, .nsm-arr')){ const b = pl.querySelector('[aria-pressed="true"]'); if (!b) continue;
    if (pl.scrollWidth > pl.clientWidth){ const x = b.offsetLeft - pl.offsetLeft; if (x + b.offsetWidth > pl.clientWidth) pl.scrollLeft = x - 8; }
    else if (pl.scrollHeight > pl.clientHeight){ const y = b.offsetTop - pl.offsetTop; if (y + b.offsetHeight > pl.clientHeight) pl.scrollTop = y - 8; } }
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
  else if (t.dataset.muv){ MUV = t.dataset.muv; try { localStorage.setItem('nba-muv', MUV); } catch (e) {} render(); }
  else if (t.dataset.mupdf){ muPdf(t); }
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
