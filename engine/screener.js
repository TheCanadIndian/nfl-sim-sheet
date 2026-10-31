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
  const WPIN = {nhl: {goal: .2}, nba: {pts: .05, reb: .05, ast: .05, fg3m: .05, dd: .05, tdbl: .05}, nfl: {rec: .25, rec_yds: .25, rush_yds: .25, pass_yds: .25, td: 0}};
  const LAB = {td: 'anytime TD', rec: 'receptions', rec_yds: 'rec yds', rush_yds: 'rush yds', pass_yds: 'pass yds', pts: 'points', reb: 'rebounds',
               ast: 'assists', fg3m: 'threes', goal: 'to score', dd: 'double-double', tdbl: 'triple-double', sog05: 'shots', sog15: 'shots', sog25: 'shots', sog35: 'shots', sog45: 'shots'};
  const STAT = {td: 'TD', rec: 'Receptions', rec_yds: 'Rec yds', rush_yds: 'Rush yds', pass_yds: 'Pass yds', pts: 'Points', reb: 'Rebounds',
                ast: 'Assists', fg3m: 'Threes', goal: 'Goals', dd: 'Double-double', tdbl: 'Triple-double', sog: 'Shots on goal'};
  const YES = new Set(['dd', 'tdbl']);                                        // yes / no props without a line (NBA double / triple-double, sharp.oa_nba_ddtd)
  const FULL = {DK: 'DraftKings', FD: 'FanDuel', MGM: 'BetMGM', 'Poly US': 'Polymarket'};
  const NOBET = new Set(['Pinnacle', 'kalshi']);
  const EXCH = ['Novig', 'Kalshi', 'Polymarket', 'Poly US', 'ProphetX'];
  // NHL anytime goal without Pinnacle (2026-10-31, research/nhl_atg_fourbook.py): sharp.py four_book parts, minus the book being bet
  const f4O = (e, book) => { const P = Object.entries((e && e.f4 && e.f4.parts) || {}).filter(([bb]) => bb !== book);
    if (!P.length) return null; const z = P.reduce((s, [, p]) => s + Math.log(p / (1 - p)), 0) / P.length; return 1 / (1 + Math.exp(-z)); };
  const dec = o => o > 0 ? 1 + o / 100 : 1 + 100 / -o;
  const am = o => (o > 0 ? '+' : '') + Math.round(o);
  const lgt = p => Math.log(p / (1 - p));
  const pc = p => (p * 100).toFixed(0) + '%';
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  const norm = s => String(s || '').toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '').replace(/[.'’]/g, '').replace(/\s+(jr|sr|ii|iii|iv)$/, '').trim();
  const jn = s => String(s || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase().replace(/[^a-z ]/g, '').replace(/\b(jr|sr|ii|iii|iv)\b/g, '').replace(/\s+/g, ' ').trim();
  const sig = p => 1 / (1 + Math.exp(-p));
  const med = a => { a = a.slice().sort((x, y) => x - y); const n = a.length; return n ? (n % 2 ? a[(n - 1) / 2] : (a[n / 2 - 1] + a[n / 2]) / 2) : null; };
  const twoWay = (o, u) => med(Object.keys(o || {}).filter(k => !NOBET.has(k) && u && u[k] && o[k][0] != null && u[k][0] != null)
    .map(k => { const a = 1 / dec(o[k][0]), b = 1 / dec(u[k][0]); return a / (a + b); }));
  const sharpO = (pin, book) => { const P = pin.parts; if (!P) return pin.o;
    let xs = Object.entries(P).filter(([k]) => k !== book).map(([, v]) => v); if (!xs.length) xs = Object.values(P);
    return 1 / (1 + Math.exp(-xs.reduce((s, v) => s + lgt(v), 0) / xs.length)); };

  // ---------------------------------------------------------------- state (remembered per device)
  // alt lines on (owner 2026-10-25: "I feel like we are missing prices"): every rung shows; their fair is carried from
  // the main line (shown with ≈ where only our distribution carries it). Devices that had saved 'off' are switched on once.
  const DEF = {sport: 'all', books: 'mine', edge: 5, price: 'all', stat: 'all', q: '', tagged: false, pinmv: false, consmv: false, knee: false, open: false, path: false, offm: false, softm: false, soon: false, alts: true, sort: 'edge'};
  let S = {...DEF};
  try { Object.assign(S, JSON.parse(localStorage.getItem('simscreen-v2') || '{}')); } catch (e) {}
  if (!S.v3){ S.alts = true; S.v3 = 1; }
  const save = () => { try { localStorage.setItem('simscreen-v2', JSON.stringify(S)); } catch (e) {} };
  let ROWS = [], FETCHED = {}, SHOW = 60;
  // saved filter sets (owner 2026-10-27: "save specific filters for the screener"): named snapshots of every filter, this
  // browser only (localStorage simscreen-presets-v1); one tap applies, × deletes, the one matching the current filters is lit
  const FKEYS = Object.keys(DEF);
  let PRE = [], NAMING = false;
  try { PRE = JSON.parse(localStorage.getItem('simscreen-presets-v1') || '[]') || []; } catch (e) {}
  const savePre = () => { try { localStorage.setItem('simscreen-presets-v1', JSON.stringify(PRE)); } catch (e) {} };
  const pick = o => Object.fromEntries(FKEYS.map(k => [k, o[k] ?? DEF[k]]));
  const same = f => FKEYS.every(k => (f[k] ?? DEF[k]) === S[k]);
  function suggest(){                                                         // a default name from what's switched on
    const on = {tagged: 'Tagged', pinmv: 'PIN moved', consmv: 'CONS moved', knee: 'Knees', path: 'Path legs', offm: 'Off market', open: 'Openers', softm: 'Soft', soon: 'Next 6h'};
    const bits = [S.sport === 'all' ? '' : SP[S.sport], ...Object.keys(on).filter(k => S[k]).map(k => on[k]), S.edge > -99 ? S.edge + '%+' : '', S.stat !== 'all' ? (STAT[S.stat] || S.stat) : '', S.price !== 'all' ? PRICE[S.price][2] : ''].filter(Boolean);
    return bits.join(' · ').slice(0, 40) || 'My filters'; }

  // ---------------------------------------------------------------- data
  const link = (v, partner) => { const u = v && v[1]; if (!u) return null; if (String(u).startsWith('http')) return u;
    return partner === 'thecanadindian' ? `https://www.gambly.com/deeplink/odds/${u}?utm_campaign=${partner}` : null; };
  function build(sport, P, F, LV, PA){
    const out = [], G = GAMES[sport] || {}, now = Date.now();
    // Kalshi's prop ladders are only in live_<sport>.json (asks / bids), not the props file: every rung with a 4c-or-less
    // spread joins its prop's ladder at the ask plus Kalshi's 7% fee (research/kalshi_liquidity_rule.py: 4c+ spreads lost 38%)
    const KAL = {};
    for (const r of (LV && LV.rows) || []) if (r.s === 'kalshi' && LAB[r.k] && r.k !== 'td' && r.l != null && r.n && r.ya && r.yb != null && r.ya - r.yb <= .04 && r.ya > .01 && r.ya < .99){
      const q = r.ya + .07 * r.ya * (1 - r.ya), o = q >= .5 ? -100 * q / (1 - q) : 100 * (1 - q) / q;
      ((KAL[`${r.g}|${jn(r.n)}|${r.k}`] = KAL[`${r.g}|${jn(r.n)}|${r.k}`] || {})[+r.l] = {o: Math.round(o), p: r.p && r.p.vegas != null ? r.p.vegas : null}); }
    const props = JSON.parse(JSON.stringify(P.props || {}));
    for (const key of Object.keys(KAL)){ const [g, nm, k] = key.split('|');            // Kalshi-only players / stats join too
      ((props[g] = props[g] || {})[nm] = props[g][nm] || {})[k] = props[g][nm][k] || {o: null, a: {}}; }
    const tagsOf = (gid, nm) => { const t = []; const v = ((F.tags || {})[gid] || {})[nm], l = ((F.lc || {})[gid] || {})[nm];
      return {v: v && v.b, lc: l && l.b}; };
    const off = new Set((F.off || []).map(x => `${x.g}|${x.n}|${x.k}|${x.l}`));
    const knee = {};                                                          // ladder knees (sharp.ladder_knees) per rung
    for (const x of F.knees || []) knee[`${x.g}|${x.n}|${x.k}|${x.l}`] = x;
    const opn = {};                                                           // early-value openers (oddshist.py), kept until the start
    for (const x of F.openers || []) opn[`${x.g}|${x.n}|${x.k}|${x.l}`] = x;
    const pth = {};                                                           // potential Path legs (the sport page's path rules, frozen ~15 min)
    for (const x of (PA && PA.legs) || []) pth[`${x.gid}|${x.name}|${x.kind}|${x.line}`] = x;
    const pinm = {}, consm = {};                                               // Pinnacle / consensus moves (sharp.pin_moves) per prop
    for (const m of F.moves || []) if ((m.src === 'pin' || m.src === 'cons') && m.sig !== 'rung') { const M_ = m.src === 'pin' ? pinm : consm;
      const c = M_[`${m.g}|${m.n}|${m.k}`] = M_[`${m.g}|${m.n}|${m.k}`] || {line: null, price: false};
      if (m.sig === 'line' || m.sig === 'both') c.line = `${m.from_l}→${m.to_l}`; if (m.sig === 'price' || m.sig === 'both') c.price = true; }
    for (const [gid, ps] of Object.entries(props)){
      const g = G[gid] || {}; const start = g.start ? new Date(g.start).getTime() : null;
      if (start && start <= now) continue;                                   // started: no pregame price
      for (const [nm, kinds] of Object.entries(ps)){
        const tg = tagsOf(gid, nm), who = (g.pl || {})[nm] || [], team = who[0] || '', disp = who[1] || nm.replace(/\b\w/g, c => c.toUpperCase()), cx = who[2] || null;
        for (const [k, e] of Object.entries(kinds)){
          if (!LAB[k] || !e) continue;
          const statKey = k.startsWith('sog') ? 'sog' : k;
          const lines = e.o ? [{l: e.l ?? null, o: e.o, u: e.u, p: e.p, main: true, f4: e.f4, pin: e.pin && Math.abs((e.pin.l ?? .5) - (e.l ?? .5)) < 1e-9 ? e.pin : null}] : [];
          for (const [L, a] of Object.entries(e.a || {})) if (+L !== +(e.l ?? -1)) lines.push({l: +L, o: a.o, u: a.u, p: a.p, main: false, apin: a.pin});
          // Kalshi rungs: on the matching line beside the books, else a rung of its own; its fair = the line's carried
          // consensus, else the main line's fair carried by our distribution (log-odds gap, shown with ≈)
          const mainFair = e.pin && e.pin.o != null && e.l != null && Math.abs((e.pin.l ?? .5) - e.l) < 1e-9 ? e.pin.o : null;
          for (const [L, kx] of Object.entries(KAL[`${gid}|${nm}|${k}`] || {})){
            const x = lines.find(y => y.l != null && Math.abs(+y.l - +L) < 1e-9);
            if (x){ if (!(x.o || {}).Kalshi){ x.o = {...(x.o || {}), Kalshi: [kx.o, null]}; x.kal = true; } if (x.p == null) x.p = kx.p; continue; }
            const carry = mainFair != null && e.p != null && kx.p != null && e.p > 0 && e.p < 1 && kx.p > 0 && kx.p < 1 ? sig(lgt(mainFair) + lgt(kx.p) - lgt(e.p)) : null;
            lines.push({l: +L, o: {Kalshi: [kx.o, null]}, u: {}, p: kx.p, main: false, apin: null, carry, kal: true});
          }
          // a Kalshi-only knee rung (Kalshi's ladders aren't in the props file): add it so the knee shows
          for (const x of F.knees || []) if (x.g === gid && x.n === nm && x.k === k && x.bk === 'Kalshi' && !lines.some(y => +y.l === +x.l))
            lines.push({l: x.l, o: {Kalshi: [x.o, null]}, u: {}, p: x.p, main: false, apin: x.fair});
          // an opener whose line the book has since pulled: still listed (owner: "flag the early openers even if I miss them")
          for (const x of F.openers || []) if (x.g === gid && x.n === nm && x.k === k && !lines.some(y => y.l != null && +y.l === +x.l))
            lines.push({l: x.l, o: {[x.bk]: [x.o, null]}, u: {}, p: null, main: false, apin: x.fnow ?? x.fair, gone: true});
          for (const x of lines){
            if (!x.o) continue;
            const books = Object.entries(x.o).filter(([b, v]) => !NOBET.has(b) && v && v[0] != null);
            if (!books.length) continue;
            const bet = k === 'td' ? 'anytime TD' : k === 'goal' ? 'to score' : YES.has(k) ? LAB[k] : `over ${x.l} ${LAB[k]}`;
            const tagBet = k === 'td' ? 'TD' : k === 'goal' ? 'goal' : YES.has(k) ? LAB[k] : `over ${x.l} ${LAB[k]}`;
            const prices = books.map(([b, v]) => {
              let fair = null, w = .5, src = '';
              if (x.main && x.pin){ fair = sharpO(x.pin, b); w = (WPIN[sport] || {})[k] ?? .5; src = 'sharp consensus'; }
              else if (!x.main && x.apin != null){ fair = x.apin; src = 'sharp consensus, carried to this line'; }
              else if (!x.main && x.carry != null){ fair = x.carry; w = 0; src = '≈ main line’s fair carried by our distribution'; }   // our model is already in the carry: don't count it twice
              else { const tw = twoWay(x.o, x.u); if (tw != null && (x.main || Object.keys(x.u || {}).length >= 2)){ fair = tw; src = 'books’ no-vig median'; }
                else if (k === 'goal' && f4O(x, b) != null){ fair = f4O(x, b); src = 'four books (Kalshi, FD, Fanatics, DK), hold removed'; }
                else if (k === 'goal'){ const ex = books.filter(([bb]) => EXCH.includes(bb)).map(([, vv]) => 1 / dec(vv[0])); const m = med(ex); if (m != null){ fair = m / 1.03; src = 'exchanges, less 3%'; } } }
              const q = fair != null ? (x.p != null ? w * x.p + (1 - w) * fair : fair) : null;
              return {b, o: v[0], u: link(v, P.partner), fair, q, w, src, edge: q != null ? q * dec(v[0]) - 1 : null};
            });
            out.push({sport, gid, nm, name: disp, team, game: g.lab || gid, start, k, statKey, l: x.l, main: x.main, ml: e.l ?? null, bet, p: x.p,
                      prices, value: tg.v === tagBet, lc: tg.lc === tagBet, pin: pinm[`${gid}|${nm}|${k}`] || null, cons: consm[`${gid}|${nm}|${k}`] || null,
                      cx: cx ? [...(cx.c || []), ...((cx.s || {})[k] || [])] : [], soft: !!(cx && ((cx.s || {})[k] || []).some(x => x[1] === 'g')), off: off.has(`${gid}|${nm}|${k}|${x.l}`) || off.has(`${gid}|${nm}|${k}|${x.l ?? .5}`), knee: knee[`${gid}|${nm}|${k}|${x.l}`] || null,
                      open: opn[`${gid}|${nm}|${k}|${x.l}`] || null, gone: !!x.gone, path: pth[`${gid}|${nm}|${k}|${x.l}`] || null});
          }
        }
      }
    }
    // Off-market prices (sharp.off_market -> flags "off"; owner 2026-10-28: 'move the off-market prices to the screener'):
    // one row per flagged price at its own book and fair (incl. Polymarket and Kalshi rungs the props file lacks); only under the Off market chip
    for (const x of F.off || []){
      const g = G[x.g] || {}, start = g.start ? new Date(g.start).getTime() : null;
      if (start && start <= now) continue;
      const who = (g.pl || {})[x.n] || [], k = x.k, cx = who[2] || null;
      const bet = k === 'td' ? 'anytime TD' : k === 'goal' ? 'to score' : YES.has(k) ? LAB[k] || k : x.b;
      out.push({sport, gid: x.g, nm: x.n, name: who[1] || x.n.replace(/\b\w/g, c => c.toUpperCase()), team: who[0] || '', game: g.lab || x.g, start, k, statKey: k.startsWith('sog') ? 'sog' : k,
                l: x.l, main: !!x.main, ml: null, bet, p: null, offOnly: x, cx: cx ? [...(cx.c || []), ...((cx.s || {})[k] || [])] : [], soft: !!(cx && ((cx.s || {})[k] || []).some(y => y[1] === 'g')),
                prices: [{b: x.bk, o: x.o, u: link([x.o, x.u], P.partner), fair: x.fair, q: x.fair, w: 0, src: x.carried ? '≈ main line’s fair carried by our projection' : x.main ? 'sharp consensus' : 'books’ two-way fair at this line',
                          edge: x.fair * dec(x.o) - 1}]});
    }
    return out;
  }
  async function load(){
    const res = await Promise.all(SPORTS.map(async sp => {
      try {
        const [P, F, LV, PA] = await Promise.all(['props', 'flags', 'live', 'paths'].map(f => fetch(`${root}markets/${f}_${sp}.json?t=${Date.now()}`, {cache: 'no-store'}).then(r => r.ok ? r.json() : {}).catch(() => ({}))));
        FETCHED[sp] = P.fetched || null;
        if (sp === 'nba'){                                                    // double / triple-doubles live in their own file (owner 2026-10-27)
          const X = await fetch(`${root}markets/ddtd_nba.json?t=${Date.now()}`, {cache: 'no-store'}).then(r => r.ok ? r.json() : {}).catch(() => ({}));
          for (const [g, ps] of Object.entries(X.props || {})) for (const [nm, ks] of Object.entries(ps)) Object.assign(((P.props = P.props || {})[g] = P.props[g] || {})[nm] = P.props[g][nm] || {}, ks);
        }
        return P && (P.props || (LV && LV.rows)) ? build(sp, {...P, props: P.props || {}}, F || {}, LV || {}, PA || {}) : [];
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
      if (!S.alts && !r.main && !(S.knee && r.knee) && !(S.path && r.path) && !S.offm) continue;
      if (!!S.offm !== !!r.offOnly) continue;                                 // off-market rows only under their chip, and only them
      if (S.knee && !r.knee) continue;
      if (S.open && !r.open) continue;
      if (S.path && !r.path) continue;
      if (r.gone && !S.open) continue;                                       // a pulled line: only in the Early openers list
      if (S.soon && !(r.start && r.start <= soon)) continue;
      if (S.tagged && !(r.value || r.lc || r.off)) continue;
      if (S.pinmv && !r.pin) continue;
      if (S.consmv && !r.cons) continue;
      if (S.softm && !r.soft) continue;
      if (q && !(norm(r.name).includes(q) || norm(r.team).includes(q) || norm(r.game).includes(q))) continue;
      const ps = r.prices.filter(x => S.books === 'all' || mine.has(x.b)).sort((a, b) => dec(b.o) - dec(a.o));
      if (!ps.length) continue;
      // Ladder knees filter: the knee's own book (that book's ladder has the kink), and no edge floor (shape, not edge)
      const kb = S.knee && r.knee ? ps.find(x => x.b === r.knee.bk) : S.open && r.open ? r.prices.find(x => x.b === r.open.bk)
        : S.path && r.path ? (r.prices.find(x => x.b === r.path.book) || ps[0]) : null;
      if ((S.knee || S.open || S.path) && !kb) continue;
      const best = kb || ps[0], [lo, hi] = PRICE[S.price] || PRICE.all;
      if (best.o < lo || best.o >= hi) continue;
      if (!S.knee && !S.open && !S.path && !S.offm && S.edge > -99 && !(best.edge != null && best.edge * 100 >= S.edge)) continue;
      if (!r.gone && !r.offOnly && best.edge != null && best.edge > .6) continue;                     // 60%+ claimed edges are stale or broken quotes
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
                    r.off || r.offOnly ? '<span class="sc-tag off" title="Off-market price: 8+ pts from the fair (often stale; check before betting)">Off</span>' : '',
                    r.pin ? `<span class="sc-tag pin" title="Pinnacle moved this prop${r.pin.line ? ': line up ' + r.pin.line : ''}${r.pin.price ? (r.pin.line ? ' and' : ':') + ' the over got shorter' : ''} (tracked, experimental)">PIN${r.pin.line ? ' ▲' : ''}${r.pin.price ? ' ▼odds' : ''}</span>` : '',
                    r.cons ? `<span class="sc-tag cons" title="Our sharp consensus (Pinnacle, Novig, ProphetX) moved this prop${r.cons.line ? ': line up ' + r.cons.line : ''}${r.cons.price ? (r.cons.line ? ' and' : ':') + ' the over got shorter' : ''} (tracked, experimental)">CONS${r.cons.line ? ' ▲' : ''}${r.cons.price ? ' ▼odds' : ''}</span>` : '', r.knee ? `<span class="sc-tag knee" title="Ladder knee at ${esc(FULL[r.knee.bk] || r.knee.bk)}: stepping up to this rung costs little, the next one costs a lot (tracked, experimental)">KNEE</span>` : '', r.path ? `<span class="sc-tag path" title="Path leg: ${r.path.f.length} of 5 path reasons (usage, matchup, rotation, ${r.sport === 'nfl' ? 'script' : 'game'}, price) on this rung (experimental)">PATH ${r.path.f.length}/5</span>` : '', r.open ? `<span class="sc-tag open" title="Early opener: ${esc(FULL[r.open.bk] || r.open.bk)} posted this line already 3%+ under the sharp fair (tracked, experimental)">OPEN</span>` : '', !r.main ? '<span class="sc-tag alt">alt</span>' : ''].join('');
      const pl = r.path ? `<div class="sc-path">${r.path.sc ? `<span class="s">${esc(r.path.sc)}: us ${pc(r.path.us)} · market ${pc(r.path.mk)}</span>` : ''}${r.path.why.map(t => `<span>${esc(t)}</span>`).join('')}</div>` : '';
      const ol = r.open ? `<div class="sc-open">Opened <b>${am(r.open.o)}</b> at ${esc(FULL[r.open.bk] || r.open.bk)}${r.open.t0 ? ' ' + esc(tm(r.open.t0 * 60e3)) : ''} · fair then ${pc(r.open.fair)} → ${r.open.gone ? '<span class="bad">line gone</span>' : `now <b>${am(r.open.now)}</b>`}${r.open.fnow != null ? ', fair now ' + pc(r.open.fnow) : ''}</div>` : '';
      const xo = r.offOnly ? `<div class="sc-open">${esc(FULL[r.offOnly.bk] || r.offOnly.bk)} implies ${pc(1 / dec(r.offOnly.o))} vs fair ${r.offOnly.carried ? '≈' : ''}${pc(r.offOnly.fair)} · <b>+${Math.round((r.offOnly.fair - 1 / dec(r.offOnly.o)) * 100)} pts</b>${r.offOnly.dep ? ` · $${Math.round(r.offOnly.dep).toLocaleString()} within 3¢` : ''} · often a stale or wrong line: check it first</div>` : '';
      const kl = r.knee ? `<div class="sc-knee">${esc(FULL[r.knee.bk] || r.knee.bk)} ladder: ${[[...r.knee.prev, 0], [r.knee.l, r.knee.o, 1], [...r.knee.next, 0]].map(([l, o, me]) => `<span${me ? ' class="me"' : ''}>${Math.ceil(l)}+ ${am(o)}</span>`).join('<i>→</i>')}</div>` : '';
      const tip = `Fair ${b.fair != null ? pc(b.fair) + ' (' + b.src + ')' : 'none'} · ours ${pc(r.p)} · chance ${b.q != null ? pc(b.q) + ' (' + Math.round(b.w * 100) + '% ours)' : '—'} · price implies ${pc(1 / dec(b.o))}`;
      const priceHtml = r.gone ? `<s>${am(b.o)}</s> ${esc(FULL[b.b] || b.b)} · gone` : `<b>${am(b.o)}</b> ${esc(FULL[b.b] || b.b)}${b.u ? ' ↗' : ''}`;
      // watchlist + odds chart (watch.js; owner 2026-10-26): this row's line at the shown book
      const wi = {sport: r.sport, gid: r.gid, name: r.nm, kind: r.k, line: r.l, book: b.b, o: b.o, fair: b.fair, main: r.ml, glab: r.game,
                  start: r.start ? new Date(r.start).toISOString() : null, lab: `${r.name} ${r.bet}`};
      const wb = window.WATCH ? `<span class="sc-w">${WATCH.both(wi)}</span>` : '';
      return `<div class="sc-row"><div class="sc-who"><b>${esc(r.name)}</b>${wb} <span class="sc-m">${esc(SP[r.sport])} · ${esc(r.team ? r.team + ' · ' : '')}${esc(r.game)} · ${esc(tm(r.start))}</span>
          <div class="sc-bet">${esc(r.bet[0].toUpperCase() + r.bet.slice(1))} ${tags}</div>${kl}${xo}${pl}${ol}${r.cx.length ? `<div class="sc-cx">${r.cx.map(x => `<span class="${x[1] === 'g' ? 'g' : x[1] === 'b' ? 'b' : ''}">${esc(x[0])}</span>`).join('')}</div>` : ''}</div>
        <div class="sc-num" title="${esc(tip)}"><span class="sc-e ${ev == null ? '' : ev >= .05 ? 'good' : ev < 0 ? 'bad' : ''}">${ev == null ? 'no fair' : (ev >= 0 ? '+' : '−') + Math.abs(Math.round(ev * 100)) + '%'}</span>
          <span class="sc-s">chance ${b.q != null ? pc(b.q) : '—'} · fair ${b.fair != null ? pc(b.fair) : '—'}</span></div>
        ${b.u ? `<a class="sc-px" href="${esc(b.u)}" target="_blank" rel="noopener">${priceHtml}</a>` : `<span class="sc-px nl">${priceHtml}</span>`}</div>`; };
    const preHtml = `<div class="sc-pre"><span class="sc-l">Saved</span>${PRE.map((f, i) => `<span class="sc-pc${same(f.f) ? ' on' : ''}"><button class="sc-pa" data-i="${i}" aria-pressed="${same(f.f)}">${esc(f.n)}</button><button class="sc-px2" data-i="${i}" aria-label="Delete ${esc(f.n)}" title="Delete">×</button></span>`).join('')}
        ${NAMING ? `<span class="sc-name"><input id="sc-pn" maxlength="40" value="${esc(suggest())}" aria-label="Name for these filters"><button id="sc-ps" class="sc-chip on">Save</button><button id="sc-pc" class="sc-reset">Cancel</button></span>`
                 : PRE.some(f => same(f.f)) ? '' : `<button id="sc-pnew" class="sc-chip sc-add">+ Save these filters</button>`}</div>`;
    el.innerHTML = preHtml + `<div class="sc-bar">
        <div class="sc-grp">${chip('sport', 'all', 'All', S.sport === 'all')}${SPORTS.map(s => chip('sport', s, SP[s], S.sport === s)).join('')}</div>
        <div class="sc-grp">${chip('books', 'mine', 'My books', S.books === 'mine')}${chip('books', 'all', 'All books', S.books === 'all')}</div>
        <div class="sc-grp"><span class="sc-l">Edge</span>${[[-100, 'Any'], [0, '0%+'], [3, '3%+'], [5, '5%+'], [10, '10%+']].map(([v, l]) => chip('edge', v, l, +S.edge === v)).join('')}</div>
        <div class="sc-grp">${Object.entries(PRICE).map(([k, v]) => chip('price', k, v[2], S.price === k)).join('')}</div>
        <div class="sc-grp">${chip('tagged', 'x', 'Tagged only', S.tagged)}${chip('pinmv', 'x', 'Pinnacle moved', S.pinmv)}${chip('consmv', 'x', 'Consensus moved', S.consmv)}${chip('offm', 'x', 'Off market', S.offm)}${chip('knee', 'x', 'Ladder knees', S.knee)}${chip('path', 'x', 'Path legs', S.path)}${chip('open', 'x', 'Early openers', S.open)}${chip('softm', 'x', 'Soft matchup', S.softm)}${chip('soon', 'x', 'Next 6 hours', S.soon)}${chip('alts', 'x', 'Alt lines', S.alts)}</div>
        <div class="sc-grp sc-in"><select id="sc-stat" aria-label="Stat"><option value="all">Every stat</option>${stats.map(s => `<option value="${s}"${S.stat === s ? ' selected' : ''}>${esc(STAT[s] || s)}</option>`).join('')}</select>
          <input id="sc-q" type="search" placeholder="Player, team or game" value="${esc(S.q)}" aria-label="Search">
          <select id="sc-sort" aria-label="Sort"><option value="edge"${S.sort === 'edge' ? ' selected' : ''}>Best edge</option><option value="gap"${S.sort === 'gap' ? ' selected' : ''}>Biggest gap</option><option value="start"${S.sort === 'start' ? ' selected' : ''}>Starting soonest</option><option value="price"${S.sort === 'price' ? ' selected' : ''}>Biggest payout</option></select>
          <button class="sc-reset" id="sc-reset">Reset</button></div></div>
      <p class="sc-count"><b>${V.length}</b> over price${V.length === 1 ? '' : 's'}${S.books === 'mine' ? ' at ' + esc(MY.join(', ')) : ' at every book'}${when ? ` · prices as of ${esc(when)}` : ''}</p>
      <div class="sc-list">${V.slice(0, SHOW).map(row).join('') || '<p class="sc-empty">Nothing matches. Try a lower edge or All books.</p>'}</div>
      ${V.length > SHOW ? `<button class="sc-more" id="sc-more">Show ${Math.min(60, V.length - SHOW)} more</button>` : ''}`;
  }
  document.addEventListener('click', e => {
    const c = e.target.closest('.sc-chip[data-k]');
    if (c){ const k = c.dataset.k, v = c.dataset.v;
      if (k === 'tagged' || k === 'pinmv' || k === 'consmv' || k === 'knee' || k === 'offm' || k === 'open' || k === 'path' || k === 'softm' || k === 'soon' || k === 'alts') S[k] = !S[k]; else if (k === 'edge') S.edge = +v; else S[k] = v;
      if (k === 'sport') S.stat = 'all';
      SHOW = 60; save(); render(); return; }
    if (e.target.id === 'sc-more'){ SHOW += 60; render(); return; }
    const pa = e.target.closest('.sc-pa'); if (pa){ S = {...S, ...pick(PRE[+pa.dataset.i].f)}; SHOW = 60; save(); render(); return; }
    const px = e.target.closest('.sc-px2'); if (px){ PRE.splice(+px.dataset.i, 1); savePre(); render(); return; }
    if (e.target.id === 'sc-pnew'){ NAMING = true; render(); const i = document.getElementById('sc-pn'); if (i){ i.focus(); i.select(); } return; }
    if (e.target.id === 'sc-pc'){ NAMING = false; render(); return; }
    if (e.target.id === 'sc-ps'){ savePreset(); return; }
    if (e.target.id === 'sc-reset'){ S = {...DEF}; SHOW = 60; save(); render(); }
  });
  function savePreset(){
    const i = document.getElementById('sc-pn'), n = ((i && i.value) || '').trim() || suggest();
    const j = PRE.findIndex(f => f.n === n);                                  // same name: replaces it
    if (j >= 0) PRE[j] = {n, f: pick(S)}; else PRE.push({n, f: pick(S)});
    if (PRE.length > 12) PRE = PRE.slice(-12);
    NAMING = false; savePre(); render(); }
  document.addEventListener('keydown', e => { if (e.target.id !== 'sc-pn') return;
    if (e.key === 'Enter'){ e.preventDefault(); savePreset(); } else if (e.key === 'Escape'){ NAMING = false; render(); } });
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
