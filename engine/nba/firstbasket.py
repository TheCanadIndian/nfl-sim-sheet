#!/usr/bin/env python3
"""NBA first basket model (owner 2026-11-07: 'build the model and show me who is projected and if they have value').

research/nba_fb_leads.py (5,746 games 2023-24 .. now, walk-forward by season, better than the flat model in all 8 halves):
who makes the game's first FG = P(his team scores first) x his share inside the team's five.
  team    the two projected jumpers' tip records (wins + 3) / (jumps + 6); tip winner makes the first FG 63.6%
  share   softmax over tonight's five of  .32 log(points share) + .28 log(first-shot share) + .13 log(first-FG share)
          + .03 center + .26 projected jumper   (coefficients of the last walk-forward fit, 2023-26 -> 2026-27)
  first-shot / first-FG share = how often he took / made his team's first shot in the games he started (decayed, half-life
  25 starts, shrunk with 6 starts at .2); projected jumper = the five's most frequent jumper (decayed share of his starts).
Starters get 97% of the game's chance, everyone else .003.

nba.db `pbp_open` (teamlink.py, per team per game: first shot, first made FG, jumper, won the tip) feeds it; project.py calls
`write` -> markets/fb_model_nba.json {gid: {norm name: {p, r, c, j, s1, f1, pt}}} and sharp.fb_nba uses it for `p`.
"""
import collections
import json
import math
import os
import sqlite3

HL, K, PRIOR = 25.0, 6.0, .2
B = dict(pts=.32, shot1=.28, fg1=.13, center=.03, jumper=.26)
TIPW = .636
DEC = .5 ** (1 / HL)


def history(con):
    """(date, game_id, team, shot1, fg1, jumper, won_tip, starters) per team-game, oldest first."""
    q = """SELECT o.game_id, g.date, o.team, o.shot1, o.fg1, o.jumper, o.won_tip FROM pbp_open o JOIN games g USING(game_id)
           WHERE g.state = 'FINAL' ORDER BY g.date, o.game_id"""
    rows = con.execute(q).fetchall()
    st = collections.defaultdict(list)
    for gid, team, pid in con.execute("SELECT game_id, team, player_id FROM players WHERE starter = 1"):
        st[(gid, team)].append(str(pid))
    return [(d, gid, t, s1, f1, j, w, st.get((gid, t), [])) for gid, d, t, s1, f1, j, w in rows]


class State:
    """Per player decayed counts over his starts: w, shot1, fg1, jumped; tip record (all jumps)."""

    def __init__(self):
        self.r = collections.defaultdict(lambda: [0.0, 0.0, 0.0, 0.0])
        self.tip = collections.defaultdict(lambda: [0, 0])

    def add(self, shot1, fg1, jumper, won, starters):
        for p in starters:
            x = self.r[p]
            x[0] = x[0] * DEC + 1
            x[1] = x[1] * DEC + (p == shot1)
            x[2] = x[2] * DEC + (p == fg1)
            x[3] = x[3] * DEC + (p == jumper)
        if jumper and won is not None:
            t = self.tip[jumper]
            t[0] += 1
            t[1] += int(won)

    def feats(self, p):
        w, s, f, j = self.r[str(p)]
        return (s + K * PRIOR) / (w + K), (f + K * PRIOR) / (w + K), (j + 1 * .2) / (w + 1)

    def tipp(self, p):
        j, w = self.tip[str(p)]
        return (w + 3) / (j + 6)


def team_model(five, state):
    """five: [(pid, pts, is_center)] -> ({pid: share inside the team}, jumper pid or None, details)."""
    f = {str(p): state.feats(p) for p, _, _ in five}
    jp = max(five, key=lambda x: f[str(x[0])][2])
    jumper = str(jp[0]) if f[str(jp[0])][2] >= .25 else next((str(p) for p, _, c in five if c), None)
    tot = sum(max(pt, .5) for _, pt, _ in five)
    u, det = {}, {}
    for p, pt, c in five:
        s1, f1, _ = f[str(p)]
        ps = max(pt, .5) / tot
        u[str(p)] = (B["pts"] * math.log(max(ps, .02)) + B["shot1"] * math.log(max(s1, .01)) + B["fg1"] * math.log(max(f1, .01))
                     + B["center"] * c + B["jumper"] * (str(p) == jumper))
        det[str(p)] = dict(s1=round(s1, 3), f1=round(f1, 3), pt=round(ps, 3))
    m = max(u.values())
    e = {p: math.exp(v - m) for p, v in u.items()}
    z = sum(e.values())
    return {p: v / z for p, v in e.items()}, jumper, det


def game_model(fiveA, fiveB, state, bench=()):
    """Both teams' fives -> {pid: p} for the game's first FG (+ bench at .003), plus jumpers / details."""
    sa, ja, da = team_model(fiveA, state)
    sb, jb, db = team_model(fiveB, state)
    ta = state.tipp(ja) if ja else .5
    tb = state.tipp(jb) if jb else .5
    pa_tip = ta / (ta + tb)
    pa = pa_tip * TIPW + (1 - pa_tip) * (1 - TIPW)
    nb = len(bench)
    scale = (1 - .003 * nb) if nb else .97
    out = {p: pa * v * scale for p, v in sa.items()}
    out.update({p: (1 - pa) * v * scale for p, v in sb.items()})
    for p in bench:
        out.setdefault(str(p), .003)
    return out, {ja: True, jb: True}, {**da, **db}


def write(payload, db):
    """markets/fb_model_nba.json for tonight's games (project.py)."""
    try:
        import parlays as PL
        con = sqlite3.connect(db)
        st = State()
        n = 0
        for d, gid, t, s1, f1, j, w, five in history(con):
            st.add(s1, f1, j, w, five)
            n += 1
        out = {}
        for g in payload["games"]:
            pl = [x for x in payload["players"] if int(x["g"]) == int(g["id"]) and x.get("st") != "Out"]
            fives, bench, rank = {}, [], {}
            for t in {x["t"] for x in pl}:
                T = [x for x in pl if x["t"] == t]
                bx = lambda x: ((x.get("bx") or {}).get("vegas") or {})
                adj = lambda x: bx(x).get("min", 0) + (30 if str(x.get("lu") or "").startswith("in") else -30 if str(x.get("lu") or "").startswith("out") else 0)
                five = sorted(sorted(T, key=lambda x: -adj(x))[:5], key=lambda x: -bx(x).get("pts", 0))
                fives[t] = [(str(x["id"]), bx(x).get("pts", 0), str(x.get("pos") or "").startswith("C")) for x in five]
                rank.update({str(x["id"]): i + 1 for i, x in enumerate(five)})
                bench += [str(x["id"]) for x in T if x not in five]
            if len(fives) != 2:
                continue
            a, b = list(fives.values())
            P, J, det = game_model(a, b, st, bench)
            nm = {str(x["id"]): x["n"] for x in pl}
            cen = {p: c for f in fives.values() for p, _, c in f}
            out[str(g["id"])] = {PL.jsnorm(nm[p]): dict(p=round(v, 4), r=rank.get(p), c=bool(cen.get(p)), j=bool(J.get(p)), **(det.get(p) or {}))
                                 for p, v in P.items() if p in nm}
        json.dump(dict(games=out, n_hist=n), open(os.path.join(PL.SITE, "markets", "fb_model_nba.json"), "w"), separators=(",", ":"))
        print(f"first basket model: {len(out)} games ({n} team-games of history)")
    except Exception as e:                                    # noqa: BLE001 -- display / tracking only
        print(f"first basket model: skipped ({type(e).__name__}: {e})")
