/* My parlay: a build-your-own slip shared by every sport page (one slip across sports, saved in this browser).
   Pages add legs with  SLIP.btn(leg)  (a small "+" button) or  SLIP.addMany(legs).
   A leg: {id, sport, g, glab, lab, p, side, stat, line, pk, draws, model}
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
    if (L.stat === 'win' || L.stat === 'total'){
      const m = decode(D.g && D.g[L.stat === 'win' ? 'margin' : 'total']); if (!m) return null;
      return Array.from(m, v => L.stat === 'win' ? (L.home ? v > 0 : v < 0) : (L.side === 'under' ? v < L.line : v > L.line));
    }
    x = decode(D.p && D.p[L.pk] && D.p[L.pk][L.stat]); if (!x) return null;
    return Array.from(x, v => L.side === 'under' ? v < L.line : v > L.line);
  }

  // ---- parlay chance: correlated within a game when its simulations are available, multiplied otherwise
  let result = null, busy = 0;
  async function compute(){
    const my = ++busy;
    const groups = {};
    legs.forEach(L => (groups[L.sport + '|' + L.g] = groups[L.sport + '|' + L.g] || []).push(L));
    let p = 1, indep = 1, linked = 0;
    for (const gl of Object.values(groups)){
      indep *= gl.reduce((t, L) => t * L.p, 1);
      const url = gl[0].draws;
      if (gl.length > 1 && url){
        const D = await loadDraws(url);
        if (D){
          const inSim = [], outSim = [];
          gl.forEach(L => { const h = L.model === 'blind' ? null : hitsFor(L, D); (h ? inSim : outSim).push(h ? [L, h] : L); });
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
    if (my !== busy) return;
    result = {p, indep, linked};
    draw();
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
@media (max-width:640px){.slippanel{bottom:64px;max-height:65vh}.slipfab{bottom:12px}}
.slippanel .sum{display:grid;grid-template-columns:repeat(3,1fr);gap:6px;text-align:center;border-top:1px solid var(--faint,#22313A);padding-top:8px}
.slippanel .sum b{display:block;font-size:18px}
.slippanel .sum span{font-size:11.5px;color:var(--muted,#95A6B0)}
.slippanel .acts{display:flex;flex-wrap:wrap;gap:6px}
.slippanel .acts a,.slippanel .acts button{font-size:12.5px;border:1px solid var(--faint,#22313A);border-radius:999px;padding:4px 11px;color:var(--ink,#EAF1F4);background:none;text-decoration:none;cursor:pointer}
.slippanel .note{font-size:11.5px;color:var(--muted,#95A6B0);margin:0}
.slipfab[hidden],.slippanel[hidden]{display:none!important}
.slipadd{border:1px solid var(--faint,#22313A);background:none;color:var(--accent,#36D399);border-radius:999px;padding:1px 8px;font-size:12px;cursor:pointer;margin-left:6px;white-space:nowrap}
.slipadd.on{background:var(--accent,#36D399);color:var(--bg,#0A0F12);border-color:var(--accent,#36D399)}`;
  const st = document.createElement('style'); st.textContent = css; document.head.appendChild(st);
  const fab = document.createElement('button'); fab.className = 'slipfab'; fab.type = 'button';
  const panel = document.createElement('div'); panel.className = 'slippanel'; panel.hidden = true;
  document.addEventListener('DOMContentLoaded', () => { document.body.append(fab, panel); draw(); });
  if (document.body) document.body.append(fab, panel);
  fab.addEventListener('click', e => { e.stopPropagation(); panel.hidden = !panel.hidden; draw(); });
  document.addEventListener('keydown', e => { if (e.key === 'Escape' && !panel.hidden){ panel.hidden = true; draw(); } });

  function gambly(){
    const text = 'Build a ' + legs.length + '-leg parlay: ' + legs.map(L => L.lab + (L.glab ? ` (${L.glab})` : '')).join(', ');
    return 'https://gambly.com/chat?' + new URLSearchParams({entry: 'ask-gambly', prompt: text, autoSubmit: '1'}).toString();
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
    fab.innerHTML = legs.length ? `My parlay <b>${legs.length}</b>${result ? ` · ${pct(result.p)}` : ''}` : 'My parlay <b>+</b>';
    place();
    if (panel.hidden) return;
    if (!legs.length){ panel.innerHTML = `<h3>My parlay <button class="x" data-slipclose aria-label="Close">✕</button></h3><p class="note">Tap a "+" next to any line on the NFL, NBA, NHL or MLB pages to add it (NHL: "+ ATG" by each player; NFL / NBA: open a player and use + Over / + Under; MLB: "+" by HR%). Legs from different sports can be combined.</p><div class="acts"><button data-slipclose>Close</button></div>`; return; }
    const r = result || {};
    panel.innerHTML = `<h3>My parlay · ${legs.length} leg${legs.length === 1 ? '' : 's'} <button class="x" data-slipclose aria-label="Close">✕</button></h3>
      <ol>${legs.map(L => `<li><span>${esc(L.lab)} <small>${esc(SPORT[L.sport] || '')}${L.glab ? ' · ' + esc(L.glab) : ''}</small></span><span>${pct(L.p)} <button class="x" data-slipdel="${esc(L.id)}" aria-label="Remove">✕</button></span></li>`).join('')}</ol>
      <div class="sum"><div><b>${pct(r.p)}</b><span>hits</span></div><div><b>${r.p ? american(r.p) : '–'}</b><span>fair odds</span></div><div><b>${pct(r.indep)}</b><span>if unrelated</span></div></div>
      <p class="note">${r.linked ? `${r.linked} legs judged together in the same simulated games (their link counts). ` : ''}Other legs multiply. Take a book's parlay only if it pays more than the fair odds.</p>
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
    if (e.target.closest('[data-slipclear]')){ legs = []; changed(); return; }
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
