"""System tray icon, dynamically rendered with Pillow."""

from __future__ import annotations

import io
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFont

from ..models import State


def render_icon(state: State, *, size: int = 64, attention_count: int = 0) -> Image.Image:
    """One tray icon: a spark glyph on a coloured disc with an attention badge."""
    base = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(base)

    pad = size // 8
    disc_color = state.color
    disc_bbox = (pad, pad, size - pad, size - pad)

    draw.ellipse(disc_bbox, fill=disc_color)
    # Inner ring gives a hint of depth without using alpha on a checkerboard.
    inner_pad = size // 16
    draw.ellipse(
        (inner_pad, inner_pad, size - inner_pad, size - inner_pad),
        outline=(255, 255, 255, 80),
        width=1,
    )

    glyph, glyph_color = _glyph(state, size)
    if glyph:
        bbox = draw.textbbox((0, 0), glyph, font=_font(size))
        x = (size - (bbox[2] - bbox[0])) // 2 - bbox[0]
        y = (size - (bbox[3] - bbox[1])) // 2 - bbox[1]
        draw.text((x, y), glyph, fill=glyph_color, font=_font(size))

    if attention_count > 1:
        _badge(draw, size, attention_count)

    return base


def _glyph(state: State, size: int) -> tuple[str, tuple[int, int, int, int]]:
    if state is State.RUNNING:
        return "*", (255, 255, 255, 255)
    if state is State.NEEDS_APPROVAL:
        return "!", (255, 255, 255, 255)
    if state is State.NEEDS_INPUT:
        return "?", (0, 0, 0, 255)
    if state is State.CRASHED:
        return "x", (255, 255, 255, 255)
    if state is State.DIALOG:
        return "?", (0, 0, 0, 255)
    return "-", (255, 255, 255, 255)


@lru_cache(maxsize=8)
def _font(size: int):
    candidates = (
        r"C:\Windows\Fonts\segoeui.ttf",
        r"C:\Windows\Fonts\arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    )
    for path in candidates:
        try:
            return ImageFont.truetype(path, size=int(size * 0.55))
        except (OSError, ValueError):
            continue
    return ImageFont.load_default()


def _badge(draw: ImageDraw.ImageDraw, size: int, count: int) -> None:
    text = str(count) if count < 100 else "99+"
    radius = size // 4
    cx, cy = size - radius - 1, radius + 1
    draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), fill="#FFFFFF", outline="#E53935", width=2)
    font = _font(size // 2)
    bbox = draw.textbbox((0, 0), text, font=font)
    x = cx - (bbox[2] - bbox[0]) // 2 - bbox[0]
    y = cy - (bbox[3] - bbox[1]) // 2 - bbox[1]
    draw.text((x, y), text, fill="#E53935", font=font)


def to_ico_bytes(image: Image.Image) -> bytes:
    """pystray accepts a file path, an Image, or a callable returning an Image;
    we just return the Image directly."""
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()
