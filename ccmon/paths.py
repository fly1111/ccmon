"""Filesystem locations ccmon reads from and writes to.

Everything Claude-owned is READ-ONLY for us. ccmon never writes into ~/.claude.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def claude_home() -> Path:
    """Claude Code's config dir. Honours CLAUDE_CONFIG_DIR like the CLI does."""
    override = os.environ.get("CLAUDE_CONFIG_DIR")
    if override:
        return Path(override)
    return Path.home() / ".claude"


def sessions_dir() -> Path:
    """Live session registry: one <pid>.json per running top-level session."""
    return claude_home() / "sessions"


def projects_dir() -> Path:
    """Transcript root: projects/<encoded-cwd>/<session-id>.jsonl."""
    return claude_home() / "projects"


def ide_dir() -> Path:
    """IDE integration locks: <port>.lock carrying pid + workspaceFolders."""
    return claude_home() / "ide"


@lru_cache(maxsize=1)
def data_dir() -> Path:
    """ccmon's own state. Kept out of ~/.claude so a Claude reinstall can't wipe it."""
    override = os.environ.get("CCMON_DATA_DIR")
    if override:
        base = Path(override)
    else:
        local = os.environ.get("LOCALAPPDATA")
        base = Path(local) / "ccmon" if local else Path.home() / ".ccmon"
    base.mkdir(parents=True, exist_ok=True)
    return base


def db_path() -> Path:
    return data_dir() / "ccmon.db"


def log_path() -> Path:
    return data_dir() / "ccmon.log"


def encode_project_dir(cwd: str) -> str:
    """Mirror Claude's cwd -> projects/ directory-name encoding.

    'D:\\ComfyUI' -> 'D--ComfyUI'; 'D:\\vscodepro\\notepad' -> 'D--vscodepro-notepad'.
    Only a fast path: callers fall back to globbing when this misses, so a
    future change to Claude's encoding degrades to "slower" rather than "broken".
    """
    return cwd.replace(":", "-").replace("\\", "-").replace("/", "-").rstrip("-")
