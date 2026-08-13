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
import os
import time
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import (
    QPoint,
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
from PySide6.QtWidgets import QApplication, QLabel, QMenu, QWidget

from ...models import Session, State
from .sprite_loader import render_frame

log = logging.getLogger(__name__)

PET_SIZE = 192
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
        self._hover = False
        self._bubble: QLabel | None = None

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
        self._overall = state
        if self._hover:
            self._refresh_bubble()

    # ---- painting ------------------------------------------------------

    def paintEvent(self, _event) -> None:
        t = time.monotonic() - self._t0
        image = render_frame(
            self._overall,
            self._sessions,
            time_seconds=t,
            size=PET_SIZE,
            spot_jitter=self._spot_jitter,
        )
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
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
            self._drag_offset = event.position().toPoint()
            event.accept()
        elif event.button() == Qt.RightButton:
            self._show_menu(event.globalPosition().toPoint())

    def mouseMoveEvent(self, event) -> None:
        if self._drag_offset is not None and event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._drag_offset = None

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._show_menu(event.globalPosition().toPoint())

    def _check_hover(self) -> None:
        """Update hover state if the cursor moves in/out without an event."""
        if not self.isVisible():
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

    def _show_bubble(self) -> None:
        if not self._sessions:
            return
        if self._bubble is None:
            bubble = QLabel(None)
            bubble.setWindowFlags(
                Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
            )
            # No WA_TranslucentBackground -- we want a fully opaque dark bubble
            # so it stays readable on any desktop background (white, light
            # grey, busy wallpaper, etc).
            bubble.setStyleSheet(
                "QLabel{background-color: #1E1E1E;"
                "color: #FFFFFF; padding: 10px 12px; border-radius: 12px;"
                "font-family: 'Segoe UI'; font-size: 13px;"
                "border: 1px solid #444;}"
            )
            bubble.setTextFormat(Qt.RichText)
            bubble.show()
            self._bubble = bubble
        self._refresh_bubble()

    def _refresh_bubble(self) -> None:
        if self._bubble is None:
            return
        sessions = [s for s in self._sessions if s.state is not State.EXITED]
        if not sessions:
            self._bubble.hide()
            return
        lines: list[str] = []
        attention = 0
        for s in sessions:
            colour = SPOT_COLORS.get(s.state, "#90A4AE")
            glyph = SPOT_GLYPHS.get(s.state, "●")
            state_label = s.state.label
            detail = s.detail
            lines.append(
                f"<span style='color:{colour};font-size:14px'>{glyph}</span> "
                f"<b>{s.project}</b> · "
                f"<span style='color:#B0BEC5'>{state_label}</span><br>"
                f"<span style='color:#ECEFF1;margin-left:18px'>{detail}</span>"
            )
            if s.state.needs_attention:
                attention += 1
        footer = f"<br><span style='color:#90A4AE;font-size:11px'>{len(sessions)} 个会话"
        if attention:
            footer += f" · {attention} 个需关注"
        footer += "</span>"
        self._bubble.setText("<div style='line-height:1.5'>" + "<br>".join(lines) + footer + "</div>")
        self._bubble.adjustSize()
        # Position above the dog, centred.
        top_left = self.mapToGlobal(QPoint(0, 0))
        bw = self._bubble.width()
        bh = self._bubble.height()
        bx = top_left.x() + (self.width() - bw) // 2
        by = top_left.y() - bh - 8
        # Keep on screen.
        screen = QGuiApplication.screenAt(top_left) or QGuiApplication.primaryScreen()
        if screen is not None:
            geo = screen.availableGeometry()
            bx = max(geo.left() + 8, min(bx, geo.right() - bw - 8))
            by = max(geo.top() + 8, by)
        self._bubble.move(bx, by)
        self._bubble.show()

    def _hide_bubble(self) -> None:
        if self._bubble is not None:
            self._bubble.hide()

    # ---- menu ----------------------------------------------------------

    def _show_menu(self, global_pos: QPoint) -> None:
        menu = QMenu(self)
        if not self._sessions:
            empty = QAction("没有会话", self)
            empty.setEnabled(False)
            menu.addAction(empty)
        else:
            for session in self._sessions:
                title = f"{session.state.glyph} {session.project} · {session.state.label}"
                header = QAction(title, self)
                header.setEnabled(False)
                menu.addAction(header)
                if session.session_id:
                    act = QAction("跳转到窗口", self)
                    act.triggered.connect(lambda _=False, pid=session.pid: self.jump_requested.emit(pid))
                    menu.addAction(act)
                    copy = QAction("复制会话ID", self)
                    sid = session.session_id
                    copy.triggered.connect(lambda _=False, s=sid: self.copy_requested.emit(s))
                    menu.addAction(copy)
                    if session.transcript:
                        open_act = QAction("打开对话记录", self)
                        path = str(session.transcript)
                        open_act.triggered.connect(lambda _=False, p=path: self.open_transcript.emit(path))
                        menu.addAction(open_act)
                mute = QAction("静音此会话", self)
                pid = session.pid
                mute.triggered.connect(lambda _=False, p=pid: self.mute_requested.emit(pid))
                menu.addAction(mute)
                menu.addSeparator()
        hide = QAction("隐藏宠物", self)
        hide.triggered.connect(self.toggle_self.emit)
        menu.addAction(hide)
        menu.addSeparator()

        # 形象 submenu
        from .sprite_loader import list_styles, get_active_style, set_active_style
        style_menu = menu.addMenu("形象")
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

    def _switch_style(self, name: str) -> None:
        from .sprite_loader import set_active_style
        set_active_style(name)
        self.style_changed.emit(name)
