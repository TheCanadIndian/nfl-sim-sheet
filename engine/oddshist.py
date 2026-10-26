#!/usr/bin/env python3
"""
Intraday odds history for the watchlist charts (owner 2026-10-26: "create a line graph to show how the various book odds
move"). Every markets run appends, for each upcoming game, every over price at every book at every line (props file main
+ alt lines; Kalshi's ladder rungs from live_<sport>.json, ask + 7% fee) and the sharp fair at that line -- only when a
value changed since the last entry. -> site markets/hist_<sport>.json (served beside props_<sport>.json):
    {gid: {"s": start, "p": {"<name>|<kind>|<line>": {"b": {book: [[t, odds], ...]}, "f": [[t, fair], ...]}}}}
t = minutes since the epoch (UTC). Games are dropped 6 h after they start.

    python oddshist.py               # append the current snapshot (markets.yml)
    python oddshist.py backfill 30   # rebuild from the last N hours of committed props snapshots (PC, git history)
"""
import datetime as dt
import json
import os
import subprocess
import sys

import markets as M

SPORTS = ("nba", "nfl", "nhl")
KEEP_AFTER_START = 6 * 60                      # minutes


def fee_odds(ya):
    q = ya + .07 * ya * (1 - ya)
    return round(-100 * q / (1 - q)) if q >= .5 else round(100 * (1 - q) / q)


def snap(P, L, starts):
    """{gid: {key: ({book: odds}, fair)}} for games not started at the snapshot."""
    out = {}
    for gid, G in (P.get("props") or {}).items():
        for nm, kinds in G.items():
            for k, e in kinds.items():
                lines = []
                if e.get("o"):                                        # no line (anytime goal / TD): stored at 0.5
                    pin = e.get("pin") or {}
                    l0 = float(e["l"]) if e.get("l") is not None else .5
                    lines.append((l0, e["o"], pin.get("o") if pin.get("o") is not None and pin.get("l", e.get("l")) == e.get("l") else None))
                for la, a in (e.get("a") or {}).items():
                    if a.get("o"):
                        lines.append((float(la), a["o"], a.get("pin")))
                for l, o, fair in lines:
                    bk = {b: v[0] for b, v in o.items() if isinstance(v, list) and v and v[0] is not None and b != "Pinnacle"}
                    if bk:
                        out.setdefault(str(gid), {})[f"{nm}|{k}|{l:g}"] = (bk, fair)
    for r in (L or {}).get("rows", []):
        if r.get("s") != "kalshi" or r.get("l") is None or not r.get("n") or not r.get("ya") or r.get("yb") is None or not .01 < r["ya"] < .99:
            continue
        if r["ya"] - r["yb"] > .04:
            continue
        key = f"{jsnorm(r['n'])}|{r['k']}|{float(r['l']):g}"
        G = out.setdefault(str(r["g"]), {})
        bk, fair = G.get(key, ({}, None))
        bk = dict(bk, Kalshi=fee_odds(r["ya"]))
        G[key] = (bk, fair)
    return out


def jsnorm(s):
    import re
    import unicodedata
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z ]", "", s)
    s = re.sub(r"\b(jr|sr|ii|iii|iv)\b", "", s)
    return re.sub(r"\s+", " ", s).strip()


def starts_of(sport):
    try:
        S = json.load(open(os.path.join(M.SITE, "markets", "schedule.json"))).get("games", [])
    except (OSError, ValueError):
        S = []
    return {str(g["game"]): g["start"] for g in S if g.get("sport") == sport}


def merge(H, S, t, starts):
    """Append changes; returns the openers seen for the first time this run: [(gid, key, book, odds, fair now, start)]."""
    new = []
    for gid, G in S.items():
        st = starts.get(gid)
        if st and int(dt.datetime.fromisoformat(st).timestamp() // 60) <= t:
            continue                                                  # started: keep the pregame history only
        g = H.setdefault(gid, {"s": st, "p": {}})
        g["s"] = st or g.get("s")
        for key, (bk, fair) in G.items():
            e = g["p"].setdefault(key, {"b": {}, "f": []})
            for b, o in bk.items():
                h = e["b"].setdefault(b, [])
                if not h:
                    new.append((gid, key, b, o, fair, st))
                if not h or h[-1][1] != o:
                    h.append([t, o])
            if fair is not None:
                fr = round(float(fair), 3)
                if not e["f"] or e["f"][-1][1] != fr:
                    e["f"].append([t, fr])
    return new


def prune(H, t):
    for gid in list(H):
        st = H[gid].get("s")
        if st and int(dt.datetime.fromisoformat(st).timestamp() // 60) + KEEP_AFTER_START < t:
            del H[gid]


# ---------------------------------------------------------------- opening lines (owner 2026-10-26: "figure out when books
# first post lines and do a capture then to find super early CLV"; "track this"). Every opener we see (a book's first price
# at a line) is scored once its game starts: the close's sharp fair at that line x the opening payout - 1 (opening CLV), next
# to the same book's last pregame price (the bet-late baseline) -> markets/openclv.json, daily totals per sport x book.
# Openers at the owner's books already 3-40% under the sharp fair when they post -> flags_<sport>.json "openers" -> Profit
# ('<SPORT> Early opener value (experimental)').
OWN = ("Hard Rock", "Novig", "Kalshi")
LAB = {"rec_yds": "rec yds", "rush_yds": "rush yds", "pass_yds": "pass yds", "rec": "receptions", "pts": "points", "reb": "rebounds",
       "ast": "assists", "fg3m": "threes"}
CLV_PATH = os.path.join(M.SITE, "markets", "openclv.json")


def dec(o):
    return 1 + o / 100 if o > 0 else 1 + 100 / abs(o)


def score_openers(sport, H, t, C):
    """Started games not scored yet -> C['days'][date][sport][book] totals."""
    from zoneinfo import ZoneInfo
    for gid, g in H.items():
        st = g.get("s")
        if not st or g.get("scored") or int(dt.datetime.fromisoformat(st).timestamp() // 60) > t:
            continue
        day = dt.datetime.fromisoformat(st).astimezone(ZoneInfo("America/New_York")).date().isoformat()
        for key, e in g["p"].items():
            if not e["f"]:
                continue
            fc = e["f"][-1][1]                                        # the close: last sharp fair before the start
            for b, h in e["b"].items():
                o0, t0, o1 = h[0][1], h[0][0], h[-1][1]
                c0, c1 = fc * dec(o0) - 1, fc * dec(o1) - 1
                if abs(c0) > .6 or abs(c1) > .6:
                    continue                                          # stale / broken quotes
                f0 = next((f for tt, f in reversed(e["f"]) if tt <= t0), None)
                A = C.setdefault("days", {}).setdefault(day, {}).setdefault(sport, {}).setdefault(b, dict(n=0, clv=0.0, beat=0, late=0.0, val_n=0, val_clv=0.0, val_beat=0, hrs=0.0))
                A["n"] += 1; A["clv"] += c0; A["beat"] += c0 > 0; A["late"] += c1
                A["hrs"] += (int(dt.datetime.fromisoformat(st).timestamp() // 60) - t0) / 60
                if f0 is not None and f0 * dec(o0) - 1 >= .03:
                    A["val_n"] += 1; A["val_clv"] += c0; A["val_beat"] += c0 > 0
        g["scored"] = True


def path(sport):
    return os.path.join(M.SITE, "markets", f"hist_{sport}.json")


def update():
    t = int(dt.datetime.now(dt.timezone.utc).timestamp() // 60)
    for sport in SPORTS:
        try:
            P = json.load(open(os.path.join(M.SITE, "markets", f"props_{sport}.json")))
        except (OSError, ValueError):
            continue
        try:
            L = json.load(open(os.path.join(M.SITE, "markets", f"live_{sport}.json")))
        except (OSError, ValueError):
            L = {}
        try:
            H = json.load(open(path(sport)))
        except (OSError, ValueError):
            H = {}
        starts = starts_of(sport)
        new = merge(H, snap(P, L, starts), t, starts)
        try:
            C = json.load(open(CLV_PATH))
        except (OSError, ValueError):
            C = {}
        score_openers(sport, H, t, C)
        json.dump(C, open(CLV_PATH, "w"), separators=(",", ":"))
        prune(H, t)
        json.dump(H, open(path(sport), "w"), separators=(",", ":"))
        # early-value openers at the owner's books -> flags (profit.tags logs and grades each first sighting)
        op = []
        for gid, key, b, o, fair, st in new:
            nm, k, l = key.rsplit("|", 2)
            if b in OWN and k in LAB and fair is not None and .03 <= fair * dec(o) - 1 <= .40:
                op.append(dict(g=gid, n=nm, k=k, l=float(l), b=f"over {float(l):g} {LAB[k]}", bk=b, o=o, fair=round(float(fair), 4), start=st))
        ff = os.path.join(M.SITE, "markets", f"flags_{sport}.json")
        try:
            FL = json.load(open(ff))
            FL["openers"] = op
            json.dump(FL, open(ff, "w"), separators=(",", ":"))
        except (OSError, ValueError):
            pass
        print(f"odds history {sport}: {len(H)} games, {sum(len(g['p']) for g in H.values())} lines, {len(new)} new openers ({len(op)} early value)")


def backfill(hours):
    """Rebuild from the committed snapshots of the last `hours` (run on the PC: the cloud checkout has no history)."""
    git = lambda *a: subprocess.run(["git", "-C", M.SITE, *a], capture_output=True, text=True, encoding="utf-8").stdout
    since = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hours)).isoformat()
    for sport in SPORTS:
        starts = starts_of(sport)
        H = {}
        commits = [l.split() for l in git("log", f"--since={since}", "--format=%H %cI", "--", f"markets/props_{sport}.json").split("\n") if l.strip()][::-1]
        for h, ts in commits:
            t = int(dt.datetime.fromisoformat(ts).timestamp() // 60)
            try:
                P = json.loads(git("show", f"{h}:markets/props_{sport}.json") or "{}")
                L = json.loads(git("show", f"{h}:markets/live_{sport}.json") or "{}")
            except ValueError:
                continue
            merge(H, snap(P, L, starts), t, starts)
        now = int(dt.datetime.now(dt.timezone.utc).timestamp() // 60)
        prune(H, now)
        json.dump(H, open(path(sport), "w"), separators=(",", ":"))
        print(f"odds history {sport}: backfilled {len(commits)} snapshots -> {len(H)} games, {sum(len(g['p']) for g in H.values())} lines, "
              f"{os.path.getsize(path(sport)) // 1024} KB")



# ---------------------------------------------------------------- the Opening lines page (markets.yml, after update)
OPEN_PAGE = r"""<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Opening Lines</title>
__FONTS__<style>
__CSS__
.good{color:var(--good)}.bad{color:var(--bad)}
</style>
<div class="wrap">
  <header><div><div class="eyebrow" id="sub"></div><h1>Opening lines</h1></div></header>
  <main id="main"></main>
  <footer>Every over price is recorded from the first time a book posts it (the site checks about every 15 minutes, and checks games it has no prices for every hour until a book posts). Once the game starts, each opener is scored against the closing sharp fair at the same line: the fair × the opening payout − 1 (positive = the opening price beat the close). “Late” is the same book’s last pregame price, scored the same way. Early-value openers (already 3%+ under the sharp fair when posted) are also graded as bets on the Profit page.</footer>
</div>
<script type="application/json" id="data">__DATA__</script>
<script>
const D = JSON.parse(document.getElementById('data').textContent);
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const pc = v => v == null ? '–' : `<span class="${v > 0 ? 'good' : v < -0.05 ? 'bad' : ''}">${v >= 0 ? '+' : '−'}${Math.abs(v * 100).toFixed(1)}%</span>`;
document.getElementById('sub').textContent = `Built ${D.generated} · ${D.days} days scored`;
const SP = {nba: 'NBA', nfl: 'NFL', nhl: 'NHL'};
let h = '';
for (const sp of ['nfl', 'nba', 'nhl']){ const R = D.rows.filter(r => r.sport === sp); if (!R.length) continue;
  h += `<section class="panel"><div class="phead"><h2>${SP[sp]}</h2><span class="small muted">${R.reduce((a, r) => a + r.n, 0)} openers scored</span></div><div class="tw"><table class="t"><thead><tr><th class="l">Book</th><th>Openers</th><th>Open CLV</th><th>Beat close</th><th>Late CLV</th><th>Value openers</th><th>Their CLV</th><th>Their beat</th><th>Posted h early</th></tr></thead><tbody>
  ${R.map(r => `<tr><td class="l">${esc(r.book)}</td><td>${r.n}</td><td>${pc(r.clv)}</td><td>${Math.round(r.beat * 100)}%</td><td>${pc(r.late)}</td><td>${r.val_n}</td><td>${r.val_n ? pc(r.val_clv) : '–'}</td><td>${r.val_n ? Math.round(r.val_beat * 100) + '%' : '–'}</td><td>${r.hrs.toFixed(1)}</td></tr>`).join('')}</tbody></table></div></section>`; }
document.getElementById('main').innerHTML = h || '<section class="panel"><p class="note" style="margin:0">No games scored yet: openers are scored as each game starts.</p></section>';
</script>
"""


def page():
    try:
        C = json.load(open(CLV_PATH))
    except (OSError, ValueError):
        return
    agg = {}
    for day, v in C.get("days", {}).items():
        for sp, bks in v.items():
            for b, a in bks.items():
                t = agg.setdefault((sp, b), dict(n=0, clv=0.0, beat=0, late=0.0, val_n=0, val_clv=0.0, val_beat=0, hrs=0.0))
                for k_ in t:
                    t[k_] += a.get(k_, 0)
    rows = []
    for (sp, b), t in agg.items():
        if t["n"] < 20:
            continue
        rows.append(dict(sport=sp, book=b, n=t["n"], clv=t["clv"] / t["n"], beat=t["beat"] / t["n"], late=t["late"] / t["n"], hrs=t["hrs"] / t["n"],
                         val_n=t["val_n"], val_clv=(t["val_clv"] / t["val_n"]) if t["val_n"] else None, val_beat=(t["val_beat"] / t["val_n"]) if t["val_n"] else None))
    own = ("Hard Rock", "Novig", "Kalshi")
    rows.sort(key=lambda r: (r["sport"], r["book"] not in own, -r["n"]))
    data = dict(generated=dt.datetime.now().strftime("%b %d, %Y %I:%M %p").replace(" 0", " "), days=len(C.get("days", {})), rows=rows)
    import importlib.util
    import report
    src = os.path.join(os.path.dirname(os.path.abspath(__file__)), "projections", "openers.html")
    os.makedirs(os.path.dirname(src), exist_ok=True)
    open(src, "w", encoding="utf-8").write(OPEN_PAGE.replace("__FONTS__", report.FONTS).replace("__CSS__", report.BASE_CSS)
                                           .replace("__DATA__", json.dumps(data, separators=(",", ":")).replace("</", "<" + chr(92) + "/")))
    spec = importlib.util.spec_from_file_location("sim_site", os.path.join(os.path.dirname(os.path.abspath(__file__)), "site.py"))
    S = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(S)
    S.inject(src, os.path.join(S.SITE, "openers.html"), 0, "openers")
    print(f"opening lines page: {len(rows)} sport x book rows")


if __name__ == "__main__":
    if sys.argv[1:2] == ["backfill"]:
        backfill(float(sys.argv[2]) if len(sys.argv) > 2 else 30)
    else:
        update()
        try:
            page()
        except Exception as e:                                         # noqa: BLE001 -- the history matters more than the page
            print(f"opening lines page: skipped ({type(e).__name__}: {e})")
