"""Power, session and device messages on the hidden top-level window (spec §3,
§5; harness §2: «скрытое окно верхнего уровня... точка входа для
WM_DEVICECHANGE и питания»).

The classification is a pure function of ``(message, wparam)`` → an
:class:`Action`, tested without Windows. :class:`SystemEvents` binds it to the
real ``HiddenWindow`` and turns each action into injected calls — post an
event, run a driver-thread job, reinstall the hook — so the window thread only
ever enqueues (the house rule), and the slow work (a 42 ms PortAudio reload)
happens on the driver thread where the capture stream lives.

Spec §3, verbatim:

- ``PBT_APMSUSPEND`` — force-finalise the recording, save it, close the audio
  stream, drop suppression → the machine's ``Suspend`` already does all four.
- ``PBT_APMRESUMEAUTOMATIC`` — re-enumerate devices, reinstall the hook
  unconditionally, reset the watchdog baselines.
- ``WM_QUERYENDSESSION`` — save everything in flight and return TRUE; Windows
  grants ~5 seconds. We post ``Suspend`` (the same finalisation) and allow the
  shutdown.
- ``WM_ENDSESSION`` (ending) — the session is really going; exit cleanly.

Spec §5: ``WM_DEVICECHANGE`` refreshes the frozen PortAudio list — but only
with **no stream open**, so a change mid-recording is deferred until the
machine is back at rest; the current dictation finalises on the device it was
recorded on (no splicing).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from enum import Enum, auto
from typing import Final

from .machine.events import Exit, Suspend

log = logging.getLogger("billytalk.system")

__all__ = ["Action", "classify", "SystemEvents"]

# Win32 message and parameter ids (winuser.h; not all in win32con).
WM_QUERYENDSESSION: Final = 0x0011
WM_ENDSESSION: Final = 0x0016
WM_POWERBROADCAST: Final = 0x0218
WM_DEVICECHANGE: Final = 0x0219

PBT_APMSUSPEND: Final = 0x0004
PBT_APMRESUMEAUTOMATIC: Final = 0x0012
PBT_APMRESUMESUSPEND: Final = 0x0007

DBT_DEVNODES_CHANGED: Final = 0x0007
DBT_DEVICEARRIVAL: Final = 0x8000
DBT_DEVICEREMOVECOMPLETE: Final = 0x8004


class Action(Enum):
    NONE = auto()
    SUSPEND = auto()      # finalise + save + drop suppression (spec §3)
    RESUME = auto()       # re-enumerate, reinstall hook, reset watchdog
    END_SESSION = auto()  # log off / shutdown committed → exit
    DEVICE_CHANGE = auto()  # refresh the device list when the stream is idle


def classify(message: int, wparam: int) -> Action:
    """One Win32 system message → the action it means. Pure; the ``__main__``
    binding performs it. ``WM_QUERYENDSESSION`` maps to ``SUSPEND`` (save now,
    return TRUE); the committing ``WM_ENDSESSION`` maps to ``END_SESSION``."""
    if message == WM_POWERBROADCAST:
        if wparam == PBT_APMSUSPEND:
            return Action.SUSPEND
        if wparam in (PBT_APMRESUMEAUTOMATIC, PBT_APMRESUMESUSPEND):
            return Action.RESUME
        return Action.NONE
    if message == WM_QUERYENDSESSION:
        return Action.SUSPEND
    if message == WM_ENDSESSION:
        # wParam TRUE = the session is really ending; FALSE = an earlier
        # end-session was cancelled, nothing to do.
        return Action.END_SESSION if wparam else Action.NONE
    if message == WM_DEVICECHANGE:
        if wparam in (DBT_DEVNODES_CHANGED, DBT_DEVICEARRIVAL,
                      DBT_DEVICEREMOVECOMPLETE):
            return Action.DEVICE_CHANGE
        return Action.NONE
    return Action.NONE


class SystemEvents:
    """Binds :func:`classify` to the hidden window and injected effects.

    ``stream_open`` and ``reload_devices`` run on the driver thread (via
    ``post_job``): the reload unloads PortAudio from under any open callback,
    so it is gated on no stream being open and deferred to the next idle
    otherwise. Everything else is enqueue-only, safe from the window thread.
    """

    def __init__(
        self,
        *,
        post_event: Callable[[object], None],
        post_job: Callable[[Callable[[], None]], None],
        stream_open: Callable[[], bool],
        reload_devices: Callable[[], None],
        reinstall_hook: Callable[[], None],
        reset_watchdog: Callable[[], None],
    ) -> None:
        self._post_event = post_event
        self._post_job = post_job
        self._stream_open = stream_open
        self._reload_devices = reload_devices
        self._reinstall_hook = reinstall_hook
        self._reset_watchdog = reset_watchdog
        self._reload_pending = False  # driver thread only

    def register(self, window: object) -> None:
        """Wire our handlers onto a ``HiddenWindow`` (``.on(message, handler)``).
        The handlers return a value only where Windows reads one."""
        on = window.on  # type: ignore[attr-defined]
        on(WM_POWERBROADCAST, lambda w, l: self._handle(WM_POWERBROADCAST, w) or 1)
        on(WM_QUERYENDSESSION, lambda w, l: self._handle(WM_QUERYENDSESSION, w) or 1)
        on(WM_ENDSESSION, lambda w, l: self._handle(WM_ENDSESSION, w))
        on(WM_DEVICECHANGE, lambda w, l: self._handle(WM_DEVICECHANGE, w) or 1)

    def _handle(self, message: int, wparam: int) -> None:
        """Window thread: classify and dispatch, enqueue-only."""
        action = classify(message, wparam)
        if action is Action.SUSPEND:
            # Save everything in flight (spec §3). The machine's Suspend closes
            # the stream and drops suppression; the audio is already durable at
            # StopCapture, so a few milliseconds before sleep suffice.
            self._post_event(Suspend())
        elif action is Action.END_SESSION:
            self._post_event(Exit())
        elif action is Action.RESUME:
            self._post_job(self._resume_job)
        elif action is Action.DEVICE_CHANGE:
            self._post_job(self._device_job)

    # -- driver-thread jobs --------------------------------------------- #

    def _resume_job(self) -> None:
        # Re-enumerate, reinstall the hook unconditionally, reset the watchdog
        # baselines (spec §3). The device reload is itself gated on the stream.
        self._reset_watchdog()
        self._reinstall_hook()
        self._device_job()

    def _device_job(self) -> None:
        if self._stream_open():
            # A change mid-recording: the current dictation finalises on the
            # device it was recorded on (spec §5, no splicing). Defer the
            # reload to the next time the machine is idle.
            self._reload_pending = True
            return
        self._reload_pending = False
        try:
            self._reload_devices()
        except Exception:
            log.exception("device reload failed")

    def on_idle(self) -> None:
        """Driver thread, from the publish observer: a deferred reload runs the
        moment the stream is closed."""
        if self._reload_pending and not self._stream_open():
            self._device_job()
