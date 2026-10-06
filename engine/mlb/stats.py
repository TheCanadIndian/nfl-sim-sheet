"""
Scouting stats for the MLB page, from mlb.db (season to date, regular season + postseason):

hitters   PA, pitches seen, BIP, ISO, xwOBA, xwOBAcon, SwStr%, PullBrl% (pulled barrels / BIP), Brl/BIP%,
          Sweet% (launch angle 8-32 / BIP), HardHit% (95+ mph / BIP), EV90 (90th-pct exit velo), HR,
          HR form (last-14-day power percentile + trend), rolling 7/14/30-day windows, damage zones (5x5)
pitchers  splits All / vs RHH / vs LHH: xwOBA, CSW%, SwStr%, Brl/BIP%, FB%, HardHit%; arsenal (usage, velo,
          movement, whiff%, xwOBAcon by pitch); count usage; location zones by batter side (5x5)

xwOBA: each batted ball gets the league-average wOBA value of balls hit at its exit velocity / launch angle
(2 mph x 4 degree cells, 2024-26), walks / HBP / strikeouts at their wOBA weights.
"""

import numpy as np
import pandas as pd

W = dict(walk=.69, hit_by_pitch=.72, single=.88, double=1.25, triple=1.58, home_run=2.03)
WHIFF = {"S", "W", "M", "Q"}
CALLED = {"C"}
CLASS = {"FF": "Four-seam", "SI": "Sinker", "FC": "Cutter", "SL": "Slider", "ST": "Sweeper", "SV": "Slurve", "CU": "Curveball",
         "KC": "Knuckle curve", "CS": "Slow curve", "CH": "Changeup", "FS": "Splitter", "FO": "Forkball", "SC": "Screwball", "KN": "Knuckleball"}
XB = [-1.33, -0.83, -0.28, 0.28, 0.83, 1.33]          # plate x edges (ft, catcher's view), 3 in-zone columns + edges
ZB = [-0.33, 0, 1 / 3, 2 / 3, 1, 1.33]                  # height in units of the batter's zone


def xwoba_table(pa):
    b = pa[pa.ev.notna() & pa.la.notna()].copy()
    b["val"] = b.event.map(W).fillna(0.0)
    b["evb"] = (b.ev // 2).clip(20, 60); b["lab"] = (b.la // 4).clip(-20, 20)
    t = b.groupby(["evb", "lab"]).val.agg(["mean", "size"])
    coarse = b.assign(evc=b.evb // 3, lac=b.lab // 3).groupby(["evc", "lac"]).val.mean()
    return t, coarse


def add_xval(pa, tables):
    t, coarse = tables
    d = pa.copy()
    evb = (d.ev // 2).clip(20, 60); lab = (d.la // 4).clip(-20, 20)
    m = t["mean"].to_dict(); n = t["size"].to_dict(); c = coarse.to_dict()
    xs = []
    for e, l, ev in zip(evb, lab, d.ev):
        if np.isnan(ev):
            xs.append(np.nan); continue
        k = (e, l)
        xs.append(m[k] if n.get(k, 0) >= 30 else c.get((e // 3, l // 3), np.nan))
    d["xcon"] = xs                                            # expected wOBA value of the batted ball
    d["xval"] = np.where(d.ev.notna(), d.xcon, d.event.map({"walk": .69, "hit_by_pitch": .72}).fillna(0.0))
    return d


def _barrel(ev, la):
    lo = np.clip(26 - 2 * (ev - 98), 8, 26); hi = np.clip(30 + 2 * (ev - 98), 30, 50)
    return (ev >= 98) & (la >= lo) & (la <= hi)


def _pulled(d):
    ang = np.degrees(np.arctan2(d.hx - 125.42, 198.27 - d.hy))
    side = np.where(d.bat_side == "S", np.where(d.pitch_hand == "R", "L", "R"), d.bat_side)
    return np.where(side == "R", ang < -15, ang > 15) & d.hx.notna()


def enrich(pa):
    d = pa.copy()
    bip = d.ev.notna() & d.la.notna()
    d["bip"] = bip.astype(int)
    d["brl"] = (bip & _barrel(d.ev.fillna(0), d.la.fillna(0))).astype(int)
    d["pullbrl"] = (d.brl.astype(bool) & _pulled(d)).astype(int)
    d["sweet"] = (bip & d.la.between(8, 32)).astype(int)
    d["hh"] = (bip & (d.ev >= 95)).astype(int)
    d["fb"] = (d.traj == "fly_ball").astype(int)
    d["ab"] = (~d.event.isin(["walk", "hit_by_pitch", "intent_walk", "sac_fly", "sac_bunt", "catcher_interf", "sac_fly_double_play"])).astype(int)
    d["tb"] = d.event.map({"single": 1, "double": 2, "triple": 3, "home_run": 4}).fillna(0)
    d["hit"] = d.event.isin(["single", "double", "triple", "home_run"]).astype(int)
    d["k"] = d.event.isin(["strikeout", "strikeout_double_play"]).astype(int)
    return d


def line(d, p=None):
    """Batting line for a slice of PAs (d) and their pitches (p)."""
    n = len(d); bip = d.bip.sum(); ab = d.ab.sum()
    out = dict(pa=int(n), bip=int(bip), hr=int(d.hr.sum()),
               iso=round(float((d.tb.sum() - d.hit.sum()) / ab), 3) if ab else None,
               xwoba=round(float(d.xval.sum() / n), 3) if n else None,
               xwobacon=round(float(d.xcon.sum() / bip), 3) if bip else None,
               brl_bip=round(float(d.brl.sum() / bip), 3) if bip else None,
               pullbrl=round(float(d.pullbrl.sum() / bip), 3) if bip else None,
               sweet=round(float(d.sweet.sum() / bip), 3) if bip else None,
               hardhit=round(float(d.hh.sum() / bip), 3) if bip else None,
               fb=round(float(d.fb.sum() / bip), 3) if bip else None,
               ev90=round(float(np.nanpercentile(d.ev.dropna(), 90)), 1) if bip >= 10 else None,
               k=round(float(d.k.sum() / n), 3) if n else None)
    if p is not None:
        np_ = len(p)
        out.update(pit=int(np_), swstr=round(float(p.code.isin(WHIFF).mean()), 3) if np_ else None,
                   csw=round(float((p.code.isin(WHIFF) | p.code.isin(CALLED)).mean()), 3) if np_ else None)
    return out


def zone_cells(p):
    """5x5 cell index (row 0 = top) from the catcher's view; None outside the grid."""
    zn = (p.pz - p.sz_bot) / (p.sz_top - p.sz_bot)
    c = np.digitize(p.px, XB) - 1
    r = 4 - (np.digitize(zn, ZB) - 1)
    ok = (c >= 0) & (c <= 4) & (r >= 0) & (r <= 4)
    return np.where(ok, r * 5 + c, -1)


def hitter_zones(p, pa):
    """Per cell: pitches seen, swing%, whiff% (of swings), xwOBAcon on contact, HR count."""
    q = p.copy(); q["cell"] = zone_cells(q)
    q = q[q.cell >= 0]
    con = q[q.inplay == 1].merge(pa[["game_pk", "idx", "xcon", "hr"]], on=["game_pk", "idx"], how="left")
    out = []
    for c in range(25):
        x = q[q.cell == c]; y = con[con.cell == c]; sw = x.swing.sum()
        out.append(dict(n=int(len(x)), swing=round(float(sw / len(x)), 3) if len(x) else None,
                        whiff=round(float(x.code.isin(WHIFF).sum() / sw), 3) if sw else None,
                        xcon=round(float(y.xcon.mean()), 3) if len(y) >= 3 else None, hr=int(y.hr.sum())))
    return out


def pitcher_zones(p):
    out = {}
    for side in ("R", "L"):
        q = p[p.side == side].copy(); q["cell"] = zone_cells(q); n = len(q)
        out[side] = [round(float((q.cell == c).sum() / n), 4) if n else 0 for c in range(25)]
    return out


def arsenal(p, pa):
    con = p[p.inplay == 1].merge(pa[["game_pk", "idx", "xcon"]], on=["game_pk", "idx"], how="left")
    n = len(p); rows = []
    for t, x in p.groupby("ptype"):
        if len(x) < max(10, .02 * n):
            continue
        sw = x.swing.sum(); y = con[con.ptype == t]
        rows.append(dict(pitch=CLASS.get(t, t), code=t, usage=round(len(x) / n, 3),
                         vs_r=round(float((x.side == "R").sum() / max((p.side == "R").sum(), 1)), 3),
                         vs_l=round(float((x.side == "L").sum() / max((p.side == "L").sum(), 1)), 3),
                         velo=round(float(x.speed.mean()), 1), hbreak=round(float(x.pfx_x.mean()), 1), vbreak=round(float(x.pfx_z.mean()), 1),
                         whiff=round(float(x.code.isin(WHIFF).sum() / sw), 3) if sw else None,
                         xwobacon=round(float(y.xcon.mean()), 3) if len(y) >= 5 else None))
    return sorted(rows, key=lambda r: -r["usage"])


def count_usage(p):
    st = np.select([p.strikes == 2, p.balls > p.strikes, p.strikes > p.balls], ["two_strikes", "behind", "ahead"], "even")
    q = p.assign(state=st)
    t = q.groupby(["state", "ptype"]).size().unstack(fill_value=0)
    t = t.div(t.sum(axis=1), axis=0)
    keep = [c for c in t.columns if t[c].max() >= .03]
    return {s: {CLASS.get(c, c): round(float(t.loc[s, c]), 3) for c in keep} for s in t.index}


def form_pct(pa_all, batters, date, days=14):
    """Last-`days` power contact (barrels + hard air per PA) percentile among hitters with 25+ PA, + trend vs prior window."""
    d = pa_all.copy(); d["dt"] = pd.to_datetime(d.date); t = pd.Timestamp(date)
    pw = lambda lo, hi: d[(d.dt >= lo) & (d.dt < hi)].groupby("batter").agg(pa=("hr", "size"), pw=("brl", "sum"), hh=("hh", "sum"))
    cur = pw(t - pd.Timedelta(days=days), t); prev = pw(t - pd.Timedelta(days=2 * days), t - pd.Timedelta(days=days))
    cur["rate"] = (cur.pw + .5 * cur.hh) / cur.pa; prev["rate"] = (prev.pw + .5 * prev.hh) / prev.pa
    ref = cur[cur.pa >= 25].rate
    out = {}
    for b in batters:
        if b in cur.index and cur.loc[b, "pa"] >= 8:
            r = cur.loc[b, "rate"]; pr = prev.rate.get(b, np.nan)
            out[b] = dict(pct=round(float((ref < r).mean() * 100)), trend=None if np.isnan(pr) else ("up" if r > pr * 1.1 else "down" if r < pr * .9 else "flat"),
                          pa=int(cur.loc[b, "pa"]))
    return out


def percentiles(values, ref):
    ref = np.asarray([v for v in ref if v is not None and not np.isnan(v)])
    return None if values is None or not len(ref) else round(float((ref < values).mean() * 100))
