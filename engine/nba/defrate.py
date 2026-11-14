"""NBA defense rating for overs (owner 2026-11-14: 'lets build a rating system for the defense with the recent finds' -> 'show all').

One number per player x stat tonight: how far the defense moves his over's chance away from the market price, in points at a
50/50 line, from the defense findings that held up against the market only (research/def_rating.py; fit on the Kalshi archive
Jan 16 - Apr 2026, the rung nearest 50c per player-stat-game, market = the mid as offset, ridge):
    log(DvP)                                   +.654   DvP = the page's matchup number (position x archetype for points / threes)
    tough matchup (DvP -8% or worse)           -.070
    opponent rested 3+ days                    -.042
    opponent on the 2nd night of a back-to-back +.052
    opponent flew 1,000+ mi to this game       -.111
    opponent's usual rim protector / big out   -.130   (the market over-adjusts for an absent defender)
    opponent's usual 3-and-D starter out       -.193
Unseen tests: Kalshi playoffs + 2026-27 (6,284) log-loss -5.1e-4 vs the mid, tough grade -3.2 pts vs the price, slightly soft +1.7;
books' main lines Oct 24 - Nov 13 (7,485) -4.8e-4, tough -4.9 vs 50%, slightly soft -2.3. No grade beats the price after the vig:
display only (a reason to skip an over, not to bet one).

write(payload, db) -- called by nba/project.py: each upcoming player gets p['dr'] = {stat: [points, grade]}, each game g['dd'] =
{defending team: {r: rest days, mi: miles flown, rim: [names out], d3: [names out]}}; markets/drate_nba.json {games: {gid: {s, p:
{norm name: {stat: [points, grade]}}}}} for the screener and allprops.py (grades every over at your books by grade).
Grades: T tough (<= -3 pts), t slightly tough (-3..-1), n neutral, s slightly soft (+1..+3), S soft (+3+).
"""
import datetime as dt
import json
import math
import os
import sqlite3

COEF = dict(dvp_log=.654, tough=-.070, o_rest3=-.042, o_b2b=.052, o_trav=-.111, rim_out=-.130, d3_out=-.193)
TOUGH_DVP, REG_MIN = .92, 24
STATS = ("pts", "reb", "ast", "fg3m")
ARENA = {  # lat, lon (research/hidden_factors.py NBA_ARENA)
    "ATL": (33.757, -84.396), "BOS": (42.366, -71.062), "BKN": (40.683, -73.975), "CHA": (35.225, -80.839), "CHI": (41.881, -87.674),
    "CLE": (41.497, -81.688), "DAL": (32.790, -96.810), "DEN": (39.749, -105.008), "DET": (42.341, -83.055), "GS": (37.768, -122.388),
    "HOU": (29.751, -95.362), "IND": (39.764, -86.155), "LAC": (33.945, -118.341), "LAL": (34.043, -118.267), "MEM": (35.138, -90.051),
    "MIA": (25.781, -80.188), "MIL": (43.045, -87.917), "MIN": (44.979, -93.276), "NO": (29.949, -90.082), "NY": (40.751, -73.993),
    "OKC": (35.463, -97.515), "ORL": (28.539, -81.384), "PHI": (39.901, -75.172), "PHX": (33.446, -112.071), "POR": (45.532, -122.667),
    "SAC": (38.580, -121.500), "SA": (29.427, -98.438), "TOR": (43.643, -79.379), "UTAH": (40.768, -111.901), "WSH": (38.898, -77.021)}


def _hav(a, b):
    (la1, lo1), (la2, lo2) = a, b
    p = math.pi / 180
    d = .5 - math.cos((la2 - la1) * p) / 2 + math.cos(la1 * p) * math.cos(la2 * p) * (1 - math.cos((lo2 - lo1) * p)) / 2
    return 7918 * math.asin(math.sqrt(d))


def grade(pts):
    return "T" if pts <= -3 else "t" if pts <= -1 else "n" if pts < 1 else "s" if pts < 3 else "S"


def shift(dv, f):
    """points of over chance at a 50/50 line for a DvP number dv and the defense flags f"""
    z = COEF["dvp_log"] * math.log(min(max(dv, .6), 1.6)) + (COEF["tough"] if dv <= TOUGH_DVP else 0)
    z += sum(COEF[k] for k in ("o_rest3", "o_b2b", "o_trav") if f.get(k)) + (COEF["rim_out"] if f.get("rim") else 0) + (COEF["d3_out"] if f.get("d3") else 0)
    return round((1 / (1 + math.exp(-z)) - .5) * 100, 1)


def _prev_game(con, team, t0):
    r = con.execute("select start_utc, home from games where (home = ? or away = ?) and start_utc < ? order by start_utc desc limit 1",
                    (team, team, t0)).fetchone()
    return r if r and r[0] else None


def _t(s):
    return dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))


def write(payload, db):
    import parlays as PL
    con = sqlite3.connect(db)
    try:
        arch = json.load(open(os.path.join(PL.SITE, "markets", "arch_nba.json")))
    except (OSError, ValueError):
        arch = {}
    DVP = {(r["team"], r["pg"]): r for r in payload.get("dvp", [])}
    now = dt.datetime.now(dt.timezone.utc)
    up = {g["id"]: g for g in payload.get("games", []) if g.get("start_utc") and _t(g["start_utc"]) > now}
    FL = {}
    for gid, g in up.items():
        dd = {}
        for dteam in (g["home"], g["away"]):                   # the defending team's situation tonight
            f, why = {}, {}
            pv = _prev_game(con, dteam, g["start_utc"])
            if pv:
                rest = (_t(g["start_utc"]) - _t(pv[0])).total_seconds() / 86400
                mi = _hav(ARENA[g["home"]], ARENA[pv[1]]) if g["home"] in ARENA and pv[1] in ARENA else None
                f.update(o_rest3=rest >= 3, o_b2b=rest < 1.6, o_trav=mi is not None and mi >= 1000)
                why.update(r=round(rest, 1), mi=None if mi is None else round(mi))
            outs = [o for o in (g.get("out") or {}).get(dteam, []) if (o.get("m") or 0) >= REG_MIN]
            rim = [o["n"] for o in outs if arch.get(PL.jsnorm(o["n"])) in ("Rim protector", "Interior big")]
            d3 = [o["n"] for o in outs if arch.get(PL.jsnorm(o["n"])) == "3-and-D role player"]
            f.update(rim=bool(rim), d3=bool(d3)); why.update(rim=rim, d3=d3)
            dd[dteam] = why; FL[(gid, dteam)] = f
        g["dd"] = dd
    out = {}
    for p in payload.get("players", []):
        gid = p.get("g")
        if gid not in up or (gid, p.get("o")) not in FL:
            continue
        r = DVP.get((p.get("o"), p.get("pg")))
        if not r:
            continue
        va = p.get("va") or {}
        dr = {}
        for st in STATS:
            if r.get(st) is None:
                continue
            dv = math.sqrt(r[st] * va[st]) if st in ("pts", "fg3m") and va.get(st) else r[st]
            s = shift(dv, FL[(gid, p["o"])])
            dr[st] = [s, grade(s)]
        if dr:
            p["dr"] = dr
            out.setdefault(str(gid), dict(s=up[gid]["start_utc"], p={}))["p"][PL.jsnorm(p["n"])] = dr
    fn = os.path.join(PL.SITE, "markets", "drate_nba.json")
    try:                                                        # keep started games 3 days (allprops.py grades them after the game)
        old = json.load(open(fn)).get("games", {})
    except (OSError, ValueError):
        old = {}
    keep = {k: v for k, v in old.items() if k not in out and _t(v["s"]) > now - dt.timedelta(days=3)}
    json.dump(dict(fetched=now.isoformat(timespec="minutes"), games={**keep, **out}), open(fn, "w"), separators=(",", ":"))
    n = sum(len(v["p"]) for v in out.values())
    print(f"defense rating: {n} players in {len(out)} games")
