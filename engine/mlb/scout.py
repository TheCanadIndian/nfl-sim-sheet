"""Builds the page's scouting payload (hitters, starters, zones, rolling, league percentiles) for one slate."""
import numpy as np
import pandas as pd

import stats as S

HIT_METRICS = ["iso", "xwoba", "xwobacon", "swstr", "pullbrl", "brl_bip", "sweet", "hardhit", "ev90", "fb"]
PIT_METRICS = ["xwoba", "csw", "swstr", "brl_bip", "fb", "hardhit"]


def build(con, date, meta, now, coef_pred, hist):
    season = int(date[:4])
    pa = pd.read_sql("SELECT p.*, g.date, g.season FROM pa p JOIN games g USING(game_pk) WHERE g.season >= ?", con, params=(season - 2,))
    tables = S.xwoba_table(pa)
    pa = S.enrich(S.add_xval(pa[pa.season >= season - 1], tables))
    cur = pa[(pa.season == season) & (pa.date < date)]
    pit = pd.read_sql("""SELECT t.*, g.date FROM pitch t JOIN games g USING(game_pk) WHERE g.season = ? AND g.date < ?""", con, params=(season, date))
    pit["side"] = np.where(pit.bat_side == "S", np.where(pit.pitch_hand == "R", "L", "R"), pit.bat_side)

    # league reference distributions (qualified hitters / pitchers) for percentile coloring
    pb = pit.groupby("batter"); pp = pit.groupby("pitcher")
    ref_h, ref_p = {m: [] for m in HIT_METRICS}, {m: [] for m in PIT_METRICS}
    for b, x in cur.groupby("batter"):
        if len(x) >= 150:
            L = S.line(x, pb.get_group(b) if b in pb.groups else None)
            for m in HIT_METRICS:
                if L.get(m) is not None: ref_h[m].append(L[m])
    for p_, x in cur.groupby("pitcher"):
        if len(x) >= 200:
            L = S.line(x, pp.get_group(p_) if p_ in pp.groups else None)
            for m in PIT_METRICS:
                if L.get(m) is not None: ref_p[m].append(L[m])
    pctl = lambda v, ref: None if v is None or not ref else round(float((np.asarray(ref) < v).mean() * 100))

    # matchup score reference: per-PA HR chances across this season's PAs
    sample = hist[hist.season == season]
    ref_pa = np.sort(coef_pred(sample.sample(min(len(sample), 60000), random_state=1)))
    form = S.form_pct(cur, [m["batter"] for m in meta], date)
    prev = pa[(pa.season == season - 1)]
    def data_n(col, ids):
        """Weighted sample: this season's PAs (to date) + 0.6 x last season's -- what the model's shrinkage sees."""
        a = cur.groupby(col).size(); b = prev.groupby(col).size()
        return {i: int(a.get(i, 0) + .6 * b.get(i, 0)) for i in ids}
    hn = data_n("batter", [m["batter"] for m in meta]); pn = data_n("pitcher", [m["sp_id"] for m in meta if m.get("sp_id")])
    light = lambda n: "green" if n >= 300 else "yellow" if n >= 120 else "red"

    hitters = {}
    for m in meta:
        b = m["batter"]
        x = cur[cur.batter == b]; p = pit[pit.batter == b]
        L = S.line(x, p)
        vs = cur[(cur.batter == b) & (cur.pitch_hand == m["sp_hand"])]
        Lh = S.line(vs, p[p.pitch_hand == m["sp_hand"]])
        r = now[(now.game_pk == m["game_pk"]) & (now.batter == b) & (now.sp == 1)]
        p_sp = float(r.p.iloc[0]) if len(r) else None
        roll = {}
        for days in (7, 14, 30):
            lo = (pd.Timestamp(date) - pd.Timedelta(days=days)).date().isoformat()
            roll[str(days)] = S.line(x[x.date >= lo], p[p.date >= lo])
        hitters[str(b)] = dict(
            season=L, vs_hand=Lh, pct={k: pctl(L.get(k), ref_h[k]) for k in HIT_METRICS},
            matchup=None if p_sp is None else round(float(np.searchsorted(ref_pa, p_sp) / len(ref_pa) * 100)),
            zone_fit=None if r.empty else round(float(np.exp(r.heat.iloc[0]) - 1), 3),
            mix_fit=None if r.empty else round(float(np.exp(r.mix.iloc[0]) - 1), 3),
            form=form.get(b), rolling=roll, zones=S.hitter_zones(p, x), swing=S.swing_plane(p), data_n=hn.get(b, 0), light=light(hn.get(b, 0)))
    pitchers = {}
    for sid in {m["sp_id"] for m in meta if m.get("sp_id")}:
        x = cur[cur.pitcher == sid]; p = pit[pit.pitcher == sid]
        splits = {"All": S.line(x, p), "vs RHH": S.line(x[x.bat_side.isin(["R"]) | ((x.bat_side == "S") & (x.pitch_hand == "L"))], p[p.side == "R"]),
                  "vs LHH": S.line(x[x.bat_side.isin(["L"]) | ((x.bat_side == "S") & (x.pitch_hand == "R"))], p[p.side == "L"])}
        pitchers[str(sid)] = dict(splits=splits, pct={k: {m: pctl(v.get(m), ref_p[m]) for m in PIT_METRICS} for k, v in splits.items()},
                                  arsenal=S.arsenal(p, x) if len(p) else [], counts=S.count_usage(p) if len(p) else {},
                                  zones=S.pitcher_zones(p) if len(p) else {}, starts=int(x[x.sp == 1].game_pk.nunique()),
                                  data_n=pn.get(sid, 0), light=light(pn.get(sid, 0)))
    return dict(hitters=hitters, pitchers=pitchers)
