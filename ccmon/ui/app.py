"""pystray application.

The tray is read-only: it watches the engine's tick stream and rebuilds the
menu when sessions change. Jumps and notifications happen elsewhere; we just
provide a place to click them.
"""

from __future__ import annotations

import logging
import threading
from functools import lru_cache
from typing import Callable

import pystray
from PIL import Image

from ..engine import Engine
from ..models import Session, State
from ..state_machine import Tick
from .tray import render_icon

log = logging.getLogger(__name__)


@lru_cache(maxsize=32)
def _label(state: State) -> str:
    return state.label


_ROW_KEYS = ("jump", "copy_id", "open_transcript", "mute")


def _row(session: Session, callbacks: dict) -> tuple[pystray.MenuItem, ...]:
    jump = callbacks["jump"]
    copy_id = callbacks["copy_id"]
    open_transcript = callbacks["open_transcript"]
    mute = callbacks["mute"]
    detail = session.detail
    title = f"{session.state.glyph} {session.project} · {session.state.label}"
    if session.waiting_for and session.state is State.NEEDS_APPROVAL:
        title = f"{session.state.glyph} {session.project} · {detail}"
    elif detail and session.state not in (State.NEEDS_APPROVAL,):
        title = f"{session.state.glyph} {session.project} · {detail}"

    items: list[pystray.MenuItem] = [
        pystray.MenuItem(title, None, enabled=False),
    ]
    if session.session_id:
        items.append(pystray.MenuItem(
            "跳转到窗口",
            lambda _, pid=session.pid: jump(pid),
        ))
        items.append(pystray.MenuItem(
            "打开对话记录",
            lambda _, t=str(session.transcript or ''): open_transcript(t),
            enabled=bool(session.transcript),
        ))
        items.append(pystray.MenuItem(
            "复制会话ID",
            lambda _, sid=session.session_id: copy_id(sid),
        ))
    items.append(pystray.MenuItem(
        "静音此会话",
        lambda _, pid=session.pid: mute(pid),
    ))
    return tuple(items)


def _menu(icon: pystray.Icon, tick_data: Tick, callbacks: dict) -> tuple[pystray.MenuItem, ...]:
    sessions = tick_data.sessions if tick_data else ()
    if not sessions:
        header = "ccmon · 没有会话"
    else:
        attention = sum(1 for s in sessions if s.state.needs_attention)
        header = f"ccmon · {len(sessions)} 个会话"
        if attention:
            header += f" · {attention} 个需关注"

    rows: list[pystray.MenuItem] = []
    for session in sessions:
        rows.append(pystray.Menu.SEPARATOR)
        try:
            items = _row(session, callbacks)
        except Exception as exc:  # noqa: BLE001 - one bad session must not blank the whole menu
            log.warning("skipping row for pid=%s: %s", session.pid, exc)
            continue
        rows.extend(items)

    return (
        pystray.MenuItem(header, None, enabled=False),
        *rows,
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(
            "桌面宠物: 显示",
            callbacks["toggle_pet"],
            visible=lambda _: not callbacks["pet_visible"](),
        ),
        pystray.MenuItem(
            "桌面宠物: 隐藏",
            callbacks["toggle_pet"],
            visible=lambda _: callbacks["pet_visible"](),
        ),
        pystray.MenuItem("通知设置", lambda _: callbacks["open_settings"]()),
        pystray.MenuItem("立即刷新", lambda _: callbacks["refresh"]()),
        pystray.MenuItem("退出", lambda _: _quit_app(icon, callbacks)),
    )


def _quit_app(icon, callbacks: dict) -> None:
    """Tray "退出" handler. Stops pystray, then runs the optional
    `on_quit` callback (used by pet-only mode to also quit the Qt
    app since the pet runs the Qt loop on the main thread)."""
    on_quit = callbacks.get("on_quit")
    if on_quit is not None:
        try:
            on_quit()
        except Exception:  # noqa: BLE001
            log.exception("on_quit callback failed")
    icon.stop()


def run(engine: Engine, callbacks: dict, *, initial_tick: Tick | None = None) -> None:
    icon_state = {"tick": initial_tick, "image": None}
    icon_lock = threading.Lock()

    def on_tick(tick: Tick) -> None:
        with icon_lock:
            icon_state["tick"] = tick
            image = render_icon(tick.overall, attention_count=sum(1 for s in tick.sessions if s.state.needs_attention))
            icon_state["image"] = image
        if tray_icon:
            try:
                tray_icon.icon = image
                # pystray on Windows mis-detects an empty menu if constructed
                # via a callable. Pass items directly and call update_menu().
                tray_icon.menu = pystray.Menu(*_menu(tray_icon, tick, callbacks))
                tray_icon.update_menu()
            except Exception:  # noqa: BLE001 - pystray has no documented raise list
                log.exception("pystray update failed")

    def menu_factory(icon: pystray.Icon):
        tick = icon_state["tick"]
        return pystray.Menu(*_menu(icon, tick, callbacks))

    initial = initial_tick or engine.latest()
    icon_image = render_icon(initial.overall) if initial else render_icon(State.IDLE)

    initial_menu = pystray.Menu(*_menu(None, initial or _empty_tick(), callbacks))
    tray_icon = pystray.Icon(
        "ccmon",
        icon=icon_image,
        title="ccmon",
        menu=initial_menu,
    )

    engine.subscribe(on_tick)
    engine.start()
    try:
        tray_icon.run()
    finally:
        engine.stop()


def _empty_tick() -> Tick:
    from ..state_machine import Tick as T

    return T(sessions=(), entered=(), left=())
