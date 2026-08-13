"""Background removal tools for the pet sprite set.

Two strategies, automatically chosen:

  1. **Chroma key** -- if the image background is solid green (genuine green
     screen), we key out anything close to that colour. Pixel-perfect edges.

  2. **Outline barrier** -- if the background is white but the dog is drawn
     with a dark outline (the default for mmx output), we find the outline,
     flood from inside, and keep only interior pixels.

Auto-detection: scan the image border. If the dominant border colour is
greenish (G > R + 30 AND G > B + 30), use chroma key. Otherwise use outline.
"""

from __future__ import annotations

import sys
from collections import deque
from pathlib import Path

from PIL import Image

_DARK_THRESHOLD = 130


# ---------- chroma key ----------------------------------------------------


def _chroma_key_alpha(r: int, g: int, b: int) -> int:
    """Chroma-key green: any pixel dominated by green is background.

    Distance from pure green drives the alpha -- the closer to pure green,
    the more transparent. The threshold is low so we also catch the green
    shadow rim that mmx produces around the character.
    """
    excess = min(g - r, g - b)  # how much G leads R and B
    if excess <= 8:
        return 255  # not green-dominant
    if excess >= 60:
        return 0  # clearly green
    # Linear ramp 8..60 -> 255..0
    return int(255 * (60 - excess) / 52)


# ---------- outline barrier ---------------------------------------------


def _dark_components(img: Image.Image, threshold: int) -> list[set[tuple[int, int]]]:
    px = img.load()
    w, h = img.size
    visited = bytearray(w * h)
    components: list[set[tuple[int, int]]] = []

    for y0 in range(h):
        for x0 in range(w):
            idx = y0 * w + x0
            if visited[idx]:
                continue
            r, g, b = px[x0, y0][:3]
            if not (r < threshold and g < threshold and b < threshold):
                continue
            q = deque([(x0, y0)])
            visited[idx] = 1
            pixels: set[tuple[int, int]] = {(x0, y0)}
            while q:
                x, y = q.popleft()
                for dx in (-1, 0, 1):
                    for dy in (-1, 0, 1):
                        if dx == 0 and dy == 0:
                            continue
                        nx, ny = x + dx, y + dy
                        if 0 <= nx < w and 0 <= ny < h:
                            nidx = ny * w + nx
                            if visited[nidx]:
                                continue
                            nr, ng, nb = px[nx, ny][:3]
                            if nr < threshold and ng < threshold and nb < threshold:
                                visited[nidx] = 1
                                q.append((nx, ny))
                                pixels.add((nx, ny))
            components.append(pixels)
    return components


def _is_outline(component: set[tuple[int, int]]) -> bool:
    """An outline component is roughly rectangular: bbox aspect ~1:1 and
    area / bbox_area is small (most pixels are on the edge, not filled in).
    """
    if not component:
        return False
    min_x = min(p[0] for p in component)
    max_x = max(p[0] for p in component)
    min_y = min(p[1] for p in component)
    max_y = max(p[1] for p in component)
    bbox_w = max_x - min_x + 1
    bbox_h = max_y - min_y + 1
    if bbox_w * bbox_h <= 0:
        return False
    density = len(component) / (bbox_w * bbox_h)
    # An outline is thin: density should be well under 0.5. A filled shape
    # (dog eye, dog nose) is dense.
    return density < 0.45


def _outline_flood_remove(img: Image.Image) -> Image.Image | None:
    components = _dark_components(img, _DARK_THRESHOLD)
    if not components:
        return None

    # Find the largest "outline-like" component (low density, big bbox).
    outline_idx = None
    outline_size = 0
    for i, comp in enumerate(components):
        if _is_outline(comp) and len(comp) > outline_size:
            outline_idx = i
            outline_size = len(comp)
    if outline_idx is None:
        return None

    outline_pixels = components[outline_idx]
    w, h = img.size
    barrier = bytearray(w * h)
    for x, y in outline_pixels:
        barrier[y * w + x] = 1

    # Find an interior seed.
    seed = None
    for y in range(1, h - 1):
        for x in range(1, w - 1):
            if not barrier[y * w + x]:
                seed = (x, y)
                break
        if seed:
            break
    if seed is None:
        return None

    dog = bytearray(w * h)
    queue: deque[tuple[int, int]] = deque([seed])
    dog[seed[1] * w + seed[0]] = 1
    while queue:
        x, y = queue.popleft()
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                if dx == 0 and dy == 0:
                    continue
                nx, ny = x + dx, y + dy
                if not (0 <= nx < w and 0 <= ny < h):
                    continue
                nidx = ny * w + nx
                if dog[nidx] or barrier[nidx]:
                    continue
                dog[nidx] = 1
                queue.append((nx, ny))

    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    out_px = out.load()
    src_px = img.load()
    # Everything inside the outline is dog. We approximate "inside" as
    # "non-barrier AND (flood-reached from interior OR is a small dark
    # feature not connected to the outline)". Since all small features
    # (eyes/nose/spots) are isolated dark components that the flood from
    # interior seeds doesn't reach, we add them by ORing all non-outline
    # dark pixels into the dog mask.
    non_outline_dark = bytearray(w * h)
    for i, pixels in enumerate(components):
        if i == outline_idx:
            continue
        for x, y in pixels:
            non_outline_dark[y * w + x] = 1

    for y in range(h):
        for x in range(w):
            idx = y * w + x
            if barrier[idx]:
                continue
            if dog[idx] or non_outline_dark[idx]:
                r, g, b, a = src_px[x, y]
                out_px[x, y] = (r, g, b, a)
    return out


# ---------- entry point --------------------------------------------------


def _border_is_green(img: Image.Image, edge: int = 12) -> bool:
    """True if the image's outer ring is dominated by green pixels."""
    w, h = img.size
    px = img.load()
    green = total = 0
    for x in range(w):
        for d in range(edge):
            for y in (d, h - 1 - d):
                r, g, b = px[x, y][:3]
                if g > r + 20 and g > b + 20:
                    green += 1
                total += 1
    for y in range(h):
        for d in range(edge):
            for x in (d, w - 1 - d):
                r, g, b = px[x, y][:3]
                if g > r + 20 and g > b + 20:
                    green += 1
                total += 1
    return green / max(total, 1) > 0.7


def remove_background(src_path: Path, dst_path: Path) -> None:
    img = Image.open(src_path).convert("RGBA")
    w, h = img.size
    src_px = img.load()

    if _border_is_green(img):
        # mmx stamps a small brand logo in the bottom-right corner. Crop
        # that area before chroma keying so the logo doesn't sneak through.
        CROP_W = 60
        CROP_H = 60
        cropped = img.crop((0, 0, max(1, w - CROP_W), max(1, h - CROP_H)))
        img = cropped
        w, h = img.size
        src_px = img.load()
        out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        out_px = out.load()
        for y in range(h):
            for x in range(w):
                r, g, b, _ = src_px[x, y]
                a = _chroma_key_alpha(r, g, b)
                if a > 0:
                    out_px[x, y] = (r, g, b, a)
        out.save(dst_path, format="PNG")
        return

    # Fallback: outline barrier flood fill.
    out = _outline_flood_remove(img)
    if out is None:
        img.save(dst_path, format="PNG")
    else:
        out.save(dst_path, format="PNG")


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: python -m scripts.remove_white_bg <src> [<dst>]", file=sys.stderr)
        return 1
    src = Path(argv[1])
    dst = Path(argv[2]) if len(argv) >= 3 else src.with_name(src.stem + "_alpha.png")
    remove_background(src, dst)
    print(f"{src} -> {dst}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
