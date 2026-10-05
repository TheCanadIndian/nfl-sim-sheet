#!/usr/bin/env python3
"""
Assemble the whole website in site/ from the generated pages, with one home page, a guide,
and the same two-level navigation on every page (sport tabs, then that sport's pages).

    python site.py            # build site/ (update.ps1 then commits and pushes it)

    /                    home: today's highlights per sport + start-here steps
    /guide.html          how to read everything (general + per-sport terms)
    /nfl/                this week's NFL projections      (projections/index.html)
    /nfl/results.html    graded past weeks                (projections/results.html)
    /nfl/weeks/          frozen weekly pages              (projections/weeks/*.html)
    /nfl/blind/          market-blind build               (projections/blind/*.html)
    /nhl/                goal sheet + results             (hockey/projections/*.html)
    /nba/, /mlb/         preview pages until those models go live

Adding a sport: add an entry to SPORTS (status "live" once it has pages), copy its pages in
main(), and give it a home-card function in HOME_CARDS.
"""

import datetime as dt
import glob
import html
import json
import os
import re
import shutil

import report

HERE = os.path.dirname(os.path.abspath(__file__))
SITE = os.path.abspath(os.environ.get("SIM_SITE") or os.path.join(HERE, "site"))   # repo root in the cloud
DATA_RE = re.compile(r'<script type="application/json" id="data">(.*?)</script>', re.S)

# One entry per sport. pages: (key, label, path from site root, one-line description)
SPORTS = [
    dict(key="nfl", name="NFL", status="live",
         blurb="Every player's box score simulated 10,000 times: yards, catches, touchdowns, first TD scorer.",
         pages=[("week", "This week", "nfl/index.html", "Projections for every game this week"),
                ("results", "Results", "nfl/results.html", "Past weeks graded against the real box scores"),
                ("markets", "Markets", "markets.html#nfl", "Kalshi prices and liquidity next to our chances, longshots first"),
                ("updates", "Model updates", "updates.html#nfl", "What the model learned and changed, week by week")]),
    dict(key="nhl", name="NHL", status="live",
         blurb="Anytime and first goal scorers, moneylines, puck lines and totals for every game.",
         pages=[("sheet", "Tonight", "nhl/index.html", "Goal chances, lineups, goalies and moneylines for the next slate"),
                ("results", "Results", "nhl/results.html", "Every night graded: scorers, first goals, moneylines"),
                ("markets", "Markets", "markets.html#nhl", "Kalshi prices and liquidity next to our chances, longshots first"),
                ("updates", "Model updates", "updates.html#nhl", "What the model learned and changed, week by week")]),
    dict(key="nba", name="NBA", status="live",
         blurb="Minutes, points, rebounds, assists and threes for every player, with and without Vegas lines.",
         pages=[("slate", "Next slate", "nba/index.html", "Every player's projection for the next slate, both models"),
                ("results", "Results", "nba/results.html", "Every night graded against the box scores, both models"),
                ("markets", "Markets", "markets.html#nba", "Kalshi prices and liquidity next to our chances, longshots first"),
                ("updates", "Model updates", "updates.html#nba", "What the model learned and changed")]),
    dict(key="mlb", name="MLB", status="soon", eta="Launching for spring training 2027",
         blurb="Hits, home runs, strikeouts and pitcher lines, plus moneylines and run totals.",
         pages=[("preview", "Preview", "mlb/index.html", "What's coming")]),
]
SPORT = {s["key"]: s for s in SPORTS}

BAR_CSS = """
.topbar{position:sticky;top:env(safe-area-inset-top,0px);z-index:20;background:var(--bg);border-bottom:1px solid var(--faint);margin-inline:-20px;padding-inline:20px;margin-bottom:6px}
.topbar .row{max-width:1560px;margin:0 auto;display:flex;align-items:center;gap:4px 6px;overflow-x:auto;white-space:nowrap;scrollbar-width:none}
.topbar .row::-webkit-scrollbar{display:none}
.topbar .r1{padding-block:9px 7px}
.topbar .r2{padding-block:0 8px}
.topbar .brand{font-family:var(--display);font-weight:700;font-size:20px;letter-spacing:.03em;text-transform:uppercase;color:var(--ink);text-decoration:none;margin-right:14px}
.topbar .sp{font-family:var(--display);font-weight:600;font-size:17px;letter-spacing:.05em;color:var(--muted);text-decoration:none;padding:4px 11px;border-radius:7px}
.topbar .sp:hover{color:var(--ink);background:var(--sunk)}
.topbar .sp[aria-current="true"]{color:var(--bg);background:var(--ink)}
.topbar .soon{font-family:var(--body);font-size:10px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;margin-left:5px;opacity:.75}
.topbar .sp[aria-current="true"] .soon{opacity:.85}
.topbar .grow{flex:1}
.topbar .help{color:var(--muted);text-decoration:none;font-size:14px;font-weight:500;padding:5px 9px;border-radius:6px}
.topbar .help:hover,.topbar .help[aria-current="page"]{color:var(--ink);background:var(--sunk)}
.topbar a.lnk{color:var(--muted);text-decoration:none;font-weight:500;font-size:14px;padding:5px 10px;border-radius:6px}
.topbar a.lnk:hover{color:var(--ink);background:var(--sunk)}
.topbar a.lnk[aria-current="page"]{color:var(--ink);background:var(--sunk);box-shadow:inset 0 -2px 0 var(--accent)}
.newbie{max-width:1560px;margin:0 auto 4px;display:flex;align-items:center;gap:10px;flex-wrap:wrap;background:var(--sunk);border-radius:8px;padding:9px 14px;font-size:14px}
.newbie a{color:var(--accent);font-weight:600;text-decoration:none}
.newbie button{margin-left:auto;border:0;background:none;color:var(--muted);cursor:pointer;font-size:13px;padding:2px 6px}
#sitenav{display:none}
/* phones: keep every page inside the screen; wide tables scroll inside their own box */
.wrap,main,.panel,.gcard,.sides>div,.detail,.card{min-width:0}
.wrap,.panel,.detail,.gcard{grid-template-columns:minmax(0,1fr)}
@media (max-width:760px){
  header{align-items:flex-start}
  header .tabs{max-width:100%;overflow-x:auto;flex-wrap:nowrap;scrollbar-width:none}
  header .tabs button{white-space:nowrap;flex:0 0 auto}
  .games{grid-template-columns:minmax(0,1fr)!important}
  .rail{position:static!important;display:flex!important;overflow-x:auto;gap:6px;padding-bottom:4px}
  .rail .game-btn{flex:0 0 auto;min-width:168px;border-color:var(--faint)}
  .two,.fo-res{grid-template-columns:minmax(0,1fr)!important}
  .score .pts{font-size:48px}
  .panel{padding:14px}
  .picks-key{font-size:12px;gap:8px}
  .mlc{min-width:92px;padding:6px 9px}
  .mlc b{font-size:17px}
  h1{font-size:32px}
}
"""

NEWBIE_JS = """<script>
(function(){ var b = document.getElementById('newbie'); if (!b) return;
  try { if (localStorage.getItem('simsheet-newbie') === 'done') { b.remove(); return; } } catch (e) {}
  b.hidden = false;
  b.querySelector('button').addEventListener('click', function(){ b.remove(); try { localStorage.setItem('simsheet-newbie', 'done'); } catch (e) {} });
})();
</script>"""


def bar(depth, active):
    """active = "sport:page", "home" or "guide"."""
    pre = "../" * depth
    sport, _, page = active.partition(":")
    tabs = []
    for s in SPORTS:
        cur = ' aria-current="true"' if s["key"] == sport else ""
        soon = '<span class="soon">soon</span>' if s["status"] == "soon" else ""
        tabs.append(f'<a class="sp" href="{pre}{s["pages"][0][2]}"{cur}>{s["name"]}{soon}</a>')
    help_cur = ' aria-current="page"' if active == "guide" else ""
    r1 = (f'<div class="row r1"><a class="brand" href="{pre}index.html">Sim Sheet</a>{"".join(tabs)}'
          f'<span class="grow"></span><a class="help" href="{pre}guide.html"{help_cur}>How to read</a></div>')
    r2 = ""
    if sport in SPORT and SPORT[sport]["status"] == "live":
        links = "".join(f'<a class="lnk" href="{pre}{path}" title="{html.escape(desc)}"'
                        f'{" aria-current=\"page\"" if key == page else ""}>{html.escape(label)}</a>'
                        for key, label, path, desc in SPORT[sport]["pages"])
        r2 = f'<div class="row r2">{links}</div>'
    newbie = (f'<div class="newbie" id="newbie" hidden><span>New here? Each page shows simulated outcomes: '
              f'the most likely number, a realistic range, and the chance something happens.</span>'
              f'<a href="{pre}guide.html">Read the 2-minute guide →</a><button type="button" aria-label="Dismiss">Got it ✕</button></div>'
              if active != "guide" else "")
    return f'<nav class="topbar" aria-label="Site">{r1}{r2}</nav>{newbie}'


MODEL_CSS = """
.mtoggle{display:flex;flex-wrap:wrap;align-items:center;gap:8px 12px;font-size:13.5px;color:var(--muted)}
.mtoggle .seg{display:inline-flex;gap:0;background:var(--sunk);border-radius:8px;padding:3px}
.mtoggle .seg a{padding:5px 13px;border-radius:6px;text-decoration:none;color:var(--muted);font-weight:600;font-size:13.5px}
.mtoggle .seg a[aria-current="true"]{background:var(--surface);color:var(--ink);box-shadow:0 1px 2px rgba(0,0,0,.12)}
.mtoggle .why{max-width:70ch}
"""
MODEL_TXT = {"vegas": "Anchored to the Vegas spread and total.",
             "blind": "Never sees betting lines: an independent second opinion. Switch to With Vegas and open Model agreement to compare the two."}


def model_toggle(model, here, other):
    """Switch between the Vegas-anchored and market-blind version of the same week.
    `other` is the counterpart page's relative path (None if it doesn't exist); the current tab
    (#board, #fair...) is carried over."""
    def link(key, label, href):
        cur = ' aria-current="true"' if key == model else ""
        h = here if key == model else (other or "")
        if key != model and not other:
            return f'<a aria-disabled="true" title="Not available for this week" style="opacity:.45">{label}</a>'
        return f'<a href="{h}"{cur} data-keephash>{label}</a>'
    return (f'<div class="mtoggle"><span>Model</span><span class="seg" role="group" aria-label="Model">'
            f'{link("vegas", "With Vegas", "")}{link("blind", "Market-blind", "")}</span>'
            f'<span class="why">{MODEL_TXT[model]}</span></div>'
            "<script>document.addEventListener('click', function(e){ var a = e.target.closest('a[data-keephash]');"
            " if (a && location.hash && !a.hasAttribute('aria-current')) { e.preventDefault(); location.href = a.getAttribute('href') + location.hash; } });</script>")


def inject(src, dst, depth, active, model=None, other=None):
    s = open(src, encoding="utf-8").read()
    s = s.replace("</style>", BAR_CSS + MODEL_CSS + "</style>", 1)
    tog = model_toggle(model, os.path.basename(dst), other) if model else ""
    s = s.replace('<div class="wrap">', bar(depth, active) + '\n<div class="wrap">' + tog, 1)
    s = s + NEWBIE_JS
    sport = os.path.basename(os.path.dirname(dst))
    if os.path.basename(dst) == "index.html" and os.path.dirname(os.path.dirname(dst)) == SITE and sport in ("nfl", "nhl", "nba"):
        s += f'<script src="../mkt.js" data-sport="{sport}"></script>'     # live market prices on the current slate
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    with open(dst, "w", encoding="utf-8") as f:
        f.write(s)


# When a run only refreshed one sport (e.g. an NHL check in the cloud), the other sport's
# source page isn't on disk; read the already-published copy instead.
PUBLISHED = {os.path.join("projections", "index.html"): os.path.join("nfl", "index.html"),
             os.path.join("projections", "results.html"): os.path.join("nfl", "results.html"),
             os.path.join("hockey", "projections", "index.html"): os.path.join("nhl", "index.html"),
             os.path.join("nba", "projections", "index.html"): os.path.join("nba", "index.html")}


def page_data(path):
    if not os.path.exists(path):
        rel = os.path.relpath(path, HERE)
        path = os.path.join(SITE, PUBLISHED[rel]) if rel in PUBLISHED else path
    if not os.path.exists(path):
        return None
    m = DATA_RE.search(open(path, encoding="utf-8").read())
    return json.loads(m.group(1).replace("<\\/", "</")) if m else None


def american(p):
    p = min(max(p, 1e-4), 1 - 1e-4)
    o = -100 * p / (1 - p) if p >= .5 else 100 * (1 - p) / p
    return f"{'+' if o > 0 else ''}{round(o)}"


PAGE_CSS = report.BASE_CSS + BAR_CSS + """
.hero{display:grid;gap:8px;padding-block:14px 4px}
.hero h1{font-size:44px}
.sub{max-width:66ch;color:var(--muted);font-size:16px;line-height:1.55;margin:0}
.steps{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,230px),1fr));gap:12px;margin:6px 0 0;padding:0;list-style:none;counter-reset:s}
.steps li{background:var(--surface);border:1px solid var(--faint);border-radius:10px;padding:14px 16px;display:grid;gap:4px;counter-increment:s}
.steps li::before{content:counter(s);font-family:var(--display);font-weight:700;font-size:26px;line-height:1;color:var(--accent)}
.steps b{font-weight:600}
.steps span{color:var(--muted);font-size:14px;line-height:1.5}
.steps a{color:var(--accent);font-weight:600;text-decoration:none}
.sechead{display:flex;align-items:baseline;justify-content:space-between;gap:12px;margin-top:6px}
.sechead h2{font-size:24px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,340px),1fr));gap:18px;align-items:start}
.card{background:var(--surface);border:1px solid var(--faint);border-radius:10px;padding:20px;display:grid;gap:12px}
.card.soon{background:none;border-style:dashed}
.ch{display:flex;justify-content:space-between;align-items:baseline;gap:10px}
.ch a{font-weight:600;color:var(--accent);text-decoration:none}
.sport{font-family:var(--display);font-weight:700;font-size:30px;letter-spacing:.04em}
.badge{font-size:11px;font-weight:600;letter-spacing:.07em;text-transform:uppercase;border-radius:999px;padding:3px 9px;background:var(--sunk);color:var(--muted)}
.badge.live{background:color-mix(in srgb,var(--good) 18%,transparent);color:var(--good)}
.lead{margin:0;font-size:15px;color:var(--muted)}
.card h3{margin:4px 0 0;font-size:12px;letter-spacing:.09em;text-transform:uppercase;color:var(--muted);font-weight:600}
.list{list-style:none;margin:0;padding:0;display:grid}
.list li{display:grid;grid-template-columns:1fr auto 64px;gap:12px;align-items:baseline;padding:8px 2px;border-bottom:1px solid var(--faint)}
.list li:last-child{border-bottom:0}
.list small{color:var(--muted);font-size:12.5px;margin-left:4px}
.list b{font-family:var(--display);font-size:22px;font-weight:700}
.list em{font-style:normal;color:var(--muted);text-align:right}
.pages{display:grid;gap:6px;margin:0;padding:0;list-style:none}
.pages a{display:grid;gap:1px;padding:9px 12px;border-radius:8px;border:1px solid var(--faint);text-decoration:none;color:var(--ink)}
.pages a:hover{border-color:var(--accent)}
.pages b{font-weight:600}
.pages span{font-size:13px;color:var(--muted)}
.acc{margin:0;padding:12px 14px;border-radius:8px;background:var(--sunk);font-size:14px;line-height:1.5}
.acc a{color:var(--accent);font-weight:600;text-decoration:none}
.guide{display:grid;gap:18px}
.guide section{background:var(--surface);border:1px solid var(--faint);border-radius:10px;padding:20px;display:grid;gap:12px}
.guide h2{font-size:24px}
.guide dl{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,290px),1fr));gap:14px 26px;margin:0}
.guide dt{font-weight:600;margin-bottom:2px}
.guide dd{margin:0;color:var(--muted);line-height:1.55;font-size:14.5px}
.guide a{color:var(--accent)}
.toc{display:flex;flex-wrap:wrap;gap:8px}
.toc a{padding:6px 12px;border:1px solid var(--faint);border-radius:999px;text-decoration:none;color:var(--ink);font-size:14px}
.toc a:hover{border-color:var(--accent)}
.plan{margin:0;padding-left:20px;color:var(--muted);line-height:1.7}
.mk-bar{display:flex;flex-wrap:wrap;gap:10px;align-items:center}
.mk-thin{display:inline-block;margin-left:4px;padding:0 6px;border-radius:9px;border:1px solid var(--sel);color:var(--sel);font-size:11px;font-weight:600;line-height:16px}
.mk-score{padding-top:14px;padding-bottom:14px}
.mk-score summary{cursor:pointer;display:flex;flex-wrap:wrap;gap:4px 12px;align-items:baseline;list-style:none}
.mk-score summary::before{content:"▸";color:var(--muted)} .mk-score[open] summary::before{content:"▾"}
.mk-score summary b{font-family:var(--display);font-size:19px;font-weight:600}
.mk-score[open] summary{margin-bottom:10px}
.mk-edge.weak b,.mk-edge.weak small{color:var(--muted)}
.mk-warn{color:var(--r1)!important;font-weight:600;letter-spacing:0!important;font-size:10.5px!important}
.mk-sort{font-size:13px;color:var(--muted);display:flex;gap:6px;align-items:center}
.mk-sort select{background:var(--surface);color:var(--ink);border:1px solid var(--faint);border-radius:6px;padding:5px 8px}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,180px),1fr));gap:10px}
.tile{background:var(--surface);border:1px solid var(--faint);border-radius:10px;padding:12px 14px;display:grid;gap:2px}
.tile .v{font-family:var(--display);font-weight:700;font-size:30px;line-height:1}
.tile .s{font-size:12.5px;color:var(--muted)}
.mk-head,.mk-row{display:grid;grid-template-columns:minmax(0,2.2fr) minmax(150px,1.4fr) 92px 120px;gap:14px;align-items:center}
.mk-head{font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);font-weight:600;padding:0 10px 6px;border-bottom:1px solid var(--faint)}
.mk-head span:nth-child(n+3){text-align:right}
.mk-row{padding:10px;border-bottom:1px solid var(--faint);cursor:pointer;border-radius:6px}
.mk-row:hover,.mk-row.open{background:var(--sunk)}
.mk-name{display:grid;gap:2px;min-width:0}
.mk-name b{font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.mk-name small{color:var(--muted);font-size:12.5px}
.mk-cmp{display:grid;gap:4px}
.mk-nums{display:flex;justify-content:space-between;font-size:12.5px;color:var(--muted)}
.mk-nums b{color:var(--ink);font-weight:600}
.mk-vs{position:relative;height:8px;border-radius:4px;background:var(--sunk);overflow:visible}
.mk-row:hover .mk-vs,.mk-row.open .mk-vs{background:var(--surface)}
.mk-vs i{position:absolute;left:0;top:0;bottom:0;background:var(--whisker);border-radius:4px}
.mk-vs b{position:absolute;top:-4px;bottom:-4px;width:3px;margin-left:-1.5px;background:var(--sel);border-radius:2px}
.mk-edge{text-align:right;display:grid}
.mk-edge b{font-family:var(--display);font-size:22px;line-height:1}
.mk-edge small{font-size:11px;font-weight:700;letter-spacing:.06em}
.mk-money{text-align:right;display:grid}
.mk-money small{color:var(--muted);font-size:11.5px}
.mk-liq{font-weight:600;font-size:15px}
.mk-liq.deep{color:var(--ink)} .mk-liq.ok{color:var(--muted)} .mk-liq.thin{color:var(--r1)}
.mk-game{font-family:var(--display);font-size:18px;font-weight:600;letter-spacing:.03em;padding:14px 10px 4px}
.mk-det{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,230px),1fr));gap:8px 18px;padding:10px 12px 16px;background:var(--sunk);border-radius:0 0 8px 8px;margin-top:-4px}
.mk-kv{display:grid;gap:1px}
.mk-kv span{font-size:12px;color:var(--muted)}
.mk-kv b{font-weight:600}
.mk-book{grid-column:1/-1;display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,260px),1fr));gap:12px}
.mk-lv{display:grid;grid-template-columns:50px 1fr 70px;gap:8px;font-size:13px;padding:2px 0}
.mk-lv span:last-child{text-align:right}
.mk-help{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,280px),1fr));gap:10px 24px;margin:0}
.mk-help dt{font-weight:600} .mk-help dd{margin:0;color:var(--muted);font-size:14px;line-height:1.5}
@media (max-width:760px){
  .mk-head{display:none}
  .mk-row{grid-template-columns:minmax(0,1fr) auto;grid-template-areas:"name edge" "cmp money";gap:8px 12px}
  .mk-name{grid-area:name} .mk-edge{grid-area:edge} .mk-cmp{grid-area:cmp} .mk-money{grid-area:money}
  .mk-name b{white-space:normal}
  .tiles{grid-template-columns:1fr 1fr}
  .tile .v{font-size:24px}
}

.g1{color:var(--g1)} .g2{color:var(--g2)} .g3{color:var(--g3)} .r1{color:var(--r1)} .r2{color:var(--r2)} .r3{color:var(--r3)}
tr.bg-r1 td{background:color-mix(in srgb,var(--r1) 6%,transparent)}
.seg{display:flex;flex-wrap:wrap;gap:4px}
.seg button{border:1px solid var(--faint);background:none;border-radius:999px;padding:4px 12px;cursor:pointer;font-size:13.5px;color:var(--ink)}
.seg button[aria-pressed="true"]{background:var(--ink);color:var(--bg);border-color:var(--ink)}
@media (max-width:600px){.hero h1{font-size:30px}.cards{grid-template-columns:1fr}.card{padding:16px}.list li{grid-template-columns:1fr auto 54px;gap:8px}.wrap,.hero,.card,.steps li{min-width:0}}
"""


def write_page(rel, title, active, body, depth=None):
    depth = rel.count("/") if depth is None else depth
    page = f"""<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>{html.escape(title)}</title>
{report.FONTS}<style>{PAGE_CSS}</style>
{bar(depth, active)}
<div class="wrap">{body}</div>{NEWBIE_JS}"""
    path = os.path.join(SITE, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(page)


# ------------------------------------------------------------------ home cards

def next_week(nfl):
    """Link the following week's preview page when it has already been projected."""
    stem = f"{nfl['season']}_wk{nfl['week'] + 1:02d}"
    if os.path.exists(os.path.join(HERE, "projections", "weeks", f"{stem}.html")):
        return (f'<p class="acc">Week {nfl["week"] + 1} projections are already up. '
                f'<a href="nfl/weeks/{stem}.html">See the Week {nfl["week"] + 1} preview →</a></p>')
    return ""


def page_list(s):
    return '<ul class="pages">' + "".join(
        f'<li><a href="{path}"><b>{html.escape(label)}</b><span>{html.escape(desc)}</span></a></li>'
        for _, label, path, desc in s["pages"]) + "</ul>"


def nfl_card():
    nfl = page_data(os.path.join(HERE, "projections", "index.html"))
    res = page_data(os.path.join(HERE, "projections", "results.html"))
    body = "<p class='muted'>No NFL projections yet.</p>"
    if nfl:
        games = nfl["games"]
        tds = sorted(((p["s"]["anytime_td"][0], p["n"], p["t"], p["o"]) for p in nfl["players"] if "anytime_td" in p["s"]),
                     reverse=True)[:5]
        body = f"""
        <p class="lead">Week {nfl['week']} · {len(games)} game{'s' if len(games) != 1 else ''} still to play
          {('· next kickoff ' + html.escape(games[0]['kickoff'])) if games else ''}</p>
        <h3>Most likely to score a TD</h3>
        <ol class="list">{''.join(f'<li><span>{html.escape(n)} <small>{t} vs {o}</small></span><b>{p*100:.0f}%</b><em>{american(p)}</em></li>' for p, n, t, o in tds)}</ol>
        {next_week(nfl)}"""
    acc = ""
    if res:
        allm = res.get("models", {}).get("vegas") or {}
        cur = next((s for s in sorted(allm, reverse=True) if allm[s].get("diag")), None)
        if cur:
            sm = allm[cur]["diag"]["summary"]
            acc = (f'<p class="acc">Track record {cur}: <b>{sm["in80"]*100:.0f}%</b> of results inside the 8-in-10 range '
                   f'(target 80%) · <b>{sm["td_act"]}</b> TD scorers vs <b>{sm["td_exp"]:.0f}</b> expected. '
                   f'<a href="nfl/results.html">Every week graded →</a></p>')
    return body + acc


def nhl_card():
    nhl = page_data(os.path.join(HERE, "hockey", "projections", "index.html"))
    if not nhl:
        return "<p class='muted'>No NHL slate yet.</p>"
    rows = sorted(((p["p"], p["name"], s["team"], o["team"], p["pp"])
                   for g in nhl["games"] for s, o in ((g["away"], g["home"]), (g["home"], g["away"]))
                   for p in s["players"]), reverse=True)[:5]
    day = dt.date.fromisoformat(nhl["date"]).strftime("%A, %B %d").replace(" 0", " ")
    return f"""
        <p class="lead">{html.escape(day)} · {len(nhl['games'])} game{'s' if len(nhl['games']) != 1 else ''}</p>
        <h3>Most likely to score a goal</h3>
        <ol class="list">{''.join(f'<li><span>{html.escape(n)} <small>{t} vs {o}{" · " + pp if pp else ""}</small></span><b>{p*100:.0f}%</b><em>{american(p)}</em></li>' for p, n, t, o, pp in rows)}</ol>"""


def nba_card():
    nba = page_data(os.path.join(HERE, "nba", "projections", "index.html"))
    if not nba:
        return "<p class='muted'>No NBA slate yet.</p>"
    rows = sorted(((p["s"]["vegas"]["pts"][3], p["n"], p["t"], p["o"]) for p in nba["players"] if p["s"].get("vegas", {}).get("pts")),
                  reverse=True)[:5]
    day = dt.date.fromisoformat(nba["date"]).strftime("%A, %B %d").replace(" 0", " ")
    return f"""
        <p class="lead">{html.escape(day)} · {len(nba['games'])} game{'s' if len(nba['games']) != 1 else ''}</p>
        <h3>Top projected scorers (median points)</h3>
        <ol class="list">{''.join(f'<li><span>{html.escape(n)} <small>{t} vs {o}</small></span><b>{v:.0f}</b><em>pts</em></li>' for v, n, t, o in rows)}</ol>"""


HOME_CARDS = {"nfl": nfl_card, "nhl": nhl_card, "nba": nba_card}


def home():
    built = dt.datetime.now().strftime("%A %b %d, %I:%M %p").replace(" 0", " ")
    live = [s for s in SPORTS if s["status"] == "live"]
    soon = [s for s in SPORTS if s["status"] == "soon"]
    cards = "".join(f"""<section class="card"><div class="ch"><span class="sport">{s['name']}</span><span class="badge live">Live</span></div>
        <p class="lead">{html.escape(s['blurb'])}</p>{HOME_CARDS[s['key']]()}{page_list(s)}</section>""" for s in live)
    soon_cards = "".join(f"""<section class="card soon"><div class="ch"><span class="sport">{s['name']}</span><span class="badge">{html.escape(s['eta'])}</span></div>
        <p class="lead">{html.escape(s['blurb'])}</p><a href="{s['pages'][0][2]}" style="color:var(--accent);font-weight:600;text-decoration:none">What's planned →</a></section>""" for s in soon)
    body = f"""
  <header class="hero">
    <div class="eyebrow">Updated {built}</div>
    <h1>Player projections, simulated</h1>
    <p class="sub">Every game is simulated thousands of times from play-by-play data. For each player you get the
      most likely result, a realistic range, the chance to score, and the fair odds that chance is worth.</p>
  </header>
  <ol class="steps">
    <li><b>Pick a sport</b><span>Use the tabs at the top. Each sport has the next slate and a Results page.</span></li>
    <li><b>Open a game</b><span>See each player's median (the coin-flip number), the range of likely outcomes, and the chance to score.</span></li>
    <li><b>Compare with your book</b><span>Tap a player to slide any line and see the over/under chance and fair odds. If a sportsbook pays more than our fair odds, that side is value by the model. <a href="guide.html">How to read the numbers →</a></span></li>
  </ol>
  <div class="sechead"><h2>Live now</h2></div>
  <div class="cards">{cards}</div>
  <div class="sechead"><h2>Coming next</h2></div>
  <div class="cards">{soon_cards}</div>"""
    write_page("index.html", "Sim Sheet", "home", body, depth=0)
    # old links pointed at index.html#guide
    with open(os.path.join(SITE, "index.html"), "a", encoding="utf-8") as f:
        f.write("<script>if (location.hash === '#guide') location.replace('guide.html');</script>")


def guide():
    body = """
  <header class="hero"><h1>How to read Sim Sheet</h1>
    <p class="sub">Two minutes, start to finish. Every number on the site comes from simulating each game thousands of
      times; these are the words you'll see and what they mean.</p>
    <nav class="toc" aria-label="Sections"><a href="#basics">The basics</a><a href="#odds">Odds and value</a><a href="#nfl">NFL terms</a><a href="#nhl">NHL terms</a><a href="#nba">NBA terms</a><a href="#trust">How accurate is it?</a></nav>
  </header>
  <div class="guide">
  <section id="basics"><h2>The basics</h2><dl>
    <div><dt>Median</dt><dd>The middle outcome: half the simulated games land above it, half below. "Median 64 receiving yards" means over or under 63.5 is about a coin flip.</dd></div>
    <div><dt>Range bar (8 in 10 games)</dt><dd>The thin line spans where 8 of every 10 simulated games landed; the darker band is the middle half. Short bar: steady player. Long bar: boom or bust.</dd></div>
    <div><dt>Chance to score</dt><dd>How often the player scored in the simulations. 40% means about 2 games in 5.</dd></div>
    <div><dt>Projected score / win chance</dt><dd>Average points or goals per team across the simulations, and how often each side won.</dd></div>
    <div><dt>▲ blind / ▼ blind</dt><dd>Next to a player: the market-blind model is noticeably higher (green) or lower (red) on that stat. Hover for both numbers. A small lean, not a lock.</dd></div>
    <div><dt>Shades of green and red</dt><dd>Green leans over or favorable, red leans under or tough. The deeper and brighter the shade, the bigger the edge: pale = slight, medium = moderate, bright = strong.</dd></div>
    <div><dt>★ and highlights</dt><dd>Stars and tinted rows mark the strongest options on a page (for example tonight's top goal scorers). Hover or tap them for the reasons.</dd></div>
    <div><dt>Results pages</dt><dd>Every past game is graded against the real box score, so you can check how often the ranges held before trusting them.</dd></div>
  </dl></section>
  <section id="odds"><h2>Odds and value</h2><dl>
    <div><dt>Fair odds</dt><dd>What a chance is worth with no bookmaker margin. 50% = −100 / +100, 33% = +200, 20% = +400.</dd></div>
    <div><dt>Finding value</dt><dd>If a book pays more than the fair odds (book +150 when fair is +120), the model thinks that side is good value. If the book's price is shorter, it isn't.</dd></div>
    <div><dt>Line slider (NFL)</dt><dd>Tap any player's name in a game to open a slider: drag the line up or down (or use − / +) and the over and under chances and fair odds update as you go. Pick the stat with the buttons above it.</dd></div>
    <div><dt>Keep it in proportion</dt><dd>Even the best picks hit well under half the time for scorer props. The fair odds tell you whether the price is right, not whether it will happen.</dd></div>
  </dl></section>
  <section id="nfl"><h2>NFL terms</h2><dl>
    <div><dt>Anytime / first TD</dt><dd>Chance to score at least one touchdown, and to score the game's first touchdown.</dd></div>
    <div><dt>Market-blind</dt><dd>A second model that never sees the Vegas spread or total; switch any NFL game to it with the With Vegas / Market-blind switch in the game's header. When it disagrees with the main model, the result tends to land in between.</dd></div>
    <div><dt>Model agreement tab</dt><dd>The two models side by side for each player, with a calibrated chance to beat the main median. Green = blind model more bullish, red = more bearish. Leans, not locks: the best are around 53–55%.</dd></div>
    <div><dt>Defense vs position</dt><dd>How many fantasy points each defense allows to QBs, RBs, WRs (slot and outside) and TEs, ranked this season and blended with recent history.</dd></div>
    <div><dt>Matchups ▲ ▼ and red-zone tags</dt><dd>Flags for unusually easy or tough matchups. They explain a projection; it already includes them, so don't add them on top.</dd></div>
    <div><dt>WR1 / TE1 / RB1</dt><dd>Depth-chart roles from the team's current chart.</dd></div>
  </dl></section>
  <section id="nhl"><h2>NHL terms</h2><dl>
    <div><dt>P(goal) / 1st goal</dt><dd>Chance to score at least one goal (regulation or OT, not shootouts), and to score the game's first goal.</dd></div>
    <div><dt>Lines and PP units</dt><dd>L1–L4 forward lines, D1–D3 defense pairs, PP1/PP2 power-play units. Each team shows its lineup source: official game roster, reported lines, or an ice-time estimate.</dd></div>
    <div><dt>Moneyline, puck line, total</dt><dd>Chance each team wins (OT and shootout included), wins by 2+ (−1.5), and the chance the game goes over 5.5 or 6.5 goals.</dd></div>
    <div><dt>SV%, GSAA, GSAx</dt><dd>Save percentage; goals saved above an average save percentage; goals saved above expected given shot quality. Positive = better goalie.</dd></div>
    <div><dt>Defense vs position</dt><dd>Expected goals each team allows to centers, wingers and defensemen, vs league average.</dd></div>
    <div><dt>H2H G-A-P</dt><dd>Goals-assists-points against tonight's opponent since 2022–23. Shown for interest; it doesn't predict much.</dd></div>
  </dl></section>
  <section id="nba"><h2>NBA terms</h2><dl>
    <div><dt>MIN, PTS, REB, AST, 3PM, PRA</dt><dd>Minutes, points, rebounds, assists, threes made, and points + rebounds + assists. Each cell shows the median with the 8-in-10 range under it.</dd></div>
    <div><dt>Out / Q / DTD</dt><dd>From the official injury report and current rosters (which follow trades). Out players are removed and their minutes shared among teammates by role; Q (questionable) and DTD (day-to-day) players are projected to play.</dd></div>
    <div><dt>▲ min / ▼ min</dt><dd>Minutes trending up or down: his last few games vs his longer average.</dd></div>
    <div><dt>Cell colors</dt><dd>The opponent's defense against the player's position (guards, forwards, centers) for that stat: green gives up more, red less.</dd></div>
    <div><dt>new</dt><dd>No NBA games yet (rookies, two-way players): projected as a small bench role until real games come in.</dd></div>
  </dl></section>
  <section id="trust"><h2>How accurate is it?</h2>
    <p style="margin:0;line-height:1.6">Every model is tested on past seasons it never saw before going live, and every slate is graded afterwards on the
      Results pages. A well-calibrated model puts about 8 in 10 results inside its 8-in-10 range, and players given a 30%
      chance score about 30% of the time. That's the standard we hold each sport to. Data: nflverse play-by-play, the NHL's
      public API, Open-Meteo weather. These are estimates, not guarantees.</p>
  </section>
  </div>"""
    write_page("guide.html", "How to read · Sim Sheet", "guide", body, depth=0)


def updates():
    """Self-learning log: what learns every run, and each weekly self-tuning run (learn.py)."""
    import learned
    log = json.load(open(os.path.join(HERE, "learning_log.json"))) if os.path.exists(os.path.join(HERE, "learning_log.json")) else []
    params = learned.load()

    def fmt(v):
        return f"{v:g}" if isinstance(v, (int, float)) else html.escape(str(v))

    def section(sport, name):
        runs = [r for r in log if r["sport"] == sport and not r.get("dry")][::-1]
        cur = params.get(sport, {})
        learned_rows = "".join(f"<li><code>{html.escape(k.rsplit('.', 1)[1])}</code> = <b>{fmt(v)}</b></li>" for k, v in sorted(cur.items())) \
            or "<li class='muted'>None yet: the code defaults are still the best tested settings.</li>"
        rows = []
        for r in runs[:20]:
            when = dt.datetime.fromisoformat(r["date"]).strftime("%b %d, %Y").replace(" 0", " ")
            a = r.get("adopted")
            if a:
                k, v = next(iter(a["change"].items()))
                res = (f'<span class="badge live">Adopted</span> {html.escape(a["what"])}: '
                       f'<b>{fmt(a["was"][k])} → {fmt(v)}</b>')
            else:
                res = '<span class="badge">No change</span> nothing beat the current settings in both test windows'
            tried = ", ".join(sorted({html.escape(t["what"]) for t in r.get("tried", [])}))
            rows.append(f"<tr><td class='l'>{when}</td><td class='l'>{res}</td><td class='l small muted' style='white-space:normal'>{tried}</td>"
                        f"<td class='muted'>{', '.join(map(str, r['windows']))}</td></tr>")
        table = (f"<div class='tw'><table class='t'><thead><tr><th class='l'>Run</th><th class='l'>Result</th><th class='l'>Settings tested</th>"
                 f"<th>Tested on</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div>") if rows else \
            "<p class='muted' style='margin:0'>No self-tuning runs yet. The first runs Tuesday morning.</p>"
        return f"""<section id="{sport}"><h2>{name}</h2>
          <p style="margin:0" class="muted">Settings the model has adopted on its own:</p><ul class="plan" style="margin:0">{learned_rows}</ul>{table}</section>"""

    body = f"""
  <header class="hero"><h1>How the models keep learning</h1>
    <p class="sub">Two kinds of learning. Every run, the models absorb the newest games. Once a week, a
      self-tuning job tests small changes to the models' settings and keeps only the ones that are proven better.</p></header>
  <div class="guide">
  <section><h2>Every run (automatic)</h2><dl>
    <div><dt>Player and team form</dt><dd>Usage, efficiency, ice time, shot rates, goalie and defense ratings update after every game, with recent games weighted most.</dd></div>
    <div><dt>Model refits</dt><dd>The NFL team model refits on all games including this season's; the NHL goal model refits on the last three seasons plus this season so far.</dd></div>
    <div><dt>Grading</dt><dd>Every slate is graded against the real results on the Results pages.</dd></div>
  </dl></section>
  <section><h2>Weekly self-tuning (Tuesdays)</h2><dl>
    <div><dt>What it tries</dt><dd>A few settings each week, in rotation (how much to trust small samples, how long form lasts, touchdown sharing, finishing skill), each nudged one step up and one step down.</dd></div>
    <div><dt>How it decides</dt><dd>Each option re-projects every game of recent seasons using only data from before each game, then is graded. A change is kept only if it beats the current settings in <b>both</b> test windows (last season and this season) by a minimum margin, without hurting any stat or the calibration.</dd></div>
    <div><dt>Guardrails</dt><dd>At most one change per sport per week, small steps inside fixed limits, and the current setting always competes, so a change that stops helping can be undone the same way.</dd></div>
  </dl></section>
  {section("nfl", "NFL")}
  {section("nhl", "NHL")}
  {section("nba", "NBA")}
  </div>"""
    write_page("updates.html", "Model updates · Sim Sheet", "updates", body, depth=0)


def markets_page():
    """Prediction markets vs our chances: summary, scorecard, and one readable row per market
    (details on tap). Data: markets/<sport>_<date>.json (markets.py), markets/grades.json."""
    data = {}
    for sport in ("nfl", "nhl", "nba"):
        fs = [f for f in sorted(glob.glob(os.path.join(HERE, "markets", f"{sport}_*.json")))]
        live = None
        for f in fs[::-1]:
            d = json.load(open(f))
            if not str(d.get("fetched", "")).startswith("backfill"):
                live = d
                live["date"] = os.path.basename(f)[len(sport) + 1:-5]
                break
        if live:
            data[sport] = live
    gp = os.path.join(HERE, "markets", "grades.json")
    grades = json.load(open(gp)) if os.path.exists(gp) else {}
    payload = json.dumps(dict(m=data, g=grades), separators=(",", ":"), default=str).replace("</", "<\\/")
    body = """
  <header class="hero"><div class="eyebrow" id="msub"></div><h1>Markets</h1>
    <p class="sub">What prediction markets (Kalshi, Polymarket) charge for an outcome, next to our chance of it, and how much money is
      really there. Longshots (first and anytime scorers, ladder tails) come first.</p></header>
  <div class="mk-bar">
    <div class="seg" role="group" aria-label="Sport" id="msport"></div>
    <div class="seg" role="group" aria-label="Show" id="mshow"></div>
    <div class="seg" role="group" aria-label="Source" id="msrc"></div>
    <div class="seg" role="group" aria-label="Spread" id="mspr"></div>
    <label class="mk-sort">Sort <select id="msort"><option value="edge">Biggest edge</option><option value="liq">Most money to buy</option><option value="game">By game</option></select></label>
  </div>
  <div class="tiles" id="mtiles"></div>
  <details class="card mk-score" id="scorebox"><summary id="scoresum"></summary><div id="score"></div></details>
  <section class="card" style="gap:10px"><div id="mlist"></div></section>
  <section class="card" style="gap:8px">
    <h2 style="font-size:20px">How to read this</h2>
    <dl class="mk-help">
      <div><dt>Ours vs price</dt><dd>The bar shows our chance (blue tick) against what the market charges (gray fill). If our tick is past the fill, we think YES is cheap.</dd></div>
      <div><dt>Edge</dt><dd>Our chance minus the price you'd pay, after Kalshi's fee. Green = we lean that side; brighter = bigger. Shown for whichever side (YES or NO) we like.</dd></div>
      <div><dt>Money at this price</dt><dd>Dollars you could spend within 3 cents of the best price. Under $100 is thin; $1,000+ is a real market. "Exit" is what you could sell back before the game.</dd></div>
      <div><dt>Thin vs tight</dt><dd>The spread is the gap between the best buy and sell price. <b>Tight</b> (1¢) markets are actively traded and have usually priced in what we know. <b>Thin</b> (2¢+) markets get less attention; so far our NHL scorer chances have beaten the price there, while the market has been sharper in tight ones.</dd></div>
      <div><dt>Big edges in busy markets</dt><dd>When a deep, actively traded market disagrees with us by a lot, it usually knows something (injury, role change). Check the news first.</dd></div>
    </dl>
  </section>
<script type="application/json" id="mdata">__MD__</script>
<script>
const ALL = JSON.parse(document.getElementById('mdata').textContent), MD = ALL.m, GR = ALL.g || {};
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const pct = p => p == null ? '–' : (p < .1 && p > 0 ? (Math.round(p*1000)/10).toFixed(1) : Math.round(p*100)) + '%';
const cents = p => p == null ? '–' : Math.round(p*100) + '¢';
const usd = v => v == null ? '–' : v >= 1000 ? '$' + (v/1000).toFixed(v >= 10000 ? 0 : 1) + 'k' : '$' + Math.round(v);
const num = v => v == null ? '–' : v >= 1000 ? (v/1000).toFixed(v >= 10000 ? 0 : 1) + 'k' : String(Math.round(v));
const shade = e => e >= .08 ? 'g3' : e >= .05 ? 'g2' : e >= .02 ? 'g1' : '';
const KIND = {win:'Winner', total:'Total', spread:'Spread', anytime_td:'Anytime TD', first_td:'First TD', rec_yds:'Rec yds', rush_yds:'Rush yds', pass_yds:'Pass yds', rec:'Receptions', goal:'Goalscorer', first_goal:'First goal', pts:'Points', reb:'Rebounds', ast:'Assists', fg3m:'Threes'};
const sports = Object.keys(MD);
let st = {sport: sports.includes(location.hash.slice(1)) ? location.hash.slice(1) : sports[0], show: 'long', src: 'all', spr: 'all', sort: 'edge', open: null};
try { const s = JSON.parse(localStorage.getItem('mk-view') || '{}'); Object.assign(st, {show: s.show || st.show, src: s.src || st.src, spr: s.spr || st.spr, sort: s.sort || st.sort}); } catch (e) {}
const money = m => (m.book && !m.book.empty) ? m.book.ask_3c : (m.ask_size != null && m.yes_ask != null ? m.ask_size * m.yes_ask : (m.liq ?? null));
const exitm = m => (m.book && !m.book.empty) ? m.book.bid_3c : null;
const spr = m => (m.yes_ask != null && m.yes_bid != null && m.yes_bid > 0) ? m.yes_ask - m.yes_bid : null;
const thin = m => { const x = spr(m); return x != null && x >= .015; };
function best(m){ const y = m.edge_yes ?? -9, n = m.edge_no ?? -9; return y >= n ? {e: y, side: 'YES'} : {e: n, side: 'NO'}; }
function bar(p, price){
  const x = v => Math.max(0, Math.min(100, v * 100));
  return `<div class="mk-vs" role="img" aria-label="ours ${pct(p)}, price ${cents(price)}"><i style="width:${x(price ?? 0)}%"></i><b style="left:${x(p ?? 0)}%"></b></div>`;
}
function liqTag(v){ if (v == null) return '<span class="muted">–</span>'; const c = v >= 1000 ? 'deep' : v >= 100 ? 'ok' : 'thin'; return `<span class="mk-liq ${c}">${usd(v)}</span>`; }
function detail(m){
  const b = m.book && !m.book.empty ? m.book : null;
  const lv = (arr, lab) => arr && arr.length ? `<div><span class="eyebrow">${lab}</span>${arr.map(([p, q]) => `<div class="mk-lv"><span>${cents(p)}</span><span>${num(q)} ${m.units === 'dollars' ? 'shares' : 'contracts'}</span><span class="muted">${usd(p*q)}</span></div>`).join('')}</div>` : '';
  return `<div class="mk-det">
    <div class="mk-kv"><span>Bid / ask (YES)</span><b>${cents(m.yes_bid)} / ${cents(m.yes_ask)}${spr(m) != null ? ` · ${Math.round(spr(m)*100)}¢ spread (${thin(m) ? 'thin' : 'tight'})` : ''}</b></div>
    <div class="mk-kv"><span>Our chance</span><b>${Object.entries(m.ours).map(([k,v]) => `${pct(v)}${k === 'vegas' ? ' (With Vegas)' : k === 'blind' ? ' (Market-blind)' : ''}`).join(' · ')}</b></div>
    <div class="mk-kv"><span>Edge YES / NO</span><b>${m.edge_yes != null ? (m.edge_yes*100).toFixed(1) + '%' : '–'} / ${m.edge_no != null ? (m.edge_no*100).toFixed(1) + '%' : '–'}</b></div>
    ${b ? `<div class="mk-kv"><span>Buy within 1¢ / 3¢ / 5¢</span><b>${usd(b.ask_1c)} / ${usd(b.ask_3c)} / ${usd(b.ask_5c)}</b></div><div class="mk-kv"><span>Exit (sell) within 1¢ / 3¢</span><b>${usd(b.bid_1c)} / ${usd(b.bid_3c)}</b></div>` : ''}
    <div class="mk-kv"><span>${m.units === 'dollars' ? 'Volume / liquidity' : 'Traded / open interest (contracts)'}</span><b>${m.units === 'dollars' ? usd(m.vol) + ' / ' + usd(m.liq) : num(m.vol) + ' / ' + num(m.oi)}</b></div>
    <div class="mk-kv"><span>Source</span><b>${m.source === 'kalshi' ? 'Kalshi' : 'Polymarket'}</b></div>
    ${b ? `<div class="mk-book">${lv(b.asks, 'Cheapest offers to buy YES')}${lv(b.bids, 'Best bids (your exit)')}</div>` : ''}</div>`;
}
function track(m){ const G = (GR.groups || []).filter(x => x.sport === st.sport && x.kind === m.kind); return G.find(x => x.source === m.source) || G[0]; }
function weak(m){ const g = track(m); return g && g.n >= 30 && g.model_ll > g.market_ll + .005; }
function row(m){
  const b = best(m), p = m.ours.vegas ?? m.ours.model, open = st.open === m.ticker, w = weak(m);
  return `<div class="mk-row${open ? ' open' : ''}" data-t="${esc(m.ticker)}" tabindex="0" role="button" aria-expanded="${open}">
    <div class="mk-name"><b>${esc(m.title)}</b><small>${esc(m.away)} @ ${esc(m.home)} · ${KIND[m.kind] || esc(m.kind)} · ${m.source === 'kalshi' ? 'Kalshi' : 'Polymarket'}${thin(m) ? ` <span class="mk-thin" title="Bid/ask gap of ${Math.round(spr(m)*100)}¢: a less-traded market">thin ${Math.round(spr(m)*100)}¢</span>` : ''}</small></div>
    <div class="mk-cmp"><div class="mk-nums"><span>Ours <b>${pct(p)}</b></span><span>Price <b>${cents(m.yes_ask)}</b></span></div>${bar(p, m.yes_ask)}</div>
    <div class="mk-edge ${b.e > -9 && !w ? shade(b.e) : ''}${w && b.e >= .02 ? ' weak' : ''}">${b.e >= .02 ? `<b>+${(b.e*100).toFixed(1)}%</b><small>${b.side}</small>${w ? '<small class="mk-warn" title="On settled games of this type the market price has been more accurate than our model, so treat this edge with caution">market ahead</small>' : ''}` : '<span class="muted">no edge</span>'}</div>
    <div class="mk-money">${liqTag(money(m))}<small>exit ${exitm(m) == null ? '–' : usd(exitm(m))}</small></div>
  </div>${open ? detail(m) : ''}`;
}
function scorecard(){
  const g = (GR.groups || []).filter(x => x.sport === st.sport);
  const el = document.getElementById('score'), sm = document.getElementById('scoresum');
  if (!g.length){ sm.innerHTML = `<b>Scorecard: model vs market</b><span class="muted">nothing settled yet for ${st.sport.toUpperCase()}</span>`; el.innerHTML = `<p class="muted" style="margin:0">Each game is graded on its last pregame price once it's final.</p>`; return; }
  const a = g.filter(x => x.kind === 'all'), N = a.reduce((t, x) => t + x.n, 0), B = a.reduce((t, x) => t + x.bets, 0), W = a.reduce((t, x) => t + x.bets_won, 0);
  const beat = g.filter(x => x.kind !== 'all' && x.n >= 30), ours = beat.filter(x => x.model_ll < x.market_ll - .002).map(x => KIND[x.kind] || x.kind), theirs = beat.filter(x => x.market_ll < x.model_ll - .002).map(x => KIND[x.kind] || x.kind);
  sm.innerHTML = `<b>Scorecard: model vs market</b><span class="muted">${N.toLocaleString()} settled · ${theirs.length ? 'market better on ' + [...new Set(theirs)].join(', ') : 'no market type where the market is clearly better'}${ours.length ? ' · model better on ' + [...new Set(ours)].join(', ') : ''} · edge bets ${W}/${B} won</span>`;
  const rows = g.filter(x => x.kind !== 'all').sort((a, b) => b.n - a.n);
  const tot = g.filter(x => x.kind === 'all');
  const who = x => x.model_ll < x.market_ll - .002 ? '<span class="g2">Model</span>' : x.market_ll < x.model_ll - .002 ? '<span class="r2">Market</span>' : '<span class="muted">Tie</span>';
  const roi = x => x.bets ? `${x.bets_won}/${x.bets} won · <b class="${x.roi > 0 ? 'g2' : 'r2'}">${x.roi > 0 ? '+' : ''}${Math.round(x.roi*100)}%</b>` : '<span class="muted">no bets</span>';
  el.innerHTML = `<p class="muted" style="margin:0">Settled games only, priced at the last pregame snapshot. "Closer" = lower prediction error (log-loss). Bets = buying the side with a 3%+ edge, $ result per $1 staked. Small samples swing a lot.</p>
    <div class="tw"><table class="t"><thead><tr><th class="l">Market</th><th class="l">Source</th><th>Settled</th><th class="l">Closer to the result</th><th class="l">3%+ edge bets</th></tr></thead><tbody>
    ${tot.concat(rows).map(x => `<tr${x.kind === 'all' ? ' style="font-weight:600"' : ''}><td class="l">${x.kind === 'all' ? 'All markets' : (KIND[x.kind] || x.kind)}</td><td class="l">${x.source === 'kalshi' ? 'Kalshi' : 'Polymarket'}</td><td>${x.n}</td><td class="l">${who(x)} <small class="muted">${x.model_ll.toFixed(3)} vs ${x.market_ll.toFixed(3)}</small></td><td class="l">${roi(x)}</td></tr>`).join('')}</tbody></table></div>`;
}
function render(){
  const seg = (id, key, opts) => { document.getElementById(id).innerHTML = opts.map(([k, l]) => `<button data-k="${key}" data-v="${k}" aria-pressed="${st[key] === k}">${l}</button>`).join(''); };
  seg('msport', 'sport', sports.map(s => [s, s.toUpperCase()]));
  seg('mshow', 'show', [['long', 'Longshots'], ['edge', 'Edges 3%+'], ['all', 'Everything']]);
  seg('msrc', 'src', [['all', 'Both'], ['kalshi', 'Kalshi'], ['polymarket', 'Polymarket']]);
  seg('mspr', 'spr', [['all', 'Any spread'], ['thin', 'Thin 2¢+'], ['tight', 'Tight 1¢']]);
  document.getElementById('msort').value = st.sort;
  try { localStorage.setItem('mk-view', JSON.stringify({show: st.show, src: st.src, spr: st.spr, sort: st.sort})); } catch (e) {}
  const D = MD[st.sport];
  if (!D){ document.getElementById('mlist').innerHTML = '<p class="muted">No market data yet.</p>'; return; }
  document.getElementById('msub').textContent = `${st.sport.toUpperCase()} · slate ${D.date} · prices updated ${String(D.fetched).replace('T', ' ')}`;
  let rows = D.markets.filter(m => st.src === 'all' || m.source === st.src);
  const all = rows;
  if (st.show === 'long') rows = rows.filter(m => m.longshot);
  if (st.show === 'edge') rows = rows.filter(m => best(m).e >= .03);
  if (st.spr === 'thin') rows = rows.filter(thin);
  if (st.spr === 'tight') rows = rows.filter(m => spr(m) != null && !thin(m));
  rows.sort((a, b) => st.sort === 'liq' ? (money(b) || 0) - (money(a) || 0) : st.sort === 'game' ? (a.away + a.home).localeCompare(b.away + b.home) || best(b).e - best(a).e : best(b).e - best(a).e);
  const ls = all.filter(m => m.longshot), med = a => { const v = a.filter(x => x != null).sort((x, y) => x - y); return v.length ? v[Math.floor(v.length / 2)] : null; };
  const t = (v, l, s) => `<div class="tile"><span class="eyebrow">${l}</span><span class="v">${v}</span><span class="s">${s}</span></div>`;
  document.getElementById('mtiles').innerHTML = t(all.length, 'Markets matched', 'to our projections') + t(all.filter(m => best(m).e >= .03).length, 'With a 3%+ edge', 'either side, after fees')
    + t(usd(med(ls.map(money))), 'Longshot money', 'typical $ within 3¢ of the price') + (() => { const x = med(ls.map(exitm)); return t(usd(x), 'Longshot exit', x != null && x < 25 ? 'almost no buyers: plan to hold to the end' : 'typical $ you could sell back'); })();
  let html = '', last = null;
  for (const m of rows.slice(0, 250)){
    const gk = m.away + ' @ ' + m.home;
    if (st.sort === 'game' && gk !== last){ html += `<div class="mk-game">${esc(gk)}</div>`; last = gk; }
    html += row(m);
  }
  document.getElementById('mlist').innerHTML = rows.length ? `<div class="mk-head"><span>Market</span><span>Ours vs price</span><span>Edge</span><span>Money at this price</span></div>${html}` : '<p class="muted">Nothing matches these filters.</p>';
  scorecard();
}
document.addEventListener('click', e => {
  const b = e.target.closest('button[data-k]'); if (b){ st[b.dataset.k] = b.dataset.v; st.open = null; if (b.dataset.k === 'sport') history.replaceState(null, '', '#' + b.dataset.v); render(); return; }
  const r = e.target.closest('.mk-row'); if (r){ st.open = st.open === r.dataset.t ? null : r.dataset.t; render(); }
});
document.addEventListener('keydown', e => { if ((e.key === 'Enter' || e.key === ' ') && e.target.classList.contains('mk-row')){ e.preventDefault(); e.target.click(); } });
document.getElementById('msort').addEventListener('change', e => { st.sort = e.target.value; render(); });
render();
</script>""".replace("__MD__", payload)
    write_page("markets.html", "Markets · Sim Sheet", "markets", body, depth=0)


def preview(s, plan):
    items = "".join(f"<li>{html.escape(x)}</li>" for x in plan)
    body = f"""
  <header class="hero"><div class="eyebrow">{html.escape(s['eta'])}</div><h1>{s['name']} is on the way</h1>
    <p class="sub">{html.escape(s['blurb'])}</p></header>
  <div class="guide"><section><h2>What you'll get</h2><ul class="plan">{items}</ul>
    <p class="note" style="margin:0">Like NFL and NHL, it will be backtested on past seasons before launch and graded after every slate.
      Until then, <a href="../nfl/index.html">NFL</a> and <a href="../nhl/index.html">NHL</a> are live.</p></section></div>"""
    write_page(f"{s['key']}/index.html", f"{s['name']} · Sim Sheet", f"{s['key']}:preview", body)


PLANS = {
    "nba": ["Points, rebounds, assists, threes, steals and blocks for every player, with ranges and fair odds",
            "Minutes projections that follow injury reports and rest days",
            "Moneylines, spreads and game totals",
            "Nightly results grading, like NHL"],
    "mlb": ["Hits, total bases, home runs, RBIs and runs for every batter",
            "Pitcher strikeouts, outs recorded and earned runs",
            "Moneylines, run lines and totals, with weather and park effects",
            "Daily results grading"],
}


def redirect(dst, target):
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    with open(dst, "w", encoding="utf-8") as f:
        f.write(f'<meta charset="utf-8"><meta http-equiv="refresh" content="0; url={target}">'
                f'<title>Moved</title><a href="{target}">This page moved here.</a>')


def main():
    os.makedirs(SITE, exist_ok=True)
    shutil.copy2(os.path.join(HERE, "mkt.js"), os.path.join(SITE, "mkt.js"))
    try:
        import markets
        for sp in ("nfl", "nhl", "nba"):
            markets.write_live(sp)
    except Exception as e:                                   # pages still work without market prices
        print("live market files skipped:", type(e).__name__, e)
    P = os.path.join(HERE, "projections")
    if os.path.exists(os.path.join(P, "index.html")):
        inject(os.path.join(P, "index.html"), os.path.join(SITE, "nfl", "index.html"), 1, "nfl:week")
    if os.path.exists(os.path.join(P, "results.html")):
        inject(os.path.join(P, "results.html"), os.path.join(SITE, "nfl", "results.html"), 1, "nfl:results")
    has = lambda *parts: os.path.exists(os.path.join(P, *parts))
    for f in glob.glob(os.path.join(P, "weeks", "*.html")):
        b = os.path.basename(f)
        inject(f, os.path.join(SITE, "nfl", "weeks", b), 2, "nfl:results")
    for f in glob.glob(os.path.join(P, "blind", "weeks", "*.html")):
        b = os.path.basename(f)
        inject(f, os.path.join(SITE, "nfl", "blind", "weeks", b), 3, "nfl:results")
    blind = sorted(glob.glob(os.path.join(P, "blind", "*_wk*.html")))
    for f in blind:
        b = os.path.basename(f)
        inject(f, os.path.join(SITE, "nfl", "blind", b), 2, "nfl:week")    # kept for old links; the switch is per game now
    if blind:
        inject(blind[-1], os.path.join(SITE, "nfl", "blind", "index.html"), 2, "nfl:week")
    H = os.path.join(HERE, "hockey", "projections")
    for f in glob.glob(os.path.join(H, "*.html")):
        inject(f, os.path.join(SITE, "nhl", os.path.basename(f)), 1,
               "nhl:results" if os.path.basename(f) == "results.html" else "nhl:sheet")
    N = os.path.join(HERE, "nba", "projections")
    for f in glob.glob(os.path.join(N, "*.html")):
        inject(f, os.path.join(SITE, "nba", os.path.basename(f)), 1,
               "nba:results" if os.path.basename(f) == "results.html" else "nba:slate")
    for s in SPORTS:
        if s["status"] == "soon":
            preview(s, PLANS.get(s["key"], []))
    home()
    guide()
    updates()
    markets_page()

    # Old addresses from before the reorganisation
    redirect(os.path.join(SITE, "results.html"), "nfl/results.html")
    for old in ("weeks", "blind"):
        d = os.path.join(SITE, old)
        if os.path.isdir(d):
            shutil.rmtree(d)
    print("Built site/:", ", ".join(sorted(os.path.relpath(p, SITE) for p in glob.glob(os.path.join(SITE, "*")))))


if __name__ == "__main__":
    main()
