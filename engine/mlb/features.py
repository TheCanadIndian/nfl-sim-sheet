"""
Pitch-level and form features for the MLB home-run model (all walk-forward: only data before the game).

  barrel / hard-hit / pulled-air   batter (per PA) and pitcher-allowed (per batter faced) rates
  k_rate, bb_rate                  batter and pitcher
  velo                             pitcher's average fastball velocity
  form                             batter's last-14-day power contact vs his own season level (log ratio)
  mix                              pitch-mix matchup: batter's power per pitch by pitch class (hard / breaking /
                                   offspeed) weighted by this pitcher's usage vs that batter side, relative
                                   to a league-average mix (log ratio; 0 = neutral)
  heat                             location matchup: batter's power per pitch in each of 10 cells (3x3 zone in/
                                   mid/away x low/mid/high, scaled to his own zone, + chase) weighted by where
                                   this pitcher puts pitches vs that side, relative to league locations
"""

import numpy as np
import pandas as pd

CLASS = {"FF": "hard", "SI": "hard", "FC": "hard", "FA": "hard",
         "SL": "brk", "ST": "brk", "SV": "brk", "CU": "brk", "KC": "brk", "CS": "brk",
         "CH": "off", "FS": "off", "FO": "off", "SC": "off", "KN": "off", "EP": "off"}
CLASSES = ["hard", "brk", "off"]
CELLS = [f"{h}{v}" for h in ("in", "mid", "away") for v in ("lo", "md", "hi")] + ["chase"]
K_MIX, K_CELL, K_FORM, K_RATE = 300, 150, 40, 200
FORM_DAYS = 14
W_PREV = 0.6


def barrel(ev, la):
    ev, la = np.asarray(ev, float), np.asarray(la, float)
    lo = np.clip(26 - 2 * (ev - 98), 8, 26)
    hi = np.clip(30 + 2 * (ev - 98), 30, 50)
    return (ev >= 98) & (la >= lo) & (la <= hi)


def pulled_air(df):
    ang = np.degrees(np.arctan2(df.hx - 125.42, 198.27 - df.hy))          # <0 toward left field
    side = np.where(df.bat_side == "S", np.where(df.pitch_hand == "R", "L", "R"), df.bat_side)
    pull = np.where(side == "R", ang < -15, ang > 15)
    return (pull & (df.la >= 15) & df.hx.notna()).astype(int)


def eff_side(bat_side, pitch_hand):
    return np.where(bat_side == "S", np.where(pitch_hand == "R", "L", "R"), bat_side)


def _cum(df, key, cols, by_extra=()):
    """Season-to-date (before each date) + last season totals for `cols`, grouped by key (+extra)."""
    keys = [key, *by_extra]
    day = df.groupby(keys + ["season", "date"])[cols].sum().reset_index().sort_values(keys + ["date"])
    cum = day.groupby(keys + ["season"])[cols].cumsum() - day[cols]
    day[[c + "_cur" for c in cols]] = cum.to_numpy()
    tot = df.groupby(keys + ["season"])[cols].sum().reset_index()
    tot["season"] += 1
    day = day.merge(tot.rename(columns={c: c + "_prev" for c in cols}), on=keys + ["season"], how="left").fillna(0)
    for c in cols:
        day[c + "_n"] = day[c + "_cur"] + W_PREV * day[c + "_prev"]
    return day[keys + ["season", "date"] + [c + "_n" for c in cols]]


def logit(p):
    p = np.clip(p, 1e-5, 1 - 1e-5)
    return np.log(p / (1 - p))


def pa_rates(pa):
    """Barrel / hard-hit / pulled-air / K / BB for batters and pitchers (pkey) as of each date."""
    d = pa.copy()
    d["one"] = 1
    d["brl"] = barrel(d.ev, d.la).astype(int)
    d["hh"] = (d.ev >= 95).astype(int)
    d["pull"] = pulled_air(d)
    d["k"] = d.event.isin(["strikeout", "strikeout_double_play"]).astype(int)
    d["bb"] = d.event.isin(["walk", "hit_by_pitch"]).astype(int)
    cols = ["one", "brl", "hh", "pull", "k", "bb"]
    lg = d.groupby("season")[cols].sum()
    out = {}
    for who, key in (("b", "batter"), ("p", "pkey")):
        c = _cum(d, key, cols)
        for m in cols[1:]:
            rate = (lg[m] / lg["one"])
            base = c.season.map(rate).fillna(rate.mean())
            c[f"{who}_{m}"] = (c[f"{m}_n"] + K_RATE * base) / (c["one_n"] + K_RATE)
        out[who] = c[[key, "season", "date"] + [f"{who}_{m}" for m in cols[1:]]]
    return out


def form(pa):
    """Batter's last FORM_DAYS of power contact per PA vs his season-to-date rate (log ratio, shrunk)."""
    d = pa[["batter", "season", "date", "pc"]].assign(one=1)
    day = d.groupby(["batter", "season", "date"])[["one", "pc"]].sum().reset_index()
    day["dt"] = pd.to_datetime(day.date)
    rows = []
    for (b, s), x in day.groupby(["batter", "season"], sort=False):
        x = x.sort_values("dt")
        t = x.dt.to_numpy(); c1 = np.cumsum(x.one.to_numpy()); cp = np.cumsum(x.pc.to_numpy())
        lo = np.searchsorted(t, t - np.timedelta64(FORM_DAYS, "D"), side="left")
        # window strictly before this date: (lo .. i-1)
        prev1 = np.concatenate([[0], c1[:-1]]); prevp = np.concatenate([[0], cp[:-1]])
        base1 = np.where(lo > 0, c1[lo - 1], 0); basep = np.where(lo > 0, cp[lo - 1], 0)
        r1, rp = prev1 - base1, prevp - basep
        season_rate = np.where(prev1 > 0, (prevp + 2) / (prev1 + 40), np.nan)
        recent = (rp + K_FORM * np.nan_to_num(season_rate, nan=.05)) / (r1 + K_FORM)
        rows.append(pd.DataFrame(dict(batter=b, season=s, date=x.date.to_numpy(),
                                      form=np.where(np.isnan(season_rate), 0, np.log(recent / season_rate)), form_n=r1)))
    return pd.concat(rows, ignore_index=True)


def cell_of(p):
    """Location cell from the catcher's view: in/mid/away by batter side, lo/md/hi scaled to his zone; else chase."""
    side = eff_side(p.bat_side, p.pitch_hand)
    zn = (p.pz - p.sz_bot) / (p.sz_top - p.sz_bot)
    inside = np.where(side == "R", -p.px, p.px)            # RHB stands on the 3B side (negative px)
    inz = (p.px.abs() <= 0.83) & (zn >= 0) & (zn <= 1)
    h = np.select([inside > 0.28, inside < -0.28], ["in", "away"], "mid")
    v = np.select([zn < 1 / 3, zn > 2 / 3], ["lo", "hi"], "md")
    out = np.where(inz, pd.Series(h).str.cat(pd.Series(v)).to_numpy(), "chase")
    return np.where(p.px.isna() | p.pz.isna(), None, out)


def matchup(pitch, pa):
    """mix and heat features per PA (batter x pitcher/bullpen x side), both log ratios vs league mix/locations."""
    p = pitch.copy()
    p["cls"] = p.ptype.map(CLASS)
    p["cell"] = cell_of(p)
    p["side"] = eff_side(p.bat_side, p.pitch_hand)
    p["pow"] = ((p.inplay == 1) & (p.ev >= 95) & p.la.between(20, 40)).astype(int)
    p["one"] = 1
    starters = pa[["game_pk", "pitcher", "sp", "fld_team"]].drop_duplicates(["game_pk", "pitcher"])
    p = p.merge(starters, on=["game_pk", "pitcher"], how="left")
    p["pkey"] = np.where(p.sp == 1, "P" + p.pitcher.astype(str), "BP" + p.fld_team.astype(str))
    feats = {}
    for name, col, levels, K in (("mix", "cls", CLASSES, K_MIX), ("heat", "cell", CELLS, K_CELL)):
        q = p[p[col].notna()]
        lg_rate = q.groupby(col).pow.sum() / q.groupby(col).one.sum()                       # power per pitch, league
        lg_use = q.groupby(["side", col]).one.sum() / q.groupby("side").one.sum()          # league usage by side
        # batter power per pitch by level (to date), shrunk to league level rate
        bt = q.pivot_table(index=["batter", "season", "date"], columns=col, values=["pow", "one"], aggfunc="sum", fill_value=0)
        bt.columns = [f"{a}_{b}" for a, b in bt.columns]
        bt = bt.reset_index()
        bc = _cum(bt.assign(), "batter", [c for c in bt.columns if c.startswith(("pow_", "one_"))])
        for L in levels:
            bc[f"r_{L}"] = (bc.get(f"pow_{L}_n", 0) + K * lg_rate.get(L, 0)) / (bc.get(f"one_{L}_n", 0) + K)
        # pitcher usage by side (to date), shrunk to league usage
        pt = q.pivot_table(index=["pkey", "side", "season", "date"], columns=col, values="one", aggfunc="sum", fill_value=0)
        pt.columns = [f"u_{c}" for c in pt.columns]
        pt = pt.reset_index()
        pc = _cum(pt, "pkey", [c for c in pt.columns if c.startswith("u_")], by_extra=("side",))
        tot = sum(pc.get(f"u_{L}_n", 0) for L in levels)
        for L in levels:
            pc[f"s_{L}"] = (pc.get(f"u_{L}_n", 0) + 200 * pc.side.map(lambda s, L=L: lg_use.get((s, L), 0))) / (tot + 200)
        feats[name] = (bc[["batter", "season", "date"] + [f"r_{L}" for L in levels]],
                       pc[["pkey", "side", "season", "date"] + [f"s_{L}" for L in levels]], lg_use, levels)
    return feats


def attach(df, pitch):
    """Add barrel/hard-hit/pull/K/BB, velocity, form, mix and heat features to the PA frame from model.features()."""
    r = pa_rates(df)
    df = df.merge(r["b"], on=["batter", "season", "date"], how="left").merge(r["p"], on=["pkey", "season", "date"], how="left")
    df = df.merge(form(df), on=["batter", "season", "date"], how="left")
    df["side"] = eff_side(df.bat_side, df.pitch_hand)
    feats = matchup(pitch, df)
    for name, (bc, pc, lg_use, levels) in feats.items():
        x = df[["batter", "pkey", "side", "season", "date"]].merge(bc, on=["batter", "season", "date"], how="left") \
            .merge(pc, on=["pkey", "side", "season", "date"], how="left")
        num = sum(x[f"s_{L}"].fillna(x.side.map(lambda s, L=L: lg_use.get((s, L), 0))) * x[f"r_{L}"] for L in levels)
        den = sum(x.side.map(lambda s, L=L: lg_use.get((s, L), 0)) * x[f"r_{L}"] for L in levels)
        df[name] = np.log((num / den).fillna(1.0).to_numpy())
    # pitcher fastball velocity to date (starters; bullpen pooled)
    fb = pitch[pitch.ptype.isin(["FF", "SI"])].merge(df[["game_pk", "pitcher", "pkey"]].drop_duplicates(["game_pk", "pitcher"]), on=["game_pk", "pitcher"])
    v = fb.assign(vs=fb.speed.fillna(0), vn=fb.speed.notna().astype(int))
    vc = _cum(v, "pkey", ["vs", "vn"])
    vc["velo"] = ((vc.vs_n + 30 * 94.0) / (vc.vn_n + 30) - 94.0) / 2
    df = df.merge(vc[["pkey", "season", "date", "velo"]], on=["pkey", "season", "date"], how="left")
    for c in ("b_brl", "b_hh", "b_pull", "b_k", "b_bb", "p_brl", "p_hh", "p_pull", "p_k", "p_bb"):
        df["l_" + c] = logit(df[c].fillna(df[c].median()))
    df["form"] = df.form.fillna(0); df["velo"] = df.velo.fillna(0)
    return df
