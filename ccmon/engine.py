"""The scanner loop: registry -> state diff -> subscribers.

Runs in its own thread. UI layers (tray, pet) attach callbacks and react.
Designed so the same loop can be driven from `ccmon ps -w` without threading.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import replace
from typing import Any

from . import registry, transcript
from .models import Session
from .paths import sessions_dir
from .state_machine import Tick, diff

log = logging.getLogger(__name__)


def snapshot() -> list[Session]:
    """One poll cycle -- scan + transcript enrichment."""
    sessions = registry.scan(sessions_dir())
    transcript.enrich(sessions)
    return sessions


def tick(previous: list[Session], *, now: float | None = None) -> Tick:
    """Compute the diff between the previous and current snapshots."""
    current = snapshot()
    return diff(previous, current)


Subscriber = Callable[[Tick], None]


class Engine:
    """Polls the registry and fans transitions out to subscribers."""

    def __init__(self, interval: float = 1.5):
        self.interval = interval
        self._previous: list[Session] = []
        self._subs: list[Subscriber] = []
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._latest: Tick | None = None
        self._lock = threading.Lock()

    def subscribe(self, fn: Subscriber) -> None:
        with self._lock:
            self._subs.append(fn)

    def latest(self) -> Tick | None:
        with self._lock:
            return self._latest

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="ccmon-scan", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=self.interval * 2)
            self._thread = None

    def _publish(self, t: Tick) -> None:
        with self._lock:
            self._latest = t
            subs = list(self._subs)
        for fn in subs:
            try:
                fn(t)
            except Exception:  # noqa: BLE001 - a buggy subscriber must not kill the loop
                log.exception("subscriber raised")

    def _run(self) -> None:
        # Prime so the first publish is a real diff, not all-entries.
        self._previous = snapshot()
        first = diff([], self._previous)
        self._publish(first)
        while not self._stop.is_set():
            self._stop.wait(self.interval)
            if self._stop.is_set():
                break
            try:
                current = snapshot()
            except Exception:  # noqa: BLE001
                log.exception("scan failed")
                continue
            tk = diff(self._previous, current)
            self._previous = current
            self._publish(tk)
