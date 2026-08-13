"""Notify orchestration + cooldown dedupe + escalation timer.

Two rules that matter most for not being annoying:

1. Toast fires immediately on the *transition* into a needs-attention state.
   If the session flaps between NEEDS_APPROVAL and RUNNING, only the first
   transition triggers a sound; subsequent ones inside the cooldown are silent.

2. Webhook fires after a configurable delay (default 60 s) ONLY if the session
   is still in the same needs-attention state. If the user already responded,
   the escalation is cancelled. The phone is for "you're away from the desk",
   not a louder version of the toast you already saw.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Iterable

from ..models import Session, State
from ..state_machine import Tick
from . import sound, toast
from .webhook import WebhookConfig, send as send_webhook

log = logging.getLogger(__name__)


@dataclass
class _Pending:
    session_pid: int
    state: State
    fired_at: float
    timer: asyncio.Task | None = None


@dataclass
class Notifier:
    cooldown_s: float = 60.0
    escalation_s: float = 60.0
    webhooks: list[WebhookConfig] = field(default_factory=list)
    sounds: dict[State, str] = field(default_factory=dict)
    muted: set[int] = field(default_factory=set)

    _last_fired: dict[tuple[int, State], float] = field(default_factory=dict)
    _pending: dict[int, _Pending] = field(default_factory=dict)

    def on_tick(self, tick: Tick) -> None:
        """Fire immediate desktop alerts and start escalation timers."""
        for transition in tick.entered_needing_attention():
            if transition.session.pid in self.muted:
                continue
            self._maybe_immediate(transition.session)
        for transition in tick.left:
            self._cancel(transition.session.pid, transition.previous)

    def _cooldown_allows(self, session: Session) -> bool:
        if session.pid in self.muted:
            return False
        key = (session.pid, session.state)
        last = self._last_fired.get(key)
        if last is not None and (time.monotonic() - last) < self.cooldown_s:
            return False
        return True

    def _maybe_immediate(self, session: Session) -> None:
        if not self._cooldown_allows(session):
            return
        self._last_fired[(session.pid, session.state)] = time.monotonic()
        toast.send(session)
        sound.play(session.state, self.sounds)
        self._start_escalation(session)

    def _start_escalation(self, session: Session) -> None:
        self._cancel(session.pid, session.state)
        if not self.webhooks:
            return
        if not any(cfg.should_fire(session.state.name) for cfg in self.webhooks):
            return
        pending = _Pending(session_pid=session.pid, state=session.state, fired_at=time.monotonic())
        self._pending[session.pid] = pending
        pending.timer = asyncio.create_task(self._escalate(pending, session))

    async def _escalate(self, pending: _Pending, snapshot: Session) -> None:
        try:
            await asyncio.sleep(self.escalation_s)
        except asyncio.CancelledError:
            return
        current = self._pending.get(pending.session_pid)
        if current is not pending or current.state != pending.state:
            return  # session moved on; the user handled it
        await self._fire_webhooks(snapshot)

    def _cancel(self, pid: int, from_state: State) -> None:
        pending = self._pending.get(pid)
        if pending is None:
            return
        if pending.timer and not pending.timer.done():
            pending.timer.cancel()
        if from_state == pending.state:
            self._pending.pop(pid, None)

    async def _fire_webhooks(self, session: Session) -> None:
        for cfg in self.webhooks:
            if not cfg.should_fire(session.state.name):
                continue
            title = f"Claude Code · {session.project}"
            message = session.detail
            try:
                await send_webhook(cfg, session, title=title, message=message)
            except Exception:  # noqa: BLE001
                log.exception("webhook %s failed", cfg.name)


def apply_mute(notifier: Notifier, pid: int, muted: bool) -> None:
    if muted:
        notifier.muted.add(pid)
    else:
        notifier.muted.discard(pid)
