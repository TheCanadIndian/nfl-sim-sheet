#!/usr/bin/env python3
"""
Rebound and assist chances from ESPN play-by-play (owner 2026-10-23: "avg 3pt attempts, rebound chances vs rebounds,
assist chances vs assists"). NBA's own tracking numbers (stats.nba.com) block us, so these are ours, from who was on
the floor (plays.parse: starters, every substitution, each period's opening five):
  reb_ch   rebounds available while he was on the floor (every player rebound by either team; team rebounds left out,
           as Basketball-Reference's TRB% does)
  ast_ch   his teammates' made field goals while he was on the floor (free throws excluded) -- assist chances; ESPN
           doesn't log passes that led to misses, so NBA's "potential assists" run higher than this
One row per player-game in nba.db table `opps_v2` (game_id, player_id, reb_ch, reb, ast_ch, ast), filled by fetch.py.
"""
import plays as PLAYS

REB = ("Rebound",)


def game_opps(gid, summary):
    """[(game_id, player_id, reb_ch, reb, ast_ch, ast)] for one ESPN summary."""
    rows = PLAYS.parse(gid, summary)
    if not rows:
        return []
    comp = ((summary.get("header") or {}).get("competitions") or [{}])[0]
    ha = {c["homeAway"]: c["team"]["abbreviation"] for c in comp.get("competitors", [])}
    C = {}
    def bump(pid, k):
        C.setdefault(pid, [0, 0, 0, 0])[k] += 1
    for r in rows:
        (_, _, _, _, team, _tid, text, shot, make, _val, att, _x, _y, p1, p2, _hs, _as, on_h, on_a) = r
        on = {ha.get("home"): [x for x in (on_h or "").split(",") if x], ha.get("away"): [x for x in (on_a or "").split(",") if x]}
        if "Rebound" in text and p1:                    # player rebounds only (team rebounds aren't available to anyone)
            for side in on.values():
                for pid in side:
                    bump(pid, 0)
            bump(p1, 1)
        elif shot and make and att != 1 and team in on:
            for pid in on[team]:
                if pid != p1:
                    bump(pid, 2)
            if p2:
                bump(p2, 3)
    return [(int(gid), int(pid), *v) for pid, v in C.items() if pid.isdigit()]
