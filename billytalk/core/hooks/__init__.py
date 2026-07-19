"""Input hooks (spec §2): the one place BillyTalk touches other people's input.

Layered so that the dangerous part stays thin:

* ``keycodes``  — the unified code space (mouse offset 0x1000). Pure.
* ``edges``     — press/release pairing, suppression, auto-repeat, the double-Esc
                  window. Pure logic, fully tested without Windows.
* ``lowlevel``  — the ``WH_MOUSE_LL``/``WH_KEYBOARD_LL`` thread: ctypes, a
                  blocking ``GetMessage`` pump, and nothing else. Its callback
                  does one table lookup and one queue put.
* ``watchdog``  — liveness: the ``GetLastInputInfo`` divergence check and the
                  ±1 px echo probe (research/07 S6).
"""

from .edges import EdgeDecision, EdgeLogic, HookSnapshot
from .keycodes import (
    CODE_ESC,
    MOUSE_CODE_BASE,
    code_for_mouse,
    is_mouse_code,
    mouse_number,
    xbutton_to_code,
)
from .lowlevel import ECHO_MARK, SELF_MARK, HookEvent, HookThread, send_echo

__all__ = [
    "EdgeDecision",
    "EdgeLogic",
    "HookSnapshot",
    "CODE_ESC",
    "MOUSE_CODE_BASE",
    "code_for_mouse",
    "is_mouse_code",
    "mouse_number",
    "xbutton_to_code",
    "ECHO_MARK",
    "SELF_MARK",
    "HookEvent",
    "HookThread",
    "send_echo",
]
