/* My parlay: a build-your-own slip shared by every sport page (one slip across sports, saved in this browser).
   Pages add legs with  SLIP.btn(leg)  (a small "+" button) or  SLIP.addMany(legs).
   A leg: {id, sport, g, glab, lab, p, side, stat, line, pk, draws, model, book, o, px}
     book, o, px  (screener legs) the price shown when added and every book's price, for 'Each book pays'
     p      chance of the leg from that page's model
     draws  url of the game's saved simulations (NFL / NBA): legs from the same game are then judged in the same
            simulated games (correlation counted); otherwise legs multiply (NHL goals, MLB homers, other games). */
(function(){
  const KEY = 'simslip-v1';
  const base = (document.currentScript && document.currentScript.src || '').replace(/slip\.js.*$/, '');
  let legs = [];
  try { legs = JSON.parse(localStorage.getItem(KEY) || '[]'); } catch (e) {}
  const save = () => { try { localStorage.setItem(KEY, JSON.stringify(legs)); } catch (e) {} };
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const pct = p => p == null ? '–' : (p < .1 ? (Math.round(p * 1000) / 10).toFixed(1) : Math.round(p * 100)) + '%';
  const american = p => { p = Math.min(Math.max(p, 1e-6), 1 - 1e-6); const o = p >= .5 ? -100 * p / (1 - p) : 100 * (1 - p) / p; return (o > 0 ? '+' : '') + Math.round(o); };
  const SPORT = {nfl: 'NFL', nba: 'NBA', nhl: 'NHL', mlb: 'MLB'};
  const OWN = ['Hard Rock', 'Novig', 'Kalshi'];
  const dec = o => o > 0 ? 1 + o / 100 : 1 + 100 / -o;
  const amS = o => (o > 0 ? '+' : '') + o;
  // the legs at one book, multiplied (a book's own parlay price can differ; same-game legs especially). Only when every leg has prices
  function payouts(){
    if (!legs.length || !legs.every(L => L.px)) return null;
    const R = [...new Set(legs.flatMap(L => Object.keys(L.px)))].map(b => { const k = legs.filter(L => L.px[b] != null).length;
      return {b, k, m: k === legs.length ? legs.reduce((t, L) => t * dec(L.px[b]), 1) : null}; });
    const own = OWN.map(b => R.find(x => x.b === b) || {b, k: 0, m: null});
    return [...own, ...R.filter(x => !OWN.includes(x.b) && x.m != null).sort((a, c) => c.m - a.m).slice(0, 2)];
  }

  // ---- saved simulations (int arrays packed as base64)
  const cache = {};
  function loadDraws(url){
    if (!cache[url]) cache[url] = fetch(base + url.replace(/^\//, '') + '?t=' + Math.floor(Date.now() / 6e5)).then(r => r.ok ? r.json() : null).catch(() => null);
    return cache[url];
  }
  function decode(a){
    if (!a) return null;
    const bin = atob(a.b), u8 = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) u8[i] = bin.charCodeAt(i);
    return a.t === 'i16' ? new Int16Array(u8.buffer) : u8;
  }
  function hitsFor(L, D){
    let x;
    if (L.stat === 'win' || L.stat === 'total' || L.stat === 'spread'){
      const m = decode(D.g && D.g[L.stat === 'total' ? 'total' : 'margin']); if (!m) return null;
      if (L.stat === 'win') return Array.from(m, v => L.home ? v > 0 : v < 0);
      if (L.stat === 'spread') return Array.from(m, v => L.home ? v > L.line : v < L.line);      // line = home margin line
      return Array.from(m, v => L.side === 'under' ? v < L.line : v > L.line);
    }
    x = decode(D.p && D.p[L.pk] && D.p[L.pk][L.stat]); if (!x) return null;
    return Array.from(x, v => L.side === 'under' ? v < L.line : v > L.line);
  }

  // ---- chance of a set of legs: correlated within a game when its simulations are available, multiplied otherwise
  const hcache = {};
  const hitsOf = (L, D, url) => { const k = L.id + '|' + url; if (!(k in hcache)) hcache[k] = L.model === 'blind' ? null : hitsFor(L, D); return hcache[k]; };
  async function chanceOf(sub){
    const groups = {};
    sub.forEach(L => (groups[L.sport + '|' + L.g] = groups[L.sport + '|' + L.g] || []).push(L));
    let p = 1, indep = 1, linked = 0;
    for (const gl of Object.values(groups)){
      indep *= gl.reduce((t, L) => t * L.p, 1);
      const url = gl[0].draws;
      if (gl.length > 1 && url){
        const D = await loadDraws(url);
        if (D){
          const inSim = [], outSim = [];
          gl.forEach(L => { const h = hitsOf(L, D, url); (h ? inSim : outSim).push(h ? [L, h] : L); });
          if (inSim.length > 1){
            const n = inSim[0][1].length; let c = 0;
            for (let i = 0; i < n; i++) if (inSim.every(([, h]) => h[i])) c++;
            // scale the simulated joint chance to the page's leg chances (pages may adjust legs), then add the rest
            const raw = inSim.reduce((t, [, h]) => t * (h.filter(Boolean).length / n), 1);
            const shown = inSim.reduce((t, [L]) => t * L.p, 1);
            p *= (c / n) * (raw > 0 ? shown / raw : 1);
            outSim.forEach(L => { p *= L.p; });
            linked += inSim.length;
            continue;
          }
        }
      }
      p *= gl.reduce((t, L) => t * L.p, 1);
    }
    return {p, indep, linked};
  }
  let result = null, busy = 0;
  async function compute(){
    const my = ++busy;
    const r = await chanceOf(legs);
    if (my !== busy) return;
    result = r;
    draw();
  }

  // ---- round robin (owner 2026-11-24: 'set up round robins that can be placed through gambly manually'): every
  // combination of the slip's legs by the sizes picked (2s, 3s, ...), each priced at one book or the best of yours,
  // each with its own Gambly link (opened ones are ticked), plus one message asking Gambly for the whole round robin.
  // It adds no edge (each parlay is worth what its legs are worth); it spreads the stake so one miss doesn't sink it.
  const RKEY = 'simslip-rr-v1', DKEY = 'simslip-rrdone', RR_MAX_LEGS = 10, RR_MAX = 120;
  let RR = {on: false, k: [2], stake: 5, book: 'best'}, done = {};
  try { RR = Object.assign(RR, JSON.parse(localStorage.getItem(RKEY) || '{}')); } catch (e) {}
  try { done = JSON.parse(localStorage.getItem(DKEY) || '{}'); } catch (e) {}
  for (const k in done) if (!(done[k] > Date.now() - 3 * 864e5)) delete done[k];   // ticks last 3 days
  const saveRR = () => { try { localStorage.setItem(RKEY, JSON.stringify(RR)); } catch (e) {} };
  const saveDone = () => { try { localStorage.setItem(DKEY, JSON.stringify(done)); } catch (e) {} };
  const BK = {'Hard Rock': 'HR', Novig: 'NV', Kalshi: 'K'};
  const rrOn = () => RR.on && legs.length >= 3;
  const sizes = () => { const k = (RR.k || []).filter(x => x >= 2 && x <= legs.length).sort((a, b) => a - b); return k.length ? k : [2]; };
  const choose = (n, k) => { let c = 1; for (let i = 0; i < k; i++) c = c * (n - i) / (i + 1); return Math.round(c); };
  function combos(){
    const out = [], n = legs.length;
    for (const k of sizes()){
      const walk = (i, pick) => { if (out.length > RR_MAX) return; if (pick.length === k){ out.push(pick.slice()); return; } for (let j = i; j < n; j++){ pick.push(j); walk(j + 1, pick); pick.pop(); } };
      walk(0, []);
    }
    return out;
  }
  const comboCount = () => sizes().reduce((t, k) => t + choose(legs.length, k), 0);
  // the combination's payout: one book's prices multiplied, or the best of your books that prices every leg (else the best other book)
  function comboPay(ix){
    const ls = ix.map(i => legs[i]);
    const at = b => ls.every(L => L.px ? L.px[b] != null : L.book === b && L.o != null) ? ls.reduce((t, L) => t * dec(L.px ? L.px[b] : L.o), 1) : null;
    if (RR.book !== 'best'){ const m = at(RR.book); return m ? {b: RR.book, m} : null; }
    const books = [...new Set(ls.flatMap(L => L.px ? Object.keys(L.px) : L.book ? [L.book] : []))];
    const R = books.map(b => ({b, m: at(b)})).filter(x => x.m);
    const own = R.filter(x => OWN.includes(x.b)).sort((a, c) => c.m - a.m);
    return own[0] || R.sort((a, c) => c.m - a.m)[0] || null;
  }
  const comboKey = ix => ix.map(i => legs[i].id).sort().join('+');
  const sameGame = ix => new Set(ix.map(i => legs[i].sport + '|' + legs[i].g)).size < ix.length;
  // chance at least one parlay hits / the round robin makes money: every outcome of the legs, legs taken as unrelated
  function rrOdds(C){
    const n = legs.length, ps = legs.map(L => L.p);
    if (n > 12 || ps.some(p => p == null)) return null;
    const M = C.map(c => ({m: c.ix.reduce((t, i) => t | 1 << i, 0), d: c.pay ? c.pay.m : null}));
    const priced = M.every(c => c.d);
    let one = 0, prof = 0;
    for (let s = 0; s < 1 << n; s++){
      let pr = 1; for (let i = 0; i < n; i++) pr *= (s >> i & 1) ? ps[i] : 1 - ps[i];
      let back = 0, any = false;
      for (const c of M) if ((s & c.m) === c.m){ any = true; back += c.d || 0; }
      if (any) one += pr;
      if (priced && back > M.length) prof += pr;
    }
    return {one, prof: priced ? prof : null};
  }
  let rrRes = null, rrBusy = 0;
  const rrSig = () => legs.map(L => L.id + ':' + L.p).join(',') + '|' + sizes().join(',');
  async function computeRR(){
    const my = ++rrBusy, sig = rrSig(), C = combos().slice(0, RR_MAX), ps = [];
    for (const ix of C) ps.push((await chanceOf(ix.map(i => legs[i]))).p);
    if (my !== rrBusy) return;
    rrRes = {sig, ps};
    draw();
  }
  const legTxt = L => L.lab + (L.glab ? ` (${L.glab})` : '');
  const gamblyUrl = text => 'https://gambly.com/chat?' + new URLSearchParams({entry: 'ask-gambly', prompt: text, autoSubmit: '1'}).toString() + '&utm_campaign=thecanadindian';
  const atBook = () => RR.book !== 'best' ? ` at ${RR.book}` : '';
  const comboText = ix => `Build this ${ix.length}-leg parlay${atBook()}:\n` + ix.map((i, j) => `${j + 1}. ${legTxt(legs[i])}`).join('\n');
  const byTxt = () => sizes().map(k => k === legs.length ? `all ${k}` : `by ${k}s`).join(' and ');
  const rrText = C => `Build a round robin ${byTxt()}${atBook()}, $${RR.stake} per parlay (${C.length} parlays), with these ${legs.length} legs:\n` + legs.map((L, i) => `${i + 1}. ${legTxt(L)}`).join('\n');
  const rrCopy = C => `Round robin ${byTxt()} · ${C.length} parlays x $${RR.stake} = $${C.length * RR.stake}\nLegs:\n`
    + legs.map((L, i) => `${i + 1}. ${legTxt(L)}${L.o != null ? ` ${amS(L.o)} ${L.book}` : ''}`).join('\n')
    + '\nParlays:\n' + C.map((c, j) => `${j + 1}. legs ${c.ix.map(i => i + 1).join(' + ')}: ${c.ix.map(i => legs[i].lab).join(' / ')}${c.pay ? ` · ${c.pay.m.toFixed(1)}x ${c.pay.b}` : ''}${c.p != null ? ` · hits ${pct(c.p)}` : ''}`).join('\n');
  const sgn = e => { const r = Math.round(e * 100); return (r >= 0 ? '+' : '−') + Math.abs(r) + '%'; };
  function rrHtml(){
    const n = legs.length;
    if (n > RR_MAX_LEGS) return `<p class="note warn">Round robins take up to ${RR_MAX_LEGS} legs; remove ${n - RR_MAX_LEGS} to build one.</p><div class="acts"><button data-slipclear>Clear</button><button data-slipclose>Close</button></div>`;
    const fresh = rrRes && rrRes.sig === rrSig(), total = comboCount();
    const C = combos().slice(0, RR_MAX).map((ix, j) => ({ix, key: comboKey(ix), pay: comboPay(ix), sg: sameGame(ix), p: fresh ? rrRes.ps[j] : null}));
    if (!fresh) computeRR();
    const anyPx = legs.some(L => L.px || L.o != null);
    const sizeChips = Array.from({length: n - 1}, (_, i) => i + 2).map(k => { const c = choose(n, k);
      return `<button type="button" class="chip${sizes().includes(k) ? ' on' : ''}" data-rrk="${k}" title="${c} parlay${c === 1 ? '' : 's'}">${k === n ? `All ${k}` : `${k}s`} <i>${c}</i></button>`; }).join('');
    const stakeChips = [1, 2, 5, 10].map(v => `<button type="button" class="chip${RR.stake === v ? ' on' : ''}" data-rrs="${v}">$${v}</button>`).join('');
    const bookChips = anyPx ? `<div class="opts"><span class="lab">Price at</span>${['best', ...OWN].map(b => `<button type="button" class="chip${RR.book === b ? ' on' : ''}" data-rrb="${esc(b)}">${b === 'best' ? 'Best of yours' : esc(b)}</button>`).join('')}</div>` : '';
    const odds = rrOdds(C), priced = C.every(c => c.pay), ready = C.every(c => c.p != null);
    const ev = priced && ready ? C.reduce((t, c) => t + c.p * c.pay.m, 0) / C.length - 1 : null;
    const back = priced ? C.reduce((t, c) => t + c.pay.m, 0) * RR.stake : null;
    const sg = C.some(c => c.sg), nOpen = C.filter(c => done[c.key]).length, cost = C.length * RR.stake;
    const rows = C.map(c => {
      const e = c.p != null && c.pay ? c.p * c.pay.m - 1 : null, txt = comboText(c.ix);
      const tip = c.ix.map(i => `${i + 1}. ${legs[i].lab}`).join('\n') + (c.sg ? '\nTwo legs from one game: most books won’t take that in a round robin; our chance counts their link' : '');
      return `<div class="rrr" title="${esc(tip)}"><div class="who"><b>${c.ix.map(i => i + 1).join(' · ')}</b>${c.sg ? ' <span class="sgt">same game</span>' : ''}<small>${esc(c.ix.map(i => legs[i].lab).join(' / '))}</small></div>
        <span class="num">${pct(c.p)}</span><span class="num">${c.pay ? `${c.pay.m.toFixed(1)}x${RR.book === 'best' ? `<i>${esc(BK[c.pay.b] || c.pay.b)}</i>` : ''}` : '—'}</span>
        <span class="num ${e == null ? '' : e >= 0 ? 'pos' : 'neg'}">${e == null ? '' : sgn(e)}</span>
        <a class="g${done[c.key] ? ' done' : ''}" data-rrgo="${esc(c.key)}" data-txt="${esc(txt)}" href="${esc(gamblyUrl(txt))}" target="_blank" rel="noopener" title="Opens Gambly’s chat with this parlay typed in (also copied, to paste if it opens empty)">${done[c.key] ? '✓ Opened' : 'Gambly ↗'}</a></div>`;
    }).join('');
    const m = sizes()[0];
    return `<div class="opts"><span class="lab">By</span>${sizeChips}</div>
      <div class="opts"><span class="lab">Each parlay</span>${stakeChips}</div>${bookChips}
      <div class="sum four"><div><b>${C.length}</b><span>parlays · $${cost}</span></div><div><b>${odds ? pct(odds.one) : '–'}</b><span>1+ hits</span></div><div><b>${odds && odds.prof != null ? pct(odds.prof) : '–'}</b><span>makes money</span></div><div><b class="${ev == null ? '' : ev >= 0 ? 'pos' : 'neg'}">${ev == null ? '–' : sgn(ev)}</b><span>expected</span></div></div>
      ${total > RR_MAX ? `<p class="note warn">${total} parlays: showing the first ${RR_MAX}. Pick fewer sizes or legs.</p>` : ''}
      <div class="rrl"><div class="rrh"><span>Legs</span><span>hits</span><span>pays</span><span>edge</span><span>${nOpen ? `${nOpen} / ${C.length} opened` : ''}</span></div>${rows}</div>
      <p class="note">A parlay hits once any ${m} legs hit${back != null ? `; all ${n} pay $${Math.round(back).toLocaleString()} back on $${cost}` : ''}. A round robin adds no edge; it spreads the stake.${sg ? ' Same-game parlays: most books won’t take them in a round robin (1+ hits / makes money treat their legs as unrelated).' : ''}${anyPx ? ' Pays = the legs’ prices when added, multiplied.' : ' Add legs from the Screener or a Ladders tab to see what each book pays.'} Gambly ↗ builds each parlay there (✓ = opened), or ask it for the whole round robin.</p>
      <div class="acts"><a href="${esc(gamblyUrl(rrText(C)))}" target="_blank" rel="noopener" data-rrall data-txt="${esc(rrText(C))}">Ask Gambly for the whole round robin ↗</a><button data-rrcopy="${esc(rrCopy(C))}">Copy all parlays</button>${nOpen ? '<button data-rrreset>Untick opened</button>' : ''}<button data-slipclear>Clear</button><button data-slipclose>Close</button></div>`;
  }

  // ---- UI
  const css = `.slipfab{position:fixed;right:16px;bottom:16px;z-index:60;border:1px solid var(--accent,#36D399);background:var(--surface,#111A1F);color:var(--ink,#EAF1F4);border-radius:999px;padding:9px 16px;font:600 14px/1 var(--body,system-ui);cursor:pointer;box-shadow:0 6px 20px rgba(0,0,0,.35)}
.slipfab b{color:var(--accent,#36D399)}
.slippanel{position:fixed;right:16px;bottom:62px;z-index:61;width:min(380px,calc(100vw - 32px));max-height:min(70vh,640px);overflow:auto;background:var(--surface,#111A1F);color:var(--ink,#EAF1F4);border:1px solid var(--faint,#22313A);border-radius:12px;padding:14px;display:grid;gap:10px;box-shadow:0 12px 40px rgba(0,0,0,.45);font:14px/1.4 var(--body,system-ui)}
.slippanel h3{margin:0;font:600 18px/1 var(--display,system-ui);text-transform:uppercase;letter-spacing:.02em;display:flex;justify-content:space-between;align-items:center}
.slippanel ol{margin:0;padding-left:20px;display:grid;gap:4px}
.slippanel li{display:grid;grid-template-columns:1fr auto;gap:8px;align-items:baseline}
.slippanel li > span:last-child{white-space:nowrap}
.slippanel li small{color:var(--muted,#95A6B0)}
.slippanel .x{border:0;background:none;color:var(--muted,#95A6B0);cursor:pointer;font-size:15px;padding:0 2px}
.slippanel h3 .x{font-size:20px;min-width:40px;min-height:40px;margin:-8px -8px -8px 0;color:var(--ink,#EAF1F4)}
.slippanel li .x{min-width:30px;min-height:30px}
.slippanel.rr{width:min(460px,calc(100vw - 32px));max-height:min(80vh,760px)}
@media (max-width:640px){.slippanel{bottom:64px;max-height:65vh}.slippanel.rr{max-height:75vh}.slipfab{bottom:12px}}
.slippanel .sum{display:grid;grid-template-columns:repeat(3,1fr);gap:6px;text-align:center;border-top:1px solid var(--faint,#22313A);padding-top:8px}
.slippanel .sum b{display:block;font-size:18px}
.slippanel .sum span{font-size:11.5px;color:var(--muted,#95A6B0)}
.slippanel .acts{display:flex;flex-wrap:wrap;gap:6px}
.slippanel .acts a,.slippanel .acts button{font-size:12.5px;border:1px solid var(--faint,#22313A);border-radius:999px;padding:4px 11px;color:var(--ink,#EAF1F4);background:none;text-decoration:none;cursor:pointer}
.slippanel .note{font-size:11.5px;color:var(--muted,#95A6B0);margin:0}
.slipfab[hidden],.slippanel[hidden]{display:none!important}
.slippanel .pays{display:flex;flex-wrap:wrap;gap:4px 10px;font-size:12.5px}.slippanel .pays b{font-weight:700}.slippanel .pays .top{color:var(--accent,#36D399)}.slippanel .pays .no{color:var(--muted,#95A6B0)}
.slipadd{border:1px solid var(--faint,#22313A);background:none;color:var(--accent,#36D399);border-radius:999px;padding:1px 8px;font-size:12px;cursor:pointer;margin-left:6px;white-space:nowrap}
.slipadd.on{background:var(--accent,#36D399);color:var(--bg,#0A0F12);border-color:var(--accent,#36D399)}
.slippanel .seg{display:flex;width:fit-content;border:1px solid var(--faint,#22313A);border-radius:999px;overflow:hidden}
.slippanel .seg button{border:0;background:none;color:var(--muted,#95A6B0);font:600 12.5px/1 var(--body,system-ui);padding:8px 14px;cursor:pointer}
.slippanel .seg button.on{background:var(--accent,#36D399);color:var(--bg,#0A0F12)}
.slippanel .opts{display:flex;flex-wrap:wrap;gap:5px;align-items:center}
.slippanel .opts .lab{font-size:11.5px;color:var(--muted,#95A6B0);min-width:62px}
.slippanel .chip{border:1px solid var(--faint,#22313A);border-radius:999px;background:none;color:var(--ink,#EAF1F4);font:12.5px/1 var(--body,system-ui);padding:7px 11px;cursor:pointer}
.slippanel .chip i{font-style:normal;color:var(--muted,#95A6B0);font-size:11px;margin-left:2px}
.slippanel .chip.on{border-color:var(--accent,#36D399);color:var(--accent,#36D399);font-weight:700}
.slippanel .sum.four{grid-template-columns:repeat(4,1fr)}
.slippanel .pos{color:var(--good,#36D399)}.slippanel .neg{color:var(--bad,#F87171)}
.slippanel .rrl{display:grid}
.slippanel .rrh,.slippanel .rrr{display:grid;grid-template-columns:minmax(0,1fr) 40px 50px 42px 78px;gap:6px;align-items:center}
.slippanel .rrh{font-size:11px;color:var(--muted,#95A6B0);padding-bottom:3px}.slippanel .rrh span:not(:first-child){text-align:right}
.slippanel .rrr{border-top:1px solid var(--faint,#22313A);padding:6px 0}
.slippanel .rrr .who{min-width:0}.slippanel .rrr .who b{font-variant-numeric:tabular-nums}
.slippanel .rrr .who small{display:block;color:var(--muted,#95A6B0);font-size:11.5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.slippanel .rrr .num{text-align:right;font-size:13px;font-variant-numeric:tabular-nums;white-space:nowrap}
.slippanel .rrr .num i{font-style:normal;color:var(--muted,#95A6B0);font-size:10.5px;margin-left:3px}
.slippanel .rrr a.g{justify-self:end;font-size:12px;font-weight:600;border:1px solid var(--faint,#22313A);border-radius:999px;padding:6px 9px;color:var(--ink,#EAF1F4);text-decoration:none;white-space:nowrap}
.slippanel .rrr a.g.done{color:var(--good,#36D399);border-color:color-mix(in srgb,var(--good,#36D399) 45%,transparent)}
.slippanel .sgt{font-size:10.5px;color:var(--warn,#E0A458);border:1px solid color-mix(in srgb,var(--warn,#E0A458) 45%,transparent);border-radius:999px;padding:0 6px;margin-left:4px}
.slippanel .note.warn{color:var(--warn,#E0A458)}
@media (max-width:420px){.slippanel .rrh,.slippanel .rrr{grid-template-columns:minmax(0,1fr) 36px 46px 38px 74px;gap:4px}.slippanel .opts .lab{min-width:0;width:100%}}`;
  const st = document.createElement('style'); st.textContent = css; document.head.appendChild(st);
  const fab = document.createElement('button'); fab.className = 'slipfab'; fab.type = 'button';
  const panel = document.createElement('div'); panel.className = 'slippanel'; panel.hidden = true;
  document.addEventListener('DOMContentLoaded', () => { document.body.append(fab, panel); draw(); });
  if (document.body) document.body.append(fab, panel);
  fab.addEventListener('click', e => { e.stopPropagation(); panel.hidden = !panel.hidden; draw(); });
  document.addEventListener('keydown', e => { if (e.key === 'Escape' && !panel.hidden){ panel.hidden = true; draw(); } });

  function gambly(){
    const text = 'Build a ' + legs.length + '-leg parlay: ' + legs.map(L => L.lab + (L.glab ? ` (${L.glab})` : '')).join(', ');
    return 'https://gambly.com/chat?' + new URLSearchParams({entry: 'ask-gambly', prompt: text, autoSubmit: '1'}).toString() + '&utm_campaign=thecanadindian';
  }
  // keep the slip inside what's on screen: wide pages (tables) can make the page wider than a phone, and fixed
  // elements then sit off the right edge. Place from the visual viewport instead.
  function place(){
    const vv = window.visualViewport, w = vv ? vv.width : window.innerWidth, ox = vv ? vv.offsetLeft : 0;
    if (w < 640){
      panel.style.left = (ox + 8) + 'px'; panel.style.right = 'auto'; panel.style.width = (w - 16) + 'px';
      fab.style.right = 'auto'; fab.style.left = (ox + w - fab.offsetWidth - 12) + 'px';
    } else {
      panel.style.left = panel.style.width = fab.style.left = ''; panel.style.right = fab.style.right = '';
    }
  }
  if (window.visualViewport){ visualViewport.addEventListener('resize', place); visualViewport.addEventListener('scroll', place); }
  window.addEventListener('resize', place);
  function draw(){
    fab.hidden = false;                                            // always visible so the slip can be found
    fab.innerHTML = !legs.length ? 'My parlay <b>+</b>' : rrOn() && legs.length <= RR_MAX_LEGS ? `My parlay <b>${legs.length}</b> · round robin ${comboCount()}`
      : `My parlay <b>${legs.length}</b>${result ? ` · ${pct(result.p)}` : ''}`;
    place();
    panel.classList.toggle('rr', !!(legs.length && rrOn()));
    if (panel.hidden) return;
    const seg = legs.length >= 3 ? `<div class="seg" role="group" aria-label="Bet as"><button type="button" class="${rrOn() ? '' : 'on'}" data-rrmode="0" aria-pressed="${!rrOn()}">One parlay</button><button type="button" class="${rrOn() ? 'on' : ''}" data-rrmode="1" aria-pressed="${rrOn()}">Round robin</button></div>` : '';
    const legList = `<ol>${legs.map(L => `<li><span>${esc(L.lab)} <small>${L.o != null ? amS(L.o) + ' ' + esc(L.book) + ' · ' : ''}${esc(SPORT[L.sport] || '')}${L.glab ? ' · ' + esc(L.glab) : ''}</small></span><span>${pct(L.p)} <button class="x" data-slipdel="${esc(L.id)}" aria-label="Remove">✕</button></span></li>`).join('')}</ol>`;
    if (legs.length && rrOn()){
      panel.innerHTML = `<h3>Round robin · ${legs.length} legs <button class="x" data-slipclose aria-label="Close">✕</button></h3>${seg}${legList}${rrHtml()}`;
      return;
    }
    if (!legs.length){ panel.innerHTML = `<h3>My parlay <button class="x" data-slipclose aria-label="Close">✕</button></h3><p class="note">Tap a "+" on any Screener row, or next to any line on the NFL, NBA, NHL or MLB pages, to add it (NHL: "+ ATG" by each player; NFL / NBA: open a player and use + Over / + Under; MLB: "+" by HR%). Legs from different sports can be combined.</p><div class="acts"><button data-slipclose>Close</button></div>`; return; }
    const r = result || {}, P = payouts(), top = P ? Math.max(0, ...P.map(x => x.m || 0)) : 0;
    const pays = P ? `<div class="pays"><span class="no">Each book pays</span>${P.map(x => `<span class="${x.m === top && top ? 'top' : x.m == null ? 'no' : ''}" title="${x.m == null ? `${esc(x.b)} has ${x.k} of ${legs.length} legs` : 'Legs multiplied'}">${esc(x.b)} <b>${x.m == null ? '—' : x.m.toFixed(1) + 'x'}</b></span>`).join('')}${r.p && top ? `<span class="${r.p * top >= 1 ? 'top' : 'no'}">best ${r.p * top >= 1 ? '+' : '−'}${Math.abs(Math.round((r.p * top - 1) * 100))}%</span>` : ''}</div>` : '';
    panel.innerHTML = `<h3>My parlay · ${legs.length} leg${legs.length === 1 ? '' : 's'} <button class="x" data-slipclose aria-label="Close">✕</button></h3>${seg}
      ${legList}
      <div class="sum"><div><b>${pct(r.p)}</b><span>hits</span></div><div><b>${r.p ? american(r.p) : '–'}</b><span>fair odds</span></div><div><b>${pct(r.indep)}</b><span>if unrelated</span></div></div>${pays}
      <p class="note">${r.linked ? `${r.linked} legs judged together in the same simulated games (their link counts). ` : ''}Other legs multiply. Take a book's parlay only if it pays more than the fair odds.${P ? ' Payouts are the legs multiplied at prices from when you added them; a book’s own parlay price can differ, same-game ones especially.' : ''}</p>
      <div class="acts"><a href="${esc(gambly())}" target="_blank" rel="noopener">Build in Gambly ↗</a><button data-slipcopy>Copy legs</button><button data-slipclear>Clear</button><button data-slipclose>Close</button></div>`;
  }
  function changed(){ save(); result = null; draw(); compute(); try { window.render && window.render(); } catch (e) {} }
  document.addEventListener('click', e => {
    const a = e.target.closest('[data-slip]');
    if (a){ e.preventDefault(); e.stopPropagation(); let L; try { L = JSON.parse(a.dataset.slip); } catch (err) { return; }
      const i = legs.findIndex(x => x.id === L.id);
      if (i >= 0) legs.splice(i, 1);
      else { legs = legs.filter(x => !(x.pk && x.pk === L.pk && x.stat === L.stat)); legs.push(L); }
      changed(); return; }
    const all = e.target.closest('[data-sliplegs]');
    if (all){ e.preventDefault(); let ls; try { ls = JSON.parse(all.dataset.sliplegs); } catch (err) { return; } SLIP.addMany(ls); panel.hidden = false; changed(); return; }
    if (e.target.closest('[data-slipclose]')){ panel.hidden = true; draw(); return; }
    if (!panel.hidden && !e.target.closest('.slippanel') && !e.target.closest('.slipfab')){ panel.hidden = true; draw(); }   // tap outside closes
    const d = e.target.closest('[data-slipdel]'); if (d){ legs = legs.filter(x => x.id !== d.dataset.slipdel); changed(); return; }
    if (e.target.closest('[data-slipclear]')){ legs = []; done = {}; saveDone(); changed(); return; }
    // round robin controls
    const md = e.target.closest('[data-rrmode]'); if (md){ RR.on = md.dataset.rrmode === '1'; saveRR(); draw(); return; }
    const rk = e.target.closest('[data-rrk]'); if (rk){ const k = +rk.dataset.rrk, cur = sizes();
      RR.k = cur.includes(k) ? cur.filter(x => x !== k) : [...cur, k]; if (!RR.k.length) RR.k = [k]; saveRR(); draw(); return; }
    const rs = e.target.closest('[data-rrs]'); if (rs){ RR.stake = +rs.dataset.rrs; saveRR(); draw(); return; }
    const rb = e.target.closest('[data-rrb]'); if (rb){ RR.book = rb.dataset.rrb; saveRR(); draw(); return; }
    const go = e.target.closest('[data-rrgo]');                  // the link opens Gambly itself; tick it after the click goes through
    if (go){ try { navigator.clipboard.writeText(go.dataset.txt); } catch (err) {} done[go.dataset.rrgo] = Date.now(); saveDone(); setTimeout(draw, 0); return; }
    const ra = e.target.closest('[data-rrall]'); if (ra){ try { navigator.clipboard.writeText(ra.dataset.txt); } catch (err) {} return; }
    const rc = e.target.closest('[data-rrcopy]'); if (rc){ try { navigator.clipboard.writeText(rc.dataset.rrcopy); } catch (err) {}
      const t = rc.textContent; rc.textContent = 'Copied ✓'; setTimeout(() => { rc.textContent = t; }, 2500); return; }
    if (e.target.closest('[data-rrreset]')){ combos().forEach(ix => delete done[comboKey(ix)]); saveDone(); draw(); return; }
    if (e.target.closest('[data-slipcopy]')){ const t = legs.map(L => `${L.lab}${L.glab ? ' (' + L.glab + ')' : ''}`).join('\n'); try { navigator.clipboard.writeText(t); } catch (err) {} return; }
  });
  window.SLIP = {
    has: id => legs.some(x => x.id === id),
    btn(L, label){ const on = legs.some(x => x.id === L.id); return `<button type="button" class="slipadd${on ? ' on' : ''}" data-slip="${esc(JSON.stringify(L))}" title="${on ? 'Remove from' : 'Add to'} my parlay">${on ? '✓' : '+'}${label ? ' ' + esc(label) : ''}</button>`; },
    allBtn(ls, label){ return `<button type="button" class="slipadd" data-sliplegs="${esc(JSON.stringify(ls))}">${esc(label || '+ Add to my parlay')}</button>`; },
    addMany(ls){ ls.forEach(L => { if (!legs.some(x => x.id === L.id)){ legs = legs.filter(x => !(x.pk && x.pk === L.pk && x.stat === L.stat)); legs.push(L); } }); changed(); },
  };
  compute(); draw();
  try { window.render && window.render(); } catch (e) {}
})();
