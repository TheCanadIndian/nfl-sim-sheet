/* Live prediction-market prices and over/under liquidity inside the model pages.
   Kalshi: markets/live_<sport>.json, refreshed by the cloud every ~15 min before games (Kalshi
   blocks browser requests). Polymarket: order books fetched live from the browser.
   Pages call MKT.player(...), MKT.game(...), MKT.cell(...) while rendering; when new prices
   arrive this script calls the page's render() again. */
(function(){
  const me = document.currentScript, sport = me.dataset.sport, base = new URL('.', me.src).href;
  const FEE = {kalshi: .07, polymarket: 0};
  const KLAB = {anytime_td: 'TD', first_td: 'first TD', rec_yds: 'rec yds', rush_yds: 'rush yds', pass_yds: 'pass yds', rec: 'receptions',
    goal: 'goal', first_goal: 'first goal', hr: 'home run', pts: 'points', reb: 'rebounds', ast: 'assists', fg3m: 'threes', win: 'winner', total: 'total', spread: 'spread'};
  const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
  const norm = s => String(s || '').normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase().replace(/[^a-z ]/g, '').replace(/\b(jr|sr|ii|iii|iv)\b/g, '').replace(/\s+/g, ' ').trim();
  const cents = p => p == null ? '–' : Math.round(p * 100) + '¢';
  const pc = p => p == null ? '–' : (p < .1 && p > 0 ? (p * 100).toFixed(1) : Math.round(p * 100)) + '%';
  const usd = v => v == null ? '–' : v >= 1e6 ? '$' + (v / 1e6).toFixed(1) + 'M' : v >= 1000 ? '$' + (v / 1000).toFixed(v >= 10000 ? 0 : 1) + 'k' : '$' + Math.round(v);
  const shade = e => e >= .08 ? 'g3' : e >= .05 ? 'g2' : e >= .02 ? 'g1' : '';
  let D = null, stamp = null, timer = null;
  let BK = null, FL = null, PR = null;            // PR: every player's prices, markets/props_<sport>.json (Unabated)                        // FL: value tags, markets/flags_<sport>.json (sharp.py: our inference only)                                   // sportsbook game lines: markets/books_<sport>.json (The Odds API, books.py)
  const BKS = {hardrockbet: 'Hard Rock', novig: 'Novig', draftkings: 'DK', fanduel: 'FD', betmgm: 'MGM', williamhill_us: 'Caesars', espnbet: 'ESPN', betrivers: 'BetRivers', fliff: 'Fliff', ballybet: 'Bally'};
  const FULL = {DK: 'DraftKings', FD: 'FanDuel', MGM: 'BetMGM', ESPN: 'ESPN BET', Bally: 'Bally Bet'};
  const ORDER = ['Novig', 'Kalshi', 'Hard Rock', 'Polymarket', 'Poly US', 'ProphetX', 'DK', 'FD', 'MGM', 'Caesars', 'Fanatics', 'theScore', 'ESPN BET', 'BetRivers', 'Fliff', 'Bally Bet', 'betPARX'];
  let MY = [], bkOpen = null;
  const OV = {g: 'all', m: 'all', q: ''};          // Book odds tab filters                      // the viewer's sportsbooks (this device), [] = all books
  try { MY = JSON.parse(localStorage.getItem('simbooks-v1') || '[]') || []; } catch (e) {}
  const mine = k => !MY.length || MY.includes(k);
  const SKIP = new Set(['kalshi']);               // The Odds API's Kalshi quote has no fee (Unabated's 'Kalshi' is fee-adjusted)               // quoted without Kalshi's fee here; Kalshi (fee included) is in the prediction-market box
  const dec = o => o > 0 ? 1 + o / 100 : 1 + 100 / -o;
  const am = o => (o > 0 ? '+' : '') + o;
  const polyAt = {}, openD = new Set();            // which 'All lines' lists are open (kept across redraws)
  const det = key => `<details data-mkd="${esc(key)}"${openD.has(key) ? ' open' : ''}>`;

  const css = document.createElement('style');
  css.textContent = `
.mkx{container-type:inline-size;text-align:left;display:grid;gap:6px;margin-top:10px;padding:10px 12px;border:1px solid var(--faint);border-radius:8px;background:var(--surface)}
.mkx-h{display:flex;flex-wrap:wrap;justify-content:space-between;gap:4px 12px;align-items:baseline}
.mkx-h .t{font-size:11px;letter-spacing:.08em;text-transform:uppercase;font-weight:600;color:var(--muted)}
.mkx-h small{color:var(--muted);font-size:11.5px}
.mkx-r{display:grid;grid-template-columns:minmax(0,1.3fr) minmax(0,1.2fr) minmax(170px,1.3fr);gap:4px 14px;align-items:center;padding:6px 0;border-top:1px solid var(--faint)}
.mkx-r.on{background:color-mix(in srgb,var(--sel) 7%,transparent);border-radius:6px;padding-inline:6px}
.mkx-l{min-width:0;font-size:13.5px}
.mkx-l b{font-weight:600}
.mkx-l small,.mkx-p small{color:var(--muted);font-size:11.5px}
.mkx-src{display:inline-block;width:16px;height:16px;line-height:16px;text-align:center;border-radius:4px;font-size:10px;font-weight:700;margin-right:6px;background:var(--sunk);color:var(--muted);vertical-align:1px}
.mkx-p{font-size:13px;color:var(--muted)}
.mkx-p b{color:var(--ink);font-weight:600}
.mkx-p .e{font-weight:700}
.mkx-warn{color:var(--r1);font-size:11px;font-weight:600}
.mkx-thin{display:inline-block;margin-left:4px;padding:0 5px;border-radius:9px;border:1px solid var(--sel);color:var(--sel);font-size:10.5px;font-weight:600;line-height:15px}
.mkx-liq{display:grid;grid-template-columns:auto 1fr auto;gap:8px;align-items:center;font-size:12px}
.mkx-liq span{white-space:nowrap;color:var(--muted)}
.mkx-liq span b{color:var(--ink);font-weight:600}
.mkx-bar{display:flex;height:7px;border-radius:4px;overflow:hidden;background:var(--sunk);min-width:50px}
.mkx-bar i:first-child{background:var(--sel)} .mkx-bar i:last-child{background:var(--whisker)}
.mkx-set{margin-left:6px;font-size:11px;padding:1px 7px;border-radius:9px;border:1px solid var(--faint);background:none;color:var(--muted);cursor:pointer}
.mkx-set:hover{color:var(--ink);border-color:var(--sel)}
.mkx details summary{cursor:pointer;color:var(--muted);font-size:12.5px;padding-top:4px}
.mkx-pat{display:inline-block;margin-left:5px;padding:0 6px;border-radius:9px;font-size:10.5px;font-weight:700;line-height:16px;white-space:nowrap;cursor:help}
.mkx-pat.up{color:var(--g2);border:1px solid color-mix(in srgb,var(--g2) 55%,transparent)}
.mkx-pat.down{color:var(--r1);border:1px solid color-mix(in srgb,var(--r1) 55%,transparent)}
.mkx-pb{display:grid;gap:6px;padding:8px 10px;margin:2px 0 4px;border-radius:6px;background:var(--sunk)}
.mkx-pr{display:grid;grid-template-columns:auto 1fr;gap:8px;align-items:start;font-size:13px}
.mkx-pr small{color:var(--muted);font-size:11.5px}
.mkx-tip{font-size:12.5px;color:var(--muted)}
.mkx-nof{display:inline-block;margin-left:4px;padding:0 6px;border-radius:9px;font-size:10.5px;font-weight:800;background:#6cb4ff;color:#0a0f12;cursor:help}
.mkx-crowd{display:inline-block;margin-left:4px;padding:0 6px;border-radius:9px;font-size:10.5px;font-weight:700;border:1px solid #e5534b;color:#e5534b;cursor:help}
.mkx-c{display:grid;gap:1px;line-height:1.2;white-space:nowrap}
.mkx-c small{color:var(--muted);font-size:11px}
.bet{flex-wrap:wrap}
.bet .bk{flex-basis:100%;contain:inline-size;font-style:normal;font-size:11.5px;color:var(--muted);text-align:left;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.bet .bk b{font:inherit;font-weight:600;color:var(--ink)}
.bet .bk.v{color:var(--good)} .bet .bk.v b{color:var(--good)}
.bet .bk.nm,.bet .bk.nm b{color:var(--muted)}
a.pc{white-space:nowrap;color:var(--ink);text-decoration:none;font-size:13px;border-bottom:1px dotted color-mix(in srgb,currentColor 40%,transparent)}
a.pc b{font-weight:700} a.pc:hover{color:var(--accent)}
a.pc.v,a.pc.v b{color:var(--good)} a.pc i{font-style:normal;font-weight:700;font-size:11.5px}
a.pc.nm,a.pc.nm b{color:var(--muted)}
.pcm{display:none}
.ovc{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-bottom:6px}
.ovc select,.ovc input{background:var(--surface);border:1px solid var(--faint);border-radius:6px;padding:6px 10px;color:var(--ink);font:inherit;font-size:13.5px}
.ovc input{flex:1;min-width:150px}
.ovm{display:flex;gap:6px;overflow-x:auto;scrollbar-width:none;max-width:100%}
.ovm::-webkit-scrollbar{display:none}
.ovm button{flex:0 0 auto;white-space:nowrap;border:1px solid var(--faint);background:none;border-radius:999px;padding:5px 11px;cursor:pointer;font:inherit;font-size:13px;color:var(--ink)}
.ovm button[aria-pressed="true"]{background:var(--ink);color:var(--bg);border-color:var(--ink)}
.ovt td,.ovt th{white-space:nowrap}
.ovt th .gl{display:block;font-size:9.5px;letter-spacing:.06em;color:var(--accent);text-transform:uppercase;margin-bottom:2px;text-align:left}
.ovt .g0{border-left:1px solid var(--faint)}
.ovt td{text-align:right}
.ovt .name{position:sticky;left:0;z-index:1;background:var(--surface);min-width:150px;white-space:normal}
.ovt thead th:first-child{position:sticky;left:0;z-index:2;background:var(--surface)}
.ovt .name b{display:block;font-weight:600}.ovt .name small,.ovt td small{display:block;color:var(--muted);font-size:11.5px}
.ovt td a{color:inherit;text-decoration:none}
.ovt td.bst{font-weight:800;text-decoration:underline;text-decoration-color:var(--accent);text-decoration-thickness:2px;text-underline-offset:4px}
.ovt td.v,.ovt td.v a{color:var(--good);font-weight:700}
.ovt th.my,.ovt td.my{background:color-mix(in srgb,var(--accent) 6%,transparent)}
@media (max-width:640px){ .ovt .name{min-width:118px;max-width:140px} .ovt td,.ovt th{padding:6px 7px;font-size:12.5px} }
@media (max-width:640px){ .pcd{display:none} .pcm{display:block;margin-top:2px;font-weight:400} .pcm a.pc{font-size:12.5px} }
.bkset{border:1px solid var(--faint);border-radius:999px;padding:0 8px;background:none;color:var(--ink);font:inherit;font-size:12px;cursor:pointer;line-height:18px}
.bkset:hover,.bkset[aria-expanded="true"]{border-color:var(--accent)}
.bkpanel{display:flex;flex-wrap:wrap;gap:6px;margin:8px 0 2px}
.bkpanel button{border:1px solid var(--faint);border-radius:999px;padding:4px 10px;background:none;color:var(--ink);font:inherit;font-size:12.5px;cursor:pointer}
.bkpanel button[aria-pressed="true"]{border-color:var(--accent);color:var(--accent);background:rgba(54,211,153,.10)}
.bkpanel .bkall{color:var(--muted)}
.bet .bk.go{cursor:pointer;text-decoration:underline;text-decoration-color:color-mix(in srgb,currentColor 35%,transparent);text-underline-offset:2px}
.bet .bk.go:hover{color:var(--ink)}
.vtag b{font-weight:800}
a.vtag{text-decoration:none} a.vtag:hover{background:color-mix(in srgb,var(--good) 12%,transparent)}
.vtag{display:inline-block;margin-left:6px;padding:0 7px;border-radius:9px;border:1px solid color-mix(in srgb,var(--good) 60%,transparent);color:var(--good);font-size:10.5px;font-weight:700;line-height:16px;white-space:nowrap;cursor:help;vertical-align:1px}
@container (max-width:640px){ .mkx-r{grid-template-columns:minmax(0,1fr)} .mkx-p{font-size:12.5px} }`;
  document.head.appendChild(css);

  function sides(r){                                       // [YES label, NO label]
    const L = r.l, k = r.k;
    if (k === 'win'){ const t = (r.ti || '').replace(/\s*\(.*\)$/, '').replace(/ wins?$/i, ''); return [t + ' wins', t + ' loses']; }
    if (k === 'total'){ const u = /^under/i.test(r.o || ''); return u ? ['Under ' + L, 'Over ' + L] : ['Over ' + L, 'Under ' + L]; }
    if (k === 'spread') return [r.ti || 'Covers', 'Doesn\'t cover'];
    if (k === 'anytime_td') return ['Scores a TD', 'No TD'];
    if (k === 'first_td') return ['Scores first TD', 'Doesn\'t'];
    if (k === 'first_goal') return ['Scores first goal', 'Doesn\'t'];
    if (k === 'goal') return L != null && L >= 1 ? [Math.ceil(L) + '+ goals', 'Fewer'] : ['Scores a goal', 'No goal'];
    if (k === 'hr') return L != null && L >= 1 ? [Math.ceil(L) + '+ HR', 'Fewer'] : ['Homers', 'No HR'];
    return ['Over ' + L, 'Under ' + L];
  }
  function ours(r, model){ const p = r.p || {}; return p[model] ?? p.vegas ?? p.model ?? null; }
  // market types where our flagged edges have lost 10%+ on settled games (markets.losing_kinds): edges still shown, muted + tagged
  const lost = k => (D && D.losing && D.losing[k]) || null;
  const lostTip = k => { const L = lost(k); return L ? `Our 3%+ edges on this market type have gone ${L.bets} bets at ${Math.round(L.roi * 100)}% ROI on settled games, so treat edges here with caution: the market price has been the better number.` : ''; };
  function edges(r, p){
    const f = FEE[r.s] || 0, ya = r.ya, na = r.yb != null ? 1 - r.yb : null;
    return {y: p != null && ya > 0 && ya < 1 ? p - ya - f * ya * (1 - ya) : null, n: p != null && na > 0 && na < 1 ? (1 - p) - na - f * na * (1 - na) : null};
  }
  const spr = r => r.ya != null && r.yb != null && r.yb > 0 ? r.ya - r.yb : null;
  const isThin = r => { const s = spr(r); return s != null && s >= .015; };

  function poly(r){                                        // live Polymarket book (CORS-open)
    const now = Date.now();
    if (r.s !== 'polymarket' || (polyAt[r.t] && now - polyAt[r.t] < 90000)) return;
    polyAt[r.t] = now;
    fetch('https://clob.polymarket.com/book?token_id=' + encodeURIComponent(r.t)).then(x => x.ok ? x.json() : null).then(b => {
      if (!b) return;
      const bids = (b.bids || []).map(x => [+x.price, +x.size]).sort((a, c) => c[0] - a[0]);
      const asks = (b.asks || []).map(x => [+x.price, +x.size]).sort((a, c) => a[0] - c[0]);
      const bb = bids.length ? bids[0][0] : null, ba = asks.length ? asks[0][0] : null;
      const by = Math.round(asks.filter(x => x[0] <= ba + .03 + 1e-9).reduce((t, x) => t + x[0] * x[1], 0));
      const bn = Math.round(bids.filter(x => x[0] >= bb - .03 - 1e-9).reduce((t, x) => t + (1 - x[0]) * x[1], 0));
      if (r.yb === bb && r.ya === ba && r.by === by && r.bn === bn) return;    // nothing moved: don't redraw
      Object.assign(r, {yb: bb, ya: ba, by, bn, live: true});
      rerender();
    }).catch(() => {});
  }
  let rr = null;
  function rerender(){ clearTimeout(rr); rr = setTimeout(() => { try { window.render && window.render(); } catch (e) {} }, 250); }

  // flow badges (Kalshi pre-game taker flow, r.fl: +1 all money bought YES, -1 all NO). Backtests Sep-Oct 2026:
  // VALUE (YES edge) with NO-heavy/mixed flow beat VALUE with YES-heavy flow in MLB HR, NFL yards and NHL goals;
  // crowded YES (80%+ of $ buying YES) ran overpriced on NFL anytime TDs (-2.8 pts) and NHL goals (-1.7).
  function flowChips(r, yesEdge){
    if (r.fl == null) return '';
    let s = '';
    if (yesEdge != null && yesEdge >= .03 && r.fl <= -.2) s += `<span class="mkx-nof" title="Pre-game money is NO-heavy or mixed (${Math.round((1 + r.fl) / 2 * 100)}% of $ bought YES) while we see YES value. Experimental: in Sep-Oct 2026 backtests VALUE spots like this beat YES-heavy ones in MLB, NFL yards and NHL (small samples).">NO flow</span>`;
    if ((r.k === 'anytime_td' || r.k === 'goal') && r.fl >= .6) s += `<span class="mkx-crowd" title="${Math.round((1 + r.fl) / 2 * 100)}% of pre-game $ bought YES. Crowded YES TD / goal markets ran overpriced in Sep-Oct 2026 (NFL TD -2.8 pts, NHL -1.7); following that crowd lost 14-17%.">crowded YES</span>`;
    return s;
  }
  function row(r, model, focus, slider){
    poly(r);
    const [yl, nl] = sides(r), p = ours(r, model), e = edges(r, p);
    const best = (e.y ?? -9) >= (e.n ?? -9) ? {v: e.y, lab: yl} : {v: e.n, lab: nl};
    const weak = (D && D.weak && D.weak.includes(r.k)) || !!lost(r.k);
    const by = r.by ?? null, bn = r.bn ?? null, tot = (by || 0) + (bn || 0);
    const setBtn = slider && r.l != null && !['win', 'spread', 'anytime_td', 'first_td', 'first_goal'].includes(r.k) ? `<button class="mkx-set" data-mline="${r.l}" title="Move the slider to this line">use line</button>` : '';
    return `<div class="mkx-r${focus ? ' on' : ''}">
      <div class="mkx-l"><span class="mkx-src" title="${r.s === 'kalshi' ? 'Kalshi' : 'Polymarket (live)'}">${r.s === 'kalshi' ? 'K' : 'P'}</span><b>${esc(yl)}</b> <small>${esc(KLAB[r.k] || r.k)}</small>${isThin(r) ? `<span class="mkx-thin" title="Bid/ask gap ${Math.round(spr(r) * 100)}¢: a less-traded market">thin ${Math.round(spr(r) * 100)}¢</span>` : ''}${(r.pat || []).map(id => pchip(id)).join('')}${flowChips(r, e.y)}${setBtn}</div>
      <div class="mkx-p">Price <b>${cents(r.ya)}</b> · ours <b>${pc(p)}</b>${best.v != null && best.v >= .02 ? ` · <span class="e ${weak ? '' : shade(best.v)}">+${(best.v * 100).toFixed(1)}% ${esc(best.lab)}</span>${lost(r.k) ? ` <span class="mkx-warn" title="${esc(lostTip(r.k))}">losing record</span>` : weak ? ' <span class="mkx-warn" title="On settled games of this type the market has been more accurate than our model">market ahead</span>' : ''}` : ' · <small>no edge</small>'}</div>
      <div class="mkx-liq" title="Dollars you could spend within 3¢ of the best price on each side"><span>${esc(yl.length > 14 ? 'Yes' : yl)} <b>${usd(by)}</b></span><div class="mkx-bar">${tot > 0 ? `<i style="width:${by / tot * 100}%"></i><i style="width:${bn / tot * 100}%"></i>` : ''}</div><span><b>${usd(bn)}</b> ${esc(nl.length > 14 ? 'No' : nl)}</span></div>
    </div>`;
  }
  function head(title){
    const k = D && D.fetched ? new Date(D.fetched) : null;
    const t = k && !isNaN(k) ? k.toLocaleTimeString([], {hour: 'numeric', minute: '2-digit'}) : '';
    return `<div class="mkx-h"><span class="t">${esc(title)}</span><small>Kalshi as of ${t || '–'} · Polymarket live · bar = money to buy each side within 3¢</small></div>`;
  }
  const same = (a, b) => String(a) === String(b);
  const forGame = gid => (D ? D.rows.filter(r => same(r.g, gid)) : []);
  const forPlayer = (gid, pid, name) => forGame(gid).filter(r => r.n && ((pid != null && r.pid != null && same(r.pid, pid)) || norm(r.n) === norm(name)));
  const order = (a, b) => a.k.localeCompare(b.k) || (a.l ?? 0) - (b.l ?? 0) || a.s.localeCompare(b.s);

  // prop patterns (patterns.py): traits that props which hit had in common, with a tracked record
  const PSIGN = id => (D && D.patterns && D.patterns[id] ? D.patterns[id].sign : 1);
  const pct0 = v => v == null ? '–' : Math.round(v * 100) + '%';
  function precord(P){
    const f = P.found || {}, t = P.tracking || {};
    const since = P.since ? new Date(P.since + 'T12:00:00').toLocaleDateString([], {month: 'short', day: 'numeric'}) : '';
    return `found in weeks 1-3: hit ${pct0(f.hit)} vs ${pct0(f.priced)} priced (${f.player_games || 0} player-games)` +
      ` · since ${since}: ${t.n ? `hit ${pct0(t.hit)} vs ${pct0(t.priced)} (${t.player_games})` : 'nothing settled yet'}`;
  }
  function pchip(id, extra){
    const P = D && D.patterns && D.patterns[id]; if (!P) return '';
    return `<span class="mkx-pat pat-${esc(id)} ${P.sign > 0 ? 'up' : 'down'}" title="${esc(P.label)}: ${esc(P.desc)} ${esc(precord(P))}${extra ? ' · ' + esc(extra) : ''}">◆ ${esc(P.short)}${P.sign > 0 ? '' : ' ▼'}</span>`;
  }
  function playerPats(gid, pid, name){
    const by = {};
    for (const r of forPlayer(gid, pid, name)) for (const id of (r.pat || [])) (by[id] = by[id] || []).push(r);
    return by;
  }
  function best(r, model){ const [yl, nl] = sides(r), e = edges(r, ours(r, model)); return (e.y ?? -9) >= (e.n ?? -9) ? {v: e.y, lab: yl, side: 'yes'} : {v: e.n, lab: nl, side: 'no'}; }
  function patBlock(gid, pid, name, focusKinds){
    const by = playerPats(gid, pid, name), ids = Object.keys(by);
    const T = (D && D.timing) || {}, f = (focusKinds || [])[0];
    const tip = f ? (T[f] || (['rec', 'rec_yds', 'rush_yds', 'pass_yds'].includes(f) ? T.props : '')) : '';
    if (!ids.length && !tip) return '';
    const line = id => { const P = D.patterns[id], rs = by[id], lines = rs.map(r => (r.k === 'anytime_td' ? 'TD' : (KLAB[r.k] || r.k) + ' ' + Math.ceil(r.l))).slice(0, 4);
      return `<div class="mkx-pr">${pchip(id)}<span><b>${esc(P.label)}</b> <small>${esc(lines.join(', '))}${rs.length > 4 ? ' …' : ''}</small><br><small>${esc(precord(P))}</small></span></div>`; };
    return `<div class="mkx-pb"><div class="mkx-h"><span class="t">Patterns in props that hit</span><small>${P_NOTE}</small></div>${ids.map(line).join('')}${tip ? `<div class="mkx-tip">⏱ ${esc(tip)}</div>` : ''}</div>`;
  }
  const P_NOTE = 'early leads from 3 weeks: the "since" record shows whether they keep hitting';
  window.MKT = {
    // best sportsbook price for one game-line option, as a small second line inside the page's bet chip.
    // kind: 'ml' (side home|away), 'spread' (line = HOME team's handicap, side home|away), 'total' (side over|under)
    bk(gid, kind, side, line, p){
      const G = BK && BK.games && BK.games[String(gid)]; if (!G) return '';
      let o = G[kind]; if (kind !== 'ml') o = o && o[String(+line)]; o = o && o[side];
      // a price is [odds, Gambly bet link] (Unabated) or plain odds (The Odds API)
      const all = o ? Object.entries(o).filter(([k]) => !SKIP.has(k)).map(([k, v]) => [k, Array.isArray(v) ? v[0] : v, Array.isArray(v) ? v[1] : null])
        .sort((a, b) => dec(b[1]) - dec(a[1]) || (!!b[2] - !!a[2])) : []; if (!all.length) return '';
      // the viewer's books (saved per device); if none of them prices this bet, show the best elsewhere, greyed
      const L = all.filter(([k]) => mine(k)), other = !L.length;
      const [bk, price, url] = (other ? all : L)[0], ev = p == null ? null : p * dec(price) - 1, mk = {ml: 'win', spread: 'spread', total: 'total'}[kind];
      const ahead = !!(D && ((D.weak && D.weak.includes(mk)) || lost(mk)));      // markets have out-predicted us on this bet type so far
      const v = ev != null && ev >= .03 && !ahead && !other, va = ev != null && ev >= .03 && (ahead || other);
      const list = all.map(([k, x]) => `${FULL[k] || BKS[k] || k} ${am(x)}`).join(' · ');
      const when = BK.fetched ? new Date(BK.fetched).toLocaleTimeString([], {hour: 'numeric', minute: '2-digit'}) : '';
      const tip = `${url ? `Tap to open this bet at ${esc(FULL[bk] || bk)}. ` : ''}${other ? 'None of your books prices this; best elsewhere. ' : MY.length ? 'Best price at your books. ' : ''}All books (as of ${esc(when)}): ${esc(list)}${ev != null ? ` · our edge at this price ${ev >= 0 ? '+' : ''}${(ev * 100).toFixed(1)}%` : ''}${va && ahead ? ' · not highlighted: so far the market has been more accurate than our model on this bet type' : ''}`;
      return `<em class="bk${v ? ' v' : ''}${url ? ' go' : ''}${other ? ' nm' : ''}"${url ? ` data-go="${esc(url)}" role="link" tabindex="0"` : ''} title="${tip}"><b>${am(price)}</b> ${esc(BKS[bk] || bk)}${(v || va) ? ` · +${(ev * 100).toFixed(0)}%` : ''}${url ? ' ↗' : ''}</em>`;
    },
    bkFetched: () => (BK ? BK.fetched : null),
    // 'Book odds' tab: every game line and player prop with each book's price side by side.
    // games: [{id, label}] in page order. Filters (game / market / player search) work on the DOM, so typing keeps focus.
    oddsView(games){
      const KL = {goal: 'Anytime goal', td: 'Anytime TD', rec: 'Receptions', rec_yds: 'Receiving yds', rush_yds: 'Rushing yds', pass_yds: 'Passing yds'};
      const fresh = x => x && x.fetched && Date.now() - new Date(x.fetched) < 12 * 3600e3;
      const rows = [], used = new Set();
      const link = (v, partner) => { const u = Array.isArray(v) ? v[1] : null; if (!u) return null;
        return u.startsWith('http') ? u : `https://www.gambly.com/deeplink/odds/${u}?utm_campaign=${encodeURIComponent(partner || '')}`; };
      const add = (r, prices, partner) => { const P = {};
        for (const [k, v] of Object.entries(prices || {})){ if (SKIP.has(k)) continue; const o = Array.isArray(v) ? v[0] : v; if (o == null) continue; P[k] = {o, u: link(v, partner)}; used.add(k); }
        if (Object.keys(P).length) rows.push({...r, P}); };
      for (const g of games){
        const G = fresh(BK) && BK.games && BK.games[String(g.id)];
        if (G){
          const main = o => Object.entries(o || {}).sort((a, b) => Object.values(b[1]).reduce((n, s) => n + Object.keys(s).length, 0) - Object.values(a[1]).reduce((n, s) => n + Object.keys(s).length, 0))[0];
          for (const sd of ['away', 'home']) add({gid: g.id, m: 'lines', o: 0, who: (g[sd] || sd), what: 'Moneyline'}, (G.ml || {})[sd]);
          const sp = main(G.spread); if (sp) for (const sd of ['away', 'home']){ const L = sd === 'home' ? +sp[0] : -sp[0];
            add({gid: g.id, m: 'lines', o: 1, who: (g[sd] || sd), what: `Spread ${L > 0 ? '+' : L < 0 ? '−' : ''}${Math.abs(L)}`}, sp[1][sd]); }
          const tt = main(G.total); if (tt) for (const sd of ['over', 'under']) add({gid: g.id, m: 'lines', o: 2, who: sd === 'over' ? 'Over' : 'Under', what: `Total ${tt[0]}`}, tt[1][sd]);
        }
        const X = fresh(PR) && PR.props && PR.props[String(g.id)];
        for (const [nm, kinds] of Object.entries(X || {})) for (const [k, e] of Object.entries(kinds)){
          const name = nm.replace(/\b\w/g, c => c.toUpperCase());
          if (e.l == null) add({gid: g.id, m: k, o: 10, who: name, what: KL[k] || k, p: e.p, w: e.w}, e.o, PR.partner);
          else { add({gid: g.id, m: k, o: 10, who: name, what: `${KL[k] || k} o${e.l}`, p: e.p, w: e.w}, e.o, PR.partner);
                 add({gid: g.id, m: k, o: 10.5, who: name, what: `${KL[k] || k} u${e.l}`, p: e.p == null ? null : 1 - e.p, w: e.w}, e.u, PR.partner); }
        }
      }
      if (!rows.length) return `<section class="panel"><div class="phead"><h2>Book odds</h2></div><p class="note">No sportsbook prices yet for these games. Game lines appear about a day before each game; player props when Unabated's props feed is up (it retries every 15 minutes).</p></section>`;
      const PMG = ['Kalshi', 'Polymarket', 'Poly US', 'Novig', 'ProphetX'];
      const ord = (a, b) => (ORDER.indexOf(a) + 99) % 99 - (ORDER.indexOf(b) + 99) % 99 || a.localeCompare(b);
      const groups = [['My books', MY.filter(k => used.has(k))], ['Prediction markets & exchanges', PMG.filter(k => used.has(k) && !MY.includes(k))],
                      ['Sportsbooks', [...used].filter(k => !PMG.includes(k) && !MY.includes(k)).sort(ord)]].filter(g => g[1].length);
      const cols = groups.flatMap(g => g[1]), first = new Set(groups.map(g => g[1][0]));
      const mkts = [...new Set(rows.map(r => r.m))], mlab = m => m === 'lines' ? 'Game lines' : KL[m] || m;
      const glab = Object.fromEntries(games.map(g => [String(g.id), g.label]));
      const show = r => (OV.g === 'all' || String(r.gid) === OV.g) && (OV.m === 'all' || r.m === OV.m) && (!OV.q || norm(r.who).includes(norm(OV.q)));
      const amS = o => (o > 0 ? '+' : '') + o;
      const fairAm = q => q >= .5 ? -Math.round(100 * q / (1 - q)) : Math.round(100 * (1 - q) / q);
      let body = '', last = null;
      rows.sort((a, b) => games.findIndex(g => String(g.id) === String(a.gid)) - games.findIndex(g => String(g.id) === String(b.gid)) || a.o - b.o || (a.m < b.m ? -1 : a.m > b.m ? 1 : 0) || (b.p ?? 0) - (a.p ?? 0));
      for (const r of rows){
        if (r.gid !== last){ last = r.gid; body += `<tr class="grp" data-ovg="${esc(String(r.gid))}"${OV.g !== 'all' && OV.g !== String(r.gid) ? ' hidden' : ''}><td colspan="${cols.length + 2}">${esc(glab[String(r.gid)] || r.gid)}</td></tr>`; }
        const best = Math.max(...Object.values(r.P).map(x => dec(x.o)));
        const cells = cols.map(k => { const x = r.P[k]; if (!x) return `<td class="muted${MY.includes(k) ? ' my' : ''}${first.has(k) ? ' g0' : ''}">–</td>`;
          const ev = r.p == null ? null : r.p * dec(x.o) - 1, v = ev != null && !r.w && ev >= ((FL && FL.min_ev) || .05);
          const t = `${FULL[k] || k} ${amS(x.o)}${ev != null ? ` · our edge ${ev >= 0 ? '+' : ''}${(ev * 100).toFixed(1)}%` : ''}${x.u ? ' · tap to open this bet' : ''}`;
          const inner = `${amS(x.o)}`, cls = `${dec(x.o) === best ? 'bst' : ''}${v ? ' v' : ''}${MY.includes(k) ? ' my' : ''}${first.has(k) ? ' g0' : ''}`;
          return `<td class="${cls}" title="${esc(t)}">${x.u ? `<a href="${esc(x.u)}" target="_blank" rel="noopener">${inner}</a>` : inner}</td>`; }).join('');
        const fair = r.p == null ? '<td class="muted">–</td>' : `<td title="Our chance ${(r.p * 100).toFixed(1)}%${r.w ? ' (low trust: outside our top 3 or likely news)' : ''}">${amS(fairAm(r.p))}<small>${(r.p * 100).toFixed(0)}%</small></td>`;
        body += `<tr data-ovr data-ovg="${esc(String(r.gid))}" data-ovm="${esc(r.m)}" data-ovn="${esc(norm(r.who))}"${show(r) ? '' : ' hidden'}><td class="l name"><b>${esc(r.who)}</b><small>${esc(r.what)}</small></td>${fair}${cells}</tr>`;
      }
      const t = x => x && x.fetched ? new Date(x.fetched).toLocaleTimeString([], {hour: 'numeric', minute: '2-digit'}) : null;
      const when = [t(fresh(BK) && BK) && `game lines ${t(BK)}`, t(fresh(PR) && PR) && `props ${t(PR)}`].filter(Boolean).join(' · ');
      return `<section class="panel ov"><div class="phead"><h2>Book odds</h2><span class="small muted">${esc(when)}</span></div>
        <div class="ovc"><select id="ov-g" aria-label="Game"><option value="all">All games</option>${games.filter(g => rows.some(r => String(r.gid) === String(g.id))).map(g => `<option value="${esc(String(g.id))}"${OV.g === String(g.id) ? ' selected' : ''}>${esc(g.label)}</option>`).join('')}</select>
        <div class="ovm" role="group" aria-label="Market">${['all', ...mkts].map(m => `<button type="button" data-ovm-pick="${esc(m)}" aria-pressed="${OV.m === m}">${m === 'all' ? 'All' : esc(mlab(m))}</button>`).join('')}</div>
        <input id="ov-q" type="search" placeholder="Player or team" value="${esc(OV.q)}" aria-label="Search players"></div>
        <p class="small muted">Best price in each row is underlined; <span style="color:var(--good)">green</span> = our chance beats it by 5%+. Tap a price to open the bet. Kalshi includes its fee. Hard Rock props: NHL anytime goal and NFL anytime TD, added close to each game. Your books: ${MY.length ? esc(MY.map(k => FULL[k] || k).join(', ')) : 'none chosen'} <button type="button" class="bkset" data-bkset="ov" aria-expanded="${bkOpen === 'ov'}">change ▾</button></p>
        ${bkOpen === 'ov' ? `<div class="bkpanel">${[...used].sort(ord).map(k => `<button type="button" data-bkpick="${esc(k)}" aria-pressed="${MY.includes(k)}">${esc(FULL[k] || k)}</button>`).join('')}<button type="button" data-bkpick="" class="bkall">All books</button></div>` : ''}
        <div class="tw ovw"><table class="t ovt"><thead><tr><th class="l">Bet</th><th>Ours</th>${groups.map(([lab, ks]) => ks.map((k, i) =>
          `<th class="${MY.includes(k) ? 'my' : ''}${i === 0 ? ' g0' : ''}">${i === 0 ? `<small class="gl">${esc(lab)}</small>` : '<small class="gl">&nbsp;</small>'}${esc(BKS[k] || k)}</th>`).join('')).join('')}</tr></thead><tbody>${body.replace(/<td class="([^"]*)" title/g, (m, c) => m)}</tbody></table></div></section>`;
    },
    hasProps: gid => !!(PR && PR.props && PR.props[String(gid)] && Date.now() - new Date(PR.fetched) < 12 * 3600e3),
    // one player's best price at the viewer's books (all books if none chosen) for a prop, as a compact link.
    // kind: 'goal' | 'td' (yes side) or 'rec' / 'rec_yds' / 'rush_yds' / 'pass_yds' (main line: whichever side we rate better)
    prop(gid, name, kind){
      if (!PR || !PR.props || Date.now() - new Date(PR.fetched) > 12 * 3600e3) return '<span class="muted">–</span>';
      const e = ((PR.props[String(gid)] || {})[norm(name)] || {})[kind]; if (!e) return '<span class="muted">–</span>';
      const pick = side => { const all = Object.entries(e[side] || {}).sort((a, b) => dec(b[1][0]) - dec(a[1][0]));
        const L = all.filter(([k]) => mine(k)); return L.length ? [L[0], false] : all.length ? [all[0], true] : null; };
      const opts = (e.u ? ['o', 'u'] : ['o']).map(sd => { const c = pick(sd); if (!c) return null;
        const p = e.p == null ? null : sd === 'o' ? e.p : 1 - e.p; return {sd, k: c[0][0], o: c[0][1][0], path: c[0][1][1], other: c[1], ev: p == null ? null : p * dec(c[0][1][0]) - 1}; })
        .filter(Boolean).sort((a, b) => (a.other - b.other) || ((b.ev ?? -9) - (a.ev ?? -9)));
      const c = opts[0]; if (!c) return '<span class="muted">–</span>';
      const url = `https://www.gambly.com/deeplink/odds/${c.path}?utm_campaign=${encodeURIComponent(PR.partner || '')}`;
      const v = !c.other && !e.w && c.ev != null && c.ev >= (FL && FL.min_ev || .05);
      const lab = e.l != null ? `${c.sd === 'o' ? 'O' : 'U'} ${e.l} ` : '';
      const tip = `${c.other ? 'None of your books has this; best elsewhere. ' : ''}${lab ? (c.sd === 'o' ? 'Over ' : 'Under ') + e.l + ': ' : ''}${esc(FULL[c.k] || c.k)} ${am(c.o)}${c.ev != null ? ` · our edge ${c.ev >= 0 ? '+' : ''}${(c.ev * 100).toFixed(1)}%` : ''}${e.w ? ' (not highlighted: outside our top 3 in this game, or far enough from the market to suggest news; those picks have lost)' : ''}. Tap to open this bet.`;
      return `<a class="pc${v ? ' v' : ''}${c.other ? ' nm' : ''}" href="${esc(url)}" target="_blank" rel="noopener" title="${tip}">${lab}<b>${am(c.o)}</b> ${esc(BKS[c.k] || c.k)}${v ? ` <i>+${(c.ev * 100).toFixed(0)}%</i>` : ''}</a>`;
    },
    // 'Value' tag next to a player: a sportsbook (one of the viewer's, if chosen) pays clearly more than our fair price
    flag(gid, name){
      if (!FL || !FL.tags || !FL.fetched || Date.now() - new Date(FL.fetched) > 12 * 3600e3) return '';
      const v = (FL.tags[String(gid)] || {})[norm(name)]; if (!v) return '';
      const b = typeof v === 'string' ? v : v.b, t = new Date(FL.fetched).toLocaleTimeString([], {hour: 'numeric', minute: '2-digit'});
      const min = FL.min_ev || .05;
      let o = typeof v === 'string' ? null : {k: v.k, o: v.o, u: v.u};
      if (o && MY.length && v.all && v.p != null){             // only the viewer's books: best of those, and only if it still clears the bar
        const c = Object.entries(v.all).filter(([k]) => mine(k)).map(([k, x]) => ({k, o: x[0], u: x[1]})).sort((x, y) => dec(y.o) - dec(x.o))[0];
        if (!c || v.p * dec(c.o) - 1 < min) return '';
        o = c;
      }
      const tip = `Value: ${o ? esc(FULL[o.k] || o.k) : 'at least one major sportsbook'} pays ${Math.round(min * 100)}%+ more than our fair price for ${esc(name)} ${esc(b)} (checked ${esc(t)}; skipped when we're far from the market, which usually means news). Not yet a proven edge: we're tracking how these do.`;
      if (o && o.u)                                          // price, book and a bet link
        return `<a class="vtag" href="${esc(o.u)}" target="_blank" rel="noopener" title="${tip} Tap to open this bet.">Value · ${esc(b)} <b>${o.o > 0 ? '+' : ''}${o.o}</b> ${esc(BKS[o.k] || o.k)} ↗</a>`;
      if (o && o.o != null)                                  // price without a link (e.g. Hard Rock)
        return `<span class="vtag" title="${tip}">Value · ${esc(b)} <b>${o.o > 0 ? '+' : ''}${o.o}</b> ${esc(BKS[o.k] || o.k)}</span>`;
      if (MY.length && typeof v === 'string') return '';     // no per-book prices to check against the viewer's books
      return `<span class="vtag" title="${tip}">Value · ${esc(b)}</span>`;
    },
    bkNote(gid){
      if (!BK || !BK.games || !BK.games[String(gid)]) return '';
      const t = BK.fetched ? new Date(BK.fetched).toLocaleString([], {weekday: 'short', hour: 'numeric', minute: '2-digit'}) : '';
      const names = [...new Set(Object.values(BK.games).flatMap(g => ['ml', 'spread', 'total'].flatMap(k => {
        const x = g[k] || {}; return k === 'ml' ? Object.values(x).flatMap(Object.keys) : Object.values(x).flatMap(sd => Object.values(sd).flatMap(Object.keys)); })))]
        .filter(k => !SKIP.has(k)).sort((a, b) => (ORDER.indexOf(a) + 99) % 99 - (ORDER.indexOf(b) + 99) % 99 || a.localeCompare(b));
      const lab = MY.length ? MY.map(k => FULL[k] || k).join(', ') : 'all books';
      const panel = bkOpen === String(gid) ? `<div class="bkpanel" role="group" aria-label="Your sportsbooks">${names.map(k => `<button type="button" data-bkpick="${esc(k)}" aria-pressed="${MY.includes(k)}">${esc(FULL[k] || k)}</button>`).join('')}<button type="button" data-bkpick="" class="bkall">All books</button></div>` : '';
      return `<div class="small muted bknote">Price under each option: best at <button type="button" class="bkset" data-bkset="${esc(String(gid))}" aria-expanded="${bkOpen === String(gid)}" title="Choose the sportsbooks you use (saved on this device)">${esc(lab)} ▾</button> (${esc(t)}); tap it to open that bet. <span style="color:var(--good)">Green</span> = our chance beats that price by 3%+ (a grey % means the market has out-predicted our model on that bet type so far).${panel}</div>`;
    },
    has: () => !!(D && D.rows.length),
    loaded: () => !!D,
    rows: () => (D ? D.rows : []),
    fetched: () => (D ? D.fetched : null),
    weak: k => !!(D && D.weak && D.weak.includes(k)),
    lost: k => !!lost(k), lostTip,
    row: (r, model, focus) => row(r, model, focus, false),
    best, thin: isThin,
    patChips(gid, pid, name){ if (!D || !D.patterns) return ''; return Object.keys(playerPats(gid, pid, name)).map(id => pchip(id)).join(''); },
    patterns: () => (D && D.patterns) || null, precord, spread: spr, usd, shade, label: k => KLAB[k] || k,
    player(gid, pid, name, focusKinds, model, slider, showPat){
      const rows = forPlayer(gid, pid, name).sort(order); if (!rows.length) return '';
      const f = new Set(focusKinds || []);
      // ladders get long (13 rush-yard rungs): show the rungs priced nearest 50/50, three for the stat
      // being viewed and one for each other stat; the rest one tap away
      const near = r => Math.abs((r.ya ?? .5) - .5), top = [], rest = [];
      for (const r of rows.slice().sort((a, b) => near(a) - near(b)))
        (top.filter(x => x.k === r.k).length < (f.has(r.k) ? 3 : 1) ? top : rest).push(r);
      const fo = (a, b) => (f.has(b.k) - f.has(a.k)) || order(a, b);
      top.sort(fo); rest.sort(fo);
      const pats = showPat ? patBlock(gid, pid, name, focusKinds) : '';
      return `<div class="mkx">${head('Prediction markets for ' + name)}${pats}${top.map(r => row(r, model, f.has(r.k), slider)).join('')}${rest.length ? `${det('p|' + gid + '|' + name)}<summary>All ${rows.length} lines for this player</summary>${rest.map(r => row(r, model, f.has(r.k), slider)).join('')}</details>` : ''}</div>`;
    },
    game(gid, model){
      // Polymarket moneyline / over-under left off the model pages (Kalshi covers them; PM spreads stay: Kalshi has none)
      const rows = forGame(gid).filter(r => ['win', 'total', 'spread'].includes(r.k) && !(r.s === 'polymarket' && r.k !== 'spread')).sort(order); if (!rows.length) return '';
      // headline: winners + the total/spread rungs priced nearest 50/50; the rest one tap away
      const key = r => r.k === 'win' ? 0 : Math.abs((r.ya ?? .5) - .5);
      const top = [], rest = [];
      for (const r of rows.slice().sort((a, b) => key(a) - key(b))) (r.k === 'win' || top.filter(x => x.k === r.k && x.s === r.s).length < 1 ? top : rest).push(r);
      top.sort(order); rest.sort(order);
      return `<div class="mkx">${head('Prediction markets')}${top.map(r => row(r, model)).join('')}${rest.length ? `${det('g|' + gid)}<summary>All ${rows.length} game lines</summary>${rest.map(r => row(r, model)).join('')}</details>` : ''}</div>`;
    },
    cell(gid, pid, name, kind, model){                        // compact table cell: price · edge · money each side
      const r = forPlayer(gid, pid, name).filter(x => x.k === kind && (x.l == null || x.l < 1)).sort((a, b) => (a.s === 'kalshi' ? -1 : 1))[0];
      if (!r) return '<span class="muted">–</span>';
      poly(r);
      const p = ours(r, model), e = edges(r, p), v = Math.max(e.y ?? -9, e.n ?? -9), side = (e.y ?? -9) >= (e.n ?? -9) ? 'yes' : 'no';
      const weak = (D.weak && D.weak.includes(r.k)) || !!lost(r.k);
      const fc = flowChips(r, e.y);
      return `<span class="mkx-c" title="${r.s === 'kalshi' ? 'Kalshi' : 'Polymarket'}: price ${cents(r.ya)} · money to buy YES ${usd(r.by)} / NO ${usd(r.bn)} within 3¢${weak ? ' · market has been more accurate on this type so far' : ''}"><span><b>${cents(r.ya)}</b>${v >= .02 ? ` <span class="${weak ? 'muted' : shade(v)}" style="font-weight:700">+${(v * 100).toFixed(0)}% ${side}</span>` : ''}${lost(r.k) && v >= .02 ? ` <small class="muted" title="${esc(lostTip(r.k))}">losing record</small>` : ''}${isThin(r) ? '<span class="mkx-thin">thin</span>' : ''}${fc}</span><small>${usd(r.by)} yes · ${usd(r.bn)} no</small></span>`;
    },
  };

  // price links inside the bet chips: open the book's bet slip instead of adding the chip to the parlay
  const go = e => { const t = e.target.closest && e.target.closest('[data-go]'); if (!t) return false;
    e.preventDefault(); e.stopPropagation(); window.open(t.dataset.go, '_blank', 'noopener'); return true; };
  document.addEventListener('click', go, true);
  // Book odds filters: hide/show rows in place (no redraw, so the search box keeps focus)
  const ovApply = () => {
    for (const tr of document.querySelectorAll('tr[data-ovr]'))
      tr.hidden = !((OV.g === 'all' || tr.dataset.ovg === OV.g) && (OV.m === 'all' || tr.dataset.ovm === OV.m) && (!OV.q || tr.dataset.ovn.includes(norm(OV.q))));
    for (const tr of document.querySelectorAll('tr.grp[data-ovg]'))
      tr.hidden = !document.querySelector(`tr[data-ovr][data-ovg="${CSS.escape(tr.dataset.ovg)}"]:not([hidden])`);
    for (const b of document.querySelectorAll('[data-ovm-pick]')) b.setAttribute('aria-pressed', b.dataset.ovmPick === OV.m);
  };
  document.addEventListener('input', e => { if (e.target.id === 'ov-q'){ OV.q = e.target.value; ovApply(); } });
  document.addEventListener('change', e => { if (e.target.id === 'ov-g'){ OV.g = e.target.value; ovApply(); } });
  document.addEventListener('click', e => { const b = e.target.closest && e.target.closest('[data-ovm-pick]'); if (b){ OV.m = b.dataset.ovmPick; ovApply(); } });
  document.addEventListener('click', e => {                 // choose your sportsbooks (saved on this device)
    const st = e.target.closest && e.target.closest('[data-bkset]');
    if (st){ bkOpen = bkOpen === st.dataset.bkset ? null : st.dataset.bkset; rerender(); return; }
    const b = e.target.closest && e.target.closest('[data-bkpick]'); if (!b) return;
    const k = b.dataset.bkpick;
    MY = !k ? [] : MY.includes(k) ? MY.filter(x => x !== k) : [...MY, k];
    try { localStorage.setItem('simbooks-v1', JSON.stringify(MY)); } catch (e2) {}
    rerender();
  });
  document.addEventListener('keydown', e => { if (e.key === 'Enter') go(e); }, true);
  document.addEventListener('toggle', e => { const k = e.target.dataset && e.target.dataset.mkd; if (k) e.target.open ? openD.add(k) : openD.delete(k); }, true);
  document.addEventListener('click', e => {                 // "use line": move the page's slider to the market line
    const b = e.target.closest('button[data-mline]'); if (!b) return;
    const inp = document.getElementById('sl-in'); if (!inp) return;
    inp.value = Math.min(+inp.max, Math.max(+inp.min, +b.dataset.mline));
    inp.dispatchEvent(new Event('input', {bubbles: true}));
  });

  function load(){
    fetch(base + 'markets/live_' + sport + '.json?t=' + Date.now(), {cache: 'no-store'}).then(x => x.ok ? x.json() : null).then(d => {
      if (!d){ if (!D){ D = {rows: [], fetched: null}; rerender(); } return; }     // no file yet: say so instead of 'loading'
      if (d.fetched === stamp) return;
      stamp = d.fetched; D = d; rerender();
    }).catch(() => {});
  }
  let bstamp = null;
  function loadBooks(){
    fetch(base + 'markets/books_' + sport + '.json?t=' + Date.now(), {cache: 'no-store'}).then(x => x.ok ? x.json() : null).then(d => {
      if (!d || d.fetched === bstamp) return;
      bstamp = d.fetched; BK = d; rerender();
    }).catch(() => {});
    fetch(base + 'markets/props_' + sport + '.json?t=' + Date.now(), {cache: 'no-store'}).then(x => x.ok ? x.json() : null).then(d => {
      if (!d || (PR && d.fetched === PR.fetched)) return;
      PR = d; rerender();
    }).catch(() => {});
    fetch(base + 'markets/flags_' + sport + '.json?t=' + Date.now(), {cache: 'no-store'}).then(x => x.ok ? x.json() : null).then(d => {
      if (!d || (FL && d.fetched === FL.fetched)) return;
      FL = d; rerender();
    }).catch(() => {});
  }
  if (sport){ load(); loadBooks(); timer = setInterval(() => { load(); loadBooks(); }, 120000); }
})();
