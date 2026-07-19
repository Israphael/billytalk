"""Clipboard sessions with the two-snapshot sequence guard (spec §8).

The rules, each one measured or reasoned in the spec and each one a way to
destroy the user's data if ignored:

* **Two different sequence snapshots.** ``seq_before`` is read before
  ``OpenClipboard``; our own write advances the counter, which is the write's
  confirmation. The guard for restoration is ``seq_after`` — taken after
  ``CloseClipboard``. Guarding on ``seq_before`` would cancel every restore and
  thereby destroy the user's own clipboard on every dictation.
* **Zero from ``GetClipboardSequenceNumber`` is a refusal, not a value** —
  restoration is skipped entirely.
* **Snapshot by explicit format list, never by enumeration.** Enumerating all
  formats forces delayed-rendering owners to render synchronously — a hung
  application then hangs us. MVP-0 snapshots ``CF_UNICODETEXT`` only.
* **Exclusion formats are real allocations** (``GlobalAlloc``, 4 bytes), never
  ``SetClipboardData(fmt, NULL)`` — NULL means delayed rendering and the flag
  silently does not exist. And they are set **by target, not by outcome**
  (spec §8): only where verification is trustworthy, which in cycle 1 is
  nowhere — so the parameter exists and nothing passes it yet, keeping Win+V
  alive exactly where it is the only insurance.
* **One global mutex** — delivery and history-insert share the clipboard and
  must share the session lock.

``OpenClipboard`` is retried up to 10 times with a 10→200 ms backoff: another
process may lawfully hold the clipboard for a moment.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

import win32clipboard
import win32con

__all__ = ["Clipboard", "ClipboardSnapshot"]

_EXCLUSION_FORMATS: Final = (
    "ExcludeClipboardContentFromMonitorProcessing",
    "CanIncludeInClipboardHistory",  # DWORD 0: keep out of Win+V history
    "CanUploadToCloudClipboard",     # DWORD 0: never sync to the cloud
)

_clipboard_mutex = threading.Lock()
"""Module-level on purpose: one per process, shared by every Clipboard instance
(delivery and history-insert share the sequence)."""


@dataclass(frozen=True, slots=True)
class ClipboardSnapshot:
    """What we will put back. ``None`` text: the clipboard held no text —
    restoring then means restoring emptiness only if we know the guard holds."""

    text: str | None
    seq_after_write: int
    had_text: bool


class Clipboard:
    """Sessions over ``win32clipboard`` with injectable sleep for tests."""

    def __init__(self, *, sleep: Callable[[float], None] = time.sleep) -> None:
        self._sleep = sleep

    # ------------------------------------------------------------------ #
    # raw session plumbing
    # ------------------------------------------------------------------ #

    def _open_with_retry(self) -> None:
        """10 attempts, 10→200 ms backoff (spec §8)."""
        delay = 0.010
        for attempt in range(10):
            try:
                win32clipboard.OpenClipboard(0)
                return
            except Exception:
                if attempt == 9:
                    raise
                self._sleep(delay)
                delay = min(delay * 2, 0.200)

    def sequence_number(self) -> int:
        """0 means access denied — a refusal, never a value (spec §8)."""
        return int(win32clipboard.GetClipboardSequenceNumber())

    # ------------------------------------------------------------------ #
    # the session
    # ------------------------------------------------------------------ #

    def write(self, text: str, *, exclude_from_history: bool = False) -> ClipboardSnapshot:
        """One session: snapshot the old text, write ours, snapshot the counter.

        Returns what restoration needs. The write is confirmed by the sequence
        counter having advanced past ``seq_before`` — not by any return value.
        """
        with _clipboard_mutex:
            seq_before = self.sequence_number()
            self._open_with_retry()
            try:
                previous: str | None = None
                had_text = False
                try:
                    if win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
                        previous = win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
                        had_text = True
                except Exception:
                    previous, had_text = None, False

                win32clipboard.EmptyClipboard()
                win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text)
                if exclude_from_history:
                    for name in _EXCLUSION_FORMATS:
                        fmt = win32clipboard.RegisterClipboardFormat(name)
                        # A real 4-byte allocation; NULL would mean delayed
                        # rendering and the flag would silently not exist.
                        win32clipboard.SetClipboardData(fmt, b"\x00\x00\x00\x00")
            finally:
                win32clipboard.CloseClipboard()
            seq_after = self.sequence_number()

            if seq_before and seq_after and seq_after == seq_before:
                # The counter did not move: the write silently did not happen.
                raise RuntimeError("clipboard write not confirmed by sequence counter")
            return ClipboardSnapshot(
                text=previous, seq_after_write=seq_after, had_text=had_text
            )

    def is_unchanged_since(self, snapshot: ClipboardSnapshot) -> bool:
        """The pre-paste re-check (spec §8): between our write and the keystroke
        a clipboard manager or the user's own Ctrl+C may have replaced the
        contents — pasting then would paste someone else's text."""
        current = self.sequence_number()
        return bool(current) and current == snapshot.seq_after_write

    def restore(self, snapshot: ClipboardSnapshot) -> bool:
        """Put the user's clipboard back, iff nothing else wrote after us.

        The guard compares against ``seq_after_write`` — comparing against the
        pre-write value would always fail and always destroy the user's copy.
        Returns whether a restore actually happened.
        """
        with _clipboard_mutex:
            current = self.sequence_number()
            if current == 0 or snapshot.seq_after_write == 0:
                return False  # refusal, not a value: do not touch
            if current != snapshot.seq_after_write:
                return False  # someone wrote after us; their copy wins
            if not snapshot.had_text:
                # The clipboard held no text before us. Emptying is the honest
                # restore; snapshot.text is None either way.
                self._open_with_retry()
                try:
                    win32clipboard.EmptyClipboard()
                finally:
                    win32clipboard.CloseClipboard()
                return True
            self._open_with_retry()
            try:
                win32clipboard.EmptyClipboard()
                win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, snapshot.text)
            finally:
                win32clipboard.CloseClipboard()
            return True
