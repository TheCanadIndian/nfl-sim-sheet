"""
Game-time weather from Open-Meteo (free, no API key): historical archive for past
games, forecast (up to 16 days out) for upcoming ones. Cached in the `weather` table.

Per game: mean temperature (F) and wind (mph) and total precipitation (in) over the
three hours from kickoff, at the stadium. Indoor games (dome, or a closed retractable
roof) get calm, 70F, dry. Retractable roofs with no status recorded are treated as
closed, which is the usual case.

    python -m boxscore.weather           # fill missing games, refresh forecasts
"""

import datetime as dt
import sqlite3
import time

import numpy as np
import pandas as pd
import requests

ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
FORECAST = "https://api.open-meteo.com/v1/forecast"
HOURLY = "temperature_2m,wind_speed_10m,precipitation"

# stadium_id -> (lat, lon, retractable roof?)
STADIUMS = {
    "ATL97": (33.755, -84.401, True), "BAL00": (39.278, -76.623, False),
    "BOS00": (42.091, -71.264, False), "BUF00": (42.774, -78.787, False),
    "BUF01": (42.774, -78.787, False), "CAR00": (35.226, -80.853, False),
    "CHI98": (41.862, -87.617, False), "CIN00": (39.095, -84.516, False),
    "CLE00": (41.506, -81.700, False), "DAL00": (32.748, -97.093, True),
    "DEN00": (39.744, -105.020, False), "DET00": (42.340, -83.046, False),
    "FRA00": (50.069, 8.645, False), "GER00": (48.219, 11.625, False),
    "GNB00": (44.501, -88.062, False), "HOU00": (29.685, -95.411, True),
    "IND00": (39.760, -86.164, True), "JAX00": (30.324, -81.637, False),
    "KAN00": (39.049, -94.484, False), "LAX01": (33.953, -118.339, False),
    "LON00": (51.556, -0.280, False), "LON01": (51.456, -0.341, False),
    "LON02": (51.604, -0.066, False), "MAD01": (40.453, -3.688, True),
    "MEL00": (-37.820, 144.983, False), "MEX00": (19.303, -99.150, False),
    "MIA00": (25.958, -80.239, False), "MIN01": (44.974, -93.258, False),
    "MUN01": (48.219, 11.625, False), "NAS00": (36.166, -86.771, False),
    "NOR00": (29.951, -90.081, False), "NYC01": (40.814, -74.074, False),
    "PAR00": (48.924, 2.360, False), "PHI00": (39.901, -75.168, False),
    "PHO00": (33.528, -112.263, True), "PIT00": (40.447, -80.016, False),
    "RIO00": (-22.912, -43.230, True), "SAO00": (-23.545, -46.474, False),
    "SEA00": (47.595, -122.332, False), "SFO01": (37.403, -121.970, False),
    "TAM00": (27.976, -82.503, False), "VEG00": (36.091, -115.184, False),
    "WAS00": (38.908, -76.864, False),
}
INDOOR = dict(temp=70.0, wind=0.0, precip=0.0)


def _indoor(roof, stadium_id):
    if roof in ("dome", "closed"):
        return True
    if roof in ("outdoors", "open"):
        return False
    return STADIUMS.get(stadium_id, (0, 0, False))[2]     # unknown: retractable -> closed


def _kickoff(g):
    t = g.gametime if isinstance(g.gametime, str) and ":" in g.gametime else "13:00"
    return pd.Timestamp(f"{str(g.gameday)[:10]} {t}")


def _fetch(url, lat, lon, start, end):
    params = dict(latitude=lat, longitude=lon, start_date=start, end_date=end, hourly=HOURLY,
                  temperature_unit="fahrenheit", wind_speed_unit="mph", precipitation_unit="inch",
                  timezone="America/New_York")
    for attempt in range(3):
        try:
            r = requests.get(url, params=params, timeout=60)
            r.raise_for_status()
            h = r.json()["hourly"]
            return pd.DataFrame({"time": pd.to_datetime(h["time"]), "temp": h["temperature_2m"],
                                 "wind": h["wind_speed_10m"], "precip": h["precipitation"]})
        except Exception:
            time.sleep(2 + 3 * attempt)
    return None


def update(con, seasons=None, verbose=True):
    """Fill weather for games missing it; always refresh games that haven't been played."""
    con.execute("""CREATE TABLE IF NOT EXISTS weather (game_id TEXT PRIMARY KEY, temp REAL,
                   wind REAL, precip REAL, indoor INTEGER, source TEXT, fetched_at TEXT)""")
    g = pd.read_sql("SELECT game_id, season, gameday, gametime, roof, stadium_id, result FROM games", con)
    if seasons:
        g = g[g.season.isin(seasons)]
    have = set(pd.read_sql("SELECT game_id FROM weather WHERE source != 'forecast'", con).game_id)
    today = pd.Timestamp.now().normalize()
    horizon = today + pd.Timedelta(days=15)
    g["kick"] = [_kickoff(r) for r in g.itertuples()]
    todo = g[~g.game_id.isin(have) & (g.kick <= horizon)]
    rows, now = [], dt.datetime.now().strftime("%Y-%m-%d %H:%M")

    for r in todo[[_indoor(a, b) for a, b in zip(todo.roof, todo.stadium_id)]].itertuples():
        rows.append((r.game_id, *INDOOR.values(), 1, "indoor", now))
    out = todo[[not _indoor(a, b) for a, b in zip(todo.roof, todo.stadium_id)]]
    unknown = sorted(set(out.stadium_id) - set(STADIUMS))
    if unknown and verbose:
        print(f"  weather: no coordinates for stadiums {unknown} -- skipped")
    out = out[out.stadium_id.isin(STADIUMS)]

    # One request per stadium per API window.
    recent = today - pd.Timedelta(days=80)
    for (sid, use_forecast), grp in out.groupby([out.stadium_id, out.kick >= recent]):
        lat, lon, _ = STADIUMS[sid]
        start, end = grp.kick.min().date().isoformat(), (grp.kick.max() + pd.Timedelta(hours=4)).date().isoformat()
        h = _fetch(FORECAST if use_forecast else ARCHIVE, lat, lon, start, end)
        if h is None:
            if verbose:
                print(f"  weather: request failed for {sid} {start}..{end}")
            continue
        for r in grp.itertuples():
            w = h[(h.time >= r.kick.floor("h")) & (h.time < r.kick.floor("h") + pd.Timedelta(hours=3))]
            if w.empty or w.temp.isna().all():
                continue
            src = "forecast" if r.kick > pd.Timestamp.now() else ("recent" if use_forecast else "archive")
            rows.append((r.game_id, float(w.temp.mean()), float(w.wind.mean()), float(w.precip.sum()),
                         0, src, now))
    con.executemany("INSERT OR REPLACE INTO weather VALUES (?,?,?,?,?,?,?)", rows)
    con.commit()
    if verbose:
        print(f"  weather: {len(rows)} games updated")
    return len(rows)


def features(con):
    """Per game: w_wind (mph outdoors), w_cold (degrees below 45F / 10), w_rain (>= 0.05 in)."""
    try:
        w = pd.read_sql("SELECT game_id, temp, wind, precip, indoor FROM weather", con)
    except Exception:
        return pd.DataFrame(columns=["game_id", "w_wind", "w_cold", "w_rain"])
    w["w_wind"] = np.where(w.indoor == 1, 0.0, w.wind)
    w["w_cold"] = np.where(w.indoor == 1, 0.0, np.clip(45 - w.temp, 0, None) / 10)
    w["w_rain"] = np.where(w.indoor == 1, 0.0, (w.precip >= 0.05).astype(float))
    return w[["game_id", "w_wind", "w_cold", "w_rain", "temp", "wind", "precip", "indoor"]]


if __name__ == "__main__":
    update(sqlite3.connect("nfl.db"))
