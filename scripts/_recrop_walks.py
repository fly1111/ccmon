"""Re-crop existing walk_<N>_alpha.png frames with per-style padding.

Why this exists:
  The original _video_walk.py uses 10% padding, which is fine when the
  rendered cat is small inside the 768x768 source frame (lots of head/
  tail room left over). But for some styles the bounding-box hits the
  full source width (1366 -> bbox may be ~1200 wide), and after cropping
  the cat ends up touching the 192x192 canvas edges -- head and tail
  visibly clipped.

  Quick fix without re-running mmx video (quota): just re-crop the
  existing walk frames with a bigger padding.

Usage:
    python scripts/_recrop_walks.py blackshiba crocodile tiger
    python scripts/_recrop_walks.py --all
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PIL import Image

REPO = Path("D:/vscodepro/ccmon")
OUT_SIZE = 192

# Per-style padding fraction. Defaults to 0.25 (=25% on each side of
# the bbox); tighter covers won't be attempted via this script -- if
# you want less padding, edit the dict.
DEFAULT_PAD = 0.25
PAD: dict[str, float] = {
    "blackshiba": 0.30,  # full-width bbox in source, was barely fitting
    "crocodile":  0.30,  # long thin body, head/tail touch edges
    "tiger":      0.30,  # same as crocodile
}


def alpha_bbox(img: Image.Image) -> tuple[int, int, int, int]:
    w, h = img.size
    px = img.load()
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
    return (min_x, min_y, max_x, max_y)


def recrop_style(style: str) -> None:
    """Use the UNION bbox across all 12 frames so every frame crops to the
    same window. Trade-off: the cat's on-canvas position is stable from
    frame to frame (no jitter) but if one frame's tail sticks out further
    than another's, the smaller ones get padded with empty space -- which
    is exactly what we want for "don't crop heads/tails".

    Before union-bbox: per-frame centring meant a frame where the cat
    was already hugging the source edge would push the crop window to
    the clamped side, leaving the cat shifted off-canvas-centre.
    """
    style_dir = REPO / "assets" / "pet" / style
    if not style_dir.is_dir():
        print(f"  {style}: dir missing", flush=True)
        return
    frames = sorted(style_dir.glob("walk_*_alpha.png"))
    frames = [f for f in frames if not (
        f.name.startswith("walk_L_") or f.name.startswith("walk_R_")
    )]
    if not frames:
        print(f"  {style}: no walk frames", flush=True)
        return
    pad = PAD.get(style, DEFAULT_PAD)
    print(f"  {style}: {len(frames)} frames, union-bbox pad={pad:.0%}",
          flush=True)

    # 1. Compute union bbox across all frames (in source-image coords).
    imgs = [Image.open(f).convert("RGBA") for f in frames]
    bboxes = [alpha_bbox(img) for img in imgs]
    valid = [b for b in bboxes if b[0] < b[2] and b[1] < b[3]]
    if not valid:
        print(f"  {style}: all frames empty, skip", flush=True)
        return
    u_min_x = min(b[0] for b in valid)
    u_min_y = min(b[1] for b in valid)
    u_max_x = max(b[2] for b in valid)
    u_max_y = max(b[3] for b in valid)
    bw = u_max_x - u_min_x
    bh = u_max_y - u_min_y
    side = max(bw, bh)
    pad_px = int(side * pad)
    side_padded = side + 2 * pad_px
    cx = (u_min_x + u_max_x) // 2
    cy = (u_min_y + u_max_y) // 2
    x0 = max(0, cx - side_padded // 2)
    y0 = max(0, cy - side_padded // 2)
    x1 = x0 + side_padded
    y1 = y0 + side_padded
    # If the union bbox already hit a source edge and we can't get full
    # padded width on one side, shift to take the loss on the OTHER side
    # (so the cat stays centred on what's available).
    src_w = imgs[0].size[0]
    src_h = imgs[0].size[1]
    if x1 > src_w:
        x0 = max(0, src_w - side_padded)
        x1 = x0 + side_padded
    if y1 > src_h:
        y0 = max(0, src_h - side_padded)
        y1 = y0 + side_padded
    box = (x0, y0, x1, y1)
    print(f"    union {bw}x{bh} -> crop {side_padded}x{side_padded} at {box}",
          flush=True)

    # 2. Same box for every frame.
    for f, img in zip(frames, imgs):
        cropped = img.crop(box).resize((OUT_SIZE, OUT_SIZE), Image.LANCZOS)
        cropped.save(f)
        print(f"    {f.name}", flush=True)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("styles", nargs="*")
    p.add_argument("--all", action="store_true",
                   help="Recrop every style that has walk frames")
    args = p.parse_args()
    if args.all:
        styles = []
        for d in (REPO / "assets" / "pet").iterdir():
            if d.is_dir() and any(d.glob("walk_*_alpha.png")):
                # skip walk_L_/walk_R_-only dirs
                ok = [
                    f for f in d.glob("walk_*_alpha.png")
                    if not (f.name.startswith("walk_L_") or f.name.startswith("walk_R_"))
                ]
                if ok:
                    styles.append(d.name)
    else:
        styles = args.styles
    if not styles:
        print("usage: _recrop_walks.py style1 style2 ...  (or --all)",
              file=sys.stderr)
        return 1
    for s in styles:
        recrop_style(s)
    return 0


if __name__ == "__main__":
    sys.exit(main())
