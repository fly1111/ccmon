"""Tests for the multi-style sprite loader."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ccmon.models import State
from ccmon.paths import data_dir
from ccmon.ui.pet import sprite_loader


@pytest.fixture
def style_env(tmp_path, monkeypatch):
    """Point the loader at a temporary assets dir, with a synthetic style."""
    assets = tmp_path / "assets" / "pet"
    assets.mkdir(parents=True)
    style_a = assets / "cute"
    style_a.mkdir()
    # Drop a transparent 1x1 PNG for each mood so the style counts as installed.
    for mood in ("happy", "anxious", "sad", "sleepy", "alert"):
        path = style_a / f"{mood}_alpha.png"
        img = __import__("PIL.Image", fromlist=["Image"]).new("RGBA", (1, 1), (0, 0, 0, 0))
        img.save(path, format="PNG")
    # Force the loader to look at our temp dir for BOTH project and user
    # roots -- one fixture, both call sites.
    monkeypatch.setattr(sprite_loader, "_project_assets_root", lambda: assets)
    monkeypatch.setattr(sprite_loader, "_user_assets_root", lambda: assets)
    monkeypatch.setattr(sprite_loader, "_style_config_path", lambda: tmp_path / "pet-style.json")
    return tmp_path


def test_list_styles_includes_builtin_always(style_env):
    styles = sprite_loader.list_styles()
    names = [s.name for s in styles]
    assert sprite_loader.BUILTIN_STYLE in names
    assert "cute" in names


def test_list_styles_excludes_empty_dirs(style_env):
    """A dir with no _alpha.png is not a usable style."""
    empty = style_env / "assets" / "pet" / "empty"
    empty.mkdir()
    names = {s.name for s in sprite_loader.list_styles()}
    assert "empty" not in names


def test_get_active_style_defaults_to_realistic(style_env):
    assert sprite_loader.get_active_style() == sprite_loader.DEFAULT_STYLE


def test_set_active_style_persists(style_env):
    sprite_loader.set_active_style("cute")
    assert sprite_loader.get_active_style() == "cute"
    raw = json.loads((style_env / "pet-style.json").read_text("utf-8"))
    assert raw["style"] == "cute"


def test_set_active_style_falls_back_for_unknown(style_env):
    sprite_loader.set_active_style("nope")
    assert sprite_loader.get_active_style() == sprite_loader.DEFAULT_STYLE


def test_render_frame_uses_active_style(style_env):
    sprite_loader.set_active_style("cute")
    img = sprite_loader.render_frame(State.NEEDS_APPROVAL, [], time_seconds=0, size=64)
    assert img.size == (64, 64)


def test_render_frame_falls_back_to_builtin(style_env):
    """No file for the requested mood in this style -> fallback to Pillow dog."""
    sprite_loader.set_active_style("cute")
    # Pass a state whose mood isn't covered -- still must produce an image.
    img = sprite_loader.render_frame(State.RUNNING, [], time_seconds=0, size=64)
    assert img.size == (64, 64)


def test_render_frame_builtin_never_reads_disk(style_env, monkeypatch):
    """The builtin style must work even if assets/pet/ is empty."""
    # Wipe the assets dir.
    import shutil
    shutil.rmtree(style_env / "assets" / "pet")
    sprite_loader.set_active_style(sprite_loader.BUILTIN_STYLE)
    img = sprite_loader.render_frame(State.RUNNING, [], time_seconds=0, size=64)
    assert img.size == (64, 64)


def test_asset_for_prefers_alpha_over_full(style_env):
    """_alpha.png takes precedence over the same-named non-alpha version."""
    sprite_loader.set_active_style("cute")
    asset = sprite_loader._asset_for("cute", State.NEEDS_APPROVAL)
    assert asset is not None
    assert asset.name.endswith("_alpha.png")
