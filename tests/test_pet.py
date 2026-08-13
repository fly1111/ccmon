"""Sprite rendering tests for the dalmatian."""

from __future__ import annotations

import math

import pytest

from ccmon.models import Session, State
from ccmon.ui.pet import fallback_sprite, sprite_loader


def _sessions(*states: State) -> list[Session]:
    return [
        Session(pid=i, state=st, cwd=f"D:\\\\proj{i}", alive=True, registry_mtime=0.0)
        for i, st in enumerate(states)
    ]


@pytest.mark.parametrize("mood,overall", [
    (fallback_sprite.MOOD_HAPPY, State.RUNNING),
    (fallback_sprite.MOOD_ANXIOUS, State.NEEDS_APPROVAL),
    (fallback_sprite.MOOD_SAD, State.CRASHED),
    (fallback_sprite.MOOD_ALERT, State.NEEDS_INPUT),
    (fallback_sprite.MOOD_SLEEPY, State.IDLE),
])
def test_mood_for_each_overall_state(mood, overall):
    assert fallback_sprite._mood_for(overall) == mood


def test_one_session_yields_at_most_two_spots():
    """A single session can have 1 or 2 spots; never zero, never more than 2."""
    frame = fallback_sprite.build_frame(
        State.RUNNING, _sessions(State.RUNNING), time_seconds=0.0, spot_jitter=1,
    )
    assert 1 <= len(frame.spots) <= 2


def test_exited_sessions_contribute_no_spots():
    """Exited sessions are dropped from the dog entirely -- not 'shown as black'."""
    frame = fallback_sprite.build_frame(
        State.IDLE, _sessions(State.EXITED, State.EXITED), time_seconds=0.0, spot_jitter=2,
    )
    assert frame.spots == []
    # A mix: the EXITED ones are filtered, the others count as usual.
    mixed = fallback_sprite.build_frame(
        State.RUNNING, _sessions(State.RUNNING, State.EXITED), time_seconds=0.0, spot_jitter=3,
    )
    assert 1 <= len(mixed.spots) <= 2  # only the RUNNING one contributed


def test_spot_colour_matches_session_state():
    frame = fallback_sprite.build_frame(
        State.NEEDS_APPROVAL,
        _sessions(State.NEEDS_APPROVAL, State.RUNNING),
        time_seconds=0.0, spot_jitter=3,
    )
    colours = [s[3] for s in frame.spots]
    assert any(c == "#E53935" for c in colours)  # at least one red (approval)
    assert any(c == "#43A047" for c in colours)  # at least one green (running)


def test_spots_always_inside_body():
    """Every spot's fractional position must lie on the body, not the head."""
    frame = fallback_sprite.build_frame(
        State.RUNNING, _sessions(*[State.RUNNING] * 8), time_seconds=0.0, spot_jitter=4,
    )
    for fx, fy, _r, _colour in frame.spots:
        # Body centre is (0.42, 0.56) with semi-axes (0.22, 0.16); the spot's
        # polar coords guarantee radial <= 0.80 so it stays inside.
        assert 0.0 <= fx <= 1.0
        assert 0.0 <= fy <= 1.0
        radial_x = (fx - 0.42) / 0.22
        radial_y = (fy - 0.56) / 0.16
        assert radial_x * radial_x + radial_y * radial_y < 1.0, (
            f"spot at ({fx:.2f},{fy:.2f}) escapes body ellipse"
        )


def test_paint_produces_non_transparent_pixels():
    image = fallback_sprite.render(State.NEEDS_APPROVAL, _sessions(State.NEEDS_APPROVAL), time_seconds=0.0, size=128)
    pixels = image.load()
    opaque = 0
    for x in range(0, 128, 8):
        for y in range(0, 128, 8):
            r, g, b, a = pixels[x, y]
            if a > 0:
                opaque += 1
    assert opaque > 20  # dog fills a meaningful fraction of the canvas


def test_animation_changes_with_time():
    """Two frames at different t should differ -- the dog is not static."""
    sessions = _sessions(State.RUNNING)
    img_a = fallback_sprite.render(State.RUNNING, sessions, time_seconds=0.0, size=128)
    img_b = fallback_sprite.render(State.RUNNING, sessions, time_seconds=math.pi / 1.6, size=128)
    assert img_a.tobytes() != img_b.tobytes()


def test_spot_jitter_is_stable_within_a_frame():
    """Same jitter+state -> same spots. UI repaint must not reshuffle them every tick."""
    a = fallback_sprite.build_frame(
        State.RUNNING, _sessions(State.RUNNING), time_seconds=0.0, spot_jitter=42,
    )
    b = fallback_sprite.build_frame(
        State.RUNNING, _sessions(State.RUNNING), time_seconds=1.0, spot_jitter=42,
    )
    assert a.spots == b.spots


def test_renderer_dispatches_via_sprite_loader():
    """The PetWindow imports render_frame from sprite_loader -- sanity check."""
    img = sprite_loader.render_frame(State.RUNNING, _sessions(State.RUNNING), time_seconds=0.0)
    assert img.size == (256, 256)
