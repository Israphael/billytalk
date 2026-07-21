"""Spec §8 «Проверка результата»: the read-only UIA verification signal.

Measured basis (spike S2, ``research/12``, the customer's machine): UIA
``TextPattern`` reads the new Notepad's document where ``WM_GETTEXT`` never
answered anything (S7), and a pasted marker becomes visible a median 152 ms
after ``SendInput`` (max 180 over 8 rounds). That signal is this module.

Outcomes, verbatim from the spec §8 table:

- element unreachable, no ``TextPattern``, or the read failed
  → ``VERIFY_IMPOSSIBLE`` — **silent** (the customer's decision: the text is
  always in the clipboard and the history; Ctrl+V / Win+V / Ctrl+Alt+Z remain);
- our text appeared past the pre-paste caret → ``INSERTED``;
- the deadline passed and the document provably did not change
  → ``PASTE_FAILED`` — loud (cue + notification, via the machine's
  ``InsertFailed``);
- the document changed but our text is not in it → ``VERIFY_IMPOSSIBLE``:
  a foreign edit means neither outcome is provable, and a false loud
  ``paste_failed`` would goad the user into pasting a duplicate
  (OPEN-QUESTIONS §26).

⚠️ **UIA is read-only here and everywhere** (spec §8): ``TextPattern`` cannot
write at all, and ``ValuePattern.SetValue`` replaces a field wholesale — the
verifier must never be handed a pattern that writes.

The COM plumbing lives behind :class:`UiaDocumentReader` and is injected, so
the decision logic is a pure poll loop testable without a single real window.
The reader is created lazily and used only on the driver thread — comtypes
initialises COM on whichever thread first touches it, and the insert ladder
(the only caller) already lives on that thread.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any, Final

log = logging.getLogger("billytalk.insert.verify")

__all__ = [
    "DocSnapshot",
    "VerifyOutcome",
    "InsertVerifier",
    "UiaDocumentReader",
    "VERIFY_DEADLINE_MS",
]

VERIFY_DEADLINE_MS: Final = 800
"""How long the paste may take to become visible before we judge. S2 measured
max 180 ms in Notepad; 800 leaves ×4 headroom for heavier targets. The full
deadline is only ever burned on the failure paths — a landed paste answers at
its own speed (median 152 ms)."""

_POLL_MS: Final = 10


def _normalize(text: str) -> str:
    """Newlines only. UIA providers answer ``\\r`` where the clipboard said
    ``\\r\\n``; anything beyond newline folding would start lying about what
    landed."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


@dataclass(frozen=True, slots=True)
class DocSnapshot:
    """The document as read before the paste.

    ``caret_offset`` is the character offset of the selection **start** (the
    caret when nothing is selected) — the point the pasted text will appear at.
    The selection start, not its end: a paste over a selection begins where the
    selection began, and slicing from the end would miss our own text. ``None``
    means the caret could not be read; verification then judges the whole
    document by occurrence count.
    """

    text: str
    caret_offset: int | None


class VerifyOutcome(Enum):
    INSERTED = "inserted"
    VERIFY_IMPOSSIBLE = "verify_impossible"
    PASTE_FAILED = "paste_failed"


class UiaDocumentReader:
    """The one comtypes touchpoint: ``TextPattern`` reads of a live window.

    Lazy on every axis — the module import is free of COM, the first use pays
    ``CoInitialize`` on the calling thread (the driver thread, by design). Every
    failure path answers ``None``, never an exception: an unreadable target is
    itself the ``verify_impossible`` result (research/12), not an error.
    """

    def __init__(self) -> None:
        self._uia: Any | None = None
        self._UIA: Any | None = None
        self._dead = False

    def _ensure(self) -> bool:
        if self._dead:
            return False
        if self._uia is not None:
            return True
        try:
            import comtypes  # noqa: F401  (CoInitialize on this thread)
            import comtypes.client

            comtypes.client.GetModule("UIAutomationCore.dll")
            from comtypes.gen import UIAutomationClient as UIA

            self._UIA = UIA
            self._uia = comtypes.client.CreateObject(
                UIA.CUIAutomation, interface=UIA.IUIAutomation
            )
            return True
        except Exception:
            # No UIA on this machine/session (or a broken gen cache): every
            # verification is impossible, once is enough for the log.
            log.exception("UIA unavailable; verification disabled for this run")
            self._dead = True
            return False

    # ------------------------------------------------------------------ #
    # reads
    # ------------------------------------------------------------------ #

    def _text_pattern(self, hwnd: int) -> Any | None:
        """The target's ``TextPattern``: the element itself if it carries one
        (the captured focus control usually does), else the first Document —
        falling back to Edit — descendant, as spike S2 did for top windows."""
        if not self._ensure():
            return None
        UIA = self._UIA
        import comtypes

        try:
            element = self._uia.ElementFromHandle(hwnd)
            if element is None:
                return None
            for candidate in (element, *self._document_descendants(element)):
                pattern = candidate.GetCurrentPattern(UIA.UIA_TextPatternId)
                if pattern:
                    return pattern.QueryInterface(UIA.IUIAutomationTextPattern)
            return None
        except (OSError, comtypes.COMError):
            return None

    def _document_descendants(self, element: Any) -> tuple[Any, ...]:
        UIA = self._UIA
        found: list[Any] = []
        for control_type in (UIA.UIA_DocumentControlTypeId, UIA.UIA_EditControlTypeId):
            condition = self._uia.CreatePropertyCondition(
                UIA.UIA_ControlTypePropertyId, control_type
            )
            child = element.FindFirst(UIA.TreeScope_Descendants, condition)
            if child is not None:
                found.append(child)
        return tuple(found)

    def read_document(self, hwnd: int) -> str | None:
        """The document's full text, normalised, or ``None`` when unreadable."""
        if not self._ensure():
            return None
        import comtypes

        try:
            pattern = self._text_pattern(hwnd)
            if pattern is None:
                return None
            return _normalize(pattern.DocumentRange.GetText(-1))
        except (OSError, comtypes.COMError):
            return None

    def read_baseline(self, hwnd: int) -> DocSnapshot | None:
        """Document text plus the caret's character offset, taken **before**
        the chord. The offset is the length of the range from the document
        start to the selection start — a second ``GetText``, paid once per
        dictation, never in the poll loop."""
        if not self._ensure():
            return None
        UIA = self._UIA
        import comtypes

        try:
            pattern = self._text_pattern(hwnd)
            if pattern is None:
                return None
            text = _normalize(pattern.DocumentRange.GetText(-1))
            caret: int | None = None
            try:
                selection = pattern.GetSelection()
                if selection is not None and selection.Length > 0:
                    prefix = pattern.DocumentRange.Clone()
                    prefix.MoveEndpointByRange(
                        UIA.TextPatternRangeEndpoint_End,
                        selection.GetElement(0),
                        UIA.TextPatternRangeEndpoint_Start,
                    )
                    caret = len(_normalize(prefix.GetText(-1)))
            except (OSError, comtypes.COMError):
                caret = None  # degraded, not fatal: whole-document judgement
            return DocSnapshot(text=text, caret_offset=caret)
        except (OSError, comtypes.COMError):
            return None


class InsertVerifier:
    """The decision loop over an injected reader (spec §8, research/12).

    ``baseline()`` before the chord, ``verify()`` after; both on the driver
    thread, inside the insert ladder. The loop never raises — every unreadable
    state folds into the silent ``VERIFY_IMPOSSIBLE``.
    """

    def __init__(
        self,
        reader: Any | None = None,
        *,
        deadline_ms: int = VERIFY_DEADLINE_MS,
        poll_ms: int = _POLL_MS,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._reader = reader
        self._deadline_s = deadline_ms / 1000
        self._poll_s = poll_ms / 1000
        self._clock = clock
        self._sleep = sleep

    def _lazy_reader(self) -> Any:
        if self._reader is None:
            self._reader = UiaDocumentReader()
        return self._reader

    def baseline(self, hwnd: int | None) -> DocSnapshot | None:
        """Before the chord: the document and the caret, or ``None`` — which
        already *is* the verify_impossible verdict, carried forward."""
        if not hwnd:
            return None
        try:
            return self._lazy_reader().read_baseline(hwnd)
        except Exception:
            log.exception("baseline read failed")
            return None

    def verify(
        self, hwnd: int | None, text: str, baseline: DocSnapshot | None
    ) -> VerifyOutcome:
        """After the chord: poll until our text shows up past the caret, the
        document proves unchanged past the deadline, or the signal dies."""
        if not hwnd or baseline is None:
            return VerifyOutcome.VERIFY_IMPOSSIBLE
        needle = _normalize(text)
        if not needle:
            return VerifyOutcome.VERIFY_IMPOSSIBLE

        origin = baseline.caret_offset or 0
        base_count = baseline.text[origin:].count(needle)
        reader = self._lazy_reader()
        deadline = self._clock() + self._deadline_s
        foreign_edit = False
        while True:
            try:
                current = reader.read_document(hwnd)
            except Exception:
                log.exception("verification read failed")
                return VerifyOutcome.VERIFY_IMPOSSIBLE
            if current is None:
                return VerifyOutcome.VERIFY_IMPOSSIBLE
            if current[origin:].count(needle) > base_count:
                return VerifyOutcome.INSERTED
            if current != baseline.text:
                foreign_edit = True
            if self._clock() >= deadline:
                # Unchanged the whole window: the paste provably did nothing.
                # Changed without our text: nothing is provable — stay silent
                # (OPEN-QUESTIONS §26).
                return (
                    VerifyOutcome.VERIFY_IMPOSSIBLE
                    if foreign_edit
                    else VerifyOutcome.PASTE_FAILED
                )
            self._sleep(self._poll_s)
