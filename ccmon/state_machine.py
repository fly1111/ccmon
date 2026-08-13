"""Pure state-transition logic.

The engine layer is small: every tick reads the registry, diffs against the
previous snapshot, and reports which sessions entered or left which state. The
notification rules are layered on top -- this module deliberately knows nothing
about toasts, sound, or webhooks.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from .models import Session, State, worst

# We only notify when the session is *entering* an attention-needing state,
# never on a still-in-it tick. Webhook escalation has its own timer.
NOTIFY_STATES: frozenset[State] = frozenset(
    {State.NEEDS_APPROVAL, State.NEEDS_INPUT, State.CRASHED, State.DIALOG}
)


@dataclass(frozen=True)
class Transition:
    session: Session
    previous: State  # the state we observed at the start of the tick


@dataclass(frozen=True)
class Tick:
    """Output of one scan-and-diff cycle."""

    sessions: tuple[Session, ...]
    entered: tuple[Transition, ...]
    left: tuple[Transition, ...]

    @property
    def overall(self) -> State:
        return worst([s.state for s in self.sessions])

    def by_pid(self) -> dict[int, Session]:
        return {s.pid: s for s in self.sessions}

    def entered_needing_attention(self) -> tuple[Transition, ...]:
        return tuple(
            t for t in self.entered
            if t.previous not in NOTIFY_STATES and t.session.state in NOTIFY_STATES
        )


def diff(previous: Iterable[Session], current: Iterable[Session]) -> Tick:
    prev = {s.pid: s.state for s in previous}
    cur_list = list(current)
    cur = {s.pid: s.state for s in cur_list}
    cur_pids = set(cur)
    prev_pids = set(prev)

    entered: list[Transition] = []
    left: list[Transition] = []
    for s in cur_list:
        before = prev.get(s.pid)
        if before is None or before != s.state:
            entered.append(Transition(session=s, previous=before or State.IDLE))
    # Sessions that vanished from the registry are ENDED for us. We only know
    # about them from the previous snapshot.
    for session in previous:
        if session.pid not in cur_pids:
            ended = Session(
                pid=session.pid,
                state=State.EXITED,
                cwd=session.cwd,
                session_id=session.session_id,
                name=session.name,
                started_at=session.started_at,
                status=None,
                waiting_for=None,
                kind=session.kind,
                entrypoint=session.entrypoint,
                version=session.version,
                alive=False,
                registry_mtime=session.registry_mtime,
                activity=session.activity,
                transcript=session.transcript,
            )
            left.append(Transition(session=ended, previous=session.state))

    from .models import PRIORITY

    order = {state: i for i, state in enumerate(PRIORITY)}
    entered.sort(key=lambda t: (order.get(t.session.state, 99), -(t.session.started_at or 0)))
    left.sort(key=lambda t: (order.get(t.previous, 99), -(t.session.started_at or 0)))

    return Tick(sessions=tuple(cur_list), entered=tuple(entered), left=tuple(left))
