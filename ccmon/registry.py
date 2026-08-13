"""Read Claude Code's live session registry.

Claude Code maintains ~/.claude/sessions/<pid>.json for every running top-level
session (see registerSession() in src/utils/concurrentSessions.ts). We only read
it -- never write, never sweep.

Three behaviours of the writer that this module has to absorb:

1. `updatePidFile()` is an unlocked read-modify-write, so a concurrent reader can
   observe a truncated file. A parse failure is normal, not exceptional.
2. Only `^\\d+\\.json$` is a session file. claude-code#34210 was a real data-loss
   bug caused by lenient parseInt on other filenames; we apply the same guard.
3. Subagents never register (`if (getAgentId() != null) return false`), so this
   naturally yields main sessions only.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import psutil

from .models import Session, State, classify
from .paths import sessions_dir

_PID_FILE = re.compile(r"^\d+\.json$")

# A pid alone is not identity: the OS reuses them. Registry startedAt is when
# Claude registered, which is a hair after process start, so allow a window.
_START_SKEW_S = 60.0


def _process_alive(pid: int, started_at_ms: int | None) -> bool:
    """True if `pid` is the same process that wrote the registry entry."""
    try:
        proc = psutil.Process(pid)
    except (psutil.NoSuchProcess, ValueError):
        return False
    except psutil.Error:
        return True  # can't tell (access denied); assume alive rather than lie
    if started_at_ms is None:
        return True
    try:
        # Registry startedAt is written just after the process starts, so it must
        # not predate process creation by more than the clock/scheduling skew.
        return proc.create_time() - _START_SKEW_S <= started_at_ms / 1000.0
    except psutil.Error:
        return True


def _read_entry(path: Path) -> dict | None:
    """Parse one registry file, tolerating a torn read."""
    try:
        raw = path.read_text("utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    if not raw.strip():
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None  # mid-write; the next tick will pick it up
    return data if isinstance(data, dict) else None


def _as_int(value: object) -> int | None:
    return value if isinstance(value, int) else None


def _as_str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def scan(directory: Path | None = None) -> list[Session]:
    """Snapshot every registered session, most-urgent first.

    Never raises: a monitor that crashes on a malformed file is worse than one
    that reports slightly stale data.
    """
    root = directory or sessions_dir()
    try:
        entries = list(os.scandir(root))
    except OSError:
        return []

    sessions: list[Session] = []
    for entry in entries:
        if not _PID_FILE.match(entry.name):
            continue
        try:
            if not entry.is_file():
                continue
            mtime = entry.stat().st_mtime
        except OSError:
            continue

        data = _read_entry(Path(entry.path))
        if data is None:
            continue

        pid = _as_int(data.get("pid"))
        if pid is None:
            # Fall back to the filename, which is authoritative anyway.
            pid = int(entry.name[:-5])

        started_at = _as_int(data.get("startedAt"))
        alive = _process_alive(pid, started_at)
        status = _as_str(data.get("status"))
        waiting_for = _as_str(data.get("waitingFor"))

        sessions.append(
            Session(
                pid=pid,
                state=classify(status, waiting_for, alive=alive),
                cwd=_as_str(data.get("cwd")) or "",
                session_id=_as_str(data.get("sessionId")),
                name=_as_str(data.get("name")),
                started_at=started_at,
                updated_at=_as_int(data.get("updatedAt")),
                status=status,
                waiting_for=waiting_for,
                kind=_as_str(data.get("kind")),
                entrypoint=_as_str(data.get("entrypoint")),
                version=_as_str(data.get("version")),
                alive=alive,
                registry_mtime=mtime,
            )
        )

    from .models import PRIORITY

    order = {state: i for i, state in enumerate(PRIORITY)}
    sessions.sort(key=lambda s: (order.get(s.state, 99), -(s.started_at or 0)))
    return sessions


def overall(sessions: list[Session]) -> State:
    """The single state the tray icon / pet should show."""
    from .models import worst

    return worst([s.state for s in sessions]) if sessions else State.IDLE
