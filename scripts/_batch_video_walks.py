"""Run _video_walk.py for several styles in one batch.

Usage:
    python scripts/_batch_video_walks.py blackcat orangecat persian

Or edit STYLES_BELOW for the default batch (today's quota).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path("D:/vscodepro/ccmon")

# Default batch: the 3 most-used cat styles. Override with CLI args.
DEFAULT_STYLES = ["blackcat", "orangecat", "persian"]


def main(argv: list[str]) -> int:
    styles = argv[1:] if len(argv) > 1 else DEFAULT_STYLES
    print(f"=== batch: {styles} ===", flush=True)
    failed: list[str] = []
    for style in styles:
        print(f"\n--- {style} ---", flush=True)
        result = subprocess.run(
            [
                str(REPO / ".venv/Scripts/python.exe"),
                str(REPO / "scripts/_video_walk.py"),
                style,
            ],
            cwd=str(REPO),
        )
        if result.returncode != 0:
            failed.append(style)
    if failed:
        print(f"\n=== failed: {failed} ===", flush=True)
        return 1
    print("\n=== batch done ===", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
