#!/usr/bin/env python3
"""Two-branch publishing (owner 2026-11-11: the repo had grown to 3.6 GB, ~165 MB a day, because every run committed pages,
engine state and odds history to main every ~15 min).

  main  keeps code, workflows, docs and the research tape WITH history: markets/*.json except hist_*, and engine/parlays/**
        (research/ reads the git history of props / live / flags / books / card / day files).
  live  holds everything else a run produces (pages, engine state, saved draws and projections, odds history ...) as ONE
        commit that every publish replaces, so it never accumulates history.

A run's workspace = a main checkout with live laid over it (`start`). At the end `commit` sends main paths to main (commit,
pull --rebase, push, as before) and lays the run's changed live files onto the NEWEST live tree, pushed as a parentless commit
with --force-with-lease (retried), so runs that publish at the same time keep each other's files (last writer wins per file,
as with the old pull --rebase -X theirs). The deploy workflow publishes main + live.

    python engine/publish.py start              # after checkout, from the repo root
    python engine/publish.py commit "message"   # publish main + live paths
    python engine/publish.py classify           # counts per side (a check)
LIVE_BRANCH env: the live branch name (default 'live'; tests use another).
"""
import fnmatch
import json
import os
import subprocess
import sys
import tempfile
import time

LIVE = os.environ.get("LIVE_BRANCH", "live")
META = {".gitignore", ".gitattributes", "CLAUDE.md", "README.md", "engine/.gitignore"}


def root():
    return subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True).stdout.strip() or os.getcwd()


ROOT = root()
try:
    sys.path.insert(0, os.path.join(ROOT, "engine"))
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from sync_engine import CODE                                   # the engine's code files (same list the PC sync uses)
except Exception:                                                  # noqa: BLE001
    CODE = ["*.py", "*.js", "*.ps1", "README.md", "requirements.txt"]
BASE = os.path.join(ROOT, ".git", "live_base.json")


def is_main(p):
    """True for paths kept on main (with history); everything else is a live path."""
    if p.startswith(".github/") or p in META or p.startswith("engine/parlays/"):
        return True
    if p.startswith("markets/") and p.count("/") == 1 and p.endswith(".json") and not p.startswith("markets/hist_"):
        return True
    return p.startswith("engine/") and any(fnmatch.fnmatch(p[7:], g) for g in CODE)


def git(*a, inp=None, env=None, check=True):
    r = subprocess.run(["git", "-C", ROOT, *a], input=inp, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       env=dict(os.environ, **(env or {})))
    if check and r.returncode:
        raise RuntimeError(f"git {' '.join(a[:3])}: {r.stderr.strip()[:400]}")
    return r


def remote_live():
    out = git("ls-remote", "origin", f"refs/heads/{LIVE}").stdout.split()
    return out[0] if out else None


def fetch_live():
    sha = remote_live()
    if sha:
        git("fetch", "-q", "--depth=1", "origin", f"+refs/heads/{LIVE}:refs/remotes/origin/{LIVE}")
    return sha


def tree_files(sha):
    out = {}
    for line in git("ls-tree", "-r", "-z", sha).stdout.split("\0"):
        if line:
            meta, path = line.split("\t", 1)
            out[path] = meta.split()[2]
    return out


def start():
    """Lay the live branch's files over the working tree (main's index untouched) and remember them as the base."""
    sha = fetch_live()
    if not sha:
        json.dump(dict(sha=None, t=None, files={}), open(BASE, "w"))
        print(f"publish: no '{LIVE}' branch yet (nothing to overlay)")
        return
    with tempfile.TemporaryDirectory() as td:
        env = {"GIT_INDEX_FILE": os.path.join(td, "index")}
        git("read-tree", sha, env=env)
        git("checkout-index", "-a", "-f", env=env)
    t = git("log", "-1", "--format=%ct", sha).stdout.strip()
    files = tree_files(sha)
    json.dump(dict(sha=sha, t=int(t) if t else None, files=files), open(BASE, "w"))
    print(f"publish: '{LIVE}' {sha[:9]} overlaid ({len(files)} files)")


def candidates():
    """Paths that differ from main's index: modified, deleted, untracked (not ignored)."""
    out = set()
    for flag in ("-m", "-d", "-o"):
        args = ["ls-files", "-z", flag] + (["--exclude-standard"] if flag == "-o" else [])
        out |= {p for p in git(*args).stdout.split("\0") if p}
    return out


def publish_main(msg):
    paths = sorted(p for p in candidates() if is_main(p))
    if not paths:
        return "main: no changes"
    for i in range(0, len(paths), 200):
        git("add", "-A", "--", *paths[i:i + 200])
    if git("diff", "--cached", "--quiet", check=False).returncode == 0:
        return "main: no changes"
    git("commit", "-q", "-m", msg)
    for i in range(6):
        r = git("pull", "-q", "--rebase", "--autostash", "-X", "theirs", check=False)
        if os.path.isdir(os.path.join(ROOT, ".git", "rebase-merge")) or os.path.isdir(os.path.join(ROOT, ".git", "rebase-apply")):
            git("rebase", "--abort", check=False)
            return f"main: rebase conflict, not pushed ({r.stderr.strip()[:200]})"
        if git("push", "-q", check=False).returncode == 0:
            return f"main: pushed {len(paths)} paths"
        time.sleep(5 * (i + 1))
    return "main: push failed 6 times"


def publish_live(msg, prefix=""):
    """prefix: only live paths under it (the PC's code sync publishes engine/ state only, never stray local test pages)."""
    base = json.load(open(BASE)) if os.path.exists(BASE) else dict(files={})
    bfiles = base.get("files") or {}
    cur = sorted(p for p in git("ls-files", "-z", "-o", "--exclude-standard").stdout.split("\0") if p and not is_main(p))
    cur += [p for p in git("ls-files", "-z").stdout.split("\0") if p and not is_main(p)]      # live paths main still tracks (pre-split)
    cur = sorted(p for p in set(cur) if p.startswith(prefix))
    hashes = git("hash-object", "--stdin-paths", inp="\n".join(cur) + "\n").stdout.split() if cur else []
    changed = [p for p, h in zip(cur, hashes) if bfiles.get(p) != h]
    present = set(cur)
    deleted = [p for p in bfiles if p.startswith(prefix) and p not in present and not is_main(p) and not os.path.exists(os.path.join(ROOT, p))]
    if not changed and not deleted:
        return "live: no changes"
    blobs = git("hash-object", "-w", "--stdin-paths", inp="\n".join(changed) + "\n").stdout.split() if changed else []
    info = "".join(f"100644 {b}\t{p}\n" for p, b in zip(changed, blobs)) + "".join(f"0 {'0' * 40}\t{p}\n" for p in deleted)
    ident = {"GIT_AUTHOR_NAME": "sim-sheet-bot", "GIT_AUTHOR_EMAIL": "sim-sheet-bot@users.noreply.github.com",
             "GIT_COMMITTER_NAME": "sim-sheet-bot", "GIT_COMMITTER_EMAIL": "sim-sheet-bot@users.noreply.github.com"}
    for i in range(6):
        latest = fetch_live()
        with tempfile.TemporaryDirectory() as td:
            env = {"GIT_INDEX_FILE": os.path.join(td, "index")}
            if latest:
                git("read-tree", latest, env=env)
            else:
                git("read-tree", "--empty", env=env)
            git("update-index", "--index-info", inp=info, env=env)
            tree = git("write-tree", env=env).stdout.strip()
        commit = git("commit-tree", tree, "-m", msg, env=ident).stdout.strip()
        r = git("push", "-q", f"--force-with-lease=refs/heads/{LIVE}:{latest or ''}", "origin", f"{commit}:refs/heads/{LIVE}", check=False)
        if r.returncode == 0:
            files = tree_files(commit)
            json.dump(dict(sha=commit, t=int(time.time()), files=files), open(BASE, "w"))
            return f"live: published {len(changed)} changed, {len(deleted)} removed ({commit[:9]})"
        time.sleep(3 * (i + 1))
    raise RuntimeError(f"live: push failed 6 times ({r.stderr.strip()[:200]})")


def classify():
    files = [p for p in git("ls-files", "-z").stdout.split("\0") if p]
    m = sum(map(is_main, files))
    print(f"tracked on this branch: {len(files)}; main paths {m}; live paths {len(files) - m}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "classify"
    if cmd == "start":
        start()
    elif cmd == "commit":
        args = [a for a in sys.argv[2:] if not a.startswith("--only=")]
        only = next((a[7:] for a in sys.argv[2:] if a.startswith("--only=")), "")
        msg = args[0] if args else "Auto: publish"
        print("publish:", publish_main(msg))
        print("publish:", publish_live(msg, only))
    else:
        classify()
