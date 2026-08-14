"""Pet entry point: own QApplication, mirrors tray state via the engine."""

from __future__ import annotations

import logging
import os
import signal
import sys
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, Signal
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
        # memmove lives in msvcrt, not in the Win32 DLLs; ctypes.windll
        # looks under the standard Win32 namespace and fails to find it.
        ctypes.cdll.msvcrt.memmove(handle, data, len(data))
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

    def _graceful_quit() -> None:
        log.info("pet: graceful quit requested")
        try:
            engine.stop()
        except Exception:  # noqa: BLE001
            log.exception("pet: engine.stop failed")
        try:
            window.hide()
        except Exception:  # noqa: BLE001
            pass
        # Tell the Qt loop to exit (best effort). The signal handler below
        # is the real exit trigger on Windows because Qt may ignore
        # app.quit() from a non-Qt thread.
        try:
            app.quit()
        except Exception:  # noqa: BLE001
            pass

    def _hard_exit(code: int = 0) -> None:
        """Immediate process exit. Called from signal handlers that cannot
        rely on Qt to deliver the quit on the main thread."""
        log.info("pet: hard exit code=%s", code)
        try:
            engine.stop()
        except Exception:  # noqa: BLE001
            pass
        import os
        os._exit(code)

    # Windows: install a console control handler. This fires on a
    # Windows-internal thread, so we cannot use Qt APIs there -- we must
    # call os._exit directly. SetConsoleCtrlHandler must be installed
    # BEFORE the Qt event loop starts blocking.
    if sys.platform == "win32":
        try:
            import ctypes

            @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_ulong)
            def _win_console_handler(ctrl_type: int) -> bool:
                # CTRL_C_EVENT=0, CTRL_BREAK_EVENT=1, CTRL_CLOSE_EVENT=2,
                # CTRL_LOGOFF_EVENT=5, CTRL_SHUTDOWN_EVENT=6
                if ctrl_type in (0, 1, 2, 5, 6):
                    log.info("pet: Win32 console event %s, hard exit", ctrl_type)
                    _hard_exit(0)
                return False  # let default handler run if we didn't catch it

            ctypes.windll.kernel32.SetConsoleCtrlHandler(_win_console_handler, True)
            log.info("pet: SetConsoleCtrlHandler installed")
        except Exception:  # noqa: BLE001
            log.exception("pet: failed to install SetConsoleCtrlHandler")

    # Belt-and-braces: also install a Python signal handler. On Windows
    # the handler runs on the main thread between bytecode instructions,
    # so it WILL fire if the Qt event loop isn't blocking. We hard-exit
    # rather than trying to coordinate with Qt.
    signal.signal(signal.SIGINT, lambda *_: _hard_exit(0))
    signal.signal(signal.SIGTERM, lambda *_: _hard_exit(0))

    log.info("pet: entering event loop")
    rc = app.exec()
    log.info("pet: event loop exited with code %s", rc)
    try:
        engine.stop()
    except Exception:  # noqa: BLE001
        pass
    # Force exit so daemon threads can't keep the interpreter alive.
    log.info("pet: forcing exit after clean shutdown")
    import os
    os._exit(rc)