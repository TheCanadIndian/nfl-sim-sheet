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


def sh(*args, check=False):
    print(f"\n$ {' '.join(args)}", flush=True)
    r = subprocess.run(list(args), cwd=HERE)
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
           "--skip", "ngs_pass", "ngs_rush", "ngs_rec", "player_week")
    else:
        sh(PY, "build_nfl_db.py", "--seasons", str(season_nfl()), "--db", "nfl.db",
           "--skip", "ngs_pass", "ngs_rush", "ngs_rec", "player_week")
    if not (news and news_check()):
        sh(PY, "project.py")
    sh(PY, "project.py", "--blind")
    sh(PY, "project.py")                     # main page picks up this run's blind numbers
    sh(PY, "backfill_missing.py")
    sh(PY, "backfill_missing.py", "--blind")
    sh(PY, "results.py")


def nhl(auto=False):
    if auto:
        rc = sh(PY, "hockey/should_run.py")
        if rc == 10:
            return False
    sh(PY, "hockey/fetch.py", "--seasons", season_nhl())
    sh(PY, "hockey/project.py")
    sh(PY, "hockey/linecheck.py")
    sh(PY, "hockey/results.py")
    sh(PY, "hockey/should_run.py", "--mark")
    return True


def season_nba():
    now = dt.date.today()
    return now.year + 1 if now.month >= 8 else now.year


def nba():
    sh(PY, "nba/fetch.py", "--seasons", str(season_nba()))
    sh(PY, "nba/project.py")
    sh(PY, "nba/results.py")
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
    ap.add_argument("mode", choices=["nfl", "nhl", "nba", "learn"])
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
    else:
        learn(a.sport)
    if did:
        sh(PY, "site.py", check=True)


if __name__ == "__main__":
    main()
