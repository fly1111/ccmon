"""ccmon command line entry point."""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

from . import __version__, registry, transcript
from .models import Session, State

_ANSI = {
    State.NEEDS_APPROVAL: "\033[1;31m",
    State.CRASHED: "\033[1;33m",
    State.NEEDS_INPUT: "\033[1;93m",
    State.DIALOG: "\033[1;93m",
    State.RUNNING: "\033[1;36m",
    State.IDLE: "\033[0;90m",
    State.UNKNOWN: "\033[0;90m",
    State.EXITED: "\033[0;90m",
}
_RESET = "\033[0m"


def _force_utf8() -> None:
    """Windows consoles default to a legacy codepage; our labels are Chinese."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass


def _enable_ansi() -> bool:
    """Turn on VT processing on Windows consoles; report whether colour is usable."""
    if not sys.stdout.isatty():
        return False
    if sys.platform != "win32":
        return True
    try:
        import ctypes

        handle = ctypes.windll.kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if not ctypes.windll.kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        return bool(
            ctypes.windll.kernel32.SetConsoleMode(handle, mode.value | 0x0004)
        )
    except Exception:  # noqa: BLE001
        return False


def _age(ms: int | None) -> str:
    if not ms:
        return "-"
    delta = max(0.0, time.time() - ms / 1000.0)
    if delta < 60:
        return f"{int(delta)}s"
    if delta < 3600:
        return f"{int(delta // 60)}m"
    if delta < 86400:
        return f"{int(delta // 3600)}h{int(delta % 3600 // 60)}m"
    return f"{int(delta // 86400)}d"


def _width(text: str) -> int:
    """Display width, counting CJK glyphs as two columns."""
    return sum(2 if ord(ch) > 0x1100 and not 0x2000 <= ord(ch) <= 0x206F else 1 for ch in text)


def _pad(text: str, width: int) -> str:
    return text + " " * max(0, width - _width(text))


def render(sessions: list[Session], *, colour: bool) -> str:
    if not sessions:
        return "没有正在运行的 Claude Code 会话。"

    rows = [
        (
            s.state,
            f"{s.state.glyph} {s.state.label}",
            s.project,
            str(s.pid),
            _age(s.updated_at or s.started_at),
            s.detail,
        )
        for s in sessions
    ]
    headers = ("状态", "项目", "PID", "更新", "详情")
    widths = [
        max(_width(headers[0]), *(_width(r[1]) for r in rows)),
        max(_width(headers[1]), *(_width(r[2]) for r in rows)),
        max(_width(headers[2]), *(_width(r[3]) for r in rows)),
        max(_width(headers[3]), *(_width(r[4]) for r in rows)),
    ]

    out = [
        "  ".join(
            [_pad(headers[0], widths[0]), _pad(headers[1], widths[1]),
             _pad(headers[2], widths[2]), _pad(headers[3], widths[3]), headers[4]]
        ),
        "  ".join(["-" * w for w in widths] + ["-" * 8]),
    ]
    for state, status, project, pid, age, detail in rows:
        line = "  ".join(
            [_pad(status, widths[0]), _pad(project, widths[1]),
             _pad(pid, widths[2]), _pad(age, widths[3]), detail]
        )
        out.append(f"{_ANSI[state]}{line}{_RESET}" if colour else line)

    attention = [s for s in sessions if s.state.needs_attention]
    summary = f"\n{len(sessions)} 个会话"
    if attention:
        summary += f" · {len(attention)} 个需要你处理"
    out.append(summary)
    return "\n".join(out)


def cmd_ps(args: argparse.Namespace) -> int:
    colour = _enable_ansi() and not args.no_color
    once = not args.watch
    try:
        while True:
            sessions = registry.scan()
            transcript.enrich(sessions)
            text = render(sessions, colour=colour)
            if once:
                print(text)
                return 0
            # Clear and repaint rather than scrolling.
            sys.stdout.write("\033[2J\033[H" if colour else "\n" * 3)
            print(text, flush=True)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        return 0


def main(argv: list[str] | None = None) -> int:
    _force_utf8()
    # Minimal logging so the graceful-shutdown breadcrumbs are visible when
    # the app is launched from a terminal.
    import logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        stream=sys.stderr,
    )
    parser = argparse.ArgumentParser(
        prog="ccmon", description="监控正在运行的 Claude Code 会话状态"
    )
    parser.add_argument("--version", action="version", version=f"ccmon {__version__}")
    sub = parser.add_subparsers(dest="command")

    ps = sub.add_parser("ps", help="列出所有会话及其状态")
    ps.add_argument("-w", "--watch", action="store_true", help="持续刷新")
    ps.add_argument("-n", "--interval", type=float, default=1.5, help="刷新间隔秒数")
    ps.add_argument("--no-color", action="store_true", help="禁用颜色")
    ps.set_defaults(func=cmd_ps)

    tray = sub.add_parser("tray", help="常驻系统托盘")
    tray.set_defaults(func=lambda _: _run_tray())

    pet = sub.add_parser("pet", help="桌面宠物")
    pet.set_defaults(func=lambda _: _run_pet())

    both = sub.add_parser("both", help="托盘 + 桌面宠物一起跑")
    both.set_defaults(func=lambda _: _run_both())

    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        args = parser.parse_args((argv or []) + ["ps"])
    return args.func(args)


def _run_tray() -> int:
    """Boot the engine, hand it to the pystray UI, block until quit."""
    import asyncio
    from pathlib import Path

    from .engine import Engine
    from .notify.dedupe import Notifier, apply_mute
    from .ui.app import run as run_tray
    from .win.activate import jump_to_session

    notifier = Notifier()
    notifier.on_jump = lambda pid, cwd: jump_to_session(pid, cwd)
    pet_visible = {"v": False}

    def do_jump(pid: int) -> None:
        for s in notifier._pending.values():  # noqa: SLF001 - intentional, used for context lookup
            pass
        # Resolve the session's cwd from the latest tick.
        tick = engine.latest()
        if tick is None:
            return
        match = next((s for s in tick.sessions if s.pid == pid), None)
        if match is None:
            return
        jump_to_session(pid, match.cwd)

    def do_open_transcript(transcript: str) -> None:
        if transcript and Path(transcript).exists():
            import os
            os.startfile(transcript)  # noqa: S606

    def do_copy_id(sid: str) -> None:
        if not sid:
            return
        import ctypes

        ctypes.windll.user32.OpenClipboard(0)
        try:
            ctypes.windll.user32.EmptyClipboard()
            handle = ctypes.windll.kernel32.GlobalAlloc(0x0002, len(sid.encode("utf-16-le")) + 2)
            ctypes.windll.kernel32.GlobalLock(handle)
            ctypes.windll.user32.SetClipboardData(13, handle)  # CF_UNICODETEXT
        finally:
            ctypes.windll.user32.CloseClipboard()

    def do_mute(pid: int) -> None:
        apply_mute(notifier, pid, True)

    engine = Engine(interval=1.5)
    engine.subscribe(notifier.on_tick)

    # Notifier escalation timers run on their own asyncio loop in a worker
    # thread, so they survive the tray event loop's quirks on Windows.
    import threading

    loop = asyncio.new_event_loop()
    notifier._loop = loop  # noqa: SLF001 - share the loop with escalation timers

    def loop_thread():
        asyncio.set_event_loop(loop)
        loop.run_forever()

    threading.Thread(target=loop_thread, name="ccmon-notify", daemon=True).start()

    callbacks = {
        "jump": do_jump,
        "copy_id": do_copy_id,
        "open_transcript": do_open_transcript,
        "mute": do_mute,
        "toggle_pet": lambda: pet_visible.update({"v": not pet_visible["v"]}),
        "pet_visible": lambda: pet_visible["v"],
        "open_settings": lambda: None,
        "refresh": lambda: None,
    }

    async def main():
        run_tray(engine, callbacks)

    asyncio.run(main())
    loop.call_soon_threadsafe(loop.stop)
    return 0


def _run_pet() -> int:
    """Launch the desktop pet only -- no tray, no notifications."""
    from .engine import Engine
    from .ui.pet.run import run as run_pet

    engine = Engine(interval=1.5)
    # run_pet will call engine.start() via the PetBridge, but call it here
    # too so the scanner runs even before the pet window's event loop
    # has a chance to subscribe.
    engine.start()
    return run_pet(engine)


def _run_both() -> int:
    """Tray + pet sharing one engine."""
    import asyncio
    import logging
    import signal
    import threading

    from .engine import Engine
    from .notify.dedupe import Notifier
    from .ui.app import run as run_tray
    from .ui.pet.run import run as run_pet

    engine = Engine(interval=1.5)
    notifier = Notifier()
    notifier.on_jump = lambda pid, cwd: jump_to_session(pid, cwd)
    engine.subscribe(notifier.on_tick)

    loop = asyncio.new_event_loop()
    notifier._loop = loop  # noqa: SLF001

    def loop_thread():
        asyncio.set_event_loop(loop)
        loop.run_forever()

    threading.Thread(target=loop_thread, name="ccmon-notify", daemon=True).start()

    # Run the pet on the main thread (Qt needs it); the tray runs on a worker.
    def tray_in_thread():
        callbacks = {
            "jump": lambda pid: _jump_via_engine(engine, pid),
            "copy_id": _copy_id,
            "open_transcript": _open_transcript,
            "mute": lambda pid: notifier.muted.add(pid),
            "toggle_pet": lambda: None,
            "pet_visible": lambda: True,
            "open_settings": lambda: None,
            "refresh": lambda: None,
        }
        run_tray(engine, callbacks)

    tray_thread = threading.Thread(target=tray_in_thread, name="ccmon-tray", daemon=True)
    tray_thread.start()

    # Wire Ctrl+C / SIGTERM to a clean shutdown that stops both the
    # engine and the notifier loop, then quits the Qt app. Qt's event
    # loop ignores SIGINT by default on Windows, so the pet's signal
    # handler in ui/pet/run.py is the real exit trigger; this one is a
    # backup that runs in the main thread.
    def _shutdown(*_a) -> None:
        logging.getLogger("ccmon").info("shutdown: signal received, cleaning up")
        try:
            engine.stop()
        except Exception:  # noqa: BLE001
            pass
        try:
            loop.call_soon_threadsafe(loop.stop)
        except Exception:  # noqa: BLE001
            pass
        # QApplication may not exist yet if signal fires before run_pet starts.
        try:
            from PySide6.QtWidgets import QApplication
            app = QApplication.instance()
            if app is not None:
                app.quit()
        except Exception:  # noqa: BLE001
            pass

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    code = run_pet(engine)
    # run_pet returned (likely because of graceful_quit from its own
    # signal handler) -- make sure the other threads are stopped.
    try:
        engine.stop()
    except Exception:  # noqa: BLE001
        pass
    try:
        loop.call_soon_threadsafe(loop.stop)
    except Exception:  # noqa: BLE001
        pass
    logging.getLogger("ccmon").info("shutdown: both modes exited with code %s", code)
    return code


def _jump_via_engine(engine, pid: int) -> None:
    from .win.activate import jump_to_session

    tick = engine.latest()
    if not tick:
        return
    for s in tick.sessions:
        if s.pid == pid:
            jump_to_session(pid, s.cwd)
            return


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


def _open_transcript(transcript: str) -> None:
    if transcript and Path(transcript).exists():
        os.startfile(transcript)  # noqa: S606


if __name__ == "__main__":
    raise SystemExit(main())
