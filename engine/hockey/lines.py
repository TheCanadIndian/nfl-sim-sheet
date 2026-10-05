"""
Tonight's lineups, best source first:

  1. official   the NHL game feed's dressed roster (posted ~30 min before puck drop)
  2. lines      Daily Faceoff line combinations: even-strength lines, power-play units,
                injured players, projected starter; with the time and source of the update
  3. estimate   (caller's fallback) the 12 F / 6 D with the most expected ice time

Daily Faceoff pages are read politely (one request per team, cached for the run).
"""

import json
import re
import time
import unicodedata

import pandas as pd
import requests

import fetch as F

DF = "https://www.dailyfaceoff.com"
HEAD = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/126 Safari/537.36"}
NEXT = re.compile(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.S)
STALE_DAYS = 5
_cache = {}


def norm(name):
    s = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z ]", "", s.replace("-", " "))
    return re.sub(r"\s+", " ", s).strip()


def _page(path):
    if path in _cache:
        return _cache[path]
    for i in range(3):
        try:
            r = requests.get(f"{DF}{path}", headers=HEAD, timeout=40)
            r.raise_for_status()
            m = NEXT.search(r.text)
            data = json.loads(m.group(1))["props"]["pageProps"] if m else None
            _cache[path] = data
            time.sleep(0.6)
            return data
        except Exception:
            time.sleep(2 + 2 * i)
    _cache[path] = None
    return None


def team_slugs():
    """{NHL abbreviation: Daily Faceoff team slug}, matched on team name."""
    st = F.get(f"{F.WEB}/standings/now") or {}
    by_name = {}
    for t in st.get("standings", []):
        by_name[norm(t["teamName"]["default"])] = t["teamAbbrev"]["default"]
    page = _page("/teams/toronto-maple-leafs/line-combinations") or {}
    out = {}
    for t in page.get("sortedTeams", []):
        slug = t.get("slug") or t.get("teamSlug")
        name = norm(t.get("name") or t.get("teamName") or slug.replace("-", " "))
        if name in by_name:
            out[by_name[name]] = slug
        else:
            hit = [a for n, a in by_name.items() if n.split()[-1] == name.split()[-1]]
            if len(hit) == 1:
                out[hit[0]] = slug
    return out


def _match(name, roster):
    """roster: {normalized name: player_id}. Exact, then unique last-name match."""
    n = norm(name)
    if n in roster:
        return roster[n]
    last = n.split()[-1] if n else ""
    hits = [pid for k, pid in roster.items() if k.split()[-1] == last]
    return hits[0] if len(hits) == 1 else None


def team_lines(team, slug, skaters, goalies):
    """One team's Daily Faceoff lines mapped to NHL ids.
    Returns dict(players={pid: dict(line, pp, status)}, out=set(pid), goalie=pid|None,
                 updated=Timestamp, source=str, fresh=bool, unmatched=[names]) or None."""
    page = _page(f"/teams/{slug}/line-combinations")
    if not page or "combinations" not in page:
        return None
    c = page["combinations"]
    rs = {norm(r.name): int(r.player_id) for r in skaters[skaters.team == team].itertuples()}
    rg = {norm(r.name): int(r.player_id) for r in goalies[goalies.team == team].itertuples()}
    players, out, unmatched, goalie = {}, set(), [], None
    for p in c.get("players", []):
        cat, grp = p.get("categoryIdentifier"), p.get("groupIdentifier")
        if cat == "ev" and grp == "g":
            if p.get("positionIdentifier") == "g1":
                goalie = _match(p["name"], rg)
            continue
        pid = _match(p["name"], rs)
        if pid is None:
            if cat in ("ev", "pp"):
                unmatched.append(p["name"])
            continue
        d = players.setdefault(pid, dict(line=None, pp=None, status=None, gtd=False))
        if cat == "ev":
            d["line"] = grp.upper()                       # F1..F4, D1..D3
            d["gtd"] = bool(p.get("gameTimeDecision"))
        elif cat == "pp":
            d["pp"] = grp.upper()                         # PP1 / PP2
        elif cat == "oi":
            d["status"] = p.get("injuryStatus") or "ir"
            if d["status"] in ("out", "ir", "ltir"):
                out.add(pid)
    updated = pd.Timestamp(c.get("updatedAt")) if c.get("updatedAt") else None
    age = (pd.Timestamp.now(tz="UTC") - updated).days if updated is not None else 99
    return dict(players=players, out=out, goalie=goalie, updated=updated,
                source=c.get("sourceName") or "Daily Faceoff", fresh=age <= STALE_DAYS,
                unmatched=sorted(set(unmatched)))


def starting_goalies(goalies):
    """{team: (player_id, status)} from Daily Faceoff's starting-goalies page for today."""
    page = _page("/starting-goalies") or {}
    names = {}
    st = F.get(f"{F.WEB}/standings/now") or {}
    for t in st.get("standings", []):
        names[norm(t["teamName"]["default"])] = t["teamAbbrev"]["default"]
    out = {}
    for g in page.get("data") or []:
        for side in ("home", "away"):
            team = names.get(norm(g.get(f"{side}TeamName", "")))
            gname = g.get(f"{side}GoalieName")
            if not team or not gname:
                continue
            status = next((g[k] for k in g if k.lower().startswith(side) and "newsstrengthname" in k.lower() and g[k]), None)
            rg = {norm(r.name): int(r.player_id) for r in goalies[goalies.team == team].itertuples()}
            pid = _match(gname, rg)
            if pid is not None:
                out[team] = (pid, status or "projected")
    return out


def find_player(name, team):
    """NHL player search for a name on a line report that isn't on the team's roster page yet
    (call-ups). Returns dict(player_id, name, pos) when exactly one active player on `team`
    (or with no team listed yet) has that exact name."""
    last = str(name).split()[-1]
    try:
        r = requests.get("https://search.d3.nhle.com/api/v1/search/player",
                         params={"culture": "en-us", "limit": 40, "q": last}, timeout=20,
                         headers={"User-Agent": "Mozilla/5.0 (personal NHL projections)"}).json()
    except Exception:
        return None
    # prospects/call-ups show active=False until they've played, so match on team first
    hits = [x for x in r if norm(x.get("name")) == norm(name) and x.get("teamAbbrev") == team]
    if not hits:
        hits = [x for x in r if norm(x.get("name")) == norm(name) and x.get("active") and not x.get("teamAbbrev")]
    if len(hits) != 1:
        return None
    return dict(player_id=int(hits[0]["playerId"]), name=hits[0]["name"], pos=hits[0]["positionCode"])


def game_roster(game_id):
    """The game's own roster from the NHL feed, before puck drop:
    {team: dict(ids=set of skaters on the game roster minus posted healthy scratches,
                complete=True when that leaves exactly a dressed lineup (17-18 skaters),
                info={id: (name, pos)} for adding call-ups missing from the team roster)}.
    The feed lists the full active roster (scratches included) until the lineup is set, so
    `complete` matters: an incomplete list is only used to rule players out."""
    pbp = F.get(f"{F.WEB}/gamecenter/{game_id}/play-by-play") or {}
    spots = pbp.get("rosterSpots") or []
    if not spots:
        return {}
    rr = F.get(f"{F.WEB}/gamecenter/{game_id}/right-rail") or {}
    abbr = {pbp["homeTeam"]["id"]: pbp["homeTeam"]["abbrev"], pbp["awayTeam"]["id"]: pbp["awayTeam"]["abbrev"]}
    side = {pbp["homeTeam"]["abbrev"]: "homeTeam", pbp["awayTeam"]["abbrev"]: "awayTeam"}
    out = {}
    for sp in spots:
        if sp.get("positionCode") == "G":
            continue
        t = abbr.get(sp["teamId"])
        d = out.setdefault(t, dict(ids=set(), info={}, scratches=set()))
        pid = int(sp["playerId"])
        d["ids"].add(pid)
        d["info"][pid] = (f"{sp['firstName']['default']} {sp['lastName']['default']}", sp["positionCode"])
    for t, d in out.items():
        sc = (rr.get("gameInfo", {}).get(side.get(t, ""), {}) or {}).get("scratches") or []
        d["scratches"] = {int(x["id"]) for x in sc if x.get("id")}
        d["ids"] -= d["scratches"]
        d["complete"] = 17 <= len(d["ids"]) <= 18
    return out


def official_roster(game_id):
    """{team: set(player_id)} of dressed skaters once the NHL feed posts them, else {}."""
    pbp = F.get(f"{F.WEB}/gamecenter/{game_id}/play-by-play") or {}
    spots = pbp.get("rosterSpots") or []
    if len(spots) < 30:
        return {}
    abbr = {pbp["homeTeam"]["id"]: pbp["homeTeam"]["abbrev"], pbp["awayTeam"]["id"]: pbp["awayTeam"]["abbrev"]}
    out = {}
    for s in spots:
        if s.get("positionCode") != "G":
            out.setdefault(abbr.get(s["teamId"]), set()).add(int(s["playerId"]))
    return out
