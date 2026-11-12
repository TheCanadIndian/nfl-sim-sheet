#!/usr/bin/env python3
"""Paper-trading account (owner 2026-11-11: "starting tomorrow I want you to papertrade a betting account. You can use all the
tools that I would have available to me... You can only use parlays... ask agents questions to help improve your hit rate and
ultimately become profitable"; "determine your own bet size and daily limit, starting value 250 usd"; "everyday I need reasons
why the bet was taken. make it all collapsable so I can check by day").

Claude runs the account (a daily cloud routine). This file holds the rules and the bookkeeping, so a pick can't be recorded at a
price the books didn't show, after its game started, or outside the bankroll rules:

    python paper.py slate [nba nhl nfl] [--out F]   every leg Claude could use right now at Hard Rock / Novig / Kalshi, with the
                                                    sharp fair, our chance, the edge and every site signal (JSON + a summary)
    python paper.py record picks.json [--dry]       check the picks against the rules and the CURRENT prices; all or nothing
    python paper.py grade                           settle from box scores (parlays.game_for / settle; markets.yml)
    python paper.py report                          the account: bankroll, record, by sport / book / legs, pending, recent legs
    python paper.py page                            /paper.html: one collapsible block per day with every bet and its reasons
    python paper.py pull-branch                     merge the paper files the routine pushed to claude/paper (markets.yml; the routine)

Files (repo root, kept on main with history): paper/bets/<ET date>.json (written by record only), paper/results.json (written by
grade only, so the routine and the workflows never write the same file), paper/journal/<date>.md (Claude's daily notes: what
it asked its research agents, what it concluded), paper/lessons.md (running lessons).
"""
import datetime as dt
import glob
import html
import json
import math
import os
import re
import statistics
import sys
import tempfile
import unicodedata
from zoneinfo import ZoneInfo

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass
HERE = os.path.dirname(os.path.abspath(__file__))
SITE = os.environ.get("SIM_SITE") or (os.path.join(HERE, "site") if os.path.isdir(os.path.join(HERE, "site")) else os.path.dirname(HERE))
MK = os.path.join(SITE, "markets")
PAPER = os.path.join(SITE, "paper")
ET = ZoneInfo("America/New_York")
UTC = dt.timezone.utc

# ---------------------------------------------------------------- the rules (Claude's own, 2026-11-11)
START, START_DATE = 250.0, "2026-11-12"
BOOKS = ("Hard Rock", "Novig", "Kalshi")          # the owner's books; a parlay is one ticket at ONE book
UNIT_PCT, UNIT_MIN, UNIT_MAX = .02, 2.0, 10.0     # a unit = 2% of the bankroll at the day's first bet, $2-$10 ($5 at the start)
DRAW_LINE, DRAW_PCT = 175.0, .01                  # below $175 (-30%): 1% units until it recovers
STOP_LINE = 100.0                                 # below $100: no new bets (research and review only)
MAX_BETS_DAY, MAX_UNITS_DAY = 4, 4.0              # at most 4 parlays and 4 units (8% of the bankroll) a day
LONG_DEC, LONG_STAKE = 6.0, .5                    # parlays paying more than 6x take half a unit
LEGS = (2, 4)                                     # parlays only: 2-4 legs, one per game
LEG_Q = (.15, .80)                                # each leg's implied chance at the price (Kalshi with its fee)
OVERS_PROPS_ONLY = True                           # owner 2026-11-12 ('Only overs only player props'): every leg a player-prop over; no unders, no game lines
MAX_DEC = 30.0                                    # no lottery tickets
LEG_REUSE = 2                                     # one leg in at most 2 of the day's parlays
MIN_LEAD_MIN = 5                                  # recorded at least 5 min before the leg's game starts
KFEE = .07

STAT = {"pts": "points", "reb": "rebounds", "ast": "assists", "fg3m": "threes", "rec": "catches", "rec_yds": "receiving yds",
        "rush_yds": "rushing yds", "pass_yds": "passing yds", "td": "anytime TD", "goal": "goal", "sog": "shots on goal",
        "ml": "moneyline", "total": "game total"}
LIVE_K = {"td": "anytime_td"}                      # props kind -> Kalshi row kind (parlays._MK)
CONS_W = {"nba": {"Novig": 5.0}}                   # sharp.CONS_W
W_OURS = {("nba", None): .05, ("nfl", None): .25, ("nfl", "td"): 0.0, ("nhl", "goal"): .2, ("nhl", None): .5}   # mkt.js WPIN / builder SW_W


def jsnorm(s):
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z ]", "", s)
    s = re.sub(r"\b(jr|sr|ii|iii|iv)\b", "", s)
    return re.sub(r"\s+", " ", s).strip()


def jl(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {} if default is None else default


def when(ts):
    t = dt.datetime.fromisoformat(str(ts).replace("Z", "+00:00").replace(" ", "T", 1))
    return t if t.tzinfo else t.replace(tzinfo=ET)


def now_utc():
    return dt.datetime.now(UTC)


def et_day(t=None):
    return (t or now_utc()).astimezone(ET).date().isoformat()


def dec(o):
    o = float(o)
    return 1 + (o / 100 if o > 0 else 100 / -o)


def amer(d):
    return round((d - 1) * 100) if d >= 2 else round(-100 / (d - 1))


def lg(p):
    p = min(max(p, 1e-6), 1 - 1e-6)
    return math.log(p / (1 - p))


def sg(x):
    return 1 / (1 + math.exp(-x))


def kal_price(ask):
    """Kalshi YES at the ask + the 7% fee, as American odds."""
    c = ask + KFEE * ask * (1 - ask)
    return None if not 0 < c < 1 else (round(-100 * c / (1 - c)) if c >= .5 else round(100 * (1 - c) / c))


def cons_of(parts, exclude, sport):
    xs = [(k, v) for k, v in parts.items() if k != exclude and v] or [(k, v) for k, v in parts.items() if v]
    if not xs:
        return None
    W = CONS_W.get(sport, {})
    ws = [W.get(k, 1.0) for k, _ in xs]
    return sg(sum(w * lg(v) for w, (_, v) in zip(ws, xs)) / sum(ws))


def money(x, sign=False):
    s = f"${abs(x):,.2f}".replace(".00", "")
    return (("+" if x > 0 else "−" if x < 0 else "") + s) if sign else s


# ---------------------------------------------------------------- the slate (every usable leg right now)
def _names_and_games(sport):
    """display names (norm -> name) and game labels (gid -> 'AWY @ HOM') from the Kalshi rows; NFL ids carry the teams."""
    names, games = {}, {}
    for r in (jl(os.path.join(MK, f"live_{sport}.json")).get("rows") or []):
        if r.get("n"):
            names.setdefault(jsnorm(r["n"]), r["n"])
        if r.get("g") is not None and r.get("a") and r.get("h"):
            games.setdefault(str(r["g"]), f"{r['a']} @ {r['h']}")
    return names, games


def _label(gid, games):
    m = re.match(r"\d{4}_\d{2}_([A-Z]+)_([A-Z]+)$", str(gid))
    return f"{m.group(1)} @ {m.group(2)}" if m else games.get(str(gid), str(gid))


KIND_WORDS = (("rec yds", "rec_yds"), ("receiving", "rec_yds"), ("receptions", "rec"), ("catches", "rec"), ("rush", "rush_yds"),
              ("pass", "pass_yds"), ("td", "td"), ("points", "pts"), ("rebounds", "reb"), ("assists", "ast"), ("threes", "fg3m"),
              ("shots", "sog"), ("goal", "goal"))


def _kind_of(text):
    t = str(text).lower()
    return next((k for w, k in KIND_WORDS if w in t), None)


def _signals(sport):
    """(gid, norm name) -> [signal text] from the site's flag files (Value / LC / moves / off-market / ladders / paths / NHL ...)."""
    S = {}

    def add(g, n, t, k=None):
        """k = the stat the signal is about (None: about the player); a leg shows its own stat's signals + the player's"""
        S.setdefault((str(g), jsnorm(n)), []).append((k if k is not None else _kind_of(t), t))
    F = jl(os.path.join(MK, f"flags_{sport}.json"))
    for g, pl in (F.get("tags") or {}).items():
        for n, t in pl.items():
            add(g, n, f"Value tag: {t.get('b')} at {t.get('k')} {t.get('o')}")
    for g, pl in (F.get("lc") or {}).items():
        for n, t in pl.items():
            add(g, n, f"LC tag: {t.get('b')} at {t.get('k')} {t.get('o')} (edge {100 * (t.get('ev') or 0):.0f}%)")
    for m in F.get("moves") or []:
        lab = {"line": "line up", "price": "odds drop", "both": "line up + odds drop", "rung": "higher-rung outlier"}.get(m.get("sig"), m.get("sig"))
        add(m.get("g"), m.get("n"), f"{'Pinnacle' if m.get('src') == 'pin' else 'Consensus'} move: {lab} ({m.get('b')})", m.get("k"))
    for m in F.get("off") or []:
        add(m.get("g"), m.get("n"), f"Off-market price: {m.get('b')} at {m.get('bk')} {m.get('o')} vs fair {100 * (m.get('fair') or 0):.0f}% (often stale; check)", m.get("k"))
    for m in F.get("knees") or []:
        add(m.get("g"), m.get("n"), f"Ladder knee: {m.get('b') or m.get('k')}")
    for m in F.get("openers") or []:
        if not m.get("gone"):
            add(m.get("g"), m.get("n"), f"Early opener value: {m.get('b')} at {m.get('bk')} {m.get('o')}")
    for L in (jl(os.path.join(MK, f"ladders_{sport}.json")).get("legs") or []):
        add(L.get("gid"), L.get("name"), f"Ladder ({L.get('cat')}): {STAT.get(L.get('kind'), L.get('kind'))} {L.get('line')}", L.get("kind"))
    for L in (jl(os.path.join(MK, f"paths_{sport}.json")).get("legs") or []):
        add(L.get("gid"), L.get("name"), f"Path leg: {STAT.get(L.get('kind'), L.get('kind'))} {L.get('line')} · " + "; ".join(L.get("why") or L.get("f") or []), L.get("kind"))
    for p in (jl(os.path.join(MK, f"card_{sport}.json")).get("picks") or []):
        for L in p.get("legs") or []:
            if L.get("gid") and L.get("name"):
                add(L["gid"], L["name"], f"On the site's Today's card ({p.get('card')}): {L.get('lab')}", L.get("kind") or "")
    if sport == "nhl":
        D = jl(os.path.join(MK, "dry_nhl.json")).get("games") or {}
        for g, x in D.items():
            for n, k in (x.get("dry") or {}).items():
                if 3 <= (k or 0) <= 9:
                    add(g, n, f"No goal in {k} games (drought: the market overprices recent scorers)", "goal")
            due = x.get("due") or {}
            for n in (due if isinstance(due, (list, dict)) else []):
                add(g, n, "Goal luck: due (xG .25+/game, .10+ under it; tracked, not proven)", "goal")
            for n in x.get("pp1t3") or []:
                add(g, n, "PP1 top-3-usage forward (every price at your books +5.5% in the books study; tracked)", "goal")
        for g, x in (jl(os.path.join(MK, "dout_nhl.json")).get("games") or {}).items():
            for n in x.get("vs") or []:
                add(g, n, "PP1, not top 3, vs a team missing a regular D (tracked)", "goal")
    return S


def _fair_two_way(o, u, skip=None):
    """median two-way no-vig over chance across books with both sides (2+), leaving out `skip`."""
    xs = []
    for b, v in (o or {}).items():
        w = (u or {}).get(b)
        if b == skip or not (isinstance(v, list) and v and v[0] is not None and isinstance(w, list) and w and w[0] is not None):
            continue
        a, c = 1 / dec(v[0]), 1 / dec(w[0])
        xs.append(a / (a + c))
    return statistics.median(xs) if len(xs) >= 2 else None


def _td_books_fair(o, skip):
    """NFL anytime TD without a sharp price: logit -.0647 + 1.1037 logit(median Yes of the other books, 3+) (sharp.td_books_fair)."""
    ys = [1 / dec(v[0]) for b, v in (o or {}).items() if b not in (skip, "Pinnacle") and isinstance(v, list) and v and v[0] is not None]
    return sg(-.0647 + 1.1037 * lg(statistics.median(ys))) if len(ys) >= 3 else None


def slate(sports=("nba", "nhl", "nfl"), at=None):
    at = at or now_utc()
    sched = {(g.get("sport"), str(g.get("game"))): g.get("start") for g in jl(os.path.join(MK, "schedule.json")).get("games", [])}
    arch, role = jl(os.path.join(MK, "arch_nba.json")), jl(os.path.join(MK, "role_nfl.json"))
    out = []
    for sport in sports:
        P = jl(os.path.join(MK, f"props_{sport}.json")).get("props") or {}
        B = jl(os.path.join(MK, f"books_{sport}.json")).get("games") or {}
        names, games = _names_and_games(sport)
        sig = _signals(sport)
        kal = {}
        for r in (jl(os.path.join(MK, f"live_{sport}.json")).get("rows") or []):
            if r.get("n") and r.get("l") is not None and r.get("ya"):
                kal[(str(r["g"]), jsnorm(r["n"]), r["k"], round(float(r["l"]), 1))] = (float(r["ya"]), r.get("yb"), r.get("start"))
        starts = {}
        for (sp, g), st in sched.items():
            if sp == sport:
                starts[g] = st
        for (g, n, k, l), (_, _, st) in kal.items():
            if st:
                starts.setdefault(g, st)

        def emit(gid, nm, key, kind, side, line, prices, fair, ours, main, extra=None):
            if OVERS_PROPS_ONLY and (not nm or side != "o"):
                return                                       # player-prop overs only (owner 2026-11-12)
            st = starts.get(str(gid))
            if not st or when(st) <= at + dt.timedelta(minutes=MIN_LEAD_MIN):
                return
            for book, pr in prices.items():
                if book not in BOOKS or pr is None:
                    continue
                f = fair(book) if callable(fair) else fair
                if f is None:
                    continue
                d = dec(pr)
                w = W_OURS.get((sport, kind), W_OURS.get((sport, None), .5)) if ours is not None else 0.0
                ch = w * ours + (1 - w) * f if ours is not None else f
                who = (names.get(nm) or " ".join(x.capitalize() for x in nm.split())) if nm else ""
                if kind == "ml":
                    gl = _label(gid, games).split(" @ ")
                    team = (gl[1] if side == "home" else gl[0]) if len(gl) == 2 else side
                    lab = f"{team} moneyline ({_label(gid, games)})"
                elif kind == "total":
                    lab = f"{_label(gid, games)} {side} {line:g}"
                elif kind in ("goal", "td"):
                    lab = f"{who} {'to score' if kind == 'goal' else 'anytime TD'}" + (" (No)" if side == "u" else "")
                else:
                    lab = f"{who} {'over' if side == 'o' else 'under'} {line:g} {STAT.get(kind, kind)}"
                tags = [t for k, t in sig.get((str(gid), nm), []) if k in (None, kind)] if nm else []
                if nm and sport == "nba" and arch.get(nm):
                    tags.append(f"archetype: {arch[nm]}")
                if nm and sport == "nfl" and role.get(nm):
                    tags.append(f"role: {role[nm]}")
                out.append(dict(id=f"{sport}|{gid}|{nm or '-'}|{key}|{line:g}|{side}", sport=sport, gid=str(gid), game=_label(gid, games),
                                start=st, name=nm, kind=kind, side=side, line=line, lab=lab, book=book, price=int(pr), dec=round(d, 4),
                                q=round(1 / d, 4), fair=round(f, 4), ours=None if ours is None else round(ours, 4), p=round(ch, 4),
                                edge=round(ch * d - 1, 4), main=main, sig=tags, **(extra or {})))

        for gid, G in P.items():
            for nm, ks in G.items():
                for key, e in ks.items():
                    kind = "sog" if key.startswith("sog") else key
                    ml = float(e["l"]) if e.get("l") is not None else .5
                    pin = e.get("pin") or {}
                    o, u = e.get("o") or {}, e.get("u") or {}
                    ko = dict(o)
                    kk = kal.get((str(gid), nm, LIVE_K.get(kind, kind), round(ml, 1)))
                    if kk:                                    # Kalshi's own ask + fee (live file, ~15 min) over the feed's Kalshi price
                        ko["Kalshi"] = [kal_price(kk[0]), None]
                    po = {b: v[0] for b, v in ko.items() if isinstance(v, list) and v}
                    pu = {b: v[0] for b, v in u.items() if isinstance(v, list) and v}
                    if kk and kk[1]:
                        pu["Kalshi"] = kal_price(1 - float(kk[1]))   # NO at its ask (1 - the YES bid) + fee
                    # the main line's fair: sharp consensus (leave the bet's book out), NHL goals without Pinnacle the four-book
                    # fair, NFL TDs without a sharp price the books' fair, else the books' two-way median
                    if pin.get("o") is not None and float(pin.get("l", ml)) == ml:
                        parts = pin.get("parts") or {}
                        fo = (lambda b, parts=parts, f0=pin["o"]: cons_of(parts, b, sport) if parts else f0)
                    elif kind == "goal" and (e.get("f4") or {}).get("o") is not None:
                        parts = e["f4"].get("parts") or {}
                        fo = (lambda b, parts=parts, f0=e["f4"]["o"]: cons_of(parts, b, sport) if parts else f0)
                    elif kind == "td":
                        fo = (lambda b, o=o: _td_books_fair(o, b))
                    else:
                        fo = (lambda b, o=o, u=u: _fair_two_way(o, u, b))
                    emit(gid, nm, key, kind, "o", ml, po, fo, e.get("p"), True)
                    if pu:
                        fu = (lambda b, fo=fo: (lambda x: None if x is None else 1 - x)(fo(b)))
                        emit(gid, nm, key, kind, "u", ml, pu, fu, None if e.get("p") is None else 1 - e["p"], True)
                    for l, a in (e.get("a") or {}).items():
                        try:
                            lf = float(l)
                        except ValueError:
                            continue
                        if lf == ml:
                            continue
                        ao, au = dict(a.get("o") or {}), a.get("u") or {}
                        kk = kal.get((str(gid), nm, LIVE_K.get(kind, kind), round(lf, 1)))
                        if kk:
                            ao["Kalshi"] = [kal_price(kk[0]), None]
                        fa = a.get("pin") if a.get("pin") is not None else _fair_two_way(a.get("o"), au)
                        emit(gid, nm, key, kind, "o", lf, {b: v[0] for b, v in ao.items() if isinstance(v, list) and v}, fa, a.get("p"), False)
                        if au and fa is not None:
                            emit(gid, nm, key, kind, "u", lf, {b: v[0] for b, v in au.items() if isinstance(v, list) and v}, 1 - fa,
                                 None if a.get("p") is None else 1 - a["p"], False)
        # game lines (moneyline, totals) at the three books; fair = Pinnacle's no-vig, else the books' two-way median
        for gid, g in B.items():
            for side, other in (("home", "away"), ("away", "home")):
                ml = g.get("ml") or {}
                po, pu = ml.get(side) or {}, ml.get(other) or {}
                if isinstance(po.get("Pinnacle"), list) and isinstance(pu.get("Pinnacle"), list):
                    a, c = 1 / dec(po["Pinnacle"][0]), 1 / dec(pu["Pinnacle"][0])
                    f = a / (a + c)
                else:
                    f = _fair_two_way(po, pu)
                emit(gid, None, "ml", "ml", side, 0.0, {b: v[0] for b, v in po.items() if isinstance(v, list) and v}, f, None, True)
            for l, t in (g.get("total") or {}).items():
                try:
                    lf = float(l)
                except ValueError:
                    continue
                for side, other in (("over", "under"), ("under", "over")):
                    po, pu = t.get(side) or {}, t.get(other) or {}
                    if isinstance(po.get("Pinnacle"), list) and isinstance(pu.get("Pinnacle"), list):
                        a, c = 1 / dec(po["Pinnacle"][0]), 1 / dec(pu["Pinnacle"][0])
                        f = a / (a + c)
                    else:
                        f = _fair_two_way(po, pu)
                    emit(gid, None, f"total{lf:g}", "total", side, lf, {b: v[0] for b, v in po.items() if isinstance(v, list) and v}, f, None, True)
    return out


def leg_key(c):
    return c["id"]


# ---------------------------------------------------------------- the ledger
def bets_all():
    out = []
    for fp in sorted(glob.glob(os.path.join(PAPER, "bets", "????-??-??.json"))):
        d = jl(fp)
        for b in d.get("bets", []):
            b.setdefault("day", d.get("date"))
            out.append(b)
    return out


def results():
    return jl(os.path.join(PAPER, "results.json"))


def account(at=None):
    """bankroll (settled), pending stakes, record."""
    R = results()
    bets = bets_all()
    net = sum((R[b["id"]]["ret"] - b["stake"]) for b in bets if (R.get(b["id"]) or {}).get("res") in ("W", "L", "V"))
    pend = [b for b in bets if (R.get(b["id"]) or {}).get("res") not in ("W", "L", "V")]
    return dict(bankroll=round(START + net, 2), pending=round(sum(b["stake"] for b in pend), 2), n_pending=len(pend), bets=bets, R=R)


def unit_for(bankroll):
    pct = DRAW_PCT if bankroll < DRAW_LINE else UNIT_PCT
    return float(min(UNIT_MAX, max(UNIT_MIN, round(bankroll * pct))))


def record(path, dry=False):
    picks = jl(path)
    ps = picks.get("parlays") if isinstance(picks, dict) else picks
    if not isinstance(ps, list) or not ps:
        raise SystemExit("record: picks file needs {'parlays': [{book, why, legs: [{id, why}], stake?}]}")
    at = now_utc()
    day = et_day(at)
    if day < START_DATE:
        print(f"record: the account opens on {START_DATE} (ET); today is {day} -- checking only")
        dry = True
    A = account(at)
    fp = os.path.join(PAPER, "bets", f"{day}.json")
    D = jl(fp) or dict(date=day, bets=[])
    if "unit" not in D:
        D["bankroll_open"], D["unit"] = A["bankroll"], unit_for(A["bankroll"])
    unit = D["unit"]
    errs = []
    if A["bankroll"] < STOP_LINE:
        errs.append(f"bankroll {money(A['bankroll'])} is under the {money(STOP_LINE)} stop line: no new bets until a review")
    idx = {}
    for c in slate(at=at):
        idx[(c["id"], c["book"])] = c
    today = D["bets"]
    used = {}
    for b in today:
        for L in b["legs"]:
            used[L["id"]] = used.get(L["id"], 0) + 1
    units_used = sum(b["stake"] for b in today) / unit if unit else 0
    new, avail = [], A["bankroll"] - A["pending"]
    for i, p in enumerate(ps, 1):
        tag = f"parlay {i}"
        book = p.get("book")
        legs = p.get("legs") or []
        why = str(p.get("why") or "").strip()
        if book not in BOOKS:
            errs.append(f"{tag}: book must be one of {', '.join(BOOKS)} (one ticket at one book)"); continue
        if not LEGS[0] <= len(legs) <= LEGS[1]:
            errs.append(f"{tag}: {len(legs)} legs (parlays only: {LEGS[0]}-{LEGS[1]})"); continue
        if len(why) < 20:
            errs.append(f"{tag}: needs a reason for the parlay ('why', a sentence or more)")
        got, games = [], set()
        for L in legs:
            lid = str(L.get("id") or "")
            parts = lid.split("|")
            if OVERS_PROPS_ONLY and (len(parts) != 6 or parts[2] == "-" or parts[5] != "o"):
                errs.append(f"{tag}: {lid} is not a player-prop over (overs on player props only: no unders, no game lines)"); continue
            c = idx.get((lid, book))
            if not c:
                errs.append(f"{tag}: no current {book} price for {lid} (not offered there, game started or within {MIN_LEAD_MIN} min, or a typo)"); continue
            if len(str(L.get("why") or "").strip()) < 15:
                errs.append(f"{tag}: leg {c['lab']} needs its own reason ('why')")
            if (c["sport"], c["gid"]) in games:
                errs.append(f"{tag}: two legs from {c['game']} (one leg per game)")
            games.add((c["sport"], c["gid"]))
            if not LEG_Q[0] <= c["q"] <= LEG_Q[1]:
                errs.append(f"{tag}: {c['lab']} at {c['price']:+d} is priced {100 * c['q']:.0f}% (legs {100 * LEG_Q[0]:.0f}-{100 * LEG_Q[1]:.0f}%)")
            if used.get(lid, 0) >= LEG_REUSE:
                errs.append(f"{tag}: {c['lab']} is already in {LEG_REUSE} of today's parlays")
            got.append(dict(c, why=str(L.get("why")).strip()))
        if len(got) != len(legs):
            continue
        for L in got:
            used[L["id"]] = used.get(L["id"], 0) + 1
        d = math.prod(L["dec"] for L in got)
        if d > MAX_DEC:
            errs.append(f"{tag}: pays {d:.1f}x (max {MAX_DEC:g}x)")
        if any(sorted(x["id"] for x in b["legs"]) == sorted(L["id"] for L in got) and b["book"] == book for b in today + new):
            errs.append(f"{tag}: the same parlay is already in today's bets")
        size = LONG_STAKE if d > LONG_DEC else 1.0
        stake = round(unit * size, 2)
        if p.get("stake") is not None:
            try:
                ask = float(p["stake"])
            except (TypeError, ValueError):
                ask = stake
            if ask > stake + 1e-9:
                errs.append(f"{tag}: stake {money(ask)} is over the rule ({money(stake)} = {size:g} unit)")
            stake = max(1.0, round(min(ask, stake), 2))
        units_used += stake / unit
        if units_used > MAX_UNITS_DAY + 1e-9:
            errs.append(f"{tag}: over the daily limit of {MAX_UNITS_DAY:g} units ({money(MAX_UNITS_DAY * unit)})")
        if stake > avail + 1e-9:
            errs.append(f"{tag}: stake {money(stake)} is more than the available {money(avail)}")
        avail -= stake
        q = math.prod(L["q"] for L in got)
        pc = math.prod(L["p"] for L in got)
        new.append(dict(id=f"{day}-{len(today) + len(new) + 1}", t=at.isoformat(timespec="seconds"), book=book, stake=stake,
                        dec=round(d, 3), odds=amer(d), q=round(q, 4), p=round(pc, 4), edge=round(pc * d - 1, 4), why=why,
                        legs=[{k: L[k] for k in ("id", "sport", "gid", "game", "start", "name", "kind", "side", "line", "lab", "book",
                                                 "price", "dec", "q", "fair", "ours", "p", "edge", "main", "sig", "why")} for L in got]))
    if len(today) + len(new) > MAX_BETS_DAY:
        errs.append(f"{len(today)} already today + {len(new)} new is over {MAX_BETS_DAY} parlays a day")
    if errs:
        print("record: NOTHING recorded --")
        for e in errs:
            print("  -", e)
        raise SystemExit(1)
    for b in new:
        print(f"  {b['id']}: {b['book']} {len(b['legs'])} legs {b['odds']:+d} ({b['dec']:.2f}x) stake {money(b['stake'])}  "
              f"priced {100 * b['q']:.1f}%, our chance {100 * b['p']:.1f}% (edge {100 * b['edge']:+.1f}%)")
        for L in b["legs"]:
            print(f"      {L['lab']} {L['price']:+d} [{L['game']}]  fair {100 * L['fair']:.0f}%  chance {100 * L['p']:.0f}%")
    if dry:
        print(f"record: checked {len(new)} parlay(s), dry run: nothing written (unit {money(unit)}, bankroll {money(A['bankroll'])})")
        return
    D["bets"] = today + new
    os.makedirs(os.path.dirname(fp), exist_ok=True)
    with open(fp, "w", encoding="utf-8") as f:
        json.dump(D, f, indent=1)
    print(f"record: {len(new)} parlay(s) recorded in paper/bets/{day}.json (unit {money(unit)}; today {len(D['bets'])} of {MAX_BETS_DAY})")


# ---------------------------------------------------------------- grading (markets.yml, every ~15 min)
def grade():
    import importlib
    sys.path.insert(0, HERE)
    PL = importlib.import_module("parlays")
    R = results()
    games, changed = {}, 0
    for b in bets_all():
        r = R.get(b["id"]) or {}
        if r.get("res") in ("W", "L", "V"):
            continue
        res = list(r.get("legs") or [None] * len(b["legs"]))
        for i, L in enumerate(b["legs"]):
            if res[i] in ("W", "L", "P", "V"):
                continue
            key = (L["sport"], L["gid"])
            if key not in games:
                try:
                    games[key] = PL.game_for(L["sport"], L["gid"], L["start"])
                except Exception as e:                                            # noqa: BLE001
                    print(f"paper: {L['gid']} not graded ({type(e).__name__})")
                    games[key] = None
            g = games[key]
            if g is None:
                if (now_utc() - when(L["start"])).days >= 3:
                    res[i] = "V"                                            # never settled: void, as the parlays record does
                continue
            side = L["side"]
            leg = dict(kind=L["kind"], side=side, line=L["line"] if L["kind"] not in ("ml",) else None, name=L.get("name") or "")
            res[i] = PL.settle(leg, g)
        if "L" in res:
            out, ret = "L", 0.0
        elif None in res:
            out, ret = None, None
        elif "W" in res:
            out = "W"
            ret = round(b["stake"] * math.prod(L["dec"] for L, x in zip(b["legs"], res) if x == "W"), 2)
        else:
            out, ret = "V", b["stake"]                                     # every leg void / pushed: stake back
        if res != r.get("legs") or out != r.get("res"):
            R[b["id"]] = dict(res=out, legs=res, ret=ret, t=now_utc().isoformat(timespec="seconds"))
            changed += 1
    if changed:
        os.makedirs(PAPER, exist_ok=True)
        with open(os.path.join(PAPER, "results.json"), "w", encoding="utf-8") as f:
            json.dump(R, f, indent=1, sort_keys=True)
    A = account()
    print(f"paper: {changed} bet(s) updated; bankroll {money(A['bankroll'])}, {A['n_pending']} pending ({money(A['pending'])})")


# ---------------------------------------------------------------- report (for the routine to read)
def _bucket(rows, key):
    out = {}
    for b, r in rows:
        k = key(b)
        o = out.setdefault(k, dict(n=0, w=0, st=0.0, ret=0.0, q=0.0))
        o["n"] += 1; o["w"] += r["res"] == "W"; o["st"] += b["stake"]; o["ret"] += r["ret"]; o["q"] += b["q"]
    return out


def report():
    A = account()
    R, bets = A["R"], A["bets"]
    done = [(b, R[b["id"]]) for b in bets if (R.get(b["id"]) or {}).get("res") in ("W", "L")]
    st = sum(b["stake"] for b, _ in done)
    ret = sum(r["ret"] for _, r in done)
    print(f"PAPER ACCOUNT  bankroll {money(A['bankroll'])} (start {money(START)} on {START_DATE}); "
          f"available {money(A['bankroll'] - A['pending'])}; unit now {money(unit_for(A['bankroll']))}")
    if done:
        print(f"settled {len(done)}: won {sum(r['res'] == 'W' for _, r in done)} vs {sum(b['q'] for b, _ in done):.1f} priced "
              f"vs {sum(b['p'] for b, _ in done):.1f} our chance; staked {money(st)}, returned {money(ret)}, ROI {100 * (ret - st) / st:+.1f}%")
        for name, fn in (("by book", lambda b: b["book"]), ("by legs", lambda b: f"{len(b['legs'])} legs"),
                         ("by sports", lambda b: "+".join(sorted({L['sport'] for L in b['legs']}))),
                         ("by payout", lambda b: "<=3x" if b["dec"] <= 3 else "3-6x" if b["dec"] <= 6 else ">6x")):
            print(f"  {name}: " + "; ".join(f"{k} {o['w']}/{o['n']} (priced {o['q']:.1f}) {100 * (o['ret'] - o['st']) / o['st']:+.0f}%"
                                         for k, o in sorted(_bucket(done, fn).items())))
        legs = [(L, x) for b, r in done for L, x in zip(b["legs"], r["legs"]) if x in ("W", "L")]
        if legs:
            print(f"  legs: hit {sum(x == 'W' for _, x in legs)} of {len(legs)} vs {sum(L['q'] for L, _ in legs):.1f} priced vs "
                  f"{sum(L['p'] for L, _ in legs):.1f} our chance")
            kinds = {}
            for L, x in legs:
                k = f"{L['sport']} {STAT.get(L['kind'], L['kind'])} {'over' if L['side'] in ('o', 'over') else 'under' if L['side'] in ('u', 'under') else L['side']}"
                o = kinds.setdefault(k, [0, 0, 0.0])
                o[0] += 1; o[1] += x == "W"; o[2] += L["q"]
            print("  legs by type: " + "; ".join(f"{k} {w}/{n} (priced {q:.1f})" for k, (n, w, q) in sorted(kinds.items(), key=lambda z: -z[1][0])))
    days = {}
    for b in bets:
        days.setdefault(b["day"], []).append(b)
    print("recent days:")
    for d in sorted(days)[-7:]:
        bs = days[d]
        rs = [R.get(b["id"]) or {} for b in bs]
        net = sum((r["ret"] - b["stake"]) for b, r in zip(bs, rs) if r.get("res") in ("W", "L", "V"))
        print(f"  {d}: {len(bs)} bets, {sum(r.get('res') == 'W' for r in rs)}-{sum(r.get('res') == 'L' for r in rs)}, "
              f"{sum(r.get('res') not in ('W', 'L', 'V') for r in rs)} pending, net {money(net, True)}")
        for b, r in zip(bs, rs):
            print(f"    {b['id']} {b['book']} {b['odds']:+d} {money(b['stake'])} -> {r.get('res') or 'pending'}: " +
                  " | ".join(f"{L['lab']} {L['price']:+d} [{x or '…'}]" for L, x in zip(b["legs"], r.get("legs") or [None] * len(b["legs"]))))


# ---------------------------------------------------------------- the page
CSS = """
.pp{display:grid;gap:18px;max-width:980px;margin:0 auto;width:100%}
.pp .phd{display:grid;gap:6px}.pp .phd p{margin:0;color:var(--muted);max-width:70ch}
.pkp{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}
.pkp div{background:var(--surface);border:1px solid var(--faint);border-radius:10px;padding:12px 14px;display:grid;gap:2px}
.pkp b{font-family:var(--display);font-size:30px;line-height:1.05;font-weight:700}.pkp span{color:var(--muted);font-size:12.5px}
.pup{color:var(--good)}.pdn{color:var(--bad)}
.pp details{background:var(--surface);border:1px solid var(--faint);border-radius:10px}
.pp details>summary{cursor:pointer;list-style:none;padding:12px 14px;display:flex;flex-wrap:wrap;gap:6px 12px;align-items:baseline}
.pp details>summary::-webkit-details-marker{display:none}
.pp details>summary::before{content:"▸";color:var(--muted);width:12px;flex:none}.pp details[open]>summary::before{content:"▾"}
.pp details>summary .t{font-weight:600;font-size:16px}.pp details>summary .m{color:var(--muted);font-size:13.5px}
.pp .pbd{padding:0 14px 14px;display:grid;gap:12px}
.pchip{display:inline-block;border-radius:999px;padding:1px 9px;font-size:12.5px;font-weight:600;border:1px solid var(--faint)}
.pchip.w{color:var(--good);border-color:color-mix(in srgb,var(--good) 45%,transparent)}
.pchip.l{color:var(--bad);border-color:color-mix(in srgb,var(--bad) 45%,transparent)}
.pchip.p{color:var(--muted)}
.pbet{border:1px solid var(--faint);border-radius:10px;padding:12px;display:grid;gap:8px;background:var(--bg)}
.pbet .ptop{display:flex;flex-wrap:wrap;gap:6px 10px;align-items:baseline}.pbet .ptop b{font-size:15px}
.pbet .ptop .m{color:var(--muted);font-size:13px}
.pwhy{margin:0;font-size:14px;line-height:1.5}.pwhy i{font-style:normal;color:var(--muted);font-weight:600;margin-right:4px}
.plegs{list-style:none;margin:0;padding:0;display:grid;gap:8px}
.plegs li{display:grid;grid-template-columns:18px minmax(0,1fr);gap:2px 8px}
.plegs .r{font-weight:700;text-align:center}.plegs .r.w{color:var(--good)}.plegs .r.l{color:var(--bad)}.plegs .r.p{color:var(--muted)}
.plegs .n{font-weight:600}.plegs .n small{font-weight:500;color:var(--muted);margin-left:6px;font-size:12.5px}
.plegs .x{grid-column:2;color:var(--muted);font-size:13px;line-height:1.45}
.plegs .y{grid-column:2;font-size:13.5px;line-height:1.45}
.psg{grid-column:2;display:flex;flex-wrap:wrap;gap:4px}.psg span{font-size:11.5px;border:1px solid var(--faint);border-radius:6px;padding:0 6px;color:var(--muted)}
.pmd{font-size:14px;line-height:1.55}.pmd h3,.pmd h4{font-family:var(--display);text-transform:uppercase;letter-spacing:.02em;margin:10px 0 4px;font-size:17px}
.pmd p{margin:4px 0}.pmd ul{margin:4px 0;padding-left:20px}.pmd li{margin:2px 0}.pmd code{background:var(--sunk);padding:0 4px;border-radius:4px}
.pnb{color:var(--muted);font-size:13.5px;margin:0}
.pchart{background:var(--surface);border:1px solid var(--faint);border-radius:10px;padding:12px 14px}.pchart .cw,.pchart .cn{position:relative}.pchart .cn{display:none}
.pchart h2{font-size:17px;margin-bottom:6px}.pchart svg{display:block;width:100%;height:auto;overflow:visible}
.pchart .ptip{position:absolute;pointer-events:none;background:var(--tip-bg);color:var(--tip-ink);border-radius:6px;padding:4px 8px;font-size:12.5px;white-space:nowrap;display:none}
@media (max-width:560px){.pchart .cw{display:none}.pchart .cn{display:block}.pkp b{font-size:25px}.pp details>summary{padding:11px 12px}.pp .pbd{padding:0 10px 12px}.pbet{padding:10px}}
"""


def _md(text):
    """the journal's markdown: headings, bullets, paragraphs, **bold**, `code`."""
    out, lst = [], False
    for raw in (text or "").splitlines():
        line = raw.rstrip()
        esc = html.escape(line.strip())
        esc = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", esc)
        esc = re.sub(r"`(.+?)`", r"<code>\1</code>", esc)
        if re.match(r"^\s*[-*] ", line):
            if not lst:
                out.append("<ul>"); lst = True
            out.append(f"<li>{re.sub(r'^[-*] ', '', esc)}</li>")
            continue
        if lst:
            out.append("</ul>"); lst = False
        m = re.match(r"^(#{1,4}) (.*)", line.strip())
        if m:
            out.append(f"<h{3 if len(m.group(1)) <= 2 else 4}>{esc[len(m.group(1)) + 1:]}</h{3 if len(m.group(1)) <= 2 else 4}>")
        elif esc:
            out.append(f"<p>{esc}</p>")
    if lst:
        out.append("</ul>")
    return "".join(out)


def _when_et(ts):
    t = when(ts).astimezone(ET)
    return t.strftime("%a %-I:%M %p") if os.name != "nt" else t.strftime("%a %#I:%M %p")


def _chart(points):
    """bankroll after each settled day: one series (line + dots), a crosshair tooltip; a wide and a narrow drawing so the
    labels stay readable on phones."""
    if len(points) < 2:
        return ""
    return (f'<div class="pchart"><h2>Bankroll after each day</h2>{_chart_svg(points, 640, 180, "cw")}{_chart_svg(points, 360, 210, "cn")}</div>')


def _chart_svg(points, W, H, cls):
    pl, pr, pt, pb = 46, 14, 10, 24
    ys = [v for _, v in points] + [START]
    lo, hi = min(ys), max(ys)
    pad = max(5.0, (hi - lo) * .12)
    lo, hi = lo - pad, hi + pad
    X = lambda i: pl + (W - pl - pr) * (i / (len(points) - 1))
    Y = lambda v: pt + (H - pt - pb) * (1 - (v - lo) / (hi - lo))
    step = next(s for s in (5, 10, 20, 25, 50, 100, 200, 500) if (hi - lo) / s <= 5)
    ticks, v = [], math.ceil(lo / step) * step
    while v <= hi:
        ticks.append(v); v += step
    g = "".join(f'<line x1="{pl}" x2="{W - pr}" y1="{Y(t):.1f}" y2="{Y(t):.1f}" stroke="var(--faint)" stroke-width="1"/>'
                f'<text x="{pl - 6}" y="{Y(t) + 4:.1f}" text-anchor="end" font-size="12" fill="var(--muted)">${t:,.0f}</text>' for t in ticks)
    base = f'<line x1="{pl}" x2="{W - pr}" y1="{Y(START):.1f}" y2="{Y(START):.1f}" stroke="var(--whisker)" stroke-dasharray="4 4" stroke-width="1"/>'
    path = " ".join(f"{'M' if i == 0 else 'L'}{X(i):.1f},{Y(v):.1f}" for i, (_, v) in enumerate(points))
    dots = "".join(f'<circle cx="{X(i):.1f}" cy="{Y(v):.1f}" r="4" fill="var(--accent)" stroke="var(--surface)" stroke-width="2"/>'
                   for i, (_, v) in enumerate(points)) if len(points) <= 40 else ""
    lab = lambda d: dt.date.fromisoformat(d).strftime("%b %d").replace(" 0", " ")
    show = {0, len(points) - 1} | (set(range(len(points))) if len(points) <= (8 if W > 400 else 4) else set())
    xl = "".join(f'<text x="{X(i):.1f}" y="{H - 6}" text-anchor="{"start" if i == 0 else "end" if i == len(points) - 1 else "middle"}" '
                 f'font-size="12" fill="var(--muted)">{lab(d)}</text>' for i, (d, _) in enumerate(points) if i in show)
    data = json.dumps([[dt.date.fromisoformat(d).strftime("%a %b %d").replace(" 0", " "), round(v, 2), round(X(i), 1), round(Y(v), 1)]
                       for i, (d, v) in enumerate(points)])
    return (f'<div class="{cls}"><svg viewBox="0 0 {W} {H}" role="img" aria-label="Bankroll after each settled day">'
            f'{g}{base}<path d="{path}" fill="none" stroke="var(--accent)" stroke-width="2"/>{dots}{xl}'
            f'<line class="xh" y1="{pt}" y2="{H - pb}" stroke="var(--muted)" stroke-width="1" opacity="0"/>'
            f'<rect x="{pl}" y="0" width="{W - pl - pr}" height="{H}" fill="transparent"/></svg><div class="ptip"></div></div>'
            f"<script>(function(){{var P={data},c=document.currentScript.previousElementSibling,s=c.querySelector('svg'),t=c.querySelector('.ptip'),x=c.querySelector('.xh');"
            f"function mv(e){{var r=s.getBoundingClientRect(),k={W}/r.width,mx=(e.clientX-r.left)*k,b=P[0];P.forEach(function(p){{if(Math.abs(p[2]-mx)<Math.abs(b[2]-mx))b=p}});"
            f"x.setAttribute('x1',b[2]);x.setAttribute('x2',b[2]);x.setAttribute('opacity',.5);t.style.display='block';t.textContent=b[0]+' · $'+b[1].toFixed(2);"
            f"var L=b[2]/k+14;if(L+t.offsetWidth>r.width)L=b[2]/k-t.offsetWidth-14;t.style.left=Math.max(0,L)+'px';t.style.top=Math.max(0,b[3]/k-10)+'px'}}"
            f"function out(){{t.style.display='none';x.setAttribute('opacity',0)}}s.addEventListener('pointermove',mv);s.addEventListener('pointerdown',mv);s.addEventListener('pointerleave',out)}})();</script>")


def _leg_html(L, res):
    mark = {"W": ("✓", "w"), "L": ("✗", "l"), "P": ("–", "p"), "V": ("void", "p")}.get(res, ("•", "p"))
    st = _when_et(L["start"])
    bits = [f"{L['price']:+d} at {html.escape(L['book'])}", f"priced {100 * L['q']:.0f}%", f"fair {100 * L['fair']:.0f}%"]
    if L.get("ours") is not None:
        bits.append(f"our model {100 * L['ours']:.0f}%")
    bits.append(f"chance used {100 * L['p']:.0f}% (edge {100 * L['edge']:+.0f}%)")
    sig = "".join(f"<span>{html.escape(s)}</span>" for s in (L.get("sig") or [])[:6])
    return (f'<li><span class="r {mark[1]}">{mark[0]}</span><span class="n">{html.escape(L["lab"])}<small>{html.escape(L["game"])} · {st}</small></span>'
            f'<span class="x">{" · ".join(bits)}</span><span class="y"><i style="font-style:normal;color:var(--muted);font-weight:600">Why:</i> {html.escape(L.get("why") or "")}</span>'
            + (f'<span class="psg">{sig}</span>' if sig else "") + "</li>")


def _bet_html(b, r):
    res = r.get("res")
    if res == "W":
        chip = f'<span class="pchip w">Won {money(r["ret"] - b["stake"], True)}</span>'
    elif res == "L":
        chip = f'<span class="pchip l">Lost {money(-b["stake"], True)}</span>'
    elif res == "V":
        chip = '<span class="pchip p">Void · stake back</span>'
    else:
        chip = '<span class="pchip p">Pending</span>'
    lr = r.get("legs") or [None] * len(b["legs"])
    return (f'<div class="pbet"><div class="ptop"><b>{html.escape(b["book"])} · {len(b["legs"])}-leg parlay</b>{chip}'
            f'<span class="m">{money(b["stake"])} at {b["odds"]:+d} ({b["dec"]:.2f}x) to return {money(b["stake"] * b["dec"])} · priced {100 * b["q"]:.1f}%, '
            f'our chance {100 * b["p"]:.1f}% · placed {_when_et(b["t"])}</span></div>'
            f'<p class="pwhy"><i>Why this parlay:</i>{html.escape(b.get("why") or "")}</p>'
            f'<ul class="plegs">{"".join(_leg_html(L, x) for L, x in zip(b["legs"], lr))}</ul></div>')


def page():
    A = account()
    R, bets = A["R"], A["bets"]
    days = {}
    for b in bets:
        days.setdefault(b["day"], []).append(b)
    jdir = os.path.join(PAPER, "journal")
    for f in glob.glob(os.path.join(jdir, "????-??-??.md")):
        days.setdefault(os.path.basename(f)[:10], [])
    days = {d: v for d, v in days.items() if d >= START_DATE}
    done = [(b, R[b["id"]]) for b in bets if (R.get(b["id"]) or {}).get("res") in ("W", "L")]
    st, ret = sum(b["stake"] for b, _ in done), sum(r["ret"] for _, r in done)
    net = A["bankroll"] - START
    w = sum(r["res"] == "W" for _, r in done)
    kp = (f'<div class="pkp"><div><span>Bankroll</span><b class="{"pup" if net > 0 else "pdn" if net < 0 else ""}">{money(A["bankroll"])}</b>'
          f'<span>{money(net, True)} since {money(START)}</span></div>'
          f'<div><span>Record</span><b>{w}–{len(done) - w}</b><span>{sum(b["q"] for b, _ in done):.1f} wins priced · {sum(b["p"] for b, _ in done):.1f} expected</span></div>'
          f'<div><span>Return on stakes</span><b class="{"pup" if ret > st else "pdn" if ret < st else ""}">{(100 * (ret - st) / st if st else 0):+.1f}%</b><span>{money(st)} staked</span></div>'
          f'<div><span>Pending</span><b>{money(A["pending"])}</b><span>{A["n_pending"]} parlay{"s" if A["n_pending"] != 1 else ""} open · unit {money(unit_for(A["bankroll"]))}</span></div></div>')
    # bankroll after each day whose bets are all settled
    pts, bal = [(START_DATE, START)], START
    for d in sorted(days):
        bs = days[d]
        if not bs:
            continue
        rs = [R.get(b["id"]) or {} for b in bs]
        if any(r.get("res") not in ("W", "L", "V") for r in rs):
            break
        bal += sum(r["ret"] - b["stake"] for b, r in zip(bs, rs))
        pts.append((d, bal))
    if len(pts) > 1 and pts[1][0] == START_DATE:
        pts = pts[1:]
        pts.insert(0, ((dt.date.fromisoformat(START_DATE) - dt.timedelta(days=1)).isoformat(), START))
    rules = ("<ul><li><b>Overs on player props only</b> (from Nov 12 evening): no unders, no game totals or moneylines.</li>"
             f"<li><b>Parlays only</b>, {LEGS[0]}-{LEGS[1]} legs, one leg per game, every leg at the same book (one ticket at Hard Rock, Novig or Kalshi), "
             f"priced at what that book showed when the bet was recorded (Kalshi with its 7% fee). Each leg priced {100 * LEG_Q[0]:.0f}-{100 * LEG_Q[1]:.0f}%; "
             f"nothing paying over {MAX_DEC:g}x.</li>"
             f"<li><b>Stake:</b> one unit = {100 * UNIT_PCT:g}% of the bankroll at the day’s first bet, rounded, {money(UNIT_MIN)}-{money(UNIT_MAX)} "
             f"({money(unit_for(START))} at the start); parlays paying over {LONG_DEC:g}x take half a unit. Below {money(DRAW_LINE)} the unit drops to "
             f"{100 * DRAW_PCT:g}%; below {money(STOP_LINE)} no new bets until a review.</li>"
             f"<li><b>Daily limit:</b> {MAX_BETS_DAY} parlays and {MAX_UNITS_DAY:g} units ({100 * UNIT_PCT * MAX_UNITS_DAY:g}% of the bankroll); a leg in at most "
             f"{LEG_REUSE} of the day’s parlays. A day with nothing good gets no bets.</li>"
             f"<li><b>Every bet has its reasons</b> (the parlay and each leg) and is checked by <code>paper.py record</code> against the live prices: "
             f"no bet after its game started, none at a price the book didn’t show. Graded from the box scores like the site’s parlays "
             f"(a void or pushed leg drops out, as at the books).</li>"
             f"<li><b>Who decides:</b> Claude, once a day (and Sunday mornings for the NFL), using the same pages and data you have, "
             f"with research agents asked specific questions each day; the notes for each day are under it.</li></ul>")
    lessons = ""
    lp = os.path.join(PAPER, "lessons.md")
    if os.path.exists(lp):
        lessons = f'<details><summary><span class="t">What I’ve learned so far</span><span class="m">updated daily</span></summary><div class="pbd pmd">{_md(open(lp, encoding="utf-8").read())}</div></details>'
    blocks = []
    for i, d in enumerate(sorted(days, reverse=True)):
        bs = days[d]
        rs = [R.get(b["id"]) or {} for b in bs]
        wn, ls = sum(r.get("res") == "W" for r in rs), sum(r.get("res") == "L" for r in rs)
        pend = sum(r.get("res") not in ("W", "L", "V") for r in rs)
        dn = sum((r["ret"] - b["stake"]) for b, r in zip(bs, rs) if r.get("res") in ("W", "L", "V"))
        lab = dt.date.fromisoformat(d).strftime("%a, %b %d").replace(" 0", " ")
        meta = [f"{len(bs)} parlay{'s' if len(bs) != 1 else ''}" if bs else "no bets"]
        if wn + ls:
            meta.append(f"{wn}–{ls}")
        if pend:
            meta.append(f"{pend} pending")
        chip = (f'<span class="pchip {"w" if dn > 0 else "l" if dn < 0 else "p"}">{money(dn, True)}</span>' if wn + ls else "")
        jp = os.path.join(jdir, f"{d}.md")
        notes = (f'<details><summary><span class="t" style="font-size:14.5px">Notes &amp; research</span><span class="m">what I asked, what I found, the plan</span></summary>'
                 f'<div class="pbd pmd">{_md(open(jp, encoding="utf-8").read())}</div></details>') if os.path.exists(jp) else ""
        body = "".join(_bet_html(b, r) for b, r in zip(bs, rs)) or '<p class="pnb">No bets this day (the reasons are in the notes).</p>'
        blocks.append(f'<details{" open" if i == 0 else ""}><summary><span class="t">{lab}</span>{chip}<span class="m">{" · ".join(meta)}</span></summary>'
                      f'<div class="pbd">{body}{notes}</div></details>')
    if not blocks:
        blocks.append(f'<p class="pnb">The account opens on {dt.date.fromisoformat(START_DATE).strftime("%A, %B %d").replace(" 0", " ")}: '
                      f'the first bets and their reasons will appear here, one block per day.</p>')
    body = (f'<style>{CSS}</style><div class="pp"><div class="phd"><div class="eyebrow">Experimental · paper money</div><h1>Paper account</h1>'
            f'<p>Claude bets a {money(START)} paper bankroll with parlays only, at Hard Rock, Novig and Kalshi prices, and writes down why. '
            f'Not advice: a test of whether the site’s tools can beat the books. Tap a day to see its bets and reasons.</p></div>{kp}{_chart(pts)}'
            f'<details><summary><span class="t">How I bet</span><span class="m">bankroll, stakes and limits</span></summary><div class="pbd pmd">{rules}</div></details>'
            f'{lessons}{"".join(blocks)}</div>')
    import importlib.util
    spec = importlib.util.spec_from_file_location("sim_site", os.path.join(HERE, "site.py"))
    S = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(S)
    S.write_page("paper.html", "Paper account · Sim Sheet", "paper", body, depth=0)
    print(f"paper page: {len(bets)} bets over {len(days)} days; bankroll {money(A['bankroll'])}")


# ---------------------------------------------------------------- the routine's branch
BRANCH = "claude/paper"      # cloud sessions push to claude/* branches; markets.yml merges the paper files into main


def pull_branch(branch=BRANCH):
    """Merge the paper files Claude pushed to `branch` into this checkout: bets by id (union), journal / lessons as pushed.
    results.json is the grader's and is never taken from the branch."""
    import subprocess
    g = lambda *a: subprocess.run(["git", "-C", SITE, *a], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if g("fetch", "-q", "--depth=1", "origin", f"+refs/heads/{branch}:refs/remotes/origin/{branch}").returncode:
        print(f"paper: no '{branch}' branch to merge")
        return
    ref, n = f"origin/{branch}", 0
    for f in [x for x in g("ls-tree", "-r", "--name-only", ref, "--", "paper").stdout.splitlines() if x]:
        if not (re.match(r"^paper/bets/\d{4}-\d{2}-\d{2}\.json$", f) or re.match(r"^paper/journal/[\w.-]+\.md$", f) or f == "paper/lessons.md"):
            continue
        theirs = g("show", f"{ref}:{f}").stdout
        path = os.path.join(SITE, *f.split("/"))
        mine = open(path, encoding="utf-8").read() if os.path.exists(path) else None
        if mine == theirs or not theirs:
            continue
        if f.startswith("paper/bets/") and mine:
            try:
                A, B = json.loads(mine), json.loads(theirs)
            except ValueError:
                continue
            have = {(b["id"], b["t"]) for b in A.get("bets", [])}
            ids = {b["id"] for b in A.get("bets", [])}
            add = []
            for b in B.get("bets", []):
                if (b["id"], b["t"]) in have:
                    continue
                if b["id"] in ids:                                   # two runs used the same number: give it the next one
                    b = dict(b, id=f"{A['date']}-{max(int(x.rsplit('-', 1)[1]) for x in ids) + 1}")
                ids.add(b["id"]); add.append(b)
            if not add:
                continue
            A["bets"] = sorted(A["bets"] + add, key=lambda b: int(b["id"].rsplit("-", 1)[1]))
            for k in ("unit", "bankroll_open"):
                if k not in A and k in B:
                    A[k] = B[k]
            theirs = json.dumps(A, indent=1)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(theirs)
        n += 1
    print(f"paper: merged {n} file(s) from '{branch}'")


def main(argv):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    cmd = argv[0] if argv else "report"
    if cmd == "slate":
        sports = [a for a in argv[1:] if a in ("nba", "nhl", "nfl")] or ["nba", "nhl", "nfl"]
        out = next((a[6:] for a in argv[1:] if a.startswith("--out=")), os.path.join(tempfile.gettempdir(), "paper_slate.json"))
        cs = slate(sports)
        with open(out, "w", encoding="utf-8") as f:
            json.dump(dict(generated=now_utc().isoformat(timespec="seconds"), legs=cs), f, indent=0)
        by = {}
        for c in cs:
            by.setdefault(c["sport"], []).append(c)
        print(f"slate: {len(cs)} priced legs at {', '.join(BOOKS)} -> {out}")
        for sp, xs in by.items():
            gs = sorted({(c["start"], c["game"]) for c in xs})
            print(f"\n{sp.upper()}: {len(xs)} legs, {len(gs)} games: " + "; ".join(f"{g} {_when_et(s)}" for s, g in gs))
            ok = [c for c in xs if LEG_Q[0] <= c["q"] <= LEG_Q[1]]
            ok.sort(key=lambda c: -c["edge"])
            print("  best edges (chance x payout - 1; claimed edges over ~15% are usually stale prices or our model talking):")
            for c in ok[:25]:
                print(f"   {c['edge']*100:+5.1f}%  {c['lab']} {c['price']:+d} @{c['book']} [{c['game']}] fair {c['fair']*100:.0f}% chance {c['p']*100:.0f}%"
                      f"{' (alt)' if not c['main'] else ''}  id={c['id']}" + (f"  · {c['sig'][0]}" if c["sig"] else ""))
    elif cmd == "record":
        if len(argv) < 2:
            raise SystemExit("record: python paper.py record picks.json [--dry]")
        record(argv[1], dry="--dry" in argv)
    elif cmd == "grade":
        grade()
    elif cmd == "pull-branch":
        pull_branch()
    elif cmd == "page":
        page()
    elif cmd == "report":
        report()
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    for part in (" ".join(sys.argv[1:]) or "report").split(" + "):
        main(part.split())
