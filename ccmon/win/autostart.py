"""Install / remove ccmon as a per-user auto-start entry.

We use the HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run registry key
because it is the simplest, least-surprising mechanism: the user can see it
in Task Manager -> Startup, and removing it is one `reg delete` away. Task
Scheduler would be more powerful but uglier; a Startup-folder shortcut is
visible but can't accept CLI args cleanly.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_RUN_KEY = r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run"
_VALUE = "ccmon"


def _command() -> str:
    """The exact command line Windows should run at user logon."""
    py = Path(sys.executable)
    # Prefer pythonw so no console window flashes; fall back to python.
    if sys.platform == "win32":
        pyw = py.with_name("pythonw.exe")
        if pyw.exists():
            py = pyw
    return f'"{py}" -m ccmon both'


def install() -> bool:
    if sys.platform != "win32":
        return False
    cmd = _command()
    # `reg add` exits 0 on success, 1 on "already exists without /f". Use /f.
    result = subprocess.run(
        ["reg", "add", _RUN_KEY, "/v", _VALUE, "/t", "REG_SZ", "/d", cmd, "/f"],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def remove() -> bool:
    if sys.platform != "win32":
        return False
    result = subprocess.run(
        ["reg", "delete", _RUN_KEY, "/v", _VALUE, "/f"],
        capture_output=True,
        text=True,
    )
    # Reg delete returns 1 if the value didn't exist; that is a successful "absent".
    return result.returncode in (0, 1)


def installed() -> bool:
    if sys.platform != "win32":
        return False
    result = subprocess.run(
        ["reg", "query", _RUN_KEY, "/v", _VALUE],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0
