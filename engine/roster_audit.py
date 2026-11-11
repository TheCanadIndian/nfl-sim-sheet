#!/usr/bin/env python3
"""Weekly NFL roster check (edge audit fix E, owner 2026-11-11). project.py saves, per game, the last pregame list of excluded
players and the active list (projections/weeks/<season>_wk<NN>_out.json). After the games this compares both with who played:
  excluded but played  -- a projected-out player with a carry, target or pass (the bug fix E targets: stale ESPN inactives)
  projected but sat    -- an active skill player we projected with no offensive snap
-> markets/roster_audit_nfl.json {week: {...}} and a printed summary (run.py nfl, after the box grades).

    python roster_audit.py [season]
"""
import glob, json, os, re, sqlite3, sys
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
SITE = os.environ.get("SIM_SITE") or os.path.join(HERE, "site")
norm = lambda s: re.sub(r"\s+", " ", re.sub(r"\b(jr|sr|ii|iii|iv)\b", "", re.sub(r"[^a-z ]", "", str(s).lower()))).strip()


def main(season=None):
    con = sqlite3.connect(os.path.join(HERE, "nfl.db"))
    season = int(season or con.execute("SELECT MAX(season) FROM games").fetchone()[0])
    pw = pd.read_sql("SELECT week, player_id, player_display_name n, recent_team t, position pos, carries, targets, attempts "
                     "FROM player_week WHERE season = ?", con, params=(season,))
    pw["nm"] = pw.n.map(norm)
    pw["use"] = pw[["carries", "targets", "attempts"]].fillna(0).sum(axis=1)
    try:
        sn = pd.read_sql("""SELECT s.game_id, r.gsis_id AS player_id FROM snaps s JOIN (SELECT DISTINCT pfr_id, gsis_id FROM rosters
                            WHERE pfr_id IS NOT NULL) r ON r.pfr_id = s.pfr_player_id WHERE s.offense_snaps > 0 AND s.season = ?""",
                         con, params=(season,))
    except Exception:                                                        # noqa: BLE001
        sn = pd.DataFrame(columns=["game_id", "player_id"])
    pos = pd.read_sql("SELECT DISTINCT gsis_id AS player_id, position FROM rosters WHERE season = ?", con, params=(season,)).drop_duplicates("player_id")
    out = {}
    for f in sorted(glob.glob(os.path.join(HERE, "projections", "weeks", f"{season}_wk??_out.json"))):
        wk = int(re.search(r"wk(\d+)", f).group(1))
        if not (pw.week == wk).any():
            continue                                                         # box scores not in yet
        G = json.load(open(f))
        a = pw[pw.week == wk]
        bad, sat, n_out, n_act = [], [], 0, 0
        for gid, x in G.items():
            for t_, n, why in x.get("out", []):
                n_out += 1
                r = a[(a.nm == norm(n)) & (a.use > 0)]
                if len(r):
                    bad.append(dict(game=gid, team=t_, name=n, why=why, touches=int(r.use.iloc[0])))
            played = set(sn[sn.game_id == gid].player_id)
            if played:
                act = [p for p in x.get("actives", []) if pos.set_index("player_id").position.get(p) in ("QB", "RB", "WR", "TE")]
                n_act += len(act)
                sat += [p for p in act if p not in played]
        out[str(wk)] = dict(excluded=n_out, excluded_played=bad, actives=n_act, projected_sat=len(sat),
                            projected_sat_ids=sat[:60])
        print(f"roster audit nfl {season} wk{wk}: {len(bad)} of {n_out} excluded players played"
              + (" (" + ", ".join("%s %s %d [%s]" % (b["name"], b["team"], b["touches"], b["why"]) for b in sorted(bad, key=lambda b: -b["touches"])[:6]) + ")" if bad else "")
              + f"; {len(sat)} of {n_act} projected skill players had no snap")
    fn = os.path.join(SITE, "markets", "roster_audit_nfl.json")
    try:
        old = json.load(open(fn))
    except (OSError, ValueError):
        old = {}
    old.setdefault(str(season), {}).update(out)
    os.makedirs(os.path.dirname(fn), exist_ok=True)
    json.dump(old, open(fn, "w"), indent=1)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
