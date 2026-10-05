"""
Turn nflverse play-by-play into per-game team and player box scores.

Stat definitions follow the official box score (what props settle on):
  - pass attempts exclude sacks and scrambles; passing yards are gross
  - scrambles count as QB rushes
  - two-point tries and spikes are excluded; kneels are excluded from team
    plays/efficiency but added back to player rushing lines (official stats
    count them as carries)
"""

import sqlite3

import numpy as np
import pandas as pd

SKILL_POS = ("QB", "RB", "WR", "TE")
POS_MAP = {"FB": "RB", "HB": "RB"}


def connect(db="nfl.db"):
    return sqlite3.connect(db)


def load_games(con):
    g = pd.read_sql("SELECT * FROM games WHERE game_type IN ('REG','WC','DIV','CON','SB')", con)
    g["gameday"] = pd.to_datetime(g["gameday"])
    return g.sort_values(["gameday", "game_id"]).reset_index(drop=True)


def load_plays(con):
    q = """
        SELECT game_id, season, week, posteam, defteam, qtr, down, ydstogo,
               yardline_100, wp, play_type, qb_dropback, qb_scramble, sack,
               complete_pass, yards_gained, pass_touchdown, rush_touchdown,
               interception, fumble_lost, fumbled_1_player_id, fumbled_1_team,
               passer_player_id, passer_player_name, rusher_player_id,
               rusher_player_name, receiver_player_id, receiver_player_name,
               air_yards, epa, drive
        FROM plays
        WHERE play_type IN ('pass','run')
          AND COALESCE(two_point_attempt,0) = 0
          AND posteam IS NOT NULL
    """
    p = pd.read_sql(q, con)
    for c in ["qb_dropback", "qb_scramble", "sack", "complete_pass", "pass_touchdown",
              "rush_touchdown", "interception", "fumble_lost"]:
        p[c] = p[c].fillna(0).astype(int)
    p["yards_gained"] = p["yards_gained"].fillna(0)
    p["is_att"] = ((p.play_type == "pass") & (p.sack == 0)).astype(int)
    p["is_scramble"] = ((p.play_type == "run") & (p.qb_scramble == 1)).astype(int)
    p["is_designed_run"] = ((p.play_type == "run") & (p.qb_scramble == 0)).astype(int)
    p["is_dropback"] = ((p.is_att == 1) | (p.sack == 1) | (p.is_scramble == 1)).astype(int)
    p["pass_yds"] = np.where((p.is_att == 1) & (p.complete_pass == 1), p.yards_gained, 0)
    p["sack_yds"] = np.where(p.sack == 1, p.yards_gained, 0)
    p["rush_yds"] = np.where(p.play_type == "run", p.yards_gained, 0)
    p["rz"] = (p.yardline_100 <= 10).astype(int)
    p["neutral"] = ((p.qtr <= 3) & p.wp.between(0.2, 0.8)).astype(int)
    return p


def team_games(plays, games):
    """One row per (game, offense). Also carries defense-allowed via opponent join."""
    p = plays
    agg = p.groupby(["game_id", "posteam", "defteam"]).agg(
        plays=("is_dropback", "size"),
        dropbacks=("is_dropback", "sum"),
        sacks=("sack", "sum"),
        sack_yds=("sack_yds", "sum"),
        scrambles=("is_scramble", "sum"),
        att=("is_att", "sum"),
        cmp=("complete_pass", "sum"),
        pass_yds=("pass_yds", "sum"),
        pass_td=("pass_touchdown", "sum"),
        ints=("interception", "sum"),
        designed_runs=("is_designed_run", "sum"),
        rush_yds=("rush_yds", "sum"),
        rush_td=("rush_touchdown", "sum"),
        fum_lost=("fumble_lost", "sum"),
        epa=("epa", "mean"),
        neutral_plays=("neutral", "sum"),
    ).reset_index().rename(columns={"posteam": "team", "defteam": "opp"})
    agg["rush_att"] = agg.designed_runs + agg.scrambles
    agg["scramble_yds"] = p[p.is_scramble == 1].groupby(["game_id", "posteam"])["yards_gained"].sum() \
        .reindex(pd.MultiIndex.from_frame(agg[["game_id", "team"]])).fillna(0).values
    agg["designed_rush_yds"] = agg.rush_yds - agg.scramble_yds
    nd = p[p.neutral == 1].groupby(["game_id", "posteam"])["is_dropback"].sum()
    agg["neutral_dropbacks"] = nd.reindex(pd.MultiIndex.from_frame(agg[["game_id", "team"]])).fillna(0).values

    g = games[["game_id", "season", "week", "gameday", "home_team", "away_team", "home_score",
               "away_score", "spread_line", "total_line"]]
    agg = agg.merge(g, on="game_id", how="inner")
    home = agg.team == agg.home_team
    agg["is_home"] = home.astype(int)
    agg["points"] = np.where(home, agg.home_score, agg.away_score)
    agg["opp_points"] = np.where(home, agg.away_score, agg.home_score)
    # spread_line is home-minus-away expected margin; convert to this team's view.
    agg["spread"] = np.where(home, agg.spread_line, -agg.spread_line)
    agg["implied"] = agg.total_line / 2 + agg.spread / 2
    agg["off_td"] = agg.pass_td + agg.rush_td
    agg = agg.drop(columns=["home_team", "away_team", "home_score", "away_score"])
    return agg.sort_values(["gameday", "game_id", "team"]).reset_index(drop=True)


def add_field_goals(tg, con):
    fg = pd.read_sql("""SELECT game_id, posteam AS team, SUM(field_goal_result='made') AS fg
                        FROM plays WHERE play_type='field_goal' GROUP BY 1,2""", con)
    tg = tg.merge(fg, on=["game_id", "team"], how="left")
    tg["fg"] = tg.fg.fillna(0).astype(int)
    return tg


def load_kneels(con):
    k = pd.read_sql("""SELECT game_id, posteam AS team, rusher_player_id AS player_id,
                              COUNT(*) AS kneels, SUM(yards_gained) AS kneel_yds
                       FROM plays WHERE play_type='qb_kneel' AND rusher_player_id IS NOT NULL
                       GROUP BY 1,2,3""", con)
    return k


def player_games(plays, kneels=None):
    """One row per (game, team, player) with official-style box score stats."""
    p = plays
    keys = ["game_id", "season", "week", "posteam"]
    pas = p[(p.is_dropback == 1) & p.passer_player_id.notna()].groupby(keys + ["passer_player_id"]).agg(
        att=("is_att", "sum"), cmp=("complete_pass", "sum"), pass_yds=("pass_yds", "sum"),
        pass_td=("pass_touchdown", "sum"), ints=("interception", "sum"), sacks=("sack", "sum"),
        dropbacks=("is_dropback", "sum"),
    ).reset_index().rename(columns={"passer_player_id": "player_id"})
    # Scramble dropbacks have no passer id; credit them to the rusher (the QB).
    scr = p[p.is_scramble == 1].groupby(keys + ["rusher_player_id"]).size().rename("scr_db").reset_index() \
        .rename(columns={"rusher_player_id": "player_id"})
    run = p[p.play_type == "run"].assign(rz_designed=lambda d: d.rz * d.is_designed_run)
    rus = run.groupby(keys + ["rusher_player_id"]).agg(
        car=("rush_yds", "size"), rush_yds=("rush_yds", "sum"), rush_td=("rush_touchdown", "sum"),
        designed_car=("is_designed_run", "sum"), scrambles=("is_scramble", "sum"),
        rz_car=("rz_designed", "sum"),
    ).reset_index().rename(columns={"rusher_player_id": "player_id"})
    tgt = p[(p.is_att == 1) & p.receiver_player_id.notna()]
    rec = tgt.groupby(keys + ["receiver_player_id"]).agg(
        tgt=("complete_pass", "size"), rec=("complete_pass", "sum"), rec_yds=("pass_yds", "sum"),
        rec_td=("pass_touchdown", "sum"), rz_tgt=("rz", "sum"),
        air_yds=("air_yards", "sum"),   # for depth of target (aDOT)
    ).reset_index().rename(columns={"receiver_player_id": "player_id"})

    out = pas
    for df in (scr, rus, rec):
        out = out.merge(df, on=keys + ["player_id"], how="outer")
    num = [c for c in out.columns if c not in keys + ["player_id"]]
    out[num] = out[num].fillna(0)
    out["dropbacks"] = out.dropbacks + out.scr_db
    out = out.drop(columns="scr_db").rename(columns={"posteam": "team"})
    scr_yds = p[p.is_scramble == 1].groupby(["game_id", "rusher_player_id"])["yards_gained"].sum()
    out["scr_yds"] = scr_yds.reindex(pd.MultiIndex.from_frame(out[["game_id", "player_id"]])).fillna(0).values
    out["designed_yds"] = out.rush_yds - out.scr_yds

    # Official carries/yards include kneels; model inputs (designed_*) do not.
    out["kneels"] = 0
    if kneels is not None and len(kneels):
        kk = kneels.set_index(["game_id", "player_id"])
        idx = pd.MultiIndex.from_frame(out[["game_id", "player_id"]])
        out["kneels"] = kk.kneels.reindex(idx).fillna(0).values
        out["car"] = out.car + out.kneels
        out["rush_yds"] = out.rush_yds + kk.kneel_yds.reindex(idx).fillna(0).values

    names = pd.concat([
        p[["passer_player_id", "passer_player_name"]].set_axis(["player_id", "name"], axis=1),
        p[["rusher_player_id", "rusher_player_name"]].set_axis(["player_id", "name"], axis=1),
        p[["receiver_player_id", "receiver_player_name"]].set_axis(["player_id", "name"], axis=1),
    ]).dropna().drop_duplicates("player_id", keep="last")
    return out.merge(names, on="player_id", how="left")


def positions(con):
    r = pd.read_sql("""SELECT gsis_id AS player_id, full_name, position, pfr_id, season, week,
                              rookie_year, entry_year, draft_number
                       FROM rosters WHERE gsis_id IS NOT NULL""", con)
    for c in ("rookie_year", "entry_year", "draft_number"):
        r[c] = pd.to_numeric(r[c], errors="coerce")
    # draft facts don't change: take them from any roster row
    draft = r.groupby("player_id").agg(rookie_year=("rookie_year", "min"), entry_year=("entry_year", "min"),
                                       draft_number=("draft_number", "min"))
    r = r.sort_values(["season", "week"]).drop_duplicates("player_id", keep="last")
    r["pos"] = r.position.replace(POS_MAP)
    r = r[["player_id", "full_name", "pos", "pfr_id"]].merge(draft.reset_index(), on="player_id", how="left")
    r["rookie_year"] = r.rookie_year.fillna(r.entry_year)
    return r.drop(columns="entry_year")


def game_actives(con, pos):
    """Players with >=1 offensive snap per game, keyed by gsis id."""
    s = pd.read_sql("""SELECT game_id, team, pfr_player_id AS pfr_id, offense_snaps, offense_pct
                       FROM snaps WHERE offense_snaps > 0""", con)
    s = s.merge(pos[["player_id", "pfr_id"]].dropna(), on="pfr_id", how="inner")
    return s[["game_id", "team", "player_id", "offense_snaps", "offense_pct"]]
