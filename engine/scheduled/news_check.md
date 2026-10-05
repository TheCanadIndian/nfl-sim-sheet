You are running unattended on a schedule in D:\football to update NFL box-score
projections with late injury news. Nobody will answer questions. Keep it short:
at most 6 web searches and 6 page fetches.

The brief below lists the target week, the games not yet kicked off, each game's
named starting QB, the official injury report, and the current overrides file.

1. Search for news from the last 24-48 hours that the brief does not already
   reflect, for these games only:
   - starting QB changes (injury, benching, IR)
   - QB/RB/WR/TE ruled out, placed on IR, suspended, or inactive
   - questionable players now confirmed as playing (no change needed) or out
   Only act on reports dated this week of the current season. Search results
   often mix in older seasons; check the date on the page before trusting it.
   Ignore rumors and "game-time decision" items that aren't resolved.

2. Update the overrides file named in the brief (create it if missing). Format:
       team,player,action
   action is out, in, or starter (starter = starting QB). Use full player names
   as they appear on team rosters and nflverse team codes (LA = Rams, LAC,
   LV, WAS, JAX, ...). Keep existing rows unless news reverses them. Above each
   new row add a comment line: # <source site>, <date>: <one-line reason>
   Don't add players already Out/Doubtful on the official report.

3. Run:  .\football\Scripts\python.exe project.py
   If it prints "no player named ... -- skipped", fix the name and rerun once.

4. Reply with at most 8 lines: what changed in overrides (with sources) and
   whether project.py ran cleanly. Don't try to publish anything; the local
   page (projections\latest.html) is the output.

BRIEF:
