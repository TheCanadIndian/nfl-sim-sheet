"""
Same-game parlays by game script, from the joint simulation (every leg is judged in the same simulated game,
so legs that move together -- a QB's yards and his WR1's yards -- are priced together, not multiplied).

Rules (user, Oct 2026): 5+ legs, no unders, no watered-down legs: player legs use the main line (the Kalshi rung
priced nearest 50/50, else our own median line) or an anytime TD, and every leg must hit 35%+ on its own.

Scripts: shootout, grind-it-out, each team pulling away, a close finish, plus the most likely parlay overall.
Cross-game: for each kickoff window and each day, one long parlay built from each game's strongest legs. Games
are simulated independently, so across games the chances multiply.
"""

import glob
import json
import os

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
NAMES = {"rec": "receptions", "rec_yds": "rec yds", "rush_yds": "rush yds", "pass_yds": "pass yds"}
MIN_P = .35                 # no lottery legs
MAX_P = .70                 # no watered-down legs (our own lines / moneylines)
MAX_LEGS = 7
SHOW = (5, 6, 7)
LEAN = 1.04                 # a script leg must be 4%+ likelier in that script than overall


def american(p):
    p = min(max(float(p), 1e-6), 1 - 1e-6)
    return f"+{round(100 * (1 - p) / p)}" if p < .5 else f"-{round(100 * p / (1 - p))}"


def kalshi_rows(gid):
    """Latest Kalshi snapshot rows for this game (empty if markets haven't listed it yet)."""
    for f in sorted(glob.glob(os.path.join(HERE, "markets", "nfl_????-??-??.json")), reverse=True):
        try:
            rows = [r for r in json.load(open(f)).get("markets", []) if r.get("source") == "kalshi" and r.get("game") == gid]
        except (OSError, ValueError):
            continue
        if rows:
            return rows
    return []


def legs_for_game(gid, h, a, hi, ai, g):
    """Candidate legs with per-simulation hit arrays."""
    stats = {}
    for res, t in ((h, hi), (a, ai)):
        pl = res["players"]
        for j, p in enumerate(t.players):
            s = dict(rec=pl["rec"][:, j], rec_yds=pl["rec_yds"][:, j], rush_yds=pl["rush_yds"][:, j],
                     anytime_td=(pl["rec_td"][:, j] + pl["rush_td"][:, j]) > 0)
            if j == res["qi"]:
                s["pass_yds"] = res["pass_yds"]
            stats[str(p.player_id)] = (t.team, p.name, p.pos, s)
    legs, have = [], set()
    rungs = {}
    for r in kalshi_rows(gid):
        if r.get("pid") is None or r.get("pid") not in stats or not r.get("yes_ask") or not r.get("yes_bid"):
            continue
        team, name, pos, s = stats[r["pid"]]
        if r["kind"] == "anytime_td":
            legs.append(_leg(team, name, pos, "anytime_td", None, s["anytime_td"], r["yes_ask"], "Kalshi"))
            have.add((r["pid"], "anytime_td"))
        elif r["kind"] in NAMES and r["kind"] in s:
            rungs.setdefault((r["pid"], r["kind"]), []).append(r)
    for (pid, kind), rs in rungs.items():
        r = min(rs, key=lambda x: abs((x["yes_bid"] + x["yes_ask"]) / 2 - .5))       # main line: priced nearest 50/50
        if not .35 <= (r["yes_bid"] + r["yes_ask"]) / 2 <= .65:
            continue
        team, name, pos, s = stats[pid]
        legs.append(_leg(team, name, pos, kind, r["line"], s[kind] > r["line"], r["yes_ask"], "Kalshi"))
        have.add((pid, kind))
    # our own main lines where Kalshi has none: median rounded to a hook (x.5), the usual book line
    for pid, (team, name, pos, s) in stats.items():
        for kind in ("pass_yds", "rush_yds", "rec_yds", "rec", "anytime_td"):
            if kind not in s or (pid, kind) in have:
                continue
            x = s[kind]
            if kind == "anytime_td":
                if x.mean() >= .3:
                    legs.append(_leg(team, name, pos, kind, None, x, None, "model"))
                continue
            med = float(np.median(x))
            floor = {"pass_yds": 150, "rush_yds": 25, "rec_yds": 20, "rec": 2}[kind]
            if med < floor:
                continue
            step = 1 if kind == "rec" else 5                                              # yardage lines sit on x4.5 / x9.5
            base = np.floor(med / step) * step
            cands = [base + k * step - .5 for k in (-1, 0, 1, 2)]
            line = min(cands, key=lambda c: abs((x > c).mean() - .5))                    # the hook nearest 50/50
            legs.append(_leg(team, name, pos, kind, float(line), x > line, None, "model"))
    # game legs: winner and the over on the Vegas total (no unders)
    margin, tot = h["points"] - a["points"], h["points"] + a["points"]
    legs.append(dict(lab=f"{g.home_team} wins", team=g.home_team, player="_win", pos="", kind="win", line=None, hit=margin > 0, price=None, src="model"))
    legs.append(dict(lab=f"{g.away_team} wins", team=g.away_team, player="_win", pos="", kind="win", line=None, hit=margin < 0, price=None, src="model"))
    if g.total_line == g.total_line:
        legs.append(dict(lab=f"Over {g.total_line:g} total points", team="", player="_total", pos="", kind="total", line=float(g.total_line), hit=tot > g.total_line, price=None, src="Vegas"))
    for L in legs:
        L["p"] = float(L["hit"].mean())
    # no watered-down legs: our own lines and moneylines above 70% are the -250 'safe' legs books push.
    # Kalshi main lines stay (the market has them near 50/50; a high chance there is our disagreement).
    keep = [L for L in legs if L["p"] >= MIN_P and (L["src"] == "Kalshi" or L["p"] <= MAX_P)]
    return keep, margin, tot


def _leg(team, name, pos, kind, line, hit, price, src):
    lab = f"{name} anytime TD" if kind == "anytime_td" else f"{name} over {line:g} {NAMES[kind]}"
    return dict(lab=lab, team=team, player=name, pos=pos, kind=kind, line=line, hit=np.asarray(hit, bool), price=price, src=src)


def scripts(g, margin, tot):
    H, A = g.home_team, g.away_team
    q75, q25 = np.percentile(tot, 75), np.percentile(tot, 25)
    n = len(tot)
    return [
        ("Most likely", np.ones(n, bool), "the likeliest 5-7 legs in any script"),
        ("Shootout", tot >= q75, f"{q75:.0f}+ combined points (top quarter of sims)"),
        ("Grind-it-out", tot <= q25, f"{q25:.0f} or fewer combined points (bottom quarter)"),
        (f"{H} pulls away", margin >= 8, f"{H} wins by 8+, {A} forced to pass"),
        (f"{A} pulls away", margin <= -8, f"{A} wins by 8+, {H} forced to pass"),
        ("Close finish", np.abs(margin) <= 3, "decided by 3 or fewer"),
    ]


def build(legs, mask, any_script):
    """Greedy: add the leg that keeps the joint chance highest inside the script (one leg per player)."""
    chosen, used, joint = [], set(), np.ones(len(mask), bool)
    for _ in range(MAX_LEGS):
        best = None
        for L in legs:
            if L["player"] in used:
                continue
            if not any_script and L["hit"][mask].mean() / max(L["p"], 1e-9) < LEAN:
                continue
            j = joint & L["hit"]
            sc = j[mask].mean() + .25 * j.mean()
            if best is None or sc > best[0]:
                best = (sc, L, j)
        if best is None:
            break
        _, L, joint = best
        chosen.append(L)
        used.add(L["player"])
    return chosen


def game_parlays(gid, h, a, hi, ai, g):
    legs, margin, tot = legs_for_game(gid, h, a, hi, ai, g)
    out = []
    for name, mask, desc in scripts(g, margin, tot):
        chosen = build(legs, mask, name == "Most likely")
        if len(chosen) < min(SHOW):
            continue
        rows = []
        for k in SHOW:
            if len(chosen) < k:
                break
            j = np.logical_and.reduce([L["hit"] for L in chosen[:k]])
            ind = float(np.prod([L["p"] for L in chosen[:k]]))
            px = [L["price"] for L in chosen[:k]]
            rows.append(dict(k=k, p=round(float(j.mean()), 4), p_script=round(float(j[mask].mean()), 4),
                             fair=american(j.mean()), indep=round(ind, 4),
                             kalshi=round(float(np.prod(px)), 4) if all(px) else None))
        out.append(dict(name=name, desc=desc, happens=round(float(mask.mean()), 3), rows=rows,
                        legs=[dict(lab=L["lab"], team=L["team"], kind=L["kind"], p=round(L["p"], 3),
                                   p_script=round(float(L["hit"][mask].mean()), 3), price=L["price"], src=L["src"])
                              for L in chosen]))
    # strongest legs for the cross-game parlays: the first legs of the most likely parlay (joint chance kept)
    core = []
    ml = build(legs, np.ones(len(margin), bool), True)
    for k in range(1, MAX_LEGS + 1):
        if len(ml) >= k:
            j = np.logical_and.reduce([L["hit"] for L in ml[:k]])
            core.append(dict(legs=[dict(lab=L["lab"], p=round(L["p"], 3), price=L["price"], src=L["src"]) for L in ml[:k]],
                             p=round(float(j.mean()), 4)))
    return dict(parlays=out, core=core)


def slot_of(gameday, gametime, weekday):
    hh = int(str(gametime).split(":")[0]) if gametime else 13
    if weekday == "Sunday":
        lab = "Sunday morning (London)" if hh < 12 else "Sunday 1 PM" if hh < 15 else "Sunday 4 PM" if hh < 19 else "Sunday night"
    else:
        lab = f"{weekday} {'night' if hh >= 19 else 'afternoon' if hh >= 12 else 'morning'}"
    return lab


def cross_game(games, per_game, min_legs=5):
    """games: rows with game_id, gameday, gametime, weekday, away_team, home_team. One long parlay per kickoff
    window and per day: each game's strongest legs (the start of its most likely same-game parlay, kept together
    so their link counts), as many per game as it takes to reach 5+ legs in total."""
    by_slot, by_day = {}, {}
    for g in games:
        if g["game_id"] not in per_game or not per_game[g["game_id"]]["core"]:
            continue
        s = slot_of(g["gameday"], g["gametime"], g["weekday"])
        by_slot.setdefault((g["gameday"], g["gametime"][:2], s), []).append(g)
        by_day.setdefault((g["gameday"], g["weekday"]), []).append(g)
    out = []

    def make(title, when, gs, n_legs):
        legs, p, px = [], 1.0, 1.0
        for g in gs:
            core = per_game[g["game_id"]]["core"]
            c = core[min(n_legs, len(core)) - 1]
            p *= c["p"]
            for L in c["legs"]:
                legs.append(dict(L, game=f"{g['away_team']} @ {g['home_team']}"))
                px = px * L["price"] if px is not None and L["price"] else None
        return dict(title=title, when=when, games=len(gs), legs=legs, p=round(p, 6), fair=american(p), kalshi=round(px, 6) if px else None)

    slots = {}
    for (day, hh, lab), gs in sorted(by_slot.items()):
        slots.setdefault((day, lab), []).extend(gs)
    per = lambda gs: -(-min_legs // len(gs))                     # legs per game to reach min_legs
    for (day, lab), gs in sorted(slots.items(), key=lambda kv: (kv[0][0], min(g["gametime"] for g in kv[1]))):
        out.append(make(lab, day, gs, per(gs)))
    for (day, wd), gs in sorted(by_day.items()):
        if len(gs) > 1 and len({slot_of(g["gameday"], g["gametime"], g["weekday"]) for g in gs}) > 1:
            out.append(make(f"All of {wd}", day, gs, per(gs)))
    return out
