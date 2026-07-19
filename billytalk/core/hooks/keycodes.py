"""The unified key-code space (spec §2): keyboard VKs as-is, mouse at +0x1000.

Wispr Flow's scheme, kept for the customer's muscle memory in config files:
``Mouse N`` maps to ``0x1000 + (N - 1)``, so the middle button (Mouse 3) is
4098, **Mouse 4 is 4099**, Mouse 5 is 4100, … Mouse 10 is 4105.

The X-button number lives in the **high word** of ``MSLLHOOKSTRUCT.mouseData`` —
the place where, per research/02, everyone gets it wrong: ``wParam`` says only
``WM_XBUTTONDOWN``, never which X button, and masking the whole DWORD against
1/2 silently fails because the low word is reserved.
"""

from __future__ import annotations

from typing import Final

__all__ = [
    "CODE_ESC",
    "MOUSE_CODE_BASE",
    "code_for_mouse",
    "hiword",
    "is_mouse_code",
    "mouse_number",
    "xbutton_to_code",
]

MOUSE_CODE_BASE: Final = 0x1000
CODE_ESC: Final = 0x1B  # VK_ESCAPE, unchanged: keyboard codes pass through

_MOUSE_MIN: Final = 1
_MOUSE_MAX: Final = 10


def hiword(value: int) -> int:
    """``HIWORD``: bits 16–31. The X-button number lives here, nowhere else."""
    return (value >> 16) & 0xFFFF


def code_for_mouse(number: int) -> int:
    """``Mouse N`` → unified code. Mouse 4 → 4099."""
    if not _MOUSE_MIN <= number <= _MOUSE_MAX:
        raise ValueError(f"mouse button number out of range: {number}")
    return MOUSE_CODE_BASE + (number - 1)


def is_mouse_code(code: int) -> bool:
    return MOUSE_CODE_BASE <= code < MOUSE_CODE_BASE + _MOUSE_MAX


def mouse_number(code: int) -> int:
    """Unified code → ``Mouse N``. The round trip of ``code_for_mouse``."""
    if not is_mouse_code(code):
        raise ValueError(f"not a mouse code: {code}")
    return code - MOUSE_CODE_BASE + 1


def xbutton_to_code(xbutton: int) -> int:
    """``HIWORD(mouseData)`` → unified code: XBUTTON1 (1) → Mouse 4 → 4099."""
    if xbutton not in (1, 2):
        raise ValueError(f"XBUTTON out of range: {xbutton}")
    return code_for_mouse(4 if xbutton == 1 else 5)
