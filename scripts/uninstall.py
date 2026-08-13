"""`python -m scripts.uninstall` -- remove auto-start."""

from __future__ import annotations

from ccmon.win.autostart import installed, remove


def main() -> int:
    if not installed():
        print("ccmon is not in auto-start.")
        return 0
    if remove():
        print("ccmon removed from auto-start.")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
