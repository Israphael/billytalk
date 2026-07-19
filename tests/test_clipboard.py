"""``clipboard`` (harness §8): session order on a fake clipboard, and the
restore guard that cancels itself when the sequence number moved.

The fake emulates the one behaviour everything depends on: the sequence number
advances on every write operation, and reads it as zero when access is denied.
``win32clipboard`` is monkeypatched at the module the code imports.
"""

from __future__ import annotations

from typing import Any

import pytest

import billytalk.core.insert.clipboard as clipboard_module
from billytalk.core.insert.clipboard import Clipboard

CF_UNICODETEXT = 13


class FakeWinClipboard:
    """A scriptable stand-in for the win32clipboard module surface we use."""

    def __init__(self) -> None:
        self.data: dict[int, Any] = {}
        self.seq = 100
        self.open = False
        self.fail_next_opens = 0
        self.access_denied = False
        self.log: list[str] = []
        self.registered: dict[str, int] = {}
        self._next_format = 0xC000
        self.frozen_seq = False  # a write that silently does not advance

    # module surface ---------------------------------------------------- #

    def OpenClipboard(self, _owner: int) -> None:
        self.log.append("open")
        if self.fail_next_opens > 0:
            self.fail_next_opens -= 1
            raise RuntimeError("clipboard busy")
        self.open = True

    def CloseClipboard(self) -> None:
        self.log.append("close")
        self.open = False

    def EmptyClipboard(self) -> None:
        assert self.open, "EmptyClipboard outside a session"
        self.log.append("empty")
        self.data.clear()
        if not self.frozen_seq:
            self.seq += 1

    def SetClipboardData(self, fmt: int, value: Any) -> None:
        assert self.open, "SetClipboardData outside a session"
        self.log.append(f"set:{fmt}")
        self.data[fmt] = value
        if not self.frozen_seq:
            self.seq += 1

    def GetClipboardData(self, fmt: int) -> Any:
        assert self.open
        self.log.append(f"get:{fmt}")
        if fmt not in self.data:
            raise RuntimeError("format not available")
        return self.data[fmt]

    def IsClipboardFormatAvailable(self, fmt: int) -> bool:
        return fmt in self.data

    def GetClipboardSequenceNumber(self) -> int:
        return 0 if self.access_denied else self.seq

    def RegisterClipboardFormat(self, name: str) -> int:
        if name not in self.registered:
            self.registered[name] = self._next_format
            self._next_format += 1
        return self.registered[name]

    # test helpers ------------------------------------------------------ #

    def external_write(self, text: str) -> None:
        """Someone else (the user's Ctrl+C, a clipboard manager) writes."""
        self.data[CF_UNICODETEXT] = text
        self.seq += 1


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeWinClipboard:
    fake = FakeWinClipboard()
    monkeypatch.setattr(clipboard_module, "win32clipboard", fake)
    return fake


@pytest.fixture
def clipboard() -> Clipboard:
    return Clipboard(sleep=lambda _s: None)


def test_write_session_order_on_a_fake_clipboard(
    fake: FakeWinClipboard, clipboard: Clipboard
) -> None:
    """One session, in the spec §8 order: open → read previous → empty → set →
    close. Both sequence snapshots are taken OUTSIDE the session."""
    fake.data[CF_UNICODETEXT] = "чужой прежний текст"
    snapshot = clipboard.write("наш текст")

    assert fake.log == ["open", f"get:{CF_UNICODETEXT}", "empty", f"set:{CF_UNICODETEXT}", "close"]
    assert fake.data[CF_UNICODETEXT] == "наш текст"
    assert snapshot.text == "чужой прежний текст"
    assert snapshot.had_text
    assert snapshot.seq_after_write == fake.seq


def test_restore_puts_the_previous_text_back(
    fake: FakeWinClipboard, clipboard: Clipboard
) -> None:
    fake.data[CF_UNICODETEXT] = "копия пользователя"
    snapshot = clipboard.write("наш текст")

    assert clipboard.restore(snapshot) is True
    assert fake.data[CF_UNICODETEXT] == "копия пользователя"


def test_restore_cancelled_when_sequence_number_changed(
    fake: FakeWinClipboard, clipboard: Clipboard
) -> None:
    """The mandatory case (harness §8): the user copied something after our
    write; restoring would destroy their newer copy."""
    fake.data[CF_UNICODETEXT] = "старая копия"
    snapshot = clipboard.write("наш текст")

    fake.external_write("свежая копия пользователя")

    assert clipboard.restore(snapshot) is False
    assert fake.data[CF_UNICODETEXT] == "свежая копия пользователя", "their copy wins"


def test_zero_sequence_number_is_a_refusal_not_a_value(
    fake: FakeWinClipboard, clipboard: Clipboard
) -> None:
    fake.data[CF_UNICODETEXT] = "что-то"
    snapshot = clipboard.write("наш текст")

    fake.access_denied = True
    assert clipboard.restore(snapshot) is False, "0 means access denied: do not touch"
    assert fake.data[CF_UNICODETEXT] == "наш текст"


def test_is_unchanged_since_detects_a_replacement(
    fake: FakeWinClipboard, clipboard: Clipboard
) -> None:
    snapshot = clipboard.write("наш текст")
    assert clipboard.is_unchanged_since(snapshot) is True

    fake.external_write("подменили")
    assert clipboard.is_unchanged_since(snapshot) is False, (
        "pasting now would paste someone else's text (spec §8)"
    )


def test_open_clipboard_retries_with_backoff(fake: FakeWinClipboard) -> None:
    slept: list[float] = []
    clipboard = Clipboard(sleep=slept.append)
    fake.fail_next_opens = 3

    clipboard.write("наш текст")

    assert slept == [0.010, 0.020, 0.040], "10 ms doubling toward the 200 ms cap"
    assert fake.data[CF_UNICODETEXT] == "наш текст"


def test_open_clipboard_gives_up_after_ten_attempts(fake: FakeWinClipboard) -> None:
    slept: list[float] = []
    clipboard = Clipboard(sleep=slept.append)
    fake.fail_next_opens = 99

    with pytest.raises(RuntimeError):
        clipboard.write("наш текст")
    assert len(slept) == 9, "ten attempts, nine waits"
    assert slept[-1] == 0.200, "capped at 200 ms"


def test_unconfirmed_write_raises(fake: FakeWinClipboard, clipboard: Clipboard) -> None:
    """The project's rule: a call that reports success proves nothing — the
    sequence counter is the observable effect, and it must move."""
    fake.frozen_seq = True
    with pytest.raises(RuntimeError, match="not confirmed"):
        clipboard.write("наш текст")


def test_restoring_an_originally_empty_clipboard_empties_it(
    fake: FakeWinClipboard, clipboard: Clipboard
) -> None:
    snapshot = clipboard.write("наш текст")
    assert not snapshot.had_text

    assert clipboard.restore(snapshot) is True
    assert CF_UNICODETEXT not in fake.data, "emptiness is what was there before us"


def test_exclusion_formats_only_when_asked_and_as_real_bytes(
    fake: FakeWinClipboard, clipboard: Clipboard
) -> None:
    """Spec §8: flags by target, not by outcome — and never a NULL handle."""
    clipboard.write("обычная запись")
    assert not fake.registered, "cycle 1 has no trusted-verification targets"

    clipboard.write("исключённая запись", exclude_from_history=True)
    assert set(fake.registered) == {
        "ExcludeClipboardContentFromMonitorProcessing",
        "CanIncludeInClipboardHistory",
        "CanUploadToCloudClipboard",
    }
    for fmt in fake.registered.values():
        assert fake.data[fmt] == b"\x00\x00\x00\x00", "a real 4-byte allocation, not NULL"
