#!/usr/bin/env python3
"""
Keep this folder and the cloud engine (site/engine in the GitHub repo) in step.

    python sync_engine.py pull    # get the cloud's saved state (projections, grades, learned settings)
    python sync_engine.py push    # send code changes (and state) up to the repo, commit and push

The cloud runs (GitHub Actions) work inside the repo's engine/ folder; this PC keeps the
same layout in D:\\football. Code always flows PC -> cloud; state (frozen pregame projections,
graded results, overrides, learned settings, logs) is pulled before you work and pushed with
your changes. Large rebuildable data (databases, raw API caches) never goes in the repo.
"""

import fnmatch
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.join(HERE, "site")
ENGINE = os.path.join(REPO, "engine")
GIT = shutil.which("git") or r"C:\Program Files\Git\cmd\git.exe"

CODE = ["*.py", "*.ps1", "README.md", "requirements.txt", "*.example.csv", "boxscore/*.py",
        "hockey/*.py", "hockey/README.md", "scheduled/*", "nba/*.py", "nba/README.md"]
STATE = ["model_params.json", "learning_log.json", "overrides/*.csv", "hockey/overrides.csv",
         "hockey/.last_run.json", "projections/*", "projections/weeks/*", "projections/blind/*",
         "projections/blind/weeks/*", "hockey/projections/*", "nba/projections/*", "nba/.last_run.json"]
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


def main(cmd):
    git("pull", "--rebase", "--quiet")
    if cmd == "pull":
        n = copy(ENGINE, HERE, files(ENGINE, STATE))
        print(f"pulled {n} state files from the cloud")
        return
    n = copy(HERE, ENGINE, files(HERE, CODE)) + copy(HERE, ENGINE, files(HERE, STATE))
    print(f"copied {n} files into engine/")
    git("add", "-A", "engine", ".github")
    if git("diff", "--cached", "--quiet") != 0:
        git("commit", "--quiet", "-m", "Engine: sync code from PC")
        git("push", "--quiet")
        print("pushed")
    else:
        print("nothing to push")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "push")
