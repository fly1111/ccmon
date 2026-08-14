"""Post-process: chroma key + mirror walk + cleanup for completed animals."""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path("D:/vscodepro/ccmon")


def chroma_key(img):
    px = img.load()
    w, h = img.size
    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    out_px = out.load()
    for y in range(h):
        for x in range(w):
            r, g, b = px[x, y][:3]
            excess = min(g - r, g - b)
            if excess <= 8:
                a = 255
            elif excess >= 60:
                a = 0
            else:
                a = int(255 * (60 - excess) / 52)
            if a > 0:
                out_px[x, y] = (r, g, b, a)
    return out


from PIL import Image, ImageOps


def finalize(style: str) -> None:
    style_dir = REPO / "assets" / "pet" / style
    if not style_dir.is_dir():
        print(f"  {style}: skip (no dir)")
        return

    # 4 walk key frames -> chroma key + mirror (face left) + resize 192x192
    for i in range(1, 5):
        raw = style_dir / f"walk_{i}_raw.png"
        if not raw.exists():
            print(f"  {style}/walk_{i}_raw.png: skip (missing)")
            continue
        keyed = chroma_key(Image.open(raw).convert("RGB"))
        mirrored = ImageOps.mirror(keyed)
        mirrored.resize((192, 192), Image.LANCZOS).save(
            style_dir / f"walk_{i}_alpha.png"
        )
        raw.unlink()

    # 5 mood frames -> chroma key (no mirror -- front view)
    for state in ("happy", "anxious", "sad", "sleepy", "alert"):
        raw = style_dir / f"{state}_raw.png"
        if not raw.exists():
            print(f"  {style}/{state}_raw.png: skip (missing)")
            continue
        chroma_key(Image.open(raw).convert("RGB")).save(
            style_dir / f"{state}_alpha.png"
        )
        raw.unlink()

    print(f"  {style}: finalized")


for s in sys.argv[1:]:
    finalize(s)
