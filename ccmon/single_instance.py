"""Single-instance lock for ccmon.

Without this, two `ccmon both` invocations each open their own
PetWindow, leading to two stacked bubbles (and double notifications,
double tray icons, etc.).

Strategy: open the lockfile with O_CREAT | O_EXCL -- this fails
atomically on POSIX if the file exists, and on Windows it fails if
the file exists OR if another process has it open for writing
(exclusive create). Either way, the second invocation hits a clear
"someone's here" path.

The lockfile carries the owning PID so a stale lock from a crashed
process can be detected (PID no longer alive) and broken.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from .paths import data_dir

LOCK_NAME = "ccmon"


def _lock_path() -> Path:
    return data_dir() / f"{LOCK_NAME}.lock"


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        if sys.platform == "win32":
            import ctypes

            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            handle = kernel32.OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION, False, pid,
            )
            if handle:
                kernel32.CloseHandle(handle)
                return True
            return False
        os.kill(pid, 0)
        return True
    except (OSError, PermissionError):
        return False


class SingleInstance:
    """File-based exclusive lock via O_CREAT | O_EXCL."""

    def __init__(self, name: str = LOCK_NAME) -> None:
        self._path = data_dir() / f"{name}.lock"
        self.holder_pid: int | None = None
        self._acquired = False

    def acquire(self) -> bool:
        """Try to acquire the lock. Returns True on success.

        On contention: read the existing PID from the file (if readable)
        and verify it is still alive. A stale lockfile (PID dead) is
        broken and the lock is retried once.
        """
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # First attempt: atomic exclusive create.
        try:
            fd = os.open(
                str(self._path),
                os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_TRUNC,
                0o600,
            )
        except FileExistsError:
            # File exists. Read its PID and decide whether to break it.
            existing = self._read_existing_pid()
            if existing and not _pid_alive(existing):
                # Stale: the owner is dead, take over.
                try:
                    self._path.unlink()
                except OSError:
                    pass
                try:
                    fd = os.open(
                        str(self._path),
                        os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_TRUNC,
                        0o600,
                    )
                except FileExistsError:
                    self.holder_pid = None
                    return False
            else:
                # Locked by a live process; report the holder.
                self.holder_pid = existing
                return False

        # We hold the lock. Write our PID for the next contender.
        try:
            os.write(fd, f"{os.getpid()}\n".encode("utf-8"))
            os.fsync(fd)
        except OSError:
            pass
        finally:
            os.close(fd)
        self._acquired = True
        return True

    def release(self) -> None:
        if not self._acquired:
            return
        try:
            self._path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass
        self._acquired = False

    def _read_existing_pid(self) -> int | None:
        try:
            text = self._path.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        try:
            return int(text.split()[0])
        except (ValueError, IndexError):
            return None