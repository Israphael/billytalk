"""The two crash defences spec §13 asks for besides the WER exclusion.

§13 names three: ``WerAddExcludedApplication`` at install time (the installer
writes it), **``SetErrorMode``**, and a **top-level handler**. The first stops
Windows Error Reporting from collecting a dump; the other two stop the process
from producing one, and from printing one.

Why this matters more in cycle 3 than before: the shipped build is
``console=False``, so an unhandled exception has nowhere to print — Python
writes it to a handle nobody owns, and Windows offers to send the crash dump
instead. That dump holds the audio buffer, the transcript, and (while a request
is in flight) the API key.

The handler logs **type and location only, never the message**. That is not
excess caution: the security review of this cycle found ``http.client``
raising ``ValueError('Invalid header value b"Bearer <the whole key>"')`` — an
exception whose *message is the secret*. There is no way to tell such an
exception apart from a harmless one at the point of logging, so the message is
never logged at all.
"""

from __future__ import annotations

import ctypes as ct
import logging
import sys
import threading
import traceback
from types import TracebackType
from typing import Final

log = logging.getLogger("billytalk.crash")

__all__ = ["install_crash_guards", "where"]

SEM_FAILCRITICALERRORS: Final = 0x0001
SEM_NOGPFAULTERRORBOX: Final = 0x0002
"""No «device not ready» box, no «program stopped working» box — both of them
freeze a background process behind a modal nobody is looking at, and the second
is the one that offers to send a dump."""


def where(tb: TracebackType | None) -> str:
    """``file:line in function`` of the innermost frame, or ``"?"``.

    Location is safe to log: it is our own source. The exception's message is
    not, so it never appears here.
    """
    frames = traceback.extract_tb(tb)
    if not frames:
        return "?"
    last = frames[-1]
    return f"{last.filename}:{last.lineno} in {last.name}"


def install_crash_guards(role: str) -> None:
    """Set the error mode and take over both excepthooks. Idempotent."""
    try:
        ct.windll.kernel32.SetErrorMode(
            SEM_FAILCRITICALERRORS | SEM_NOGPFAULTERRORBOX
        )
    except Exception:  # pragma: no cover — a non-Windows test runner
        log.debug("SetErrorMode unavailable")

    def handle(exc_type: type[BaseException], exc: BaseException,
               tb: TracebackType | None) -> None:
        # Type and place. Never str(exc) — see the module docstring.
        log.error("unhandled %s in %s at %s", exc_type.__name__, role, where(tb))

    def thread_handle(args: threading.ExceptHookArgs) -> None:
        log.error(
            "unhandled %s in %s thread %s at %s",
            args.exc_type.__name__, role,
            getattr(args.thread, "name", "?"), where(args.exc_traceback),
        )

    sys.excepthook = handle
    threading.excepthook = thread_handle
