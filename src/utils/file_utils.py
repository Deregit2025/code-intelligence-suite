"""
File system utilities: directory walking, extension routing, safe reads.
"""

from __future__ import annotations

import fnmatch
from pathlib import Path
from typing import Iterator

from src.config import CONFIG
from src.models.nodes import Language


def iter_repo_files(repo_root: Path) -> Iterator[Path]:
    """
    Recursively yield all files in *repo_root* that are not in a skip directory
    and are smaller than the configured max size.
    """
    skip_dirs = set(CONFIG.analysis.skip_dirs)

    for path in repo_root.rglob("*"):
        if not path.is_file():
            continue

        # Skip if any parent part matches a skip pattern
        parts = path.relative_to(repo_root).parts
        if any(
            any(fnmatch.fnmatch(part, pattern) for pattern in skip_dirs)
            for part in parts
        ):
            continue

        # Skip oversized files
        try:
            if path.stat().st_size > CONFIG.analysis.max_file_bytes:
                continue
        except OSError:
            continue

        yield path


def detect_language(path: Path) -> Language:
    """Map a file extension to a Language enum value."""
    ext = path.suffix.lower()
    cfg = CONFIG.analysis
    if ext in cfg.python_extensions:
        return Language.PYTHON
    if ext in cfg.sql_extensions:
        return Language.SQL
    if ext in cfg.yaml_extensions:
        return Language.YAML
    if ext in cfg.notebook_extensions:
        return Language.NOTEBOOK
    if ext in cfg.js_extensions:
        return Language.JAVASCRIPT
    return Language.OTHER


def safe_read(path: Path) -> str | None:
    """Read a text file, returning None on any error."""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None


def relative_path(path: Path, repo_root: Path) -> str:
    """Return a POSIX repo-relative path string."""
    try:
        return path.relative_to(repo_root).as_posix()
    except ValueError:
        return str(path)