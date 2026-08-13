"""Tests for the sticker-aware background remover."""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from scripts.remove_white_bg import remove_background


def _frame_with_dog(dog_color=(252, 252, 244)) -> Image.Image:
    """Synthesize: a 512x512 image with a closed rectangle outline that
    encloses a small dog feature. The outline is 2px thick so an 8-connected
    flood fill from the image edges can't slip through.
    """
    img = Image.new("RGBA", (512, 512), (254, 254, 254, 255))
    px = img.load()
    # Card interior = white.
    for y in range(52, 459):
        for x in range(88, 424):
            px[x, y] = (252, 252, 252, 255)
    # 2px-thick 8-connected outline ring.
    for x in range(86, 427):
        for y in (49, 50, 51, 460, 461, 462):
            px[x, y] = (50, 50, 50, 255)
    for y in range(49, 463):
        for x in (86, 87, 425, 426):
            px[x, y] = (50, 50, 50, 255)
    # Dog eye -- a small dark feature inside the card.
    for y in range(180, 200):
        for x in range(180, 200):
            px[x, y] = (20, 20, 20, 255)
    return img


def test_erases_frame_outline(tmp_path: Path) -> None:
    src = tmp_path / "src.png"
    _frame_with_dog().save(src)
    dst = tmp_path / "out.png"
    remove_background(src, dst)
    out_px = Image.open(dst).convert("RGBA").load()
    # Frame outline positions must be transparent now.
    for x in range(86, 427):
        for y in (49, 50, 460, 461):
            assert out_px[x, y][3] == 0, f"frame not erased at ({x}, {y})"


def test_keeps_dog_features(tmp_path: Path) -> None:
    src = tmp_path / "src.png"
    _frame_with_dog().save(src)
    dst = tmp_path / "out.png"
    remove_background(src, dst)
    out_px = Image.open(dst).convert("RGBA").load()
    # The dog eye must be preserved.
    assert out_px[190, 190][3] == 255


def test_clears_card_interior(tmp_path: Path) -> None:
    """The card's white interior outside the dog silhouette must be transparent."""
    src = tmp_path / "src.png"
    _frame_with_dog().save(src)
    dst = tmp_path / "out.png"
    remove_background(src, dst)
    out_px = Image.open(dst).convert("RGBA").load()
    # (100, 100) is inside the card, well separated from the dog eye (180-200).
    # The outline barrier must keep page background out and dog in.
    assert out_px[100, 100][3] == 0, "card top-left interior not cleared"
    assert out_px[190, 190][3] == 255, "dog eye not preserved"


def test_no_dark_pixels_no_crash(tmp_path: Path) -> None:
    src = tmp_path / "src.png"
    Image.new("RGBA", (64, 64), (255, 255, 255, 255)).save(src)
    dst = tmp_path / "out.png"
    remove_background(src, dst)
    assert dst.exists()


def test_only_frame_no_dog_no_crash(tmp_path: Path) -> None:
    """Just a frame outline with empty interior."""
    img = Image.new("RGBA", (64, 64), (255, 255, 255, 255))
    px = img.load()
    # A simple rectangle outline.
    for x in range(10, 54):
        px[x, 10] = (40, 40, 40, 255)
        px[x, 53] = (40, 40, 40, 255)
    for y in range(10, 54):
        px[10, y] = (40, 40, 40, 255)
        px[53, y] = (40, 40, 40, 255)
    src = tmp_path / "src.png"
    img.save(src)
    dst = tmp_path / "out.png"
    remove_background(src, dst)
    assert dst.exists()
