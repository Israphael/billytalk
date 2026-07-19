"""Effects — the machine's entire output vocabulary (spec §4).

An effect is *data describing a request*, never the action itself. The machine
returns them; ``machine/driver.py`` (cycle 1, not built yet) performs them. This
is what makes the whole state table testable with no microphone, no network and
no Windows: a test asserts on the effect list, and the invariants in spec §4 are
statements about effect sequences.

Every effect that concerns one dictation carries its ``seq`` — the press-order
number. The delivery invariants ("WriteHistory always precedes Insert") are only
checkable per-dictation, because transcriptions of different dictations may be in
flight concurrently while delivery stays strictly ordered.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

__all__ = [
    "Cue",
    "DeliveryStatus",
    "ErrorCode",
    "Effect",
    "StartCapture",
    "StopCapture",
    "CancelCapture",
    "PersistAudio",
    "WriteHistory",
    "Transcribe",
    "WriteClipboard",
    "Insert",
    "PlayCue",
    "Notify",
    "EnqueueRetry",
    "ReinstallHook",
    "SetSuppression",
    "Shutdown",
]


class Cue(Enum):
    """Audible cues (spec §5).

    The spec names five distinguishable cues; ``WARN`` is a sixth, required by the
    ``HistoryWriteFailed`` cell of the state table ("PlayCue(warn)"). See
    OPEN-QUESTIONS §4.
    """

    START = "start"
    STOP = "stop"
    CLIPBOARD = "clipboard"
    REJECT = "reject"
    ERROR = "error"
    WARN = "warn"


class DeliveryStatus(Enum):
    """``history.delivery_status`` (harness §4). Values match the SQL CHECK exactly."""

    PENDING_TRANSCRIBE = "pending_transcribe"
    PENDING_RETRY = "pending_retry"
    INSERTED = "inserted"
    LEFT_ON_CLIPBOARD = "left_on_clipboard"
    FOCUS_LOST = "focus_lost"
    VERIFY_IMPOSSIBLE = "verify_impossible"
    BLOCKED_SECURE = "blocked_secure"
    TRANSCRIBE_FAILED = "transcribe_failed"
    CANCELLED = "cancelled"
    TOO_SHORT = "too_short"
    EMPTY = "empty"


class ErrorCode(Enum):
    """Error taxonomy (harness §7). The code is stable; the text is localised."""

    MIC_DENIED = "mic_denied"
    MIC_BUSY = "mic_busy"
    NO_API_KEY = "no_api_key"
    KEY_INVALID = "key_invalid"
    RATE_LIMITED = "rate_limited"
    NETWORK_DOWN = "network_down"
    PROVIDER_ERROR = "provider_error"
    PASTE_FAILED = "paste_failed"
    FOCUS_LOST = "focus_lost"
    SECURE_FIELD = "secure_field"
    HOOK_DEAD = "hook_dead"
    CLIP_TOO_LONG = "clip_too_long"


@dataclass(frozen=True, slots=True)
class StartCapture:
    """Open the audio stream. Answered later by a ``CaptureStarted`` event."""

    seq: int


@dataclass(frozen=True, slots=True)
class StopCapture:
    """Close the audio stream, keeping what was recorded."""

    seq: int


@dataclass(frozen=True, slots=True)
class CancelCapture:
    """Close the audio stream and discard what was recorded."""

    seq: int


@dataclass(frozen=True, slots=True)
class PersistAudio:
    """Trim, encode to FLAC, write to disk. Spec §3: durability before the network."""

    seq: int


@dataclass(frozen=True, slots=True)
class WriteHistory:
    """Insert or update the history row. Emitted at stop time, before any network call."""

    seq: int
    status: DeliveryStatus


@dataclass(frozen=True, slots=True)
class Transcribe:
    seq: int


@dataclass(frozen=True, slots=True)
class WriteClipboard:
    """Unconditional (spec §8): the clipboard is the primary delivery path, not a fallback."""

    seq: int


@dataclass(frozen=True, slots=True)
class Insert:
    seq: int


@dataclass(frozen=True, slots=True)
class PlayCue:
    kind: Cue


@dataclass(frozen=True, slots=True)
class Notify:
    code: ErrorCode


@dataclass(frozen=True, slots=True)
class EnqueueRetry:
    """Move a dictation to the retry track, which never auto-inserts (spec §6)."""

    seq: int


@dataclass(frozen=True, slots=True)
class ReinstallHook:
    pass


@dataclass(frozen=True, slots=True)
class SetSuppression:
    """Whether the bound button is swallowed.

    Spec §2: suppression is released whenever a recording cannot start, because a
    bound button must never mean "nothing happens" — Mouse 4 has to go back to
    being Back.
    """

    on: bool


@dataclass(frozen=True, slots=True)
class Shutdown:
    pass


Effect = (
    StartCapture
    | StopCapture
    | CancelCapture
    | PersistAudio
    | WriteHistory
    | Transcribe
    | WriteClipboard
    | Insert
    | PlayCue
    | Notify
    | EnqueueRetry
    | ReinstallHook
    | SetSuppression
    | Shutdown
)
