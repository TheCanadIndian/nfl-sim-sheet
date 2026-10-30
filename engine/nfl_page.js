const D = JSON.parse(document.getElementById('data').textContent);
const [MEAN,P10,P25,MED,P75,P90] = [0,1,2,3,4,5];
const STATS = {
  pass_yds:{label:'Passing yards',min:50}, att:{label:'Pass attempts',min:10}, cmp:{label:'Completions',min:5},
  pass_td:{label:'Passing TDs',min:.3,dec:1}, ints:{label:'Interceptions',min:.2,dec:1},
  rush_yds:{label:'Rushing yards',min:5}, car:{label:'Carries',min:1.5},
  rec_yds:{label:'Receiving yards',min:5}, rec:{label:'Receptions',min:.8}, tgt:{label:'Targets',min:1},
  anytime_td:{label:'Anytime TD',min:.04,prob:true},
  first_td:{label:'First TD scorer',min:.01,prob:true},
};
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const f0 = v => Math.round(v).toString();
const f1 = v => (Math.round(v*10)/10).toFixed(1);
const pct = p => Math.round(p*100) + '%';
const pct1 = p => p < .1 ? (Math.round(p*1000)/10).toFixed(1) + '%' : Math.round(p*100) + '%';
const american = p => { p = Math.min(Math.max(p,1e-4),1-1e-4); const o = p>=.5 ? -100*p/(1-p) : 100*(1-p)/p; return (o>0?'+':'') + Math.round(o); };
const niceCeil = v => { if (v<=0) return 1; const e = 10**Math.floor(Math.log10(v)); for (const m of [1,1.2,1.5,2,2.5,3,4,5,6,8,10]) if (m*e >= v - 1e-9) return m*e; };
const fmtTick = v => Math.abs(v) >= 10 || Number.isInteger(v) ? f0(v) : f1(v);

const state = {script:'all', view:'games', game:D.games[0]?.id, group:'receiving', bstat:'rec_yds', bpos:'All', q:'', bflag:false, bpin:false, dpos:'WR', rzscope:'two'};
try { Object.assign(state, JSON.parse(localStorage.getItem('simsheet-ui') || '{}')); } catch (e) {}
if (!D.games.some(g => g.id === state.game)) state.game = D.games[0]?.id;
if (location.hash === '#books') location.replace('markets.html#books');          // Book odds moved to the Markets page
if (['games','board','dvp','fair','props','agree','parlays'].includes(location.hash.slice(1))) state.view = location.hash.slice(1);
if (state.view === 'wfair') state.view = 'games';      // the Experimental tab is gone (graded in the background on the Profit page)
if (state.view === 'props' && !D.props.length) state.view = 'games';
if (state.view === 'rz') { state.view = 'games'; state.group = 'td'; }
const save = () => { try { localStorage.setItem('simsheet-ui', JSON.stringify({view:state.view, gtab:state.gtab, group:state.group, bstat:state.bstat, bpos:state.bpos, fo:state.fo, dpos:state.dpos, rzscope:state.rzscope, tds:state.tds, tdsd:state.tdsd, tdall:state.tdall})); } catch (e) {} };

document.getElementById('h1').textContent = `Week ${D.week} Sim Sheet`;
if (D.subtitle){ const n = document.getElementById('subnote'); n.textContent = D.subtitle; n.hidden = false; }
document.getElementById('sitenav').innerHTML = (D.nav || []).map(([label, href, cur]) => `<a href="${esc(href)}"${cur ? ' aria-current="page"' : ''}>${esc(label)}</a>`).join('');
document.getElementById('sub').textContent = `${D.season} season · ${D.games.length} game${D.games.length===1?'':'s'}` + (D.sims ? ` · ${D.sims.toLocaleString()} simulations each` : '') + ` · built ${D.generated}`;
document.getElementById('foot').innerHTML = `Every number comes from simulated games anchored to the Vegas spread and total. Medians are the 50/50 point: half the simulations land above, half below. Bars show where 8 in 10 simulations landed (10th to 90th percentile), with the darker band holding the middle half. Fair odds are the model's no-vig price. Rebuild this page each time <code>project.py</code> runs.`;
if (!D.props.length) document.getElementById('tab-props').hidden = true;
if (!D.sgp) { document.getElementById('tab-parlays').hidden = true; if (state.view === 'parlays') state.view = 'games'; }

function scaleFor(rows, stat){
  let lo = 0, hi = 0;
  for (const p of rows){ const s = p.s[stat]; if (!s) continue; lo = Math.min(lo, s[P10]); hi = Math.max(hi, s[P90]); }
  lo = lo < 0 ? -niceCeil(-lo) : 0;
  hi = niceCeil(hi);
  return {lo, hi, x: v => ((v - lo) / (hi - lo)) * 100};
}
function ticks(sc){ const mid = (sc.lo + sc.hi) / 2; return sc.lo < 0 ? [sc.lo, 0, sc.hi] : [0, mid, sc.hi]; }
function axis(sc){
  return `<div class="axis">${ticks(sc).map((t,i,a) => `<span style="left:${sc.x(t)}%;transform:translateX(${i===0?'0':i===a.length-1?'-100%':'-50%'})">${fmtTick(t)}</span>`).join('')}</div>`;
}
function strip(s, sc, label){
  const x = sc.x, g = ticks(sc).map(t => `<i class="g" style="left:${x(t)}%"></i>`).join('');
  const tip = `<b>${esc(label)}</b><br>Median ${f1(s[MED])} · mean ${f1(s[MEAN])}<br>Middle half ${f1(s[P25])} to ${f1(s[P75])}<br>8 in 10 games ${f1(s[P10])} to ${f1(s[P90])}`;
  return `<div class="strip" tabindex="0" data-tip="${esc(tip)}" aria-label="${esc(label)}: median ${f1(s[MED])}, 10th to 90th percentile ${f1(s[P10])} to ${f1(s[P90])}">${g}`
    + `<i class="w" style="left:${x(s[P10])}%;width:${Math.max(x(s[P90])-x(s[P10]),.5)}%"></i>`
    + `<i class="b" style="left:${x(s[P25])}%;width:${Math.max(x(s[P75])-x(s[P25]),.5)}%"></i>`
    + `<i class="m" style="left:${x(s[MED])}%"></i></div>`;
}
function pbar(p, max){ return `<div class="pbar" role="img" aria-label="${pct(p)}"><i style="width:${Math.min(p/max,1)*100}%"></i></div>`; }
const posTag = p => `<span class="pos">${esc(p)}</span>`;
const posCell = p => `<span class="pos" title="${p.lab ? 'Depth chart' : 'Not on the current depth chart'}">${esc(p.lab || p.pos)}</span>`;
const chip = t => `<span class="mm ${t.dir > 0 ? 'up' : 'down'}${t.strong ? ' strong' : ''}" title="${esc(t.text)}">${t.dir > 0 ? '▲' : '▼'} ${esc(t.short || t.kind)}</span>`;
// Shade by degree: g3/r3 strong, g2/r2 moderate, g1/r1 slight, '' neutral.
const shadeP = p => p >= .65 ? 'g3' : p >= .56 ? 'g2' : p >= .51 ? 'g1' : p <= .35 ? 'r3' : p <= .44 ? 'r2' : p <= .49 ? 'r1' : '';
const shadeEdge = e => e >= .06 ? 'g3' : e >= .04 ? 'g2' : e >= .02 ? 'g1' : e <= -.06 ? 'r3' : e <= -.04 ? 'r2' : e <= -.02 ? 'r1' : '';
const shadeRank = r => r <= 4 ? 'g3' : r <= 8 ? 'g2' : r <= 12 ? 'g1' : r >= 29 ? 'r3' : r >= 25 ? 'r2' : r >= 21 ? 'r1' : '';
const shadeRatio = x => x >= 1.2 ? 'g3' : x >= 1.12 ? 'g2' : x >= 1.05 ? 'g1' : x <= 1/1.2 ? 'r3' : x <= 1/1.12 ? 'r2' : x <= 1/1.05 ? 'r1' : '';
const chips = p => (p.tags && p.tags.length) ? `<div class="chips">${p.tags.slice(0, 2).map(chip).join('')}</div>` : '';

/* ---------------- games view ---------------- */
function spreadText(g){
  const d = g.home_implied - g.away_implied, fav = d >= 0 ? g.home : g.away;
  const total = g.home_implied + g.away_implied;
  return (Math.abs(d) < .25 ? 'Pick' : `${fav} −${f1(Math.abs(d)).replace('.0','')}`) + ` · O/U ${f1(total).replace('.0','')}`;
}
function gameList(){
  return D.games.map(g => {
    const T = D.teams[g.id], hw = T[g.home].win_prob;
    return `<button class="game-btn" data-game="${esc(g.id)}" aria-current="${g.id===state.game}">
      <span class="m"><span class="teams">${esc(g.away)} @ ${esc(g.home)}</span><span class="line">${esc(g.kickoff.replace(/ \w+ \d+ · /, ' ').replace(/ ET$/, ''))}</span></span>
      <span class="line">${esc(spreadText(g))}</span>
      <span class="mini" aria-hidden="true"><i style="flex:${1-hw};background:var(--away)"></i><i style="flex:${hw};background:var(--accent)"></i></span>
    </button>`;
  }).join('');
}
const GROUPS = {
  passing:{label:'Passing', stat:'pass_yds', keep:p => p.s.att && p.s.att[MEAN] >= 5,
    head:['Cmp / Att','','Median','TD','INT'],
    cells:(p,sc) => [`${f0(p.s.cmp[MED])} / ${f0(p.s.att[MED])}`, strip(p.s.pass_yds, sc, `${p.n} passing yards`), `<b>${f0(p.s.pass_yds[MED])}</b>`, f1(p.s.pass_td[MEAN]), f1(p.s.ints[MEAN])]},
  rushing:{label:'Rushing', stat:'rush_yds', keep:p => p.s.car && p.s.car[MEAN] >= 1,
    head:['Carries','','Median'],
    cells:(p,sc) => [f0(p.s.car[MED]), strip(p.s.rush_yds, sc, `${p.n} rushing yards`), `<b>${f0(p.s.rush_yds[MED])}</b>`]},
  receiving:{label:'Receiving', stat:'rec_yds', keep:p => p.s.tgt && p.s.tgt[MEAN] >= 1,
    head:['Tgt','Rec','','Median'],
    cells:(p,sc) => [f0(p.s.tgt[MED]), f0(p.s.rec[MED]), strip(p.s.rec_yds, sc, `${p.n} receiving yards`), `<b>${f0(p.s.rec_yds[MED])}</b>`]},
  td:{label:'Touchdowns', stat:'anytime_td', keep:p => p.s.anytime_td && p.s.anytime_td[MEAN] >= .03,
    head:['','Anytime','Fair odds','First TD','Fair odds','Red zone'],
    cells:(p,sc) => [pbar(p.s.anytime_td[MEAN], sc.hi), `<b>${pct(p.s.anytime_td[MEAN])}</b>`, american(p.s.anytime_td[MEAN]),
      p.s.first_td ? `<b>${pct1(p.s.first_td[MEAN])}</b>` : '–', p.s.first_td ? american(p.s.first_td[MEAN]) : '–', rzTags(p)]},
};
function playerTable(g){
  const G = GROUPS[state.group];
  const rows = PL(g).filter(p => G.keep(p));
  const by = p => G.stat === 'anytime_td' ? p.s.anytime_td[MEAN] : p.s[G.stat][MED];
  let sc;
  if (G.stat === 'anytime_td') { const m = Math.max(.1, ...rows.map(by)); sc = {hi: Math.min(1, niceCeil(m))}; }
  else sc = scaleFor(rows, G.stat);
  const scIdx = G.head.indexOf('');
  const headCells = G.head.map((h,i) => i === scIdx
    ? `<th class="sc l">${G.stat === 'anytime_td' ? `<div class="axis"><span style="left:0">0%</span><span style="left:100%;transform:translateX(-100%)">${pct(sc.hi)}</span></div>` : axis(sc)}</th>`
    : `<th>${h}</th>`).join('');
  // best price at the viewer's books (mkt.js), the tab's main bet
  const bo = !!(window.MKT && MKT.hasProps && MKT.hasProps(g.id)), bk = {passing: 'pass_yds', rushing: 'rush_yds', receiving: 'rec_yds', td: 'td'}[state.group];
  const ncol = G.head.length + 2 + (bo ? 1 : 0);
  // phones: the key number on the right, the rest in one small line under the name
  const kIdx = {passing: 2, rushing: 2, receiving: 3, td: 1}[state.group];
  const rng = s => s ? ` · usually ${f0(s[P10])}–${f0(s[P90])} yds` : '';
  const mmeta = p => { const s = p.s, m = {
      passing: () => `${f0(s.cmp[MED])}/${f0(s.att[MED])} · ${f1(s.pass_td[MEAN])} TD · ${f1(s.ints[MEAN])} INT${rng(s.pass_yds)}`,
      rushing: () => `${f0(s.car[MED])} carries${rng(s.rush_yds)}`,
      receiving: () => `${f0(s.tgt[MED])} tgt · ${f0(s.rec[MED])} rec${rng(s.rec_yds)}`,
      td: () => `fair ${american(s.anytime_td[MEAN])}${s.first_td ? ` · first TD ${pct1(s.first_td[MEAN])}` : ''}`}[state.group];
    return `<span class="mmeta">${esc(p.lab || p.pos)} · ${m ? m() : ''}</span>`; };
  let body = '';
  for (const team of [g.away, g.home]){
    const tr = rows.filter(p => p.t === team).sort((a,b) => by(b) - by(a));
    const oppTeam = team === g.home ? g.away : g.home;
    body += `<tr class="grp"><td colspan="${ncol}">${esc(team)}${state.group === 'td' ? rzTeamLine(team, oppTeam) : ''}</td></tr>`;
    body += tr.length ? tr.map(p => `<tr class="${state.open === p.k ? 'opened' : ''}">${nameCell(p, GROUP_STAT[state.group], mmeta(p) + (bo ? `<span class="pcm">${MKT.prop(g.id, p.n, bk)}</span>` : ''))}<td class="l">${posCell(p)}</td>${G.cells(p, sc).map((c,i) => `<td class="${i===scIdx?'l':''}${i===kIdx?' kp':''}">${c}</td>`).join('')}${bo ? `<td class="l pcd">${MKT.prop(g.id, p.n, bk)}</td>` : ''}</tr>`
        + (state.open === p.k ? `<tr class="xrow"><td colspan="${ncol}">${sliderPanel(p)}</td></tr>` : '')).join('')
      : `<tr><td colspan="${ncol}" class="l muted small">No players with meaningful volume.</td></tr>`;
  }
  return `<div class="tw"><table class="t pt"><thead><tr><th class="l">Player</th><th class="l">Pos</th>${headCells}${bo ? '<th class="l pcd" title="Best price at your sportsbooks (choose them under the game bets); over/unders show the side we rate better. Green = our chance beats it by 5%+">Best odds</th>' : ''}</tr></thead><tbody>${body}</tbody></table></div>`;
}
// Most likely first TD scorers in this game (backtest 2024-26: calibrated; top pick scored first 14%).
function firstTdBlock(g){
  const ps = PL(g).filter(p => p.s.first_td).sort((a, b) => b.s.first_td[MEAN] - a.s.first_td[MEAN]).slice(0, 5);
  if (!ps.length) return '';
  const hi = Math.max(.15, ps[0].s.first_td[MEAN]);
  return `<div class="ftd"><div class="ftd-h"><span class="eyebrow">Most likely first TD scorer</span><span class="small muted">chance · fair odds · anytime</span></div>
    ${ps.map((p, i) => `<div class="ftd-r${i === 0 ? ' top' : ''}"><span class="n">${i === 0 ? '<span class="star">★</span>' : ''}${esc(p.n)} <span class="small muted">${esc(p.t)} · ${esc(p.lab || p.pos)}</span></span><span class="pbar"><i style="width:${p.s.first_td[MEAN] / hi * 100}%"></i></span><b>${pct1(p.s.first_td[MEAN])}</b><span class="muted">${american(p.s.first_td[MEAN])}</span><span class="small muted">${pct(p.s.anytime_td[MEAN])}</span></div>`).join('')}
  </div>`;
}
// Per-game model switch: "vegas" (this page's model) or "blind" (market-blind numbers embedded as D.alt).
state.gm = state.gm || {};
const isBlind = g => state.gm[g.id] === 'blind' && D.alt && D.alt.teams[g.id];
const TM = g => isBlind(g) ? D.alt.teams[g.id] : D.teams[g.id];
const PL = g => (isBlind(g) ? D.alt.players : D.players).filter(p => p.g === g.id);
function modelSwitch(g){
  if (!D.alt || !D.alt.teams[g.id]) return '';
  const b = !!isBlind(g);
  return `<span class="gmsw" role="group" aria-label="Model for this game"><button data-gmodel="vegas" aria-pressed="${!b}">With Vegas</button><button data-gmodel="blind" aria-pressed="${b}">Market-blind</button></span>`;
}
/* ---------------- parlays (sgp.py: joint simulation, 5-7 legs, no unders, main lines only) ---------------- */
const SRC_TIP = {model: "Our median line (Kalshi has no line for this yet): check your book's number", Vegas: 'Vegas total'};
const srcTag = L => L.src === 'Kalshi' ? '' : `<span class="legsrc" title="${esc(SRC_TIP[L.src] || L.src)}">${L.src === 'model' ? 'our line' : esc(L.src)}</span>`;
// the other model's chance for the same leg (from its saved distributions / win chance)
const LEG_STAT = {rec: 'rec', rec_yds: 'rec_yds', rush_yds: 'rush_yds', pass_yds: 'pass_yds', anytime_td: 'tds'};
let SGP_G = null, SGP_BLIND = false;
function otherP(L){
  const g = SGP_G; if (!g || !D.alt) return null;
  if (L.kind === 'win'){ const T = (SGP_BLIND ? D.teams : D.alt.teams)[g.id]; return T && T[L.team] ? T[L.team].win_prob : null; }
  const st = LEG_STAT[L.kind]; if (!st || L.pid == null) return null;
  const p = (SGP_BLIND ? D.players : D.alt.players).find(x => x.g === g.id && String(x.id) === String(L.pid));
  const d = p && p.d && p.d[st]; if (!d) return null;
  const r = probs(d, L.kind === 'anytime_td' ? .5 : L.line); return r.over;
}
function otherTag(L){
  const q = otherP(L); if (q == null) return '';
  const who = SGP_BLIND ? 'Vegas model' : 'Market-blind';
  return q >= Math.min(.5, L.p) - .02 ? ` <span class="legsrc both" title="${who}: ${pct(q)}. Both models like this leg (the other model has it at least as high, up to 50%).">both ✓</span>`
                 : ` <span class="legsrc" title="${who} has this leg at ${pct(q)}">${SGP_BLIND ? 'vegas' : 'blind'} ${pct(q)}</span>`;
}
const legLine = (L, script, n) => `<li${n ? ` value="${n}"` : ''}>${esc(L.lab)}${srcTag(L)}${otherTag(L)}${L.mean != null ? ` <span class="small muted" title="Projected average vs median">avg ${L.mean} · med ${L.med}</span>` : ''}<span class="r">${pct(L.p)}${script && L.p_script != null && Math.abs(L.p_script - L.p) >= .02 ? ` → ${pct(L.p_script)}` : ''}${L.price ? ` · ${window.MKT && MKT.px ? MKT.px(L.price, 'kalshi') : Math.round(L.price * 100) + '¢'}` : ''}</span></li>`;
// "Build in Gambly": opens Gambly's chat with the parlay typed in (its own ask-Gambly links use these params);
// Gambly turns it into bet slips at the user's books (Hard Rock, Kalshi, Novig, ...). Unofficial: may change.
function gamblyLink(legs, k, game){
  const ls = legs.slice(0, k).map(L => L.lab).join(', ');
  const prompt = `Build a ${k}-leg ${game ? 'same game parlay for ' + game : 'parlay'}: ${ls}`;
  return 'https://gambly.com/chat?' + new URLSearchParams({entry: 'ask-gambly', prompt, autoSubmit: '1'}).toString() + '&utm_campaign=thecanadindian';
}
// a card's legs as slip legs (player key looked up on this page; game legs read the saved margin / total)
function cardSlipLegs(legs, gid){
  const g = D.games.find(x => x.id === gid); if (!g) return [];
  const pool = (state.gm[gid] === 'blind' && D.alt ? D.alt.players : D.players);
  return legs.map(L => {
    const base = {sport: 'nfl', g: gid, glab: `${g.away} @ ${g.home}`, lab: L.lab, p: L.p, model: state.gm[gid] === 'blind' ? 'blind' : 'vegas', draws: `nfl/draws/${gid}.json`};
    if (L.kind === 'win') return {...base, id: `nfl|${gid}|win|${L.team}`, stat: 'win', home: L.team === g.home, side: 'over'};
    if (L.kind === 'total') return {...base, id: `nfl|${gid}|total|over|${L.line}`, stat: 'total', line: L.line, side: 'over'};
    const p = pool.find(x => x.g === gid && String(x.id) === String(L.pid)); if (!p) return null;
    const stat = L.kind === 'anytime_td' ? 'tds' : L.kind, line = L.kind === 'anytime_td' ? .5 : L.line;
    return {...base, id: `nfl|${gid}|${p.k}|${stat}|over|${line}|${base.model}`, stat, line, side: 'over', pk: p.k};
  }).filter(Boolean);
}
const gamblyBtns = (legs, ks, game) => `<div class="gbtns"><span class="small muted">Build in Gambly:</span>${ks.map(k => `<a class="gbtn" href="${esc(gamblyLink(legs, k, game))}" target="_blank" rel="noopener" title="Opens Gambly's chat with these ${k} legs typed in; pick your book there">${k} legs ↗</a>`).join('')}</div>`;
// Parlay chance = (each leg's market-anchored chance) x the sims' correlation lift (joint / legs multiplied).
// research/sgp_pairs_test.py, 2026 wk 5-6 (8,966 same-game Kalshi pairs): where the sims saw legs rise together
// (lift 1.15+) both hit 19.6% vs 19.6% married, 15.7% multiplying the market, 14.4% sims alone; anti-correlated
// (lift <= 0.87): 7.2% vs 8.5% married, 10.4% market, 5.1% sims. Legs no market prices at their line keep our chance.
const PLAYER_OF = lab => { const m = /^(.*?) (over|under|anytime|to score)\b/i.exec(lab || ''); return m ? m[1] : null; };
function legQ(L, gid){
  const name = PLAYER_OF(L.lab), M = name && window.MKT && MKT.legMarket ? MKT.legMarket(gid, name, L.kind, L.line) : null;
  return M ? {q: M.w * L.p + (1 - M.w) * M.m, src: M.src} : {q: L.p, src: null};
}
function married(legs, gid, pSim, pInd){
  const Q = legs.map(L => legQ(L, gid || L.gid));
  const prod = Q.reduce((s, x) => s * x.q, 1), lift = pInd > 0 ? pSim / pInd : 1;
  return {p: Math.min(prod * lift, ...Q.map(x => x.q)), n: Q.filter(x => x.src).length, of: Q.length};
}
// Game script check: our simulated chance of a script vs the prediction markets' (MKT.scriptMarket: Kalshi total
// ladders, Polymarket winning-margin ladders). Same-game parlays live or die on the script, and a script the market
// already prices as likely gives no reason to play.
function scriptSpec(P, g){
  const n = P.name || '', d = P.desc || '', m = /^(.+?) pulls away/.exec(n);
  if (m) return {kind: 'margin', team: m[1], min: (/wins by (\d+)\+/.exec(d) || [0, 8])[1] * 1};
  if (/blowout/.test(n)){ const t = /: (\S+) blowout/.exec(n); return t ? {kind: 'margin', team: t[1], min: 14} : null; }
  if (/close game/.test(n)) return {kind: 'close', max: 7};
  if (n === 'Close finish') return {kind: 'close', max: 3};
  if (n === 'Shootout'){ const x = /^(\d+)\+ combined/.exec(d); return x ? {kind: 'over', at: x[1] - .5} : null; }
  if (/^Grind/.test(n)){ const x = /^(\d+) or fewer combined/.exec(d); return x ? {kind: 'under', at: +x[1] + .5} : null; }
  return null;
}
function scriptCheck(g, spec, ours){
  if (!spec || ours == null || !(window.MKT && MKT.scriptMarket)) return '';
  const T = (D.teams[g.id] || {})[g.home] || {}, fav = (T.spread_line ?? 0) >= 0 ? g.home : g.away;
  const M = MKT.scriptMarket(g.id, spec, {home: g.home, away: g.away, fav}); if (!M) return '';
  const d = ours - M.m, cls = d >= .05 ? 'up' : d <= -.05 ? 'dn' : '';
  const verdict = d >= .05 ? 'we see it more than the market' : d <= -.05 ? 'the market sees it more: no reason to play it' : 'in line with the market';
  return `<span class="ladc sck ${cls}" title="Our simulated games: ${pct(ours)}. ${esc(M.src)}’s ladder: ${pct(M.m)}. ${verdict}.">script: us ${pct(ours)} · market ${pct(M.m)}</span>`;
}
function sgpCard(P){
  const any = P.any ?? P.name === 'Most likely', cut = P.rows.length ? P.rows[0].k : 5;
  const gid = SGP_G && SGP_G.id;
  const rows = P.rows.map(r => { const M = married(P.legs.slice(0, r.k), gid, r.p, r.indep);
    return `<tr><td>${r.k} legs<small class="muted" style="display:block">${M.n}/${M.of} priced</small></td><td><b title="Each leg’s market-anchored chance (${M.n} of ${M.of} legs have a market line) times our sims’ correlation lift">${pct1(M.p)}</b></td><td>${esc(american(M.p))}</td>${any ? '' : `<td>${pct1(r.p_script)}</td>`}<td>${pct1(r.p)}</td><td>${r.kalshi ? american(r.kalshi) : '–'}</td></tr>`; }).join('');
  const legs = P.legs.map((L, i) => (i === cut && i < P.legs.length ? `<li class="after">+ ${P.legs.length - cut === 1 ? 'this' : 'these'} for the longer version${P.legs.length - cut === 1 ? '' : 's'}</li>` : '') + legLine(L, !any, i === cut ? cut + 1 : 0)).join('');
  return `<div class="sgp${P.expected ? ' exp' : ''}"><h3>${esc(P.name)} <small>${P.name === 'Higher lines' ? 'fewer legs, bumped lines' : any ? 'any script' : `happens in ${pct(P.happens)} of sims`}</small></h3>
    <div class="small muted">${esc(P.desc)}</div>
    ${SGP_G ? scriptCheck(SGP_G, scriptSpec(P, SGP_G), P.happens) : ''}
    <ol>${legs}</ol>
    <table><thead><tr><th></th><th>Hits</th><th>Fair</th>${any ? '' : '<th title="Chance the parlay hits if this script happens">In script</th>'}<th title="Our simulated games alone (before the market's leg chances)">Sims only</th><th title="Kalshi asks multiplied (when every leg has one)">Kalshi</th></tr></thead><tbody>${rows}</tbody></table>
    ${gamblyBtns(P.legs, P.rows.map(r => r.k), SGP_G ? `${SGP_G.away} @ ${SGP_G.home}` : '')}
    ${window.SLIP && SGP_G ? `<div class="gbtns"><span class="small muted">My parlay:</span>${P.rows.map(r => SLIP.allBtn(cardSlipLegs(P.legs.slice(0, r.k), SGP_G.id), `+ ${r.k} legs`)).join('')}</div>` : ''}</div>`;
}
function sgpPanel(g){
  SGP_G = g; SGP_BLIND = !!isBlind(g);
  const S = SGP_BLIND ? (D.alt.sgp && D.alt.sgp.games[g.id]) : (D.sgp && D.sgp.games[g.id]);
  if (!S || !S.parlays.length) return '<section class="panel"><div class="empty">No parlays for this game yet: it needs 5+ main-line legs that clear 35%.</div></section>';
  return `<section class="panel" aria-label="Same-game parlays"><div class="phead"><h2>${esc(g.away)} at ${esc(g.home)}: same-game parlays</h2>${modelSwitch(g)}<span class="small muted">${SGP_BLIND ? 'market-blind model' : 'Vegas model'} · leg chance overall → inside the script · Kalshi ask</span></div>
    <p class="note"><b>Hits</b> = each leg's market chance (the sharp consensus or Kalshi at that line, our number weighted in lightly) times how much our simulated games say the legs rise or fall together (a QB's yards and his receivers' rise together). On 8,966 same-game pairs (weeks 5-6) that matched the real hit rate, where multiplying the market's prices missed correlated pairs by about 4 points and our sims alone ran low. <b>Sims only</b> = our simulated games alone; "priced" = legs with a market line. Main lines only (the Kalshi rung nearest 50/50, or our own median line until Kalshi lists one), no unders, every leg 35%+ on its own. Hit chances are calibrated to 2025-26 backtests (335 games). Each leg is adjusted for the player's depth role: backup RBs, QB yards on our own lines, WR2 yards and WR3+ catches ran low and are marked down; RB1 rushing yards and TDs ran high and are marked up. Legs 6 and 7 must be 50%+ alone and a reliable type (a Kalshi line, game leg, RB1 rushing/TD, WR1 or TE1), so some parlays stop at 6. The "Expected" parlay only appears when the model makes a clear close-game or blowout call (in backtests those hit above their shown chance; slight leans didn't). "Higher lines" (3-4 overs set at or above the player's average) backtested right on its numbers. Fair is the no-vig price: a book's parlay is only worth it if it pays more. Switch models above: each leg shows the other model's chance, and "both ✓" when the other model has it at least as high (up to 50%). In the Model agreement backtest, legs where the market-blind model leans the same way hit more often.</p>
    ${outlookBar(S.outlook)}
    <div class="sgp-grid">${S.parlays.map(sgpCard).join('')}</div></section>`;
}
function outlookBar(o){
  if (!o) return '';
  const lean = (p, b) => { const d = Math.round((p - b) * 100); return `<span class="${d >= 3 ? 'good' : 'muted'}">${d >= 0 ? '+' : '−'}${Math.abs(d)} vs the model's typical game</span>`; };
  return `<div class="outlook"><b>${o.expected === 'blowout' ? `Model ${o.slight ? 'slightly leans to' : 'expects'} a ${esc(o.fav)} blowout` : `Model ${o.slight ? 'slightly leans to' : 'expects'} a close game`}</b>
    <span>Decided by 7 or fewer: <b>${pct(o.p_close)}</b> ${lean(o.p_close, o.base_close)}</span>
    <span>Decided by 14+: <b>${pct(o.p_blowout)}</b> ${lean(o.p_blowout, o.base_blowout)} · ${esc(o.fav)} by 14+: ${pct(o.p_fav_blowout)}</span>
    ${D.alt && D.alt.sgp && D.sgp ? otherOutlook() : ''}</div>`;
}
function otherOutlook(){
  const S = (SGP_BLIND ? D.sgp : D.alt.sgp).games[SGP_G.id]; const o = S && S.outlook; if (!o) return '';
  return `<span class="muted">${SGP_BLIND ? 'Vegas model' : 'Market-blind model'}: ${o.expected === 'blowout' ? `${esc(o.fav)} blowout` : 'close game'} (7 or fewer ${pct(o.p_close)}, 14+ ${pct(o.p_blowout)})</span>`;
}
function crossSlipLegs(legs){
  const byGame = {};
  legs.forEach(L => { const g = D.games.find(x => `${x.away} @ ${x.home}` === L.game); if (g) (byGame[g.id] = byGame[g.id] || []).push(L); });
  return Object.entries(byGame).flatMap(([gid, ls]) => cardSlipLegs(ls, gid));
}
function tierParlays(){
  if (!(window.MKT && MKT.tierCard)) return '';
  const gameLegs = [];
  for (const g of D.games){
    const T = D.teams[g.id]; if (!T) continue;
    const H = T[g.home], A = T[g.away]; if (H.spread_line == null) continue;
    const L = H.spread_line, tl = H.total_line, sp = v => (v > 0 ? '+' : v < 0 ? '−' : '') + Math.abs(v);
    gameLegs.push({gid: g.id, kind: 'ml', side: 'away', p: A.win_prob, lab: `${g.away} moneyline`}, {gid: g.id, kind: 'ml', side: 'home', p: H.win_prob, lab: `${g.home} moneyline`},
                  {gid: g.id, kind: 'spread', side: 'away', line: -L, p: A.p_cover, lab: `${g.away} ${sp(L)}`}, {gid: g.id, kind: 'spread', side: 'home', line: -L, p: H.p_cover, lab: `${g.home} ${sp(-L)}`},
                  {gid: g.id, kind: 'total', side: 'over', line: tl, p: H.p_over, lab: `${g.away}-${g.home} over ${tl}`}, {gid: g.id, kind: 'total', side: 'under', line: tl, p: H.p_under, lab: `${g.away}-${g.home} under ${tl}`});
  }
  return MKT.tierCard({sport: 'nfl', overOnly: true, title: 'Parlays by payout (your books)', games: D.games.map(g => ({id: g.id, label: `${g.away} @ ${g.home}`, start: g.sort})),
                       gameLegs, propKinds: {td: 'TD', rec_yds: 'rec yds', rush_yds: 'rush yds', pass_yds: 'pass yds', rec: 'receptions'},
                       // Kalshi 2026 (17,517 graded props): 50/50 with the market and a 5%+ edge returned +8.4%; our projection alone lost
                       minEdge: .05});
}
// Ladder candidates (experimental): players facing one of the 8 defenses most generous to their position (defense vs
// position, this season). Kalshi 2026 wk 1-5: their high rungs (2+ above the 50/50 rung) cleared 3-8 pts more often than
// the price said, in both halves (z 1.9 / 2.5 per player); with a 2nd signal (underdog, team total 25+) similar. Tracked
// on the Profit page; not proven.
function ladderCard(){
  if (!D.dvp) return '';
  const KL = {rec: 'catches', rec_yds: 'rec yds', rush_yds: 'rush yds', pass_yds: 'pass yds'};
  const posKey = p => p.pos === 'QB' ? 'QB' : p.pos === 'RB' ? 'RB' : p.pos === 'TE' ? 'TE' : 'WR';
  const now = Date.now(), cards = [];
  for (const g of D.games){
    if (g.sort && new Date(g.sort) <= now) continue;
    for (const p of D.players.filter(x => x.g === g.id)){
      const v = (p.s.tgt && p.s.tgt[MEAN] >= 3) || (p.s.car && p.s.car[MEAN] >= 8) || (p.s.att && p.s.att[MEAN] >= 20);
      if (!v) continue;
      const k = posKey(p), r = dvpRow(k, p.o); if (!r || r.rank > 8) continue;
      const T = (D.teams[g.id] || {})[p.t] || {};
      const extra = [T.win_prob != null && T.win_prob <= .40 ? 'underdog' : null, T.vegas_implied >= 25 ? `team total ${T.vegas_implied}` : null].filter(Boolean);
      const rungs = window.MKT && MKT.ladder ? MKT.ladder(g.id, p.id, p.n) : [];
      cards.push({g, p, r, k, extra, rungs});
    }
  }
  if (!cards.length) return '';
  cards.sort((a, b) => b.extra.length - a.extra.length || a.r.rank - b.r.rank);
  const row = c => `<div class="lad"><div class="ladh"><b>${esc(c.p.n)}</b> <span class="small muted">${esc(c.p.t)} vs ${esc(c.p.o)} · ${esc(c.p.pos)}</span>
      <span class="ladc soft" title="${esc(c.p.o)} gives up the ${ord(c.r.rank)} most to ${c.k === 'WR' ? 'wide receivers' : c.k + 's'} this season (defense vs position)">soft: ${ord(c.r.rank)} most to ${c.k}s</span>${c.extra.map(x => `<span class="ladc">${esc(x)}</span>`).join('')}</div>
    <div class="ladr">${c.rungs.slice(0, 3).map(u => `<span class="lru" title="Kalshi ${Math.round(u.l + .5)}+ ${KL[u.k]}: price ${Math.round(u.mid * 100)}% · our chance ${u.p != null ? Math.round(u.p * 100) + '%' : '–'}"><b>${Math.round(u.l + .5)}+</b> ${KL[u.k]} <i>${u.price}</i>${window.WATCH ? ' ' + WATCH.both({sport: 'nfl', gid: String(c.g.id), name: c.p.n, kind: u.k, line: u.l, book: 'Kalshi', o: Math.round(u.cost >= .5 ? -100 * u.cost / (1 - u.cost) : 100 * (1 - u.cost) / u.cost), glab: `${c.g.away} @ ${c.g.home}`, start: c.g.sort || null, lab: `${c.p.n} ${Math.round(u.l + .5)}+ ${KL[u.k]}`}) : ''}</span>`).join('')}</div></div>`;
  const on = cards.filter(c => c.rungs.length), off = cards.filter(c => !c.rungs.length);
  const wait = off.length ? `<details class="ladw"><summary>${on.length ? `${off.length} more` : `${off.length} players`} · Kalshi ladders not posted yet</summary><div class="ladr">${off.map(c =>
      `<span class="lru" title="${esc(c.p.o)}: ${ord(c.r.rank)} most to ${c.k}s${c.extra.length ? ' · ' + c.extra.join(' · ') : ''}"><b>${esc(c.p.n)}</b> <i>${esc(c.p.pos)} vs ${esc(c.p.o)}</i></span>`).join('')}</div></details>` : '';
  return `<section class="panel"><div class="phead"><h2>Ladder candidates <span class="exp3">experimental</span></h2><span class="small muted">${cards.length} player${cards.length === 1 ? '' : 's'}</span></div>
    <details class="how"><summary>What this is</summary><p class="note">Players facing one of the 8 defenses that have given up the most to their position this season, with their higher Kalshi rungs (two or more lines above the one priced near 50/50). In Kalshi’s 2026 markets (weeks 1-5), those higher rungs cleared 3-8 points more often than their price said, in both halves of the sample; a second signal (underdog, or a team total of 25+) pointed the same way. The market seems to price the average game against a bad defense but not the ceiling. That is a small sample from one season, so these are tracked on the Profit page to see if it holds, not a recommendation. Defense rankings early in the season rest on only a few games.</p></details>
    ${on.length ? `<div class="ladsw" role="group" aria-label="Ladder candidates view">${[['game', 'By game'], ['rank', 'Softest first']].map(([k, l]) => `<button type="button" data-nladv="${k}" aria-pressed="${NLADV === k}">${l}</button>`).join('')}</div>` : ''}
    ${NLADV === 'game' ? ladByGame(on, row) : `<div class="ladg">${on.slice(0, 8).map(row).join('')}</div>${on.length > 8 ? `<details class="ladw"><summary>${on.length - 8} more with ladders</summary><div class="ladg">${on.slice(8).map(row).join('')}</div></details>` : ''}`}${wait}</section>`;
}
// Ladder candidates by game (owner 2026-10-25: "why are my ladder candidates not separated by game"): games in kickoff
// order, each game's players softest matchup first, five shown and the rest folded; remembered on this device (nfl-ladv).
let NLADV = 'game';
try { NLADV = localStorage.getItem('nfl-ladv') || 'game'; } catch (e) {}
function ladByGame(cards, row){
  const by = new Map();
  for (const c of cards) (by.get(c.g.id) || by.set(c.g.id, []).get(c.g.id)).push(c);
  return D.games.filter(g => by.has(g.id)).map(g => { const L = by.get(g.id);
    return `<div class="ladgm"><div class="ladgh"><b>${esc(g.away)} @ ${esc(g.home)}</b> <span class="small muted">${esc(g.kickoff || '')} · ${L.length} player${L.length === 1 ? '' : 's'}</span></div>
      <div class="ladg">${L.slice(0, 5).map(row).join('')}</div>${L.length > 5 ? `<details class="ladw"><summary>${L.length - 5} more in this game</summary><div class="ladg">${L.slice(5).map(row).join('')}</div></details>` : ''}</div>`; }).join('');
}
document.addEventListener('click', e => { const b = e.target.closest('[data-nladv]'); if (!b) return;
  NLADV = b.dataset.nladv; try { localStorage.setItem('nfl-ladv', NLADV); } catch (x) {} render(); });
// An achievable rung our projection backs (Ladder longshots / Primetime ladders; owner 2026-10-20: "Kittle 130 yards is
// very unlikely, his price at 60 or 70 would have been good"): 2+ above Kalshi's 50/50 rung with our chance 20-45%
// (inside our projection's realistic range, about +120 to +400), Kalshi mid 15-45c, spread 5c or less, our chance no
// more than 2 pts under the mid, and real volume for the stat (targets 4+, carries 10+, attempts 25+). The longshot
// payout comes from stacking 2-3 such legs. research/top_rungs.py-style test (Kalshi 2026 wk 1-6, one rung per
// player-stat, ours closest to 30%): soft-matchup players +25% (+24% / +28% by half, hit 31%), everyone -3%; the old
// near-top rungs (1-2 below the top) were +22% / -11% and hit 12-16%. research/top_rungs.py
// (Kalshi 2026 wk 1-6, one rung per player-stat): with our chance required, soft-matchup players +31% vs +22% without,
// everyone -7% vs -11% (both within the noise; our projection agreeing never made it worse).
const LAD_VOL = {rec: ['tgt', 4], rec_yds: ['tgt', 4], rush_yds: ['car', 10], pass_yds: ['att', 25]};
function rungOk(p, u){
  const v = LAD_VOL[u.k], vol = v && p.s[v[0]] ? p.s[v[0]][MEAN] : 0;
  return u.mid >= .15 && u.mid <= .45 && u.yb > 0 && u.ya - u.yb <= .05
    && u.p != null && u.p >= .20 && u.p <= .45 && u.p >= u.mid - .02 && (!v || vol >= v[1]);
}
const projChip = (p, u) => { const d = p.s[u.k]; return `<span class="ladc" title="Our projection: median ${d ? f1(d[MED]) : '–'}, 9 in 10 games under ${d ? f1(d[P90]) : '–'}; our chance of ${Math.round(u.l + .5)}+ ${pct(u.p)} vs Kalshi ${pct(u.mid)}">proj ${d ? f0(d[MED]) : '–'} · us ${pct(u.p)} vs ${pct(u.mid)}</span>`; };
// Ladder longshots (experimental, owner's idea 2026-10-19): parlays of near-maxed Kalshi rungs (1-2 below the top rung
// and 2+ above the one priced near 50/50) for players with a soft matchup (defense top 8 most allowed to the position)
// or a live over Value tag. research/top_rungs.py (Kalshi 2026 wk 1-6, 13,985 graded rungs): for everyone, top rungs lost
// 33% and the next two 9-16% (longshot bias); for soft-matchup players 1-2 below the top returned +13% / +18% (wide
// error, +-40%). One leg per game; the chance shown is the market's (Kalshi mids), no model edge claimed. Tracked.
function ladderLongshots(){
  if (!D.dvp || !(window.MKT && MKT.ladder)) return '';
  const KL = {rec: 'catches', rec_yds: 'rec yds', rush_yds: 'rush yds', pass_yds: 'pass yds'};
  const posKey = p => p.pos === 'QB' ? 'QB' : p.pos === 'RB' ? 'RB' : p.pos === 'TE' ? 'TE' : 'WR';
  const now = Date.now(), best = {};
  for (const g of D.games){
    if (g.sort && new Date(g.sort) <= now) continue;
    for (const p of D.players.filter(x => x.g === g.id)){
      if (!((p.s.tgt && p.s.tgt[MEAN] >= 3) || (p.s.car && p.s.car[MEAN] >= 8) || (p.s.att && p.s.att[MEAN] >= 20))) continue;
      const k = posKey(p), r = dvpRow(k, p.o), soft = !!(r && r.rank <= 8), tk = MKT.tagKind ? MKT.tagKind(g.id, p.n) : null;
      if (!soft && !tk) continue;
      const rungs = MKT.ladder(g.id, p.id, p.n).filter(u => rungOk(p, u));
      if (!rungs.length) continue;
      const u = rungs.sort((a, b) => (b.k === tk) - (a.k === tk) || Math.abs(a.p - .30) - Math.abs(b.p - .30))[0];   // ours closest to 30%
      const why = [soft ? `soft: ${ord(r.rank)} most to ${k}s` : null, tk && tk === u.k ? 'Value tag' : tk ? 'Value tag (other stat)' : null].filter(Boolean);
      const sc = (soft ? 2 : 0) + (tk === u.k ? 2 : tk ? 1 : 0);
      const cand = {g, p, u, why, sc};
      if (!best[g.id] || sc > best[g.id].sc || (sc === best[g.id].sc && u.mid > best[g.id].u.mid)) best[g.id] = cand;
    }
  }
  const legs = Object.values(best).sort((a, b) => b.sc - a.sc || b.u.mid - a.u.mid);
  if (legs.length < 2) return '';
  const combos = [];
  for (let i = 0; i < legs.length; i++) for (let j = i + 1; j < legs.length; j++){
    combos.push([legs[i], legs[j]]);
    for (let k = j + 1; k < legs.length; k++) combos.push([legs[i], legs[j], legs[k]]);
  }
  const val = c => ({c, p: c.reduce((s, L) => s * L.u.mid, 1), d: c.reduce((s, L) => s / L.u.cost, 1), sc: c.reduce((s, L) => s + L.sc, 0)});
  const used = {};                                   // no player in more than two of the shown parlays
  const pick = (n, k) => { const out = [];
    for (const x of combos.filter(c => c.length === n).map(val).sort((a, b) => b.sc - a.sc || b.p - a.p)){
      if (out.length >= k) break;
      if (x.c.some(L => (used[L.p.k] || 0) >= 2)) continue;
      x.c.forEach(L => { used[L.p.k] = (used[L.p.k] || 0) + 1; }); out.push(x); }
    return out; };
  const two = pick(2, 3), three = pick(3, 2);
  const amO = q => q >= .5 ? -Math.round(100 * q / (1 - q)) : Math.round(100 * (1 - q) / q);
  const legOf = L => ({gid: L.g.id, start: L.g.sort, kind: L.u.k, side: 'o', line: L.u.l, name: MKT.norm(L.p.n), lab: `${L.p.n} ${Math.round(L.u.l + .5)}+ ${KL[L.u.k]}`, p: +L.u.mid.toFixed(4), price: amO(L.u.cost), book: 'Kalshi'});
  if (MKT.reg) [...two, ...three].forEach(x => MKT.reg('Ladder longshots (experimental)', `${x.c.length}-leg`, x.c.map(legOf), x.p, x.d));
  const card = x => `<div class="dcp"><div class="dch"><span class="slot ls">${x.c.length} legs</span>Kalshi</div>
      <ul>${x.c.map(L => `<li><b>${esc(L.p.n)} ${Math.round(L.u.l + .5)}+ ${KL[L.u.k]}</b><span>${esc(L.p.t)} vs ${esc(L.p.o)} · ${L.u.price}</span></li><li class="lwhy">${L.why.map(w => `<span class="ladc${/soft/.test(w) ? ' soft' : ''}">${esc(w)}</span>`).join('')}${projChip(L.p, L.u)}</li>`).join('')}</ul>
      <div class="dcs"><span>pays <b>${x.d.toFixed(0)}x</b></span><span>$5 → <b>$${(5 * x.d).toFixed(0)}</b></span><span>market chance <b>${pct1(x.p)}</b></span></div></div>`;
  return `<section class="panel"><div class="phead"><h2>Ladder longshots <span class="exp3">experimental</span></h2><span class="small muted">achievable Kalshi rungs · one per game</span></div>
    <details class="how"><summary>What this is</summary><p class="note">Parlays of rungs inside our projection’s realistic range: our chance 20-45% (about +120 to +400), so roughly at or a little above the player’s median, only for players with a soft matchup (a defense in the 8 that allow the most to his position) or a live Value tag, with real volume for the stat (4+ targets, 10+ carries or 25+ passes) and our chance no more than 2 points under Kalshi’s. The payout comes from stacking two or three such legs from different games. On Kalshi’s 2026 ladders (weeks 1-6) these rungs returned +25% for soft-matchup players (both halves, hitting 31% of the time) and −3% for everyone, while rungs near the top of the ladder were −9 to −33% for everyone: longshot rungs are overpriced. The chance shown is the market’s own (Kalshi’s mid prices); every parlay is tracked on the Profit page.</p></details>
    <div class="dcg">${[...two, ...three].map(card).join('')}</div></section>`;
}
// Primetime ladders (experimental, owner's call 2026-10-19): a primetime game (7 PM ET or later: TNF / SNF / MNF) stands
// alone, so its ladder parlay is same-game: 2-3 near-maxed Kalshi rungs (as Ladder longshots: 1-2 below the top, mid
// 5-30c) for soft-matchup / Value-tag players, else the game's high-usage players. Chance = the Kalshi mids multiplied x
// our sims' correlation lift (saved draws), the married chance tested on 8,966 same-game pairs. Books price same-game
// parlays with their own correlation, so the card's Fair is the price to beat, not the Kalshi product.
function primetimeLadders(){
  if (!D.dvp || !(window.MKT && MKT.ladder)) return '';
  const KL = {rec: 'catches', rec_yds: 'rec yds', rush_yds: 'rush yds', pass_yds: 'pass yds'};
  const posKey = p => p.pos === 'QB' ? 'QB' : p.pos === 'RB' ? 'RB' : p.pos === 'TE' ? 'TE' : 'WR';
  const mu = (p, s_) => p.s[s_] ? p.s[s_][MEAN] : 0;
  const now = Date.now(), out = [];
  const amO = q => q >= .5 ? -Math.round(100 * q / (1 - q)) : Math.round(100 * (1 - q) / q);
  for (const g of D.games){
    if (!g.sort || new Date(g.sort) <= now || +String(g.sort).slice(11, 13) < 19) continue;
    const pool = D.players.filter(x => x.g === g.id && ((x.s.tgt && x.s.tgt[MEAN] >= 3) || (x.s.car && x.s.car[MEAN] >= 8) || (x.s.att && x.s.att[MEAN] >= 20)));
    const top = new Set([g.home, g.away].flatMap(t => pool.filter(p => p.t === t).sort((a, b) => (mu(b, 'tgt') + mu(b, 'car') + mu(b, 'att') / 4) - (mu(a, 'tgt') + mu(a, 'car') + mu(a, 'att') / 4)).slice(0, 3)));
    const cands = [];
    for (const p of pool){
      const k = posKey(p), r = dvpRow(k, p.o), soft = !!(r && r.rank <= 8), tk = MKT.tagKind ? MKT.tagKind(g.id, p.n) : null, use = top.has(p);
      if (!soft && !tk && !use) continue;
      const rungs = MKT.ladder(g.id, p.id, p.n).filter(u => rungOk(p, u));
      if (!rungs.length) continue;
      const u = rungs.sort((a, b) => (b.k === tk) - (a.k === tk) || Math.abs(a.p - .30) - Math.abs(b.p - .30))[0];   // ours closest to 30%
      const why = [soft ? `soft: ${ord(r.rank)} most to ${k}s` : null, tk && tk === u.k ? 'Value tag' : null, !soft && !(tk && tk === u.k) ? 'high usage' : null].filter(Boolean);
      cands.push({g, p, u, why, sc: (soft ? 2 : 0) + (tk === u.k ? 2 : 0)});
    }
    if (cands.length < 2) continue;
    const DW = scriptDraws(g.id), ready = DW && DW !== 'load' && DW.p;
    const hits = L => { const a = ready && DW.p[L.p.k] ? sdecode(DW.p[L.p.k][L.u.k]) : null; return a ? Array.from(a, v => v > L.u.l) : null; };
    const H = new Map(cands.map(L => [L, hits(L)]));
    const lift = c => { const hs = c.map(L => H.get(L)); if (hs.some(h => !h)) return null;
      const n = hs[0].length; let j = 0; for (let i = 0; i < n; i++) if (hs.every(h => h[i])) j++;
      const ind = hs.reduce((s, h) => s * (h.filter(Boolean).length + .5) / (n + 1), 1); return ((j + .5) / (n + 1)) / ind; };
    const combos = [];
    for (let i = 0; i < cands.length; i++) for (let j = i + 1; j < cands.length; j++){
      combos.push([cands[i], cands[j]]); for (let k = j + 1; k < cands.length; k++) combos.push([cands[i], cands[j], cands[k]]); }
    const val = c => { const m = c.reduce((s, L) => s * L.u.mid, 1), lf = lift(c);
      return {c, m, lf, p: lf != null ? Math.min(m * lf, ...c.map(L => L.u.mid)) : m, kd: c.reduce((s, L) => s / L.u.cost, 1), sc: c.reduce((s, L) => s + L.sc, 0)}; };
    // rank: the most signals, then legs that rise together (lift), then the likeliest; a player in at most 2 shown
    const used = {}, rank = (a, b) => b.sc - a.sc || (b.lf ?? 1) - (a.lf ?? 1) || b.p - a.p;
    const pick = (n, k) => { const o = [];
      for (const x of combos.filter(c => c.length === n).map(val).filter(x => x.p >= .004).sort(rank)){   // 250-1 or shorter
        if (o.length >= k) break;
        if (x.c.some(L => (used[L.p.k] || 0) >= 2)) continue;
        x.c.forEach(L => { used[L.p.k] = (used[L.p.k] || 0) + 1; }); o.push(x); }
      return o; };
    const P = [...pick(2, 2), ...pick(3, 1)];
    if (P.length) out.push({g, P, ready});
  }
  if (!out.length) return '';
  const legOf = L => ({gid: L.g.id, start: L.g.sort, kind: L.u.k, side: 'o', line: L.u.l, name: MKT.norm(L.p.n), lab: `${L.p.n} ${Math.round(L.u.l + .5)}+ ${KL[L.u.k]}`, p: +L.u.mid.toFixed(4), price: amO(L.u.cost), book: 'Kalshi'});
  if (MKT.reg) out.forEach(G => G.P.forEach(x => MKT.reg('Primetime ladders (experimental)', `${x.c.length}-leg`, x.c.map(legOf), x.p, x.kd)));
  const card = x => `<div class="dcp"><div class="dch"><span class="slot ls">${x.c.length} legs</span>same game${x.lf != null ? ` · <span class="${x.lf >= 1.1 ? 'good' : x.lf < .95 ? 'bad' : ''}" title="How much more often our simulated games see these legs hit together than if they were unrelated">legs move together ×${x.lf.toFixed(2)}</span>` : ''}</div>
      <ul>${x.c.map(L => `<li><b>${esc(L.p.n)} ${Math.round(L.u.l + .5)}+ ${KL[L.u.k]}</b><span>${esc(L.p.t)} · Kalshi ${L.u.price}</span></li><li class="lwhy">${L.why.map(w => `<span class="ladc${/soft/.test(w) ? ' soft' : ''}">${esc(w)}</span>`).join('')}${projChip(L.p, L.u)}</li>`).join('')}</ul>
      <div class="dcs"><span>hits <b>${pct1(x.p)}</b></span><span title="The least a same-game parlay should pay for this to break even">fair <b>${american(x.p)}</b></span><span class="muted" title="The legs’ Kalshi prices multiplied: what separate legs would pay if they were unrelated">legs multiplied ${x.kd.toFixed(0)}x</span></div></div>`;
  return `<section class="panel"><div class="phead"><h2>Primetime ladders <span class="exp3">experimental</span></h2><span class="small muted">same-game · 2-3 achievable rungs</span></div>
    <details class="how"><summary>What this is</summary><p class="note">A primetime game stands alone, so its ladder parlay is a same-game parlay: two or three rungs inside our projection’s realistic range (our chance 20-45%) for players with a soft matchup, a Value tag or the most volume in the game, where our projection backs the rung. <b>Hits</b> = Kalshi’s prices for the legs multiplied, times how much more often our simulated games see them hit together (a QB’s yards and his top receiver’s rise together), the method that matched real results on 8,966 same-game pairs. <b>Fair</b> is the price to beat: books price same-game parlays with their own correlation, so take one only where your book pays more than Fair. Tracked on the Profit page.</p></details>
    ${out.map(G => `<div class="scteam"><h3>${esc(G.g.away)} @ ${esc(G.g.home)}</h3><span class="small muted">${esc(G.g.kickoff)}${G.ready ? '' : ' · loading simulated games…'}</span></div><div class="dcg">${G.P.map(card).join('')}</div>`).join('')}</section>`;
}
// Path longshots (owner's idea 2026-10-19): "a combination of outcomes with actual paths to happening based on usage,
// matchups, rotations, game scripts and odds discrepancies." Per game: a script our sims rate 3+ pts above the
// prediction markets (MKT.scriptMarket); the legs it feeds (rung lift 1.15+ in that script, from the saved draws); each
// leg scored on five factors: usage (team top 3 by volume / starting QB), matchup (defense top 8 most allowed to the
// position), rotation (a teammate out or a snap jump, from the Summary tab), script, price (our market-anchored chance
// beats the best price at the owner's books by 5%+, or a Value tag). 3+ factors including the script. One leg per game
// so books pay the legs multiplied; 2-3 games. Every leg records its factors so the Profit page can learn which matter.
const PATH_KL = {rec: 'catches', rec_yds: 'rec yds', rush_yds: 'rush yds', pass_yds: 'pass yds'};
function roleOf(g, p){
  const e = ((D.spot || {})[g.id] || []).find(x => x.name === p.n); if (!e) return null;
  const out = (e.reasons || []).map(r => /^(.+?) OUT -- (\d+)% of .+? (targets|carries)/.exec(r)).find(Boolean);
  if (out) return `${out[1]} out: ${out[2]}% of ${out[3]} open`;
  const sn = (e.facts || []).map(f => /snaps jumped to (\d+)%/.exec(f)).find(Boolean);
  return sn ? `snaps up to ${sn[1]}%` : null;
}
function pathLegs(g){
  const T = D.teams[g.id] || {}, H = T[g.home], A = T[g.away];
  if (!H || H.spread_line == null || !(window.MKT && MKT.altLadder && MKT.scriptMarket)) return [];
  const DW = scriptDraws(g.id); if (!DW || DW === 'load' || !DW.g) return [];
  const L = H.spread_line, tl = H.total_line, homeFav = L > 0 || (L === 0 && H.win_prob >= .5);
  const fav = homeFav ? g.home : g.away, dog = homeFav ? g.away : g.home;
  const M = sdecode(DW.g.margin), TT = sdecode(DW.g.total);
  const SC = [[`${fav} pulls away`, (m) => (homeFav ? m : -m) >= 10, {kind: 'margin', team: fav, min: 10}],
              ['Close game', (m) => Math.abs(m) <= 7, {kind: 'close', max: 7}],
              [`${dog} wins`, (m) => homeFav ? m < 0 : m > 0, {kind: 'win', team: dog}],
              ['Shootout', (m, t) => t > tl, {kind: 'over', at: tl}],
              ['Low-scoring', (m, t) => t < tl, {kind: 'under', at: tl}]];
  const scripts = SC.map(([name, f, spec]) => { const mask = Array.from(M, (m, i) => f(m, TT[i])), n = mask.filter(Boolean).length;
    const mk = MKT.scriptMarket(g.id, spec, {home: g.home, away: g.away, fav}); return {name, mask, n, ours: n / M.length, mk: mk ? mk.m : null}; })
    .filter(s => s.mk != null && s.ours - s.mk >= .03);
  if (!scripts.length) return [];
  const posKey = p => p.pos === 'QB' ? 'QB' : p.pos === 'RB' ? 'RB' : p.pos === 'TE' ? 'TE' : 'WR';
  const mu = (p, s_) => p.s[s_] ? p.s[s_][MEAN] : 0;
  const pool = D.players.filter(p => p.g === g.id);
  const usage = new Set([g.home, g.away].flatMap(t => { const tp = pool.filter(p => p.t === t);
    const qb = tp.filter(p => p.pos === 'QB').sort((a, b) => mu(b, 'att') - mu(a, 'att'))[0];
    return [qb, ...tp.filter(p => p.pos !== 'QB').sort((a, b) => (mu(b, 'tgt') + mu(b, 'car')) - (mu(a, 'tgt') + mu(a, 'car'))).slice(0, 3)].filter(Boolean); }));
  const lg = v => Math.log(v / (1 - v)), sg = v => 1 / (1 + Math.exp(-v));
  const legs = [];
  for (const p of pool){
    const k = posKey(p), r = dvpRow(k, p.o), soft = !!(r && r.rank <= 8), role = roleOf(g, p), use = usage.has(p);
    if (!use && !soft && !role) continue;
    const tk = MKT.tagKind ? MKT.tagKind(g.id, p.n) : null;
    const stats = p.pos === 'QB' ? ['pass_yds'] : p.pos === 'RB' ? ['rush_yds', 'rec_yds'] : ['rec_yds', 'rec'];
    for (const st of stats){
      const v = LAD_VOL[st]; if (v && mu(p, v[0]) < v[1]) continue;
      const x = DW.p && DW.p[p.k] ? sdecode(DW.p[p.k][st]) : null; if (!x) continue;
      const R = MKT.altLadder(g.id, p.n, st), main = R.find(u => u.main);
      const gap = main && main.fair != null && main.p > 0 && main.p < 1 ? lg(main.fair) - lg(main.p) : null;
      for (const u of R){
        if (!u.best || (main && u.l < main.l - 1e-9)) continue;                     // overs at or above the main line, at the owner's books
        const dO = u.best.o > 0 ? 1 + u.best.o / 100 : 1 + 100 / -u.best.o;
        if (dO < 2.0 || dO > 6) continue;                                          // achievable legs: about +100 to +500                                          // longshot legs: +150 to +900
        let fair = u.fair; if (fair == null && gap != null && u.p > 0 && u.p < 1) fair = sg(lg(u.p) + gap);
        const m = fair ?? u.mid, w = fair != null ? (main ? main.w : .25) : .5;
        if (m == null) continue;                                                    // never our number alone
        const q = u.p == null ? m : w * u.p + (1 - w) * m;
        if (q == null || q < .20 || q > .45) continue;                             // inside our projection's realistic range                                         //
        let a = 0; for (const z of x) if (z > u.l) a++;
        for (const s of scripts){
          let b = 0; for (let i = 0; i < x.length; i++) if (s.mask[i] && x[i] > u.l) b++;
          const lift = ((b + 1) / (s.n + 2)) / ((a + 1) / (x.length + 2)); if (lift < 1.15) continue;
          const ev = q * dO - 1, F = {usage: use, matchup: soft, rotation: !!role, script: true, price: ev >= .05 || tk === st};
          const nf = Object.values(F).filter(Boolean).length; if (nf < 3) continue;
          legs.push({g, p, st, u, q, dO, ev, lift, s, F, nf, role, r, k, tk});
        }
      }
    }
  }
  // per player-stat: its best rung (most factors, then the most in-script value)
  const best = {};
  for (const L of legs){ const key = L.p.k + '|' + L.st, sc = L.nf * 10 + L.q * L.lift * L.dO;
    if (!best[key] || sc > best[key].sc) best[key] = {...L, sc}; }
  return Object.values(best).sort((a, b) => b.sc - a.sc);
}
function pathWhy(L, k){
  return k === 'usage' ? (L.p.pos === 'QB' ? `QB · ${f0(L.p.s.att ? L.p.s.att[MEAN] : 0)} att` : `usage · ${f1((L.p.s.tgt ? L.p.s.tgt[MEAN] : 0) + (L.p.s.car ? L.p.s.car[MEAN] : 0))} touches`)
    : k === 'matchup' ? `matchup · ${ord(L.r.rank)} most to ${L.k}s` : k === 'rotation' ? L.role : k === 'script' ? `script · ${L.s.name} +${Math.round((L.lift - 1) * 100)}%`
    : L.ev >= .05 ? `price · +${Math.round(L.ev * 100)}% vs our chance` : 'price · Value tag';
}
function pathLongshots(){
  if (!D.dvp || !(window.MKT && MKT.altLadder)) return '';
  const now = Date.now(), per = [];
  for (const g of D.games){ if (g.sort && new Date(g.sort) <= now) continue; const L = pathLegs(g); if (L.length) per.push(L.slice(0, 3));
    if (MKT.regPath) L.forEach(x => MKT.regPath({gid: String(g.id), start: g.sort, name: MKT.norm(x.p.n), kind: x.st, line: x.u.l, p: +x.q.toFixed(4), price: x.u.best.o, book: x.u.best.k,
      sc: x.s.name, us: +x.s.ours.toFixed(3), mk: +x.s.mk.toFixed(3), lift: +x.lift.toFixed(2), f: Object.keys(x.F).filter(k => x.F[k]), why: Object.keys(x.F).filter(k => x.F[k]).map(k => pathWhy(x, k))})); }
  if (per.length < 2) return per.length || D.games.every(g => g.sort && new Date(g.sort) <= now) ? '' : `<section class="panel"><div class="phead"><h2>Path longshots</h2></div><p class="note">Building paths: needs two games with a script our sims rate above the market and legs it feeds. ${D.games.some(g => SCR[g.id] === undefined || SCR[g.id] === 'load') ? 'Loading simulated games…' : 'None today yet.'}</p></section>`;
  const combos = [], walk = (i, pk) => { if (pk.length >= 2) combos.push(pk); if (pk.length === 3) return;
    for (let j = i; j < per.length; j++) for (const L of per[j]) walk(j + 1, [...pk, L]); };
  walk(0, []);
  const val = c => ({c, p: c.reduce((s, L) => s * L.q, 1), d: c.reduce((s, L) => s * L.dO, 1), nf: c.reduce((s, L) => s + L.nf, 0)});
  const used = {}, pick = (n, k) => { const o = [];
    for (const x of combos.filter(c => c.length === n).map(val).filter(x => x.p * x.d - 1 >= 0).sort((a, b) => b.nf / b.c.length - a.nf / a.c.length || (b.p * b.d) - (a.p * a.d))){
      if (o.length >= k) break; if (x.c.some(L => (used[L.p.k] || 0) >= 2)) continue;
      x.c.forEach(L => { used[L.p.k] = (used[L.p.k] || 0) + 1; }); o.push(x); }
    return o; };
  const P = [...pick(2, 2), ...pick(3, 2)];
  if (!P.length) return '';
  const FL = {usage: 'usage', matchup: 'matchup', rotation: 'rotation', script: 'script', price: 'price'};
  const legOf = L => ({gid: L.g.id, start: L.g.sort, kind: L.st, side: 'o', line: L.u.l, name: MKT.norm(L.p.n), lab: `${L.p.n} ${Math.ceil(L.u.l)}+ ${PATH_KL[L.st]}`,
                       p: +L.q.toFixed(4), price: L.u.best.o, book: L.u.best.k, sc: L.s.name, f: Object.keys(L.F).filter(k => L.F[k])});
  if (MKT.reg) P.forEach(x => MKT.reg('Path longshots', `${x.c.length}-leg`, x.c.map(legOf), x.p, x.d));
  const chip = (L, k) => { if (!L.F[k]) return '';
    const t = k === 'usage' ? (L.p.pos === 'QB' ? `QB · ${f0(L.p.s.att ? L.p.s.att[MEAN] : 0)} att` : `usage · ${f1((L.p.s.tgt ? L.p.s.tgt[MEAN] : 0) + (L.p.s.car ? L.p.s.car[MEAN] : 0))} touches`)
      : k === 'matchup' ? `matchup · ${ord(L.r.rank)} most to ${L.k}s` : k === 'rotation' ? L.role : k === 'script' ? `script · +${Math.round((L.lift - 1) * 100)}% if it happens`
      : L.ev >= .05 ? `price · +${Math.round(L.ev * 100)}% vs our chance` : 'price · Value tag';
    return `<span class="ladc ${k === 'matchup' || k === 'price' ? 'soft' : ''}">${esc(t)}</span>`; };
  const legHtml = L => `<li class="pth"><span class="psc">${esc(L.g.away)} @ ${esc(L.g.home)} · <b>${esc(L.s.name)}</b> <i title="Our simulated games vs the prediction markets">us ${pct(L.s.ours)} · market ${pct(L.s.mk)}</i></span></li>
      <li><b>${esc(L.p.n)} ${Math.ceil(L.u.l)}+ ${PATH_KL[L.st]}</b><span>${L.u.best.u ? `<a href="${esc(L.u.best.u)}" target="_blank" rel="noopener">${esc(L.u.best.name)} ${L.u.best.o > 0 ? '+' : ''}${L.u.best.o}</a>` : `${esc(L.u.best.name)} ${L.u.best.o > 0 ? '+' : ''}${L.u.best.o}`}</span></li>
      <li class="lwhy">${Object.keys(FL).map(k => chip(L, k)).join('')}<span class="ladc">us ${pct(L.q)}</span></li>`;
  const card = x => `<div class="dcp"><div class="dch"><span class="slot ls">${x.c.length} games</span>${x.nf} of ${x.c.length * 5} factors</div><ul>${x.c.map(legHtml).join('')}</ul>
      <div class="dcs"><span>pays <b>${x.d.toFixed(0)}x</b></span><span>$5 → <b>$${(5 * x.d).toFixed(0)}</b></span><span>hits <b>${pct1(x.p)}</b></span><span class="${x.p * x.d - 1 >= .05 ? 'good' : 'muted'}">edge ${x.p * x.d - 1 >= 0 ? '+' : '−'}${Math.abs(Math.round((x.p * x.d - 1) * 100))}%</span></div></div>`;
  return `<section class="panel"><div class="phead"><h2>Path longshots</h2><span class="small muted">usage · matchups · rotations · game scripts · odds</span></div>
    <details class="how"><summary>How these are built</summary><p class="note">Each leg needs a path. First a game script our simulated games rate at least 3 points more likely than the prediction markets do (shown as us vs market). Then a rung that script feeds: the line clears 15%+ more often when it happens. The player needs at least three of five reasons: <b>usage</b> (his team’s top three by touches, or the starting QB), <b>matchup</b> (a defense in the 8 that allow the most to his position), <b>rotation</b> (a teammate out or a snap jump), <b>script</b>, and <b>price</b> (our market-anchored chance beats the best price at your books by 5%+, or a Value tag). Each leg sits inside our projection’s realistic range (our chance 20-45%, paying about +100 to +500), and legs come from different games, so the book pays them multiplied. <b>Hits</b> uses each leg’s market-anchored chance (not the script-boosted one). Every leg records its reasons, so the Profit page will show which ones actually matter.</p></details>
    <div class="dcg">${P.map(card).join('')}</div></section>`;
}
// Learning builder candidates (builder.py, owner 2026-10-22): in freeze mode, every over the page could bet at the owner's
// books (QB pass yds; RB rush / rec yds; WR / TE rec yds, catches; anytime TD) with its path reasons -- usage, matchup
// (DvP top 8), rotation (roleOf), script (a script our sims rate 3+ pts above the markets lifts his line 15%+) -- and
// whether it fits: the script, or 2+ of the others. Graded later for every candidate, picked or not.
function learnCandsNFL(){
  const now = Date.now();
  for (const g of D.games){
    if (!g.sort || new Date(g.sort) <= now) continue;
    const pool = D.players.filter(p => p.g === g.id), mu = (q, s_) => q.s[s_] ? q.s[s_][MEAN] : 0;
    const top = new Set([g.home, g.away].flatMap(t => { const tp = pool.filter(q => q.t === t);
      const qb = tp.filter(q => q.pos === 'QB').sort((a, b) => mu(b, 'att') - mu(a, 'att'))[0];
      return [qb, ...tp.filter(q => q.pos !== 'QB').sort((a, b) => (mu(b, 'tgt') + mu(b, 'car')) - (mu(a, 'tgt') + mu(a, 'car'))).slice(0, 3)].filter(Boolean); }));
    const sc = gameScriptsOf(g), DW = SCR[g.id];
    for (const p of pool){
      if (p.pos === 'QB' ? mu(p, 'att') < 20 : mu(p, 'tgt') + mu(p, 'car') < 3) continue;
      const k = p.pos === 'QB' ? 'QB' : p.pos === 'RB' ? 'RB' : p.pos === 'TE' ? 'TE' : 'WR', r = dvpRow(k, p.o);
      const base = [top.has(p) ? 'usage' : null, r && r.rank <= 8 ? 'matchup' : null, roleOf(g, p) ? 'rotation' : null].filter(Boolean);
      const stats = p.pos === 'QB' ? ['pass_yds'] : p.pos === 'RB' ? ['rush_yds', 'rec_yds', 'td'] : ['rec_yds', 'rec', 'td'];
      for (const st of stats){
        const v = LAD_VOL[st]; if (v && mu(p, v[0]) < v[1]) continue;
        const ss = p.s[WHY_ST[st] || st] || p.s[st];
        let script = null;
        const x = sc && sc.length && DW && DW.p && DW.p[p.k] ? sdecode(DW.p[p.k][WHY_ST[st] || st]) : null;
        if (x){ const med = st === 'td' ? 0 : ((ss || [])[MED] ?? 0); let a = 0; for (const z of x) if (z > med) a++;
          for (const s_ of sc){ let b = 0; for (let i = 0; i < x.length; i++) if (s_.mask[i] && x[i] > med) b++;
            if (((b + 1) / (s_.n + 2)) / ((a + 1) / (x.length + 2)) >= 1.15){ script = s_.name; break; } } }
        const f = script ? [...base, 'script'] : base, fit = !!script || base.length >= 2;
        MKT.cand(g.id, g.sort, p.n, st, null, null, f, fit);              // our chance at the books' main line: the props file's
      }
    }
  }
}
function renderParlays(){
  if (window.MKT && MKT.FREEZE && MKT.cand) try { learnCandsNFL(); } catch (e) {}
  const ladder = (window.MKT && MKT.dailyCard ? MKT.dailyCard() : '') + pathLongshots() + ladderCard() + primetimeLadders() + ladderLongshots();
  const C = (D.sgp && D.sgp.cross) || [];
  const tiers = tierParlays() + (window.MKT && MKT.tagParlays ? MKT.tagParlays({sport: 'nfl', title: 'Value-tag parlays (higher odds)',
    games: D.games.map(g => ({id: g.id, label: `${g.away} @ ${g.home}`, start: g.sort}))}) : '');
  if (window.MKT && MKT.reg){                                      // freeze mode: record every model parlay for the Results page
    const byLab = Object.fromEntries(D.games.map(g => [`${g.away} @ ${g.home}`, g])), byId = Object.fromEntries(D.games.map(g => [g.id, g]));
    const PK = {anytime_td: 'td', rec: 'rec', rec_yds: 'rec_yds', rush_yds: 'rush_yds', pass_yds: 'pass_yds'};
    const leg = (L, g) => { g = g || {};
      const base = {gid: g.id || null, start: g.sort || null, lab: L.lab, p: L.p, price: L.price, book: L.price ? 'Kalshi' : null};
      if (L.kind === 'win') return {...base, kind: 'ml', side: L.team === g.home ? 'home' : 'away', line: null, name: null};
      if (L.kind === 'total') return {...base, kind: 'total', side: /^under/i.test(L.lab) ? 'under' : 'over', line: L.line, name: null};
      return {...base, kind: PK[L.kind] || L.kind, side: 'o', line: L.kind === 'anytime_td' ? .5 : L.line, name: MKT.norm(L.lab.replace(/ (anytime TD|over .*)$/, ''))}; };
    C.forEach(c => MKT.reg('Cross-game parlays', c.title, c.legs.map(L => leg(L, byLab[L.game])), c.p, c.kalshi ? 1 / c.kalshi : null));
    // same-game parlays on each game's page: every script at every size, both models
    for (const [lab, S] of [['Same-game parlays', D.sgp], ['Same-game parlays (market-blind)', D.alt && D.alt.sgp]]){
      if (!S || !S.games) continue;
      for (const [gid, G] of Object.entries(S.games)){ const g = byId[gid]; if (!g) continue;
        for (const P of G.parlays || []) for (const r of P.rows || []){
          const legs = (P.legs || []).slice(0, r.k).map(L => leg(L, g)); if (legs.length < r.k) continue;
          MKT.reg(lab, `${P.name} · ${r.k} legs`, legs, r.p, r.kalshi ? 1 / r.kalshi : null); } }
    }
  }
  if (!C.length) return ladder + tiers + '<section class="panel"><div class="empty">No cross-game parlays this week yet.</div></section>';
  const day = s => { const d = new Date(String(s).slice(0, 10) + 'T12:00:00'); return isNaN(d) ? s : d.toLocaleDateString([], {weekday: 'short', month: 'short', day: 'numeric'}); };
  const card = c => `<div class="sgp"><h3>${esc(c.title)} <small>${day(c.when)} · ${c.games} game${c.games === 1 ? '' : 's'} · ${c.legs.length} legs</small></h3>
    <ol>${c.legs.map(L => `<li>${esc(L.lab)} <span class="small muted">${esc(L.game)}</span>${srcTag(L)}<span class="r">${pct(L.p)}${L.price ? ` · ${window.MKT && MKT.px ? MKT.px(L.price, 'kalshi') : Math.round(L.price * 100) + '¢'}` : ''}</span></li>`).join('')}</ol>
    ${(() => { const byG = Object.fromEntries(D.games.map(x => [`${x.away} @ ${x.home}`, x.id]));
      const M = married(c.legs.map(L => ({...L, gid: byG[L.game]})), null, c.p, c.legs.reduce((s, L) => s * L.p, 1));
      return `<table><thead><tr><th></th><th>Hits</th><th>Fair</th><th>Sims only</th><th>Kalshi</th></tr></thead><tbody><tr><td>all ${c.legs.length} legs<small class="muted" style="display:block">${M.n}/${M.of} priced</small></td><td><b title="Market-anchored leg chances times our sims’ correlation lift">${pct1(M.p)}</b></td><td>${esc(american(M.p))}</td><td>${pct1(c.p)}</td><td>${c.kalshi ? american(c.kalshi) : '–'}</td></tr></tbody></table>`; })()}
    ${gamblyBtns(c.legs.map(L => ({lab: `${L.lab} (${L.game})`})), [c.legs.length], '')}
    ${window.SLIP ? `<div class="gbtns"><span class="small muted">My parlay:</span>${SLIP.allBtn(crossSlipLegs(c.legs), '+ add parlay')}</div>` : ''}</div>`;
  return ladder + tiers + `<section class="panel"><div class="phead"><h2>Long parlays by kickoff window and day</h2><span class="small muted">each game's strongest legs, stacked</span></div>
    <p class="note">Built from each game's most likely same-game parlay: its first legs (kept together, so their link counts), as many from each game as it takes to reach 5+ legs. The full-day parlay covers every game that day. Games are simulated separately, so across games the chances multiply. These overlap with the per-game parlays on purpose.</p>
    <div class="sgp-grid">${C.map(card).join('')}</div></section>`;
}
/* ---------------- game summary: players worth a look, tiered, with the reasons (display only) ---------------- */
// Projected box score (owner 2026-10-24: "full box scores... x person 27 points 6 rebounds"; highly experimental): each
// player's simulated average (50,000 games), rounded largest-remainder within the team so the parts add up (catches =
// the QB's completions, receiving yards = his passing yards, receiving TDs = his TDs). TDs from project.py _box.json.
function lrRound(vals){
  const tot = Math.round(vals.reduce((a, b) => a + b, 0)), fl = vals.map(v => Math.floor(v));
  let rem = tot - fl.reduce((a, b) => a + b, 0);
  vals.map((v, i) => [v - Math.floor(v), i]).sort((a, b) => b[0] - a[0]).forEach(([, i]) => { if (rem > 0){ fl[i]++; rem--; } });
  return fl;
}
function boxPanel(g){
  const mean = (p, k) => p.s[k] ? p.s[k][MEAN] : 0, td = (p, i) => p.tdm ? p.tdm[i] : 0;
  const nm = p => `<td class="l"><b>${esc(p.n)}</b> <span class="small muted">${esc(p.pos)}</span></td>`;
  const tbl = (head, rows) => rows.length ? `<div class="tw"><table class="t nbx"><thead><tr>${head.map((h, i) => `<th${i ? '' : ' class="l"'}>${h}</th>`).join('')}</tr></thead><tbody>${rows.join('')}</tbody></table></div>` : '';
  // owner 2026-10-24: highlight box numbers above the books' main line (an over lean); strip lists them with the line
  const LN = (p, k) => window.MKT && MKT.propLine ? MKT.propLine(g.id, p.n, k) : null;
  const side = t => {
    const P = PL(g).filter(p => p.t === t), over = [];
    const ov = (p, k, v, lab) => { const l = LN(p, k); if (l == null || !(v > l)) return v; over.push(`<span class="bxoc">${esc(p.n.replace(/\s+(Jr\.?|Sr\.?|II|III|IV|V)$/, '').split(' ').slice(-1)[0])} ${lab} <b>${v}</b> <i>line ${l}</i></span>`); return `<span class="bxo" title="above the books’ line ${l}">${v}</span>`; };
    const qb = P.filter(p => p.s.att).sort((a, b) => mean(b, 'att') - mean(a, 'att'))[0];
    const rec = P.filter(p => mean(p, 'tgt') >= 1).sort((a, b) => mean(b, 'rec_yds') - mean(a, 'rec_yds'));
    const ru = P.filter(p => mean(p, 'car') >= 1).sort((a, b) => mean(b, 'rush_yds') - mean(a, 'rush_yds'));
    const R = (L, f) => lrRound(L.map(f));
    const rc = R(rec, p => mean(p, 'rec')), tg = R(rec, p => mean(p, 'tgt')), ry = R(rec, p => mean(p, 'rec_yds')), rt = R(rec, p => td(p, 1));
    const ca = R(ru, p => mean(p, 'car')), uy = R(ru, p => mean(p, 'rush_yds')), ut = R(ru, p => td(p, 0));
    const sum = a => a.reduce((x, y) => x + y, 0);
    const pass = qb ? [`<tr>${nm(qb)}<td>${sum(rc)}/${Math.max(Math.round(mean(qb, 'att')), sum(rc))}</td><td class="bxp">${ov(qb, 'pass_yds', sum(ry), 'pass yds')}</td><td>${sum(rt)}</td><td>${Math.round(mean(qb, 'ints'))}</td></tr>`] : [];
    const rush = ru.map((p, i) => `<tr>${nm(p)}<td>${ca[i]}</td><td class="bxp">${ov(p, 'rush_yds', uy[i], 'rush yds')}</td><td>${ut[i]}</td></tr>`);
    const recv = rec.map((p, i) => `<tr>${nm(p)}<td>${ov(p, 'rec', rc[i], 'rec')}</td><td>${Math.max(tg[i], rc[i])}</td><td class="bxp">${ov(p, 'rec_yds', ry[i], 'rec yds')}</td><td>${rt[i]}</td></tr>`);
    return `<div class="nbxteam"><h3>${esc(t)} <span class="muted small">${f1(TM(g)[t].points)} pts</span></h3>
      ${tbl([`Passing`, 'C/ATT', 'YDS', 'TD', 'INT'], pass)}${tbl(['Rushing', 'CAR', 'YDS', 'TD'], rush)}${tbl(['Receiving', 'REC', 'TGT', 'YDS', 'TD'], recv)}${over.length ? `<div class="bxos"><span class="eyebrow">Above the books’ line</span>${over.join('')}</div>` : ''}</div>`;
  };
  return `<section class="panel" aria-label="Projected box score"><div class="phead"><h2>Projected box score <span class="exp3">highly experimental</span></h2>${modelSwitch(g)}<span class="muted small">${esc(g.away)} at ${esc(g.home)} · each player’s simulated average, rounded so team totals hold</span></div>
    <div class="nbxgrid">${side(g.away)}${side(g.home)}</div>
    <p class="note" style="margin:8px 0 0">One number per stat: the average of each player’s simulated games, rounded so the parts add up (catches = completions, receiving yards = passing yards, TDs to the players most likely to score them). Highlighted = above the books’ main line for that prop (an over lean; the chips list the line). A single projection, not a prediction of the exact game: the Projections tab shows each player’s range. Highly experimental. <a href="${/\/(weeks|blind)\//.test(location.pathname) ? '../' : ''}boxes.html">How accurate it has been, graded every week →</a></p></section>`;
}
function summaryPanel(g){
  const L = D.spot[g.id] || [];
  const row = c => `<div class="sp-row"><span class="tierb t${c.tier}">Tier ${c.tier}</span><div class="sp-main">
      <div class="sp-name"><b>${esc(c.name)}</b><span>${esc(c.team)} · ${esc(c.pos)}${c.label ? ' · ' + esc(c.label) : ''}</span></div>
      ${c.facts.length ? `<div class="sp-facts">${c.facts.map(esc).join(' · ')}</div>` : ''}
      ${c.reasons.map(r => `<div class="sp-why${/^tough/.test(r) ? ' neg' : ''}">↳ ${esc(r).replace(/ -- /g, ' — ')}</div>`).join('')}</div></div>`;
  return `<section class="panel sp"><div class="phead"><h2>${esc(g.away)} at ${esc(g.home)}: what changed</h2><span class="small muted">${esc(g.kickoff)}</span></div>
    ${L.length ? `<div class="sp-list">${L.map(row).join('')}</div>` : '<div class="empty" style="padding:8px 0">No notable changes for this game: no snap jumps, injuries opening up volume, or missing defenders.</div>'}
    <p class="note">Context only: the projections already include these.</p></section>`;
}
// Matchup map: where each offense goes (number = share of its passes or runs) and how this defense does there
// (color = success it allows vs the league in that spot; striped = few plays, so the color is mostly league average)
const FM_ROWS = [['deep', 'Deep 20+'], ['inter', 'Medium 10-19'], ['short', 'Short 1-9'], ['behind', 'Screens / behind line']];
const FM_COLS = [['left', 'Left'], ['middle', 'Middle'], ['right', 'Right']];
const FM_RUN = [['left|end', 'L end'], ['left|tackle', 'L tackle'], ['left|guard', 'L guard'], ['middle|', 'Middle'], ['right|guard', 'R guard'], ['right|tackle', 'R tackle'], ['right|end', 'R end']];
// areas a missing defensive starter usually covers (boxscore/defout.py roles), for the dashed outline
const FM_COVER = {CB1: ['short|left', 'short|right', 'inter|left', 'inter|right', 'deep|left', 'deep|right'], CB2: ['short|left', 'short|right', 'inter|left', 'inter|right', 'deep|left', 'deep|right'],
  CB3: ['short|middle', 'inter|middle', 'behind|left', 'behind|right'], S: ['deep|left', 'deep|middle', 'deep|right', 'inter|middle'],
  LB: ['short|middle', 'inter|middle', 'behind|middle', 'run|middle|', 'run|left|guard', 'run|right|guard'],
  DL: ['run|middle|', 'run|left|guard', 'run|right|guard', 'run|left|tackle', 'run|right|tackle', 'run|left|end', 'run|right|end']};
function fmZoneName(z){
  const [a, b, c] = z.split('|');
  if (a === 'run') return b === 'middle' ? 'runs up the middle' : `runs off the ${b} ${c}`;
  return `${{deep: 'deep', inter: 'medium', short: 'short', behind: 'screen/behind-the-line'}[a]} ${b} passes`;
}
function fmCell(c, def, off, z, outs){
  if (!c) return '<div class="fm-c"><small class="muted">–</small></div>';
  const ob = (outs || []).filter(o => (FM_COVER[o.role] || []).includes(z)).map(o => o.role);
  const e = c.da - c.lg, a = Math.min(Math.abs(e) / .10, 1) * .55, thin = c.n < 12;
  const bg = Math.abs(e) < .015 ? '' : `background-color:color-mix(in srgb,var(${e > 0 ? '--good' : '--bad'}) ${Math.round(a * 100)}%,var(--sunk))`;
  const W = c.who || [], unit = z.startsWith('run') ? 'carries' : 'targets';
  const wh = W.length ? `<span class="wh">${W.slice(0, 2).map(([n, e], i) => `<span>${i ? ' · ' : ''}${esc(n)} <em>${(+e).toFixed(1)}</em></span>`).join('')}</span>` : '';
  const tip = `${off}: ${Math.round(c.s * 100)}% of its ${z.startsWith('run') ? 'runs' : 'passes'} go here${W.length ? ` (likeliest: ${W.map(([n, e]) => n + ' ' + e + ' ' + unit).join(', ')})` : ''}. ${def} allows success on ${Math.round(c.da * 100)}% of these (league ${Math.round(c.lg * 100)}%)${thin ? '; few plays here, so mostly league average' : ''}.`;
  return `<div class="fm-c${thin ? ' thin' : ''}${ob.length ? ' out' : ''}" style="${bg}" title="${esc(tip + (ob.length ? ' Missing here: ' + ob.join(', ') + '.' : ''))}"><b>${Math.round(c.s * 100)}%</b><small>${Math.abs(e) < .015 ? 'even' : (e > 0 ? '+' : '−') + Math.round(Math.abs(e) * 100) + ' pts'}</small>${wh}${ob.length ? `<span class="ob">${[...new Set(ob)].join(' · ')} out</span>` : ''}</div>`;
}
function mapPanel(g){
  const M = D.fmap[g.id];
  const block = off => {
    const X = M[off]; if (!X) return ''; const C = X.cells, def = X.vs;
    const outs = ((D.defout || {})[g.id] || {})[def] || [];
    const RN = {CB1: 'top corner', CB2: 'No. 2 corner', CB3: 'slot corner', S: 'safety', LB: 'linebacker', DL: 'D-lineman'};
    const miss = outs.length ? `<div class="fm-miss"><b>${esc(def)} missing:</b> ${outs.map(o => `${esc(o.name)} (${esc(RN[o.role] || o.role)}${o.status && o.status !== 'out' ? ', ' + esc(o.status) : ''})`).join(', ')}
      <small>Dashed boxes are where they usually play. In 2022-26 games, a missing starter changed the success allowed in his area by only about 1-2 points (not enough to trust), so the colors stay as they are.</small></div>` : '';
    const grid = `<div class="fm-grid"><span></span>${FM_COLS.map(([, l]) => `<span class="cl">${l}</span>`).join('')}
      ${FM_ROWS.map(([r, lab], i) => `${i === 3 ? '<div class="fm-los"><span>LINE OF SCRIMMAGE</span></div>' : ''}<span class="rl">${lab}</span>${FM_COLS.map(([c]) => fmCell(C[r + '|' + c], def, off, r + '|' + c, outs)).join('')}`).join('')}</div>`;
    const run = `<div class="fm-sub" style="margin-top:14px">Runs, by lane</div><div class="fm-run">${FM_RUN.map(([z]) => fmCell(C['run|' + z], def, off, 'run|' + z, outs)).join('')}${FM_RUN.map(([, l]) => `<span class="cl">${l}</span>`).join('')}</div>`;
    const Z = Object.entries(C).filter(([, c]) => c.s >= .05 && c.n >= 12).map(([z, c]) => ({z, c, e: c.da - c.lg, w: c.s * (c.da - c.lg)}));
    const line = ({z, c, e}) => `<li><b>${esc(fmZoneName(z)[0].toUpperCase() + fmZoneName(z).slice(1))}</b>: ${esc(off)} goes here ${Math.round(c.s * 100)}% of the time; ${esc(def)} allows success on <span class="${e > 0 ? 'up' : 'dn'}">${Math.round(c.da * 100)}%</span> (league ${Math.round(c.lg * 100)}%).${(c.who || []).length ? ` Likeliest: ${c.who.slice(0, 2).map(([n, x]) => `${esc(n)} (${(+x).toFixed(1)} ${z.startsWith('run') ? 'carries' : 'targets'})`).join(', ')}.` : ''}</li>`;
    const best = Z.filter(x => x.e > .02).sort((a, b) => b.w - a.w).slice(0, 3), tough = Z.filter(x => x.e < -.02).sort((a, b) => a.w - b.w).slice(0, 2);
    return `<div class="fm-off"><h3>${esc(off)} offense <small>vs ${esc(def)} defense</small></h3>${miss}<div class="fm-wrap"><div><div class="fm-sub">Passes, by depth and direction</div>${grid}</div>
      <div>${run}<div class="fm-sub" style="margin-top:16px">Where ${esc(off)} should find room</div><ul class="fm-list">${best.map(line).join('') || '<li class="muted">No spot stands out.</li>'}</ul>
      <div class="fm-sub" style="margin-top:12px">Where ${esc(def)} is tough</div><ul class="fm-list">${tough.map(line).join('') || '<li class="muted">No spot stands out.</li>'}</ul></div></div></div>`;
  };
  return `<section class="panel"><div class="phead"><h2>${esc(g.away)} at ${esc(g.home)}: matchup map</h2><span class="small muted">${esc(g.kickoff)}</span></div>
    <div class="fm-key"><span><b style="color:var(--ink)">22%</b> = how often the offense goes there</span><span><i style="background:color-mix(in srgb,var(--good) 55%,var(--sunk))"></i>defense gives up more than average</span><span><i style="background:color-mix(in srgb,var(--bad) 55%,var(--sunk))"></i>defense is tougher than average</span><span><i style="background:var(--sunk);background-image:repeating-linear-gradient(45deg,transparent 0 3px,rgba(127,127,127,.35) 3px 6px)"></i>few plays: mostly league average</span><span><b style="color:var(--ink)">Name 1.7</b> = expected targets (or carries) there this game</span></div>
    ${block(g.away)}${block(g.home)}
    <p class="note">Names: who is likeliest to get the ball in each spot, from this week's projected targets and carries times where his targets and carries have gone (players with few looks lean toward their position's usual spread). Where the ball was thrown, not full route charts, which aren't published during the season. Success = a play that improved the offense's expected points. This season so far (last season counts a third as much); each defense's rates lean toward the league average until it has enough plays in a spot. Context only: the projections already include each defense's overall strength.</p></section>`;
}
// Game script tab: the game lines (who's favored, by how much, the total) and, for the high-usage players and the ones
// facing a soft defense, each prop's over ladder (main line + alt lines + Kalshi rungs) at the viewer's books. Chance
// = ours weighted with the market's fair at that line (MKT.altLadder: sharp consensus / the books' alt-line fair / Kalshi
// mid), the same anchoring as everywhere else; "in script" scales it by how much our saved simulated games say that
// line rises or falls when the chosen script happens (e.g. the favorite pulls away by 10+).
const SCR = {};
const SCR_ROOT = (() => { const el = document.querySelector('script[src*="slip.js"]'); return el ? el.src.replace(/slip\.js.*$/, '') : '../'; })();
function scriptDraws(gid){
  if (SCR[gid] === undefined){
    SCR[gid] = 'load';
    fetch(`${SCR_ROOT}nfl/draws/${gid}.json?t=${Math.floor(Date.now() / 6e5)}`).then(r => r.ok ? r.json() : null).catch(() => null)
      .then(j => { SCR[gid] = j; if ((state.gtab === 'script' && state.game === gid) || state.view === 'parlays' || state.view === 'games' || state.view === 'board') render(); });
  }
  return SCR[gid];
}
function sdecode(a){
  if (!a) return null;
  const bin = atob(a.b), u8 = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) u8[i] = bin.charCodeAt(i);
  return a.t === 'i16' ? new Int16Array(u8.buffer) : u8;
}
const SC_KIND = {pass_yds: 'pass yds', rush_yds: 'rush yds', rec_yds: 'rec yds', rec: 'catches'};
const decO = o => o > 0 ? 1 + o / 100 : 1 + 100 / -o;
const amS = o => (o > 0 ? '+' : '') + o;
function scriptPanel(g){
  SGP_G = g; SGP_BLIND = false;
  const T = D.teams[g.id] || {}, H = T[g.home], A = T[g.away];
  if (!H || H.spread_line == null) return '<section class="panel"><div class="empty">No Vegas lines for this game yet.</div></section>';
  const L = H.spread_line, tl = H.total_line, homeFav = L > 0 || (L === 0 && H.win_prob >= .5);
  const fav = homeFav ? g.home : g.away, dog = homeFav ? g.away : g.home, F = homeFav ? H : A, U = homeFav ? A : H;
  const DW = scriptDraws(g.id), ready = !!(DW && DW !== 'load' && DW.g);
  const M = ready ? sdecode(DW.g.margin) : null, TT = ready ? sdecode(DW.g.total) : null;
  const SC = [['all', 'Expected', () => true, 'every simulated game', null],
              ['pull', `${fav} pulls away`, (m) => (homeFav ? m : -m) >= 10, `${fav} wins by 10+`, {kind: 'margin', team: fav, min: 10}],
              ['close', 'Close game', (m) => Math.abs(m) <= 7, 'decided by 7 or fewer', {kind: 'close', max: 7}],
              ['upset', `${dog} wins`, (m) => homeFav ? m < 0 : m > 0, `the underdog ${dog} wins`, {kind: 'win', team: dog}],
              ['shoot', 'Shootout', (m, t) => t > tl, `over ${tl} total points`, {kind: 'over', at: tl}],
              ['low', 'Low-scoring', (m, t) => t < tl, `under ${tl} total points`, {kind: 'under', at: tl}]];
  const sc = SC.find(x => x[0] === state.script) || SC[0];
  const share = f => { if (!M) return null; let c = 0; for (let i = 0; i < M.length; i++) if (f(M[i], TT[i])) c++; return c / M.length; };
  const mask = M && sc[0] !== 'all' ? Array.from(M, (m, i) => sc[2](m, TT[i])) : null, nIn = mask ? mask.filter(Boolean).length : 0;
  const sw = `<div class="seg scsw" role="group" aria-label="Game script">${SC.map(x => { const s_ = x[0] === 'all' ? null : share(x[2]);
    return `<button data-script="${x[0]}" aria-pressed="${sc[0] === x[0]}" title="${esc(x[3])}">${esc(x[1])}${s_ != null ? `<small>${pct(s_)}</small>` : ''}</button>`; }).join('')}</div>`;
  const O = D.sgp && D.sgp.games && D.sgp.games[g.id] && D.sgp.games[g.id].outlook;
  const head = `<div class="gsh"><span class="big">${esc(fav)} −${f1(Math.abs(L)).replace('.0', '')}</span>
      <span>Win: ${esc(fav)} <b>${pct(F.win_prob)}</b> · ${esc(dog)} <b>${pct(U.win_prob)}</b></span>
      <span>Total <b>${tl}</b> · over <b>${pct(H.p_over)}</b></span>
      <span class="muted small">Vegas team totals: ${esc(fav)} ${f1(F.vegas_implied)} · ${esc(dog)} ${f1(U.vegas_implied)}</span></div>`;
  // players: the starting QB, each team's top 3 by targets + carries, and anyone facing a top-8 soft defense for his position
  const posKey = p => p.pos === 'QB' ? 'QB' : p.pos === 'RB' ? 'RB' : p.pos === 'TE' ? 'TE' : 'WR';
  const mu = (p, s_) => p.s[s_] ? p.s[s_][MEAN] : 0;
  const pool = D.players.filter(p => p.g === g.id), cards = {[fav]: [], [dog]: []}, waiting = [], offs = [];
  const lift = (x, l) => { if (!mask || !x) return 1; let a = 0, b = 0;
    for (let i = 0; i < x.length; i++){ if (x[i] > l){ a++; if (mask[i]) b++; } }
    return ((b + 1) / (nIn + 2)) / ((a + 1) / (x.length + 2)); };
  for (const team of [fav, dog]){
    const mine = pool.filter(p => p.t === team);
    const qb = mine.filter(p => p.pos === 'QB').sort((a, b) => mu(b, 'att') - mu(a, 'att'))[0];
    const top = mine.filter(p => p.pos !== 'QB').sort((a, b) => (mu(b, 'tgt') + mu(b, 'car')) - (mu(a, 'tgt') + mu(a, 'car'))).slice(0, 3);
    for (const p of mine){
      const k = posKey(p), r = dvpRow(k, p.o), soft = !!(r && r.rank <= 8);
      const usage = p === qb || top.includes(p);
      if (!usage && !(soft && (mu(p, 'tgt') >= 3 || mu(p, 'car') >= 8 || mu(p, 'att') >= 20))) continue;
      const stats = p.pos === 'QB' ? ['pass_yds', ...(mu(p, 'car') >= 4 ? ['rush_yds'] : [])]
        : p.pos === 'RB' ? ['rush_yds', ...(mu(p, 'tgt') >= 3 ? ['rec_yds', 'rec'] : [])] : ['rec_yds', 'rec'];
      const ladders = [];
      for (const st of stats){
        const R = window.MKT && MKT.altLadder ? MKT.altLadder(g.id, p.n, st) : [];
        const x = ready && DW.p && DW.p[p.k] ? sdecode(DW.p[p.k][st]) : null;
        const main = R.find(r_ => r_.main);
        // a rung no book prices two-way (Kalshi-only): the main line's sharp fair carried up the ladder by our
        // distribution's shape (same log-odds gap to ours), never Kalshi's own mid (that made fake tail edges)
        const lg = v => Math.log(v / (1 - v)), sgm = v => 1 / (1 + Math.exp(-v));
        const gap = main && main.fair != null && main.p != null && main.p > 0 && main.p < 1 ? lg(main.fair) - lg(main.p) : null;
        const rows = R.filter(r_ => (r_.best || r_.any) && (!main || r_.l >= main.l - 1e-9)).map(r_ => {
          let fair = r_.fair, carried = false;
          if (fair == null && gap != null && r_.p != null && r_.p > 0 && r_.p < 1){ fair = sgm(lg(r_.p) + gap); carried = true; }
          const w = fair != null ? main.w : .5, m = fair ?? r_.mid, p0 = r_.p, q = m == null ? null : p0 == null ? m : w * p0 + (1 - w) * m;   // no market reference: no chance or edge shown
          const lf = lift(x, r_.l), qs = q == null ? null : Math.min(.99, q * lf), B = r_.best || r_.any, im = 1 / decO(B.o);
          // way off market: a book whose price says 8+ pts more or less than the fair (stale or mispriced line)
          const off = fair == null ? [] : (r_.all || []).map(z => ({...z, im: 1 / decO(z.o)})).filter(z => Math.abs(z.im - fair) >= .08);
          return {...r_, fair, carried, q, qs, lf, B, mineB: !!r_.best, off, ev: q != null ? q * decO(B.o) - 1 : null, evs: qs != null ? qs * decO(B.o) - 1 : null};
        }).slice(0, 5);
        rows.forEach(u => u.off.forEach(z => offs.push({p, st, u, z})));
        if (rows.length) ladders.push({st, rows, lf: rows[0].lf});
      }
      const c = {p, r, soft, usage, ladders, vol: p.pos === 'QB' ? mu(p, 'att') : mu(p, 'tgt') + mu(p, 'car')};
      if (ladders.length) cards[team].push(c); else waiting.push(c);
    }
    cards[team].sort((a, b) => sc[0] === 'all' ? b.vol - a.vol : Math.max(...b.ladders.map(l => l.lf)) - Math.max(...a.ladders.map(l => l.lf)) || b.vol - a.vol);
  }
  const showS = sc[0] !== 'all' && ready;
  const ecls = e => e == null ? '' : e >= .05 ? 'e2' : e >= .03 ? 'e1' : e < 0 ? 'neg' : '';
  const evp = e => e == null ? '–' : (e >= 0 ? '+' : '−') + Math.abs(e * 100).toFixed(Math.abs(e) < .1 ? 1 : 0) + '%';
  const sg = e => e == null ? '–' : (e >= 0 ? '+' : '−') + Math.abs(Math.round(e * 100)) + '%';
  const lad = Lz => `<table class="sct"><caption>${SC_KIND[Lz.st]}${showS ? ` · <span class="${Lz.lf >= 1.05 ? 'good' : ''}">${sg(Lz.lf - 1)} at the main line in this script</span>` : ''}</caption>
      <thead><tr><th>Over</th><th>Best price</th><th title="The market’s no-vig chance at this line">Fair</th><th title="Our chance weighted with the market’s fair">Chance</th>${showS ? '<th title="Chance if this script happens (our simulated games)">In script</th>' : ''}<th title="Expected return per bet at the best price${showS ? ', if the script happens' : ''}">Edge</th></tr></thead>
      <tbody>${Lz.rows.map(u => { const e = showS ? u.evs : u.ev;
        return `<tr class="${u.mineB ? '' : 'far'}"><td><b>${Math.ceil(u.l)}+</b>${u.main ? '<span class="mainl">main</span>' : ''}</td>
        <td>${u.B.u ? `<a href="${esc(u.B.u)}" target="_blank" rel="noopener">${amS(u.B.o)}</a>` : amS(u.B.o)}<small>${esc(u.B.name)}${u.mineB ? '' : ' · not yours'}</small>${u.off.length ? `<span class="offm" title="${esc(u.off.map(z => `${z.name} ${amS(z.o)} says ${pct(z.im)} vs fair ${pct(u.fair)}`).join(' · '))}">off market</span>` : ''}</td>
        <td>${u.fair != null ? (u.carried ? '≈' : '') + pct(u.fair) : u.mid != null ? pct(u.mid) + '<small>Kalshi mid</small>' : '–'}</td>
        <td>${u.q != null ? pct(u.q) : '–'}</td>${showS ? `<td><b>${u.qs != null ? pct(u.qs) : '–'}</b></td>` : ''}<td class="${ecls(e)}">${evp(e)}</td></tr>`; }).join('')}</tbody></table>`;
  const card = c => { const k = posKey(c.p), best = Math.max(...c.ladders.map(l => l.lf));
    const vol = c.p.pos === 'QB' ? `${f0(mu(c.p, 'att'))} pass att` : [mu(c.p, 'tgt') >= 2 ? `${f1(mu(c.p, 'tgt'))} tgt` : null, mu(c.p, 'car') >= 2 ? `${f1(mu(c.p, 'car'))} car` : null].filter(Boolean).join(' · ');
    return `<div class="scc"><div class="ladh"><b>${esc(c.p.n)}</b> <span class="small muted">${esc(c.p.pos)} · vs ${esc(c.p.o)}</span>
      ${c.usage ? `<span class="ladc" title="Projected volume (top 3 on his team by targets + carries, or the starting QB)">${esc(vol)}</span>` : ''}
      ${c.soft ? `<span class="ladc soft" title="${esc(c.p.o)} gives up the ${ord(c.r.rank)} most to ${k === 'WR' ? 'wide receivers' : k + 's'} (defense vs position)">soft: ${ord(c.r.rank)} most to ${k}s</span>` : ''}
      ${showS ? `<span class="ladc ${best >= 1.05 ? 'up' : best <= .95 ? 'dn' : ''}">script ${sg(best - 1)}</span>` : ''}</div>
      ${c.ladders.map(lad).join('')}</div>`; };
  const team = (t, lab) => cards[t].length ? `<div class="scteam"><h3>${esc(t)}</h3><span class="small muted">${lab}</span></div><div class="scgrid">${cards[t].map(card).join('')}</div>` : '';
  const wait = waiting.length ? `<details class="ladw"><summary>${waiting.length} more player${waiting.length === 1 ? '' : 's'} · no lines posted yet</summary><div class="ladr">${waiting.map(c => `<span class="lru"><b>${esc(c.p.n)}</b> <i>${esc(c.p.t)} ${esc(c.p.pos)}</i></span>`).join('')}</div></details>` : '';
  const none = !cards[fav].length && !cards[dog].length;
  // every book price 8+ pts from the fair, biggest gap first: green = the over is cheap there, red = priced too high (its under may be the value)
  offs.sort((a, b) => Math.abs(b.z.im - b.u.fair) - Math.abs(a.z.im - a.u.fair));
  const offBox = offs.length ? `<div class="offbox"><span class="eyebrow">Way off market</span><div class="offl">${offs.slice(0, 12).map(({p, st, u, z}) => { const cheap = z.im < u.fair;
      return `<span class="offc ${cheap ? 'cheap' : 'rich'}${z.mine ? ' mine' : ''}" title="${esc(z.name)} prices ${esc(p.n)} ${Math.ceil(u.l)}+ ${SC_KIND[st]} at ${amS(z.o)} (${pct(z.im)}); the market’s fair is ${u.carried ? 'about ' : ''}${pct(u.fair)}. ${cheap ? 'The over is cheap here: check the line hasn’t moved before betting.' : 'The over is priced too high here; the under at this book may be the value.'}">${z.u ? `<a href="${esc(z.u)}" target="_blank" rel="noopener">` : ''}<b>${esc(p.n)}</b> ${Math.ceil(u.l)}+ ${SC_KIND[st]} · ${esc(z.name)} ${amS(z.o)} <i>${pct(z.im)} vs fair ${u.carried ? '≈' : ''}${pct(u.fair)}</i>${z.u ? '</a>' : ''}</span>`; }).join('')}${offs.length > 12 ? `<span class="small muted">+${offs.length - 12} more</span>` : ''}</div>
    <div class="small muted">Green: the over is cheap at that book. Red: priced too high there (the under may be the value). Your books outlined.</div></div>` : '';
  return `<section class="panel" aria-label="Game script"><div class="phead"><h2>${esc(g.away)} at ${esc(g.home)}: game script</h2><span class="small muted">${esc(g.kickoff)} · Vegas model</span></div>
    ${head}
    ${O ? outlookBar(O) : ''}
    ${gameBets(g)}
    <div><span class="eyebrow">Props if the game goes this way</span>${sw}
      <div class="small muted">${ready ? (sc[0] === 'all' ? 'All simulated games. Pick a script to see whose lines rise and fall with it.' : `${pct(nIn / M.length)} of our simulated games: ${esc(sc[3])}. Players sorted by how much their lines rise in it.` + ` ${scriptCheck(g, sc[4], nIn / M.length)}`) : DW === 'load' ? 'Loading simulated games…' : 'Simulated games not available for this game; showing the expected view.'}</div></div>
    ${offBox}
    ${none ? '<p class="note">No prop lines posted yet for this game’s high-usage or soft-matchup players. Books post them about a day out.</p>' : team(fav, `favored by ${f1(Math.abs(L)).replace('.0', '')}`) + team(dog, 'underdog')}
    ${wait}
    <details class="how"><summary>How to read this</summary><p class="note">Players: each team’s starting QB and top 3 by projected targets + carries, plus anyone facing one of the 8 defenses that give up the most to his position. Each ladder is the main line and the higher alt lines (books’ alt lines and Kalshi’s rungs). <b>Fair</b> = the market’s no-vig chance at that line (the sharp consensus at the main line; the books’ own two-way alt prices for alts; ≈ = the main line’s fair carried up the ladder by our distribution’s shape where only Kalshi prices the rung). <b>Chance</b> = ours weighted with the fair, the same anchoring tested for the rest of the site. <b>In script</b> = that chance scaled by how often the line clears in our simulated games where the script happens vs all of them. <b>Edge</b> = expected return per bet at the best price at your books (green 3%+, bold 5%+; faded rows: only at other books). <b>Off market</b> = a book whose price implies 8+ points more or less than the fair. This view is for screening: the tested Value-tag rules are on the Markets page.</p></details>
  </section>`;
}
function gameDetail(){
  const g = D.games.find(x => x.id === state.game); if (!g) return '<div class="empty">No games.</div>';
  const gsw = (D.sgp || D.spot) ? `<div class="seg gsw" role="group" aria-label="Game view">${[['proj', 'Projections'], ['box', 'Box score'], ...(D.sgp ? [['script', 'Game script']] : []), ...(D.spot && g.id in D.spot ? [['sum', 'Summary']] : []), ...(D.fmap && D.fmap[g.id] ? [['map', 'Matchup map']] : []), ...(D.sgp ? [['sgp', 'Same-game parlays']] : [])].map(([k, l]) => `<button data-gtab="${k}" aria-pressed="${(state.gtab || 'proj') === k}">${l}</button>`).join('')}</div>` : '';
  if (state.gtab === 'box') return gsw + boxPanel(g);
  if (D.sgp && state.gtab === 'sgp') return gsw + sgpPanel(g);
  if (D.sgp && state.gtab === 'script') return gsw + scriptPanel(g);
  if (state.gtab === 'sum' && D.spot && g.id in D.spot) return gsw + summaryPanel(g);
  if (state.gtab === 'map' && D.fmap && D.fmap[g.id]) return gsw + mapPanel(g);
  return gsw + gameDetailMain(g);
}
// game bets for the parlay slip: moneyline, spread and total at the Vegas lines (judged in the same saved simulations)
// one tappable chip per bet option (adds it to the parlay slip; ✓ once added)
function betChip(L, lab, p, bk){                         // bk: best sportsbook price line (mkt.js MKT.bk), if any
  const on = window.SLIP && SLIP.has(L.id);
  const odd = p == null ? '' : american(p);
  return `<button type="button" class="bet${on ? ' on' : ''}"${window.SLIP ? ` data-slip="${esc(JSON.stringify(L))}" title="${on ? 'Remove from' : 'Add to'} my parlay"` : ' disabled'}><span>${esc(lab)}</span><b>${pct(p)}</b><small>${odd}</small><i>${on ? '✓' : '+'}</i>${bk || ''}</button>`;
}
const BK = (...a) => window.MKT && MKT.bk ? MKT.bk(...a) : '';
const betRow = (lab, chips) => `<div class="betrow"><span class="eyebrow">${lab}</span><div class="bets">${chips.join('')}</div></div>`;
function gameBets(g){
  const T = D.teams[g.id], H = T[g.home], A = T[g.away]; if (H.spread_line == null) return '';
  const L = H.spread_line, tl = H.total_line, glab = `${g.away} @ ${g.home}`, base = {sport: 'nfl', g: g.id, glab, draws: `nfl/draws/${g.id}.json`, model: 'vegas'};
  const sp = v => (v > 0 ? '+' : v < 0 ? '−' : '') + f1(Math.abs(v)).replace('.0', '');
  const leg = (id, extra, lab) => ({...base, id: `nfl|${g.id}|${id}`, side: 'over', lab, ...extra});
  return `<div class="betgrid">${[
    betRow('Moneyline', [betChip(leg(`win|${g.away}`, {stat: 'win', home: false}, `${g.away} moneyline`), g.away, A.win_prob, BK(g.id, 'ml', 'away', null, A.win_prob)),
                         betChip(leg(`win|${g.home}`, {stat: 'win', home: true}, `${g.home} moneyline`), g.home, H.win_prob, BK(g.id, 'ml', 'home', null, H.win_prob))]),
    betRow('Spread', [betChip(leg(`spread|${g.away}|${L}`, {stat: 'spread', home: false, line: L}, `${g.away} ${sp(L)}`), `${g.away} ${sp(L)}`, A.p_cover, BK(g.id, 'spread', 'away', -L, A.p_cover)),
                      betChip(leg(`spread|${g.home}|${L}`, {stat: 'spread', home: true, line: L}, `${g.home} ${sp(-L)}`), `${g.home} ${sp(-L)}`, H.p_cover, BK(g.id, 'spread', 'home', -L, H.p_cover))]),
    betRow(`Total ${tl}`, [betChip(leg(`total|over|${tl}`, {stat: 'total', line: tl}, `Over ${tl} (${glab})`), 'Over', H.p_over, BK(g.id, 'total', 'over', tl, H.p_over)),
                           betChip(leg(`total|under|${tl}`, {stat: 'total', line: tl, side: 'under'}, `Under ${tl} (${glab})`), 'Under', H.p_under, BK(g.id, 'total', 'under', tl, H.p_under))]),
  ].join('')}${window.MKT && MKT.bkNote ? MKT.bkNote(g.id) : ''}</div>`;
}
function gameDetailMain(g){
  const A = TM(g)[g.away], H = TM(g)[g.home];
  const side = (t, T, cls) => `<div class="side ${cls}"><span class="code">${esc(t)}</span><span class="pts">${f1(T.points)}</span><span class="muted small">Vegas ${f1(D.teams[g.id][t].vegas_implied)}</span></div>`;
  const rows = [
    ['Win probability', T => pct(T.win_prob)], ['Plays', T => f1(T.plays)],
    ['Cmp / Att', T => `${f1(T.cmp)} / ${f1(T.pass_att)}`], ['Passing yards', T => f0(T.pass_yds)],
    ['Sacks taken', T => f1(T.sacks)], ['Interceptions', T => f1(T.ints)],
    ['Rush attempts', T => f1(T.rush_att)], ['Rushing yards', T => f0(T.rush_yds)],
    ['Passing TDs', T => f1(T.pass_td)], ['Rushing TDs', T => f1(T.rush_td)], ['Field goals', T => f1(T.fg)],
  ];
  return `<section class="panel" aria-label="Game summary">
    <div class="phead"><h2>${esc(g.away)} at ${esc(g.home)}</h2>${modelSwitch(g)}<span class="muted small">${esc(g.kickoff)} · ${esc(spreadText(g))}${g.weather ? ` · <span${g.weather.flag ? ' class="wx-flag"' : ''} title="${g.weather.src === 'forecast' ? 'Kickoff forecast (Open-Meteo)' : 'Kickoff conditions'}">${esc(g.weather.text)}</span>` : ''}</span></div>
    <div class="two">
      <div style="display:grid;gap:16px;align-content:start">
        <div class="score">${side(g.away, A, 'away')}<span class="at">@</span>${side(g.home, H, 'home')}</div>
        <div class="winbar"><div class="bar" role="img" aria-label="Win probability ${esc(g.away)} ${pct(A.win_prob)}, ${esc(g.home)} ${pct(H.win_prob)}"><i style="flex:${A.win_prob};background:var(--away)"></i><i style="flex:${H.win_prob};background:var(--accent)"></i></div>
          <div class="lbl"><span>${esc(g.away)} ${pct(A.win_prob)}</span><span class="muted">projected points and win chance</span><span>${esc(g.home)} ${pct(H.win_prob)}</span></div></div>
        ${gameBets(g)}
        ${firstTdBlock(g)}
        ${window.MKT ? MKT.game(g.id, isBlind(g) ? 'blind' : 'vegas') : ''}
        ${isBlind(g) ? '<p class="note" style="margin:0">Market-blind: this game simulated without the Vegas spread or total (its own points forecast). Vegas lines shown for comparison.</p>' : ''}
      </div>
      <div><div class="boxhead"><span>${esc(g.away)}</span><span class="eyebrow">Team box (averages)</span><span>${esc(g.home)}</span></div>
        <table class="box">${rows.map(([l,f]) => `<tr><td>${f(A)}</td><td>${l}</td><td>${f(H)}</td></tr>`).join('')}</table></div>
    </div>
  </section>
  <section class="panel" aria-label="Player projections">
    <div class="phead">
      <div class="seg" role="group" aria-label="Stat group">${Object.entries(GROUPS).map(([k,G]) => `<button data-group="${k}" aria-pressed="${k===state.group}">${G.label}</button>`).join('')}</div>
      ${state.group === 'td' ? `<span class="key">${D.rz && D.rz.teams ? 'Red-zone numbers from' : 'Chance of scoring at least one rushing or receiving TD'}${D.rz ? `${D.rz.teams ? '' : ' · red-zone tags from'} <span class="seg" role="group" aria-label="Seasons" style="display:inline-flex">${['season','two'].map(k => `<button data-rzscope="${k}" aria-pressed="${k===state.rzscope}">${k === 'season' ? D.rz.labels.season + ' only' : D.rz.labels.two}</button>`).join('')}</span>` : ''}</span>` : '<span class="key"><span><i class="kw"></i>8 in 10 games</span><span><i class="kb"></i>middle half</span><span><i class="km"></i>median</span></span>'}
    </div>
    ${state.group === 'td' && D.rz && D.rz.teams ? tdBoard(g) : playerTable(g)}
    <p class="muted small" style="margin:0">Tap a player's name to slide the line and see the over/under chance, fair odds and live market prices.</p>
  </section>
  ${matchupPanel(g)}`;
}
const ord = n => { const s = ['th','st','nd','rd'], v = n % 100; return n + (s[(v - 20) % 10] || s[v] || s[0]); };
const rankText = r => r <= 16 ? `${ord(r)} most` : `${ord(33 - r)} fewest`;
const signPct = r => { const v = Math.round((r - 1) * 100); return (v >= 0 ? '+' : '−') + Math.abs(v) + '%'; };
const DVP_CELLS = [['QB','QB'],['RB','RB'],['WR','WR'],['SLOT','Slot WR'],['OUT','Outside WR'],['TE','TE']];
function dvpRow(key, team){ const v = D.dvp && D.dvp[key]; return v ? v.rows.find(r => r.team === team) : null; }
function matchupPanel(g){
  if (!D.dvp || !D.dvp.WR) return '';
  const side = (off, def) => {
    const cells = DVP_CELLS.map(([k, lbl]) => { const r = dvpRow(k, def); if (!r) return '';
      const cols = D.dvp[k].cols;
      if (r.season_rank) {
        const cls = shadeRank(r.season_rank);
        const tip = `<b>${esc(def)} vs ${lbl}s, this season</b> (${r.season_games} game${r.season_games === 1 ? '' : 's'})<br>` +
          cols.map((c, i) => `${esc(c)}: ${r.season_stats[i] == null ? '–' : f1(r.season_stats[i])} per game`).join('<br>') +
          `<br>${f1(r.season_ppr)} fantasy pts per game, ${rankText(r.season_rank)} in the league` +
          `<br><span style="opacity:.8">Blended with recent history: ${signPct(r.ratio)} vs average, ${rankText(r.rank)}</span>`;
        return `<div class="mcell" tabindex="0" data-tip="${esc(tip)}"><span class="eyebrow">${lbl}</span><span class="v ${cls}">${ord(r.season_rank)}</span><span class="muted small">${r.season_rank <= 16 ? 'most allowed' : ord(33 - r.season_rank) + ' fewest'} · ${f1(r.season_ppr)} pts/g</span><span class="muted small">blended ${signPct(r.ratio)}</span></div>`;
      }
      const cls = shadeRatio(r.ratio);
      return `<div class="mcell" title="${esc(def)} allows ${f1(r.ppr_adj)} fantasy pts per game to ${lbl}s (adjusted)"><span class="eyebrow">${lbl}</span><span class="v ${cls}">${signPct(r.ratio)}</span><span class="muted small">${rankText(r.rank)}</span></div>`; }).join('');
    const tagged = D.players.filter(p => p.g === g.id && p.t === off && p.tags && p.tags.length)
      .sort((a,b) => b.tags[0].strength - a.tags[0].strength);
    const list = tagged.length ? `<ul class="mlist">${tagged.map(p => `<li><span class="who">${esc(p.n)} <span class="pos">${esc(p.lab || p.pos)}</span></span>${p.tags.map(t => `${chip(t)}<span class="small muted">${esc(t.text)}</span>`).join(' ')}</li>`).join('')}</ul>`
      : `<p class="note">No ${esc(off)} player has a matchup outside the normal range.</p>`;
    const RN = {CB1: 'top CB', CB2: 'No. 2 CB', CB3: 'slot CB', S: 'S', LB: 'LB', DL: 'DL'};
    const dmiss = ((D.defout || {})[g.id] || {})[def] || [];
    const dline = dmiss.length ? `<p class="dmiss"><b>${esc(def)} missing:</b> ${dmiss.map(m => `${esc(m.name)} <span class="muted">(${RN[m.role] || m.role}, ${m.status === 'out' ? 'out' : 'questionable'})</span>`).join(', ')}</p>` : '';
    return `<div style="display:grid;gap:12px;align-content:start"><h3 class="eyebrow" style="margin:0">${esc(off)} offense vs ${esc(def)} defense</h3>${dline}<div class="mgrid">${cells}</div>${list}</div>`;
  };
  return `<section class="panel" aria-label="Matchups">
    <div class="phead"><h2>Matchups</h2><span class="key"><span class="mm up">▲ favorable</span><span class="mm down">▼ tough</span></span></div>
    <div class="two" style="grid-template-columns:repeat(auto-fit,minmax(280px,1fr))">${side(g.away, g.home)}${side(g.home, g.away)}</div>
    <p class="note">Big number: where the defense ranks this season in fantasy points allowed per game to that position (1st = gives up the most; green = top 8, red = bottom 8). Hover for the stats behind it. "Blended" is the steadier number: allowed vs league average, weighted toward recent games, carrying some of last season and adjusted toward average for small samples. The projections already account for the opponent, so these notes explain the numbers. In 2025–26 backtests, flagged players did not beat their projections more often than other players. "Missing" lists the defense's regular starters (60%+ of snaps lately) who are out or questionable; ▲ CB1 out / 2 DBs out / LB out flags mark the receivers offenses have historically fed more in that spot (2022–26), tracked on the Markets page.</p>
  </section>`;
}

function renderDvp(){
  const v = D.dvp && D.dvp[state.dpos];
  if (!v) return '<section class="panel"><div class="empty">No defense-vs-position data in this file. Re-run project.py.</div></section>';
  const seg = Object.entries(D.dvp).map(([k, x]) => `<button data-dpos="${k}" aria-pressed="${k===state.dpos}">${esc(x.label)}</button>`).join('');
  const bySeason = state.drank !== 'blend' && v.rows.some(r => r.season_rank);
  const rows = v.rows.slice().sort((a, b) => bySeason ? (a.season_rank || 99) - (b.season_rank || 99) : a.rank - b.rank);
  const rankSeg = `<div class="seg" role="group" aria-label="Rank by"><button data-drank="season" aria-pressed="${bySeason}">Rank: this season</button><button data-drank="blend" aria-pressed="${!bySeason}">Rank: blended</button></div>`;
  const maxDev = Math.max(.3, ...v.rows.map(r => Math.abs(r.ratio - 1)));
  const bar = r => { const d = (r.ratio - 1) / maxDev * 50, fill = d >= 0 ? `left:50%;width:${d}%;background:var(--good)` : `left:${50+d}%;width:${-d}%;background:var(--bad)`;
    return `<div class="dvbar" role="img" aria-label="${signPct(r.ratio)} vs average"><i style="${fill}"></i><span class="c"></span></div>`; };
  const statHead = v.cols.map(c => `<th>${esc(c)}</th>`).join('');
  const rawHead = `<th>Fantasy pts</th><th>${bySeason ? 'Blended rank' : 'Season rank'}</th>`;
  const body = rows.map(r => `<tr><td class="big">${bySeason ? (r.season_rank || '–') : r.rank}</td><td class="l name">${esc(r.team)}</td><td class="l">${r.faces ? esc(r.faces) : '<span class="small muted">no game</span>'}</td><td>${r.season_games ?? r.games}</td>${(r.season_stats || r.stats).map(x => `<td>${x == null ? '–' : f1(x)}</td>`).join('')}<td>${r.season_ppr == null ? '–' : f1(r.season_ppr)}</td><td class="muted">${bySeason ? r.rank : (r.season_rank || '–')}</td><td class="big">${f1(r.ppr_adj)}</td><td class="l">${bar(r)}</td><td class="big">${signPct(r.ratio)}</td></tr>`).join('');
  return `<section class="panel">
    <div class="phead"><div class="controls"><div class="seg" role="group" aria-label="Position">${seg}</div>${rankSeg}</div>
      <span class="key"><span><i class="kb" style="background:var(--good)"></i>gives up more (favorable for offenses)</span><span><i class="kb" style="background:var(--bad)"></i>gives up less (tough)</span></span></div>
    <div class="tw"><table class="t"><thead><tr><th>${bySeason ? 'Season rank' : 'Blended rank'}</th><th class="l">Defense</th><th class="l">Faces this week</th><th>Games</th>${statHead}${rawHead}<th>Adjusted pts</th><th class="l" style="min-width:150px">vs league average</th><th></th></tr></thead><tbody>${body}</tbody></table></div>
    <p class="note">Stat columns and "Fantasy pts" are this season only, per game allowed, unadjusted${v.adjusted_only ? " (slot and outside use each receiver's depth-chart alignment; games = games with alignment data)" : ''}. "Season rank" ranks those fantasy points (1 = gives up the most). "Adjusted pts" and the blended rank weight recent games more, carry some of last season and pull small samples toward league average: steadier early in the season. Fantasy points are PPR: 1 per catch, 0.1 per rushing or receiving yard, 6 per TD, 0.04 per passing yard, 4 per passing TD, −2 per interception.</p>
  </section>`;
}

/* ---------------- red zone (tags in the Touchdowns table) ---------------- */
function rzUsage(p){ const u = D.rz && D.rz.usage && D.rz.usage[state.rzscope]; return u ? u[p.k] : null; }
function rzOppTag(pos, opp){
  const r = rzDefRow(opp); if (!r || !r.pos[pos]) return '';
  const x = r.pos[pos], cls = x.ratio >= 1.12 ? 'up' : x.ratio <= 1/1.12 ? 'down' : '';
  const tip = `${opp} defense vs ${RZN[pos]} inside the 20: ${x.td} TDs allowed in ${r.games} games (${signPct(x.ratio)} vs league, ${ord(x.rank)} most). ${x.share == null ? '' : pct(x.share) + ' of the TDs they allow in the red zone go to ' + RZN[pos] + '.'}`;
  return `<span class="mm ${cls}"${cls ? '' : ' style="color:var(--muted)"'} title="${esc(tip)}">${cls === 'up' ? '▲ ' : cls === 'down' ? '▼ ' : ''}${esc(opp)} D vs ${pos} ${signPct(x.ratio)}</span>`;
}
function rzTags(p){
  const u = rzUsage(p), t = [];
  if (u){
    const g = `${u.games} game${u.games === 1 ? '' : 's'}`;
    if (u.rz_car_share != null && u.rz_car_share >= .2) t.push(`<span class="mm tagn" title="${u.rz_car} of the team's red-zone carries over ${g}">RZ carries ${pct(u.rz_car_share)}</span>`);
    if (u.rz_tgt_share != null && u.rz_tgt_share >= .1) t.push(`<span class="mm tagn" title="${u.rz_tgt} red-zone targets over ${g}">RZ targets ${pct(u.rz_tgt_share)}</span>`);
    if (u.gl_car >= 2) t.push(`<span class="mm tagn" title="Carries inside the 5 over ${g}">${u.gl_car} carries inside 5</span>`);
    t.push(`<span class="mm tagn" title="Touchdowns scored inside the 20 over ${g}">${u.rz_td} RZ TD${u.rz_td === 1 ? '' : 's'} / ${u.games}g</span>`);
  }
  const opp = rzOppTag(p.pos, p.o);
  if (opp) t.push(opp);
  return t.length ? `<div class="chips rzchips">${t.join('')}</div>` : '<span class="small muted">–</span>';
}
function rzTeamLine(team, opp){
  const r = rzDefRow(opp); if (!r) return '';
  const parts = RZP.map(ps => { const x = r.pos[ps], cls = x.ratio >= 1.12 ? 'up' : x.ratio <= 1/1.12 ? 'down' : '';
    return `<span class="${cls ? 'mm ' + cls : 'small muted'}" title="${x.td} red-zone TDs allowed to ${RZN[ps]} in ${r.games} games">${cls === 'up' ? '▲ ' : cls === 'down' ? '▼ ' : ''}${ps} ${signPct(x.ratio)}</span>`; }).join(' ');
  return `<span class="rzline">vs ${esc(opp)} D in the red zone (TD rate ${pct(r.td_rate)}): ${parts}</span>`;
}

/* ---------------- Touchdowns: red-zone usage table (owner 2026-10-30: like a TD usage table he shared) ---------------- */
const TEAM_C = {ARI:'#97233F',ATL:'#A71930',BAL:'#241773',BUF:'#00338D',CAR:'#0085CA',CHI:'#0B162A',CIN:'#FB4F14',CLE:'#311D00',DAL:'#041E42',DEN:'#FB4F14',
  DET:'#0076B6',GB:'#203731',HOU:'#03202F',IND:'#002C5F',JAX:'#006778',KC:'#E31837',LA:'#003594',LAC:'#0080C6',LV:'#4b4f52',MIA:'#008E97',MIN:'#4F2683',
  NE:'#002244',NO:'#9F8958',NYG:'#0B2265',NYJ:'#125740',PHI:'#004C54',PIT:'#FFB612',SEA:'#002244',SF:'#AA0000',TB:'#D50A0A',TEN:'#0C2340',WAS:'#5A1414'};
const teamChip = t => `<span class="tchip" style="background:${TEAM_C[t] || 'var(--sunk)'};color:${t === 'PIT' ? '#111' : '#fff'}">${esc(t)}</span>`;
const ring = (v, lab) => { if (v == null) return '<span class="muted">–</span>';
  const c = v >= .5 ? 'var(--good)' : v >= .25 ? '#c9a227' : '#d9822b', r = 15, L = 2 * Math.PI * r;
  return `<span class="ring" title="${esc(lab)}"><svg viewBox="0 0 38 38" width="38" height="38" aria-hidden="true"><circle cx="19" cy="19" r="${r}" fill="none" stroke="var(--sunk)" stroke-width="4"/><circle cx="19" cy="19" r="${r}" fill="none" stroke="${c}" stroke-width="4" stroke-linecap="round" stroke-dasharray="${(Math.min(1, v) * L).toFixed(1)} ${L.toFixed(1)}" transform="rotate(-90 19 19)"/></svg><b>${pct(v)}</b></span>`; };
const heat = (v, mx) => v == null ? '<td class="hv"><span class="muted">–</span></td>'
  : `<td class="hv"><span class="hc${v === 0 ? ' z' : ''}" style="${v ? `--a:${(.12 + .5 * Math.min(1, v / Math.max(mx, 1))).toFixed(2)}` : ''}">${v}</span></td>`;
const sbar = (v, span = .2) => { if (v == null) return '<span class="muted">–</span>';
  const w = Math.min(50, Math.abs(v) / span * 50);
  return `<span class="sb"><span class="${v >= 0 ? 'good' : 'bad'}">${v >= 0 ? '+' : '−'}${(Math.abs(v) * 100).toFixed(1)}%</span><i><b style="left:${v >= 0 ? 50 : 50 - w}%;width:${w}%;background:${v >= 0 ? 'var(--good)' : 'var(--bad)'}"></b><s></s></i></span>`; };
const pbar2 = (v, mu) => v == null ? '<span class="muted">–</span>' : `<span class="sb"><span${mu ? ' class="muted"' : ''}>${pct(v)}</span><i class="lf"><b style="width:${v * 100}%"${mu ? ' class="mu"' : ''}></b></i></span>`;
const TDS = {name: r => r.p.n, rz_car: r => r.u && r.u.rz_car, gl_car: r => r.u && r.u.gl_car, rz_tgt: r => r.u && r.u.rz_tgt, ez_tgt: r => r.u && r.u.ez_tgt,
  opp: r => r.u && r.u.rz_opp_share, proe: r => r.T && r.T.proe, pass: r => r.T && r.T.pass_rate, run: r => r.T && r.T.pass_rate != null ? 1 - r.T.pass_rate : null,
  dproe: r => r.O && r.O.def_proe, dtd: r => r.R && r.R.td_rate, xtd: r => r.u && r.u.xtd, td: r => r.u && r.u.td, any: r => r.p.s.anytime_td[MEAN]};
function tdBoard(g){
  const sc = state.rzscope, TM = (D.rz.teams || {})[sc] || {};
  const pool = state.tdall ? D.players : PL(g);
  const rows = pool.filter(p => p.s.anytime_td && p.s.anytime_td[MEAN] >= .03 && p.pos !== 'QB')
    .map(p => ({p, u: rzUsage(p), T: TM[p.t], O: TM[p.o], R: rzDefRow(p.o)}));
  const key = TDS[state.tds] ? state.tds : 'any', dir = state.tdsd === 'asc' ? 1 : -1;
  rows.sort((a, b) => { const x = TDS[key](a), y = TDS[key](b);
    if (key === 'name') return dir * String(x).localeCompare(String(y));
    return (x == null) - (y == null) || dir * ((x ?? 0) - (y ?? 0)) || b.p.s.anytime_td[MEAN] - a.p.s.anytime_td[MEAN]; });
  const mx = k => Math.max(1, ...rows.map(r => (r.u && r.u[k]) || 0));
  const M = {rz_car: mx('rz_car'), gl_car: mx('gl_car'), rz_tgt: mx('rz_tgt'), ez_tgt: mx('ez_tgt')};
  const mxx = Math.max(.5, ...rows.map(r => (r.u && r.u.xtd) || 0));
  const th = (k, lab, tip, cls = '') => `<th class="${k === 'name' ? 'l ' : ''}${cls}"><button class="tds${key === k ? ' on' : ''}" data-tds="${k}"${tip ? ` title="${esc(tip)}"` : ''}>${lab}<span class="ar">${key === k ? (dir > 0 ? '▲' : '▼') : '↕'}</span></button></th>`;
  const dots = n => n == null ? '<span class="muted">–</span>' : `<span class="tdc"><b>${n}</b><span class="dots" aria-hidden="true">${'●'.repeat(Math.min(n, 8))}${n > 8 ? '+' : ''}</span></span>`;
  const body = rows.map(({p, u, T, O, R}) => {
    const bo = !!(window.MKT && MKT.hasProps && MKT.hasProps(p.g)), a = p.s.anytime_td[MEAN];
    const gl = u ? `${u.games} game${u.games === 1 ? '' : 's'}` : 'no games yet';
    return `<tr class="${state.open === p.k ? 'opened' : ''}"><td class="l tdname"><div class="tdp">${teamChip(p.t)}<div class="tdpn"><button class="plink${state.open === p.k ? ' open' : ''}" data-price="${esc(p.k)}" data-pstat="tds" title="Slide a line for ${esc(p.n)}">${esc(p.n)}</button><span class="tdsub">${esc(p.lab || p.pos)} · vs ${esc(p.o)} · ${gl}</span></div></div></td>`
      + heat(u ? u.rz_car : null, M.rz_car) + heat(u ? u.gl_car : null, M.gl_car) + heat(u ? u.rz_tgt : null, M.rz_tgt) + heat(u ? u.ez_tgt : null, M.ez_tgt)
      + `<td>${ring(u && u.rz_opp_share, 'Share of his team’s red-zone carries + targets in the games he played')}</td>`
      + `<td class="gs">${sbar(T && T.proe)}</td><td>${pbar2(T && T.pass_rate)}</td><td>${pbar2(T && T.pass_rate != null ? 1 - T.pass_rate : null, true)}</td>`
      + `<td class="gs">${sbar(O && O.def_proe)}</td>`
      + `<td>${R ? `<span class="tdr"><span class="pill ${R.td_rate >= .6 ? 'good' : R.td_rate < .45 ? 'bad' : ''}">${pct(R.td_rate)}</span><span class="tdsub">${R.td_trips_n ?? '?'} of ${R.trips_n ?? '?'} trips</span></span>` : '<span class="muted">–</span>'}</td>`
      + `<td class="gs">${u ? `<span class="sb"><span class="xv">${u.xtd.toFixed(2)}</span><i class="lf"><b style="width:${Math.min(100, u.xtd / mxx * 100)}%"></b></i></span>` : '<span class="muted">–</span>'}</td><td>${dots(u ? u.td : null)}</td>`
      + `<td class="gs"><span class="tdany">${ring(a, 'Our chance he scores a rushing or receiving TD in this game')}<span class="tdsub">fair ${american(a)}</span></span></td>`
      + `<td class="l">${bo ? MKT.prop(p.g, p.n, 'td') : '<span class="muted">–</span>'}</td></tr>`
      + (state.open === p.k ? `<tr class="xrow"><td colspan="16">${sliderPanel(p)}</td></tr>` : ''); }).join('');
  const lab = D.rz.labels[sc];
  return `<div class="tdbar"><div class="seg" role="group" aria-label="Players"><button data-tdall="0" aria-pressed="${!state.tdall}">This game</button><button data-tdall="1" aria-pressed="${!!state.tdall}">Whole slate</button></div>
    <span class="small muted">Red zone = inside the 20, goal line = inside the 5 · tap a header to sort</span></div>
    <div class="tw tdw"><table class="t tdt"><thead>
      <tr class="gh"><th class="tdname"></th><th colspan="5">His red-zone usage</th><th colspan="3" class="gs">His team inside the 20</th><th colspan="2" class="gs">Opponent defense</th><th colspan="2" class="gs">Touchdowns, ${esc(lab)}</th><th colspan="2" class="gs">This game</th></tr>
      <tr>${th('name', 'Player', '', 'tdname')}${th('rz_car', 'RZ rush att', 'Carries inside the 20')}${th('gl_car', 'GL rush att', 'Carries inside the 5')}${th('rz_tgt', 'RZ targets', 'Targets inside the 20')}${th('ez_tgt', 'EZ targets', 'Targets thrown into the end zone')}${th('opp', 'RZ opp share', 'His share of his team’s red-zone carries + targets in the games he played (red-zone snap counts aren’t in our data)')}`
      + `${th('proe', 'PROE', 'Pass rate over expected inside the 20: how much more (or less) his team passes than the league in the same down, distance, field spot and game state', 'gs')}${th('pass', 'Pass %')}${th('run', 'Run %')}`
      + `${th('dproe', 'Opp RZ PROE allowed', 'How much more offenses pass inside the 20 against this defense than expected', 'gs')}${th('dtd', 'Opp RZ TD rate', 'Red-zone trips that ended in a touchdown against this defense')}`
      + `${th('xtd', 'Expected TDs', 'The league’s TD rate on each of his carries and targets, by distance to the end zone, added up', 'gs')}${th('td', 'Actual TDs', 'Rushing + receiving touchdowns')}`
      + `${th('any', 'Anytime TD', 'Our simulated chance he scores a rushing or receiving TD in this game', 'gs')}<th class="l">Best odds</th></tr></thead>
    <tbody>${body || '<tr><td colspan="16" class="l muted small">No players with a meaningful TD chance.</td></tr>'}</tbody></table></div>`;
}
/* ---------------- red zone ---------------- */
const RZP = ['QB','RB','WR','TE'];
const RZC = {QB:'var(--c-qb)', RB:'var(--c-rb)', WR:'var(--c-wr)', TE:'var(--c-te)'};
const RZN = {QB:'QBs', RB:'RBs', WR:'WRs', TE:'TEs'};
const sw = p => `<span class="swatch" style="background:${RZC[p]}"></span>`;
function rzDefRow(team){ const d = D.rz && D.rz.defense[state.rzscope]; return d ? d.rows.find(r => r.team === team) : null; }
function renderGames(){
  return `<div class="games"><nav class="rail" aria-label="Games">${gameList()}</nav><div class="detail">${gameDetail()}</div></div>`;
}

/* ---------------- board view ---------------- */
function renderBoard(){
  const st = STATS[state.bstat], q = state.q.trim().toLowerCase();
  let rows = D.players.filter(p => p.s[state.bstat] && p.s[state.bstat][MEAN] >= st.min && (!state.bflag || (p.tags && p.tags.length)) && (!state.bpin || (window.MKT && MKT.pinMoves && MKT.pinMoves(p.g, p.n).some(m => m.src === 'pin')))
    && (state.bpos === 'All' || p.pos === state.bpos) && (!q || p.n.toLowerCase().includes(q) || p.t.toLowerCase() === q));
  const key = p => st.prob ? p.s[state.bstat][MEAN] : p.s[state.bstat][MED] + p.s[state.bstat][MEAN]/1e4;
  rows.sort((a,b) => key(b) - key(a));
  const opts = Object.entries(STATS).map(([k,v]) => `<option value="${k}" ${k===state.bstat?'selected':''}>${v.label}</option>`).join('');
  const pos = ['All','QB','RB','WR','TE'].map(p => `<button data-pos="${p}" aria-pressed="${p===state.bpos}">${p}</button>`).join('');
  let table;
  if (!rows.length) table = `<div class="empty">No players match. Clear the search or pick another position.</div>`;
  else if (st.prob){
    const hi = Math.min(1, niceCeil(Math.max(...rows.map(key))));
    table = `<table class="t"><thead><tr><th>#</th><th class="l">Player</th><th class="l">Pos</th><th class="sc l"><div class="axis"><span style="left:0">0%</span><span style="left:100%;transform:translateX(-100%)">${pct(hi)}</span></div></th><th>Chance</th><th>Fair odds</th></tr></thead><tbody>`
      + rows.map((p,i) => `<tr><td class="muted">${i+1}</td>${nameCell(p, st.prob ? 'tds' : state.bstat, `<small>${esc(p.t)} vs ${esc(p.o)}</small>`)}<td class="l">${posCell(p)}</td><td class="l">${pbar(p.s[state.bstat][MEAN], hi)}</td><td class="big">${pct1(p.s[state.bstat][MEAN])}</td><td>${american(p.s[state.bstat][MEAN])}</td></tr>`).join('') + '</tbody></table>';
  } else {
    const sc = scaleFor(rows, state.bstat), d = st.dec ? f1 : f0;
    table = `<table class="t"><thead><tr><th>#</th><th class="l">Player</th><th class="l">Pos</th><th class="sc l">${axis(sc)}</th><th>Median</th><th>Mean</th><th>10th</th><th>90th</th></tr></thead><tbody>`
      + rows.map((p,i) => { const s = p.s[state.bstat]; return `<tr class="${state.open === p.k ? 'opened' : ''}"><td class="muted">${i+1}</td>${nameCell(p, state.bstat === 'anytime_td' ? 'tds' : state.bstat, `<small>${esc(p.t)} vs ${esc(p.o)}</small>`)}<td class="l">${posCell(p)}</td><td class="l">${strip(s, sc, `${p.n} ${st.label.toLowerCase()}`)}</td><td class="big">${d(s[MED])}</td><td>${f1(s[MEAN])}</td><td class="muted">${d(s[P10])}</td><td class="muted">${d(s[P90])}</td></tr>` + (state.open === p.k ? `<tr class="xrow"><td colspan="8">${sliderPanel(p)}</td></tr>` : ''); }).join('') + '</tbody></table>';
  }
  return `<section class="panel">
    <div class="controls">
      <label for="bstat">Stat</label><select id="bstat">${opts}</select>
      <div class="seg" role="group" aria-label="Position">${pos}</div>
      <button class="flagbtn" data-bflag="1" aria-pressed="${state.bflag}">Matchup flags only</button>
      <button class="flagbtn" data-bpin="1" aria-pressed="${state.bpin}" title="Players whose prop Pinnacle has moved: line up and / or the over shortened (tracked, experimental)">Pinnacle moved</button>
      <input type="search" id="bsearch" placeholder="Player name or team code" value="${esc(state.q)}" aria-label="Search players">
      <span class="muted small">${rows.length} player${rows.length===1?'':'s'}</span>
    </div>
    ${st.prob ? '' : '<div class="key"><span><i class="kw"></i>8 in 10 games</span><span><i class="kb"></i>middle half</span><span><i class="km"></i>median</span></div>'}
    <div class="tw">${table}</div>
  </section>`;
}

/* ---------------- fair odds view ---------------- */
const FSTATS = {
  pass_yds:{label:'Passing yards',step:10}, att:{label:'Pass attempts',step:1}, cmp:{label:'Completions',step:1},
  pass_td:{label:'Passing TDs',step:1}, ints:{label:'Interceptions',step:1},
  rush_yds:{label:'Rushing yards',step:5}, car:{label:'Carries',step:1},
  rec_yds:{label:'Receiving yards',step:5}, rec:{label:'Receptions',step:1}, tgt:{label:'Targets',step:1},
  tds:{label:'Touchdowns (rush + rec)',step:1},
};
const GROUP_STAT = {passing:'pass_yds', rushing:'rush_yds', receiving:'rec_yds', td:'tds'};
const byKey = Object.fromEntries(D.players.map(p => [p.k, p]));
const plabel = p => `${p.n} (${p.t} vs ${p.o})`;
function probs(d, L){
  if (d.p){ let over = 0, under = 0; d.p.forEach((v,i) => { const k = d.o + i; if (k > L) over += v; else if (k < L) under += v; }); return {over, under, push: Math.max(0, 1 - over - under)}; }
  const n = d.q.length, over = d.q.filter(v => v > L).length / n, under = d.q.filter(v => v < L).length / n;
  return {over, under, push: Math.max(0, 1 - over - under)};
}
function dmed(d){
  if (d.p){ let c = 0; for (let i = 0; i < d.p.length; i++){ c += d.p[i]; if (c >= .5) return d.o + i; } return d.o + d.p.length - 1; }
  return (d.q[49] + d.q[50]) / 2;
}
const defaultLine = (stat, d) => stat === 'tds' ? .5 : Math.max(.5, Math.floor(dmed(d)) + .5);
function fairStats(p){ return Object.keys(FSTATS).filter(s => p.d[s] && (s !== 'pass_yds' && s !== 'att' && s !== 'cmp' && s !== 'pass_td' && s !== 'ints' || (p.s.att && p.s.att[MEAN] >= 5))); }
function ensureFo(){
  let fo = state.fo;
  if (!fo || !byKey[fo.key] || !byKey[fo.key].d[fo.stat]){
    const top = D.players.filter(p => p.d.rec_yds).sort((a,b) => b.s.rec_yds[MED] - a.s.rec_yds[MED])[0] || D.players.find(p => Object.keys(p.d).length);
    if (!top) return null;
    const stat = top.d.rec_yds ? 'rec_yds' : fairStats(top)[0];
    fo = state.fo = {key: top.k, stat, line: defaultLine(stat, top.d[stat])};
  }
  return fo;
}
function priceBlock(p, stat, L){
  const d = p.d[stat], r = probs(d, L), nopush = Math.max(r.over + r.under, 1e-9);
  const tdName = stat === 'tds' ? (L === .5 ? 'Anytime TD' : L === 1.5 ? '2+ touchdowns' : '') : '';
  const side = (name, pr) => `<div class="fo-side"><span class="eyebrow">${name}</span><span class="fo-p">${pct(pr)}</span><span class="fo-o">${american(pr / nopush)}</span></div>`;
  const s = p.s[stat === 'tds' ? 'anytime_td' : stat];
  const range = stat === 'tds' || !s ? '' : `Median ${f1(s[MED])} · mean ${f1(s[MEAN])} · 8 in 10 games between ${f1(s[P10])} and ${f1(s[P90])}`;
  return `<div class="fo-res">${side(tdName ? tdName + ' · yes' : 'Over ' + L, r.over)}${side(tdName ? tdName + ' · no' : 'Under ' + L, r.under)}</div>
    <p class="muted small" style="margin:0">${r.push >= .005 ? `Push (exactly ${L}) ${pct(r.push)} of games; odds exclude pushes. ` : ''}${range}</p>`;
}
function ladder(p, stat, L){
  const d = p.d[stat], step = FSTATS[stat].step;
  let lines;
  if (stat === 'tds') lines = [.5, 1.5, 2.5];
  else { const c = Math.floor(dmed(d)) + .5; lines = []; for (let k = -4; k <= 4; k++){ const v = c + k*step; if (v > 0) lines.push(v); } }
  if (!lines.includes(L)) lines.push(L);
  lines.sort((a,b) => a - b);
  return `<table class="t"><thead><tr><th class="l">Line</th><th class="l" style="min-width:160px">Chance of over</th><th>Over</th><th>Fair over</th><th>Fair under</th></tr></thead><tbody>`
    + lines.map(v => { const r = probs(d, v), np = Math.max(r.over + r.under, 1e-9);
      return `<tr${v === L ? ' class="cur"' : ''}><td class="l"><button class="plink" data-line="${v}">${v}</button></td><td class="l">${pbar(r.over, 1)}</td><td class="big">${pct(r.over)}</td><td>${american(r.over/np)}</td><td>${american(r.under/np)}</td></tr>`; }).join('')
    + '</tbody></table>';
}
/* ---------------- model agreement ---------------- */
const AG_LABEL = {pass_yds:'Passing yards', rush_yds:'Rushing yards', rec_yds:'Receiving yards', rec:'Receptions'};
function agTag(r){
  const c = shadeEdge(r.edge);
  if (r.edge >= .05) return `<span class="ag ${c}">▲ lean over</span>`;
  if (r.edge >= .02) return `<span class="ag ${c}">▲ slight over</span>`;
  if (r.edge <= -.05) return `<span class="ag ${c}">▼ lean under</span>`;
  if (r.edge <= -.02) return `<span class="ag ${c}">▼ slight under</span>`;
  return '<span class="ag ok">agree</span>';
}
function renderAgree(){
  const A = D.agree || [];
  if (!A.length) return '<section class="panel"><div class="empty">No market-blind projections for this week yet. Run project.py --blind, then rebuild this page.</div></section>';
  state.astat = state.astat || 'all'; state.aflt = state.aflt || 'all'; state.agame = state.agame || 'all';
  state.agroup = state.agroup ?? true;
  const gamesWith = D.games.filter(g => A.some(r => r.g === g.id));
  if (state.agame !== 'all' && !gamesWith.some(g => g.id === state.agame)) state.agame = 'all';
  const rows = A.filter(r => (state.agame === 'all' || r.g === state.agame) && (state.astat === 'all' || r.stat === state.astat)
    && (state.aflt === 'all' || (state.aflt === 'over' ? r.edge >= .02 : state.aflt === 'under' ? r.edge <= -.02 : Math.abs(r.edge) < .02)));
  rows.sort((a, b) => b.p - a.p);
  const seg = (key, opts) => `<div class="seg" role="group">${opts.map(([k, l]) => `<button data-${key}="${k}" aria-pressed="${state[key] === k}">${l}</button>`).join('')}</div>`;
  const nOver = A.filter(r => r.edge >= .02).length, nUnder = A.filter(r => r.edge <= -.02).length;
  const bar = p => { const x = Math.max(0, Math.min(1, (p - .35) / .3)) * 100; return `<div class="agbar" role="img" aria-label="${pct(p)}"><i style="width:${x}%"></i><b></b></div>`; };
  const rowHtml = r => `<tr class="${shadeEdge(r.edge) ? 'bg-' + shadeEdge(r.edge) : ''}"><td class="l name">${esc(r.n)} <span class="small muted">${esc(r.t)} vs ${esc(r.o)}</span></td><td class="l"><span class="pos">${esc(r.pos)}</span></td><td class="l">${AG_LABEL[r.stat]}</td><td>${f1(r.vmed)}</td><td>${f1(r.bmed)}</td><td class="${r.bmed > r.vmed ? 'pos-d' : r.bmed < r.vmed ? 'neg-d' : ''}">${r.bmed >= r.vmed ? '+' : '−'}${f1(Math.abs(r.bmed - r.vmed))}</td><td class="l">${bar(r.p)}</td><td class="big">${pct(r.p)}</td><td class="l">${agTag(r)}</td></tr>`;
  // grouped by game (kickoff order) unless one game is picked or grouping is off
  let body;
  if (state.agame === 'all' && state.agroup) {
    body = gamesWith.map(g => { const rs = rows.filter(r => r.g === g.id); if (!rs.length) return '';
      const nO = rs.filter(r => r.edge >= .02).length, nU = rs.filter(r => r.edge <= -.02).length;
      return `<tr class="grp"><td colspan="9">${esc(g.away)} @ ${esc(g.home)} <span class="small muted" style="font-family:var(--body);font-weight:400;letter-spacing:0">${esc(g.kickoff)} · ${nO} lean over · ${nU} lean under</span></td></tr>` + rs.map(rowHtml).join(''); }).join('');
  } else body = rows.map(rowHtml).join('');
  if (!rows.length) body = '<tr><td colspan="9" class="l muted">No lines match these filters.</td></tr>';
  const gameSeg = `<div class="controls"><label for="agame">Game</label><select id="agame"><option value="all"${state.agame === 'all' ? ' selected' : ''}>All games</option>${gamesWith.map(g => `<option value="${esc(g.id)}"${state.agame === g.id ? ' selected' : ''}>${esc(g.away)} @ ${esc(g.home)} · ${esc(g.kickoff)}</option>`).join('')}</select>${state.agame === 'all' ? `<div class="seg" role="group"><button data-agroup="1" aria-pressed="${state.agroup}">Grouped by game</button><button data-agroup="0" aria-pressed="${!state.agroup}">One list</button></div>` : ''}</div>`;
  return `<section class="panel">${gameSeg}
    <div class="phead"><div class="controls">${seg('astat', [['all','All'], ['pass_yds','Pass yds'], ['rush_yds','Rush yds'], ['rec_yds','Rec yds'], ['rec','Receptions']])}${seg('aflt', [['all','All'], ['over',`Blind higher (${nOver})`], ['under',`Blind lower (${nUnder})`], ['agree','Agree']])}</div></div>
    <div class="tw"><table class="t"><thead><tr><th class="l">Player</th><th class="l">Pos</th><th class="l">Stat</th><th>Vegas model median</th><th>Blind model median</th><th>Gap</th><th class="l" style="min-width:140px">Chance to beat Vegas median</th><th></th><th class="l">Read</th></tr></thead><tbody>${body}</tbody></table></div>
    <p class="note">Both models simulate every player; this compares them on the same line. "Chance to beat Vegas median" starts from the Vegas model's own chance (a little under 50% for catches and other counts, where landing exactly on the median is common) and moves it 45% of the way toward the blind model's chance: the weight that fit 2025 results and improved 2026 out of sample. Green rows (blind model 5+ points more bullish) and red rows (5+ points more bearish) are the strongest leans. History, 2025–26: when the blind model was higher, players beat the Vegas median 49–50% of the time vs 44–48% otherwise; when it was much lower, 33–49%. The edge is real but small: nobody here is better than about 55%. When the models agree, players hit their projection on average.</p>
  </section>`;
}

function renderFair(){
  const fo = ensureFo(); if (!fo) return '<section class="panel"><div class="empty">No player distributions in this file. Re-run project.py to include them.</div></section>';
  const p = byKey[fo.key];
  const opts = D.players.filter(x => Object.keys(x.d).length).map(x => `<option value="${esc(plabel(x))}"></option>`).join('');
  const stats = fairStats(p).map(s => `<option value="${s}" ${s===fo.stat?'selected':''}>${FSTATS[s].label}</option>`).join('');
  return `<section class="panel">
    <div class="controls">
      <label for="fo-player">Player</label><input id="fo-player" list="fo-list" value="${esc(plabel(p))}" autocomplete="off"><datalist id="fo-list">${opts}</datalist>
      <label for="fo-stat">Stat</label><select id="fo-stat">${stats}</select>
      <label for="fo-line">Line</label><input id="fo-line" type="number" step="0.5" min="0" value="${fo.line}">
    </div>
    <div id="fo-result">${priceBlock(p, fo.stat, fo.line)}</div>
  </section>
  <section class="panel"><div class="phead"><h2>Nearby lines</h2><span class="muted small">No-vig prices from the simulations. Click a line to price it above.</span></div>
    <div class="tw" id="fo-ladder">${ladder(p, fo.stat, fo.line)}</div></section>`;
}
function refreshFair(){
  const p = byKey[state.fo.key];
  document.getElementById('fo-result').innerHTML = priceBlock(p, state.fo.stat, state.fo.line);
  document.getElementById('fo-ladder').innerHTML = ladder(p, state.fo.stat, state.fo.line);
  save();
}
function openFair(key, stat){
  const p = byKey[key]; if (!p || !Object.keys(p.d).length) return;
  if (!p.d[stat]) stat = fairStats(p)[0];
  state.fo = {key, stat, line: defaultLine(stat, p.d[stat])};
  state.view = 'fair'; render(); window.scrollTo({top: 0});
}
// Why a flagged price might hit (owner 2026-10-21: "flag any price that is possible cause of dvp or game script or any
// of the other rules set forth for path"): for players with a Value, LC or off-market price, the path reasons that
// apply -- usage (team top 3 by touches / starting QB), matchup (defense top 8 most allowed to the position), rotation
// (a teammate out or a snap jump), script (a game script our sims rate 3+ pts above the markets lifts his line 15%+ at
// his median). "No clear reason" says the price has no path behind it.
const WHY_ST = {td: 'tds'};
function gameScriptsOf(g){
  const T = D.teams[g.id] || {}, H = T[g.home]; if (!H || H.spread_line == null || !(window.MKT && MKT.scriptMarket)) return null;
  const DW = scriptDraws(g.id); if (!DW || DW === 'load' || !DW.g) return null;
  if (DW._scr) return DW._scr;
  const L = H.spread_line, tl = H.total_line, homeFav = L > 0 || (L === 0 && H.win_prob >= .5), fav = homeFav ? g.home : g.away, dog = homeFav ? g.away : g.home;
  const M = sdecode(DW.g.margin), TT = sdecode(DW.g.total);
  const SC = [[`${fav} pulls away`, m => (homeFav ? m : -m) >= 10, {kind: 'margin', team: fav, min: 10}], ['close game', m => Math.abs(m) <= 7, {kind: 'close', max: 7}],
              [`${dog} wins`, m => homeFav ? m < 0 : m > 0, {kind: 'win', team: dog}], ['shootout', (m, t) => t > tl, {kind: 'over', at: tl}], ['low-scoring', (m, t) => t < tl, {kind: 'under', at: tl}]];
  DW._scr = SC.map(([name, f, spec]) => { const mask = Array.from(M, (m, i) => f(m, TT[i])), n = mask.filter(Boolean).length;
    const mk = MKT.scriptMarket(g.id, spec, {home: g.home, away: g.away, fav}); return {name, mask, n, edge: mk ? n / M.length - mk.m : null}; })
    .filter(s => s.edge != null && s.edge >= .03);
  return DW._scr;
}
function whyNFL(p){
  if (!(window.MKT && MKT.tagStats)) return '';
  const sts = MKT.tagStats(p.g, p.n); if (!sts.length) return '';
  const g = D.games.find(x => x.id === p.g); if (!g) return '';
  const mu = (q, s_) => q.s[s_] ? q.s[s_][MEAN] : 0, team = D.players.filter(q => q.g === p.g && q.t === p.t);
  const qb = team.filter(q => q.pos === 'QB').sort((a, b) => mu(b, 'att') - mu(a, 'att'))[0];
  const top = team.filter(q => q.pos !== 'QB').sort((a, b) => (mu(b, 'tgt') + mu(b, 'car')) - (mu(a, 'tgt') + mu(a, 'car'))).slice(0, 3);
  const k = p.pos === 'QB' ? 'QB' : p.pos === 'RB' ? 'RB' : p.pos === 'TE' ? 'TE' : 'WR', r = dvpRow(k, p.o), role = roleOf(g, p);
  const R = [];
  if (p === qb || top.includes(p)) R.push('usage');
  if (r && r.rank <= 8) R.push(`matchup (${ord(r.rank)} most to ${k}s)`);
  if (role) R.push(role);
  const sc = gameScriptsOf(g);
  if (sc && sc.length){
    const DW = SCR[g.id];
    for (const st of sts){
      const x = DW && DW.p && DW.p[p.k] ? sdecode(DW.p[p.k][WHY_ST[st] || st]) : null; if (!x) continue;
      const med = (p.s[WHY_ST[st] || st] || [])[MED] ?? 0;
      let a = 0; for (const z of x) if (z > med) a++;
      for (const s of sc){ let b = 0; for (let i = 0; i < x.length; i++) if (s.mask[i] && x[i] > med) b++;
        const lift = ((b + 1) / (s.n + 2)) / ((a + 1) / (x.length + 2));
        if (lift >= 1.15){ R.push(`script (${s.name} +${Math.round((lift - 1) * 100)}%)`); break; } }
      if (R.some(x => x.startsWith('script'))) break;
    }
  }
  return R.length ? `<span class="why" title="Path reasons behind this flagged price (the Path longshot rules)">why: <b>${esc(R.join(' · '))}</b></span>`
    : `<span class="why none" title="None of the path reasons (usage, matchup, rotation, game script) applies: the price has no clear reason behind it">no clear reason</span>`;
}
// Role chip + matchup lines under each name (the NBA page's archetype chip / matchup lines, NFL version; owner 2026-10-21).
// Role: from his alignment (depth chart) and projected usage. Matchup: what tonight's defense allowed per game this season
// to his group (slot / outside WRs, TEs, RBs, QBs) vs the league average; 15%+ either way, only where he has volume; when it
// shows, it replaces the older one-number matchup chip (same row, kept for the Matchup flags filter).
// Display only: in our 2025-26 test, extra position / archetype matchups on top of the projection added nothing (the model
// has its own defense terms).
function roleOf2(p){
  const m = (k, i) => p.s[k] ? p.s[k][i] : 0;          // WRs: alignment is already in the position column (WR1 · X)
  if (p.pos === 'RB'){ const t = m('tgt', MEAN), c = m('car', MEAN); if (t + c < 6) return null; const r = t / (t + c);
    return r >= .3 ? ['Receiving back', `${Math.round(r * 100)}% of his projected touches are targets`] : r <= .17 ? ['Early-down back', `${Math.round(r * 100)}% of his projected touches are targets`] : ['Three-down back', `${Math.round(r * 100)}% of his projected touches are targets`]; }
  if (p.pos === 'TE') return m('tgt', MEAN) >= 5 ? ['Pass-catching TE', `${f1(m('tgt', MEAN))} projected targets`] : null;
  if (p.pos === 'QB' && m('att', MEAN) >= 20) return m('rush_yds', MEAN) >= 25 ? ['Dual-threat QB', `${f0(m('rush_yds', MEAN))} projected rushing yards`] : ['Pocket passer', `${f0(m('rush_yds', MEAN))} projected rushing yards`];
  return null;
}
const MXG = {QB: 'QBs', RB: 'RBs', TE: 'TEs', WR: 'WRs', SLOT: 'slot WRs', OUT: 'outside WRs'};
// [page stat, DvP column, label, volume floor on his projected median]
const MXS = {QB: [['pass_yds', 'Pass yds', 'Pass yds', 150], ['rush_yds', 'Rush yds', 'Rush yds', 20]],
             RB: [['rush_yds', 'Rush yds', 'Rush yds', 30], ['rec_yds', 'Rec yds', 'Rec yds', 20]],
             WR: [['rec_yds', 'Rec yds', 'Rec yds', 25], ['rec', 'Rec', 'Catches', 3]], TE: [['rec_yds', 'Rec yds', 'Rec yds', 25], ['rec', 'Rec', 'Catches', 3]]};
function mxNFL(p){
  if (!D.dvp) return '';
  const al = (p.lab || '').split(' · ')[1] || '';
  const key = p.pos === 'WR' ? (al === 'Slot' ? 'SLOT' : al === 'X' || al === 'Z' ? 'OUT' : 'WR') : p.pos;
  const T = D.dvp[key], spec = MXS[p.pos]; if (!T || !spec) return '';
  const row = T.rows.find(r => r.team === p.o); if (!row || !row.season_stats || (row.season_games || 0) < 3) return '';
  const L = [];
  for (const [st, col, lab, floor] of spec){
    const i = T.cols.indexOf(col); if (i < 0 || !p.s[st] || p.s[st][MED] < floor) continue;
    const xs = T.rows.filter(r => r.season_stats).map(r => r.season_stats[i]), lg = xs.reduce((a, b) => a + b, 0) / xs.length;
    if (!(lg > 0)) continue;
    const v = row.season_stats[i] / lg; if (Math.abs(v - 1) < .15) continue;
    L.push({lab, v, tip: `${p.o} allowed ${f1(row.season_stats[i])} ${lab.toLowerCase()} a game to ${MXG[key]} this season (${row.season_games} games) vs ${f1(lg)} league average. Context only: the projection uses its own defense terms.`});
  }
  if (!L.length) return '';
  const sg = v => (v >= 1 ? '+' : '−') + Math.abs(Math.round((v - 1) * 100)) + '%';
  L.sort((a, b) => Math.abs(b.v - 1) - Math.abs(a.v - 1));
  // two stats the same way share one line ("Rec yds +33% · Catches +30% vs outside WRs")
  const groups = L.length === 2 && (L[0].v > 1) === (L[1].v > 1) ? [L] : L.map(x => [x]);
  return `<div class="mxw">${groups.map(G => `<span class="mxl ${G[0].v > 1 ? 'up' : 'dn'}" title="${esc(G.map(x => x.tip).join(' '))}">${G.map(x => `${x.lab} ${sg(x.v)}`).join(' · ')} <i>vs ${MXG[key]}</i></span>`).join('')}</div>`;
}
const roleChip = p => { const r = roleOf2(p); return r ? `<span class="tag arch" title="${esc(r[1])} (display only)">${esc(r[0])}</span>` : ''; };
const nameCell = (p, stat, extra='') => `<td class="l name"><button class="plink${state.open === p.k ? ' open' : ''}" data-price="${esc(p.k)}" data-pstat="${stat}" title="Slide a line for ${esc(p.n)}"><span class="caret">${state.open === p.k ? '▾' : '▸'}</span>${esc(p.n)}</button>${agChip(p, stat)}${window.MKT && MKT.flag ? MKT.flag(p.g, p.n) + (MKT.lcFlag ? MKT.lcFlag(p.g, p.n) : '') + (MKT.offFlag ? MKT.offFlag(p.g, p.n) : '') + (MKT.moveFlag ? MKT.moveFlag(p.g, p.n) : '') + whyNFL(p) : ''}${window.MKT && state.gm[p.g] === 'blind' ? MKT.patChips(p.g, p.id, p.n) : ''}${extra}${roleChip(p)}${(() => { const mx = mxNFL(p); return mx || chips(p); })()}</td>`;
// Market-blind lean, inline (same rule as the Model agreement tab)
const AGK = Object.fromEntries((D.agree || []).map(r => [r.k + '|' + r.stat, r]));
function agChip(p, stat){
  const r = AGK[p.k + '|' + stat]; if (!r || Math.abs(r.edge) < .02) return '';
  const up = r.edge > 0, tip = `Market-blind model ${up ? 'higher' : 'lower'}: ${f1(r.bmed)} vs ${f1(r.vmed)}. Chance to beat the median: ${pct(r.p)}.`;
  return `<span class="lean ${shadeEdge(r.edge)}" title="${esc(tip)}">${up ? '▲' : '▼'} blind</span>`;
}
// Slider panel: drag the line, see over/under % and fair odds from the simulated distribution.
function sliderRange(d, stat){
  if (stat === 'tds') return {lo: .5, hi: 2.5, step: 1};
  if (d.p){ const lo = d.o, hi = d.o + d.p.length - 1; return {lo: Math.max(.5, lo + .5), hi: Math.max(1.5, hi - .5), step: 1}; }
  const step = FSTATS[stat] && FSTATS[stat].step >= 5 ? 1 : .5;
  return {lo: Math.max(.5, Math.floor(d.q[2]) + .5), hi: Math.ceil(d.q[96]) + .5, step};
}
// build-your-own parlay (slip.js): legs from this page carry the game's saved simulations so same-game legs link
const SLIP_LAB = {pass_yds: 'pass yds', att: 'pass att', cmp: 'completions', pass_td: 'pass TDs', ints: 'INTs', rush_yds: 'rush yds', car: 'carries', rec_yds: 'rec yds', rec: 'receptions', tgt: 'targets', tds: 'TDs'};
const glabel = gid => { const g = D.games.find(x => x.id === gid); return g ? `${g.away} @ ${g.home}` : ''; };
function slipLeg(p, stat, L, side, prob){
  const model = state.gm[p.g] === 'blind' ? 'blind' : 'vegas';
  const lab = stat === 'tds' && L === .5 ? (side === 'over' ? `${p.n} anytime TD` : `${p.n} no TD`) : `${p.n} ${side} ${L} ${SLIP_LAB[stat] || stat}`;
  return {id: `nfl|${p.g}|${p.k}|${stat}|${side}|${L}|${model}`, sport: 'nfl', g: p.g, glab: glabel(p.g), lab, p: prob, side, stat, line: L, pk: p.k, model, draws: `nfl/draws/${p.g}.json`};
}
function sliderReadout(p, stat, L){
  const d = p.d[stat], r = probs(d, L), np = Math.max(r.over + r.under, 1e-9);
  const ov = r.over / np, un = r.under / np;
  const so = shadeP(ov), su = shadeP(un);
  return `<div class="sl-out"><div class="sl-side over"><span>Over ${L}</span><b class="${so}">${pct(r.over)}</b><em>${american(ov)}</em></div>
    <div class="sl-meter" aria-hidden="true"><i class="f-${so || 'n'}" style="width:${r.over * 100}%"></i><i class="f-${su || 'n'}" style="width:${r.under * 100}%"></i></div>
    <div class="sl-side under"><span>Under ${L}</span><b class="${su}">${pct(r.under)}</b><em>${american(un)}</em></div></div>
    ${r.push >= .005 ? `<p class="muted small" style="margin:0">Exactly ${L}: ${pct(r.push)} (a push; odds exclude it)</p>` : ''}
    ${window.SLIP ? `<div class="small muted" style="display:flex;gap:6px;align-items:center;flex-wrap:wrap">My parlay: ${SLIP.btn(slipLeg(p, stat, L, 'over', r.over), 'Over ' + L)}${SLIP.btn(slipLeg(p, stat, L, 'under', r.under), 'Under ' + L)}</div>` : ''}`;
}
const MK_KIND = {rec_yds: ['rec_yds'], rush_yds: ['rush_yds'], pass_yds: ['pass_yds'], rec: ['rec'], tds: ['anytime_td', 'first_td']};
function sliderPanel(p){
  const stats = fairStats(p); if (!stats.length) return '<p class="muted small">No distribution saved for this player.</p>';
  let st = state.ostat && stats.includes(state.ostat) ? state.ostat : stats[0];
  state.ostat = st;
  const d = p.d[st], rg = sliderRange(d, st);
  if (state.oline == null || state.okey !== p.k + st) { state.oline = defaultLine(st, d); state.okey = p.k + st; }
  const L = Math.min(Math.max(state.oline, rg.lo), rg.hi);
  const sum = p.s[st === 'tds' ? 'anytime_td' : st];
  const info = st === 'tds' || !sum ? '' : `Median <b>${f1(sum[MED])}</b> · 8 in 10 games between ${f1(sum[P10])} and ${f1(sum[P90])}`;
  return `<div class="slider" data-sk="${esc(p.k)}">
    <div class="sl-top"><div class="seg" role="group" aria-label="Stat">${stats.map(x => `<button data-ostat="${x}" aria-pressed="${x === st}">${FSTATS[x].label}</button>`).join('')}</div><span class="muted small">${info}</span></div>
    <div class="sl-row"><label for="sl-in">Line</label><button class="sl-step" data-ostep="-1" aria-label="Lower the line">−</button>
      <input id="sl-in" type="range" min="${rg.lo}" max="${rg.hi}" step="${rg.step}" value="${L}" aria-label="Line">
      <button class="sl-step" data-ostep="1" aria-label="Raise the line">+</button><b class="sl-val" id="sl-val">${L}</b></div>
    <div id="sl-res">${sliderReadout(p, st, L)}</div>
    ${window.MKT ? MKT.player(p.g, p.id, p.n, MK_KIND[st] || [], state.gm[p.g] === 'blind' ? 'blind' : 'vegas', true, state.gm[p.g] === 'blind') : ''}</div>`;
}
const playerByKey = k => (D.alt && D.alt.players.find(x => x.k === k && state.gm[x.g] === 'blind')) || byKey[k];

/* ---------------- props view ---------------- */
function renderProps(){
  const slot = {};
  D.games.forEach((g,i) => { slot[g.home] = slot[g.away] = i; });
  const rows = D.props.map((r,i) => [r,i]).sort(([a,i],[b,j]) => (slot[a.team] ?? 1e9) - (slot[b.team] ?? 1e9) || i - j).map(([r]) => r);
  const body = rows.map(r => {
    if (r.p_over == null) return `<tr><td class="l name">${esc(r.player)}</td><td class="l">${esc(STATS[r.stat]?.label || r.stat)}</td><td>${esc(r.line)}</td><td colspan="5" class="l muted">${esc(r.note || 'Not projected')}</td></tr>`;
    const p = r.p_over / Math.max(r.p_over + r.p_under, 1e-9);
    const fill = p >= .5 ? `left:50%;width:${(p-.5)*100}%;background:var(--accent)` : `left:${p*100}%;width:${(.5-p)*100}%;background:var(--away)`;
    const st = STATS[r.stat] || {};
    return `<tr><td class="l name">${esc(r.player)}<small>${esc(r.team)}</small></td><td class="l">${esc(st.label || r.stat)}</td><td class="big">${esc(r.line)}</td><td>${st.prob ? pct(r.proj_mean) : (st.dec ? f1(r.proj_median) : f0(r.proj_median))}</td>
      <td class="l"><div class="dv" role="img" aria-label="Over ${pct(p)}"><i style="${fill}"></i><span class="c"></span></div></td>
      <td class="big">${pct(p)}</td><td>${esc(r.fair_over > 0 ? '+'+r.fair_over : r.fair_over)}</td><td>${esc(r.fair_under > 0 ? '+'+r.fair_under : r.fair_under)}</td></tr>`;
  }).join('');
  return `<section class="panel">
    <div class="phead"><h2>Your lines</h2><span class="key"><span><i class="kb" style="background:var(--accent)"></i>leans over</span><span><i class="kb" style="background:var(--away)"></i>leans under</span></span></div>
    <div class="tw"><table class="t"><thead><tr><th class="l">Player</th><th class="l">Stat</th><th>Line</th><th>Proj</th><th class="l" style="min-width:160px">Under ← 50% → Over</th><th>Over</th><th>Fair over</th><th>Fair under</th></tr></thead><tbody>${body}</tbody></table></div>
  </section>`;
}

/* ---------------- shell ---------------- */
function render(){
  for (const b of document.querySelectorAll('.tabs button')) b.setAttribute('aria-selected', b.dataset.view === state.view);
  const main = document.getElementById('main');
  main.innerHTML = state.view === 'board' ? renderBoard() : state.view === 'props' ? renderProps()
    : state.view === 'books' ? (window.MKT && MKT.oddsView ? MKT.oddsView(D.games.map(g => ({id: g.id, label: `${g.away} @ ${g.home}`, away: g.away, home: g.home}))) : '<div class="empty">Loading prices…</div>')
    : state.view === 'parlays' ? renderParlays() : state.view === 'fair' ? renderFair() : state.view === 'agree' ? renderAgree() : state.view === 'dvp' ? renderDvp() : renderGames();
  save();
  const cur = document.querySelector('.rail .game-btn[aria-current="true"]');
  if (cur){ const r = cur.parentElement, rr = r.getBoundingClientRect(), cr = cur.getBoundingClientRect(); if (r.scrollWidth > r.clientWidth && (cr.left < rr.left || cr.right > rr.right)) r.scrollLeft += cr.left - rr.left - 12; }
}
document.addEventListener('change', e => { if (e.target.id === 'agame'){ state.agame = e.target.value; render(); } });
document.addEventListener('click', e => {
  const t = e.target.closest('button'); if (!t) return;
  if (t.dataset.price){ const k = t.dataset.price; state.open = state.open === k ? null : k;
    state.ostat = {tds: 'tds'}[t.dataset.pstat] || t.dataset.pstat; state.okey = null; render(); }
  else if (t.dataset.ostat){ state.ostat = t.dataset.ostat; state.okey = null; render(); }
  else if (t.dataset.ostep){ const inp = document.getElementById('sl-in'); if (inp){ inp.value = Math.min(+inp.max, Math.max(+inp.min, +inp.value + (+t.dataset.ostep) * (+inp.step))); inp.dispatchEvent(new Event('input', {bubbles: true})); } }
  else if (t.dataset.line){ state.fo.line = +t.dataset.line; document.getElementById('fo-line').value = state.fo.line; refreshFair(); }
  else if (t.dataset.view){ state.view = t.dataset.view; render(); }
  else if (t.dataset.gtab){ state.gtab = t.dataset.gtab; render(); }
  else if (t.dataset.script){ state.script = t.dataset.script; render(); }
  else if (t.dataset.game){ state.game = t.dataset.game; render(); }
  else if (t.dataset.gmodel){ state.gm[state.game] = t.dataset.gmodel; render(); }
  else if (t.dataset.group){ state.group = t.dataset.group; render(); }
  else if (t.dataset.pos){ state.bpos = t.dataset.pos; render(); }
  else if (t.dataset.dpos){ state.dpos = t.dataset.dpos; render(); }
  else if (t.dataset.astat){ state.astat = t.dataset.astat; render(); }
  else if (t.dataset.aflt){ state.aflt = t.dataset.aflt; render(); }
  else if (t.dataset.agroup){ state.agroup = t.dataset.agroup === '1'; render(); }
  else if (t.dataset.drank){ state.drank = t.dataset.drank; render(); }
  else if (t.dataset.rzscope){ state.rzscope = t.dataset.rzscope; render(); }
  else if (t.dataset.tds){ const k = t.dataset.tds; state.tdsd = state.tds === k ? (state.tdsd === 'asc' ? 'desc' : 'asc') : (k === 'name' ? 'asc' : 'desc'); state.tds = k; render(); }
  else if (t.dataset.tdall){ state.tdall = t.dataset.tdall === '1'; render(); }
  else if (t.dataset.bflag){ state.bflag = !state.bflag; render(); }
  else if (t.dataset.bpin){ state.bpin = !state.bpin; render(); }
});
document.addEventListener('change', e => {
  const id = e.target.id;
  if (id === 'bstat'){ state.bstat = e.target.value; render(); }
  else if (id === 'fo-stat'){ const p = byKey[state.fo.key]; state.fo.stat = e.target.value; state.fo.line = defaultLine(state.fo.stat, p.d[state.fo.stat]); render(); }
  else if (id === 'fo-player'){
    const p = D.players.find(x => plabel(x) === e.target.value) || D.players.find(x => x.n.toLowerCase() === e.target.value.trim().toLowerCase());
    if (p) openFair(p.k, state.fo.stat); else e.target.value = plabel(byKey[state.fo.key]);
  }
});
document.addEventListener('input', e => {
  if (e.target.id === 'sl-in'){ const box = e.target.closest('.slider'), p = playerByKey(box.dataset.sk); state.oline = +e.target.value;
    document.getElementById('sl-val').textContent = state.oline; document.getElementById('sl-res').innerHTML = sliderReadout(p, state.ostat, state.oline); return; }
  if (e.target.id === 'fo-line'){ const v = parseFloat(e.target.value); if (!isNaN(v)){ state.fo.line = v; refreshFair(); } return; }
  if (e.target.id === 'fo-player'){ const p = D.players.find(x => plabel(x) === e.target.value); if (p) openFair(p.k, state.fo.stat); return; }
  if (e.target.id !== 'bsearch') return;
  state.q = e.target.value; const pos = e.target.selectionStart; render();
  const s = document.getElementById('bsearch'); s.focus(); s.setSelectionRange(pos, pos);
});
const tip = document.getElementById('tip');
function showTip(el, x, y){
  tip.innerHTML = el.dataset.tip; tip.hidden = false;
  const r = tip.getBoundingClientRect();
  tip.style.left = Math.min(Math.max(8, x - r.width/2), innerWidth - r.width - 8) + 'px';
  tip.style.top = (y - r.height - 12 < 8 ? y + 18 : y - r.height - 12) + 'px';
}
document.addEventListener('pointermove', e => { const el = e.target.closest('[data-tip]'); if (el) showTip(el, e.clientX, e.clientY); else tip.hidden = true; });
document.addEventListener('focusin', e => { const el = e.target.closest('[data-tip]'); if (el){ const r = el.getBoundingClientRect(); showTip(el, r.left + r.width/2, r.top); } });
document.addEventListener('focusout', () => { tip.hidden = true; });
render();
