"""State classification tests.

These pin the mapping from Claude Code's raw registry fields onto ccmon states.
The raw vocabulary is fixed by claude-code's src/screens/REPL.tsx; if a future
release adds a `waitingFor` reason, the fallback assertions here should catch it
degrading into something misleading.
"""

from __future__ import annotations

import pytest

from ccmon.models import State, classify, worst


@pytest.mark.parametrize(
    ("status", "waiting_for", "expected"),
    [
        ("busy", None, State.RUNNING),
        ("idle", None, State.IDLE),
        ("waiting", "approve Bash", State.NEEDS_APPROVAL),
        ("waiting", "approve Edit", State.NEEDS_APPROVAL),
        ("waiting", "worker request", State.NEEDS_APPROVAL),
        ("waiting", "sandbox request", State.NEEDS_APPROVAL),
        ("waiting", "dialog open", State.DIALOG),
        ("waiting", "input needed", State.NEEDS_INPUT),
    ],
)
def test_known_vocabulary(status, waiting_for, expected):
    assert classify(status, waiting_for, alive=True) is expected


def test_missing_status_is_unknown_not_idle():
    """A build that doesn't publish activity must not be reported as idle.

    Claiming "idle" for a session that is actually mid-task is the single most
    misleading thing this tool could do.
    """
    assert classify(None, None, alive=True) is State.UNKNOWN


def test_dead_process_beats_any_status():
    assert classify("busy", None, alive=False) is State.CRASHED
    assert classify("waiting", "approve Bash", alive=False) is State.CRASHED


def test_unrecognised_waiting_reason_still_flags_the_human():
    """An unknown reason means blocked-on-user; never silently downgrade to idle."""
    assert classify("waiting", "some future reason", alive=True) is State.NEEDS_INPUT
    assert classify("waiting", None, alive=True) is State.NEEDS_INPUT


def test_unrecognised_status_is_unknown():
    assert classify("teleporting", None, alive=True) is State.UNKNOWN


def test_approval_outranks_everything_else():
    states = [State.RUNNING, State.IDLE, State.NEEDS_INPUT, State.NEEDS_APPROVAL]
    assert worst(states) is State.NEEDS_APPROVAL


def test_crash_outranks_input_but_not_approval():
    assert worst([State.NEEDS_INPUT, State.CRASHED]) is State.CRASHED
    assert worst([State.CRASHED, State.NEEDS_APPROVAL]) is State.NEEDS_APPROVAL


def test_worst_of_nothing_is_idle():
    assert worst([]) is State.IDLE


def test_attention_set():
    assert State.NEEDS_APPROVAL.needs_attention
    assert State.NEEDS_INPUT.needs_attention
    assert State.CRASHED.needs_attention
    assert not State.RUNNING.needs_attention
    assert not State.IDLE.needs_attention
    assert not State.DIALOG.needs_attention
