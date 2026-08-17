"""Center the cat in each walk frame and add uniform padding.

Some of the existing walk_<N>_alpha.png frames have the cat drawn
off-centre in the source render -- after a bbox-centre crop the
alpha-bbox sits in one corner of the canvas (e.g. L=37 R=1), which
means the cat's tail (right side) is clipped.

This script fixes that by:
  1. Finding the alpha-bbox of each frame
  2. Computing a target canvas with N% padding around the bbox
  3. Centring the bbox in the new canvas (shifting if necessary)
  4. Saving the new frame

Per-frame centring (rather than union-bbox) keeps each frame's
motion legible -- but means the cat may shift slightly between
frames. Trade-off: stable frame-to-frame position vs consistent
on-canvas centring. We chose consistent centring because the user
feedback was specifically about clipping, not jitter.

Output canvas size and padding are tunable per-style.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PIL import Image

REPO = Path("D:/vscodepro/ccmon")
DEFAULT_OUT = 256

# Per-style padding fraction (around the bbox, each side).
# 0.30 = 30% on each side. Total bbox-side becomes 1.6x original.
# Styles with severe off-centre rendering need more padding.
PAD: dict[str, float] = {
    "luna":      0.45,  # long fur + tail, needs room
    "persian":   0.40,  # long fluffy body
    "blackcat":  0.35,
    "orangecat": 0.35,
    "peter2":    0.35,
    # The 3 padded-by-_pad_walks styles still have cat filling most
    # of the canvas; bump them up too so the cat is smaller.
    "blackshiba": 0.20,
    "tiger":      0.20,
    "crocodile":  0.20,
}
DEFAULT_PAD = 0.30


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


def recenter_frame(p: Path, out_size: int, pad: float) -> dict:
    """Re-centre `p` so the alpha-bbox is at the canvas centre.

    pad = padding fraction around the bbox (per side). e.g. 0.30 means
    the canvas is bbox_side * 1.6.

    out_size: target canvas side. If bbox + 2*padding fits in this,
    use it; otherwise expand to bbox + 2*padding (cap at out_size).

    Returns diagnostic dict.
    """
    img = Image.open(p).convert("RGBA")
    src_w, src_h = img.size
    bb = alpha_bbox(img)
    if bb[0] >= bb[2] or bb[1] >= bb[3]:
        return {"ok": False, "reason": "empty bbox"}
    bw = bb[2] - bb[0]
    bh = bb[3] - bb[1]
    bbox_side = max(bw, bh)
    pad_px = int(round(bbox_side * pad))
    crop_side = bbox_side + 2 * pad_px
    crop_side = min(out_size, max(crop_side, bbox_side))  # never shrink bbox

    # Source crop box: centred on the bbox, sized to crop_side.
    # If the bbox is off-centre in source, we may need to extend
    # beyond source bounds; the new transparent canvas handles that.
    bbox_cx = (bb[0] + bb[2]) // 2
    bbox_cy = (bb[1] + bb[3]) // 2
    src_x0 = bbox_cx - crop_side // 2
    src_y0 = bbox_cy - crop_side // 2
    src_x1 = src_x0 + crop_side
    src_y1 = src_y0 + crop_side

    # Paste the relevant slice of `img` onto a fresh transparent
    # canvas of size crop_side. Out-of-bounds slices become transparent.
    canvas = Image.new("RGBA", (crop_side, crop_side), (0, 0, 0, 0))
    # Clip the source slice to actual image bounds.
    slice_x0 = max(0, src_x0)
    slice_y0 = max(0, src_y0)
    slice_x1 = min(src_w, src_x1)
    slice_y1 = min(src_h, src_y1)
    if slice_x1 > slice_x0 and slice_y1 > slice_y0:
        slice_img = img.crop((slice_x0, slice_y0, slice_x1, slice_y1))
        paste_x = slice_x0 - src_x0
        paste_y = slice_y0 - src_y0
        canvas.paste(slice_img, (paste_x, paste_y), slice_img)

    # If the source was off-centre and the bbox can't fit in crop_side,
    # we lose the bbox edges. That's the original-clipping problem.
    canvas.save(p)

    new_bb = alpha_bbox(canvas)
    return {
        "ok": True,
        "src_bb": bb,
        "src_size": (src_w, src_h),
        "crop_side": crop_side,
        "new_bb": new_bb,
        "pad_applied": pad_px,
    }


def recenter_style(style: str, out_size: int, pad: float) -> None:
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
    style_pad = PAD.get(style, pad)
    print(f"  {style}: {len(frames)} frames, pad={style_pad:.0%}, "
          f"target={out_size}", flush=True)
    for f in frames:
        result = recenter_frame(f, out_size, style_pad)
        if not result["ok"]:
            print(f"    {f.name}: SKIP ({result['reason']})", flush=True)
            continue
        nb = result["new_bb"]
        if nb[0] >= nb[2]:
            print(f"    {f.name}: empty after recenter", flush=True)
            continue
        bw = nb[2] - nb[0]
        bh = nb[3] - nb[1]
        nw = result["crop_side"]
        margins = (nb[0], nw - nb[2], nb[1], nw - nb[3])
        print(
            f"    {f.name}  cat={bw}x{bh}  "
            f"L={margins[0]} R={margins[1]} T={margins[2]} B={margins[3]}  "
            f"canvas={nw}x{nw}",
            flush=True,
        )


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("styles", nargs="*")
    p.add_argument("--all", action="store_true")
    p.add_argument("--size", type=int, default=DEFAULT_OUT,
                   help=f"target canvas side (default {DEFAULT_OUT})")
    p.add_argument("--pad", type=float, default=DEFAULT_PAD,
                   help=f"padding fraction (default {DEFAULT_PAD})")
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
        print("usage: _center_pad_walks.py style1 style2 ...  (or --all)",
              file=sys.stderr)
        return 1
    for s in styles:
        recenter_style(s, args.size, args.pad)
    return 0


if __name__ == "__main__":
    sys.exit(main())