#!/usr/bin/env python3
"""
NBA slate projections: both the Vegas-anchored and the market-blind model.

    python nba/project.py                  # next slate with games not yet started
    python nba/project.py --date 2026-10-20

Who plays: each team's current ESPN roster (follows trades and signings) and the injury
report. Out / Doubtful are removed and their minutes go to teammates in proportion to role;
Questionable / Day-To-Day stay in, flagged. Lines (spread, total) come from the game's
pregame odds; without them the Vegas model uses the blind team forecast and says so.

Writes nba/projections/<date>.csv/.json/.html (+ index.html). Games that already started keep
their frozen pregame rows.
"""

import argparse
import datetime as dt
import json
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(1, os.path.dirname(HERE))
import fetch as F  # noqa: E402
import model as M  # noqa: E402
import learned  # noqa: E402
learned.apply("nba")

OUT = os.path.join(HERE, "projections")
ROTATION = 10           # always project the top-10 by expected minutes, plus anyone else at 10+ min
MARGIN_SD = 12.5        # NBA final-margin spread around the expected margin (points)
STATUS_OUT = {"Out", "Doubtful", "Suspension", "Suspended"}


NBA_TEAMS = {"ATL", "BKN", "BOS", "CHA", "CHI", "CLE", "DAL", "DEN", "DET", "GS", "HOU", "IND", "LAC", "LAL", "MEM", "MIA",
             "MIL", "MIN", "NO", "NY", "OKC", "ORL", "PHI", "PHX", "POR", "SA", "SAC", "TOR", "UTAH", "WSH"}

def american(p):
    p = min(max(p, 1e-4), 1 - 1e-4)
    return round(-100 * p / (1 - p)) if p >= .5 else round(100 * (1 - p) / p)


def teams():
    d = F.get(f"{F.API}/teams") or {}
    return {t["team"]["abbreviation"]: t["team"]["id"] for t in d["sports"][0]["leagues"][0]["teams"]}


def rosters(abbrs):
    """Current roster per team with injury status. Returns DataFrame(team, player_id, name, pos, status, note)."""
    ids, rows = teams(), []
    inj = {}
    for blk in (F.get(f"{F.API}/injuries") or {}).get("injuries", []):
        for i in blk.get("injuries", []):
            href = ((i.get("athlete") or {}).get("links") or [{}])[0].get("href", "")
            try:
                pid = int(href.split("/id/")[1].split("/")[0])
            except (IndexError, ValueError):
                continue
            inj[pid] = (i.get("status", ""), i.get("shortComment", "") or "")
    for t in abbrs:
        r = F.get(f"{F.API}/teams/{ids[t]}/roster") or {}
        for a in r.get("athletes", []):
            pid = int(a["id"])
            st, note = inj.get(pid, ("", ""))
            if not st and a.get("injuries"):
                st = a["injuries"][0].get("status", "")
            rows.append(dict(team=t, player_id=pid, name=a["displayName"],
                             pos=(a.get("position") or {}).get("abbreviation", ""), status=st or "Active", note=note))
    return pd.DataFrame(rows)


def lines(game_ids):
    out = {}
    for gid in game_ids:
        sm = F.get(f"{F.API}/summary?event={gid}") or {}
        _, ln = F.parse_summary(int(gid), sm)
        if ln:
            out[int(gid)] = ln
    return out


def compress(x, stat):
    """Counts: exact probabilities (offset + pmf). Minutes: 1% quantiles."""
    if stat == "min":
        return {"q": [round(float(v), 1) for v in np.quantile(x, np.arange(.005, 1, .01))]}
    x = np.asarray(x).astype(int)
    lo = int(x.min())
    pmf = np.bincount(x - lo) / len(x)
    return {"o": lo, "p": [round(float(v), 4) for v in pmf]}


def summarize(out, sims, mode):
    rows, dists = [], {}
    for i, r in enumerate(out.itertuples()):
        k = f"{r.game_id}|{r.team}|{r.name}"
        d = {}
        for c in M.SHOW:
            s = sims[c][i]
            q = np.quantile(s, [.1, .25, .5, .75, .9])
            rows.append(dict(game_id=r.game_id, team=r.team, opp=r.opp, player_id=r.player_id, name=r.name, pos=r.pos,
                             model=mode, stat=c, mean=float(getattr(r, "mu_" + c)), p10=q[0], p25=q[1], median=q[2], p75=q[3], p90=q[4]))
            d[c] = compress(s, c)
        dists[k] = d
    return pd.DataFrame(rows), dists


def save_draws(out, sims, n=1000):
    """First n simulations per player and stat, packed as small ints: <OUT>/draws/<game_id>.json."""
    import base64
    def pack(x):
        v = np.rint(np.asarray(x, float)[:n]).astype(int)
        if v.min() >= 0 and v.max() <= 255:
            return {"t": "u8", "b": base64.b64encode(v.astype(np.uint8).tobytes()).decode()}
        return {"t": "i16", "b": base64.b64encode(np.clip(v, -32768, 32767).astype("<i2").tobytes()).decode()}
    by = {}
    for i, r in enumerate(out.itertuples()):
        by.setdefault(int(r.game_id), {"n": n, "p": {}})["p"][f"{r.game_id}|{r.team}|{r.name}"] = {c: pack(sims[c][i]) for c in sims if c != "min"}
    d = os.path.join(OUT, "draws")
    os.makedirs(d, exist_ok=True)
    for gid, dd in by.items():
        json.dump(dd, open(os.path.join(d, f"{gid}.json"), "w"), separators=(",", ":"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date")
    a = ap.parse_args()
    g, _ = M.load()
    now = pd.Timestamp.now(tz="UTC")
    start = pd.to_datetime(g.start_utc, utc=True)
    up = g[(g.state != "FINAL") & g.game_type.isin([2, 3]) & (start > now)]
    if a.date:
        up = up[up.date == pd.Timestamp(a.date)]
    else:
        up = up[up.date == up.date.min()]
    if up.empty:
        sys.exit("No upcoming NBA games.")
    date = up.date.iloc[0].date().isoformat()
    abbrs = sorted(set(up.home) | set(up.away))
    print(f"{date}: {len(up)} games; rosters for {len(abbrs)} teams")
    ro = rosters(abbrs)
    ln = lines(up.game_id)
    fut = up[["game_id", "season", "date", "start_utc", "home", "away"]].copy()
    fut["spread"] = fut.game_id.map(lambda x: ln.get(int(x), {}).get("spread"))
    fut["total"] = fut.game_id.map(lambda x: ln.get(int(x), {}).get("total"))
    fut["home_score"] = fut["away_score"] = 0
    fp = []
    for gm in fut.itertuples():
        for t in (gm.home, gm.away):
            x = ro[(ro.team == t) & ~ro.status.isin(STATUS_OUT)].assign(game_id=gm.game_id)
            fp.append(x[["game_id", "team", "player_id", "name", "pos"]])
    fp = pd.concat(fp, ignore_index=True)
    fr = M.Frames(future=fut, future_players=fp)
    rows = fr.p[fr.p.game_id.isin(fut.game_id)].copy()
    # rotation: the top players by expected minutes, plus anyone else who usually plays 10+
    rows["rk"] = rows.groupby(["game_id", "team"]).e_min.rank(ascending=False, method="first")
    rows = rows[(rows.rk <= ROTATION + 3) | (rows.e_min >= 10)].copy()
    st = ro.set_index(["team", "player_id"])
    rows["status"] = [st.status.get((t, p), "Active") for t, p in zip(rows.team, rows.player_id)]
    rows["note"] = [st.note.get((t, p), "") for t, p in zip(rows.team, rows.player_id)]
    tabs, dists = [], {}
    for mode in ("vegas", "blind"):
        out, sims = M.project(fr, rows, mode=mode, live=True)
        tab, d = summarize(out, sims, mode)
        tabs.append(tab)
        dists[mode] = d
        if mode == "vegas":                       # saved simulations for the page's build-your-own parlay slip
            # with each game's shared state (gamestate.py: pace / team factors, blowout minutes); means unchanged
            sv = {c: np.asarray(v)[:, :1000].copy() for c, v in sims.items()}
            try:
                import gamestate as GSt
                o_, r_ = out.reset_index(drop=True), rows.reset_index(drop=True)
                for k_, (gid, idx) in enumerate(o_.groupby("game_id").groups.items()):
                    idx = np.asarray(idx)
                    for c, v in GSt.draws(o_.iloc[idx].reset_index(drop=True), r_.iloc[idx].reset_index(drop=True), n=1000, seed=k_).items():
                        sv[c][idx] = v
            except Exception as e:
                print("game-state draws skipped:", type(e).__name__, e)
            save_draws(out, sv)
    tab = pd.concat(tabs, ignore_index=True)
    meta = rows[["game_id", "team", "player_id", "status", "note", "starter", "min_trend", "games_prior", "pg", "p_play"]
                + [c for c in ["arch"] + [f"dva_{s}" for s in M.DVA_STATS] if c in rows]].copy()
    if fr.arch is not None and "arch" in meta:                         # archetype name for the page
        meta["arch_name"] = meta.arch.map(lambda a: fr.arch["names"][int(a)] if pd.notna(a) else None)
    meta["e_min"] = M.team_minutes(rows, live=True)
    # per-game team view
    games = []
    # each out player's usual minutes (his latest expected minutes before this game): a teammate out matters when he plays
    lastmin = {pid: round(float(m), 1) for pid, m in fr.p[fr.p.played == 1].sort_values("date").groupby("player_id").e_min.last().items()}
    for gm in fut.sort_values("start_utc").itertuples():
        tv = fr.team[fr.team.game_id == gm.game_id].set_index("team")
        side = {}
        for mode in ("vegas", "blind"):
            h = float(tv.loc[gm.home, "vegas_pts" if mode == "vegas" else "blind_pts"]) if pd.notna(tv.loc[gm.home, "vegas_pts"]) or mode == "blind" else float(tv.loc[gm.home, "blind_pts"])
            aw = float(tv.loc[gm.away, "vegas_pts" if mode == "vegas" else "blind_pts"]) if pd.notna(tv.loc[gm.away, "vegas_pts"]) or mode == "blind" else float(tv.loc[gm.away, "blind_pts"])
            from math import erf, sqrt
            ph = 0.5 * (1 + erf((h - aw) / (MARGIN_SD * sqrt(2))))
            side[mode] = dict(home_pts=round(h, 1), away_pts=round(aw, 1), home_win=round(ph, 3))
        games.append(dict(id=int(gm.game_id), home=gm.home, away=gm.away, start_utc=gm.start_utc,
                          start=pd.Timestamp(gm.start_utc).tz_convert("America/New_York").strftime("%a %b %d · %I:%M %p ET").replace(" 0", " "),
                          spread=None if pd.isna(gm.spread) else float(gm.spread), total=None if pd.isna(gm.total) else float(gm.total),
                          has_line=bool(pd.notna(gm.spread)), models=side,
                          out={t: [dict(n=r.name, st=r.status, note=r.note, m=lastmin.get(r.player_id)) for r in ro[(ro.team == t) & ro.status.isin(STATUS_OUT)].itertuples()]
                               for t in (gm.home, gm.away)}))
    # defense vs position: latest per defense
    dv = fr.dvp_hist.sort_values("date").groupby(["opp", "pg"]).tail(1)
    dv = dv[dv.opp.isin(NBA_TEAMS)]                        # drop All-Star / exhibition teams (STRIPES, WORLD, ...)
    dvp = [dict(team=r.opp, pg=r.pg, **{c: round(float(getattr(r, "dvp_" + c)), 3) for c in M.MATCH_STATS}) for r in dv.itertuples()]
    # defense vs archetype: latest per defense (points and threes; Defense tab "By archetype")
    dva, arch_names = [], None
    if fr.arch is not None and hasattr(fr, "dva_hist"):
        arch_names = fr.arch["names"]
        da = fr.dva_hist.sort_values("date").groupby(["opp", "arch"]).tail(1)
        da = da[da.opp.isin(NBA_TEAMS)]
        dva = [dict(team=r.opp, a=int(r.arch), **{c: round(float(getattr(r, "dva_" + c)), 3) for c in M.DVA_ALL}) for r in da.itertuples()]
    os.makedirs(OUT, exist_ok=True)
    # freeze games that already started: keep their earlier rows
    csv = os.path.join(OUT, f"{date}.csv")
    keep_ids = set(fut.game_id)
    if os.path.exists(csv):
        old = pd.read_csv(csv)
        tab = pd.concat([old[~old.game_id.isin(keep_ids)], tab], ignore_index=True)
    tab.round(3).to_csv(csv, index=False)
    import page
    payload = dict(date=date, generated=dt.datetime.now().strftime("%b %d, %Y %I:%M %p").replace(" 0", " "),
                   games=games, players=page.player_payload(tab, meta, dists), dvp=dvp, dva=dva, arch=arch_names, dva_used=list(M.DVA_STATS))
    jp = os.path.join(OUT, f"{date}.json")
    if os.path.exists(jp):
        old = json.load(open(jp))
        payload["games"] = sorted([x for x in old["games"] if x["id"] not in keep_ids] + payload["games"], key=lambda x: x["start_utc"])
        # started games keep their earlier players (frozen); this run adds only the upcoming games' players. The new
        # payload is built from `tab`, which also carries the started games' old rows, so those are dropped here (they
        # stacked one copy per run before 2026-10-21); each player key appears once
        keep = {int(g) for g in keep_ids}
        new = [x for x in payload["players"] if int(x["g"]) in keep]
        seen, merged = set(), []
        for x in [x for x in old["players"] if int(x["g"]) not in keep] + new:
            if x["k"] not in seen:
                seen.add(x["k"]); merged.append(x)
        payload["players"] = merged
    json.dump(payload, open(jp, "w"), separators=(",", ":"))
    html = page.render(payload)
    for f in (f"{date}.html", "index.html"):
        open(os.path.join(OUT, f), "w", encoding="utf-8").write(html)
    v = tab[(tab.model == "vegas") & (tab.stat == "pts")].sort_values("median", ascending=False).head(10)
    print(v[["team", "opp", "name", "median", "p10", "p90"]].to_string(index=False))
    print(f"Wrote {os.path.join(OUT, date + '.html')}")


if __name__ == "__main__":
    main()
