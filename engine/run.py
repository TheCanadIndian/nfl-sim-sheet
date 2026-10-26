#!/usr/bin/env python3
"""
Cross-platform pipeline runner (used by the GitHub Actions workflows; update.ps1 is the
Windows equivalent). Builds everything and the site; committing/publishing is left to the
caller (the workflow commits the repo, update.ps1 pushes site/).

    python run.py nfl            # refresh data, both NFL models, archives, results
    python run.py nfl --news     # same, with the Claude news check first (needs ANTHROPIC_API_KEY
                                 # and the `claude` CLI; skipped otherwise)
    python run.py nhl --auto     # quick check; full NHL update only when useful (should_run.py)
    python run.py nhl            # full NHL update
    python run.py learn --sport nfl   # weekly self-tuning for one sport, then rebuild that sport
Each sport runs on its own (separate workflows, caches and schedules); site.py rebuilds the
shared pages (home, guide, updates) from whatever each sport last published.

Site output goes to $SIM_SITE (default ./site).
"""

import argparse
import datetime as dt
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable


# Steps whose crash leaves a page stale (owner 2026-10-23: the NHL page froze overnight while every run showed green).
# The run still finishes and publishes what worked; the failures go to a file outside the repo and the workflow's
# last step (run-sport action, "Fail the run if a page build crashed") turns the run red so GitHub emails.
CRITICAL = ("project.py", "page.py", "results.py", "boxgrade.py")
FAILS = os.path.join(os.environ.get("RUNNER_TEMP") or __import__("tempfile").gettempdir(), "sim_run_failures.txt")


def sh(*args, check=False):
    print(f"\n$ {' '.join(args)}", flush=True)
    r = subprocess.run(list(args), cwd=HERE)
    if r.returncode and len(args) > 1 and str(args[1]).endswith(CRITICAL):
        msg = f"{' '.join(str(a) for a in args[1:])} exited {r.returncode}"
        print(f"::error::page build failed: {msg}", flush=True)
        with open(FAILS, "a", encoding="utf-8") as f:
            f.write(msg + "\n")
    if check and r.returncode:
        sys.exit(r.returncode)
    return r.returncode


def season_nfl():
    now = dt.date.today()
    return now.year if now.month >= 3 else now.year - 1


def season_nhl():
    now = dt.date.today()
    y = now.year if now.month >= 8 else now.year - 1
    return f"{y}{y + 1}"


def news_check():
    claude = shutil.which("claude")
    if not claude or not os.environ.get("ANTHROPIC_API_KEY"):
        print("news check skipped (no claude CLI or ANTHROPIC_API_KEY)")
        return False
    brief = subprocess.run([PY, "scheduled/week_context.py"], cwd=HERE, capture_output=True, text=True).stdout
    prompt = open(os.path.join(HERE, "scheduled", "news_check.md"), encoding="utf-8").read()
    prompt = (prompt.replace("D:\\football", HERE).replace(".\\football\\Scripts\\python.exe project.py", f"{PY} project.py")
              .replace("projections\\latest.html", "projections/latest.html"))
    r = subprocess.run([claude, "-p", "--allowedTools", "Bash", "Read", "Write", "Edit", "Glob", "Grep", "WebSearch", "WebFetch"],
                       input=prompt + "\n" + brief, text=True, cwd=HERE)
    print(f"news check exit {r.returncode}")
    return r.returncode == 0


def nfl(news=False):
    if not os.path.exists(os.path.join(HERE, "nfl.db")):
        # first run without a cached database: build the seasons the models use
        y = season_nfl()
        sh(PY, "build_nfl_db.py", "--seasons", *map(str, range(y - 4, y + 1)), "--db", "nfl.db",
           "--skip", "ngs_pass", "ngs_rush", "ngs_rec")      # player_week: weekly stats for outperf.py
    else:
        sh(PY, "build_nfl_db.py", "--seasons", str(season_nfl()), "--db", "nfl.db",
           "--skip", "ngs_pass", "ngs_rush", "ngs_rec")      # player_week: weekly stats for outperf.py
    if not (news and news_check()):
        sh(PY, "project.py")
    sh(PY, "project.py", "--blind")
    sh(PY, "project.py")                     # main page picks up this run's blind numbers
    sh(PY, "backfill_missing.py")
    sh(PY, "backfill_missing.py", "--blind")
    sh(PY, "results.py")
    sh(PY, "boxgrade.py", "nfl")             # projected box scores graded (nfl/boxes.html)
    sh(PY, "shadow.py")                      # shadow tests: failed changes graded beside the live numbers (shadow.html)
    sh(PY, "markets.py", "nfl")
    sh(PY, "grade_markets.py", "nfl")
    sh(PY, "profit.py", "rows", "nfl")          # Profit page: picks under the site's rules
    sh(PY, "patterns.py")                    # prop patterns: tracked record


def nhl(auto=False):
    if auto:
        rc = sh(PY, "hockey/should_run.py")
        if rc == 10:
            return False
    sh(PY, "hockey/fetch.py", "--seasons", season_nhl())
    sh(PY, "hockey/project.py")
    sh(PY, "hockey/linecheck.py")
    sh(PY, "hockey/results.py")
    sh(PY, "shadow.py")                      # shadow tests (shadow.html)
    sh(PY, "markets.py", "nhl")
    sh(PY, "grade_markets.py", "nhl")
    sh(PY, "profit.py", "rows", "nhl")
    sh(PY, "hockey/should_run.py", "--mark")
    return True


def season_nba():
    now = dt.date.today()
    return now.year + 1 if now.month >= 8 else now.year


def nba():
    sh(PY, "nba/fetch.py", "--seasons", str(season_nba()))
    sh(PY, "nba/project.py")
    sh(PY, "nba/results.py")
    sh(PY, "boxgrade.py", "nba")             # projected box scores graded (nba/boxes.html)
    sh(PY, "markets.py", "nba")
    sh(PY, "grade_markets.py", "nba")
    sh(PY, "profit.py", "rows", "nba")
    sh(PY, "nba/should_run.py", "--mark")          # the 15-min check in markets.yml starts the next run (same rules as NHL)
    return True


def mlb():
    sh(PY, "mlb/fetch.py", "--seasons", str(dt.date.today().year))
    sh(PY, "mlb/project.py")
    sh(PY, "mlb/results.py")
    sh(PY, "markets.py", "mlb")
    sh(PY, "grade_markets.py", "mlb")
    sh(PY, "profit.py", "rows", "mlb")
    return True


def learn(sport):
    sh(PY, "learn.py", sport)
    if sport == "nfl":
        sh(PY, "project.py")
        sh(PY, "project.py", "--blind")
        sh(PY, "project.py")
        sh(PY, "results.py")
    else:
        sh(PY, "hockey/project.py")
        sh(PY, "hockey/results.py")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["nfl", "nhl", "nba", "mlb", "learn"])
    ap.add_argument("--news", action="store_true")
    ap.add_argument("--auto", action="store_true")
    ap.add_argument("--sport", choices=["nfl", "nhl"], default="nfl")
    a = ap.parse_args()
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    did = True
    if a.mode == "nfl":
        nfl(a.news)
    elif a.mode == "nhl":
        did = nhl(a.auto)
    elif a.mode == "nba":
        did = nba()
    elif a.mode == "mlb":
        did = mlb()
    else:
        learn(a.sport)
    if did:
        sh(PY, "site.py", check=True)


if __name__ == "__main__":
    main()
