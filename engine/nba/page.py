"""NBA slate page (same look and site navigation as NFL/NHL)."""

import json

import pandas as pd
import os
import sys

sys.path.insert(1, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import report  # noqa: E402

Q = ["mean", "p10", "p25", "median", "p75", "p90"]


def player_payload(tab, meta, dists):
    m = meta.set_index(["game_id", "team", "player_id"])
    out = []
    for (gid, team, pid), x in tab.groupby(["game_id", "team", "player_id"], sort=False):
        r = x.iloc[0]
        k = f"{gid}|{team}|{r['name']}"
        mm = m.loc[(gid, team, pid)] if (gid, team, pid) in m.index else None
        s = {mode: {row.stat: [round(float(getattr(row, q)), 2) for q in Q] for row in y.itertuples()}
             for mode, y in x.groupby("model")}
        out.append(dict(k=k, g=int(gid), t=team, o=r["opp"], n=r["name"], id=int(pid), pos=r["pos"],
                        pg=None if mm is None else mm.pg, st=None if mm is None else mm.status,
                        note=None if mm is None else (mm.note or ""), start=None if mm is None else bool(mm.starter == 1),
                        trend=None if mm is None or pd.isna(mm.min_trend) else round(float(mm.min_trend), 1),
                        gp=None if mm is None else int(mm.games_prior or 0), s=s,
                        ar=None if mm is None or "arch_name" not in mm.index else mm.arch_name,
                        va=None if mm is None or "dva_pts" not in mm.index else {c: round(float(mm["dva_" + c]), 3) for c in ("pts", "fg3m", "stl") if "dva_" + c in mm.index and pd.notna(mm["dva_" + c])},
                        x=None if mm is None or "x_gp" not in mm.index or pd.isna(mm.x_gp) else {
                            k_: (None if pd.isna(mm[c]) else round(float(mm[c]), 3 if c in ("x_rr", "x_ar") else 1))
                            for k_, c in (("gp", "x_gp"), ("season", "x_season"), ("fg3a", "x_fg3a"), ("fg3a10", "x_fg3a10"), ("rch", "x_rch"), ("rr", "x_rr"),
                                          ("ach", "x_ach"), ("ar", "x_ar"), ("reb", "x_reb"), ("ast", "x_ast"), ("on", "x_on"))},
                        d={mode: dists[mode].get(k, {}) for mode in ("vegas", "blind")}))
    return out


def render(payload):
    data = json.dumps(payload, separators=(",", ":")).replace("</", "<\\/")
    return TEMPLATE.replace("__FONTS__", report.FONTS).replace("__CSS__", report.BASE_CSS).replace("__DATA__", data)


TEMPLATE = r"""<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>NBA Sim Sheet</title>
__FONTS__<style>
__CSS__
.gmsw{display:inline-flex;background:var(--sunk);border-radius:8px;padding:3px;margin-right:auto;margin-left:6px}
.gmsw button{border:0;background:none;padding:4px 11px;border-radius:6px;cursor:pointer;font-weight:600;font-size:12.5px;color:var(--muted)}
.gmsw button[aria-pressed="true"]{background:var(--surface);color:var(--ink);box-shadow:0 1px 2px rgba(0,0,0,.12)}
.cell b{font-weight:600}
.cell{border-radius:6px}.t td.cell,.t td.hm{background-clip:padding-box;border:2px solid transparent}
.exp3{font-family:var(--body);font-size:10.5px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;color:#F2C14E;border:1px solid currentColor;border-radius:4px;padding:1px 5px;margin-left:6px;vertical-align:3px}
.lad{border-top:1px solid var(--faint);padding:10px 0}.ladh{display:flex;flex-wrap:wrap;align-items:baseline;gap:4px 8px}
.ladc{display:inline-block;border:1px solid var(--faint);border-radius:999px;padding:0 8px;font-size:11.5px;color:var(--muted);white-space:nowrap}.ladc.soft{color:var(--ink);border-color:color-mix(in srgb,var(--good) 50%,transparent)}
.ladr{display:flex;flex-wrap:wrap;gap:6px;margin-top:6px}.lru{border:1px solid var(--faint);border-radius:8px;padding:3px 9px;font-size:13px;background:var(--sunk);white-space:nowrap}.lru i{font-style:normal;color:var(--muted);margin-left:4px}
.lru .ledge{color:var(--good);font-weight:700;margin-left:4px}
.ladsw{display:flex;gap:6px;margin:4px 0 8px}.ladsw button{border:1px solid var(--faint);background:none;color:var(--ink);border-radius:999px;padding:4px 12px;font:600 13px var(--body);cursor:pointer}.ladsw button[aria-pressed="true"]{background:var(--ink);color:var(--bg);border-color:var(--ink)}
.ladgm{border-top:1px solid var(--faint);padding:10px 0 2px}.ladgh{font-size:14.5px;margin-bottom:2px}.ladgm .lad:first-child{border-top:0}
.ladg{display:grid;column-gap:32px}@media(min-width:900px){.ladg{grid-template-columns:1fr 1fr}}.ladw{border-top:1px solid var(--faint);padding:10px 0 2px}.ladw summary{cursor:pointer;color:var(--muted);font-size:13px}
.mgkey{display:inline-flex;align-items:center;gap:6px;font-size:11.5px;color:var(--muted);white-space:nowrap}
.mgkey i{display:inline-block;width:84px;height:8px;border-radius:4px;background:linear-gradient(90deg,color-mix(in srgb,var(--bad) 55%,transparent),transparent 50%,color-mix(in srgb,var(--good) 55%,transparent))}
.cell small{display:block;color:var(--muted);font-size:11.5px}
.tag{font-size:10.5px;font-weight:700;border-radius:4px;padding:0 4px;margin-left:5px;border:1px solid currentColor;white-space:nowrap;cursor:help}
.tag.arch{color:var(--muted);font-weight:600;border-color:var(--faint)}
.dcp li.lwhy{gap:4px;flex-wrap:wrap;margin:-2px 0 2px 0}.dcp li.lwhy span{margin-left:0;font-size:11px}
.mxw{display:flex;flex-wrap:wrap;gap:3px 6px;margin:2px 0 0 18px}.mxl{font-size:11px;font-weight:700;white-space:nowrap;cursor:help}.mxl i{font-style:normal;font-weight:500;color:var(--muted)}
.mxl.up{color:var(--good)}.mxl.dn{color:var(--bad)}
.usw{display:flex;flex-wrap:wrap;gap:2px 10px;margin:2px 0 0 18px}.usl{font-size:11px;color:var(--muted);white-space:nowrap;cursor:help}.usl b{color:var(--ink);font-weight:700}.usl i{font-style:normal}
.mxstrip{display:flex;flex-wrap:wrap;align-items:center;gap:6px 8px;margin:0 0 10px;padding:8px 10px;border:1px solid var(--faint);border-radius:8px;background:var(--sunk)}
.mxstrip .eyebrow{margin-right:2px}.mxstrip .eyebrow:not(:first-child){margin-left:6px}
.mxs{border:1px solid var(--faint);border-radius:999px;padding:2px 9px;font-size:12.5px;cursor:help}.mxs i{font-style:normal;color:var(--muted);font-size:11.5px;margin-left:3px}
.mxs.up{border-color:color-mix(in srgb,var(--good) 50%,transparent)}.mxs.up b{color:var(--good)}.mxs.dn{border-color:color-mix(in srgb,var(--bad) 45%,transparent)}.mxs.dn b{color:var(--bad)}
.dva th{padding:4px 2px}.dvs{border:0;background:none;color:inherit;font:inherit;font-size:12px;font-weight:600;cursor:pointer;padding:2px 4px;border-radius:4px;white-space:normal;line-height:1.15}.dvs.on{background:var(--ink);color:var(--bg)}
.tag.q{color:#F2C14E} .tag.out{color:var(--r2)}
.lean{font-size:11px;font-weight:700;margin-left:6px;white-space:nowrap;cursor:help}
.g1{color:var(--g1)!important} .g2{color:var(--g2)!important} .g3{color:var(--g3)!important}
.r1{color:var(--r1)!important} .r2{color:var(--r2)!important} .r3{color:var(--r3)!important}
.hm.g1{background:color-mix(in srgb,var(--g1) 12%,transparent)} .hm.g2{background:color-mix(in srgb,var(--g2) 22%,transparent)} .hm.g3{background:color-mix(in srgb,var(--g3) 34%,transparent)}
.hm.r1{background:color-mix(in srgb,var(--r1) 12%,transparent)} .hm.r2{background:color-mix(in srgb,var(--r2) 22%,transparent)} .hm.r3{background:color-mix(in srgb,var(--r3) 34%,transparent)}
.plink .caret{display:inline-block;width:12px;color:var(--muted);font-size:11px}
.plink.open{color:var(--sel)}
tr.opened td{background:color-mix(in srgb,var(--sel) 8%,transparent)}
tr.xrow td{background:color-mix(in srgb,var(--sel) 5%,transparent);padding:12px 14px 16px;white-space:normal}
.slider{display:grid;gap:12px;max-width:860px}
.sl-top{display:flex;flex-wrap:wrap;gap:8px 14px;align-items:center}
.sl-row{display:flex;align-items:center;gap:10px}
.sl-row label{font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);font-weight:600}
.sl-row input[type=range]{flex:1;accent-color:var(--sel);height:28px;min-width:120px}
.sl-step{border:1px solid var(--faint);background:var(--surface);color:var(--ink);border-radius:6px;width:30px;height:30px;cursor:pointer;font-size:16px}
.sl-val{font-family:var(--display);font-size:26px;min-width:64px;text-align:right}
.sl-out{display:grid;grid-template-columns:auto minmax(80px,1fr) auto;gap:14px;align-items:center}
.sl-side{display:grid}
.sl-side span{font-size:12px;color:var(--muted);text-transform:uppercase;letter-spacing:.07em;font-weight:600}
.sl-side b{font-family:var(--display);font-size:34px;line-height:1;color:var(--ink)}
.sl-side em{font-style:normal;color:var(--muted);font-size:14px}
.sl-side.under{text-align:right}
.sl-meter{height:12px;border-radius:6px;overflow:hidden;display:flex;gap:2px;background:var(--sunk)}
.sl-meter i{display:block;height:100%}
.sl-meter .f-g1{background:var(--g1)} .sl-meter .f-g2{background:var(--g2)} .sl-meter .f-g3{background:var(--g3)}
.sl-meter .f-r1{background:var(--r1)} .sl-meter .f-r2{background:var(--r2)} .sl-meter .f-r3{background:var(--r3)} .sl-meter .f-n{background:var(--whisker)}
.noline{font-size:12.5px;color:#F2C14E}
</style>
<div class="wrap">
  <header>
    <div><div class="eyebrow" id="sub"></div><h1>NBA Sim Sheet</h1></div>
    <div class="tabs" role="tablist" aria-label="View">
      <button role="tab" data-view="games">Games</button>
      <button role="tab" data-view="board">Player board</button>
      <button role="tab" data-view="dvp">Defense vs position</button>
      <button role="tab" data-view="parlays">Parlays</button>
    </div>
  </header>
  <main id="main"></main>
  <footer>Each game is simulated thousands of times. Minutes come from recent games (with a trend toward the latest), the current roster and the injury report; players ruled out are removed and their minutes go to teammates by role. Per-minute production is recency-weighted and pulled toward position averages for small samples; the opponent's defense vs the player's position and the game's pace adjust it. <b>With Vegas</b> scales each team's players to the points implied by the spread and total; <b>Market-blind</b> uses each team's own scoring and defense instead. Medians are the coin-flip number; ranges show 8 in 10 games. Tap a name to slide any line. Q / DTD = questionable / day-to-day (projected to play).</footer>
</div>
<div id="tip" hidden></div>
<script type="application/json" id="data">__DATA__</script>
<script>
__JS__</script>
"""
# The page script lives in nba/page.js (owner 2026-10-23: scripts out of Python strings); pasted in at build so
# each dated page stays self-contained.
TEMPLATE = TEMPLATE.replace("__JS__", open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "page.js"), encoding="utf-8").read())

