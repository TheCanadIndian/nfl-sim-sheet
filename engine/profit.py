#!/usr/bin/env python3
"""
Profit page: is every model making money? One record of what the site recommends, graded at the prices shown.

    python profit.py rows <sport>   # in each sport's run: grade that sport's prediction-market picks under the site's
                                    # own rules -> markets/profit_<sport>.json
    python profit.py tags           # every 15 min (markets job): log each Value tag's first price and its last price
                                    # before the game, then settle tags from box scores -> markets/profit_tags.json
    python profit.py page           # build /profit.html from the above + the parlay records (parlays.py)

Sources and rules (what the site actually tells you to bet):
  - Prediction markets (Kalshi with its fee, Polymarket), last pregame quote: chance = half ours, half the market mid.
      NFL / NBA props: the side with a 5%+ edge.  NFL / NHL / NBA game lines (win, total, spread): 3%+.
      NHL anytime goals: our top 3 per game, any edge (YES).  MLB home runs: the VALUE rule, ours 6%+ over the ask.
  - Value tags (NFL / NHL, at the tagged book): also closing-line value (CLV) = the book's last price before the game
    vs the price when the tag first appeared; beating the close is the fastest sign of a real edge.
  - Parlays: every card's record from parlays.py.
Flat 1-unit stakes. Verdict per row: Proven (95% range of the return above 0, 30+ bets), Losing (95% range below 0),
Promising (beats the closing line, z >= 2), Too early (< 30 bets), otherwise Unproven.
"""
import datetime as dt
import glob
import json
import math
import os
import sys
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
OUT = os.path.join(HERE, "markets")
ET = ZoneInfo("America/New_York")
FEE = {"kalshi": .07, "polymarket": 0.0}
PROP_KINDS = {"anytime_td", "rec", "rec_yds", "rush_yds", "pass_yds", "pts", "reb", "ast", "fg3m"}
LINE_KINDS = {"win", "total", "spread"}
LAB = {"rec yds": "rec_yds", "rush yds": "rush_yds", "pass yds": "pass_yds", "receptions": "rec"}


def dec(o):
    o = float(o)
    return 1 + (o / 100 if o > 0 else 100 / -o)


# ---------------------------------------------------------------- prediction-market picks (each sport's run)
def market_rows(sport):
    import grade_markets as GM
    R = {"nfl": GM.nfl_results, "nhl": GM.nhl_results, "nba": GM.nba_results, "mlb": GM.mlb_results}[sport]()
    rows = []
    for f in sorted(glob.glob(os.path.join(OUT, f"{sport}_20??-??-??.json"))):
        date = os.path.basename(f)[len(sport) + 1:len(sport) + 11]
        mk = json.load(open(f)).get("markets", [])
        # NHL: our top 3 per game among the listed scorers
        rank = {}
        if sport == "nhl":
            gl = [r for r in mk if r.get("kind") == "goal" and (r.get("line") or .5) < 1 and r.get("source") == "kalshi"]
            by = {}
            for r in gl:
                o = r["ours"].get("model")
                if o is not None:
                    by.setdefault(r["game"], []).append((o, r.get("pid")))
            for g, xs in by.items():
                for i, (_, pid) in enumerate(sorted(xs, key=lambda t: -t[0])):
                    rank[(g, pid)] = i + 1
        for r in mk:
            k, src = r.get("kind"), r.get("source")
            p = r["ours"].get("vegas", r["ours"].get("model"))
            ya, yb, na = r.get("yes_ask"), r.get("yes_bid"), r.get("no_ask")
            if p is None or not ya or src not in FEE:
                continue
            m = (ya + yb) / 2 if yb is not None and yb > 0 else ya
            fee = FEE[src]
            cy = ya + fee * ya * (1 - ya)
            cn = na + fee * na * (1 - na) if na else None
            if sport == "mlb":
                if k != "hr" or (r.get("line") or .5) >= 1:
                    continue
                cat, q, need, sides = "MLB home runs (VALUE)", p, .06, ("YES",)
            elif sport == "nhl" and k == "goal":
                if (r.get("line") or .5) >= 1 or rank.get((r["game"], r.get("pid")), 99) > 3:
                    continue
                cat, q, need, sides = "NHL anytime goals (top 3)", .5 * p + .5 * m, 0.0, ("YES",)
            elif k in LINE_KINDS:
                cat, q, need, sides = f"{sport.upper()} game lines", .5 * p + .5 * m, .03, ("YES", "NO")
            elif k in PROP_KINDS:
                cat, q, need, sides = f"{sport.upper()} player props", .5 * p + .5 * m, .05, ("YES", "NO")
            else:
                continue                                   # first scorer etc.: not something the site recommends
            bet = None
            # NFL yardage / catch props: the side our median is on, our number 10+ pts from the mid, a tight 15c+ market
            nflp = sport == "nfl" and k in ("rec", "rec_yds", "rush_yds", "pass_yds")
            ok = lambda ps, c_: not nflp or (ps >= .5 and abs(p - m) >= .10 and (ya - (yb or ya)) <= .035 and c_ >= .15)
            if "YES" in sides and q / cy - 1 > need and ok(p, ya):
                bet, cost, win = "YES", cy, q
            elif "NO" in sides and cn and (1 - q) / cn - 1 > need and ok(1 - p, na):
                bet, cost, win = "NO", cn, 1 - q
            if not bet:
                continue
            y = GM.outcome(sport, r, R)
            if y is None:
                continue
            hit = y if bet == "YES" else 1 - y
            rows.append(dict(date=date, sport=sport, cat=cat, src=src, kind=k, title=r.get("title"), bet=bet, cost=round(cost, 4),
                             q=round(win, 4), won=int(hit), pnl=round(hit / cost - 1, 4)))
    json.dump(dict(sport=sport, rows=rows), open(os.path.join(OUT, f"profit_{sport}.json"), "w"), separators=(",", ":"))
    print(f"profit {sport}: {len(rows)} graded market picks")


# ---------------------------------------------------------------- Value tags: price when tagged, closing price, result
def _starts():
    try:
        s = json.load(open(os.path.join(os.environ.get("SIM_SITE") or os.path.join(HERE, "site"), "markets", "schedule.json")))
        return {str(g["game"]): g["start"] for g in s.get("games", [])}
    except (OSError, ValueError):
        return {}


def _tag_leg(sport, b):
    if sport == "nhl":
        return dict(kind="goal", side="o", line=.5) if b == "goal" else None
    if b == "TD":
        return dict(kind="td", side="o", line=.5)
    parts = b.split(" ", 2)
    if len(parts) == 3 and parts[0] in ("over", "under") and parts[2] in LAB:
        return dict(kind=LAB[parts[2]], side="o" if parts[0] == "over" else "u", line=float(parts[1]))
    return None


def tags():
    import parlays as PL
    site = os.environ.get("SIM_SITE") or os.path.join(HERE, "site")
    fp = os.path.join(OUT, "profit_tags.json")
    log = json.load(open(fp)) if os.path.exists(fp) else {}
    now = dt.datetime.now(dt.timezone.utc)
    starts = _starts()
    for sport in ("nfl", "nhl"):
        try:
            FL = json.load(open(os.path.join(site, "markets", f"flags_{sport}.json")))
            PR = json.load(open(os.path.join(site, "markets", f"props_{sport}.json")))
        except (OSError, ValueError):
            continue
        # every live tag: first sighting keeps its price; later runs refresh the book's latest price (the close)
        for gid, tg in FL.get("tags", {}).items():
            st = starts.get(str(gid))
            if st and PL.when(st) <= now:
                continue
            for nm, v in tg.items():
                if not isinstance(v, dict) or v.get("o") is None:
                    continue
                key = f"{sport}|{gid}|{nm}|{v['b']}"
                e = log.setdefault(key, dict(sport=sport, gid=gid, name=nm, bet=v["b"], book=v["k"], o=v["o"], first=now.isoformat(),
                                             p=v.get("p"), m=v.get("m"), start=st))
                e.setdefault("start", st)
        # closing price: the tagged book's current price for that bet, until the game starts
        for key, e in log.items():
            if e["sport"] != sport or e.get("res") is not None:
                continue
            if e.get("start") and PL.when(e["start"]) <= now:
                continue
            L = _tag_leg(sport, e["bet"])
            kind = {"td": "td", "goal": "goal"}.get(L["kind"], L["kind"]) if L else None
            x = (((PR.get("props") or {}).get(str(e["gid"])) or {}).get(e["name"]) or {}).get(kind) if kind else None
            if x:
                book = (x.get("o" if L["side"] == "o" else "u") or {}).get(e["book"])
                if book:
                    e["close"] = book[0]
                    e["close_t"] = now.isoformat()
        # settle started tags from box scores
        for key, e in log.items():
            if e["sport"] != sport or e.get("res") is not None or not e.get("start") or PL.when(e["start"]) > now - dt.timedelta(hours=3):
                continue
            L = _tag_leg(sport, e["bet"])
            if not L:
                e["res"] = "V"
                continue
            try:
                g = PL.nhl_game(e["gid"]) if sport == "nhl" else PL.nfl_game(e["gid"], PL.et_date(e["start"]))
            except Exception:                                                   # noqa: BLE001
                g = None
            if g:
                e["res"] = PL.settle(dict(L, name=e["name"]), g)
    json.dump(log, open(fp, "w"), separators=(",", ":"))
    print(f"profit tags: {len(log)} logged, {sum(1 for e in log.values() if e.get('res') in ('W', 'L'))} settled")


# ---------------------------------------------------------------- page
def _stats(pnl, clv=None):
    n = len(pnl)
    if not n:
        return None
    a = np.asarray(pnl, float)
    roi, sd = float(a.mean()), float(a.std(ddof=1)) if n > 1 else 0.0
    moe = 1.96 * sd / math.sqrt(n) if n > 1 else None
    out = dict(n=n, won=int((a > 0).sum()), units=round(float(a.sum()), 2), roi=round(roi, 4), moe=None if moe is None else round(moe, 4))
    c = None if clv is None else np.asarray([x for x in clv if x is not None], float)
    if c is not None and len(c):
        out.update(clv=round(float(c.mean()), 4), clv_beat=round(float((c > 0).mean()), 3), clv_n=int(len(c)),
                   clv_z=round(float(c.mean() / (c.std(ddof=1) / math.sqrt(len(c)))), 2) if len(c) > 1 and c.std(ddof=1) > 0 else None)
    if n < 30:
        v = "too early"
    elif moe is not None and roi - moe > 0:
        v = "proven"
    elif moe is not None and roi + moe < 0:
        v = "losing"
    elif out.get("clv_z") is not None and out["clv"] > 0 and out["clv_z"] >= 2:
        v = "promising"
    else:
        v = "unproven"
    out["verdict"] = v
    return out


def page_data():
    rows = []                                          # (sport, category, date, pnl, clv)
    for f in glob.glob(os.path.join(OUT, "profit_*.json")):
        if f.endswith("profit_tags.json"):
            continue
        for r in json.load(open(f)).get("rows", []):
            rows.append(dict(sport=r["sport"], cat=f"{r['cat']} ({'Kalshi' if r['src'] == 'kalshi' else 'Polymarket'})", date=r["date"], pnl=r["pnl"], clv=None))
    tp = os.path.join(OUT, "profit_tags.json")
    for e in (json.load(open(tp)) if os.path.exists(tp) else {}).values():
        if e.get("res") not in ("W", "L"):
            continue
        d_ = dec(e["o"])
        clv = (1 / dec(e["close"]) - 1 / d_) if e.get("close") is not None else None
        rows.append(dict(sport=e["sport"], cat=f"{e['sport'].upper()} Value tags", date=(e.get("start") or e["first"])[:10],
                         pnl=(d_ - 1) if e["res"] == "W" else -1.0, clv=clv))
    for f in glob.glob(os.path.join(HERE, "parlays", "*", "*.json")):
        d = json.load(open(f))
        for p in d.get("parlays", []):
            if p.get("res") not in ("W", "L"):
                continue
            pay = p.get("paid") or p.get("d")
            if not pay:
                continue
            rows.append(dict(sport=d["sport"], cat=f"{d['sport'].upper()} parlays: {p['card']}", date=d["date"],
                             pnl=(pay - 1) if p["res"] == "W" else -1.0, clv=None))
    if not rows:
        return dict(rows=[], overall=None, sports=[])
    df = pd.DataFrame(rows)
    today = dt.datetime.now(ET).date().isoformat()
    last30 = (dt.date.fromisoformat(today) - dt.timedelta(days=30)).isoformat()
    sports = []
    for sp, x in df.groupby("sport"):
        cats = []
        for c, y in x.groupby("cat"):
            s = _stats(y.pnl.values, y.clv.values if y.clv.notna().any() else None)
            s30 = _stats(y[y.date >= last30].pnl.values)
            cats.append(dict(cat=c, **s, roi30=s30["roi"] if s30 else None, n30=s30["n"] if s30 else 0))
        cats.sort(key=lambda c: ({"proven": 0, "promising": 1, "unproven": 2, "too early": 3, "losing": 4}[c["verdict"]], -c["n"]))
        curve = x.sort_values("date").groupby("date").pnl.sum().cumsum().round(2)
        sports.append(dict(sport=sp, **_stats(x.pnl.values), cats=cats, curve=[[d, float(v)] for d, v in curve.items()]))
    order = {"nfl": 0, "nhl": 1, "nba": 2, "mlb": 3}
    sports.sort(key=lambda s: order.get(s["sport"], 9))
    o = _stats(df.pnl.values)
    o30 = _stats(df[df.date >= last30].pnl.values)
    return dict(overall=dict(**o, roi30=o30["roi"] if o30 else None, n30=o30["n"] if o30 else 0), sports=sports)


PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Profit</title>__FONTS__<style>__CSS__
.rec{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:6px 0 14px}
.rec div{background:var(--sunk);border-radius:8px;padding:10px 12px}.rec b{display:block;font-family:var(--display);font-size:26px;line-height:1.1}.rec span{font-size:12px;color:var(--muted)}.rec b span{font-size:inherit}.rec b span.rp{color:var(--good)}.rec b span.rn{color:var(--bad)}
.rp{color:var(--good)}.rn{color:var(--bad)}
.vd{display:inline-block;border-radius:999px;padding:1px 9px;font-size:11.5px;font-weight:700;white-space:nowrap}
.vd.proven{background:color-mix(in srgb,var(--good) 20%,transparent);color:var(--good)}.vd.promising{background:color-mix(in srgb,#6CB4FF 22%,transparent);color:#6CB4FF}
.vd.unproven,.vd.too{background:var(--sunk);color:var(--muted)}.vd.losing{background:color-mix(in srgb,var(--bad) 18%,transparent);color:var(--bad)}
.pf td.l{white-space:normal}.pf small{display:block;color:var(--muted);font-size:11.5px}
.spark{width:100%;height:56px;display:block;margin:4px 0 10px}
.sph{display:flex;flex-wrap:wrap;align-items:baseline;gap:6px 16px}.sph h2{margin-right:auto}
@media (max-width:640px){.pf th:nth-child(3),.pf td:nth-child(3),.pf th:nth-child(5),.pf td:nth-child(5){display:none}}
</style></head><body><div class="wrap">
<header><div><div class="eyebrow">All sports</div><h1>Profit</h1></div></header>
<main id="main"></main>
<footer>Everything the site recommends, graded at the price it showed, 1 unit per bet. Prediction-market picks follow the site's rules on the last pregame quote (Kalshi includes its fee); Value tags use the tagged book's price when the tag first appeared, and CLV compares it with that book's last price before the game (positive = you beat the closing line, the quickest sign of a real edge); parlays are every card on the Plays / Parlays tabs, frozen before their first game. Verdicts: Proven = the 95% range of the return is above zero (30+ bets); Losing = below zero; Promising = beats the closing line by a clear margin; Too early = fewer than 30 bets.</footer>
</div><script>
const D = __DATA__;
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
const pc = (v, d = 1) => v == null ? '–' : `<span class="${v >= 0 ? 'rp' : 'rn'}">${v >= 0 ? '+' : ''}${(v * 100).toFixed(d)}%</span>`;
const un = v => v == null ? '–' : `<span class="${v >= 0 ? 'rp' : 'rn'}">${v >= 0 ? '+' : ''}${v.toFixed(1)}u</span>`;
const VL = {proven: 'Proven', promising: 'Promising', unproven: 'Unproven', 'too early': 'Too early', losing: 'Losing'};
const vd = v => `<span class="vd ${v === 'too early' ? 'too' : v}">${VL[v]}</span>`;
function spark(c){
  if (!c || c.length < 2) return '';
  const ys = c.map(p => p[1]), lo = Math.min(0, ...ys), hi = Math.max(0, ...ys), W = 600, H = 56, X = i => i / (c.length - 1) * W, Y = v => H - 4 - (v - lo) / ((hi - lo) || 1) * (H - 8);
  const last = ys[ys.length - 1];
  return `<svg class="spark" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="Units over time"><line x1="0" x2="${W}" y1="${Y(0)}" y2="${Y(0)}" stroke="var(--faint)" stroke-dasharray="4 4"/><polyline fill="none" stroke="${last >= 0 ? 'var(--good)' : 'var(--bad)'}" stroke-width="2.5" points="${c.map((p, i) => X(i) + ',' + Y(p[1])).join(' ')}"/></svg>`;
}
function render(){
  const O = D.overall;
  if (!O){ document.getElementById('main').innerHTML = '<section class="panel"><p class="note">Nothing graded yet.</p></section>'; return; }
  const tiles = `<div class="rec"><div><b>${un(O.units)}</b><span>${O.n} bets · all sports</span></div><div><b>${pc(O.roi)}</b><span>return per bet ± ${O.moe != null ? (O.moe * 100).toFixed(1) : '–'}%</span></div><div><b>${pc(O.roi30)}</b><span>last 30 days (${O.n30} bets)</span></div><div><b>${D.sports.reduce((n, s) => n + s.cats.filter(c => c.verdict === 'proven').length, 0)}</b><span>proven profitable bet types</span></div></div>`;
  const sp = s => `<section class="panel"><div class="sph"><h2>${s.sport.toUpperCase()}</h2><span>${un(s.units)} · ${pc(s.roi)} per bet · ${s.n} bets</span></div>${spark(s.curve)}
    <div class="tw"><table class="t pf"><thead><tr><th class="l">Bet type</th><th>Verdict</th><th>Record</th><th>Return</th><th>Beat close</th><th>Last 30 d</th></tr></thead><tbody>
    ${s.cats.map(c => `<tr><td class="l"><b>${esc(c.cat)}</b><small>${c.n} bets · ${un(c.units)}</small></td><td>${vd(c.verdict)}</td><td>${c.won}-${c.n - c.won}</td>
      <td>${pc(c.roi)}<small>± ${c.moe != null ? (c.moe * 100).toFixed(0) : '–'}%</small></td><td>${c.clv != null ? `${pc(c.clv, 1)}<small>${Math.round(c.clv_beat * 100)}% of ${c.clv_n}</small>` : '<span class="muted">–</span>'}</td>
      <td>${c.n30 ? pc(c.roi30, 0) + `<small>${c.n30} bets</small>` : '<span class="muted">–</span>'}</td></tr>`).join('')}</tbody></table></div></section>`;
  document.getElementById('main').innerHTML = `<section class="panel"><div class="phead"><h2>Overall</h2></div>${tiles}</section>` + D.sports.map(sp).join('');
}
render();
</script></body></html>"""


def page():
    import importlib.util
    import report
    spec = importlib.util.spec_from_file_location("sim_site", os.path.join(HERE, "site.py"))
    S = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(S)
    d = page_data()
    s = (PAGE.replace("__FONTS__", report.FONTS).replace("__CSS__", report.BASE_CSS).replace("__DATA__", json.dumps(d, separators=(",", ":"))))
    src = os.path.join(OUT, "profit_page.html")
    open(src, "w", encoding="utf-8").write(s)
    site = os.environ.get("SIM_SITE") or os.path.join(HERE, "site")
    S.inject(src, os.path.join(site, "profit.html"), 0, "profit")
    o = d.get("overall")
    print("profit page:", f"{o['units']:+.1f}u over {o['n']} bets" if o else "empty")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "page"
    try:
        if cmd == "rows":
            market_rows(sys.argv[2])
        elif cmd == "tags":
            tags()
        elif cmd == "page":
            page()
    except Exception as e:                                                      # noqa: BLE001
        print(f"profit {cmd}: failed ({type(e).__name__}: {e})")
