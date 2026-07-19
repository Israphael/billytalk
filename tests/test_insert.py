"""``insert/``: app rules, newline flattening, and the delivery ladder with
every Windows touchpoint faked (harness §8: FakeInput, no real windows).
"""

from __future__ import annotations

import pytest

from billytalk.core.insert.apprules import PasteChord, rule_for
from billytalk.core.insert.clipboard import ClipboardSnapshot
from billytalk.core.insert.focus import Target
from billytalk.core.insert.inserter import Inserter, prepare_text
from billytalk.core.machine.effects import DeliveryStatus, ErrorCode
from tests.fakes.input import FakeInput


def _target(**overrides: object) -> Target:
    defaults: dict[str, object] = dict(
        hwnd=0x111, focus_hwnd=0x222, pid=4242,
        process_name="notepad.exe", window_class="Notepad",
        focus_class="RichEditD2DPT", secure=False, elevated=False,
    )
    defaults.update(overrides)
    return Target(**defaults)  # type: ignore[arg-type]


class StubClipboard:
    def __init__(self, *, unchanged: bool = True) -> None:
        self.unchanged = unchanged
        self.checks = 0

    def is_unchanged_since(self, _snapshot: ClipboardSnapshot) -> bool:
        self.checks += 1
        return self.unchanged


SNAPSHOT = ClipboardSnapshot(text="прежнее", seq_after_write=7, had_text=True)


def _inserter(
    fake: FakeInput,
    *,
    clipboard: StubClipboard | None = None,
    focused: bool = True,
    restore_ok: bool = False,
) -> tuple[Inserter, StubClipboard, list[str]]:
    clip = clipboard or StubClipboard()
    calls: list[str] = []

    def focused_fn(_t: Target) -> bool:
        calls.append("focused?")
        return focused

    def restore_fn(_t: Target) -> bool:
        calls.append("restore")
        return restore_ok

    inserter = Inserter(
        clip,  # type: ignore[arg-type]
        send_chord=fake.send_chord,
        any_modifier_down=fake.any_modifier_down,
        focused=focused_fn,
        restore_focus=restore_fn,
        clock=fake.clock,
        sleep=fake.sleep,
    )
    return inserter, clip, calls


# --------------------------------------------------------------------------- #
# app rules (spec §8)
# --------------------------------------------------------------------------- #


def test_windows_terminal_gets_ctrl_shift_v_and_flattening() -> None:
    rule = rule_for("windowsterminal.exe", "CASCADIA_HOSTING_WINDOW_CLASS")
    assert rule.paste is PasteChord.CTRL_SHIFT_V
    assert rule.newline_to_space
    assert not rule.press_enter_allowed


def test_legacy_console_takes_plain_ctrl_v_but_still_flattens() -> None:
    rule = rule_for("cmd.exe", "ConsoleWindowClass")
    assert rule.paste is PasteChord.CTRL_V
    assert rule.newline_to_space, "a \\n into any live console can execute"


def test_window_class_beats_process_name() -> None:
    """A shell inside Windows Terminal reports its own process; the class is
    what tells the truth about who handles the paste."""
    rule = rule_for("powershell.exe", "CASCADIA_HOSTING_WINDOW_CLASS")
    assert rule.paste is PasteChord.CTRL_SHIFT_V


def test_putty_and_mintty_are_modern_terminals() -> None:
    assert rule_for("putty.exe", None).paste is PasteChord.CTRL_SHIFT_V
    assert rule_for("mintty.exe", None).newline_to_space


def test_unknown_targets_get_the_default() -> None:
    rule = rule_for("notepad.exe", "Notepad")
    assert rule.paste is PasteChord.CTRL_V
    assert not rule.newline_to_space


def test_word_is_blacklisted_from_background_delivery() -> None:
    assert rule_for("winword.exe", "_WwG").background_delivery is False


def test_prepare_text_flattens_newlines_only_for_terminals() -> None:
    terminal = rule_for("putty.exe", None)
    plain = rule_for("notepad.exe", None)
    text = "ssh box\r\nrm -rf ./staging\nне выполняй"
    assert prepare_text(text, terminal) == "ssh box rm -rf ./staging не выполняй"
    assert prepare_text(text, plain) == text


# --------------------------------------------------------------------------- #
# the ladder
# --------------------------------------------------------------------------- #


def test_happy_path_sends_the_rule_chord() -> None:
    fake = FakeInput()
    inserter, clip, _ = _inserter(fake)
    report = inserter.insert(_target(), SNAPSHOT)

    assert report.ok
    assert fake.chords == [PasteChord.CTRL_V]
    assert clip.checks == 1, "the sequence number is re-checked right before the send"


def test_terminal_target_gets_its_chord() -> None:
    fake = FakeInput()
    inserter, _, _ = _inserter(fake)
    report = inserter.insert(
        _target(process_name="putty.exe", window_class="PuTTY"), SNAPSHOT
    )
    assert report.ok
    assert fake.chords == [PasteChord.CTRL_SHIFT_V]


def test_secure_field_is_refused_loudly() -> None:
    fake = FakeInput()
    inserter, _, _ = _inserter(fake)
    report = inserter.insert(_target(secure=True), SNAPSHOT)

    assert not report.ok and report.failure is not None
    assert report.failure.status is DeliveryStatus.BLOCKED_SECURE
    assert report.failure.code is ErrorCode.SECURE_FIELD
    assert fake.chords == [], "nothing is typed at a password field"


def test_lost_focus_with_failed_restore_is_focus_lost() -> None:
    fake = FakeInput()
    inserter, _, calls = _inserter(fake, focused=False, restore_ok=False)
    report = inserter.insert(_target(), SNAPSHOT)

    assert calls == ["focused?", "restore"], "exactly one bare attempt (spec §8)"
    assert not report.ok and report.failure is not None
    assert report.failure.status is DeliveryStatus.FOCUS_LOST
    assert fake.chords == []


def test_lost_focus_with_successful_restore_pastes() -> None:
    fake = FakeInput()
    inserter, _, _ = _inserter(fake, focused=False, restore_ok=True)
    report = inserter.insert(_target(), SNAPSHOT)
    assert report.ok
    assert fake.chords == [PasteChord.CTRL_V]


def test_briefly_held_modifier_is_waited_out() -> None:
    fake = FakeInput(modifier_timeline=[True, True, True, False])
    inserter, _, _ = _inserter(fake)
    report = inserter.insert(_target(), SNAPSHOT)

    assert report.ok
    assert len(fake.slept) == 3, "polled until the modifiers came up"
    assert fake.chords == [PasteChord.CTRL_V]


def test_modifier_held_past_500ms_cancels_the_paste() -> None:
    """Spec §8: a paste under the user's held Shift becomes something else —
    do not paste; the text stays on the clipboard."""
    fake = FakeInput(modifier_timeline=[True])  # held forever
    inserter, _, _ = _inserter(fake)
    report = inserter.insert(_target(), SNAPSHOT)

    assert not report.ok and report.failure is not None
    assert report.failure.status is DeliveryStatus.LEFT_ON_CLIPBOARD
    assert report.failure.code is ErrorCode.PASTE_FAILED
    assert fake.chords == []
    assert fake.now >= 0.5, "the full 500 ms courtesy was extended"


def test_replaced_clipboard_cancels_the_paste() -> None:
    """OPEN-QUESTIONS §16: pasting would paste THEIR text; their copy wins."""
    fake = FakeInput()
    inserter, _, _ = _inserter(fake, clipboard=StubClipboard(unchanged=False))
    report = inserter.insert(_target(), SNAPSHOT)

    assert not report.ok and report.failure is not None
    assert report.failure.code is ErrorCode.PASTE_FAILED
    assert fake.chords == [], "no chord: Ctrl+V would deliver someone else's clipboard"
