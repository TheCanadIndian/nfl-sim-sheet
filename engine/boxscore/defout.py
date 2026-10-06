"""
Missing defensive starters and the offensive players who tend to see more targets because of it.

Starters: players averaging 60%+ of a defense's snaps over its last 4 games (snap counts), by group:
CB (ranked 1-3 by snap share; the 3rd is usually the slot/nickel), S, LB, DL. Status for an upcoming
game comes from the weekly injury report and ESPN's game-day list (project.espn_out); for a played game,
"out" means he took no defensive snaps.

What offenses did when they were out (2022-26, adjusted for offense and defense season strength;
scratchpad study 2026-10-05) -- only the shifts that held up are flagged:
  top corner (CB1) out        -> the WR2 gained ~1.2 pts of target share (z 2.0), TEs lost some
  2+ defensive backs out      -> WRs +1.4 pts of target share (z 2.2), mostly WR2/WR3
  a starting linebacker out   -> the TE1 gained ~0.7 pts of target share (z 1.8)
Slot-corner and safety absences showed no target shift (safety out: fewer deep shots, if anything).
Our projections did not miss systematically in these games, so these are context flags, tracked by
patterns.py, not model inputs.
"""

import re

import pandas as pd

GROUP = {"CB": "CB", "DB": "CB", "FS": "S", "SS": "S", "S": "S", "LB": "LB", "ILB": "LB", "OLB": "LB", "MLB": "LB",
         "DE": "DL", "DT": "DL", "NT": "DL", "DL": "DL"}
ROLE_NAME = {"CB1": "top corner", "CB2": "No. 2 corner", "CB3": "slot corner", "S": "safety", "LB": "linebacker", "DL": "D-lineman"}


def norm(n):
    n = re.sub(r"[^a-z ]", "", str(n).lower().replace("-", " "))
    return re.sub(r"\s+(jr|sr|ii|iii|iv|v)$", "", n).strip()


def _snaps(con):
    s = pd.read_sql("SELECT game_id, team, player, pfr_player_id pid, position, defense_snaps, defense_pct FROM snaps", con)
    s["grp"] = s.position.map(GROUP)
    s = s[s.grp.notna()]
    g = pd.read_sql("SELECT game_id, gameday FROM games", con)
    return s.merge(g, on="game_id")


def starters(sn, team, before):
    """[(name, pid, role, share)] for a defense's regular starters going into a game on `before`."""
    t = sn[(sn.team == team) & (sn.gameday < str(before)[:10])]
    last = t.drop_duplicates("game_id").sort_values("gameday").game_id.iloc[-4:]
    if len(last) < 2:
        return []
    t = t[t.game_id.isin(last)]
    share = (t.groupby(["grp", "pid", "player"]).defense_pct.sum() / len(last)).reset_index()
    out = []
    cb = share[share.grp == "CB"].sort_values("defense_pct", ascending=False).head(3)
    for k, r in enumerate(cb.itertuples(), 1):
        if r.defense_pct >= (.45 if k == 3 else .60):
            out.append((r.player, r.pid, f"CB{k}", r.defense_pct))
    for grp in ("S", "LB", "DL"):
        for r in share[(share.grp == grp) & (share.defense_pct >= .60)].itertuples():
            out.append((r.player, r.pid, grp, r.defense_pct))
    return out


def upcoming(con, todo, season, week, espn=None):
    """{(game_id, defteam): [dict(name, role, status)]} for missing / doubtful-to-play starters."""
    sn = _snaps(con)
    inj = pd.read_sql(f"SELECT team, full_name, report_status FROM injuries WHERE season={season} AND week={week}", con)
    rep = {(r.team, norm(r.full_name)): r.report_status for r in inj.itertuples() if isinstance(r.report_status, str)}
    espn = espn or {}
    res = {}
    for g in todo.itertuples():
        for team in (g.home_team, g.away_team):
            miss = []
            for name, pid, role, share in starters(sn, team, g.gameday):
                k = (team, norm(name))
                st = espn.get(k) or rep.get(k)
                if not st:
                    continue
                st = "out" if str(st).startswith(("Out", "Doubtful")) else "questionable" if str(st).startswith("Questionable") else None
                if st:
                    miss.append(dict(name=name, role=role, status=st))
            if miss:
                res[(g.game_id, team)] = miss
    return res


def played(con, game_ids):
    """Same, for games already played: starters (by the 4 games before) who took no defensive snaps."""
    sn = _snaps(con)
    g = pd.read_sql("SELECT game_id, gameday, home_team, away_team FROM games", con).set_index("game_id")
    res = {}
    for gid in game_ids:
        if gid not in g.index:
            continue
        row = g.loc[gid]
        here = set(sn[(sn.game_id == gid) & (sn.defense_snaps > 0)].pid)
        for team in (row.home_team, row.away_team):
            miss = [dict(name=n, role=r, status="out") for n, pid, r, s in starters(sn, team, row.gameday) if pid not in here]
            if miss:
                res[(gid, team)] = miss
    return res


def offense_flags(missing, off_players):
    """missing: list for the DEFENSE; off_players: DataFrame (player, pos, tgt) for the offense facing it.
    Returns {player: [tag]} in the page's matchup-tag format (kind='defout')."""
    if not missing or off_players.empty:
        return {}
    wr = off_players[off_players.pos == "WR"].sort_values("tgt", ascending=False).player.tolist()
    te = off_players[off_players.pos == "TE"].sort_values("tgt", ascending=False).player.tolist()
    out = {}

    def add(player, short, text, strength, sure):
        if player:
            out.setdefault(player, []).append(dict(kind="defout", dir=1, strength=strength if sure else strength / 2,
                                                   strong=False, short=short + ("" if sure else "?"),
                                                   text=text + ("" if sure else " -- if he sits (listed Questionable)")))
    who = lambda roles: [m for m in missing if m["role"] in roles]
    for m in who({"CB1"}):
        add(wr[1] if len(wr) > 1 else None, "CB1 out",
            f"{m['name']} (their top corner) {m['status']}: when a defense's top corner sat, the WR2 gained ~1.2 pts of target share (2022-26)",
            .012, m["status"] == "out")
    dbs = [m for m in missing if m["role"] in ("CB1", "CB2", "CB3", "S")]
    if len([m for m in dbs if m["status"] == "out"]) >= 2 or len(dbs) >= 2:
        sure = len([m for m in dbs if m["status"] == "out"]) >= 2
        names = ", ".join(m["name"] for m in dbs)
        for p in wr[1:3]:
            add(p, "2 DBs out", f"{names} {'out' if sure else 'out/questionable'}: with 2+ defensive backs missing, WRs gained ~1.4 pts of target share (2022-26)", .014, sure)
    for m in who({"LB"}):
        add(te[0] if te else None, "LB out",
            f"{m['name']} (starting linebacker) {m['status']}: TE1s gained ~0.7 pts of target share when a starting LB sat (2022-26)",
            .007, m["status"] == "out")
    return out
