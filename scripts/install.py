"""`python -m scripts.install` -- enable ccmon to start at logon."""

from __future__ import annotations

import sys

from ccmon.win.autostart import install, installed


def main() -> int:
    if installed():
        print("ccmon already in auto-start.")
        return 0
    if install():
        print("ccmon installed in HKCU\\...\\Run -- will start at next logon.")
        return 0
    print("failed to install auto-start entry.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
