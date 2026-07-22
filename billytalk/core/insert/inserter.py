"""The delivery ladder (spec §8), verification included since cycle-2 M1.

Order, with the reasons welded on:

1. The text is already in the history (written at StopCapture) and already in
   the clipboard (the machine emits ``WriteClipboard`` before ``Insert``).
2. Secure field → refuse loudly (``blocked_secure``); silence is forbidden.
3. Focus still on the captured window → paste in place. Focus gone → one bare
   ``SetForegroundWindow`` (25%), then give up to the clipboard cue.
4. Held modifiers → wait up to 500 ms; still held → do not paste (a Ctrl+V
   with the user's own Shift on top becomes something else entirely).
5. Re-check the clipboard sequence number. If a clipboard manager or the
   user's own Ctrl+C replaced our text, pasting would paste *their* content —
   the paste is cancelled and their copy is left alone (OPEN-QUESTIONS §16).
6. Send the chord — VK 0x56 without ``KEYEVENTF_SCANCODE``, so it survives
   Cyrillic, AZERTY and DVORAK layouts — marked with ``SELF_MARK`` so our own
   hook passes it through instead of re-entering itself.
7. Verify by the UIA ``TextPattern`` read (``verify.py``, research/12):
   ``inserted`` / silent ``verify_impossible`` / loud ``paste_failed``. The
   baseline is taken **before** the chord — after it, the pre-paste caret is
   gone.

Restoration of the user's clipboard happens later, scheduled by the driver
after :data:`RESTORE_DELAY_MS`: restoring immediately would race the target's
WM_PASTE handling and paste the *restored* content instead of ours.

The machine's ``InsertFailed`` writes ``left_on_clipboard``; spec §8 wants the
finer statuses (``blocked_secure``, ``focus_lost``). The report carries the
precise status and the driver writes it (OPEN-QUESTIONS §17).
"""

from __future__ import annotations

import ctypes as ct
import time
from collections.abc import Callable
from ctypes import wintypes
from dataclasses import dataclass
from typing import Final

from ..hooks.lowlevel import SELF_MARK  # the loop-guard mark, shared project-wide
from ..machine.effects import DeliveryStatus, ErrorCode
from .apprules import AppRule, PasteChord, rule_for
from .clipboard import Clipboard, ClipboardSnapshot
from .focus import Target, current_focus_hwnd, is_still_focused, try_restore_focus
from .verify import InsertVerifier, VerifyOutcome

__all__ = ["InsertFailure", "InsertReport", "Inserter", "RESTORE_DELAY_MS", "prepare_text"]

RESTORE_DELAY_MS: Final = 300
"""Delay before the clipboard restore. Confirmed conservative by spike S2
(research/12): a pasted marker becomes visible in the target a median 152 ms
after SendInput (max 180 over 8 rounds), so 300 ms clears it comfortably. Kept
at 300 rather than trimmed — a late restore is harmless, an early one pastes
the wrong text."""

MODIFIER_WAIT_MS: Final = 500  # spec §8: wait up to 500 ms for release

_user32 = ct.WinDLL("user32", use_last_error=True)
_user32.GetAsyncKeyState.restype = ct.c_short
_user32.GetAsyncKeyState.argtypes = (ct.c_int,)

_VK_SHIFT: Final = 0x10
_VK_CONTROL: Final = 0x11
_VK_MENU: Final = 0x12
_VK_LWIN: Final = 0x5B
_VK_RWIN: Final = 0x5C
_VK_V: Final = 0x56
_VK_INSERT: Final = 0x2D
_MODIFIERS: Final = (_VK_SHIFT, _VK_CONTROL, _VK_MENU, _VK_LWIN, _VK_RWIN)

_KEYEVENTF_KEYUP: Final = 0x0002
_INPUT_KEYBOARD: Final = 1

_ULONG_PTR = ct.c_size_t


class _KEYBDINPUT(ct.Structure):
    _fields_ = (
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", _ULONG_PTR),
    )


class _INPUT(ct.Structure):
    class _U(ct.Union):
        _fields_ = (("ki", _KEYBDINPUT), ("pad", ct.c_byte * 32))

    _anonymous_ = ("u",)
    _fields_ = (("type", wintypes.DWORD), ("u", _U))


def send_paste_chord(chord: PasteChord) -> None:
    """The target's own paste chord, by VK code, marked as our synthetic input.

    Three of them, because terminals do not agree: Ctrl+V for ordinary windows
    and legacy conhost, Ctrl+Shift+V for Windows Terminal and modern PuTTY,
    Shift+Insert for mintty — measured there rather than assumed, because Git
    Bash ignores Ctrl+Shift+V outright. VK codes without KEYEVENTF_SCANCODE, so
    the chord survives Cyrillic, AZERTY and Dvorak (spec §8).
    """
    if chord is PasteChord.SHIFT_INSERT:
        keys = [_VK_SHIFT, _VK_INSERT]
    else:
        keys = [_VK_CONTROL]
        if chord is PasteChord.CTRL_SHIFT_V:
            keys.append(_VK_SHIFT)
        keys.append(_VK_V)

    sequence = keys + [-k for k in reversed(keys)]  # downs, then ups in reverse
    events = (_INPUT * len(sequence))()
    for i, signed_vk in enumerate(sequence):
        events[i].type = _INPUT_KEYBOARD
        events[i].ki = _KEYBDINPUT(
            abs(signed_vk), 0, _KEYEVENTF_KEYUP if signed_vk < 0 else 0, 0, SELF_MARK
        )
    _user32.SendInput(len(sequence), events, ct.sizeof(_INPUT))


def modifiers_down() -> bool:
    return any(_user32.GetAsyncKeyState(vk) & 0x8000 for vk in _MODIFIERS)


def prepare_text(text: str, rule: AppRule) -> str:
    """Terminal targets get their newlines flattened **before** the clipboard
    write (spec §8): a ``\\n`` pasted into a live SSH session executes."""
    if rule.newline_to_space:
        return text.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
    return text


@dataclass(frozen=True, slots=True)
class InsertFailure:
    code: ErrorCode
    status: DeliveryStatus
    detail: str


@dataclass(frozen=True, slots=True)
class InsertReport:
    ok: bool
    status: DeliveryStatus = DeliveryStatus.INSERTED
    """The precise row status for the ok path: ``INSERTED`` when the paste was
    verified (or verification is not wired, the cycle-1 stance), the silent
    ``VERIFY_IMPOSSIBLE`` when the signal was unavailable (spec §8). The driver
    writes it the same way it writes ``failure.status`` (OPEN-QUESTIONS §17)."""
    failure: InsertFailure | None = None


class Inserter:
    """The ladder with every Windows touchpoint injectable, so the whole policy
    is testable without a single real window."""

    def __init__(
        self,
        clipboard: Clipboard,
        *,
        verifier: InsertVerifier | None = None,
        send_chord: Callable[[PasteChord], None] = send_paste_chord,
        any_modifier_down: Callable[[], bool] = modifiers_down,
        focused: Callable[[Target], bool] = is_still_focused,
        restore_focus: Callable[[Target], bool] = try_restore_focus,
        focused_control: Callable[[], int | None] = current_focus_hwnd,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._clipboard = clipboard
        self._verifier = verifier
        self._send_chord = send_chord
        self._any_modifier_down = any_modifier_down
        self._focused = focused
        self._restore_focus = restore_focus
        self._focused_control = focused_control
        self._clock = clock
        self._sleep = sleep

    def rule_for_target(self, target: Target) -> AppRule:
        return rule_for(target.process_name, target.window_class)

    def insert(
        self, target: Target, snapshot: ClipboardSnapshot, text: str | None = None
    ) -> InsertReport:
        """Steps 2–7 of the ladder. The clipboard write already happened;
        ``text`` is what it wrote (post ``prepare_text``) — the needle the
        verification searches for. ``None`` (or no verifier) skips step 7."""
        rule = self.rule_for_target(target)

        if target.secure:
            return InsertReport(
                ok=False,
                failure=InsertFailure(
                    ErrorCode.SECURE_FIELD, DeliveryStatus.BLOCKED_SECURE,
                    "password field or credential dialog",
                ),
            )

        if not self._focused(target):
            if not self._restore_focus(target):
                # Measured 25%: a normal outcome. The loud clipboard cue and the
                # notification are the driver's, triggered by this status.
                return InsertReport(
                    ok=False,
                    failure=InsertFailure(
                        ErrorCode.FOCUS_LOST, DeliveryStatus.FOCUS_LOST,
                        "target lost focus; one bare restore attempt failed",
                    ),
                )

        # Spec §8: «HWND и контрол в фокусе захватываются в момент нажатия.
        # Перед вставкой сверяются.» The window survived the check above, but
        # the focused CONTROL may have moved — the user clicked another field
        # of the same form during transcription. Pasting would land in the
        # wrong field (Wispr Flow complaint №2), and verification would read
        # the press-time control and cry paste_failed over a landed paste
        # (M1 review, the confirmed high finding). Loud, never blind.
        live_control = self._focused_control()
        if (
            live_control is not None
            and target.focus_hwnd
            and live_control != target.focus_hwnd
        ):
            return InsertReport(
                ok=False,
                failure=InsertFailure(
                    ErrorCode.FOCUS_LOST, DeliveryStatus.FOCUS_LOST,
                    "focused control changed since press",
                ),
            )

        if not self._wait_modifiers_released():
            return InsertReport(
                ok=False,
                failure=InsertFailure(
                    ErrorCode.PASTE_FAILED, DeliveryStatus.LEFT_ON_CLIPBOARD,
                    "a modifier stayed held past 500 ms",
                ),
            )

        if not self._clipboard.is_unchanged_since(snapshot):
            # Someone wrote after us. Pasting now would paste THEIR text, and
            # overwriting again would destroy their newer copy — their intent is
            # fresher, so the paste is cancelled (OPEN-QUESTIONS §16).
            return InsertReport(
                ok=False,
                failure=InsertFailure(
                    ErrorCode.PASTE_FAILED, DeliveryStatus.LEFT_ON_CLIPBOARD,
                    "clipboard replaced between write and paste",
                ),
            )

        if self._verifier is None or text is None:
            # No verifier wired (tests, cycle-1 assemblies): the report says
            # the chord was sent — the cycle-1 stance, status INSERTED.
            self._send_chord(rule.paste)
            return InsertReport(ok=True)

        # The baseline must precede the chord: afterwards the pre-paste caret
        # position is gone. A None baseline is already the verify_impossible
        # verdict and rides through verify() unchanged. Verified against the
        # control that is focused NOW — the one the chord actually lands in;
        # unreadable → the top window, whose reader finds the document itself.
        verify_hwnd = live_control or target.hwnd
        baseline = self._verifier.baseline(verify_hwnd)
        self._send_chord(rule.paste)
        outcome = self._verifier.verify(verify_hwnd, text, baseline)
        if outcome is VerifyOutcome.PASTE_FAILED:
            # Loud (spec §8): the document provably ignored the chord. The text
            # stays on the clipboard, so the status is left_on_clipboard with
            # the precise error code — the same shape OPEN-QUESTIONS §16 set.
            return InsertReport(
                ok=False,
                failure=InsertFailure(
                    ErrorCode.PASTE_FAILED, DeliveryStatus.LEFT_ON_CLIPBOARD,
                    "verified: target document unchanged after the chord",
                ),
            )
        status = (
            DeliveryStatus.INSERTED
            if outcome is VerifyOutcome.INSERTED
            else DeliveryStatus.VERIFY_IMPOSSIBLE
        )
        return InsertReport(ok=True, status=status)

    def _wait_modifiers_released(self) -> bool:
        deadline = self._clock() + MODIFIER_WAIT_MS / 1000
        while self._any_modifier_down():
            if self._clock() >= deadline:
                return False
            self._sleep(0.016)
        return True
