"""Per-application delivery rules (spec §8), window class first.

The class is the reliable signal — a console window is ``ConsoleWindowClass``
whoever launched it, and Windows Terminal is ``CASCADIA_HOSTING_WINDOW_CLASS``
regardless of the shell inside — so class rules run before process rules.

The terminal rules exist for one hard reason (spec §8): the customer manages
VPN infrastructure over SSH, a paste with ``\\n`` into a live session
**executes the command**, and there is no undo there. So for every terminal
class: newlines become spaces before the clipboard write, and
``press_enter_after`` is forced off (moot in cycle 1 — nothing implements it —
but the rule carries it so cycle 2 cannot forget).

``_WwG`` (Word) is blacklisted from background delivery permanently: its input
stack ignores posted messages (research/10), and time spent trying is time the
user waits. Background delivery defaults to off for everyone in MVP-0.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Final

__all__ = ["AppRule", "PasteChord", "rule_for"]


class PasteChord(Enum):
    CTRL_V = "ctrl_v"
    CTRL_SHIFT_V = "ctrl_shift_v"
    SHIFT_INSERT = "shift_insert"


@dataclass(frozen=True, slots=True)
class AppRule:
    paste: PasteChord
    newline_to_space: bool
    press_enter_allowed: bool
    background_delivery: bool
    verifiable: bool = True
    """Can :mod:`billytalk.core.insert.verify` ever confirm a paste here?

    False for terminals: they draw their own screen and expose no UIA text, so
    the verifier answers ``verify_impossible`` **every** time — not
    occasionally. Spec §8 makes that outcome silent, which is right when a
    paste usually lands and wrong when it structurally cannot be checked: in a
    terminal the user would be guessing after every dictation. Where this flag
    is False the driver plays the quiet «текст в буфере» cue instead of saying
    nothing (customer's decision, 22.07 — OPEN-QUESTIONS §40)."""


_DEFAULT: Final = AppRule(
    paste=PasteChord.CTRL_V,
    newline_to_space=False,
    press_enter_allowed=True,
    background_delivery=False,  # default off (research/10): «кое-как» is not a path
)

_MODERN_TERMINAL: Final = AppRule(
    paste=PasteChord.CTRL_SHIFT_V,
    newline_to_space=True,
    press_enter_allowed=False,
    background_delivery=False,
    verifiable=False,
)

_MINTTY: Final = AppRule(
    # MEASURED on the customer's machine, 22.07: in Git Bash's mintty
    # Ctrl+Shift+V does nothing, Shift+Insert pastes, right-click pastes.
    # mintty was classified as a «modern terminal» by extrapolation — the spec's
    # table (§8) never named it — and the extrapolation was wrong. The result
    # was the worst possible pair: a chord that never lands, and an outcome
    # (verify_impossible) that says nothing about it, so dictation into Git Bash
    # looked like a product that does not work.
    paste=PasteChord.SHIFT_INSERT,
    newline_to_space=True,
    press_enter_allowed=False,
    background_delivery=False,
    verifiable=False,
)

_LEGACY_CONSOLE: Final = AppRule(
    paste=PasteChord.CTRL_V,  # legacy conhost accepts plain Ctrl+V (spec §8)
    newline_to_space=True,
    press_enter_allowed=False,
    background_delivery=False,
    verifiable=False,
)

_WORD: Final = AppRule(
    paste=PasteChord.CTRL_V,
    newline_to_space=False,
    press_enter_allowed=True,
    background_delivery=False,  # _WwG: blacklisted forever (research/10)
)

_BY_CLASS: Final[dict[str, AppRule]] = {
    "CASCADIA_HOSTING_WINDOW_CLASS": _MODERN_TERMINAL,  # Windows Terminal
    "ConsoleWindowClass": _LEGACY_CONSOLE,  # cmd / legacy conhost
    "mintty": _MINTTY,
    "_WwG": _WORD,
}

_BY_PROCESS: Final[dict[str, AppRule]] = {
    "windowsterminal.exe": _MODERN_TERMINAL,
    "alacritty.exe": _MODERN_TERMINAL,
    "putty.exe": _MODERN_TERMINAL,  # new builds take Ctrl+Shift+V (spec §8)
    "mintty.exe": _MINTTY,
    "cmd.exe": _LEGACY_CONSOLE,
    "conhost.exe": _LEGACY_CONSOLE,
    "winword.exe": _WORD,
}


def rule_for(process_name: str | None, window_class: str | None) -> AppRule:
    """The delivery rule for a target. Class beats process beats default."""
    if window_class:
        rule = _BY_CLASS.get(window_class)
        if rule is not None:
            return rule
    if process_name:
        rule = _BY_PROCESS.get(process_name.lower())
        if rule is not None:
            return rule
    return _DEFAULT
