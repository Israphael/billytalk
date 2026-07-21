"""Delivery (spec §8): the clipboard is the primary path, everything else is a
bonus attempt.

* ``apprules``  — per-application paste rules, keyed by window class first and
                  process name second. Terminals get Ctrl+Shift+V and newline
                  flattening — a ``\\n`` pasted into a live SSH session executes.
* ``focus``     — capture the target at press time, one bare
                  ``SetForegroundWindow`` attempt later. ``AttachThreadInput``
                  is struck from the project (measured: 0 of 8).
* ``clipboard`` — sessions with the two-snapshot sequence guard.
* ``inserter``  — the ladder: write, check focus, check modifiers, re-check the
                  sequence number, send the keystroke, verify.
* ``verify``    — the read-only UIA ``TextPattern`` signal (research/12):
                  ``inserted`` / silent ``verify_impossible`` / loud
                  ``paste_failed``.
"""

from .apprules import AppRule, PasteChord, rule_for
from .clipboard import Clipboard, ClipboardSnapshot
from .focus import Target, capture_target, try_restore_focus
from .inserter import InsertFailure, Inserter, InsertReport
from .verify import DocSnapshot, InsertVerifier, UiaDocumentReader, VerifyOutcome

__all__ = [
    "AppRule",
    "PasteChord",
    "rule_for",
    "Clipboard",
    "ClipboardSnapshot",
    "Target",
    "capture_target",
    "try_restore_focus",
    "InsertFailure",
    "Inserter",
    "InsertReport",
    "DocSnapshot",
    "InsertVerifier",
    "UiaDocumentReader",
    "VerifyOutcome",
]
