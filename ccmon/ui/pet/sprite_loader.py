"""Pet sprite loader -- multiple styles, AI assets preferred, Pillow fallback always available.

Layout (searched in this order):

  1. <project_root>/assets/pet/<style>/   -- versioned, ship with the repo
  2. <data_dir>/assets/pet/<style>/      -- user-local, overrides project

A style is just a directory containing alpha PNGs:

    <style>/happy.png, happy_alpha.png, anxious.png, anxious_alpha.png, ...

The active style is selected via the pet window's menu (or programmatic
API), persisted to <data_dir>/pet-style.json so the choice survives
restarts.

If you drop a new style under either assets/pet/ location with the same
naming convention (state name + "_alpha.png"), it appears automatically
in the menu.
"""

from __future__ import annotations

import json
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
    source: str  # "project" or "user" -- for UI display


def _project_assets_root() -> Path:
    """assets/pet/ directory next to the installed ccmon package.

    Walk up from this file to find the repo root (where pyproject.toml
    lives), then look for assets/pet/. Lets the project ship default
    styles and lets users commit their own.
    """
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "assets" / "pet"
        if (parent / "pyproject.toml").exists() and candidate.is_dir():
            return candidate
    # Fallback: the assets/pet/ next to this file (works if running from
    # the project tree without pyproject.toml nearby).
    return here.parent.parent.parent / "assets" / "pet"


def _user_assets_root() -> Path:
    return data_dir() / "assets" / "pet"


def _assets_roots() -> list[Path]:
    """Project first, then user-local. User can override the project
    versions by dropping a file with the same name under their data dir.
    """
    return [_project_assets_root(), _user_assets_root()]


def _asset_dirs_for(style: str) -> list[Path]:
    if style == BUILTIN_STYLE:
        return []
    return [root / style for root in _assets_roots()]


def list_styles() -> list[StyleInfo]:
    """Every installed style, builtin always last.

    `_builtin` is appended unconditionally -- it never needs files on disk.
    When the same style name exists in both project and user dirs, we
    surface it once (project version wins) but tag the source.
    """
    found: dict[str, StyleInfo] = {}
    project_root = _project_assets_root()
    user_root = _user_assets_root()
    for root in (project_root, user_root):
        if not root.is_dir():
            continue
        source = "project" if root == project_root else "user"
        for child in sorted(root.iterdir()):
            if not child.is_dir() or child.name.startswith("."):
                continue
            if not any(child.glob("*_alpha.png")):
                continue
            label = child.name
            if child.name not in found:
                found[child.name] = StyleInfo(
                    name=child.name, has_assets=True, label=label, source=source,
                )
    out = list(found.values())
    out.append(StyleInfo(name=BUILTIN_STYLE, has_assets=True, label="代码绘制 (内置)", source="builtin"))
    return out


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
    for d in _asset_dirs_for(style):
        for name in (mood, state_name):
            for suffix in ("_alpha", ""):
                candidate = d / f"{name}{suffix}.png"
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
    """One frame of the pet, painted from scratch. Cheap enough for 30 fps."""
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
