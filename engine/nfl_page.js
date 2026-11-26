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
if (['games','board','dvp','mu','fair','props','agree','parlays','ladders'].includes(location.hash.slice(1))) state.view = location.hash.slice(1);
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
if (!D.dvp && document.getElementById('tab-ladders')) { document.getElementById('tab-ladders').hidden = true; if (state.view === 'ladders') state.view = 'games'; }
if (!D.dvp && document.getElementById('tab-mu')) { document.getElementById('tab-mu').hidden = true; if (state.view === 'mu') state.view = 'games'; }

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
const KL = {rec: 'catches', rec_yds: 'rec yds', rush_yds: 'rush yds', pass_yds: 'pass yds'};
// Edge audit fix D (owner 2026-11-11; research/edge_audit_round2.py): on NFL Kalshi rungs our top-rung chances ran ~1.7x high and
// Kalshi's ran low (claims 100%+: won 15 vs 10.6 priced vs 26 ours). The ladder chance is market-anchored: logit p = .21 + .70
// logit(Kalshi mid) + .25 logit(ours) (fit before Oct 19; after it log-loss .3974 vs ours .3988 vs Kalshi .4054, claims match
// outcomes). `praw` keeps our own number for the tooltip.
const LAD_A = [.21, .70, .25], lgN = v => Math.log(v / (1 - v)), sgN = z => 1 / (1 + Math.exp(-z)), clN = v => Math.min(Math.max(v, .002), .998);
const ladAnchor = u => u.p == null || u.mid == null ? u : {...u, praw: u.p, p: sgN(LAD_A[0] + LAD_A[1] * lgN(clN(u.mid)) + LAD_A[2] * lgN(clN(u.p)))};
const ladPrice = u => { const opts = [{b: 'Kalshi', price: u.price, pay: 1 / u.cost}, ...Object.entries(u.bk || {}).map(([b, v]) => ({b, price: (v.o > 0 ? '+' : '') + v.o, u: v.u, pay: v.o > 0 ? 1 + v.o / 100 : 1 + 100 / -v.o}))];
  const best = opts.reduce((a, c) => c.pay > a.pay ? c : a); return {...u, opts, best, edge: u.p != null ? u.p * best.pay - 1 : null}; };
// one rung; its number is a dropdown of this rung and the ones below it, to check their prices (owner 2026-11-01). Lower
// rungs outside the card's tracked set show our chance, no edge: raw-model edges near the main line have lost
const LADSEL = {}, LADC = {};
// his average a game for the rung's stat (owner 2026-11-20: 'for the ladder candidates i want to see the avg for the player
// stat'): this season's games before this week (last season's when he has under 2; report.py _season_avgs -> av), last 3 in the tooltip
function ladAvg(p, k){
  const a = p.av && p.av[k]; if (!a || a[0] == null) return '';
  return ` <i class="lavg" title="${esc(`${KL[k]} a game: ${f1(a[0])} over his ${p.av.gp} game${p.av.gp === 1 ? '' : 's'} ${p.av.cur ? 'this season' : `last season (${p.av.season}; under 2 games this season)`}; last 3 games ${f1(a[1])}. Context only.`)}">avg <b>${f1(a[0])}</b></i>`;
}
function lruChip(c, u0){
  const key = `${c.p.k}|${u0.k}|${u0.l}`, lo = c.all.filter(x => x.k === u0.k && x.l <= u0.l).sort((a, b) => b.l - a.l);
  LADC[key] = {c, u0};
  const u = lo.find(x => x.l === LADSEL[key]) || u0, tracked = c.rungs.some(x => x.k === u.k && x.l === u.l), b = u.best, kc = u.cost;
  const n = l => Math.round(l + .5), tx = o => `${o.b === 'Kalshi' ? 'K' : o.b === 'Hard Rock' ? 'HR' : 'NV'} ${o.price}`;
  const num = lo.length > 1 ? `<select class="lsel" data-lrk="${esc(key)}" aria-label="Change rung">${lo.map(x => `<option value="${x.l}"${x.l === u.l ? ' selected' : ''}>${n(x.l)}+</option>`).join('')}</select>` : `<b>${n(u.l)}+</b>`;
  return `<span class="lru" title="${n(u.l)}+ ${KL[u.k]}: chance ${u.p != null ? Math.round(u.p * 100) + '%' : '–'}${u.praw != null ? ` (26% our ${Math.round(u.praw * 100)}% + 74% Kalshi’s mid; our own number runs high on these rungs)` : ''} · Kalshi ${Math.round(u.mid * 100)}% (${Math.round(kc * 100)}% with the fee)${u.opts.filter(o => o.b !== 'Kalshi').map(o => ` · ${o.b} ${o.price}`).join('')}${u.opts.length === 1 ? ' · no Hard Rock / Novig price at this number' : ''}">${num}${window.MKT && MKT.rungTag ? MKT.rungTag(c.g.id, c.p.id, c.p.n, u.k, u.l) : ''}${aStar(c.p, u)} ${KL[u.k]}${ladAvg(c.p, u.k)} ${u.opts.map(o => o === b ? (o.u ? `<a class="lbk best" href="${esc(o.u)}" target="_blank" rel="noopener">${tx(o)} ↗</a>` : `<i class="lbk best">${tx(o)}</i>`) : `<i class="lbk">${tx(o)}</i>`).join('')}${tracked ? (u.edge != null && u.edge >= .05 ? ` <b class="ledge">+${Math.round(u.edge * 100)}%</b>` : '') : u.p != null ? ` <i class="lbk">chance ${Math.round(u.p * 100)}%</i>` : ''}${window.MKT && MKT.statMove ? MKT.statMove(c.g.id, c.p.n, u.k) : ''}${window.WATCH ? ' ' + WATCH.both({sport: 'nfl', gid: String(c.g.id), name: c.p.n, kind: u.k, line: u.l, book: b.b, o: b.b === 'Kalshi' ? Math.round(kc >= .5 ? -100 * kc / (1 - kc) : 100 * (1 - kc) / kc) : Number(String(b.price).replace('+', '')), glab: `${c.g.away} @ ${c.g.home}`, start: c.g.sort || null, lab: `${c.p.n} ${n(u.l)}+ ${KL[u.k]}`}) : ''}${rungSlip(c.g, c.p, u)}</span>`;
}
document.addEventListener('change', e => { const t = e.target.closest('[data-lrk]'); if (!t) return;
  const k = t.dataset.lrk, x = LADC[k]; LADSEL[k] = +t.value; const w = document.createElement('div'); w.innerHTML = lruChip(x.c, x.u0); t.closest('.lru').replaceWith(w.firstElementChild); });
// a rung in a ladder category, for the screener (freeze mode only)
const regL = (g, p, u, cat) => { if (window.MKT && MKT.regLad) MKT.regLad({gid: String(g.id), start: g.sort, name: MKT.norm(p.n), kind: u.k, line: u.l, cat, ya: u.ya, p: u.p == null ? null : +u.p.toFixed(4)}); };
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
document.addEventListener('click', e => { const b = e.target.closest('[data-lmf]'); if (!b) return; LADM = b.dataset.lmf; render(); });
let RODDS = null, ROLES = null;
const RST = {rec_yds: 'Receiving yds', rec: 'Catches', rush_yds: 'Rushing yds', pass_yds: 'Passing yds'};
const ROB = [[-99999, 99, 'minus money'], [100, 150, '+100–150'], [151, 350, '+151–350'], [351, 600, '+351–600'], [601, 800, '+601–800'],
             [801, 1200, '+801–1200'], [1201, 1500, '+1201–1500'], [1501, 2500, '+1501–2500'], [2501, 10000, '+2501–10000'], [10001, 1e9, 'over +10000']];
const rnorm = s => String(s || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase().replace(/[^a-z ]/g, '').replace(/\b(jr|sr|ii|iii|iv)\b/g, '').replace(/\s+/g, ' ').trim();
function aStar(p, u){
  if (!RODDS || !ROLES || !u.best) return '';
  const ro = ROLES[rnorm(p.n)]; if (!ro) return '';
  const am = u.best.pay < 2 ? 0 : Math.round((u.best.pay - 1) * 100), band = am === 0 ? 'minus money' : (ROB.find(([lo, hi]) => am >= lo && am <= hi) || [])[2];
  const c = RODDS.find(x => x.a === ro && x.s === RST[u.k] && x.b === band);
  if (!c || c.n < 20 || c.r <= 0 || c.w <= c.e) return '';
  return ` <b class="lstar" title="${esc(`${ro} · ${RST[u.k].toLowerCase()} · ${c.b}: ${c.w} won vs ${c.e.toFixed(1)} priced on ${c.n} straights, +${Math.round(c.r * 100)}% (Profit page, NFL straights by role, last 30 days). Picked after the fact: a lead, not a rule.`)}">★</b>`;
}
let LADS = false;
try { LADS = localStorage.getItem('nfl-lads') === '1'; } catch (e) {}
document.addEventListener('click', e => { const b = e.target.closest('[data-nlads]'); if (!b) return;
  LADS = !LADS; try { localStorage.setItem('nfl-lads', LADS ? '1' : '0'); } catch (x) {} render(); });
// rungs shown on a row (top 3 by the move / star filters); every rung of his ladder with a ★ (flagged first, then the dropdown's)
const ladShown = c => (LADM ? c.rungs.filter(u => ladMv(c, u) === LADM) : c.rungs).filter(u => !LADS || aStar(c.p, u)).slice(0, 3);
const starRungs = c => [...c.rungs, ...(c.all || []).filter(u => !c.rungs.some(r => r.k === u.k && r.l === u.l))].filter(u => aStar(c.p, u));
function starElse(c){
  const shown = ladShown(c);
  if (shown.some(u => aStar(c.p, u))) return '';
  const u = starRungs(c).find(x => !shown.some(y => y.k === x.k && y.l === x.l));
  return u ? `<div class="lstarx"><span class="small muted">★ on another rung:</span> ${lruChip(c, u)}</div>` : '';
}
const ladMv = (c, u) => window.MKT && MKT.consKind ? MKT.consKind(c.g.id, c.p.n, u.k) : null;
function ladderCard(){
  if (!D.dvp) return '';
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
      // like the NBA's ladder edges (owner 2026-11-01): each rung at Kalshi (fee in) and at Hard Rock / Novig for the same
      // number, best price bold, best edge first. No 5% floor here: the record is the soft matchup (edge under 5% +6% on 1,919)
      const rungs = (window.MKT && MKT.ladder ? MKT.ladder(g.id, p.id, p.n) : []).map(ladAnchor).map(ladPrice).sort((a, b) => (b.edge ?? -9) - (a.edge ?? -9));
      const all = window.MKT && MKT.ladderAll ? MKT.ladderAll(g.id, p.id, p.n).map(ladAnchor).map(ladPrice) : rungs;
      rungs.forEach(u => regL(g, p, u, 'Ladder candidate'));
      cards.push({g, p, r, k, extra, rungs, all, best: rungs.length ? rungs[0].edge ?? -9 : -9});
    }
  }
  if (!cards.length) return '';
  cards.sort((a, b) => b.best - a.best || a.r.rank - b.r.rank);
  const row = c => `<div class="lad"><div class="ladh"><b>${esc(c.p.n)}</b> <span class="small muted">${esc(c.p.t)} vs ${esc(c.p.o)} · ${esc(c.p.pos)}</span>
      <span class="ladc soft" title="${esc(c.p.o)} gives up the ${ord(c.r.rank)} most to ${c.k === 'WR' ? 'wide receivers' : c.k + 's'} this season (defense vs position)">soft: ${ord(c.r.rank)} most to ${c.k}s</span>${c.extra.map(x => `<span class="ladc">${esc(x)}</span>`).join('')}</div>
    <div class="ladr">${ladShown(c).map(u => lruChip(c, u)).join('')}</div>${starElse(c)}</div>`;
  const on0 = cards.filter(c => c.rungs.length);
  if (LADM && !on0.some(c => c.rungs.some(u => ladMv(c, u) === LADM))) LADM = '';
  const nStar = on0.filter(c => starRungs(c).length).length;
  const on = (LADM ? on0.filter(c => c.rungs.some(u => ladMv(c, u) === LADM)) : on0).filter(c => !LADS || starRungs(c).some(u => !LADM || ladMv(c, u) === LADM));
  if (!on.some(c => String(c.g.id) === LADG)) LADG = '';
  const vis = LADG ? on.filter(c => String(c.g.id) === LADG) : on, lim = LADG ? 999 : 8, off = cards.filter(c => !c.rungs.length && (!LADG || String(c.g.id) === LADG));
  const wait = off.length ? `<details class="ladw"><summary>${on.length ? `${off.length} more` : `${off.length} players`} · Kalshi ladders not posted yet</summary><div class="ladr">${off.map(c =>
      `<span class="lru" title="${esc(c.p.o)}: ${ord(c.r.rank)} most to ${c.k}s${c.extra.length ? ' · ' + c.extra.join(' · ') : ''}"><b>${esc(c.p.n)}</b> <i>${esc(c.p.pos)} vs ${esc(c.p.o)}</i></span>`).join('')}</div></details>` : '';
  return `<section class="panel"><div class="phead"><h2>Ladder candidates <span class="exp3">experimental</span></h2><span class="small muted">${cards.length} player${cards.length === 1 ? '' : 's'}</span></div>
    <details class="how"><summary>What this is</summary><p class="note">Players facing one of the 8 defenses that have given up the most to their position this season, with their higher Kalshi rungs (two or more lines above the one priced near 50/50). Each rung shows Kalshi (K, fee included), Hard Rock’s alt line (HR) and Novig’s ladder (NV) at the same number; the bold one is the best price (tap it to open the bet when the book gives a link). Tap a rung’s number to switch to a lower rung and check its prices; rungs below the card’s range show our chance, with no edge claimed. A green % is our chance vs that best price, shown at 5% or more, and players are ordered by it. Rungs without one stay on the list: the record so far is the soft matchup, not our number (2026 through week 8: +8% on 2,885 rungs, up in both halves of the season, and +6% on rungs where we showed under 5%). The market seems to price the average game against a bad defense but not the ceiling. That is one season, and several rungs of one ladder win or lose together, so these are tracked on the Profit page, not a recommendation.</p></details>
    ${LADS && !on.length && on0.length ? `<p class="note" style="margin:0 0 6px">No ladder candidate has a ★ right now. Tap ★ only again to see them all.</p>` : ''}
    ${on0.length ? `<div class="ladsw" role="group" aria-label="Ladder candidates view">${[['game', 'By game'], ['edge', 'Best first']].map(([k, l]) => `<button type="button" data-nladv="${k}" aria-pressed="${NLADV === k}">${l}</button>`).join('')}</div><div class="ladsw" role="group" aria-label="Star filter"><button type="button" data-nlads="1" aria-pressed="${LADS}" title="Only players / rungs whose role, stat and odds range made money in NFL straights by role on the Profit page (last 30 days, 20+ bets). Picked after the fact: a lead, not a rule.">★ only <i>${nStar}</i></button></div>${window.MKT && MKT.consBar ? MKT.consBar(on0, LADM) : ''}` : ''}
    <div class="ladbody">${ladRail(on, D.games)}${NLADV === 'game' ? ladByGame(vis, row) : `<div class="ladg">${vis.slice(0, lim).map(row).join('')}</div>${vis.length > lim ? `<details class="ladw"><summary>${vis.length - lim} more with ladders</summary><div class="ladg">${vis.slice(lim).map(row).join('')}</div></details>` : ''}`}${wait}</div></section>`;
}
// Ladders tab (owner 2026-11-11: 'move the ladder ... candidates ... out side of the parlays tab and give them their own tab')
function renderLadders(){ return ladderCard() || '<section class="panel pempty"><div class="phead"><h2>Ladder candidates <span class="exp3">experimental</span></h2></div><p class="note">No soft-matchup players with Kalshi ladders yet · they fill in as the week’s ladders post.</p></section>'; }
// + on every ladder rung (owner 2026-11-11: 'make all bets in there able to be added to the parlay builder even if i switch to a
// different leg'): the leg is the rung shown (the dropdown re-renders the chip with its own +); another rung of the same
// player-stat replaces it in the slip. Prices: every book at that number (Kalshi with its fee) for 'Each book pays'
const amK = c => Math.round(c >= .5 ? -100 * c / (1 - c) : 100 * (1 - c) / c);
function rungSlip(g, p, u){
  if (!window.SLIP || !u) return '';
  const px = {};
  for (const o of u.opts || []) px[o.b] = o.b === 'Kalshi' ? amK(u.cost) : Number(String(o.price).replace('+', ''));
  const b = (u.best && u.best.b) || 'Kalshi', L = slipLeg(p, u.k, u.l, 'over', u.p);
  return ' ' + SLIP.btn({...L, lab: `${p.n} ${Math.round(u.l + .5)}+ ${SLIP_LAB[u.k] || u.k}`, book: b, o: px[b], px});
}
// Ladder candidates by game (owner 2026-10-25: "why are my ladder candidates not separated by game"): games in kickoff
// order, each game's players best edge first, five shown and the rest folded; remembered on this device (nfl-ladv).
let NLADV = 'game';
try { NLADV = localStorage.getItem('nfl-ladv') || 'game'; if (NLADV === 'rank') NLADV = 'edge'; } catch (e) {}
function ladByGame(cards, row){
  const by = new Map();
  for (const c of cards) (by.get(c.g.id) || by.set(c.g.id, []).get(c.g.id)).push(c);
  const cap = LADG ? 999 : 5;
  return D.games.filter(g => by.has(g.id)).map(g => { const L = by.get(g.id);
    return `<div class="ladgm"><div class="ladgh"><b>${esc(g.away)} @ ${esc(g.home)}</b> <span class="small muted">${esc(g.kickoff || '')} · ${L.length} player${L.length === 1 ? '' : 's'}${L[0].best >= .05 ? ` · best +${Math.round(L[0].best * 100)}%` : ''}</span></div>
      <div class="ladg">${L.slice(0, cap).map(row).join('')}</div>${L.length > cap ? `<details class="ladw"><summary>${L.length - cap} more in this game</summary><div class="ladg">${L.slice(cap).map(row).join('')}</div></details>` : ''}</div>`; }).join('');
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
      const rungs = MKT.ladder(g.id, p.id, p.n).filter(u => rungOk(p, u) && (!MKT.legOk || MKT.legOk({kind: u.k, price: u.cost}, 'nfl')));   // + the one leg rule
      if (!rungs.length) continue;
      rungs.forEach(u => regL(g, p, u, 'Ladder longshot'));
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
      const rungs = MKT.ladder(g.id, p.id, p.n).filter(u => rungOk(p, u) && (!MKT.legOk || MKT.legOk({kind: u.k, price: u.cost}, 'nfl')));   // + the one leg rule
      if (!rungs.length) continue;
      rungs.forEach(u => regL(g, p, u, 'Primetime ladder'));
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
        if (MKT.legOk && !MKT.legOk({kind: st, price: u.best.o}, 'nfl')) continue;  // the one leg rule (25-55%, no passing / rushing)
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
  if (window.MKT && MKT.FREEZE) try { ladderCard(); } catch (e) {}      // the freeze loads #parlays: ladder rungs still register for the screener
  const ladder = (window.MKT && MKT.dailyCard ? MKT.dailyCard() : '') + pathLongshots() + primetimeLadders() + ladderLongshots();
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
/* ---------------- field map (owner 2026-11-23: 'I want a heat map for nfl like the nhl shot map or nba shot map'): fieldmap.py ->
   markets/fieldmap_nfl.json, loaded the first time the Field map view opens. Replaces the old Matchup map grid (the same depth x
   direction areas, finer, and the run lanes) with a heat map: where the offense goes / what the defense allows / both, by volume,
   yards a play or success, the defense against one receiver group, and each listed player's targets and carries. This season's
   regular-season plays; display only (no number on the page uses it). ---------------- */
let NFM = null, NFM_ST = '';
function nfmLoad(){
  if (NFM || NFM_ST) return; NFM_ST = 'loading';
  try { fetch(SCR_ROOT + 'markets/fieldmap_nfl.json?t=' + Math.floor(Date.now() / 6e5), {cache: 'no-store'}).then(r => r.ok ? r.json() : null)
    .then(d => { NFM = d && d.teams ? d : null; NFM_ST = NFM ? 'ok' : 'none'; render(); }).catch(() => { NFM_ST = 'none'; render(); }); } catch (e) { NFM_ST = 'none'; }
}
const nfLS = (k, ok, dflt) => { try { const v = localStorage.getItem(k); return ok.includes(v) ? v : dflt; } catch (e) { return dflt; } };
let NFMODE = nfLS('nfl-fmode', ['ov', 'off', 'def'], 'ov');     // overlay / where they go / what the defense allows
let NFMET = nfLS('nfl-fmet', ['vol', 'yds', 'suc'], 'vol');     // volume / yards a play / success rate
let NFPV = nfLS('nfl-fpv', ['spots', 'dots'], 'spots');         // a picked player: his spots or every target
let NFGRP = '', NFDF = 'all';                                    // defense view: targets to one group; dots: all / caught / not
const NFMP = {}, NFMC = {};                                      // team -> picked player id / tapped area 'p:i' (pass) or 'r:i' (run)
// pass areas: 7 air-yard bands (bottom = behind the line) x left / middle / right, index row * 3 + col; [label, from, to] in yards
const NF_ROWS = [['Behind the line', -6, 0], ['1-4 yds', 0, 5], ['5-9 yds', 5, 10], ['10-14 yds', 10, 15], ['15-19 yds', 15, 20], ['20-29 yds', 20, 30], ['30+ yds', 30, 42]];
const NF_RL = ['behind', '1-4', '5-9', '10-14', '15-19', '20-29', '30+'];
const NF_COLS = ['left', 'middle', 'right'];
const NF_LANES = ['L end', 'L tackle', 'L guard', 'Middle', 'R guard', 'R tackle', 'R end'];
const NF_GRP = {OUT: 'outside WRs', SLOT: 'slot WRs', TE: 'TEs', RB: 'RBs'};
const NF_PZ = [['Behind line', [0, 1, 2], '#99e9f2'], ['Short 1-9', [3, 4, 5, 6, 7, 8], '#4c6ef5'], ['Medium 10-19', [9, 10, 11, 12, 13, 14], '#f08c00'], ['Deep 20+', [15, 16, 17, 18, 19, 20], '#c92a2a']];
const NF_RZ = [['Inside runs', [2, 3, 4]], ['Off tackle', [1, 5]], ['Outside runs', [0, 6]]];
const nfPC = (rows, cols) => rows.flatMap(r => cols.map(c => r * 3 + c));
// areas a missing defensive starter usually covers (boxscore/defout.py roles), outlined dashed
const NF_COVER = {CB1: {p: nfPC([1, 2, 3, 4, 5, 6], [0, 2])}, CB2: {p: nfPC([1, 2, 3, 4, 5, 6], [0, 2])}, CB3: {p: [...nfPC([1, 2, 3, 4], [1]), ...nfPC([0], [0, 2])]},
  S: {p: [...nfPC([5, 6], [0, 1, 2]), ...nfPC([3, 4], [1])]}, LB: {p: nfPC([0, 1, 2, 3, 4], [1]), r: [2, 3, 4]}, DL: {r: [0, 1, 2, 3, 4, 5, 6]}};
const NF_RN = {CB1: 'top corner', CB2: 'No. 2 corner', CB3: 'slot corner', S: 'safety', LB: 'linebacker', DL: 'D-lineman'};
// holes in the defense by unit (owner 2026-11-23: 'Holes in the defense the line or the secondary etc'): the areas each unit mostly
// answers for (the same split as the missing-starter outlines); [key, name, what, areas (p = pass areas, r = run lanes; none = sacks)]
const NF_UNITS = [['rush', 'Pass rush', 'sacks a dropback', null], ['in', 'Run D inside', 'middle and guard runs', {r: [2, 3, 4]}],
  ['edge', 'Run D outside', 'tackle and end runs', {r: [0, 1, 5, 6]}], ['under', 'Underneath', 'screens, short middle', {p: [0, 1, 2, 4, 7]}],
  ['cb', 'Corners', 'short and medium outside', {p: nfPC([1, 2, 3, 4], [0, 2])}], ['saf', 'Safeties', 'deep and medium middle', {p: [...nfPC([3, 4], [1]), ...nfPC([5, 6], [0, 1, 2])]}]];
const NFUN = {};                                                  // team (offense) -> the unit picked in its opponent's holes table
// a unit's record: yards on plays in its areas over what the league gains in the same areas on the same plays (25 plays at the
// league's rate pulled in), success the same way, and the side's share of its passes / runs there; pass rush = 1 / (sack rate over
// the league's, 150 dropbacks pulled in), so above 1 = good for the offense everywhere
function nfUnitStat(T, side, u){
  const L = NFM.lg;
  if (!u[3]){ const x = T[side].sk || [0, 0], lr = L.sk ? L.sk[1] / L.sk[0] : null; if (!lr) return {v: 1, rate: null, lg: null};
    return {v: 1 / (((x[1] + 150 * lr) / (x[0] + 150)) / lr), rate: x[0] ? x[1] / x[0] : null, lg: lr, n: x[0]}; }
  const run = !!u[3].r, ix = run ? u[3].r : u[3].p, C = run ? T[side].r : T[side].p, LC = run ? L.r : L.p, K = 25;
  let Y = 0, E = 0, S = 0, ES = 0, n = 0, ly = 0, ls_ = 0, ln = 0;
  for (const i of ix){ const x = C[i], l = LC[i]; if (!l[0]) continue; Y += x[2]; E += x[0] * l[2] / l[0]; S += x[3]; ES += x[0] * l[3] / l[0]; n += x[0]; ly += l[2]; ls_ += l[3]; ln += l[0]; }
  const rb = ly / ln, sb = ls_ / ln, N = nfSum(C), LN = nfSum(LC), A = run ? NF_A.r : NF_A.p, lsh = ln / LN;
  return {v: (Y + K * rb) / (E + K * rb), sv: (S + K * sb) / (ES + K * sb), ypp: n ? Y / n : null, sr: n ? S / n : null, lg: rb, lsr: sb, n, sh: ((n + A * lsh) / (N + A)) / lsh, shr: N ? n / N : 0, lsh};
}
let NFHOLE = null;
function nfHoles(){                                              // every team's units, ranked 1 = the biggest hole of the 32
  if (NFHOLE && NFHOLE.src === NFM) return NFHOLE;
  const H = {src: NFM, t: {}};
  for (const [tm, T] of Object.entries(NFM.teams)) H.t[tm] = Object.fromEntries(NF_UNITS.map(u => [u[0], {d: nfUnitStat(T, 'd', u), o: nfUnitStat(T, 'o', u)}]));
  for (const u of NF_UNITS) Object.entries(H.t).sort((a, b) => b[1][u[0]].d.v - a[1][u[0]].d.v).forEach(([tm], i) => { H.t[tm][u[0]].rk = i + 1; });
  NFHOLE = H; return H;
}
const nfGrade = v => v >= 1.12 ? ['Hole', 'h2'] : v >= 1.05 ? ['Soft spot', 'h1'] : v > .95 ? ['Average', 'h0'] : v > .88 ? ['Solid', 's1'] : ['Strength', 's2'];
// the blended map (owner 2026-11-23: 'Don't make it a grid you can blend it so it looks better'): each area's value sits at its
// centre and is blended smoothly to its neighbours (smoothstep between centres) on a 1-yard canvas, which the browser scales up
// smoothly; the areas are still there to tap
const NF_XC = [53.33 / 6, 53.33 / 2, 53.33 * 5 / 6], NF_YC = [-3, 2.5, 7.5, 12.5, 17.5, 25, 36];
const nfSeg = (C, v) => { if (v <= C[0]) return [0, 0, 0]; for (let j = 0; j < C.length - 1; j++) if (v < C[j + 1]){ const t = (v - C[j]) / (C[j + 1] - C[j]); return [j, j + 1, t * t * (3 - 2 * t)]; } const k = C.length - 1; return [k, k, 0]; };
function nfRGBA(v, w){
  if (NFMET === 'vol') return v >= 1 ? [255, 99, 71, Math.min(1, (v - 1) / .4) * .95 * w] : [70, 150, 255, Math.min(1, (1 - v) / .3) * .85 * w];
  const s = NFMET === 'yds' ? .25 : .15;
  return v >= 1 ? [52, 210, 122, Math.min(1, (v - 1) / s) * .95 * w] : [240, 72, 72, Math.min(1, (1 - v) / s) * .9 * w];
}
// a gentle Gaussian blur of the value grid (edges held), so neighbouring areas melt into each other instead of banding
function nfBlur(A, w, h, sx, sy){
  const ker = sg => { if (!sg) return [1]; const r = Math.ceil(sg * 2.5), a = []; let t = 0; for (let i = -r; i <= r; i++){ const v = Math.exp(-i * i / (2 * sg * sg)); a.push(v); t += v; } return a.map(v => v / t); };
  const kx = ker(sx), ky = ker(sy), rx = (kx.length - 1) / 2, ry = (ky.length - 1) / 2, T = new Float32Array(w * h), O = new Float32Array(w * h);
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++){ let s_ = 0; for (let i = -rx; i <= rx; i++) s_ += A[y * w + Math.min(w - 1, Math.max(0, x + i))] * kx[i + rx]; T[y * w + x] = s_; }
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++){ let s_ = 0; for (let j = -ry; j <= ry; j++) s_ += T[Math.min(h - 1, Math.max(0, y + j)) * w + x] * ky[j + ry]; O[y * w + x] = s_; }
  return O;
}
const NFIMG = new Map();
function nfImg(V, W, run){                                       // values (+ fade weights) -> a data URL, cached
  const key = (run ? 'r' : 'p') + NFMET + V.map((v, i) => (v == null ? 'n' : v.toFixed(3)) + ':' + W[i].toFixed(2)).join(',');
  if (NFIMG.has(key)) return NFIMG.get(key);
  if (NFIMG.size > 300) NFIMG.clear();
  let url = '';
  try {
    const cw = run ? 56 : 53, ch = run ? 1 : 48, cv = document.createElement('canvas'); cv.width = cw; cv.height = ch;
    const ctx = cv.getContext('2d'), im = ctx.createImageData(cw, ch), val = i => V[i] == null ? 1 : V[i];
    const GV = new Float32Array(cw * ch), GW = new Float32Array(cw * ch);
    for (let py = 0; py < ch; py++){
      const [r0, r1, ty] = run ? [0, 0, 0] : nfSeg(NF_YC, 42 - (py + .5));
      for (let px = 0; px < cw; px++){
        let v, w;
        if (run){ const [a, b, t] = nfSeg([4, 12, 20, 28, 36, 44, 52], px + .5); v = val(a) * (1 - t) + val(b) * t; w = W[a] * (1 - t) + W[b] * t; }
        else { const [c0, c1, tx] = nfSeg(NF_XC, px + .5), at = (r, c) => r * 3 + c;
          const top = (i, j) => val(at(r0, i)) * (1 - tx) + val(at(r0, j)) * tx, bot = (i, j) => val(at(r1, i)) * (1 - tx) + val(at(r1, j)) * tx;
          v = top(c0, c1) * (1 - ty) + bot(c0, c1) * ty;
          const wt = (r, i, j) => W[at(r, i)] * (1 - tx) + W[at(r, j)] * tx; w = wt(r0, c0, c1) * (1 - ty) + wt(r1, c0, c1) * ty; }
        GV[py * cw + px] = v; GW[py * cw + px] = w;
      }
    }
    const BV = nfBlur(GV, cw, ch, run ? 3 : 3, run ? 0 : 2.2), BW = nfBlur(GW, cw, ch, run ? 3 : 3, run ? 0 : 2.2);
    for (let q = 0; q < cw * ch; q++){ const [R, G, B, A] = nfRGBA(BV[q], BW[q]), o = q * 4;
      im.data[o] = R; im.data[o + 1] = G; im.data[o + 2] = B; im.data[o + 3] = Math.round(Math.max(0, Math.min(1, A)) * 255); }
    ctx.putImageData(im, 0, 0); url = cv.toDataURL();
  } catch (e) { url = ''; }
  NFIMG.set(key, url); return url;
}
// per column, the runs of consecutive rows in a set of pass areas (for outlines)
function nfRuns(set){
  const out = [];
  for (let c = 0; c < 3; c++){ let r0 = null; for (let r = 0; r <= 7; r++){ const inn = r < 7 && set.has(r * 3 + c); if (inn && r0 == null) r0 = r; if (!inn && r0 != null){ out.push({c, r0, r1: r - 1}); r0 = null; } } }
  return out;
}
const NF_HATCH = '<defs><pattern id="nfm-hatch" patternUnits="userSpaceOnUse" width="7" height="7" patternTransform="rotate(45)"><rect width="7" height="7" fill="rgba(242,193,78,.12)"/><line x1="0" y1="0" x2="0" y2="7" stroke="#F2C14E" stroke-width="1.6" stroke-opacity=".75"/></pattern></defs>';
const NFB64 = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_';
const NF_A = {p: 80, r: 60, g: 40}, NF_K = 12;                    // pseudo-plays toward the league's mix / rate
const nfPc = v => v == null ? '–' : (v >= 1 ? '+' : '−') + Math.abs(Math.round((v - 1) * 100)) + '%';
const nfOrd = n => { const s_ = ['th', 'st', 'nd', 'rd'], v = n % 100; return n + (s_[(v - 20) % 10] || s_[v] || s_[0]); };
const nfLast = n => { const w = String(n || '').replace(/\s+(Jr\.?|Sr\.?|II|III|IV|V)$/i, '').trim().split(/\s+/); return w[w.length - 1] || n; };
const nfSum = C => C.reduce((s, x) => s + x[0], 0);
// each area: the side's share of its plays over the league's share there (A pseudo-plays at the league's mix), or yards a play /
// success rate over the league's in that area (NF_K pseudo-plays at the league's rate); null where the league has none
function nfRel(C, LC, A, met){
  const N = nfSum(C), LN = nfSum(LC);
  return C.map((x, i) => { const L = LC[i]; if (!L || !L[0] || !LN) return null;
    if (met === 'vol'){ const d = L[0] / LN; return (x[0] + A * d) / (N + A) / d; }
    const j = met === 'yds' ? 2 : 3, lr = L[j] / L[0]; if (!(lr > 0)) return null;
    return ((x[j] + NF_K * lr) / (x[0] + NF_K)) / lr; });
}
const nfMul = (a, b) => a.map((v, i) => v == null || b[i] == null ? null : v * b[i]);
function nfGrids(O, F, met, G){                                 // {P, R} for the view shown + each side's own
  const L = NFM.lg, oP = nfRel(O.o.p, L.p, NF_A.p, met), oR = nfRel(O.o.r, L.r, NF_A.r, met);
  const dP = G ? nfRel(F.g[G], L.g[G], NF_A.g, met) : nfRel(F.d.p, L.p, NF_A.p, met), dR = nfRel(F.d.r, L.r, NF_A.r, met);
  const P = NFMODE === 'off' ? oP : NFMODE === 'def' ? dP : nfMul(oP, dP), R = NFMODE === 'off' ? oR : NFMODE === 'def' ? dR : nfMul(oR, dR);
  return {P, R, oP, oR, dP, dR};
}
const nfSpFill = v => { if (v == null) return 'rgba(255,255,255,.10)'; const vol = NFMET === 'vol', d = v - 1, a = Math.min(1, Math.abs(d) / (vol ? .25 : NFMET === 'yds' ? .15 : .1));
  return `rgba(${d >= 0 ? (vol ? '255,99,71' : '52,210,122') : (vol ? '70,150,255' : '240,72,72')},${(.1 + .9 * a).toFixed(2)})`; };
const nfRow = a => a <= 0 ? 0 : a <= 4 ? 1 : a <= 9 ? 2 : a <= 14 ? 3 : a <= 19 ? 4 : a <= 29 ? 5 : 6;
const NFTC = new Map();
function nfTg(s){                                               // packed targets -> [{a: air yards, c: direction, k: caught, cell}]
  if (!s) return [];
  if (!NFTC.has(s)){ const o = [];
    for (let i = 0; i + 1 < s.length; i += 2){ let v = NFB64.indexOf(s[i]) * 64 + NFB64.indexOf(s[i + 1]); const k = v % 2; v = (v - k) / 2;
      const c = v % 3, a = (v - c) / 3 - 10; o.push({a, c, k, cell: nfRow(a) * 3 + c}); }
    NFTC.set(s, o); }
  return NFTC.get(s);
}
// geometry: 53 1/3 yds wide = 318 px (6 px a yard), a 36 px gutter for the depth labels, -6 (behind the line) to 42 yds downfield
const NFX0 = 36, NFW = 318, NFU = 6, NFTOP = 16;
const nfY = yd => NFTOP + (42 - yd) * NFU, nfCX = c => NFX0 + c * NFW / 3;
const nfCell = i => { const r = Math.floor(i / 3), c = i % 3, [, lo, hi] = NF_ROWS[r]; return {r, c, x: nfCX(c) + NFW / 6, y: (nfY(lo) + nfY(hi)) / 2, h: (hi - lo) * NFU}; };
function nfDots(T){
  let out = '';
  T.forEach((x, i) => {
    if ((NFDF === 'made' && !x.k) || (NFDF === 'miss' && x.k)) return;
    const jit = (((i * 37 + x.a * 11) % 29) / 28 - .5) * NFW / 3 * .82, cx = (nfCX(x.c) + NFW / 6 + jit).toFixed(1), cy = nfY(Math.max(-5.6, Math.min(41.5, x.a + (x.a <= 0 ? -1.5 : 0)))).toFixed(1);
    out += x.k ? `<circle cx="${cx}" cy="${cy}" r="2.9" class="nfm-dm"/>` : `<circle cx="${cx}" cy="${cy}" r="2.2" class="nfm-dx"/>`;
  });
  return out;
}
function nfSvg(team, V, marks, pick, dim, cover, hl){
  const LP = NFM.lg.p, LN = nfSum(LP), dmax = Math.max(...LP.map(x => x[0] / LN));
  const W = LP.map(x => Math.max(.5, Math.min(1, Math.sqrt(x[0] / LN / dmax) * 1.5)));
  let hits = '', lab = '', cov = '', hat = '';
  for (let r = 0; r < 7; r++){
    const [, lo, hi] = NF_ROWS[r], y0 = nfY(hi), h = (hi - lo) * NFU;
    lab += `<text x="${NFX0 - 5}" y="${(y0 + h / 2 + 3.5).toFixed(1)}" class="nfm-rl">${NF_RL[r]}</text>`;
    for (let c = 0; c < 3; c++){
      const i = r * 3 + c, x0 = nfCX(c);
      if (hl && hl.has(i)) hat += `<rect x="${x0.toFixed(1)}" y="${y0.toFixed(1)}" width="${(NFW / 3).toFixed(1)}" height="${h}" fill="url(#nfm-hatch)"/>`;
      hits += `<rect x="${x0.toFixed(1)}" y="${y0.toFixed(1)}" width="${(NFW / 3).toFixed(1)}" height="${h}" class="nfm-hit${pick === 'p:' + i ? ' on' : ''}" data-nfc="${esc(team)}:p:${i}"/>`;
    }
  }
  if (cover) for (const q of nfRuns(cover.p)){ const y1 = nfY(NF_ROWS[q.r1][2]), y2 = nfY(NF_ROWS[q.r0][1]);
    cov += `<rect x="${(nfCX(q.c) + 3).toFixed(1)}" y="${(y1 + 3).toFixed(1)}" width="${(NFW / 3 - 6).toFixed(1)}" height="${(y2 - y1 - 6).toFixed(1)}" rx="6" class="nfm-cov"/>`; }
  const url = nfImg(V, W, false);
  const heat = url ? `<image href="${url}" x="${NFX0}" y="${NFTOP}" width="${NFW}" height="${48 * NFU}" preserveAspectRatio="none" class="nfm-heat"${dim ? ' opacity=".35"' : ''}/>` : '';
  let fl = `<rect x="${NFX0 + .5}" y="${NFTOP + .5}" width="${NFW - 1}" height="${48 * NFU - 1}" class="side"/>`;
  for (let yd = 5; yd <= 40; yd += 5) fl += `<line x1="${NFX0}" x2="${NFX0 + NFW}" y1="${nfY(yd)}" y2="${nfY(yd)}" class="yl${yd % 10 ? '' : ' ten'}"/>`;
  for (let yd = -5; yd <= 41; yd++) if (yd % 5) for (const hx of [23.58, 29.75]) fl += `<line x1="${(NFX0 + hx * NFU - 2).toFixed(1)}" x2="${(NFX0 + hx * NFU + 2).toFixed(1)}" y1="${nfY(yd)}" y2="${nfY(yd)}" class="hs"/>`;
  fl += `<line x1="${NFX0}" x2="${NFX0 + NFW}" y1="${nfY(0)}" y2="${nfY(0)}" class="los"/>`;
  const cl = NF_COLS.map((c, i) => `<text x="${(nfCX(i) + NFW / 6).toFixed(1)}" y="11" class="nfm-cl">${c}</text>`).join('');
  return `<svg class="nfm-svg" viewBox="0 0 ${NFX0 + NFW + 3} ${NFTOP + 48 * NFU + 3}" role="img" aria-label="${esc(team)} passes by depth and direction">
    ${NF_HATCH}${cl}${lab}<text x="${NFX0 - 5}" y="${nfY(0) + 3}" class="nfm-rl los">LOS</text>${heat}${hat}<g class="nfm-field" fill="none">${fl}</g>${cov}${marks ? `<g>${marks}</g>` : ''}<g>${hits}</g></svg>`;
}
function nfRunSvg(team, V, marks, pick, dim, cover, hl){
  const w = NFW / 7; let hits = '', lab = '', cov = '', hat = '';
  for (let i = 0; i < 7; i++){
    const x0 = NFX0 + i * w;
    if (hl && hl.has(i)) hat += `<rect x="${x0.toFixed(1)}" y="15" width="${w.toFixed(1)}" height="34" fill="url(#nfm-hatch)"/>`;
    lab += `<text x="${(x0 + w / 2).toFixed(1)}" y="61" class="nfm-ll">${NF_LANES[i]}</text>`;
    hits += `<rect x="${x0.toFixed(1)}" y="14" width="${w.toFixed(1)}" height="36" class="nfm-hit${pick === 'r:' + i ? ' on' : ''}" data-nfc="${esc(team)}:r:${i}"/>`;
  }
  // the missing starters' lanes: one outline a run of neighbouring lanes
  if (cover){ let a = null; for (let i = 0; i <= 7; i++){ const inn = i < 7 && cover.r.has(i); if (inn && a == null) a = i; if (!inn && a != null){
    cov += `<rect x="${(NFX0 + a * w + 3).toFixed(1)}" y="17.5" width="${((i - a) * w - 6).toFixed(1)}" height="29" rx="6" class="nfm-cov"/>`; a = null; } } }
  const url = nfImg(V, Array(7).fill(1), true);
  const heat = url ? `<image href="${url}" x="${NFX0}" y="15" width="${NFW}" height="34" preserveAspectRatio="none" class="nfm-heat"${dim ? ' opacity=".35"' : ''}/>` : '';
  const ol = [1, 2, 3.5, 5, 6].map(k => `<circle cx="${(NFX0 + k * w).toFixed(1)}" cy="7.5" r="3.6" class="ol"/>`).join('');
  return `<svg class="nfm-svg nfm-run" viewBox="0 0 ${NFX0 + NFW + 3} 66" role="img" aria-label="${esc(team)} runs by lane">
    ${NF_HATCH}<text x="${NFX0 - 5}" y="35" class="nfm-rl">runs</text>${heat}${hat}<g class="nfm-field" fill="none"><rect x="${NFX0 + .5}" y="15" width="${NFW - 1}" height="34" class="side"/><line x1="${NFX0}" x2="${NFX0 + NFW}" y1="12.5" y2="12.5" class="los"/>${ol}</g>${cov}${lab}${marks ? `<g>${marks}</g>` : ''}<g>${hits}</g></svg>`;
}
const nfFitOf = (cnt, vals) => { let w = 0, s = 0; cnt.forEach((n, i) => { if (n && vals[i] != null){ w += n; s += n * vals[i]; } }); return w ? s / w : null; };
const nfAgg = (C, ix) => ix.reduce((a, i) => [a[0] + C[i][0], a[1] + C[i][1], a[2] + C[i][2], a[3] + C[i][3]], [0, 0, 0, 0]);
const nfName = (k, i) => k === 'r' ? `Runs ${NF_LANES[i] === 'Middle' ? 'up the middle' : 'off ' + NF_LANES[i].replace('L ', 'left ').replace('R ', 'right ')}`
  : (() => { const r = Math.floor(i / 3), c = i % 3; return r ? `${NF_COLS[c][0].toUpperCase() + NF_COLS[c].slice(1)}, ${NF_ROWS[r][0]}` : `Behind the line, ${NF_COLS[c]}`; })();
function nfBlock(off, def, g){
  const O = NFM.teams[off], F = NFM.teams[def];
  if (!O || !F) return `<div class="nfm-b"><h3>${esc(off)} offense <small>vs ${esc(def)} defense</small></h3><div class="empty">No plays yet this season.</div></div>`;
  const G = NFMODE === 'def' && NFGRP ? NFGRP : '', who = G ? NF_GRP[G] : 'opponents';
  const V = nfGrids(O, F, NFMET, G), LP = NFM.lg.p, LR = NFM.lg.r, LNp = nfSum(LP), LNr = nfSum(LR);
  // players: tonight's listed RBs / WRs / TEs (and running QBs) with targets or carries this season, most projected looks first
  const P = NFM.players || {}, mean = (p, st) => p.s && p.s[st] ? p.s[st][MEAN] : 0;
  const all = D.players.filter(p => p.g === g.id && p.t === off && P[p.id] && (mean(p, 'tgt') >= 1 || mean(p, 'car') >= 2) && (p.pos !== 'QB' || mean(p, 'car') >= 3))
    .map(p => ({p, x: P[p.id], key: nfhKey(p)})).sort((a, b) => mean(b.p, 'tgt') + mean(b.p, 'car') - mean(a.p, 'tgt') - mean(a.p, 'car'));
  const same = G ? all.filter(r => r.key === G) : all, pl = same.length ? same : all;
  const sel = all.find(r => String(r.p.id) === String(NFMP[off])), S = sel && sel.x;
  const cnt = x => { const T = nfTg(x && x.t), pc = Array(21).fill(0), pk = Array(21).fill(0); for (const t of T){ pc[t.cell]++; pk[t.cell] += t.k; }
    return {T, pc, pk, rc: x && x.r ? x.r.map(y => y[0]) : Array(7).fill(0)}; };
  const SC = S ? cnt(S) : null, spots = S && NFPV === 'spots';
  const mxP = SC ? Math.max(1, ...SC.pc) : 1, mxR = SC ? Math.max(1, ...SC.rc) : 1;
  const pMarks = !SC ? '' : spots ? SC.pc.map((n, i) => { if (!n) return ''; const q = nfCell(i);
    return `<circle cx="${q.x.toFixed(1)}" cy="${q.y.toFixed(1)}" r="${Math.min(q.h / 2 - 1.5, 3.5 + 11 * Math.sqrt(n / mxP)).toFixed(1)}" class="nfm-sp" style="fill:${nfSpFill(V.P[i])}"/>`; }).join('') : nfDots(SC.T);
  const rMarks = SC ? SC.rc.map((n, i) => n ? `<circle cx="${(NFX0 + (i + .5) * NFW / 7).toFixed(1)}" cy="32" r="${(3 + 11.5 * Math.sqrt(n / mxR)).toFixed(1)}" class="nfm-sp" style="fill:${nfSpFill(V.R[i])}"/>` : '').join('') : '';
  // missing defensive starters and the areas they usually cover (shown on the defense and overlay views)
  const outs = ((D.defout || {})[g.id] || {})[def] || [];
  const cover = outs.length && NFMODE !== 'off' ? {p: new Set(outs.flatMap(o => (NF_COVER[o.role] || {}).p || [])), r: new Set(outs.flatMap(o => (NF_COVER[o.role] || {}).r || []))} : null;
  const miss = outs.length ? `<div class="nfm-miss"><b>${esc(def)} missing:</b> ${outs.map(o => `${esc(o.name)} (${esc(NF_RN[o.role] || o.role)}${o.status && o.status !== 'out' ? ', ' + esc(o.status) : ''})`).join(', ')}${cover ? ' <small>dashed = where they usually play</small>' : ''}</div>` : '';
  // holes in the defense by unit, ranked across the 32 defenses, with how much this offense goes at each; a tapped unit's areas are hatched
  const H = nfHoles(), hd = H.t[def], ho = H.t[off], uk = NFUN[off], U = NF_UNITS.find(u => u[0] === uk);
  const hlP = U && U[3] && U[3].p ? new Set(U[3].p) : null, hlR = U && U[3] && U[3].r ? new Set(U[3].r) : null;
  const urow = u => { const k = u[0], d = hd[k].d, o = ho[k].o, [gl, gc] = nfGrade(d.v), on = uk === k, run = !!(u[3] && u[3].r), unit = run ? 'carry' : 'target';
    const allow = !u[3] ? (d.rate != null ? `${(d.rate * 100).toFixed(1)}% <small>sacks · league ${(d.lg * 100).toFixed(1)}%</small>` : '–')
      : d.ypp != null ? `${d.ypp.toFixed(1)} <small>yds a ${unit} · ${nfPc(d.v)}</small>` : '–';
    const tip = !u[3] ? `${def} sacks the QB on ${d.rate != null ? (d.rate * 100).toFixed(1) : '–'}% of dropbacks (${d.n}) vs ${(d.lg * 100).toFixed(1)}% league-wide.`
      : `${def} allows ${d.ypp != null ? d.ypp.toFixed(1) : '–'} yards a ${unit} on ${d.n} plays in these areas, ${nfPc(d.v)} vs what the league gains on the same plays; success on ${d.sr != null ? Math.round(d.sr * 100) : '–'}% (${nfPc(d.sv)}).`;
    const into = u[3] && d.v >= 1.05 && o.sh >= 1.05;
    const og = !u[3] ? (o.rate != null ? `${(o.rate * 100).toFixed(1)}% <small>sacks taken</small>` : '–')
      : `<b class="${o.sh >= 1.1 ? 'up' : o.sh <= .9 ? 'dn' : ''}">${nfPc(o.sh)}</b> <small>${Math.round(o.shr * 100)}% of its ${run ? 'runs' : 'targets'}</small>${into ? ' <span class="nfm-into">goes at it</span>' : ''}`;
    const otip = !u[3] ? `${off} is sacked on ${o.rate != null ? (o.rate * 100).toFixed(1) : '–'}% of its dropbacks.` : `${off} sends ${Math.round(o.shr * 100)}% of its ${run ? 'runs' : 'targets'} here (league ${Math.round(o.lsh * 100)}%) and gains ${o.ypp != null ? o.ypp.toFixed(1) : '–'} yards a ${unit} there (${nfPc(o.v)} vs the league on the same plays).`;
    return `<tr class="${on ? 'on' : ''}"><td class="l"><button type="button" class="nfm-ub" data-nfu="${esc(off)}:${k}" aria-pressed="${on}"><b>${u[1]}</b><small>${u[2]}</small></button></td><td title="${esc(tip)}">${allow}</td><td><span class="nfm-g ${gc}">${gl}</span> <small class="nfm-rk" title="Rank among the 32 defenses: 1st = the biggest hole">${nfOrd(hd[k].rk)}</small></td><td title="${esc(otip)}">${og}</td></tr>`; };
  const holes = `<div class="nfm-holes"><div class="nfm-hh">Holes in the ${esc(def)} defense <small>by unit · rank of 32 (1st = biggest hole) · tap a unit to hatch its areas on the field</small></div>
    <div class="tw"><table class="t nfm-ht"><thead><tr><th class="l">Unit</th><th>${esc(def)} allows</th><th>Grade</th><th>${esc(off)} goes there</th></tr></thead><tbody>${NF_UNITS.map(urow).join('')}</tbody></table></div></div>`;
  // his spots on volume and yards (the line reports both whichever map is shown)
  let fitLine = '';
  if (SC){
    const Gv = nfGrids(O, F, 'vol', G), Gy = nfGrids(O, F, 'yds', G), nt = SC.T.length, nr = SC.rc.reduce((s, n) => s + n, 0);
    const fpc = f => f == null ? '–' : `<b class="${NFMODE === 'off' ? '' : f >= 1.03 ? 'good' : f <= .97 ? 'bad' : ''}">${nfPc(f)}</b>`;
    const say = (fv, fy, run) => NFMODE === 'def' ? `${run ? 'On his run lanes' : 'From his spots'} ${esc(def)} allows ${run ? 'opponents' : esc(who)} ${fpc(fv)} ${run ? 'carries' : 'targets'} and ${fpc(fy)} yards a ${run ? 'carry' : 'target'} vs average`
      : NFMODE === 'off' ? `${run ? 'On his run lanes' : 'From his spots'} ${esc(off)} ${run ? 'runs' : 'throws'} ${fpc(fv)} and gains ${fpc(fy)} yards a ${run ? 'carry' : 'target'} vs average`
      : `His ${run ? 'run lanes' : 'spots'} tonight: ${fpc(fv)} on volume (${esc(off)} goes there, ${esc(def)} gives it up), ${fpc(fy)} on yards`;
    const share = (pc, vals) => { let w = 0, u = 0, d = 0; pc.forEach((n, i) => { if (n && vals[i] != null){ w += n; if (vals[i] >= 1.05) u += n; else if (vals[i] <= .95) d += n; } }); return w ? [u / w, d / w] : null; };
    const sh = nt ? share(SC.pc, V.P) : null;
    fitLine = [nt >= 5 ? `<div class="nfm-fitl">${say(nfFitOf(SC.pc, Gv.P), nfFitOf(SC.pc, Gy.P), false)}${sh ? ` · on this map ${Math.round(sh[0] * 100)}% of his targets come from areas above average, ${Math.round(sh[1] * 100)}% below` : ''}</div>` : '',
      nr >= 5 ? `<div class="nfm-fitl">${say(nfFitOf(SC.rc, Gv.R), nfFitOf(SC.rc, Gy.R), true)}</div>` : ''].join('');
  }
  // tapped area: both sides' raw numbers, the likeliest players there tonight, and the picked player's
  const cc = NFMC[off];
  const cellInfo = cc ? (() => { const [k, si] = cc.split(':'), i = +si, run = k === 'r';
    const L = run ? LR : LP, Oc = run ? O.o.r : O.o.p, Dc = run ? F.d.r : G ? F.g[G] : F.d.p, Lc = run ? LR : G ? NFM.lg.g[G] : LP;
    const raw = (C, i_) => { const x = C[i_], N = nfSum(C); return x[0] ? `${Math.round(x[0] / N * 100)}% of ${run ? 'carries' : 'targets'} · ${(x[2] / x[0]).toFixed(1)} yds${run ? '' : ` · ${Math.round(x[1] / x[0] * 100)}% caught`} <small>(${x[0]})</small>` : 'none'; };
    const unit = run ? 'carries' : 'targets', st = run ? 'car' : 'tgt';
    const lik = all.map(r => { const c2 = cnt(r.x), n = run ? c2.rc.reduce((s, y) => s + y, 0) : c2.T.length, own = run ? c2.rc[i] : c2.pc[i], ls = L[i][0] / (run ? LNr : LNp);
      return [r.p.n, mean(r.p, st) * (own + 6 * ls) / (n + 6)]; }).filter(x => x[1] >= .15).sort((a, b) => b[1] - a[1]).slice(0, 3);
    const me = SC ? (run ? ` · <b>${esc(nfLast(sel.p.n))}</b> ${SC.rc[i]} of ${SC.rc.reduce((s, y) => s + y, 0)} carries here` : ` · <b>${esc(nfLast(sel.p.n))}</b> ${SC.pc[i]} of ${SC.T.length} targets here${SC.pc[i] ? `, ${SC.pk[i]} caught` : ''}`) : '';
    return `<div class="nfm-ci"><b>${esc(nfName(k, i))}</b> · league ${raw(L, i)}<br>${esc(off)} ${raw(Oc, i)} · ${esc(def)} allows${G && !run ? ' ' + esc(who) : ''} ${raw(Dc, i)}${lik.length ? `<br>Likeliest tonight: ${lik.map(([n, e]) => `${esc(nfLast(n))} ${e.toFixed(1)}`).join(', ')} ${unit}` : ''}${me} <button type="button" class="nfm-x" data-nfc="${esc(off)}:x:0">close</button></div>`; })()
    : '<div class="nfm-ci muted">Tap an area of the field for its numbers.</div>';
  // areas that stand out on the map shown (passes where the league throws 2.5%+ of its targets, every run lane)
  const th = NFMET === 'vol' ? [1.12, .88] : [1.06, .94];
  const cand = [...V.P.map((v, i) => ({k: 'p', i, v, ok: LP[i][0] / LNp >= .025})), ...V.R.map((v, i) => ({k: 'r', i, v, ok: true}))].filter(x => x.ok && x.v != null);
  const hot = cand.filter(x => x.v >= th[0]).sort((a, b) => b.v - a.v).slice(0, 3), cold = cand.filter(x => x.v <= th[1]).sort((a, b) => a.v - b.v).slice(0, 2);
  const word = {vol: ['goes there a lot', 'rarely goes there', 'gives it up', 'takes it away', 'goes there and', 'gives it up', 'little of it for'],
    yds: ['gains a lot there', 'gains little there', 'gives up yards there', 'is stingy there', 'gains there and', 'gives up yards', 'not much there for'],
    suc: ['succeeds there', 'struggles there', 'gives up success there', 'stops it there', 'succeeds there and', 'allows it', 'little success there for']}[NFMET];
  const say = up => NFMODE === 'off' ? `${esc(off)} ${up ? word[0] : word[1]}` : NFMODE === 'def' ? `${esc(def)} ${up ? word[2] : word[3]}`
    : up ? `${esc(off)} ${word[4]} ${esc(def)} ${word[5]}` : `${word[6]} ${esc(off)} tonight`;
  // area table: the current view's columns (overlay: both sides); shares of passes / runs and yards a play
  const showO = NFMODE !== 'def', showD = NFMODE !== 'off';
  const zc = (C, LC, ix, run) => { const a = nfAgg(C, ix), l = nfAgg(LC, ix), N = nfSum(C), LN = nfSum(LC); if (!a[0]) return '<td class="muted">–</td><td class="muted">–</td>';
    const sh = a[0] / N, lsh = l[0] / LN, y = a[2] / a[0], ly = l[2] / l[0], v = sh / lsh, dy = y - ly, big = a[0] >= 30, tol = run ? .4 : .8;
    return `<td><b class="${v >= 1.15 ? 'up' : v <= .87 ? 'dn' : ''}">${Math.round(sh * 100)}%</b></td><td class="${big && dy >= tol ? 'good' : big && dy <= -tol ? 'bad' : ''}" title="${a[0]} ${run ? 'carries' : 'targets'}${run ? '' : `, ${Math.round(a[1] / a[0] * 100)}% caught`}, ${Math.round(a[3] / a[0] * 100)}% successful (league ${ly.toFixed(1)} yds${run ? '' : `, ${Math.round(l[1] / l[0] * 100)}% caught`})">${y.toFixed(1)}</td>`; };
  const zrow = (lab, ix, run, sw) => { const LC = run ? LR : G ? NFM.lg.g[G] : LP, l = nfAgg(LC, ix);
    return `<tr><td class="l">${sw ? `<i class="nfm-sw" style="background:${sw}"></i>` : ''}${lab}</td><td class="muted">${Math.round(l[0] / nfSum(LC) * 100)}% <small>${(l[2] / l[0]).toFixed(1)}</small></td>${showO ? zc(run ? O.o.r : O.o.p, run ? LR : LP, ix, run) : ''}${showD ? zc(run ? F.d.r : G ? F.g[G] : F.d.p, LC, ix, run) : ''}</tr>`; };
  const ztab = `<div class="tw"><table class="t nfm-zt"><thead><tr><th class="l">Area</th><th title="${G ? `Share of the league’s targets to ${esc(who)}` : 'Share of the league’s targets (runs: carries) here, and its yards a play'}">League${G ? ' <small>' + esc(who) + '</small>' : ''}</th>${showO ? `<th title="${esc(off)}’s share of its targets (runs: carries) here">${esc(off)} goes</th><th title="${esc(off)}’s yards a target (a carry) here">yds</th>` : ''}${showD ? `<th title="Share of ${esc(who)}’ targets (runs: carries) against ${esc(def)} here">${esc(def)} allows</th><th title="Yards a target (a carry) ${esc(def)} allows here">yds</th>` : ''}</tr></thead><tbody>
    ${NF_PZ.map(([lab, ix, sw]) => zrow(lab, ix, false, sw)).join('')}${NF_RZ.map(([lab, ix]) => zrow(lab, ix, true)).join('')}</tbody></table></div>`;
  const ko = O.ko || {}, kd = F.kd || {}, ch = (x, f) => x ? `${f(x[0])} (${nfOrd(x[1])})` : '–', p0 = v => Math.round(v * 100) + '%';
  const chips = `<div class="nfm-chips"><span class="ladc">${esc(off)} ${ch(ko.tpg, f1)} targets a game</span><span class="ladc">aDOT ${ch(ko.adot, f1)}</span><span class="ladc">${ch(ko.deep, p0)} deep 20+</span><span class="ladc">${ch(ko.scr, p0)} behind the line</span><span class="ladc">${ch(ko.ypt, f1)} yds a target</span><span class="ladc">${ch(ko.ypc, f1)} yds a carry</span>
    <span class="ladc">${esc(def)} allows ${ch(kd.ypt, f1)} yds a target</span><span class="ladc">${ch(kd.deep, p0)} deep</span><span class="ladc">${ch(kd.cr, p0)} caught</span><span class="ladc">${ch(kd.ypc, f1)} yds a carry</span></div>`;
  const bar = T => { const z = NF_PZ.map(([, ix]) => T.filter(t => ix.includes(t.cell)).length); return `<span class="nfm-zb" aria-hidden="true">${NF_PZ.map(([, , col], j) => `<i style="flex:${z[j]};background:${col}"></i>`).join('')}</span>`; };
  const defY = nfGrids(O, F, 'yds', '').dP, defYR = nfGrids(O, F, 'yds', '').dR;
  const prow = r => { const x = r.x, c2 = cnt(x), nt = c2.T.length, nr = c2.rc.reduce((s, n) => s + n, 0), on = sel && r === sel;
    const adot = nt ? c2.T.reduce((s, t) => s + t.a, 0) / nt : 0, ck = nt ? c2.T.reduce((s, t) => s + t.k, 0) / nt : 0, ry = x.r ? x.r.reduce((s, y) => s + y[1], 0) : 0;
    // chip: yards tonight's defense allows at his spots (its yards a play by area, pulled toward the league) vs average. Untested.
    const fy = nt >= 10 ? nfFitOf(c2.pc, defY) : nr >= 15 ? nfFitOf(c2.rc, defYR) : null, unit = nt >= 10 ? 'target' : 'carry';
    const fc = fy != null && Math.abs(fy - 1) >= .05 ? `<span class="nfm-fit ${fy > 1 ? 'up' : 'dn'}" title="${esc(def)} allows ${nfPc(fy)} yards a ${unit} vs average in the areas ${esc(r.p.n)} ${nt >= 10 ? 'is targeted' : 'runs'} (its yards by area, pulled toward average). Display only, untested.">${nfPc(fy)} yds</span>` : '';
    const proj = [mean(r.p, 'tgt') >= .5 ? `${f1(mean(r.p, 'tgt'))} tgt` : '', mean(r.p, 'car') >= 1 ? `${f1(mean(r.p, 'car'))} car` : ''].filter(Boolean).join(' · ');
    const ssn = [nt ? `${nt} tgt · aDOT ${adot.toFixed(1)} · ${Math.round(ck * 100)}% caught` : '', nr >= 5 ? `${nr} car · ${(ry / nr).toFixed(1)} ypc` : ''].filter(Boolean).join(' · ');
    return `<button type="button" class="nfm-p" data-nfmp="${esc(off)}:${esc(r.p.id)}" aria-pressed="${!!on}">
      <span class="nfm-pn"><b>${esc(r.p.n)}</b> <small>${esc(r.p.lab || r.p.pos)}</small>${fc}</span>${nt >= 5 ? bar(c2.T) : ''}
      <span class="nfm-ps">tonight ${proj} · season ${ssn} (${x.gp} gm)</span></button>`; };
  const df = ['all', 'made', 'miss'].map(k => `<button type="button" data-nfdf="${k}" aria-pressed="${NFDF === k}">${k === 'all' ? 'All' : k === 'made' ? 'Caught' : 'Not caught'}</button>`).join('');
  const pv2 = ['spots', 'dots'].map(k => `<button type="button" data-nfpv="${k}" aria-pressed="${NFPV === k}">${k === 'spots' ? 'His spots' : 'Every target'}</button>`).join('');
  const selNote = S ? `<div class="nfm-sel"><b>${esc(sel.p.n)}</b> · ${SC.T.length} targets${SC.rc.some(n => n) ? `, ${SC.rc.reduce((s, n) => s + n, 0)} carries` : ''} this season (${S.gp} games) ${SC.T.length ? `<span class="gmsw nfm-df" role="group" aria-label="Show">${pv2}</span>` : ''}${spots || !SC.T.length ? ' <span class="nfm-spk">circle size = how often he gets the ball there · colour = the map at that spot (pale = about average)</span>' : ` <span class="gmsw nfm-df" role="group" aria-label="Targets shown">${df}</span> <i class="nfm-k m"></i>caught <i class="nfm-k x"></i>not caught`}${fitLine}
      <span class="nfm-pz">${NF_PZ.map(([lab, ix, col]) => { const a = ix.reduce((s, i) => s + SC.pc[i], 0), k = ix.reduce((s, i) => s + SC.pk[i], 0); return a ? `<span><i class="nfm-sw" style="background:${col}"></i>${lab} ${a} · ${Math.round(k / a * 100)}% caught</span>` : ''; }).join('')}</span>
      <button type="button" class="nfm-x" data-nfmp="${esc(off)}:0">clear</button></div>` : '';
  const grpNote = G ? `<div class="nfm-arn">${esc(def)} vs ${esc(who)}: ${nfSum(F.g[G])} targets faced, against where the league throws to ${esc(who)}${nfSum(F.g[G]) < 60 ? ' (a small sample: the map stays close to average)' : ''}. Runs stay every carry.</div>` : '';
  const plHead = G ? (same.length ? `${esc(off)} ${esc(who)}` : `${esc(off)} receivers <small>no ${esc(who)} listed</small>`) : `${esc(off)} receivers and backs`;
  return `<div class="nfm-b"><h3>${esc(off)} offense <small>vs ${esc(def)} defense</small></h3>${chips}${miss}${holes}
    <div class="nfm-row"><div class="nfm-mapc">${grpNote}${selNote}${nfSvg(off, V.P, pMarks, cc, !!S, cover, hlP)}${nfRunSvg(off, V.R, rMarks, cc, !!S, cover, hlR)}${cellInfo}
      <ul class="nfm-list">${hot.map(x => `<li><b class="up">${nfPc(x.v)}</b> ${esc(nfName(x.k, x.i))}: ${say(true)}</li>`).join('')}${cold.map(x => `<li><b class="dn">${nfPc(x.v)}</b> ${esc(nfName(x.k, x.i))}: ${say(false)}</li>`).join('')}${!hot.length && !cold.length ? '<li class="muted">No area stands out.</li>' : ''}</ul>
      ${ztab}</div>
      <div class="nfm-pc"><div class="nfm-ph">${plHead} <small>tap one to plot his targets</small></div><div class="nfm-pl">${pl.length ? pl.map(prow).join('') : '<div class="muted small">No listed player has targets or carries this season.</div>'}</div></div></div></div>`;
}
function nfmPanel(g){
  nfmLoad();
  if (!NFM) return `<section class="panel"><div class="phead"><h2>Field map</h2></div><div class="empty">${NFM_ST === 'none' ? 'No field map yet: it appears after the next NFL run.' : 'Loading the field map…'}</div></section>`;
  const seg = `<div class="gmsw nfm-seg" role="group" aria-label="Map view">${[['ov', 'Overlay'], ['off', 'Where they go'], ['def', 'Where it’s allowed']].map(([k, l]) =>
    `<button data-nfmode="${k}" aria-pressed="${NFMODE === k}">${l}</button>`).join('')}</div>`;
  const met = `<div class="gmsw nfm-seg" role="group" aria-label="Measure">${[['vol', 'Volume'], ['yds', 'Yards a play'], ['suc', 'Success']].map(([k, l]) =>
    `<button data-nfmet="${k}" aria-pressed="${NFMET === k}">${l}</button>`).join('')}</div>`;
  const grp = NFMODE === 'def' ? `<div class="nfm-arr"><span class="small muted">Targets to:</span><button type="button" data-nfgrp="" aria-pressed="${!NFGRP}">All</button>${Object.entries(NF_GRP).map(([k, l]) =>
    `<button type="button" data-nfgrp="${k}" aria-pressed="${NFGRP === k}">${l[0].toUpperCase() + l.slice(1)}</button>`).join('')}</div>` : '';
  const key = NFMET === 'vol' ? `<span><i style="background:rgba(255,99,71,.85)"></i>more of the plays than average</span><span><i style="background:rgba(70,150,255,.7)"></i>fewer</span>`
    : `<span><i style="background:rgba(52,210,122,.85)"></i>${NFMET === 'yds' ? 'more yards a play' : 'more successful plays'} than average</span><span><i style="background:rgba(240,72,72,.8)"></i>${NFMET === 'yds' ? 'fewer yards' : 'less success'}</span>`;
  return `<section class="panel"><div class="phead"><h2>${esc(g.away)} at ${esc(g.home)}: field map</h2><span class="muted small">this season · ${NFM.games} games through ${esc(NFM.through)}</span></div>
    <div class="nfm-top">${seg}${met}<div class="nfm-key">${key}<span class="muted">offense going up · faded where few passes go</span></div></div>${grp}
    ${nfBlock(g.away, g.home, g)}${nfBlock(g.home, g.away, g)}
    <p class="note" style="margin:8px 0 0"><b>Passes</b> by where the ball was thrown: depth past the line of scrimmage (air yards) and left / middle / right, from each team’s targets this season; <b>runs</b> by the lane the play-by-play charts (designed runs, no scrambles or kneels). <b>Volume:</b> where they go = the offense’s share of its targets (or carries) in each area over the league’s share there; where it’s allowed = the same for plays against the defense; overlay = the two multiplied (red where this offense likes to go and this defense gives it up). <b>Yards a play</b> and <b>Success</b> (a play that raised the offense’s expected points): the rate in each area over the league’s rate there (green = good for the offense, also on the defense view). Each rate is pulled toward the league’s on small samples (80 targets / 60 carries for shares, 12 plays for rates), so thin areas stay pale; each area’s value sits at its centre and blends smoothly into its neighbours (tap anywhere for the area it falls in). <b>Holes in the defense:</b> each unit is judged on the areas it mostly answers for (pass rush = sacks a dropback; run D inside = middle and guard runs; outside = tackle and end runs; underneath = screens and short middle, linebackers and nickel; corners = short and medium outside; safeties = deep and medium middle): yards a play there over what the league gains on the same plays (25 plays at the league’s rate pulled in), graded Hole (+12%+) / Soft spot (+5%) / Average / Solid / Strength, ranked among the 32 defenses (1st = the biggest hole); ‘goes at it’ = this offense also sends 5%+ more of its plays there than the league. Areas, not players: a unit’s numbers include whoever else was in that area. <b>Targets to</b> (Where it’s allowed): the defense against outside WRs, slot WRs (each receiver’s depth-chart alignment that week), TEs or RBs, against where the league throws to that group. Tap a player to see his spots: his targets this season (any team) grouped by area, each circle sized by how often he gets the ball there and filled with the map’s colour at that spot, plus his carries by lane; ‘Every target’ plots each one at its depth (gold = caught). ‘Likeliest tonight’ = his projected targets (carries) x his share in that area, leaning toward the league’s mix. Not route charts (the ball’s landing area only; those aren’t published during the season). Display only: no number on the page uses these maps and none has been tested as a betting signal; the projections already rate each defense.</p></section>`;
}
document.addEventListener('click', e => {
  const t = e.target.closest('[data-nfmode],[data-nfmet],[data-nfgrp],[data-nfdf],[data-nfpv],[data-nfmp],[data-nfc],[data-nfu]'); if (!t) return;
  if (t.dataset.nfu){ const [tm, k] = t.dataset.nfu.split(':'); NFUN[tm] = NFUN[tm] === k ? null : k; render(); return; }
  if (t.dataset.nfmode){ NFMODE = t.dataset.nfmode; try { localStorage.setItem('nfl-fmode', NFMODE); } catch (x) {} }
  else if (t.dataset.nfmet){ NFMET = t.dataset.nfmet; try { localStorage.setItem('nfl-fmet', NFMET); } catch (x) {} }
  else if (t.dataset.nfgrp != null){ NFGRP = t.dataset.nfgrp; }
  else if (t.dataset.nfdf){ NFDF = t.dataset.nfdf; }
  else if (t.dataset.nfpv){ NFPV = t.dataset.nfpv; try { localStorage.setItem('nfl-fpv', NFPV); } catch (x) {} }
  else if (t.dataset.nfmp){ const j = t.dataset.nfmp.indexOf(':'), tm = t.dataset.nfmp.slice(0, j), id = t.dataset.nfmp.slice(j + 1); NFMP[tm] = id === '0' || NFMP[tm] === id ? null : id; }
  else if (t.dataset.nfc){ const [tm, k, i] = t.dataset.nfc.split(':'); NFMC[tm] = k === 'x' || NFMC[tm] === k + ':' + i ? null : k + ':' + i; }
  render();
});
// Game script tab: the game lines (who's favored, by how much, the total) and, for the high-usage players and the ones
// facing a soft defense, each prop's over ladder (main line + alt lines + Kalshi rungs) at the viewer's books. Chance
// = ours weighted with the market's fair at that line (MKT.altLadder: sharp consensus / the books' alt-line fair / Kalshi
// mid), the same anchoring as everywhere else; "in script" scales it by how much our saved simulated games say that
// line rises or falls when the chosen script happens (e.g. the favorite pulls away by 10+).
const SCR = {};
const SCR_ROOT = (() => { const el = document.querySelector('script[src*="slip.js"]'); return el ? el.src.replace(/slip\.js.*$/, '') : '../'; })();
// ★ on ladder rungs (owner 2026-11-08, like the NBA's): the player's role (markets/role_nfl.json) x stat x the rung's best price
// falls in a range that made money in 'NFL straights by role' on the Profit page (markets/role_odds_nfl.json: 20+ bets, won
// more than priced, positive return, last 30 days). Picked after the fact: display only.
try { fetch(SCR_ROOT + 'markets/role_odds_nfl.json?t=' + Date.now(), {cache: 'no-store'}).then(r => r.ok ? r.json() : null).then(d => { if (d && d.cells){ RODDS = d.cells; render(); } }).catch(() => {});
      fetch(SCR_ROOT + 'markets/role_nfl.json?t=' + Date.now(), {cache: 'no-store'}).then(r => r.ok ? r.json() : null).then(d => { if (d){ ROLES = d; render(); } }).catch(() => {}); } catch (e) {}
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
  const gsw = (D.sgp || D.spot) ? `<div class="seg gsw" role="group" aria-label="Game view">${[['proj', 'Projections'], ['box', 'Box score'], ...(D.sgp ? [['script', 'Game script']] : []), ...(D.spot && g.id in D.spot ? [['sum', 'Summary']] : []), ['map', 'Field map'], ...(D.sgp ? [['sgp', 'Same-game parlays']] : [])].map(([k, l]) => `<button data-gtab="${k}" aria-pressed="${(state.gtab || 'proj') === k}">${l}</button>`).join('')}</div>` : '';
  if (state.gtab === 'box') return gsw + boxPanel(g);
  if (D.sgp && state.gtab === 'sgp') return gsw + sgpPanel(g);
  if (D.sgp && state.gtab === 'script') return gsw + scriptPanel(g);
  if (state.gtab === 'sum' && D.spot && g.id in D.spot) return gsw + summaryPanel(g);
  if (state.gtab === 'map') return gsw + nfmPanel(g);
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
// ---------------- injury watch (owner 2026-11-16: Emmett Johnson / Kenneth Walker, KC @ ATL wk 10) ----------------
// Questionable regulars: project.py simulated the game again with him sitting (D.sit); his teammates' prices at your books
// with our chance if he plays (this projection) and if he sits. Ruled-out regulars (already out of the projection): the
// teammates who gained (next man up, p.nm). Edge = chance x payout - 1 at the best of Hard Rock / Novig / Kalshi.
const IW_BOOKS = ['Hard Rock', 'Novig', 'Kalshi'], IW_STAT = {td: 'TD', rush_yds: 'rush yds', rec_yds: 'rec yds', rec: 'catches'};
const iwDec = o => o > 0 ? 1 + o / 100 : 1 + 100 / -o;
function iwBest(o){ let b = null; for (const k of IW_BOOKS){ const v = (o || {})[k]; if (v && v[0] != null && (!b || iwDec(v[0]) > iwDec(b.o))) b = {bk: k, o: v[0]}; } return b; }
// the bets one teammate can be judged on: TD, and the main line (+ any cheaper number one of your books still posts) of the
// stats he gains; plays / sits = chance at that line in each case
function iwBets(g, name, stats, dPlay, dSit, tdPlay, tdSit){
  const out = [];
  for (const st of stats){
    const e = window.MKT && MKT.propEntry ? MKT.propEntry(g.id, name, st) : null; if (!e) continue;
    if (st === 'td'){ const b = iwBest(e.o); if (b) out.push({st, l: .5, b, pp: tdPlay, ps: tdSit}); continue; }
    const lines = [[e.l, e.o], ...Object.entries(e.a || {}).map(([l, a]) => [+l, (a || {}).o]).filter(([l]) => l < e.l)];
    const rows = lines.map(([l, o]) => ({st, l, b: iwBest(o), pp: dPlay && dPlay[st] ? probs(dPlay[st], l).over : null, ps: dSit && dSit[st] ? probs(dSit[st], l).over : null}))
      .filter(r => r.b && r.l != null);
    const main = rows.find(r => r.l === e.l), cheap = rows.filter(r => r.l !== e.l && r.ps != null && r.ps * iwDec(r.b.o) - 1 >= .1).sort((a, b) => (b.ps * iwDec(b.b.o)) - (a.ps * iwDec(a.b.o))).slice(0, 2);
    if (main) out.push(main); out.push(...cheap);
  }
  return out;
}
function iwRow(name, r, sitLab){
  const ev = r.ps != null ? r.ps * iwDec(r.b.o) - 1 : null, ln = r.st === 'td' ? 'anytime TD' : `${Math.ceil(r.l)}+ ${IW_STAT[r.st]}`;
  return `<div class="iwr"><span class="iwb">${esc(ln)}</span><span class="iwp">${esc(r.b.bk)} ${amS(r.b.o)}</span>`
    + `${r.pp != null ? `<span class="iwc" title="Our chance with him playing (this projection)">plays ${pct(r.pp)}</span>` : ''}`
    + `${r.ps != null ? `<span class="iwc s" title="Our chance ${esc(sitLab)}">${esc(sitLab)} ${pct(r.ps)}</span>` : ''}`
    + `${ev != null ? `<span class="iwe${ev >= .1 ? ' up' : ev < 0 ? ' dn' : ''}" title="Expected return at that price if our ${esc(sitLab)} chance is right">${ev >= 0 ? '+' : '−'}${Math.round(Math.abs(ev) * 100)}% ${sitLab === 'sits' ? 'if he sits' : ''}</span>` : ''}`
    + `${r.pp != null && r.ps != null && r.ps - r.pp >= .03 ? `<span class="iwi" title="Break-even: by our ‘plays’ and ‘sits’ numbers this price pays off if he sits at least this often (the book’s margin in). 0% = our number beats the price even if he plays.">worth it if he sits ≥ ${pct(Math.min(1, Math.max(0, (1 / iwDec(r.b.o) - r.pp) / (r.ps - r.pp))))}</span>` : ''}</div>`;
}
function injuryWatch(g){
  const S = (D.sit || {})[g.id] || [], outs = {};
  for (const p of D.players) if (p.g === g.id && p.nm && (p.nm.w || []).length) for (const w of p.nm.w) (outs[`${p.t}|${w}`] = outs[`${p.t}|${w}`] || []).push(p);
  if (!S.length && !Object.keys(outs).length) return '';
  const q = S.map(x => {
    const ben = x.ben.slice(0, 3).map(b => {
      const pk = byKey[`${g.id}|${x.team}|${b.n}`], stats = ['td', ...(b.sit.car - (b.base.car || 0) >= 1.5 ? ['rush_yds'] : []), ...(b.sit.tgt - (b.base.tgt || 0) >= .8 ? ['rec', 'rec_yds'] : [])];
      const bets = iwBets(g, b.n, stats, pk && pk.d, b.d, b.base.td, b.sit.td);
      const vol = [b.sit.car - (b.base.car || 0) >= 1.5 ? `car ${f0(b.base.car || 0)} → ${f0(b.sit.car)}` : '', b.sit.tgt - (b.base.tgt || 0) >= .8 ? `tgt ${f1(b.base.tgt || 0)} → ${f1(b.sit.tgt)}` : '', `TD ${pct(b.base.td || 0)} → ${pct(b.sit.td)}`].filter(Boolean).join(' · ');
      return `<div class="iwp1"><div class="iwn"><b>${esc(b.n)}</b> <span class="small muted">${esc(b.pos)} · ${esc(vol)}</span></div>${bets.map(r => iwRow(b.n, r, 'sits')).join('') || '<div class="small muted">no prices at your books yet</div>'}</div>`; }).join('');
    return `<div class="iwg"><div class="iwh"><b>${esc(x.n)}</b> <span class="small muted">${esc(x.team)} ${esc(x.pos)}</span> <span class="iwst">${esc(x.st)}</span> <span class="small muted">· if he sits:</span></div>${ben}</div>`; }).join('');
  const o = Object.entries(outs).map(([k, ps]) => {
    const [team, who] = k.split('|');
    const ben = ps.sort((a, b) => ((b.nm.c || 0) / 2 + (b.nm.t || 0)) - ((a.nm.c || 0) / 2 + (a.nm.t || 0))).slice(0, 3).map(p => {
      const stats = ['td', ...((p.nm.c || 0) >= 2 ? ['rush_yds'] : []), ...((p.nm.t || 0) >= 1 ? ['rec', 'rec_yds'] : [])];
      const td = p.s.anytime_td ? p.s.anytime_td[MEAN] : null;
      const bets = iwBets(g, p.n, stats, null, p.d, null, td).map(r => ({...r, pp: null}));
      return `<div class="iwp1"><div class="iwn"><b>${esc(p.n)}</b> <span class="small muted">${esc(p.pos)} · ${[(p.nm.c || 0) >= 2 ? `+${f1(p.nm.c)} car` : '', (p.nm.t || 0) >= 1 ? `+${f1(p.nm.t)} tgt` : ''].filter(Boolean).join(' · ')}</span></div>${bets.map(r => iwRow(p.n, r, 'projected')).join('') || '<div class="small muted">no prices at your books yet</div>'}</div>`; }).join('');
    return `<div class="iwg"><div class="iwh"><b>${esc(who)}</b> <span class="small muted">${esc(team)}</span> <span class="iwst out">out</span> <span class="small muted">· in the projection already; who gains:</span></div>${ben}</div>`; }).join('');
  return `<section class="panel iw" aria-label="Injury watch"><div class="phead"><h2>Injury watch <span class="exp3">experimental</span></h2><span class="small muted">backups’ prices at your books vs our chance</span></div>
    ${q}${o}
    <details class="how"><summary>How to read this</summary><p class="note"><b>Questionable</b> (ESPN): we simulate the game again with him sitting; next man up hands his share to the teammates with room to grow. <b>plays</b> = our chance with him in (this projection), <b>sits</b> = with him out. The books price a mix of the two until the inactives post about 90 minutes before kickoff, so a backup’s price between them is the bet on him sitting. <b>Out</b>: already out of the projection; the chance shown includes his absence. A cheaper number one of your books still posts (a line that hasn’t caught up) is listed under the main line when its edge is 10%+. Tracked on the Profit page as ‘Injury watch’ (experimental); not a tested rule.</p></details></section>`;
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
  ${injuryWatch(g)}
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
// TD luck (research/td_layout_study.py + td_luck_check.py, 2026-10-30): players who scored more TDs than their carries and
// targets predict (season to date, 2+ games) come in under our chance next game, and those with fewer come in over it --
// 7,264 player-games 2025-26, better log-loss in all three out-of-sample parts, slope ~ -1.0 per TD a game on the odds.
const TD_LUCK = 1.0;
function tdLuck(p){
  const u = D.rz && D.rz.usage && D.rz.usage.season && D.rz.usage.season[p.k], a = p.s.anytime_td && p.s.anytime_td[MEAN];
  if (!u || u.games < 2 || u.xtd == null || a == null) return null;
  const gap = Math.max(-.6, Math.min(.6, (u.td - u.xtd) / u.games)), xpg = u.xtd / u.games;
  const z = Math.log(a / (1 - a)) - TD_LUCK * gap, adj = 1 / (1 + Math.exp(-z));
  return {tot: u.td - u.xtd, gap, adj, tag: xpg >= .3 && gap <= -.2 ? 'due' : xpg >= .3 && gap >= .2 ? 'hot' : null};
}
const TDS = {name: r => r.p.n, rz_car: r => r.u && r.u.rz_car, gl_car: r => r.u && r.u.gl_car, rz_tgt: r => r.u && r.u.rz_tgt, ez_tgt: r => r.u && r.u.ez_tgt,
  opp: r => r.u && r.u.rz_opp_share, proe: r => r.T && r.T.proe, pass: r => r.T && r.T.pass_rate, run: r => r.T && r.T.pass_rate != null ? 1 - r.T.pass_rate : null,
  dproe: r => r.O && r.O.def_proe, dtd: r => r.R && r.R.td_rate, xtd: r => r.u && r.u.xtd, td: r => r.u && r.u.td, luck: r => r.L && r.L.tot, any: r => r.p.s.anytime_td[MEAN], adj: r => r.L ? r.L.adj : r.p.s.anytime_td[MEAN]};
// phones: one card per player, the chance and the price first (owner 2026-11-01: the table left them ~1,000 px off screen)
const TD_PHONE = window.matchMedia ? matchMedia('(max-width:640px)') : null;
if (TD_PHONE && TD_PHONE.addEventListener) TD_PHONE.addEventListener('change', () => { if (state.view === 'games' && state.group === 'td') render(); });
const TD_SORT = [['any', 'Anytime TD'], ['adj', 'Luck-adj.'], ['xtd', 'Expected TDs'], ['td', 'Actual TDs'], ['luck', 'TD luck'], ['rz_car', 'RZ rush att'], ['gl_car', 'GL rush att'], ['rz_tgt', 'RZ targets'], ['ez_tgt', 'EZ targets'], ['opp', 'RZ opp share'], ['name', 'Player']];
function tdBoard(g){
  const sc = state.rzscope, TM = (D.rz.teams || {})[sc] || {};
  const pool = state.tdall ? D.players : PL(g);
  const rows = pool.filter(p => p.s.anytime_td && p.s.anytime_td[MEAN] >= .03 && p.pos !== 'QB')
    .map(p => ({p, u: rzUsage(p), T: TM[p.t], O: TM[p.o], R: rzDefRow(p.o), L: tdLuck(p)}));
  const key = TDS[state.tds] ? state.tds : 'any', dir = state.tdsd === 'asc' ? 1 : -1;
  rows.sort((a, b) => { const x = TDS[key](a), y = TDS[key](b);
    if (key === 'name') return dir * String(x).localeCompare(String(y));
    return (x == null) - (y == null) || dir * ((x ?? 0) - (y ?? 0)) || b.p.s.anytime_td[MEAN] - a.p.s.anytime_td[MEAN]; });
  const mx = k => Math.max(1, ...rows.map(r => (r.u && r.u[k]) || 0));
  const M = {rz_car: mx('rz_car'), gl_car: mx('gl_car'), rz_tgt: mx('rz_tgt'), ez_tgt: mx('ez_tgt')};
  const mxx = Math.max(.5, ...rows.map(r => (r.u && r.u.xtd) || 0));
  const th = (k, lab, tip, cls = '') => `<th class="${k === 'name' ? 'l ' : ''}${cls}"><button class="tds${key === k ? ' on' : ''}" data-tds="${k}"${tip ? ` title="${esc(tip)}"` : ''}>${lab}<span class="ar">${key === k ? (dir > 0 ? '▲' : '▼') : '↕'}</span></button></th>`;
  const dots = n => n == null ? '<span class="muted">–</span>' : `<span class="tdc"><b>${n}</b><span class="dots" aria-hidden="true">${'●'.repeat(Math.min(n, 8))}${n > 8 ? '+' : ''}</span></span>`;
  const luckCell = L => !L ? '<span class="muted">–</span>'
    : `<span class="tdr"><b class="${L.tot <= -.5 ? 'good' : L.tot >= .5 ? 'bad' : ''}">${L.tot >= 0 ? '+' : '−'}${Math.abs(L.tot).toFixed(1)}</b>${L.tag ? `<span class="lk2 ${L.tag}">${L.tag === 'due' ? 'due' : 'ran hot'}</span>` : ''}</span>`;
  const adjCell = (L, a) => { if (!L) return `<b>${pct(a)}</b>`; const d = L.adj - a;
    return `<span class="tdr"><b>${pct(L.adj)}</b><span class="tdsub ${d >= .02 ? 'good' : d <= -.02 ? 'bad' : ''}">${Math.abs(d) < .005 ? 'same' : (d > 0 ? '▲ ' : '▼ ') + Math.round(Math.abs(d) * 100) + ' pts'}</span></span>`; };
  const body = rows.map(({p, u, T, O, R, L}) => {
    const bo = !!(window.MKT && MKT.hasProps && MKT.hasProps(p.g)), a = p.s.anytime_td[MEAN];
    const gl = u ? `${u.games} game${u.games === 1 ? '' : 's'}` : 'no games yet';
    return `<tr class="${state.open === p.k ? 'opened' : ''}"><td class="l tdname"><div class="tdp">${teamChip(p.t)}<div class="tdpn"><button class="plink${state.open === p.k ? ' open' : ''}" data-price="${esc(p.k)}" data-pstat="tds" title="Slide a line for ${esc(p.n)}">${esc(p.n)}</button><span class="tdsub">${esc(p.lab || p.pos)} · vs ${esc(p.o)} · ${gl}</span><div class="tdtags">${mktTags(p)}${roleChip(p)}${nmChip(p)}</div>${mxNFL(p) || chips(p)}</div></div></td>`
      + heat(u ? u.rz_car : null, M.rz_car) + heat(u ? u.gl_car : null, M.gl_car) + heat(u ? u.rz_tgt : null, M.rz_tgt) + heat(u ? u.ez_tgt : null, M.ez_tgt)
      + `<td>${ring(u && u.rz_opp_share, 'Share of his team’s red-zone carries + targets in the games he played')}</td>`
      + `<td class="gs">${sbar(T && T.proe)}</td><td>${pbar2(T && T.pass_rate)}</td>`
      + `<td class="gs">${sbar(O && O.def_proe)}</td>`
      + `<td>${R ? `<span class="tdr"><span class="pill ${R.td_rate >= .6 ? 'good' : R.td_rate < .45 ? 'bad' : ''}">${pct(R.td_rate)}</span><span class="tdsub">${R.td_trips_n ?? '?'} of ${R.trips_n ?? '?'} trips</span></span>` : '<span class="muted">–</span>'}</td>`
      + `<td class="gs">${u ? `<span class="sb"><span class="xv">${u.xtd.toFixed(2)}</span><i class="lf"><b style="width:${Math.min(100, u.xtd / mxx * 100)}%"></b></i></span>` : '<span class="muted">–</span>'}</td><td>${dots(u ? u.td : null)}</td><td>${luckCell(L)}</td>`
      + `<td class="gs"><span class="tdany">${ring(a, 'Our chance he scores a rushing or receiving TD in this game')}<span class="tdsub">fair ${american(a)}</span></span></td><td>${adjCell(L, a)}</td>`
      + `<td class="l">${bo ? MKT.prop(p.g, p.n, 'td') : '<span class="muted">–</span>'}</td></tr>`
      + (state.open === p.k ? `<tr class="xrow"><td colspan="17">${sliderPanel(p)}</td></tr>` : ''); }).join('');
  const lab = D.rz.labels[sc];
  const best = [...rows].sort((a, b) => (b.L ? b.L.adj : b.p.s.anytime_td[MEAN]) - (a.L ? a.L.adj : a.p.s.anytime_td[MEAN])).slice(0, state.tdall ? 8 : 4);
  const due = rows.filter(r => r.L && r.L.tag === 'due').sort((a, b) => b.L.adj - a.L.adj).slice(0, state.tdall ? 6 : 4);
  const chip = r => `<span class="tdread"><b>${esc(r.p.n)}</b> <span class="muted">${esc(r.p.t)}</span> ${pct(r.L ? r.L.adj : r.p.s.anytime_td[MEAN])}${r.L && r.L.tag === 'due' ? ' <span class="lk2 due">due</span>' : r.L && r.L.tag === 'hot' ? ' <span class="lk2 hot">ran hot</span>' : ''}</span>`;
  const reads = rows.length ? `<div class="tdreads"><div><span class="eyebrow">Most likely to score</span>${best.map(chip).join('')}</div>${due.length ? `<div><span class="eyebrow">Due · under their expected TDs</span>${due.map(chip).join('')}</div>` : ''}
    <details class="tdhow"><summary class="small">How to read this</summary><p class="small muted">Our chance already counts his red-zone and goal-line work, his team and the defense -- in a 2025-26 test (7,264 player-games) none of those columns added anything once our chance was known. The one that did is <b>TD luck</b>: players well under their expected TDs scored more often than our chance said, and players who ran hot less. <b>Luck-adj.</b> is our chance with that built in. Not a bet rule yet: against Kalshi’s price the ‘due’ group was close to break-even after the fee.</p></details></div>` : '';
  const seg = `<div class="seg" role="group" aria-label="Players"><button data-tdall="0" aria-pressed="${!state.tdall}">This game</button><button data-tdall="1" aria-pressed="${!!state.tdall}">Whole slate</button></div>`;
  if (TD_PHONE && TD_PHONE.matches){
    const tile = (lab, v, st = '') => `<div class="tdkt${st}"><span>${lab}</span><b>${v}</b></div>`;
    const heatT = (lab, v, m) => v == null ? tile(lab, '–') : `<div class="tdkt hot${v ? '' : ' z'}" style="${v ? `--a:${(.12 + .5 * Math.min(1, v / Math.max(m, 1))).toFixed(2)}` : ''}"><span>${lab}</span><b>${v}</b></div>`;
    const sgn = v => v == null ? '–' : `<span class="${v >= 0 ? 'good' : 'bad'}">${v >= 0 ? '+' : '−'}${(Math.abs(v) * 100).toFixed(1)}%</span>`;
    const cards = rows.map(({p, u, T, O, R, L}) => {
      const bo = !!(window.MKT && MKT.hasProps && MKT.hasProps(p.g)), a = p.s.anytime_td[MEAN], d = L ? L.adj - a : 0, on = state.open === p.k;
      const tags = `${mktTags(p)}${roleChip(p)}${nmChip(p)}`, mx = mxNFL(p) || chips(p);
      return `<article class="tdk${on ? ' opened' : ''}"><div class="tdkh">${teamChip(p.t)}<div class="tdpn"><button class="plink${on ? ' open' : ''}" data-price="${esc(p.k)}" data-pstat="tds" title="Slide a line for ${esc(p.n)}">${esc(p.n)}</button><span class="tdsub">${esc(p.lab || p.pos)} · vs ${esc(p.o)}${u ? ` · ${u.games}g` : ''}</span></div>`
        + `<div class="tdkc"><b>${pct(a)}</b><span class="tdsub">fair ${american(a)}</span></div></div>`
        + `<div class="tdkm">${L ? `<span class="tdkl"><span class="tdsub">Luck-adj.</span> <b>${pct(L.adj)}</b>${Math.abs(d) >= .005 ? ` <span class="tdsub ${d >= .02 ? 'good' : d <= -.02 ? 'bad' : ''}">${d > 0 ? '▲' : '▼'} ${Math.round(Math.abs(d) * 100)}</span>` : ''}${L.tag ? ` <span class="lk2 ${L.tag}">${L.tag === 'due' ? 'due' : 'ran hot'}</span>` : ''}</span>` : '<span></span>'}<span class="tdko">${bo ? MKT.prop(p.g, p.n, 'td') : '<span class="muted">–</span>'}</span></div>`
        + (tags || mx ? `<div class="tdkx"><div class="tdtags">${tags}</div>${mx}</div>` : '')
        + `<div class="tdks">${heatT('RZ rush', u ? u.rz_car : null, M.rz_car)}${heatT('Goal line', u ? u.gl_car : null, M.gl_car)}${heatT('RZ tgt', u ? u.rz_tgt : null, M.rz_tgt)}${heatT('End zone', u ? u.ez_tgt : null, M.ez_tgt)}`
        + `${tile('RZ share', u && u.rz_opp_share != null ? pct(u.rz_opp_share) : '–')}${tile('Exp. TDs', u ? u.xtd.toFixed(1) : '–')}${tile('TDs', u ? u.td : '–')}${tile('TD luck', L ? `<span class="${L.tot <= -.5 ? 'good' : L.tot >= .5 ? 'bad' : ''}">${L.tot >= 0 ? '+' : '−'}${Math.abs(L.tot).toFixed(1)}</span>` : '–')}</div>`
        + `<details class="tdkd"><summary class="small">Team and defense</summary><div class="tdks">${tile('Team PROE', sgn(T && T.proe))}${tile('Pass %', T && T.pass_rate != null ? pct(T.pass_rate) : '–')}${tile('Opp PROE', sgn(O && O.def_proe))}${tile('Opp RZ TD', R ? pct(R.td_rate) : '–')}</div></details>`
        + (on ? `<div class="tdkp">${sliderPanel(p)}</div>` : '') + `</article>`; }).join('');
    return reads + `<div class="tdbar">${seg}<label class="tdsort"><span class="small muted">Sort</span><select id="tds-sort" aria-label="Sort players">${TD_SORT.map(([k, l]) => `<option value="${k}"${k === key ? ' selected' : ''}>${esc(l)}</option>`).join('')}</select><button class="tdsdir" data-tds="${key}" aria-label="Flip the order">${dir > 0 ? '▲' : '▼'}</button></label></div>
      <p class="tdlg small muted">RZ = inside the 20, goal line = inside the 5, ${esc(lab)}. Tap a name to slide a line.</p>
      <div class="tdks0">${cards || '<div class="small muted">No players with a meaningful TD chance.</div>'}</div>`;
  }
  return reads + `<div class="tdbar">${seg}
    <span class="small muted">Red zone = inside the 20, goal line = inside the 5 · tap a header to sort</span></div>
    <div class="tw tdw"><table class="t tdt"><thead>
      <tr class="gh"><th class="tdname"></th><th colspan="5">His red-zone usage</th><th colspan="2" class="gs">His team inside the 20</th><th colspan="2" class="gs">Opponent defense</th><th colspan="3" class="gs">Touchdowns, ${esc(lab)}</th><th colspan="3" class="gs">This game</th></tr>
      <tr>${th('name', 'Player', '', 'tdname')}${th('rz_car', 'RZ rush att', 'Carries inside the 20')}${th('gl_car', 'GL rush att', 'Carries inside the 5')}${th('rz_tgt', 'RZ targets', 'Targets inside the 20')}${th('ez_tgt', 'EZ targets', 'Targets thrown into the end zone')}${th('opp', 'RZ opp share', 'His share of his team’s red-zone carries + targets in the games he played (red-zone snap counts aren’t in our data)')}`
      + `${th('proe', 'PROE', 'Pass rate over expected inside the 20: how much more (or less) his team passes than the league in the same down, distance, field spot and game state', 'gs')}${th('pass', 'Pass %', 'Share of his team’s plays inside the 20 that were dropbacks (the rest are runs)')}`
      + `${th('dproe', 'Opp RZ PROE allowed', 'How much more offenses pass inside the 20 against this defense than expected', 'gs')}${th('dtd', 'Opp RZ TD rate', 'Red-zone trips that ended in a touchdown against this defense')}`
      + `${th('xtd', 'Expected TDs', 'The league’s TD rate on each of his carries and targets, by distance to the end zone, added up', 'gs')}${th('td', 'Actual TDs', 'Rushing + receiving touchdowns')}${th('luck', 'TD luck', 'Actual TDs minus expected TDs this season. Players far under (due) have scored more than our chance next game; far over (ran hot) less -- 2025-26 test, every out-of-sample part')}`
      + `${th('any', 'Anytime TD', 'Our simulated chance he scores a rushing or receiving TD in this game', 'gs')}${th('adj', 'Luck-adj.', 'Our chance adjusted for TD luck this season (the one table read that beat our model in every test)')}<th class="l">Best odds</th></tr></thead>
    <tbody>${body || '<tr><td colspan="17" class="l muted small">No players with a meaningful TD chance.</td></tr>'}</tbody></table></div>`;
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
// next man up (boxscore/pipeline.NEXT_MAN, owner 2026-11-16): the volume he gains tonight because a regular teammate is out. An
// absent player's share goes mostly to his own position and to the players there with room to grow (WR out: WRs with under 3% of
// targets got 1.74x their old proportional projection); fit 2023-24, better on 2025-26 in every half. Already in his numbers.
const lastName = n => { const w = String(n || '').replace(/\s+(Jr\.?|Sr\.?|II|III|IV|V)$/i, '').trim().split(/\s+/); return w[w.length - 1] || n; };
const nmChip = p => { const m = p.nm; if (!m) return '';
  const parts = [m.t >= 1 ? `+${m.t.toFixed(1)} tgt` : '', m.c >= 2 ? `+${m.c.toFixed(1)} car` : ''].filter(Boolean);
  if (!parts.length) return '';
  const w = m.w || [], who = w.length ? `${lastName(w[0])}${w.length > 1 ? ' +' + (w.length - 1) : ''} out` : 'teammate out';
  return `<span class="lean g2" title="Next man up: ${esc(w.join(', ') || 'a regular')} out tonight. He gets ${parts.join(' and ')} more than with everyone in: an absent player’s share goes mostly to his position and to the players there with room to grow (tested on 2025-26). Already in his projection.">▲ ${parts.join(' · ')} · ${esc(who)}</span>`; };
const mktTags = p => `${window.MKT && MKT.flag ? MKT.flag(p.g, p.n) + (MKT.lcFlag ? MKT.lcFlag(p.g, p.n) : '') + (MKT.offFlag ? MKT.offFlag(p.g, p.n) : '') + (MKT.moveFlag ? MKT.moveFlag(p.g, p.n) : '') + whyNFL(p) : ''}${window.MKT && state.gm[p.g] === 'blind' ? MKT.patChips(p.g, p.id, p.n) : ''}`;
const nameCell = (p, stat, extra='') => `<td class="l name"><button class="plink${state.open === p.k ? ' open' : ''}" data-price="${esc(p.k)}" data-pstat="${stat}" title="Slide a line for ${esc(p.n)}"><span class="caret">${state.open === p.k ? '▾' : '▸'}</span>${esc(p.n)}</button>${agChip(p, stat)}${mktTags(p)}${extra}${roleChip(p)}${nmChip(p)}${mxNFL(p) || chips(p)}</td>`;
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

/* ---------------- Matchups heatmap (owner 2026-11-23: 'I was wondering if the matchup map for NFL should get a heatmap' -> 'yes build it') ---------------- */
// The NBA Matchups heatmap for the NFL. Each cell = what tonight's defense allowed per game this season to the player's group (QB, RB,
// slot / outside WR by his depth-chart alignment, TE) in that stat, over the league average: the number behind the matchup line under
// each name (mxNFL), here for every eligible player and stat (not only 15%+), and in a second grid for every defense. This season's
// raw per-game numbers like the line (no smoothing); defenses with under 3 games are blank. Display only: in our 2025-26 test, extra
// position / archetype matchups added nothing beyond the projection's own defense terms.
const NFH_COLS = [['pass_yds', 'Pass yds'], ['rush_yds', 'Rush yds'], ['rec', 'Catches'], ['rec_yds', 'Rec yds'], ['td', 'TDs']];
// per position: column -> [DvP column, his projected average needed for the cell (td: anytime-TD chance)]
const NFH_SPEC = {QB: {pass_yds: ['Pass yds', 150], rush_yds: ['Rush yds', 15], td: ['Pass TD', .15]},
  RB: {rush_yds: ['Rush yds', 15], rec: ['Targets', 1.5], rec_yds: ['Rec yds', 10], td: ['Rush TD', .15]},
  WR: {rec: ['Rec', 2], rec_yds: ['Rec yds', 15], td: ['Rec TD', .15]}, TE: {rec: ['Rec', 2], rec_yds: ['Rec yds', 15], td: ['Rec TD', .15]}};
const NFH_GRP = {QB: 'QBs', RB: 'RBs', WR: 'WRs', SLOT: 'slot WRs', OUT: 'outside WRs', TE: 'TEs'};
const nfhKey = p => { const al = (p.lab || '').split(' · ')[1] || ''; return p.pos === 'WR' ? (al === 'Slot' ? 'SLOT' : al === 'X' || al === 'Z' ? 'OUT' : 'WR') : p.pos; };
const NFH_LG = {};
function nfhVal(key, team, col){                               // [allowed a game, league average, games] or null
  const T = D.dvp && D.dvp[key]; if (!T) return null;
  const i = T.cols.indexOf(col); if (i < 0) return null;
  const lk = key + '|' + col;
  if (!(lk in NFH_LG)){ const xs = T.rows.filter(r => r.season_stats && (r.season_games || 0) >= 3).map(r => r.season_stats[i]).filter(x => x != null);
    NFH_LG[lk] = xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null; }
  const row = T.rows.find(r => r.team === team), lg = NFH_LG[lk];
  if (!row || !row.season_stats || (row.season_games || 0) < 3 || !(lg > 0) || row.season_stats[i] == null) return null;
  return [row.season_stats[i], lg, row.season_games];
}
// TDs are a handful a season per defense: pulled toward the league with 6 pseudo-games (yards / catches stay raw, like the matchup line)
const nfhV = (x, col) => /TD$/.test(col) ? (x[0] * x[2] + x[1] * 6) / (x[2] + 6) / x[1] : x[0] / x[1];
const nfhShade = v => { const d = v - 1, a = Math.min(1, Math.abs(d) / .3), w = Math.round(8 + a * 62);
  return Math.abs(d) < .03 ? '' : `background:color-mix(in srgb,var(${d > 0 ? '--good' : '--bad'}) ${w}%,transparent)${a >= .75 ? ';color:#fff' : ''}`; };
const nfhPct = v => { const n = Math.round((v - 1) * 100); return `${n > 0 ? '+' : n < 0 ? '−' : ''}${Math.abs(n)}<i>%</i>`; };
function nfhTeam(g, team, opp){
  const PO = {QB: 0, RB: 1, WR: 2, TE: 3}, use = p => p.pos === 'QB' ? (p.s.att || [0])[MEAN] : (p.s.tgt || [0])[MEAN] + (p.s.car || [0])[MEAN];
  const rows = [];
  for (const p of D.players.filter(x => x.g === g.id && x.t === team && NFH_SPEC[x.pos])){
    const key = nfhKey(p), c = {};
    for (const [st, [col, floor]] of Object.entries(NFH_SPEC[p.pos])){
      const mine = st === 'td' ? (p.s.anytime_td || [0])[MEAN] : (p.s[st] || [0])[MEAN]; if (!(mine >= floor)) continue;
      const k = key === 'SLOT' || key === 'OUT' ? key : p.pos, x = nfhVal(k, opp, col); if (!x) continue;
      c[st] = {v: nfhV(x, col), tip: `${p.n}: ${opp} allowed ${f1(x[0])} ${col.toLowerCase()} a game to ${NFH_GRP[k]} (${x[2]} games) vs ${f1(x[1])} league average${/TD$/.test(col) ? ' (TDs pulled toward average with 6 games: they are few)' : ''}`};
    }
    if (Object.keys(c).length) rows.push({p, c});
  }
  rows.sort((a, b) => PO[a.p.pos] - PO[b.p.pos] || use(b.p) - use(a.p));
  if (!rows.length) return `<div class="nfh-w"><div class="nfh-h">${esc(team)}</div><p class="note">No defense data yet.</p></div>`;
  const best = {}; for (const [st] of NFH_COLS) best[st] = Math.max(...rows.map(r => r.c[st] ? r.c[st].v : 0));
  return `<div class="nfh-w"><div class="nfh-h">${esc(team)} <small>vs ${esc(opp)} defense</small></div><div class="tw"><table class="nfh-t"><thead><tr><th class="l">Player</th>${NFH_COLS.map(([, l]) => `<th>${l}</th>`).join('')}</tr></thead><tbody>${rows.map(({p, c}) =>
    `<tr><td class="l"><b>${esc(lastName(p.n))}</b><small>${esc(p.lab || p.pos)}</small></td>${NFH_COLS.map(([st]) => { const x = c[st]; if (!x) return '<td class="na">–</td>';
      const bst = x.v >= 1.05 && x.v === best[st];
      return `<td class="${bst ? 'best' : ''}" style="${nfhShade(x.v)}" title="${esc(x.tip)}${bst ? ' · his team’s best for this stat' : ''}">${nfhPct(x.v)}</td>`; }).join('')}</tr>`).join('')}</tbody></table></div></div>`;
}
const NFH_DEF = [['QB', 'Pass yds', 'QB', 'Pass'], ['QB', 'Rush yds', 'QB', 'Rush'], ['RB', 'Rush yds', 'RB', 'Rush'], ['RB', 'Rec yds', 'RB', 'Rec yds'],
  ['SLOT', 'Rec yds', 'Slot WR', 'Rec yds'], ['OUT', 'Rec yds', 'Outside WR', 'Rec yds'], ['TE', 'Rec yds', 'TE', 'Rec yds'], ['TE', 'Rec TD', 'TE', 'TDs']];
function nfhDefRow(team, faces){
  return `<tr><td class="l"><b>${esc(team)}</b>${faces ? `<small>vs ${esc(faces)}</small>` : ''}</td>${NFH_DEF.map(([k, col]) => { const x = nfhVal(k, team, col); if (!x) return '<td class="na">–</td>';
    const v = nfhV(x, col); return `<td style="${nfhShade(v)}" title="${esc(`${team} allowed ${f1(x[0])} ${col.toLowerCase()} a game to ${NFH_GRP[k]} (${x[2]} games) vs ${f1(x[1])} league average${/TD$/.test(col) ? ' (pulled toward average with 6 games)' : ''}`)}">${nfhPct(v)}</td>`; }).join('')}</tr>`;
}
function renderMatchups(){
  if (!D.dvp) return '<section class="panel"><div class="empty">No defense-vs-position data on this page.</div></section>';
  const games = [...D.games].sort((a, b) => String(a.sort || '').localeCompare(String(b.sort || '')));
  const slate = new Set(games.flatMap(g => [g.home, g.away]));
  const all = [...new Set(Object.values(D.dvp).flatMap(T => T.rows.map(r => r.team)))].sort();
  const key = `<div class="nfh-k"><span class="nfh-g" aria-hidden="true"></span><span class="small muted">tough −30% … league average … +30% soft · <span class="nfh-kb">ringed</span> = his team’s best for that stat</span></div>`;
  const head = `<thead><tr><th class="l">Defense</th>${NFH_DEF.map(([, , gl, sl]) => `<th>${gl}<small>${sl}</small></th>`).join('')}</tr></thead>`;
  const rest = all.filter(t => !slate.has(t));
  return `<section class="panel"><div class="phead"><h2>Matchup heatmap, every player</h2><span class="small muted">week ${esc(D.week)} · ${games.length} game${games.length === 1 ? '' : 's'} on this page</span></div>${key}
    <details class="how"><summary>How to read this</summary><p class="note">Each cell is what tonight’s defense allowed per game this season to his group (QBs, RBs, slot or outside WRs by his depth-chart alignment, TEs) in that stat, against the league average: the same number as the matchup line under his name, shown here for every player and stat. Catches for RBs use targets allowed. TDs: passing TDs allowed for QBs, rushing TDs for RBs, receiving TDs for WRs and TEs (a handful a season, so they are pulled toward the league average with 6 games). Players need some volume in the stat (about 2+ projected catches, 15+ yards, 150+ passing yards, a 15%+ anytime-TD chance); defenses with under 3 games are blank. This season’s raw numbers (about 11 games each, so expect noise). Display only: the projections already use their own defense terms, and in our 2025-26 test extra position matchups added nothing beyond them.</p></details>
    <div class="nfh-m">${games.map(g => `<div class="nfh-gm"><div class="nfh-gh">${esc(g.away)} @ ${esc(g.home)} <small>${esc(g.kickoff || '')}</small></div><div class="nfh-tt">${nfhTeam(g, g.away, g.home)}${nfhTeam(g, g.home, g.away)}</div></div>`).join('')}</div></section>
    <section class="panel"><div class="phead"><h2>Every defense by position group</h2><span class="small muted">allowed a game vs league average · this season</span></div>
    <div class="tw"><table class="nfh-t nfh-d">${head}<tbody>${games.flatMap(g => [nfhDefRow(g.home, g.away), nfhDefRow(g.away, g.home)]).join('')}</tbody></table></div>
    ${rest.length ? `<details class="nfh-o"><summary>Other defenses (${rest.length})</summary><div class="tw"><table class="nfh-t nfh-d">${head}<tbody>${rest.map(t => nfhDefRow(t, null)).join('')}</tbody></table></div></details>` : ''}
    <p class="note" style="margin:8px 0 0">Defenses in this page’s games first (each listed with the offense it faces), the rest below. Green = gives up more than average to that group (a softer matchup), red = less. Slot and outside splits use each receiver’s latest depth-chart alignment; TE TDs are receiving TDs allowed to tight ends.</p></section>`;
}

/* ---------------- shell ---------------- */
function render(){
  for (const b of document.querySelectorAll('.tabs button')) b.setAttribute('aria-selected', b.dataset.view === state.view);
  const main = document.getElementById('main');
  try {
  main.innerHTML = state.view === 'board' ? renderBoard() : state.view === 'props' ? renderProps()
    : state.view === 'books' ? (window.MKT && MKT.oddsView ? MKT.oddsView(D.games.map(g => ({id: g.id, label: `${g.away} @ ${g.home}`, away: g.away, home: g.home}))) : '<div class="empty">Loading prices…</div>')
    : state.view === 'parlays' ? renderParlays() : state.view === 'ladders' ? renderLadders() : state.view === 'fair' ? renderFair() : state.view === 'agree' ? renderAgree() : state.view === 'dvp' ? renderDvp() : state.view === 'mu' ? renderMatchups() : renderGames();
  } catch (e) { console.error(e); main.innerHTML = `<div class="empty">This view hit an error (${esc(String(e && e.message || e))}). The other tabs still work; it is fixed on the next page build.</div>`; }
  // field map player lists (a strip on phones, a scrolling column on desktop): keep the picked player in view after a redraw
  for (const pl of document.querySelectorAll('.nfm-pl, .nfm-arr')){ const b = pl.querySelector('[aria-pressed="true"]'); if (!b) continue;
    if (pl.scrollWidth > pl.clientWidth){ const x = b.offsetLeft - pl.offsetLeft; if (x + b.offsetWidth > pl.clientWidth) pl.scrollLeft = x - 8; }
    else if (pl.scrollHeight > pl.clientHeight){ const y = b.offsetTop - pl.offsetTop; if (y + b.offsetHeight > pl.clientHeight) pl.scrollTop = y - 8; } }
  save();
  const cur = document.querySelector('.rail .game-btn[aria-current="true"]');
  if (cur){ const r = cur.parentElement, rr = r.getBoundingClientRect(), cr = cur.getBoundingClientRect(); if (r.scrollWidth > r.clientWidth && (cr.left < rr.left || cr.right > rr.right)) r.scrollLeft += cr.left - rr.left - 12; if (r.scrollHeight > r.clientHeight && (cr.top < rr.top || cr.bottom > rr.bottom - 64)) r.scrollTop += cr.top - rr.top - (r.clientHeight - cr.height) / 2; }
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
  else if (id === 'tds-sort'){ state.tds = e.target.value; state.tdsd = state.tds === 'name' ? 'asc' : 'desc'; render(); }
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
