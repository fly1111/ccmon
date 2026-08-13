"""Desktop toast notifications via winotify.

winotify needs an AppUserModelID for the toast to attribute to ccmon (vs the
generic "Python"). The AUMID is set up at install time by scripts/register_aumid
and recorded in the Windows registry under HKCU\\Software\\Classes\\AppUserModelID.
"""

from __future__ import annotations

import logging

from ..models import Session, State

log = logging.getLogger(__name__)

_AUMID = "ccmon"
_APP_NAME = "ccmon"


def _enabled() -> bool:
    try:
        from winotify import Notification  # noqa: F401 - import for capability check
    except Exception:  # noqa: BLE001
        return False
    return True


_TITLES: dict[State, str] = {
    State.NEEDS_APPROVAL: "Claude Code 等待你授权",
    State.NEEDS_INPUT: "Claude Code 等待你输入",
    State.DIALOG: "Claude Code 等待你选择",
    State.CRASHED: "Claude Code 异常退出",
}

_BODIES: dict[State, str] = {
    State.CRASHED: "进程已不存在，可能被关闭或崩溃",
}


def send(session: Session) -> bool:
    """Fire a toast. Returns True on success; swallows errors silently."""
    title = _TITLES.get(session.state)
    if not title:
        return False
    if not _enabled():
        log.debug("winotify unavailable; skipping toast for %s", session.pid)
        return False
    body = _BODIES.get(session.state, session.detail)
    try:
        from winotify import Notification, audio

        toast = Notification(
            app_id=_AUMID,
            title=f"{title} · {session.project}",
            msg=body,
            duration="short",
        )
        toast.set_audio(audio.Default, loop=False)
        toast.show()
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("toast failed for %s: %s", session.pid, exc)
        return False
