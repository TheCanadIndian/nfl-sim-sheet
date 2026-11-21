"""Matchup charts (owner 2026-11-21, from a PDF made with our data for the Nov 20 slate: 'Create a tab under nba that shows
this for all slates from now on. Make it exportable as a pdf from the page and make them easier to see when using ladders
tab or screener').

One calculation shared by the NBA page's Matchups tab, its Ladders tab and the screener (site.py ctx_nba), so the three
can't disagree. For each player-stat:
  cell  = the number that shades his stat cell on the sheet (page.js `cell` / `mx`): what tonight's defense allows per
          minute vs the league, to his position (DvP), blended 50/50 in logs with his archetype for points and threes
  level = fixed bands on that number: deep green +10%+, green +5..+10%, light green +2..+5%, neutral within 2%, and the
          same steps on the red side (the PDF's labels were read off the colors, so a few borderline cells differ)
  best  = the greenest level among his team's eligible players for that stat (the PDF's Chart 1; ties all listed)
  chip  = the D rating (defrate.py) for that stat, points vs the market at a 50/50 line
  dis   = +1: green or deeper but the chip is -1.0 or lower (its slightly tough / tough grades; the PDF counted any negative chip,
          which lists -0.1s the chip itself calls neutral); -1: red or deeper with a chip of +1.0 or more (PDF Chart 2)
Eligible: not Out, 20+ projected minutes (median), and real volume in the stat (page.js MX_MIN: the sheet shows a matchup
line or D chip only there -- this drops the PDF's big men with 0.0 threes that it had to tell the reader to discount).
Display only: nothing tracked, no bet rule.
"""

STATS = ("pts", "reb", "ast", "fg3m")
VOL = {"pts": 8, "reb": 3, "ast": 2, "fg3m": 1}      # page.js MX_MIN (projected median)
MIN_MIN = 20                                          # projected minutes (median) to be listed; under 24 gets a '*'
BANDS = (10, 5, 2)                                    # % per minute vs league: deep, green, light
CHIP = 1.0                                            # a D chip counts as disagreeing at -1.0 or lower (+1.0 the other way): its own
                                                      # slightly tough / tough grades; -0.1..-0.9 is the chip's neutral band
MED = 3                                               # quantile index of the median in the page payload


def level(pct):
    a = abs(pct)
    n = 3 if a >= BANDS[0] else 2 if a >= BANDS[1] else 1 if a >= BANDS[2] else 0
    return n if pct > 0 else -n


def board(payload):
    dvp = {(r["team"], r["pg"]): r for r in payload.get("dvp") or []}
    rows = {}
    for p in payload.get("players", []):
        if p.get("st") == "Out":
            continue
        vg = (p.get("s") or {}).get("vegas") or {}
        mn = (vg.get("min") or [None] * 6)[MED]
        if mn is None or mn < MIN_MIN:
            continue
        r = dvp.get((p.get("o"), p.get("pg")))
        if not r:
            continue
        va, dr, out = p.get("va") or {}, p.get("dr") or {}, {}
        for st in STATS:
            if not vg.get(st) or vg[st][MED] < VOL[st] or r.get(st) is None:
                continue
            v = (r[st] * va[st]) ** .5 if st in ("pts", "fg3m") and va.get(st) else r[st]
            pct = round((v - 1) * 100, 1)
            chip = dr[st][0] if st in dr else None
            lv = level(pct)
            out[st] = [pct, lv, 0, chip, 1 if lv >= 2 and chip is not None and chip <= -CHIP else -1 if lv <= -2 and chip is not None and chip >= CHIP else 0]
        if out:
            rows[p["k"]] = dict(g=p["g"], t=p["t"], m=round(mn, 1), s=out)
    top = {}
    for x in rows.values():
        for st, c in x["s"].items():
            key = (x["g"], x["t"], st)
            top[key] = max(top.get(key, -9), c[1])
    for x in rows.values():
        for st, c in x["s"].items():
            c[2] = int(c[1] == top[(x["g"], x["t"], st)])
    return dict(bands=list(BANDS), min=MIN_MIN, p={k: dict(m=x["m"], s=x["s"]) for k, x in rows.items()})
