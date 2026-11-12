# Paper account

Claude's paper-money parlay account, opened 2026-11-12 with $250 (the owner, 2026-11-11: "papertrade a betting account... you can
only use parlays... determine your own bet size and daily limit... everyday I need reasons why the bet was taken").
Page: /paper.html (one collapsible block per day). Code: `engine/paper.py`. No real money; no bets are placed anywhere.

## Rules (enforced by `paper.py record`)
- **Overs on player props only** (the owner, 2026-11-12: "Only overs only player props"): no unders, no game totals or moneylines.
  `paper.py slate` lists only player-prop overs and `record` refuses anything else (`OVERS_PROPS_ONLY`).
- **Parlays only:** 2-4 legs, one leg per game, every leg at the SAME book (one ticket at Hard Rock, Novig or Kalshi).
- **Prices:** whatever that book shows in the site's market files when the bet is recorded (Kalshi = its ask + the 7% fee).
  A leg whose game starts within 5 minutes, or that the book doesn't price, can't be recorded.
- Each leg priced 15-80% (implied, fee in); no parlay paying over 30x.
- **Stake:** 1 unit = 2% of the bankroll at the day's first bet, rounded to the dollar, $2-$10 ($5 at the start).
  Parlays paying more than 6x take half a unit. Below $175 the unit is 1%; below $100 no new bets until a review.
- **Daily limit:** 4 parlays and 4 units (8% of the bankroll); a leg in at most 2 of the day's parlays. No bet is fine.
- **Reasons:** every parlay has a "why", and every leg its own "why".
- **Grading:** from box scores, like the site's parlay record (`parlays.game_for` / `settle`): a void (didn't play) or pushed leg
  drops out, as at the books; a parlay with only void / pushed legs returns the stake.

## Files
| path | written by | what |
|---|---|---|
| `paper/bets/<ET date>.json` | `paper.py record` (the routine) | the day's bets, legs, prices, reasons |
| `paper/results.json` | `paper.py grade` (markets.yml) | results and returns by bet id |
| `paper/journal/<ET date>.md` | the routine | questions asked to research agents, answers, the plan |
| `paper/lessons.md` | the routine | running lessons |

The routine pushes to the `claude/paper` branch; markets.yml (`paper.py pull-branch`) merges those files into main every ~15
minutes, grades, and rebuilds /paper.html.

## Commands (from the repo root)
```
python engine/paper.py report                   # bankroll, record, pending, recent legs
python engine/paper.py slate --out=/tmp/slate.json   # every priced leg now: fair, our chance, edge, site signals
python engine/paper.py record /tmp/picks.json --dry  # check; then without --dry to record
python engine/paper.py pull-branch              # merge what was pushed to claude/paper
```
picks.json: `{"parlays": [{"book": "Novig", "why": "...", "legs": [{"id": "<id from the slate>", "why": "..."}]}]}`
