"""
Git helpers: velocity analysis, recent-change detection, repo cloning.
"""

from __future__ import annotations

import re
import subprocess
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

import requests


def clone_repo(url: str, dest: Path) -> Path:
    """
    Clone a GitHub repo URL to *dest* (if not already present).
    Returns the local repo root.
    """
    if dest.exists():
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "clone", "--depth=500", url, str(dest)], check=True)
    return dest


def get_git_log(repo_root: Path, days: int = 30) -> list[dict]:
    """
    Return a list of {file, date, author, message} dicts for the last *days* days.
    Gracefully returns [] if the directory has no git history.
    """
    since = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(repo_root),
                "log",
                f"--since={since}",
                "--name-only",
                "--pretty=format:COMMIT|%H|%ai|%an|%s",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError:
        return []

    entries = []
    current_commit: dict = {}
    for line in result.stdout.splitlines():
        if line.startswith("COMMIT|"):
            parts = line.split("|", 4)
            current_commit = {
                "hash": parts[1],
                "date": parts[2],
                "author": parts[3],
                "message": parts[4] if len(parts) > 4 else "",
            }
        elif line.strip() and current_commit:
            entries.append({**current_commit, "file": line.strip()})
    return entries


def compute_velocity(repo_root: Path, days: int = 30) -> Counter:
    """
    Return a Counter mapping repo-relative file path → commit count
    for the last *days* days.
    """
    log = get_git_log(repo_root, days=days)
    counter: Counter = Counter()
    for entry in log:
        counter[entry["file"]] += 1
    return counter


def get_changed_files_since_hash(repo_root: Path, since_hash: str) -> list[str]:
    """
    Return list of files changed since a given commit hash.
    Used for incremental re-analysis.
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), "diff", "--name-only", since_hash, "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]
    except subprocess.CalledProcessError:
        return []


def get_head_hash(repo_root: Path) -> str | None:
    """Return the current HEAD commit hash, or None if not a git repo."""
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()
    except subprocess.CalledProcessError:
        return None


def is_github_url(s: str) -> bool:
    return bool(re.match(r"https?://github\.com/", s))