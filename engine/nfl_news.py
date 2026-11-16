#!/usr/bin/env python3
"""NFL news trigger (owner 2026-11-16, after Emmett Johnson / Kenneth Walker, KC @ ATL wk 10: the books moved Johnson's anytime TD
+488 -> +130 at 2:40 PM ET Friday and to -150 by Saturday noon, but no NFL run happened from Friday 6 PM to Sunday 1 AM, and our
projection only caught up after the official inactives). Plain Python, no tokens. Exit 0 = start an NFL run, 10 = skip.

    python nfl_news.py          check (markets.yml, every ~15 min; it runs `gh workflow run nfl.yml` on exit 0)
    python nfl_news.py --mark   end of every NFL run (run.py nfl): save what this run saw

A run is due when, for a game in the next 4 days that hasn't started:
  - ESPN's injury feed shows a projected regular (latest projections/<stem>_players.csv: carries 8+, targets 4+ or QB attempts
    20+) with a different status than the last NFL run saw (Active / Questionable / Doubtful / Out / Injured Reserve ...);
  - the market moved a player's role since the last run: anytime-TD implied (median over books) up 15+ pts, a rushing /
    receiving-yards main line 2x and 20+ yds higher, or a catches line 2+ higher;
  - Thursday - Saturday with a game within 48 h, the last NFL run is 6+ h old (there were no Saturday runs).
Cooldown: none within 60 min of the last trigger (markets/nfl_news_trig.json, written only here) or of the last run (the run's
--mark writes markets/nfl_news_state.json; one writer per file). Prints one line with the reason.
"""
import glob
import json
import os
import re
import sys
import unicodedata

import pandas as pd
import requests

HERE = os.path.dirname(os.path.abspath(__file__))
SITE = os.environ.get("SIM_SITE") or os.path.join(HERE, "site")
STATE = os.path.join(HERE, "markets", "nfl_news_state.json")
TRIG = os.path.join(HERE, "markets", "nfl_news_trig.json")
PROPS = os.path.join(SITE, "markets", "props_nfl.json")
INJ = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/injuries"
H = {"User-Agent": "Mozilla/5.0"}
DAYS, COOL_MIN, REFRESH_H, SOON_H = 4, 60, 6, 48
TD_JUMP, YDS_X, YDS_MIN, REC_JUMP = .15, 2.0, 20, 2.0


def norm(s):
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z ]", "", s)
    s = re.sub(r"\b(jr|sr|ii|iii|iv)\b", "", s)
    return re.sub(r"\s+", " ", s).strip()


def load(p, d):
    try:
        return json.load(open(p, encoding="utf-8"))
    except (OSError, ValueError):
        return d


def games(now):
    """{game_id: (home, away, kickoff UTC)} for games in the next DAYS days that haven't started."""
    sys.path.insert(0, HERE)
    import markets as M
    O = M.ours_nfl() or {}
    out = {}
    for g in O.get("games", []):
        st = pd.Timestamp(g["start"]).tz_convert("UTC")
        if now < st <= now + pd.Timedelta(days=DAYS):
            out[str(g["id"])] = (g["home"], g["away"], st)
    return out


def regulars(G):
    """{(team, norm name): pos} for projected regulars of these games."""
    stems = sorted(glob.glob(os.path.join(HERE, "projections", "20??_wk??_players.csv")))
    if not stems:
        return {}
    P = pd.read_csv(stems[-1])
    P = P[P.game_id.astype(str).isin(G)]
    piv = P.pivot_table(index=["team", "player", "pos"], columns="stat", values="mean", aggfunc="first").reset_index()
    for c in ("car", "tgt", "att"):
        if c not in piv:
            piv[c] = 0
    keep = piv[(piv.car.fillna(0) >= 8) | (piv.tgt.fillna(0) >= 4) | ((piv.pos == "QB") & (piv.att.fillna(0) >= 20))]
    return {f"{t}|{norm(n)}": p for t, n, p in zip(keep.team, keep.player, keep.pos)}


def team_code():
    sys.path.insert(0, HERE)
    import books as B
    return B.NAMES["nfl"]


def statuses(regs):
    """ESPN status for each regular ('Active' when not listed)."""
    r = requests.get(INJ, headers=H, timeout=30)
    r.raise_for_status()
    code = team_code()
    st = {}
    for t in r.json().get("injuries", []):
        tc = code.get(t.get("displayName"))
        for i in t.get("injuries", []):
            k = f"{tc}|{norm((i.get('athlete') or {}).get('displayName'))}"
            if k in regs:
                st[k] = i.get("status") or "Active"
    return {k: st.get(k, "Active") for k in regs}


def market(G):
    """{game|norm name: {td: median implied, rush_yds / rec_yds / rec: main line}} from the props file."""
    P = (load(PROPS, {}) or {}).get("props") or {}
    dec = lambda o: 1 + o / 100 if o > 0 else 1 + 100 / abs(o)
    out = {}
    for gid, Gp in P.items():
        if str(gid) not in G:
            continue
        for nm, ks in Gp.items():
            x = {}
            for k, e in ks.items():
                if k == "td":
                    imp = sorted(1 / dec(v[0]) for v in (e.get("o") or {}).values() if isinstance(v, list) and v and v[0])
                    if imp:
                        x["td"] = round(imp[len(imp) // 2], 4)
                elif k in ("rush_yds", "rec_yds", "rec") and e.get("l") is not None:
                    x[k] = float(e["l"])
            if x:
                out[f"{gid}|{norm(nm)}"] = x
    return out


def check():
    now = pd.Timestamp.now(tz="UTC")
    S, T = load(STATE, {}), load(TRIG, {})
    last_run = pd.Timestamp(S["t"]) if S.get("t") else None
    last_trig = pd.Timestamp(T["t"]) if T.get("t") else None
    for lab, t in (("last trigger", last_trig), ("last run", last_run)):
        if t is not None and (now - t).total_seconds() < COOL_MIN * 60:
            print(f"skip: {lab} {int((now - t).total_seconds() // 60)} min ago")
            return 10
    G = games(now)
    if not G:
        print("skip: no NFL game in the next 4 days")
        return 10
    why = []
    try:
        regs = regulars(G)
        st = statuses(regs)
        old = S.get("status") or {}
        for k, v in st.items():
            if k in old and old[k] != v:
                why.append(f"{k.split('|')[1]} ({k.split('|')[0]}) {old[k]} -> {v}")
    except Exception as e:                                          # noqa: BLE001 -- the market and refresh checks still count
        print(f"  injury feed check skipped ({type(e).__name__}: {e})")
    mk, om = market(G), S.get("market") or {}
    for k, x in mk.items():
        o = om.get(k)
        if not o:
            continue
        if x.get("td") is not None and o.get("td") is not None and x["td"] - o["td"] >= TD_JUMP:
            why.append(f"{k.split('|')[1]} anytime TD {o['td']:.0%} -> {x['td']:.0%}")
        for s in ("rush_yds", "rec_yds"):
            if x.get(s) and o.get(s) and x[s] >= YDS_X * o[s] and x[s] - o[s] >= YDS_MIN:
                why.append(f"{k.split('|')[1]} {s} {o[s]} -> {x[s]}")
        if x.get("rec") and o.get("rec") and x["rec"] - o["rec"] >= REC_JUMP:
            why.append(f"{k.split('|')[1]} catches {o['rec']} -> {x['rec']}")
    et = now.tz_convert("America/New_York")
    soon = [st for _, _, st in G.values() if st <= now + pd.Timedelta(hours=SOON_H)]
    if not why and et.dayofweek in (3, 4, 5) and soon and (last_run is None or (now - last_run).total_seconds() > REFRESH_H * 3600):
        why.append(f"refresh: game within {SOON_H} h, last NFL run {'never' if last_run is None else f'{last_run.tz_convert(et.tz):%a %I:%M %p}'}")
    if not why:
        print("skip: no NFL news since the last run")
        return 10
    print("run: " + "; ".join(why[:6]) + (f" (+{len(why) - 6} more)" if len(why) > 6 else ""))
    json.dump(dict(t=now.isoformat(), why=why[:20]), open(TRIG, "w"), indent=0)
    return 0


def mark():
    now = pd.Timestamp.now(tz="UTC")
    G = games(now)
    S = dict(t=now.isoformat(), status={}, market=market(G))
    try:
        S["status"] = statuses(regulars(G))
    except Exception as e:                                          # noqa: BLE001
        print(f"nfl_news mark: injury feed skipped ({type(e).__name__}: {e})")
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    json.dump(S, open(STATE, "w"), separators=(",", ":"))
    print(f"nfl_news mark: {len(S['status'])} regulars, {len(S['market'])} players priced")


if __name__ == "__main__":
    if "--mark" in sys.argv:
        mark()
        sys.exit(0)
    sys.exit(check())
