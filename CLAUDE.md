# Sim Sheet — working on this repo

Sports projection site: https://thecanadindian.github.io/nfl-sim-sheet/ (NFL, NHL, NBA, MLB).
This repo is **public** and GitHub Pages serves its root, so anything committed here (this file included) is world-readable.

## Two places to work

| | Main PC (`D:\football`) | Any other device (this repo, e.g. claude.ai/code or a clone) |
|---|---|---|
| Code | `D:\football\*.py` is the source of truth | `engine/` holds the same code |
| Databases | `nfl.db`, `mlb/mlb.db`, `nba/nba.db`, hockey data | **not in the repo**; the cloud runs keep them in the Actions cache (seed: release `data-seed`) |
| Keys | `D:\football\private\*.txt` | none; the cloud uses GitHub secrets |
| Private tools | `private/` (trackers, Windows scheduled task) | not available |
| Publishing | `python sync_engine.py push`, then run workflows | commit + push `engine/`, then run workflows |

The cloud (GitHub Actions) does all scheduled work. From another device you can still change code and rebuild the site.

### From another device
1. Edit code in `engine/` (same layout as `D:\football`). Don't hand-edit the generated pages (`*/index.html`, `nhl/2026-*.html`, etc.); `engine/site.py` rebuilds them.
2. Test what you can without the databases (syntax, small pure functions). Heavy runs (projections, backtests) belong in the cloud or on the PC.
3. `git pull --rebase` first, since the cloud commits every few minutes. Then commit and push.
4. Run the affected workflow: `gh workflow run nfl.yml` (or `nhl.yml`, `nba.yml`, `mlb.yml`, `markets.yml`). Watch it: `gh run list -L 3`, `gh run watch <id>`.
5. Check the live page (cache-bust: `?x=123`). Pages deploy via `deploy.yml` about a minute after a push.

### Back on the PC afterwards
Run `python sync_engine.py code` to copy code edited in the repo down to `D:\football` before editing there. `push` also refuses to overwrite a repo file committed more recently than the PC copy was last edited, and copies that file down instead.

### On the PC
- `python sync_engine.py pull` gets the cloud's state: frozen projections, grades, learned settings, `markets/*.json`.
- `python sync_engine.py push` sends code + state and commits ("Engine: sync code from PC"). If git is behind: `cd site && git pull --rebase && git push`.
- Python venv: `D:\football\football\Scripts\python.exe`. Shell is Windows (PowerShell + Git Bash).

## Pipeline
- **Schedules:** a Cloudflare Worker (`sim-sheet-cron`, Eastern time) starts the workflows; all also run by hand (`workflow_dispatch`).
- **Sport workflows:** `nfl.yml`, `nhl.yml`, `nba.yml` and `mlb.yml` use the shared action `.github/actions/run-sport` (Python 3.12, `pip install -r requirements.txt`, `python run.py <mode>`).
  - NFL also runs `outperf.py`.
- **`markets.yml` (about every 15 min):** runs `books.py`, `sharp.py` and `watch.py`.
  - It writes `markets/books_<sport>.json`, `props_<sport>.json`, `flags_<sport>.json` and `live_<sport>.json`.
- **Page script:** `mkt.js` loads that market data on each sport's current-slate page only (`<sport>/index.html`; dated pages don't load it). `site.py` copies `mkt.js` and `slip.js` to the root.
- **Builders:** NFL page `report.py` (also the shared CSS), NHL `hockey/project.py` (page "Goals & Shots"), MLB `mlb/page.py`, NBA `nba/`.

## Hard rules
- **Keys:** never in code, commits or pages. Secrets: `SHARPAPI_KEY`, `ODDS_API_KEY`, `UNABATED_API_KEY`. PC copies live in `private/` (never commit `private/`).
- **Unabated (free tier):** every published Gambly link must end `?utm_campaign=thecanadindian`. `books.py` and `sharp.py` refuse to publish otherwise.
- **SharpAPI:** publish only our own inference (Value tags), never its odds or book names.
- **Never automate clicks** on affiliate or bet links.
- **The Odds API:** 500 credits/month. `books.budget_ok` paces it, weighted toward NFL Sundays. Pinnacle NFL props are pulled about 3.5 h before kickoff (about 5 credits/game).
- **UX first:** every site change is checked on phone (~500 px) and desktop before shipping. Prefer clean chips over text clutter.
- **JS inside Python templates:** a straight apostrophe inside a JS string breaks the page. Use ’ in display text.

## Owner's context
- Bets at **Novig, Kalshi, Hard Rock** (the page's "my books" setting, `localStorage simbooks-v1`). Mostly overs, small edges, parlays. Wants fewer long droughts.
- **Prices:** Unabated is primary, with partner links. The Odds API fills in books Unabated lacks (Hard Rock, Pinnacle, …). Pinnacle is a reference only: never shown as a "best price".
- **Pinnacle de-vig:** multiplicative for two-way lines, power for one-way. Alt and mismatched lines are carried over with our distribution's shape (shown with ≈). Comparisons span **all** books, with the owner's books highlighted.

## Model rules that were tested (don't loosen without a new test)
- **NFL props:** chance = 50% ours + 50% market no-vig (Pinnacle when available), with a 5%+ edge required. Yardage / catch props also need the side our median is on and our number 10+ points from the market (exchanges: spread 3¢ or less, price 15¢+); the plain rule lost 5% on Kalshi 2026 weeks 1-5. Track everything on /profit.html. The gap matters more than percentage EV: tight markets need 5+ points, wider ones 8+. Projections run 50,000 sims per game; the build-your-own slip gets 5,000 draws.
- **NFL ladder candidates (experimental, Parlays tab):** players facing a top-8 defense for their position (DvP); YES on Kalshi rungs 2+ above the ~50/50 rung. Kalshi 2026 wk 1-5: cleared 3-8 pts above price in both halves. Tracked on /profit.html (rungs of one ladder are correlated, so read its confidence loosely).
- **NHL anytime goal:** legs only from our top 3 per game by usage rank. Chance = 50% ours + 50% market (Pinnacle no-vig when pulled, else the exchanges), each leg at 30%+. Game totals: `gamesim.REG_SCALE` 0.90. Round robins cut droughts. Shots on goal use a negative binomial (`hockey/sog_params.json`).
- **MLB HR blend** (`mlb/blend.py`):
  - **How to test:** compare feature sets with 5-seed averaged fits. A single fit swings ±0.01 top-3 HR hitters per game.
  - **Bar:** top-3 capture up in every half of two test windows, and log-loss not worse.
  - **Current model:** the swing plane passed. Bullpen, pitch-type miss rates and pull-direction × park failed. The live blend averages 5 seeds (`blend.Avg`).
- **Rejected:** shots/xG/DvP signals for longshot goal pairs made picks worse.
  Goalie save % by shot zone and the team shot-location overlay (2024-26): no better than the model's xG + GSAx ratings, so the NHL Shot map tab is display only. Player soft-spot fit (opponent allows more where he shoots): correlates 0.86 with the opponent's overall shots allowed (already in sog.py); the location-only part predicted neither goals nor shots consistently. Opponent unblocked shots allowed as a goal-model input (`model.USE_OPP_FA`, off): steady weight (~0.95) but log-loss better in only 3 of 4 halves of 2024-26 and top-3 capture down in one; recheck with more 2026-27 games.
