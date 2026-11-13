#!/usr/bin/env python3
"""
Keep this folder and the cloud engine (site/engine in the GitHub repo) in step.

    python sync_engine.py pull    # get the cloud's saved state (projections, grades, learned settings)
    python sync_engine.py push    # send code changes (and state) up to the repo, commit and push
    python sync_engine.py code    # copy code edited in the repo (from another device) down to this PC

The cloud runs (GitHub Actions) work inside the repo's engine/ folder; this PC keeps the
same layout in D:\\football. Code always flows PC -> cloud; state (frozen pregame projections,
graded results, overrides, learned settings, logs) is pulled before you work and pushed with
your changes. Large rebuildable data (databases, raw API caches) never goes in the repo.
"""

import fnmatch
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.join(HERE, "site")
ENGINE = os.path.join(REPO, "engine")
GIT = shutil.which("git") or r"C:\Program Files\Git\cmd\git.exe"

CODE = ["*.py", "*.js", "*.ps1", "README.md", "requirements.txt", "*.example.csv", "boxscore/*.py",
        "hockey/*.py", "hockey/*.js", "hockey/README.md", "hockey/usage_params.json", "hockey/sog_params.json", "hockey/shiftsim_params.json", "scheduled/*", "nba/*.py", "nba/*.js", "nba/README.md", "nba/archetypes.json", "nba/corr_params.json", "nba/threes_params.json", "nba/steps_params.json", "mlb/*.py", "mlb/params.json", "outperf_params.json",
        "profit_restore.json"]                                   # profit tags lost to the pre-split commit race (2026-11-12), merged by profit.tags
STATE = ["model_params.json", "learning_log.json", "overrides/*.csv", "hockey/overrides.csv",
         "hockey/.last_run.json", "projections/*", "projections/weeks/*", "projections/blind/*",
         "projections/blind/weeks/*", "hockey/projections/*", "nba/projections/*", "nba/.last_run.json", "nba/lineups_log.json", "markets/*.json",
         "mlb/projections/*", "parlays/nhl/*.json", "parlays/nfl/*.json", "parlays/nba/*.json", "parlays/multi/*.json", "parlays/learn/*.json", "parlays/learn/nfl/*.json", "parlays/learn/nba/*.json", "parlays/learn/nhl/*.json", "parlays/slate/*.json", "parlays/slate/meta/*.json", "parlays/slate/cands/*.json"]
SKIP = ["*.html", "*_pregame_*", "backtest_rows.csv"]     # generated pages / one-off files


def files(root, patterns):
    out = []
    for pat in patterns:
        d, base = os.path.split(pat)
        full = os.path.join(root, d)
        if not os.path.isdir(full):
            continue
        for f in os.listdir(full):
            p = os.path.join(full, f)
            if os.path.isfile(p) and fnmatch.fnmatch(f, base) and not any(fnmatch.fnmatch(f, s) for s in SKIP):
                out.append(os.path.relpath(p, root))
    return sorted(set(out))


def copy(src_root, dst_root, rels):
    n = 0
    for r in rels:
        s, d = os.path.join(src_root, r), os.path.join(dst_root, r)
        if os.path.exists(d) and open(s, "rb").read() == open(d, "rb").read():
            continue
        os.makedirs(os.path.dirname(d), exist_ok=True)
        shutil.copy2(s, d)
        n += 1
    return n


def git(*a):
    return subprocess.run([GIT, "-C", REPO, *a]).returncode


def overlay():
    """Lay the live branch (pages + engine state since 2026-11-11, publish.py) over the repo's working tree."""
    subprocess.run([sys.executable, os.path.join(HERE, "publish.py"), "start"], cwd=REPO)


def live_time():
    try:
        return json.load(open(os.path.join(REPO, ".git", "live_base.json"))).get("t")
    except (OSError, ValueError):
        return None


def main(cmd, msg=None):
    git("pull", "--rebase", "--quiet")
    overlay()
    if cmd == "pull":
        n = copy(ENGINE, HERE, files(ENGINE, STATE))
        print(f"pulled {n} state files from the cloud")
        return
    if cmd == "code":                                   # after editing code in the repo from another device
        n = copy(ENGINE, HERE, files(ENGINE, CODE))
        print(f"copied {n} code files from the repo to the PC")
        return
    # state: never push a PC copy over a newer cloud copy (the cloud runs keep updating these)
    # last commit time of every engine/parlays/ path in ONE git log pass (2026-11-12: was one `git log -1` per file, ~30 s on 190 files)
    plog = subprocess.run([GIT, "-C", REPO, "log", "--format=@%ct", "--name-only", "--", "engine/parlays/"], capture_output=True, text=True).stdout
    ptime, cur = {}, None
    for ln in plog.splitlines():
        if ln.startswith("@"):
            cur = ln[1:]
        elif ln.strip() and cur and ln not in ptime:
            ptime[ln] = cur                          # newest first: the first sighting is the last commit
    newer_here, newer_cloud = [], []
    for r in files(HERE, STATE):
        e = os.path.join(ENGINE, r)
        if not os.path.exists(e):
            newer_here.append(r); continue
        rp = "engine/" + r.replace(os.sep, "/")
        if rp.startswith("engine/parlays/"):          # still on main, with history
            t = ptime.get(rp, "")
        else:                                        # on the one-commit live branch: its time is the newest publish of any file
            t = str(live_time() or "")
        # 5 min margin: a commit reaches GitHub after its timestamp, so a pull right then copies older data with a later mtime
        (newer_cloud if t and int(t) + 300 > os.path.getmtime(os.path.join(HERE, r)) else newer_here).append(r)
    if newer_cloud:
        copy(ENGINE, HERE, newer_cloud)
        print(f"kept {len(newer_cloud)} newer cloud state files (copied them to the PC)")
    # code edited in the repo from another device (see CLAUDE.md): keep it rather than overwrite it with an older PC copy
    code, newer_repo = [], []
    for r in files(HERE, CODE):
        e = os.path.join(ENGINE, r)
        if os.path.exists(e) and open(e, "rb").read() != open(os.path.join(HERE, r), "rb").read():
            t = subprocess.run([GIT, "-C", REPO, "log", "-1", "--format=%ct", "--", os.path.join("engine", r)], capture_output=True, text=True).stdout.strip()
            if t and int(t) > os.path.getmtime(os.path.join(HERE, r)):
                newer_repo.append(r); continue
        code.append(r)
    for r in files(ENGINE, CODE):                       # new files added in the repo
        if not os.path.exists(os.path.join(HERE, r)):
            newer_repo.append(r)
    if newer_repo:
        copy(ENGINE, HERE, newer_repo)
        print(f"kept {len(newer_repo)} code files edited in the repo (copied them to the PC): {', '.join(newer_repo)}")
    n = copy(HERE, ENGINE, code) + copy(HERE, ENGINE, newer_here)
    print(f"copied {n} files into engine/")
    # code + research tape -> main (history); engine state -> the live branch (publish.py). 'push' publishes only engine/
    # live paths (never stray local test pages); 'push-all' (the PC failover) publishes every page / data file it rebuilt.
    only = [] if cmd == "push-all" else ["--only=engine/"]
    subprocess.run([sys.executable, os.path.join(HERE, "publish.py"), "commit", msg or "Engine: sync code from PC", *only], cwd=REPO)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "push", sys.argv[2] if len(sys.argv) > 2 else None)
