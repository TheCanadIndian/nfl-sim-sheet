"""MLB home-run sheet page (mlb/projections/index.html + <date>.html), dark theme shared with the other sports."""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import report  # noqa: E402

OUT = os.path.join(HERE, "projections")

TEMPLATE = r"""<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>MLB home runs __DATE__</title>
__FONTS__
<style>__CSS__
.hrbar{position:relative;height:8px;border-radius:4px;background:var(--sunk);min-width:70px}
.hrbar i{position:absolute;left:0;top:0;bottom:0;border-radius:4px;background:var(--accent)}
.why{display:flex;flex-wrap:wrap;gap:4px}
.why span{font-size:11px;font-weight:600;padding:0 6px;border-radius:9px;line-height:17px;border:1px solid var(--faint);color:var(--muted);white-space:nowrap}
.why .g1,.why .g2,.why .g3{border-color:color-mix(in srgb,var(--g2) 55%,transparent)} .why .r1,.why .r2,.why .r3{border-color:color-mix(in srgb,var(--r2) 55%,transparent)}
.gcard{display:grid;gap:12px}
.gmeta{display:flex;flex-wrap:wrap;gap:6px 14px;color:var(--muted);font-size:13.5px}
.gmeta b{color:var(--ink);font-weight:600}
.two{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,460px),1fr));gap:16px}
.lu{font-size:11px;color:var(--muted);font-weight:600;letter-spacing:.04em;text-transform:uppercase}
.pick td{background:color-mix(in srgb,var(--accent) 7%,transparent)}
.star{color:var(--accent)}
</style>
<div class="wrap">
  <header class="hero" style="display:grid;gap:6px"><div class="eyebrow" id="sub"></div><h1>Home run sheet</h1>
    <p class="sub" style="color:var(--muted);max-width:70ch;margin:0">Each hitter's chance of at least one home run today: his power, the pitchers he'll face (starter, then bullpen), handedness, pitch mix and where they locate, recent form, park and weather, and how many trips to the plate his lineup spot gets. Chips show what pushes his number up (green) or down (red) vs an average hitter.</p></header>
  <nav class="tabs" role="tablist"><button data-view="board" aria-selected="true">Top of the slate</button><button data-view="games">By game</button><button data-view="how">How it works</button></nav>
  <main id="main"></main>
</div>
<script type="application/json" id="data">__DATA__</script>
<script>
const D = JSON.parse(document.getElementById('data').textContent);
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const pct = p => (p * 100).toFixed(1) + '%';
const odds = o => (o > 0 ? '+' : '') + o;
const LAB = {power: 'power', pitcher: 'pitcher', matchup: 'matchup', form: 'form', park: 'park/wx', opportunity: 'PAs'};
const sh = v => v >= .2 ? 'g3' : v >= .1 ? 'g2' : v >= .04 ? 'g1' : v <= -.2 ? 'r3' : v <= -.1 ? 'r2' : v <= -.04 ? 'r1' : '';
const why = w => `<div class="why">${Object.entries(w).filter(([k, v]) => Math.abs(v) >= .04).sort((a, b) => Math.abs(b[1]) - Math.abs(a[1])).slice(0, 4)
  .map(([k, v]) => `<span class="${sh(v)}" title="${esc(LAB[k])}: ${v > 0 ? '+' : ''}${Math.round(v * 100)}% to his HR chance vs an average hitter">${v > 0 ? '▲' : '▼'} ${LAB[k]} ${v > 0 ? '+' : ''}${Math.round(v * 100)}%</span>`).join('')}</div>`;
const G = Object.fromEntries(D.games.map(g => [g.id, g]));
const TOP = new Set(D.players.slice().sort((a, b) => b.p_hr - a.p_hr).slice(0, 5).map(p => p.batter + '|' + p.game_pk));
const maxp = Math.max(.25, ...D.players.map(p => p.p_hr));
const bar = p => `<div class="hrbar" role="img" aria-label="${pct(p)}"><i style="width:${p / maxp * 100}%"></i></div>`;
const row = (p, i, showGame) => `<tr class="${TOP.has(p.batter + '|' + p.game_pk) ? 'pick' : ''}">${i != null ? `<td class="muted">${i + 1}</td>` : `<td class="muted">${p.slot}</td>`}
  <td class="l name">${TOP.has(p.batter + '|' + p.game_pk) ? '<span class="star">★</span> ' : ''}${esc(p.name)} <small class="muted">${esc(p.team)} · bats ${esc(p.bats)}${showGame ? ` · vs ${esc(p.sp_name || 'TBD')}` : ''}</small>${why(p.why)}</td>
  <td class="l">${bar(p.p_hr)}</td><td class="big">${pct(p.p_hr)}</td><td>${odds(p.fair)}</td>
  <td class="muted" title="expected plate appearances (vs starter)">${p.exp_pa.toFixed(1)} <small>(${p.exp_pa_sp.toFixed(1)})</small></td>
  <td class="muted" title="HR chance per PA vs the starter / bullpen">${pct(p.p_pa_sp)} / ${pct(p.p_pa_bp)}</td>
  ${window.MKT && MKT.has() ? `<td class="l">${MKT.cell(p.game_pk, p.batter, p.name, 'hr')}</td>` : ''}</tr>`;
const head = first => `<thead><tr><th>${first}</th><th class="l">Batter</th><th class="l" style="min-width:80px">Chance</th><th>1+ HR</th><th>Fair</th><th title="expected PAs (vs the starter)">PA (SP)</th><th title="per-PA HR chance vs starter / bullpen">per PA SP / BP</th>${window.MKT && MKT.has() ? '<th class="l" title="Kalshi 1+ HR price, our edge, and dollars to buy YES / NO within 3¢">Kalshi</th>' : ''}</tr></thead>`;
function board(){
  const ps = D.players.slice().sort((a, b) => b.p_hr - a.p_hr).slice(0, 40);
  return `<section class="panel"><div class="phead"><h2>Most likely to homer</h2><span class="muted small">★ top 5 of the slate</span></div>
    <div class="tw"><table class="t">${head('#')}<tbody>${ps.map((p, i) => row(p, i, true)).join('')}</tbody></table></div></section>`;
}
function games(){
  return D.games.map(g => {
    const side = t => D.players.filter(p => p.game_pk === g.id && p.team === t).sort((a, b) => a.slot - b.slot);
    const one = (t, sp) => { const ps = side(t); if (!ps.length) return '';
      return `<div><div class="lu">${esc(t)} lineup · ${esc(ps[0].lineup)} · vs ${esc(ps[0].sp_name || 'TBD')} (${esc(ps[0].sp_hand)}HP)</div>
        <div class="tw"><table class="t">${head('#')}<tbody>${ps.map(p => row(p, null, false)).join('')}</tbody></table></div></div>`; };
    const any = D.players.find(p => p.game_pk === g.id) || {};
    const t = new Date(g.start_utc).toLocaleTimeString([], {hour: 'numeric', minute: '2-digit'});
    return `<section class="panel gcard"><div class="phead"><h2>${esc(g.away)} at ${esc(g.home)}</h2><span class="muted small">${esc(g.series || '')} · ${t}</span></div>
      <div class="gmeta"><span>${esc(g.venue)} · HR factor <b>${any.park != null ? (any.park >= 1 ? '+' : '') + Math.round((any.park - 1) * 100) + '%' : '–'}</b></span>
        <span>Temp <b>${any.temp != null ? any.temp + '°F' : 'n/a'}</b></span><span>Wind out <b>${any.wind_out != null ? any.wind_out + ' mph' : 'n/a'}</b></span>
        <span>SP: <b>${esc(g.sp_away || 'TBD')}</b> / <b>${esc(g.sp_home || 'TBD')}</b></span></div>
      <div class="two">${one(g.away)}${one(g.home)}</div></section>`;
  }).join('');
}
function how(){
  return `<section class="panel" style="display:grid;gap:10px;max-width:80ch"><h2>How the number is built</h2>
    <p>Every plate appearance gets a home-run chance from a model trained on every PA since 2024 (walk-forward: nothing after the game it predicts). Inputs that earned their place in testing:</p>
    <ul>${D.features.map(f => `<li><code>${esc(f)}</code></li>`).join('')}</ul>
    <p>Then his lineup spot sets how many times he bats (leadoff ~4.6, ninth ~3.7), and the starter's usual depth decides how many of those come against him before the bullpen. The chance of 1+ HR combines them.</p>
    <p class="muted small">Generated ${esc(D.generated)}. Lineups marked "projected" use each team's most recent order until the real one is posted.</p></section>`;
}
let view = 'board';
function render(){
  document.querySelectorAll('.tabs button').forEach(b => b.setAttribute('aria-selected', b.dataset.view === view));
  document.getElementById('main').innerHTML = view === 'games' ? games() : view === 'how' ? how() : board();
}
document.getElementById('sub').textContent = `${D.games.length} game${D.games.length === 1 ? '' : 's'} · ${D.date} · built ${D.generated.replace('T', ' ')}`;
document.addEventListener('click', e => { const b = e.target.closest('button[data-view]'); if (b){ view = b.dataset.view; render(); } });
render();
</script>"""


def write(payload):
    os.makedirs(OUT, exist_ok=True)
    html = TEMPLATE.replace("__FONTS__", report.FONTS).replace("__CSS__", report.BASE_CSS).replace("__DATE__", payload["date"]) \
        .replace("__DATA__", json.dumps(payload, separators=(",", ":")).replace("</", "<\\/"))
    for f in (f"{payload['date']}.html", "index.html"):
        open(os.path.join(OUT, f), "w", encoding="utf-8").write(html)
    print("Wrote", os.path.join(OUT, "index.html"))
