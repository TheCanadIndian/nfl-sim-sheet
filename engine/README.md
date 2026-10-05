# NFL box-score model

Projects full team and player box scores as **distributions** (10,000 simulated
games each), calibrated for player props.

## Weekly use

```powershell
# 1. refresh data (Tuesday for results; again Sat/Sun for injury reports + depth charts)
.\football\Scripts\python.exe build_nfl_db.py --seasons 2025 2026 --skip ngs_pass ngs_rush ngs_rec player_week

# 2. project the next unplayed week
.\football\Scripts\python.exe project.py --lines lines.csv --overrides overrides.csv
```

The first time, build all seasons: `build_nfl_db.py --seasons 2022 2023 2024 2025 2026`.

Outputs in `projections/`:
- `<season>_wk<NN>_players.csv`: player × stat: mean, median, p10/p25/p75/p90
- `<season>_wk<NN>_teams.csv`: team box score, sim win probability vs Vegas implied points
- `<season>_wk<NN>_props.csv`: with `--lines`, P(over)/P(under) and no-vig fair American odds

See `lines.example.csv` and `overrides.example.csv` for formats. Overrides are for
news the feeds don't have yet (late scratches, QB changes). Injury-report
Out/Doubtful players are dropped automatically.

## Website

`python site.py` assembles everything into `site/` (published to GitHub Pages by the
scheduled runs), with one navigation bar on every page:

| Address | Page |
|---|---|
| `/` | Home: this week's NFL slate, today's NHL slate, model accuracy, how to read the numbers |
| `/nfl/` | This week's NFL projections (games, player board, defense vs position, fair odds) |
| `/nfl/results.html` | Every past week graded; what to improve |
| `/nfl/weeks/` | Frozen projections for each week (also the next week's preview) |
| `/nfl/blind/` | Experimental market-blind build |
| `/nfl/results.html#compare` | Vegas-anchored vs market-blind, same player-games, by stat and week |
| `/nfl/blind/weeks/` | Frozen market-blind pages per week |
| `/nhl/` | Anytime goal sheet |
| `/nhl/results.html` | Each night's list graded: scorers vs expected, calibration, top-3 hit rate |

## Automatic updates

`update.ps1` refreshes the data and rebuilds the projections; open `projections\latest.html`.
`update.ps1 -News` also has Claude search injury news and update this week's overrides
(`overrides\<season>_wk<NN>.csv`, reused by every later run that week). Background
Claude runs can't publish the shared link; ask Claude in a normal session to republish
`projections\latest.html` when you want it online. Logs go to `logs\`.
Run scripts with `powershell -ExecutionPolicy Bypass -File .\update.ps1 [-News]`.

Install the weekly schedule (Windows Task Scheduler, folder "NFL Box Score"):

```powershell
powershell -ExecutionPolicy Bypass -File .\install_schedule.ps1          # install
powershell -ExecutionPolicy Bypass -File .\install_schedule.ps1 -Remove  # uninstall
```

| When (ET) | Run |
|---|---|
| Tue 9:00 AM | free: results |
| Wed, Thu, Fri 6:00 PM | free: injury reports |
| Thu 4:30 PM | **news**: before TNF |
| Sun 10:30 AM | **news**: before early games |
| Sun 11:45 AM, 3:15 PM | free: inactives |
| Mon 5:30 PM | **news**: before MNF |

Runs skip games that have kicked off and roll to next week after the last one.
Edit the `$runs` table in `install_schedule.ps1` to change times, then re-run it.

## How it works

```
Vegas spread/total ──► game script z (points vs implied), drawn from real historical pairs
                           │
team priors ───────────────┼──► plays, pass rate, sack/scramble rate, cmp%, ypa, ypc, INT,
(recency-weighted,         │    TDs, FGs  (regressions fit on history, relative to the
 league-relative)          │    rolling league level, conditioned on z and on volume)
                           ▼
player priors ──► target / carry shares (Dirichlet-multinomial), catch rate, yards per
(shrunk toward    catch/carry drawn from real per-position distributions, TDs by each
 replacement)     player's TD share × that sim's volume
```

Every simulated box score is internally consistent: QB passing yards = sum of
receiving yards, and team totals = sum of players.

Code: `boxscore/data.py` (pbp → official-stat box scores; validated against
nflverse player stats: passing, targets, receptions and TDs match 100%),
`priors.py`, `team_model.py`, `sim.py`, `pipeline.py`.

## Accuracy (walk-forward, out of sample)

`python backtest.py`: 2025 fit on 2022–24, 2026 (weeks 1–3) fit on 2022–25; 318 games.
A calibrated model puts ~50% of actuals inside its 50% interval and ~80% inside its 80% interval.

| stat | MAE model | MAE trailing-5 avg | in 50% | in 80% |
|---|---|---|---|---|
| QB pass yds | 59.1 | 68.4 | 47.6% | 80.5% |
| QB attempts | 6.9 | 7.9 | 49.7% | 78.0% |
| rush yds | 20.9 | 23.3 | 49.2% | 79.9% |
| carries | 3.7 | 4.1 | 55.8% | 84.2% |
| receptions | 1.52 | 1.66 | 50.6% | 81.4% |
| rec yds | 21.0 | 23.4 | 51.0% | 80.2% |

Anytime TD: Brier 0.183 vs 0.219 for the trailing baseline; within ~3 pts per bucket.

## Model tuning log

`python tune.py <wr|td|exit>` scores settings on 2024 (fit on 2022–23);
`python tune.py confirm '<json>'` checks the chosen ones against the current model on
2025–26, which is never used for choosing. TD chances are graded for every simulated
player so all settings are scored on the same set.

**2026-09-28** (from the results page's "What to improve")
- **Adopted: TD shares compressed (`TD_GAMMA` 0.85) and a QB goal-line weight
  (`QB_TD_MULT` 1.8).** On 2025–26: TD log-loss 0.4102 → 0.4032, calibration error
  0.0253 → 0.0171, top bucket 54.1%/50.5% → 52.7%/52.2% (projected/actual),
  QB TD chance 7.5% → 10.9% (actual 12.7%).
- **Not adopted: early exits for RB/WR/TE (`P_EXIT`).** 6% brought 2024 (78.9% inside the
  80% range) to target but pushed 2025–26 from 81.1% to 82.3%. Across the three
  seasons the current ranges already average about 80%.
- **Not adopted: more shrinkage on WR target shares/catch rates (`K_TGT`, `K_CATCH`).**
  2024 WR receptions were unbiased (+0.5%); the 2025 over-projection (~5%) came with a
  league-wide drop in WR catch rate (64.1% projected, 62.2% actual). Open item: make the
  position catch-rate baselines recency-weighted so they follow shifts like that.

**2026-09-28, sharpness** (goal: results closer to the median at the same ~80% coverage;
scored by median error and quantile score on a fixed set of 2024 player-games)
- **Not adopted: snap-share usage** (expected snaps × targets/carries per snap, blended
  into the share priors; `W_SNAP_*`, off). Every blend weight, snap memory and shrinkage
  tried was equal or worse on targets and worse on yards (best: targets 2.117 vs 2.119,
  receiving yards 21.48 vs 21.34). The recency-weighted shares already follow role changes.
- **Not adopted: yards-per-touch shrinkage changes** (`K_YPR` 15/60, `K_YPC` 40/160): the
  current 30 / 80 were best.
- **Error floor (2025):** receiving yards error is 25.5 with no model, 20.8 with this
  model, 15.0 if each player's exact targets were known in advance (rushing: 27.2 / 21.1 /
  13.8). Target error (2.1 per player-game) is near the ±2 game-to-game randomness of
  targets for a fixed role, so remaining gains need new pregame information, not tuning.

**2026-09-28, weather** (`boxscore/weather.py`: Open-Meteo kickoff wind/temp/rain per
stadium, archive for past games, 16-day forecast for upcoming; cached in the `weather`
table and refreshed by `build_nfl_db.py`)
- **Not adopted in the model (`team_model.USE_WEATHER` off).** Wind, cold and rain as
  inputs to pass rate, completion %, yards/attempt, yards/carry, sacks, INTs, FGs and the
  pass/rush TD split: 2024 worse (QB passing yards error 67.3 → 67.8, receiving 21.34 →
  21.47), 2025–26 about flat (59.1 → 58.8, 20.99 → 21.00). Fitted effects are small once
  the Vegas total is known (20 mph ≈ −0.2 yds/attempt), because totals already price
  weather. Windy games (15+ mph) are too few to show a reliable edge.
- **Shown on the projections page** for context: kickoff forecast per game, highlighted
  at 15+ mph wind, rain, or 32°F and below.

**2026-09-30, who beats the middle half** (walk-forward backtest 2024–26, every player-game;
"beat" = finished above the projected 75th percentile, 25% if calibrated)
- **No signal for veterans:** a player's earlier beat rate, recent misses vs his median,
  target-share trend, aDOT, yards per target, implied total and spread did not predict the
  next game out of sample (fit 2024, test 2025–26: log-loss no better than a flat 25%).
  An early version that looked predictive was leaking other stats from the same game.
- **Adopted: early-round rookie usage boost (`priors.ROOKIE_USAGE` 0.15).** Rookies
  drafted in the top 64 beat the middle half ~31% and fell below it ~20%, in both 2024 and
  2025–26, all season long (+7–12% more targets, carries and yards than projected). Their
  priors start at a replacement-level share and update slowly. The boost multiplies their
  target and carry shares in their rookie season (the team total is unchanged, so it comes
  out of teammates). Graded on every player-game (`tune.py rookie --all-rows`):
  2025–26 rookie bias +13.7% → +3.6%, quantile score better for targets, catches,
  receiving yards, carries and rushing yards, veterans slightly better, TD log-loss
  0.4021 → 0.4018. Not adopted: a yards-per-catch bump (no gain), top-32 picks only (worse).
- **Not adopted: head-to-head history** (2026-09-30). A player's results vs this opponent
  relative to his norm (2022+), his earlier model misses vs this opponent (2024+), and a
  team's points vs Vegas in earlier meetings: no effect on the next meeting. Player
  slopes within ±1.4 standard errors with signs flipping between 2024 and 2025–26; team
  miss correlation +0.02 (2024) and −0.04 (2025–26); game totals likewise. The opponent's
  current defense (already in the model) carries what matters.
- **Results page "Above the range" tab:** season table of who finished above the middle
  half most often, and each week's list.

## Experimental: market-blind build

`python project.py --blind` (writes `projections/blind/`) replaces the Vegas spread and
total with `boxscore/market_free.py`: each team's points predicted from opponent-adjusted
EPA and points (offense and defense, recency-weighted), starting-QB efficiency, pace,
home field (neutral sites excluded), rest/short week/bye, and kickoff weather. Fit
walk-forward. `python blind_backtest.py` compares it with the Vegas model on 2025–26:

| | Vegas | Blind |
|---|---|---|
| Team points error (per team) | 7.37 | 7.61 (+3.3%) |
| Winner picked (2025) | 65.8% | 59.5% |
| QB passing / receiving / rushing yards error | 59.1 / 20.99 / 20.91 | 59.5 / 21.16 / 21.03 (+0.6–0.8%) |
| Inside 80% range | 81.1% | 80.8% |

Box scores barely suffer because they're driven by player roles and volume, not game
totals. In the blind build the page's "Vegas" figures are the model's own forecast.

## Past weeks and results

- `projections/weeks/<season>_wk<NN>*`: every game's **last pregame projection**
  (each live run replaces only the games it projected, so a game freezes at kickoff),
  plus a frozen page per week. `archive.py` maintains it; `project.py` calls it.
- Weeks that weren't saved live are rebuilt with
  `python project.py --season S --week W --played --archive` (model fit only on games
  before that week) and labeled "reconstructed". A reconstructed projection never
  replaces a live one. `backfill_missing.py` does this automatically for finished games
  missing from the archive (e.g. play-by-play that posted late).
- `python results.py` grades the archive against final box scores and writes
  `projections/results.html`:
  - **Week review**: tiles (lines graded, % inside 80% range / middle half, beat the
    median, TD scorers vs expected), each game's score vs projection, every player's
    projected range with the actual result marked, and the week's biggest misses with
    the likely cause (targets/carries, yards per touch, snaps, team pass volume).
  - **What to improve**: season-to-date suggested fixes (only when the evidence is beyond
    normal error), calibration by stat and position, volume-vs-efficiency split of
    yardage misses, anytime-TD calibration, team scoring vs Vegas, matchup-flag check.
- The site mirrors `projections/`: `index.html` (this week), `results.html`, `weeks/`.

## Matchups page features

- **Depth labels** on every player from the latest ESPN depth chart before the game:
  QB1, RB2, TE1, WR1 · X / WR2 · Z / WR3 · Slot (`boxscore/matchups.py: depth_labels`).
- **Defense vs position** tab: per-game stats and PPR points each defense allowed to
  QBs, RBs, WRs, slot WRs, outside WRs (alignment from depth charts, 2025 on) and TEs,
  this season raw plus an adjusted view (recency-weighted, shrunk by 4 pseudo-games).
- **Matchup flags** (▲ favorable / ▼ tough) for players with a real role, when the
  adjusted rate is 12%+ from average: fantasy points allowed to the position/alignment,
  deep threats (high aDOT) vs deep-pass defense, lead backs vs yards/carry allowed,
  pass-catching backs vs RB targets allowed, sack-prone QBs vs strong pass rushes.
  Backtested on 2025–26: flagged players did not beat their projections more often than
  unflagged ones (favorable −4.1% vs −2.1% overall, tough −1.1%, ±2.7%), because the
  projections already price the opponent. Treat flags as explanation, not extra edge.

## Opposing defense

What's in, all as recency-weighted priors of the opponent, fitted on history:
completion %, yards/attempt, yards/carry, sack rate, INT rate and scramble rate
allowed; how much opponents pass against them (pass vs. run funnel); plays allowed;
and a light target-share funnel by receiver position (WR/TE/RB, strength 0.25).
The Vegas spread and total also carry the market's read on each defense.

Tested and left out because they made out-of-sample predictions worse on 2025:
- blitz rate, pass rushers and box count (FTN charting); already reflected in the
  results-based priors above
- yards-per-target funnels by position; noise beyond the overall pass-defense priors

Not available live: man/zone and coverage shells (nflverse participation data
covers 2024–25 but hasn't been published for 2026).

## Known limitations

- **QB rushing TDs run ~5 pts low** (sneak/goal-line roles are under-captured).
- **Carry ranges are slightly wide** (84% inside the 80% interval), so carry overs/unders are a bit conservative.
- **League-level shifts between seasons are only learned once games are played**
  (2025's passing drop made early-2025 receiving projections about 3% high).
- **Starters' snap roles**: a player returning from injury isn't included unless he's
  on the current depth chart or you add him with an `in` override.
- **Sim points run ~0.5 above Vegas implied**, which matches actual 2025–26 scoring.
- **Kneel-downs** are modeled (official rushing stats include them); spikes are ignored.

**2026-10-01, first TD scorer** (`project.py first_td`). In each simulated game every TD
(both offenses, plus defense/special-teams TDs at the league rate of 0.27 per game) is
equally likely to come first; a team's first-TD chance goes to its listed players by their
share of its TDs. Walk-forward backtest 2024–26 (618 games): first-TD scorers 269 predicted vs
269 actual (2024) and 315 vs 313 (2025–26); calibrated by bucket within about a point; the
top pick per game scored first 14.1% (predicted 15.0%); defense/ST first TD 5.1% vs 5.0%. The
sim's "unlisted player" TD bucket (kept for anytime-TD calibration) is left out here: it
almost never scores first. Shown in the Touchdowns table and the player board.

**2026-10-02, three weeks into 2026: what would make it more accurate?** 2026 weeks 1–3:
80.2% inside the 80% range, 50.0% above the median, team points error 7.97 (Vegas 8.04).
Walk-forward backtest 2024–26 with the current model:
- **Not adopted: position corrections** (`pipeline.RB_TGT_MULT`, `RB_YPR_MULT`, `QB_YPC_MULT`,
  `QB_SCR_MULT`, left at 1.0). On props-graded rows RB targets/receiving yards looked
  over-projected (−4% to −13%) and QB rushing yards under-projected (+7–8%) every season,
  but graded on every player the biases mostly vanish (2025–26: RB targets −0.7%, QB rushing
  +5%): it was selection, since players projected just over a prop floor regress. The
  corrections overshot and made 2025–26 scores worse. Use `--all-rows` when tuning anything
  that moves volume.
- **Not adopted: faster/slower memory** (`LEAGUE_HL` 128/512, `TEAM_CARRY` .3/.6,
  `PLAYER_CARRY` .25/.55, `PLAYER_HL` 3.5/7, `TEAM_HL` 4/9): all within noise of the
  current settings on 2024; current settings best or tied on passing yards and TDs.
- **2026 pass/rush TD split** (67% of TDs through the air in weeks 1–3) is inside the usual
  early-season range (57–68%, 2022–25) and regressed every past season; no change.
- **Injury designations** (2025+ only): Questionable players who played were 3% under
  projection (n≈150 player-games, not significant); teammates of WR/TE/RB ruled Out were
  on projection (the active-list renormalization already handles vacated volume). Recheck
  at midseason.

**2026-10-02, coverage scheme, blitz and separation** (tested against the 2024–26 walk-forward
backtest; a slope of 1 per unit of matchup factor, or a consistent sign per 1 sd, would be needed)
- **Not adopted: man vs zone.** Coverage per play from nflverse participation (FTN; 2023–25
  only, published after each season, so live use needs last season's tendencies). Matchup
  factor = receiver's target share and yards per target vs man and vs zone (shrunk) under the
  defense's man rate. It moves expectations only ±3% (sd); slopes on targets −0.47±0.54
  (2024), +0.28±0.31 (2025) with last season's rates; same-season rates (not usable live)
  +0.79±0.91 / +1.16±0.93. League man rate also swings by year (42% / 48% / 31%).
- **Not adopted: blitz** (FTN charting, in-season). Opponent blitz rate: slopes flip sign by
  season for QB and receiver stats. QB-vs-blitz matchup (defense blitz rate × QB's EPA split
  vs blitz): passing yards −0.006/−0.052/−0.034 per sd, the wrong direction for the idea and
  only 2025 clear of noise.
- **Not adopted: receiver separation** (Next Gen Stats avg separation; weekly rows exist only
  when a receiver got enough targets that week, so features must use earlier games only, or
  they leak the outcome). Receiver's separation vs position: slopes −0.03 to +0.01 (2024–25);
  separation a defense allows vs receivers' norms: +0.061/−0.016/+0.039 on yards. The model's
  target share, catch rate and yards-per-catch histories already carry what these measure.
- Data cached in `data_cache/` (participation 2023–25, NGS receiving); NGS needs the combined
  `ngs_receiving.parquet` file, the per-season names in `build_nfl_db.py` don't exist.

**2026-10-04, Model agreement tab.** On 16,862 matched 2025–26 player lines, agreement between
the Vegas-anchored and market-blind models did not mean outperformance (players hit the shared
projection, +1%). When they disagreed the result landed between them, closer to Vegas. The tab
(`report.agreement`) shows each upcoming line's two medians and a calibrated chance to beat
the Vegas-model median: P_vegas(over) + 0.45 × (P_blind(over) − P_vegas(over)); weight fit on
2025, 2026 log-loss 0.6903 → 0.6893. Rows where the blind model is 5+ points more bullish
(green) or bearish (red) are highlighted; when it was higher, players beat the Vegas median
49–50% vs 44–48% otherwise. Max ~55%: a lean, not a lock. `update.ps1` rebuilds the main page
after the blind run so the tab is current.

## Website structure (2026-10-05)
`site.py` builds everything in `site/` (published to GitHub Pages):
- Two-level navigation on every page: sport tabs (NFL, NHL, NBA, MLB; "soon" until live),
  then the selected sport's pages; "How to read" links to `/guide.html`.
- Home: three start-here steps, a "Live now" card per live sport (top picks, track record,
  links to each page with a one-line description), "Coming next" cards for upcoming sports.
- `/guide.html`: the basics, odds and value, NFL terms, NHL terms, how accuracy is checked.
- A dismissible "New here?" strip on every page (remembered per browser).
- Phone layout: every page fits a 390px screen; wide tables, tab bars and the game list
  scroll inside their own box.

**Adding a sport (NBA, MLB):** set its `SPORTS` entry to `status="live"` with its pages, copy
its generated pages in `main()` with `inject(src, dst, depth, "sport:pagekey")`, add a
home-card function to `HOME_CARDS`, and add its terms to `guide()`. Until then its `/nba/`
or `/mlb/` page is a preview built from `PLANS`.

## Self-learning (2026-10-05)
- **Every run:** priors absorb each new game; the NFL team model refits on all games including
  this season; the NHL goal model refits on the last 3 seasons plus this season so far
  (`hockey/project.py FIT_SEASONS`; in-season refit tested at 6 checkpoints in 2024–26:
  better at 5, ~−0.00003 log-loss; weighting this season double was clearly worse).
- **Weekly self-tuning, `learn.py` (Tuesday 10:00, `update.ps1 -Learn`):** tries 4 settings per
  sport (rotating through `TUNABLE`), each one step up and down, scored with the walk-forward
  backtest on two windows (last season + this season once it has enough games). Adopts at most
  one change, only if it improves BOTH windows (NFL: avg −0.15% across target/catch/yardage/
  carry quantile scores and TD log-loss, no stat worse by >1%, 80% range still 76–84%; NHL:
  log-loss −0.00004 in each). Every player graded (no props floor). Adopted values live in
  `model_params.json` (applied by `learned.py`); every run is logged in `learning_log.json` and
  shown at `/updates.html`. Delete an entry in `model_params.json` to revert it.
- First dry runs (2026-10-05) adopted nothing: best NFL candidate (catch-rate shrinkage 40 → 30)
  improved both windows by only 0.03–0.04%; best NHL ones by < 0.00004.

## Running in the cloud (2026-10-05)
Everything runs on GitHub Actions in the site repo (TheCanadIndian/nfl-sim-sheet), so the PC
can be off. Layout: the repo root is the published site; `engine/` holds the code and the
state that must persist (frozen pregame projections, grades, overrides, learned settings,
logs). Large rebuildable data (nfl.db, hockey/nhl.db, hockey/raw) lives in the Actions cache,
seeded once from the `data-seed` release; if the cache is ever evicted it's reseeded/rebuilt.
- Workflows, one per sport (independent schedules, queues and caches): `nfl.yml` (the old Task
  Scheduler times + Sun 19:15 SNF; Tue 10:00 self-tuning), `nhl.yml` (30-min check; Tue 10:30
  self-tuning), `deploy.yml` (publishes the latest site; triggered by each sport's run and by
  pushes from the PC). Shared steps: `.github/actions/run-sport`. Times are UTC for Eastern
  daylight time; bump the hours by 1 after Nov 1 if exact ET timing matters.
- `run.py` is the cross-platform runner (`nfl [--news]`, `nhl [--auto]`, `learn --sport X`).
- News checks run in the cloud only if an `ANTHROPIC_API_KEY` repo secret is added; otherwise
  those runs are plain updates.
- Working on the PC: `python sync_engine.py pull` (get the cloud's latest state) before editing,
  `python sync_engine.py push` to send code changes up. Remove the local Task Scheduler tasks
  (`install_schedule.ps1 -Remove`) so the PC and the cloud don't both publish.
- Run by hand: GitHub → Actions → nfl / nhl → Run workflow (pick a mode).

## Prediction markets (2026-10-05)
`markets.py <sport>` pulls Kalshi's public market data (no account) for every market we also
project (moneyline, total, anytime/first TD and goal scorers, player yards/receptions,
NBA points/rebounds/assists/threes), matches it to our projections by game (event ticker) and
player name, and records bid/ask, sizes, volume, open interest and our edge after the taker fee
(~7% x p x (1-p)). High-variance markets (scorer markets, YES at 25 cents or less) get the full
order book: dollars within 1/3/5 cents of the best ask and bid, totals, top levels. Runs after
each sport's update in the cloud; snapshots in markets/<sport>_<date>.json (state); shown at
/markets.html. Big edges in deep, active markets usually mean news the model doesn't have.
Next: grade model vs market on the last pregame snapshot.
