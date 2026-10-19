#!/usr/bin/env python3
"""
Parlay record: freeze every parlay the NHL / NFL pages show, grade them, and publish <sport>/parlays.html
(the "Parlays" tab of each sport's Results page).

    python parlays.py freeze     # load each sport's page headless (?freeze=1, priced at Novig / Kalshi / Hard Rock),
                                 # keep the last version before each day's first game
    python parlays.py grade      # settle legs from box scores (NHL API; ESPN for the NFL)
    python parlays.py page       # write the Parlays pages
    python parlays.py all

Files: parlays/<sport>/<date>.json (date = Eastern date of a parlay's earliest leg). Each parlay: card, tier, legs,
p (our blended chance when shown), d (decimal payout at the prices shown; null when unpriced), result.
Leg results: W, L, P (push), V (void: the player didn't play). A parlay loses on any losing leg; void and pushed legs
drop out (as at the books). NFL box scores list only players with a stat, so a missing NFL player counts as zero.
"""
import datetime as dt
import glob
import html
import json
import math
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import unicodedata
from zoneinfo import ZoneInfo

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
SITE = os.environ.get("SIM_SITE") or (os.path.join(HERE, "site") if os.path.isdir(os.path.join(HERE, "site")) else os.path.dirname(HERE))
OUT = os.path.join(HERE, "parlays")
ET = ZoneInfo("America/New_York")
PAGES = {"nhl": "plays", "nfl": "parlays", "nba": "parlays"}   # sport -> the page tab that renders every parlay card
NHL_API = "https://api-web.nhle.com/v1"
ESPN = "https://site.api.espn.com/apis/site/v2/sports/football/nfl"
ESPN_NBA = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"
ESPN_TEAM = {"LA": "LAR", "WAS": "WSH"}               # our codes -> ESPN's where they differ


def jsnorm(s):
    """Same as mkt.js norm()."""
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z ]", "", s)
    s = re.sub(r"\b(jr|sr|ii|iii|iv)\b", "", s)
    return re.sub(r"\s+", " ", s).strip()


def when(ts):
    """A start time as an aware datetime; times without a zone (the NFL page's) are Eastern."""
    t = dt.datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=ET)


def et_date(ts):
    return when(ts).astimezone(ET).date().isoformat()


def dec(o):
    o = float(o)
    return 1 + (o / 100 if o > 0 else 100 / -o)


# ---------------------------------------------------------------- freeze
def browser():
    for b in (os.environ.get("CHROME"), shutil.which("google-chrome"), shutil.which("chromium"), shutil.which("chromium-browser"),
              r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe", r"C:\Program Files\Google\Chrome\Application\chrome.exe"):
        if b and os.path.exists(b):
            return b
    return None


def snapshot(sport, port, exe):
    url = f"http://127.0.0.1:{port}/{sport}/index.html?freeze=1#{PAGES[sport]}"
    import tempfile
    fn = os.path.join(tempfile.gettempdir(), f"parlays_{sport}_dom.html")     # a real file: Edge on Windows writes --dump-dom nowhere else
    args = [exe, "--headless=new", "--disable-gpu", "--virtual-time-budget=25000", "--dump-dom", url]
    if os.name != "nt":
        args.insert(1, "--no-sandbox")                  # GitHub's Linux runners
    if os.name == "nt":                                 # Edge's launcher only honours cmd's redirection
        subprocess.run(f'cmd /c ""{exe}" {" ".join(args[1:-1])} "{url}" > "{fn}" 2>nul"', shell=True, timeout=180)
    else:
        with open(fn, "wb") as f:
            subprocess.run(args, stdout=f, stderr=subprocess.DEVNULL, timeout=180)
    dom = open(fn, encoding="utf-8", errors="ignore").read()
    m = re.search(r'<script type="application/json" id="frozen-parlays">(.*?)</script>', dom, re.S)
    return json.loads(html.unescape(m.group(1))) if m else None


def freeze():
    exe = browser()
    if not exe:
        print("parlays: no headless browser found; skipped")
        return
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    srv = subprocess.Popen([sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1", "--directory", SITE],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        time.sleep(1.5)
        now = dt.datetime.now(dt.timezone.utc)
        for sport in PAGES:
            if not os.path.exists(os.path.join(SITE, sport, "index.html")):
                continue
            snap = snapshot(sport, port, exe)
            if not snap:
                print(f"parlays {sport}: page gave no parlays (prices not loaded?)")
                continue
            by = {}
            for p in snap["parlays"]:
                starts = [when(L["start"]) for L in p["legs"] if L.get("start")]
                if not starts:
                    continue
                p["first"] = min(starts).astimezone(dt.timezone.utc).isoformat()
                by.setdefault(et_date(p["first"]), []).append(p)
            # Each parlay locks at its own first game: what counts is the last version the page showed before that
            # game started (cards keep updating through the day, and drop games once they start). Parlays the page
            # replaced before their first game don't count.
            today = dt.datetime.now(ET).date()
            dates = set(by) | {os.path.basename(f)[:10] for f in glob.glob(os.path.join(OUT, sport, "*.json"))
                               if dt.date.fromisoformat(os.path.basename(f)[:10]) >= today - dt.timedelta(days=1)}
            for date in sorted(dates):
                fp = os.path.join(OUT, sport, f"{date}.json")
                old = json.load(open(fp)) if os.path.exists(fp) else None
                if old and old.get("graded"):
                    continue
                locked = [dict(p, locked=True) for p in (old["parlays"] if old else []) if p.get("locked") or when(p["first"]) <= now]
                pending = [p for p in by.get(date, []) if when(p["first"]) > now]
                ps = pick_card(sport, locked + pending, now)
                if not ps or (old and ps == old["parlays"]):
                    continue
                os.makedirs(os.path.dirname(fp), exist_ok=True)
                json.dump(dict(sport=sport, date=date, lock=min(p["first"] for p in ps), frozen_at=snap["generated"], books=snap["books"],
                               parlays=ps, graded=False), open(fp, "w"), indent=0)
                print(f"parlays {sport} {date}: {len(locked)} locked, {len(pending)} still updating")
    finally:
        srv.terminate()


# ---------------------------------------------------------------- the daily card
# Not every parlay: 4 a day per sport, $5 each (owner's stake: half his $10 straight unit). From the graded record
# (335 parlays to 2026-10-18): 5+ legs / 15x+ went 0-101 vs ~10 expected, 2-4 legs at 3-15x matched our chances, claimed
# edges over 50% went 0-33 (the model, not the market, was wrong), NHL parlays hit ~70% of our chance, and the same leg sat
# in up to 13 parlays a day. So: 3 core picks + 1 all-day longshot (the owner likes them), every leg priced at his books,
# legs from different games (books' payouts multiply only then), edge 5-40% on a market-anchored chance, a leg in at most
# 2 picks. Same-game cards are out: no book prices them as the product of their legs.
CARD_N, CARD_STAKE, CARD_CORE = 4, 5, 3
CARD_BOOKS = {"Novig", "Kalshi", "Hard Rock"}           # the owner's books: every leg must be bettable there
# NHL parlays won 19 vs 27 expected (z about -1.6, not settled): the chance shown is cut 15% (half-way), but picking uses
# the anchored edge itself; cutting 25% left almost no NHL parlay with an edge
CARD_DISCOUNT = {"nhl": .85}


def _legkey(L):
    return f"{L.get('gid')}|{L.get('name') or L.get('lab')}|{L.get('kind')}|{L.get('side')}|{L.get('line')}"


def card_slot(sport, p):
    """('core' | 'longshot' | None, edge on the parlay's market-anchored chance) for one parlay."""
    legs, d = p.get("legs") or [], p.get("d")
    if not d or p.get("p") is None or str(p.get("card", "")).startswith("Same-game") or len(legs) < 2:
        return None, None
    if any(L.get("price") is None or L.get("book") not in CARD_BOOKS for L in legs) or len({str(L.get("gid")) for L in legs}) < len(legs):
        return None, None
    ev = p["p"] * d - 1
    if not .05 <= ev <= .40:
        return None, ev
    if len(legs) <= 4 and 3 <= d <= 15:
        return "core", ev
    if len(legs) <= 5 and 15 < d <= 100:
        return "longshot", ev
    return None, ev


def pick_card(sport, ps, now):
    """Mark the day's card (p['pick'] = 'core' / 'longshot'). Picks whose first game started stay; the rest are chosen
    again from what the page shows now (best edge first, a leg in at most 2 picks)."""
    keep = [p for p in ps if p.get("pick") and when(p["first"]) <= now]
    for p in ps:
        if p.get("pick") and not any(p is k for k in keep):
            p.pop("pick", None)
    used, sets = {}, {tuple(sorted(_legkey(L) for L in p["legs"])) for p in keep}
    for p in keep:
        for L in p["legs"]:
            used[_legkey(L)] = used.get(_legkey(L), 0) + 1
    cands = []
    for p in ps:
        if p.get("pick") or when(p["first"]) <= now:
            continue
        slot, ev = card_slot(sport, p)
        if slot:
            cands.append((slot, ev, p))
    cands.sort(key=lambda c: -c[1])
    count = lambda f: sum(1 for p in ps if p.get("pick") and f(p["pick"]))

    def take(ok, cap):
        for slot, ev, p in cands:
            if cap() <= 0:
                return
            k = tuple(sorted(_legkey(L) for L in p["legs"]))
            if not ok(slot) or p.get("pick") or k in sets or any(used.get(_legkey(L), 0) >= 2 for L in p["legs"]):
                continue
            p["pick"] = slot
            sets.add(k)
            for L in p["legs"]:
                used[_legkey(L)] = used.get(_legkey(L), 0) + 1
    take(lambda x: x == "longshot", lambda: 1 - count(lambda x: x == "longshot"))
    take(lambda x: x == "core", lambda: CARD_CORE - count(lambda x: x == "core"))
    take(lambda x: x == "core", lambda: CARD_N - count(lambda x: True))     # no longshot today: a 4th core pick
    return ps


def write_card(sport):
    """markets/card_<sport>.json for the pages: today's (or the next day's) picks and the card's record in dollars."""
    today = dt.datetime.now(ET).date().isoformat()
    days, rec = [], dict(n=0, w=0, l=0, net=0.0)
    for fp in sorted(glob.glob(os.path.join(OUT, sport, "*.json"))):
        try:
            d = json.load(open(fp))
        except (OSError, ValueError):
            continue
        picks = [p for p in d.get("parlays", []) if p.get("pick")]
        for p in picks:
            if p.get("res") in ("W", "L"):
                win = p["res"] == "W"
                rec["n"] += 1
                rec["w"] += win
                rec["l"] += not win
                rec["net"] += CARD_STAKE * (((p.get("paid") or p["d"]) - 1) if win else -1)
        if picks:
            days.append((d["date"], picks))
    show = next(((dd, ps) for dd, ps in days if dd >= today), days[-1] if days else (None, []))
    out = dict(sport=sport, stake=CARD_STAKE, date=show[0], updated=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
               record=dict(rec, net=round(rec["net"], 2)),
               picks=[dict(slot=p["pick"], card=p["card"], tier=p.get("tier"), p=round(p["p"] * CARD_DISCOUNT.get(sport, 1), 4), d=p["d"],
                           first=p["first"], res=p.get("res"), paid=p.get("paid"),
                           legs=[dict(lab=L.get("lab"), price=L.get("price"), book=L.get("book"), p=L.get("p"), res=L.get("res")) for L in p["legs"]])
                      for p in sorted(show[1], key=lambda p: (p["pick"] != "core", p["first"]))])
    os.makedirs(os.path.join(SITE, "markets"), exist_ok=True)
    json.dump(out, open(os.path.join(SITE, "markets", f"card_{sport}.json"), "w"), separators=(",", ":"))
    return out


# ---------------------------------------------------------------- grade
_cache = {}


def get(url, **kw):
    if url not in _cache:
        for i in range(4):                               # the NHL API sometimes answers empty or rate-limits: back off and retry
            try:
                r = requests.get(url, timeout=30, **kw)
                if r.ok and r.content:
                    _cache[url] = r.json()
                    return _cache[url]
            except (requests.RequestException, ValueError):
                pass
            time.sleep(2 * (i + 1))
        return None                                       # not cached: tried again next run
    return _cache[url]


def nhl_game(gid):
    """{'final', 'home', 'away', 'players': {name: {'goals', 'sog'}}} or None if not final."""
    box = get(f"{NHL_API}/gamecenter/{gid}/boxscore")
    if not box or box.get("gameState") not in ("OFF", "FINAL"):
        return None
    pbp = get(f"{NHL_API}/gamecenter/{gid}/play-by-play") or {}
    names = {r["playerId"]: jsnorm(f"{r['firstName']['default']} {r['lastName']['default']}") for r in pbp.get("rosterSpots", [])}
    pl = {}
    for side in ("awayTeam", "homeTeam"):
        for grp in ("forwards", "defense"):
            for x in box.get("playerByGameStats", {}).get(side, {}).get(grp, []):
                nm = names.get(x["playerId"])
                if nm:
                    pl[nm] = dict(goals=x.get("goals", 0), sog=x.get("sog", x.get("shots", 0)))
    return dict(home=box["homeTeam"]["score"], away=box["awayTeam"]["score"], players=pl, zero_missing=False)


def nfl_game(gid, date):
    """gid like 2026_06_SEA_DEN (away_home)."""
    m = re.match(r"\d{4}_\d{2}_([A-Z]+)_([A-Z]+)$", str(gid))
    if not m:
        return None
    away, home = (ESPN_TEAM.get(t, t) for t in m.groups())
    sb = get(f"{ESPN}/scoreboard?dates={date.replace('-', '')}")
    ev = None
    for e in (sb or {}).get("events", []):
        c = e["competitions"][0]
        t = {x["homeAway"]: x["team"]["abbreviation"] for x in c["competitors"]}
        if t.get("home") == home and t.get("away") == away:
            ev = e
    if not ev or not ev["status"]["type"].get("completed"):
        return None
    c = ev["competitions"][0]
    sc = {x["homeAway"]: int(x["score"]) for x in c["competitors"]}
    summ = get(f"{ESPN}/summary?event={ev['id']}") or {}
    pl = {}
    want = {"passing": {"passingYards": "pass_yds"}, "rushing": {"rushingYards": "rush_yds", "rushingTouchdowns": "rush_td"},
            "receiving": {"receptions": "rec", "receivingYards": "rec_yds", "receivingTouchdowns": "rec_td"}}
    for team in summ.get("boxscore", {}).get("players", []):
        for cat in team.get("statistics", []):
            keys = cat.get("keys") or []
            for a in cat.get("athletes", []):
                nm = jsnorm(a["athlete"]["displayName"])
                d = pl.setdefault(nm, {})
                for k, ours in want.get(cat.get("name"), {}).items():
                    if k in keys:
                        try:
                            d[ours] = float(str(a["stats"][keys.index(k)]).replace(",", ""))
                        except (ValueError, IndexError):
                            pass
    return dict(home=sc["home"], away=sc["away"], players=pl, zero_missing=True)


def nba_game(gid):
    """gid = ESPN event id. Players who didn't play aren't in the box (their legs void)."""
    s = get(f"{ESPN_NBA}/summary?event={gid}")
    c = ((s or {}).get("header") or {}).get("competitions", [{}])[0]
    if not c or not ((c.get("status") or {}).get("type") or {}).get("completed"):
        return None
    sc = {x["homeAway"]: int(x["score"]) for x in c["competitors"]}
    pl = {}
    for team in (s.get("boxscore") or {}).get("players", []):
        for cat in team.get("statistics", []):
            keys = cat.get("keys") or []
            for a in cat.get("athletes", []):
                v = a.get("stats") or []
                if len(v) != len(keys):
                    continue                                    # did not play
                x = dict(zip(keys, v))
                try:
                    pl[jsnorm(a["athlete"]["displayName"])] = dict(
                        pts=float(x.get("points", 0)), reb=float(x.get("rebounds", 0)), ast=float(x.get("assists", 0)),
                        fg3m=float(str(x.get("threePointFieldGoalsMade-threePointFieldGoalsAttempted", "0-0")).split("-")[0]))
                except ValueError:
                    pass
    return dict(home=sc["home"], away=sc["away"], players=pl, zero_missing=False)


def game_for(sport, gid, start):
    return nhl_game(gid) if sport == "nhl" else nba_game(gid) if sport == "nba" else nfl_game(gid, et_date(start))


def settle(L, g):
    if L["kind"] in ("ml", "spread", "total"):
        h, a = g["home"], g["away"]
        if L["kind"] == "ml":
            return "W" if (h > a) == (L["side"] == "home") else "L"
        if L["kind"] == "spread":
            m = (h - a if L["side"] == "home" else a - h) + float(L["line"])
            return "P" if m == 0 else "W" if m > 0 else "L"
        t = h + a
        return "P" if t == float(L["line"]) else "W" if (t > float(L["line"])) == (L["side"] == "over") else "L"
    s = g["players"].get(L.get("name") or "")
    if s is None:
        if not g["zero_missing"]:
            return "V"                                  # didn't dress (NHL box scores list every dressed skater)
        s = {}
    key = {"goal": "goals", "sog": "sog"}.get(L["kind"], L["kind"])
    v = (s.get("rush_td", 0) + s.get("rec_td", 0)) if L["kind"] == "td" else s.get(key, 0)
    line = float(L["line"] if L.get("line") is not None else .5)
    if v == line:
        return "P"
    return "W" if (v > line) == (L["side"] == "o") else "L"


def grade():
    today = dt.datetime.now(ET).date()
    for fp in sorted(glob.glob(os.path.join(OUT, "*", "*.json"))):
        d = json.load(open(fp))
        if d.get("graded") or dt.date.fromisoformat(d["date"]) > today:
            continue
        sport, games, pending = d["sport"], {}, 0
        for p in d["parlays"]:
            for L in p["legs"]:
                if L.get("res") in ("W", "L", "P", "V"):
                    continue
                gid = L.get("gid")
                if gid not in games:
                    try:
                        games[gid] = game_for(sport, gid, L["start"])
                    except Exception as e:                                     # noqa: BLE001
                        print(f"parlays {sport}: {gid} not graded ({type(e).__name__})")
                        games[gid] = None
                g = games[gid]
                if g is None:
                    pending += 1
                    continue
                L["res"] = settle(L, g)
            rs = [L.get("res") for L in p["legs"]]
            if "L" in rs:
                p["res"] = "L"
            elif None in rs:
                p["res"] = None
            elif "W" in rs:
                p["res"] = "W"
                pr = [L.get("price") for L in p["legs"] if L.get("res") == "W"]
                full = all(L.get("res") == "W" for L in p["legs"])
                if all(x is not None and str(x) not in ("", "None") for x in pr) and all(abs(float(x)) >= 100 for x in pr):
                    p["paid"] = round(float(math.prod(dec(x) for x in pr)), 3)
                elif full and p.get("d"):
                    p["paid"] = p["d"]
            else:
                p["res"] = "V"
        old = (today - dt.date.fromisoformat(d["date"])).days >= 3
        if pending == 0 or old:                                             # 3 days on: anything unsettled voids
            for p in d["parlays"]:
                if p.get("res") is None:
                    p["res"] = "V"
            d["graded"] = True
        json.dump(d, open(fp, "w"), indent=0)
        n = sum(1 for p in d["parlays"] if p.get("res") in ("W", "L"))
        print(f"parlays {sport} {d['date']}: {sum(p.get('res') == 'W' for p in d['parlays'])}-{sum(p.get('res') == 'L' for p in d['parlays'])} "
              f"({n} settled, {pending} legs waiting)")


# ---------------------------------------------------------------- page
def summarize(ps):
    s = [p for p in ps if p.get("res") in ("W", "L")]
    w = sum(p["res"] == "W" for p in s)
    priced = [p for p in s if p.get("d") or p.get("paid")]
    roi = (sum((p.get("paid") or p["d"]) - 1 if p["res"] == "W" else -1 for p in priced) / len(priced)) if priced else None
    return dict(n=len(s), w=w, l=len(s) - w, exp=(sum(p["p"] for p in s) / len(s)) if s else None, roi=roi,
                open=sum(1 for p in ps if p.get("res") is None))


def group_tier(p):
    """Summary rows: 15x pairs as one card, cross-game parlays across slots, same-game parlays by size (script names stay on each parlay)."""
    if p["card"].startswith("2-leg pairs"):
        return "15x+"
    if p["card"] == "Cross-game parlays":
        return "every slot"
    if p["card"].startswith("Same-game"):
        return p["tier"].split(" · ")[-1]
    return p["tier"]


def page_data(sport):
    days = []
    for fp in sorted(glob.glob(os.path.join(OUT, sport, "*.json")), reverse=True):
        d = json.load(open(fp))
        days.append(dict(date=d["date"], graded=d.get("graded"), parlays=[dict(card=p["card"], tier=p["tier"], p=p["p"], d=p.get("d"), rebuilt=p.get("rebuilt"),
                     paid=p.get("paid"), res=p.get("res"), legs=[dict(lab=L.get("lab"), p=L.get("p"), price=L.get("price"), book=L.get("book"),
                     res=L.get("res")) for L in p["legs"]]) for p in d["parlays"]]))
    allp = [p for d in days for p in d["parlays"]]
    cards = {}
    for p in allp:                                      # the 15x pairs are one card (their tier is just the slate)
        tier = group_tier(p)
        cards.setdefault((p["card"], tier), []).append(p)
    return dict(sport=sport, overall=summarize(allp),                   # no build time: the page only changes when the record does
                cards=[dict(card=c, tier=t, **summarize(ps)) for (c, t), ps in sorted(cards.items())],
                days=[dict(date=d["date"], graded=d["graded"], rebuilt=any(p.get("rebuilt") for p in d["parlays"]), **summarize(d["parlays"]),
               groups=[dict(card=c, tier=t, **summarize(ps), parlays=ps) for (c, t), ps in sorted(
                   {k: [p for p in d["parlays"] if (p["card"], group_tier(p)) == k] for k in {(p["card"], group_tier(p)) for p in d["parlays"]}}.items())])
          for d in days])


PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>__TITLE__</title>__FONTS__<style>__CSS__
.rec{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px;margin:6px 0 14px}
.rec div{background:var(--sunk);border-radius:8px;padding:10px 12px}.rec b{display:block;font-family:var(--display);font-size:26px;line-height:1.1}.rec span{font-size:12px;color:var(--muted)}.rec b span{font-size:inherit}.rec b span.rp{color:var(--good)}.rec b span.rn{color:var(--bad)}.rb{border:1px solid var(--faint);border-radius:4px;padding:0 5px;font-size:10.5px;color:var(--muted);margin-left:4px;white-space:nowrap}
.pt2 td.l{white-space:normal}.pt2 .tier{color:var(--muted);font-size:12px;display:block}
.rp{color:var(--good);font-weight:700}.rn{color:var(--bad);font-weight:700}
details.day{border:1px solid var(--faint);border-radius:10px;margin:8px 0;background:var(--surface)}
details.day>summary{cursor:pointer;list-style:none;display:flex;flex-wrap:wrap;align-items:baseline;gap:4px 14px;padding:12px 14px}
details.day>summary::-webkit-details-marker{display:none}
details.day>summary b{font-family:var(--display);font-size:18px;letter-spacing:.02em;text-transform:uppercase;margin-right:auto}
details.day>summary .chev{color:var(--muted);transition:transform .15s}details.day[open]>summary .chev{transform:rotate(180deg)}
details.grp{border-top:1px solid var(--faint)}details.grp>summary{cursor:pointer;list-style:none;display:flex;flex-wrap:wrap;align-items:baseline;gap:2px 12px;padding:10px 14px;font-size:14px}
details.grp>summary::-webkit-details-marker{display:none}details.grp>summary .gn{margin-right:auto}details.grp>summary .chev{color:var(--muted);transition:transform .15s}details.grp[open]>summary .chev{transform:rotate(180deg)}
details.grp .pl{padding-left:24px;background:color-mix(in srgb,var(--sunk) 50%,transparent)}
.pl{border-top:1px solid var(--faint);padding:10px 14px;display:grid;grid-template-columns:minmax(0,1fr) auto;gap:4px 12px}
.pl .hd{font-size:12px;color:var(--muted)}.pl ul{list-style:none;margin:4px 0 0;padding:0;display:grid;gap:2px;font-size:13.5px}
.pl li .m{display:inline-block;width:18px;font-weight:800}.pl li.W .m{color:var(--good)}.pl li.L .m{color:var(--bad)}.pl li.V .m,.pl li.P .m,.pl li.N .m{color:var(--muted)}
.pl .out{text-align:right;font-size:13px;white-space:nowrap}.pl .out b{display:block;font-family:var(--display);font-size:18px}
.badge{display:inline-block;border-radius:4px;padding:0 6px;font-size:11px;font-weight:700;letter-spacing:.04em}
.badge.W{background:color-mix(in srgb,var(--good) 18%,transparent);color:var(--good)}.badge.L{background:color-mix(in srgb,var(--bad) 16%,transparent);color:var(--bad)}.badge.O,.badge.V{background:var(--sunk);color:var(--muted)}
</style></head><body><div class="wrap">
<header><div><div class="eyebrow">Results</div><h1>__H1__</h1></div></header>
<main id="main"></main>
<footer>Every parlay the __SPORT__ page showed, frozen at the last price check before each day's first game (priced at Novig, Kalshi and Hard Rock), then graded from the box scores. A parlay loses on any losing leg; legs on players who didn't play, and pushes, drop out as they do at the books. Expected = our chance when shown. Return = flat 1-unit stakes at the prices shown (cards without book prices count at their fair payout). NHL Oct 9-13: the 15x pairs are as saved; the anytime-goal parlays and longshot two-mans are rebuilt with the same rules from that night's projections and Kalshi prices (marked "rebuilt"); the payout-tier and shots + moneyline cards weren't saved before Oct 14.</footer>
</div><script>
const D = __DATA__;
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
const pc = v => v == null ? '–' : (v * 100).toFixed(1) + '%';
const roi = v => v == null ? '–' : `<span class="${v >= 0 ? 'rp' : 'rn'}">${v >= 0 ? '+' : ''}${(v * 100).toFixed(0)}%</span>`;
const am = o => o == null ? '' : (+o > 0 ? '+' : '') + o;
const day = s => new Date(s + 'T12:00:00').toLocaleDateString([], {weekday: 'short', month: 'short', day: 'numeric'});
function legLine(L){
  const m = {W: '✓', L: '✗', P: '–', V: '∅'}[L.res] || '·';
  const price = L.price != null && Math.abs(+L.price) >= 100 ? ` · ${am(L.price)}${L.book ? ' ' + esc(L.book) : ''}` : '';
  return `<li class="${L.res || 'N'}"><span class="m">${m}</span>${esc(L.lab)} <span class="small muted">${pc(L.p)}${price}${L.res === 'V' ? ' · void (didn’t play)' : L.res === 'P' ? ' · push' : ''}</span></li>`;
}
function parlay(p){
  const st = p.res === 'W' ? '<span class="badge W">WON</span>' : p.res === 'L' ? '<span class="badge L">LOST</span>' : p.res === 'V' ? '<span class="badge V">VOID</span>' : '<span class="badge O">OPEN</span>';
  const pay = p.paid || p.d;
  return `<div class="pl"><div><div class="hd">${esc(p.card)} · ${esc(p.tier)}${p.rebuilt ? ` <span class="rb" title="Saved before tracking started: rebuilt with the page’s rules from that night’s projections and ${esc(p.rebuilt)}">rebuilt · ${esc(p.rebuilt)}</span>` : ''}</div><ul>${p.legs.map(legLine).join('')}</ul></div>
    <div class="out">${st}<b>${pc(p.p)}</b>${pay ? pay.toFixed(1) + 'x' : 'fair only'}</div></div>`;
}
function render(){
  const O = D.overall;
  const tiles = `<div class="rec"><div><b>${O.w}-${O.l}</b><span>record (${O.n} settled${O.open ? `, ${O.open} open` : ''})</span></div>
    <div><b>${O.n ? pc(O.w / O.n) : '–'}</b><span>hit rate</span></div><div><b>${pc(O.exp)}</b><span>expected (our chance)</span></div>
    <div><b>${roi(O.roi)}</b><span>return, flat stakes</span></div></div>`;
  const cards = D.cards.length ? `<div class="tw"><table class="t pt2"><thead><tr><th class="l">Card</th><th>Record</th><th>Hit</th><th>Expected</th><th>Return</th></tr></thead><tbody>
    ${D.cards.map(c => `<tr><td class="l"><b>${esc(c.card)}</b><span class="tier">${esc(c.tier)}</span></td><td>${c.w}-${c.l}${c.open ? ` <small class="muted">+${c.open} open</small>` : ''}</td><td>${c.n ? pc(c.w / c.n) : '–'}</td><td class="muted">${pc(c.exp)}</td><td>${roi(c.roi)}</td></tr>`).join('')}</tbody></table></div>` : '';
  const openAt = Math.max(0, D.days.findIndex(d => d.n));          // the latest day with results starts open
  const days = D.days.map((d, i) => `<details class="day"${i === openAt ? ' open' : ''}><summary><b>${day(d.date)}</b><span>${d.w}-${d.l}${d.open ? ` · ${d.open} open` : ''}</span><span>${d.n ? pc(d.w / d.n) + ' hit' : ''}</span><span>${d.roi == null ? '' : roi(d.roi)}</span><span class="chev" aria-hidden="true">▾</span></summary>
    ${d.groups.map(gr => `<details class="grp"><summary><span class="gn"><b>${esc(gr.card)}</b> <span class="muted">${esc(gr.tier)}</span></span><span>${gr.w}-${gr.l}${gr.open ? ` · ${gr.open} open` : ''}</span><span>${gr.roi == null ? '' : roi(gr.roi)}</span><span class="chev" aria-hidden="true">▾</span></summary>
      ${gr.parlays.slice().sort((a, b) => ({W: 0, O: 1, L: 2, V: 3}[a.res || 'O'] - {W: 0, O: 1, L: 2, V: 3}[b.res || 'O'])).map(parlay).join('')}</details>`).join('')}</details>`).join('');
  document.getElementById('main').innerHTML = D.days.length
    ? `<section class="panel"><div class="phead"><h2>Overall</h2><span class="small muted">since ${day(D.days[D.days.length - 1].date)}</span></div>${tiles}${cards}</section><section class="panel"><div class="phead"><h2>By day</h2></div>${days}</section>`
    : `<section class="panel"><div class="phead"><h2>Overall</h2></div><p class="note">Tracking starts with the next slate: every parlay card is saved before the first game and graded the next morning.</p></section>`;
}
render();
</script></body></html>"""


def page():
    sys.path.insert(0, HERE)
    import importlib.util
    import report
    spec = importlib.util.spec_from_file_location("sim_site", os.path.join(HERE, "site.py"))   # not Python's own 'site' module
    S = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(S)
    for sport in PAGES:
        c = write_card(sport)
        print(f"parlays {sport}: card {c['date']} {len(c['picks'])} picks, record {c['record']['w']}-{c['record']['l']} ${c['record']['net']:+.2f}")
        if sport not in ("nhl", "nfl"):
            continue                                    # NBA: the card only (no Parlays results page yet)
        d = page_data(sport)
        name = {"nhl": "NHL", "nfl": "NFL"}[sport]
        s = (PAGE.replace("__TITLE__", f"{name} parlay results").replace("__H1__", f"{name} parlays").replace("__SPORT__", name)
             .replace("__FONTS__", report.FONTS).replace("__CSS__", report.BASE_CSS).replace("__DATA__", json.dumps(d, separators=(",", ":"))))
        os.makedirs(os.path.join(OUT, sport), exist_ok=True)
        src = os.path.join(OUT, sport, "page.html")
        open(src, "w", encoding="utf-8").write(s)
        S.inject(src, os.path.join(SITE, sport, "parlays.html"), 1, f"{sport}:results")
        print(f"parlays {sport}: page {d['overall']['w']}-{d['overall']['l']}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "all"
    for step, fn in (("freeze", freeze), ("grade", grade), ("page", page)):
        if cmd in (step, "all"):
            try:
                fn()
            except Exception as e:                                                  # noqa: BLE001
                print(f"parlays {step}: failed ({type(e).__name__}: {e})")
