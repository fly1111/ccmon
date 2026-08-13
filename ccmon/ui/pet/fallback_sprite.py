"""Pillow-drawn dalmatian -- the always-available fallback sprite.

Body geometry: a single horizontal ellipse (the body), a slightly smaller
ellipse attached to its right end (the head). Spots land on the body only,
randomly distributed so they don't stack.

All animation is driven by `t` (seconds); the caller repaints at 30 fps.
"""

from __future__ import annotations

import io
import math
import random
from dataclasses import dataclass
from typing import Iterable

from PIL import Image, ImageDraw

from ...models import Session, State

_SIZE = 256

MOOD_HAPPY = "happy"
MOOD_ANXIOUS = "anxious"
MOOD_SAD = "sad"
MOOD_SLEEPY = "sleepy"
MOOD_ALERT = "alert"

_SPOT_COLOR: dict[State, str] = {
    State.RUNNING: "#43A047",          # green
    State.NEEDS_APPROVAL: "#E53935",   # red -- the headline colour
    State.NEEDS_INPUT: "#FDD835",      # yellow
    State.DIALOG: "#FB8C00",           # orange
    State.CRASHED: "#212121",          # near-black
    State.IDLE: "#90A4AE",             # gray
    State.UNKNOWN: "#90A4AE",
    State.EXITED: "#546E7A",
}


@dataclass
class Frame:
    time: float
    mood: str
    spots: list[tuple[float, float, float, str]]
    body_origin: tuple[float, float]  # body centre on the canvas


def _mood_for(state: State) -> str:
    if state is State.NEEDS_APPROVAL:
        return MOOD_ANXIOUS
    if state is State.CRASHED:
        return MOOD_SAD
    if state is State.NEEDS_INPUT or state is State.DIALOG:
        return MOOD_ALERT
    if state in (State.IDLE, State.EXITED):
        return MOOD_SLEEPY
    return MOOD_HAPPY


def build_frame(
    overall: State,
    sessions: Iterable[Session],
    *,
    time_seconds: float,
    size: int = _SIZE,
    spot_jitter: int = 0,
) -> Frame:
    """Compute animation state and spot positions. No drawing.

    Spots land inside the body ellipse using polar coordinates: pick a radius
    up to (1 - margin) of the ellipse axes, plus an angle. This guarantees
    spots always sit on the body and never escape onto the legs or head.
    """
    spots: list[tuple[float, float, float, str]] = []
    rng = random.Random(f"spots-{spot_jitter}-{size}")
    # Body sits with its centre slightly left of the canvas centre; head
    # attaches on the right. We render the body at pixels below.
    body_cx = 0.42
    body_cy = 0.56
    body_rx = 0.22  # fraction of canvas
    body_ry = 0.16
    for session in sessions:
        if session.state is State.EXITED:
            continue
        n = 1 if rng.random() < 0.5 else 2
        for _ in range(n):
            angle = rng.uniform(0, math.tau)
            radial = rng.uniform(0.20, 0.80)  # 0 = dead centre, 1 = on the rim
            x = body_cx + math.cos(angle) * body_rx * radial
            y = body_cy + math.sin(angle) * body_ry * radial
            r = rng.uniform(0.030, 0.045)
            spots.append((x, y, r, _SPOT_COLOR.get(session.state, "#90A4AE")))
    return Frame(
        time=time_seconds,
        mood=_mood_for(overall),
        spots=spots,
        body_origin=(body_cx, body_cy),
    )


def paint(frame: Frame, *, size: int = _SIZE) -> Image.Image:
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    t = frame.time
    mood = frame.mood

    # Subtle whole-body breathing (~2% scale, 1.6 rad/s).
    breathe = 1.0 + 0.02 * math.sin(t * 1.6)

    # Tail amplitude depends on mood.
    tail_amp = {
        MOOD_HAPPY: 0.30,
        MOOD_ANXIOUS: 0.04,  # tucked under body
        MOOD_SAD: 0.0,
        MOOD_SLEEPY: 0.08,
        MOOD_ALERT: 0.18,
    }[mood]
    tail_speed = 6.0 if mood == MOOD_HAPPY else 2.5
    tail_phase = math.sin(t * tail_speed)
    tail_angle = tail_phase * tail_amp

    # Head bob: anxious = jittery, sad = droopy, sleepy = slow nod.
    head_bob_y = {
        MOOD_HAPPY: 0.0,
        MOOD_ANXIOUS: 1.5 * math.sin(t * 8.0),  # small fast jitter
        MOOD_SAD: -2.0,                           # lowered
        MOOD_SLEEPY: 2.0 * math.sin(t * 1.0),    # slow nod
        MOOD_ALERT: 0.5 * math.sin(t * 3.0),
    }[mood]

    # Body geometry in absolute pixels (after applying breathe around centre).
    cx = size / 2
    cy = size / 2
    body_w = size * 0.46
    body_h = size * 0.34
    body_bx0 = cx + (0 - body_w / 2) * breathe
    body_bx1 = cx + (body_w / 2) * breathe
    body_by0 = cy + (-body_h / 2 + size * 0.02) * breathe
    body_by1 = cy + (body_h / 2 + size * 0.02) * breathe
    body_box = [body_bx0, body_by0, body_bx1, body_by1]

    # ----- Legs (drawn first so they sit behind the body) -------------
    leg_color = "#37474F"
    leg_w = size * 0.045
    leg_h = size * 0.13
    for lx_rel in (-body_w * 0.35, -body_w * 0.12, body_w * 0.12, body_w * 0.35):
        lx = cx + lx_rel * breathe
        ly = body_by1 - 4
        draw.rounded_rectangle(
            [lx - leg_w / 2, ly, lx + leg_w / 2, ly + leg_h],
            radius=leg_w / 2,
            fill=leg_color,
        )

    # ----- Body --------------------------------------------------------
    draw.ellipse(body_box, fill="#FAFAFA", outline="#37474F", width=2)

    # ----- Spots (always inside the body ellipse) ----------------------
    for fx, fy, fr, colour in frame.spots:
        spot_x = cx + (fx - 0.5) * size * breathe
        spot_y = cy + (fy - 0.5) * size * breathe
        wobble = 1.0 + 0.03 * math.sin(t * 2.0 + fx * 5)
        r = fr * size
        draw.ellipse(
            [spot_x - r * wobble, spot_y - r * wobble,
             spot_x + r * wobble, spot_y + r * wobble],
            fill=colour,
            outline="#212121",
        )

    # ----- Head --------------------------------------------------------
    # Head sits on the right side of the body, slightly overlapping.
    head_w = size * 0.30
    head_h = size * 0.30
    head_cx = body_bx1 + head_w * 0.25
    head_cy = body_by0 + body_h * 0.20 + head_bob_y
    head_box = [
        head_cx - head_w / 2,
        head_cy - head_h / 2,
        head_cx + head_w / 2,
        head_cy + head_h / 2,
    ]
    draw.ellipse(head_box, fill="#FAFAFA", outline="#37474F", width=2)

    # ----- Ears (drooped vs perky) -------------------------------------
    # Ears always sit on the upper half of the head, drooping straight down
    # by varying amounts. We never raise them above the head crown.
    ear_drop = {
        MOOD_HAPPY: 0.15,    # mostly upright, just a slight tilt
        MOOD_ANXIOUS: 0.60,  # pinned back, hanging well below the crown
        MOOD_SAD: 0.75,      # very droopy
        MOOD_SLEEPY: 0.50,
        MOOD_ALERT: 0.25,
    }[mood]
    ear_twitch_l = (math.sin(t * 0.7 + 0.0) > 0.94)
    ear_twitch_r = (math.sin(t * 0.7 + 1.5) > 0.94)
    ear_top_y = head_cy - head_h * 0.40  # always inside the head's upper half
    for ear_cx_rel, twitch in ((-head_w * 0.30, ear_twitch_l), (head_w * 0.30, ear_twitch_r)):
        ear_cx = head_cx + ear_cx_rel
        # Vertical centre of the ear -- drop = how far it hangs.
        ear_cy = ear_top_y + head_h * ear_drop * (0.4 if twitch else 1.0)
        ear_w = head_w * 0.30
        ear_h = head_h * 0.55
        draw.ellipse(
            [ear_cx - ear_w / 2, ear_cy - ear_h / 2,
             ear_cx + ear_w / 2, ear_cy + ear_h / 2],
            fill="#37474F",
        )

    # ----- Eyes --------------------------------------------------------
    blink = math.sin(t * 0.9) > 0.96
    sleeping = mood == MOOD_SLEEPY and math.sin(t * 0.6) > -0.1
    closed = blink or sleeping
    eye_y = head_cy + head_h * 0.05
    eye_dx = head_w * 0.17
    eye_r = max(4, size // 36)
    if closed:
        # Downward arc (closed lid)
        for ex in (head_cx - eye_dx, head_cx + eye_dx):
            draw.arc(
                [ex - eye_r * 1.5, eye_y - eye_r * 0.5,
                 ex + eye_r * 1.5, eye_y + eye_r * 0.5],
                start=10, end=170, fill="#212121", width=2,
            )
    else:
        for ex in (head_cx - eye_dx, head_cx + eye_dx):
            if mood == MOOD_HAPPY:
                # Crescent smile eyes
                draw.arc(
                    [ex - eye_r * 1.1, eye_y - eye_r,
                     ex + eye_r * 1.1, eye_y + eye_r * 0.4],
                    start=200, end=340, fill="#212121", width=2,
                )
            elif mood == MOOD_ANXIOUS:
                # Wide eye with small highlight (the worried puppy look)
                draw.ellipse(
                    [ex - eye_r, eye_y - eye_r,
                     ex + eye_r, eye_y + eye_r],
                    fill="#FFFFFF", outline="#212121", width=1,
                )
                pupil_r = eye_r * 0.55
                draw.ellipse(
                    [ex - pupil_r, eye_y - pupil_r,
                     ex + pupil_r, eye_y + pupil_r],
                    fill="#212121",
                )
                # tiny white catchlight
                draw.ellipse(
                    [ex - eye_r * 0.5, eye_y - eye_r * 0.7,
                     ex - eye_r * 0.1, eye_y - eye_r * 0.3],
                    fill="#FFFFFF",
                )
            elif mood == MOOD_SAD:
                # Droopy eyes with tear
                draw.ellipse(
                    [ex - eye_r, eye_y - eye_r * 0.6,
                     ex + eye_r, eye_y + eye_r * 0.6],
                    fill="#212121",
                )
                # Tear drop
                draw.ellipse(
                    [ex - 1.5, eye_y + eye_r * 1.4,
                     ex + 1.5, eye_y + eye_r * 2.2],
                    fill="#42A5F5",
                )
            else:  # alert / sleepy-but-not-sleeping
                draw.ellipse(
                    [ex - eye_r, eye_y - eye_r,
                     ex + eye_r, eye_y + eye_r],
                    fill="#212121",
                )

    # ----- Nose --------------------------------------------------------
    nose_y = head_cy + head_h * 0.30
    nose_w = head_w * 0.10
    nose_h = head_h * 0.07
    draw.ellipse(
        [head_cx - nose_w, nose_y - nose_h / 2,
         head_cx + nose_w, nose_y + nose_h / 2],
        fill="#212121",
    )

    # ----- Mouth -------------------------------------------------------
    mouth_y = nose_y + head_h * 0.20
    half = head_w * 0.16
    if mood == MOOD_HAPPY:
        # Open smile with little tongue
        draw.arc(
            [head_cx - half, mouth_y - half * 0.4,
             head_cx + half, mouth_y + half],
            start=10, end=170, fill="#212121", width=2,
        )
        tongue_w = half * 0.5
        draw.ellipse(
            [head_cx - tongue_w / 2, mouth_y + half * 0.2,
             head_cx + tongue_w / 2, mouth_y + half * 0.7],
            fill="#E57373",
        )
    elif mood == MOOD_ANXIOUS:
        # Small worried wavy line
        for i, dx in enumerate([-half, -half / 2, 0, half / 2, half]):
            y = mouth_y + (1 if i % 2 else -1) * 1.5
            draw.ellipse([head_cx + dx - 1, y - 1, head_cx + dx + 1, y + 1], fill="#212121")
    elif mood == MOOD_SAD:
        # Frown
        draw.arc(
            [head_cx - half, mouth_y - half * 0.3,
             head_cx + half, mouth_y + half * 0.7],
            start=190, end=350, fill="#212121", width=2,
        )
    elif mood == MOOD_SLEEPY:
        # Tiny "o" -- light snore
        draw.ellipse(
            [head_cx - 3, mouth_y - 3, head_cx + 3, mouth_y + 3],
            fill="#212121",
        )
    else:  # alert
        draw.line(
            [head_cx - half, mouth_y, head_cx + half, mouth_y],
            fill="#212121", width=2,
        )

    # ----- Tail (drawn last so it sits on top of the body) ------------
    # Root is at the LEFT side of the body (the dog's rear), curling UP and
    # LEFT. We draw a short stub; wagging swings the tip left/right.
    tail_root_x = body_bx0 + 2
    tail_root_y = body_by0 + body_h * 0.05
    tail_len = size * 0.10
    # base = pointing straight up; tail_angle swings ±tail_amp from that.
    base_angle = -math.pi / 2
    end_x = tail_root_x + tail_len * math.cos(base_angle + tail_angle)
    end_y = tail_root_y + tail_len * math.sin(base_angle + tail_angle)
    draw.line(
        [tail_root_x, tail_root_y, end_x, end_y],
        fill="#37474F",
        width=int(size * 0.045),
    )

    return image


def render(
    overall: State,
    sessions: Iterable[Session],
    *,
    time_seconds: float | None = None,
    size: int = _SIZE,
    spot_jitter: int = 0,
) -> Image.Image:
    import time as _t

    return paint(
        build_frame(
            overall,
            sessions,
            time_seconds=_t.time() if time_seconds is None else time_seconds,
            size=size,
            spot_jitter=spot_jitter,
        ),
        size=size,
    )


def to_png_bytes(image: Image.Image) -> bytes:
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()
