"""``insert/verify.py``: the spec §8 verification verdicts, with the UIA read
faked so every branch of the decision loop is reachable without a window.

The scripted reader hands out documents one poll at a time (the last one
holds), so the loop's timing — appear late, never change, change without our
text — is fully deterministic against the fake clock.
"""

from __future__ import annotations

import pytest

from billytalk.core.insert.verify import (
    DocSnapshot,
    InsertVerifier,
    VerifyOutcome,
)

HWND = 0x222


class Ticker:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps = 0

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps += 1
        self.now += seconds


class ScriptedReader:
    """Documents per poll; ``None`` scripts a dead signal; an ``Exception``
    instance scripts a read that raises."""

    def __init__(self, docs: list[object], *, baseline: object = None) -> None:
        self._docs = list(docs)
        self._baseline = baseline
        self.reads = 0

    def read_document(self, hwnd: int) -> str | None:
        self.reads += 1
        doc = self._docs.pop(0) if len(self._docs) > 1 else self._docs[0]
        if isinstance(doc, Exception):
            raise doc
        return doc  # type: ignore[return-value]

    def read_baseline(self, hwnd: int) -> DocSnapshot | None:
        if isinstance(self._baseline, Exception):
            raise self._baseline
        return self._baseline  # type: ignore[return-value]


def _verifier(reader: ScriptedReader, ticker: Ticker | None = None) -> InsertVerifier:
    t = ticker or Ticker()
    return InsertVerifier(reader, clock=t.clock, sleep=t.sleep)


# --------------------------------------------------------------------------- #
# the three verdicts (spec §8, verbatim)
# --------------------------------------------------------------------------- #


def test_text_appearing_past_the_caret_is_inserted() -> None:
    base = DocSnapshot(text="шапка\n", caret_offset=6)
    reader = ScriptedReader(["шапка\n", "шапка\n", "шапка\nновый текст"])
    outcome = _verifier(reader).verify(HWND, "новый текст", base)
    assert outcome is VerifyOutcome.INSERTED
    assert reader.reads == 3, "polled until it appeared, then stopped"


def test_unchanged_document_past_the_deadline_is_paste_failed() -> None:
    base = DocSnapshot(text="как было", caret_offset=None)
    ticker = Ticker()
    reader = ScriptedReader(["как было"])
    outcome = _verifier(reader, ticker).verify(HWND, "текст", base)
    assert outcome is VerifyOutcome.PASTE_FAILED
    assert ticker.now >= 0.8, "the loud verdict is only reached at the deadline"


def test_dead_signal_is_verify_impossible() -> None:
    base = DocSnapshot(text="", caret_offset=None)
    assert (
        _verifier(ScriptedReader([None])).verify(HWND, "текст", base)
        is VerifyOutcome.VERIFY_IMPOSSIBLE
    )


# --------------------------------------------------------------------------- #
# the uncovered fourth row (OPEN-QUESTIONS §26)
# --------------------------------------------------------------------------- #


def test_foreign_edit_without_our_text_is_verify_impossible_not_paste_failed() -> None:
    """The document changed but our text is nowhere: neither outcome is
    provable, and a false loud paste_failed would goad a duplicate paste."""
    base = DocSnapshot(text="документ", caret_offset=None)
    reader = ScriptedReader(["документ пользователь печатает"])
    assert (
        _verifier(reader).verify(HWND, "текст", base)
        is VerifyOutcome.VERIFY_IMPOSSIBLE
    )


# --------------------------------------------------------------------------- #
# the caret slice and the occurrence count
# --------------------------------------------------------------------------- #


def test_text_already_before_the_caret_does_not_count_as_inserted() -> None:
    """The needle exists in the document ahead of the caret; nothing new ever
    appears past it. Whole-document search would lie ``inserted`` here."""
    base = DocSnapshot(text="текст и хвост:", caret_offset=14)
    reader = ScriptedReader(["текст и хвост:"])
    assert _verifier(reader).verify(HWND, "текст", base) is VerifyOutcome.PASTE_FAILED


def test_a_second_occurrence_past_the_caret_is_inserted() -> None:
    """Dictating a word into a document already full of it: the *count* past
    the caret must grow, mere presence proves nothing."""
    base = DocSnapshot(text="да, да: ", caret_offset=0)
    reader = ScriptedReader(["да, да: да", ])
    assert _verifier(reader).verify(HWND, "да", base) is VerifyOutcome.INSERTED


def test_paste_over_a_selection_is_found_from_the_selection_start() -> None:
    """The baseline's offset is the selection START — a paste over a selection
    begins where the selection began."""
    base = DocSnapshot(text="X[выделено]", caret_offset=1)
    reader = ScriptedReader(["Xтекст"])
    assert _verifier(reader).verify(HWND, "текст", base) is VerifyOutcome.INSERTED


def test_unknowable_caret_falls_back_to_whole_document_count() -> None:
    base = DocSnapshot(text="уже текст", caret_offset=None)
    reader = ScriptedReader(["уже текст текст"])
    assert _verifier(reader).verify(HWND, "текст", base) is VerifyOutcome.INSERTED


# --------------------------------------------------------------------------- #
# newline honesty
# --------------------------------------------------------------------------- #


def test_crlf_needle_matches_the_lf_document() -> None:
    """The clipboard said CRLF, the UIA provider answers LF — newline folding
    is the one normalisation allowed (verify.py's contract)."""
    base = DocSnapshot(text="", caret_offset=0)
    reader = ScriptedReader(["строка\nвторая"])
    assert (
        _verifier(reader).verify(HWND, "строка\r\nвторая", base)
        is VerifyOutcome.INSERTED
    )


# --------------------------------------------------------------------------- #
# degraded inputs never raise, never go loud
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "hwnd, text, baseline",
    [
        (None, "текст", DocSnapshot(text="", caret_offset=0)),
        (0, "текст", DocSnapshot(text="", caret_offset=0)),
        (HWND, "текст", None),
        (HWND, "", DocSnapshot(text="", caret_offset=0)),
    ],
)
def test_degraded_inputs_are_verify_impossible(hwnd, text, baseline) -> None:
    reader = ScriptedReader(["что угодно"])
    assert (
        _verifier(reader).verify(hwnd, text, baseline)
        is VerifyOutcome.VERIFY_IMPOSSIBLE
    )


def test_a_raising_reader_is_caught_to_verify_impossible() -> None:
    base = DocSnapshot(text="", caret_offset=0)
    reader = ScriptedReader([RuntimeError("COM died")])
    assert (
        _verifier(reader).verify(HWND, "текст", base)
        is VerifyOutcome.VERIFY_IMPOSSIBLE
    )


def test_a_signal_dying_mid_poll_is_verify_impossible() -> None:
    base = DocSnapshot(text="пусто", caret_offset=None)
    reader = ScriptedReader(["пусто", None])
    assert (
        _verifier(reader).verify(HWND, "текст", base)
        is VerifyOutcome.VERIFY_IMPOSSIBLE
    )


def test_baseline_guards_falsy_hwnd_and_raising_reader() -> None:
    v = _verifier(ScriptedReader([""], baseline=RuntimeError("boom")))
    assert v.baseline(None) is None
    assert v.baseline(0) is None
    assert v.baseline(HWND) is None, "a raising reader folds to None, not up"


def test_baseline_passes_the_readers_snapshot_through() -> None:
    snap = DocSnapshot(text="документ", caret_offset=3)
    v = _verifier(ScriptedReader([""], baseline=snap))
    assert v.baseline(HWND) is snap
