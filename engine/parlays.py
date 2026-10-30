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


def fail_note(msg):
    """A step that failed but let the run go on: noted for markets.yml's last step, which turns the run red
    (owner 2026-10-23: crashes must not hide behind green runs)."""
    try:
        import tempfile
        with open(os.path.join(os.environ.get("RUNNER_TEMP") or tempfile.gettempdir(), "sim_run_failures.txt"), "a", encoding="utf-8") as f:
            f.write(msg + "\n")
        print(f"::error::{msg}", flush=True)
    except OSError:
        pass


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
            if sport == "nba":
                lu_seen(now)                                         # NBA picks lock once their first game's lineups are confirmed
            snap = snapshot(sport, port, exe)
            if not snap:
                print(f"parlays {sport}: page gave no parlays (prices not loaded?)")
                continue
            if sport in ("nfl", "nba") and snap.get("paths") is not None:   # every potential Path leg -> the screener's Path legs filter
                json.dump(dict(generated=now.isoformat(), legs=snap["paths"]), open(os.path.join(SITE, "markets", f"paths_{sport}.json"), "w"), separators=(",", ":"))
                print(f"parlays {sport}: {len(snap['paths'])} path legs")
            try:                                                     # learning builder (builder.py): log candidates, add its parlays
                import builder as LB
                LB.save_cands(sport, snap.get("cands"), now)
                lp = LB.build(sport, snap.get("cands"), now)
                cp = LB.build_confluence(sport, snap.get("cands"), now)          # owner 2026-10-25: run alongside Today's card
                mp = LB.build_moveknee(sport, snap.get("cands"), now)            # NBA: sharp move up -> one rung above the main line
                sw = LB.build_sweet(sport, snap.get("cands"), now)               # owner 2026-10-27: edge 5-40% legs priced 15-45%
                snap["parlays"] = snap.get("parlays", []) + lp + cp + mp + sw
                print(f"parlays {sport}: {len(snap.get('cands') or [])} learning candidates, {len(lp)} learning parlays, {len(cp)} confluence parlays, {len(mp)} sharp move parlays, {len(sw)} sweet-spot parlays")
            except Exception as e:                                   # noqa: BLE001
                print(f"parlays {sport}: learning builder skipped ({type(e).__name__}: {e})")
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
                fin = card_final_at(date, (old["parlays"] if old else []) + by.get(date, []), sport)
                locked = [dict(p, locked=True) for p in (old["parlays"] if old else []) if p.get("locked") or when(p["first"]) <= now
                          or (p.get("pick") and now >= pick_lock(sport, p["pick"], p, fin))
                          or (str(p.get("card", "")).startswith(BUILDER_CARDS) and now >= pick_lock(sport, "core", p, fin))]
                lk = {tuple(sorted(_legkey(L) for L in p["legs"])) for p in locked}
                pending = [p for p in by.get(date, []) if when(p["first"]) > now and tuple(sorted(_legkey(L) for L in p["legs"])) not in lk]
                # learning / confluence parlays: at most 3 a day each; once some lock, new ones only from other games (and
                # not past their lock)
                keep = list(pending)
                for bc in BUILDER_CARDS:
                    LP = lambda q: str(q.get("card", "")).startswith(bc)
                    lgids = {str(L.get("gid")) for q in locked if LP(q) for L in q["legs"]}
                    room = 3 - sum(1 for q in locked if LP(q))
                    nxt = []
                    for q in keep:
                        if LP(q):
                            if room <= 0 or now >= pick_lock(sport, "core", q, fin) or any(str(L.get("gid")) in lgids for L in q["legs"]):
                                continue
                            room -= 1
                        nxt.append(q)
                    keep = nxt
                pending = keep
                ps = pick_card(sport, locked + pending, now, fin)
                if not ps or (old and ps == old["parlays"]):
                    continue
                os.makedirs(os.path.dirname(fp), exist_ok=True)
                json.dump(dict(sport=sport, date=date, lock=min(p["first"] for p in ps), frozen_at=snap["generated"], books=snap["books"],
                               final_at=fin.isoformat(), parlays=ps, graded=False), open(fp, "w"), indent=0)
                print(f"parlays {sport} {date}: {len(locked)} locked, {len(pending)} still updating")
        freeze_multi(now)
        try:                                                         # plus-money slate (slate.py, owner 2026-10-27)
            import slate as SLT
            SLT.freeze(now)
        except Exception as e:                                       # noqa: BLE001
            print(f"slate: skipped ({type(e).__name__}: {e})")
            fail_note(f"slate freeze: {type(e).__name__}: {e}")
    finally:
        srv.terminate()


# ---------------------------------------------------------------- the daily card
# Not every parlay: 4 a day per sport, $5 each (owner's stake: half his $10 straight unit). From the graded record
# (335 parlays to 2026-10-18): 5+ legs / 15x+ went 0-101 vs ~10 expected, 2-4 legs at 3-15x matched our chances, claimed
# edges over 50% went 0-33 (the model, not the market, was wrong), NHL parlays hit ~70% of our chance, and the same leg sat
# in up to 13 parlays a day. So: 3 core picks + 1 all-day longshot (the owner likes them), every leg priced at his books,
# legs from different games (books' payouts multiply only then), edge 5-40% on a market-anchored chance, a leg in at most
# 2 picks, overs only (no under legs). Same-game cards are out: no book prices them as the product of their legs.
CARD_N, CARD_STAKE, CARD_CORE = 4, 5, 3
CARD_BOOKS = {"Novig", "Kalshi", "Hard Rock"}           # the owner's books: every leg must be bettable there
# NHL parlays won 19 vs 27 expected (z about -1.6, not settled): the chance shown is cut 15% (half-way), but picking uses
# the anchored edge itself; cutting 25% left almost no NHL parlay with an edge
CARD_DISCOUNT = {"nhl": .85}


BUILDER_CARDS = ("Learning parlays", "Confluence parlays", "Sharp move parlays", "Sweet-spot edge parlays")       # builder.py's cards: tracked on their own, never Today's card


def _legkey(L):
    return f"{L.get('gid')}|{L.get('name') or L.get('lab')}|{L.get('kind')}|{L.get('side')}|{L.get('line')}"


def card_slot(sport, p):
    """('core' | 'longshot' | None, edge on the parlay's market-anchored chance) for one parlay."""
    legs, d = p.get("legs") or [], p.get("d")
    if not d or p.get("p") is None or str(p.get("card", "")).startswith(("Same-game",) + BUILDER_CARDS) or len(legs) < 2:
        return None, None                                            # the learning builder's parlays are tracked on their own
    if any(L.get("price") is None or L.get("book") not in CARD_BOOKS for L in legs) or len({str(L.get("gid")) for L in legs}) < len(legs):
        return None, None
    if any(str(L.get("side")).lower() in ("u", "under") for L in legs):  # overs only (owner, 2026-10-20): no unders, props or totals
        return None, None
    ev = p["p"] * d - 1
    if not .05 <= ev <= .40:
        return None, ev
    if len(legs) <= 4 and 3 <= d <= 15:
        return "core", ev
    if len(legs) <= 5 and 15 < d <= 100:
        return "longshot", ev
    return None, ev


# The card goes final at a fixed time (owner 2026-10-21: "they change from the morning to the evening" and he can't place
# bets at the last minute): 2 hours before the day's first game (owner 2026-10-21; was noon Eastern or that, if
# earlier); noon Eastern only when no game time is known. After that the picks never change and are graded at those prices.
CARD_FINAL_HOUR, CARD_FINAL_BEFORE = 12, dt.timedelta(hours=2)
# NFL core picks lock per slate instead (owner 2026-10-21: "after each slate confirms lineups and we rerun our data"):
# 60 min before the parlay's first kickoff -- inactives / ESPN's official starters post ~90 min before, and the NFL run
# after them lands ~75 min before. The all-day longshot keeps the day's lock (2 h before the first game: 7:30 AM ET on a
# London Sunday; owner: "the long all day parlay can lock at 7:30").
NFL_SLATE_LOCK = dt.timedelta(minutes=60)
# NHL / NBA cards lock after the first game's lineups (owner 2026-10-21): NHL official rosters post 15-45 min before puck
# drop and the NHL run at :40 picks them up (2026-10-20: 8 of 22 teams official in the 6:45 PM run, none at 6:15); the NBA
# runs 30 min before 7 PM tips. Owner 2026-10-21 (later): lock 1 hour before the first game (was 10 min).
LINEUP_LOCK = {"nhl": dt.timedelta(minutes=60), "nba": dt.timedelta(minutes=60)}
# NBA picks wait for confirmed lineups instead (owner 2026-10-28: "lets not lock basketball until after lineups and injury
# are confirmed"): each NBA pick (Today's card and the builder cards) keeps being re-picked until the first freeze that
# sees NBA.com's Confirmed five for both teams of its first game in the projection run (nba/lineups_log.json `off_at`;
# Confirmed posts ~30 min before tip, after the injury report's game-time calls). That freeze picks from the confirmed
# run and the pick locks right after it. No confirmation by tip -> it locks when its first game starts.
LU_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "nba", "lineups_log.json")
LU_SEEN_F = os.path.join(SITE, "markets", "lineups_seen_nba.json")
_LU_SEEN = None


def lu_seen(now=None):
    """{gid: iso time the freeze first saw both teams' confirmed lineups}; with now, adds newly confirmed games."""
    global _LU_SEEN
    if _LU_SEEN is None:
        try:
            _LU_SEEN = json.load(open(LU_SEEN_F))
        except (OSError, ValueError):
            _LU_SEEN = {}
    if now is not None:
        try:
            log = json.load(open(LU_LOG))
        except (OSError, ValueError):
            log = {}
        new = [g for g, d in log.items() if g not in _LU_SEEN and len(d) == 2 and all(v.get("src") == "official" for v in d.values())]
        for g in new:
            _LU_SEEN[g] = now.isoformat()
        if new:
            _LU_SEEN = dict(list(_LU_SEEN.items())[-300:])
            os.makedirs(os.path.dirname(LU_SEEN_F), exist_ok=True)
            json.dump(_LU_SEEN, open(LU_SEEN_F, "w"), separators=(",", ":"))
            print(f"parlays nba: lineups confirmed for {len(new)} game(s): {', '.join(new)}")
    return _LU_SEEN


def nba_lineup_lock(p):
    """(lock time, waiting): just after the freeze that saw the first game's confirmed lineups, else its first tip."""
    first = when(p["first"])
    gids = {str(L.get("gid")) for L in p["legs"] if L.get("start") and when(L["start"]) <= first}
    seen = lu_seen()
    if gids and all(g in seen for g in gids):
        return min(first, max(when(seen[g]) for g in gids) + dt.timedelta(seconds=1)), False
    return first, True


def pick_lock(sport, slot, p, card_fin):
    """When a pick (or a candidate for a slot) stops changing."""
    if sport == "nfl" and slot == "core":
        return when(p["first"]) - NFL_SLATE_LOCK
    if sport == "nba":
        return nba_lineup_lock(p)[0]
    return card_fin


def card_final_at(date, ps, sport=None):
    noon = dt.datetime.combine(dt.date.fromisoformat(date), dt.time(CARD_FINAL_HOUR), ET)
    firsts = [when(p["first"]) for p in ps if p.get("first")]
    before = LINEUP_LOCK.get(sport, CARD_FINAL_BEFORE)
    return (min(f - before for f in firsts) if firsts else noon).astimezone(dt.timezone.utc)


def pick_card(sport, ps, now, final_at=None):
    """Mark the day's card (p['pick'] = 'core' / 'longshot'). A pick stays once its first game starts or its lock passes
    (pick_lock: the card's final_at; NFL core picks 60 min before their first kickoff); before that the rest are chosen
    again from what the page shows now (best edge first, a leg in at most 2 picks). A slot whose lock passed is filled
    only if the card has no picks at all (picked once, then it stays)."""
    lock = lambda p, slot: pick_lock(sport, slot, p, final_at) if final_at is not None else when(p["first"])
    for p in ps:
        if p.get("pick") and now >= lock(p, p["pick"]):
            p["final"] = True
    keep = [p for p in ps if p.get("pick") and (when(p["first"]) <= now or p.get("final"))]
    none_yet = not keep
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
        if slot and (now < lock(p, slot) or none_yet):
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
            if now >= lock(p, slot):
                p["final"] = True
            sets.add(k)
            for L in p["legs"]:
                used[_legkey(L)] = used.get(_legkey(L), 0) + 1
    take(lambda x: x == "longshot", lambda: 1 - count(lambda x: x == "longshot"))
    take(lambda x: x == "core", lambda: CARD_CORE - count(lambda x: x == "core"))
    take(lambda x: x == "core", lambda: CARD_N - count(lambda x: True))     # no longshot today: a 4th core pick
    take(lambda x: True, lambda: CARD_N - count(lambda x: True))            # still short of 4 (few core parlays): more longshots
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
            days.append((d["date"], picks, d.get("final_at")))
    show = next(((dd, ps, fa) for dd, ps, fa in days if dd >= today), days[-1] if days else (None, [], None))
    out = dict(sport=sport, stake=CARD_STAKE, date=show[0], final_at=show[2], updated=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
               record=dict(rec, net=round(rec["net"], 2)),
               picks=[dict(slot=p["pick"], card=p["card"], tier=p.get("tier"), p=round(p["p"] * CARD_DISCOUNT.get(sport, 1), 4), d=p["d"],
                           first=p["first"], res=p.get("res"), paid=p.get("paid"),
                           lock=pick_lock(sport, p["pick"], p, when(show[2])).astimezone(dt.timezone.utc).isoformat() if show[2] else None,
                           lw=(sport == "nba" and nba_lineup_lock(p)[1]) or None,
                           legs=[dict(lab=L.get("lab"), price=L.get("price"), book=L.get("book"), p=L.get("p"), res=L.get("res")) for L in p["legs"]])
                      for p in sorted(show[1], key=lambda p: (p["pick"] != "core", p["first"]))])
    os.makedirs(os.path.join(SITE, "markets"), exist_ok=True)
    json.dump(out, open(os.path.join(SITE, "markets", f"card_{sport}.json"), "w"), separators=(",", ":"))
    return out


# ---------------------------------------------------------------- multisport all-day longshots
# One leg per game across sports (owner 2026-10-20: "multisport all day longshots"), from each sport's strongest legs at
# the owner's books: NFL / NBA Path-longshot legs (each with 3+ reasons, our chance 20-45%) and NHL anytime-goal legs
# (top-3-usage scorers with an edge). 3-4 legs, 2+ sports, paying 15-300x, edge >= 0 on the legs' market-anchored
# chances, a leg in at most 2 parlays. Frozen and graded like the rest (each leg carries its sport).
MULTI_SRC = {"nfl": ("Path longshots",), "nba": ("Path longshots",), "nhl": ("Anytime-goal parlays", "Longshot two-mans")}


def multi_parlays(date):
    pool = {}
    for sport, cards in MULTI_SRC.items():
        fp = os.path.join(OUT, sport, f"{date}.json")
        if not os.path.exists(fp):
            continue
        for q in json.load(open(fp)).get("parlays", []):
            if q.get("card") not in cards:
                continue
            for L in q["legs"]:
                if L.get("price") is None or L.get("book") not in CARD_BOOKS or str(L.get("side")).lower() in ("u", "under") or not L.get("p"):
                    continue
                k = (sport, str(L.get("gid")), L.get("name"), L.get("kind"), L.get("line"))
                nf = len(L.get("f") or []) or 3                                  # NHL goal legs: usage + edge + price
                if k not in pool or nf > pool[k]["nf"]:
                    pool[k] = dict(L, sport=sport, nf=nf)
    # each game's best leg
    best = {}
    for L in pool.values():
        g = (L["sport"], str(L["gid"]))
        sc = L["nf"] * 10 + L["p"] * dec(L["price"])
        if g not in best or sc > best[g][0]:
            best[g] = (sc, L)
    legs = sorted((L for _, L in best.values()), key=lambda L: -(L["nf"] * 10 + L["p"] * dec(L["price"])))[:14]
    import itertools
    cands = []
    for n in (3, 4):
        for c in itertools.combinations(legs, n):
            if len({L["sport"] for L in c}) < 2:
                continue
            pr, d = math.prod(L["p"] for L in c), math.prod(dec(L["price"]) for L in c)
            if not 15 <= d <= 300 or pr * d - 1 < 0:
                continue
            cands.append((sum(L["nf"] for L in c) / n, pr * d - 1, n, c, pr, d))
    cands.sort(key=lambda x: (-x[0], -x[1]))
    out, used = [], {}
    for want_n, k in ((3, 2), (4, 1)):
        got = 0
        for avg, ev, n, c, pr, d in cands:
            if n != want_n or got >= k:
                continue
            keys = [(L["sport"], L["gid"], L["name"]) for L in c]
            if any(used.get(x, 0) >= 2 for x in keys):
                continue
            for x in keys:
                used[x] = used.get(x, 0) + 1
            legs_ = [{k_: v for k_, v in L.items() if k_ != "nf"} for L in c]
            first = min(when(L["start"]) for L in c).astimezone(dt.timezone.utc).isoformat()
            out.append(dict(card="Multisport longshots", tier=f"{n} legs · {' + '.join(sorted({L['sport'].upper() for L in c}))}",
                            legs=legs_, p=round(pr, 5), d=round(d, 3), first=first))
            got += 1
    return out


def freeze_multi(now):
    today = dt.datetime.now(ET).date()
    dates = {os.path.basename(f)[:10] for sp in MULTI_SRC for f in glob.glob(os.path.join(OUT, sp, "*.json"))}
    for date in sorted(x for x in dates if dt.date.fromisoformat(x) >= today):
        fp = os.path.join(OUT, "multi", f"{date}.json")
        old = json.load(open(fp)) if os.path.exists(fp) else None
        if old and old.get("graded"):
            continue
        new = multi_parlays(date)
        fin = card_final_at(date, (old["parlays"] if old else []) + new)
        final = now >= fin and old and old.get("parlays")
        locked = [dict(q, locked=True) for q in (old["parlays"] if old else []) if q.get("locked") or when(q["first"]) <= now or final]
        lk = {tuple(sorted((L["sport"], L["gid"], L["name"]) for L in q["legs"])) for q in locked}
        pending = [] if final else [q for q in new if when(q["first"]) > now and tuple(sorted((L["sport"], L["gid"], L["name"]) for L in q["legs"])) not in lk]
        ps = locked + pending[:max(0, 3 - len(locked))]
        if not ps or (old and ps == old["parlays"]):
            continue
        os.makedirs(os.path.dirname(fp), exist_ok=True)
        json.dump(dict(sport="multi", date=date, lock=min(q["first"] for q in ps), frozen_at=now.isoformat(), books=sorted(CARD_BOOKS),
                       final_at=fin.isoformat(), parlays=ps, graded=False), open(fp, "w"), indent=0)
        print(f"parlays multi {date}: {len(locked)} locked, {len(ps) - len(locked)} still updating")


# ---------------------------------------------------------------- one page for every parlay (/parlays.html)
HUB_SKIP = ("Same-game",)                                 # same-game cards stay on each game's page (no book pays their product)


# Each book's payout for a parlay's legs (owner 2026-10-30: 'stick with the legs you like but list each book's projected
# multiplier'): the legs as picked, priced at every book that offers all of them now (main / alt lines, game lines, Kalshi
# rungs from live_<sport>.json with the 7% fee). 'Projected' = the legs' prices multiplied; a book's own parlay price can
# differ. Pinnacle is a reference only (never a price to bet).
MY_BOOKS = ("Hard Rock", "Novig", "Kalshi")
_MK = {"td": "anytime_td"}


def _mk_files(sport, cache):
    if sport not in cache:
        ld = lambda f: (lambda x: x)(json.load(open(os.path.join(SITE, "markets", f)))) if os.path.exists(os.path.join(SITE, "markets", f)) else {}
        try:
            P, B, K = ld(f"props_{sport}.json").get("props", {}), ld(f"books_{sport}.json").get("games", {}), ld(f"live_{sport}.json").get("rows", [])
        except (OSError, ValueError):
            P, B, K = {}, {}, []
        kal = {}
        for r in K:
            if r.get("n") and r.get("ya") and r.get("l") is not None:
                kal[(str(r["g"]), jsnorm(r["n"]), r["k"], round(float(r["l"]), 1))] = float(r["ya"])
        cache[sport] = (P, B, kal)
    return cache[sport]


def leg_quotes(L, sport, cache):
    """{book: decimal odds} for one leg at every book pricing it now."""
    P, B, kal = _mk_files(L.get("sport") or sport, cache)
    gid, kind, side, line = str(L.get("gid")), L.get("kind"), L.get("side"), L.get("line")
    q = {}
    if kind in ("ml", "spread", "total"):
        g = B.get(gid) or {}
        src = g.get("ml", {}).get(side) if kind == "ml" else (g.get(kind, {}).get(f"{float(line):g}") or {}).get(side) if line is not None else None
        q = {b: v[0] for b, v in (src or {}).items() if isinstance(v, list) and v and v[0] is not None}
    elif L.get("name"):
        ks = (P.get(gid) or {}).get(L["name"]) or {}
        key = f"sog{int(round(float(line) * 10))}" if kind == "sog" and line is not None else kind
        e = ks.get(key) or {}
        sd = "o" if side in ("o", "over") else "u"
        if e:
            ml = e.get("l") if e.get("l") is not None else .5
            src = e.get(sd) if line is None or abs(float(line) - float(ml)) < 1e-6 or kind == "sog" else ((e.get("a") or {}).get(f"{float(line):g}") or {}).get(sd)
            q = {b: v[0] for b, v in (src or {}).items() if isinstance(v, list) and v and v[0] is not None}
        if sd == "o" and "Kalshi" not in q and line is not None:
            ya = kal.get((gid, L["name"], _MK.get(kind, kind), round(float(line), 1)))
            if ya:
                c = ya + .07 * ya * (1 - ya)
                if 0 < c < 1:
                    q["Kalshi"] = round(-100 * c / (1 - c)) if c >= .5 else round(100 * (1 - c) / c)
    return {b: dec(o) for b, o in q.items() if b != "Pinnacle"}


def book_mults(legs, sport, cache):
    """[[book, multiplier or None, legs priced]] -- the owner's books always (None = a leg not offered), then every other
    book with all legs, best first."""
    if not legs:
        return []
    qs = [leg_quotes(L, sport, cache) for L in legs]
    books = set().union(*[set(x) for x in qs])
    full = {b: round(math.prod(x[b] for x in qs), 2) for b in books if all(b in x for x in qs)}
    mine = [[b, full.get(b), sum(b in x for x in qs)] for b in MY_BOOKS]
    rest = sorted(([b, v, len(qs)] for b, v in full.items() if b not in MY_BOOKS), key=lambda r: -r[1])
    return mine + rest[:4] if full or any(m[2] for m in mine) else []


def hub_data():
    today = dt.datetime.now(ET).date().isoformat()
    sports = []
    for sport in ("nfl", "nhl", "nba", "multi", "slate"):
        allf = sorted(glob.glob(os.path.join(OUT, sport, "????-??-??.json")))
        fs = [f for f in allf if os.path.basename(f)[:10] >= today]
        # keep yesterday's cards (with their results) up until today's are picked (owner 2026-10-30)
        has_picks = lambda f: any(q.get("pick") or sport in ("multi", "slate") for q in json.load(open(f)).get("parlays", []))
        yday = (dt.date.fromisoformat(today) - dt.timedelta(days=1)).isoformat()
        prev = [f for f in allf if os.path.basename(f)[:10] == yday]
        if prev and has_picks(prev[0]) and not (fs and has_picks(fs[0])):
            fs = prev
        if not fs:
            continue
        d = json.load(open(fs[0]))
        fin = when(d["final_at"]) if d.get("final_at") else None
        def lock_of(q):                                             # when this parlay stops changing (owner 2026-10-27: a clear locked time)
            if not q.get("first"):
                return None
            if sport == "slate":
                return when(q["first"]) - dt.timedelta(minutes=60)  # slate.LOCK_MIN
            if fin is not None and (sport == "multi" or q.get("pick")):
                return fin if sport == "multi" else pick_lock(sport, q["pick"], q, fin)
            if fin is not None and str(q.get("card", "")).startswith(BUILDER_CARDS):
                return pick_lock(sport, "core", q, fin)
            return when(q["first"])                                 # the other cards freeze at their first game
        cards, mk_cache = {}, {}
        for q in d["parlays"]:
            if str(q.get("card", "")).startswith(HUB_SKIP):
                continue
            lk = lock_of(q)
            cards.setdefault(q["card"], []).append(dict(tier=q.get("tier"), p=q.get("p"), d=q.get("d"), first=q.get("first"), res=q.get("res"),
                                                         lock=min(lk, when(q["first"])).astimezone(dt.timezone.utc).isoformat() if lk else None,
                                                         lw=bool(sport == "nba" and (q.get("pick") or str(q.get("card", "")).startswith(BUILDER_CARDS)) and q.get("legs") and nba_lineup_lock(q)[1]) or None,
                                                         paid=q.get("paid"), pick=q.get("pick"),
                                                         bk=book_mults(q["legs"], sport, mk_cache) if q.get("res") is None and q.get("first") and when(q["first"]) > dt.datetime.now(dt.timezone.utc) else None,
                                                         legs=[dict(lab=L.get("lab"), book=L.get("book"), price=L.get("price"), res=L.get("res"),
                                                                    sport=L.get("sport", sport), f=L.get("f")) for L in q["legs"]]))
        sports.append(dict(sport=sport, date=d["date"], final_at=d.get("final_at"), cards=[dict(card=c, parlays=v) for c, v in cards.items()]))
    L = None
    try:                                                            # the learning builder's status (builder.py)
        st = json.load(open(os.path.join(OUT, "learn", "state.json")))
        segs = st.get("segments", [])
        show = [x for x in segs if x["status"] in ("Proven", "Dropped")] + sorted([x for x in segs if x["status"] == "Watching"], key=lambda x: -x["n"])[:6]
        L = dict(builder=st.get("builder"), conf=st.get("confluence"), mk=st.get("moveknee"), sw=st.get("sweet"), rows=st.get("rows"), live=st.get("live_rows"), segs=show[:16], changes=st.get("changes", [])[-6:], updated=st.get("updated"))
    except (OSError, ValueError):
        pass
    SLM = None
    try:                                                            # the plus-money slate's learning model (slate.py)
        m = json.load(open(os.path.join(OUT, "slate", "meta", "model.json")))
        SLM = dict(coef=m.get("coef"), se=m.get("se"), live=m.get("live"), record=m.get("record"), n_seed=m.get("n_seed"), n_live=m.get("n_live"), updated=m.get("updated"))
    except (OSError, ValueError):
        pass
    return dict(updated=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), stake=CARD_STAKE, sports=sports, learn=L, slate=SLM)


HUB = r"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Parlays</title>__FONTS__<style>__CSS__
.hsw{display:flex;flex-wrap:wrap;gap:6px;margin:4px 0 14px}.hsw button{border:1px solid var(--faint);background:none;color:var(--ink);border-radius:999px;padding:5px 14px;font:600 13.5px var(--body);cursor:pointer}
.hsw button[aria-pressed="true"]{background:var(--ink);color:var(--bg);border-color:var(--ink)}
.htabs{display:flex;gap:4px;overflow-x:auto;scrollbar-width:none;margin:0 0 14px;padding:3px;border:1px solid var(--faint);border-radius:12px;background:var(--sunk)}.htabs::-webkit-scrollbar{display:none}
.htabs button{flex:none;border:0;background:none;color:var(--muted);border-radius:9px;padding:7px 12px;font:600 13.5px var(--body);cursor:pointer;white-space:nowrap}
.htabs button[aria-selected="true"]{background:var(--surface);color:var(--ink);box-shadow:0 0 0 1px var(--faint)}.htabs button span{font-size:11.5px;color:var(--muted);margin-left:5px;font-weight:500}
.slm{width:100%;border-collapse:collapse;font-size:13px}.slm td{padding:4px 6px;border-top:1px solid var(--faint)}.slm td:last-child{text-align:right;white-space:nowrap}
.lk{margin-left:auto;font-size:12px;font-weight:700;white-space:nowrap;padding:2px 9px;border-radius:999px;border:1px solid color-mix(in srgb,var(--warn,#F5B547) 55%,transparent);color:var(--warn,#F5B547)}.lk.on{border-color:color-mix(in srgb,var(--good,#36D399) 55%,transparent);color:var(--good,#36D399)}
.hg{display:grid;grid-template-columns:repeat(auto-fill,minmax(270px,1fr));gap:10px;margin:8px 0 4px}
.hp{border:1px solid var(--faint);border-radius:10px;padding:10px 12px;background:var(--sunk);display:grid;gap:6px;align-content:start}
.hp.pick{border-color:color-mix(in srgb,var(--accent) 55%,transparent)}.hp.W{border-color:color-mix(in srgb,var(--good) 60%,transparent)}.hp.L{opacity:.65}
.hh{display:flex;align-items:center;gap:6px;flex-wrap:wrap;font-size:12px;color:var(--muted)}.hh .st{margin-left:auto;font-weight:700}.hh .st.W{color:var(--good)}.hh .st.L{color:var(--bad)}
.tg{font-size:10.5px;font-weight:800;letter-spacing:.05em;text-transform:uppercase;border-radius:4px;padding:1px 6px;background:var(--faint);color:var(--ink)}
.tg.card{background:color-mix(in srgb,var(--accent) 20%,transparent);color:var(--accent)}.tg.ls{background:color-mix(in srgb,#F2C14E 20%,transparent);color:#F2C14E}
.hp ul{list-style:none;margin:0;padding:0;display:grid;gap:4px;font-size:13.5px}.hp li{display:flex;gap:7px;align-items:baseline}
.hp li .sp{font-size:10px;font-weight:800;color:var(--muted);width:28px;flex:none}.hp li .bk{margin-left:auto;white-space:nowrap;font-size:12.5px;color:var(--muted)}
.hp li i{font-style:normal;font-weight:800;width:12px;flex:none}.hp li i.W{color:var(--good)}.hp li i.L{color:var(--bad)}
.hbx{display:flex;flex-wrap:wrap;gap:4px;align-items:center;font-size:12px}.hbx>span:first-child{color:var(--muted);margin-right:2px}
.hbx .bq{border:1px solid var(--faint);border-radius:999px;padding:1px 8px;white-space:nowrap;color:var(--muted)}.hbx .bq b{font-family:var(--display);font-size:13px;color:var(--ink);margin-left:3px}
.hbx .bq.mine{border-color:color-mix(in srgb,var(--ink) 35%,transparent);color:var(--ink)}.hbx .bq.best{border-color:color-mix(in srgb,var(--good) 65%,transparent)}.hbx .bq.best b{color:var(--good)}.hbx .bq.na{opacity:.6}
.hs{display:flex;flex-wrap:wrap;gap:4px 12px;border-top:1px solid var(--faint);padding-top:6px;font-size:13px}.hs b{font-family:var(--display);font-size:16px}.hs .good{color:var(--good)}
.hsec h2 small{font-family:var(--body);font-size:12.5px;color:var(--muted);font-weight:500;margin-left:8px;letter-spacing:0;text-transform:none}
.lst{font-family:var(--body);font-size:10.5px;font-weight:800;letter-spacing:.05em;text-transform:uppercase;border-radius:4px;padding:1px 6px;margin-left:8px;vertical-align:3px;border:1px solid currentColor}
.lst.exp{color:#F2C14E}.lst.Proven{color:var(--good)}.lst.Dropped{color:var(--bad)}.lst.Watching,.lst.Learning{color:var(--muted)}
.lt td.good{color:var(--good)}.lt td.bad{color:var(--bad)}.lt td{font-size:13px}
details.hc{border-top:1px solid var(--faint);padding:8px 0}details.hc summary{cursor:pointer;font-weight:700}details.hc summary span{color:var(--muted);font-weight:500;font-size:12.5px;margin-left:6px}
</style></head><body><div class="wrap">
<header><div><div class="eyebrow">All sports · $5 a parlay</div><h1>Parlays</h1></div></header>
<main id="main"></main>
<footer>Every parlay the site builds, in one place: each sport’s Today’s card (4 a day: 3 core + 1 longshot, overs only, every leg at your books and from a different game), the multisport all-day longshots, and every card from the sport pages. Frozen at each parlay’s first game and graded on the Profit page. Same-game parlays stay on each game’s page (books don’t pay them as the legs multiplied). Updated about every 15 minutes before games.</footer>
</div><script>
const D = __DATA__;
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
const pc = p => p == null ? '–' : p < .1 ? (Math.round(p * 1000) / 10).toFixed(1) + '%' : Math.round(p * 100) + '%';
const am = o => o == null ? '' : (o > 0 ? '+' : '') + o;
const tm = t => new Date(t).toLocaleTimeString([], {hour: 'numeric', minute: '2-digit'});
const day = d => new Date(d + 'T12:00:00').toLocaleDateString([], {weekday: 'short', month: 'short', day: 'numeric'});
const SP = {nfl: 'NFL', nhl: 'NHL', nba: 'NBA', multi: 'Multi'};
let F = 'all'; try { F = localStorage.getItem('simhub-v1') || 'all'; } catch (e) {}
let T = 'card'; try { T = localStorage.getItem('simhub-tab') || 'card'; } catch (e) {}
// lock time (owner 2026-10-27: 'a clear locked time'): before it the parlay can still be re-picked; after it, it is final
const dayOf = t => new Date(t).toLocaleDateString('en-CA');
const lockChip = q => { const t = q.lock || q.first; if (!t) return '';
  const when_ = (dayOf(t) !== new Date().toLocaleDateString('en-CA') ? new Date(t).toLocaleDateString([], {weekday: 'short'}) + ' ' : '') + tm(t);
  return new Date(t) <= Date.now() ? `<span class="lk on" title="Final: this parlay won’t change. Place it any time before ${tm(q.first)}.">🔒 Locked · place by ${tm(q.first)}</span>`
    : q.lw ? `<span class="lk" title="Re-picked until the confirmed starting lineups and injury calls for its first game are in our numbers (~30 min before tip); then it won’t change.">🔓 Locks after lineups · by ${when_}</span>`
    : `<span class="lk" title="Until then it can still be swapped for a better one; after that it won’t change. First game ${tm(q.first)}.">🔓 Locks ${when_}</span>`; };
// each book's payout for these legs (owner 2026-10-30): your books always (— = a leg it doesn’t offer), then the best others
const BKS = {'Hard Rock': 'Hard Rock', Novig: 'Novig', Kalshi: 'Kalshi', DK: 'DraftKings', FD: 'FanDuel', MGM: 'BetMGM'};
const xf = v => v.toFixed(v < 10 ? 2 : 1) + 'x';
function bkRow(q){
  if (!q.bk || !q.bk.length) return '';
  const n = q.legs.length, best = Math.max(...q.bk.map(r => r[1] || 0));
  return `<div class="hbx" title="What each book pays for these legs right now, their prices multiplied (a book’s own parlay price can differ). Your books first; — = it doesn’t offer every leg."><span>${n > 1 ? 'Each book pays' : 'At each book'}</span>${q.bk.map(([b, v, k], i) =>
    `<span class="bq${i < 3 ? ' mine' : ''}${v && v === best ? ' best' : ''}${v ? '' : ' na'}"${v ? '' : ` title="${k} of ${n} leg${n === 1 ? '' : 's'} offered there"`}>${esc(BKS[b] || b)}<b>${v ? xf(v) : '—'}</b></span>`).join('')}</div>`;
}
function card(q, sport, lab){
  const sk = /straight/.test(q.tier || '') ? 10 : D.stake;
  const st = q.res === 'W' ? `<span class="st W">Won +$${(sk * ((q.paid || q.d) - 1)).toFixed(2)}</span>` : q.res === 'L' ? '<span class="st L">Lost</span>' : q.res === 'V' ? '<span class="st">Void</span>'
    : new Date(q.first) <= Date.now() ? '<span class="st">Live</span>' : lockChip(q);
  const ev = q.p && q.d ? q.p * q.d - 1 : null;
  return `<div class="hp ${q.pick ? 'pick' : ''} ${q.res || ''}"><div class="hh">${q.pick ? `<span class="tg card">Today’s card</span>` : ''}${lab ? `<span class="tg ${/long/i.test(lab) ? 'ls' : ''}">${esc(lab)}</span>` : ''}<span>${esc(q.tier || '')}</span>${st}</div>
    <ul>${q.legs.map(L => `<li><i class="${L.res || ''}">${L.res === 'W' ? '✓' : L.res === 'L' ? '✗' : '·'}</i>${sport === 'multi' ? `<span class="sp">${SP[L.sport] || ''}</span>` : ''}<b>${esc(L.lab)}</b><span class="bk">${esc(L.book || '')} ${am(L.price)}</span></li>`).join('')}</ul>
    ${bkRow(q)}
    <div class="hs">${q.d ? `<span>pays <b>${q.d.toFixed(q.d < 10 ? 1 : 0)}x</b></span><span>$${sk} → <b>$${(sk * q.d).toFixed(q.d < 10 ? 2 : 0)}</b></span>` : ''}<span>hits <b>${pc(q.p)}</b></span>${ev != null ? `<span class="${ev >= .05 ? 'good' : 'muted'}">edge ${ev >= 0 ? '+' : '−'}${Math.abs(Math.round(ev * 100))}%</span>` : ''}</div></div>`;
}
function render(){
  const S = D.sports.filter(s => s.sport !== 'slate' && (F === 'all' || s.sport === F));
  const sw = `<div class="hsw">${['all', ...D.sports.filter(s => s.sport !== 'slate').map(s => s.sport)].map(k => `<button data-f="${k}" aria-pressed="${F === k}">${k === 'all' ? 'All' : SP[k]}</button>`).join('')}</div>`;
  const picks = S.filter(s => s.sport !== 'multi').flatMap(s => s.cards.flatMap(c => c.parlays.filter(q => q.pick).map(q => ({q, s, c}))));
  const multi = S.find(s => s.sport === 'multi');
  const sec = (h, sub, body) => `<section class="panel hsec"><div class="phead"><h2>${h}<small>${sub}</small></h2></div>${body}</section>`;
  const TB = {};
  let html = '';
  const today = new Date().toLocaleDateString('en-CA');
  const lwOf = s => s.cards.some(c => c.parlays.some(q => q.pick && q.lw && !q.res && new Date(q.first) > Date.now()));
  const fins = S.filter(s => s.final_at && s.sport !== 'multi').map(s => s.sport === 'nba' ? `NBA ${lwOf(s) ? 'locks after lineups' : 'final'}`
    : `${SP[s.sport]} ${new Date(s.final_at) <= Date.now() ? 'final' : 'final at ' + tm(s.final_at)}`);
  const stale = picks.length && picks.every(x => x.s.date < today);
  if (picks.length) TB.card = sec(stale ? 'Yesterday’s cards' : 'Today’s cards', stale ? `${picks.length} parlays · today’s picks start building around 7 AM ET` : `${picks.length} parlays · $${D.stake} each${fins.length ? ' · ' + fins.join(' · ') : ''}`, `<div class="hg">${picks.map(x => card(x.q, x.s.sport, `${SP[x.s.sport]} · ${x.q.pick === 'longshot' ? 'longshot' : 'core'}${x.s.date !== today ? ' · ' + day(x.s.date) : ''}`)).join('')}</div>`);
  else TB.card = sec('Today’s cards', `$${D.stake} each`, `<p class="note">Today’s cards aren’t picked yet. Each day’s cards start building around 7 AM ET and fill in as the books post player props (most NBA / NHL props come in late morning). Yesterday’s cards and their results are on the <a href="profit.html">Profit page</a>.</p>`);
  let lrnHtml = '';
  const LRN = S.filter(s => s.sport !== 'multi').flatMap(s => s.cards.filter(c => /^Learning parlays/.test(c.card)).flatMap(c => c.parlays.map(q => ({q, s}))));
  if (D.learn || LRN.length){
    const B = (D.learn && D.learn.builder) || {n: 0, status: 'Experimental'}, proven = B.status === 'Proven';
    const rec = B.n ? `${B.w}-${B.n - B.w} · ${B.net >= 0 ? '+' : '−'}$${Math.abs(B.net).toFixed(2)} at $${D.stake}` : 'no graded parlays yet';
    const segRow = x => `<tr><td class="l">${esc(x.seg)}</td><td>${x.n}</td><td class="${x.ret >= 0 ? 'good' : 'bad'}">${x.ret >= 0 ? '+' : '−'}${Math.abs(Math.round(x.ret * 100))}%</td><td>${x.h1 == null ? '–' : (x.h1 >= 0 ? '+' : '−') + Math.abs(Math.round(x.h1 * 100)) + '%'} / ${x.h2 == null ? '–' : (x.h2 >= 0 ? '+' : '−') + Math.abs(Math.round(x.h2 * 100)) + '%'}</td><td><span class="lst ${x.status}">${x.status}</span></td></tr>`;
    const learned = D.learn ? `<details class="hc"><summary>What it has learned<span>${D.learn.rows} graded value props${D.learn.live ? ` · ${D.learn.live} with path reasons` : ''}</span></summary>
      <p class="note">Every over the pages could bet at your books is logged with its reasons and graded, picked or not. A kind of leg is <b>Proven</b> after 30+ bets with a positive return in both halves of the record, <b>Dropped</b> after 30+ negative in both (the builder skips it), and <b>Watching</b> in between. Started from the Oct 12-22 price history (no kind of value prop had consistent returns yet: all 5-40% edges −14% on 305).</p>
      <div class="tw"><table class="t lt"><thead><tr><th class="l">Kind of leg</th><th>Bets</th><th>Return</th><th>Halves</th><th>Status</th></tr></thead><tbody>${D.learn.segs.map(segRow).join('')}</tbody></table></div>
      ${D.learn.changes.length ? `<p class="note">Recent changes: ${D.learn.changes.map(c => `${esc(c.date.slice(5))} ${esc(c.seg)} ${esc(c.was)} → <b>${esc(c.now)}</b>`).join(' · ')}</p>` : ''}</details>` : '';
    lrnHtml += sec(`Learning parlays <span class="lst ${proven ? 'Proven' : 'exp'}">${proven ? 'Proven' : 'Experimental'}</span>`, `record ${rec}`,
      (LRN.length ? `<details class="hc"><summary>Today’s learning parlays<span>${LRN.length}</span></summary><div class="hg">${LRN.map(x => card(x.q, x.s.sport, SP[x.s.sport])).join('')}</div></details>` : '<p class="note">No legs qualify yet today: every leg must fit a game script or path, carry a 5-40% edge at your books, and not belong to a kind of leg it has dropped.</p>')
      + `<details class="how"><summary>How it works</summary><p class="note">Builds up to three parlays a sport a day (two 2-leg, one 3-leg), one leg per game, overs only, from legs that fit a game script or our paths (NFL: the script, or two of usage, matchup, rotation; NBA: two of usage, matchup, rotation, game; NHL: top-3 usage plus first power-play unit or a struggling goalie). It ranks them by edge, prefers kinds of legs it has proven and skips the ones it has dropped, and it relearns after every graded game. It stays Experimental until its own record is consistent: 60+ graded parlays, positive in both halves.</p></details>` + learned);
  }
  const CNF = S.filter(s => s.sport !== 'multi').flatMap(s => s.cards.filter(c => /^Confluence parlays/.test(c.card)).flatMap(c => c.parlays.map(q => ({q, s}))));
  {
    const B = (D.learn && D.learn.conf) || {n: 0, status: 'Experimental'}, proven = B.status === 'Proven';
    const rec = B.n ? `${B.w}-${B.n - B.w} · ${B.net >= 0 ? '+' : '−'}$${Math.abs(B.net).toFixed(2)} at $${D.stake}` : 'no graded parlays yet';
    const why = q => { const v = [...new Set(q.legs.flatMap(L => L.v || []))]; return v.length ? `<p class="note" style="margin:6px 0 0">${q.legs.map(L => `<b>${esc(L.lab.split(' ').slice(0, 2).join(' '))}</b>: ${esc((L.v || []).join(', '))}`).join(' · ')}</p>` : ''; };
    TB.conf = sec(`Confluence parlays <span class="lst ${proven ? 'Proven' : 'exp'}">${proven ? 'Proven' : 'Experimental'}</span>`, `record ${rec} · tracked beside Today’s card`,
      (CNF.length ? `<div class="hg">${CNF.map(x => `<div>${card(x.q, x.s.sport, SP[x.s.sport])}${why(x.q)}</div>`).join('')}</div>` : '<p class="note">No legs qualify yet today: each needs four or more things agreeing, a price between about −120 and +300 at your books, and a 0-15% edge.</p>')
      + `<details class="how"><summary>How it works</summary><p class="note">Legs where several things agree: our reasons (usage, rotation, matchup, game script / game), our model above the market’s fair price, and the market’s own signals (Pinnacle or our sharp consensus moving the prop up, a Value or LC tag, a ladder knee). A leg needs four or more; a reason the learning record has dropped counts zero, a proven one counts double. Only legs priced about −120 to +300 with a 0-15% edge (longshot legs hit 10% vs 20% priced, and big claimed edges hit 24% vs 40%). The payout comes from stacking near-fair legs: a 2-leg, a 3-leg and a 4-5 leg longshot paying 20-100x, one leg per game. The chance shown is the market’s fair price, not ours. Tracked beside Today’s card; it stays Experimental until 60+ graded parlays are positive in both halves.</p></details>`);
  }
  const SWP = S.filter(s => s.sport !== 'multi').flatMap(s => s.cards.filter(c => /^Sweet-spot edge parlays/.test(c.card)).flatMap(c => c.parlays.map(q => ({q, s}))));
  {
    const B = (D.learn && D.learn.sw) || {n: 0, status: 'Experimental'}, proven = B.status === 'Proven';
    const rec = B.n ? `${B.w}-${B.n - B.w} · ${B.net >= 0 ? '+' : '−'}$${Math.abs(B.net).toFixed(2)} at $${D.stake}` : 'no graded parlays yet';
    TB.sweet = sec(`Sweet-spot edge parlays <span class="lst ${proven ? 'Proven' : 'exp'}">${proven ? 'Proven' : 'Experimental'}</span>`, `record ${rec} · tracked beside Today’s card`,
      (SWP.length ? `<div class="hg">${SWP.map(x => card(x.q, x.s.sport, SP[x.s.sport])).join('')}</div>` : '<p class="note">No legs qualify yet today: each needs a 5-40% edge at your books on a leg priced about −120 to +570. They show up once the sharp books price the props, mostly a few hours before the games.</p>')
      + `<details class="how"><summary>How it works</summary><p class="note">Every over at Hard Rock, Novig and Kalshi, main and alternate lines, where our chance (our number blended with the sharp books’ fair price at that line, the site’s usual weights) beats the best price by 5-40%, on legs priced about −120 to +570. One leg per game, its biggest edge; a 2-leg, a 3-leg and a second 2-leg. Why: of every over priced at your books Oct 12-26 (24,944 graded legs, the price 1-3 hours before the start), all legs together lost 29%; this edge returned +16% on 319 legs, positive in both halves, and +23% on the 164 priced in this band. The same edge without our number lost 12%, and chasing line moves lost too. The price band was chosen from that same data, so this stays Experimental until its own graded record holds up.</p></details>`);
  }
  const MKP = S.filter(s => s.sport !== 'multi').flatMap(s => s.cards.filter(c => /^Sharp move parlays/.test(c.card)).flatMap(c => c.parlays.map(q => ({q, s}))));
  if (MKP.length || (D.learn && D.learn.mk && D.learn.mk.n)){
    const B = (D.learn && D.learn.mk) || {n: 0};
    const rec = B.n ? `${B.w}-${B.n - B.w} · ${B.net >= 0 ? '+' : '−'}$${Math.abs(B.net).toFixed(2)} at $${D.stake}` : 'no graded parlays yet';
    const why = q => `<p class="note" style="margin:6px 0 0">${q.legs.map(L => `<b>${esc(L.lab.split(' over ')[0])}</b>: ${esc((L.v || []).join(' · '))}`).join('<br>')}</p>`;
    TB.move = sec('NBA sharp move parlays <span class="lst exp">Experimental</span>', `record ${rec} · tracked beside Today’s card`,
      (MKP.length ? `<div class="hg">${MKP.map(x => `<div>${card(x.q, x.s.sport, SP[x.s.sport])}${why(x.q)}</div>`).join('')}</div>` : '<p class="note">None today: the sharp market hasn’t moved enough NBA props up yet (moves show up through the day, mostly in the last hours before tip).</p>')
      + `<details class="how"><summary>How it works</summary><p class="note">Starts from a prop Pinnacle or our sharp consensus moved up (the line went up, the over got shorter, or both) and takes the first rung above the main line at your best price (Hard Rock, Novig or Kalshi with its fee). Pinnacle moves rank first. In Oct 23-25 NBA markets that rung went 37% vs 32% priced (+13%, 134 bets; small sample), while the main line was +6% and two rungs up −17%; requiring a matchup or tag made every rung worse, so they’re shown as context only. One leg per game, 2 and 3 legs. Experimental and tracked; never on Today’s card.</p></details>`);
  }
  TB.learn = lrnHtml;
  // everything else the pages build: tracked on the Profit page, not picks (owner 2026-10-25: the section felt like noise)
  const more = [];
  if (multi && multi.cards.length){ const qs = multi.cards.flatMap(c => c.parlays);
    more.push(`<details class="hc"><summary>Multisport all-day longshots<span>${qs.length}</span></summary><div class="hg">${qs.map(q => card(q, 'multi', '')).join('')}</div></details>`); }
  for (const s of S.filter(s => s.sport !== 'multi'))
    for (const c of s.cards.filter(c => !/^(Learning|Confluence|Sharp move|Sweet-spot edge) parlays/.test(c.card)))
      more.push(`<details class="hc"><summary>${SP[s.sport]} · ${esc(c.card)}<span>${c.parlays.length}</span></summary><div class="hg">${c.parlays.map(q => card(q, s.sport, '')).join('')}</div></details>`);
  if (more.length) TB.more = sec('More parlays we track', 'not picks · graded on the Profit page', more.join(''));
  TB.slate = slateTab();
  const TABS = [['card', 'Today’s cards'], ['slate', 'Plus-money slate'], ['sweet', 'Sweet-spot'], ['conf', 'Confluence'], ['move', 'Sharp move'], ['learn', 'Learning'], ['more', 'More']];
  const cnt = {card: picks.length, slate: ((D.sports.find(s => s.sport === 'slate') || {cards: []}).cards.reduce((n, c) => n + c.parlays.length, 0))};
  if (!TB[T]) T = TABS.find(([k]) => TB[k]) ? TABS.find(([k]) => TB[k])[0] : 'card';
  const bar = `<div class="htabs" role="tablist">${TABS.filter(([k]) => TB[k] || k === 'card').map(([k, lab]) => `<button role="tab" data-t="${k}" aria-selected="${T === k}">${lab}${cnt[k] ? `<span>${cnt[k]}</span>` : ''}</button>`).join('')}</div>`;
  html = bar + (T === 'slate' ? '' : sw) + (TB[T] || '<section class="panel"><p class="note">No parlays yet today: they appear once prices are in.</p></section>');
  document.getElementById('main').innerHTML = html;
  const on = document.querySelector('.htabs [aria-selected="true"]'); if (on) on.scrollIntoView({block: 'nearest', inline: 'nearest'});
}
// Plus-money slate (slate.py; owner 2026-10-27): straights + 2-leg parlays, every leg +250 or longer, picked by a learning model
const FL_ = {gap: 'Our number above the market', pvf: 'Price shorter than the fair', dry3: 'NHL: no goal in 3+ games', hot0: 'NHL: scored last game',
  tag: 'Value or LC tag', move: 'Sharp market moved it up', alt: 'Alternate line', nba: 'NBA leg', nhl: 'NHL leg', td: 'Anytime TD', goal: 'Anytime goal', far: 'Priced +600 or longer', const: 'Baseline vs the market'};
function slateTab(){
  const sl = D.sports.find(s => s.sport === 'slate'), M = D.slate || {};
  const qs = sl ? sl.cards.flatMap(c => c.parlays) : [];
  const st = qs.filter(q => /straight/.test(q.tier || '')), pa = qs.filter(q => !/straight/.test(q.tier || ''));
  const R = M.record || {}, money = v => `${v >= 0 ? '+' : '−'}$${Math.abs(v || 0).toFixed(2)}`;
  const rec = R.days ? `${R.straight.w}-${R.straight.n - R.straight.w} straights · ${R.parlay.w}-${R.parlay.n - R.parlay.w} parlays · ${money(R.net)} on $${R.staked}` : 'no graded bets yet';
  const sec = (h, sub, body) => `<section class="panel hsec"><div class="phead"><h2>${h}<small>${sub}</small></h2></div>${body}</section>`;
  const grid = xs => `<div class="hg">${xs.map(q => card(q, 'slate', (q.legs[0].sport || '').toUpperCase() + (q.legs.length > 1 ? ' · ' + [...new Set(q.legs.map(L => (L.sport || '').toUpperCase()))].join(' + ') : ''))).join('')}</div>`;
  const C = M.coef || {}, SE = M.se || {};
  const rows = Object.keys(FL_).filter(k => k in C).map(k => ({k, v: C[k], z: SE[k] ? C[k] / SE[k] : 0})).sort((a, b) => Math.abs(b.v) - Math.abs(a.v));
  const lv = M.live || {};
  const model = `<details class="hc"><summary>What the model weighs<span>${M.n_seed || 0} seed + ${M.n_live || 0} live graded legs</span></summary>
    <p class="note">Each leg starts at the sharp market’s fair chance at that line; the model adds what has paid on +250-or-longer legs (refit every night). Positive = that leg hits more often than the market says.${lv.n ? ` Live so far: log-loss ${lv.ll_model} (model) vs ${lv.ll_market} (market alone); its 5%+ edges returned ${lv.picks_ret == null ? '–' : (lv.picks_ret * 100).toFixed(0) + '%'} on ${lv.picks}.` : ''}</p>
    <div class="tw"><table class="slm"><tbody>${rows.map(r => `<tr><td>${FL_[r.k]}</td><td class="${r.v > 0 ? 'good' : r.v < 0 ? 'bad' : ''}">${r.v > 0 ? '+' : ''}${r.v.toFixed(2)}${Math.abs(r.z) >= 2 ? ' ●' : ''}</td></tr>`).join('')}</tbody></table></div>
    <p class="note">● = clear in the data so far (2+ standard errors).</p></details>`;
  const how = `<details class="how"><summary>How it works</summary><p class="note">Every over at Hard Rock, Novig and Kalshi priced +250 to +1000 (main and alternate lines, NBA, NHL and NFL) that the sharp books price. A learning model estimates each one’s real chance: the sharp market’s fair price, adjusted for our projection’s gap to it, the price against the fair, NHL goal droughts (players who just scored have been overpriced, players 3+ games without a goal underpriced), Value / LC tags, sharp line moves, alternate lines and price band. It learns from every graded candidate every night (started from 2,271 graded +250 legs, Oct 12-26). The slate takes legs with a 5-60% edge: the 4 best as straights ($10, one per game) and the best 2-leg parlays from different games ($5), 10 bets at most, a leg in at most 2. Each bet locks 60 minutes before its first game. Walk-forward test on Oct 12-26 (fit on one half of the days, slate built on the other, both ways): +$167 and +$154 — only 6 days each way, so it stays Experimental until its own record holds up. A +250 straight wins about 1 time in 4 and a 2-leg parlay about 1 in 15: expect long dry spells; judge it on profit over weeks.</p></details>`;
  return sec('Plus-money slate <span class="lst exp">Experimental</span>', `record ${rec}`,
      (qs.length ? `<div class="eyebrow" style="margin-top:4px">Straights · $10</div>${st.length ? grid(st) : '<p class="note">None yet.</p>'}<div class="eyebrow" style="margin-top:12px">Parlays · $5</div>${pa.length ? grid(pa) : '<p class="note">None yet.</p>'}`
        : '<p class="note">No legs qualify yet today: each needs a 5%+ edge on a +250-or-longer price at your books, and the sharp books post most player props a few hours before the games.</p>')
      + model + how);
}
document.addEventListener('click', e => { const t = e.target.closest('button[data-t]'); if (t){ T = t.dataset.t; try { localStorage.setItem('simhub-tab', T); } catch (x) {} render(); return; } const b = e.target.closest('button[data-f]'); if (!b) return; F = b.dataset.f; try { localStorage.setItem('simhub-v1', F); } catch (x) {} render(); });
render();
</script></body></html>"""


def write_hub(S):
    import report
    d = hub_data()
    src = os.path.join(OUT, "hub.html")
    open(src, "w", encoding="utf-8").write(HUB.replace("__FONTS__", report.FONTS).replace("__CSS__", report.BASE_CSS).replace("__DATA__", json.dumps(d, separators=(",", ":"))))
    S.inject(src, os.path.join(SITE, "parlays.html"), 0, "parlays")
    print(f"parlays hub: {sum(len(c['parlays']) for s in d['sports'] for c in s['cards'])} parlays across {len(d['sports'])} sports")


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
                        fg3m=float(str(x.get("threePointFieldGoalsMade-threePointFieldGoalsAttempted", "0-0")).split("-")[0]),
                        stl=float(x.get("steals", 0)), blk=float(x.get("blocks", 0)))
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
    if L["kind"] in ("dd", "tdbl"):                     # NBA double / triple-double: 10+ in 2 / 3 of pts, reb, ast, stl, blk
        n10 = sum(1 for k in ("pts", "reb", "ast", "stl", "blk") if s.get(k, 0) >= 10)
        return "W" if n10 >= (2 if L["kind"] == "dd" else 3) else "L"
    key = {"goal": "goals", "sog": "sog"}.get(L["kind"], L["kind"])
    v = (s.get("rush_td", 0) + s.get("rec_td", 0)) if L["kind"] == "td" else s.get(key, 0)
    line = float(L["line"] if L.get("line") is not None else .5)
    if v == line:
        return "P"
    return "W" if (v > line) == (L["side"] == "o") else "L"


def grade():
    today = dt.datetime.now(ET).date()
    for fp in sorted(glob.glob(os.path.join(OUT, "*", "????-??-??.json"))):
        if os.path.basename(os.path.dirname(fp)) == "learn":          # the learning builder's own files (builder.py)
            continue
        d = json.load(open(fp))
        if d.get("graded") or dt.date.fromisoformat(d["date"]) > today:
            continue
        sport, games, pending = d["sport"], {}, 0
        for p in d["parlays"]:
            for L in p["legs"]:
                if L.get("res") in ("W", "L", "P", "V"):
                    continue
                gid = L.get("gid")
                lsp = L.get("sport") or sport
                if (lsp, gid) not in games:
                    try:
                        games[(lsp, gid)] = game_for(lsp, gid, L["start"])
                    except Exception as e:                                     # noqa: BLE001
                        print(f"parlays {sport}: {gid} not graded ({type(e).__name__})")
                        games[(lsp, gid)] = None
                g = games[(lsp, gid)]
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
    write_hub(S)                                        # /parlays.html: every sport in one place


def hub():
    import importlib.util
    sys.path.insert(0, HERE)
    spec = importlib.util.spec_from_file_location("sim_site", os.path.join(HERE, "site.py"))
    S = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(S)
    write_hub(S)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "all"
    for step, fn in (("freeze", freeze), ("grade", grade), ("page", page), ("hub", hub)):
        if cmd in (step, "all"):
            try:
                fn()
            except Exception as e:                                                  # noqa: BLE001
                print(f"parlays {step}: failed ({type(e).__name__}: {e})")
                fail_note(f"parlays {step}: {type(e).__name__}: {e}")
