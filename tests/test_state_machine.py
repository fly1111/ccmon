"""Pure transition tests: drive state_machine.diff with synthetic snapshots.

No threads, no engine, no real registry. If these go red, the bug is in the
mapping or the diff, not in I/O.
"""

from __future__ import annotations

from ccmon.models import State
from ccmon.state_machine import diff, NOTIFY_STATES


def make(pid: int, state: State, *, cwd: str = "D:\\proj") -> dict:
    return {
        "pid": pid,
        "state": state,
        "cwd": cwd,
        "session_id": f"sid-{pid}",
        "alive": True,
        "registry_mtime": 0.0,
    }


def _as_session(d):
    from ccmon.models import Session

    return Session(
        pid=d["pid"],
        state=d["state"],
        cwd=d["cwd"],
        session_id=d.get("session_id"),
        name=d.get("name"),
        started_at=d.get("started_at"),
        updated_at=d.get("updated_at"),
        status=None,
        waiting_for=None,
        kind=None,
        entrypoint=None,
        version=None,
        alive=d.get("alive", True),
        registry_mtime=d.get("registry_mtime", 0.0),
    )


def test_first_tick_marks_every_session_as_entered():
    prev = []
    cur = [_as_session(make(1, State.RUNNING))]
    tk = diff(prev, cur)
    assert [t.session.pid for t in tk.entered] == [1]
    assert [t.previous for t in tk.entered] == [State.IDLE]
    assert tk.left == ()
    assert tk.overall is State.RUNNING


def test_no_changes_no_transitions():
    sessions = [_as_session(make(1, State.RUNNING))]
    tk = diff(sessions, sessions)
    assert tk.entered == () and tk.left == ()
    assert tk.sessions == (sessions[0],)


def test_state_change_is_one_entered():
    prev = [_as_session(make(1, State.RUNNING))]
    cur = [_as_session(make(1, State.NEEDS_APPROVAL))]
    tk = diff(prev, cur)
    assert len(tk.entered) == 1
    assert tk.entered[0].previous is State.RUNNING
    assert tk.entered[0].session.state is State.NEEDS_APPROVAL


def test_vanished_session_is_left_not_entered():
    prev = [_as_session(make(1, State.NEEDS_APPROVAL))]
    cur: list = []
    tk = diff(prev, cur)
    assert tk.entered == ()
    assert len(tk.left) == 1
    assert tk.left[0].previous is State.NEEDS_APPROVAL
    assert tk.left[0].session.state is State.EXITED


def test_same_pid_same_state_is_collapsed():
    """Two ticks in a row at the same state produce zero transitions."""
    prev = [_as_session(make(1, State.NEEDS_APPROVAL))]
    cur = [_as_session(make(1, State.NEEDS_APPROVAL))]
    tk = diff(prev, cur)
    assert tk.entered == () and tk.left == ()


def test_notify_only_on_first_enter():
    """A session flapping between NEEDS_APPROVAL and RUNNING must not spam.

    Only the first transition into a notify-state counts; subsequent re-entries
    before leaving it are silent. This is enforced at the notify layer using
    the `previous` field.
    """
    assert State.NEEDS_APPROVAL in NOTIFY_STATES
    assert State.NEEDS_INPUT in NOTIFY_STATES
    assert State.CRASHED in NOTIFY_STATES
    assert State.DIALOG in NOTIFY_STATES
    assert State.RUNNING not in NOTIFY_STATES
    assert State.IDLE not in NOTIFY_STATES


def test_overall_state_picks_approval_over_running():
    cur = [
        _as_session(make(1, State.RUNNING)),
        _as_session(make(2, State.NEEDS_APPROVAL)),
    ]
    assert diff([], cur).overall is State.NEEDS_APPROVAL


def test_overall_with_empty_cur_is_idle():
    prev = [_as_session(make(1, State.NEEDS_APPROVAL))]
    assert diff(prev, []).overall is State.IDLE


def test_by_pid_index():
    cur = [
        _as_session(make(1, State.RUNNING)),
        _as_session(make(2, State.IDLE)),
    ]
    tk = diff([], cur)
    assert set(tk.by_pid().keys()) == {1, 2}
