"""
Defense vs. position, depth-chart labels, and player/defense mismatch flags.

Everything is as of kickoff for each game (only earlier games count), so the
same code serves live projections and backtests.

Defense vs. position: what each defense allowed per game to QBs, RBs, WRs,
TEs, and (2025+, from depth-chart alignment) slot vs. outside WRs. Two views:
  raw_*   this season, per game, unadjusted (what fans quote)
  dvp_*   recency-weighted across seasons and shrunk toward league average by
          K_GAMES pseudo-games; dvr_* = dvp / league average (1.0 = average).
          Mismatch flags use this view so two fluky games don't trigger them.

Mismatch flags are context for reading the projections, not extra inputs: the
model already uses each defense's pass/run efficiency and target funnels.
"""

import numpy as np
import pandas as pd

from . import priors as P

GROUPS = ["QB", "RB", "WR", "TE"]
ALIGN = {1: "X", 2: "Z", 8: "Slot"}           # ESPN depth-chart WR slots
K_GAMES = 4.0                                  # shrinkage for per-game allowed stats
K_DEEP = 40.0                                  # deep attempts
STAT_COLS = ["att", "cmp", "pass_yds", "pass_td", "ints", "car", "rush_yds", "rush_td",
             "tgt", "rec", "rec_yds", "rec_td", "ppr"]
ALIGN_COLS = ["tgt", "rec", "rec_yds", "rec_td", "ppr"]
MIN_ROLE = {"tgt_share": 0.10, "rb_car_share": 0.30, "rb_tgt_share": 0.08}
FAV, STRONG = 1.12, 1.20                       # ratio thresholds for flags


def ppr(d):
    return (0.04 * d.pass_yds + 4 * d.pass_td - 2 * d.ints + 0.1 * (d.rush_yds + d.rec_yds)
            + 6 * (d.rush_td + d.rec_td) + d.rec)


# ------------------------------------------------------------ depth charts

def depth_labels(con, team_games):
    """Depth-chart role for every player as of each game: latest snapshot before game day.
    team_games: DataFrame(game_id, team, gameday). Returns game_id, team, player_id,
    depth_pos, depth_rank, align, label (e.g. 'WR1 · X', 'RB2', 'TE1')."""
    d = pd.read_sql("""SELECT team, gsis_id AS player_id, pos_abb, pos_slot, pos_rank, dt
                       FROM depth_charts WHERE pos_abb IN ('QB','RB','WR','TE','FB')
                         AND gsis_id IS NOT NULL""", con)
    if d.empty:
        return pd.DataFrame(columns=["game_id", "team", "player_id", "label"])
    d["dt"] = pd.to_datetime(d.dt, utc=True).dt.tz_localize(None)
    snaps = d[["team", "dt"]].drop_duplicates().sort_values("dt")
    tg = team_games[["game_id", "team", "gameday"]].copy()
    tg["gameday"] = pd.to_datetime(tg.gameday)
    tg = tg.sort_values("gameday")
    pick = pd.merge_asof(tg, snaps, left_on="gameday", right_on="dt", by="team",
                         direction="backward", allow_exact_matches=False)
    pick = pick.dropna(subset=["dt"])
    # Ignore snapshots more than 21 days stale (offseason gaps).
    pick = pick[(pick.gameday - pick.dt) <= pd.Timedelta(days=21)]
    out = pick.merge(d, on=["team", "dt"])
    out = out.sort_values("pos_rank").drop_duplicates(["game_id", "team", "player_id"])
    out["depth_pos"] = out.pos_abb
    out["depth_rank"] = out.groupby(["game_id", "team", "pos_abb"]).pos_rank.rank(method="first").astype(int)
    out["align"] = np.where(out.pos_abb == "WR", out.pos_slot.map(ALIGN), None)
    out["label"] = np.where(out.pos_abb == "FB", "FB",
                            out.pos_abb + out.depth_rank.astype(str)
                            + np.where(out["align"].notna(), " · " + out["align"].fillna(""), ""))
    return out[["game_id", "team", "player_id", "depth_pos", "depth_rank", "align", "label"]]


# --------------------------------------------------------- defense vs pos

def _position_game_stats(fr):
    pg = fr.pg.merge(fr.pos[["player_id", "pos"]], on="player_id", how="left")
    pg["pos"] = pg.pos.fillna(pd.Series(np.select([pg.att > 0, pg.designed_car > pg.tgt],
                                                  ["QB", "RB"], "WR"), index=pg.index))
    pg = pg[pg.pos.isin(GROUPS)].copy()
    pg["ppr"] = ppr(pg)
    w = pg.pivot_table(index=["game_id", "team"], columns="pos", values=STAT_COLS,
                       aggfunc="sum", fill_value=0)
    w.columns = [f"{p}_{c}" for c, p in w.columns]
    return w.reset_index(), pg


def _alignment_game_stats(fr, labels, pg):
    wr = pg[pg.pos == "WR"].merge(labels[["game_id", "team", "player_id", "align"]],
                                  on=["game_id", "team", "player_id"], how="inner")
    wr = wr[wr["align"].notna()]
    wr["grp"] = np.where(wr["align"] == "Slot", "SLOT", "OUT")
    w = wr.pivot_table(index=["game_id", "team"], columns="grp", values=ALIGN_COLS,
                       aggfunc="sum", fill_value=0)
    w.columns = [f"{g}_{c}" for c, g in w.columns]
    w = w.reset_index()
    w["has_align"] = 1.0
    return w


def _deep_game_stats(fr):
    p = fr.plays
    deep = p[(p.is_att == 1) & (p.air_yards >= 20)]
    return deep.groupby(["game_id", "posteam"]).agg(
        deep_att=("is_att", "sum"), deep_cmp=("complete_pass", "sum"), deep_yds=("pass_yds", "sum"),
    ).reset_index().rename(columns={"posteam": "team"})


def defense_frame(fr, labels, extra_rows=None):
    """One row per (game_id, team=offense) with the opposing defense's as-of numbers.
    extra_rows: optional DataFrame(game_id, team, opp, season, gameday) of pseudo-games
    (e.g. 'now' for every defense) to evaluate the latest state."""
    base = fr.tp[["game_id", "team", "opp", "season", "week", "gameday", "plays", "sacks",
                  "dropbacks"]].copy()
    if extra_rows is not None:
        base = pd.concat([base, extra_rows], ignore_index=True)
    stats, pg = _position_game_stats(fr)
    al = _alignment_game_stats(fr, labels, pg)
    deep = _deep_game_stats(fr)
    d = base.merge(stats, on=["game_id", "team"], how="left") \
        .merge(al, on=["game_id", "team"], how="left") \
        .merge(deep, on=["game_id", "team"], how="left")
    played = d.plays.notna()
    cols = [c for c in d.columns if c.split("_", 1)[0] in (*GROUPS, "SLOT", "OUT")] \
        + ["deep_att", "deep_cmp", "deep_yds", "sacks", "dropbacks", "has_align"]
    d[cols] = d[cols].fillna(0)          # unplayed rows add nothing: their 'one' is 0
    d["one"] = played.astype(float)
    d = d.sort_values(["gameday", "game_id", "team"]).reset_index(drop=True)

    s = P.decayed_sums(d, "opp", cols + ["one"], P.TEAM_HL, P.TEAM_CARRY)
    lg_rows = d[played]
    base_cols = d[["game_id", "team", "opp", "season", "gameday"]]
    new = {}
    grp_cols = [c for c in cols if c.split("_", 1)[0] in GROUPS]
    for c in grp_cols:
        lg = lg_rows[c].mean()
        new[f"dvp_{c}"] = (s[f"_s_{c}"] + K_GAMES * lg) / (s["_s_one"] + K_GAMES)
        new[f"dvr_{c}"] = new[f"dvp_{c}"] / lg if lg else pd.Series(1.0, index=d.index)
    al_rows = lg_rows[lg_rows.has_align == 1]
    for c in [c for c in cols if c.startswith(("SLOT_", "OUT_"))]:
        lg = al_rows[c].mean()
        new[f"dvp_{c}"] = (s[f"_s_{c}"] + K_GAMES * lg) / (s["_s_has_align"] + K_GAMES)
        new[f"dvr_{c}"] = new[f"dvp_{c}"] / lg if lg else pd.Series(1.0, index=d.index)
    lg_deep = lg_rows.deep_yds.sum() / lg_rows.deep_att.sum()
    new["dvp_deep_ypa"] = (s["_s_deep_yds"] + K_DEEP * lg_deep) / (s["_s_deep_att"] + K_DEEP)
    new["dvr_deep_ypa"] = new["dvp_deep_ypa"] / lg_deep
    lg_sack = lg_rows.sacks.sum() / lg_rows.dropbacks.sum()
    new["dvp_sack_rate"] = (s["_s_sacks"] + 150 * lg_sack) / (s["_s_dropbacks"] + 150)
    new["dvr_sack_rate"] = new["dvp_sack_rate"] / lg_sack

    # This season, raw per game (for display).
    g = d.groupby(["opp", "season"])
    games = g.one.cumsum() - d.one
    new["raw_games"] = games
    for c in grp_cols + ["deep_att", "deep_yds", "sacks"]:
        prev = g[c].cumsum() - d[c]
        new[f"raw_{c}"] = pd.Series(np.where(games > 0, prev / games.clip(lower=1), np.nan), index=d.index)
    # slot / outside splits: per game with alignment data, this season
    ag = g.has_align.cumsum() - d.has_align
    new["raw_align_games"] = ag
    for c in [c for c in cols if c.startswith(("SLOT_", "OUT_"))]:
        prev = g[c].cumsum() - d[c]
        new[f"raw_{c}"] = pd.Series(np.where(ag > 0, prev / ag.clip(lower=1), np.nan), index=d.index)
    out = pd.concat([base_cols, pd.DataFrame(new, index=d.index)], axis=1)
    return out


def defense_now(fr, labels, season, when, teams):
    """Every defense's current numbers (after all games played so far)."""
    extra = pd.DataFrame({"game_id": [f"NOW_{t}" for t in teams], "team": "NOW", "opp": list(teams),
                          "season": season, "gameday": pd.Timestamp(when)})
    d = defense_frame(fr, labels, extra)
    now = d[d.team == "NOW"].drop(columns=["game_id", "team"]).rename(columns={"opp": "defense"})
    return now.reset_index(drop=True)


# ------------------------------------------------------------- mismatches

def _tag(kind, ratio, text_fn, short="", invert=False):
    """Return (strength, direction, text) or None. direction +1 favorable for the offense."""
    if ratio is None or not np.isfinite(ratio):
        return None
    r = 1 / ratio if invert else ratio
    if r >= FAV:
        return dict(kind=kind, dir=1, strength=round(float(r - 1), 3), strong=bool(r >= STRONG),
                    text=text_fn(ratio), short=f"{short} {pct(ratio)}")
    if r <= 1 / FAV:
        return dict(kind=kind, dir=-1, strength=round(float(1 - r), 3), strong=bool(r <= 1 / STRONG),
                    text=text_fn(ratio), short=f"{short} {pct(ratio)}")
    return None


def pct(r):
    return f"{(r - 1) * 100:+.0f}%"


def player_context(fr, con, keys):
    """Depth label + mismatch tags for players.
    keys: DataFrame(game_id, team, player_id). Returns one row per key with columns
    label, align, depth_rank, tags (list of dicts)."""
    tg = fr.tp[["game_id", "team", "gameday"]]
    labels = depth_labels(con, tg[tg.game_id.isin(keys.game_id)])
    dv = defense_frame(fr, depth_labels(con, tg)).set_index(["game_id", "team"])
    team = fr.df.set_index(["game_id", "team"])
    pp = fr.pp.set_index(["game_id", "team", "player_id"])
    starters = set(map(tuple, fr.starters[["game_id", "team", "player_id"]].to_numpy()))
    lab = labels.set_index(["game_id", "team", "player_id"])
    lg_adot = fr.base.adot.to_dict()

    rows = []
    for k in keys.itertuples(index=False):
        key3 = (k.game_id, k.team, k.player_id)
        L = lab.loc[key3] if key3 in lab.index else None
        row = dict(game_id=k.game_id, team=k.team, player_id=k.player_id,
                   label=None if L is None else L["label"],
                   align=L["align"] if L is not None and isinstance(L["align"], str) else None,
                   depth_rank=None if L is None else int(L["depth_rank"]), tags=[])
        if key3 not in pp.index or (k.game_id, k.team) not in dv.index:
            rows.append(row)
            continue
        p = pp.loc[key3]
        if isinstance(p, pd.DataFrame):
            p = p.iloc[0]
        D = dv.loc[(k.game_id, k.team)]
        T = team.loc[(k.game_id, k.team)] if (k.game_id, k.team) in team.index else None
        opp = D["opp"]
        pos = p["pos"]
        tags = []
        if pos == "QB" and key3 in starters:
            tags.append(_tag("pos", D["dvr_QB_ppr"], lambda r: f"QBs vs {opp}: {pct(r)} fantasy pts allowed", "vs QBs"))
            if T is not None and np.isfinite(p.get("q_sack_rate", np.nan)):
                qb_hi = p["q_sack_rate"] - T["lg_sack_rate"] >= 0.01
                if qb_hi and D["dvr_sack_rate"] >= FAV:
                    tags.append(dict(kind="pressure", dir=-1, strength=round(float(D["dvr_sack_rate"] - 1), 3),
                                     strong=bool(D["dvr_sack_rate"] >= STRONG),
                                     text=f"Sack-prone QB vs {opp} pass rush ({pct(D['dvr_sack_rate'])} sack rate)",
                                     short=f"pass rush {pct(D['dvr_sack_rate'])}"))
        elif pos in ("WR", "TE") and p["p_tgt_share"] >= MIN_ROLE["tgt_share"]:
            grp = pos
            if pos == "WR" and row["align"] is not None:
                grp = "SLOT" if row["align"] == "Slot" else "OUT"
            name = {"WR": "WRs", "TE": "TEs", "SLOT": "slot WRs", "OUT": "outside WRs"}[grp]
            tags.append(_tag("pos", D[f"dvr_{grp}_ppr"],
                             lambda r, n=name: f"{n} vs {opp}: {pct(r)} fantasy pts allowed", f"vs {name}"))
            if p["p_adot"] >= max(12.5, 1.15 * lg_adot.get(pos, 10)):
                tags.append(_tag("deep", D["dvr_deep_ypa"],
                                 lambda r: f"Deep threat ({p['p_adot']:.1f} aDOT) vs {opp} deep passing: {pct(r)} yds/att", "deep D"))
        elif pos == "RB":
            if p["p_car_share"] >= MIN_ROLE["rb_car_share"] and T is not None:
                r = T["def_ypc"] / T["lg_ypc"]
                t = _tag("run", r, lambda r: f"Run game vs {opp}: {pct(r)} yds/carry allowed", "run D")
                if t:  # ypc ratios are tighter than fantasy-point ratios
                    tags.append(t)
                else:
                    small = FAV ** 0.5
                    if r >= small or r <= 1 / small:
                        tags.append(dict(kind="run", dir=1 if r > 1 else -1, strength=round(abs(r - 1), 3),
                                         strong=False, text=f"Run game vs {opp}: {pct(r)} yds/carry allowed",
                                         short=f"run D {pct(r)}"))
                tags.append(_tag("pos", D["dvr_RB_ppr"], lambda r: f"RBs vs {opp}: {pct(r)} fantasy pts allowed", "vs RBs"))
            if p["p_tgt_share"] >= MIN_ROLE["rb_tgt_share"]:
                tags.append(_tag("rbrec", D["dvr_RB_tgt"], lambda r: f"Pass-catching back vs {opp}: {pct(r)} RB targets allowed", "RB targets"))
        row["tags"] = sorted([t for t in tags if t], key=lambda t: -t["strength"])
        rows.append(row)
    return pd.DataFrame(rows)


# ------------------------------------------------------------- red zone

RZ_LINE, GOAL_LINE = 20, 5
RZ_POS = ["QB", "RB", "WR", "TE"]
K_RZ_GAMES = 4.0


def _rz_plays(fr, seasons, before):
    """Plays from `seasons` before date `before`, tagged with the player's position."""
    p = fr.plays.merge(fr.games[["game_id", "gameday"]], on="game_id")
    p = p[p.season.isin(seasons) & (p.gameday < pd.Timestamp(before))].copy()
    pos = fr.pos.set_index("player_id").pos
    p["tgt_pos"] = p.receiver_player_id.map(pos)
    p["run_pos"] = p.rusher_player_id.map(pos)
    p["rz"] = p.yardline_100 <= RZ_LINE
    return p


def redzone_defense(fr, seasons, before, faces):
    """Per defense: red-zone trips and TD rate allowed, and by position the red-zone
    targets, carries and TDs allowed (this season, before `before`)."""
    p = _rz_plays(fr, seasons, before)
    if p.empty:
        return None
    games = p.groupby("defteam").game_id.nunique()
    rz = p[p.rz]
    trips = rz.groupby(["defteam", "game_id", "posteam", "drive"]).size().reset_index()[["defteam", "game_id", "posteam", "drive"]]
    td_drives = p[(p.pass_touchdown == 1) | (p.rush_touchdown == 1)][["game_id", "posteam", "drive"]].drop_duplicates()
    trips = trips.merge(td_drives.assign(td=1), on=["game_id", "posteam", "drive"], how="left").fillna({"td": 0})
    tr = trips.groupby("defteam").agg(trips=("td", "size"), td_trips=("td", "sum"))

    tgt = rz[(rz.is_att == 1) & rz.receiver_player_id.notna()]
    car = rz[rz.play_type == "run"]
    rec_td = rz[rz.pass_touchdown == 1]
    rush_td = rz[rz.rush_touchdown == 1]
    by = {}
    for name, df, col in (("tgt", tgt, "tgt_pos"), ("car", car, "run_pos"),
                          ("rtd", rec_td, "tgt_pos"), ("utd", rush_td, "run_pos")):
        by[name] = df.groupby(["defteam", col]).size().unstack(fill_value=0).reindex(columns=RZ_POS, fill_value=0)
    td = by["rtd"].add(by["utd"], fill_value=0)

    total_games = games.sum()
    lg_td = td.sum() / total_games                                    # league RZ TDs per game by position
    lg_share = td.sum() / td.sum().sum()
    rows = []
    for d in games.index:
        g = int(games[d])
        t = td.loc[d] if d in td.index else pd.Series(0, index=RZ_POS)
        adj = (t + K_RZ_GAMES * lg_td) / (g + K_RZ_GAMES) / lg_td
        tot = float(t.sum())
        rows.append(dict(
            team=d, faces=faces.get(d), games=g,
            trips=round(float(tr.trips.get(d, 0)) / g, 2),
            td_rate=round(float(tr.td_trips.get(d, 0)) / max(float(tr.trips.get(d, 0)), 1), 3),
            pos={ps: dict(
                tgt=round(float(by["tgt"].loc[d, ps]) / g if d in by["tgt"].index else 0, 2),
                car=round(float(by["car"].loc[d, ps]) / g if d in by["car"].index else 0, 2),
                td=int(t[ps]), share=round(float(t[ps]) / tot, 3) if tot else None,
                ratio=round(float(adj[ps]), 3)) for ps in RZ_POS},
        ))
    for ps in RZ_POS:                                                  # rank 1 = gives up the most
        for i, r in enumerate(sorted(rows, key=lambda r: -r["pos"][ps]["ratio"])):
            r["pos"][ps]["rank"] = i + 1
    return dict(rows=rows, league={ps: dict(td_per_game=round(float(lg_td[ps]), 3),
                                            share=round(float(lg_share[ps]), 3)) for ps in RZ_POS})


def redzone_usage(fr, keyrows, seasons, before):
    """Per player (keyed like the page): overall and red-zone usage over `seasons`,
    games before `before`. Shares are of the team's totals in the games he played."""
    p = fr.plays.merge(fr.games[["game_id", "gameday"]], on="game_id")
    p = p[p.season.isin(seasons) & (p.gameday < pd.Timestamp(before))].copy()
    p["rz"] = p.yardline_100 <= RZ_LINE
    tg = p[(p.is_att == 1) & p.receiver_player_id.notna()]
    ru = p[p.play_type == "run"]
    parts = [
        tg.groupby(["game_id", "posteam", "receiver_player_id"]).agg(
            tgt=("is_att", "size"), rz_tgt=("rz", "sum"), rec_td=("pass_touchdown", "sum")),
        ru.groupby(["game_id", "posteam", "rusher_player_id"]).agg(
            car=("play_type", "size"), rz_car=("rz", "sum"), gl_car=("yardline_100", lambda s: int((s <= GOAL_LINE).sum())),
            rush_td=("rush_touchdown", "sum")),
    ]
    for x in parts:
        x.index = x.index.set_names(["game_id", "team", "player_id"])
    rz_td_rec = tg[tg.rz & (tg.pass_touchdown == 1)].groupby(["game_id", "posteam", "receiver_player_id"]).size()
    rz_td_run = ru[ru.rz & (ru.rush_touchdown == 1)].groupby(["game_id", "posteam", "rusher_player_id"]).size()
    for s in (rz_td_rec, rz_td_run):
        s.index = s.index.set_names(["game_id", "team", "player_id"])
    pl = pd.concat(parts, axis=1).fillna(0)
    pl["rz_td"] = rz_td_rec.reindex(pl.index).fillna(0) + rz_td_run.reindex(pl.index).fillna(0)
    pl = pl.reset_index()
    team = pl.groupby(["game_id", "team"])[["tgt", "rz_tgt", "car", "rz_car"]].sum() \
        .add_prefix("team_").reset_index()
    pl = pl.merge(team, on=["game_id", "team"])
    agg = pl.groupby("player_id").agg(games=("game_id", "nunique"), tgt=("tgt", "sum"), rz_tgt=("rz_tgt", "sum"),
                                      car=("car", "sum"), rz_car=("rz_car", "sum"), gl_car=("gl_car", "sum"),
                                      td=("rec_td", "sum"), rush_td=("rush_td", "sum"), rz_td=("rz_td", "sum"),
                                      team_tgt=("team_tgt", "sum"), team_rz_tgt=("team_rz_tgt", "sum"),
                                      team_rz_car=("team_rz_car", "sum"))
    out = {}
    for g, team_, pid, name in keyrows:
        if pid not in agg.index:
            continue
        a = agg.loc[pid]
        n = int(a.games)
        out[f"{g}|{team_}|{name}"] = dict(
            games=n, tgt_pg=round(a.tgt / n, 2), tgt_share=round(a.tgt / a.team_tgt, 3) if a.team_tgt else None,
            rz_tgt=int(a.rz_tgt), rz_tgt_share=round(a.rz_tgt / a.team_rz_tgt, 3) if a.team_rz_tgt else None,
            rz_car=int(a.rz_car), rz_car_share=round(a.rz_car / a.team_rz_car, 3) if a.team_rz_car else None,
            gl_car=int(a.gl_car), rz_td=int(a.rz_td), td=int(a.td + a.rush_td),
            rz_opp_pg=round((a.rz_tgt + a.rz_car) / n, 2))
    return out


# -------------------------------------------------------------- summary

DVP_VIEWS = {
    "QB": dict(label="Quarterbacks", cols=[("QB_pass_yds", "Pass yds"), ("QB_pass_td", "Pass TD"),
                                           ("QB_ints", "INT"), ("QB_rush_yds", "Rush yds")],
               ratio="QB_ppr"),
    "RB": dict(label="Running backs", cols=[("RB_car", "Carries"), ("RB_rush_yds", "Rush yds"),
                                            ("RB_tgt", "Targets"), ("RB_rec_yds", "Rec yds"),
                                            ("RB_rush_td", "Rush TD")], ratio="RB_ppr"),
    "WR": dict(label="Wide receivers", cols=[("WR_tgt", "Targets"), ("WR_rec", "Rec"),
                                             ("WR_rec_yds", "Rec yds"), ("WR_rec_td", "Rec TD")],
               ratio="WR_ppr"),
    "SLOT": dict(label="Slot WRs", cols=[("SLOT_tgt", "Targets"), ("SLOT_rec", "Rec"),
                                         ("SLOT_rec_yds", "Rec yds"), ("SLOT_rec_td", "Rec TD")],
                 ratio="SLOT_ppr", adjusted_only=True),
    "OUT": dict(label="Outside WRs", cols=[("OUT_tgt", "Targets"), ("OUT_rec", "Rec"),
                                           ("OUT_rec_yds", "Rec yds"), ("OUT_rec_td", "Rec TD")],
                ratio="OUT_ppr", adjusted_only=True),
    "TE": dict(label="Tight ends", cols=[("TE_tgt", "Targets"), ("TE_rec", "Rec"),
                                         ("TE_rec_yds", "Rec yds"), ("TE_rec_td", "Rec TD")],
               ratio="TE_ppr"),
}


def dvp_summary(now, opponents):
    """JSON-ready defense-vs-position tables. opponents: {defense: offense it faces this week}."""
    out = {}
    for key, v in DVP_VIEWS.items():
        rows = []
        for r in now.itertuples():
            d = r._asdict()
            ratio = d[f"dvr_{v['ratio']}"]
            rows.append(dict(
                team=r.defense, faces=opponents.get(r.defense), games=int(d["raw_games"]),
                ratio=round(float(ratio), 3), ppr_adj=round(float(d[f"dvp_{v['ratio']}"]), 1),
                ppr_raw=None if v.get("adjusted_only") or pd.isna(d.get(f"raw_{v['ratio']}")) else round(float(d[f"raw_{v['ratio']}"]), 1),
                stats=[None if pd.isna(d.get(f"raw_{c}")) else round(float(d[f"raw_{c}"]), 1) for c, _ in v["cols"]]
                if not v.get("adjusted_only") else
                [round(float(d[f"dvp_{c}"]), 1) for c, _ in v["cols"]],
            ))
        # This season only, raw per game: its own rank (1 = allows the most) and the stats behind it.
        n_col = "raw_align_games" if v.get("adjusted_only") else "raw_games"
        for x, r in zip(rows, now.itertuples()):
            d = r._asdict()
            pts = d.get(f"raw_{v['ratio']}")
            x["season_games"] = int(d.get(n_col) or 0)
            x["season_ppr"] = None if pts is None or pd.isna(pts) else round(float(pts), 1)
            x["season_stats"] = [None if pd.isna(d.get(f"raw_{c}")) else round(float(d[f"raw_{c}"]), 1) for c, _ in v["cols"]]
        have = [x for x in rows if x["season_ppr"] is not None and x["season_games"] > 0]
        lg = np.mean([x["season_ppr"] for x in have]) if have else None
        for i, x in enumerate(sorted(have, key=lambda x: -x["season_ppr"])):
            x["season_rank"] = i + 1
            x["season_ratio"] = round(x["season_ppr"] / lg, 3) if lg else None
        rows.sort(key=lambda x: -x["ratio"])
        for i, x in enumerate(rows):
            x["rank"] = i + 1          # 1 = allows the most (softest)
        out[key] = dict(label=v["label"], cols=[n for _, n in v["cols"]], rows=rows,
                        adjusted_only=bool(v.get("adjusted_only")))
    return out
