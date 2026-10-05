#!/usr/bin/env python3
"""
Prop patterns: traits the NFL props that hit had in common (found in weeks 1-3 of 2026, 11,434
settled Kalshi props), tagged on upcoming markets and tracked forward.

    python patterns.py        # grade every settled NFL market file -> markets/patterns.json

"Hit vs priced": how often props with the trait hit, against what their prices implied. A pattern
is only worth acting on if it keeps beating its price after it was found (the "since tracking"
record), so both are kept separately. Tags for the live slate are added by markets.write_live
(tag_nfl), so the pages and this grader use the same rules.
"""

import glob
import json
import os
import re

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
TRACK_FROM = "2026-10-05"          # slates on/after this date count as "since tracking"
OVERS = ("rec", "rec_yds", "rush_yds", "pass_yds")

PATTERNS = {
    "high_total": dict(short="high total", sign=+1, label="Overs in a high-total game (48+)",
                       desc="Yardage and catch overs when the Vegas game total is 48 or more."),
    "dog_wr1": dict(short="dog WR1", sign=+1, label="Underdog's #1 receiver, receiving yards overs",
                    desc="The underdog team's top projected receiver (a WR): receiving-yard overs."),
    "dog_qb": dict(short="dog QB", sign=+1, label="Underdog QB passing yards overs",
                   desc="Passing-yard overs for the underdog's quarterback (trailing teams throw more)."),
    "te_td": dict(short="TE TD", sign=+1, label="Tight end anytime TD",
                  desc="Anytime-TD YES on tight ends."),
    "low_rung": dict(short="low rung", sign=+1, label="Lowest rung of the ladder",
                     desc="The easiest line on a player's yardage or catch ladder."),
    "cheap_over": dict(short="cheap over", sign=+1, label="Cheap overs (10-25¢)",
                       desc="Yardage and catch overs priced 10-25¢; they tended to climb before kickoff, so buy early."),
    "low_total": dict(short="low total", sign=-1, label="Overs in a low-total game (42.5 or less)",
                      desc="Yardage and catch overs when the Vegas game total is 42.5 or less hit LESS often than priced."),
    "rb_td": dict(short="RB TD", sign=-1, label="Running back anytime TD",
                  desc="Anytime-TD YES on running backs scored LESS often than priced, most of all on underdogs."),
}

# when to buy, from the Kalshi price-history study (2026-10-05)
TIMING = {"anytime_td": "Buy TDs late (after inactives): TD prices slip ~0.5¢ into kickoff.",
          "first_td": "Buy late, after inactives.",
          "win": "Moneylines can be taken early in the week.",
          "total": "No timing edge found on totals.",
          "props": "Buy late (after inactives), when spreads are tightest; cheap overs (10-25¢) are the exception: they climb ~2¢ from 2 days out."}


def context(players, teams):
    """{(game, pid): dict(pos, team, fav, total, role{stat})} from one week's projection tables."""
    T = teams.set_index(["game_id", "team"])
    ctx = {}
    for (gid, team, stat), x in players.groupby(["game_id", "team", "stat"]):
        x = x.sort_values("mean", ascending=False)
        for rank, r in enumerate(x.itertuples(), 1):
            key = (gid, str(r.player_id))
            c = ctx.setdefault(key, dict(pos=r.pos, team=team, role={}))
            c["role"][stat] = rank
            if "total" not in c and (gid, team) in T.index and (gid, r.opp) in T.index:
                me, op = T.loc[(gid, team), "vegas_implied"], T.loc[(gid, r.opp), "vegas_implied"]
                c["total"], c["fav"] = float(me + op), bool(me > op)
    return ctx


def tag_nfl(kind, line, price, pid, game, ctx, ladder_low):
    """Pattern ids that apply to one market (YES side). ladder_low: lowest line offered for this player/kind."""
    c = ctx.get((game, str(pid))) if pid is not None else None
    if not c:
        return []
    out = []
    tot, fav = c.get("total"), c.get("fav")
    if kind in OVERS:
        if tot is not None and tot >= 48:
            out.append("high_total")
        if tot is not None and tot <= 42.5:
            out.append("low_total")
        if kind == "rec_yds" and c["pos"] == "WR" and c["role"].get("rec_yds") == 1 and fav is False:
            out.append("dog_wr1")
        if kind == "pass_yds" and c["pos"] == "QB" and fav is False:
            out.append("dog_qb")
        if line is not None and ladder_low is not None and abs(line - ladder_low) < 1e-9:
            out.append("low_rung")
        if price is not None and .10 <= price <= .25:
            out.append("cheap_over")
    elif kind == "anytime_td":
        if c["pos"] == "TE":
            out.append("te_td")
        if c["pos"] == "RB":
            out.append("rb_td")
    return out


def week_tables(game_id):
    m = re.match(r"(\d{4})_(\d{2})_", str(game_id))
    if not m:
        return None
    for d in ("projections/weeks", "projections"):
        stem = os.path.join(HERE, d, f"{m.group(1)}_wk{m.group(2)}")
        if os.path.exists(stem + "_players.csv") and os.path.exists(stem + "_teams.csv"):
            return stem
    return None


def ladders(markets):
    low = {}
    for r in markets:
        if r["kind"] in OVERS and r.get("pid") is not None and r.get("line") is not None:
            k = (r["game"], str(r["pid"]), r["kind"])
            low[k] = min(low.get(k, 1e9), r["line"])
    return low


def main():
    import grade_markets as G
    R = G.nfl_results()
    ctx_cache, rows = {}, []
    for f in sorted(glob.glob(os.path.join(HERE, "markets", "nfl_????-??-??.json"))):
        date = os.path.basename(f)[4:14]
        ms = [r for r in json.load(open(f)).get("markets", []) if r["kind"] in OVERS + ("anytime_td",)]
        low = ladders(ms)
        for r in ms:
            stem = week_tables(r["game"])
            if stem is None:
                continue
            if stem not in ctx_cache:
                ctx_cache[stem] = context(pd.read_csv(stem + "_players.csv"), pd.read_csv(stem + "_teams.csv"))
            m = G.mid(r)
            pats = tag_nfl(r["kind"], r.get("line"), m, r.get("pid"), r["game"], ctx_cache[stem], low.get((r["game"], str(r.get("pid")), r["kind"])))
            if not pats:
                continue
            y = G.outcome("nfl", r, R)
            if y is None or m is None:
                continue
            for p in pats:
                rows.append(dict(p=p, y=y, m=m, pg=f"{r['game']}|{r.get('pid')}", tracked=date >= TRACK_FROM, src=r["source"]))
    d = pd.DataFrame(rows, columns=["p", "y", "m", "pg", "tracked", "src"])
    rec = lambda x: dict(n=int(len(x)), player_games=int(x.pg.nunique()), hit=round(float(x.y.mean()), 4) if len(x) else None,
                         priced=round(float(x.m.mean()), 4) if len(x) else None)
    out = {}
    for pid, meta in PATTERNS.items():
        x = d[d.p == pid]
        out[pid] = dict(meta, found=rec(x[~x.tracked]), tracking=rec(x[x.tracked]), since=TRACK_FROM)
    json.dump(dict(patterns=out, timing=TIMING), open(os.path.join(HERE, "markets", "patterns.json"), "w"), indent=1)
    for k, v in out.items():
        f, t = v["found"], v["tracking"]
        print(f"{v['label'][:52]:52s} found: {f['hit'] if f['hit'] is not None else '-'} vs {f['priced'] if f['priced'] is not None else '-'} (n {f['n']}) | since {TRACK_FROM}: n {t['n']}")


if __name__ == "__main__":
    main()
