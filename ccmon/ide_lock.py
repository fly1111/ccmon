"""Match a session to an IDE window via ~/.claude/ide/<port>.lock files.

The lock file is the authoritative "this IDE is bound to these workspaces"
record -- it's written by the Claude VS Code extension at connect time and
removed at disconnect. Matching by workspaceFolders is the cheapest reliable
cue for "which VS Code window owns this session".
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import NamedTuple

from .paths import ide_dir


class IdeBinding(NamedTuple):
    pid: int
    workspace: str  # one folder from workspaceFolders
    ide_name: str


def _basename(path: str) -> str:
    return path.replace("/", "\\").rstrip("\\").rsplit("\\", 1)[-1].casefold()


def scan(directory: Path | None = None) -> list[IdeBinding]:
    """Return every live IDE lock. Caller decides if any match a session."""
    root = directory or ide_dir()
    try:
        entries = list(os.scandir(root))
    except OSError:
        return []
    out: list[IdeBinding] = []
    for entry in entries:
        if not entry.name.endswith(".lock"):
            continue
        try:
            data = json.loads(Path(entry.path).read_text("utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        pid = data.get("pid")
        folders = data.get("workspaceFolders") or []
        ide_name = str(data.get("ideName") or "")
        if not isinstance(pid, int) or not isinstance(folders, list):
            continue
        for folder in folders:
            if isinstance(folder, str) and folder:
                out.append(IdeBinding(pid=pid, workspace=folder, ide_name=ide_name))
                break  # one binding per lock file is enough
    return out


def match_ide_for_session(session_cwd: str, bindings: list[IdeBinding] | None = None) -> IdeBinding | None:
    """Find the IDE bound to a folder whose basename matches the session's cwd."""
    if not session_cwd:
        return None
    needle = _basename(session_cwd)
    if not needle:
        return None
    for binding in bindings or scan():
        if _basename(binding.workspace) == needle:
            return binding
    return None
