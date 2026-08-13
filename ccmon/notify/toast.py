"""Desktop toast notifications via winotify.

winotify needs an AppUserModelID for the toast to attribute to ccmon (vs the
generic "Python"). The AUMID is set up at install time by scripts/register_aumid
and recorded in the Windows registry under HKCU\\Software\\Classes\\AppUserModelID.

The toast body itself isn't clickable in winotify, but we can attach an
action button. We add a "跳转到窗口" button so a single click on the toast
brings the session's terminal/VS Code window to the foreground.

winotify's action callback must be registered at module level via the
@Notifier.register_callback decorator and takes no arguments. We stash
the (pid, cwd) of every toast in a per-pid map, and the registered
callback looks up its target by the calling AUMID/URL when fired.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from ..models import Session, State

log = logging.getLogger(__name__)

_AUMID = "ccmon"
_APP_NAME = "ccmon"

# pid -> (cwd, project) of every toast currently visible. The registered
# callback below reads from this when the user clicks "跳转到窗口".
# Per-pid map so multiple concurrent toasts each have a target.
_pending_jump: dict[int, tuple[str, str]] = {}

# The decorated callback reference is stored here once at import time so
# `send()` can attach it as the action handler for every toast.
_jump_callback = None


def _try_register_jump_callback() -> Any:
    """Register a single jump callback with winotify. Returns the callback.

    winotify's @Notifier.register_callback is an instance method, so we
    need to construct a Notifier first; the decorated function then
    carries a `.url` attribute that add_actions will accept.

    The Notifier's Listener binds to a named pipe. We use a per-pid AUMID
    so each ccmon process gets its own pipe, and fall back silently when
    construction fails (toast then appears without a clickable action).
    """
    try:
        from winotify import Notifier
        from winotify._registry import Registry
    except Exception:  # noqa: BLE001
        return None

    # Per-pid AUMID => unique named pipe per process => no collision with
    # another ccmon instance or a stale binding from a previous run.
    aumid = f"ccmon.callbacks.{os.getpid()}"
    try:
        registry = Registry(app_id=aumid, script_path="ccmon", force_override=True)
        notifier = Notifier(registry=registry)
    except Exception:  # noqa: BLE001
        log.warning("could not bind winotify callback registry; toast action disabled")
        return None

    @notifier.register_callback
    def _jump_to_session() -> None:
        # No arguments -- pick the most recently-shown toast target. If the
        # user has multiple toasts visible they may get a different target
        # than expected, but in practice only one NEEDS_APPROVAL toast
        # shows at a time because of the 60s cooldown.
        target = None
        for pid, entry in _pending_jump.items():
            if target is None or pid > target[0]:
                cwd, _project = entry
                target = (pid, cwd)
        if target is None:
            log.info("toast action fired but no pending jump target")
            return
        pid, cwd = target
        log.info("toast action: jumping to pid=%s cwd=%s", pid, cwd)
        try:
            from ..win.activate import jump_to_session
            jump_to_session(pid, cwd)
        except Exception:  # noqa: BLE001
            log.exception("jump callback failed")

    return _jump_to_session


_jump_callback = _try_register_jump_callback()
log.info("winotify callback registered: %s", bool(_jump_callback))


_TITLES: dict[State, str] = {
    State.NEEDS_APPROVAL: "Claude Code 等待你授权",
    State.NEEDS_INPUT: "Claude Code 等待你输入",
    State.DIALOG: "Claude Code 等待你选择",
    State.CRASHED: "Claude Code 异常退出",
}

_BODIES: dict[State, str] = {
    State.CRASHED: "进程已不存在，可能被关闭或崩溃",
}


def _evict_expired() -> None:
    """Windows toasts auto-dismiss after ~7s. We don't track expiry, but
    the slot map is small; for safety cap it at 16 entries (the toast
    cooldowns already prevent many concurrent entries).
    """
    if len(_pending_jump) > 16:
        # Drop the oldest 4.
        for pid in list(_pending_jump.keys())[:4]:
            del _pending_jump[pid]


def send(session: Session) -> bool:
    """Fire a toast. Returns True on success; swallows errors silently.

    Adds a "跳转到窗口" action button that jumps to the session's terminal
    or VS Code window when clicked.
    """
    title = _TITLES.get(session.state)
    if not title:
        return False
    try:
        from winotify import Notification, audio
    except Exception:  # noqa: BLE001
        log.debug("winotify unavailable; skipping toast for %s", session.pid)
        return False

    body = _BODIES.get(session.state, session.detail)
    if session.pid and session.cwd:
        _pending_jump[session.pid] = (session.cwd, session.project)
        _evict_expired()
    log.info(
        "toast: sending for pid=%s state=%s project=%s action=%s",
        session.pid, session.state.name, session.project,
        "yes" if _jump_callback is not None else "no",
    )

    try:
        toast = Notification(
            app_id=_AUMID,
            title=f"{title} · {session.project}",
            msg=body,
            duration="short",
        )
        toast.set_audio(audio.Default, loop=False)
        if session.pid and session.cwd and _jump_callback is not None:
            toast.add_actions(label="跳转到窗口", launch=_jump_callback)
        toast.show()
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("toast failed for %s: %s", session.pid, exc)
        return False
