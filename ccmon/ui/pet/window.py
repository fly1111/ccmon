"""Frameless, translucent desktop pet -- the live dalmatian.

Behaviour:

  - 30 fps animation runs *continuously* -- the dog breathes, blinks, twitches
    its ears and wags its tail whether or not anything changes. CPU at idle
    is dominated by the Pillow repaint (we measured ~1-2 % on the test
    machine) -- acceptable for an app that lives in the corner all day.

  - Spots on the dog show live sessions, coloured by their state. Spot count
    is therefore "how busy you are", and the colours say "what kind of busy".

  - Hover the dog: a bubble pops up listing each session with a coloured
    glyph matching its spot. Hover again or move away: the bubble hides.

  - Left-drag moves the dog. Double-click opens a session menu.

  - Right-click opens the same session menu.

The pet never invents input. It only observes the engine and repaints.
"""

from __future__ import annotations

import logging
import math
import os
import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

from PySide6.QtCore import (
    QEasingCurve,
    QPoint,
    QPropertyAnimation,
    QRect,
    QSize,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QAction,
    QColor,
    QCursor,
    QFont,
    QGuiApplication,
    QImage,
    QPainter,
    QPixmap,
)
from PySide6.QtWidgets import QApplication, QLabel, QMenu, QStyle, QWidget

from PIL import Image, ImageOps

from ...models import Session, State
from .sprite_loader import BUILTIN_STYLE, get_active_style, render_frame

log = logging.getLogger(__name__)

PET_SIZE = 192

# ---- mood mapping (mirrors fallback_sprite._state_to_mood) ----------
MOOD_HAPPY = "happy"
MOOD_ANXIOUS = "anxious"
MOOD_SAD = "sad"
MOOD_SLEEPY = "sleepy"
MOOD_ALERT = "alert"


def _state_to_mood(state: State) -> str:
    """Engine State -> mood file name. Same rule as sprite_loader."""
    if state is State.NEEDS_APPROVAL:
        return MOOD_ANXIOUS
    if state is State.CRASHED:
        return MOOD_SAD
    if state in (State.NEEDS_INPUT, State.DIALOG):
        return MOOD_ALERT
    if state in (State.IDLE, State.EXITED, State.UNKNOWN):
        return MOOD_SLEEPY
    return MOOD_HAPPY


# Yawn animation timing. Three phases: sink, hold, rise. The curve is a
# triangle wave through these phases, not a sin -- a yawn has a clear
# "drop ... pause ... return" shape that a sin would smooth over.
_YAWN_SINK = 0.30  # seconds
_YAWN_HOLD = 0.50
_YAWN_RISE = 0.30
_YAWN_DEPTH = 6.0  # pixels of downward translate at peak


class _MoodMotion(NamedTuple):
    head_amp: float      # pixel amplitude
    head_freq: float     # rad/s
    head_offset: float   # constant drop (sad sits lower)


# Per-mood head motion. We deliberately skip the full-body breathing scale
# that the _builtin dalmatian uses -- it read as "jelly wobble" on AI
# sprites. Head bob alone gives mood without distorting the art.
MOOD_MOTION: dict[str, _MoodMotion] = {
    MOOD_HAPPY:   _MoodMotion(0.5,  3.0,  0.0),
    MOOD_ANXIOUS: _MoodMotion(2.0, 10.0,  0.0),
    MOOD_ALERT:   _MoodMotion(0.5,  4.0,  0.0),
    MOOD_SLEEPY:  _MoodMotion(2.5,  1.0,  0.0),
    MOOD_SAD:     _MoodMotion(0.5,  1.0, -2.0),
}

# Crossfade duration when the overall state changes.
TRANSITION_SECONDS = 0.4
SPOT_GLYPHS: dict[State, str] = {
    State.RUNNING: "●",
    State.NEEDS_APPROVAL: "●",
    State.NEEDS_INPUT: "●",
    State.DIALOG: "●",
    State.CRASHED: "●",
    State.IDLE: "●",
    State.UNKNOWN: "●",
    State.EXITED: "●",
}

SPOT_COLORS: dict[State, str] = {
    State.RUNNING: "#43A047",
    State.NEEDS_APPROVAL: "#E53935",
    State.NEEDS_INPUT: "#FDD835",
    State.DIALOG: "#FB8C00",
    State.CRASHED: "#212121",
    State.IDLE: "#90A4AE",
    State.UNKNOWN: "#90A4AE",
    State.EXITED: "#546E7A",
}


class PetWindow(QWidget):
    """The desktop pet."""

    jump_requested = Signal(int)
    copy_requested = Signal(str)
    open_transcript = Signal(str)
    mute_requested = Signal(int)
    toggle_self = Signal()
    style_changed = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._overall = State.IDLE
        self._sessions: tuple[Session, ...] = ()
        self._spot_jitter = int(time.time())  # stable per-launch for spot layout
        self._t0 = time.monotonic()
        self._drag_offset: QPoint | None = None
        self._press_pos: QPoint | None = None
        # Pending single-click jump -- fires after doubleClickInterval so a
        # double-click (open menu) can cancel it before it lands.
        self._click_timer: QTimer | None = None
        self._hover = False
        self._bubble: QLabel | None = None
        # Set while a right-click menu is open. _check_hover must skip its
        # show-bubble branch during this window or the bubble flashes back
        # in 100ms after we hide it -- Qt's event loop keeps running through
        # the blocking menu.exec().
        self._menu_open = False
        # Set while a fade-out animation is in flight, so the 100ms hover
        # timer doesn't fight the animation by re-showing mid-fade.
        self._fading = False
        self._fade_anim: QPropertyAnimation | None = None
        # Idle yawn animation. _next_yawn_at is the wall-clock time of the
        # next yawn trigger; _yawn_started_at is the start of the in-flight
        # yawn (0.0 = not yawning). The yawn is a 1.1s non-sinusoidal offset
        # added to the head-bob translate when mood is sleepy.
        self._next_yawn_at: float = time.monotonic() + random.uniform(6.0, 12.0)
        self._yawn_started_at: float = 0.0
        self._yawn_duration: float = 1.1
        # Walk-around behaviour. _walk_phase: "idle" | "going" | "staying"
        # | "returning". _walk_origin is the home position the pet returns
        # to; _walk_target is the cursor-following destination. _walk_anim
        # is the QPropertyAnimation driving the position. Mouse idle is
        # tracked by _last_mouse_pos and _mouse_idle_since.
        self._walk_phase: str = "idle"
        self._walk_origin: QPoint = QPoint()
        self._walk_target: QPoint = QPoint()
        self._walk_anim: QPropertyAnimation | None = None
        self._stay_until: float = 0.0
        self._last_mouse_pos: QPoint | None = None
        self._mouse_idle_since: float | None = None
        self._walk_idle_threshold: float = 5.0  # seconds before walking
        self._walk_stay_seconds: float = 3.0
        # Walk cooldown: after returning home, ignore new walk triggers
        # for this many seconds, even if the mouse is still parked. The
        # pet needs to rest. Otherwise it oscillates between home and the
        # mouse every ~8s (5 idle + 3 stay) when the user leaves the
        # cursor near the home position.
        self._walk_cooldown: float = 30.0
        self._last_walk_ended_at: float = 0.0
        # Cache of body/legs layer splits keyed by walk-frame path. Built
        # once per frame (the cut is at a fixed ratio) and reused every
        # paint tick -- a crop is cheap but doing it 30 times a second
        # for the same image is wasted work.
        self._walk_frame_cache: dict = {}
        # Petting: long-press (>500ms hold) shows a heart above the pet
        # for the duration of the press. Cheap interaction feedback; no
        # audio yet (sound comes in Phase 5).
        self._petting: bool = False
        self._long_press_timer: QTimer | None = None
        # Auto-avoid: when the foreground window covers >50% of the pet,
        # the pet jumps to the screen edge farthest from the covering
        # window. A 1s poll is enough; the user can drag a window over
        # the pet and it'll move out of the way almost immediately.
        self._avoid_timer: QTimer | None = None
        self._avoid_anim: QPropertyAnimation | None = None
        self._last_avoid_at: float = 0.0
        self._avoid_cooldown: float = 2.0  # seconds between auto-jumps
        # Follow active window: when the foreground hwnd changes, drift
        # 30% of the way toward the new window's centre. Subtler than
        # the full avoid jump.
        self._follow_anim: QPropertyAnimation | None = None
        self._last_follow_hwnd: int = 0
        # Sleep mode: 5 minutes of cursor-idle (no movement, not even
        # micro-jitter) drops the pet into sleep. Cursor activity wakes
        # it. Detected in _check_hover which already runs every 100ms.
        self._sleeping: bool = False
        self._last_active_at: float = time.monotonic()
        self._last_cursor_pos: QPoint | None = QCursor.pos()
        self._sleep_idle_seconds: float = 300.0  # 5 min
        # Laser pointer mode: when the cursor moves fast enough we paint
        # a small red dot at its on-canvas position -- the "laser
        # pointer" toy. After 1s of slow movement the dot fades. This
        # gives the cursor a way to draw the pet's attention without
        # triggering a 5s-idle walk.
        self._laser_mode: bool = False
        self._laser_pos_global: QPoint | None = None  # in screen coords
        self._laser_slow_since: float = 0.0
        self._laser_speed_threshold: float = 800.0  # px/s
        self._laser_slow_seconds: float = 1.0
        self._laser_prev_pos: QPoint | None = None
        self._laser_prev_time: float | None = None
        # Cache of rendered frames keyed by State, so paintEvent doesn't
        # re-open the PNG (or re-paint the builtin dalmatian) every tick.
        self._image_cache: dict[State, Image.Image] = {}
        # Crossfade state -- when overall changes, blend old and new for a
        # short window so AI-style swaps don't snap.
        self._prev_overall: State | None = None
        self._transition_start: float = 0.0

        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        # Accept mouse so hover/drag/menu all work.
        self.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        self.setFixedSize(QSize(PET_SIZE, PET_SIZE))
        # Strong focus so the pet can receive Esc / digit-key shortcuts
        # while the user is looking at the bubble. The pet is a Tool window
        # so this doesn't steal activation from the IDE behind it.
        self.setFocusPolicy(Qt.StrongFocus)
        self._position_on_screen()

        # 30 fps repaint. The dog breathes at 1.6 rad/s so even slow blinks
        # complete in under a second; 33 ms ticks are plenty.
        self._animation_timer = QTimer(self)
        self._animation_timer.setTimerType(Qt.PreciseTimer)
        self._animation_timer.timeout.connect(self.update)
        self._animation_timer.start(1000 // 30)

        # Auto-avoid: poll for the foreground window covering us and
        # jump to a screen edge when it does. 1s poll is enough resolution
        # for "user dragged a window over the pet" workflows.
        self._avoid_timer = QTimer(self)
        self._avoid_timer.timeout.connect(self._check_avoid)
        self._avoid_timer.start(1000)

        # Hover detection -- track the cursor globally so the bubble shows
        # whenever the cursor is over the dog, not just on a hover event.
        self._hover_timer = QTimer(self)
        self._hover_timer.timeout.connect(self._check_hover)
        self._hover_timer.start(100)

    def _position_on_screen(self) -> None:
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            self.move(100, 100)
            return
        geo = screen.availableGeometry()
        x = geo.right() - PET_SIZE - 24
        y = geo.bottom() - PET_SIZE - 24
        self.move(x, y)

    # ---- public API ----------------------------------------------------

    def update_state(self, state: State, sessions: object) -> None:
        if isinstance(sessions, tuple):
            self._sessions = sessions
        else:
            self._sessions = tuple(sessions)  # type: ignore[arg-type]
        if state != self._overall:
            self._prev_overall = self._overall
            self._transition_start = time.monotonic()
        self._overall = state
        if self._hover:
            self._refresh_bubble()

    # ---- painting ------------------------------------------------------

    def paintEvent(self, _event) -> None:
        t = time.monotonic() - self._t0
        # Walk-around bypasses the mood image cache entirely -- the body
        # and legs layers are composited on each paint tick to read as
        # stepping. Outside walk mode we use the same per-state cache as
        # before so a stable state doesn't keep re-opening the PNG.
        if self._walk_phase in ("going", "returning"):
            image = self._render_walk_frame()
            use_transform = get_active_style() != BUILTIN_STYLE
        else:
            image = self._image_cache.get(self._overall)
            if image is None:
                image = render_frame(
                    self._overall,
                    self._sessions,
                    time_seconds=t,
                    size=PET_SIZE,
                    spot_jitter=self._spot_jitter,
                )
                self._image_cache[self._overall] = image

            # Crossfade old -> new while inside the transition window. Skip
            # the motion transform during the fade so both sprites render at
            # rest; otherwise the jittery mood would clash with the previous
            # mood's pose.
            in_transition = False
            if (
                self._prev_overall is not None
                and self._prev_overall != self._overall
            ):
                elapsed = time.monotonic() - self._transition_start
                if elapsed < TRANSITION_SECONDS:
                    in_transition = True
                    prev = self._image_cache.get(self._prev_overall)
                    if prev is None:
                        prev = render_frame(
                            self._prev_overall,
                            self._sessions,
                            time_seconds=t,
                            size=PET_SIZE,
                            spot_jitter=self._spot_jitter,
                        )
                        self._image_cache[self._prev_overall] = prev
                    alpha = elapsed / TRANSITION_SECONDS
                    image = Image.blend(prev, image, alpha)
            use_transform = (
                get_active_style() != BUILTIN_STYLE
                and not in_transition
            )

        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.Antialiasing, True)

            if use_transform:
                if self._walk_phase in ("going", "returning"):
                    # Walk frames already carry motion; do NOT rotate or
                    # translate them -- the mmx video was rendered assuming
                    # a fixed camera, and any extra transform distorts the
                    # legs / head in a way that breaks the cycle.
                    pass
                else:
                    mood = _state_to_mood(self._overall)
                    m = MOOD_MOTION[mood]
                    head_bob = m.head_amp * math.sin(t * m.head_freq) + m.head_offset
                    # Yawn only when sleepy -- a busy session has no business
                    # looking drowsy. The helper self-ticks its own schedule.
                    yawn = self.yawn_offset() if mood == MOOD_SLEEPY else 0.0
                    painter.translate(PET_SIZE / 2, PET_SIZE / 2 + head_bob + yawn)
                    painter.translate(-PET_SIZE / 2, -PET_SIZE / 2)

            pixmap = QPixmap.fromImage(
                QImage(image.tobytes("raw", "RGBA"), image.width, image.height, QImage.Format_RGBA8888)
            )
            painter.drawPixmap(0, 0, pixmap)

            # Petting indicator: a heart floats above the pet while the user
            # holds the left mouse button. Drawn AFTER the pixmap so it sits
            # on top.
            if self._petting:
                painter.setPen(QColor("#FF6B9D"))
                painter.setFont(QFont("Segoe UI Emoji", 22))
                painter.drawText(PET_SIZE - 38, 30, "♥")
            # Sleep indicator: dim the pet 30% and float a Zzz above it.
            if self._sleeping:
                painter.setOpacity(0.7)
                painter.setPen(QColor("#90A4AE"))
                painter.setFont(QFont("Segoe UI Emoji", 16))
                painter.drawText(PET_SIZE - 36, 28, "Zzz")
                painter.setOpacity(1.0)
            # Laser pointer: a small red dot at the cursor's on-canvas
            # position. Drawn last so it sits on top of the pet.
            if self._laser_mode and self._laser_pos_global is not None:
                local = self.mapFromGlobal(self._laser_pos_global)
                painter.setRenderHint(QPainter.Antialiasing, True)
                painter.setBrush(QColor(255, 50, 50, 220))
                painter.setPen(QColor(255, 200, 200, 255))
                painter.drawEllipse(local.x() - 6, local.y() - 6, 12, 12)
        finally:
            # Always release the painter, even if an exception fires
            # mid-paint. Without this, QBackingStore::endPaint() logs
            # 'called with active painter' on the next repaint.
            painter.end()

    def _render_walk_frame(self) -> Image.Image:
        """Pick the current walk-cycle frame for the active style.

        Walk frames are pre-rendered PNGs (mmx video -> ffmpeg -> per-frame
        chroma key) and cycled at WALK_FPS. Loaded images are cached on
        first use; we don't reload the same frame 30 times a second.

        The saved walk frame for each style already faces the desired
        "going" direction (gen_pet_sprites.sh mirrors after generation).
        On the return trip we mirror the image so the cat faces the
        opposite way; the mirror is cached per (asset, flipped) so it
        happens at most once per frame.
        """
        from .sprite_loader import list_walk_frames, WALK_FPS
        frames = list_walk_frames(get_active_style())
        if not frames:
            # No walk cycle for this style -- render the mood art instead
            # so the pet stays visible while walking.
            return render_frame(
                self._overall,
                self._sessions,
                time_seconds=time.monotonic() - self._t0,
                size=PET_SIZE,
                spot_jitter=self._spot_jitter,
            )
        idx = int(time.monotonic() * WALK_FPS) % len(frames)
        asset = frames[idx]
        flipped = self._walk_phase == "returning"
        cache_key = (asset, flipped)
        image = self._walk_frame_cache.get(cache_key)
        if image is None:
            image = Image.open(asset).convert("RGBA")
            if image.size != (PET_SIZE, PET_SIZE):
                image = image.resize((PET_SIZE, PET_SIZE), Image.LANCZOS)
            if flipped:
                image = ImageOps.mirror(image)
            self._walk_frame_cache[cache_key] = image
        return image

    # ---- interaction ---------------------------------------------------

    def enterEvent(self, event) -> None:
        self._hover = True
        self._show_bubble()

    def leaveEvent(self, event) -> None:
        self._hover = False
        self._hide_bubble()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            pos = event.position().toPoint()
            self._drag_offset = pos
            self._press_pos = pos
            # Schedule the single-click jump. The timer is the gate that lets
            # mouseDoubleClickEvent cancel the jump before it fires -- a click
            # is only "really a click" once Qt is sure it isn't the first half
            # of a double-click.
            self._click_timer = QTimer(self)
            self._click_timer.setSingleShot(True)
            self._click_timer.timeout.connect(self._jump_to_attention)
            self._click_timer.start(QApplication.doubleClickInterval())
            # Long press: a hold of >500ms starts a petting interaction
            # (heart above the pet). Cancelled by release / double-click.
            self._long_press_timer = QTimer(self)
            self._long_press_timer.setSingleShot(True)
            self._long_press_timer.timeout.connect(self._start_petting)
            self._long_press_timer.start(500)
            event.accept()
        elif event.button() == Qt.RightButton:
            self._show_menu(event.globalPosition().toPoint())

    def mouseMoveEvent(self, event) -> None:
        if self._drag_offset is not None and event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() != Qt.LeftButton:
            return
        press_pos = self._press_pos
        self._press_pos = None
        self._drag_offset = None
        # Stop the long-press timer if it hadn't fired yet. If it already
        # fired (we're mid-petting), end the petting.
        if self._long_press_timer is not None:
            self._long_press_timer.stop()
        if self._petting:
            self._stop_petting()
            return
        # If the user dragged (instead of clicking), the jump shouldn't fire.
        if press_pos is not None and self._click_timer is not None:
            moved = (event.position().toPoint() - press_pos).manhattanLength() > 5
            if moved:
                self._click_timer.stop()

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            # Cancel the pending single-click jump and long-press before
            # the menu takes over.
            if self._click_timer is not None:
                self._click_timer.stop()
            if self._long_press_timer is not None:
                self._long_press_timer.stop()
            if self._petting:
                self._stop_petting()
            self._show_menu(event.globalPosition().toPoint())

    def _start_petting(self) -> None:
        self._petting = True
        # Force a repaint so the heart draws immediately.
        self.update()

    def _stop_petting(self) -> None:
        self._petting = False
        self.update()

    def keyPressEvent(self, event) -> None:
        """Keyboard shortcuts while the bubble is up.

        Esc hides the bubble; 1..9 jumps to the Nth session in priority
        order. The shortcut set is intentionally tiny -- the bubble is
        the primary interface, the keys are just for power users who
        already have the cursor on the pet.
        """
        key = event.key()
        if key == Qt.Key_Escape:
            self._hide_bubble()
            event.accept()
            return
        if Qt.Key_1 <= key <= Qt.Key_9:
            idx = key - Qt.Key_1
            target = self._nth_session_for_jump(idx)
            if target is not None:
                from ...win.activate import jump_to_session
                jump_to_session(target.pid, target.cwd)
                self._hide_bubble()
            event.accept()
            return
        super().keyPressEvent(event)

    def _nth_session_for_jump(self, idx: int) -> Session | None:
        """Pick the idx'th live session in jump priority order.

        Same priority as single-click: NEEDS_APPROVAL first, then
        NEEDS_INPUT, DIALOG, RUNNING, IDLE, UNKNOWN. Excludes EXITED.
        """
        priority = (
            State.NEEDS_APPROVAL,
            State.NEEDS_INPUT,
            State.DIALOG,
            State.CRASHED,
            State.RUNNING,
            State.IDLE,
            State.UNKNOWN,
        )
        rank = {s: i for i, s in enumerate(priority)}
        live = [s for s in self._sessions if s.state is not State.EXITED]
        live.sort(key=lambda s: rank.get(s.state, 99))
        if 0 <= idx < len(live):
            return live[idx]
        return None

    def _jump_to_attention(self) -> None:
        """Single-click: focus the most-urgent session, if any.

        Priority order: NEEDS_APPROVAL > NEEDS_INPUT > DIALOG. No-op when no
        session needs attention -- a normal idle pet shouldn't fire a jump
        every time the user bumps the cursor.
        """
        from ...win.activate import jump_to_session

        priority = (State.NEEDS_APPROVAL, State.NEEDS_INPUT, State.DIALOG)
        candidates = [s for s in self._sessions if s.state in priority]
        if not candidates:
            return
        target = min(candidates, key=lambda s: priority.index(s.state))
        jump_to_session(target.pid, target.cwd)

    def yawn_offset(self) -> float:
        """Vertical pixel offset for the in-flight yawn, or 0.

        Self-schedules: each call checks whether the next yawn should start,
        progresses an in-flight yawn, and rolls the dice for the next gap
        (8-16 s) once the current one finishes. The state is fully on the
        PetWindow so paintEvent only reads, never writes.
        """
        t = time.monotonic()
        if self._yawn_started_at == 0.0 and t >= self._next_yawn_at:
            self._yawn_started_at = t
        if self._yawn_started_at == 0.0:
            return 0.0
        elapsed = t - self._yawn_started_at
        total = _YAWN_SINK + _YAWN_HOLD + _YAWN_RISE
        if elapsed >= total:
            self._yawn_started_at = 0.0
            self._next_yawn_at = t + random.uniform(8.0, 16.0)
            return 0.0
        if elapsed < _YAWN_SINK:
            return _YAWN_DEPTH * (elapsed / _YAWN_SINK)
        if elapsed < _YAWN_SINK + _YAWN_HOLD:
            return _YAWN_DEPTH
        rise = elapsed - _YAWN_SINK - _YAWN_HOLD
        return _YAWN_DEPTH * (1.0 - rise / _YAWN_RISE)

    def _check_avoid(self) -> None:
        """Poll the foreground window; jump if it covers us.

        Uses Win32 GetForegroundWindow + GetWindowRect. We exclude our
        own window so clicking the pet doesn't make it dodge itself.

        Also handles "follow active window" subtly: when the foreground
        window changes (Alt+Tab or clicking another app), the pet
        drifts 30% of the way toward the new window's centre.
        """
        if not self.isVisible():
            return
        import ctypes
        from ctypes import wintypes
        hwnd = ctypes.windll.user32.GetForegroundWindow()
        if not hwnd or hwnd == int(self.winId()):
            return
        rect = wintypes.RECT()
        ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))
        fg = QRect(rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top)
        pet = QRect(self.pos(), self.size())
        inter = fg.intersected(pet)
        # Only fire the heavy "jump to edge" path when the pet is
        # genuinely obscured. Subtle follow uses the same poll.
        covered = (
            not inter.isEmpty()
            and inter.width() * inter.height()
            >= pet.width() * pet.height() * 0.5
        )
        if covered:
            now = time.monotonic()
            if now - self._last_avoid_at >= self._avoid_cooldown:
                self._last_avoid_at = now
                self._jump_to_edge_away_from(fg.center())
        # Follow active window: if the foreground hwnd changed since the
        # last poll, drift toward it. The _follow_anim field stores the
        # in-flight animation; we re-target by stopping and restarting.
        if hwnd != self._last_follow_hwnd:
            self._last_follow_hwnd = hwnd
            self._drift_toward(fg.center(), strength=0.30)
            return  # prefer follow over avoid when both could apply
        # If we're here, the foreground didn't change; nothing to do.

    def _drift_toward(self, target_global: QPoint, strength: float = 0.3) -> None:
        """Move part-way toward a point in screen coordinates.

        `strength` is the fraction of the distance we close -- 0.3 means
        "drift 30% of the way there". This is the "subtle" part: the
        pet doesn't yank itself to the new window, just leans that way.
        """
        cur = self.pos()
        # Pet's top-left target so its center ends up between cur and target.
        pet_center = cur + QPoint(self.width() // 2, self.height() // 2)
        delta = target_global - pet_center
        new_center = pet_center + QPoint(int(delta.x() * strength), int(delta.y() * strength))
        new_pos = new_center - QPoint(self.width() // 2, self.height() // 2)
        # Clamp to screen
        screen = QGuiApplication.screenAt(cur) or QGuiApplication.primaryScreen()
        if screen is not None:
            geo = screen.availableGeometry()
            new_pos.setX(max(geo.left() + 8, min(geo.right() - self.width() - 8, new_pos.x())))
            new_pos.setY(max(geo.top() + 8, min(geo.bottom() - self.height() - 8, new_pos.y())))
        if self._follow_anim is not None:
            self._follow_anim.stop()
        anim = QPropertyAnimation(self, b"pos")
        anim.setDuration(400)
        anim.setStartValue(cur)
        anim.setEndValue(new_pos)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._follow_anim = anim
        anim.start()

    def _jump_to_edge_away_from(self, blocker_center: QPoint) -> None:
        """Animate the pet to the screen edge farthest from a point."""
        screen = QGuiApplication.screenAt(self.pos()) or QGuiApplication.primaryScreen()
        if screen is None:
            return
        geo = screen.availableGeometry()
        # Pick the corner of the screen that maximizes distance from blocker
        corners = [
            QPoint(geo.left() + 24, geo.top() + 24),
            QPoint(geo.right() - self.width() - 24, geo.top() + 24),
            QPoint(geo.left() + 24, geo.bottom() - self.height() - 24),
            QPoint(geo.right() - self.width() - 24, geo.bottom() - self.height() - 24),
        ]
        target = max(corners, key=lambda c: (c - blocker_center).manhattanLength())
        if self._avoid_anim is not None:
            self._avoid_anim.stop()
        anim = QPropertyAnimation(self, b"pos")
        anim.setDuration(300)
        anim.setStartValue(self.pos())
        anim.setEndValue(target)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._avoid_anim = anim
        anim.start()

    def _check_hover(self) -> None:
        """Update hover state if the cursor moves in/out without an event.

        Also drives the walk-around state machine: the pet notices when
        the mouse hasn't moved for `walk_idle_threshold` seconds, walks
        over to where the cursor is parked, and walks back home after
        a short stay. Only fires when the overall state is calm (idle /
        exited / unknown) -- busy moods take priority.

        Sleep mode: 5 minutes of cursor-idle (no movement, not even
        micro-jitter) drops the pet into sleep. Cursor movement wakes
        it. The 100ms tick is enough resolution for both behaviours.
        """
        if self._menu_open or self._fading or not self.isVisible():
            return
        top_left = self.mapToGlobal(QPoint(0, 0))
        rect = QRect(top_left, self.size())
        cursor = QCursor.pos()
        inside = rect.contains(cursor)
        if inside and not self._hover:
            self._hover = True
            self._show_bubble()
        elif not inside and self._hover:
            self._hover = False
            self._hide_bubble()
        # Walk-around tick. Tracks mouse movement and advances the
        # go/stay/return state machine.
        self._update_walk_state(cursor)
        # Sleep mode tick. Uses a 4 px threshold to filter out sensor
        # jitter (same as the walk-around state machine above).
        if (cursor - self._last_cursor_pos).manhattanLength() > 4:
            self._last_cursor_pos = cursor
            self._last_active_at = time.monotonic()
            if self._sleeping:
                self._sleeping = False
                self.update()
        elif not self._sleeping and time.monotonic() - self._last_active_at > self._sleep_idle_seconds:
            self._sleeping = True
            self.update()
        # Laser pointer tick. Speed = distance / time between two
        # 100ms polls. Above threshold -> enable; below for >1s -> disable.
        now = time.monotonic()
        if self._laser_prev_pos is not None and self._laser_prev_time is not None:
            dt = now - self._laser_prev_time
            if dt > 0:
                dist = (cursor - self._laser_prev_pos).manhattanLength()
                speed = dist / dt
                if speed > self._laser_speed_threshold:
                    self._laser_mode = True
                    self._laser_pos_global = cursor
                    self._laser_slow_since = 0.0
                elif self._laser_mode:
                    if self._laser_slow_since == 0.0:
                        self._laser_slow_since = now
                    elif now - self._laser_slow_since > self._laser_slow_seconds:
                        self._laser_mode = False
                        self._laser_slow_since = 0.0
        self._laser_prev_pos = cursor
        self._laser_prev_time = now

    def _update_walk_state(self, cursor: QPoint) -> None:
        if self._walk_phase in ("going", "returning"):
            # Don't update mouse-idle while in motion -- the walk itself
            # generates position changes that would otherwise count as
            # "user moved the mouse". An in-flight walk runs to
            # completion regardless of cursor activity.
            return
        # Track cursor idle. A 4 px threshold filters out hand-jitter so
        # the counter doesn't reset on every sub-pixel sensor blip.
        if self._last_mouse_pos is None or (
            cursor - self._last_mouse_pos
        ).manhattanLength() > 4:
            self._last_mouse_pos = cursor
            self._mouse_idle_since = time.monotonic()
        if self._walk_phase == "staying":
            if time.monotonic() >= self._stay_until:
                self._start_walk_back()
            return
        # _walk_phase == "idle" -- see if we should start.
        if self._mouse_idle_since is None:
            return
        if time.monotonic() - self._mouse_idle_since < self._walk_idle_threshold:
            return
        # Walk cooldown: don't oscillate between home and a parked mouse.
        # After returning home, ignore new walk triggers for
        # _walk_cooldown seconds even if the cursor is still parked.
        if time.monotonic() - self._last_walk_ended_at < self._walk_cooldown:
            return
        # Only walk when there's nothing urgent to attend to.
        if self._overall not in (State.IDLE, State.EXITED, State.UNKNOWN):
            return
        self._start_walk_to_mouse()

    def _start_walk_to_mouse(self) -> None:
        """Begin the trip to where the mouse is parked.

        Records home position, computes the destination (mouse minus half
        the pet so the cursor sits over the pet's ear, not under its
        belly), and starts a QPropertyAnimation that moves the window.
        Walk speed is ~200 px/s so a 600 px trip takes 3s -- slow enough
        to look like a casual stroll.
        """
        if self._last_mouse_pos is None:
            return
        self._walk_origin = self.pos()
        target = QPoint(
            self._last_mouse_pos.x() - PET_SIZE // 2,
            self._last_mouse_pos.y() - PET_SIZE // 2,
        )
        self._walk_target = target
        self._walk_phase = "going"
        self._start_walk_anim(self._walk_origin, target, going=True)

    def _start_walk_back(self) -> None:
        """Head home from wherever the pet is now."""
        self._walk_phase = "returning"
        current = self.pos()
        self._start_walk_anim(current, self._walk_origin, going=False)

    def _start_walk_anim(self, start: QPoint, end: QPoint, *, going: bool) -> None:
        if self._walk_anim is not None:
            self._walk_anim.stop()
        distance = (end - start).manhattanLength()
        # Cap at 8s so a long trip doesn't strand the pet mid-screen if
        # the user is dragging windows around. Floor at 0.6s for short
        # hops that would otherwise look instant.
        duration_ms = max(600, min(8000, int(distance * 5)))
        anim = QPropertyAnimation(self, b"pos")
        anim.setDuration(duration_ms)
        anim.setStartValue(start)
        anim.setEndValue(end)
        anim.finished.connect(self._on_walk_finished)
        self._walk_anim = anim
        anim.start()

    def _on_walk_finished(self) -> None:
        if self._walk_phase == "going":
            # Arrived at the mouse. Park here for a few seconds, then
            # walk back. Don't reset the mouse-idle timer; the pet will
            # just walk again if the user is still parked.
            self._walk_phase = "staying"
            self._stay_until = time.monotonic() + self._walk_stay_seconds
        elif self._walk_phase == "returning":
            self._walk_phase = "idle"
            self._walk_anim = None
            # Mark cooldown start so the pet gets to rest at home even
            # if the cursor is still parked nearby.
            self._last_walk_ended_at = time.monotonic()
        # If the walk was interrupted (style switch, etc.) the phase
        # would already be "idle" -- nothing to do.

    # ---- bubble --------------------------------------------------------

    @staticmethod
    def _age_str(ms: int | None) -> str:
        """Compact "12s / 3m / 1h" for bubble rendering. Mirrors the CLI's _age."""
        if not ms:
            return ""
        delta = max(0.0, time.time() - ms / 1000.0)
        if delta < 60:
            return f"{int(delta)}s"
        if delta < 3600:
            return f"{int(delta // 60)}m"
        if delta < 86400:
            return f"{int(delta // 3600)}h{int(delta % 3600 // 60)}m"
        return f"{int(delta // 86400)}d"

    def _show_bubble(self) -> None:
        if not self._sessions:
            return
        if self._bubble is None:
            bubble = QLabel(None)
            bubble.setWindowFlags(
                Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
            )
            # Layered look: dark rounded card with a coloured left edge that
            # echoes the global worst state. Solid background so it's readable
            # on any desktop colour.
            bubble.setStyleSheet(
                "QLabel{"
                "background-color: #1F2329;"
                "color: #ECEFF1;"
                "padding: 14px 18px 12px 22px;"
                "border-radius: 12px;"
                "border: 1px solid #2E333B;"
                "font-family: 'Segoe UI', 'Microsoft YaHei', sans-serif;"
                "font-size: 12px;"
                "}"
            )
            bubble.setTextFormat(Qt.RichText)
            bubble.setWordWrap(True)
            self._bubble = bubble
        # Cancel any pending fade-out so a fast mouse-in/mouse-out doesn't
        # leave the bubble stuck in a half-faded state.
        if self._fade_anim is not None:
            self._fade_anim.stop()
        self._fading = False
        self._refresh_bubble()
        # Fade in: 0 -> 1 over 150ms. Skip on the very first show (opacity
        # already 0 from the window's default-until-shown state) -- it
        # would just delay the user seeing the bubble for no reason.
        start_opacity = self._bubble.windowOpacity()
        if start_opacity < 0.99:
            self._bubble.show()
            self._fade_anim = QPropertyAnimation(self._bubble, b"windowOpacity")
            self._fade_anim.setDuration(150)
            self._fade_anim.setStartValue(start_opacity)
            self._fade_anim.setEndValue(1.0)
            self._fade_anim.start()
        else:
            # Already visible (state refresh) -- just keep it on.
            pass

    def _refresh_bubble(self) -> None:
        if self._bubble is None:
            return
        sessions = [s for s in self._sessions if s.state is not State.EXITED]
        if not sessions:
            self._bubble.hide()
            return

        accent = self._overall.color  # colour the bubble border by overall state
        rows: list[str] = []
        attention = 0
        # Cap visible rows so a session explosion (8+ cc tabs) doesn't
        # produce a 600px bubble that covers half the screen. Overflow goes
        # to the right-click menu, which lists everything.
        max_visible = 4
        visible = sessions[:max_visible]
        hidden = sessions[max_visible:]
        for s in visible:
            colour = SPOT_COLORS.get(s.state, "#90A4AE")
            glyph = SPOT_GLYPHS.get(s.state, "●")
            state_label = s.state.label
            detail = s.detail or ""
            age = self._age_str(s.updated_at)
            # Last 3 digits of PID, e.g. #860 -- enough to disambiguate
            # duplicate project names without making the row feel noisy.
            pid_tag = f"#{s.pid % 1000}"
            rows.append(
                f"<tr><td style='padding:0 8px 6px 0;vertical-align:top;'>"
                f"<span style='color:{colour};font-size:13px'>{glyph}</span></td>"
                f"<td style='padding:0 0 6px 0;vertical-align:top;'>"
                f"<div style='line-height:1.35'>"
                f"<span style='font-size:13px;font-weight:600;color:#F5F7FA'>{s.project}</span>"
                f"  <span style='color:#5C6773;font-size:10px;font-weight:500'>"
                f"{pid_tag}</span>"
                f"  <span style='color:{colour};font-size:11px;font-weight:600;"
                f"text-transform:uppercase;letter-spacing:0.5px'>· {state_label}</span>"
                f"<span style='color:#5C6773;font-size:11px;margin-left:4px'>· {age}</span>"
                f"</div>"
                f"<div style='color:#9AA5B1;font-size:11px;line-height:1.35;"
                f"margin-top:1px'>{detail}</div>"
                f"</td></tr>"
            )
            if s.state.needs_attention:
                attention += 1
        if hidden:
            rows.append(
                f"<tr><td colspan='2' style='padding:6px 0 0 0;"
                f"color:#5C6773;font-size:10px;font-style:italic'>"
                f"+ {len(hidden)} 个未显示，右键菜单查看全部</td></tr>"
            )

        body = (
            "<table cellspacing='0' cellpadding='0' style='border-collapse:collapse'>"
            + "".join(rows)
            + "</table>"
        )
        footer_count = f"{len(sessions)} 个会话"
        if attention:
            footer_count += (
                f"  ·  <span style='color:{accent};font-weight:600'>"
                f"{attention} 个需关注</span>"
            )
        # 6px coloured left bar done by wrapping content in a 1x1 coloured
        # table with the dark panel as its right cell.
        html = (
            "<table cellspacing='0' cellpadding='0' style='border-collapse:collapse'>"
            "<tr>"
            f"<td style='width:4px;background:{accent};border-radius:2px'>"
            "&nbsp;</td>"
            "<td style='padding-left:12px'>"
            f"<div style='margin-bottom:8px;color:#F5F7FA;font-size:13px;font-weight:600;"
            f"letter-spacing:0.3px'>Claude Code 会话</div>"
            f"{body}"
            f"<div style='margin-top:8px;padding-top:8px;"
            f"border-top:1px solid #2E333B;color:#7A8593;font-size:11px'>"
            f"{footer_count}</div>"
            f"<div style='margin-top:4px;color:#5C6773;font-size:10px'>"
            f"单击跳到最紧急  ·  双击打开菜单  ·  右键菜单</div>"
            "</td></tr></table>"
        )
        self._bubble.setText(html)
        # Cap width at 360 px so long details wrap instead of stretching.
        self._bubble.setMaximumWidth(360)
        self._bubble.setMinimumWidth(220)
        self._bubble.adjustSize()
        top_left = self.mapToGlobal(QPoint(0, 0))
        bw = self._bubble.width()
        bh = self._bubble.height()
        bx = top_left.x() + (self.width() - bw) // 2
        by = top_left.y() - bh - 10
        screen = QGuiApplication.screenAt(top_left) or QGuiApplication.primaryScreen()
        if screen is not None:
            geo = screen.availableGeometry()
            bx = max(geo.left() + 8, min(bx, geo.right() - bw - 8))
            by = max(geo.top() + 8, by)
        self._bubble.move(bx, by)
        # _show_bubble already called show(); this path is for the
        # update_state -> _refresh_bubble branch where the bubble may not
        # have been shown yet. Guard against the double-show to keep the
        # fade animation coherent.
        if not self._bubble.isVisible():
            self._bubble.show()

    def _hide_bubble(self) -> None:
        if self._bubble is None or not self._bubble.isVisible():
            return
        # Animate opacity to 0, then hide(). _fading blocks the 100ms hover
        # timer from re-showing mid-animation.
        if self._fade_anim is not None:
            self._fade_anim.stop()
        self._fading = True
        self._fade_anim = QPropertyAnimation(self._bubble, b"windowOpacity")
        self._fade_anim.setDuration(150)
        self._fade_anim.setStartValue(self._bubble.windowOpacity())
        self._fade_anim.setEndValue(0.0)
        self._fade_anim.finished.connect(self._on_fade_out_done)
        self._fade_anim.start()

    def _on_fade_out_done(self) -> None:
        # Hide after fade completes, clear the guard so a re-hover can show
        # the bubble again. Use a guarded hide in case the bubble has
        # already been replaced by a new one (shouldn't happen, but cheap
        # to be safe).
        self._fading = False
        if self._bubble is not None:
            self._bubble.hide()

    # ---- menu ----------------------------------------------------------

    def _show_menu(self, global_pos: QPoint) -> None:
        # The hover bubble and the right-click menu both appear at roughly the
        # same spot and would stack on top of each other. Hide the bubble AND
        # drop the hover flag for the duration of the menu; _check_hover will
        # re-show the bubble naturally once the menu closes and the cursor is
        # still over the pet. (Just hiding without flipping the flag would
        # leave the bubble stuck off until the cursor leaves and re-enters.)
        #
        # The _menu_open flag also short-circuits _check_hover for the whole
        # exec() call -- otherwise the 100ms hover timer fires mid-menu and
        # re-shows the bubble, causing a visible flash.
        self._hide_bubble()
        self._hover = False
        self._menu_open = True
        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu{padding:6px;}"
            "QMenu::item{padding:7px 22px 7px 28px;border-radius:6px;margin:1px 4px;}"
            "QMenu::item:selected{background:#2A323C;color:#F5F7FA;}"
            "QMenu::item:disabled{color:#5C6773;}"
            "QMenu::separator{height:1px;background:#2E333B;margin:4px 8px;}"
        )
        std = menu.style().standardIcon
        if not self._sessions:
            empty = QAction(std(QStyle.SP_MessageBoxInformation), "没有会话", self)
            empty.setEnabled(False)
            menu.addAction(empty)
        else:
            for index, session in enumerate(self._sessions):
                if index:
                    menu.addSeparator()
                # Header row: coloured glyph + project + state
                colour = SPOT_COLORS.get(session.state, "#90A4AE")
                header = QAction(
                    f"{session.state.glyph}  {session.project}  ·  {session.state.label}",
                    self,
                )
                header.setEnabled(False)
                menu.addAction(header)
                if session.session_id:
                    jump = QAction(std(QStyle.SP_ArrowRight), "跳转到窗口", self)
                    jump.triggered.connect(lambda _=False, pid=session.pid: self.jump_requested.emit(pid))
                    menu.addAction(jump)
                    copy = QAction(std(QStyle.SP_DialogSaveButton), "复制会话ID", self)
                    sid = session.session_id
                    copy.triggered.connect(lambda _=False, s=sid: self.copy_requested.emit(s))
                    menu.addAction(copy)
                    if session.transcript:
                        open_act = QAction(std(QStyle.SP_FileIcon), "打开对话记录", self)
                        path = str(session.transcript)
                        open_act.triggered.connect(lambda _=False, p=path: self.open_transcript.emit(path))
                        menu.addAction(open_act)
                mute = QAction(std(QStyle.SP_MediaVolumeMuted), "静音此会话", self)
                mute.triggered.connect(lambda _=False, p=session.pid: self.mute_requested.emit(session.pid))
                menu.addAction(mute)

        menu.addSeparator()
        hide = QAction(menu.style().standardIcon(QStyle.SP_DialogCloseButton), "隐藏宠物", self)
        hide.triggered.connect(self.toggle_self.emit)
        menu.addAction(hide)

        # 形象 submenu
        from .sprite_loader import list_styles, get_active_style, set_active_style
        style_menu = menu.addMenu(menu.style().standardIcon(QStyle.SP_DirHomeIcon), "形象")
        current_style = get_active_style()
        for style in list_styles():
            action = QAction(style.label, self)
            action.setCheckable(True)
            action.setChecked(style.name == current_style)
            action.triggered.connect(
                lambda _=False, name=style.name: self._switch_style(name)
            )
            style_menu.addAction(action)

        menu.exec(global_pos)
        # Reset the hover guard now that exec() has returned. If exec raises
        # the user gets a one-time bug (hover stays off until restart) but
        # QMenu.exec in practice doesn't.
        self._menu_open = False

    def _switch_style(self, name: str) -> None:
        from .sprite_loader import set_active_style
        set_active_style(name)
        # Drop the per-State frame cache so the next paintEvent reloads
        # from the new style's PNGs instead of replaying the old style.
        self._image_cache.clear()
        self.style_changed.emit(name)
