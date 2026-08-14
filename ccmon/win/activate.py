"""Resolve a session pid to a top-level window and bring it to the foreground.

Strategy cascade (first hit wins):
  1. IDE lock with matching workspace basename -> windows owned by that pid
  2. psutil parents walk: first ancestor with a visible top-level window
  3. Window-title fuzzy match anywhere on the desktop

Windows-only. On failure we open the session's project folder in Explorer as
a last resort -- the user gets to their files even when we can't get their
window.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import subprocess
import sys
from dataclasses import dataclass

import psutil

from ..ide_lock import match_ide_for_session

if sys.platform != "win32":
    raise RuntimeError("ccmon win helpers are Windows-only")

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

_WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)

# Don't walk past these: the goal is the user's actual terminal window, not
# their desktop or a system service.
_STOP_NAMES = frozenset({"explorer.exe", "services.exe", "svchost.exe", "winlogon.exe"})


@dataclass
class Window:
    hwnd: int
    pid: int
    title: str


def _visible_top_level_windows_for_pid(pid: int, *, cwd: str = "") -> list[Window]:
    """All visible top-level windows owned by pid, ordered by best-match
    first. Callers that don't pass cwd get the raw EnumWindows order.

    Match priority (first hit wins):
      1. title contains the cwd's basename (e.g. WT tab that set
         its title to the project path, or VS Code window title
         which shows the workspace folder)
      2. title contains "claude" (catches WT tabs that didn't
         update the title to the cwd, but are running Claude Code)
      3. first window as captured by EnumWindows
    """
    EnumWindows = user32.EnumWindows
    GetWindowThreadProcessId = user32.GetWindowThreadProcessId
    IsWindowVisible = user32.IsWindowVisible
    GetWindowTextW = user32.GetWindowTextW
    GetWindowTextLengthW = user32.GetWindowTextLengthW

    cwd_needle = ""
    if cwd:
        cwd_needle = os.path.basename(cwd.replace("/", "\\").rstrip("\\")).casefold()
    cwd_hits: list[Window] = []
    claude_hits: list[Window] = []
    rest: list[Window] = []

    def cb(hwnd, _lparam):
        owner_pid = wt.DWORD()
        GetWindowThreadProcessId(hwnd, ctypes.byref(owner_pid))
        if owner_pid.value != pid:
            return True
        if not IsWindowVisible(hwnd):
            return True
        length = GetWindowTextLengthW(hwnd)
        if length == 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value
        if not title.strip():
            return True
        w = Window(hwnd=hwnd, pid=pid, title=title)
        title_lc = title.casefold()
        if cwd_needle and cwd_needle in title_lc:
            cwd_hits.append(w)
        elif "claude" in title_lc:
            claude_hits.append(w)
        else:
            rest.append(w)
        return True

    EnumWindows(_WNDENUMPROC(cb), 0)
    return cwd_hits + claude_hits + rest


def _resolve_window_for_pid(pid: int, cwd: str = "") -> Window | None:
    """Pick the best top-level window for a pid (with cwd filter)."""
    windows = _visible_top_level_windows_for_pid(pid, cwd=cwd)
    if not windows:
        return None
    return windows[0]


def _ancestors(pid: int, *, max_depth: int = 10) -> list[int]:
    """[pid, parent, grandparent, ...] stopping before explorer or max depth."""
    chain: list[int] = []
    try:
        proc = psutil.Process(pid)
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return chain
    chain.append(pid)
    current = proc
    for _ in range(max_depth):
        try:
            current = current.parent()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            break
        if current is None:
            break
        try:
            name = (current.name() or "").casefold()
        except psutil.Error:
            name = ""
        if name in _STOP_NAMES:
            break
        chain.append(current.pid)
    return chain


def _console_hwnd_for_pid(pid: int) -> int:
    """Return the Win32 console window hwnd owned by `pid`, or 0.

    Uses AttachConsole(pid) + GetConsoleWindow. The process's
    console window is the WT tab / cmd / powershell / ConPTY in
    which it lives, regardless of psutil's parent chain. Freeing
    and re-attaching the calling process's own console around
    the call keeps ccmon's own console intact.
    """
    kernel32 = ctypes.windll.kernel32
    orig_hwnd = kernel32.GetConsoleWindow()
    try:
        if orig_hwnd:
            kernel32.FreeConsole()
        if not kernel32.AttachConsole(pid):
            return 0
        hwnd = kernel32.GetConsoleWindow()
        return hwnd
    finally:
        # Detach from target console and re-attach to our own.
        kernel32.FreeConsole()
        if orig_hwnd:
            kernel32.AttachConsole(0xFFFFFFFF)  # ATTACH_PARENT_PROCESS


def _wt_for_session(pid: int) -> int | None:
    """Find the WindowsTerminal.exe (or wt.exe) PID hosting this pid.

    Strategy cascade:
      1. Win32 AttachConsole + GetConsoleWindow on `pid` directly.
         This is the only fully reliable method -- it returns the
         actual console hwnd regardless of who owns the parent
         chain (ConPTY in particular detaches the shell from wt
         in psutil's view).
      2. WT_SESSION env-var lookup. Useful as a fallback for
         callers that can't AttachConsole.
    """
    # Strategy 1: direct console hwnd -> process pid
    hwnd = _console_hwnd_for_pid(pid)
    if hwnd:
        owner_pid = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner_pid))
        if owner_pid.value:
            return owner_pid.value

    # Strategy 2: env-var WT_SESSION on every wt.exe's descendants
    try:
        proc = psutil.Process(pid)
        target_session = proc.environ().get("WT_SESSION")
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return None
    if not target_session:
        return None
    try:
        wt_candidates = [
            p for p in psutil.process_iter(["pid", "name"])
            if "wt.exe" in (p.info["name"] or "").casefold()
        ]
    except psutil.Error:
        return None
    for wt_proc in wt_candidates:
        try:
            for child in wt_proc.children(recursive=True):
                try:
                    if child.environ().get("WT_SESSION") == target_session:
                        return wt_proc.pid
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return None


def focus_window(hwnd: int) -> bool:
    """Bring a hwnd to the foreground. Returns True iff our hwnd is now foreground."""
    if not user32.IsWindow(hwnd):
        return False
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    target_pid = wt.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(target_pid))

    # Primary: ask Windows nicely. Works when both processes share a logon
    # session -- which is our normal case (the user's own window).
    user32.AllowSetForegroundWindow(target_pid.value)
    user32.SetForegroundWindow(hwnd)
    if user32.GetForegroundWindow() == hwnd:
        return True

    # Fallback: lift the foreground lock with the AttachThreadInput trick.
    fg = user32.GetForegroundWindow()
    fg_tid = user32.GetWindowThreadProcessId(fg, ctypes.byref(wt.DWORD()))
    our_tid = kernel32.GetCurrentThreadId()
    user32.AttachThreadInput(our_tid, fg_tid, True)
    try:
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
    finally:
        user32.AttachThreadInput(our_tid, fg_tid, False)
    return user32.GetForegroundWindow() == hwnd


def jump_to_session(pid: int, cwd: str) -> bool:
    """Resolve and focus. Returns True if we actually moved a window."""
    # 0. WT-specific: find the right wt.exe for this session's
    #    WT_SESSION. Necessary because in ConPTY mode the shell's
    #    parent is services.exe, not wt.exe, so a plain psutil parent
    #    walk would never reach wt. Without this we fall through to
    #    stage 3's "any visible window with the cwd in its title" and
    #    jump to whichever WT was focused last.
    wt_pid = _wt_for_session(pid)
    if wt_pid:
        window = _resolve_window_for_pid(wt_pid, cwd=cwd)
        if window:
            return focus_window(window.hwnd)

    # 1. IDE lock fast path
    binding = match_ide_for_session(cwd)
    if binding:
        # VS Code / Cursor: pick the window whose title contains the
        # matching workspace folder basename (so multi-window VS Code
        # doesn't jump to the wrong project).
        window = _resolve_window_for_pid(binding.pid, cwd=cwd)
        if window:
            return focus_window(window.hwnd)

    # 2. Process-tree walk
    for ancestor in _ancestors(pid):
        window = _resolve_window_for_pid(ancestor, cwd=cwd)
        if window:
            return focus_window(window.hwnd)

    # 3. Title fallback: any visible window whose title contains the cwd's basename.
    if cwd:
        needle = os.path.basename(cwd.replace("/", "\\").rstrip("\\")).casefold()
        if needle:
            for window in _all_visible_windows():
                if needle in window.title.casefold():
                    if focus_window(window.hwnd):
                        return True

    # 4. Last resort: open the project folder.
    if cwd and os.path.isdir(cwd):
        try:
            subprocess.Popen(["explorer", cwd])
            return False  # caller may want to know we fell back
        except OSError:
            pass
    return False


def _all_visible_windows() -> list[Window]:
    out: list[Window] = []

    def cb(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length == 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value
        if not title.strip():
            return True
        owner = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        out.append(Window(hwnd=hwnd, pid=owner.value, title=title))
        return True

    user32.EnumWindows(_WNDENUMPROC(cb), 0)
    return out
