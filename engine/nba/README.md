# NBA player model (2026-10-05)

Data: ESPN public API (`fetch.py`): schedule, box scores with minutes (2022-23 on), pregame
spread/total (summary pickcenter; core odds feed for seasons the summaries lack), current
rosters (follow trades/signings) and the injury report. stats.nba.com / cdn.nba.com block scripts.

Model (`model.py`): minutes (short/long memory blended by games this season, normalized so each
team adds up to a full game; live, each player counts by how often he plays when available) ×
per-minute rates (recency-weighted, light shrink toward position) × opponent defense vs position
(G/F/C, ^0.5) × team scale (Vegas: implied points from spread+total; blind: own offense vs
opponent defense). Simulation: minutes normal, stats negative binomial with dispersion growing
with projection size.

Backtest (`backtest.py`, walk-forward, players who played): 2024-25 and 2025-26, both models:
minutes and all stats within ~±1% (steals +4%); every stat beats a 10-game average; points
80% inside the 80% range, by tier .77/.82/.83 after tuning (K_SCALE 0.05: full shrink under-
projected stars 11% and over-projected bench 12%; DISP pts 3 with DISP_GAMMA 1.5). Team points
MAE: Vegas implied 9.03, blind 9.52.

Live: `project.py` (next slate, both models, rosters + injuries, Out minutes reallocated),
`results.py` (nightly grading). Cloud: `.github/workflows/nba.yml` (10:00, 13:00, 17:00, 18:30,
21:00 ET). Open items: validate the blind-lean chip and minutes-trend tags on NBA data once
the season starts; NBA self-tuning settings for learn.py.
