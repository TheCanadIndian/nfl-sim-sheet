const D = JSON.parse(document.getElementById('data').textContent);
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const pct = p => (p*100).toFixed(1) + '%';
const odds = o => (o > 0 ? '+' : '') + o;
if (location.hash === '#books') location.replace('markets.html#books');          // Book odds moved to the Markets page
const state = {view: ['#board', '#posdef', '#goalies', '#shots', '#plays'].includes(location.hash) ? location.hash.slice(1) : 'games', sort: 'team', dir: 1};
// Highlights: tonight's top 5 by goal chance (backtest 2024-26: scored 38%, average skater 15%)
// and the best option in each game (38%). The model's probability already includes every factor
// that tested as useful, so the ranking is the selection rule; reasons explain it.
const ALLP = D.games.flatMap(g => [g.away, g.home].flatMap(s => s.players.map(p => ({p, g: g.id}))));
const TOP5 = new Set(ALLP.slice().sort((a, b) => b.p.p - a.p.p).slice(0, 5).map(x => x.p));
const BEST = new Set(D.games.map(g => [...g.away.players, ...g.home.players].sort((a, b) => b.p - a.p)[0]).filter(Boolean));
const rowCls = p => TOP5.has(p) ? 'pick' : BEST.has(p) ? 'best' : '';
function pickMark(p){
  if (TOP5.has(p)) {
    const tip = `<b>Top 5 tonight</b><br>${(p.p*100).toFixed(1)}% to score${p.why && p.why.length ? '<br>' + p.why.map(esc).join(' · ') : ''}<br><span style="opacity:.8">In backtests the night's top 5 scored 38% of the time (average skater 15%).</span>`;
    return `<span class="star" tabindex="0" data-tip="${esc(tip)}" aria-label="Top 5 tonight">★</span>`;
  }
  return '';
}
const whyLine = p => TOP5.has(p) && p.why && p.why.length ? `<span class="why">${p.why.map(esc).join(' · ')}</span>` : '';
const PD = Object.fromEntries((D.posdef || []).map(r => [r.team, r]));
const PNAME = {C: 'C', L: 'LW', R: 'RW', D: 'D'};
// degree shades: 1 slight, 2 moderate, 3 strong (green = good for the shooter, red = bad)
const hshade = v => v >= 1.20 ? 'g3' : v >= 1.12 ? 'g2' : v >= 1.05 ? 'g1' : v <= 1/1.2 ? 'r3' : v <= 1/1.12 ? 'r2' : v <= 1/1.05 ? 'r1' : '';
const rel = v => { const n = Math.round((v - 1) * 100); return (n >= 0 ? '+' : '−') + Math.abs(n) + '%'; };
// Matchup tag: opponent's even-strength xG allowed to the player's position, 10%+ off average.
// Triple green: green head-to-head (3+ meetings, +0.25 pts/game over his overall rate), opposing goalie GSAA -1.5 or worse
// (this season once he has 3 games, else last season), and the opponent allowing 5%+ more expected goals to his position.
// Backtest 2024-26: all three together scored 12-20% above the model's chance (~340 player-games a season; not proven);
// one or two greens: nothing consistent. Experimental and tracked (Parlays results).
function gGreen(o){ const g = o && o.gstats; if (!g) return false; const x = g.now && g.now.gp >= 3 ? g.now : (g.last || g.now); return !!x && x.gsaa != null && x.gsaa <= -1.5; }
function tripleGreen(p, oppSide){
  const h = p.h2h, hot = !!(h && h.gp >= 3 && h.pts / h.gp >= h.ppg_all + .25);
  const r = PD[oppSide.team], k = PNAME[p.pos] ? p.pos : 'C', dv = !!(r && r[k] && r[k].ev >= 1.05);
  return hot && dv && gGreen(oppSide);
}
const TG3_TIP = 'Triple green: strong head-to-head vs this opponent, a struggling opposing goalie (GSAA -1.5 or worse) and a defense that allows more to his position. In 2024-26 backtests players with all three scored 12-20% more often than our chance (small sample: experimental, tracked on the Parlays results tab).';
function mtag(pos, opp){
  const r = PD[opp], k = PNAME[pos] ? pos : 'C';
  if (!r) return '';
  const v = r[k].ev;
  if (v >= 1.10) return `<span class="mu soft ${hshade(v)}" title="${esc(opp)} allows ${rel(v)} expected goals to ${PNAME[k]} at even strength vs league average">soft vs ${PNAME[k]} ${rel(v)}</span>`;
  if (v <= 0.90) return `<span class="mu tough ${hshade(v)}" title="${esc(opp)} allows ${rel(v)} expected goals to ${PNAME[k]} at even strength vs league average">tough vs ${PNAME[k]} ${rel(v)}</span>`;
  return '';
}
// Head-to-head vs tonight's opponent: games, goals-assists-points; tooltip lists the last 5 meetings.
function h2hCell(h, opp){
  if (!h || !h.gp) return '<span class="muted small">—</span>';
  const ppg = h.pts / h.gp, hot = h.gp >= 3 && ppg >= h.ppg_all + 0.25;
  const tip = `<b>vs ${esc(opp)} since 2022–23</b><br>${h.gp} GP · ${h.g} G · ${h.a} A · ${h.pts} P (${ppg.toFixed(2)} per game; ${h.ppg_all.toFixed(2)} vs everyone)<br>` +
    h.last.map(([d, g, a, po]) => `${esc(d)}${po ? ' (playoffs)' : ''}: ${g} G, ${a} A`).join('<br>');
  return `<span class="h2h" tabindex="0" data-tip="${esc(tip)}"><b class="${hot ? 'hot' : ''}">${h.g}-${h.a}-${h.pts}</b> <span class="small muted">(${h.gp})</span></span>`;
}
// Opposing goalie: save % and GSAA (goals saved above an average save %), this season and last.
const sv3 = v => v == null ? '–' : v.toFixed(3).replace(/^0/, '');
const sgn = v => v == null ? '–' : (v >= 0 ? '+' : '−') + Math.abs(v).toFixed(1);
function gcell(x, lab){ return x ? `${lab}: <b>${sv3(x.sv)}</b> SV% · GSAA <b class="${x.gsaa >= 8 ? 'r3' : x.gsaa >= 4 ? 'r2' : x.gsaa >= 1.5 ? 'r1' : x.gsaa <= -8 ? 'g3' : x.gsaa <= -4 ? 'g2' : x.gsaa <= -1.5 ? 'g1' : ''}">${sgn(x.gsaa)}</b> <span class="muted">(${x.gp} GP)</span>` : `${lab}: <span class="muted">no games</span>`; }
function goalieLine(opp){
  const g = opp.gstats; if (!g) return '';
  return `<div class="small muted pdline">${esc(opp.goalie)}: ${gcell(g.now, 'this season')} · ${gcell(g.last, 'last season')}</div>`;
}
function pdline(opp){
  const r = PD[opp];
  if (!r) return '';
  const cell = k => { const v = r[k].ev; return `${PNAME[k]} <b class="${hshade(v)}">${rel(v)}</b>`; };
  return `<div class="small muted pdline">${esc(opp)} defense vs position (xG allowed, even strength): ${['C','L','R','D'].map(cell).join(' · ')}</div>`;
}
document.getElementById('sub').textContent = `${D.games.length} games · ${D.date} · built ${D.generated}`;
const bar = (p, max) => `<div class="pbar" role="img" aria-label="${pct(p)}"><i style="width:${Math.min(p/max,1)*100}%"></i></div>`;
// build-your-own parlay (slip.js): the goal model treats players' goals as independent, so goal legs multiply
function nhlSlip(p, team){
  if (!window.SLIP) return '';
  const g = D.games.find(x => x.home.team === team || x.away.team === team); if (!g) return '';
  const lam = -Math.log(1 - Math.min(p.p, .999)), p2 = 1 - Math.exp(-lam) * (1 + lam);
  const leg = (n, prob) => ({id: `nhl|${g.id}|${p.id}|goal${n}`, sport: 'nhl', g: g.id, glab: `${g.away.team} @ ${g.home.team}`, lab: n === 1 ? `${p.name} to score` : `${p.name} 2+ goals`, p: prob, side: 'over', stat: 'goals', line: n - .5});
  return SLIP.btn(leg(1, p.p), 'ATG') + (p2 >= .02 ? SLIP.btn(leg(2, p2), '2+') : '');
}
// usage rank (display only): rank in the game by live expected goals x usage multiplier; the model's own rank for comparison
const MRANK = {};
D.games.forEach(g => [...g.away.players, ...g.home.players].sort((a, b) => b.p - a.p).forEach((p, i) => { MRANK[g.id + '|' + p.id] = i + 1; }));
const UTIP = "Rank in this game after a usage adjustment: shot share while on the ice, shift length and count, 6-on-5 and late-game ice time (from this season's shift charts). Backtest 2025-26: the usage top 3 caught 1.045 scorers per game vs 1.032 for the plain ranking. P(goal) and fair odds are unchanged.";
function urCell(p, gid){
  if (p.ur == null) return '<span class="muted">–</span>';
  const m = MRANK[gid + '|' + p.id], d = m ? m - p.ur : 0;
  return `<span class="${p.ur <= 3 ? 'urtop' : ''}" title="Usage-adjusted rank ${p.ur} (model rank ${m}); usage multiplier ${p.um}">#${p.ur}</span>${d ? ` <span class="small ${d > 0 ? 'urup' : 'urdn'}">${d > 0 ? '▲' : '▼'}${Math.abs(d)}</span>` : ''}`;
}
// lineup changes vs the team's previous game (lineup_changes in project.py)
const LN = l => l ? l.replace('F', 'L') : '';
// games since his last goal (owner 2026-10-27, research/player_trends_nhl.py; experimental, tracked on the Profit page)
const dryChip = p => p.dry != null && p.dry >= 3 && p.dry <= 9 ? `<span class="lchg dry" title="No goal in his last ${p.dry} games. Tracked (experimental): the market has overpriced players who just scored and underpriced droughts (3+ games dry: +21% on 173 legs at your books this season, last season’s prices agree). Not proven yet.">no goal in ${p.dry}</span>` : '';
function chgChips(p){
  if (!p.chg) return '';
  const cut = p.cut ? ` · −${Math.round(p.cut * 100)}%` : '', cutT = p.cut ? ` Our goal chance is cut ${Math.round(p.cut * 100)}% for this (early 2026-27 evidence: demoted players scored below our chance; not yet proven).` : '';
  return p.chg.map(([k, b]) => k === 'in' ? (p.new ? '' : `<span class="lchg in" title="Didn’t play last game">back in</span>`)
    : k === 'up' ? `<span class="lchg up" title="Moved up: ${LN(b)} last game, ${LN(p.line)} tonight">▲ ${LN(b)} → ${LN(p.line)}</span>`
    : k === 'down' ? `<span class="lchg dn" title="Moved down: ${LN(b)} last game, ${LN(p.line)} tonight.${p.pos !== 'D' ? cutT : ''}">▼ ${LN(b)} → ${LN(p.line)}${p.pos !== 'D' ? cut : ''}</span>`
    : !b ? `<span class="lchg up" title="Not on a power-play unit last game">▲ new ${esc(p.pp)}</span>`
    : !p.pp ? `<span class="lchg dn" title="Was on ${esc(b)} last game.${cutT}">▼ off ${esc(b)}${cut}</span>`
    : `<span class="lchg ${p.pp < b ? 'up' : 'dn'}" title="${esc(b)} last game, ${esc(p.pp)} tonight.${p.pp < b ? '' : cutT}">${p.pp < b ? '▲' : '▼'} ${esc(b)} → ${esc(p.pp)}${p.pp < b ? '' : cut}</span>`).join('');
}
function chgLine(s){
  const c = s.lchg; if (!c) return '';
  const n = (s.players || []).filter(p => p.chg && p.chg.some(([k]) => k !== 'in' || !p.new)).length + (s.players || []).filter(p => p.new).length;
  const out = c.out.length ? `<b>Out:</b> ${c.out.map(o => `${esc(o.name)}${o.line ? ` <span class="muted">(${LN(o.line)})</span>` : ''}`).join(', ')}` : 'nobody out';
  return `<div class="small lchgl"><span class="muted">Since last game (${esc(c.prev)}):</span> ${n ? `${n} change${n === 1 ? '' : 's'} marked` : 'same lineup'}${c.known ? '' : ' <span class="muted">(no saved lines for that game; only who’s in and out)</span>'} · ${out}</div>`;
}
function side(s, opp, gid){
  const max = .5, mk = window.MKT && MKT.has(), bo = !!(window.MKT && MKT.hasProps && MKT.hasProps(gid));
  return `<div><div class="sidehead"><span class="code">${esc(s.team)}</span><span class="small muted">${s.exp_goals} expected goals · vs ${esc(opp.goalie)}${opp.goalie_status ? ` (${esc(opp.goalie_status)})` : ''}</span></div>
    ${s.lineup ? `<div class="small muted src">${esc(s.lineup)}</div>` : ''}${chgLine(s)}${goalieLine(opp)}${pdline(opp.team)}
    <div class="tw"><table class="t pt"><thead><tr><th class="l">Player</th><th style="min-width:96px" title="Chance to score, and the fair (no-vig) odds">Goal · fair</th><th title="${UTIP}">Usage</th><th title="Chance to score the game's first goal, and its fair odds">1st goal · fair</th><th>TOI</th><th class="l" title="Expected shots on goal, and the chance of 2+ and 3+ (shots model: calibrated within ~1 pt on 2024-26)">Shots</th><th class="l" title="Goals-assists-points vs this opponent since 2022-23">H2H G-A-P</th>${bo ? '<th class="l pcd" title="Best anytime-goal price at your sportsbooks (choose them under the game bets). Green = our chance beats it by 5%+">Best odds</th>' : ''}${mk ? '<th class="l mkc" title="Kalshi / Polymarket anytime-goal price, our edge, and dollars to buy YES / NO within 3¢">Market</th>' : ''}</tr></thead><tbody>
    ${gamePlayers(s.players, gid).map(p => `<tr class="${rowCls(p)}"><td class="l name">${pickMark(p)}${esc(p.name)}${nhlSlip(p, s.team)}${window.MKT && MKT.flag ? MKT.flag(gid, p.name) + (MKT.lcFlag ? MKT.lcFlag(gid, p.name) : '') : ''}${p.pp ? `<span class="pp">${p.pp}</span>` : ''}${chgChips(p)}${dryChip(p)}<span class="lp">${esc(p.line ? p.line.replace('F', 'L') + ' · ' + p.pos : p.pos)}</span><span class="mmeta">${esc(p.line ? p.line.replace('F', 'L') + ' · ' + p.pos : p.pos)} · fair ${odds(p.fair)}${p.ur ? ` · usage #${p.ur}${(() => { const m = MRANK[gid + '|' + p.id], d = m ? m - p.ur : 0; return d ? ` ${d > 0 ? '▲' : '▼'}${Math.abs(d)}` : ''; })()}` : ''}${p.pf != null ? ` · 1st goal ${pct(p.pf)}` : ''}</span>${bo ? `<span class="pcm">${MKT.prop(gid, p.name, 'goal')}</span>` : ''}${p.new ? '<span class="newp">no NHL history</span>' : ''}${tripleGreen(p, opp) ? `<span class="tg3" title="${esc(TG3_TIP)}">Triple green</span>` : ''}${mtag(p.pos, opp.team)}${whyLine(p)}</td><td class="big kp"><div class="kpv">${pct(p.p)}<small class="dk">${odds(p.fair)}</small></div><div class="dk">${bar(p.p, max)}</div>${p.sog != null ? `<small class="kps">${p.sog.toFixed(1)} shots<br>3+ ${pct(p.so[2])}</small>` : ''}</td><td>${urCell(p, gid)}</td><td style="white-space:nowrap">${p.pf != null ? `${pct(p.pf)}<span class="fo">${p.fair_first != null ? odds(p.fair_first) : ''}</span>` : '–'}</td><td class="muted">${p.toi}</td><td class="l">${p.sog != null ? `<b>${p.sog.toFixed(1)}</b> <small class="muted">2+ ${pct(p.so[1])} · 3+ ${pct(p.so[2])}</small>` : '<span class="muted">–</span>'}</td><td class="l">${h2hCell(p.h2h, opp.team)}</td>${bo ? `<td class="l pcd">${MKT.prop(gid, p.name, 'goal')}</td>` : ''}${mk ? `<td class="l mkc">${MKT.cell(gid, p.id, p.name, 'goal')}</td>` : ''}</tr>`).join('')}
    ${state.bval && !gamePlayers(s.players, gid).length ? '<tr><td colspan="9" class="l name muted small">No value plays on this team at your books.</td></tr>' : ''}
    </tbody></table></div></div>`;
}
function picksKey(){
  const top = ALLP.filter(x => TOP5.has(x.p)).sort((a, b) => b.p.p - a.p.p).map(x => `<b>${esc(x.p.name)}</b> ${(x.p.p*100).toFixed(0)}%`);
  return `<div class="picks-key"><span><span class="star">★</span>Top 5 tonight: ${top.join(' · ')}</span><span><span style="display:inline-block;width:3px;height:12px;background:var(--accent);vertical-align:-1px;margin-right:6px"></span>best option in each game</span></div>`;
}
// Moneyline / puck line / total from the game simulation (gamesim.py).
function mlBlock(g){
  const m = g.ml; if (!m) return '';
  const o = p => p == null ? '–' : odds(Math.abs(p - .5) < 1e-9 ? -100 : (p >= .5 ? -Math.round(100 * p / (1 - p)) : Math.round(100 * (1 - p) / p)));
  const gl = `${g.away.team} @ ${g.home.team}`;
  // each option is one tappable chip that adds it to the parlay slip (game legs multiply with goal legs)
  const BK = (...a) => window.MKT && MKT.bk ? MKT.bk(g.id, ...a) : '';
  const bkOf = {ml_away: ['ml', 'away'], ml_home: ['ml', 'home'], pl_away_m: ['spread', 'away', 1.5], pl_home_m: ['spread', 'home', -1.5],
                pl_away_p: ['spread', 'away', -1.5], pl_home_p: ['spread', 'home', 1.5]};      // spread line = home team's handicap
  const chip = (key, lab, p, full) => {
    const bk = key.startsWith('over') ? BK('total', 'over', key.slice(4), p) : key.startsWith('under') ? BK('total', 'under', key.slice(5), p)
             : bkOf[key] ? BK(bkOf[key][0], bkOf[key][1], bkOf[key][2], p) : '';
    const L = {id: `nhl|${g.id}|${key}`, sport: 'nhl', g: g.id, glab: gl, lab: `${full || lab} (${gl})`, p, side: 'over', stat: key};
    const on = window.SLIP && SLIP.has(L.id);
    return `<button type="button" class="bet${on ? ' on' : ''}"${window.SLIP ? ` data-slip="${esc(JSON.stringify(L))}" title="${on ? 'Remove from' : 'Add to'} my parlay"` : ' disabled'}><span>${esc(lab)}</span><b>${pct(p)}</b><small>${o(p)}</small><i>${on ? '✓' : '+'}</i>${bk}</button>`;
  };
  const row = (lab, chips) => `<div class="betrow"><span class="eyebrow">${lab}</span><div class="bets">${chips.join('')}</div></div>`;
  const A = g.away.team, H = g.home.team;
  const rows = [row('Moneyline', [chip('ml_away', A, m.away), chip('ml_home', H, m.home)]),
                row('Puck line', [chip('pl_away_m', A + ' −1.5', m.away_pl), chip('pl_home_m', H + ' −1.5', m.home_pl)]
                  .concat(m.away_pl_plus != null ? [chip('pl_away_p', A + ' +1.5', m.away_pl_plus), chip('pl_home_p', H + ' +1.5', m.home_pl_plus)] : []))];
  for (const k of ['5.5', '6.5']) if (m.totals[k] != null)
    rows.push(row('Total ' + k, [chip('over' + k, 'Over', m.totals[k], 'Over ' + k), chip('under' + k, 'Under', 1 - m.totals[k], 'Under ' + k)]));
  return `<div class="betgrid">${rows.join('')}<div class="small muted">Projected total ${m.total.toFixed(1)} goals · overtime ${pct(m.ot)}</div>${window.MKT && MKT.bkNote ? MKT.bkNote(g.id) : ''}</div>`;
}
// Anytime-goal parlays priced at the viewer's books: legs only from our top 3 per game (usage-adjusted rank, the one slice
// that has held up vs Kalshi), one leg per game, games not started. Ranked by payout vs our fair odds.
function atgParlays(){
  if (!(window.MKT && MKT.propBest)) return '';
  const now = Date.now(), dec = o => o > 0 ? 1 + o / 100 : 1 + 100 / -o, am = o => (o > 0 ? '+' : '') + o;
  const legs = [];
  for (const g of D.games){
    if (g.start_utc && new Date(g.start_utc) <= now) continue;
    for (const side of [g.away, g.home]) for (const p of side.players){
      if (!(p.ur && p.ur <= 3) || p.p < .30) continue;              // our top 3, and 30%+ (shorter droughts, same calibration)
      const b = MKT.propBest(g.id, p.name, 'goal'); if (!b) continue;
      const m = MKT.propMarket ? MKT.propMarket(g.id, p.name, 'goal') : null;
      // 50/50 ours and the exchanges (Kalshi 2026: 3,822 graded legs, top-3 legs +10%); 20/80 with Pinnacle when pulled (2025-26 closing props)
      const q = MKT.blend ? MKT.blend(g.id, p.name, 'goal', p.p) : m != null ? .5 * p.p + .5 * m : p.p;
      const ev = q * dec(b.o) - 1;
      if (ev <= 0) continue;                                         // every leg needs its own edge
      legs.push({g, p, team: side.team, b, q, m, ev});
    }
  }
  if (legs.length < 2) return '';
  const byGame = {}; legs.forEach(L => (byGame[L.g.id] = byGame[L.g.id] || []).push(L));
  const gids = Object.keys(byGame), combos = [];
  const walk = (i, pick, k) => {
    if (pick.length === k){ const p = pick.reduce((a, L) => a * L.q, 1), d = pick.reduce((a, L) => a * dec(L.b.o), 1); combos.push({legs: pick, p, d, ev: p * d - 1}); return; }
    for (let j = i; j < gids.length; j++) for (const L of byGame[gids[j]]) walk(j + 1, [...pick, L], k);
  };
  walk(0, [], 2); if (gids.length >= 3) walk(0, [], 3);
  if (!combos.length) return '';
  const top = k => combos.filter(c => c.legs.length === k).sort((a, b) => b.ev - a.ev).slice(0, 4);
  const legSlip = L => ({id: `nhl|${L.g.id}|${L.p.id}|goal1`, sport: 'nhl', g: L.g.id, glab: `${L.g.away.team} @ ${L.g.home.team}`, lab: `${L.p.name} to score`, p: L.p.p, side: 'over', stat: 'goals', line: .5});
  const row = c => `<tr><td class="l">${c.legs.map(L => `<b>${esc(L.p.name)}</b> <span class="small muted" title="Our chance ${pct(L.p.p)}${L.m != null ? ', exchanges ' + pct(L.m) + ', blended ' + pct(L.q) : ' (no exchange price to blend)'}">${esc(L.team)} · ${pct(L.q)} · ${L.b.u ? `<a href="${esc(L.b.u)}" target="_blank" rel="noopener">${am(L.b.o)} ${esc(L.b.name)} ↗</a>` : `${am(L.b.o)} ${esc(L.b.name)}`}${L.b.mine ? '' : ' (not at your books)'}</span>`).join('<br>')}</td>
    <td class="big">${pct(c.p)}</td><td class="pay">${c.d.toFixed(1)}x</td><td class="edge ${c.ev >= .1 ? 'pos-d' : c.ev < 0 ? 'neg-d' : ''}" style="font-weight:700">${c.ev >= 0 ? '+' : ''}${(c.ev * 100).toFixed(0)}%</td>
    <td class="l add">${window.SLIP ? SLIP.allBtn(c.legs.map(legSlip), '+ add parlay') : ''}</td></tr>`;
  // round robin: the 3 best-edge legs from different games, bet as all three 2-leg parlays (backtest 2024-26: longest
  // dry spell 7-9 nights vs 20-23 for a single nightly 2-leg, same long-run return)
  const rr = [], seen = new Set();
  for (const L of legs.slice().sort((a, b) => b.ev - a.ev)) if (!seen.has(L.g.id) && rr.length < 3){ seen.add(L.g.id); rr.push(L); }
  const rrPairs = rr.length === 3 ? [[rr[0], rr[1]], [rr[0], rr[2]], [rr[1], rr[2]]].map(pk => {
    const p = pk[0].q * pk[1].q, d = dec(pk[0].b.o) * dec(pk[1].b.o); return {legs: pk, p, d, ev: p * d - 1}; }) : [];
  const rrBlock = rrPairs.length ? `<div class="rr"><div class="eyebrow">Round robin · fewer dry spells</div>
    <p class="small muted" style="margin:4px 0 8px">Bet all three 2-leg parlays below (same stake each). At least one hits whenever 2 of the 3 score: ${pct(rr[0].q * rr[1].q + rr[0].q * rr[2].q + rr[1].q * rr[2].q - 2 * rr[0].q * rr[1].q * rr[2].q)} by our blended chances; in the 2024-26 backtest the longest stretch with no winner was 7-9 nights, vs 20-23 for one 2-leg a night.</p>
    <div class="tw"><table class="t pairs atgp"><thead><tr><th class="l">2-leg</th><th>Chance</th><th>Pays</th><th>Edge</th><th class="l">My parlay</th></tr></thead><tbody>${rrPairs.map(row).join('')}</tbody></table></div></div>` : '';
  if (window.MKT && MKT.reg){                                                    // freeze mode: record for the Results page
    const lg = L => ({gid: String(L.g.id), start: L.g.start_utc, kind: 'goal', side: 'o', line: .5, name: MKT.norm(L.p.name), lab: `${L.p.name} to score`, p: +L.q.toFixed(4), price: L.b.o, book: L.b.k});
    top(2).forEach(c => MKT.reg('Anytime-goal parlays', 'Best 2-leg', c.legs.map(lg), c.p, c.d));
    top(3).forEach(c => MKT.reg('Anytime-goal parlays', 'Best 3-leg', c.legs.map(lg), c.p, c.d));
    rrPairs.forEach(c => MKT.reg('Anytime-goal parlays', 'Round robin', c.legs.map(lg), c.p, c.d));
  }
  const mine = MKT.myBooks();
  return `<section class="panel"><div class="phead"><h2>Anytime-goal parlays at your books</h2><span class="small muted">${mine.length ? 'Priced at ' + esc(mine.join(', ')) : 'Priced at the best book (choose yours under any game’s bets)'}</span></div>
    <details class="how"${innerWidth > 640 ? ' open' : ''}><summary>How these are picked</summary><p class="note">Legs: our top 3 scorers in each game (usage-adjusted rank; on Kalshi those roughly broke even while everyone else lost) who are 30%+ to score. Each leg's chance is half our number, half the exchanges' price: on Kalshi this season (3,822 graded legs) that blend was more accurate than our number alone, and our top-3 picks returned +10% (vs +6% leaning 75% on our model). Each leg must beat its own price. One player per game, so legs are independent; parlays multiply the books' margin, so the edge column matters more than the payout. Edge = our chance × the payout − 1. Backtest (2024-26, the likeliest such parlays each night): 2-leg hit 15.6-16.2% vs 15-17% predicted, 3-leg 6.7-7.2% vs 6-7%, so a 2-leg needs to pay about 6.3x and a 3-leg about 14x just to break even; the nightly top 2-leg once went 24 nights without hitting. Treat these as small stakes.</p></details>
    ${rrBlock}
    ${[2, 3].map(k => top(k).length ? `<div class="eyebrow" style="margin-top:10px">Best ${k}-leg parlays</div><div class="tw"><table class="t pairs atgp"><thead><tr><th class="l">Legs</th><th>Chance</th><th>Pays</th><th>Edge</th><th class="l">My parlay</th></tr></thead><tbody>${top(k).map(row).join('')}</tbody></table></div>` : '').join('')}</section>`;
}
// Likeliest-scorer parlays (experimental, owner 2026-10-31: 'atgs parlays with a high hit rate and payout';
// research/nhl_atg_parlay_build.py, 4,510 graded Yes prices at Novig / Kalshi, 130 games Oct 12-30): only our top 3 by
// usage priced 25%+ (+300 or shorter) were fair or better at your books (+2% on 388, hit 36.6%); longshot legs lost 10%,
// legs claiming 10%+ edge lost 25% (stale prices), and droughts / our gap / PP1 added nothing beyond the price. So: those
// legs, likeliest first (not biggest edge), one per game, a leg in at most 2; 2-legs paying 6-10x and 3-legs 15-30x.
// Replay: 2-leg 11 of 39 vs 6.2 expected, 3-leg 4 of 30 vs 2.0 -- above expectation, noise-level; the legs price ~fair.
function likelyCard(){
  if (!(window.MKT && MKT.propBest)) return '';
  const now = Date.now(), dec = o => o > 0 ? 1 + o / 100 : 1 + 100 / -o, am = o => (o > 0 ? '+' : '') + o, legs = [];
  for (const g of D.games){
    if (g.start_utc && new Date(g.start_utc) <= now) continue;
    for (const side of [g.away, g.home]) for (const p of side.players){
      if (!(p.ur && p.ur <= 3)) continue;
      const b = MKT.propBest(g.id, p.name, 'goal'); if (!b || !b.mine || 1 / dec(b.o) < .25) continue;
      const q = MKT.blend ? MKT.blend(g.id, p.name, 'goal', p.p) : p.p;
      if (q * dec(b.o) - 1 > .10) continue;                      // a 10%+ claimed edge is usually a stale price
      legs.push({g, p, team: side.team, b, q});
    }
  }
  const best = {}; legs.forEach(L => { if (!best[L.g.id] || L.q > best[L.g.id].q) best[L.g.id] = L; });
  const pool = Object.values(best);
  const pick = (k, lo, hi, n) => {
    const C = [], walk = (i, s) => {
      if (s.length === k){ const d = s.reduce((a, L) => a * dec(L.b.o), 1); if (d >= lo && d < hi) C.push({legs: s, p: s.reduce((a, L) => a * L.q, 1), d}); return; }
      for (let j = i; j < pool.length; j++) walk(j + 1, [...s, pool[j]]);
    };
    walk(0, []);
    const used = {}, out = [];
    for (const c of C.sort((a, b) => b.p - a.p)){
      if (out.length >= n) break;
      if (c.legs.some(L => (used[L.p.id] || 0) >= 2)) continue;
      c.legs.forEach(L => used[L.p.id] = (used[L.p.id] || 0) + 1); out.push(c);
    }
    return out;
  };
  const two = pick(2, 6, 10, 3), three = pick(3, 15, 30, 2);
  if (!two.length && !three.length) return '';
  if (MKT.reg){
    const lg = L => ({gid: String(L.g.id), start: L.g.start_utc, kind: 'goal', side: 'o', line: .5, name: MKT.norm(L.p.name), lab: `${L.p.name} to score`, p: +L.q.toFixed(4), price: L.b.o, book: L.b.k});
    two.forEach(c => MKT.reg('Likeliest-scorer parlays (experimental)', '2-leg 6-10x', c.legs.map(lg), c.p, c.d));
    three.forEach(c => MKT.reg('Likeliest-scorer parlays (experimental)', '3-leg 15-30x', c.legs.map(lg), c.p, c.d));
  }
  const legSlip = L => ({id: `nhl|${L.g.id}|${L.p.id}|goal1`, sport: 'nhl', g: L.g.id, glab: `${L.g.away.team} @ ${L.g.home.team}`, lab: `${L.p.name} to score`, p: L.q, side: 'over', stat: 'goals', line: .5});
  const row = c => `<tr><td class="l">${c.legs.map(L => `<b>${esc(L.p.name)}</b> <span class="small muted">${esc(L.team)} · ${pct(L.q)} · ${L.b.u ? `<a href="${esc(L.b.u)}" target="_blank" rel="noopener">${am(L.b.o)} ${esc(L.b.name)} ↗</a>` : `${am(L.b.o)} ${esc(L.b.name)}`}</span>`).join('<br>')}</td>
    <td class="big">${pct(c.p)}</td><td class="pay">${c.d.toFixed(1)}x</td><td class="l add">${window.SLIP ? SLIP.allBtn(c.legs.map(legSlip), '+ add parlay') : ''}</td></tr>`;
  const table = (lab, list) => list.length ? `<div class="eyebrow" style="margin-top:10px">${lab}</div><div class="tw"><table class="t pairs atgp"><thead><tr><th class="l">Legs</th><th>Chance</th><th>Pays</th><th class="l">My parlay</th></tr></thead><tbody>${list.map(row).join('')}</tbody></table></div>` : '';
  return `<section class="panel"><div class="phead"><h2>Likeliest-scorer parlays <span class="exp3">experimental</span></h2><span class="small muted">Priced at ${esc(MKT.myBooks().join(', ') || 'the best book')}</span></div>
    <details class="how"${innerWidth > 640 ? ' open' : ''}><summary>How these are picked</summary><p class="note">We graded 4,510 anytime-goal prices at your books from Oct 12-30. The only legs that paid their price were our top 3 scorers by usage priced +300 or shorter (hit 37%, +2%); longer shots lost about 10%, and legs that looked like 10%+ edges lost 25% (usually a stale price). Goal droughts, power-play role and our model’s gap to the market added nothing the price didn’t already have. So these parlays use only those legs, likeliest first, one per game: 2-legs paying 6-10x (about 1 in 4 to 1 in 6) and 3-legs paying 15-30x (about 1 in 10). The legs are priced about fair, so expect roughly break-even over time with fewer long dry spells, not a proven edge. Tracked on the Profit page.</p></details>
    ${table('2-leg · pays 6-10x', two)}${table('3-leg · pays 15-30x', three)}</section>`;
}
// parlays by payout tier (mkt.js tierCard): anytime goals from our top 3 per game (30%+), plus game lines
function tierParlays(){
  if (!(window.MKT && MKT.tierCard)) return '';
  const gameLegs = [], top = {};
  for (const g of D.games){
    for (const s of [g.away, g.home]) for (const p of s.players) if (p.ur && p.ur <= 3 && p.p >= .30) top[g.id + '|' + MKT.norm(p.name)] = 1;
    const m = g.ml; if (!m) continue;
    const A = g.away.team, H = g.home.team;
    gameLegs.push({gid: g.id, kind: 'ml', side: 'away', p: m.away, lab: `${A} moneyline`}, {gid: g.id, kind: 'ml', side: 'home', p: m.home, lab: `${H} moneyline`},
                  {gid: g.id, kind: 'spread', side: 'away', line: 1.5, p: m.away_pl, lab: `${A} −1.5`}, {gid: g.id, kind: 'spread', side: 'home', line: -1.5, p: m.home_pl, lab: `${H} −1.5`},
                  {gid: g.id, kind: 'spread', side: 'away', line: -1.5, p: m.away_pl_plus, lab: `${A} +1.5`}, {gid: g.id, kind: 'spread', side: 'home', line: 1.5, p: m.home_pl_plus, lab: `${H} +1.5`});
    for (const k of ['5.5', '6.5']) if (m.totals[k] != null)
      gameLegs.push({gid: g.id, kind: 'total', side: 'over', line: +k, p: m.totals[k], lab: `${A}-${H} over ${k}`}, {gid: g.id, kind: 'total', side: 'under', line: +k, p: 1 - m.totals[k], lab: `${A}-${H} under ${k}`});
  }
  const games = D.games.map(g => ({id: g.id, label: `${g.away.team} @ ${g.home.team}`, start: g.start_utc}));
  const shots = {sog05: 'shots', sog15: 'shots', sog25: 'shots', sog35: 'shots', sog45: 'shots'};
  // anytime goals only from our top 3 (30%+); shots from anyone (the shots model is calibrated across the board)
  const all = MKT.tierCard({sport: 'nhl', title: 'Parlays by payout (your books)', games, gameLegs, wOurs: {goal: .5},
                            propKinds: {goal: 'to score', ...shots}, propOk: (gid, nm, k) => k !== 'goal' || !!top[gid + '|' + nm]});
  // shots + moneyline: priced at the best book anywhere, every combo has at least one of each
  const sml = MKT.tierCard({sport: 'nhl', title: 'Shots + moneyline parlays', games, gameLegs: gameLegs.filter(L => L.kind === 'ml'),
                            propKinds: shots, overOnly: true, anyBook: true, need: ['ml', 'shots']});
  const body = h => h.replace(/<h2>[^<]*<\/h2>/, '');
  return body(all) === body(sml) ? sml : all + sml;                 // same picks (no goal prices yet): show one card
}
// Plays tab: every parlay card in one place
// each card folds under its title; folded cards are remembered on this device
let FOLD = {};
try { FOLD = JSON.parse(localStorage.getItem('nhl-plays-fold') || '{}'); } catch (e) {}
function foldable(html){
  return html.split(/(?=<section class="panel")/).filter(x => x.trim()).map(sec => {
    const m = sec.match(/<h2>([\s\S]*?)<\/h2>/); if (!m) return sec;
    const key = m[1].replace(/<[^>]+>/g, '').trim();
    const body = sec.replace(m[0], '');
    return `<details class="pfold" data-fold="${esc(key)}"${FOLD[key] ? '' : ' open'}><summary><h2>${m[1]}</h2><span class="chev" aria-hidden="true">▾</span></summary>${body}</details>`;
  }).join('');
}
// Triple-green longshots (experimental): two triple-green players from different games, each 15-35% to score, whose fair
// payout is 12x+; the likeliest six. Backtest 2024-26: such pairs hit ~16 times vs ~10 expected (pairs share players, so
// weaker evidence than it looks). Shown and tracked, not recommended.
function tripleCard(){
  const legs = [], now = Date.now(), dec = o => o > 0 ? 1 + o / 100 : 1 + 100 / -o, am = o => (o > 0 ? '+' : '') + o;
  for (const g of D.games){
    if (g.start_utc && new Date(g.start_utc) <= now) continue;
    for (const [s, o] of [[g.away, g.home], [g.home, g.away]]) for (const p of s.players)
      if (tripleGreen(p, o)) legs.push({g, p, team: s.team, b: window.MKT && MKT.propBest ? MKT.propBest(g.id, p.name, 'goal') : null});
  }
  if (!legs.length) return '';
  const pairs = [];
  for (let i = 0; i < legs.length; i++) for (let j = i + 1; j < legs.length; j++){
    const a = legs[i], c = legs[j];
    if (a.g.id === c.g.id || a.p.p < .15 || a.p.p > .35 || c.p.p < .15 || c.p.p > .35) continue;
    const pr = a.p.p * c.p.p; if (pr > 1 / 12) continue;
    pairs.push({legs: [a, c], p: pr, d: a.b && c.b ? dec(a.b.o) * dec(c.b.o) : 1 / pr});
  }
  pairs.sort((x, y) => y.p - x.p); const top = pairs.slice(0, 6);
  if (window.MKT && MKT.reg) top.forEach(c => MKT.reg('Triple-green longshots (experimental)', '2-leg', c.legs.map(L => ({gid: String(L.g.id), start: L.g.start_utc, kind: 'goal', side: 'o', line: .5,
    name: MKT.norm(L.p.name), lab: `${L.p.name} to score`, p: L.p.p, price: L.b ? L.b.o : null, book: L.b ? L.b.k : null})), c.p, c.d));
  const price = L => L.b ? (L.b.u ? `<a href="${esc(L.b.u)}" target="_blank" rel="noopener">${am(L.b.o)} ${esc(L.b.name)} ↗</a>` : `${am(L.b.o)} ${esc(L.b.name)}`) : '<span class="muted">no price yet</span>';
  const who = legs.map(L => `<b>${esc(L.p.name)}</b> <span class="small muted">${esc(L.team)} · ${pct(L.p.p)} · ${price(L)}</span>`).join('<br>');
  const rows = top.map(c => `<tr><td class="l">${c.legs.map(L => `<b>${esc(L.p.name)}</b> <span class="small muted">${esc(L.team)} · ${pct(L.p.p)} · ${price(L)}</span>`).join('<br>')}</td>
    <td class="big">${pct(c.p)}</td><td class="pay">${c.d.toFixed(1)}x</td><td class="l add">${window.SLIP ? SLIP.allBtn(c.legs.map(L => ({id: `nhl|${L.g.id}|${L.p.id}|goal1`, sport: 'nhl', g: L.g.id, glab: `${L.g.away.team} @ ${L.g.home.team}`, lab: `${L.p.name} to score`, p: L.p.p, side: 'over', stat: 'goals', line: .5})), '+ add pair') : ''}</td></tr>`).join('');
  return `<section class="panel"><div class="phead"><h2>Triple-green longshots <span class="exp3">experimental</span></h2><span class="small muted">${legs.length} triple-green player${legs.length === 1 ? '' : 's'} tonight</span></div>
    <details class="how"><summary>What this is</summary><p class="note">Players with all three green flags: a strong head-to-head record against tonight's opponent (3+ meetings, 0.25+ points a game above his usual rate), an opposing goalie struggling this season (GSAA −1.5 or worse) and a defense that allows more expected goals to his position. One or two of these on their own predicted nothing in our 2024-26 tests, but players with all three scored 12-20% more often than our chance in both seasons, and pairs of them hit about 16 times vs 10 expected. That is a small sample (about 340 player-games a season), so this card is tracked on the Parlays results tab to see if it holds up, not a recommendation. Chances shown are our model's, unadjusted.</p></details>
    <div class="eyebrow" style="margin-top:8px">Tonight's triple-green players</div><p style="margin:6px 0 10px;line-height:1.7">${who}</p>
    ${top.length ? `<div class="eyebrow">Pairs paying 12x+ (fair)</div><div class="tw"><table class="t pairs atgp"><thead><tr><th class="l">Two-man</th><th>Chance</th><th>Pays</th><th class="l">My parlay</th></tr></thead><tbody>${rows}</tbody></table></div>` : '<p class="small muted">No pair from two different games in the 15-35% range tonight.</p>'}</section>`;
}
// Same-game combos (experimental, 2026-10-21): goal + his team wins, goal + game over 5.5, 3+ shots + goal, for each game's
// top 3 by usage rank. Chance = each leg's market-anchored chance x the game-state sims' lift (shiftsim.py; 2025-26
// backtest, 1,312 games: legs multiplied ran 20-35% low on these, the lift fixed it in both halves). Fair = what a
// same-game parlay must pay. Tracked at fair odds (no book same-game price to grade against).
function sameGameCard(){
  if (!(window.MKT && MKT.sgLeg)) return '';
  const now = Date.now(), am = o => (o > 0 ? '+' : '') + o, dec = o => o > 0 ? 1 + o / 100 : 1 + 100 / -o;
  const fairAm = q => q >= .5 ? -Math.round(100 * q / (1 - q)) : Math.round(100 * (1 - q) / q);
  const out = [];
  for (const g of D.games){
    if ((g.start_utc && new Date(g.start_utc) <= now) || !g.ml) continue;
    const combos = [], gl0 = {gid: String(g.id), start: g.start_utc};
    for (const [s, home] of [[g.away, false], [g.home, true]]) for (const p of s.players){
      if (!p.sg) continue;
      const goal = MKT.sgLeg(g.id, 'goal', 'o', .5, p.p, p.name); if (!goal) continue;
      const G = {lab: `${p.name} to score`, L: goal, leg: {...gl0, kind: 'goal', side: 'o', line: .5, name: MKT.norm(p.name), lab: `${p.name} to score`}};
      const opts = [
        ['win', {lab: `${s.team} wins`, L: MKT.sgLeg(g.id, 'ml', home ? 'home' : 'away', null, home ? g.ml.home : g.ml.away), leg: {...gl0, kind: 'ml', side: home ? 'home' : 'away', line: null, name: null, lab: `${s.team} moneyline`}}],
        ['o55', {lab: 'Game over 5.5', L: g.ml.totals && g.ml.totals['5.5'] != null ? MKT.sgLeg(g.id, 'total', 'over', 5.5, g.ml.totals['5.5']) : null, leg: {...gl0, kind: 'total', side: 'over', line: 5.5, name: null, lab: `${g.away.team}-${g.home.team} over 5.5`}}],
        ['s3', {lab: `${p.name} 3+ shots`, L: p.so ? MKT.sgLeg(g.id, 'sog25', 'o', 2.5, p.so[2], p.name) : null, leg: {...gl0, kind: 'sog', side: 'o', line: 2.5, name: MKT.norm(p.name), lab: `${p.name} 3+ shots`}}]];
      for (const [k, o] of opts){
        if (!o.L || !p.sg[k]) continue;
        const pr = Math.min(G.L.q * o.L.q * p.sg[k], G.L.q, o.L.q);
        combos.push({k, legs: [G, o], pr, lift: p.sg[k], mult: G.L.b && o.L.b ? dec(G.L.b.o) * dec(o.L.b.o) : null});
      }
    }
    combos.sort((a, b) => b.pr - a.pr);
    if (combos.length) out.push({g, c: combos.slice(0, 4)});
  }
  if (!out.length) return '';
  const KL = {win: 'Goal + win', o55: 'Goal + over 5.5', s3: '3+ shots + goal'};
  if (MKT.reg) out.forEach(G => G.c.forEach(c => MKT.reg('Same-game combos (experimental)', KL[c.k],
    c.legs.map(x => ({...x.leg, p: +x.L.q.toFixed(4), price: x.L.b ? x.L.b.o : null, book: x.L.b ? x.L.b.k : null})), c.pr, 1 / c.pr)));
  const price = b => !b ? '<span class="muted">no price</span>' : b.u ? `<a href="${esc(b.u)}" target="_blank" rel="noopener">${am(b.o)} ${esc(b.name)} ↗</a>` : `${am(b.o)} ${esc(b.name)}`;
  const slip = (g, c) => c.legs.map((x, i) => ({id: `nhl|${g.id}|sg|${c.k}|${MKT.norm(x.lab)}`, sport: 'nhl', g: g.id, glab: `${g.away.team} @ ${g.home.team}`, lab: x.lab, p: x.L.q, side: 'over'}));
  const row = (g, c) => `<tr><td class="l">${c.legs.map(x => `<b>${esc(x.lab)}</b> <span class="small muted">${pct(x.L.q)} · ${price(x.L.b)}</span>`).join('<br>')}</td>
    <td class="big">${pct(c.pr)}</td><td class="pay" title="${c.mult ? 'The legs bet separately at these prices would pay ' + c.mult.toFixed(1) + 'x; a book’s same-game price pays less than that' : ''}"><b>${am(fairAm(c.pr))}</b></td>
    <td><span class="sgl" title="In our simulated games these two happen together ${c.lift.toFixed(2)}x as often as if they were unrelated">×${c.lift.toFixed(2)}</span></td>
    <td class="l add">${window.SLIP ? SLIP.allBtn(slip(g, c), '+ add') : ''}</td></tr>`;
  const body = out.map(G => `<tr class="slate"><td class="l" colspan="5"><b>${esc(G.g.away.team)} @ ${esc(G.g.home.team)}</b> <span class="small muted">${esc(G.g.start || '')}</span></td></tr>` + G.c.map(c => row(G.g, c)).join('')).join('');
  return `<section class="panel"><div class="phead"><h2>Same-game combos <span class="exp3">experimental</span></h2><span class="small muted">take one only if your book’s same-game price beats Fair</span></div>
    <details class="how"><summary>How these work</summary><p class="note">Two legs from the same game that tend to happen together: a goal and his team winning, a goal and the game going over 5.5, or 3+ shots and a goal. Each leg’s chance is market-anchored (anytime goal: our number blended with the market; moneyline and total: ours averaged with the books’ no-vig line; shots: ours 50/50 with the books’ no-vig), then multiplied by how much more often the two happen together in our game simulations (power plays, score effects, empty nets, overtime, shared to players by their even-strength and power-play roles). Tested on 1,312 games of 2025-26: multiplying the legs as if unrelated ran 20-35% low on these combos, and the simulated link fixed it in both halves of the season. Fair is the price a same-game parlay must pay to break even; books build in their own correlation, so most same-game prices will be below Fair. Leaders: each game’s top 3 by usage rank. Tracked on the Profit page at fair odds.</p></details>
    <div class="tw"><table class="t pairs atgp sgc"><thead><tr><th class="l">Combo</th><th>Chance</th><th>Fair</th><th>Together</th><th class="l">My parlay</th></tr></thead><tbody>${body}</tbody></table></div></section>`;
}
// Learning builder candidates (builder.py, owner 2026-10-22): in freeze mode, anytime goals for skaters 15%+ and shots
// 2+ / 3+ for players projecting 2+ shots, at the owner's books, with reasons -- usage (our top 3 in the game), pp1
// (first power-play unit), goalie (the opposing goalie struggling, as the page's green flag); fits with usage + one more.
function learnCandsNHL(){
  const now = Date.now();
  for (const g of D.games){
    if (g.start_utc && new Date(g.start_utc) <= now) continue;
    for (const [s, o] of [[g.away, g.home], [g.home, g.away]]) for (const p of s.players){
      const f = [p.ur && p.ur <= 3 ? 'usage' : null, p.pp === 'PP1' ? 'pp1' : null, gGreen(o) ? 'goalie' : null].filter(Boolean);
      const fit = f.includes('usage') && f.length >= 2;
      if (p.p >= .15) MKT.cand(g.id, g.start_utc, p.name, 'goal', .5, null, f, fit);
      if ((p.sog ?? 0) >= 2) for (const k of ['sog15', 'sog25']) MKT.cand(g.id, g.start_utc, p.name, k, null, null, f, fit);
    }
  }
}
function playsView(){
  if (window.MKT && MKT.FREEZE && MKT.cand) try { learnCandsNHL(); } catch (e) {}
  const h = (window.MKT && MKT.dailyCard ? MKT.dailyCard() : '') + foldable(likelyCard() + atgParlays() + sameGameCard() + tierParlays() + pairsCard() + tripleCard());
  return h || '<section class="panel"><div class="empty">No plays yet: they appear once sportsbook prices for tonight’s games are in.</div></section>';
}
// Longshot two-mans priced at the viewer's books: legs 15-35% to score, chance blended with the exchanges', every
// leg beating its price, two different games, paying 12x+ at the book. (Backtest 2024-26: adding shot / xG / DvP signals
// to the pick made these pairs hit LESS often -- 5.4% vs 6.0% and 5.1% vs 7.3% -- so the goal chance stays the pick.)
function pricedLongshots(){
  if (!(window.MKT && MKT.propBest && MKT.myBooks().length)) return [];
  const now = Date.now(), dec = o => o > 0 ? 1 + o / 100 : 1 + 100 / -o, legs = [];
  for (const g of D.games){
    if (g.start_utc && new Date(g.start_utc) <= now) continue;
    for (const side of [g.away, g.home]) for (const p of side.players){
      if (p.p < .15 || p.p > .35 || !(p.ur && p.ur <= 3)) continue;  // our top 3 per game only: everyone else has lost on Kalshi
      const b = MKT.propBest(g.id, p.name, 'goal'); if (!b || !b.mine) continue;
      const m = MKT.propMarket ? MKT.propMarket(g.id, p.name, 'goal') : null, q = MKT.blend ? MKT.blend(g.id, p.name, 'goal', p.p) : m != null ? .5 * p.p + .5 * m : p.p;
      if (q * dec(b.o) - 1 <= 0) continue;
      legs.push({g, p, team: side.team, b, q, m});
    }
  }
  const out = [];
  for (let i = 0; i < legs.length; i++) for (let j = i + 1; j < legs.length; j++){
    const a = legs[i], c = legs[j]; if (a.g.id === c.g.id) continue;
    const d = dec(a.b.o) * dec(c.b.o); if (d < 12) continue;
    out.push({legs: [a, c], p: a.q * c.q, d});
  }
  return out.sort((x, y) => y.p - x.p).slice(0, 6);
}
function pairsCard(){
  const P = D.pairs || [];
  const L2 = pricedLongshots(), am = o => (o > 0 ? '+' : '') + o;
  const pricedRows = L2.map(c => `<tr><td class="l">${c.legs.map(L => `<b>${esc(L.p.name)}</b> <span class="small muted" title="Our chance ${pct(L.p.p)}${L.m != null ? ', exchanges ' + pct(L.m) : ''}">${esc(L.team)} · ${pct(L.q)} · ${L.b.u ? `<a href="${esc(L.b.u)}" target="_blank" rel="noopener">${am(L.b.o)} ${esc(L.b.name)} ↗</a>` : `${am(L.b.o)} ${esc(L.b.name)}`}</span>`).join('<br>')}</td>
      <td class="big">${pct(c.p)}</td><td class="pay">${c.d.toFixed(1)}x</td><td class="edge ${c.p * c.d - 1 >= .1 ? 'pos-d' : ''}" style="font-weight:700">+${((c.p * c.d - 1) * 100).toFixed(0)}%</td>
      <td class="l add">${window.SLIP ? SLIP.allBtn(c.legs.map(L => ({id: `nhl|${L.g.id}|${L.p.id}|goal1`, sport: 'nhl', g: L.g.id, glab: `${L.g.away.team} @ ${L.g.home.team}`, lab: `${L.p.name} to score`, p: L.q, side: 'over', stat: 'goals', line: .5})), '+ add pair') : ''}</td></tr>`).join('');
  const priced = L2.length ? `<div class="tw"><table class="t pairs atgp"><thead><tr><th class="l">Two-man</th><th>Chance</th><th>Pays</th><th>Edge</th><th class="l">My parlay</th></tr></thead><tbody>${pricedRows}</tbody></table></div>` : '';
  if (window.MKT && MKT.reg){
    const gs = Object.fromEntries(D.games.map(g => [String(g.id), g.start_utc]));
    L2.forEach(c => MKT.reg('Longshot two-mans', '12x+', c.legs.map(L => ({gid: String(L.g.id), start: L.g.start_utc, kind: 'goal', side: 'o', line: .5, name: MKT.norm(L.p.name), lab: `${L.p.name} to score`, p: +L.q.toFixed(4), price: L.b.o, book: L.b.k})), c.p, c.d));
    P.forEach(x => MKT.reg('2-leg pairs paying 15x+', x.slate || 'Tonight', x.legs.map(L => ({gid: String(L.gid), start: gs[String(L.gid)], kind: 'goal', side: 'o', line: .5, name: MKT.norm(L.name), lab: `${L.name} to score`, p: L.p, price: null, book: null})), x.p, x.payout));
  }
  if (!P.length && !priced) return '';
  const R = D.pairs_record, rec = R && R.n ? `Record since ${esc(R.since)}: <b>${R.hits}-${R.n - R.hits}</b> (${(R.hits / R.n * 100).toFixed(1)}% vs ${(R.pred * 100).toFixed(1)}% implied) · return if paid our fair odds ${R.ret >= 0 ? '+' : ''}${(R.ret * 100).toFixed(0)}%` : 'Record: tracking starts with tonight\'s pairs.';
  const legSlip = L => ({id: `nhl|${L.gid}|${L.id}|goal1`, sport: 'nhl', g: L.gid, glab: L.game, lab: `${L.name} to score`, p: L.p, side: 'over', stat: 'goals', line: .5});
  const order = ['Early slate', 'Middle slate', 'Late slate'], by = {};
  P.forEach(x => (by[x.slate || 'Tonight'] = by[x.slate || 'Tonight'] || []).push(x));
  const rowsFor = list => list.map(x => `<tr><td class="l">${x.legs.map(L => `<b>${esc(L.name)}</b> <span class="small muted">${esc(L.team)} · ${pct(L.p)}</span>`).join(' + ')}</td><td class="big">${pct(x.p)}</td><td class="pay">${x.payout}x</td><td class="l add">${window.SLIP ? SLIP.allBtn(x.legs.map(legSlip), '+ add pair') : ''}</td></tr>`).join('');
  const singles = Object.entries(D.slates || {}).filter(([sl, n]) => n < 2 && !by[sl]).map(([sl]) => `<tr class="slate"><td class="l muted" colspan="4"><b>${esc(sl)}</b>: one game only, no different-game pair possible</td></tr>`).join('');
  const body = [...order.filter(k => by[k]), ...Object.keys(by).filter(k => !order.includes(k))].map(k => `<tr class="slate"><td class="l" colspan="4"><b>${esc(k)}</b> <span class="small muted">first puck drop ${esc(by[k][0].slate_start || '')} ET · ${by[k].length} pair${by[k].length === 1 ? '' : 's'}</span></td></tr>` + rowsFor(by[k])).join('') + singles;
  const tracked = P.length ? `<details${priced ? '' : ' open'}><summary class="small muted" style="cursor:pointer;margin:10px 0 6px">Tracked 15x pairs (fair odds) · ${rec}</summary>
    <div class="tw"><table class="t pairs"><thead><tr><th class="l">Pair</th><th>Chance</th><th>Fair payout</th><th class="l">My parlay</th></tr></thead><tbody>
    ${body}
    </tbody></table></div></details>` : '';
  return `<section class="panel"><div class="phead"><h2>Longshot two-mans</h2><span class="small muted">${priced ? 'Priced at ' + esc(MKT.myBooks().join(', ')) + ' · pays 12x+' : 'waiting for prices at your books'}</span></div>
    <details class="how"${innerWidth > 640 ? ' open' : ''}><summary>How these are picked</summary><p class="note">Two anytime-goal legs from different games, each from our top 3 in its game and 15-35% to score (on Kalshi this season, legs outside our top 3 have lost money at every weighting), with each chance half ours and half the exchanges' and each beating its price at your books; the pairs paying 12x+ that are most likely to hit. We tested adding shot volume, expected-goal trends and defense-vs-position on top of our goal chance (2024-26): pairs picked that way hit less often (5.4% vs 6.0%, 5.1% vs 7.3%), because the goal model already uses shot quality, the opponent and the goalie. Expect about 1 hit in 12-15.</p></details>
    ${priced}${tracked}</section>`;
  return `<section class="panel"><div class="phead"><h2>2-leg pairs paying 15x+</h2><span class="small muted">${rec}</span></div>
    <details class="how"${innerWidth > 640 ? ' open' : ''}><summary>How these work</summary><p class="note">The likeliest anytime-goal pairs from different games whose fair payout is 15x or more (only take one if your book pays at least that). These are fairly priced, not a proven edge: in backtests, pairs built this way hit about their implied 1 in 15 (anywhere from 5% to 8% depending on exactly which near-identical pair was picked). Both legs come from the same slate (early before 5 PM ET, middle 5-9 PM, late 9 PM+), and each slate's pairs freeze at its first puck drop and are graded on the Results page.</p></details>
    <div class="tw"><table class="t pairs"><thead><tr><th class="l">Pair</th><th>Chance</th><th>Fair payout</th><th class="l">My parlay</th></tr></thead><tbody>
    ${body}
    </tbody></table></div></section>`;
}
function jumpBar(){
  if (D.games.length < 2) return '';
  const t = g => (g.start || '').split('·').pop().replace(' ET', '').trim();
  return `<div class="gjw"><button type="button" class="gjarr" data-gj="-1" aria-label="Earlier games">‹</button><nav class="gjump" aria-label="Jump to game">${D.games.map(g => `<a href="#g${g.id}">${esc(g.away.team)} @ ${esc(g.home.team)}<small>${esc(t(g))}</small></a>`).join('')}</nav><button type="button" class="gjarr" data-gj="1" aria-label="Later games">›</button></div>`;
}
// game strip: arrows and the mouse wheel scroll it sideways on desktop; fades / disabled arrows show where more games are
function gjSync(){
  for (const w of document.querySelectorAll('.gjw')){
    const n = w.querySelector('.gjump'); if (!n) continue;
    const l = n.scrollLeft > 2, r = n.scrollLeft + n.clientWidth < n.scrollWidth - 2;
    w.classList.toggle('ml', l); w.classList.toggle('mr', r);
    const [a, b] = w.querySelectorAll('.gjarr'); if (a) a.disabled = !l; if (b) b.disabled = !r;
  }
}
document.addEventListener('click', e => { const b = e.target.closest && e.target.closest('[data-gj]'); if (!b) return;
  const n = b.parentElement.querySelector('.gjump'); n.scrollBy({left: +b.dataset.gj * n.clientWidth * .7}); });
document.addEventListener('wheel', e => { const n = e.target.closest && e.target.closest('.gjump'); if (!n || Math.abs(e.deltaX) > Math.abs(e.deltaY)) return;
  if (n.scrollWidth <= n.clientWidth) return; e.preventDefault(); n.style.scrollBehavior = 'auto'; n.scrollLeft += e.deltaY; n.style.scrollBehavior = ''; }, {passive: false});
document.addEventListener('scroll', e => { if (e.target.classList && e.target.classList.contains('gjump')) gjSync(); }, true);
addEventListener('resize', gjSync);
new MutationObserver(() => requestAnimationFrame(gjSync)).observe(document.body, {childList: true, subtree: true});
// the All players sort / Value-only chips, shared with the Games tab (same state, so the choice carries over)
function playerChips(){
  return `<div class="pchips"><div class="seg" role="group" aria-label="Sort players"><button data-bsort="p" aria-pressed="${state.bsort !== 'pf' && state.bsort !== 'u'}">Anytime goal</button><button data-bsort="pf" aria-pressed="${state.bsort === 'pf'}">First goal</button><button data-bsort="u" aria-pressed="${state.bsort === 'u'}" title="${UTIP}">Usage rank</button></div><div class="seg" role="group" aria-label="Filter"><button data-bval="1" aria-pressed="${!!state.bval}" title="Only players whose best anytime-goal price at your books beats our chance by 5%+ (top-3 usage players; same rule as the green prices)">Value only</button></div></div>`;
}
function gamePlayers(list, gid){
  let x = list.slice();
  if (state.bval) x = x.filter(p => isValue({...p, gid}));
  return x.sort((a, b) => state.bsort === 'pf' ? (b.pf ?? 0) - (a.pf ?? 0) : state.bsort === 'u' ? ulam(b) - ulam(a) : b.p - a.p);
}
// Games: NFL-style rail of games + one game at a time, with tabs (Projections / Shot map)
const UI = (() => { try { return JSON.parse(localStorage.getItem('nhl-ui') || '{}'); } catch (e) { return {}; } })();
const saveUI = () => { try { localStorage.setItem('nhl-ui', JSON.stringify({gtab: state.gtab, smode: state.smode})); } catch (e) {} };
state.gtab = UI.gtab === 'map' ? 'map' : 'proj'; state.smode = ['ov', 'off', 'def'].includes(UI.smode) ? UI.smode : 'ov';
{ const m = /^#g(\d+)$/.exec(location.hash), up = D.games.find(g => !g.start_utc || new Date(g.start_utc) > Date.now());
  state.game = m && D.games.some(g => g.id === +m[1]) ? +m[1] : (up || D.games[0] || {}).id; }
function gameList(){
  return D.games.map(g => { const hw = g.ml ? g.ml.home : .5, fav = hw >= .5 ? g.home.team : g.away.team;
    return `<button class="game-btn" data-game="${g.id}" aria-current="${g.id === state.game}">
      <span class="m"><span class="teams">${esc(g.away.team)} @ ${esc(g.home.team)}</span><span class="line">${esc(String(g.start).replace(/^\w+ \w+ \d+ · /, '').replace(/ ET$/, ''))}</span></span>
      ${g.ml ? `<span class="line">${esc(fav)} ${Math.round(Math.max(hw, 1 - hw) * 100)}% · total ${g.ml.total.toFixed(1)}</span>
      <span class="mini" aria-hidden="true"><i style="flex:${1 - hw};background:var(--away)"></i><i style="flex:${hw};background:var(--accent)"></i></span>` : ''}
    </button>`; }).join('');
}
function gameCard(g){
  return `<section class="panel gcard" id="g${g.id}"><div class="gh"><h2>${esc(g.away.team)} at ${esc(g.home.team)}</h2><span class="small muted">${esc(g.start)} · goalies ${esc(g.away.goalie)} / ${esc(g.home.goalie)}</span></div>
    ${mlBlock(g)}${window.MKT ? MKT.game(g.id) : ''}<div class="sides">${side(g.away, g.home, g.id)}${side(g.home, g.away, g.id)}</div></section>`;
}
function games(){
  const g = D.games.find(x => x.id === state.game);
  if (!g) return '<div class="empty">No games.</div>';
  const gsw = `<div class="seg gsw" role="group" aria-label="Game view">${[['proj', 'Projections'], ['map', 'Shot map']].map(([k, l]) =>
    `<button data-gtab="${k}" aria-pressed="${state.gtab === k}">${l}</button>`).join('')}</div>`;
  const body = state.gtab === 'map' ? shotMapPanel(g) : picksKey() + playerChips() + gameCard(g);
  return `<div class="games"><nav class="rail" aria-label="Games">${gameList()}</nav><div class="detail">${gsw}${body}</div></div>`;
}
/* ---------------- shot map: where each team shoots vs where the other allows (display only) ---------------- */
const SM_ZONE = [['inner', 'Net front & low slot'], ['high', 'High slot'], ['perim', 'Outside']];
const ordn = n => { const s_ = ['th', 'st', 'nd', 'rd'], v = n % 100; return n + (s_[(v - 20) % 10] || s_[v] || s_[0]); };
function smRegion(r, c){
  const d = r * 5 - 7.5, l = Math.abs(c * 5 - 40);
  if (d < 0) return 'behind the net';
  if (d <= 12 && l <= 10) return 'net front';
  if (d <= 32 && l <= 12) return 'slot';
  if (d <= 50 && l <= 15) return 'high slot';
  if (d >= 50) return 'the points';
  if (d <= 40 && l <= 32) return 'the circles';
  return 'sharp angles';
}
function smFill(v, w){
  if (v == null) return 'none';
  return v >= 1 ? `rgba(255,99,71,${(Math.min(1, (v - 1) / .3) * .95 * w).toFixed(3)})` : `rgba(70,150,255,${(Math.min(1, (1 - v) / .25) * .85 * w).toFixed(3)})`;
}
function smSvg(vals, tipf, dots){
  const DN = D.shotmap.lg_density, dmax = Math.max(...DN.flat().filter(x => x != null));
  const P = (l, d) => `${(127.5 + l * 3).toFixed(1)},${((d + 10) * 3).toFixed(1)}`;
  let cells = '';
  for (let r = 0; r < 15; r++) for (let c = 0; c < 17; c++){
    const v = vals[r][c]; if (v == null) continue;
    const w = Math.max(.5, Math.min(1, Math.sqrt(DN[r][c] / dmax) * 1.3));
    cells += `<rect x="${c * 15}" y="${r * 15}" width="15" height="15" fill="${smFill(v, w)}" data-tip="${esc(tipf(r, c))}"/>`;
  }
  return `<svg class="smsvg" viewBox="-2 -2 259 229" role="img" aria-label="Shot map">
    <defs><filter id="smblur" x="-5%" y="-5%" width="110%" height="110%"><feGaussianBlur stdDeviation="6"/></filter><clipPath id="smclip"><path d="M0,225 L0,84 Q0,0 84,0 L171,0 Q255,0 255,84 L255,225 Z"/></clipPath></defs>
    <g clip-path="url(#smclip)"><g filter="url(#smblur)"${dots ? ' opacity=".35"' : ''}>${cells}</g>${dots || ''}</g>
    <g class="rink" fill="none">
      <path d="M0,225 L0,84 Q0,0 84,0 L171,0 Q255,0 255,84 L255,225"/>
      <line x1="0" y1="30" x2="255" y2="30" class="gl"/><line x1="0" y1="222" x2="255" y2="222" class="bl"/>
      <rect x="118.5" y="20" width="18" height="10" rx="2"/><path d="M109.5,30 A18,18 0 0 0 145.5,30" class="cr"/>
      <circle cx="61.5" cy="90" r="45"/><circle cx="193.5" cy="90" r="45"/><circle cx="61.5" cy="90" r="2" class="dot"/><circle cx="193.5" cy="90" r="2" class="dot"/>
      <polygon points="${P(-3, 0)} ${P(-22, 20)} ${P(-22, 35)} ${P(22, 35)} ${P(22, 20)} ${P(3, 0)}" class="hp"/>
    </g></svg>`;
}
function smBlock(off, def){
  const SM = D.shotmap, O = SM.teams[off.team], F = SM.teams[def.team];
  if (!O || !F) return `<div class="smb"><h3>${esc(off.team)} attack</h3><div class="empty">No shot data yet.</div></div>`;
  const ov = O.off.map((row, r) => row.map((v, c) => v == null || F.dfn[r][c] == null ? null : v * F.dfn[r][c] / 1e4));
  const vals = state.smode === 'off' ? O.off.map(row => row.map(v => v == null ? null : v / 100))
    : state.smode === 'def' ? F.dfn.map(row => row.map(v => v == null ? null : v / 100)) : ov;
  const pc = v => (v >= 1 ? '+' : '−') + Math.abs(Math.round((v - 1) * 100)) + '%';
  const tipf = (r, c) => `<b>${smRegion(r, c)}</b><br>${esc(off.team)} shoots here ${pc(O.off[r][c] / 100)} vs average<br>${esc(def.team)} allows ${pc(F.dfn[r][c] / 100)}<br>Together: ${pc(ov[r][c])}`;
  // areas where the two meet, weighted by how often the league shoots there
  const DN = SM.lg_density, agg = {};
  O.off.forEach((row, r) => row.forEach((v, c) => { if (ov[r][c] == null) return; const k = smRegion(r, c), a = agg[k] = agg[k] || {w: 0, v: 0}; a.w += DN[r][c]; a.v += DN[r][c] * ov[r][c]; }));
  const reg = Object.entries(agg).filter(([, a]) => a.w > .4).map(([k, a]) => ({k, v: a.v / a.w}));
  const hot = reg.filter(x => x.v >= 1.08).sort((a, b) => b.v - a.v).slice(0, 2), cold = reg.filter(x => x.v <= .92).sort((a, b) => a.v - b.v).slice(0, 1);
  const G = def.goalie_id != null && SM.goalies[def.goalie_id], LZ = SM.lg_zone || {};
  const sv = x => x.toFixed(3).replace(/^0/, '');
  const gz = G ? SM_ZONE.filter(([z]) => G[z]).map(([z, lab]) => { const d = G[z].sv - LZ[z];
      return `<span class="smg" tabindex="0" data-tip="${esc(def.goalie)}: ${sv(G[z].sv)} save % on ${G[z].n} shots from here (league ${sv(LZ[z])}), last two seasons">${lab} <b class="${d <= -.03 ? 'g2' : d <= -.012 ? 'g1' : d >= .03 ? 'r2' : d >= .012 ? 'r1' : ''}">${sv(G[z].sv)}</b></span>`; }).join('') : '';
  // shooters: tap one to plot his last shots on the map
  const SH = SM.shooters || {}, PZ = SM.pos_zone || {};
  const pl = (off.players || []).filter(p => SH[p.id]).sort((a, b) => (b.sog ?? 0) - (a.sog ?? 0));
  const selId = (state.smp || {})[off.team], sel = pl.find(p => p.id === selId), S = sel && SH[sel.id];
  let dots = '';
  if (S){ for (let i = 0; i < S.xy.length; i += 3){ const [l, d, gl] = [S.xy[i], S.xy[i + 1], S.xy[i + 2]];
    if (d < -10 || d > 64) continue;
    dots += `<circle cx="${(127.5 + l * 3).toFixed(1)}" cy="${((d + 10) * 3).toFixed(1)}" r="${gl ? 5 : 3.2}" class="${gl ? 'smd g' : 'smd'}"/>`; } }
  const zb = (z, grp) => { const a = PZ[grp] || {}; return `<span class="smzb" aria-hidden="true">${[['inner', 'zi'], ['high', 'zh'], ['perim', 'zp']].map(([k, c]) => `<i class="${c}" style="flex:${z[k] || 0}"></i>`).join('')}</span>`; };
  const TY = {'tip-in': 'tips', 'wrap-around': 'wraps', 'between-legs': 'between-legs', deflected: 'deflections'};
  const tyl = t => TY[t] || t;
  // fit: how much more than average the opponent allows shots where this player shoots (his last shots)
  const fit = id => { const x = SH[id]; let t = 0, n = 0;
    for (let i = 0; i < x.xy.length; i += 3){ const r = Math.floor((x.xy[i + 1] + 10) / 5), c = Math.floor((x.xy[i] + 42.5) / 5);
      const v = F.dfn[r] && F.dfn[r][c]; if (v != null){ t += v / 100; n++; } }
    return n >= 8 ? t / n : null; };
  pl.forEach(p => { p._fit = fit(p.id); });
  const good = pl.filter(p => p._fit != null && p._fit >= 1.08).sort((a, b) => b._fit - a._fit);
  const fitTip = p => `${esc(def.team)} allows ${Math.round((p._fit - 1) * 100)}% more shots than average in the spots ${esc(p.name)} shoots from (his last ${SH[p.id].n} shots). Tested on 2024-26: this mostly reflects how many shots ${esc(def.team)} allows overall, which our shots projection already uses; the location part didn’t predict goals or extra shots.`;
  const srow = p => { const x = SH[p.id], a = (PZ[x.grp] || {}).inner || .25, d = (x.z.inner || 0) - a, gd = p._fit != null && p._fit >= 1.08;
    return `<button type="button" class="smp${gd ? ' fit' : ''}" data-smp="${esc(off.team)}:${p.id}" aria-pressed="${p.id === selId}">
      <span class="smpn"><b>${esc(p.name)}</b> <small>${esc(p.pos)}</small>${gd ? ` <span class="smfit" data-tip="${fitTip(p)}">fit +${Math.round((p._fit - 1) * 100)}%</span>` : ''}</span>${zb(x.z, x.grp)}
      <span class="smps"><b class="${d >= .08 ? 'up' : d <= -.08 ? 'dn' : ''}">${Math.round((x.z.inner || 0) * 100)}%</b> in close · ${esc(tyl(x.ty[0][0]))} ${Math.round(x.ty[0][1] * 100)}%</span></button>`; };
  const goodLine = good.length ? `<div class="smgood"><span class="small muted">Shoots where ${esc(def.team)} is soft:</span>${good.slice(0, 4).map(p =>
      `<button type="button" class="smfit" data-smp="${esc(off.team)}:${p.id}" data-tip="${fitTip(p)}">${esc(p.name)} +${Math.round((p._fit - 1) * 100)}%</button>`).join('')}</div>` : '';
  const shooters = pl.length ? `${goodLine}<details class="smsh"${selId ? ' open' : ''}><summary>Shooters <span class="small muted">· tap one to see his last shots</span></summary>
      <div class="smzk"><span><i class="zi"></i>in close</span><span><i class="zh"></i>high slot</span><span><i class="zp"></i>outside</span><span class="muted">${esc(off.team)} forwards average ${Math.round(((PZ.F || {}).inner || 0) * 100)}% in close, D ${Math.round(((PZ.D || {}).inner || 0) * 100)}%</span></div>
      <div class="smpl">${pl.filter((p, i) => i < 12 || good.includes(p)).map(srow).join('')}</div></details>` : '';
  const selNote = S ? `<div class="smsel"><b>${esc(sel.name)}</b>: last ${S.n} shots, <i class="smd-k g"></i>${S.g} goal${S.g === 1 ? '' : 's'} · average ${S.dist} ft out · ${S.ty.map(t => `${esc(tyl(t[0]))} ${Math.round(t[1] * 100)}%`).join(', ')} <button type="button" class="smx" data-smp="${esc(off.team)}:0">clear</button></div>` : '';
  return `<div class="smb"><h3>${esc(off.team)} attack <small>vs ${esc(def.team)} defense</small></h3>
    <div class="smchips"><span class="ladc">${esc(off.team)} ${O.f} shots/gm (${ordn(O.f_rk)})</span><span class="ladc">${Math.round(O.fi * 100)}% from in close (${ordn(O.fi_rk)})</span>
      <span class="ladc">${esc(def.team)} allows ${F.a}/gm (${ordn(F.a_rk)} most)</span><span class="ladc">${Math.round(F.ai * 100)}% in close (${ordn(F.ai_rk)} most)</span></div>
    ${selNote}${smSvg(vals, tipf, dots)}
    <ul class="smlist">${hot.map(x => `<li><b class="up">${pc(x.v)}</b> ${x.k}: ${esc(off.team)} goes there and ${esc(def.team)} gives it up</li>`).join('')}${cold.map(x => `<li><b class="dn">${pc(x.v)}</b> ${x.k}: little room for ${esc(off.team)}</li>`).join('')}${!hot.length && !cold.length ? '<li class="muted">No area stands out.</li>' : ''}</ul>
    ${gz ? `<div class="smgl"><span class="small muted">${esc(def.goalie)} save %:</span>${gz}</div>` : ''}${shooters}</div>`;
}
function shotMapPanel(g){
  if (!D.shotmap) return '<section class="panel"><div class="empty">No shot maps on this page yet.</div></section>';
  const seg = `<div class="seg" role="group" aria-label="Map view">${[['ov', 'Overlay'], ['off', 'Where they shoot'], ['def', 'Where it’s allowed']].map(([k, l]) =>
    `<button data-smode="${k}" aria-pressed="${state.smode === k}">${l}</button>`).join('')}</div>`;
  const n = (D.shotmap.teams[g.home.team] || {}).n || 30;
  return `<section class="panel"><div class="phead"><h2>${esc(g.away.team)} at ${esc(g.home.team)}: shot map</h2>${seg}</div>
    <div class="smkey"><span><i style="background:rgba(255,99,71,.8)"></i>more than average</span><span><i style="background:rgba(70,150,255,.6)"></i>less</span><span class="muted">net at the top · last ${n} games · faded where few shots happen · tap a square</span></div>
    <div class="smw">${smBlock(g.away, g.home)}${smBlock(g.home, g.away)}</div>
    <p class="note">Overlay = where this team shoots more than average times where the other team allows more than average (unblocked shots, empty nets left out). Goalie save % by zone is from the last two seasons. Display only: in our 2024-26 tests neither the overlay nor zone save % predicted goals beyond what the model already uses (it rates every shot by location through expected goals, and goalies by goals saved above expected), so the maps don’t change any number on the page.</p></section>`;
}
const ulam = p => -Math.log(1 - Math.min(p.p, .999)) * (p.um ?? 1);
// value play: the best anytime-goal price at the viewer's books beats our chance by 5%+ (not a low-trust pick)
function isValue(p){
  const b = window.MKT && MKT.propBest ? MKT.propBest(p.gid, p.name, 'goal') : null;
  if (!b || !b.mine || b.w) return false;
  const d = b.o > 0 ? 1 + b.o / 100 : 1 + 100 / -b.o;
  return p.p * d - 1 >= .05;
}
function board(){
  const rows = D.games.flatMap(g => [[g.away, g.home], [g.home, g.away]].flatMap(([s, o]) => s.players.map(p => ({...p, orig: p, team: s.team, opp: o.team, gid: g.id}))))
    .filter(p => !state.bval || isValue(p))
    .sort((a,b) => state.bsort === 'pf' ? (b.pf ?? 0) - (a.pf ?? 0) : state.bsort === 'u' ? ulam(b) - ulam(a) : b.p - a.p);
  const seg = `<div class="phead"><div class="seg" role="group" aria-label="Sort by"><button data-bsort="p" aria-pressed="${state.bsort !== 'pf'}">Anytime goal</button><button data-bsort="pf" aria-pressed="${state.bsort === 'pf'}">First goal</button><button data-bsort="u" aria-pressed="${state.bsort === 'u'}" title="${UTIP}">Usage rank</button></div><div class="seg" role="group" aria-label="Filter"><button data-bval="1" aria-pressed="${!!state.bval}" title="Only players whose best anytime-goal price at your books beats our chance by 5%+ (top-3 usage players; same rule as the green prices)">Value only</button></div><span class="small muted">First goal = chance to score the game's first goal (regulation or overtime)</span></div>`;
  const bv = p => state.bsort === 'pf' ? (p.pf ?? 0) : p.p, bmax = state.bsort === 'pf' ? .12 : .5;
  return `<section class="panel">${seg}<div class="tw"><table class="t pt"><thead><tr><th>#</th><th class="l">Player</th><th class="l">Team</th><th class="l">Pos</th><th class="l" style="min-width:140px">Chance</th><th>P(goal)</th><th>Fair</th><th>1st goal</th><th>Fair</th><th>Exp. goals</th><th title="${UTIP}">Usage rank</th><th>TOI</th><th class="l">H2H G-A-P</th><th class="l pcd" title="Best anytime-goal price at your books; green = our chance beats it by 5%+">Best odds</th></tr></thead><tbody>
    ${rows.map((p,i) => `<tr class="${rowCls(p.orig)}"><td class="muted">${i+1}</td><td class="l name">${pickMark(p.orig)}${esc(p.name)}${nhlSlip(p.orig, p.team)}${p.pp ? `<span class="pp">${p.pp}</span>` : ''}<span class="mmeta">${esc(p.team)} vs ${esc(p.opp)} · ${esc(p.pos)}${p.ur ? ` · usage #${p.ur}${(() => { const m = MRANK[p.gid + '|' + p.id], d = m ? m - p.ur : 0; return d ? ` ${d > 0 ? '▲' : '▼'}${Math.abs(d)}` : ''; })()}` : ''}${p.pf != null ? ` · 1st goal ${pct(p.pf)}` : ''}</span>${window.MKT && MKT.prop ? `<span class="pcm">${MKT.prop(p.gid, p.name, 'goal')}</span>` : ''}${mtag(p.pos, p.opp)}${whyLine(p.orig)}</td><td class="l">${esc(p.team)} <span class="small muted">vs ${esc(p.opp)}</span></td><td class="l"><span class="pos">${esc(p.pos)}</span></td><td class="l">${bar(bv(p), bmax)}</td><td class="${state.bsort === 'pf' ? '' : 'big'} kp">${pct(p.p)}${p.sog != null ? `<small class="kps">${p.sog.toFixed(1)} shots</small>` : ''}</td><td>${odds(p.fair)}</td><td class="${state.bsort === 'pf' ? 'big' : ''}">${p.pf != null ? pct(p.pf) : '–'}</td><td>${p.fair_first != null ? odds(p.fair_first) : '–'}</td><td>${p.lam.toFixed(2)}</td><td>${urCell(p.orig, p.gid)}</td><td class="muted">${p.toi}</td><td class="l">${h2hCell(p.h2h, p.opp)}</td><td class="l pcd">${window.MKT && MKT.prop ? MKT.prop(p.gid, p.name, 'goal') : ''}</td></tr>`).join('')}${!rows.length ? `<tr><td colspan="14" class="l muted">${state.bval ? 'No value plays at your books right now (prices appear once books post tonight’s anytime-goal odds; choose your books under any game’s bets).' : ''}</td></tr>` : ''}
    </tbody></table></div></section>`;
}
function posdef(){
  const rows = (D.posdef || []).slice();
  const val = (r, k) => k === 'team' ? r.team : k.split('.').reduce((o, x) => o[x], r);
  rows.sort((a, b) => { const x = val(a, state.sort), y = val(b, state.sort); return (x < y ? -1 : x > y ? 1 : 0) * state.dir; });
  const today = new Set(D.games.flatMap(g => [g.away.team, g.home.team]));
  const hm = v => `hm ${hshade(v)}`;
  const th = (k, label) => `<th class="sort" data-sort="${k}" aria-sort="${state.sort === k ? (state.dir > 0 ? 'ascending' : 'descending') : 'none'}">${label}</th>`;
  const P = ['C','L','R','D'];
  return `<section class="panel"><div class="phead"><h2>Defense vs position</h2><span class="small muted">Click a column to sort · bold teams play today</span></div>
  <div class="tw"><table class="t"><thead>
  <tr><th></th><th class="gs" colspan="4">xG allowed, even strength</th><th class="gs" colspan="4">xG allowed, penalty kill</th><th class="gs" colspan="4">Goals allowed</th><th></th></tr>
  <tr><th class="l sort" data-sort="team">Team</th>${['ev','pk','ga'].map(s => P.map(p => th(p + '.' + s, PNAME[p])).join('')).join('')}<th>Games this season</th></tr></thead><tbody>
  ${rows.map(r => `<tr><td class="l">${today.has(r.team) ? `<b>${esc(r.team)}</b>` : esc(r.team)}</td>${['ev','pk','ga'].map(s => P.map(p => `<td class="${hm(r[p][s])}">${rel(r[p][s])}</td>`).join('')).join('')}<td class="muted">${r.games}</td></tr>`).join('')}
  </tbody></table></div>
  <p class="small muted">How much each defense allows to opposing centers, left wings, right wings and defensemen, compared with the average team (+15% = 15% more). Shooter positions are the NHL's roster listings. Recent games count most, carried over from last season early on, and small samples are pulled toward average. Expected goals (xG) is the steadier measure; actual goals are noisier. Green = soft spot for that position, red = tough. On the Games tab, players facing a 10%+ soft or tough even-strength matchup get a tag. This is context, not part of the probabilities: in backtests (2024–25 and 2025–26) it added nothing beyond each opponent's overall defense, which the model already uses.</p></section>`;
}
// shots on goal (sog.py): every skater by expected shots, with the chance of 2+ / 3+ / 4+
function shotsView(){
  const rows = [];
  for (const g of D.games) for (const [me, op] of [[g.away, g.home], [g.home, g.away]]) for (const p of me.players)
    if (p.sog != null) rows.push({p, team: me.team, opp: op.team, gid: g.id, start: g.start});
  if (!rows.length) return '<section class="panel"><div class="empty">Shots on goal appear with tonight’s projections.</div></section>';
  rows.sort((a, b) => b.p.sog - a.p.sog);
  const t = state.shotsAll ? rows : rows.slice(0, 60);
  return `<section class="panel"><div class="phead"><h2>Shots on goal</h2><span class="small muted">expected shots and the chance of each line going over</span></div>
    <details class="how"${innerWidth > 640 ? ' open' : ''}><summary>How these work</summary><p class="note">Each skater's shots per 60 minutes (recent form counts a little; few games are pulled toward his position's average) times tonight's expected ice time, adjusted for how many shots the opponent allows, power-play time, home ice and back-to-backs. Shot counts spread out more than a simple average suggests, so the chances use a wider (negative binomial) curve. Checked on games the model never saw (2024-25 and 2025-26): over 1.5 / 2.5 / 3.5 shots were predicted 42.2 / 21.5 / 10.1% and happened 42.7 / 21.9 / 10.2%.</p></details>
    <div class="tw"><table class="t pt"><thead><tr><th class="l">Player</th><th>Expected</th><th>2+ (over 1.5)</th><th>3+ (over 2.5)</th><th>4+ (over 3.5)</th><th class="l">Game</th></tr></thead><tbody>
    ${t.map(r => `<tr><td class="l name">${esc(r.p.name)} <span class="small muted">${esc(r.team)} · ${esc(r.p.pos)}${r.p.pp ? ' · ' + esc(r.p.pp) : ''}</span>
      <span class="mmeta">2+ <b>${pct(r.p.so[1])}</b> · 3+ <b>${pct(r.p.so[2])}</b> · 4+ <b>${pct(r.p.so[3])}</b> · vs ${esc(r.opp)}</span></td>
      <td class="big kp">${r.p.sog.toFixed(1)}</td><td>${pct(r.p.so[1])}</td><td>${pct(r.p.so[2])}</td><td>${pct(r.p.so[3])}</td><td class="l small muted">vs ${esc(r.opp)} · ${esc(r.start)}</td></tr>`).join('')}
    </tbody></table></div>${rows.length > 60 && !state.shotsAll ? `<p><button type="button" class="bkset" data-shotsall="1">Show all ${rows.length} skaters</button></p>` : ''}</section>`;
}
function goaliesView(){
  const rows = (D.goalies || []).slice().sort((a, b) => ((b.now || b.last || {}).gsaa ?? -99) - ((a.now || a.last || {}).gsaa ?? -99));
  const c = x => x ? `<td>${x.gp}</td><td>${x.sa}</td><td>${x.ga}</td><td class="big">${sv3(x.sv)}</td><td class="big">${sgn(x.gsaa)}</td><td>${sgn(x.gsax)}</td>` : '<td colspan="6" class="muted small">no games</td>';
  return `<section class="panel"><div class="phead"><h2>Tonight's starting goalies</h2><span class="small muted">league save % ${sv3(D.lg_sv)}</span></div>
  <div class="tw"><table class="t"><thead><tr><th class="l"></th><th class="l"></th><th class="gs" colspan="6">This season</th><th class="gs" colspan="6">Last season</th></tr>
  <tr><th class="l">Goalie</th><th class="l">Team</th>${['GP','SA','GA','SV%','GSAA','GSAx'].map(h => `<th>${h}</th>`).join('').repeat(2)}</tr></thead><tbody>
  ${rows.map(r => `<tr><td class="l name">${esc(r.name)}</td><td class="l">${esc(r.team)}</td>${c(r.now)}${c(r.last)}</tr>`).join('')}</tbody></table></div>
  <p class="small muted">SV% = saves ÷ shots on goal. GSAA = goals saved above average: goals a league-average save % would have allowed on his shots, minus what he allowed (+ is good). GSAx = goals saved above expected: same idea using our shot-quality (expected goals) model, so it credits goalies who face harder shots. Empty-net goals excluded; regular season and playoffs. The goal model rates goalies on GSAx (recent games weighted, pulled toward average); in backtests a save-%/GSAA rating was no better (2024–25) or slightly worse (2025–26).</p></section>`;
}
function render(){
  for (const b of document.querySelectorAll('.tabs button')) b.setAttribute('aria-selected', b.dataset.view === state.view);
  document.getElementById('main').innerHTML = state.view === 'board' ? board() : state.view === 'posdef' ? posdef() : state.view === 'goalies' ? goaliesView() : state.view === 'shots' ? shotsView() : state.view === 'plays' ? playsView()
    : state.view === 'books' ? (window.MKT && MKT.oddsView ? MKT.oddsView(D.games.map(g => ({id: g.id, label: `${g.away.team} @ ${g.home.team}`, away: g.away.team, home: g.home.team}))) : '<div class="empty">Loading prices…</div>')
 : games();
}
document.addEventListener('click', e => {
  const b = e.target.closest('button[data-view]');
  if (b){ state.view = b.dataset.view; history.replaceState(null, '', state.view === 'games' ? location.pathname : '#' + state.view); render(); return; }
  const gb = e.target.closest('[data-game]');
  if (gb){ state.game = +gb.dataset.game; history.replaceState(null, '', '#g' + state.game); render(); return; }
  const gt = e.target.closest('[data-gtab]');
  if (gt){ state.gtab = gt.dataset.gtab; saveUI(); render(); return; }
  const sp = e.target.closest('[data-smp]');
  if (sp){ const [t, id] = sp.dataset.smp.split(':'); state.smp = state.smp || {}; state.smp[t] = state.smp[t] === +id || +id === 0 ? null : +id; render(); return; }
  const sm = e.target.closest('[data-smode]');
  if (sm){ state.smode = sm.dataset.smode; saveUI(); render(); return; }
  if (e.target.closest('[data-shotsall]')){ state.shotsAll = true; render(); return; }
  if (e.target.closest('details.pfold > summary')){
    const d = e.target.closest('details.pfold');
    setTimeout(() => { FOLD[d.dataset.fold] = !d.open; try { localStorage.setItem('nhl-plays-fold', JSON.stringify(FOLD)); } catch (e2) {} }, 0);
    return;
  }
  if (e.target.closest('[data-bval]')){ state.bval = !state.bval; render(); return; }
  const bs = e.target.closest('button[data-bsort]');
  if (bs){ state.bsort = bs.dataset.bsort; render(); return; }
  const h = e.target.closest('th[data-sort]');
  if (h){ const k = h.dataset.sort; state.dir = state.sort === k ? -state.dir : (k === 'team' ? 1 : -1); state.sort = k; render(); }
});
render();
const tip = document.getElementById('tip');
function showTip(el, x, y){ tip.innerHTML = el.dataset.tip; tip.hidden = false; const r = tip.getBoundingClientRect();
  tip.style.left = Math.min(Math.max(8, x - r.width/2), innerWidth - r.width - 8) + 'px'; tip.style.top = (y - r.height - 12 < 8 ? y + 18 : y - r.height - 12) + 'px'; }
document.addEventListener('pointermove', e => { const el = e.target.closest('[data-tip]'); if (el) showTip(el, e.clientX, e.clientY); else tip.hidden = true; });
document.addEventListener('focusin', e => { const el = e.target.closest('[data-tip]'); if (el){ const r = el.getBoundingClientRect(); showTip(el, r.left + r.width/2, r.top); } });
