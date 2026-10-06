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
.mkx-c{display:grid;gap:1px;line-height:1.2;white-space:nowrap}
.mkx-c small{color:var(--muted);font-size:11px}
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

  function row(r, model, focus, slider){
    poly(r);
    const [yl, nl] = sides(r), p = ours(r, model), e = edges(r, p);
    const best = (e.y ?? -9) >= (e.n ?? -9) ? {v: e.y, lab: yl} : {v: e.n, lab: nl};
    const weak = D && D.weak && D.weak.includes(r.k);
    const by = r.by ?? null, bn = r.bn ?? null, tot = (by || 0) + (bn || 0);
    const setBtn = slider && r.l != null && !['win', 'spread', 'anytime_td', 'first_td', 'first_goal'].includes(r.k) ? `<button class="mkx-set" data-mline="${r.l}" title="Move the slider to this line">use line</button>` : '';
    return `<div class="mkx-r${focus ? ' on' : ''}">
      <div class="mkx-l"><span class="mkx-src" title="${r.s === 'kalshi' ? 'Kalshi' : 'Polymarket (live)'}">${r.s === 'kalshi' ? 'K' : 'P'}</span><b>${esc(yl)}</b> <small>${esc(KLAB[r.k] || r.k)}</small>${isThin(r) ? `<span class="mkx-thin" title="Bid/ask gap ${Math.round(spr(r) * 100)}¢: a less-traded market">thin ${Math.round(spr(r) * 100)}¢</span>` : ''}${(r.pat || []).map(id => pchip(id)).join('')}${setBtn}</div>
      <div class="mkx-p">Price <b>${cents(r.ya)}</b> · ours <b>${pc(p)}</b>${best.v != null && best.v >= .02 ? ` · <span class="e ${weak ? '' : shade(best.v)}">+${(best.v * 100).toFixed(1)}% ${esc(best.lab)}</span>${weak ? ' <span class="mkx-warn" title="On settled games of this type the market has been more accurate than our model">market ahead</span>' : ''}` : ' · <small>no edge</small>'}</div>
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
    return `<span class="mkx-pat ${P.sign > 0 ? 'up' : 'down'}" title="${esc(P.label)}: ${esc(P.desc)} ${esc(precord(P))}${extra ? ' · ' + esc(extra) : ''}">◆ ${esc(P.short)}${P.sign > 0 ? '' : ' ▼'}</span>`;
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
    has: () => !!(D && D.rows.length),
    loaded: () => !!D,
    rows: () => (D ? D.rows : []),
    fetched: () => (D ? D.fetched : null),
    weak: k => !!(D && D.weak && D.weak.includes(k)),
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
      const weak = D.weak && D.weak.includes(r.k);
      return `<span class="mkx-c" title="${r.s === 'kalshi' ? 'Kalshi' : 'Polymarket'}: price ${cents(r.ya)} · money to buy YES ${usd(r.by)} / NO ${usd(r.bn)} within 3¢${weak ? ' · market has been more accurate on this type so far' : ''}"><span><b>${cents(r.ya)}</b>${v >= .02 ? ` <span class="${weak ? 'muted' : shade(v)}" style="font-weight:700">+${(v * 100).toFixed(0)}% ${side}</span>` : ''}${isThin(r) ? '<span class="mkx-thin">thin</span>' : ''}</span><small>${usd(r.by)} yes · ${usd(r.bn)} no</small></span>`;
    },
  };

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
  if (sport){ load(); timer = setInterval(load, 120000); }
})();
