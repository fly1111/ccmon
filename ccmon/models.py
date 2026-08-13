"""Session state vocabulary.

The states map from what Claude Code writes into ~/.claude/sessions/<pid>.json.
The raw fields come from `updateSessionActivity()` in claude-code's
src/utils/concurrentSessions.ts; the derivation is in src/screens/REPL.tsx:

    sessionStatus = isWaitingForApproval || isShowingLocalJSXCommand ? 'waiting'
                  : isLoading ? 'busy' : 'idle'
    waitingFor    = toolUseConfirmQueue.length > 0 ? `approve ${tool.name}`
                  : pendingWorkerRequest  ? 'worker request'
                  : pendingSandboxRequest ? 'sandbox request'
                  : isShowingLocalJSXCommand ? 'dialog open'
                  : 'input needed'
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class State(str, Enum):
    NEEDS_APPROVAL = "NEEDS_APPROVAL"  # blocked on a permission prompt -- the thing we care about
    CRASHED = "CRASHED"  # registry file present but process gone
    NEEDS_INPUT = "NEEDS_INPUT"  # turn finished, awaiting the user
    DIALOG = "DIALOG"  # a local dialog is open (e.g. AskUserQuestion)
    RUNNING = "RUNNING"  # actively working
    IDLE = "IDLE"  # alive but doing nothing
    UNKNOWN = "UNKNOWN"  # alive, but this build isn't publishing status
    EXITED = "EXITED"  # registry file gone

    @property
    def label(self) -> str:
        return _LABELS[self]

    @property
    def glyph(self) -> str:
        return _GLYPHS[self]

    @property
    def color(self) -> str:
        return _COLORS[self]

    @property
    def needs_attention(self) -> bool:
        return self in _ATTENTION


# Ordered most-urgent first. The tray icon colour and the pet's expression both
# take the first state present among live sessions.
PRIORITY: tuple[State, ...] = (
    State.NEEDS_APPROVAL,
    State.CRASHED,
    State.NEEDS_INPUT,
    State.DIALOG,
    State.RUNNING,
    State.IDLE,
    State.UNKNOWN,
    State.EXITED,
)

_ATTENTION = frozenset({State.NEEDS_APPROVAL, State.NEEDS_INPUT, State.CRASHED})

_LABELS: dict[State, str] = {
    State.NEEDS_APPROVAL: "等待授权",
    State.CRASHED: "异常退出",
    State.NEEDS_INPUT: "等待输入",
    State.DIALOG: "等待选择",
    State.RUNNING: "运行中",
    State.IDLE: "空闲",
    State.UNKNOWN: "状态未知",
    State.EXITED: "已退出",
}

_GLYPHS: dict[State, str] = {
    State.NEEDS_APPROVAL: "!",
    State.CRASHED: "x",
    State.NEEDS_INPUT: "?",
    State.DIALOG: "?",
    State.RUNNING: "*",
    State.IDLE: "-",
    State.UNKNOWN: "-",
    State.EXITED: ".",
}

_COLORS: dict[State, str] = {
    State.NEEDS_APPROVAL: "#E53935",  # red
    State.CRASHED: "#FB8C00",  # orange
    State.NEEDS_INPUT: "#FDD835",  # amber
    State.DIALOG: "#FDD835",
    State.RUNNING: "#1E88E5",  # blue
    State.IDLE: "#9E9E9E",  # grey
    State.UNKNOWN: "#616161",
    State.EXITED: "#424242",
}


def worst(states: list[State]) -> State:
    """The state the tray icon / pet should reflect."""
    for candidate in PRIORITY:
        if candidate in states:
            return candidate
    return State.IDLE


def classify(status: str | None, waiting_for: str | None, *, alive: bool) -> State:
    """Map raw registry fields onto our state vocabulary.

    `status` is absent when the CLI build does not publish activity (the writer
    is gated behind a feature flag). Absent must NOT be read as "idle" -- we
    genuinely do not know, so say so and let the transcript fallback fill in.
    """
    if not alive:
        return State.CRASHED
    if status is None:
        return State.UNKNOWN
    if status == "busy":
        return State.RUNNING
    if status == "idle":
        return State.IDLE
    if status == "waiting":
        wf = (waiting_for or "").strip()
        if wf.startswith("approve ") or wf in ("worker request", "sandbox request"):
            return State.NEEDS_APPROVAL
        if wf == "dialog open":
            return State.DIALOG
        # 'input needed', or an unrecognised reason -- either way the session is
        # blocked on the human, which is what the user needs to see.
        return State.NEEDS_INPUT
    return State.UNKNOWN


@dataclass
class Session:
    """One live Claude Code session.

    Identity is (pid, started_at): `session_id` is mutated in place by /resume,
    so it is display data, not a key.
    """

    pid: int
    state: State
    cwd: str
    session_id: str | None = None
    name: str | None = None
    started_at: int | None = None  # ms epoch
    updated_at: int | None = None  # ms epoch, pushed on status change only
    status: str | None = None  # raw registry value
    waiting_for: str | None = None  # raw registry value
    kind: str | None = None
    entrypoint: str | None = None
    version: str | None = None
    alive: bool = True
    registry_mtime: float = 0.0
    activity: str | None = None  # "Bash: pytest -q", from the transcript tail
    transcript: Path | None = None
    extra: dict[str, object] = field(default_factory=dict)

    @property
    def project(self) -> str:
        """Short display name for the project this session runs in."""
        if self.cwd:
            tail = self.cwd.replace("/", "\\").rstrip("\\").rsplit("\\", 1)[-1]
            if tail:
                return tail
        return self.name or f"pid:{self.pid}"

    @property
    def detail(self) -> str:
        """One-line explanation of why the session is in its current state.

        For NEEDS_APPROVAL we prefer `activity` (e.g. "Bash: pytest -q") over
        the raw `waiting_for` ("approve Bash") because the actual command is
        what the user needs to decide on -- seeing "Bash" alone isn't enough
        to know whether to allow it.
        """
        if self.state is State.NEEDS_APPROVAL:
            if self.activity:
                return f"需要授权 · {self.activity}"
            wf = (self.waiting_for or "").strip()
            if wf.startswith("approve "):
                return f"需要授权 · {wf[len('approve '):]}"
            return f"需要授权 · {wf or '未知'}"
        if self.state is State.CRASHED:
            return "进程已不存在"
        if self.state is State.UNKNOWN:
            return "该版本未上报状态"
        return self.activity or self.state.label
