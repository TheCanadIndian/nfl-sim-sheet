#!/usr/bin/env python3
"""
Today's MLB home-run sheet.

    python mlb/project.py [--date 2026-10-06]

For each game on the date: lineups (posted, else each team's most recent batting order), probable
starters, park, weather (game-day weather from the feed once posted; roof games = 70F, no wind).
Each batter's HR chance per PA vs the starter and vs the bullpen comes from the fitted model
(features as of today, same code as the backtest), then
    P(1+ HR) = sum over his PA count k (by lineup slot, home/away) of P(k) x
               mean over the starter's batters faced of prod_j (1 - p_j)
with the j-th PA against the starter while the team's PA number (slot + 9(j-1)) <= that starter's
batters faced. Writes mlb/projections/<date>.json/.csv (+ index.html via page.py).

The "why" breakdown: each feature's push on the log-odds relative to an average batter / pitcher /
park, grouped as power, pitcher, matchup (handedness + pitch mix + locations), form, park & weather,
and opportunity (expected PAs vs an average lineup spot), shown as % change in HR chance.
"""

import argparse
import datetime as dt
import json
import os
import sqlite3
import sys

import numpy as np
import pandas as pd
import requests

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import model as M          # noqa: E402

OUT = os.path.join(HERE, "projections")
API = "https://statsapi.mlb.com/api"
H = {"User-Agent": "Mozilla/5.0 (personal sports projections)"}
GROUPS = {"power": ["l_bhr", "l_bpc", "l_bfb", "l_b_brl", "l_b_hh", "l_b_pull", "l_b_k", "l_b_bb"],
          "pitcher": ["l_phr", "l_ppc", "l_p_brl", "l_p_hh", "l_p_pull", "l_p_k", "l_p_bb", "velo"],
          "matchup": ["same", "mix", "heat"], "form": ["form"], "park": ["l_park", "temp", "wind_out"], "post": ["post"]}


def get(url, **p):
    try:
        r = requests.get(url, params=p, headers=H, timeout=30)
        return r.json() if r.ok else None
    except requests.RequestException:
        return None


def games_on(date):
    d = get(f"{API}/v1/schedule", sportId=1, date=date, hydrate="probablePitcher,venue,lineups,team")
    out = []
    for day in (d or {}).get("dates", []):
        for g in day["games"]:
            if g["status"].get("detailedState") in ("Postponed", "Cancelled"):
                continue
            out.append(g)                       # finished / live games stay on the sheet, frozen at first pitch
    return out


def last_lineup(con, team, before):
    """Most recent batting order for a team (starters = first batter seen in each slot)."""
    gp = con.execute("SELECT game_pk FROM games WHERE (home=? OR away=?) AND date<? ORDER BY date DESC, game_pk DESC LIMIT 1",
                     (team, team, before)).fetchone()
    if not gp:
        return []
    rows = con.execute("SELECT slot, batter, bat_side FROM pa WHERE game_pk=? AND bat_team=? AND slot IS NOT NULL ORDER BY idx",
                       (gp[0], team)).fetchall()
    seen, order = set(), []
    for slot, b, side in rows:
        if slot not in seen:
            seen.add(slot); order.append((slot, b, side))
    return sorted(order)


def roster_hitters(team_id, date):
    """Active-roster position players (two-way players included) from the stats API."""
    d = get(f"{API}/v1/teams/{team_id}/roster", rosterType="active", date=date) or {}
    return [(r["person"]["id"], r["person"]["fullName"]) for r in d.get("roster", [])
            if (r.get("position") or {}).get("type") != "Pitcher" or (r.get("position") or {}).get("abbreviation") == "TWP"]


def start_odds(con, team, before, sp_hand, n=14):
    """Chance each hitter starts, from the team's last n games: his start share, leaning toward games vs the
    same starter handedness (platoons), plus his usual lineup spot."""
    gs = [r[0] for r in con.execute("SELECT game_pk FROM games WHERE (home=? OR away=?) AND date<? ORDER BY date DESC, game_pk DESC LIMIT ?",
                                    (team, team, before, n))]
    if not gs:
        return {}
    q = f"""SELECT p.game_pk, p.slot, p.batter, p.idx FROM pa p WHERE p.game_pk IN ({','.join(map(str, gs))}) AND p.bat_team=? AND p.slot IS NOT NULL"""
    pa = pd.read_sql(q, con, params=(team,))
    st = pa.sort_values("idx").drop_duplicates(["game_pk", "slot"])            # first batter in each slot = the starter
    hand = pd.read_sql(f"""SELECT game_pk, pitch_hand FROM pa WHERE game_pk IN ({','.join(map(str, gs))}) AND fld_team!=? AND sp=1
                           GROUP BY game_pk""", con, params=(team,)).set_index("game_pk").pitch_hand.to_dict()
    st["vs"] = st.game_pk.map(hand)
    ago = {g: i for i, g in enumerate(gs)}                       # 0 = most recent game
    w = {g: .5 ** (i / 3) for g, i in ago.items()}               # recency: half weight every 3 games back
    tot_w = sum(w.values()); same = [g for g in gs if hand.get(g) == sp_hand]; same_w = sum(w[g] for g in same)
    out = {}
    for b, x in st.groupby("batter"):
        all_rate = (sum(w[g] for g in x.game_pk) + .1) / (tot_w + .2)
        hs = [g for g in x.game_pk if hand.get(g) == sp_hand]
        hand_rate = (sum(w[g] for g in hs) + .1) / (same_w + .2) if same else all_rate
        k = min(len(same), 6) / 6 * .5                           # lean on same-hand games when there are enough
        streak = all(g in set(x.game_pk) for g in gs[:5])         # started each of the last 5: an everyday player
        pr = k * hand_rate + (1 - k) * all_rate
        out[b] = dict(p=min(.98, max(pr, .95) if streak else pr), slot=int(x.sort_values("game_pk", key=lambda c: c.map(ago)).slot.iloc[:5].median()))
    return out


def feed_weather(pk):
    f = get(f"{API}/v1.1/game/{pk}/feed/live")
    w = ((f or {}).get("gameData") or {}).get("weather") or {}
    return w


def pa_pmf(con):
    """P(k PAs) by (home, slot) from history."""
    pa = pd.read_sql("SELECT game_pk, top, slot, batter FROM pa WHERE slot IS NOT NULL", con)
    n = pa.groupby(["game_pk", "top", "slot"]).size().rename("k").reset_index()
    n["home"] = 1 - n.top
    pmf = {}
    for (h, s), x in n.groupby(["home", "slot"]):
        v = x.k.clip(2, 7).value_counts(normalize=True).sort_index()
        pmf[(h, s)] = v.to_dict()
    return pmf


def starter_tbf(con, pid, season):
    r = con.execute("""SELECT COUNT(*) FROM pa p JOIN games g USING(game_pk) WHERE p.pitcher=? AND p.sp=1 AND g.season=?
                       GROUP BY p.game_pk ORDER BY g.date DESC LIMIT 10""", (pid, season)).fetchall()
    post = con.execute("""SELECT AVG(n) FROM (SELECT COUNT(*) n FROM pa p JOIN games g USING(game_pk) WHERE p.sp=1 AND g.type!='R'
                          GROUP BY p.game_pk, p.pitcher)""").fetchone()[0] or 20.0
    vals = [x[0] for x in r]
    return (sum(vals) + 4 * post) / (len(vals) + 4) if vals else post


def main(date=None):
    date = date or dt.datetime.now(dt.timezone(dt.timedelta(hours=-4))).date().isoformat()
    con = sqlite3.connect(M.DB)
    games = games_on(date)
    if not games:
        print(f"No MLB games on {date}")
        return
    season = int(date[:4])
    pmf = pa_pmf(con)
    pinfo = {r[0]: (r[1], r[2], r[3]) for r in con.execute("SELECT id, name, bats, throws FROM players")}
    future, meta = [], []
    for g in games:
        pk, home, away = g["gamePk"], g["teams"]["home"]["team"]["abbreviation"], g["teams"]["away"]["team"]["abbreviation"]
        w = feed_weather(pk)
        temp = float(w["temp"]) if str(w.get("temp", "")).isdigit() else None
        import re
        mm = re.match(r"(\d+)\s*mph,?\s*(.*)", w.get("wind") or "")
        sp = {s: (g["teams"][s].get("probablePitcher") or {}) for s in ("home", "away")}
        lu = g.get("lineups") or {}
        for side, team, opp in (("away", away, home), ("home", home, away)):
            posted = lu.get(f"{side}Players") or []
            osp = sp["home" if side == "away" else "away"]
            sp_id = osp.get("id")
            sp_hand = (pinfo.get(sp_id) or (None, None, "R"))[2] or "R"
            if posted:            # confirmed lineup: only the nine
                order = [(i + 1, p["id"], (pinfo.get(p["id"]) or (None, None, None))[1], 1.0) for i, p in enumerate(posted)]
            else:                 # not posted: every active hitter, with a start chance and his usual spot
                odds = start_odds(con, team, date, sp_hand)
                roster = roster_hitters(g["teams"][side]["team"]["id"], date) or [(b, None) for _, b, _ in last_lineup(con, team, date)]
                order = []
                for bid, nm in roster:
                    o = odds.get(bid, dict(p=.04, slot=9))
                    if nm and bid not in pinfo:
                        pinfo[bid] = (nm, None, None)
                    order.append((o["slot"], bid, (pinfo.get(bid) or (None, None, None))[1], round(float(o["p"]), 3)))
                order.sort(key=lambda r: (-r[3], r[0]))
            tbf = starter_tbf(con, sp_id, season) if sp_id else 20.0
            # bullpen handedness mix this season
            bp = con.execute("""SELECT pitch_hand, COUNT(*) FROM pa p JOIN games g USING(game_pk) WHERE p.fld_team=? AND p.sp=0 AND g.season=?
                                GROUP BY pitch_hand""", (opp, season)).fetchall()
            bpw = {h: n for h, n in bp if h}; tot = sum(bpw.values()) or 1
            for slot, bid, bside, p_start in order:
                bside = bside or (pinfo.get(bid) or (None, "R"))[1] or "R"
                base = dict(game_pk=pk, date=date, season=season, type=g.get("gameType", "D"), home=home, away=away,
                            venue_id=g["venue"]["id"], temp=temp, wind_mph=float(mm.group(1)) if mm else None,
                            wind_dir=mm.group(2).strip() if mm else None, condition=w.get("condition"),
                            top=int(side == "away"), bat_team=team, fld_team=opp, batter=bid, bat_side=bside, slot=slot,
                            event="", hr=0, ev=np.nan, la=np.nan, dist=np.nan, traj=None, hx=np.nan, hy=np.nan)
                future.append(dict(base, idx=-(len(future) + 1), pitcher=sp_id or 0, pitch_hand=sp_hand, sp=1))
                for hand in ("R", "L"):
                    future.append(dict(base, idx=-(len(future) + 1), pitcher=0, pitch_hand=hand, sp=0))
                meta.append(dict(game_pk=pk, team=team, opp=opp, home=int(side == "home"), slot=slot, batter=bid,
                                 name=(pinfo.get(bid) or ("?",))[0], bats=bside, sp_id=sp_id, sp_name=osp.get("fullName"),
                                 sp_hand=sp_hand, tbf=tbf, bp_r=bpw.get("R", 0) / tot, p_start=p_start,
                                 lineup="posted" if posted else ("projected" if p_start >= .5 else "bench")))
    fut = pd.DataFrame(future)
    print(f"{date}: {len(games)} games, {len(meta)} batters ({sum(m['lineup'] == 'posted' for m in meta)} from posted lineups)", flush=True)

    # features as of today: append the future rows (they only see data before today), plus placeholder pitches
    df = M.full_frame(extra_pa=fut)
    hist = df[df.date < date]
    params = json.load(open(os.path.join(HERE, "params.json"))) if os.path.exists(os.path.join(HERE, "params.json")) else {}
    feats = params.get("features", M.BASE)
    coef = M.fit(hist[hist.season >= season - 1], feats=feats)
    now = df[(df.date == date) & (df.idx < 0)].copy()
    now["p"] = M.predict(now, coef)
    means = hist[hist.season == season][feats].mean()
    contrib = {g: sum((coef[c] * (now[c] - means[c]) for c in cols if c in coef), pd.Series(0.0, index=now.index))
               for g, cols in GROUPS.items()}

    rows = []
    for m in meta:
        x = now[(now.game_pk == m["game_pk"]) & (now.batter == m["batter"])]
        if x.empty:
            continue
        psp = float(x[x.sp == 1].p.iloc[0])
        pbp = float(m["bp_r"] * x[(x.sp == 0) & (x.pitch_hand == "R")].p.iloc[0] + (1 - m["bp_r"]) * x[(x.sp == 0) & (x.pitch_hand == "L")].p.iloc[0])
        rng = np.random.default_rng(m["batter"] % 2**32)
        tb = np.clip(rng.normal(m["tbf"], 4, 400), 9, 32)
        pk_ = pmf.get((m["home"], m["slot"]), {4: 1.0})
        miss, one, epa, esp = 0.0, 0.0, 0.0, 0.0
        for k, pr in pk_.items():
            idxs = m["slot"] + 9 * np.arange(k)
            vs_sp = (idxs[None, :] <= tb[:, None])
            pj = np.where(vs_sp, psp, pbp)                                  # per-PA chance, each TBF draw
            q0 = (1 - pj).prod(axis=1)
            q1 = (q0[:, None] * pj / (1 - pj)).sum(axis=1)                  # exactly one HR
            miss += pr * q0.mean(); one += pr * q1.mean(); epa += pr * k; esp += pr * vs_sp.sum(axis=1).mean()
        p1 = 1 - miss
        p2 = max(0.0, 1 - miss - one)
        p1_start = p1                                         # bettable only if he starts: HR% is given that he starts
        r = x[x.sp == 1].iloc[0]
        why = {g: float(np.exp(contrib[g].loc[r.name]) - 1) for g in GROUPS if g != "post" and (contrib[g] != 0).any()}
        why["opportunity"] = float(epa / 4.3 - 1)
        rows.append(dict(m, p_hr=round(p1, 4), p_hr_if_start=round(p1_start, 4), p_hr2=round(p2, 4), p_pa_sp=round(psp, 4), p_pa_bp=round(pbp, 4), exp_pa=round(epa, 2), exp_pa_sp=round(esp, 2),
                         fair=int(round(100 * (1 - p1) / p1)) if p1 < .5 else -int(round(100 * p1 / (1 - p1))),
                         why={k: round(v, 3) for k, v in why.items()},
                         park=round(float(np.exp(r.l_park)), 3), temp=None if pd.isna(r.temp) else round(float(r.temp * 10 + 70)),
                         wind_out=round(float(r.wind_out * 10), 1)))
    res = pd.DataFrame(rows)
    # blend (blend.py): batter-game model on top of the per-PA one; ranks hitters within each game
    try:
        import blend as BL
        hist_s = hist[hist.season >= season - 1]
        ek = BL.epa_table(hist_s)
        tr = BL.batter_games(hist_s, coef, ek)
        tr = tr[tr.slot.notna() & (tr.n_pa >= 2)]
        cur = now[now.sp == 1].copy()
        cur["home"] = 1 - cur.top
        cur["side"] = np.where(cur.bat_side == "S", np.where(cur.pitch_hand == "R", "L", "R"), cur.bat_side)
        cur = BL.add_mixes(cur, ek)
        tabs = BL.swing_tables(con, season - 1, extra=cur[["batter", "pitcher", "side", "season", "date"]])
        tr = BL.attach_swing(tr, tabs)
        cur = BL.attach_swing(cur, tabs)
        gb = BL.fit(tr)
        pb = dict(zip(zip(cur.game_pk, cur.batter), gb.predict_proba(cur[BL.FEATS])[:, 1]))
        q1, q2 = tr.pbz.quantile([1 / 3, 2 / 3])
        plane = {(g, b): (round(float(la), 1), "steeper" if z >= q2 else "flatter" if z <= q1 else "average")
                 for g, b, la, z in zip(cur.game_pk, cur.batter, cur.la_at_sp, cur.pbz)}
        res["plane_la"] = [plane.get((g, b), (None, None))[0] for g, b in zip(res.game_pk, res.batter)]
        res["plane_slope"] = [plane.get((g, b), (None, None))[1] for g, b in zip(res.game_pk, res.batter)]
        res["p_model"] = res.p_hr
        res["p_hr"] = [round(float(pb.get((g, b), ph)), 4) for g, b, ph in zip(res.game_pk, res.batter, res.p_hr)]
        res["fair"] = [int(round(100 * (1 - q) / q)) if q < .5 else -int(round(100 * q / (1 - q))) for q in res.p_hr]
        print(f"blend: trained on {len(tr):,} batter-games")
    except Exception as e:
        print("blend skipped (model HR% kept):", type(e).__name__, e)
    likely = res.p_start >= .5                              # ranks / picks: confirmed or likely starters only
    res["game_rank"] = res[likely].groupby("game_pk").p_hr.rank(ascending=False, method="first")
    res["game_rank"] = res.game_rank.astype("Int64")
    res = res.sort_values("p_hr", ascending=False)
    os.makedirs(OUT, exist_ok=True)
    # games that already started keep their last pregame numbers (graded as of first pitch)
    jp = os.path.join(OUT, f"{date}.json")
    now_utc = pd.Timestamp.now(tz="UTC")
    started = {g["gamePk"] for g in games if pd.Timestamp(g["gameDate"]) <= now_utc}
    if os.path.exists(jp) and started:
        old = json.load(open(jp))
        keep = [p for p in old.get("players", []) if p["game_pk"] in started]
        res = pd.concat([res[~res.game_pk.isin(started)], pd.DataFrame(keep)], ignore_index=True).sort_values("p_hr", ascending=False)
    res.drop(columns=["why"]).to_csv(os.path.join(OUT, f"{date}.csv"), index=False)
    gl = [dict(id=g["gamePk"], start_utc=g["gameDate"], date=date, away=g["teams"]["away"]["team"]["abbreviation"], home=g["teams"]["home"]["team"]["abbreviation"],
               series=g.get("seriesDescription"), venue=g["venue"]["name"],
               sp_away=(g["teams"]["away"].get("probablePitcher") or {}).get("fullName"), sp_home=(g["teams"]["home"].get("probablePitcher") or {}).get("fullName"))
          for g in games]
    payload = dict(date=date, generated=dt.datetime.now().isoformat(timespec="minutes"), features=feats,
                   coef={k: round(v, 4) for k, v in coef.items()}, games=gl, players=json.loads(res.to_json(orient="records")))
    try:                                                    # scouting tables for the page (context, not model inputs)
        import scout
        payload["scout"] = scout.build(con, date, meta, now, lambda x: M.predict(x, coef), hist)
    except Exception as e:
        import traceback; traceback.print_exc()
        print("scouting tables skipped:", type(e).__name__, e)
    json.dump(payload, open(os.path.join(OUT, f"{date}.json"), "w"), separators=(",", ":"))
    print(res[["name", "team", "slot", "sp_name", "p_hr", "fair", "exp_pa", "lineup"]].head(20).to_string(index=False))
    try:
        import page
        page.write(payload)
    except Exception as e:
        print("page skipped:", type(e).__name__, e)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--date")
    main(ap.parse_args().date)
