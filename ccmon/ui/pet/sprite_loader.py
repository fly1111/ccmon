"""Pet sprite loader -- multiple styles, AI assets preferred, Pillow fallback always available.

Layout under <data_dir>/assets/pet/:

    realistic/       # the AI-generated kawaii dalmatian (default)
        happy.png, happy_alpha.png, ...
    builtin/         # not on disk -- the Pillow-drawn dalmatian is implicit

The active style is selected via the pet window's menu (or programmatic API),
persisted to <data_dir>/pet-style.json so the choice survives restarts.

If you drop a new style under assets/pet/<your-style>/ with the same naming
convention (state name + "_alpha.png"), it appears automatically in the menu.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from PIL import Image

from ...models import Session, State
from ...paths import data_dir
from . import fallback_sprite

BUILTIN_STYLE = "_builtin"  # never on disk; always available
DEFAULT_STYLE = "peter"


@dataclass
class StyleInfo:
    name: str
    has_assets: bool
    label: str


def _assets_root() -> Path:
    return data_dir() / "assets" / "pet"


def _style_dir(style: str) -> Path:
    if style == BUILTIN_STYLE:
        return _assets_root() / "__builtin__"  # never used; guarded below
    return _assets_root() / style


def list_styles() -> list[StyleInfo]:
    """Every installed style, builtin always last.

    `_builtin` is appended unconditionally -- it never needs files on disk.
    """
    root = _assets_root()
    found: list[StyleInfo] = []
    if root.is_dir():
        for child in sorted(root.iterdir()):
            if not child.is_dir() or child.name.startswith("."):
                continue
            # A style is "installed" if it has at least one alpha asset.
            has = any(child.glob("*_alpha.png"))
            if has:
                found.append(StyleInfo(name=child.name, has_assets=True, label=child.name))
    found.append(StyleInfo(name=BUILTIN_STYLE, has_assets=True, label="代码绘制 (内置)"))
    return found


def _style_config_path() -> Path:
    return data_dir() / "pet-style.json"


def get_active_style() -> str:
    path = _style_config_path()
    if path.exists():
        try:
            return json.loads(path.read_text("utf-8")).get("style", DEFAULT_STYLE)
        except (OSError, json.JSONDecodeError):
            pass
    return DEFAULT_STYLE


def set_active_style(name: str) -> None:
    """Persist the choice. Unknown style falls back to default silently."""
    valid = {s.name for s in list_styles()}
    if name not in valid:
        name = DEFAULT_STYLE
    path = _style_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"style": name}), encoding="utf-8")


def _state_to_mood(state: State) -> str:
    if state is State.NEEDS_APPROVAL:
        return fallback_sprite.MOOD_ANXIOUS
    if state is State.CRASHED:
        return fallback_sprite.MOOD_SAD
    if state in (State.NEEDS_INPUT, State.DIALOG):
        return fallback_sprite.MOOD_ALERT
    if state in (State.IDLE, State.EXITED, State.UNKNOWN):
        return fallback_sprite.MOOD_SLEEPY
    return fallback_sprite.MOOD_HAPPY


def _asset_for(style: str, overall: State) -> Path | None:
    mood = _state_to_mood(overall)
    state_name = overall.name.lower()
    root = _style_dir(style)
    for name in (mood, state_name):
        for suffix in ("_alpha", ""):
            candidate = root / f"{name}{suffix}.png"
            if candidate.exists():
                return candidate
    return None


def render_frame(
    overall: State,
    sessions: Iterable[Session],
    *,
    time_seconds: float | None = None,
    size: int = 256,
    spot_jitter: int = 0,
    style: str | None = None,
) -> Image.Image:
    """One frame of the pet.

    Resolution order:
      1. <style>/<mood or state>_alpha.png  -- if the requested style has an asset
      2. Pillow fallback (animated dalmatian)
    """
    style = style or get_active_style()
    if style != BUILTIN_STYLE:
        asset = _asset_for(style, overall)
        if asset is not None:
            try:
                base = Image.open(asset).convert("RGBA")
                if base.size != (size, size):
                    base = base.resize((size, size), Image.LANCZOS)
                return base
            except OSError:
                pass
    return fallback_sprite.render(
        overall,
        sessions,
        time_seconds=time_seconds,
        size=size,
        spot_jitter=spot_jitter,
    )
