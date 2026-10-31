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
  // price display for prediction-market contracts: cents (as listed), American odds or a payout multiplier.
  // Odds / multiplier include Kalshi's taker fee (7% x p x (1-p)), so they're what a $1 YES bet really pays.
  let PXF = 'am';                                   // default: American odds
  try { PXF = localStorage.getItem('simpx-v1') || 'am'; } catch (e) {}
  const px = (p, src) => {
    if (p == null) return '–';
    if (PXF === 'c') return cents(p);
    const q = Math.min(.999, p + ((src || 'kalshi') === 'kalshi' ? .07 * p * (1 - p) : 0));
    if (PXF === 'x') return (1 / q).toFixed(1 / q < 10 ? 2 : 1) + 'x';
    const o = q >= .5 ? -Math.round(100 * q / (1 - q)) : Math.round(100 * (1 - q) / q);
    return (o > 0 ? '+' : '') + o;
  };
  const pxSwitch = () => `<span class="pxs" role="group" aria-label="Price format">${[['c', '¢'], ['am', 'odds'], ['x', 'x']].map(([k, l]) => `<button type="button" data-pxf="${k}" aria-pressed="${PXF === k}" title="${k === 'c' ? 'Contract price in cents' : k === 'am' ? 'American odds (Kalshi fee included)' : 'Payout per $1 staked (Kalshi fee included)'}">${l}</button>`).join('')}</span>`;
  const pc = p => p == null ? '–' : (p < .1 && p > 0 ? (p * 100).toFixed(1) : Math.round(p * 100)) + '%';
  const usd = v => v == null ? '–' : v >= 1e6 ? '$' + (v / 1e6).toFixed(1) + 'M' : v >= 1000 ? '$' + (v / 1000).toFixed(v >= 10000 ? 0 : 1) + 'k' : '$' + Math.round(v);
  const shade = e => e >= .08 ? 'g3' : e >= .05 ? 'g2' : e >= .02 ? 'g1' : '';
  let D = null, stamp = null, timer = null;
  let BK = null, FL = null, PR = null, CARD = null, BL = null;   // BL: books' main lines frozen at each start (boxgrade.py lines)
  //            // PR: every player's prices, markets/props_<sport>.json (Unabated)                        // FL: value tags, markets/flags_<sport>.json (sharp.py: our inference only)                                   // sportsbook game lines: markets/books_<sport>.json (The Odds API, books.py)
  const BKS = {hardrockbet: 'Hard Rock', novig: 'Novig', draftkings: 'DK', fanduel: 'FD', betmgm: 'MGM', williamhill_us: 'Caesars', espnbet: 'ESPN', betrivers: 'BetRivers', fliff: 'Fliff', ballybet: 'Bally'};
  const FULL = {DK: 'DraftKings', FD: 'FanDuel', MGM: 'BetMGM', ESPN: 'ESPN BET', Bally: 'Bally Bet'};
  const ORDER = ['Novig', 'Kalshi', 'Hard Rock', 'Polymarket', 'Poly US', 'ProphetX', 'DK', 'FD', 'MGM', 'Caesars', 'Fanatics', 'theScore', 'ESPN BET', 'BetRivers', 'Fliff', 'Bally Bet', 'betPARX'];
  let MY = [], bkOpen = null;
  const OV = {g: 'all', m: 'all', q: '', alt: false, view: 'cards', ref: 'sharp', edge: true};
  try { Object.assign(OV, JSON.parse(localStorage.getItem('simov-v1') || '{}')); } catch (e) {}
  const ovSave = () => { try { localStorage.setItem('simov-v1', JSON.stringify({view: OV.view, ref: OV.ref, edge: OV.edge})); } catch (e) {} };
  // Off market view filters (owner 2026-10-28): search, game, stat, book, main / alt, gap floor, sharp fair only, sort
  const OFV = {q: '', g: 'all', k: 'all', bk: 'all', ln: 'all', gap: 0, sharp: false, sort: 'gap'};
  try { Object.assign(OFV, JSON.parse(localStorage.getItem('simoff-v1') || '{}'), {q: '', g: 'all'}); } catch (e) {}
  const ofvSave = () => { try { const {q, g, ...k} = OFV; localStorage.setItem('simoff-v1', JSON.stringify(k)); } catch (e) {} };
  const WV = {edge: true, mine: false, all: false, long: false};   // Weighted fair tab toggles          // Book odds tab filters                      // the viewer's sportsbooks (this device), [] = all books
  // the owner's books (Novig, Kalshi, Hard Rock) until this device saves its own choice ("all books" saves [] and is kept)
  const OWNER = ['Novig', 'Kalshi', 'Hard Rock'];
  MY = OWNER.slice();
  try { const sv = localStorage.getItem('simbooks-v1'); if (sv != null) MY = JSON.parse(sv) || []; } catch (e) {}
  // ?freeze=1 (parlays.py, in the cloud): price at the owner's books and record every parlay the cards show, so the
  // Results page can grade them. Each leg: {gid, start, kind, side, line, name, lab, p, price, book}.
  const FREEZE = /[?&]freeze=1(&|$)/.test(location.search);
  if (FREEZE) MY = ['Novig', 'Kalshi', 'Hard Rock'];
  let FROZEN = [], CANDS = [], PATHS = [];
  const reg = (card, tier, legs, p, d) => { if (FREEZE && legs && legs.length) FROZEN.push({card, tier, legs, p: +(+p).toFixed(5), d: d == null ? null : +(+d).toFixed(3)}); };
  // learning builder (builder.py): every over the page could bet at the owner's books, with its path / script reasons (f)
  const regCand = c => { if (FREEZE && c) CANDS.push(c); };
  // every potential Path leg (owner 2026-10-26: filterable in the screener) -> parlays.py -> markets/paths_<sport>.json
  const regPath = c => { if (FREEZE && c) PATHS.push(c); };
  const dumpFrozen = () => { let el = document.getElementById('frozen-parlays');
    if (!el){ el = document.createElement('script'); el.type = 'application/json'; el.id = 'frozen-parlays'; document.body.appendChild(el); }
    el.textContent = JSON.stringify({generated: new Date().toISOString(), books: MY, prices: BK && BK.fetched, props: PR && PR.fetched, parlays: FROZEN, cands: CANDS, paths: PATHS}); };
  const mine = k => !MY.length || MY.includes(k);
  // Pinnacle's no-vig is on the prop when its line matches ours (TD props have no line: .5). w: 2 two-way (multiplicative), 1 one-way (power)
  const pinHow = (w, pl) => w === 1 ? 'one-way price, power de-vig' : w === 3 ? `carried from Pinnacle’s ${pl != null ? pl + ' ' : ''}line by our distribution’s shape` : 'both sides, multiplicative de-vig';
  const pinOk = e => !!(e && e.pin && e.pin.o != null && Math.abs(e.pin.l - (e.l ?? .5)) < 1e-9);
  // Weight on our number when the market half is Pinnacle's no-vig line (research/blend_weights.py: 2025-26 closing props, best weight 0-0.4 by half; about 0.2 NHL goals, 0.1 NBA, 0.25 NFL yardage / catches, 0 anytime TD). Without a Pinnacle line the 50/50 with the exchanges tested on Kalshi stays.
  const WPIN = {nhl: {goal: .2}, nba: {pts: .05, reb: .05, ast: .05, fg3m: .05}, nfl: {rec: .25, rec_yds: .25, rush_yds: .25, pass_yds: .25, td: 0}};
  const wOf = (sp, kind, isPin) => isPin ? ((WPIN[sp] || {})[kind] ?? .5) : .5;
  // NHL anytime goal without Pinnacle (2026-10-31, research/nhl_atg_fourbook.py): sharp.py four_book's fair from Kalshi,
  // FanDuel, Fanatics, DraftKings (hold removed per book), leaving out the book being bet; null when none of them prices it
  const f4O = (e, book) => { const P = Object.entries((e && e.f4 && e.f4.parts) || {}).filter(([b]) => b !== book);
    if (!P.length) return null; const z = P.reduce((s, [, p]) => s + Math.log(p / (1 - p)), 0) / P.length; return 1 / (1 + Math.exp(-z)); };
  // Sharp consensus (research/consensus_test.py): the log-odds mean of Pinnacle's, Novig's and ProphetX's no-vig at the same line beat Pinnacle alone on NBA props 2025-26 (-0.0023 log-loss, z -2.5, all 4 stats) and NFL 2026 (-0.0021, z -2.3), flat on NFL 2025; tight Kalshi added nothing beyond Novig, so Kalshi stays out. Leave-one-out: a bet at a book is judged against the consensus without that book.
  const lgt = p => Math.log(Math.min(Math.max(p, 1e-4), 1 - 1e-4) / (1 - Math.min(Math.max(p, 1e-4), 1 - 1e-4)));
  const sharpO = (e, book) => { const P = e.pin && e.pin.parts; if (!P) return e.pin.o;
    let xs = Object.entries(P).filter(([k]) => k !== book).map(([, v]) => v); if (!xs.length) xs = Object.values(P);
    return 1 / (1 + Math.exp(-xs.reduce((s, v) => s + lgt(v), 0) / xs.length)); };
  const sharpLab = e => e && e.pin && e.pin.parts && Object.keys(e.pin.parts).length > 1 ? 'Sharp consensus (' + Object.keys(e.pin.parts).join(' + ') + ')' : 'Pinnacle';
  const NOBET = new Set(['Pinnacle']);             // sharp reference only: never a 'best price' (not bettable from the US)
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
.pxs{display:inline-flex;margin-left:6px;border:1px solid var(--faint);border-radius:999px;overflow:hidden;vertical-align:1px}
.pxs button{border:0;background:none;color:var(--muted);font:inherit;font-size:11px;padding:0 7px;line-height:17px;cursor:pointer}
.pxs button[aria-pressed="true"]{background:var(--ink);color:var(--bg)}
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
.ovsw{display:flex;flex-wrap:wrap;gap:8px 14px;align-items:center;margin:8px 0}.ovsw .seg{display:inline-flex;align-items:center;gap:4px}
.pcards{display:grid;gap:8px;margin:10px 0}.pcard{border:1px solid var(--faint);border-radius:10px;background:var(--sunk)}
.pcard>summary{display:flex;justify-content:space-between;align-items:center;gap:10px;padding:10px 12px;cursor:pointer;list-style:none}
.pcard>summary::-webkit-details-marker{display:none}.pcard[open]>summary{border-bottom:1px solid var(--faint)}
.pcw{display:flex;flex-direction:column;min-width:0}.pcw small{color:var(--muted);font-size:12px}
.pcb{text-align:right;white-space:nowrap;font-size:13.5px}.pcb a{color:inherit}.pcb i,.pct .pe,.pct .ne{font-style:normal;font-weight:700}
.pcb .pe,.pct .pe{color:var(--good)}.pcb .ne,.pct .ne{color:var(--muted)}.pcb .mine,.pct .mine{color:var(--good)}
.pct{margin:4px 0 8px;font-size:13px}.pct td,.pct th{white-space:nowrap;text-align:right;padding:5px 8px}.pct .l{text-align:left}.pct tr.top td{background:color-mix(in srgb,var(--good) 10%,transparent)}
.pcmore{justify-self:start;background:none;border:1px solid var(--faint);border-radius:999px;color:var(--ink);font:inherit;font-size:13px;padding:4px 12px;cursor:pointer}
@media(max-width:560px){.pcard>summary{flex-direction:column;align-items:flex-start}.pcb{text-align:left;white-space:normal}}
.ovt th .gl{display:block;font-size:9.5px;letter-spacing:.06em;color:var(--accent);text-transform:uppercase;margin-bottom:2px;text-align:left}
.ovt .g0{border-left:1px solid var(--faint)}
.ovt td{text-align:right}
.ovt .name{position:sticky;left:0;z-index:1;background:var(--surface);min-width:150px;white-space:normal}
.ovt thead th:first-child{position:sticky;left:0;z-index:2;background:var(--surface)}
.ovt .name b{display:block;font-weight:600}.ovt .name small,.ovt td small{display:block;color:var(--muted);font-size:11.5px}
.ovt td a{color:inherit;text-decoration:none}
.ovt td.bst{font-weight:800}.ovt td.bst .pp{text-decoration:underline;text-decoration-color:var(--accent);text-decoration-thickness:2px;text-underline-offset:3px}.ovt td .pp+small{margin-top:4px;font-weight:400}.ovt td .pp+small.pe{font-weight:700}
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
@media (max-width:640px){.vtag{display:block;width:fit-content;white-space:normal;max-width:100%;line-height:1.35;margin:3px 4px 0 0}.vtag .pinv{margin-left:0}td.name:has(> .vtag){white-space:normal;max-width:62vw}}
.mybk{color:var(--good);font-weight:700}
.beta{font-family:var(--body);font-size:10.5px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;color:#F2C14E;border:1px solid currentColor;border-radius:4px;padding:1px 5px;margin-left:6px;vertical-align:3px}
.wvc{display:flex;flex-wrap:wrap;gap:6px;margin:4px 0 10px}.wvc button,.wvmore{border:1px solid var(--faint);border-radius:999px;padding:4px 12px;background:none;color:var(--ink);font:inherit;font-size:13px;cursor:pointer}
.wvc button[aria-pressed="true"]{border-color:var(--accent);color:var(--accent);background:rgba(54,211,153,.10)}.wvmore{margin-top:10px}
.wv details.how div{font-size:12.5px;color:var(--muted);margin-top:4px}
.wvt td{white-space:nowrap}.wvt td small{display:block;color:var(--muted);font-size:11.5px}.wvt .name{white-space:normal;min-width:150px}.wvt td.pe{color:var(--good);font-weight:700}.wvt td a{color:inherit;text-decoration:none}
@media (max-width:640px){.wvt th:nth-child(2),.wvt td:nth-child(2),.wvt th:nth-child(4),.wvt td:nth-child(4),.wvt th:nth-child(7),.wvt td:nth-child(7){display:none}.wvt td,.wvt th{padding:6px 4px}.wvt td{white-space:normal}.wvt .name{min-width:0;max-width:34vw}.wvt td small{font-size:11px}}
.bestshot{border:1px solid color-mix(in srgb,var(--good) 45%,transparent);background:color-mix(in srgb,var(--good) 7%,transparent);border-radius:10px;padding:10px 12px;margin:8px 0 12px}
.bestshot ul{list-style:none;margin:6px 0;padding:0;display:grid;gap:4px}.bestshot li{display:flex;flex-wrap:wrap;gap:2px 8px;align-items:baseline}.bestshot li a{margin-left:auto;color:inherit;white-space:nowrap}
.bestshot .bsum{display:flex;flex-wrap:wrap;gap:6px 16px;align-items:center;border-top:1px solid var(--faint);padding-top:8px;font-size:13.5px}.bestshot .bsum button{margin-left:auto}
.dcard{border:1px solid color-mix(in srgb,var(--accent) 45%,transparent)}.dfin{font-size:11px;font-weight:800;letter-spacing:.04em;text-transform:uppercase;border-radius:4px;padding:2px 7px;margin-left:8px;color:var(--muted);border:1px solid var(--faint)}.dfin.on{color:var(--good);border-color:color-mix(in srgb,var(--good) 55%,transparent)}.dcard .phead{align-items:baseline}
.dcrec{display:flex;flex-wrap:wrap;gap:6px 14px;font-size:13px;color:var(--muted)}.dcrec b{color:var(--ink)}
.dcg{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:10px;margin-top:10px}
.dcp{border:1px solid var(--faint);border-radius:10px;padding:10px 12px;background:var(--sunk);display:grid;gap:6px;align-content:start}
.dcp.W{border-color:color-mix(in srgb,var(--good) 60%,transparent)}.dcp.L{opacity:.7}
.dch{display:flex;align-items:center;gap:8px;font-size:12px;color:var(--muted)}.dch .slot{font-size:10.5px;font-weight:800;letter-spacing:.06em;text-transform:uppercase;border-radius:4px;padding:1px 6px;background:color-mix(in srgb,var(--accent) 18%,transparent);color:var(--accent)}
.dch .slot.ls{background:color-mix(in srgb,#F2C14E 20%,transparent);color:#F2C14E}.dch .st{margin-left:auto;font-weight:700}.dch .st.W{color:var(--good)}.dch .st.L{color:var(--bad)}
.dcp ul{list-style:none;margin:0;padding:0;display:grid;gap:4px;font-size:13.5px}.dcp li{display:flex;gap:8px;align-items:baseline}.dcp li span{margin-left:auto;white-space:nowrap;font-size:12.5px;color:var(--muted)}
.dcp li i{font-style:normal;width:12px;font-weight:800}.dcp li i.W{color:var(--good)}.dcp li i.L{color:var(--bad)}
.dcs{display:flex;flex-wrap:wrap;gap:4px 12px;border-top:1px solid var(--faint);padding-top:6px;font-size:13px}.dcs b{font-family:var(--display);font-size:16px}
.pinv b{color:var(--good);font-weight:800}.pinv.neg b{color:var(--muted);font-weight:600}
.pev .mine{color:var(--good);font-weight:700}.pev h3 .mine{font-weight:600}
.altb{display:inline-block;padding:0 5px;border-radius:4px;border:1px solid var(--faint);font-size:10px;line-height:14px;text-transform:uppercase;letter-spacing:.05em}
.pev details summary{cursor:pointer;color:var(--muted);font-size:12.5px;padding:4px 0}.pev details ol{margin-top:4px}
.ovt i.anc{font-style:normal;color:var(--muted);margin-right:2px}
a.pinv{color:var(--muted)}a.pinv:hover{border-color:var(--good)}
.ovt td small.pe{color:var(--good);font-weight:700}
.pev{margin:10px 0 14px;padding:10px 12px;border:1px solid var(--faint);border-radius:8px;background:var(--sunk)}
.pev h3{margin:0 0 6px;font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);font-weight:600}
.pev ol{list-style:none;margin:0;padding:0;display:grid;gap:4px}
.pev li a{display:flex;flex-wrap:wrap;align-items:baseline;gap:4px 8px;color:inherit;text-decoration:none;padding:3px 0}
.pev li a:hover b{text-decoration:underline}
.pev li .w{color:var(--muted);font-size:12.5px}.pev li .px{margin-left:auto;white-space:nowrap}.pev li .px i{font-style:normal;color:var(--good);font-weight:800;margin-left:6px}
.pinv{display:inline-block;margin-left:4px;padding:0 6px;border-radius:9px;border:1px solid var(--faint);color:var(--muted);font-size:10.5px;font-weight:600;line-height:16px;white-space:nowrap;text-decoration:none}
a.vtag{text-decoration:none} a.vtag:hover{background:color-mix(in srgb,var(--good) 12%,transparent)}
.offr{display:grid;grid-template-columns:1fr auto auto;gap:4px 14px;align-items:baseline;padding:7px 0;border-top:1px solid var(--faint);font-size:13.5px}
.offr.mine .offn b{color:var(--good)}.offn small,.offp small{display:block;font-size:11.5px;color:var(--muted)}.offn i{font-style:normal;font-size:11px;color:var(--muted);border:1px solid var(--faint);border-radius:4px;padding:0 4px}
.offp{text-align:right;white-space:nowrap}.offp a{color:inherit}.offg{text-align:right;white-space:nowrap;color:var(--muted);font-size:12.5px}.offg b{display:block;color:var(--good);font-size:14px}
.offf{display:flex;gap:8px;align-items:center;margin:0 0 6px;min-width:0}.offl{flex:0 0 40px;font-size:11.5px;color:var(--muted);text-transform:uppercase;letter-spacing:.04em}.offf .ovm{flex:1;min-width:0}.offsep{flex:0 0 1px;align-self:stretch;background:var(--faint);margin:3px 2px}.offcount{margin:2px 0 6px}.offbar select{max-width:100%}
@media (max-width:640px){.offr{grid-template-columns:1fr auto}.offg{grid-column:1/-1;text-align:left}.offg b{display:inline;margin-left:6px}}
.vtag.offt{border-color:color-mix(in srgb,#F2C14E 60%,transparent);color:#F2C14E}.vtag.offt i{font-style:normal;font-weight:700;margin-left:2px}
.why{display:inline-block;font-size:11px;color:var(--muted);border:1px dashed var(--faint);border-radius:999px;padding:0 8px;margin:2px 4px 0 0;white-space:nowrap}.why b{color:var(--ink);font-weight:600}.why.none{opacity:.75}
.vtag.lct{border-color:color-mix(in srgb,#6CB4FF 60%,transparent);color:#6CB4FF}.vtag.pmv{border-color:color-mix(in srgb,#C084FC 55%,transparent);color:#C084FC}.vtag.pmv.st{background:color-mix(in srgb,#C084FC 14%,transparent);font-weight:700}.vtag.pmv i{font-style:normal;font-weight:700}.vtag.lct i{font-style:normal;font-weight:700;margin-left:2px}
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
  // The chance an exchange row's edge is judged on: never our number alone. research/kalshi_pick.py (2026 NFL Kalshi props,
  // 6,800 bets): edges from With Vegas or Market-blind alone lost 8% (+-4%); research/agreement_test.py: both lost ~3% at
  // the sharp price at Hard Rock / Novig. Where a sharp line exists at this number: WPIN weights vs the consensus without
  // this exchange; otherwise the 50/50 with the exchange's mid tested on Kalshi.
  const PK = {anytime_td: 'td'};
  function pq(r, model){
    const p = ours(r, model); if (p == null) return p;
    const kind = PK[r.k] || r.k;
    const e = r.n && PR && PR.props ? ((PR.props[String(r.g)] || {})[norm(r.n)] || {})[kind] : null;
    if (e && pinOk(e) && Math.abs((e.pin.l ?? .5) - (r.l ?? .5)) < 1e-9){
      const w = wOf(sport, kind, true), sh = sharpO(e, r.s === 'kalshi' ? 'Kalshi' : 'Polymarket');
      return w * p + (1 - w) * sh;
    }
    const m = r.ya != null && r.yb != null && r.yb > 0 ? (r.ya + r.yb) / 2 : r.ya;
    if (m == null) return p;
    if (['win', 'total', 'spread'].includes(r.k)) return .5 * p + .5 * m;       // game lines: the 50/50 held up (+21%, 165 bets)
    // props: trust Kalshi's mid by how deep it is (research/kalshi_liquidity_rule.py, 2026-10-21: every Kalshi prop YES
    // returned -10.8%, the 50/50 rule -12.9% on 2,015 bets; Kalshi's mid out-predicted ours where deep). Our weight:
    // 10% with $1,000+ to buy within 3c, 25% with $100+, else 50%. A spread of 4c+ (-38%) means the mid can't be read: no edge.
    if (r.yb == null || r.ya - r.yb > .035) return m;
    const w = r.by >= 1000 ? .10 : r.by >= 100 ? .25 : .5;
    return w * p + (1 - w) * m;
  }
  function edges(r, p){
    const f = FEE[r.s] || 0, ya = r.ya, na = r.yb != null ? 1 - r.yb : null;
    return {y: p != null && ya > 0 && ya < 1 ? p - ya - f * ya * (1 - ya) : null, n: p != null && na > 0 && na < 1 ? (1 - p) - na - f * na * (1 - na) : null};
  }
  const spr = r => r.ya != null && r.yb != null && r.yb > 0 ? r.ya - r.yb : null;
  const isThin = r => { const s = spr(r); return s != null && s >= .015; };
  // NFL yardage / catch props: only the side our median is on, our number 10+ pts from the market, and on exchanges a tight (3c or less), 15c+ market. Kalshi 2026 wk 1-5: the plain 5%-edge rule lost 5% in both halves; this cut it to about breakeven (-0.6% / +7.7%) by dropping far-side ladder rungs and thin markets.
  const NFLP = new Set(['rec', 'rec_yds', 'rush_yds', 'pass_yds']);
  const nflRuleOk = (r, p, side) => { if (!NFLP.has(r.k) || p == null) return true;
    const ps = side === 'yes' ? p : 1 - p, m = r.yb != null && r.yb > 0 ? (r.ya + r.yb) / 2 : r.ya, s = spr(r), cost = side === 'yes' ? r.ya : (r.yb != null ? 1 - r.yb : null);
    return ps >= .5 && (s == null || s <= .035) && (cost == null || cost >= .15) && (m == null || Math.abs(p - m) >= .10); };

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
  function rerender(){ clearTimeout(rr); rr = setTimeout(() => { FROZEN = []; CANDS = []; PATHS = []; try { window.render && window.render(); } catch (e) {} if (FREEZE) dumpFrozen(); }, 250); }

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
    const [yl, nl] = sides(r), p = ours(r, model), e = edges(r, pq(r, model));
    const best = (e.y ?? -9) >= (e.n ?? -9) ? {v: e.y, lab: yl} : {v: e.n, lab: nl};
    const weak = (D && D.weak && D.weak.includes(r.k)) || !!lost(r.k) || !nflRuleOk(r, p, (e.y ?? -9) >= (e.n ?? -9) ? 'yes' : 'no');
    const by = r.by ?? null, bn = r.bn ?? null, tot = (by || 0) + (bn || 0);
    const setBtn = slider && r.l != null && !['win', 'spread', 'anytime_td', 'first_td', 'first_goal'].includes(r.k) ? `<button class="mkx-set" data-mline="${r.l}" title="Move the slider to this line">use line</button>` : '';
    return `<div class="mkx-r${focus ? ' on' : ''}">
      <div class="mkx-l"><span class="mkx-src" title="${r.s === 'kalshi' ? 'Kalshi' : 'Polymarket (live)'}">${r.s === 'kalshi' ? 'K' : 'P'}</span><b>${esc(yl)}</b> <small>${esc(KLAB[r.k] || r.k)}</small>${isThin(r) ? `<span class="mkx-thin" title="Bid/ask gap ${Math.round(spr(r) * 100)}¢: a less-traded market">thin ${Math.round(spr(r) * 100)}¢</span>` : ''}${(r.pat || []).map(id => pchip(id)).join('')}${flowChips(r, e.y)}${setBtn}</div>
      <div class="mkx-p">Price <b>${px(r.ya, r.s)}</b> · ours <b>${pc(p)}</b>${best.v != null && best.v >= .02 ? ` · <span class="e ${weak ? '' : shade(best.v)}">+${(best.v * 100).toFixed(1)}% ${esc(best.lab)}</span>${lost(r.k) ? ` <span class="mkx-warn" title="${esc(lostTip(r.k))}">losing record</span>` : weak ? ' <span class="mkx-warn" title="On settled games of this type the market has been more accurate than our model">market ahead</span>' : ''}` : ' · <small>no edge</small>'}</div>
      <div class="mkx-liq" title="Dollars you could spend within 3¢ of the best price on each side"><span>${esc(yl.length > 14 ? 'Yes' : yl)} <b>${usd(by)}</b></span><div class="mkx-bar">${tot > 0 ? `<i style="width:${by / tot * 100}%"></i><i style="width:${bn / tot * 100}%"></i>` : ''}</div><span><b>${usd(bn)}</b> ${esc(nl.length > 14 ? 'No' : nl)}</span></div>
    </div>`;
  }
  function head(title){
    const k = D && D.fetched ? new Date(D.fetched) : null;
    const t = k && !isNaN(k) ? k.toLocaleTimeString([], {hour: 'numeric', minute: '2-digit'}) : '';
    return `<div class="mkx-h"><span class="t">${esc(title)}</span><small>Kalshi as of ${t || '–'} · Polymarket live · bar = money to buy each side within 3¢ ${pxSwitch()}</small></div>`;
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
  function best(r, model){ const [yl, nl] = sides(r), e = edges(r, pq(r, model)); return (e.y ?? -9) >= (e.n ?? -9) ? {v: e.y, lab: yl, side: 'yes'} : {v: e.n, lab: nl, side: 'no'}; }
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
  // the sportsbooks' over prices at one exact number, for comparing a Kalshi rung (owner 2026-10-23: Hard Rock alt props
  // and Novig ladders): the props file's main line or that number's alt line; {book: {o, u}} (u = bet link or null)
  const booksAt = (gid, name, kind, line) => {
    const e = PR && PR.props && ((PR.props[String(gid)] || {})[norm(name)] || {})[kind]; if (!e || line == null) return {};
    const x = Math.abs((e.l ?? -99) - line) < 1e-9 ? e.o : ((e.a || {})[String(line)] || (e.a || {})[(+line).toFixed(1)] || {}).o;
    const out = {};
    for (const [b, v] of Object.entries(x || {})) if (['Hard Rock', 'Novig'].includes(b) && v && v[0] != null)
      out[b] = {o: v[0], u: v[1] ? (String(v[1]).startsWith('http') ? v[1] : `https://www.gambly.com/deeplink/odds/${v[1]}?utm_campaign=${encodeURIComponent(PR.partner || '')}`) : null};
    return out;
  };
  window.MKT = {
    // best sportsbook price for one game-line option, as a small second line inside the page's bet chip.
    // kind: 'ml' (side home|away), 'spread' (line = HOME team's handicap, side home|away), 'total' (side over|under)
    bk(gid, kind, side, line, p){
      const G = BK && BK.games && BK.games[String(gid)]; if (!G) return '';
      let o = G[kind]; if (kind !== 'ml') o = o && o[String(+line)]; o = o && o[side];
      // a price is [odds, Gambly bet link] (Unabated) or plain odds (The Odds API)
      const all = o ? Object.entries(o).filter(([k]) => !SKIP.has(k) && !NOBET.has(k)).map(([k, v]) => [k, Array.isArray(v) ? v[0] : v, Array.isArray(v) ? v[1] : null])
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
    px, pxSwitch,
    // EXPERIMENTAL tab: fair chance = the owner's book weights (weighted mean of each book's no-vig chance; the book
    // being judged is left out). Two-way prices de-vig multiplicatively; one-way prices with the power method, each
    // book's exponent solved from its own two-way lines on this slate. Compared with the plain median of the books.
    weightView(games){
      const W_LINES = {Novig: 7.5, FD: 1.1, Kalshi: 1.1, DK: 1, ProphetX: 1, Polymarket: 1, 'Poly US': 1, Pinnacle: 1, Caesars: .3, MGM: .1, Fanatics: .1};
      const W_PROPS = {Novig: 7, Kalshi: 7, DK: 1.4, ProphetX: 1.4, Pinnacle: 1.4, FD: 1, Caesars: .5, Fliff: .5, bet365: .3, 'Hard Rock': .1};
      const KL = {goal: 'Anytime goal', td: 'Anytime TD', rec: 'Receptions', rec_yds: 'Receiving yds', rush_yds: 'Rushing yds', pass_yds: 'Passing yds'};
      ['05', '15', '25', '35', '45'].forEach(x => { KL['sog' + x] = 'Shots'; });
      const fresh = x => x && x.fetched && Date.now() - new Date(x.fetched) < 12 * 3600e3;
      const P0 = v => Array.isArray(v) ? v[0] : v;
      const link = v => { const u = Array.isArray(v) ? v[1] : null; if (!u) return null;
        return u.startsWith('http') ? u : `https://www.gambly.com/deeplink/odds/${u}?utm_campaign=${encodeURIComponent((PR && PR.partner) || '')}`; };
      const amS = o => (o > 0 ? '+' : '') + o, pc = v => v == null ? '–' : (v * 100).toFixed(1) + '%';
      const median = a => { a = a.slice().sort((x, y) => x - y); const n = a.length; return n ? (n % 2 ? a[(n - 1) / 2] : (a[n / 2 - 1] + a[n / 2]) / 2) : null; };
      // markets: {gid, who, what, kind ('lines'|'props'), p (ours), A: {book: odds} for this side, B: {book: odds} other side}
      const mk = [];
      for (const g of games){
        const G = fresh(BK) && BK.games && BK.games[String(g.id)];
        if (G){
          const main = o => Object.entries(o || {}).sort((a, b) => Object.values(b[1]).reduce((n, x) => n + Object.keys(x).length, 0) - Object.values(a[1]).reduce((n, x) => n + Object.keys(x).length, 0))[0];
          for (const [sd, ot] of [['away', 'home'], ['home', 'away']]) mk.push({gid: g.id, who: g[sd] || sd, what: 'Moneyline', kind: 'lines', A: (G.ml || {})[sd], B: (G.ml || {})[ot]});
          const sp = main(G.spread); if (sp) for (const [sd, ot] of [['away', 'home'], ['home', 'away']]){ const L = sd === 'home' ? +sp[0] : -sp[0];
            mk.push({gid: g.id, who: g[sd] || sd, what: `Spread ${L > 0 ? '+' : L < 0 ? '−' : ''}${Math.abs(L)}`, kind: 'lines', A: sp[1][sd], B: sp[1][ot]}); }
          const tt = main(G.total); if (tt) for (const [sd, ot] of [['over', 'under'], ['under', 'over']]) mk.push({gid: g.id, who: sd === 'over' ? 'Over' : 'Under', what: `Total ${tt[0]}`, kind: 'lines', A: tt[1][sd], B: tt[1][ot]});
        }
        const X = fresh(PR) && PR.props && PR.props[String(g.id)];
        for (const [nm, kinds] of Object.entries(X || {})) for (const [k, e] of Object.entries(kinds)){
          if (!KL[k]) continue;
          const name = nm.replace(/\b\w/g, c => c.toUpperCase()), line = e.l ?? (k.startsWith('sog') ? (+k.slice(3) / 10) : null);
          const lab = line != null && k !== 'goal' && k !== 'td' ? ` ${line}` : '';
          mk.push({gid: g.id, who: name, what: `${KL[k]}${lab ? ' o' + lab.trim() : ''}`, kind: 'props', p: e.p, A: e.o, B: e.u});
          if (e.u) mk.push({gid: g.id, who: name, what: `${KL[k]}${lab ? ' u' + lab.trim() : ''}`, kind: 'props', p: e.p == null ? null : 1 - e.p, A: e.u, B: e.o});
        }
      }
      // each book's power exponent for one-way prices, from its two-way pairs here (median); default 1.05
      const ks = {};
      for (const m of mk) for (const [b, v] of Object.entries(m.A || {})){ const o = m.B && m.B[b]; if (o == null || SKIP.has(b)) continue;
        const a = 1 / dec(P0(v)), c = 1 / dec(P0(o)); if (a + c <= 1) continue;
        let lo = 1, hi = 3; for (let i = 0; i < 40; i++){ const kk = (lo + hi) / 2; (a ** kk + c ** kk > 1) ? lo = kk : hi = kk; }
        (ks[b] = ks[b] || []).push((lo + hi) / 2); }
      const K = b => ks[b] ? median(ks[b]) : 1.05;
      const chance = (m, b) => { const v = m.A[b]; if (v == null) return null; const a = 1 / dec(P0(v)); const o = m.B && m.B[b];
        if (o != null){ const c = 1 / dec(P0(o)); return a / (a + c); } return Math.min(a ** K(b), .999); };
      const rows = [];
      for (const m of mk){
        if (!m.A) continue;
        const W = m.kind === 'props' ? W_PROPS : W_LINES;
        const books = Object.keys(m.A).filter(b => !SKIP.has(b));
        const ch = Object.fromEntries(books.map(b => [b, chance(m, b)]).filter(([, q]) => q != null));
        const fairEx = ex => { let sw = 0, sq = 0; for (const [b, q] of Object.entries(ch)) if (b !== ex && W[b]){ sw += W[b]; sq += W[b] * q; } return sw ? sq / sw : null; };
        const medEx = ex => median(Object.entries(ch).filter(([b]) => b !== ex && m.B && m.B[b] != null).map(([, q]) => q));
        let best = null;
        for (const b of books){ if (NOBET.has(b)) continue; const f = fairEx(b); if (f == null) continue;
          const o = P0(m.A[b]), ev = f * dec(o) - 1, md = medEx(b);
          if (!best || ev > best.ev || (ev === best.ev && MY.includes(b))) best = {b, o, ev, f, u: link(m.A[b]), evm: md == null ? null : md * dec(o) - 1}; }
        if (!best) continue;
        const nW = Object.keys(ch).filter(b => b !== best.b && W[b]).length;
        rows.push({...m, best, fair: fairEx(null), med: medEx(null), nW});
      }
      const glab = Object.fromEntries(games.map(g => [String(g.id), g.label]));
      let list = rows.filter(r => r.nW >= 2 && (WV.long || r.best.f >= .08) && (!WV.mine || MY.includes(r.best.b)) && (!WV.edge || r.best.ev >= .02)).sort((a, b) => b.best.ev - a.best.ev);
      const total = list.length, LIM = 60;
      if (!WV.all) list = list.slice(0, LIM);
      const trs = list.map(r => `<tr><td class="l name"><b>${esc(r.who)}</b><small>${esc(r.what)} · ${esc(glab[String(r.gid)] || '')}</small></td>
        <td>${r.p == null ? '–' : pc(r.p)}</td>
        <td title="Weighted fair from ${r.nW} book${r.nW === 1 ? '' : 's'} (the priced book left out)"><b>${pc(r.best.f)}</b><small>${r.nW} book${r.nW === 1 ? '' : 's'}</small></td>
        <td class="muted">${pc(r.med)}</td>
        <td class="l">${r.best.u ? `<a href="${esc(r.best.u)}" target="_blank" rel="noopener">${amS(r.best.o)} <span class="${MY.includes(r.best.b) ? 'mybk' : ''}">${esc(FULL[r.best.b] || r.best.b)}</span> ↗</a>` : `${amS(r.best.o)} <span class="${MY.includes(r.best.b) ? 'mybk' : ''}">${esc(FULL[r.best.b] || r.best.b)}</span>`}</td>
        <td class="${r.best.ev >= .02 ? 'pe' : ''}">${r.best.ev >= 0 ? '+' : ''}${(r.best.ev * 100).toFixed(1)}%</td>
        <td class="muted">${r.best.evm == null ? '–' : (r.best.evm >= 0 ? '+' : '') + (r.best.evm * 100).toFixed(1) + '%'}</td></tr>`).join('');
      const wl = (lab, W) => `<div><b>${lab}</b> ${Object.entries(W).sort((a, b) => b[1] - a[1]).map(([b, w]) => `${esc(FULL[b] || b)} ${w}`).join(' · ')} · everything else off</div>`;
      const chip = (k, lab) => `<button type="button" data-wv="${k}" aria-pressed="${!!WV[k]}">${lab}</button>`;
      return `<section class="panel wv"><div class="phead"><h2>Weighted fair <span class="beta">experimental</span></h2><span class="small muted">${MY.length ? '<span class="mybk">your books in green</span>' : ''}</span></div>
        <div class="wvc">${chip('edge', '2%+ edge only')}${chip('mine', 'Your books only')}${chip('long', 'Include longshots')}</div>
        <details class="how"><summary>How this works</summary><p class="note">Fair chance = a weighted average of each book’s no-vig chance, using your weights; the book being priced is left out of its own fair. Two-sided prices are de-vigged multiplicatively; one-sided prices (like a lone “yes”) with the power method, each book’s exponent fit from its own two-sided lines tonight. Edge = weighted fair × the payout − 1, at the best price anywhere. A fair needs 2+ weighted books; longshots (fair under 8%) are hidden unless you include them, since one-sided longshot prices de-vig least reliably. “Median” is the plain median of the books, for comparison. In private tracking so far (24 games), the weighted fair was slightly more accurate on anytime goals and flagged about a third fewer edges, and the ones it dropped lost money. Edges against the market alone have still lost overall (the biggest gaps are usually stale prices), so treat these as a check, not picks.</p>${wl('Game lines:', W_LINES)}${wl('Player props:', W_PROPS)}</details>
        ${total ? `<div class="tw"><table class="t wvt"><thead><tr><th class="l">Bet</th><th>Ours</th><th>Weighted fair</th><th>Median</th><th class="l">Best price</th><th>Edge</th><th title="Edge against the median fair">vs median</th></tr></thead><tbody>${trs}</tbody></table></div>
        ${total > LIM ? `<button type="button" class="wvmore" data-wv="all">${WV.all ? 'Show top ' + LIM : 'Show all ' + total}</button>` : ''}`
        : `<p class="note">${WV.edge ? 'No price beats the weighted fair by 2% right now.' : 'No prices yet.'}</p>`}</section>`;
    },
    // 'Book odds' tab: every game line and player prop with each book's price side by side.
    // games: [{id, label}] in page order. Filters (game / market / player search) work on the DOM, so typing keeps focus.
    // games for pages without a schedule of their own (the Markets page): from the live rows, soonest first
    games(){
      const G = {};
      const now = Date.now();                                   // games not started yet
      for (const r of (D ? D.rows : [])) if (r.g != null && !G[r.g] && !(r.start && new Date(r.start) <= now)) G[r.g] = {id: r.g, label: `${r.a} @ ${r.h}`, away: r.a, home: r.h, start: r.start};
      return Object.values(G).sort((a, b) => String(a.start || '').localeCompare(String(b.start || '')));
    },
    oddsView(games){
      const KL = {goal: 'Anytime goal', td: 'Anytime TD', rec: 'Receptions', rec_yds: 'Receiving yds', rush_yds: 'Rushing yds', pass_yds: 'Passing yds', sog: 'Shots on goal',
                  pts: 'Points', reb: 'Rebounds', ast: 'Assists', fg3m: 'Threes'};
      ['05', '15', '25', '35', '45'].forEach(x => { KL['sog' + x] = 'Shots'; });
      const mkey = k => k.startsWith('sog') ? 'sog' : k;
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
          const pinOnly = pinOk(e) ? (e.pin.pn ?? (e.pin.parts ? (e.pin.parts.Pinnacle ?? null) : e.pin.o)) : null;
          const pin = pinOk(e) ? (OV.ref === 'pin' ? pinOnly : e.pin.o) : null;
          const pl = e.pin ? (e.pin.pl ?? e.pin.l) : null;
          for (const [al, x] of Object.entries(e.a || {}).sort((a, b) => a[0] - b[0])){
            const ap = (OV.ref === 'pin' ? (x.pin_p ?? x.pin) : x.pin) ?? null, base = {gid: g.id, m: mkey(k), who: name, w: e.w, pw: ap != null ? 3 : null, pl, alt: 1};
            add({...base, o: 11, what: `${KL[k] || k} o${al}`, p: x.p, pin: ap}, x.o, PR.partner);
            add({...base, o: 11.5, what: `${KL[k] || k} u${al}`, p: x.p == null ? null : 1 - x.p, pin: ap == null ? null : 1 - ap}, x.u, PR.partner);
          }
          if (e.l == null) add({gid: g.id, m: mkey(k), o: 10, who: name, what: KL[k] || k, p: e.p, w: e.w, pin, pw: pin != null ? e.pin.w : null}, e.o, PR.partner);
          else { add({gid: g.id, m: mkey(k), o: 10, who: name, what: `${KL[k] || k} o${e.l}`, p: e.p, w: e.w, pin, pw: pin != null ? e.pin.w : null, pl}, e.o, PR.partner);
                 add({gid: g.id, m: mkey(k), o: 10.5, who: name, what: `${KL[k] || k} u${e.l}`, p: e.p == null ? null : 1 - e.p, w: e.w, pin: pin == null ? null : 1 - pin, pw: pin != null ? e.pin.w : null, pl}, e.u, PR.partner); }
        }
      }
      if (!rows.length) return `<section class="panel"><div class="phead"><h2>Sportsbooks</h2></div><p class="note">No sportsbook prices yet for these games. Game lines appear about a day before each game; player props when Unabated's props feed is up (it retries every 15 minutes).</p></section>`;
      const PMG = ['Pinnacle', 'Kalshi', 'Polymarket', 'Poly US', 'Novig', 'ProphetX'];
      const ord = (a, b) => (ORDER.indexOf(a) + 99) % 99 - (ORDER.indexOf(b) + 99) % 99 || a.localeCompare(b);
      const groups = [['My books', MY.filter(k => used.has(k))], ['Sharp book & exchanges', PMG.filter(k => used.has(k) && !MY.includes(k))],
                      ['Sportsbooks', [...used].filter(k => !PMG.includes(k) && !MY.includes(k)).sort(ord)]].filter(g => g[1].length);
      const cols = groups.flatMap(g => g[1]), first = new Set(groups.map(g => g[1][0]));
      const mkts = [...new Set(rows.map(r => r.m))], mlab = m => m === 'lines' ? 'Game lines' : KL[m] || m;
      const glab = Object.fromEntries(games.map(g => [String(g.id), g.label]));
      const show = r => (OV.g === 'all' || String(r.gid) === OV.g) && (OV.m === 'all' || r.m === OV.m) && (!OV.q || norm(r.who).includes(norm(OV.q)));
      const showRow = r => show(r) && (!r.alt || OV.alt);
      const amS = o => (o > 0 ? '+' : '') + o;
      const fairAm = q => q >= .5 ? -Math.round(100 * q / (1 - q)) : Math.round(100 * (1 - q) / q);
      let body = '', last = null;
      const hasPin = rows.some(r => r.pin != null);
      rows.sort((a, b) => games.findIndex(g => String(g.id) === String(a.gid)) - games.findIndex(g => String(g.id) === String(b.gid)) || a.o - b.o || (a.m < b.m ? -1 : a.m > b.m ? 1 : 0) || (b.p ?? 0) - (a.p ?? 0));
      for (const r of rows){
        if (r.gid !== last){ last = r.gid; body += `<tr class="grp" data-ovg="${esc(String(r.gid))}"${OV.g !== 'all' && OV.g !== String(r.gid) ? ' hidden' : ''}><td colspan="${cols.length + 2 + (hasPin ? 1 : 0)}">${esc(glab[String(r.gid)] || r.gid)}</td></tr>`; }
        const best = Math.max(...Object.entries(r.P).filter(([k]) => !NOBET.has(k)).map(([, x]) => dec(x.o)));
        const cells = cols.map(k => { const x = r.P[k]; if (!x) return `<td class="muted${MY.includes(k) ? ' my' : ''}${first.has(k) ? ' g0' : ''}">–</td>`;
          const ev = r.p == null ? null : r.p * dec(x.o) - 1, v = ev != null && !r.w && ev >= ((FL && FL.min_ev) || .05);
          const t = `${FULL[k] || k} ${amS(x.o)}${ev != null ? ` · our edge ${ev >= 0 ? '+' : ''}${(ev * 100).toFixed(1)}%` : ''}${r.pin != null && !NOBET.has(k) ? ` · vs Pinnacle no-vig ${r.pin * dec(x.o) >= 1 ? '+' : ''}${((r.pin * dec(x.o) - 1) * 100).toFixed(1)}%` : ''}${x.u ? ' · tap to open this bet' : ''}`;
          const pe = r.pin != null && !NOBET.has(k) ? r.pin * dec(x.o) - 1 : null;
          const inner = `<span class="pp">${amS(x.o)}</span>${pe != null ? `<small class="${pe > 0 ? 'pe' : ''}">${pe > 0 ? '+' : ''}${(pe * 100).toFixed(1)}%</small>` : ''}`, cls = `${dec(x.o) === best ? 'bst' : ''}${v ? ' v' : ''}${MY.includes(k) ? ' my' : ''}${first.has(k) ? ' g0' : ''}`;
          return `<td class="${cls}" title="${esc(t)}">${x.u ? `<a href="${esc(x.u)}" target="_blank" rel="noopener">${inner}</a>` : inner}</td>`; }).join('');
        const fair = r.p == null ? '<td class="muted">–</td>' : `<td title="Our chance ${(r.p * 100).toFixed(1)}%${r.w ? ' (low trust: outside our top 3 or likely news)' : ''}">${amS(fairAm(r.p))}<small>${(r.p * 100).toFixed(0)}%</small></td>`;
        const pinCell = hasPin ? (r.pin != null ? `<td title="Pinnacle no-vig ${(r.pin * 100).toFixed(1)}% (${pinHow(r.pw, r.pl)})">${r.pw === 3 ? '<i class="anc" aria-hidden="true">≈</i>' : ''}${amS(fairAm(r.pin))}<small>${(r.pin * 100).toFixed(0)}%</small></td>` : '<td class="muted">–</td>') : '';
        body += `<tr data-ovr${r.alt ? ' data-alt="1"' : ''} data-ovg="${esc(String(r.gid))}" data-ovm="${esc(r.m)}" data-ovn="${esc(norm(r.who))}"${showRow(r) ? '' : ' hidden'}><td class="l name"><b>${esc(r.who)}</b><small>${esc(r.what)}</small></td>${fair}${pinCell}${cells}</tr>`;
      }
      // props where a bettable book (yours, if chosen) pays more than Pinnacle's no-vig price: best book per prop, by EV
      let pev = '';
      if (hasPin){
        // alt lines only where the carried-over fair chance is 10-90% (our distribution's far tails aren't reliable)
        const its = rows.filter(r => r.pin != null && (r.pw !== 3 || (r.pin >= .10 && r.pin <= .90))).map(r => {
          const c = Object.entries(r.P).filter(([k]) => !NOBET.has(k)).map(([k, x]) => ({k, x, ev: r.pin * dec(x.o) - 1})).sort((a, b) => b.ev - a.ev)[0];
          return c && c.ev > 0 && (r.pw !== 3 || c.ev <= .25) ? {r, ...c} : null; }).filter(Boolean).sort((a, b) => b.ev - a.ev);   // carried-over (≈) lines: a 25%+ "edge" is our tail, not the book
        const li = ({r, k, x, ev}) => {
          const inner = `<b>${esc(r.who)}</b><span class="w">${esc(r.what)}${r.alt ? ' <span class="altb">alt</span>' : ''} · fair ${r.pw === 3 ? '≈' : ''}${(r.pin * 100).toFixed(0)}%</span><span class="px">${amS(x.o)} <span class="${MY.includes(k) ? 'mine' : ''}">${esc(BKS[k] || k)}</span><i>+${(ev * 100).toFixed(1)}%</i></span>`;
          return `<li data-ovr data-ovg="${esc(String(r.gid))}" data-ovm="${esc(r.m)}" data-ovn="${esc(norm(r.who))}"${show(r) ? '' : ' hidden'}>${x.u ? `<a href="${esc(x.u)}" target="_blank" rel="noopener" title="${esc(FULL[k] || k)} pays ${(ev * 100).toFixed(1)}% more than Pinnacle’s fair price. Tap to open this bet.">${inner}</a>` : `<a>${inner}</a>`}</li>`; };
        const TOP = 12;
        pev = `<div class="pev"><h3>${OV.ref === 'pin' ? 'Beats Pinnacle’s no-vig' : 'Beats the sharp line'} · all books${MY.length ? ' · <span class="mine">yours in green</span>' : ''}</h3>${its.length ? `<ol>${its.slice(0, TOP).map(li).join('')}</ol>${its.length > TOP ? `<details><summary>${its.length - TOP} more</summary><ol>${its.slice(TOP).map(li).join('')}</ol></details>` : ''}`
          : `<p class="small muted" style="margin:0">No book pays more than Pinnacle’s fair price right now.</p>`}${its.some(i => i.r.pw === 3) ? `<p class="small muted" style="margin:6px 0 0">≈ Pinnacle doesn’t post this number (alt line, or a different main line), so its price is carried over with our distribution’s shape.</p>` : ''}</div>`;
      }

      // Best lines: one card per player-stat, its best line on the face, the whole ladder inside (default view)
      let cards = '';
      if (OV.view === 'cards'){
        const C = {};
        for (const r of rows){
          if (r.m === 'lines') continue;
          const key = `${r.gid}|${r.who}|${r.m}`, c = C[key] = C[key] || {r, lines: []};
          const q = r.pin;
          const bk = Object.entries(r.P).filter(([k]) => !NOBET.has(k)).map(([k, x]) => ({k, x, d: dec(x.o)}));
          const any = bk.slice().sort((a, b) => b.d - a.d)[0], mineB = bk.filter(b => MY.includes(b.k)).sort((a, b) => b.d - a.d)[0];
          const pick = MY.length ? mineB : any;
          const tail = r.pw === 3 && q != null && (q < .10 || q > .90);
          const edge = q != null && pick && !tail ? q * pick.d - 1 : null;
          c.lines.push({r, q, any, mineB, pick, edge: edge != null && r.pw === 3 && edge > .25 ? null : edge});
        }
        const L = Object.values(C).map(c => ({...c, best: c.lines.filter(x => x.edge != null).sort((a, b) => b.edge - a.edge)[0]}))
          .sort((a, b) => (b.best ? b.best.edge : -9) - (a.best ? a.best.edge : -9));
        const side = w => { const t = String(w).split(' ').pop(); return t[0] === 'o' ? 'over ' + t.slice(1) : t[0] === 'u' ? 'under ' + t.slice(1) : t; };
        const bkl = b => b ? `${amS(b.x.o)} <span class="${MY.includes(b.k) ? 'mine' : ''}">${esc(BKS[b.k] || b.k)}</span>` : '<span class="muted">–</span>';
        const lnk = (b, inner) => b && b.x.u ? `<a href="${esc(b.x.u)}" target="_blank" rel="noopener">${inner}</a>` : inner;
        const card = (c, i) => {
          const b = c.best, r = c.r, ed = b && b.edge > 0;
          const face = b ? `<span class="pcb">${esc(side(b.r.what))}${b.r.alt ? ' <span class="altb">alt</span>' : ''} ${lnk(b.pick, `<b>${amS(b.pick.x.o)}</b> ${esc(BKS[b.pick.k] || b.pick.k)}`)} <i class="${b.edge > 0 ? 'pe' : 'ne'}">${b.edge > 0 ? '+' : ''}${(b.edge * 100).toFixed(1)}%</i></span>`
            : '<span class="pcb muted">no sharp line to compare</span>';
          const lines = c.lines.slice().sort((a, x) => parseFloat(String(a.r.what).split(' ').pop().slice(1)) - parseFloat(String(x.r.what).split(' ').pop().slice(1)) || a.r.o - x.r.o);
          // watchlist + odds chart (watch.js; owner 2026-10-26): each over line at your best price (or the best anywhere)
          const WB = x => { const w = String(x.r.what).split(' ').pop(), bk = x.mineB || x.any;
            if (!window.WATCH || w[0] !== 'o' || !bk) return '';
            return ' ' + WATCH.both({sport, gid: String(r.gid), name: norm(r.who), kind: r.m, line: parseFloat(w.slice(1)), book: bk.k, o: bk.x.o, fair: x.q,
                                     glab: glab[String(r.gid)] || '', lab: `${r.who} ${side(x.r.what)} ${mlab(r.m)}`}); };
          const tr = x => `<tr${x === b ? ' class="top"' : ''}><td class="l">${esc(side(x.r.what))}${x.r.alt ? ' <span class="altb">alt</span>' : ''}${WB(x)}</td><td>${x.r.p != null ? Math.round(x.r.p * 100) + '%' : '–'}</td>
            <td>${x.q != null ? `${x.r.pw === 3 ? '≈' : ''}${Math.round(x.q * 100)}%` : '–'}</td><td>${MY.length ? lnk(x.mineB, bkl(x.mineB)) : ''}</td><td>${lnk(x.any, bkl(x.any))}</td>
            <td class="${x.edge > 0 ? 'pe' : 'ne'}">${x.edge != null ? (x.edge > 0 ? '+' : '') + (x.edge * 100).toFixed(1) + '%' : '–'}</td></tr>`;
          return `<details class="pcard" data-ovr data-ovg="${esc(String(r.gid))}" data-ovm="${esc(r.m)}" data-ovn="${esc(norm(r.who))}"${ed ? ' data-ed="1"' : ''}${i >= 15 ? ' data-more="1"' : ''}${(OV.edge && !ed) || i >= 15 || !show(r) ? ' hidden' : ''}>
            <summary><span class="pcw"><b>${esc(r.who)}</b><small>${esc(mlab(r.m))} · ${esc(glab[String(r.gid)] || '')}</small></span>${face}</summary>
            <div class="tw"><table class="t pct"><thead><tr><th class="l">Line</th><th>Ours</th><th>Fair</th>${MY.length ? '<th>Your best</th>' : '<th></th>'}<th>Best anywhere</th><th>Edge</th></tr></thead><tbody>${lines.map(tr).join('')}</tbody></table></div></details>`; };
        const nEd = L.filter(c => c.best && c.best.edge > 0).length;
        cards = `<div class="pcards">${L.length ? L.map(card).join('') : ''}<p class="small muted pcempty"${nEd || !OV.edge ? ' hidden' : ''}>No line beats the ${OV.ref === 'pin' ? 'Pinnacle' : 'sharp'} fair price${MY.length ? ' at your books' : ''} right now. Turn off “Edges only” to see every player.</p>
          ${L.length > 15 ? `<button type="button" class="pcmore" data-pcmore>Show more</button>` : ''}</div>`;
      }
      const t = x => x && x.fetched ? new Date(x.fetched).toLocaleTimeString([], {hour: 'numeric', minute: '2-digit'}) : null;
      const when = [t(fresh(BK) && BK) && `game lines ${t(BK)}`, t(fresh(PR) && PR) && `props ${t(PR)}`].filter(Boolean).join(' · ');
      return `<section class="panel ov"><div class="phead"><h2>Sportsbooks</h2><span class="small muted">${esc(when)}</span></div>
        <div class="ovc"><select id="ov-g" aria-label="Game"><option value="all">All games</option>${games.filter(g => rows.some(r => String(r.gid) === String(g.id))).map(g => `<option value="${esc(String(g.id))}"${OV.g === String(g.id) ? ' selected' : ''}>${esc(g.label)}</option>`).join('')}</select>
        <div class="ovm" role="group" aria-label="Market">${['all', ...mkts].map(m => `<button type="button" data-ovm-pick="${esc(m)}" aria-pressed="${OV.m === m}">${m === 'all' ? 'All' : esc(mlab(m))}</button>`).join('')}${rows.some(r => r.alt) ? `<button type="button" class="ovalt" data-ovalt aria-pressed="${OV.alt}" title="Show alternate lines in the table">+ Alt lines</button>` : ''}</div>
        <input id="ov-q" type="search" placeholder="Player or team" value="${esc(OV.q)}" aria-label="Search players"></div>
        <div class="ovsw"><span class="seg" role="group" aria-label="View"><button type="button" data-ovview="cards" aria-pressed="${OV.view === 'cards'}">Best lines</button><button type="button" data-ovview="table" aria-pressed="${OV.view === 'table'}">Full table</button></span>
          <span class="seg" role="group" aria-label="Fair price from"><span class="small muted">Fair from</span><button type="button" data-ovref="sharp" aria-pressed="${OV.ref === 'sharp'}" title="Pinnacle, Novig and ProphetX averaged, each with its margin removed">Sharp consensus</button><button type="button" data-ovref="pin" aria-pressed="${OV.ref === 'pin'}" title="Pinnacle’s own no-vig price only">Pinnacle only</button></span>
          ${OV.view === 'cards' ? `<button type="button" class="ovalt" data-ovedge aria-pressed="${OV.edge}">Edges only</button>` : ''}</div>
        ${OV.view === 'cards' ? `<p class="small muted">Each card is one player and stat with its best line: the price ${MY.length ? 'at your books' : 'anywhere'} against the ${OV.ref === 'pin' ? 'Pinnacle' : 'sharp consensus'} fair chance (Kalshi includes its fee). Tap a card for every line, main and alternate. ≈ = a line the sharp books don’t post: its fair price comes from the other books’ own over / under prices at that number. Your books: ${MY.length ? esc(MY.map(k => FULL[k] || k).join(', ')) : 'none chosen'} <button type="button" class="bkset" data-bkset="ov" aria-expanded="${bkOpen === 'ov'}">change ▾</button></p>` : ''}
        <p class="small muted"${OV.view === 'cards' ? ' hidden' : ''}>Best price in each row is underlined; <span style="color:var(--good)">green</span> = our chance beats it by 5%+. Tap a price to open the bet. Kalshi includes its fee. Hard Rock props (anytime goal / TD, shots on goal, yardage and catches, NBA stats) are added in the last hours before each game. Your books: ${MY.length ? esc(MY.map(k => FULL[k] || k).join(', ')) : 'none chosen'} <button type="button" class="bkset" data-bkset="ov" aria-expanded="${bkOpen === 'ov'}">change ▾</button></p>
        ${bkOpen === 'ov' ? `<div class="bkpanel">${[...used].sort(ord).map(k => `<button type="button" data-bkpick="${esc(k)}" aria-pressed="${MY.includes(k)}">${esc(FULL[k] || k)}</button>`).join('')}<button type="button" data-bkpick="" class="bkall">All books</button></div>` : ''}
        ${OV.view === 'cards' ? cards : pev}<div class="tw ovw"${OV.view === 'cards' ? ' hidden' : ''}><table class="t ovt"><thead><tr><th class="l">Bet</th><th>Ours</th>${hasPin ? (OV.ref === 'pin' ? '<th title="Pinnacle’s own no-vig price">Pinnacle<br>no-vig</th>' : '<th title="Sharp no-vig: Pinnacle, Novig and ProphetX averaged with their margins removed (Pinnacle alone when it’s the only one)">Sharp<br>no-vig</th>') : ''}${groups.map(([lab, ks]) => ks.map((k, i) =>
          `<th class="${MY.includes(k) ? 'my' : ''}${i === 0 ? ' g0' : ''}">${i === 0 ? `<small class="gl">${esc(lab)}</small>` : '<small class="gl">&nbsp;</small>'}${esc(BKS[k] || k)}</th>`).join('')).join('')}</tr></thead><tbody>${body.replace(/<td class="([^"]*)" title/g, (m, c) => m)}</tbody></table></div></section>`;
    },
    // best YES price for a player prop at the viewer's books (all books if none chosen): {k, o, u, mine} or null
    propBest(gid, name, kind){
      if (!PR || !PR.props || Date.now() - new Date(PR.fetched) > 12 * 3600e3) return null;
      const e = ((PR.props[String(gid)] || {})[norm(name)] || {})[kind]; if (!e || !e.o) return null;
      const all = Object.entries(e.o).sort((a, b) => dec(b[1][0]) - dec(a[1][0]));
      const L = all.filter(([k]) => mine(k)), c = (L.length ? L : all)[0];
      const u = c[1][1] ? (String(c[1][1]).startsWith('http') ? c[1][1] : `https://www.gambly.com/deeplink/odds/${c[1][1]}?utm_campaign=${encodeURIComponent(PR.partner || '')}`) : null;
      return {k: c[0], name: FULL[c[0]] || BKS[c[0]] || c[0], o: c[1][0], u, mine: !!L.length, w: !!e.w};
    },
    myBooks: () => MY.slice(),
    // the books' main line for a player prop (props file `l`), for the projected box score's 'above the line' highlight
    propLine(gid, name, kind){
      const e = PR && PR.props && Date.now() - new Date(PR.fetched) < 12 * 3600e3 ? ((PR.props[String(gid)] || {})[norm(name)] || {})[kind] : null;
      if (e && e.l != null) return +e.l;
      // a started game's props leave the live file: the line frozen at its start (owner 2026-10-25: highlights vanished)
      const f = BL && BL[String(gid)] && ((BL[String(gid)].p || {})[norm(name)] || {})[kind];
      return f != null ? +f : null;
    },
    // one leg of a same-game combo, market-anchored like the payout cards: game lines ('ml' side home|away, 'total' side
    // over at line) = ours averaged with Pinnacle's no-vig (else the books' median no-vig); props ('sog25', ...) = ours 50/50
    // with the books' two-way no-vig median (Pinnacle at WPIN weights when pulled), 'goal' = blend(). Returns {q, m, b}
    // (b = best price at the viewer's books, else anywhere) or null with no market reference.
    sgLeg(gid, kind, side, line, p, name){
      if (p == null) return null;
      const med = a => { a = a.slice().sort((x, y) => x - y); const n = a.length; return n ? (n % 2 ? a[(n - 1) / 2] : (a[n / 2 - 1] + a[n / 2]) / 2) : null; };
      if (kind === 'goal'){
        const m = window.MKT.propMarket(gid, name, 'goal'); if (m == null) return null;
        return {q: window.MKT.blend(gid, name, 'goal', p), m, b: window.MKT.propBest(gid, name, 'goal')};
      }
      if (kind !== 'ml' && kind !== 'total'){
        const e = PR && PR.props && ((PR.props[String(gid)] || {})[norm(name)] || {})[kind]; if (!e || !e.o) return null;
        let m = null, w = .5;
        if (pinOk(e)) { m = e.pin.o; w = wOf(sport, kind, true); }
        else m = med(Object.keys(e.o).filter(k => e.u && e.u[k] && !SKIP.has(k)).map(k => { const a = 1 / dec(e.o[k][0]), c = 1 / dec(e.u[k][0]); return a / (a + c); }));
        if (m == null) return null;
        return {q: w * p + (1 - w) * m, m, b: window.MKT.propBest(gid, name, kind)};
      }
      const G = BK && BK.games && BK.games[String(gid)]; if (!G) return null;
      let o = G[kind]; if (kind !== 'ml') o = o && o[String(+line)]; if (!o || !o[side]) return null;
      const other = {home: 'away', away: 'home', over: 'under', under: 'over'}[side];
      const v = (sd, k) => { const x = o[sd] && o[sd][k]; return x == null ? null : Array.isArray(x) ? x[0] : x; };
      const pa = v(side, 'Pinnacle'), pb = v(other, 'Pinnacle');
      const m = pa != null && pb != null ? (1 / dec(pa)) / (1 / dec(pa) + 1 / dec(pb))
        : med(Object.keys(o[side]).filter(k => !SKIP.has(k) && v(other, k) != null).map(k => { const a = 1 / dec(v(side, k)), c = 1 / dec(v(other, k)); return a / (a + c); }));
      if (m == null) return null;
      const all = Object.entries(o[side]).filter(([k]) => !SKIP.has(k) && !NOBET.has(k)).map(([k, x]) => [k, Array.isArray(x) ? x[0] : x, Array.isArray(x) ? x[1] : null]).sort((a, c) => dec(c[1]) - dec(a[1]));
      const L = all.filter(([k]) => mine(k)), c = (L.length ? L : all)[0];
      return {q: (p + m) / 2, m, b: c ? {k: c[0], name: FULL[c[0]] || BKS[c[0]] || c[0], o: c[1], u: c[2], mine: !!L.length} : null};
    },
    // the market's chance for a player prop: median implied chance at the exchanges / prediction markets (low margin;
    // Kalshi already fee-adjusted), less ~3% for their margin. null when no exchange prices it.
    // our chance p blended with the market's for a prop: Pinnacle at WPIN weights, else 50/50 with the exchanges
    blend(gid, name, kind, p){
      const e = PR && PR.props && ((PR.props[String(gid)] || {})[norm(name)] || {})[kind];
      const m = window.MKT.propMarket(gid, name, kind); if (m == null) return p;
      const w = wOf(sport, kind, pinOk(e)); return w * p + (1 - w) * m;
    },
    propMarket(gid, name, kind){
      if (!PR || !PR.props) return null;
      const e = ((PR.props[String(gid)] || {})[norm(name)] || {})[kind]; if (!e || !e.o) return null;
      if (pinOk(e)) return e.pin.o;                     // Pinnacle's two-way no-vig when pulled (NHL anytime goals, ~2 h before puck drop)
      if (kind === 'goal' && e.f4) return e.f4.o;      // else the four-book fair (Kalshi / FD / Fanatics / DK)
      const q = Object.entries(e.o).filter(([k]) => ['Novig', 'Kalshi', 'Polymarket', 'Poly US', 'ProphetX'].includes(k)).map(([, v]) => 1 / dec(v[0])).sort((a, b) => a - b);
      if (!q.length) return null;
      const med = q.length % 2 ? q[(q.length - 1) / 2] : (q[q.length / 2 - 1] + q[q.length / 2]) / 2;
      return med / 1.03;
    },
    // Parlays by payout tier, built from legs with an edge at the viewer's books. Legs: player props (props file) and
    // game lines (page supplies our chance). Each leg's chance = ours averaged with the market's (exchanges for props,
    // the books' no-vig for game lines); a leg must beat its own price. One leg per game, so legs are independent:
    // chance = product, payout = product of prices, and the parlay cashes more often than it pays when every leg does.
    // opts: {games:[{id,label,start}], gameLegs:[{gid,kind:'ml'|'spread'|'total',side,line,p,lab}], propKinds:{kind:label},
    //        propOk(gid,name,kind,p) -> bool, sport, title}
    tierCard(opts){
      const now = Date.now(), legs = [];
      const amS = o => (o > 0 ? '+' : '') + o;
      const median = a => { a = a.slice().sort((x, y) => x - y); const n = a.length; return n ? (n % 2 ? a[(n - 1) / 2] : (a[n / 2 - 1] + a[n / 2]) / 2) : null; };
      const pick = (o, side) => {                       // best price at the viewer's books for one side: [book, odds, link]
        const all = Object.entries(o || {}).filter(([k]) => !SKIP.has(k) && !NOBET.has(k)).map(([k, v]) => [k, Array.isArray(v) ? v[0] : v, Array.isArray(v) ? v[1] : null]).sort((a, b) => dec(b[1]) - dec(a[1]));
        if (opts.anyBook) return all[0] || null;          // best price anywhere (the viewer's books are marked)
        const L = all.filter(([k]) => mine(k)); return L.length ? L[0] : null; };
      const link = u => !u ? null : String(u).startsWith('http') ? u : `https://www.gambly.com/deeplink/odds/${u}?utm_campaign=${encodeURIComponent((PR && PR.partner) || '')}`;
      const live = new Set(opts.games.filter(g => !g.start || new Date(g.start) > now).map(g => String(g.id)));
      const startOf = Object.fromEntries(opts.games.map(g => [String(g.id), g.start || null]));
      const glab = Object.fromEntries(opts.games.map(g => [String(g.id), g.label]));
      // player props
      if (PR && PR.props && now - new Date(PR.fetched) < 12 * 3600e3) for (const [gid, pl] of Object.entries(PR.props)) {
        if (!live.has(gid)) continue;
        for (const [nm, kinds] of Object.entries(pl)) for (const [k, e] of Object.entries(kinds)) {
          if (!opts.propKinds[k] || e.p == null || e.w) continue;
          for (const sd of e.u && !opts.overOnly ? ['o', 'u'] : ['o']) {
            const p = sd === 'o' ? e.p : 1 - e.p, name = nm.replace(/\b\w/g, c => c.toUpperCase());
            if (opts.propOk && !opts.propOk(gid, nm, k, p)) continue;
            const b = pick(e[sd]); if (!b) continue;
            const ex = Object.entries(e[sd] || {}).filter(([bk]) => ['Novig', 'Kalshi', 'Polymarket', 'Poly US', 'ProphetX'].includes(bk)).map(([, v]) => 1 / dec(v[0]));
            const pin = pinOk(e) ? (sd === 'o' ? sharpO(e, b[0]) : 1 - sharpO(e, b[0])) : null;
            const w = pin != null ? wOf(opts.sport, k, true) : ((opts.wOurs || {})[k] ?? .5), m = pin != null ? pin : k === 'goal' && f4O(e, b[0]) != null ? f4O(e, b[0]) : ex.length ? median(ex) / 1.03 : null, q = m != null ? w * p + (1 - w) * m : p;
            if ((opts.sport === 'nfl' && ['rec', 'rec_yds', 'rush_yds', 'pass_yds'].includes(k) || opts.sport === 'nba') && (p < .5 || (m != null && Math.abs(p - m) < .10))) continue;
            const isSog = k.startsWith('sog');
            const leg = {gid, start: startOf[gid], kind: isSog ? 'sog' : k, side: sd, line: isSog ? +k.slice(3) / 10 : (e.l ?? .5), name: nm,
                         lab: `${name} ${e.l != null ? (sd === 'o' ? 'over ' : 'under ') + e.l + ' ' : ''}${opts.propKinds[k]}`, p: +q.toFixed(4), price: b[1], book: b[0]};
            legs.push({gid, leg, kind: opts.propKinds[k], lab: `${name} ${e.l != null ? (sd === 'o' ? 'over ' : 'under ') + e.l + ' ' : ''}${opts.propKinds[k]}`, q, p, m, b, u: link(b[2]), ev: q * dec(b[1]) - 1,
                       slip: {id: `${opts.sport}|${gid}|tier|${nm}|${k}|${sd}`, sport: opts.sport, g: gid, glab: glab[gid], lab: `${name} ${e.l != null ? (sd === 'o' ? 'over ' : 'under ') + e.l + ' ' : ''}${opts.propKinds[k]}`, p: q, side: 'over'}});
          }
        }
      }
      // game lines
      for (const L of opts.gameLegs || []) {
        if (!live.has(String(L.gid)) || L.p == null || (opts.overOnly && L.side === 'under')) continue;
        const G = BK && BK.games && BK.games[String(L.gid)]; if (!G) continue;
        let o = G[L.kind]; if (L.kind !== 'ml') o = o && o[String(+L.line)];
        if (!o || !o[L.side]) continue;
        const b = pick(o[L.side]); if (!b) continue;
        const other = {home: 'away', away: 'home', over: 'under', under: 'over'}[L.side];
        const nv = Object.keys(o[L.side]).filter(k => o[other] && o[other][k] != null && !SKIP.has(k)).map(k => {
          const a = 1 / dec(Array.isArray(o[L.side][k]) ? o[L.side][k][0] : o[L.side][k]), c = 1 / dec(Array.isArray(o[other][k]) ? o[other][k][0] : o[other][k]); return a / (a + c); });
        // the market's chance: Pinnacle's no-vig line when it prices both sides (the sharp benchmark), else the books' median
        const px = (s, k) => { const v = o[s] && o[s][k]; return v == null ? null : 1 / dec(Array.isArray(v) ? v[0] : v); };
        const pa = px(L.side, 'Pinnacle'), pb = px(other, 'Pinnacle');
        const m = pa && pb ? pa / (pa + pb) : nv.length ? median(nv) : null, q = m != null ? (L.p + m) / 2 : L.p;
        const leg = {gid: String(L.gid), start: startOf[String(L.gid)], kind: L.kind, side: L.side,
                     line: L.kind === 'spread' ? (L.side === 'home' ? +L.line : -L.line) : (L.line ?? null),   // spread: that team's handicap
                     name: null, lab: L.lab, p: +q.toFixed(4), price: b[1], book: b[0]};
        legs.push({gid: String(L.gid), leg, kind: L.kind, lab: L.lab, q, p: L.p, m, b, u: link(b[2]), ev: q * dec(b[1]) - 1,
                   slip: {id: `${opts.sport}|${L.gid}|tier|${L.kind}|${L.side}|${L.line}`, sport: opts.sport, g: L.gid, glab: glab[String(L.gid)], lab: L.lab, p: q, side: 'over'}});
      }
      // prediction-market legs (opts.kalshiKinds, e.g. NBA ladders): our chance blended 50/50 with Kalshi's mid, under
      // the NFL props rule (the side our median is on, our number 10+ pts from the mid, spread 3c or less, price 15c+).
      // One leg per player-stat (its best rung). Untested for the NBA: tracked on the Profit page.
      if (opts.kalshiKinds && D && D.rows && (opts.anyBook || mine('Kalshi'))) {
        const bestBy = {};
        for (const r of D.rows) {
          if (r.s !== 'kalshi' || !opts.kalshiKinds[r.k] || !live.has(String(r.g)) || !(r.ya > 0) || r.yb == null || r.l == null) continue;
          const p = ours(r, 'vegas'); if (p == null || r.ya - r.yb > .035) continue;
          const pe = PR && PR.props && ((PR.props[String(r.g)] || {})[norm(r.n)] || {})[r.k];
          const pin = pe && pinOk(pe) && Math.abs(pe.pin.l - r.l) < 1e-9 ? pe.pin.o : null;
          const m = pin != null ? pin : (r.ya + r.yb) / 2, wk = pin != null ? wOf(opts.sport, r.k, true) : r.by >= 1000 ? .10 : r.by >= 100 ? .25 : .5;   // depth-weighted, as pq
          for (const sd of opts.overOnly ? ['o'] : ['o', 'u']) {
            const ps = sd === 'o' ? p : 1 - p, ms = sd === 'o' ? m : 1 - m, c = sd === 'o' ? r.ya : 1 - r.yb;
            if (ps < .5 || (pin == null && Math.abs(p - m) < .10) || c < .15 || c > .95) continue;
            const cost = c + .07 * c * (1 - c), q = wk * ps + (1 - wk) * ms, ev = q / cost - 1;
            const key = r.g + '|' + (r.pid || r.n) + '|' + r.k;
            if (ev <= (opts.minEdge || 0) || (bestBy[key] && bestBy[key].ev >= ev)) continue;
            const o = cost >= .5 ? -Math.round(100 * cost / (1 - cost)) : Math.round(100 * (1 - cost) / cost);
            const n = Math.round(r.l + .5), lab = `${r.n} ${sd === 'o' ? n + '+' : 'under ' + n} ${opts.kalshiKinds[r.k]}`;
            const leg = {gid: String(r.g), start: startOf[String(r.g)], kind: r.k, side: sd, line: r.l, name: norm(r.n), lab, p: +q.toFixed(4), price: o, book: 'Kalshi'};
            bestBy[key] = {gid: String(r.g), leg, kind: r.k, lab, q, p: ps, m: ms, b: ['Kalshi', o, null], u: null, ev,
                           slip: {id: `${opts.sport}|${r.g}|tier|${r.t}|${sd}`, sport: opts.sport, g: r.g, glab: glab[String(r.g)], lab, p: q, side: 'over'}};
          }
        }
        legs.push(...Object.values(bestBy));
      }
      const good = legs.filter(L => L.ev > (opts.minEdge || 0));
      if (!MY.length && !opts.anyBook) return `<section class="panel"><div class="phead"><h2>${esc(opts.title || 'Parlays by payout')}</h2></div><p class="note">Choose your sportsbooks first (the "all books ▾" button under any game's bets): these parlays are priced only at the books you can bet.</p></section>`;
      // per game: the 3 best-edge legs
      const by = {}; good.sort((a, b) => b.ev - a.ev).forEach(L => { (by[L.gid] = by[L.gid] || []).length < 3 && by[L.gid].push(L); });
      const gids = Object.keys(by);
      const TIERS = [['+250 to +600', 3.5, 7], ['+600 to +1500', 7, 16]];
      const best = TIERS.map(() => []);
      const walk = (i, pk, pr, d) => {
        if (pk.length >= 2 && (opts.need || []).every(k => pk.some(L => L.kind === k))) TIERS.forEach(([, lo, hi], t) => { if (d >= lo && d < hi) best[t].push({legs: pk, p: pr, d}); });
        if (pk.length === 4 || d >= 16) return;
        for (let j = i; j < gids.length; j++) for (const L of by[gids[j]]) walk(j + 1, [...pk, L], pr * L.q, d * dec(L.b[1]));
      };
      walk(0, [], 1, 1);
      const rows = best.map(list => list.sort((a, b) => b.p - a.p || a.legs.length - b.legs.length).slice(0, 3));
      if (!rows.some(r => r.length)) return opts.anyBook ? `<section class="panel"><div class="phead"><h2>${esc(opts.title || 'Parlays by payout')}</h2></div><p class="note">No combination with an edge on every leg yet. Prices fill in through the afternoon; check back closer to puck drop.</p></section>` : '';
      // 'best shot': the likeliest combo paying +250 or more whose edge is at least 10%
      const pickBest = opts.anyBook ? best.flat().filter(c => c.p * c.d - 1 >= .10).sort((a, b) => b.p - a.p)[0] : null;
      rows.forEach((list, t) => list.forEach(c => reg(opts.title, TIERS[t][0], c.legs.map(L => L.leg), c.p, c.d)));
      if (pickBest) reg(opts.title, 'Best shot', pickBest.legs.map(L => L.leg), pickBest.p, pickBest.d);
      const bkLab = k => `<span class="${MY.includes(k) ? 'mybk' : ''}">${esc(FULL[k] || k)}</span>`;
      const row = c => `<tr><td class="l">${c.legs.map(L => `<b>${esc(L.lab)}</b> <span class="small muted" title="Our chance ${pct(L.p)}${L.m != null ? ', market ' + pct(L.m) : ''}, blended ${pct(L.q)}">${esc(glab[L.gid] || '')} · ${pct(L.q)} · ${L.u ? `<a href="${esc(L.u)}" target="_blank" rel="noopener">${amS(L.b[1])} ${bkLab(L.b[0])} ↗</a>` : `${amS(L.b[1])} ${bkLab(L.b[0])}`}</span>`).join('<br>')}</td>
        <td class="big">${pct(c.p)}</td><td class="pay">${c.d.toFixed(1)}x</td><td class="edge ${c.p * c.d - 1 >= .1 ? 'pos-d' : ''}" style="font-weight:700">+${((c.p * c.d - 1) * 100).toFixed(0)}%</td>
        <td class="l add">${window.SLIP ? SLIP.allBtn(c.legs.map(L => L.slip), '+ add parlay') : ''}</td></tr>`;
      const bestBox = pickBest ? `<div class="bestshot"><div class="eyebrow">Best shot tonight</div><ul>${pickBest.legs.map(L => `<li><b>${esc(L.lab)}</b> <span class="small muted">${esc(glab[L.gid] || '')} · ${pct(L.q)}</span> ${L.u ? `<a href="${esc(L.u)}" target="_blank" rel="noopener">${amS(L.b[1])} ${bkLab(L.b[0])} ↗</a>` : `${amS(L.b[1])} ${bkLab(L.b[0])}`}</li>`).join('')}</ul>
        <div class="bsum"><span><b>${pct(pickBest.p)}</b> to hit</span><span>pays <b>${pickBest.d.toFixed(1)}x</b> (${amS(Math.round((pickBest.d - 1) * 100))})</span><span class="pos-d"><b>+${((pickBest.p * pickBest.d - 1) * 100).toFixed(0)}%</b> edge</span>${window.SLIP ? SLIP.allBtn(pickBest.legs.map(L => L.slip), '+ add parlay') : ''}</div></div>` : '';
      return `<section class="panel"><div class="phead"><h2>${esc(opts.title || 'Parlays by payout')}</h2><span class="small muted">${opts.anyBook ? `Best price at any book${MY.length ? '; <span class="mybk">yours in green</span>' : ''}` : 'Priced at ' + esc(MY.map(k => FULL[k] || k).join(', '))}</span></div>${bestBox}
        <details class="how"${innerWidth > 640 ? ' open' : ''}><summary>How these are built</summary><p class="note">Every leg beats its price ${opts.anyBook ? 'at the best book for it' : 'at your books'}: its chance is our number blended with the market's (NHL goals and NFL props 50/50, where that's what made money, NFL props also needing a 5%+ edge; game lines 50/50 with the books' no-vig line). One leg per game, so the legs are independent; for each payout range these are the combinations most likely to cash. Because every leg has an edge, they should cash more often than the payout implies, but only by the edge column: a +600 parlay still loses most of the time.</p></details>
        ${TIERS.map(([lab], t) => rows[t].length ? `<div class="eyebrow" style="margin-top:10px">Pays ${lab}</div><div class="tw"><table class="t pairs atgp"><thead><tr><th class="l">Legs</th><th>Chance</th><th>Pays</th><th>Edge</th><th class="l">My parlay</th></tr></thead><tbody>${rows[t].map(row).join('')}</tbody></table></div>` : '').join('')}</section>`;
    },
    // Value-tag parlays (higher odds, experimental): legs = Value tags that still clear the bar at the viewer's books (a book
    // pays 5%+ more than the market-anchored chance: since 2026-10-18 tags are judged against the sharp consensus), one per
    // game so legs are independent; chance = product of the legs' anchored chances; tiers +300..+1000 and +1000..+3000.
    tagParlays(opts){
      if (!FL || !FL.tags || !FL.fetched || Date.now() - new Date(FL.fetched) > 12 * 3600e3) return '';
      const now = Date.now(), min = FL.min_ev || .05, amS = o => (o > 0 ? '+' : '') + o;
      const start = Object.fromEntries(opts.games.map(g => [String(g.id), g.start || null]));
      const glab = Object.fromEntries(opts.games.map(g => [String(g.id), g.label]));
      const LK = {'rec yds': 'rec_yds', 'rush yds': 'rush_yds', 'pass yds': 'pass_yds', receptions: 'rec'};
      const by = {};
      for (const [gid, T] of Object.entries(FL.tags)){
        if (!(gid in start) || (start[gid] && new Date(start[gid]) <= now)) continue;
        for (const [nm, v] of Object.entries(T)){
          if (!v || typeof v === 'string' || v.p == null || !v.all) continue;
          const c = Object.entries(v.all).filter(([k]) => mine(k)).map(([k, x]) => ({k, o: x[0], u: x[1]})).sort((a, b) => dec(b.o) - dec(a.o))[0];
          if (!c) continue;
          const q = v.m != null ? (v.wp != null ? v.wp * v.p + (1 - v.wp) * v.m : (v.p + v.m) / 2) : v.p;
          const ev = q * dec(c.o) - 1;
          if (ev < min || ev > .40) continue;                 // 40%+ "edges" are almost always a stale or mismatched quote
          const b = String(v.b || ''), sp = b.split(' ');
          const kind = b === 'TD' ? 'td' : b === 'goal' ? 'goal' : LK[sp.slice(2).join(' ')] || null;
          if (!kind) continue;
          const side = sp[0] === 'under' ? 'u' : 'o', line = kind === 'td' || kind === 'goal' ? .5 : +sp[1];
          const name = nm.replace(/\b\w/g, x => x.toUpperCase()), lab = `${name} ${b === 'TD' ? 'anytime TD' : b}`;
          (by[gid] = by[gid] || []).push({gid, q, ev, c, lab,
            leg: {gid, start: start[gid], kind, side, line, name: nm, lab, p: +q.toFixed(4), price: c.o, book: c.k},
            slip: {id: `${opts.sport}|${gid}|tag|${nm}|${kind}|${side}|${line}`, sport: opts.sport, g: gid, glab: glab[gid], lab, p: q, side: 'over'}});
        }
      }
      const gids = Object.keys(by);
      gids.forEach(g => by[g] = by[g].sort((a, b) => b.ev - a.ev).slice(0, 2));
      const TIERS = [['+300 to +1000', 4, 11], ['+1000 to +3000', 11, 31]], best = TIERS.map(() => []);
      const walk = (i, pk, pr, d) => {
        if (pk.length >= 2) TIERS.forEach(([, lo, hi], t) => { if (d >= lo && d < hi) best[t].push({legs: pk, p: pr, d}); });
        if (pk.length === 5 || d >= 31) return;
        for (let j = i; j < gids.length; j++) for (const L of by[gids[j]]) walk(j + 1, [...pk, L], pr * L.q, d * dec(L.c.o));
      };
      walk(0, [], 1, 1);
      const rows = best.map(list => list.sort((a, b) => b.p - a.p).slice(0, 3));
      rows.forEach((list, t) => list.forEach(c => reg(opts.title, TIERS[t][0], c.legs.map(L => L.leg), c.p, c.d)));
      const n = gids.reduce((s, g) => s + by[g].length, 0);
      const head = `<div class="phead"><h2>${esc(opts.title)} <span class="exp3">experimental</span></h2><span class="small muted">${n} tag leg${n === 1 ? '' : 's'} at your books</span></div>
        <details class="how"><summary>How these are built</summary><p class="note">Every leg is a Value tag that still clears its bar at your books: the book pays 5%+ more than our chance blended with the sharp consensus (Pinnacle, Novig, ProphetX). One leg per game, so the legs are independent and the parlay’s chance is their product. Higher-odds tiers, the likeliest combinations first. New on Oct 18 and tracked on the Profit page; a +1500 parlay still loses about 15 times in 16 even with an edge.</p></details>`;
      if (!rows.some(r => r.length)) return `<section class="panel">${head}<p class="note">No combination of tags reaches these payouts yet (${n} leg${n === 1 ? '' : 's'} so far). Tags fill in as books post props, mostly on game day.</p></section>`;
      const leg = L => `<b>${esc(L.lab)}</b> <span class="small muted">${esc(glab[L.gid] || '')} · ${pc(L.q)} · ${L.c.u ? `<a href="${esc(L.c.u)}" target="_blank" rel="noopener">${amS(L.c.o)} ${esc(BKS[L.c.k] || L.c.k)}</a>` : `${amS(L.c.o)} ${esc(BKS[L.c.k] || L.c.k)}`}</span>`;
      const row = c => `<tr><td class="l">${c.legs.map(leg).join('<br>')}</td><td class="big">${pc(c.p)}</td><td class="pay">${c.d.toFixed(1)}x</td><td class="edge ${c.p * c.d - 1 >= .1 ? 'pos-d' : ''}" style="font-weight:700">+${((c.p * c.d - 1) * 100).toFixed(0)}%</td>
        <td class="l add">${window.SLIP ? SLIP.allBtn(c.legs.map(L => L.slip), '+ add parlay') : ''}</td></tr>`;
      return `<section class="panel">${head}${TIERS.map(([lab], t) => rows[t].length ? `<div class="eyebrow" style="margin-top:10px">Pays ${lab}</div><div class="tw"><table class="t pairs atgp"><thead><tr><th class="l">Legs</th><th>Chance</th><th>Pays</th><th>Edge</th><th class="l">My parlay</th></tr></thead><tbody>${rows[t].map(row).join('')}</tbody></table></div>` : '').join('')}</section>`;
    },
    // the market's over / yes chance for one parlay leg at its own line: the sharp consensus (weight w on ours), else
    // Kalshi's mid at that rung (50/50); null when nothing prices that line
    legMarket(gid, name, kind, line){
      const kk = kind === 'anytime_td' ? 'td' : kind, ln = line ?? .5;
      const e = PR && PR.props ? ((PR.props[String(gid)] || {})[norm(name)] || {})[kk] : null;
      if (e && pinOk(e) && Math.abs((e.pin.l ?? .5) - ln) < 1e-9) return {m: e.pin.o, w: wOf(sport, kk, true), src: sharpLab(e)};
      const r = forPlayer(gid, null, name).find(x => x.s === 'kalshi' && (x.k === kind || (kk === 'td' && x.k === 'anytime_td'))
        && Math.abs((x.l ?? .5) - ln) < 1e-9 && x.ya > 0 && x.yb != null && x.ya - x.yb <= .10);
      return r ? {m: (r.ya + r.yb) / 2, w: .5, src: 'Kalshi'} : null;
    },
    norm, reg, regPath, FREEZE,
    // one learning-builder candidate: an over (main line, or yes for TD / goal) priced at the owner's books with the
    // site's anchored chance (sgLeg); f = the page's reasons (keys), fit = whether it fits a script / path (page rule)
    cand(gid, start, name, kind, line, p, f, fit){
      if (!FREEZE) return;
      const e0 = PR && PR.props && ((PR.props[String(gid)] || {})[norm(name)] || {})[kind];
      if (p == null) p = e0 && e0.p;                                     // our chance at the books' main line (props file)
      if (p == null) return;
      const L = window.MKT.sgLeg(gid, kind, 'o', line, p, name); if (!L || !L.b || !L.b.mine || L.b.o == null) return;
      const d = dec(L.b.o), tag = window.MKT.tagKind ? window.MKT.tagKind(gid, name) : null;
      const e = PR && PR.props && ((PR.props[String(gid)] || {})[norm(name)] || {})[kind];
      const ln = kind === 'goal' || kind === 'td' ? .5 : e && e.l != null ? e.l : line;          // sgLeg prices the main line
      regCand({gid: String(gid), start, name: norm(name), lab: name, kind, line: ln ?? .5, p: +(+p).toFixed(4), q: +L.q.toFixed(4), m: +L.m.toFixed(4),
               price: L.b.o, book: L.b.k, edge: +(L.q * d - 1).toFixed(4), f: f || [], fit: !!fit, tag: tag || null});
    },
    hasProps: gid => !!(PR && PR.props && PR.props[String(gid)] && Date.now() - new Date(PR.fetched) < 12 * 3600e3),
    // one player's best price at the viewer's books (all books if none chosen) for a prop, as a compact link.
    // kind: 'goal' | 'td' (yes side) or 'rec' / 'rec_yds' / 'rush_yds' / 'pass_yds' (main line: whichever side we rate better)
    prop(gid, name, kind){
      if (!PR || !PR.props || Date.now() - new Date(PR.fetched) > 12 * 3600e3) return '<span class="muted">–</span>';
      const e = ((PR.props[String(gid)] || {})[norm(name)] || {})[kind]; if (!e) return '<span class="muted">–</span>';
      const pick = side => { const all = Object.entries(e[side] || {}).sort((a, b) => dec(b[1][0]) - dec(a[1][0]));
        const L = all.filter(([k]) => mine(k)); return L.length ? [L[0], false] : all.length ? [all[0], true] : null; };
      // NFL props: our chance averaged 50/50 with the market's no-vig chance (books pricing both sides). On Kalshi 2026
      // that blend with a 5%+ edge returned +8.4%; our projection alone lost. NHL keeps our number (its top picks beat Kalshi).
      const nvOf = (sd, bk) => {
        if (sport === 'nhl' && kind === 'goal' && sd === 'o') return window.MKT.propMarket(gid, name, 'goal');   // 50/50 with the exchanges (Kalshi 2026)
        if (sport !== 'nfl') return null;
        if (pinOk(e)) return sd === 'o' ? sharpO(e, bk) : 1 - sharpO(e, bk);   // sharp consensus without the book being bet
        if (!e.o || !e.u) return null;
        const xs = Object.keys(e[sd] || {}).filter(k => (e[sd === 'o' ? 'u' : 'o'] || {})[k]).map(k => { const a = 1 / dec(e[sd][k][0]), b = 1 / dec(e[sd === 'o' ? 'u' : 'o'][k][0]); return a / (a + b); }).sort((x, y) => x - y);
        return xs.length ? xs[(xs.length - 1) >> 1] : null; };
      const opts = (e.u ? ['o', 'u'] : ['o']).map(sd => { const c = pick(sd); if (!c) return null;
        const p0 = e.p == null ? null : sd === 'o' ? e.p : 1 - e.p, m = nvOf(sd, c[0][0]), w = wOf(sport, kind, pinOk(e)), p = p0 != null && m != null ? w * p0 + (1 - w) * m : p0;
        return {sd, k: c[0][0], o: c[0][1][0], path: c[0][1][1], other: c[1], p0, m, ev: p == null ? null : p * dec(c[0][1][0]) - 1}; })
        .filter(Boolean).sort((a, b) => (a.other - b.other) || ((b.ev ?? -9) - (a.ev ?? -9)));
      const c = opts[0]; if (!c) return '<span class="muted">–</span>';
      // Gambly path (Unabated: partner code required) or a direct sportsbook link (The Odds API); none = plain text
      const lk = p_ => !p_ ? null : String(p_).startsWith('http') ? p_ : `https://www.gambly.com/deeplink/odds/${p_}?utm_campaign=${encodeURIComponent(PR.partner || '')}`;
      const url = lk(c.path);
      const sideOk = sport !== 'nfl' || !['rec', 'rec_yds', 'rush_yds', 'pass_yds'].includes(kind) || (c.p0 != null && c.p0 >= .5 && (c.m == null || Math.abs(c.p0 - c.m) >= .10));
      const v = !c.other && !e.w && sideOk && c.ev != null && c.ev >= (FL && FL.min_ev || .05);
      const lab = e.l != null ? `${c.sd === 'o' ? 'O' : 'U'} ${e.l} ` : '';
      const tip = `${c.other ? 'None of your books has this; best elsewhere. ' : ''}${lab ? (c.sd === 'o' ? 'Over ' : 'Under ') + e.l + ': ' : ''}${esc(FULL[c.k] || c.k)} ${am(c.o)}${c.ev != null ? ` · ${c.m != null ? `ours ${(c.p0 * 100).toFixed(1)}%, ${e.pin ? 'Pinnacle no-vig' : 'market'} ${(c.m * 100).toFixed(1)}%, blended ` : ''}edge ${c.ev >= 0 ? '+' : ''}${(c.ev * 100).toFixed(1)}%` : ''}${e.w ? ' (not highlighted: outside our top 3 in this game, or far enough from the market to suggest news; those picks have lost)' : ''}. Tap to open this bet.`;
      const pinP = ['nfl', 'nhl', 'nba'].includes(sport) && pinOk(e) ? (c.sd === 'o' ? e.pin.o : 1 - e.pin.o) : null;
      // the bet that beats Pinnacle's no-vig by the most: any book, main or alt line
      const cands = [];
      const addC = (sd, line, q, prices, alt) => { if (q == null) return; for (const [k, v] of Object.entries(prices || {})){
        if (NOBET.has(k) || SKIP.has(k) || !Array.isArray(v)) continue; const qq = sd === 'o' ? q : 1 - q; cands.push({sd, line, q: qq, k, o: v[0], path: v[1], alt, pe: qq * dec(v[0]) - 1}); } };
      if (pinP != null){ addC('o', e.l, e.pin.o, e.o, 0); addC('u', e.l, e.pin.o, e.u, 0); }
      if (sport === 'nfl' || sport === 'nba') for (const [al, x] of Object.entries(e.a || {})){ addC('o', +al, x.pin, x.o, 1); addC('u', +al, x.pin, x.u, 1); }
      const pb = cands.sort((a, b) => b.pe - a.pe)[0];
      const pinChip = pb && pb.pe > 0
        ? ` <a class="pinv" href="${esc(lk(pb.path) || '#')}" target="_blank" rel="noopener" title="${esc(`${pb.line != null ? (pb.sd === 'o' ? 'Over ' : 'Under ') + pb.line + (pb.alt ? ' (alt line)' : '') + ': ' : ''}${FULL[pb.k] || pb.k} ${am(pb.o)} pays ${(pb.pe * 100).toFixed(1)}% more than Pinnacle's fair price (${(pb.q * 100).toFixed(1)}%: ${pinHow(pb.alt ? 3 : e.pin && e.pin.w, e.pin && (e.pin.pl ?? e.pin.l))}). Tap to open this bet.`)}">Pin <b>+${(pb.pe * 100).toFixed(1)}%</b> ${pb.line != null && (pb.alt || pb.sd !== c.sd) ? (pb.sd === 'o' ? 'O' : 'U') + pb.line + ' ' : ''}${esc(BKS[pb.k] || pb.k)}${pb.alt || pb.sd !== c.sd || pb.k !== c.k ? ' ' + am(pb.o) : ''}</a>`
        : pinP != null ? ` <span class="pinv neg" title="${esc(sharpLab(e))} no-vig chance for this side (${pinHow(e.pin.w, e.pin.pl ?? e.pin.l)}); no book beats its fair price">Pin ${e.pin.w === 3 ? '≈' : ''}${(pinP * 100).toFixed(0)}%</span>` : '';
      return `<${url ? 'a' : 'span'} class="pc${v ? ' v' : ''}${c.other ? ' nm' : ''}"${url ? ` href="${esc(url)}" target="_blank" rel="noopener"` : ''} title="${tip}">${lab}<b>${am(c.o)}</b> ${esc(BKS[c.k] || c.k)}${v ? ` <i>+${(c.ev * 100).toFixed(0)}%</i>` : ''}</${url ? 'a' : 'span'}>${pinChip}`;
    },
    // the stat of a player's live over Value tag ('rec_yds', 'td', ...), or null
    tagKind(gid, name){
      if (!FL || !FL.tags || !FL.fetched || Date.now() - new Date(FL.fetched) > 12 * 3600e3) return null;
      const v = (FL.tags[String(gid)] || {})[norm(name)]; if (!v) return null;
      const b = String(typeof v === 'string' ? v : v.b || '');
      if (/^TD$|anytime|goal/i.test(b)) return /goal/i.test(b) ? 'goal' : 'td';
      const m = /^over [\d.]+ (.+)$/.exec(b); if (!m) return null;
      return {'rec yds': 'rec_yds', 'rush yds': 'rush_yds', 'pass yds': 'pass_yds', receptions: 'rec'}[m[1]] || null;
    },
    // Off-market prices (sharp.off_market -> flags_<sport>.json "off"): every over price at every book and prediction
    // market, main and alt lines, 8+ pts under the fair at that line (or 35%+ under on longshots). Your books first.
    offView(games){
      if (!FL || !FL.off || !FL.fetched) return '<section class="panel"><p class="note">No off-market scan yet: it runs with the sportsbook prices (about every 15 minutes before games).</p></section>';
      const glab = Object.fromEntries((games || []).map(g => [String(g.id), g.label]));
      const now = Date.now(), start = Object.fromEntries((games || []).map(g => [String(g.id), g.start]));
      const L = FL.off.filter(x => !start[String(x.g)] || new Date(start[String(x.g)]) > now);
      const link = u => !u ? null : String(u).startsWith('http') ? u : `https://www.gambly.com/deeplink/odds/${u}?utm_campaign=${encodeURIComponent((PR && PR.partner) || '')}`;
      const SK = {goal: 'Goal', td: 'TD', rec: 'Catches', rec_yds: 'Rec yds', rush_yds: 'Rush yds', pass_yds: 'Pass yds', pts: 'Points', reb: 'Rebounds', ast: 'Assists', fg3m: 'Threes',
                  stl: 'Steals', blk: 'Blocks', dd: 'Double-double', tdbl: 'Triple-double'};
      const skey = k => String(k).startsWith('sog') ? 'sog' : k, slab = k => k === 'sog' ? 'Shots' : SK[k] || k;
      const gidx = Object.fromEntries((games || []).map((g, i) => [String(g.id), i]));
      const SORT = {gap: (a, b) => (b.fair - b.imp) - (a.fair - a.imp), rel: (a, b) => b.fair / b.imp - a.fair / a.imp,
                    soon: (a, b) => (gidx[String(a.g)] ?? 99) - (gidx[String(b.g)] ?? 99) || (b.fair - b.imp) - (a.fair - a.imp), pay: (a, b) => b.o - a.o};
      L.sort(SORT[OFV.sort] || SORT.gap);
      const mineF = L.filter(x => mine(x.bk)), rest = L.filter(x => !mine(x.bk));
      const t = new Date(FL.fetched).toLocaleTimeString([], {hour: 'numeric', minute: '2-digit'});
      const row = x => { const u = link(x.u), nm = x.n.replace(/\b\w/g, c => c.toUpperCase()), gap = Math.round((x.fair - x.imp) * 100);
        const price = `${x.o > 0 ? '+' : ''}${x.o} ${esc(FULL[x.bk] || BKS[x.bk] || x.bk)}`;
        return `<div class="offr${mine(x.bk) ? ' mine' : ''}" data-offr data-g="${esc(String(x.g))}" data-k="${esc(skey(x.k))}" data-bk="${esc(x.bk)}" data-my="${mine(x.bk) ? 1 : ''}" data-main="${x.main ? 1 : ''}" data-gap="${gap}" data-c="${x.carried ? 1 : ''}" data-s="${esc(norm(nm + ' ' + (glab[String(x.g)] || '')))}"><span class="offn"><b>${esc(nm)}</b> ${esc(x.b)}${x.main ? '' : ' <i>alt</i>'}<small>${esc(glab[String(x.g)] || '')}</small></span>
          <span class="offp">${u ? `<a href="${esc(u)}" target="_blank" rel="noopener">${price} ↗</a>` : price}<small>${x.dep ? usd(x.dep) + ' within 3¢' : ''}</small></span>
          <span class="offg" title="Price implies ${pc(x.imp)}; fair ${x.carried ? '(carried from the main line by our projection) ' : ''}${pc(x.fair)}">${pc(x.imp)} vs ${x.carried ? '≈' : ''}${pc(x.fair)}<b>+${gap} pts</b></span></div>`; };
      // filters: chips filter in place (offApply), so typing in the search box keeps its focus
      const chips = (key, opts) => opts.map(([v, l]) => `<button type="button" data-ofv="${key}" data-v="${esc(String(v))}" aria-pressed="${String(OFV[key]) === String(v)}">${esc(l)}</button>`).join('');
      const ks = [...new Set(L.map(x => skey(x.k)))], bks = [...new Set(L.map(x => x.bk))].sort((a, b) => (ORDER.indexOf(a) + 99) % 99 - (ORDER.indexOf(b) + 99) % 99 || a.localeCompare(b));
      const gs = (games || []).filter(g => L.some(x => String(x.g) === String(g.id)));
      if (OFV.k !== 'all' && !ks.includes(OFV.k)) OFV.k = 'all';
      if (!['all', 'mine'].includes(OFV.bk) && !bks.includes(OFV.bk)) OFV.bk = 'all';
      const bar = L.length ? `<div class="ovc offbar"><input id="off-q" type="search" placeholder="Find a player or team" value="${esc(OFV.q)}" aria-label="Find a player or team">
          <select id="off-g" aria-label="Game"><option value="all">All games</option>${gs.map(g => `<option value="${esc(String(g.id))}"${OFV.g === String(g.id) ? ' selected' : ''}>${esc(g.label)}</option>`).join('')}</select>
          <select id="off-sort" aria-label="Sort"><option value="gap"${OFV.sort === 'gap' ? ' selected' : ''}>Biggest gap</option><option value="rel"${OFV.sort === 'rel' ? ' selected' : ''}>Most under fair (%)</option><option value="soon"${OFV.sort === 'soon' ? ' selected' : ''}>Soonest game</option><option value="pay"${OFV.sort === 'pay' ? ' selected' : ''}>Biggest payout</option></select></div>
        <div class="offf"><span class="offl">Stat</span><div class="ovm">${chips('k', [['all', 'All'], ...ks.map(k => [k, slab(k)])])}</div></div>
        <div class="offf"><span class="offl">Book</span><div class="ovm">${chips('bk', [['all', 'All'], ['mine', 'Your books'], ...bks.map(b => [b, FULL[b] || BKS[b] || b])])}</div></div>
        <div class="offf"><span class="offl">Line</span><div class="ovm">${chips('ln', [['all', 'Main + alt'], ['main', 'Main only'], ['alt', 'Alt only']])}</div></div>
        <div class="offf"><span class="offl">Gap</span><div class="ovm">${chips('gap', [[0, 'Any'], [10, '10+'], [15, '15+'], [20, '20+']])}<span class="offsep"></span><button type="button" data-ofsharp aria-pressed="${OFV.sharp}" title="Hide prices whose fair is carried from the main line by our projection (≈); keep only a fair the books price themselves">Sharp fair only</button></div></div>
        <p class="small muted offcount" aria-live="polite"></p>` : '';
      return `<section class="panel"><div class="phead"><h2>Off market</h2><span class="small muted">overs priced far under fair · checked ${esc(t)}</span></div>
        ${L.length ? `${bar}${mineF.length ? `<div class="eyebrow offh" style="margin:4px 0">At your books</div>${mineF.map(row).join('')}` : ''}${rest.length ? `<div class="eyebrow offh" style="margin:12px 0 4px">Other books and markets</div>${rest.map(row).join('')}` : ''}
          <p class="note offnone" hidden>No off-market price matches these filters. <button type="button" class="bkset" data-ofreset>Clear filters</button></p>`
          : '<p class="note">Nothing far off right now: every over is within 8 points of its fair price.</p>'}
        <details class="how"><summary>How this works</summary><p class="note">Every over price at every book, Kalshi and Polymarket, main and alt lines, against the fair chance at that exact line: the sharp consensus at the main line, the books’ own two-way price at an alt line, and where no book prices both sides (most prediction-market rungs) the main line’s fair carried along our projection (shown with ≈, less certain). Listed when the price implies 8+ points less than fair, or 35%+ less on longshots; Kalshi includes its fee. A price this far off is sometimes a stale or wrong line, so check it before betting. Tracked on the Profit page as Off-market prices.</p></details></section>`;
    },
    offApply: () => offApply(),
    // the player's off-market price on the sport pages (best one; the owner's books first)
    offFlag(gid, name){
      if (!FL || !FL.off || !FL.fetched || Date.now() - new Date(FL.fetched) > 12 * 3600e3) return '';
      const L = FL.off.filter(x => String(x.g) === String(gid) && x.n === norm(name)); if (!L.length) return '';
      const x = L.filter(z => mine(z.bk))[0] || L[0];
      const u = x.u ? (String(x.u).startsWith('http') ? x.u : `https://www.gambly.com/deeplink/odds/${x.u}?utm_campaign=${encodeURIComponent((PR && PR.partner) || '')}`) : null;
      const tip = `Off market: ${esc(FULL[x.bk] || BKS[x.bk] || x.bk)} prices ${esc(name)} ${esc(x.b)} at ${pc(x.imp)}; fair ${x.carried ? 'about ' : ''}${pc(x.fair)}. A price this far off is sometimes a stale or wrong line: check it before betting.`;
      const body = `Off · ${esc(x.b)} <b>${x.o > 0 ? '+' : ''}${x.o}</b> ${esc(BKS[x.bk] || x.bk)} <i>+${Math.round((x.fair - x.imp) * 100)} pts</i>`;
      return u ? `<a class="vtag offt" href="${esc(u)}" target="_blank" rel="noopener" title="${tip}">${body} ↗</a>` : `<span class="vtag offt" title="${tip}">${body}</span>`;
    },
    // the stats a player's flagged prices are on (Value, LC, off-market): ['rec_yds', 'td', ...]
    tagStats(gid, name){
      if (!FL) return [];
      const K = {'rec yds': 'rec_yds', 'rush yds': 'rush_yds', 'pass yds': 'pass_yds', receptions: 'rec', points: 'pts', rebounds: 'reb', assists: 'ast', threes: 'fg3m'};
      const of = b => { b = String(b || ''); if (/^TD$/i.test(b)) return 'td'; if (/^goal$/i.test(b)) return 'goal'; const m = /^over [\d.]+ (.+)$/.exec(b); return m ? (K[m[1]] || m[1]) : null; };
      const n = norm(name), out = new Set();
      const v = ((FL.tags || {})[String(gid)] || {})[n]; if (v && typeof v === 'object') out.add(of(v.b));
      const l = ((FL.lc || {})[String(gid)] || {})[n]; if (l) out.add(of(l.b));
      (FL.off || []).filter(x => String(x.g) === String(gid) && x.n === n).forEach(x => out.add(x.k));
      out.delete(null); return [...out];
    },
    // 'LC' tag (experimental): the price at one of the owner's books beats the liquidity-weighted fair (sharp consensus
    // + Kalshi / Polymarket weighted by depth, never the book being bet; sharp.lc_tags) by 5%+. Tracked on the Profit page.
    // Pinnacle line moves (sharp.pin_moves; owner 2026-10-23): this player's live Pinnacle signals, and a chip --
    // 'PIN ▲ ast 5.5→6.5' (line up), 'PIN odds ▼ reb' (over shortened at the same line), both together; the tooltip adds
    // the consensus agreeing and any higher-rung outliers at the owner's books. Tracked on the Profit page (experimental).
    pinMoves(gid, name){
      if (!FL || !FL.moves || !FL.fetched || Date.now() - new Date(FL.fetched) > 12 * 3600e3) return [];
      return FL.moves.filter(m => String(m.g) === String(gid) && m.n === norm(name));
    },
    moveFlag(gid, name){
      const M = window.MKT.pinMoves(gid, name), P = M.filter(m => m.src === 'pin' && m.sig !== 'rung'); if (!P.length) return '';
      const SL = {pts: 'pts', reb: 'reb', ast: 'ast', fg3m: '3PM', rec: 'catches', rec_yds: 'rec yds', rush_yds: 'rush yds', pass_yds: 'pass yds', td: 'TD'};
      const by = {}; P.forEach(m => { const c = by[m.k] = by[m.k] || {k: m.k, line: null, price: false}; if (m.sig === 'line' || m.sig === 'both') c.line = m; if (m.sig === 'price' || m.sig === 'both') c.price = true; });
      return Object.values(by).map(c => {
        const cons = M.some(m => m.src === 'cons' && m.k === c.k && m.sig !== 'rung'), rungs = M.filter(m => m.sig === 'rung' && m.k === c.k);
        const body = `PIN ${c.line ? `▲ ${SL[c.k] || c.k} ${c.line.from_l}→${c.line.to_l}` : `${SL[c.k] || c.k}`}${c.price ? ' · odds ▼' : ''}`;
        const tip = `Pinnacle ${c.line ? `moved ${esc(name)}’s ${SL[c.k] || c.k} line up, ${c.line.from_l} to ${c.line.to_l}` : `kept the line`}${c.price ? `${c.line ? ' and' : ','} the over got shorter (its no-vig chance up 2-3+ pts)` : ''}${c.line && c.price ? ': the strong version of the signal' : ''}.${cons ? ' Our sharp consensus moved the same way.' : ''}${rungs.length ? ' Higher rungs priced above the moved fair at your books: ' + rungs.map(r => `${esc(r.b)} ${r.o > 0 ? '+' : ''}${r.o} ${esc(BKS[r.bk] || r.bk)} (+${Math.round(r.ev * 100)}%)`).join(', ') + '.' : ''} Tracked on the Profit page; not yet a tested rule.`;
        return `<span class="vtag pmv${c.line && c.price ? ' st' : ''}" title="${tip}">${body}${rungs.length ? ` <i>+${rungs.length} rung${rungs.length === 1 ? '' : 's'}</i>` : ''}</span>`;
      }).join('');
    },
    lcFlag(gid, name){
      if (!FL || !FL.lc || !FL.fetched || Date.now() - new Date(FL.fetched) > 12 * 3600e3) return '';
      const v = (FL.lc[String(gid)] || {})[norm(name)]; if (!v || (MY.length && !MY.includes(v.k))) return '';
      const u = v.u ? (String(v.u).startsWith('http') ? v.u : `https://www.gambly.com/deeplink/odds/${v.u}?utm_campaign=${encodeURIComponent((PR && PR.partner) || '')}`) : null;
      const tip = `Liquidity consensus (experimental): ${esc(FULL[v.k] || v.k)} pays ${Math.round(v.ev * 100)}% more than our chance ${(v.q * 100).toFixed(1)}% for ${esc(name)} ${esc(v.b)}. Fair ${(v.m * 100).toFixed(1)}% = the sharp books’ consensus with the prediction markets, deeper markets counting more (never the book being bet). Tracked on the Profit page.`;
      const body = `LC · ${esc(v.b)} <b>${v.o > 0 ? '+' : ''}${v.o}</b> ${esc(BKS[v.k] || v.k)} <i>+${Math.round(v.ev * 100)}%</i>`;
      return u ? `<a class="vtag lct" href="${esc(u)}" target="_blank" rel="noopener" title="${tip}">${body} ↗</a>` : `<span class="vtag lct" title="${tip}">${body}</span>`;
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
        const q = v.m != null ? (v.wp != null ? v.wp * v.p + (1 - v.wp) * v.m : (v.p + v.m) / 2) : v.p;   // the cloud's weights (wp: Pinnacle-anchored)
        if (!c || q * dec(c.o) - 1 < min) return '';
        o = c;
      }
      const tip = `Value: ${o ? esc(FULL[o.k] || o.k) : 'at least one major sportsbook'} pays ${Math.round(min * 100)}%+ more than our fair price for ${esc(name)} ${esc(b)} (checked ${esc(t)}; skipped when we're far from the market, which usually means news). Not yet a proven edge: we're tracking how these do.`;
      if (o && o.u)                                          // price, book and a bet link
        return `<a class="vtag" href="${esc(o.u)}" target="_blank" rel="noopener" title="${tip} Tap to open this bet.${v.pin != null ? ` Pinnacle's no-vig chance: ${(v.pin * 100).toFixed(1)}%.` : ''}">Value · ${esc(b)} <b>${o.o > 0 ? '+' : ''}${o.o}</b> ${esc(BKS[o.k] || o.k)} ↗${v.pin != null ? (() => { const pe = v.pin * dec(o.o) - 1; return ` <span class="pinv${pe > 0 ? '' : ' neg'}">Pin ${(v.pin * 100).toFixed(0)}% · <b>${pe > 0 ? '+' : ''}${(pe * 100).toFixed(1)}%</b></span>`; })() : ''}</a>`;
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
    // One player-stat's over ladder for the Game script tab: the main line, the books' alt lines and Kalshi's live rungs.
    // Each rung: {l, main, p (ours), fair (sharp consensus no-vig, or the books' alt-line fair), mid (Kalshi mid when no
    // fair), w (weight on ours), best: {k, name, o, u} at the viewer's books, any: best anywhere}. Pinnacle never a price.
    altLadder(gid, name, kind){
      const out = [], e = PR && PR.props && Date.now() - new Date(PR.fetched) < 12 * 3600e3 ? ((PR.props[String(gid)] || {})[norm(name)] || {})[kind] : null;
      const link = u => !u ? null : String(u).startsWith('http') ? u : `https://www.gambly.com/deeplink/odds/${u}?utm_campaign=${encodeURIComponent((PR && PR.partner) || '')}`;
      const prices = o => Object.entries(o || {}).filter(([k]) => !SKIP.has(k) && !NOBET.has(k)).map(([k, v]) => ({k, name: FULL[k] || k, o: Array.isArray(v) ? v[0] : v, u: link(Array.isArray(v) ? v[1] : null)}))
        .filter(x => x.o != null).sort((a, b) => dec(b.o) - dec(a.o));
      const rung = (l, main, p, fair, o) => { const P = prices(o), M = P.filter(x => mine(x.k));
        out.push({l, main, p: p ?? null, fair: fair ?? null, mid: null, w: fair != null ? wOf(sport, kind, true) : .5, best: M[0] || null, any: P[0] || null, n: P.length, all: P.map(x => ({...x, mine: mine(x.k)}))}); };
      if (e && e.l != null) rung(+e.l, true, e.p, pinOk(e) ? e.pin.o : null, e.o);
      if (e) for (const [al, x] of Object.entries(e.a || {})) if (!out.some(r => Math.abs(r.l - al) < 1e-9)) rung(+al, false, x.p, x.pin, x.o);
      // Kalshi's live board has more rungs than the props feed; quotes wider than 10c say nothing about the price
      for (const r of forPlayer(gid, null, name).filter(x => x.s === 'kalshi' && x.k === kind && x.l != null && x.ya > 0 && x.ya < 1 && x.yb != null && x.ya - x.yb <= .10)){
        const q = Math.min(.999, r.ya + .07 * r.ya * (1 - r.ya)), o = q >= .5 ? -Math.round(100 * q / (1 - q)) : Math.round(100 * (1 - q) / q);
        const K = {k: 'Kalshi', name: 'Kalshi', o, u: null};
        let R = out.find(x => Math.abs(x.l - r.l) < 1e-9);
        if (!R){ out.push(R = {l: r.l, main: false, p: ours(r, 'vegas'), fair: null, w: .5, best: null, any: null, n: 0, all: []}); }
        if (!R.all.some(x => x.k === 'Kalshi')) R.all.push({...K, mine: mine('Kalshi')});
        R.mid = (r.ya + r.yb) / 2;
        if (mine('Kalshi') && (!R.best || dec(o) > dec(R.best.o))) R.best = K;
        if (!R.any || dec(o) > dec(R.any.o)) R.any = K;
        R.n = Math.max(R.n, 1);
      }
      return out.sort((a, b) => a.l - b.l);
    },
    // Today's card (parlays.py pick_card): 4 parlays a day per sport, $5 each, chosen from every card the page shows:
    // 3 core (2-4 legs, 3-15x) + 1 all-day longshot (15-100x), every leg at the owner's books, legs from different games,
    // edge 5-40% on the market-anchored chance, a leg in at most 2 picks. Frozen at each pick's first game; graded.
    dailyCard(){
      const C = CARD; if (!C || !C.picks || !C.picks.length) return '';
      const amS = o => (o > 0 ? '+' : '') + o, R = C.record || {}, stake = C.stake || 5;
      const day = C.date ? new Date(C.date + 'T12:00:00').toLocaleDateString([], {weekday: 'long', month: 'short', day: 'numeric'}) : '';
      const tm = t => new Date(t).toLocaleTimeString([], {hour: 'numeric', minute: '2-digit'});
      const money = v => (v >= 0 ? '+$' : '−$') + Math.abs(v).toFixed(2);
      const pick = P => { const st = P.res === 'W' ? `<span class="st W">Won ${money(stake * ((P.paid || P.d) - 1))}</span>` : P.res === 'L' ? '<span class="st L">Lost</span>'
          : P.res === 'V' ? '<span class="st">Void</span>' : new Date(P.first) <= Date.now() ? '<span class="st">Live</span>'
          : P.lock && new Date(P.lock) > Date.now() && byPick ? `<span class="st"${P.lw ? ' title="Re-picked until the confirmed starting lineups and injury calls for its first game are in our numbers (~30 min before tip)"' : ''}>${P.lw ? 'locks after lineups' : 'locks ' + tm(P.lock)}</span>` : `<span class="st">first game ${tm(P.first)}</span>`;
        return `<div class="dcp ${P.res || ''}"><div class="dch"><span class="slot${P.slot === 'longshot' ? ' ls' : ''}">${P.slot === 'longshot' ? 'Longshot' : 'Core'}</span>${P.legs.length} legs${st}</div>
          <ul>${P.legs.map(L => `<li><i class="${L.res || ''}">${L.res === 'W' ? '✓' : L.res === 'L' ? '✗' : '·'}</i><b>${esc(L.lab)}</b><span>${esc(FULL[L.book] || L.book || '')} ${L.price != null ? amS(L.price) : ''}</span></li>`).join('')}</ul>
          <div class="dcs"><span>pays <b>${P.d.toFixed(1)}x</b></span><span>$${stake} → <b>$${(stake * P.d).toFixed(2)}</b></span><span>hits <b>${pc(P.p)}</b></span><span class="${P.p * P.d - 1 >= .05 ? 'good' : 'muted'}">edge ${P.p * P.d - 1 >= 0 ? '+' : '−'}${Math.abs(Math.round((P.p * P.d - 1) * 100))}%</span></div></div>`; };
      // NFL core picks lock by slate (60 min before their first kickoff, after inactives + our rerun); others lock with the card
      const locks = C.picks.map(P => P.lock).filter(Boolean), byPick = locks.some(t => new Date(t).getTime() !== new Date(C.final_at).getTime());
      const next = locks.map(t => new Date(t)).filter(t => t > Date.now()).sort((a, b) => a - b)[0];
      const nextLw = next && C.picks.some(P => P.lw && P.lock && new Date(P.lock).getTime() === next.getTime());
      const fin = byPick ? next || null : C.final_at ? new Date(C.final_at) : null, isFinal = byPick ? !next : fin && fin <= Date.now();
      const finTag = byPick || fin ? `<span class="dfin${isFinal ? ' on' : ''}" title="${isFinal ? 'These picks are set: they won’t change, and they’re graded at these prices. Place them any time before their first game.' : byPick ? 'Each pick locks at its own time (shown on the pick); until then it can still change.' : 'Picks can still change until then; after that they’re set for the day.'}">${isFinal ? 'Final · place any time' : nextLw ? 'Locks after lineups · by ' + tm(fin) : (byPick ? 'Next lock ' : 'Final at ') + tm(fin)}</span>` : '';
      return `<section class="panel dcard"><div class="phead"><h2>Today’s card</h2>${finTag}<span class="small muted">${esc(day)} · ${C.picks.length} parlay${C.picks.length === 1 ? '' : 's'} · $${stake} each</span></div>
        <div class="dcrec">${R.n ? `<span>Card record <b>${R.w}-${R.l}</b></span><span><b class="${R.net >= 0 ? 'good' : ''}">${money(R.net)}</b> at $${stake} a parlay</span>` : '<span>First graded picks still to come.</span>'}<span>Prices at your books when picked</span></div>
        <div class="dcg">${C.picks.map(pick).join('')}</div>
        <details class="how"><summary>How these are picked</summary><p class="note">Four a day from every parlay on this tab: three core parlays (2-4 legs, paying 3x to 15x) and one all-day longshot (15x to 100x). Every leg is an over, priced at your books, and comes from a different game; the parlay’s chance uses each leg’s market-anchored chance and needs a 5-40% edge (bigger claimed edges have been the model being wrong); a leg is in at most two picks. Our record so far: 5+ leg parlays went 0 for 101, while 2-4 leg ones matched our chances.${sport === 'nhl' ? ' NHL chances shown are cut 15%: our NHL parlays have hit less often than we said.' : ''} ${sport === 'nfl' ? 'The all-day longshot goes final 2 hours before the day’s first game; core picks lock by slate, an hour before their first kickoff (after inactives and our rerun).' : 'Picks go final 1 hour before the day’s first game.'} After that they never change, so you can place them any time; they’re graded on the Profit page at the prices shown.</p></details></section>`;
    },
    // The market's chance of a game script, from the prediction markets' full ladders: game totals (Kalshi first, then
    // Polymarket: over X at every few points) and winning margins (Polymarket: each team -X). Mid prices, quotes wider than
    // 5c dropped, made monotone, read between rungs (never past the end of a ladder). spec: {kind: 'margin', team, min}
    // (team wins by min+), {kind: 'close', max} (decided by max or fewer), {kind: 'over' | 'under', at} (total), {kind: 'win', team};
    // g: {home, away, fav}. Returns {m, src} or null.
    scriptMarket(gid, spec, g){
      const rows = forGame(gid).filter(r => r.ya > 0 && r.yb != null && r.yb > 0 && r.ya - r.yb <= .05);
      const mid = r => (r.ya + r.yb) / 2;
      // P(X > x) from rungs [(x, p)]: monotone (non-increasing in x), linear between rungs, null outside them
      const curve = pts => { pts = pts.sort((a, b) => a[0] - b[0]); for (let i = 1; i < pts.length; i++) pts[i][1] = Math.min(pts[i][1], pts[i - 1][1]);
        return x => { if (pts.length < 2 || x < pts[0][0] - 1e-9 || x > pts[pts.length - 1][0] + 1e-9) return null;
          for (let i = 1; i < pts.length; i++) if (x <= pts[i][0] + 1e-9){ const [x0, p0] = pts[i - 1], [x1, p1] = pts[i]; return x1 === x0 ? p1 : p0 + (p1 - p0) * (x - x0) / (x1 - x0); }
          return pts[pts.length - 1][1]; }; };
      const tot = src => curve(rows.filter(r => r.k === 'total' && r.l != null && r.s === src && !/under/i.test(r.ti || '')).map(r => [r.l, mid(r)]));
      const over = x => { for (const s of ['kalshi', 'polymarket']){ const v = tot(s)(x); if (v != null) return {m: v, src: s === 'kalshi' ? 'Kalshi' : 'Polymarket'}; } return null; };
      // winning-margin ladders by team nickname; nickname -> code from the markets' winner rows ("Commanders win (WAS)")
      const groups = {}; rows.filter(r => r.k === 'spread' && r.o && r.l != null && r.l < 0).forEach(r => (groups[r.o] = groups[r.o] || []).push([-r.l, mid(r)]));
      const nick = {}; forGame(gid).filter(r => r.k === 'win' && r.o && /\(([A-Z]{2,3})\)/.test(r.ti || '')).forEach(r => { nick[/\(([A-Z]{2,3})\)/.exec(r.ti)[1]] = r.o; });
      const names = Object.keys(groups);
      const codeOf = n => Object.keys(nick).find(c => nick[c] === n);
      const teamGroup = t => { if (nick[t] && groups[nick[t]]) return groups[nick[t]];
        const other = t === g.home ? g.away : g.home;
        if (names.length === 2 && nick[other] && groups[nick[other]]) return groups[names.find(n => n !== nick[other])];
        if (names.length === 2 && !Object.keys(nick).length){                     // no names: the group pricing a 1.5-point win higher is the favorite
          const at = n => curve(groups[n].map(x => [...x]))(1.5) ?? 0; const fn = at(names[0]) >= at(names[1]) ? names[0] : names[1];
          return groups[t === g.fav ? fn : names.find(n => n !== fn)]; }
        return null; };
      const by = (t, n) => { const G = teamGroup(t); if (!G) return null; const v = curve(G.map(x => [...x]))(n - .5); return v == null ? null : {m: v, src: 'Polymarket'}; };
      if (spec.kind === 'over') return over(spec.at);
      if (spec.kind === 'under'){ const v = over(spec.at); return v ? {m: 1 - v.m, src: v.src} : null; }
      if (spec.kind === 'margin') return by(spec.team, spec.min);
      if (spec.kind === 'close'){ const a = by(g.home, spec.max + 1), b = by(g.away, spec.max + 1); return a && b ? {m: Math.max(0, 1 - a.m - b.m), src: 'Polymarket'} : null; }
      if (spec.kind === 'win'){                          // the winner market that names its team ("Commanders win (WAS)")
        const w = rows.find(r => r.k === 'win' && /\(([A-Z]{2,3})\)/.test(r.ti || ''));
        if (!w) return null; const c = /\(([A-Z]{2,3})\)/.exec(w.ti)[1];
        return c === spec.team ? {m: mid(w), src: 'Polymarket'} : (c === g.home || c === g.away) ? {m: 1 - mid(w), src: 'Polymarket'} : null; }
      return null;
    },
    // Kalshi yardage / catch ladders: the rungs 2+ above the one priced nearest 50/50 (Ladder candidates, NFL Parlays tab)
    booksAt(gid, name, kind, line){ return booksAt(gid, name, kind, line); },
    ladder(gid, pid, name, kinds){
      kinds = kinds || ['rec', 'rec_yds', 'rush_yds', 'pass_yds'];
      // wide quotes (spread over 10c) say nothing about the price: left out
      const rows = forPlayer(gid, pid, name).filter(r => r.s === 'kalshi' && kinds.includes(r.k) && r.l != null && r.ya > 0 && r.yb != null && r.ya - r.yb <= .10);
      const by = {}; rows.forEach(r => (by[r.k] = by[r.k] || []).push(r));
      const out = [];
      for (const [k, L] of Object.entries(by)){
        L.sort((a, b) => a.l - b.l);
        const mid = r => (r.ya + r.yb) / 2; let i0 = 0;
        L.forEach((r, i) => { if (Math.abs(mid(r) - .5) < Math.abs(mid(L[i0]) - .5)) i0 = i; });
        L.slice(i0 + 2).forEach((r, i) => { if (mid(r) < mid(L[i0])) out.push({k, i, l: r.l, ya: r.ya, yb: r.yb, mid: mid(r), p: ours(r, 'vegas'), price: px(r.ya, 'kalshi'),
          top: L.length - 1 - (i0 + 2 + i), cost: r.ya + .07 * r.ya * (1 - r.ya), bk: booksAt(gid, name, k, r.l)}); });   // top: rungs above this one (0 = the top rung)
      }
      return out.sort((a, b) => a.i - b.i);                         // each stat's nearest high rung first
    },
    cell(gid, pid, name, kind, model){                        // compact table cell: price · edge · money each side
      const r = forPlayer(gid, pid, name).filter(x => x.k === kind && (x.l == null || x.l < 1)).sort((a, b) => (a.s === 'kalshi' ? -1 : 1))[0];
      if (!r) return '<span class="muted">–</span>';
      poly(r);
      const p = ours(r, model), e = edges(r, pq(r, model)), v = Math.max(e.y ?? -9, e.n ?? -9), side = (e.y ?? -9) >= (e.n ?? -9) ? 'yes' : 'no';
      const weak = (D.weak && D.weak.includes(r.k)) || !!lost(r.k) || !nflRuleOk(r, p, side);
      const fc = flowChips(r, e.y);
      return `<span class="mkx-c" title="${r.s === 'kalshi' ? 'Kalshi' : 'Polymarket'}: price ${cents(r.ya)}${PXF === 'c' ? '' : ` = ${px(r.ya, r.s)} with fee`} · money to buy YES ${usd(r.by)} / NO ${usd(r.bn)} within 3¢${weak ? ' · market has been more accurate on this type so far' : ''}"><span><b>${px(r.ya, r.s)}</b>${v >= .02 ? ` <span class="${weak ? 'muted' : shade(v)}" style="font-weight:700">+${(v * 100).toFixed(0)}% ${side}</span>` : ''}${lost(r.k) && v >= .02 ? ` <small class="muted" title="${esc(lostTip(r.k))}">losing record</small>` : ''}${isThin(r) ? '<span class="mkx-thin">thin</span>' : ''}${fc}</span><small>${usd(r.by)} yes · ${usd(r.bn)} no</small></span>`;
    },
  };

  // price links inside the bet chips: open the book's bet slip instead of adding the chip to the parlay
  const go = e => { const t = e.target.closest && e.target.closest('[data-go]'); if (!t) return false;
    e.preventDefault(); e.stopPropagation(); window.open(t.dataset.go, '_blank', 'noopener'); return true; };
  document.addEventListener('click', go, true);
  document.addEventListener('click', e => {                 // price format switch (saved on this device)
    const b = e.target.closest && e.target.closest('[data-pxf]'); if (!b) return;
    PXF = b.dataset.pxf; try { localStorage.setItem('simpx-v1', PXF); } catch (e2) {}
    rerender();
  });
  // Book odds filters: hide/show rows in place (no redraw, so the search box keeps focus)
  const ovApply = () => {
    for (const tr of document.querySelectorAll('[data-ovr]'))
      tr.hidden = !((OV.g === 'all' || tr.dataset.ovg === OV.g) && (OV.m === 'all' || tr.dataset.ovm === OV.m) && (!OV.q || tr.dataset.ovn.includes(norm(OV.q))) && (!tr.dataset.alt || OV.alt)
                    && (!tr.classList.contains('pcard') || ((!OV.edge || tr.dataset.ed) && (!tr.dataset.more || OV.more || OV.q))));
    const pe_ = document.querySelector('.pcempty'); if (pe_) pe_.hidden = !OV.edge || !!document.querySelector('.pcard[data-ed]:not([hidden])') || !!OV.q;
    for (const b of document.querySelectorAll('[data-ovalt]')) b.setAttribute('aria-pressed', OV.alt);
    for (const tr of document.querySelectorAll('tr.grp[data-ovg]'))
      tr.hidden = !document.querySelector(`tr[data-ovr][data-ovg="${CSS.escape(tr.dataset.ovg)}"]:not([hidden])`);
    for (const b of document.querySelectorAll('[data-ovm-pick]')) b.setAttribute('aria-pressed', b.dataset.ovmPick === OV.m);
  };
  const offApply = () => {
    const q = norm(OFV.q || ''), R = [...document.querySelectorAll('[data-offr]')]; if (!R.length) return;
    let n = 0;
    for (const r of R){
      const d = r.dataset;
      r.hidden = !((OFV.g === 'all' || d.g === OFV.g) && (OFV.k === 'all' || d.k === OFV.k) && (OFV.bk === 'all' || (OFV.bk === 'mine' ? d.my : d.bk === OFV.bk))
                   && (OFV.ln === 'all' || (OFV.ln === 'main' ? d.main : !d.main)) && +d.gap >= +OFV.gap && (!OFV.sharp || !d.c) && (!q || d.s.includes(q)));
      if (!r.hidden) n++;
    }
    for (const h of document.querySelectorAll('.offh')){ let s = h.nextElementSibling, any = false;
      while (s && s.matches('[data-offr]')){ if (!s.hidden){ any = true; break; } s = s.nextElementSibling; } h.hidden = !any; }
    for (const b of document.querySelectorAll('[data-ofv]')) b.setAttribute('aria-pressed', String(OFV[b.dataset.ofv]) === b.dataset.v);
    for (const b of document.querySelectorAll('[data-ofsharp]')) b.setAttribute('aria-pressed', OFV.sharp);
    const c = document.querySelector('.offcount'); if (c) c.textContent = n === R.length ? `${R.length} price${R.length === 1 ? '' : 's'}` : `Showing ${n} of ${R.length} prices`;
    const z = document.querySelector('.offnone'); if (z) z.hidden = n > 0;
  };
  document.addEventListener('input', e => { if (e.target.id === 'off-q'){ OFV.q = e.target.value; offApply(); } });
  document.addEventListener('change', e => { if (e.target.id === 'off-g'){ OFV.g = e.target.value; offApply(); }
    if (e.target.id === 'off-sort'){ OFV.sort = e.target.value; ofvSave(); rerender(); } });
  document.addEventListener('click', e => {
    const b = e.target.closest && e.target.closest('[data-ofv]'); if (b){ OFV[b.dataset.ofv] = b.dataset.ofv === 'gap' ? +b.dataset.v : b.dataset.v; ofvSave(); offApply(); return; }
    if (e.target.closest && e.target.closest('[data-ofsharp]')){ OFV.sharp = !OFV.sharp; ofvSave(); offApply(); return; }
    if (e.target.closest && e.target.closest('[data-ofreset]')){ Object.assign(OFV, {q: '', g: 'all', k: 'all', bk: 'all', ln: 'all', gap: 0, sharp: false}); ofvSave(); rerender(); }
  });
  document.addEventListener('input', e => { if (e.target.id === 'ov-q'){ OV.q = e.target.value; ovApply(); } });
  document.addEventListener('change', e => { if (e.target.id === 'ov-g'){ OV.g = e.target.value; ovApply(); } });
  document.addEventListener('click', e => { const b = e.target.closest && e.target.closest('[data-ovm-pick]'); if (b){ OV.m = b.dataset.ovmPick; ovApply(); } });
  document.addEventListener('click', e => { const b = e.target.closest && e.target.closest('[data-wv]'); if (b){ WV[b.dataset.wv] = !WV[b.dataset.wv]; rerender(); } });
  document.addEventListener('click', e => { const b = e.target.closest && e.target.closest('[data-ovalt]'); if (b){ OV.alt = !OV.alt; ovApply(); } });
  document.addEventListener('click', e => {
    const v = e.target.closest && e.target.closest('[data-ovview]'); if (v){ OV.view = v.dataset.ovview; ovSave(); rerender(); return; }
    const f = e.target.closest && e.target.closest('[data-ovref]'); if (f){ OV.ref = f.dataset.ovref; ovSave(); rerender(); return; }
    const d = e.target.closest && e.target.closest('[data-ovedge]'); if (d){ OV.edge = !OV.edge; ovSave(); d.setAttribute('aria-pressed', OV.edge); ovApply(); return; }
    const m = e.target.closest && e.target.closest('[data-pcmore]'); if (m){ OV.more = true; m.hidden = true; ovApply(); }
  });
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
    if (!BL && (sport === 'nba' || sport === 'nfl')) fetch(base + 'markets/boxlines_' + sport + '.json?t=' + Date.now(), {cache: 'no-store'}).then(x => x.ok ? x.json() : null).then(d => {
      if (d){ BL = d; rerender(); }
    }).catch(() => {});
    if (!FREEZE) fetch(base + 'markets/card_' + sport + '.json?t=' + Date.now(), {cache: 'no-store'}).then(x => x.ok ? x.json() : null).then(d => {
      if (!d || (CARD && d.updated === CARD.updated)) return;
      CARD = d; rerender();
    }).catch(() => {});
  }
  if (sport){ load(); loadBooks(); timer = setInterval(() => { load(); loadBooks(); }, 120000); }
})();
