"""
As-of-kickoff priors for teams and players.

Every prior for a game uses only games strictly before it, so backtests are
leak-free. Rates are ratio-of-decayed-sums with a pseudocount pulling small
samples toward a baseline:

    prior = (sum_w num + k * base) / (sum_w den + k)

Weights decay by game (half-life HL) and are multiplied by CARRY at each
season boundary, and for players also at a team change.
"""

import numpy as np
import pandas as pd

TEAM_HL, TEAM_CARRY = 6.0, 0.45
# Player settings grid-searched on 2023-24 target/carry share prediction
# (weighted MSE of renormalized shares vs actual).
PLAYER_HL, PLAYER_CARRY = 5.0, 0.4
K_TGT, K_CAR = 20, 15
K_CATCH = 40                 # catch-rate pseudo-targets toward the position average
K_YPR, K_YPC = 30, 80        # yards-per-catch / per-carry pseudo-counts toward the position average
K_REC_TD, K_RUSH_TD = 20, 15  # TD-share pseudo-TDs toward red-zone usage (was 4/3; 2026-10-06: past TDs over-weighted vs
                               # red-zone usage -- TD log-loss 2024 .4084->.4044, 2025-26 .4022->.3974)
# Snap-based usage: expected snap share (short memory, follows role changes fast) x
# targets/carries per on-field play (steadier than raw share). Blended into the share
# priors with weights W_SNAP_*; 0 = off. Tuned in tune.py (phase "snap").
SNAP_HL, SNAP_CARRY = 1.5, 0.5
K_SNAP = 0.1                 # pseudo-games toward the position's newcomer snap share (light: a
                             # heavier pull drags every starter's expected snaps down)
K_RATE_TGT, K_RATE_CAR = 60, 40
W_SNAP_TGT, W_SNAP_CAR = 0.0, 0.0
REPL_SCALE = 0.5   # unproven players get half the early-career average share
ROOKIE_PICK = 64   # draft slots counted as early-round
ROOKIE_USAGE = 0.15  # target/carry share boost for early-round rookies (their rookie season).
                     # Adopted 2026-09-30: they ran +14-17% over projection; tuned on 2024, confirmed 2025-26.
ROOKIE_YPR = 0.0    # yards-per-catch boost for the same players
TEAM_CHANGE_CARRY = 0.7


def decayed_sums(df, by, cols, hl, carry, change_col=None, change_carry=1.0):
    """Decayed sums of `cols` over each group's *previous* rows. df must be time-sorted."""
    d = 0.5 ** (1.0 / hl)
    vals = df[cols].to_numpy(float)
    seasons = df["season"].to_numpy()
    chg = df[change_col].to_numpy() if change_col else None
    out = np.zeros_like(vals)
    for idx in df.groupby(by, sort=False).indices.values():
        S = np.zeros(vals.shape[1])
        prev_s = prev_c = None
        for i in idx:
            if prev_s is not None and seasons[i] != prev_s:
                S *= carry
            if chg is not None and prev_c is not None and chg[i] != prev_c:
                S *= change_carry
            out[i] = S
            S = d * S + np.nan_to_num(vals[i])
            prev_s = seasons[i]
            prev_c = chg[i] if chg is not None else None
    return pd.DataFrame(out, columns=[f"_s_{c}" for c in cols], index=df.index)


def shrunk(sums, num, den, k, base):
    return (sums[f"_s_{num}"] + k * base) / (sums[f"_s_{den}"] + k)


# (name, numerator, denominator, pseudocount) for team offense and defense-allowed.
TEAM_RATES = [
    ("plays_pg",   "plays",           "one",           4),
    ("neutral_pr", "neutral_dropbacks", "neutral_plays", 150),
    ("pass_rate",  "dropbacks",       "plays",         150),
    ("sack_rate",  "sacks",           "dropbacks",     200),
    ("scr_rate",   "scrambles",       "nonsack_db",    200),
    ("cmp_pct",    "cmp",             "att",           150),
    ("ypa",        "pass_yds",        "att",           150),
    ("ypc",        "designed_rush_yds", "designed_runs", 150),
    ("int_rate",   "ints",            "att",           400),
    ("td_pp",      "off_td",          "plays",         300),
    ("pass_td_frac", "pass_td",       "off_td",        12),
    ("epa_pp",     "epa_sum",         "plays",         200),
]


def _prep_team(tg):
    tg = tg.copy()
    tg["one"] = 1.0
    tg["nonsack_db"] = tg.dropbacks - tg.sacks
    tg["epa_sum"] = tg.epa * tg.plays
    return tg


def league_means(tg):
    tg = _prep_team(tg)
    return {name: tg[num].sum() / tg[den].sum() for name, num, den, _ in TEAM_RATES}


LEAGUE_HL = 256  # team-games (~8 weeks); tracks league-wide drift in pace/passing


def team_priors(tg, means):
    """Adds off_<rate> (team's offense), def_<rate> (opponent defense allowed) and
    lg_<rate> (rolling league level) columns."""
    tg = _prep_team(tg).sort_values(["gameday", "game_id", "team"]).reset_index(drop=True)
    cols = sorted({c for _, n, d, _ in TEAM_RATES for c in (n, d)})
    off = decayed_sums(tg, "team", cols, TEAM_HL, TEAM_CARRY)
    dfn = decayed_sums(tg, "opp", cols, TEAM_HL, TEAM_CARRY)  # what `opp` allowed before
    lg = decayed_sums(tg.assign(_all=0), "_all", cols, LEAGUE_HL, 1.0)
    # Same-day games must not see each other: use the league state at the day's first game.
    first = tg.groupby("gameday").cumcount() == 0
    lg = lg.where(first).groupby(tg.gameday).transform("first")
    for name, num, den, k in TEAM_RATES:
        tg[f"lg_{name}"] = shrunk(lg, num, den, 1, means[name])
        tg[f"off_{name}"] = shrunk(off, num, den, k, tg[f"lg_{name}"])
        tg[f"def_{name}"] = shrunk(dfn, num, den, k, tg[f"lg_{name}"])
    tg["team_games_prior"] = off["_s_one"]
    return tg


# ------------------------------------------------ defense vs. position

FUNNEL_POS = ("WR", "TE", "RB")
K_FUNNEL_ATT, K_FUNNEL_TGT = 150, 60


def defense_position_priors(pg, pos, tg):
    """How each defense distributes targets and yards by receiver position, as of kickoff.

    One row per (game_id, team) = the offense facing that defense. Columns:
      share_<P>:  defense's target share allowed to P / league share (1.0 = neutral)
      ypt_<P>:    defense's yards/target allowed to P relative to its yards/target
                  allowed overall, over the same ratio for the league. Relative on
                  purpose: overall pass defense is already in the team model.
    """
    x = pg.merge(pos[["player_id", "pos"]], on="player_id", how="left")
    x = x[x.pos.isin(FUNNEL_POS)]
    agg = x.pivot_table(index=["game_id", "team"], columns="pos", values=["tgt", "rec_yds"],
                        aggfunc="sum", fill_value=0)
    agg.columns = [f"{a}_{b}" for a, b in agg.columns]
    d = tg[["game_id", "team", "opp", "season", "gameday", "att"]].merge(
        agg.reset_index(), on=["game_id", "team"], how="left")
    d = d.sort_values(["gameday", "game_id", "team"]).reset_index(drop=True)
    cols = ["att"] + [f"tgt_{p}" for p in FUNNEL_POS] + [f"rec_yds_{p}" for p in FUNNEL_POS]
    for c in cols:
        d[c] = d[c].fillna(0) if c != "att" else d[c]
    s = decayed_sums(d, "opp", cols, TEAM_HL, TEAM_CARRY)

    done = d.att.notna()
    lg_att = d.loc[done, "att"].sum()
    lg_tgt = {p: d.loc[done, f"tgt_{p}"].sum() for p in FUNNEL_POS}
    lg_yds = {p: d.loc[done, f"rec_yds_{p}"].sum() for p in FUNNEL_POS}
    lg_ypt_all = sum(lg_yds.values()) / sum(lg_tgt.values())
    out = d[["game_id", "team"]].copy()
    s_tgt_all = sum(s[f"_s_tgt_{p}"] for p in FUNNEL_POS)
    s_yds_all = sum(s[f"_s_rec_yds_{p}"] for p in FUNNEL_POS)
    ypt_all = (s_yds_all + K_FUNNEL_TGT * lg_ypt_all) / (s_tgt_all + K_FUNNEL_TGT) / lg_ypt_all
    for p in FUNNEL_POS:
        lg_share, lg_ypt = lg_tgt[p] / lg_att, lg_yds[p] / lg_tgt[p]
        share = (s[f"_s_tgt_{p}"] + K_FUNNEL_ATT * lg_share) / (s["_s_att"] + K_FUNNEL_ATT)
        ypt = (s[f"_s_rec_yds_{p}"] + K_FUNNEL_TGT * lg_ypt) / (s[f"_s_tgt_{p}"] + K_FUNNEL_TGT)
        out[f"share_{p}"] = share / lg_share
        out[f"ypt_{p}"] = (ypt / lg_ypt) / ypt_all
    return out


# ---------------------------------------------------------------- players

PLAYER_COLS = ["tgt", "rec", "rec_yds", "rec_td", "rz_tgt", "designed_car", "designed_yds",
               "rush_td", "rz_car", "att", "cmp", "pass_yds", "pass_td", "ints", "sacks",
               "dropbacks", "scrambles", "scr_yds", "team_att", "team_designed_runs",
               "team_rz_tgt", "team_rz_car", "team_dropbacks", "team_pass_td", "team_rush_td",
               "air_yds", "snap", "has_snap", "onfield_att", "onfield_runs", "tgt_on", "car_on", "one"]


def player_frame(pg, actives, tg, pos):
    """All (game, team, player) rows where a player was active, with team denominators."""
    keys = ["game_id", "team", "player_id"]
    base = pd.concat([actives[keys], pg[keys]]).drop_duplicates()
    f = base.merge(pg.drop(columns=["season", "week"]), on=keys, how="left")
    stat_cols = [c for c in pg.columns if c not in keys + ["season", "week", "name"]]
    f[stat_cols] = f[stat_cols].fillna(0)

    team_rz = pg.groupby(["game_id", "team"])[["rz_tgt", "rz_car"]].sum() \
        .rename(columns={"rz_tgt": "team_rz_tgt", "rz_car": "team_rz_car"}).reset_index()
    t = tg[["game_id", "team", "season", "week", "gameday", "att", "designed_runs", "dropbacks",
            "pass_td", "rush_td"]] \
        .rename(columns={"att": "team_att", "designed_runs": "team_designed_runs",
                         "dropbacks": "team_dropbacks", "pass_td": "team_pass_td",
                         "rush_td": "team_rush_td"})
    f = f.merge(t, on=["game_id", "team"], how="inner").merge(team_rz, on=["game_id", "team"], how="left")
    f[["team_rz_tgt", "team_rz_car"]] = f[["team_rz_tgt", "team_rz_car"]].fillna(0)
    f = f.merge(pos[["player_id", "full_name", "pos"] + [c for c in ("rookie_year", "draft_number") if c in pos]],
                on="player_id", how="left")
    # Roster full names ("Bijan Robinson") match how books list players; pbp names
    # ("Bi.Robinson") are only the fallback.
    f["name"] = f["full_name"].fillna(f["name"])
    f["pos"] = f["pos"].fillna(_infer_pos(f))
    f["one"] = 1.0
    snaps = actives[keys + ["offense_pct"]].dropna().drop_duplicates(keys) \
        if "offense_pct" in actives else pd.DataFrame(columns=keys + ["offense_pct"])
    f = f.merge(snaps, on=keys, how="left")
    f["has_snap"] = (f.offense_pct.notna() & f.team_att.notna()).astype(float)
    f["snap"] = f.offense_pct.fillna(0) * f.has_snap
    f["onfield_att"] = f.team_att.fillna(0) * f.snap
    f["onfield_runs"] = f.team_designed_runs.fillna(0) * f.snap
    f["tgt_on"] = f.tgt * f.has_snap            # usage counted only where snaps are known
    f["car_on"] = f.designed_car * f.has_snap
    return f[f.pos.isin(["QB", "RB", "WR", "TE"])].sort_values(["gameday", "game_id", "team"]) \
        .reset_index(drop=True)


def _infer_pos(f):
    return pd.Series(np.select([f.att > 0, f.designed_car > f.tgt], ["QB", "RB"], "WR"), index=f.index)


def position_baselines(pf):
    """Position means for efficiency, and 'replacement' shares for thin samples."""
    g = pf.groupby("pos")
    base = pd.DataFrame({
        "catch_rate": g.rec.sum() / g.tgt.sum(),
        "ypr": g.rec_yds.sum() / g.rec.sum(),
        "ypc": g.designed_yds.sum() / g.designed_car.sum(),
        "adot": g.air_yds.sum() / g.tgt.sum(),
    })
    # Replacement share: median per-game share among players in their first 3 games.
    pf = pf.assign(n_prev=pf.groupby("player_id").cumcount())
    early = pf[pf.n_prev < 3]
    base["repl_tgt_share"] = REPL_SCALE * (early.tgt / early.team_att.clip(lower=1)).groupby(early.pos).mean()
    base["repl_car_share"] = REPL_SCALE * (early.designed_car / early.team_designed_runs.clip(lower=1)) \
        .groupby(early.pos).mean()
    sn = pf[pf.has_snap == 1]
    base["tgt_rate"] = sn.groupby("pos").tgt.sum() / sn.groupby("pos").onfield_att.sum()
    base["car_rate"] = sn.groupby("pos").designed_car.sum() / sn.groupby("pos").onfield_runs.sum()
    early_sn = early[early.has_snap == 1]
    base["repl_snap"] = early_sn.groupby("pos").snap.mean()
    base = base.fillna(0)
    qb = pf[pf.att > 0]
    qb_early = qb[qb.n_prev < 4]
    base.attrs["qb_league"] = _qb_rates(qb)
    base.attrs["qb_repl"] = _qb_rates(qb_early)
    base.attrs["scr_ypc"] = qb.scr_yds.sum() / max(qb.scrambles.sum(), 1)
    return base


def _qb_rates(q):
    return {
        "cmp_pct": q.cmp.sum() / q.att.sum(),
        "ypa": q.pass_yds.sum() / q.att.sum(),
        "int_rate": q.ints.sum() / q.att.sum(),
        "sack_rate": q.sacks.sum() / q.dropbacks.sum(),
        "scr_rate": q.scrambles.sum() / (q.dropbacks - q.sacks).sum(),
    }


def player_priors(pf, base):
    pf = pf.sort_values(["gameday", "game_id", "team"]).reset_index(drop=True)
    s = decayed_sums(pf, "player_id", PLAYER_COLS, PLAYER_HL, PLAYER_CARRY,
                     change_col="team", change_carry=TEAM_CHANGE_CARRY)
    b = base.reindex(pf.pos)
    b.index = pf.index
    pr = pf.copy()
    pr["p_games"] = s["_s_one"]
    pr["p_tgt_share"] = shrunk(s, "tgt", "team_att", K_TGT, b.repl_tgt_share)
    pr["p_car_share"] = shrunk(s, "designed_car", "team_designed_runs", K_CAR, b.repl_car_share)
    if W_SNAP_TGT or W_SNAP_CAR:
        s2 = decayed_sums(pf, "player_id", ["snap", "has_snap"], SNAP_HL, SNAP_CARRY,
                          change_col="team", change_carry=SNAP_CARRY)
        pr["p_snap"] = shrunk(s2, "snap", "has_snap", K_SNAP, b.repl_snap)
        pr["p_tgt_rate"] = shrunk(s, "tgt_on", "onfield_att", K_RATE_TGT, b.tgt_rate)
        pr["p_car_rate"] = shrunk(s, "car_on", "onfield_runs", K_RATE_CAR, b.car_rate)
        pr["p_tgt_share"] = (1 - W_SNAP_TGT) * pr.p_tgt_share + W_SNAP_TGT * pr.p_tgt_rate * pr.p_snap
        pr["p_car_share"] = (1 - W_SNAP_CAR) * pr.p_car_share + W_SNAP_CAR * pr.p_car_rate * pr.p_snap
    if (ROOKIE_USAGE or ROOKIE_YPR) and "rookie_year" in pr:
        # Early-round rookies out-earn their (replacement-level, slow-to-update) priors all season.
        rk = ((pr.season == pr.rookie_year) & (pr.draft_number <= ROOKIE_PICK)).astype(float)
        pr["p_tgt_share"] = pr.p_tgt_share * (1 + ROOKIE_USAGE * rk)
        pr["p_car_share"] = pr.p_car_share * (1 + ROOKIE_USAGE * rk)
    pr["p_rz_tgt_share"] = shrunk(s, "rz_tgt", "team_rz_tgt", 25, pr.p_tgt_share)
    pr["p_rz_car_share"] = shrunk(s, "rz_car", "team_rz_car", 25, pr.p_car_share)
    # Share of team TDs, shrunk toward red-zone usage. Captures goal-line roles
    # (QB sneaks, short-yardage backs) that carry/target shares miss.
    pr["p_rec_td_share"] = shrunk(s, "rec_td", "team_pass_td", K_REC_TD, pr.p_rz_tgt_share)
    pr["p_rush_td_share"] = shrunk(s, "rush_td", "team_rush_td", K_RUSH_TD, pr.p_rz_car_share)
    pr["p_catch_rate"] = shrunk(s, "rec", "tgt", K_CATCH, b.catch_rate)
    pr["p_ypr"] = shrunk(s, "rec_yds", "rec", K_YPR, b.ypr)
    if ROOKIE_YPR and "rookie_year" in pr:
        pr["p_ypr"] = pr.p_ypr * (1 + ROOKIE_YPR * rk)
    pr["p_ypc"] = shrunk(s, "designed_yds", "designed_car", K_YPC, b.ypc)
    pr["p_adot"] = shrunk(s, "air_yds", "tgt", 30, b.adot)
    lg, rp = base.attrs["qb_league"], base.attrs["qb_repl"]
    # QBs shrink toward replacement level, not league average.
    pr["q_cmp_pct"] = shrunk(s, "cmp", "att", 150, rp["cmp_pct"])
    pr["q_ypa"] = shrunk(s, "pass_yds", "att", 200, rp["ypa"])
    pr["q_int_rate"] = shrunk(s, "ints", "att", 400, rp["int_rate"])
    pr["q_sack_rate"] = shrunk(s, "sacks", "dropbacks", 200, rp["sack_rate"])
    pr["q_scr_rate"] = (s["_s_scrambles"] + 100 * rp["scr_rate"]) / \
        (s["_s_dropbacks"] - s["_s_sacks"] + 100)
    pr["q_scr_ypc"] = shrunk(s, "scr_yds", "scrambles", 30, base.attrs["scr_ypc"])
    pr["q_att_prior"] = s["_s_att"]
    return pr
