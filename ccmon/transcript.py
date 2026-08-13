"""Derive "what is this session doing right now" from its transcript tail.

Zero-intrusion alternative to a PreToolUse hook: the last tool_use block in
~/.claude/projects/<encoded-cwd>/<session-id>.jsonl tells us the current tool,
and the absence of a matching tool_result tells us it is still in flight.

Only the tail of the file is read -- transcripts reach megabytes.
"""

from __future__ import annotations

import json
from pathlib import Path

from .paths import encode_project_dir, projects_dir

# Enough to cover a handful of entries; tool_use inputs can be large.
_TAIL_BYTES = 256 * 1024
_MAX_SCAN_LINES = 400


def find_transcript(cwd: str, session_id: str | None) -> Path | None:
    """Locate a session's transcript, preferring the encoded-path fast path."""
    if not session_id:
        return None
    root = projects_dir()
    if cwd:
        candidate = root / encode_project_dir(cwd) / f"{session_id}.jsonl"
        if candidate.exists():
            return candidate
    # Encoding rules could change, or the session may have moved cwd. Globbing a
    # dozen project dirs is cheap next to being wrong.
    try:
        return next(root.glob(f"*/{session_id}.jsonl"), None)
    except OSError:
        return None


def _tail_lines(path: Path) -> list[str]:
    try:
        size = path.stat().st_size
        with path.open("rb") as fh:
            if size > _TAIL_BYTES:
                fh.seek(size - _TAIL_BYTES)
                fh.readline()  # discard the partial first line
            blob = fh.read()
    except OSError:
        return []
    text = blob.decode("utf-8", errors="replace")
    return text.splitlines()[-_MAX_SCAN_LINES:]


def _summarise(tool: str, tool_input: object) -> str:
    """Compress a tool call into something that fits one menu row."""
    if not isinstance(tool_input, dict):
        return tool
    for key in ("command", "file_path", "path", "pattern", "url", "description", "prompt"):
        value = tool_input.get(key)
        if isinstance(value, str) and value.strip():
            detail = " ".join(value.split())
            if key in ("file_path", "path"):
                detail = detail.replace("/", "\\").rsplit("\\", 1)[-1]
            if len(detail) > 60:
                detail = detail[:57] + "..."
            return f"{tool}: {detail}"
    return tool


def current_activity(path: Path | None) -> str | None:
    """Best-effort description of the most recent tool call.

    Returns e.g. "Bash: pytest -q" (running) or "已完成 Edit: models.py".
    """
    if path is None:
        return None
    lines = _tail_lines(path)
    if not lines:
        return None

    completed: set[str] = set()
    for line in reversed(lines):
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue  # truncated tail line, or a partial write
        if not isinstance(entry, dict):
            continue

        message = entry.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, list):
            continue

        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "tool_result":
                tid = block.get("tool_use_id")
                if isinstance(tid, str):
                    completed.add(tid)
            elif btype == "tool_use":
                name = block.get("name")
                if not isinstance(name, str):
                    continue
                summary = _summarise(name, block.get("input"))
                tid = block.get("id")
                done = isinstance(tid, str) and tid in completed
                return f"已完成 {summary}" if done else summary
    return None


def enrich(sessions: list) -> None:
    """Attach transcript-derived activity text to each session, in place."""
    for session in sessions:
        try:
            session.transcript = find_transcript(session.cwd, session.session_id)
            session.activity = current_activity(session.transcript)
        except Exception:  # noqa: BLE001 - never let display detail break the scan
            session.activity = None
