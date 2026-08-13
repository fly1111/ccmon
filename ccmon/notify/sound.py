"""System sound playback. Zero-dependency: stdlib winsound."""

from __future__ import annotations

import logging
import sys

from ..models import State

log = logging.getLogger(__name__)

# Map states to SystemSound aliases (Windows). Falls back to a single ding for
# everything on non-Windows hosts so dev/CI don't error.
_DEFAULTS: dict[State, str] = {
    State.NEEDS_APPROVAL: "SystemExclamation",
    State.NEEDS_INPUT: "SystemAsterisk",
    State.DIALOG: "SystemAsterisk",
    State.CRASHED: "SystemHand",
}


def play(state: State, sounds: dict[State, str] | None = None) -> bool:
    if sys.platform != "win32":
        return False
    alias = (sounds or {}).get(state) or _DEFAULTS.get(state)
    if not alias:
        return False
    try:
        import winsound

        winsound.PlaySound(alias, winsound.SND_ALIAS | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
        return True
    except Exception as exc:  # noqa: BLE001
        log.debug("sound failed: %s", exc)
        return False
