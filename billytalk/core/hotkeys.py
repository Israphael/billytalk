"""Hotkey capture (spec §12 «живой захват», §14) and the last-dictation
chords Ctrl+Alt+Z / Ctrl+Alt+X (spec §9).

Both live on the driver thread via ``post_job`` — the same lane the history
insert rides, so a chord paste is serialised with ordinary delivery (spec §8's
clipboard mutex by construction). The hook thread only enqueues.

Capture (spec §14, verbatim constraints): **таймаут 30 с** — a self-validating
scheduler guard, because the scheduler cannot cancel; **снимается при разрыве
канала** — the server's on_disconnect posts the cancel, so a dead interface
can never leave keys suppressed. The captured binding applies immediately
(мастер шага 3: «оно назначится само»): config saved, the hook's bound set
swapped, the interface told via ``hotkey_captured``.

MVP-0 records a **single** final code into ``ptt_code`` (the unified space of
spec §2) — modifiers pressed on the way are swallowed by the capture and
ignored (OPEN-QUESTIONS §30). The fixed chords (Ctrl+Alt+D/T/Z/X/B) are not
capturable in MVP-0.

Ctrl+Alt+Z pastes the freshest dictation **shown to the user** — retry-track
arrivals are excluded by ``retry_count = 0`` (spec §9). Older than two
minutes, or a terminal target (spec §8), asks for confirmation: the warn cue,
then the same chord again within ten seconds (OPEN-QUESTIONS §29). The
clipboard is not restored afterwards — the user asked for this text
(OPEN-QUESTIONS §27).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any, Final

from .insert.apprules import rule_for
from .insert.inserter import prepare_text
from .ipc.protocol import reply
from .machine.effects import Cue

log = logging.getLogger("billytalk.hotkeys")

__all__ = [
    "CHORD_COPY_VK",
    "CHORD_PASTE_VK",
    "CAPTURE_TIMEOUT_MS",
    "CONFIRM_WINDOW_MS",
    "OLD_AFTER_MS",
    "HotkeyActions",
    "HotkeyCapture",
    "display_for_code",
]

CHORD_PASTE_VK: Final = 0x5A  # Z — вставить последнее (spec §2)
CHORD_COPY_VK: Final = 0x58   # X — скопировать последнее

CAPTURE_TIMEOUT_MS: Final = 30_000  # spec §14, verbatim
CONFIRM_WINDOW_MS: Final = 10_000   # OPEN-QUESTIONS §29
OLD_AFTER_MS: Final = 2 * 60_000    # spec §9: «старше 2 минут — подтверждение»

_MOUSE_BASE: Final = 0x1000

_VK_NAMES: Final = {
    0x08: "Backspace", 0x09: "Tab", 0x0D: "Enter", 0x20: "Space",
    0x21: "PageUp", 0x22: "PageDown", 0x23: "End", 0x24: "Home",
    0x25: "←", 0x26: "↑", 0x27: "→", 0x28: "↓",
    0x2C: "PrintScreen", 0x2D: "Insert", 0x2E: "Delete",
}


def display_for_code(code: int) -> str:
    """The human name of a unified-space code (spec §2) for the capture
    answer and the settings row."""
    if _MOUSE_BASE <= code < _MOUSE_BASE + 10:
        return f"Mouse {code - _MOUSE_BASE + 1}"
    if 0x70 <= code <= 0x87:
        return f"F{code - 0x6F}"
    if 0x30 <= code <= 0x39 or 0x41 <= code <= 0x5A:
        return chr(code)
    return _VK_NAMES.get(code, f"VK 0x{code:02X}")


def _capturable(code: int) -> bool:
    """What may become the dictation button: a mouse button of the unified
    space, or a plain key that is not Esc (the cancel gesture, already routed
    apart by the edge logic — this is defence in depth)."""
    if _MOUSE_BASE <= code < _MOUSE_BASE + 10:
        return True
    return 0x08 <= code <= 0xFE and code != 0x1B


class HotkeyCapture:
    """The capture lifecycle. Entered by a verb, left by a captured key, Esc,
    the 30-second guard, a stop verb, or the channel breaking — whichever
    comes first; every exit path ends suppression (spec §14)."""

    def __init__(
        self,
        *,
        post_job: Callable[[Callable[[], None]], None],
        schedule_at: Callable[[int, Callable[[], None]], None],
        now_ms: Callable[[], int],
        begin_capture: Callable[[], None],
        end_capture: Callable[[], None],
        apply_binding: Callable[[int], None],
        send: Callable[[dict[str, Any]], bool],
    ) -> None:
        self._post_job = post_job
        self._schedule_at = schedule_at
        self._now_ms = now_ms
        self._begin_capture = begin_capture
        self._end_capture = end_capture
        self._apply_binding = apply_binding
        self._send = send
        # Driver-thread state. The generation stamps every timeout guard: the
        # scheduler cannot cancel, so a stale guard must find itself outvoted.
        self._action: str | None = None
        self._generation = 0

    # -- verbs (server read thread) ------------------------------------- #

    def start(self, rid: int | None, action: object) -> None:
        if action != "ptt":
            self._reply(rid, error="bad_action")
            return

        def job() -> None:
            if self._action is not None:
                self._reply(rid, error="busy")
                return
            self._action = "ptt"
            self._generation += 1
            generation = self._generation
            self._begin_capture()
            self._schedule_at(self._now_ms() + CAPTURE_TIMEOUT_MS,
                              lambda: self._timeout(generation))
            self._reply(rid, result={})

        self._post_job(job)

    def stop(self, rid: int | None) -> None:
        def job() -> None:
            if self._action is not None:
                self._deactivate()
            self._reply(rid, result={})

        self._post_job(job)

    # -- hook thread ----------------------------------------------------- #

    def on_capture_event(self, kind: str, code: int) -> None:
        """Enqueue-only, as the hook demands."""
        self._post_job(lambda: self._event_job(kind, code))

    # -- channel --------------------------------------------------------- #

    def cancel_on_disconnect(self) -> None:
        """Server read thread, on channel break (spec §14: «снимается при
        разрыве канала»). No frame — there is no one left to tell."""
        self._post_job(self._deactivate_if_active)

    # -- driver-thread jobs ---------------------------------------------- #

    def _event_job(self, kind: str, code: int) -> None:
        if self._action is None:
            return
        if kind == "capture_cancel":
            self._deactivate()
            self._send({"type": "hotkey_captured", "codes": [], "display": ""})
            return
        if not _capturable(code):
            return  # swallowed by the hook; keep waiting for a real key
        # apply_binding writes config.json — a real OSError path (disk full,
        # OneDrive locking the roaming file). If it raised, _deactivate would
        # be skipped and capture mode would stay on, swallowing every key until
        # the 30 s guard. Deactivate in finally, unconditionally: leaving the
        # keyboard dead is far worse than a binding that did not persist.
        applied = False
        try:
            self._apply_binding(code)
            applied = True
        except Exception:
            log.exception("applying the captured binding failed")
        finally:
            self._deactivate()
        if applied:
            self._send({
                "type": "hotkey_captured",
                "codes": [code],
                "display": display_for_code(code),
            })
        else:
            # The binding did not stick — tell the interface it was not
            # captured, so it does not show a key the core is not bound to.
            self._send({"type": "hotkey_captured", "codes": [], "display": ""})

    def _timeout(self, generation: int) -> None:
        if self._action is None or generation != self._generation:
            return  # a newer capture (or none) owns the mode now
        self._deactivate()
        self._send({"type": "hotkey_captured", "codes": [], "display": ""})

    def _deactivate_if_active(self) -> None:
        if self._action is not None:
            self._deactivate()

    def _deactivate(self) -> None:
        self._action = None
        self._generation += 1
        self._end_capture()

    def _reply(self, rid: int | None, *, result: dict[str, Any] | None = None,
               error: str | None = None) -> None:
        if rid is not None:
            self._send(reply(rid, result, error=error))


class HotkeyActions:
    """Ctrl+Alt+Z / Ctrl+Alt+X, executed by the CORE (spec §9): the target is
    captured at the chord press — the user is standing in the window the text
    is owed to — and the ladder runs with verification, exactly like delivery.
    """

    def __init__(
        self,
        *,
        post_job: Callable[[Callable[[], None]], None],
        last_shown: Callable[[], Any],
        capture_target: Callable[[], Any],
        clipboard_write: Callable[[str], Any],
        insert: Callable[[Any, Any, str], Any],
        play_cue: Callable[[Cue], None],
        now_ms: Callable[[], int],
        wall_ms: Callable[[], int],
    ) -> None:
        self._post_job = post_job
        self._last_shown = last_shown
        self._capture_target = capture_target
        self._clipboard_write = clipboard_write
        self._insert = insert
        self._play_cue = play_cue
        self._now_ms = now_ms
        self._wall_ms = wall_ms
        self._pending_confirm: tuple[int, int] | None = None  # (row_id, deadline)

    # -- hook thread ------------------------------------------------------ #

    def on_chord(self, code: int) -> None:
        """Enqueue-only chord router (the driver's ``on_chord``)."""
        if code == CHORD_PASTE_VK:
            self._post_job(self._paste_job)
        elif code == CHORD_COPY_VK:
            self._post_job(self._copy_job)

    # -- driver-thread jobs ------------------------------------------------ #

    def _fresh_row(self) -> tuple[Any, str] | None:
        row = self._last_shown()
        if row is None:
            self._play_cue(Cue.REJECT)
            return None
        text = row["text_final"] or row["text_raw"]
        if not text:
            self._play_cue(Cue.REJECT)
            return None
        return row, text

    def _paste_job(self) -> None:
        found = self._fresh_row()
        if found is None:
            return
        row, text = found
        target = self._capture_target()
        rule = rule_for(
            getattr(target, "process_name", None),
            getattr(target, "window_class", None),
        )
        age_ms = self._wall_ms() - int(row["created_at"])
        # Spec §9: older than two minutes asks first. Spec §8: a terminal
        # target always asks — a pasted \n executes in a live SSH session.
        if age_ms > OLD_AFTER_MS or rule.newline_to_space:
            if not self._confirmed(int(row["id"])):
                self._pending_confirm = (
                    int(row["id"]), self._now_ms() + CONFIRM_WINDOW_MS
                )
                self._play_cue(Cue.WARN)  # «ещё раз тем же аккордом — вставит»
                return
        self._pending_confirm = None
        prepared = prepare_text(text, rule)
        # clipboard.write can raise (the board owned by another app, the
        # sequence guard tripping). The driver would log-and-swallow it, but a
        # chord the user pressed must never fail silently (spec §11): sound the
        # error cue. No clipboard restore on success (OPEN-QUESTIONS §27).
        try:
            snapshot = self._clipboard_write(prepared)
            if target is None:
                self._play_cue(Cue.CLIPBOARD)
                return
            report = self._insert(target, snapshot, prepared)
        except Exception:
            log.exception("chord paste failed")  # no transcript in the trace
            self._play_cue(Cue.ERROR)
            return
        if not report.ok:
            self._play_cue(Cue.CLIPBOARD)

    def _copy_job(self) -> None:
        found = self._fresh_row()
        if found is None:
            return
        _row, text = found
        try:
            self._clipboard_write(text)
        except Exception:
            log.exception("chord copy failed")
            self._play_cue(Cue.ERROR)
            return
        self._play_cue(Cue.CLIPBOARD)

    def _confirmed(self, row_id: int) -> bool:
        pending = self._pending_confirm
        return (
            pending is not None
            and pending[0] == row_id
            and self._now_ms() <= pending[1]
        )
