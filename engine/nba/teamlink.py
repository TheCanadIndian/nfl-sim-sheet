#!/usr/bin/env python3
"""NBA teammate links from the play-by-play (owner 2026-11-06, after research/pbp_factors.py: 'yes' to tracking + chips).

Every game's assists by passer -> scorer (made field goals, no free throws) and the game's first baskets are stored in nba.db
(fetch.py calls `store`; same backfill as opps_v2):
  assist_pairs  game_id, team, passer, scorer, n
  pbp_first     game_id, tip (team that got the opening tip), fg_team, fg_p (first made field goal), fp_team, fp_p (first points)

For tonight's games (`attach`, called by project.py before the payload is written) a teammate is 'key and out' when he is on
tonight's out list, averaged 24+ minutes in his last 10 games and played for the team in its last 10 games. Each player with
such a teammate out gets `tl` = {f: share of HIS assisted baskets that came from the out teammate(s) (20+ assisted baskets this
season), a: share of HIS assists that went to them (20+ assists), w: their names}. The study, Kalshi Jan - Nov 2026, one rung
per player-stat-game nearest 50c, vs the mid (baseline -1.6 pts): f 25%+ -> his points / threes overs -4.8 pts on 691, every
period (z -2.6); a under 15% -> his assists overs +3.8 pts on 759, 4 of 4 periods >= 0 (z +2.1, +2.0% at the ask after fee).
Display + tracking only (markets/teamlink_nba.json -> profit.tags src 'tl').
"""
import collections
import json
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
KEY_MIN, MIN_N, FED, FREE = 24, 20, .25, .15


def ensure(con):
    con.execute("CREATE TABLE IF NOT EXISTS assist_pairs (game_id INTEGER, team TEXT, passer TEXT, scorer TEXT, n INTEGER)")
    con.execute("CREATE TABLE IF NOT EXISTS pbp_first (game_id INTEGER, tip TEXT, fg_team TEXT, fg_p TEXT, fp_team TEXT, fp_p TEXT)")
    # per team: its first shot attempt, its first made FG, its jumper on the opening tip (first-basket model, owner 2026-11-07)
    con.execute("CREATE TABLE IF NOT EXISTS pbp_open (game_id INTEGER, team TEXT, shot1 TEXT, fg1 TEXT, jumper TEXT, won_tip INTEGER)")


def game_rows(gid, sm):
    import plays as PLAYS
    rows = PLAYS.parse(int(gid), sm)
    c = collections.Counter()
    tip = fg = fp = None
    for r in rows:
        team, typ, shot, made, p1, p2, period = r[4], str(r[6] or ""), r[7], r[8], r[13], r[14], r[2]
        if tip is None and period == 1 and typ == "Jumpball":
            tip = team
        if shot and made:
            if fp is None:
                fp = (team, p1)
            if "Free Throw" not in typ:
                if fg is None:
                    fg = (team, p1)
                if p2:
                    c[(team, str(p2), str(p1))] += 1
    ap = [(int(gid), t, a, b, n) for (t, a, b), n in c.items()]
    first = (int(gid), tip, fg[0] if fg else None, str(fg[1]) if fg else None, fp[0] if fp else None, str(fp[1]) if fp else None)
    return ap, first, open_rows(gid, rows, tip)


def open_rows(gid, rows, tip):
    """Each team's opening: first shot attempt (no FTs), first made FG, its jumper on the opening tip."""
    team_of, s1, f1, jb = {}, {}, {}, None
    for r in rows:
        team, typ, shot, made, p1, p2, period = r[4], str(r[6] or ""), r[7], r[8], r[13], r[14], r[2]
        if p1 and team:
            team_of.setdefault(str(p1), team)
        if period == 1 and typ == "Jumpball" and jb is None:
            jb = (str(p1) if p1 else None, str(p2) if p2 else None)
        if period == 1 and shot and "Free Throw" not in typ and team:
            s1.setdefault(team, str(p1) if p1 else None)
            if made:
                f1.setdefault(team, str(p1) if p1 else None)
    jt = {}
    for p in (jb or ()):
        if p and team_of.get(p):
            jt[team_of[p]] = p
    return [(int(gid), t, s1.get(t), f1.get(t), jt.get(t), int(t == tip) if tip else None) for t in set(s1) | set(jt)]


def store(con, games):
    """games: {game_id: summary json}. Stores what isn't there yet."""
    ensure(con)
    have = {r[0] for r in con.execute("SELECT game_id FROM pbp_first")}
    have_o = {r[0] for r in con.execute("SELECT DISTINCT game_id FROM pbp_open")}
    n = 0
    for gid, sm in games.items():
        if (int(gid) in have and int(gid) in have_o) or not sm:
            continue
        try:
            ap, first, op = game_rows(gid, sm)
        except Exception:                                     # noqa: BLE001 -- one bad summary must not stop the rest
            continue
        if int(gid) not in have:
            con.executemany("INSERT INTO assist_pairs VALUES (?,?,?,?,?)", ap)
            con.execute("INSERT INTO pbp_first VALUES (?,?,?,?,?,?)", first)
        if int(gid) not in have_o and op:
            con.executemany("INSERT INTO pbp_open VALUES (?,?,?,?,?,?)", op)
        n += 1
    return n


def missing(con, game_ids):
    ensure(con)
    have = {r[0] for r in con.execute("SELECT game_id FROM pbp_first")} & {r[0] for r in con.execute("SELECT DISTINCT game_id FROM pbp_open")}
    return [g for g in game_ids if int(g) not in have]


def attach(payload, db, season):
    """Adds `tl` to tonight's players and returns {gid: {start, players: {norm name: tl}}} for markets/teamlink_nba.json."""
    import pandas as pd
    import parlays as PL
    con = sqlite3.connect(db)
    ensure(con)
    gm = pd.read_sql(f"SELECT game_id, date, home, away FROM games WHERE season={int(season)} AND state='FINAL'", con)
    pl = pd.read_sql("SELECT game_id, team, player_id, name, dnp, min FROM players WHERE game_id IN (%s)" % (",".join(map(str, gm.game_id)) or "0"), con)
    ap = pd.read_sql("SELECT game_id, team, passer, scorer, n FROM assist_pairs WHERE game_id IN (%s)" % (",".join(map(str, gm.game_id)) or "0"), con)
    if pl.empty or ap.empty:
        return {}
    pl = pl.merge(gm[["game_id", "date"]], on="game_id").sort_values("date")
    pl["pid"] = pl.player_id.astype(str); pl["nn"] = pl.name.map(PL.jsnorm)
    played = pl[(pl.dnp == 0) & (pl["min"].fillna(0) > 0)]
    min10 = played.groupby("pid")["min"].apply(lambda s: s.tail(10).mean())
    pid_of = {(t, n): p for t, n, p in zip(pl.team, pl.nn, pl.pid)}
    recent = {}
    for t in pd.unique(pd.concat([gm.home, gm.away])):
        last10 = gm[(gm.home == t) | (gm.away == t)].sort_values("date").game_id.tail(10)
        recent[t] = set(played[(played.team == t) & played.game_id.isin(last10)].pid)
    fed_den = ap.groupby("scorer").n.sum(); ast_den = ap.groupby("passer").n.sum()
    out_json = {}
    for g in payload["games"]:
        outs = {}
        for t, L in (g.get("out") or {}).items():
            ids = []
            for o in L:
                p = pid_of.get((t, PL.jsnorm(o["n"])))
                if p and min10.get(p, 0) >= KEY_MIN and p in recent.get(t, set()):
                    ids.append((p, o["n"]))
            if ids:
                outs[t] = ids
        if not outs:
            continue
        G = out_json.setdefault(str(g["id"]), dict(start=g.get("start_utc"), outs={t: [n for _, n in v] for t, v in outs.items()}, players={}))
        for x in payload["players"]:
            if int(x["g"]) != int(g["id"]) or x["t"] not in outs:
                continue
            me = str(x.get("id"))
            oid = {p for p, _ in outs[x["t"]]}
            if me in oid:
                continue
            fd, ad = fed_den.get(me, 0), ast_den.get(me, 0)
            f = float(ap[(ap.scorer == me) & ap.passer.isin(oid)].n.sum() / fd) if fd >= MIN_N else None
            a = float(ap[(ap.passer == me) & ap.scorer.isin(oid)].n.sum() / ad) if ad >= MIN_N else None
            if f is None and a is None:
                continue
            x["tl"] = dict(f=None if f is None else round(f, 3), a=None if a is None else round(a, 3), w=[n for _, n in outs[x["t"]]])
            G["players"][PL.jsnorm(x["n"])] = x["tl"]
    return out_json


def write(payload, db, season):
    try:
        import parlays as PL
        out = attach(payload, db, season)
        json.dump(dict(games=out), open(os.path.join(PL.SITE, "markets", "teamlink_nba.json"), "w"), separators=(",", ":"))
        n = sum(len(v["players"]) for v in out.values())
        print(f"teammate links: {len(out)} games with a key teammate out, {n} players tagged")
    except Exception as e:                                    # noqa: BLE001 -- display / tracking only
        print(f"teammate links: skipped ({type(e).__name__}: {e})")
