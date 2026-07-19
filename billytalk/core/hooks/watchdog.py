"""Hook liveness (spec §2, research/07 S6): silent unhooking is undetectable
from inside, so the watchdog watches from outside.

Two mechanisms, combined exactly as the spike concluded:

1. **Divergence** — ``GetLastInputInfo`` moved but our hook's last-event tick
   did not, by more than 200 ms. Cheap, one API call, injects nothing. The
   comparison is **signed** on the wrapped 32-bit tick domain: ``dwTime`` may
   read *behind* our tick, which means "no input since", not divergence.
2. **Echo confirmation** — before declaring death, send the marked ±1 px probe
   and give the hook 50 ms to see its own mark. This is what prevents a false
   reinstall when the user simply walked away from the machine.

After an echo the divergence base is reset and never re-read (spec §2): the
``SendInput`` updated ``GetLastInputInfo`` itself, so a re-read would always
look healthy and mask a genuinely dead hook.

During a UAC prompt both the hook and ``GetLastInputInfo`` go silent together,
so no divergence arises and no echo is sent — the spec's requirement holds by
construction rather than by detection.
"""

from __future__ import annotations

import ctypes as ct
from collections.abc import Callable
from ctypes import wintypes
from typing import Final

from .lowlevel import HookThread, send_echo

__all__ = ["HookWatchdog"]

_user32 = ct.WinDLL("user32", use_last_error=True)


class _LASTINPUTINFO(ct.Structure):
    _fields_ = (("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD))


def _last_input_tick() -> int:
    info = _LASTINPUTINFO()
    info.cbSize = ct.sizeof(_LASTINPUTINFO)
    if not _user32.GetLastInputInfo(ct.byref(info)):
        return 0
    return int(info.dwTime)


def signed_tick_delta(later: int, earlier: int) -> int:
    """``later - earlier`` on the wrapping 32-bit tick clock, signed.

    Negative means ``later`` is actually behind — for the watchdog, "no input
    has happened since the hook's last event", which is health, not death.
    """
    delta = (later - earlier) & 0xFFFFFFFF
    if delta >= 0x8000_0000:
        delta -= 0x1_0000_0000
    return delta


class HookWatchdog:
    """Drive :meth:`probe` from the driver's timer every few seconds."""

    DIVERGENCE_MS: Final = 200
    ECHO_TIMEOUT_S: Final = 0.05

    def __init__(
        self,
        hook: HookThread,
        *,
        last_input: Callable[[], int] = _last_input_tick,
        echo: Callable[[], None] = send_echo,
    ) -> None:
        self._hook = hook
        self._last_input = last_input
        self._echo = echo

    def probe(self) -> bool:
        """``True`` — alive (or no input to judge by); ``False`` — confirmed dead.

        May block up to 50 ms waiting for the echo; the caller is the driver's
        housekeeping timer, never the hook thread itself.
        """
        divergence = signed_tick_delta(self._last_input(), self._hook.last_event_tick)
        if divergence <= self.DIVERGENCE_MS:
            return True

        # Suspicion. Confirm by echo only — input may simply have stopped.
        self._hook.echo_seen.clear()
        self._echo()
        alive = self._hook.echo_seen.wait(self.ECHO_TIMEOUT_S)
        # The echo moved GetLastInputInfo itself; reset the base and never
        # re-read the divergence in this round (spec §2).
        self._hook.note_probe_sent()
        return alive
