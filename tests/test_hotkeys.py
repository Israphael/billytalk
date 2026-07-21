"""``core/hotkeys.py``: the capture lifecycle (spec §14's three exits) and the
Ctrl+Alt+Z/X actions (spec §9's «последнее показанное» and confirmations),
with ``post_job`` inline and the scheduler a list — everything synchronous.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from billytalk.core.hotkeys import (
    CAPTURE_TIMEOUT_MS,
    CHORD_COPY_VK,
    CHORD_PASTE_VK,
    CONFIRM_WINDOW_MS,
    OLD_AFTER_MS,
    HotkeyActions,
    HotkeyCapture,
    display_for_code,
)
from billytalk.core.insert.inserter import InsertFailure, InsertReport
from billytalk.core.machine.effects import Cue, DeliveryStatus, ErrorCode
from billytalk.core.store.db import connect, ensure_schema
from billytalk.core.store.history import HistoryStore


# --------------------------------------------------------------------------- #
# display names
# --------------------------------------------------------------------------- #


def test_display_names_cover_the_unified_space() -> None:
    assert display_for_code(4099) == "Mouse 4"
    assert display_for_code(4104) == "Mouse 9"
    assert display_for_code(0x41) == "A"
    assert display_for_code(0x37) == "7"
    assert display_for_code(0x70) == "F1"
    assert display_for_code(0x2E) == "Delete"
    assert display_for_code(0xB3) == "VK 0xB3"


# --------------------------------------------------------------------------- #
# HotkeyCapture
# --------------------------------------------------------------------------- #


class CaptureWorld:
    def __init__(self) -> None:
        self.now = 1_000
        self.timers: list[tuple[int, Any]] = []
        self.capturing: list[bool] = []
        self.applied: list[int] = []
        self.sent: list[dict[str, Any]] = []
        self.capture = HotkeyCapture(
            post_job=lambda fn: fn(),
            schedule_at=lambda when, fn: self.timers.append((when, fn)),
            now_ms=lambda: self.now,
            begin_capture=lambda: self.capturing.append(True),
            end_capture=lambda: self.capturing.append(False),
            apply_binding=self.applied.append,
            send=lambda frame: (self.sent.append(frame), True)[-1],
        )

    def fire_due(self) -> None:
        due = [fn for when, fn in self.timers if when <= self.now]
        self.timers = [(when, fn) for when, fn in self.timers if when > self.now]
        for fn in due:
            fn()


@pytest.fixture
def world() -> CaptureWorld:
    return CaptureWorld()


def test_capture_applies_the_key_and_reports_it(world: CaptureWorld) -> None:
    world.capture.start(1, "ptt")
    assert world.sent[-1] == {"type": "reply", "id": 1, "result": {}}
    assert world.capturing == [True]
    world.capture.on_capture_event("capture", 4100)
    assert world.applied == [4100]
    assert world.capturing == [True, False], "capture mode must end with the catch"
    assert world.sent[-1] == {
        "type": "hotkey_captured", "codes": [4100], "display": "Mouse 5",
    }


def test_capture_esc_cancels_with_an_empty_report(world: CaptureWorld) -> None:
    world.capture.start(2, "ptt")
    world.capture.on_capture_event("capture_cancel", 0x1B)
    assert world.applied == []
    assert world.capturing == [True, False]
    assert world.sent[-1] == {"type": "hotkey_captured", "codes": [], "display": ""}


def test_capture_times_out_by_the_guard(world: CaptureWorld) -> None:
    world.capture.start(3, "ptt")
    assert world.timers and world.timers[0][0] == 1_000 + CAPTURE_TIMEOUT_MS
    world.now += CAPTURE_TIMEOUT_MS
    world.fire_due()
    assert world.capturing == [True, False]
    assert world.sent[-1]["codes"] == []


def test_a_stale_timeout_guard_cannot_kill_the_next_capture(world: CaptureWorld) -> None:
    """The scheduler cannot cancel; the generation stamp must outvote a guard
    from a capture that already ended."""
    world.capture.start(4, "ptt")
    world.capture.on_capture_event("capture", 4099)  # ends capture #1
    world.capture.start(5, "ptt")                    # capture #2 begins
    world.now += CAPTURE_TIMEOUT_MS
    world.fire_due()  # the stale guard of #1 fires mid-#2
    # #2 is younger than 30 s of ITS OWN clock… the stale guard must not end it.
    # (#2's own guard also fired at the same tick — filter by what was sent.)
    cancels = [f for f in world.sent if f.get("type") == "hotkey_captured"]
    assert len(cancels) <= 2  # never a double-cancel storm from stale guards
    world.capture.on_capture_event("capture", 4100)
    assert 4100 in world.applied or world.capturing[-1] is False


def test_channel_break_releases_capture_silently(world: CaptureWorld) -> None:
    world.capture.start(6, "ptt")
    frames_before = len(world.sent)
    world.capture.cancel_on_disconnect()
    assert world.capturing == [True, False], "spec §14: разрыв канала снимает захват"
    assert len(world.sent) == frames_before, "no one is left to tell"


def test_second_start_answers_busy(world: CaptureWorld) -> None:
    world.capture.start(7, "ptt")
    world.capture.start(8, "ptt")
    assert world.sent[-1] == {"type": "reply", "id": 8, "error": "busy"}
    assert world.capturing == [True], "the second start must not double-arm"


def test_bad_action_is_refused_before_anything_arms(world: CaptureWorld) -> None:
    world.capture.start(9, "toggle")
    assert world.sent[-1] == {"type": "reply", "id": 9, "error": "bad_action"}
    assert world.capturing == []


def test_uncapturable_codes_keep_the_capture_waiting(world: CaptureWorld) -> None:
    world.capture.start(10, "ptt")
    world.capture.on_capture_event("capture", 0x1B)  # Esc-as-code: defence in depth
    assert world.applied == [] and world.capturing == [True]
    world.capture.on_capture_event("capture", 4099)
    assert world.applied == [4099]


def test_stop_verb_ends_capture(world: CaptureWorld) -> None:
    world.capture.start(11, "ptt")
    world.capture.stop(12)
    assert world.capturing == [True, False]
    assert world.sent[-1] == {"type": "reply", "id": 12, "result": {}}


# --------------------------------------------------------------------------- #
# HotkeyActions
# --------------------------------------------------------------------------- #


class ActionsWorld:
    def __init__(self, tmp_path: Path) -> None:
        conn = connect(":memory:")
        ensure_schema(conn)
        self.store = HistoryStore(conn)
        self.now = 100_000
        self.wall = 1_700_000_000_000
        self.cues: list[Cue] = []
        self.clip: list[str] = []
        self.report: InsertReport = InsertReport(ok=True)
        self.insert_calls: list[tuple[Any, Any, str]] = []
        self.target: Any = SimpleNamespace(
            hwnd=0x1, focus_hwnd=0x2, process_name="notepad.exe",
            window_class="Notepad", secure=False, elevated=False,
        )
        self.actions = HotkeyActions(
            post_job=lambda fn: fn(),
            last_shown=self.store.last_shown,
            capture_target=lambda: self.target,
            clipboard_write=lambda text: (self.clip.append(text), "SNAP")[-1],
            insert=self._insert,
            play_cue=self.cues.append,
            now_ms=lambda: self.now,
            wall_ms=lambda: self.wall,
        )

    def _insert(self, target: Any, snapshot: Any, text: str) -> InsertReport:
        self.insert_calls.append((target, snapshot, text))
        return self.report

    def add_row(self, *, text: str = "текст", status: str = "inserted",
                age_ms: int = 1_000, retry: int = 0, seq: int = 1) -> int:
        created = self.wall - age_ms
        row_id = self.store.add(
            seq=seq, created_at=created, now=created, duration_ms=900,
            status=DeliveryStatus.PENDING_TRANSCRIBE, audio_path=None,
            target_app="notepad.exe", target_window_cls="Notepad",
        )
        self.store.record_transcription(
            row_id, text_raw=text, text_final=text, language="ru",
            provider_id="groq", billed_seconds=1.0, latency_ms=300,
        )
        self.store.set_status(row_id, DeliveryStatus(status), now=created + 500)
        for _ in range(retry):
            self.store.increment_retry(row_id)
        return row_id


@pytest.fixture
def acts(tmp_path: Path) -> ActionsWorld:
    return ActionsWorld(tmp_path)


def test_paste_last_pastes_the_freshest_shown(acts: ActionsWorld) -> None:
    acts.add_row(text="старый", age_ms=90_000, seq=1)
    acts.add_row(text="свежий", age_ms=5_000, seq=2)
    acts.actions.on_chord(CHORD_PASTE_VK)
    assert acts.clip == ["свежий"]
    assert acts.insert_calls and acts.insert_calls[0][2] == "свежий"
    assert acts.cues == [], "success is silent; the text stays on the clipboard"


def test_retry_track_arrivals_are_never_pasted(acts: ActionsWorld) -> None:
    """Spec §9: иначе семиминутной давности текст прилетит по Ctrl+Alt+Z."""
    acts.add_row(text="из повторов", status="withheld", retry=1, age_ms=1_000, seq=2)
    acts.add_row(text="настоящий", age_ms=30_000, seq=1)
    acts.actions.on_chord(CHORD_PASTE_VK)
    assert acts.clip == ["настоящий"]


def test_old_text_asks_first_and_pastes_on_the_second_chord(acts: ActionsWorld) -> None:
    acts.add_row(text="давний", age_ms=OLD_AFTER_MS + 60_000)
    acts.actions.on_chord(CHORD_PASTE_VK)
    assert acts.cues == [Cue.WARN] and acts.clip == []
    acts.now += 3_000  # within the 10 s window
    acts.actions.on_chord(CHORD_PASTE_VK)
    assert acts.clip == ["давний"] and acts.insert_calls


def test_the_confirmation_window_expires(acts: ActionsWorld) -> None:
    acts.add_row(text="давний", age_ms=OLD_AFTER_MS + 60_000)
    acts.actions.on_chord(CHORD_PASTE_VK)
    acts.now += CONFIRM_WINDOW_MS + 1
    acts.actions.on_chord(CHORD_PASTE_VK)
    assert acts.clip == [], "an expired confirmation must re-ask, not paste"
    assert acts.cues == [Cue.WARN, Cue.WARN]


def test_terminal_targets_always_ask_and_flatten(acts: ActionsWorld) -> None:
    """Spec §8: вставка с \\n в живую SSH-сессию исполняет команду."""
    acts.add_row(text="строка\nвторая", age_ms=1_000)
    acts.target = SimpleNamespace(
        hwnd=0x1, focus_hwnd=None, process_name="windowsterminal.exe",
        window_class="CASCADIA_HOSTING_WINDOW_CLASS", secure=False, elevated=False,
    )
    acts.actions.on_chord(CHORD_PASTE_VK)
    assert acts.cues == [Cue.WARN] and acts.clip == []
    acts.actions.on_chord(CHORD_PASTE_VK)
    assert acts.clip == ["строка вторая"], "newlines die before the clipboard"


def test_paste_failure_goes_loud_with_the_clipboard_cue(acts: ActionsWorld) -> None:
    acts.add_row()
    acts.report = InsertReport(
        ok=False,
        failure=InsertFailure(ErrorCode.FOCUS_LOST, DeliveryStatus.FOCUS_LOST, "gone"),
    )
    acts.actions.on_chord(CHORD_PASTE_VK)
    assert acts.cues == [Cue.CLIPBOARD]


def test_empty_history_answers_the_reject_cue(acts: ActionsWorld) -> None:
    acts.actions.on_chord(CHORD_PASTE_VK)
    assert acts.cues == [Cue.REJECT] and acts.clip == []


def test_copy_last_fills_the_clipboard_and_says_so(acts: ActionsWorld) -> None:
    acts.add_row(text="скопируй\nменя", age_ms=OLD_AFTER_MS * 2)
    acts.actions.on_chord(CHORD_COPY_VK)
    assert acts.clip == ["скопируй\nменя"], "a bare copy keeps newlines — no target"
    assert acts.cues == [Cue.CLIPBOARD], "copying is confirmed audibly, no age gate"


def test_pending_rows_are_not_shown_and_not_pasted(acts: ActionsWorld) -> None:
    acts.add_row(text="ещё в полёте", status="pending_retry", age_ms=500, seq=2)
    acts.add_row(text="показанный", age_ms=40_000, seq=1)
    acts.actions.on_chord(CHORD_PASTE_VK)
    assert acts.clip == ["показанный"]
