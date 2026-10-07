"""
Same-game parlays by game script, from the joint simulation (every leg is judged in the same simulated game,
so legs that move together -- a QB's yards and his WR1's yards -- are priced together, not multiplied).

Rules (user, Oct 2026): 5+ legs, no unders, no watered-down legs: player legs use the main line (the Kalshi rung
priced nearest 50/50, else our own median line) or an anytime TD, and every leg must hit 35%+ on its own.

Scripts: shootout, grind-it-out, each team pulling away, a close finish (kept at the user's request though
its backtest ran low: 1.3% hit vs 5.0% predicted), plus the most likely parlay overall, and an "Expected" parlay
built inside the script this model expects (close game if likelier than in a typical game, else a blowout). Plus "Higher lines": 3-4 legs, overs at the hook at or
above the player's simulated MEAN for players whose mean beats their median (user idea; backtest 2025-26:
calibrated, 3 legs 14.6% hit vs 14.7% predicted).

Shown chances are scaled by the backtest's hit/predicted ratio (CAL): 5-7 main-line legs ran 16-24% high
(greedy picking favours legs whose simulations ran hot).
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
CAL = {3: 1.0, 4: .92, 5: .84, 6: .85, 7: .93}     # hit / predicted by leg count, two 2025-26 backtests averaged (Oct 2026)
BASE_CLOSE, BASE_BLOWOUT = .423, .332   # the model's own typical game (2025 wk8-12 sims; real games .486 / .371: sims run margins wide)
CAL_CROSS = .80             # cross-game parlays: 6 hits vs 8.1 predicted (132)
BUMP = {"rec_yds": (5, 20), "rush_yds": (5, 20), "rec": (1, 2), "pass_yds": (5, 150)}   # line step, min median


def american(p):
    p = min(max(float(p), 1e-6), 1 - 1e-6)
    return f"+{round(100 * (1 - p) / p)}" if p < .5 else f"-{round(100 * p / (1 - p))}"


_KCACHE = {}


def kalshi_rows(gid):
    """Latest Kalshi snapshot rows for this game (empty if markets haven't listed it yet)."""
    if gid in _KCACHE:
        return _KCACHE[gid]
    _KCACHE[gid] = _kalshi_rows(gid)
    return _KCACHE[gid]


def _kalshi_rows(gid):
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
            legs.append(_leg(team, name, pos, "anytime_td", None, s["anytime_td"], r["yes_ask"], "Kalshi", r["pid"]))
            have.add((r["pid"], "anytime_td"))
        elif r["kind"] in NAMES and r["kind"] in s:
            rungs.setdefault((r["pid"], r["kind"]), []).append(r)
    for (pid, kind), rs in rungs.items():
        r = min(rs, key=lambda x: abs((x["yes_bid"] + x["yes_ask"]) / 2 - .5))       # main line: priced nearest 50/50
        if not .35 <= (r["yes_bid"] + r["yes_ask"]) / 2 <= .65:
            continue
        team, name, pos, s = stats[pid]
        legs.append(_leg(team, name, pos, kind, r["line"], s[kind] > r["line"], r["yes_ask"], "Kalshi", pid))
        have.add((pid, kind))
    # our own main lines where Kalshi has none: median rounded to a hook (x.5), the usual book line
    for pid, (team, name, pos, s) in stats.items():
        for kind in ("pass_yds", "rush_yds", "rec_yds", "rec", "anytime_td"):
            if kind not in s or (pid, kind) in have:
                continue
            x = s[kind]
            if kind == "anytime_td":
                if x.mean() >= .3:
                    legs.append(_leg(team, name, pos, kind, None, x, None, "model", pid))
                continue
            med = float(np.median(x))
            floor = {"pass_yds": 150, "rush_yds": 25, "rec_yds": 20, "rec": 2}[kind]
            if med < floor:
                continue
            step = 1 if kind == "rec" else 5                                              # yardage lines sit on x4.5 / x9.5
            base = np.floor(med / step) * step
            cands = [base + k * step - .5 for k in (-1, 0, 1, 2)]
            line = min(cands, key=lambda c: abs((x > c).mean() - .5))                    # the hook nearest 50/50
            legs.append(_leg(team, name, pos, kind, float(line), x > line, None, "model", pid))
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


def _leg(team, name, pos, kind, line, hit, price, src, pid=None):
    lab = f"{name} anytime TD" if kind == "anytime_td" else f"{name} over {line:g} {NAMES[kind]}"
    return dict(lab=lab, team=team, player=name, pid=pid, pos=pos, kind=kind, line=line, hit=np.asarray(hit, bool), price=price, src=src)


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
        ("Close finish", np.abs(margin) <= 3, "decided by 3 or fewer. Experimental: backtest hit 1.3% vs 5.0% predicted (79 parlays)"),
    ]


def build(legs, mask, any_script):
    """Greedy: add the leg that keeps the joint chance highest inside the script (one leg per player). Script
    legs must lean 4%+ into the script; if that leaves fewer than 5, fill with legs at least as likely in the
    script as overall (no unders leaves grind-it-out / close-finish short of leaning overs)."""
    chosen, used, joint = [], set(), np.ones(len(mask), bool)
    for lean in ((LEAN, 1.0) if not any_script else (0,)):
        if lean == 1.0 and len(chosen) >= min(SHOW):
            break
        chosen, used, joint = _greedy(legs, mask, any_script, lean, chosen, used, joint)
    return chosen


def _greedy(legs, mask, any_script, lean, chosen, used, joint):
    chosen, used = list(chosen), set(used)
    while len(chosen) < MAX_LEGS:
        best = None
        for L in legs:
            if L["player"] in used:
                continue
            if not any_script and L["hit"][mask].mean() / max(L["p"], 1e-9) < lean:
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
    return chosen, used, joint


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
            c = CAL.get(k, 1.0)
            rows.append(dict(k=k, p=round(float(j.mean()) * c, 4), p_raw=round(float(j.mean()), 4), p_script=round(float(j[mask].mean()) * c, 4),
                             fair=american(j.mean() * c), indep=round(ind, 4),
                             kalshi=round(float(np.prod(px)), 4) if all(px) else None))
        out.append(dict(name=name, desc=desc, happens=round(float(mask.mean()), 3), rows=rows, any=name == "Most likely",
                        legs=[dict(lab=L["lab"], team=L["team"], kind=L["kind"], pid=L.get("pid"), line=L.get("line"), p=round(L["p"], 3),
                                   p_script=round(float(L["hit"][mask].mean()), 3), price=L["price"], src=L["src"])
                              for L in chosen]))
    # the script this model expects: close game or blowout, whichever this game leans to more than a typical game
    H, A = g.home_team, g.away_team
    fav = H if margin.mean() >= 0 else A
    fm = margin if fav == H else -margin
    p_close, p_blow, p_fav_blow = float((np.abs(margin) <= 7).mean()), float((np.abs(margin) >= 14).mean()), float((fm >= 14).mean())
    rb, rc = p_blow / BASE_BLOWOUT, p_close / BASE_CLOSE
    exp = "blowout" if rb > rc else "close"
    slight = max(rb, rc) < 1.05                       # within 5% of the model's typical game: a lean, not a call
    outlook = dict(fav=fav, p_close=round(p_close, 3), p_blowout=round(p_blow, 3), p_fav_blowout=round(p_fav_blow, 3),
                   base_close=BASE_CLOSE, base_blowout=BASE_BLOWOUT, expected=exp, slight=slight)
    mask = (fm >= 14) if exp == "blowout" else (np.abs(margin) <= 7)
    chosen = build(legs, mask, False) if not slight else []    # 'Leans' calls backtested as noise: no parlay
    if len(chosen) >= min(SHOW):
        rows = []
        for k in SHOW:
            if len(chosen) < k:
                break
            j = np.logical_and.reduce([L["hit"] for L in chosen[:k]])
            c = CAL.get(k, 1.0)
            px = [L["price"] for L in chosen[:k]]
            rows.append(dict(k=k, p=round(float(j.mean()) * c, 4), p_raw=round(float(j.mean()), 4), p_script=round(float(j[mask].mean()) * c, 4),
                             fair=american(j.mean() * c), indep=round(float(np.prod([L["p"] for L in chosen[:k]])), 4),
                             kalshi=round(float(np.prod(px)), 4) if all(px) else None))
        name = (f"{'Leans' if slight else 'Expected'}: {fav} blowout" if exp == "blowout" else f"{'Leans' if slight else 'Expected'}: close game")
        desc = (f"{fav} wins by 14+ ({p_fav_blow:.0%} of sims; any 14+ margin {p_blow:.0%} vs {BASE_BLOWOUT:.0%} in a typical simulated game)" if exp == "blowout"
                else f"decided by 7 or fewer ({p_close:.0%} of sims vs {BASE_CLOSE:.0%} in a typical simulated game)")
        out.insert(1, dict(name=name, desc=desc, happens=round(float(mask.mean()), 3), rows=rows, any=False, expected=True,
                           legs=[dict(lab=L["lab"], team=L["team"], kind=L["kind"], pid=L.get("pid"), line=L.get("line"), p=round(L["p"], 3),
                                      p_script=round(float(L["hit"][mask].mean()), 3), price=L["price"], src=L["src"]) for L in chosen]))
    hl = higher_lines(gid, h, a, hi, ai)
    if hl:
        out.insert(2 if len(out) > 1 and out[1].get("expected") else 1, hl)
    # strongest legs for the cross-game parlays: the first legs of the most likely parlay (joint chance kept)
    core = []
    ml = build(legs, np.ones(len(margin), bool), True)
    for k in range(1, MAX_LEGS + 1):
        if len(ml) >= k:
            j = np.logical_and.reduce([L["hit"] for L in ml[:k]])
            core.append(dict(legs=[dict(lab=L["lab"], team=L["team"], kind=L["kind"], pid=L.get("pid"), line=L.get("line"), p=round(L["p"], 3), price=L["price"], src=L["src"]) for L in ml[:k]],
                             p=round(float(j.mean()), 4)))
    return dict(parlays=out, core=core, outlook=outlook)


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
        p *= CAL_CROSS
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


def _hook_at_or_above(v, step):
    return np.floor(v) + .5 if step == 1 else np.ceil((v + .5) / step) * step - .5     # x4.5 / x9.5 for yards


def higher_lines(gid, h, a, hi, ai, legs_max=4):
    """Overs at the line at or just above the simulated mean, for players whose mean beats their median; the
    Kalshi ladder rung at/above the mean when one is listed. Greedy joint chance, one leg per player, 3-4 legs."""
    rungs = {}
    for r in kalshi_rows(gid):
        if r.get("pid") and r["kind"] in BUMP and r.get("yes_ask"):
            rungs.setdefault((r["pid"], r["kind"]), []).append((float(r["line"]), float(r["yes_ask"])))
    pool = []
    for res, t in ((h, hi), (a, ai)):
        pl = res["players"]
        for j, p in enumerate(t.players):
            st = dict(rec=pl["rec"][:, j], rec_yds=pl["rec_yds"][:, j], rush_yds=pl["rush_yds"][:, j])
            if j == res["qi"]:
                st["pass_yds"] = res["pass_yds"]
            pid = str(p.player_id)
            for kind, x in st.items():
                step, mmin = BUMP[kind]
                med, mean = float(np.median(x)), float(x.mean())
                if med < mmin or mean <= med:
                    continue
                line, price, src = _hook_at_or_above(mean, step), None, "model"
                rs = [rr for rr in rungs.get((pid, kind), []) if rr[0] >= mean - .5 * step]
                if rs:
                    (line, price), src = min(rs), "Kalshi"
                hit = x > line
                if hit.mean() < .25:
                    continue
                L = _leg(t.team, p.name, p.pos, kind, float(line), hit, price, src, pid)
                L.update(p=float(hit.mean()), mean=round(mean, 1), med=round(med, 1))
                pool.append(L)
    chosen, used, joint = [], set(), None
    for _ in range(legs_max):
        best = None
        for L in pool:
            if L["player"] in used:
                continue
            jj = L["hit"] if joint is None else joint & L["hit"]
            if best is None or jj.mean() > best[0]:
                best = (jj.mean(), L, jj)
        if best is None:
            break
        _, L, joint = best
        chosen.append(L)
        used.add(L["player"])
    if len(chosen) < 3:
        return None
    rows = []
    for k in (3, 4):
        if len(chosen) < k:
            break
        j = np.logical_and.reduce([L["hit"] for L in chosen[:k]])
        c = CAL.get(k, 1.0)
        px = [L["price"] for L in chosen[:k]]
        rows.append(dict(k=k, p=round(float(j.mean()) * c, 4), p_raw=round(float(j.mean()), 4), p_script=None, fair=american(j.mean() * c),
                         indep=round(float(np.prod([L["p"] for L in chosen[:k]])), 4), kalshi=round(float(np.prod(px)), 4) if all(px) else None))
    return dict(name="Higher lines", any=True, happens=1.0, rows=rows,
                desc="3-4 overs set at or above each player's projected average (players whose average beats their median)",
                legs=[dict(lab=L["lab"], team=L["team"], kind=L["kind"], pid=L.get("pid"), line=L.get("line"), p=round(L["p"], 3),
                           p_script=None, price=L["price"], src=L["src"], mean=L["mean"], med=L["med"]) for L in chosen])
