"""Pet entry point: own QApplication, mirrors tray state via the engine."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication

from ...engine import Engine
from ...win.activate import jump_to_session
from .window import PetWindow

log = logging.getLogger(__name__)


class PetBridge(QObject):
    """Adapter so the engine can push state into the pet without coupling."""

    update = Signal(object, object)  # state, sessions tuple

    def __init__(self, window: PetWindow) -> None:
        super().__init__()
        self.update.connect(window.update_state)

    def receive(self, tick) -> None:
        self.update.emit(tick.overall, tick.sessions)


def _open_transcript(path: str) -> None:
    if path and Path(path).exists():
        os.startfile(path)  # noqa: S606


def _copy_id(sid: str) -> None:
    if not sid:
        return
    import ctypes

    ctypes.windll.user32.OpenClipboard(0)
    try:
        ctypes.windll.user32.EmptyClipboard()
        data = sid.encode("utf-16-le") + b"\x00\x00"
        handle = ctypes.windll.kernel32.GlobalAlloc(0x0002, len(data))
        ctypes.windll.kernel32.GlobalLock(handle)
        ctypes.memmove(handle, data, len(data))
        ctypes.windll.user32.SetClipboardData(13, handle)
    finally:
        ctypes.windll.user32.CloseClipboard()


def run(engine: Engine) -> int:
    """Launch the pet under its own QApplication. Blocks."""
    # High-DPI scaling -- must happen before QApplication construction.
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication.instance() or QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    window = PetWindow()
    window.show()
    bridge = PetBridge(window)
    engine.subscribe(bridge.receive)

    def on_jump(pid: int) -> None:
        tick = engine.latest()
        if not tick:
            return
        for s in tick.sessions:
            if s.pid == pid:
                jump_to_session(pid, s.cwd)
                return

    window.jump_requested.connect(on_jump)
    window.copy_requested.connect(_copy_id)
    window.open_transcript.connect(_open_transcript)

    def hide_self() -> None:
        window.hide()

    window.toggle_self.connect(hide_self)

    return app.exec()
