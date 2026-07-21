"""Logging, and the redaction invariant (harness §6, spec §13).

Three mechanisms, because Python has no compiler and "compile error" is therefore
not available to us: a base type whose rendering is a constant, a ``logging``
filter that drops records carrying it, and a lint rule in CI.

**Never** logged: audio, transcript text or any substring, hash or length of it,
the target window's title or contents, clipboard contents, keystrokes beyond the
hotkey itself. **Allowed**: process names, error codes, latencies, audio
durations, counters. The database is not a log — ``target_app`` belongs in the
history table and never in ``core.log``.
"""

from __future__ import annotations

import http.client
import logging
import logging.handlers
from pathlib import Path
from typing import Any, Final

__all__ = [
    "REDACTED",
    "NOISY_THIRD_PARTY_LOGGERS",
    "RedactionFilter",
    "Sensitive",
    "Transcript",
    "AudioBuffer",
    "configure_logging",
]

REDACTED: Final = "<redacted:transcript>"

LOG_ROTATION_BYTES: Final = 2 * 1024 * 1024
LOG_ROTATION_COUNT: Final = 5

NOISY_THIRD_PARTY_LOGGERS: Final = ("urllib3", "http.client", "requests", "comtypes")
"""Pinned to WARNING at startup (spec §13): at DEBUG these libraries print request
bodies, and our request bodies are the user's speech."""


class Sensitive:
    """Base type for anything that must never reach a log: audio and transcripts.

    Rendering returns the constant :data:`REDACTED`. It deliberately does **not**
    raise, and that is the whole point rather than a convenience:

    A raising ``__repr__`` breaks assertion output and the debugger, which is
    annoying. But it also fires from inside ``except`` blocks, where the natural
    thing to write is ``log.error("insert failed: %s", value)`` — and there it
    converts a recoverable paste failure into an unhandled exception. Spec §3
    makes that outcome unacceptable: the crash destroys the in-flight dictation
    along with all three escape hatches, which all grow out of the history row.
    A privacy mechanism is not permitted to lose the user's words. So the type
    stays silent and boring, and the :class:`RedactionFilter` is what actually
    enforces the invariant.
    """

    __slots__ = ()

    def __repr__(self) -> str:
        return REDACTED

    def __str__(self) -> str:
        return REDACTED

    def __format__(self, spec: str) -> str:
        # Ignores the format spec on purpose: honouring width or precision would
        # let f"{transcript:.3}" leak a length or a prefix.
        return REDACTED


class Transcript(Sensitive):
    """Recognised text. Read it with :attr:`value`; never interpolate it."""

    __slots__ = ("value",)

    def __init__(self, value: str) -> None:
        self.value = value


class AudioBuffer(Sensitive):
    """Raw or encoded audio frames."""

    __slots__ = ("frames",)

    def __init__(self, frames: Any) -> None:
        self.frames = frames


class RedactionFilter(logging.Filter):
    """Drops any record that carries a :class:`Sensitive` value.

    The record is discarded whole rather than scrubbed. Scrubbing would leave a
    message whose shape still describes the transcript — how many arguments, how
    long the format string ran — and a dropped line costs nothing, since nothing
    worth logging is ever built out of the user's words.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, Sensitive):
            return False
        args = record.args
        if isinstance(args, dict):
            candidates: tuple[Any, ...] = tuple(args.values())
        else:
            candidates = tuple(args or ())
        return not any(isinstance(arg, Sensitive) for arg in candidates)


def configure_logging(
    log_dir: Path, *, level: int = logging.INFO, filename: str = "core.log"
) -> logging.Logger:
    """Install the rotating log with redaction attached, and quiet the noisy
    third-party loggers.

    ``filename`` lets the two processes keep separate logs (``core.log`` and the
    UI's ``ui.log``): one rotating handler per file, never two processes rotating
    the same one. The redaction guarantee is identical — the UI receives
    ``transcription_ready``, which carries the user's words.

    The filter is attached to the *handler*, not to one logger: a filter on a
    logger is not consulted for records that propagate up from its children, so a
    library logger would bypass it.
    """
    log_dir.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        log_dir / filename,
        maxBytes=LOG_ROTATION_BYTES,
        backupCount=LOG_ROTATION_COUNT,
        encoding="utf-8",
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    handler.addFilter(RedactionFilter())

    root = logging.getLogger()
    root.setLevel(level)
    root.addHandler(handler)

    for name in NOISY_THIRD_PARTY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)

    # Spec §13's second startup duty in the same bullet as the logger pins:
    # http.client emits its debug output through bare print(), not logging, so
    # the WARNING pin above is a no-op for it — debuglevel=1 set anywhere in
    # the process would print every Groq request (Authorization header and the
    # multipart body that IS the user's speech) past every redaction we have.
    # Resetting beats refusing to start: the point is that the channel closes.
    if http.client.HTTPConnection.debuglevel != 0:
        http.client.HTTPConnection.debuglevel = 0
        root.warning("http.client debuglevel was nonzero at startup; reset to 0")

    return root
