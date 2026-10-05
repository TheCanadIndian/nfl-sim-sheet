# NHL anytime goal scorer model

Chance each skater scores at least one goal (regulation or OT, not shootouts), with no
betting lines as inputs. Data: the free NHL API (play-by-play with shot locations; per-game
even-strength / power-play / shorthanded ice time).

## Daily use

```powershell
.\football\Scripts\python.exe hockey\fetch.py --seasons 20262027   # results + schedule
.\football\Scripts\python.exe hockey\project.py                   # next slate
```
Open `hockey/projections/index.html`, or the site at `/nhl/`. The scheduled task
"NHL check every 30 min" (`update.ps1 -Hockey -Auto`) runs a quick check (`should_run.py`,
one small schedule request) and does the full update + publish only when:
- a game starts within 75 minutes and hasn't started (start-time changes, official rosters),
- there's a game later today and the last update is 4+ hours old (lines, goalies),
- it's after 10 AM and last night hasn't been graded yet.
Skipped checks are one line each in `logs/nhl_checks.log`. Change the interval with
`$nhlEveryMinutes` in `install_schedule.ps1`, then re-run it.

`hockey/overrides.csv` (optional) for news the API doesn't carry:
```
team,player,action
TOR,Auston Matthews,out        # scratched / injured
TOR,Easton Cowan,in            # force into the lineup
BOS,Joonas Korpisalo,goalie    # confirmed starting goalie
```
Lineups (`lines.py`), best source first, shown per team on the page:
1. **Official NHL roster** from the game feed once posted (~30 min before puck drop).
2. **Reported lines** from Daily Faceoff: even-strength lines, power-play units, injured
   players, and the starting goalie (confirmed / likely / projected).
3. **Estimate**: the 12 forwards and 6 defensemen with the most expected ice time, and the
   team's most recent starter (used when lines are missing or more than 5 days old).

A listed power-play unit moves the player's expected PP time halfway from his recent history
to that unit's typical time (`LINE_PP_BLEND`; PP1/PP2 shares measured from game data); a
player on neither unit keeps 40% of his historical PP time. This can't be backtested (no
historical line reports), so it is moderate on purpose and judged on the nightly results.

## How it works (`model.py`)

1. **xG model:** logistic regression on every unblocked shot (distance, angle, shot type,
   rebound, rush, power play, shorthanded, empty net), fit on prior seasons.
2. **Player priors, as of puck drop:**
   - expected ice time at even strength and on the PP (recent games; early in a season it
     leans on longer memory until ~5 games in; regular-season games only)
   - individual xG per 60 at even strength and on the PP (stable; pulled toward position)
   - finishing = goals per xG, long memory, heavily pulled toward 1.0
3. **Game adjustment (Poisson GLM):** opponent xG allowed, opposing starter's goals allowed
   per xG faced, opponent's penalty rate (power-play chances), home ice, back-to-backs,
   position, league scoring environment (last 30 days).
4. P(goal) = 1 − e^(−λ), with a small bend above 30% (elite scorers were over-projected).

## Backtest (`nhl_backtest.py`, walk-forward; 2022–23 is history only)

| | 2024–25 | 2025–26 |
|---|---|---|
| Log-loss, model vs goals-per-game baseline | **0.3865** vs 0.3912 | **0.3908** vs 0.3950 |
| Brier | **0.1181** vs 0.1192 | **0.1196** vs 0.1206 |
| Top 3 per game scored (predicted) | 34.6% (34.0%) | 34.6% (36.2%) |

Calibrated within about a point from 5% to 35%. The 2025–26 fit ran ~3% high overall.

## Tuning log (`tune.py`: tune on 2024–25, confirm on 2025–26)
- Adopted: finishing shrinkage 40 xG (better both seasons); early-season ice-time blend
  (better both); regular-season-only ice time (neutral mid-season, fixes opening night after
  deep playoff runs); top-end calibration −0.18 above 30% (fit on 2024–25 at −0.08 it
  improved 2025–26 log-loss and Brier).
- Neutral / kept for robustness: league scoring environment.
- No gain, shown as context only: opponent's defense vs position (xG allowed to C / LW / RW / D,
  even strength and PK, `USE_POS_D`). Tried relative to the team's overall rate and raw, with
  20 / 10 / 40 pseudo-games of shrinkage: log-loss unchanged to 5 decimals in both seasons. The
  opponent's overall xG allowed already carries it. The sheet shows it per game, as player
  tags (10%+ soft or tough at even strength) and in the "Defense vs position" tab.
- No gain: head-to-head vs this opponent (`USE_H2H`). Player goals in earlier meetings vs
  what his priors expected (shrunk 1 or 3 expected goals), player xG in earlier meetings,
  and team goals vs this opponent: log-loss within ±0.00002 in both seasons, fitted
  weights near zero (player goals −0.04, i.e. slightly the wrong way). Typical player has 3
  earlier meetings, about 0.6 expected goals: far too little to separate from luck.
- No gain: memory lengths for ice time and shot rates, ice-time shrinkage, team memory,
  goalie shrinkage, a curved expected-goal term.

## Nightly results

`python hockey/results.py` grades every saved night (`hockey/projections/<date>.csv`, frozen at
puck drop: later runs only re-project games that haven't started) against who scored, matched
by NHL player ID; scratched players are voided. The daily task runs it and publishes
`/nhl/results.html`.

## Not modeled yet
Line combinations / linemate quality, injuries (use overrides), confirmed lineups and
goalies before the morning skate.

## First goal scorer
Each dressed skater's goals arrive at a steady rate, so P(first goal) = his expected goals /
all skaters' expected goals in the game x P(at least one goal). Backtest 2024–26 (2,792
games, out-of-sample rates): 2.77% predicted vs 2.78% actual per skater, calibrated by
bucket, forwards 1.01x and defensemen 0.98x of predicted, log score −3.368 per game vs
−3.586 for a uniform pick. No sign that top lines score first more than their rate implies
(they start the game, but the effect didn't show). Shown as "1st goal" on the sheet.

## Highlighted picks
The sheet stars tonight's top 5 players by goal chance and marks the best option in each
game, with up to three reasons (elite shot volume, weak opposing goalie, leaky defense,
opponent takes penalties, proven finisher, top minutes, opponent on a back-to-back).
Backtest 2024–26: the night's top 5 scored 38.0% (predicted 37.4%), the single top pick
42.5%, each game's best option 37.8%, vs 15.1% for the average skater. No extra filter beat
the model's own ranking (it is calibrated, so anything useful is already in the number);
the reasons explain the pick rather than change it.

## Selection system (tested 2026-10-02)
Signals tested on top of the model's probability (fit 2024-25, scored 2025-26): goals,
shots and xG over the last 5 games, "due" (xG minus goals), 3-game scoring streak, team
and game expected goals, PP1, home ice, opposing goalie, opponent xG allowed, finishing,
ice-time rank. None moved log-loss by more than 0.0002: hot and cold streaks, recent shots
and "due" players carry no information the model doesn't already use. The selection rule
is the model's ranking:
- Top 5 of the night: scored 37.7% / 38.4% (2024-25 / 2025-26), predicted 36.4% / 38.4%.
- Best option in each game: 38.1% / 37.4%.
- 30%+ chance: 35.0% / 34.2%, but ~20-25 players a night.
- Average skater: 15%.
The 30%+ tier was calibrated in the first week of both backtest seasons, so a cold start
over a few nights is not a sign of early-season bias. Both picks are tracked on the results page.

## Lineup check (`linecheck.py`, runs with every NHL update)
Rebuilds the real lines from the NHL shift charts (even strength, goalies excluded) and
compares them with the pregame sheet. First 11 games (2026-09-30 to 10-01): positions 99.7%
right (one C the game sheet listed at RW); 90% of listed forward linemates (97% with reported
lines only) and 97% of D partners really were the player's most frequent even-strength
linemates; 93% of listed PP1 players were among the team's top five in PP time. Line *numbers*
often differ because a "second" line can out-play the first in ice time, so linemates are the
measure that matters. Shown on the results page under lineup accuracy.

## Moneylines, puck lines and totals (`gamesim.py`)
Team expected goals = sum of its dressed skaters' expected goals. Regulation goals are
Poisson, with 20% extra regulation ties (`TIE_INFLATE`); a team leading by 1 or 2 adds an
empty-netter 45% / 63% of the time; ties go to OT/shootout, home wins 53.5%, winner +1 goal.
Exact score distribution (no random draws). OT/empty-net rates from 2022–24 results;
`REG_SCALE` 0.95 and `TIE_INFLATE` fit on 2024–25 out-of-sample expected goals.
- 2025–26 test (1,312 games): moneyline log-loss 0.681 vs 0.696 for "home team" (2024–25,
  fitted: 0.663 vs 0.685). Favorites won 54.1% vs 57.7% predicted in 2025–26 (59.8% vs 57.2%
  in 2024–25): win odds about right on average, a little too confident in 2025–26.
- Regulation ties 19.5% predicted vs 24.8% actual in 2025–26 (20.7% in 2024–25).
- Totals run high when the goal model does: 2025–26 6.45 projected vs 6.25 actual; first 17
  games of 2026–27 6.95 vs 6.53. A trailing self-correction (scale by recent actual/projected)
  fixed the average but not over/under accuracy (log-loss unchanged), so not adopted. Treat
  totals as directional. Graded nightly on the results page.

## Roster audit (2026-10-03)
Pregame sheets vs who actually dressed, first four nights:
- Opening night (ice-time estimate, no line reports): 18 dressed players missing and 18
  listed who didn't play (rookies/new signings with no ice-time history, depth players).
- "Official roster" sheets (Oct 1–2): 25 listed players didn't play. The feed's pregame
  roster includes healthy scratches; the scratches are posted separately (right-rail
  `gameInfo.scratches`). All 25 were on that list. Fix: `lines.game_roster()` removes them and
  only treats the roster as final at 17–18 skaters; until then it only rules players out.
- Call-ups missing from team roster pages (Josh Samanski, EDM) are added from the game
  roster or looked up by name (`lines.find_player`, NHL player search, matched on team).
- Line reports that come up short (a listed player also on the injury list, 17 names, or two
  players with the same name, e.g. VAN's Elias Pettersson C and D) are filled to 18 by
  expected ice time from eligible players; 11 F / 7 D reports are kept. The team note on the
  sheet names anyone added this way.
- Positions: 99.8% match the official game sheet (503 players).

## Goalies: SV% and GSAA (2026-10-03)
Shown on the sheet (under each team, for the goalie it faces: this season and last) and in a
Goalies tab (GP, shots, GA, SV%, GSAA, GSAx). GSAA = shots × league GA-per-shot − GA;
GSAx = our expected goals faced − GA; empty-net goals excluded. Tested a save-%/GSAA goalie
rating in place of the GSAx rating (`model.GQ_MODE = "sv"`): log-loss 0.38649 = 0.38649
(2024–25), 0.39087 vs 0.39075 (2025–26, worse). The model keeps GSAx.

## Totals running high: fixes (2026-10-04)
First 35 games of 2026–27: projected 6.85 goals a game, actual 6.20.
- **Data bug (fixed):** three games were loaded before their ice-time stats posted; the empty
  stats file was cached, so every 30-minute check re-added the games' shots (up to 14 extra
  "goals" in one game). That inflated those players' and the league's recent scoring and left
  the games ungraded. `fetch.py` now skips and re-downloads games with empty ice-time data, and
  replaces a game's shots whenever it reloads them; the bad rows were purged and reloaded.
- **Ice-time cap (adopted, `model.TOI_NORM`):** a lineup's expected even-strength + PK ice
  time is scaled to 16,400 skater-seconds before the goal model sees it (live lineups had run
  up to 4% over, backtest lineups 3% under). Log-loss 0.38650 → 0.38648 (2024–25), 0.39075 →
  0.39067 (2025–26).
- **Totals scale:** `gamesim.REG_SCALE` refit on 2024–25 + 2025–26 together: 0.95 → 0.94
  (over/under log-loss better pooled; moneylines unchanged).
- **Remaining:** the goal model is ~3% high on 2026–27's real lineups (6.39 vs 6.17), as it was
  in 2025–26, but it was 1.5% low in 2024–25, so no fixed cut is applied. Recheck at 150 games.
- A trailing "recent actual / projected" scaling was tested and rejected (2025–26 log-loss
  worse at every window).
