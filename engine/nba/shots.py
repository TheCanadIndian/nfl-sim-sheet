"""Shot locations from the ESPN game summaries (owner 2026-11-23: 'I want to create an NBA shot map per player per team' ->
'defense and offense heatmaps like in nhl'). fetch.py stores every field-goal attempt with ESPN's court coordinates into
nba.db table `shots` (new games as they're fetched, plus earlier games of the season still missing, like teamlink.py);
shotmap.py turns them into the NBA page's shot maps.

ESPN coordinates: x 0-50 across the court (rim at 25), y along it with the rim at about 1.25 (dunks average 1.4; the
baseline is about -4, the top of the three-point arc about 25). Free throws carry no location and are left out.

    shots       game_id, team, player_id, x, y, made, three
    shots_done  game_id  (games read, even if ESPN had no coordinates, so they aren't fetched again)
"""


def ensure(con):
    con.execute("CREATE TABLE IF NOT EXISTS shots (game_id INTEGER, team TEXT, player_id INTEGER, x INTEGER, y INTEGER, made INTEGER, three INTEGER)")
    con.execute("CREATE TABLE IF NOT EXISTS shots_done (game_id INTEGER PRIMARY KEY)")


def game_rows(gid, sm):
    teams = {str(t["team"]["id"]): t["team"]["abbreviation"] for t in (sm.get("boxscore") or {}).get("teams", [])}
    out = []
    for p in sm.get("plays") or []:
        att = int(p.get("pointsAttempted") or 0)
        if not p.get("shootingPlay") or att not in (2, 3):
            continue
        c = p.get("coordinate") or {}
        x, y = c.get("x"), c.get("y")
        parts = [x_["athlete"]["id"] for x_ in p.get("participants") or [] if x_.get("athlete")]
        tm = teams.get(str((p.get("team") or {}).get("id") or ""))
        if x is None or y is None or abs(x) > 100 or abs(y) > 100 or not parts or not tm:
            continue
        out.append((int(gid), tm, int(parts[0]), int(round(x)), int(round(y)), int(bool(p.get("scoringPlay"))), int(att == 3)))
    return out


def store(con, games):
    """games: {game_id: summary}. Returns how many games were stored."""
    ensure(con)
    n = 0
    for gid, sm in games.items():
        rows = game_rows(gid, sm)
        con.execute("DELETE FROM shots WHERE game_id = ?", (int(gid),))
        if rows:
            con.executemany("INSERT INTO shots VALUES (?,?,?,?,?,?,?)", rows)
            n += 1
        con.execute("INSERT OR IGNORE INTO shots_done VALUES (?)", (int(gid),))
    return n


def missing(con, game_ids):
    ensure(con)
    have = {r[0] for r in con.execute("SELECT game_id FROM shots_done")}
    return [g for g in game_ids if g not in have]
