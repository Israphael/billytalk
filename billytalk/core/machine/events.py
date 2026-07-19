"""Events — the machine's entire input vocabulary (spec §4, state table).

Events are facts that already happened. Nothing here asks for anything and
nothing carries a clock: ``step`` takes ``now`` as a parameter so that every test
is deterministic and no test ever sleeps (harness §8).

Two modelling decisions worth stating, both recorded in OPEN-QUESTIONS:

* Single vs. double Esc is resolved **below** the machine. The hook layer owns the
  400 ms window (spec §2) and synthesises :class:`DoubleEsc`; a lone
  :class:`EscPressed` reaches the machine only so that the state table has
  somewhere to say "passes through", and it produces no effects in any phase.
* The fallback binding (Ctrl+Alt+D) is a ``ptt`` binding, so
  :class:`PressFallback` / :class:`ReleaseFallback` behave exactly like the mouse
  ptt events. They exist as separate types only because the state table gives the
  fallback its own row.
"""

from __future__ import annotations

from dataclasses import dataclass

from .effects import ErrorCode

__all__ = [
    "Event",
    "PressPTT",
    "ReleasePTT",
    "PressFallback",
    "ReleaseFallback",
    "PressToggle",
    "ReleaseToggle",
    "CaptureStarted",
    "TranscribeOk",
    "NetworkError",
    "ClipEmpty",
    "InsertOk",
    "InsertFailed",
    "HistoryWriteFailed",
    "EscPressed",
    "DoubleEsc",
    "MaxHoldReached",
    "MicError",
    "HookDied",
    "Suspend",
    "SetDictationEnabled",
    "FailedTimeout",
    "Exit",
]


@dataclass(frozen=True, slots=True)
class PressPTT:
    """Bound push-to-talk code went down.

    ``target_hwnd`` is captured at press time, not at delivery time (spec §8): by
    the time the text comes back the user may have clicked elsewhere, and the
    window we owe the text to is the one that had focus when they started talking.
    """

    target_hwnd: int | None = None


@dataclass(frozen=True, slots=True)
class ReleasePTT:
    pass


@dataclass(frozen=True, slots=True)
class PressFallback:
    target_hwnd: int | None = None


@dataclass(frozen=True, slots=True)
class ReleaseFallback:
    pass


@dataclass(frozen=True, slots=True)
class PressToggle:
    target_hwnd: int | None = None


@dataclass(frozen=True, slots=True)
class ReleaseToggle:
    """Ignored in every phase. Present so the state table's row is executable."""


@dataclass(frozen=True, slots=True)
class CaptureStarted:
    """The audio stream is actually open and the first frame has been captured.

    This is the event a deferred release waits for, and the moment the 250 ms
    "too short" clock starts — spec §4 measures from the first captured frame,
    not from the press, because the Bluetooth path takes 100-300 ms to wake.
    """


@dataclass(frozen=True, slots=True)
class TranscribeOk:
    seq: int


@dataclass(frozen=True, slots=True)
class NetworkError:
    seq: int
    code: ErrorCode = ErrorCode.NETWORK_DOWN


@dataclass(frozen=True, slots=True)
class ClipEmpty:
    """No frame in the clip crossed the threshold (spec §4). Recorded, but silent."""

    seq: int


@dataclass(frozen=True, slots=True)
class InsertOk:
    seq: int


@dataclass(frozen=True, slots=True)
class InsertFailed:
    seq: int
    code: ErrorCode = ErrorCode.PASTE_FAILED


@dataclass(frozen=True, slots=True)
class HistoryWriteFailed:
    seq: int


@dataclass(frozen=True, slots=True)
class EscPressed:
    """A single Esc. Always passes through to the foreground application."""


@dataclass(frozen=True, slots=True)
class DoubleEsc:
    """Two Esc presses within 400 ms, already coalesced by the hook layer."""


@dataclass(frozen=True, slots=True)
class MaxHoldReached:
    """The hold safety fuse fired. The text is kept but never auto-pasted."""


@dataclass(frozen=True, slots=True)
class MicError:
    code: ErrorCode = ErrorCode.MIC_BUSY


@dataclass(frozen=True, slots=True)
class HookDied:
    pass


@dataclass(frozen=True, slots=True)
class Suspend:
    """``PBT_APMSUSPEND``. Windows gives us very little time; finalise now."""


@dataclass(frozen=True, slots=True)
class SetDictationEnabled:
    enabled: bool


@dataclass(frozen=True, slots=True)
class FailedTimeout:
    """Leaves the transient ``Failed`` phase.

    Spec §4 says the machine "returns to Idle by itself" after notifying. A pure
    function cannot wait, so the timer lives in the driver and reports back here.
    """


@dataclass(frozen=True, slots=True)
class Exit:
    pass


Event = (
    PressPTT
    | ReleasePTT
    | PressFallback
    | ReleaseFallback
    | PressToggle
    | ReleaseToggle
    | CaptureStarted
    | TranscribeOk
    | NetworkError
    | ClipEmpty
    | InsertOk
    | InsertFailed
    | HistoryWriteFailed
    | EscPressed
    | DoubleEsc
    | MaxHoldReached
    | MicError
    | HookDied
    | Suspend
    | SetDictationEnabled
    | FailedTimeout
    | Exit
)
