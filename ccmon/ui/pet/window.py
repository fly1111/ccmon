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
import time
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

from PySide6.QtCore import (
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
    QCursor,
    QGuiApplication,
    QImage,
    QPainter,
    QPixmap,
)
from PySide6.QtWidgets import QApplication, QLabel, QMenu, QStyle, QWidget

from PIL import Image

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

        # Crossfade old -> new while inside the transition window. Skip the
        # motion transform during the fade so both sprites render at rest;
        # otherwise the jittery mood would clash with the previous mood's pose.
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

        # _builtin paints motion inside the image; AI styles are static PNGs,
        # so we apply per-mood transform ourselves.
        use_transform = get_active_style() != BUILTIN_STYLE and not in_transition

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)

        if use_transform:
            mood = _state_to_mood(self._overall)
            m = MOOD_MOTION[mood]
            head_bob = m.head_amp * math.sin(t * m.head_freq) + m.head_offset
            painter.translate(PET_SIZE / 2, PET_SIZE / 2 + head_bob)
            painter.translate(-PET_SIZE / 2, -PET_SIZE / 2)

        pixmap = QPixmap.fromImage(
            QImage(image.tobytes("raw", "RGBA"), image.width, image.height, QImage.Format_RGBA8888)
        )
        painter.drawPixmap(0, 0, pixmap)
        painter.end()

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
        # If the user dragged (instead of clicking), the jump shouldn't fire.
        # We cancel the pending timer here rather than letting it run -- if
        # the cursor travels more than a few pixels between press and release
        # the user clearly meant to move the pet, not jump.
        if press_pos is not None and self._click_timer is not None:
            moved = (event.position().toPoint() - press_pos).manhattanLength() > 5
            if moved:
                self._click_timer.stop()

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            # Cancel the pending single-click jump before the menu takes over.
            if self._click_timer is not None:
                self._click_timer.stop()
            self._show_menu(event.globalPosition().toPoint())

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

    def _check_hover(self) -> None:
        """Update hover state if the cursor moves in/out without an event."""
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
