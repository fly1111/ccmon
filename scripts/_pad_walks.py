"""Add a transparent border around existing walk_<N>_alpha.png frames.

Why: mmx video tends to render the subject filling 90%+ of the source
frame, which leaves no room for the bbox crop to add headroom. Result:
the cat's paws / head / tail touch the canvas edge.

Without re-running mmx (quota is 3 calls/day), we can post-process the
existing 192x192 frames: paste them onto a larger transparent canvas
with the cat centred. The visible "cat" shrinks proportionally, but
head/tail are now safely inside the new canvas with margin on all
sides.

Output size and margin are tunable; default 256x256 gives 32 px
transparent border around the 192x192 source -- which reads as ~25%
padding.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PIL import Image

REPO = Path("D:/vscodepro/ccmon")
DEFAULT_OUT = 256


def pad_frame(p: Path, out_size: int) -> tuple[int, int, int, int] | None:
    """Paste a 192x192 alpha frame onto a larger transparent canvas.

    Returns the alpha-bbox in the new canvas (or None if p is empty).
    """
    img = Image.open(p).convert("RGBA")
    canvas = Image.new("RGBA", (out_size, out_size), (0, 0, 0, 0))
    ox = (out_size - img.size[0]) // 2
    oy = (out_size - img.size[1]) // 2
    canvas.paste(img, (ox, oy), img)
    canvas.save(p)
    px = canvas.load()
    w, h = canvas.size
    min_x, min_y, max_x, max_y = w, h, 0, 0
    for y in range(h):
        for x in range(w):
            if px[x, y][3] > 8:
                if x < min_x:
                    min_x = x
                if y < min_y:
                    min_y = y
                if x > max_x:
                    max_x = x
                if y > max_y:
                    max_y = y
    if min_x >= max_x or min_y >= max_y:
        return None
    return (min_x, min_y, max_x, max_y)


def pad_style(style: str, out_size: int) -> None:
    style_dir = REPO / "assets" / "pet" / style
    if not style_dir.is_dir():
        print(f"  {style}: dir missing")
        return
    frames = sorted(style_dir.glob("walk_*_alpha.png"))
    frames = [f for f in frames if not (
        f.name.startswith("walk_L_") or f.name.startswith("walk_R_")
    )]
    if not frames:
        print(f"  {style}: no walk frames")
        return
    print(f"  {style}: {len(frames)} frames -> {out_size}x{out_size}")
    for f in frames:
        bb = pad_frame(f, out_size)
        if bb is None:
            continue
        bw = bb[2] - bb[0]
        bh = bb[3] - bb[1]
        cx = (bb[0] + bb[2]) // 2
        cy = (bb[1] + bb[3]) // 2
        print(
            f"    {f.name}  cat={bw}x{bh}  "
            f"margins T={bb[1]} B={out_size-bb[3]} L={bb[0]} R={out_size-bb[2]}  "
            f"center=({cx},{cy})"
        )


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("styles", nargs="*")
    p.add_argument("--all", action="store_true")
    p.add_argument("--size", type=int, default=DEFAULT_OUT,
                   help=f"output canvas size (default {DEFAULT_OUT})")
    args = p.parse_args()
    if args.all:
        styles = [
            d.name for d in (REPO / "assets" / "pet").iterdir()
            if d.is_dir() and any(
                f for f in d.glob("walk_*_alpha.png")
                if not (f.name.startswith("walk_L_") or f.name.startswith("walk_R_"))
            )
        ]
    else:
        styles = args.styles
    if not styles:
        print("usage: _pad_walks.py style1 style2 ...  (or --all)",
              file=sys.stderr)
        return 1
    for s in styles:
        pad_style(s, args.size)
    return 0


if __name__ == "__main__":
    sys.exit(main())
