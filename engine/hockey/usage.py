"""
Usage ranking for the NHL goal sheet (display only: P(goal) and its tiers stay the live model's).

From each completed game's shift chart + play-by-play, per skater: shot attempts while on the ice (his and his
team's), shifts and ice time, overtime, last 5 minutes of a tied / one-goal 3rd, and 6-on-5 time. Season to date
these become five usage measures; the ranking score is the live model's expected goals x exp(beta . usage), with
beta fit inside the live goal model's design on 2023-26 (usage_params.json).

Backtest 2025-26 (fit 2023-25): ranking by that score caught 1.045 scorers per game in each game's top 3 vs 1.032
for the live model (both halves better), top 5 of the night 39.4% vs 38.0%. Adding usage to the probabilities
themselves made the goal tiers less accurate, so it only reorders.
"""

import gzip
import json
import os

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
PARAMS = json.load(open(os.path.join(HERE, "usage_params.json")))
ROWS = os.path.join(HERE, "raw", "usage_rows.csv")
COLS = ["game_id", "player_id", "team", "icf", "oncf", "blk", "shifts", "toi_s", "ot_s", "ot_team", "late_s", "late_team", "x6_s", "x6_team"]
_sec = lambda t: int(str(t).split(":")[0]) * 60 + int(str(t).split(":")[1])


def game_usage(gid, pos, shots, shift_rows):
    sh = [x for x in shift_rows if x.get("typeCode") == 517 and x.get("duration")]
    if not sh:
        return []
    rows = [((x["period"] - 1) * 1200 + _sec(x["startTime"]), (x["period"] - 1) * 1200 + _sec(x["endTime"]), x["playerId"], x["teamAbbrev"]) for x in sh]
    end = max(r[1] for r in rows) + 1
    teams = sorted({r[3] for r in rows})
    if len(teams) != 2:
        return []
    on, ids, nshift = {}, {}, {}
    for t in teams:
        pl = sorted({r[2] for r in rows if r[3] == t and pos.get(r[2]) not in (None, "G")})
        ix = {q: i for i, q in enumerate(pl)}
        m = np.zeros((end, len(pl)), bool)
        for a, b, q, tt in rows:
            if tt == t and q in ix and b > a:
                m[a:b, ix[q]] = True
                nshift[q] = nshift.get(q, 0) + 1
        on[t], ids[t] = m, pl
    sc = {t: np.zeros(end, int) for t in teams}
    for r in shots[shots.goal == 1].itertuples():
        s_ = int((r.period - 1) * 1200 + r.t)
        if r.team in sc and s_ < end:
            sc[r.team][s_:] += 1
    tsec = np.arange(end)
    late = (tsec >= 2 * 1200 + 900) & (tsec < 3 * 1200) & (np.abs(sc[teams[0]] - sc[teams[1]]) <= 1)
    ot = tsec >= 3 * 1200
    out = []
    for t in teams:
        m = on[t]
        x6 = m.sum(1) >= 6
        att = shots[shots.team == t]
        aidx = np.clip(((att.period - 1) * 1200 + att.t).astype(int).to_numpy(), 0, end - 1)
        onc = m[aidx].sum(0)
        for i, q in enumerate(ids[t]):
            mine = att[att.shooter == q]
            out.append((gid, q, t, len(mine), int(onc[i]), int((mine.kind == "blocked-shot").sum()), nshift.get(q, 0), int(m[:, i].sum()),
                        int(m[ot, i].sum()), int(ot.sum()), int(m[late, i].sum()), int(late.sum()), int(m[x6, i].sum()), int(x6.sum())))
    return out


def update(con, season):
    """Usage rows for every completed game of the season not yet in raw/usage_rows.csv (fetches shift charts)."""
    import linecheck as LC
    have = pd.read_csv(ROWS) if os.path.exists(ROWS) else pd.DataFrame(columns=COLS)
    done = set(have.game_id)
    gids = [g for (g,) in con.execute("SELECT game_id FROM games WHERE season = ? AND game_type IN (2, 3) AND state IN ('FINAL', 'OFF')", (season,))
            if g not in done]
    if not gids:
        return _types(have)
    toi = pd.read_sql(f"SELECT game_id, player_id, pos FROM toi WHERE game_id IN ({','.join(map(str, gids))})", con)
    shots = pd.read_sql(f"SELECT game_id, period, t, team, kind, goal, shooter, empty_net FROM shots WHERE game_id IN ({','.join(map(str, gids))})", con)
    shots = shots[shots.empty_net.fillna(0) == 0]
    new = []
    for g in gids:
        try:
            sr = LC.shifts(g)
        except Exception:
            continue
        x = toi[toi.game_id == g]
        new += game_usage(g, dict(zip(x.player_id, x.pos)), shots[shots.game_id == g], sr)
    if new:
        have = pd.concat([have, pd.DataFrame(new, columns=COLS)], ignore_index=True)
        os.makedirs(os.path.dirname(ROWS), exist_ok=True)
        have.to_csv(ROWS, index=False)
    return _types(have)


def _types(df):
    df = df.copy()
    df[["game_id", "player_id"]] = df[["game_id", "player_id"]].astype("int64")
    df[COLS[3:]] = df[COLS[3:]].astype(float)
    return df


def scores(rows, season_game_ids, player_ids):
    """exp(beta . usage) per player from his games so far this season (shrunk toward average with few games)."""
    U = rows[rows.game_id.isin(season_game_ids)]
    s = U.astype({c: float for c in COLS[3:]}).groupby("player_id")[COLS[3:]].sum().reindex(player_ids).fillna(0)
    gp = U.groupby("player_id").size().reindex(player_ids).fillna(0)
    lg, mu, beta = PARAMS["lg_att"], PARAMS["mu"], PARAMS["beta"]
    f = pd.DataFrame(index=s.index)
    f["l_att"] = np.log((s.icf + 40 * lg) / (s.oncf + 40) / lg)
    f["l_len"] = np.log((s.toi_s + 20 * 45) / (s.shifts + 20) / 45)
    f["l_spg"] = np.log((s.shifts + 3 * 20) / (gp + 3) / 20)
    f["l_x6"] = np.log((s.x6_s + 5) / (s.x6_team + 30) + .01)
    f["l_late"] = np.log((s.late_s + 30) / (s.late_team + 120))
    z = sum(beta[i] * (f[c] - mu[c]) for i, c in enumerate(PARAMS["features"]))
    return np.exp(z)
