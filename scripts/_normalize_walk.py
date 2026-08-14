"""Normalize walk frame positions: bbox-centre each frame so 4-frame cycles
read as one cat walking in place, not 4 different framings.

For each walk_1..4_alpha.png in every style:
  - find non-transparent bbox
  - compute union bbox across all 4 frames (so the same crop window is
    applied to every frame; cat stays at the same on-canvas position
    frame-to-frame)
  - centre-crop to 1:1 at the union bbox
  - resize to 192x192 (PET_SIZE)

Result: 4 frames in the cycle have the cat at the same on-canvas
position and size, eliminating the "jumping around" visual.
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

REPO = Path("D:/vscodepro/ccmon")
OUT_SIZE = 192


def cat_bbox(img: Image.Image) -> tuple[int, int, int, int]:
    """Bounding box of all non-transparent pixels."""
    w, h = img.size
    px = img.load()
    min_x, min_y, max_x, max_y = w, h, 0, 0
    for y in range(h):
        for x in range(w):
            r, g, b, a = px[x, y]
            if a > 8:  # treat near-transparent as background
                if x < min_x:
                    min_x = x
                if y < min_y:
                    min_y = y
                if x > max_x:
                    max_x = x
                if y > max_y:
                    max_y = y
    return (min_x, min_y, max_x, max_y)


def union_bbox(bboxes: list[tuple[int, int, int, int]]) -> tuple[int, int, int, int]:
    xs = [b[0] for b in bboxes]
    ys = [b[1] for b in bboxes]
    Xs = [b[2] for b in bboxes]
    Ys = [b[3] for b in bboxes]
    return (min(xs), min(ys), max(Xs), max(ys))


def normalize_style(style: str) -> None:
    """Centre each walk frame's cat independently.

    Each frame gets its own bbox; the cat is cropped to a square window
    centred on its own bbox centre, then resized to OUT_SIZE. The
    trade-off: the cat appears at the same on-canvas position in every
    frame (centre), but its size may differ slightly between frames
    (because each frame's cat is a different size in the source).

    That's a better trade than the union-bbox approach: union-bbox
    meant the cat is at the same place on canvas, but if a frame's cat
    bbox was off-centre, the clamp to image bounds pushed the cropped
    cat off-centre too.
    """
    style_dir = REPO / "assets" / "pet" / style
    if not style_dir.is_dir():
        return
    paths = [style_dir / f"walk_{i}_alpha.png" for i in range(1, 5)]
    paths = [p for p in paths if p.exists()]
    if len(paths) < 4:
        print(f"  {style}: skip ({len(paths)}/4 walk frames)")
        return

    imgs = [Image.open(p).convert("RGBA") for p in paths]
    for p, img in zip(paths, imgs):
        bb = cat_bbox(img)
        if bb[0] >= bb[2] or bb[1] >= bb[3]:
            print(f"  {style}: {p.name} has empty bbox")
            continue
        bw = bb[2] - bb[0]
        bh = bb[3] - bb[1]
        side = max(bw, bh)
        pad = int(side * 0.10)
        side_padded = side + 2 * pad
        cx = (bb[0] + bb[2]) // 2
        cy = (bb[1] + bb[3]) // 2
        x0 = max(0, cx - side_padded // 2)
        y0 = max(0, cy - side_padded // 2)
        x1 = min(img.size[0], x0 + side_padded)
        y1 = min(img.size[1], y0 + side_padded)
        # If we hit the right/bottom edge before reaching the desired
        # side length, shift the box left/up so the centre stays put.
        if x1 - x0 < side_padded:
            x0 = max(0, x1 - side_padded)
        if y1 - y0 < side_padded:
            y0 = max(0, y1 - side_padded)
        cropped = img.crop((x0, y0, x0 + side_padded, y0 + side_padded)).resize(
            (OUT_SIZE, OUT_SIZE), Image.LANCZOS
        )
        cropped.save(p)
    print(f"  {style}: 4 frames independently centred")


for s in sys.argv[1:]:
    normalize_style(s)
