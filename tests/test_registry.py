"""Registry scanning tests -- all the ways ~/.claude/sessions/ can be hostile."""

from __future__ import annotations

import json
import os

import pytest

from ccmon import registry
from ccmon.models import State


def write_entry(directory, pid: int, **fields) -> None:
    payload = {"pid": pid, "cwd": "D:\\proj", "sessionId": f"sid-{pid}", **fields}
    (directory / f"{pid}.json").write_text(json.dumps(payload), encoding="utf-8")


@pytest.fixture
def alive(monkeypatch):
    """Treat every pid as live unless a test says otherwise."""
    monkeypatch.setattr(registry, "_process_alive", lambda pid, started: True)


def test_reads_a_waiting_session(tmp_path, alive):
    write_entry(tmp_path, 100, status="waiting", waitingFor="approve Bash")
    (session,) = registry.scan(tmp_path)
    assert session.pid == 100
    assert session.state is State.NEEDS_APPROVAL
    assert "Bash" in session.detail


def test_torn_read_is_skipped_not_fatal(tmp_path, alive):
    """updatePidFile() is an unlocked read-modify-write; half a file is normal."""
    write_entry(tmp_path, 100, status="busy")
    (tmp_path / "200.json").write_text('{"pid": 200, "status": "bu', encoding="utf-8")
    sessions = registry.scan(tmp_path)
    assert [s.pid for s in sessions] == [100]


def test_empty_file_is_skipped(tmp_path, alive):
    (tmp_path / "300.json").write_text("", encoding="utf-8")
    assert registry.scan(tmp_path) == []


def test_non_pid_filenames_are_ignored(tmp_path, alive):
    """claude-code#34210: lenient parseInt on stray filenames destroyed user data.

    ccmon never deletes, but it must not invent sessions out of unrelated files.
    """
    write_entry(tmp_path, 100, status="busy")
    for name in ("2026-03-14_notes.md", "notes.json", "12ab.json", ".json", "1.json.bak"):
        (tmp_path / name).write_text('{"pid": 999}', encoding="utf-8")
    assert [s.pid for s in registry.scan(tmp_path)] == [100]


def test_dead_process_reports_crashed(tmp_path, monkeypatch):
    monkeypatch.setattr(registry, "_process_alive", lambda pid, started: pid != 200)
    write_entry(tmp_path, 100, status="busy")
    write_entry(tmp_path, 200, status="busy")
    states = {s.pid: s.state for s in registry.scan(tmp_path)}
    assert states == {100: State.RUNNING, 200: State.CRASHED}


def test_missing_directory_is_not_an_error(tmp_path):
    assert registry.scan(tmp_path / "nope") == []


def test_filename_wins_when_pid_field_is_missing(tmp_path, alive):
    (tmp_path / "404.json").write_text('{"cwd": "D:\\\\x", "status": "idle"}', encoding="utf-8")
    (session,) = registry.scan(tmp_path)
    assert session.pid == 404


def test_sorted_most_urgent_first(tmp_path, alive):
    write_entry(tmp_path, 1, status="idle")
    write_entry(tmp_path, 2, status="busy")
    write_entry(tmp_path, 3, status="waiting", waitingFor="approve Write")
    write_entry(tmp_path, 4, status="waiting", waitingFor="input needed")
    assert [s.state for s in registry.scan(tmp_path)] == [
        State.NEEDS_APPROVAL,
        State.NEEDS_INPUT,
        State.RUNNING,
        State.IDLE,
    ]


def test_overall_picks_the_urgent_one(tmp_path, alive):
    write_entry(tmp_path, 1, status="busy")
    write_entry(tmp_path, 2, status="waiting", waitingFor="approve Bash")
    assert registry.overall(registry.scan(tmp_path)) is State.NEEDS_APPROVAL


def test_overall_with_no_sessions_is_idle():
    assert registry.overall([]) is State.IDLE


def test_project_name_derived_from_cwd(tmp_path, alive):
    write_entry(tmp_path, 100, status="busy", cwd="D:\\vscodepro\\ccmon")
    (session,) = registry.scan(tmp_path)
    assert session.project == "ccmon"


@pytest.mark.skipif(os.name != "nt", reason="pid reuse guard uses real process times")
def test_pid_reuse_is_rejected(tmp_path):
    """A recycled pid must not resurrect a dead session as 'running'."""
    write_entry(tmp_path, os.getpid(), status="busy", startedAt=0)
    (session,) = registry.scan(tmp_path)
    assert session.state is State.CRASHED
