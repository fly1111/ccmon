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
from PySide6.QtWidgets import QApplication, QLabel, QMenu, QStyle, QWidget

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

        accent = self._overall.color  # colour the bubble border by overall state
        rows: list[str] = []
        attention = 0
        for s in sessions:
            colour = SPOT_COLORS.get(s.state, "#90A4AE")
            glyph = SPOT_GLYPHS.get(s.state, "●")
            state_label = s.state.label
            detail = s.detail or ""
            # Each session is a small block: a coloured dot, the project name
            # in bold, the state label, then the detail on the next line in
            # muted colour indented to match the dot.
            rows.append(
                f"<tr><td style='padding:0 8px 6px 0;vertical-align:top;'>"
                f"<span style='color:{colour};font-size:13px'>{glyph}</span></td>"
                f"<td style='padding:0 0 6px 0;vertical-align:top;'>"
                f"<div style='line-height:1.35'>"
                f"<span style='font-size:13px;font-weight:600;color:#F5F7FA'>{s.project}</span>"
                f"  <span style='color:{colour};font-size:11px;font-weight:600;"
                f"text-transform:uppercase;letter-spacing:0.5px'>· {state_label}</span>"
                f"</div>"
                f"<div style='color:#9AA5B1;font-size:11px;line-height:1.35;"
                f"margin-top:1px'>{detail}</div>"
                f"</td></tr>"
            )
            if s.state.needs_attention:
                attention += 1

        body = (
            "<table cellspacing='0' cellpadding='0' style='border-collapse:collapse'>"
            + "".join(rows)
            + "</table>"
        )
        footer_text = f"{len(sessions)} 个会话"
        if attention:
            footer_text += f"  ·  <span style='color:{accent};font-weight:600'>{attention} 个需关注</span>"
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
            f"{footer_text}</div>"
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
        self._bubble.show()

    def _hide_bubble(self) -> None:
        if self._bubble is not None:
            self._bubble.hide()

    # ---- menu ----------------------------------------------------------

    def _show_menu(self, global_pos: QPoint) -> None:
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

    def _switch_style(self, name: str) -> None:
        from .sprite_loader import set_active_style
        set_active_style(name)
        self.style_changed.emit(name)
