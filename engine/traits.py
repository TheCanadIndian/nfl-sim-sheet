"""What winners share, every day (owner 2026-11-09: 'for all winning parlays and positions daily look for similarities in
them so that we can improve leg selection').

A trait that is in most winners means nothing if it is in most losers too (short prices fill every list of winners), so each
trait is scored against its price: wins vs what the prices implied, return at $1, both halves of the last 30 days, z
clustered by day. ~150 traits are scanned, so a few look good by luck every day -- hence the FORWARD CHECK: the first day a
trait clears the bar it is written to markets/traits_state.json, and from then on it is graded only on positions settled
AFTER that day. Only that forward record can say a trait helps leg selection. Display only: no card or rule reads this.

Positions = every graded over at Hard Rock / Novig / Kalshi the site tracked: Profit tags (markets/profit_tags.json; baselines
and 'expected to lose' rows left out) and every leg of every frozen parlay (parlays/<sport>/<date>.json), one row per
distinct bet (same game, player, stat, line, book), with every signal / reason that ever flagged it. Parlays = cross-game
over parlays at those books, repeats removed. Only traits known before the game (no closing-line traits).

    python traits.py          # rebuild markets/traits.json + /winners.html when new results are in
    python traits.py force    # rebuild anyway
"""
import os, re, sys, json, glob, math, html, collections, datetime as dt
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
SITE = os.environ.get("SIM_SITE") or os.path.join(HERE, "site")
OUT = os.path.join(HERE, "markets")
STATE = os.path.join(OUT, "traits_state.json")
ET = ZoneInfo("America/New_York")
OWN = {"Hard Rock", "Novig", "Kalshi"}
WINDOW = 30
FLAG_N, FLAG_HALF, FWD_N = 40, 15, 30
dec = lambda o: 1 + o / 100 if o > 0 else 1 + 100 / abs(o)


def amer(v):
    """American odds; some cards store a Kalshi price as a chance (0.55) -> odds"""
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    if 0 < v < 1:
        return -100 * v / (1 - v) if v >= .5 else 100 * (1 - v) / v
    return v if abs(v) >= 100 else None


def _norm(s):
    import unicodedata
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z ]", "", s)
    s = re.sub(r"\b(jr|sr|ii|iii|iv)\b", "", s)
    return " ".join(s.split())


def _day(t):
    try:
        x = dt.datetime.fromisoformat(str(t).replace("Z", "+00:00"))
        return (x if x.tzinfo else x.replace(tzinfo=dt.timezone.utc)).astimezone(ET)
    except (TypeError, ValueError):
        return None


KIND_TXT = (("double", "dd"), ("triple", "tdbl"), ("goal", "goal"), ("td", "td"), ("touchdown", "td"), ("rec yds", "rec_yds"), ("receiving", "rec_yds"),
            ("rush", "rush_yds"), ("pass", "pass_yds"), ("receptions", "rec"), ("points", "pts"), ("rebounds", "reb"), ("assists", "ast"),
            ("threes", "fg3m"), ("shots", "sog"), ("first basket", "fb"))
KIND_LAB = {"goal": "anytime goal", "td": "anytime TD", "rec_yds": "receiving yards", "rush_yds": "rushing yards", "pass_yds": "passing yards",
            "rec": "catches", "pts": "points", "reb": "rebounds", "ast": "assists", "fg3m": "threes", "3pm": "threes", "sog": "shots on goal",
            "dd": "double-double", "tdbl": "triple-double", "ml": "moneyline", "total": "game total over", "spread": "spread", "fg": "first goal",
            "fb": "first basket", "pra": "pts + reb + ast", "pts_reb_ast": "pts + reb + ast"}
SIG_LAB = {"value": "Value tag", "lc": "LC tag", "off": "off-market tag", "knee": "ladder knee", "open": "early opener value", "dry": "NHL goal drought 3+",
           "due": "NHL goal luck: due", "dout": "NHL vs D out", "tl": "NBA teammate link", "mbook": "NBA Novig rung under the books", "g2": "NHL 2+ goals",
           "ddtd": "double-double edge", "f4": "NHL four-book fair edge", "fx": "NHL exchanges fair edge", "fg": "NHL first goal", "fb": "NBA first basket",
           "pin-line": "Pinnacle line up", "pin-price": "Pinnacle odds drop", "pin-both": "Pinnacle line up + odds drop", "pin-rung": "Pinnacle move higher rung",
           "cons-line": "consensus line up", "cons-price": "consensus odds drop", "cons-both": "consensus line up + odds drop", "cons-rung": "consensus move higher rung"}


def kind_of(b):
    b = (b or "").lower()
    for k, w in KIND_TXT:
        if k in b:
            return w
    return "other"


def qband(q):
    for hi, lab in ((.15, "priced under 15% (+567 or longer)"), (.25, "priced 15-25% (+300..+567)"), (.35, "priced 25-35% (+186..+300)"),
                    (.45, "priced 35-45% (+122..+186)"), (.55, "priced 45-55% (about even)"), (9, "priced 55%+ (favorite)")):
        if q < hi:
            return lab


def eband(e):
    if e is None:
        return None
    for hi, lab in ((0, "claimed edge under 0"), (.05, "claimed edge 0-5%"), (.15, "claimed edge 5-15%"), (.4, "claimed edge 15-40%"), (9e9, "claimed edge 40%+")):
        if e < hi:
            return lab


def tod(t):
    h = t.hour + t.minute / 60
    return "starts before 5 PM ET" if h < 17 else "starts 5-8 PM ET" if h < 20 else "starts 8 PM ET or later"


def maps():
    M = {}
    for f, sp in (("arch_nba.json", "nba"), ("role_nfl.json", "nfl")):
        try:
            M[sp] = {_norm(k): v for k, v in json.load(open(os.path.join(SITE, "markets", f), encoding="utf-8")).items() if isinstance(v, str)}
        except (OSError, ValueError):
            M[sp] = {}
    return M


# ---------------------------------------------------------------- positions + parlays
def load():
    P = {}                                  # key -> position
    def put(key, row, sigs=(), reasons=(), card=None):
        x = P.get(key)
        if x is None:
            x = P[key] = dict(row, sigs=set(), reasons=set(), cards=set())
        elif row.get("first") and (not x.get("first") or row["first"] < x["first"]):
            x["first"] = row["first"]
        x["sigs"].update(sigs); x["reasons"].update(reasons)
        if card:
            x["cards"].add(card)
        if x.get("p") is None and row.get("p") is not None:
            x["p"] = row["p"]
    try:
        T = json.load(open(os.path.join(OUT, "profit_tags.json"), encoding="utf-8"))
    except (OSError, ValueError):
        T = {}
    for e in T.values():
        if e.get("res") not in ("W", "L") or e.get("book") not in OWN or amer(e.get("o")) is None:
            continue
        b = (e.get("bet") or "").lower()
        if b.startswith("under") or " under " in f" {b} " or e.get("grp") in ("all", "fed"):
            continue
        t = _day(e.get("start"))
        if not t:
            continue
        k = kind_of(b); m = re.search(r"(\d+(?:\.\d+)?)", b)
        line = float(m.group(1)) if m and k not in ("goal", "td", "dd", "tdbl", "fb") else .5
        src = e.get("src") or "value"
        sig = f"{e.get('msrc')}-{e.get('sig')}" if src == "move" else src
        put((e["sport"], str(e["gid"]), _norm(e["name"]), k, line, e["book"]),
            dict(sport=e["sport"], name=e["name"], kind=k, line=line, book=e["book"], o=amer(e["o"]), y=int(e["res"] == "W"), t=t, day=t.date().isoformat(),
                 p=e.get("p"), first=e.get("first"), lab=f"{e['name'].title()} {b}", straight=True), sigs=[sig])
    PL, seen = [], set()
    for sp in ("nfl", "nba", "nhl", "multi", "slate"):
        for f in sorted(glob.glob(os.path.join(HERE, "parlays", sp, "????-??-??.json"))):
            try:
                j = json.load(open(f, encoding="utf-8"))
            except (OSError, ValueError):
                continue
            for p in j.get("parlays") or []:
                L = p.get("legs") or []
                card = re.sub(r"\s*\((experimental|your books)\)", "", (p.get("card") or "").replace("·", "·"))
                if card.startswith("Same-game"):                      # no same-game cards (books don't pay the product)
                    continue
                for x in L:
                    x["price"] = amer(x.get("price"))
                good = [x for x in L if x.get("side") != "u" and x.get("book") in OWN and x.get("price") is not None]
                for x in good:
                    if x.get("res") not in ("W", "L"):
                        continue
                    t = _day(x.get("start")) or _day(j.get("date") + "T19:00:00-05:00")
                    k = {"3pm": "fg3m"}.get(x.get("kind"), x.get("kind") or "other")
                    line = float(x.get("line")) if x.get("line") is not None else .5
                    put((x.get("sport") or sp, str(x.get("gid")), _norm(x.get("name") or x.get("lab")), k, line, x["book"]),
                        dict(sport=x.get("sport") or sp, name=x.get("name") or "", kind=k, line=line, book=x["book"], o=float(x["price"]), y=int(x["res"] == "W"),
                             t=t, day=t.date().isoformat(), p=x.get("p"), first=p.get("first"), lab=x.get("lab") or "", straight=len(L) == 1),
                        reasons=list(x.get("f") or []) + list(x.get("v") or []), card=card)
                if p.get("res") not in ("W", "L") or len(L) < 2 or not p.get("d") or len(good) < len(L) or len({x.get("gid") for x in L}) < len(L):
                    continue
                key = frozenset((x.get("gid"), x.get("name"), x.get("kind"), x.get("line"), x.get("book")) for x in L)
                if key in seen:
                    continue
                seen.add(key)
                q = [1 / dec(float(x["price"])) for x in L]
                PL.append(dict(sport=sp, card=card, n=len(L), d=float(p["d"]), y=int(p["res"] == "W"), day=j.get("date"), q=q, p=p.get("p"),
                               bk={x["book"] for x in L}, ks={x.get("kind") for x in L}, sps={x.get("sport") or sp for x in L},
                               legs=[(x.get("lab") or "", x.get("price"), x.get("res")) for x in L]))
    return list(P.values()), PL


def pos_traits(x, M):
    d = dec(x["o"]); q = 1 / d; out = [f"sport {x['sport'].upper()}", f"{x['sport'].upper()} {KIND_LAB.get(x['kind'], x['kind'])}",
                                       f"at {x['book']}", f"{x['sport'].upper()} at {x['book']}", qband(q), tod(x["t"])]
    p = x.get("p")
    e = eband(p * d - 1 if isinstance(p, (int, float)) else None)
    if e:
        out.append(e)
    out.append("straight bet (a tracked tag or slate pick)" if x["straight"] or (x["sigs"] and not x["cards"]) else "only as a parlay leg")
    for s in x["sigs"]:
        out.append("signal: " + SIG_LAB.get(s, s))
    n = len(x["sigs"])
    if n >= 2:
        out.append("2+ tracked signals on it")
    for r in x["reasons"]:
        out.append("reason: " + r)
    nr = len(x["reasons"])
    if nr >= 3:
        out.append("3+ reasons stored")
    for c in x["cards"]:
        out.append("picked by: " + c)
    g = M.get(x["sport"], {}).get(_norm(x["name"]))
    if g:
        out.append(("NBA archetype: " if x["sport"] == "nba" else "NFL role: ") + g)
        out.append(f"{g} {KIND_LAB.get(x['kind'], x['kind'])}")
    f = _day(x.get("first"))
    if f:
        h = (x["t"] - f).total_seconds() / 3600
        out.append("found 12h+ before the start" if h >= 12 else "found 3-12h before" if h >= 3 else "found under 3h before")
    return sorted({o for o in out if o})


def parlay_traits(p):
    d, mq = p["d"], min(p["q"])
    out = [f"sport {p['sport'].upper()}", f"{p['n']} legs" if p["n"] < 5 else "5+ legs", "card: " + p["card"],
           next(lab for hi, lab in ((3, "pays under 3x"), (6, "pays 3-6x"), (10, "pays 6-10x"), (15, "pays 10-15x"), (30, "pays 15-30x"), (100, "pays 30-100x"), (9e9, "pays 100x+")) if d < hi)]
    if mq >= .4: out.append("every leg priced 40%+")
    if mq >= .25: out.append("every leg priced 25%+")
    elif mq < .15: out.append("a leg priced under 15%")
    else: out.append("shortest-odds leg 15-25%")
    for b in OWN:
        if p["bk"] == {b}: out.append(f"every leg at {b}")
    if len(p["ks"]) == 1: out.append("every leg the same stat")
    for k in p["ks"]:
        if k: out.append(f"has a {KIND_LAB.get(k, k)} leg")
    if len(p["sps"]) > 1: out.append("legs from 2+ sports")
    pe = p.get("p")
    if isinstance(pe, (int, float)):
        e = pe * d - 1
        out.append("claimed edge under 0" if e < 0 else "claimed edge 0-10%" if e < .1 else "claimed edge 10-25%" if e < .25 else "claimed edge 25%+")
    return sorted(set(out))


# ---------------------------------------------------------------- scoring
def score(rows, unit):
    """rows: dicts with y, q, d, day, tr. -> {trait: stats} over these rows"""
    if not rows:
        return {}
    days = sorted(r["day"] for r in rows); mid = days[len(days) // 2]
    S = collections.defaultdict(lambda: dict(n=0, w=0, e=0.0, net=0.0, n1=0, net1=0.0, n2=0, net2=0.0, dd=collections.defaultdict(float)))
    for r in rows:
        ret = r["d"] - 1 if r["y"] else -1.0
        for t in r["tr"] + ["all " + unit]:
            s = S[t]; s["n"] += 1; s["w"] += r["y"]; s["e"] += 1 / r["d"]; s["net"] += ret; s["dd"][r["day"]] += ret
            if r["day"] < mid: s["n1"] += 1; s["net1"] += ret
            else: s["n2"] += 1; s["net2"] += ret
    out = {}
    for t, s in S.items():
        v = sum(x * x for x in s["dd"].values())
        out[t] = dict(n=s["n"], w=s["w"], e=round(s["e"], 2), ret=round(s["net"] / s["n"], 4), h1=round(s["net1"] / s["n1"], 4) if s["n1"] else None,
                      h2=round(s["net2"] / s["n2"], 4) if s["n2"] else None, n1=s["n1"], n2=s["n2"], z=round(s["net"] / math.sqrt(v), 2) if v > 0 else 0.0)
    return out


def flag_ok(s):
    return (s["n"] >= FLAG_N and s["w"] > s["e"] and s["n1"] >= FLAG_HALF and s["n2"] >= FLAG_HALF and (s["h1"] or 0) > 0 and (s["h2"] or 0) > 0 and s["z"] >= 1)


def build(force=False):
    M = maps()
    pos, par = load()
    if not pos:
        print("traits: no graded positions"); return
    for x in pos:
        x["d"] = dec(x["o"]); x["tr"] = pos_traits(x, M)
    for p in par:
        p["tr"] = parlay_traits(p)
    last = max(x["day"] for x in pos)
    sig = f"{last}|{len(pos)}|{len(par)}|{sum(x['y'] for x in pos)}"
    st = {}
    try:
        st = json.load(open(STATE, encoding="utf-8"))
    except (OSError, ValueError):
        pass
    if not force and st.get("sig") == sig and os.path.exists(os.path.join(SITE, "winners.html")):
        print("traits: nothing new since", st.get("built")); return
    lo = (dt.date.fromisoformat(last) - dt.timedelta(days=WINDOW - 1)).isoformat()
    W = {"positions": score([x for x in pos if x["day"] >= lo], "positions"), "parlays": score([p for p in par if p["day"] >= lo], "parlays")}
    flags = st.get("flags", {})
    if not st.get("replayed"):
        # first build: replay every earlier day as if this had run then, so the forward check starts with history
        # (each flag uses only results up to that day; its forward record only results after it)
        days = sorted({x["day"] for x in pos})
        for d in days[14:-1]:
            l0 = (dt.date.fromisoformat(d) - dt.timedelta(days=WINDOW - 1)).isoformat()
            for unit, R in (("positions", pos), ("parlays", par)):
                for t, s in score([r for r in R if l0 <= r["day"] <= d], unit).items():
                    if not t.startswith("all ") and flag_ok(s) and f"{unit}|{t}" not in flags:
                        flags[f"{unit}|{t}"] = d
        st["replayed"] = last
    for unit, S in W.items():
        for t, s in S.items():
            if not t.startswith("all ") and flag_ok(s) and f"{unit}|{t}" not in flags:
                flags[f"{unit}|{t}"] = last
    # forward record: only positions / parlays settled after the day the trait was first flagged
    fwd = []
    for k, d0 in flags.items():
        unit, t = k.split("|", 1)
        R = [r for r in (pos if unit == "positions" else par) if r["day"] > d0 and t in r["tr"]]
        s = score(R, unit).get(t) if R else None
        n = s["n"] if s else 0
        verdict = ("too early" if n < FWD_N else "holding up" if s["w"] > s["e"] and s["ret"] > 0 and s["z"] >= 1
                   else "faded" if s["ret"] < 0 else "mixed")
        fwd.append(dict(unit=unit, trait=t, since=d0, n=n, w=s["w"] if s else 0, e=s["e"] if s else 0, ret=s["ret"] if s else None, z=s["z"] if s else None, verdict=verdict))
    fwd.sort(key=lambda r: ({"holding up": 0, "mixed": 1, "too early": 2, "faded": 3}[r["verdict"]], -(r["n"] or 0)))
    # yesterday (the last day with results): winners, and what they shared vs that day's losers
    Y = [x for x in pos if x["day"] == last]; YP = [p for p in par if p["day"] == last]
    def shared(R):
        wn = [r for r in R if r["y"]]; ls = [r for r in R if not r["y"]]
        if not wn or not ls:
            return []
        cw, cl = collections.Counter(t for r in wn for t in r["tr"]), collections.Counter(t for r in ls for t in r["tr"])
        rows = []
        for t, k in cw.items():
            if k >= 3:
                a, b = k / len(wn), cl[t] / len(ls)
                if a - b >= .1:
                    rows.append(dict(trait=t, win=k, of_w=round(a, 3), of_l=round(b, 3), w30=W["positions" if R is Y else "parlays"].get(t)))
        return sorted(rows, key=lambda r: r["of_l"] - r["of_w"])[:10]
    yd = dict(day=last, pos=dict(n=len(Y), w=sum(x["y"] for x in Y), e=round(sum(1 / x["d"] for x in Y), 1)),
              par=dict(n=len(YP), w=sum(p["y"] for p in YP), e=round(sum(1 / p["d"] for p in YP), 2)),
              shared_pos=shared(Y), shared_par=shared(YP),
              winners=sorted([dict(sport=x["sport"], lab=x["lab"], book=x["book"], o=x["o"], tr=[t for t in x["tr"] if not t.startswith(("sport ", "starts "))]) for x in Y if x["y"]],
                             key=lambda r: -r["o"])[:60],
              won_parlays=[dict(sport=p["sport"], card=p["card"], d=round(p["d"], 1), legs=p["legs"]) for p in YP if p["y"]])
    n_tr = len([t for t in W["positions"] if W["positions"][t]["n"] >= FLAG_N]) + len([t for t in W["parlays"] if W["parlays"][t]["n"] >= FLAG_N])
    data = dict(built=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), last=last, window=[lo, last], scanned=n_tr, yesterday=yd, forward=fwd,
                positions={t: s for t, s in W["positions"].items() if s["n"] >= 20}, parlays={t: s for t, s in W["parlays"].items() if s["n"] >= 20})
    json.dump(data, open(os.path.join(OUT, "traits.json"), "w", encoding="utf-8"), separators=(",", ":"))
    json.dump(dict(sig=sig, built=data["built"], flags=flags, replayed=st.get("replayed")), open(STATE, "w", encoding="utf-8"), indent=0)
    page(data)
    print(f"traits: {len(pos)} positions, {len(par)} parlays, last day {last}, {len(flags)} traits flagged so far "
          f"({sum(r['verdict'] == 'holding up' for r in fwd)} holding up, {sum(r['verdict'] == 'faded' for r in fwd)} faded)")


# ---------------------------------------------------------------- page
def page(D):
    """the section now lives as a tab on the Profit page (owner 2026-11-09: 'make this a tab under profits');
    /winners.html only forwards there"""
    open(os.path.join(SITE, "winners.html"), "w", encoding="utf-8").write(
        '<!doctype html><meta charset="utf-8"><meta http-equiv="refresh" content="0; url=profit.html#winners">'
        '<title>What winners share</title><a href="profit.html#winners">What winners share is now a tab on the Profit page →</a>')


def body(D=None):
    """the 'What winners share' tab for profit.py page(); '' when there is no data yet"""
    if D is None:
        try:
            D = json.load(open(os.path.join(OUT, "traits.json"), encoding="utf-8"))
        except (OSError, ValueError):
            return ""
    e = html.escape
    pct = lambda v: "–" if v is None else f"{v * 100:+.0f}%"
    cls = lambda v: "" if v is None else ' class="up"' if v > 0 else ' class="dn"'
    odds = lambda o: f"+{o:.0f}" if o > 0 else f"{o:.0f}"
    def table(rows, unit_lab, empty):
        if not rows:
            return f'<p class="note">{empty}</p>'
        body = "".join(f'<tr><td class="l tr">{e(t)}</td><td>{s["n"]}</td><td>{s["w"]} <i>vs {s["e"]:.1f}</i></td><td{cls(s["ret"])}>{pct(s["ret"])}</td>'
                       f'<td class="hv"><span{cls(s["h1"])}>{pct(s["h1"])}</span> / <span{cls(s["h2"])}>{pct(s["h2"])}</span></td><td>{s["z"]:+.1f}</td></tr>' for t, s in rows)
        return (f'<div class="tw"><table class="t wt"><thead><tr><th class="l">{unit_lab}</th><th>Bets</th><th>Won vs priced</th><th>Return</th>'
                f'<th>Halves</th><th>z</th></tr></thead><tbody>{body}</tbody></table></div>')
    def sect(unit):
        Sx = D[unit]; base = Sx.get("all " + unit)
        good = sorted([(t, s) for t, s in Sx.items() if not t.startswith("all ") and flag_ok(s)], key=lambda r: -r[1]["z"])[:15]
        bad = sorted([(t, s) for t, s in Sx.items() if not t.startswith("all ") and s["n"] >= FLAG_N and (s["h1"] or 0) < 0 and (s["h2"] or 0) < 0 and s["z"] <= -1], key=lambda r: r[1]["z"])[:12]
        most = sorted([(t, s) for t, s in Sx.items() if not t.startswith("all ") and base and s["w"] >= .5 * base["w"]], key=lambda r: -r[1]["w"])[:10]
        lab = "positions" if unit == "positions" else "parlays"
        head = (f'<p class="note">All {lab}: {base["n"]} bets, {base["w"]} won vs {base["e"]:.1f} the prices implied, return {pct(base["ret"])}.</p>' if base else "")
        return (f'{head}<h3>Beat their price in both halves <span class="small muted">({FLAG_N}+ bets, z 1+)</span></h3>'
                + table(good, "Trait", "Nothing clears the bar in this window.")
                + f'<details class="how"><summary>In most winners ({len(most)}) — usually just short prices</summary>{table(most, "Trait", "–")}</details>'
                + f'<details class="how"><summary>Lost in both halves ({len(bad)})</summary>{table(bad, "Trait", "Nothing loses in both halves.")}</details>')
    y = D["yesterday"]
    try:
        dl = dt.date.fromisoformat(y["day"]).strftime("%a %b %-d")
    except ValueError:
        dl = dt.date.fromisoformat(y["day"]).strftime("%a %b %d")
    chips = lambda T: "".join(f'<span class="tc">{e(t)}</span>' for t in T[:9])
    wl = "".join(f'<li><b>{e(w["lab"])}</b> <span class="small muted">{e(w["book"])} {odds(w["o"])}</span><div class="tcs">{chips(w["tr"])}</div></li>' for w in y["winners"])
    wp = "".join(f'<li><b>{e(p["card"])}</b> <span class="small muted">{p["sport"].upper()} · paid {p["d"]}x</span><ul class="lg">'
                 + "".join(f'<li>{e(l[0])} <span class="small muted">{odds(float(l[1])) if l[1] is not None else ""} {"✓" if l[2] == "W" else "✗" if l[2] == "L" else ""}</span></li>' for l in p["legs"]) + "</ul></li>"
                 for p in y["won_parlays"])
    def shared_t(rows):
        if not rows:
            return '<p class="note">Not enough winners and losers that day to compare.</p>'
        return ('<div class="tw"><table class="t wt"><thead><tr><th class="l">Trait</th><th>Of winners</th><th>Of losers</th><th>Last 30 days</th></tr></thead><tbody>'
                + "".join(f'<tr><td class="l tr">{e(r["trait"])}</td><td>{r["of_w"] * 100:.0f}% <i>({r["win"]})</i></td><td>{r["of_l"] * 100:.0f}%</td>'
                          f'<td{cls(r["w30"]["ret"] if r["w30"] else None)}>{pct(r["w30"]["ret"]) + " on " + str(r["w30"]["n"]) if r["w30"] else "–"}</td></tr>' for r in rows)
                + "</tbody></table></div>")
    vcls = {"holding up": "up", "faded": "dn", "mixed": "", "too early": "muted"}
    fw = D["forward"]
    fwt = ('<div class="tw"><table class="t wt"><thead><tr><th class="l">Trait</th><th>Flagged</th><th>Since then</th><th>Return</th><th>Verdict</th></tr></thead><tbody>'
           + "".join(f'<tr><td class="l tr">{e(r["trait"])} <span class="small muted">· {r["unit"]}</span></td><td>{r["since"][5:]}</td><td>{r["n"]} <i>· {r["w"]} vs {r["e"]:.1f}</i></td>'
                     f'<td{cls(r["ret"])}>{pct(r["ret"])}</td><td class="{vcls[r["verdict"]]}">{r["verdict"]}</td></tr>' for r in fw)
           + "</tbody></table></div>") if fw else '<p class="note">No trait has cleared the bar yet. The first ones are written down the day they do.</p>'
    body = f"""<style>
.wn h2{{margin:28px 0 6px}}.wn h3{{font-size:15px;margin:14px 0 6px}}.wn .tw{{overflow-x:auto;-webkit-overflow-scrolling:touch}}
.wn table.wt td.tr{{white-space:normal;min-width:150px;max-width:300px;line-height:1.35}}.wn table.wt i{{font-style:normal;color:var(--muted)}}
.wn .up{{color:var(--good)}}.wn .dn{{color:var(--bad,#e5484d)}}.wn .tiles{{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px;margin:12px 0}}
.wn .tile{{background:var(--sunk);border-radius:8px;padding:10px 12px}}.wn .tile b{{display:block;font-size:20px}}.wn ul.wl{{list-style:none;padding:0;margin:0}}
.wn ul.wl>li{{border-top:1px solid var(--faint);padding:8px 0}}.wn .tcs{{display:flex;flex-wrap:wrap;gap:4px;margin-top:4px}}
.wn .tc{{border:1px solid var(--faint);border-radius:999px;padding:0 8px;font-size:11.5px;color:var(--muted)}}.wn ul.lg{{margin:4px 0 0;padding-left:18px;font-size:13.5px}}
.wn details.how summary{{cursor:pointer;color:var(--muted);font-size:13.5px;margin:8px 0}}
</style><div class="wn">
<section class="panel"><div class="phead"><h2>What winners share</h2></div>
<p class="note" style="margin:0 0 6px">Every day, every graded over at Hard Rock, Novig and Kalshi (tracked tags, slate picks, every parlay leg) and every cross-game parlay,
sorted by what they had in common before the game. Updated {e(D["built"][:16].replace("T", " "))} UTC · results through {e(dl)}.</p>
<details class="how" open><summary>How to read this</summary><p class="note">A trait found in most winners is usually just a short price: favorites win most
of the time and fill every list of winners. So each trait is judged against its price: <b>won vs priced</b> (wins vs what the odds implied) and the
<b>return</b> at $1 a bet, in both halves of the last {WINDOW} days. About {D["scanned"]} traits are checked every day, so a few will look good by luck.
That is why the <b>forward check</b> matters most: the day a trait first clears the bar it is written down, and from then on it is graded only on bets
settled after that day. A trait that keeps beating its price there is a real lead for leg selection; it still needs the owner’s OK before any card uses it.</p></details>
<h2>Forward check</h2><p class="note">Traits that cleared the bar (both halves positive, {FLAG_N}+ bets, z 1+), graded only after the day they first did. Verdict needs {FWD_N}+ bets since.</p>
{fwt}
<h2>{e(dl)}: the winners</h2>
<div class="tiles"><div class="tile"><span class="small muted">Positions</span><b>{y["pos"]["w"]} of {y["pos"]["n"]}</b><span class="small muted">priced {y["pos"]["e"]}</span></div>
<div class="tile"><span class="small muted">Parlays</span><b>{y["par"]["w"]} of {y["par"]["n"]}</b><span class="small muted">priced {y["par"]["e"]}</span></div></div>
<h3>What that day’s winners shared that its losers didn’t</h3><p class="note">One day is noise; the last column says whether the trait has paid over {WINDOW} days.</p>
{shared_t(y["shared_pos"])}
{('<h3>Parlays</h3>' + shared_t(y["shared_par"])) if y["par"]["n"] else ''}
<details class="how"><summary>Winning parlays ({len(y["won_parlays"])})</summary><ul class="wl">{wp or '<li class="note">None.</li>'}</ul></details>
<details class="how"><summary>Winning positions ({y["pos"]["w"]})</summary><ul class="wl">{wl or '<li class="note">None.</li>'}</ul></details>
<h2>Last {WINDOW} days: positions (legs and straights)</h2>{sect("positions")}
<h2>Last {WINDOW} days: parlays</h2>{sect("parlays")}
<p class="note" style="margin-top:24px">Display only: no card, tag or bet rule reads this page. Data: markets/traits.json.</p></section></div>"""
    return body


if __name__ == "__main__":
    build(force="force" in sys.argv[1:])
