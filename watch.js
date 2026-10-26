/* Watchlist (owner 2026-10-26: "a way for me to put lines on a watchlist for later in the day and their movement, from
   the screener or anywhere on the site"). Saved in this browser (localStorage simwatch-v1), shared by every page.
   Pages add a line with  WATCH.btn(item)  (a small ☆ button). An item:
     {sport, gid, name (the page key: lower-case, accents / suffixes stripped), disp, kind, line, book, o (price when added),
      fair (fair chance when added, optional), glab, start, lab}
   The panel re-reads markets/props_<sport>.json and live_<sport>.json (Kalshi's ladders) every minute while a page is open:
   that book's price at that line now, the books' main line now (moved?), the fair now and the edge at the price, and a
   short history of every change it saw. Started games freeze at their last pregame price. */
(function(){
  const KEY = 'simwatch-v1';
  const base = (document.currentScript && document.currentScript.src || '').replace(/watch\.js.*$/, '');
  let items = [];
  try { items = JSON.parse(localStorage.getItem(KEY) || '[]'); } catch (e) {}
  const save = () => { try { localStorage.setItem(KEY, JSON.stringify(items)); } catch (e) {} };
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  const dec = o => o > 0 ? 1 + o / 100 : 1 + 100 / -o;
  const am = o => o == null ? '–' : (o > 0 ? '+' : '') + Math.round(o);
  const pc = p => p == null ? '–' : Math.round(p * 100) + '%';
  const norm = s => String(s || '').normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase().replace(/[^a-z ]/g, '').replace(/\b(jr|sr|ii|iii|iv)\b/g, '').replace(/\s+/g, ' ').trim();
  const SP = {nfl: 'NFL', nba: 'NBA', nhl: 'NHL'};
  const idOf = it => [it.sport, it.gid, norm(it.name), it.kind, it.line, it.book].join('|');
  const fee = ya => ya + .07 * ya * (1 - ya);
  const toAm = q => q >= .5 ? -100 * q / (1 - q) : 100 * (1 - q) / q;

  // ---------------------------------------------------------------- current prices
  const DATA = {};
  async function load(sport){
    const get = f => fetch(`${base}markets/${f}_${sport}.json?t=${Date.now()}`, {cache: 'no-store'}).then(r => r.ok ? r.json() : null).catch(() => null);
    const [P, L] = await Promise.all([get('props'), get('live')]);
    DATA[sport] = {P, L, at: Date.now()};
  }
  function now(it){
    const D = DATA[it.sport]; if (!D || !D.P) return null;
    const e = (((D.P.props || {})[String(it.gid)] || {})[norm(it.name)] || {})[it.kind];
    const out = {main: e && e.l != null ? +e.l : null, o: null, fair: null};
    if (e){
      const at = (e.l == null && (it.line == null || +it.line === .5)) || (e.l != null && Math.abs(+e.l - +it.line) < 1e-9) ? e : (e.a || {})[String(it.line)] || (e.a || {})[(+it.line).toFixed(1)];
      if (at && at.o && at.o[it.book] && at.o[it.book][0] != null) out.o = at.o[it.book][0];
      if (at === e && e.pin && e.pin.o != null && Math.abs((e.pin.l ?? e.l) - e.l) < 1e-9) out.fair = e.pin.o;
      else if (at && at.pin != null) out.fair = at.pin;
    }
    if (out.o == null && it.book === 'Kalshi' && D.L){                    // Kalshi's ladder rungs (ask + fee)
      const r = (D.L.rows || []).find(x => x.s === 'kalshi' && String(x.g) === String(it.gid) && x.k === it.kind && x.l != null && Math.abs(+x.l - +it.line) < 1e-9 && norm(x.n) === norm(it.name));
      if (r && r.ya) out.o = Math.round(toAm(fee(r.ya)));
    }
    return out;
  }
  async function refresh(){
    const sports = [...new Set(items.map(x => x.sport))];
    await Promise.all(sports.map(load));
    let ch = false;
    for (const it of items){
      if (it.start && new Date(it.start) <= Date.now()){ it.done = true; continue; }       // started: keep the last pregame price
      const n = now(it); if (!n) continue;
      it.cur = n; it.seen = Date.now();
      const last = (it.h || [])[it.h.length - 1];
      if (!last || last.o !== n.o || last.m !== n.main){ (it.h = it.h || []).push({t: Date.now(), o: n.o, m: n.main}); it.h = it.h.slice(-12); ch = true; }
    }
    save(); draw();
  }

  // ---------------------------------------------------------------- odds chart (owner 2026-10-26: "a line graph to show how
  // the various book odds move"). One line per book: the price's implied chance over time (one axis), the sharp fair as a
  // dashed neutral line; colors follow the book (fixed order: your books first), never its rank; legend always, end labels
  // when 4 or fewer; crosshair + one tooltip listing every book at that time. Dark palette from the dataviz reference.
  const HIST = {};
  const loadHist = sport => HIST[sport] || (HIST[sport] = fetch(`${base}markets/hist_${sport}.json?t=${Math.floor(Date.now() / 6e4)}`, {cache: 'no-store'}).then(r => r.ok ? r.json() : null).catch(() => null));
  const BOOKC = [['Hard Rock', '#3987e5'], ['Novig', '#d95926'], ['Kalshi', '#199e70'], ['DK', '#c98500'], ['FD', '#d55181'], ['Caesars', '#008300'], ['MGM', '#9085e9'], ['ProphetX', '#e66767']];
  const colorOf = b => (BOOKC.find(x => x[0] === b) || [null, '#7d8b93'])[1];
  const FULLB = {DK: 'DraftKings', FD: 'FanDuel', MGM: 'BetMGM'};
  const SHORTB = {'Hard Rock': 'HR', Novig: 'NV', Kalshi: 'K', DK: 'DK', FD: 'FD', Caesars: 'CZR', MGM: 'MGM', ProphetX: 'PX'};   // end labels (the legend has full names)
  function series(H, it){
    const g = H && H[String(it.gid)]; if (!g) return null;
    const ln = it.line == null ? .5 : +it.line;
    const e = g.p[`${norm(it.name)}|${it.kind}|${ln.toString()}`] || g.p[`${norm(it.name)}|${it.kind}|${ln.toFixed(1)}`];
    if (!e) return null;
    const S = Object.entries(e.b).map(([b, h]) => ({b, h: h.map(([t, o]) => [t * 6e4, 1 / dec(o), o])}));
    const rank = b => { const i = BOOKC.findIndex(x => x[0] === b); return i < 0 ? 99 : i; };
    S.sort((a, b) => (b.b === it.book) - (a.b === it.book) || rank(a.b) - rank(b.b));
    const other = S.slice(8);                                            // a 9th book never gets a generated hue: it folds away
    return {S: S.slice(0, 8), fair: (e.f || []).map(([t, f]) => [t * 6e4, f]), folded: other.length};
  }
  function chartHTML(it, D){
    if (!D || !D.S.length) return `<p class="note">No price history for this line yet: the site starts recording a line when a book posts it (every ~15 minutes).</p>`;
    const W = 420, Hh = 170, L = 34, R = 74, T = 10, B = 22;
    const now = Date.now(), t0 = Math.min(...D.S.map(x => x.h[0][0]), ...(D.fair.length ? [D.fair[0][0]] : [])), t1 = Math.min(now, it.start ? new Date(it.start).getTime() : now);
    const vals = [...D.S.flatMap(x => x.h.map(v => v[1])), ...D.fair.map(v => v[1])];
    let lo = Math.min(...vals), hi = Math.max(...vals); const pad = Math.max(.02, (hi - lo) * .15); lo = Math.max(0, lo - pad); hi = Math.min(1, hi + pad);
    const X = t => L + (W - L - R) * (t1 > t0 ? (t - t0) / (t1 - t0) : 1), Y = v => T + (Hh - T - B) * (1 - (v - lo) / (hi - lo || 1));
    const step = h => { let d = ''; h.forEach(([t, v], i) => { const x = X(Math.min(t, t1)).toFixed(1), y = Y(v).toFixed(1); d += i ? ` H${x} V${y}` : `M${x} ${y}`; }); return d + ` H${X(t1).toFixed(1)}`; };
    const ticks = [lo, (lo + hi) / 2, hi].map(v => `<line x1="${L}" x2="${W - R}" y1="${Y(v)}" y2="${Y(v)}" class="g"/><text x="${L - 4}" y="${Y(v) + 3}" text-anchor="end">${Math.round(v * 100)}%</text>`).join('');
    const tl = [t0, (t0 + t1) / 2, t1].map((t, i) => `<text x="${X(t)}" y="${Hh - 6}" text-anchor="${['start', 'middle', 'end'][i]}">${new Date(t).toLocaleTimeString([], {hour: 'numeric', minute: '2-digit'})}</text>`).join('');
    const fair = D.fair.length ? `<path d="${step(D.fair)}" class="fair"/>` : '';
    const lines = D.S.map(x => `<path d="${step(x.h)}" stroke="${colorOf(x.b)}" class="ln${x.b === it.book ? ' me' : ''}"/>`).join('');
    // end labels (4 or fewer series): the latest price, nudged apart so they don't collide
    let ends = '';
    if (D.S.length <= 4){ const ys = D.S.map(x => ({x, y: Y(x.h[x.h.length - 1][1])})).sort((a, b) => a.y - b.y);
      for (let i = 1; i < ys.length; i++) if (ys[i].y - ys[i - 1].y < 11) ys[i].y = ys[i - 1].y + 11;
      ends = ys.map(({x, y}) => `<circle cx="${X(t1)}" cy="${Y(x.h[x.h.length - 1][1])}" r="3" fill="${colorOf(x.b)}" class="dot"/><text x="${X(t1) + 6}" y="${y + 3}" class="lab">${esc(SHORTB[x.b] || x.b.slice(0, 3))} ${am(x.h[x.h.length - 1][2])}</text>`).join(''); }
    const legend = D.S.map(x => `<span><i style="background:${colorOf(x.b)}"></i>${esc(FULLB[x.b] || x.b)}${x.b === it.book ? ' (watched)' : ''}</span>`).join('') + (D.fair.length ? '<span><i class="fl"></i>Sharp fair</span>' : '') + (D.folded ? `<span class="muted">+${D.folded} more books</span>` : '');
    const id = 'oc' + Math.random().toString(36).slice(2);
    setTimeout(() => wireHover(id, D, X, t0, t1, W, L, R), 0);
    return `<div class="oc" id="${id}"><svg viewBox="0 0 ${W} ${Hh}" role="img" aria-label="Price history by book for ${esc(it.lab)}">${ticks}${tl}${fair}${lines}${ends}<line class="xh" x1="0" x2="0" y1="${T}" y2="${Hh - B}" visibility="hidden"/><rect class="hit" x="${L}" y="${T}" width="${W - L - R}" height="${Hh - T - B}"/></svg>
      <div class="tip" hidden></div><div class="lg">${legend}</div><p class="note">Implied chance of each book’s price (higher = shorter odds). Hover or touch for every book at that time.</p></div>`;
  }
  function wireHover(id, D, X, t0, t1, W, L, R){
    const el = document.getElementById(id); if (!el) return;
    const svg = el.querySelector('svg'), xh = el.querySelector('.xh'), tip = el.querySelector('.tip'), hit = el.querySelector('.hit');
    const at = (h, t) => { let v = null; for (const p of h) { if (p[0] <= t) v = p; else break; } return v; };
    const show = ev => { const r = svg.getBoundingClientRect(), px = (ev.touches ? ev.touches[0].clientX : ev.clientX) - r.left, sx = px * W / r.width;
      const t = t0 + (t1 - t0) * Math.min(1, Math.max(0, (sx - L) / (W - L - R)));
      xh.setAttribute('x1', X(t)); xh.setAttribute('x2', X(t)); xh.setAttribute('visibility', 'visible');
      tip.hidden = false; tip.textContent = '';
      const hd = document.createElement('b'); hd.textContent = new Date(t).toLocaleTimeString([], {hour: 'numeric', minute: '2-digit'}); tip.appendChild(hd);
      D.S.forEach(x => { const v = at(x.h, t); if (!v) return; const row = document.createElement('div'); const sw = document.createElement('i'); sw.style.background = colorOf(x.b);
        const val = document.createElement('strong'); val.textContent = am(v[2]); row.append(sw, val, document.createTextNode(' ' + (FULLB[x.b] || x.b) + ` · ${Math.round(v[1] * 100)}%`)); tip.appendChild(row); });
      const f = at(D.fair, t); if (f){ const row = document.createElement('div'); row.textContent = `Sharp fair ${Math.round(f[1] * 100)}%`; row.className = 'muted'; tip.appendChild(row); }
      tip.style.left = Math.min(Math.max(0, px - 70), r.width - 150) + 'px'; };
    const hide = () => { xh.setAttribute('visibility', 'hidden'); tip.hidden = true; };
    hit.addEventListener('pointermove', show); hit.addEventListener('pointerdown', show); hit.addEventListener('pointerleave', hide);
  }
  async function openChart(it, host){
    host.innerHTML = '<p class="note">Loading price history…</p>';
    const H = await loadHist(it.sport);
    host.innerHTML = chartHTML(it, series(H, it));
  }
  const modal = document.createElement('div'); modal.className = 'oddsmodal'; modal.hidden = true;
  // ---------------------------------------------------------------- ui
  const css = `
.watchfab{position:fixed;left:12px;bottom:12px;z-index:60;border:1px solid var(--faint,#22313A);background:var(--surface,#11181D);color:var(--ink,#E8EEF1);border-radius:999px;padding:8px 14px;font:600 13.5px var(--body,system-ui);cursor:pointer;box-shadow:0 4px 14px rgba(0,0,0,.35)}
.watchfab b{color:#F2C14E}
.watchpanel{position:fixed;left:12px;bottom:58px;z-index:61;width:min(460px,calc(100vw - 24px));max-height:70vh;overflow:auto;background:var(--surface,#11181D);border:1px solid var(--faint,#22313A);border-radius:12px;padding:12px 14px;box-shadow:0 10px 30px rgba(0,0,0,.45);font:13.5px var(--body,system-ui);color:var(--ink,#E8EEF1)}
.watchpanel h3{margin:0 0 8px;font:700 16px var(--display,system-ui);letter-spacing:.03em;display:flex;align-items:center;gap:8px}
.watchpanel h3 .x{margin-left:auto}
.watchpanel .x{border:0;background:none;color:var(--muted,#95A6B0);cursor:pointer;font-size:14px}
.watchpanel .wi{border-top:1px solid var(--faint,#22313A);padding:8px 0;display:grid;gap:3px}
.watchpanel .wh{display:flex;gap:6px;align-items:baseline}
.watchpanel .wh b{flex:1}
.watchpanel .wm{color:var(--muted,#95A6B0);font-size:12px}
.watchpanel .wp{display:flex;flex-wrap:wrap;gap:4px 12px;align-items:baseline;font-size:13px}
.watchpanel .up{color:var(--good,#36D399)}.watchpanel .dn{color:var(--bad,#F06A6A)}
.watchpanel .hist{font-size:11.5px;color:var(--muted,#95A6B0)}
.watchpanel .note{font-size:11.5px;color:var(--muted,#95A6B0);margin:6px 0 0}
.watchpanel .acts{display:flex;gap:8px;margin-top:8px}.watchpanel .acts button{border:1px solid var(--faint,#22313A);background:none;color:var(--ink,#E8EEF1);border-radius:999px;padding:4px 10px;cursor:pointer;font-size:12.5px}
.watchfab[hidden],.watchpanel[hidden]{display:none!important}
.watchadd{border:0;background:none;color:var(--muted,#95A6B0);cursor:pointer;font-size:15px;line-height:1;padding:0 3px;vertical-align:-1px}
.watchadd.on{color:#F2C14E}
.oc{position:relative;margin-top:4px}.oc svg{width:100%;height:auto;display:block;font:10.5px var(--body,system-ui);fill:var(--muted,#95A6B0)}
.oc .g{stroke:var(--faint,#22313A);stroke-width:1}.oc .ln{fill:none;stroke-width:2;stroke-linejoin:round;opacity:.85}.oc .ln.me{stroke-width:3;opacity:1}
.oc .fair{fill:none;stroke:#95A6B0;stroke-width:1.5;stroke-dasharray:4 3}.oc .dot{stroke:var(--surface,#11181D);stroke-width:2}.oc .lab{fill:var(--ink,#E8EEF1);font-size:10.5px}
.oc .xh{stroke:var(--muted,#95A6B0);stroke-width:1}.oc .hit{fill:transparent;cursor:crosshair;touch-action:pan-y}
.oc .tip{position:absolute;top:4px;width:150px;background:var(--bg,#0A0F12);border:1px solid var(--faint,#22313A);border-radius:8px;padding:6px 8px;font-size:12px;pointer-events:none;color:var(--ink,#E8EEF1)}
.oc .tip i,.oc .lg i{display:inline-block;width:10px;height:3px;border-radius:2px;margin-right:5px;vertical-align:3px}.oc .tip strong{margin-right:4px}.oc .tip .muted{color:var(--muted,#95A6B0)}
.oc .lg{display:flex;flex-wrap:wrap;gap:3px 12px;font-size:12px;color:var(--ink,#E8EEF1);margin-top:4px}.oc .lg i.fl{background:none;border-top:2px dashed #95A6B0;height:0}
.oddsmodal{position:fixed;inset:auto 12px 58px auto;z-index:62;width:min(470px,calc(100vw - 24px));background:var(--surface,#11181D);border:1px solid var(--faint,#22313A);border-radius:12px;padding:12px 14px;box-shadow:0 10px 30px rgba(0,0,0,.45);font:13.5px var(--body,system-ui);color:var(--ink,#E8EEF1)}
.oddsmodal h3{margin:0 0 6px;font:700 15px var(--display,system-ui);display:flex;gap:8px;align-items:center}.oddsmodal h3 .x{margin-left:auto;border:0;background:none;color:var(--muted,#95A6B0);cursor:pointer}
.oddsmodal .note{font-size:11.5px;color:var(--muted,#95A6B0);margin:4px 0 0}.oddsmodal[hidden]{display:none!important}
.watchpanel .wc{border:0;background:none;color:var(--accent,#36D399);cursor:pointer;font-size:12.5px;padding:0}
.chartadd{border:0;background:none;cursor:pointer;font-size:13px;line-height:1;padding:0 3px;vertical-align:-1px;opacity:.85}`;
  const st = document.createElement('style'); st.textContent = css; document.head.appendChild(st);
  const fab = document.createElement('button'); fab.className = 'watchfab'; fab.type = 'button';
  const panel = document.createElement('div'); panel.className = 'watchpanel'; panel.hidden = true;
  const mount = () => { if (!fab.isConnected) document.body.append(fab, panel, modal); draw(); };
  if (document.body) mount(); else document.addEventListener('DOMContentLoaded', mount);
  fab.addEventListener('click', e => { e.stopPropagation(); panel.hidden = !panel.hidden; draw(); if (!panel.hidden) refresh(); });
  function place(){                                                // like slip.js: stay inside a phone's visible viewport
    const vv = window.visualViewport, w = vv ? vv.width : window.innerWidth, ox = vv ? vv.offsetLeft : 0;
    if (w < 640){ panel.style.left = (ox + 8) + 'px'; panel.style.width = (w - 16) + 'px'; fab.style.left = (ox + 12) + 'px'; }
    else { panel.style.left = panel.style.width = fab.style.left = ''; }
  }
  if (window.visualViewport){ visualViewport.addEventListener('resize', place); visualViewport.addEventListener('scroll', place); }
  window.addEventListener('resize', place);
  const tm = t => new Date(t).toLocaleTimeString([], {hour: 'numeric', minute: '2-digit'});
  function row(it){
    const c = it.cur || {}, o = c.o, d = o != null && it.o != null ? dec(o) - dec(it.o) : null;
    const moveO = d == null || Math.abs(d) < 1e-9 ? '' : d > 0 ? `<span class="up">▲ pays more</span>` : `<span class="dn">▼ pays less</span>`;
    const moveL = c.main != null && it.main0 != null && c.main !== it.main0 ? `<span class="${c.main > it.main0 ? 'up' : 'dn'}" title="the books moved their main line ${c.main > it.main0 ? 'up: the market now expects more, good for an over' : 'down: the market now expects less'}">main line ${it.main0} → ${c.main}</span>` : '';
    const edge = c.fair != null && o != null ? c.fair * dec(o) - 1 : null;
    const hist = (it.h || []).length > 1 ? `<div class="hist">${it.h.map(x => `${tm(x.t)} ${am(x.o)}${x.m != null && x.m !== it.line ? ` (main ${x.m})` : ''}`).join(' → ')}</div>` : '';
    return `<div class="wi"><div class="wh"><b>${esc(it.lab)}</b><button class="x" data-watchdel="${esc(it.id)}" aria-label="Remove">✕</button></div>
      <div class="wm">${esc(SP[it.sport] || '')}${it.glab ? ' · ' + esc(it.glab) : ''}${it.start ? ' · ' + esc(new Date(it.start).toLocaleString([], {weekday: 'short', hour: 'numeric', minute: '2-digit'})) : ''} · ${esc(it.book)} · added ${tm(it.t)}${it.done ? ' · <b>started</b>' : ''}</div>
      <div class="wp"><button class="wc" data-watchchart="${esc(it.id)}">${OPEN.has(it.id) ? 'Hide chart' : '📈 Chart'}</button><span>then <b>${am(it.o)}</b></span><span>now <b>${o == null ? (it.done ? am((it.h || []).slice(-1)[0]?.o) : 'not posted') : am(o)}</b></span>${moveO}${moveL}
        ${c.fair != null ? `<span>fair ${pc(c.fair)}${it.fair != null && Math.abs(c.fair - it.fair) >= .01 ? ` <span class="${c.fair > it.fair ? 'up' : 'dn'}">(was ${pc(it.fair)})</span>` : ''}</span>` : ''}
        ${edge != null ? (edge > .6 ? `<span class="wm" title="A claimed edge over 60% is usually a stale or off-market quote (the screener hides these)">edge ? · check price</span>` : `<span class="${edge >= .03 ? 'up' : edge < 0 ? 'dn' : ''}">edge ${edge >= 0 ? '+' : '−'}${Math.abs(Math.round(edge * 100))}%</span>`) : ''}</div>${hist}${OPEN.has(it.id) ? `<div class="wch" data-wch="${esc(it.id)}"></div>` : ''}</div>`;
  }
  const OPEN = new Set();
  function draw(){
    const live = items.filter(x => !x.done).length;
    fab.innerHTML = items.length ? `☆ Watchlist <b>${items.length}</b>` : '☆ Watchlist';
    place();
    if (panel.hidden) return;
    if (!items.length){
      panel.innerHTML = `<h3>Watchlist <button class="x" data-watchclose aria-label="Close">✕</button></h3><p class="note">Tap ☆ next to a price in the Screener, on the Markets page’s best lines or on a ladder rung to watch it. Each saved line shows its price then and now, whether the books moved the line, and the fair price and edge, refreshed every minute while a page is open. Saved in this browser.</p>`;
      return;
    }
    const ord = items.slice().sort((a, b) => (a.done - b.done) || (new Date(a.start || 0) - new Date(b.start || 0)));
    panel.innerHTML = `<h3>Watchlist · ${items.length} <button class="x" data-watchclose aria-label="Close">✕</button></h3>${ord.map(row).join('')}
      <p class="note">Prices refresh every minute while a page is open (the site updates them about every 15 minutes). Fair = the sharp market’s no-vig chance at that line; edge = fair × payout − 1.${live < items.length ? ' Started games keep their last pregame price.' : ''}</p>
      <div class="acts"><button data-watchclean>Remove started</button><button data-watchclear>Clear all</button></div>`;
    panel.querySelectorAll('[data-wch]').forEach(h => { const it = items.find(x => x.id === h.dataset.wch); if (it) openChart(it, h); });
  }
  function rerenderPage(){ try { window.render && window.render(); } catch (e) {} document.querySelectorAll('[data-watch]').forEach(b => { let it; try { it = JSON.parse(b.dataset.watch); } catch (e) { return; } const on = items.some(x => x.id === idOf(it)); b.classList.toggle('on', on); b.textContent = on ? '★' : '☆'; }); }
  document.addEventListener('click', e => {
    const a = e.target.closest('[data-watch]');
    if (a){ e.preventDefault(); e.stopPropagation(); let it; try { it = JSON.parse(a.dataset.watch); } catch (err) { return; }
      const id = idOf(it), i = items.findIndex(x => x.id === id);
      if (i >= 0) items.splice(i, 1);
      else { items.push({...it, id, t: Date.now(), main0: it.main ?? null, h: [{t: Date.now(), o: it.o, m: it.main ?? null}]}); }
      save(); draw(); rerenderPage(); return; }
    const wc = e.target.closest('[data-watchchart]');
    if (wc){ e.stopPropagation(); OPEN.has(wc.dataset.watchchart) ? OPEN.delete(wc.dataset.watchchart) : OPEN.add(wc.dataset.watchchart); draw(); return; }
    const oc = e.target.closest('[data-oddschart]');
    if (oc){ e.preventDefault(); e.stopPropagation(); let it; try { it = JSON.parse(oc.dataset.oddschart); } catch (err) { return; }
      modal.hidden = false; modal.innerHTML = `<h3>${esc(it.lab)} <span class="note" style="margin:0">${esc(SP[it.sport] || '')}${it.glab ? ' · ' + esc(it.glab) : ''}</span><button class="x" data-oddsclose aria-label="Close">✕</button></h3><div class="mh"></div>`;
      openChart(it, modal.querySelector('.mh')); return; }
    if (e.target.closest('[data-oddsclose]') || (!modal.hidden && !e.target.closest('.oddsmodal'))){ modal.hidden = true; }
    if (e.target.closest('[data-watchclose]')){ panel.hidden = true; draw(); return; }
    const d = e.target.closest('[data-watchdel]'); if (d){ items = items.filter(x => x.id !== d.dataset.watchdel); save(); draw(); rerenderPage(); return; }
    if (e.target.closest('[data-watchclean]')){ items = items.filter(x => !x.done); save(); draw(); rerenderPage(); return; }
    if (e.target.closest('[data-watchclear]')){ items = []; save(); draw(); rerenderPage(); return; }
    if (!panel.hidden && !e.target.closest('.watchpanel') && !e.target.closest('.watchfab')){ panel.hidden = true; draw(); }
  });
  window.WATCH = {
    has: it => items.some(x => x.id === idOf(it)),
    btn(it){ const on = items.some(x => x.id === idOf(it)); return `<button type="button" class="watchadd${on ? ' on' : ''}" data-watch="${esc(JSON.stringify(it))}" title="${on ? 'Remove from' : 'Add to'} watchlist">${on ? '★' : '☆'}</button>`; },
    chartBtn(it){ return `<button type="button" class="chartadd" data-oddschart="${esc(JSON.stringify(it))}" title="How the books' prices moved">📈</button>`; },
    both(it){ return this.btn(it) + this.chartBtn(it); },
  };
  draw();
  try { window.render && window.render(); } catch (e) {}           // pages that rendered before this loaded get their ☆ buttons
  if (items.length) refresh();
  setInterval(() => { if (items.some(x => !x.done)) refresh(); }, 60000);
})();
