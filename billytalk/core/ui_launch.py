"""Launching and re-launching the interface process (harness §2).

The core owns the UI's lifetime: it raises the interface on demand and, when the
interface dies, brings it back — but **no more than once every 30 seconds**, so a
UI that crashes on start cannot become a fork bomb. The throttle is on launch
attempts, not on liveness checks: :meth:`UiHost.ensure_running` is called freely
(every state publish during a dictation, every tray-menu open — OPEN-QUESTIONS
§25) and is a no-op while a process is alive or was launched too recently.

``spawn`` and ``now`` are injected so the throttle is testable without spawning a
real process or sleeping, the same discipline the driver uses for its clock.
"""

from __future__ import annotations

import logging
import subprocess
import threading
import time
from collections.abc import Callable, Sequence
from typing import Any, Final

__all__ = ["UiHost", "MIN_RELAUNCH_S"]

log = logging.getLogger("billytalk.ui_launch")

MIN_RELAUNCH_S: Final = 30.0
"""Harness §2: the interface is restarted at most once per 30 seconds."""


class UiHost:
    """Keeps at most one interface process alive, relaunching within the budget."""

    def __init__(
        self,
        argv: Sequence[str],
        *,
        spawn: Callable[[Sequence[str]], Any] = subprocess.Popen,
        now: Callable[[], float] = time.monotonic,
        min_relaunch_s: float = MIN_RELAUNCH_S,
    ) -> None:
        self._argv = list(argv)
        self._spawn = spawn
        self._now = now
        self._min_relaunch_s = min_relaunch_s
        self._proc: Any = None
        self._last_launch: float | None = None
        self._lock = threading.Lock()

    def ensure_running(self) -> None:
        """Launch the interface unless one is already alive or was launched
        within the relaunch budget. Safe to call from any thread and as often
        as wanted."""
        with self._lock:
            if self._proc is not None and self._proc.poll() is None:
                return  # alive
            now = self._now()
            if self._last_launch is not None and now - self._last_launch < self._min_relaunch_s:
                return  # launched too recently; let the budget pass
            try:
                self._proc = self._spawn(self._argv)
            except OSError:
                log.exception("failed to launch the interface process")
                return
            self._last_launch = now
            log.info("interface process launched")

    def stop(self) -> None:
        """Best-effort teardown on core exit. The interface exits itself when the
        channel drops (see ``ui/__main__``); this only backstops a hung one."""
        with self._lock:
            proc, self._proc = self._proc, None
        if proc is None or proc.poll() is not None:
            return
        try:
            proc.wait(timeout=2.0)
        except Exception:
            try:
                proc.kill()
            except Exception:
                log.exception("failed to kill the interface process")
