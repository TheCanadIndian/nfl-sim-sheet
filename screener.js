/* Odds screener (owner 2026-10-22: "a screener for odds that is easy to use"). Every over price on the current slates
   (NFL, NBA, NHL; main and alt lines) at your books or all books, against the fair price, with the same rules as the
   sport pages (mkt.js):
     fair    main lines: sharp consensus without the book being bet (Pinnacle / Novig / ProphetX two-way no-vig, log-odds
             mean); no consensus: the books' two-way no-vig median; NHL anytime goals: the exchanges' median less 3%.
             alt lines: the consensus carried to that line (props file `pin`), else the books' two-way median (2+ books).
     chance  our number at the WPIN weight + the rest the fair (50/50 without a sharp price)
     edge    chance x the payout - 1; Kalshi prices are fee-adjusted; Pinnacle is a reference, never a price.
   Overs only (the owner plays overs). Value / LC / off-market chips come from flags_<sport>.json.
   Filters are remembered on this device (localStorage simscreen-v2). Page: screener.html (site.py write_screener). */
(() => {
  const root = (document.currentScript && document.currentScript.dataset.root) || '';
  const GAMES = window.SCREEN_GAMES || {};
  const SPORTS = ['nfl', 'nba', 'nhl'];
  const SP = {nfl: 'NFL', nba: 'NBA', nhl: 'NHL'};
  const OWNER = ['Novig', 'Kalshi', 'Hard Rock'];
  let MY = OWNER.slice();
  try { const sv = localStorage.getItem('simbooks-v1'); if (sv != null) MY = JSON.parse(sv) || []; } catch (e) {}
  if (!MY.length) MY = OWNER.slice();
  const WPIN = {nhl: {goal: .2}, nba: {pts: .05, reb: .05, ast: .05, fg3m: .05}, nfl: {rec: .25, rec_yds: .25, rush_yds: .25, pass_yds: .25, td: 0}};
  const LAB = {td: 'anytime TD', rec: 'receptions', rec_yds: 'rec yds', rush_yds: 'rush yds', pass_yds: 'pass yds', pts: 'points', reb: 'rebounds',
               ast: 'assists', fg3m: 'threes', goal: 'to score', sog05: 'shots', sog15: 'shots', sog25: 'shots', sog35: 'shots', sog45: 'shots'};
  const STAT = {td: 'TD', rec: 'Receptions', rec_yds: 'Rec yds', rush_yds: 'Rush yds', pass_yds: 'Pass yds', pts: 'Points', reb: 'Rebounds',
                ast: 'Assists', fg3m: 'Threes', goal: 'Goals', sog: 'Shots on goal'};
  const FULL = {DK: 'DraftKings', FD: 'FanDuel', MGM: 'BetMGM', 'Poly US': 'Polymarket'};
  const NOBET = new Set(['Pinnacle', 'kalshi']);
  const EXCH = ['Novig', 'Kalshi', 'Polymarket', 'Poly US', 'ProphetX'];
  const dec = o => o > 0 ? 1 + o / 100 : 1 + 100 / -o;
  const am = o => (o > 0 ? '+' : '') + Math.round(o);
  const lgt = p => Math.log(p / (1 - p));
  const pc = p => (p * 100).toFixed(0) + '%';
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  const norm = s => String(s || '').toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '').replace(/[.'’]/g, '').replace(/\s+(jr|sr|ii|iii|iv)$/, '').trim();
  const med = a => { a = a.slice().sort((x, y) => x - y); const n = a.length; return n ? (n % 2 ? a[(n - 1) / 2] : (a[n / 2 - 1] + a[n / 2]) / 2) : null; };
  const twoWay = (o, u) => med(Object.keys(o || {}).filter(k => !NOBET.has(k) && u && u[k] && o[k][0] != null && u[k][0] != null)
    .map(k => { const a = 1 / dec(o[k][0]), b = 1 / dec(u[k][0]); return a / (a + b); }));
  const sharpO = (pin, book) => { const P = pin.parts; if (!P) return pin.o;
    let xs = Object.entries(P).filter(([k]) => k !== book).map(([, v]) => v); if (!xs.length) xs = Object.values(P);
    return 1 / (1 + Math.exp(-xs.reduce((s, v) => s + lgt(v), 0) / xs.length)); };

  // ---------------------------------------------------------------- state (remembered per device)
  // alt lines off by default: their fair is carried from the main line (the least reliable fairs); one tap turns them on
  const DEF = {sport: 'all', books: 'mine', edge: 5, price: 'all', stat: 'all', q: '', tagged: false, pinmv: false, softm: false, soon: false, alts: false, sort: 'edge'};
  let S = {...DEF};
  try { Object.assign(S, JSON.parse(localStorage.getItem('simscreen-v2') || '{}')); } catch (e) {}
  const save = () => { try { localStorage.setItem('simscreen-v2', JSON.stringify(S)); } catch (e) {} };
  let ROWS = [], FETCHED = {}, SHOW = 60;

  // ---------------------------------------------------------------- data
  const link = (v, partner) => { const u = v && v[1]; if (!u) return null; if (String(u).startsWith('http')) return u;
    return partner === 'thecanadindian' ? `https://www.gambly.com/deeplink/odds/${u}?utm_campaign=${partner}` : null; };
  function build(sport, P, F){
    const out = [], G = GAMES[sport] || {}, now = Date.now();
    const tagsOf = (gid, nm) => { const t = []; const v = ((F.tags || {})[gid] || {})[nm], l = ((F.lc || {})[gid] || {})[nm];
      return {v: v && v.b, lc: l && l.b}; };
    const off = new Set((F.off || []).map(x => `${x.g}|${x.n}|${x.k}|${x.l}`));
    const pinm = {};                                                           // Pinnacle moves (sharp.pin_moves) per prop
    for (const m of F.moves || []) if (m.src === 'pin' && m.sig !== 'rung') { const c = pinm[`${m.g}|${m.n}|${m.k}`] = pinm[`${m.g}|${m.n}|${m.k}`] || {line: null, price: false};
      if (m.sig === 'line' || m.sig === 'both') c.line = `${m.from_l}→${m.to_l}`; if (m.sig === 'price' || m.sig === 'both') c.price = true; }
    for (const [gid, ps] of Object.entries(P.props || {})){
      const g = G[gid] || {}; const start = g.start ? new Date(g.start).getTime() : null;
      if (start && start <= now) continue;                                   // started: no pregame price
      for (const [nm, kinds] of Object.entries(ps)){
        const tg = tagsOf(gid, nm), who = (g.pl || {})[nm] || [], team = who[0] || '', disp = who[1] || nm.replace(/\b\w/g, c => c.toUpperCase()), cx = who[2] || null;
        for (const [k, e] of Object.entries(kinds)){
          if (!LAB[k] || !e) continue;
          const statKey = k.startsWith('sog') ? 'sog' : k;
          const lines = [{l: e.l ?? null, o: e.o, u: e.u, p: e.p, main: true, pin: e.pin && Math.abs((e.pin.l ?? .5) - (e.l ?? .5)) < 1e-9 ? e.pin : null}];
          for (const [L, a] of Object.entries(e.a || {})) if (+L !== +(e.l ?? -1)) lines.push({l: +L, o: a.o, u: a.u, p: a.p, main: false, apin: a.pin});
          for (const x of lines){
            if (x.p == null || !x.o) continue;
            const books = Object.entries(x.o).filter(([b, v]) => !NOBET.has(b) && v && v[0] != null);
            if (!books.length) continue;
            const bet = k === 'td' ? 'anytime TD' : k === 'goal' ? 'to score' : `over ${x.l} ${LAB[k]}`;
            const tagBet = k === 'td' ? 'TD' : k === 'goal' ? 'goal' : `over ${x.l} ${LAB[k]}`;
            const prices = books.map(([b, v]) => {
              let fair = null, w = .5, src = '';
              if (x.main && x.pin){ fair = sharpO(x.pin, b); w = (WPIN[sport] || {})[k] ?? .5; src = 'sharp consensus'; }
              else if (!x.main && x.apin != null){ fair = x.apin; src = 'sharp consensus, carried to this line'; }
              else { const tw = twoWay(x.o, x.u); if (tw != null && (x.main || Object.keys(x.u || {}).length >= 2)){ fair = tw; src = 'books’ no-vig median'; }
                else if (k === 'goal'){ const ex = books.filter(([bb]) => EXCH.includes(bb)).map(([, vv]) => 1 / dec(vv[0])); const m = med(ex); if (m != null){ fair = m / 1.03; src = 'exchanges, less 3%'; } } }
              const q = fair != null ? w * x.p + (1 - w) * fair : null;
              return {b, o: v[0], u: link(v, P.partner), fair, q, w, src, edge: q != null ? q * dec(v[0]) - 1 : null};
            });
            out.push({sport, gid, nm, name: disp, team, game: g.lab || gid, start, k, statKey, l: x.l, main: x.main, bet, p: x.p,
                      prices, value: tg.v === tagBet, lc: tg.lc === tagBet, pin: pinm[`${gid}|${nm}|${k}`] || null,
                      cx: cx ? [...(cx.c || []), ...((cx.s || {})[k] || [])] : [], soft: !!(cx && ((cx.s || {})[k] || []).some(x => x[1] === 'g')), off: off.has(`${gid}|${nm}|${k}|${x.l}`) || off.has(`${gid}|${nm}|${k}|${x.l ?? .5}`)});
          }
        }
      }
    }
    return out;
  }
  async function load(){
    const res = await Promise.all(SPORTS.map(async sp => {
      try {
        const [P, F] = await Promise.all(['props', 'flags'].map(f => fetch(`${root}markets/${f}_${sp}.json?t=${Date.now()}`, {cache: 'no-store'}).then(r => r.ok ? r.json() : {}).catch(() => ({}))));
        FETCHED[sp] = P.fetched || null;
        return P && P.props ? build(sp, P, F || {}) : [];
      } catch (e) { return []; }
    }));
    ROWS = res.flat();
    render();
  }

  // ---------------------------------------------------------------- filter + render
  const PRICE = {all: [-1e9, 1e9, 'All prices'], fav: [-1e9, -150, 'Favorites'], flip: [-150, 150, 'Coin flips'], plus: [150, 400, 'Plus money'], long: [400, 1e9, 'Longshots']};
  function view(){
    const q = norm(S.q), soon = Date.now() + 6 * 3600e3, mine = new Set(MY);
    const out = [];
    for (const r of ROWS){
      if (S.sport !== 'all' && r.sport !== S.sport) continue;
      if (S.stat !== 'all' && r.statKey !== S.stat) continue;
      if (!S.alts && !r.main) continue;
      if (S.soon && !(r.start && r.start <= soon)) continue;
      if (S.tagged && !(r.value || r.lc || r.off)) continue;
      if (S.pinmv && !r.pin) continue;
      if (S.softm && !r.soft) continue;
      if (q && !(norm(r.name).includes(q) || norm(r.team).includes(q) || norm(r.game).includes(q))) continue;
      const ps = r.prices.filter(x => S.books === 'all' || mine.has(x.b)).sort((a, b) => dec(b.o) - dec(a.o));
      if (!ps.length) continue;
      const best = ps[0], [lo, hi] = PRICE[S.price] || PRICE.all;
      if (best.o < lo || best.o >= hi) continue;
      if (S.edge > -99 && !(best.edge != null && best.edge * 100 >= S.edge)) continue;
      if (best.edge != null && best.edge > .6) continue;                     // 60%+ claimed edges are stale or broken quotes
      out.push({...r, best, n: ps.length});
    }
    const key = {edge: r => -(r.best.edge ?? -9), gap: r => -((r.best.q ?? 0) - 1 / dec(r.best.o)), start: r => r.start || 9e15, price: r => -r.best.o}[S.sort] || (r => 0);
    return out.sort((a, b) => key(a) - key(b));
  }
  const chip = (k, v, lab, on) => `<button class="sc-chip${on ? ' on' : ''}" data-k="${k}" data-v="${esc(v)}" aria-pressed="${on}">${lab}</button>`;
  function render(){
    const el = document.getElementById('screener'); if (!el) return;
    const V = view(), stats = [...new Set(ROWS.filter(r => S.sport === 'all' || r.sport === S.sport).map(r => r.statKey))];
    const when = Object.entries(FETCHED).filter(([, t]) => t).map(([s, t]) => `${SP[s]} ${new Date(t).toLocaleTimeString([], {hour: 'numeric', minute: '2-digit'})}`).join(' · ');
    const tm = t => t ? new Date(t).toLocaleString([], {weekday: 'short', hour: 'numeric', minute: '2-digit'}) : '';
    const row = r => { const b = r.best, ev = b.edge;
      const tags = [r.value ? '<span class="sc-tag v" title="Value tag on the sport page">Value</span>' : '', r.lc ? '<span class="sc-tag lc" title="LC tag: beats the liquidity-weighted consensus">LC</span>' : '',
                    r.off ? '<span class="sc-tag off" title="Off-market price: 8+ pts from the fair (often stale; check before betting)">Off</span>' : '',
                    r.pin ? `<span class="sc-tag pin" title="Pinnacle moved this prop${r.pin.line ? ': line up ' + r.pin.line : ''}${r.pin.price ? (r.pin.line ? ' and' : ':') + ' the over got shorter' : ''} (tracked, experimental)">PIN${r.pin.line ? ' ▲' : ''}${r.pin.price ? ' ▼odds' : ''}</span>` : '', !r.main ? '<span class="sc-tag alt">alt</span>' : ''].join('');
      const tip = `Fair ${b.fair != null ? pc(b.fair) + ' (' + b.src + ')' : 'none'} · ours ${pc(r.p)} · chance ${b.q != null ? pc(b.q) + ' (' + Math.round(b.w * 100) + '% ours)' : '—'} · price implies ${pc(1 / dec(b.o))}`;
      const priceHtml = `<b>${am(b.o)}</b> ${esc(FULL[b.b] || b.b)}${b.u ? ' ↗' : ''}`;
      return `<div class="sc-row"><div class="sc-who"><b>${esc(r.name)}</b> <span class="sc-m">${esc(SP[r.sport])} · ${esc(r.team ? r.team + ' · ' : '')}${esc(r.game)} · ${esc(tm(r.start))}</span>
          <div class="sc-bet">${esc(r.bet[0].toUpperCase() + r.bet.slice(1))} ${tags}</div>${r.cx.length ? `<div class="sc-cx">${r.cx.map(x => `<span class="${x[1] === 'g' ? 'g' : x[1] === 'b' ? 'b' : ''}">${esc(x[0])}</span>`).join('')}</div>` : ''}</div>
        <div class="sc-num" title="${esc(tip)}"><span class="sc-e ${ev == null ? '' : ev >= .05 ? 'good' : ev < 0 ? 'bad' : ''}">${ev == null ? 'no fair' : (ev >= 0 ? '+' : '−') + Math.abs(Math.round(ev * 100)) + '%'}</span>
          <span class="sc-s">chance ${b.q != null ? pc(b.q) : '—'} · fair ${b.fair != null ? pc(b.fair) : '—'}</span></div>
        ${b.u ? `<a class="sc-px" href="${esc(b.u)}" target="_blank" rel="noopener">${priceHtml}</a>` : `<span class="sc-px nl">${priceHtml}</span>`}</div>`; };
    el.innerHTML = `<div class="sc-bar">
        <div class="sc-grp">${chip('sport', 'all', 'All', S.sport === 'all')}${SPORTS.map(s => chip('sport', s, SP[s], S.sport === s)).join('')}</div>
        <div class="sc-grp">${chip('books', 'mine', 'My books', S.books === 'mine')}${chip('books', 'all', 'All books', S.books === 'all')}</div>
        <div class="sc-grp"><span class="sc-l">Edge</span>${[[-100, 'Any'], [0, '0%+'], [3, '3%+'], [5, '5%+'], [10, '10%+']].map(([v, l]) => chip('edge', v, l, +S.edge === v)).join('')}</div>
        <div class="sc-grp">${Object.entries(PRICE).map(([k, v]) => chip('price', k, v[2], S.price === k)).join('')}</div>
        <div class="sc-grp">${chip('tagged', 'x', 'Tagged only', S.tagged)}${chip('pinmv', 'x', 'Pinnacle moved', S.pinmv)}${chip('softm', 'x', 'Soft matchup', S.softm)}${chip('soon', 'x', 'Next 6 hours', S.soon)}${chip('alts', 'x', 'Alt lines', S.alts)}</div>
        <div class="sc-grp sc-in"><select id="sc-stat" aria-label="Stat"><option value="all">Every stat</option>${stats.map(s => `<option value="${s}"${S.stat === s ? ' selected' : ''}>${esc(STAT[s] || s)}</option>`).join('')}</select>
          <input id="sc-q" type="search" placeholder="Player, team or game" value="${esc(S.q)}" aria-label="Search">
          <select id="sc-sort" aria-label="Sort"><option value="edge"${S.sort === 'edge' ? ' selected' : ''}>Best edge</option><option value="gap"${S.sort === 'gap' ? ' selected' : ''}>Biggest gap</option><option value="start"${S.sort === 'start' ? ' selected' : ''}>Starting soonest</option><option value="price"${S.sort === 'price' ? ' selected' : ''}>Biggest payout</option></select>
          <button class="sc-reset" id="sc-reset">Reset</button></div></div>
      <p class="sc-count"><b>${V.length}</b> over price${V.length === 1 ? '' : 's'}${S.books === 'mine' ? ' at ' + esc(MY.join(', ')) : ' at every book'}${when ? ` · prices as of ${esc(when)}` : ''}</p>
      <div class="sc-list">${V.slice(0, SHOW).map(row).join('') || '<p class="sc-empty">Nothing matches. Try a lower edge or All books.</p>'}</div>
      ${V.length > SHOW ? `<button class="sc-more" id="sc-more">Show ${Math.min(60, V.length - SHOW)} more</button>` : ''}`;
  }
  document.addEventListener('click', e => {
    const c = e.target.closest('.sc-chip');
    if (c){ const k = c.dataset.k, v = c.dataset.v;
      if (k === 'tagged' || k === 'pinmv' || k === 'softm' || k === 'soon' || k === 'alts') S[k] = !S[k]; else if (k === 'edge') S.edge = +v; else S[k] = v;
      if (k === 'sport') S.stat = 'all';
      SHOW = 60; save(); render(); return; }
    if (e.target.id === 'sc-more'){ SHOW += 60; render(); return; }
    if (e.target.id === 'sc-reset'){ S = {...DEF}; SHOW = 60; save(); render(); }
  });
  document.addEventListener('change', e => {
    if (e.target.id === 'sc-stat'){ S.stat = e.target.value; save(); render(); }
    if (e.target.id === 'sc-sort'){ S.sort = e.target.value; save(); render(); }
  });
  let qt = null;
  document.addEventListener('input', e => { if (e.target.id !== 'sc-q') return; clearTimeout(qt);
    qt = setTimeout(() => { S.q = e.target.value; save(); const pos = e.target.selectionStart; render();
      const i = document.getElementById('sc-q'); if (i){ i.focus(); i.setSelectionRange(pos, pos); } }, 250); });
  load();
  setInterval(load, 5 * 60e3);                                                // prices refresh every ~15 min in the cloud
})();
