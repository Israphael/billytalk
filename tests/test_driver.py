"""The driver (harness §9, item «Драйвер»): the machine's effects against a
fully faked world, with the real ``HistoryStore`` underneath — the history
rows are the product's promise, so these tests assert against actual SQLite.

Nothing sleeps: the clock is a ``FakeClock``, timers fire via ``pump_timers``,
transcription workers are a list the test drains by hand.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from billytalk.core.insert.clipboard import ClipboardSnapshot
from billytalk.core.insert.inserter import InsertFailure, InsertReport
from billytalk.core.machine.driver import (
    FAILED_TIMEOUT_MS,
    Driver,
    DriverDeps,
)
from billytalk.core.machine.effects import Cue, DeliveryStatus, ErrorCode
from billytalk.core.machine.events import (
    CaptureStarted,
    DoubleEsc,
    PressPTT,
    ReleasePTT,
    Suspend,
    TranscribeOk,
)
from billytalk.core.machine.states import Phase
from billytalk.core.store.db import connect, ensure_schema
from billytalk.core.store.history import CleanupGate, HistoryStore
from billytalk.core.stt.errors import KeyInvalid, NetworkDown
from billytalk.core.text.dictionary import DEFAULT_RULES, Dictionary
from tests.fakes import FakeClock, FakeProvider


class Samples(list):
    """List with numpy's ``size`` so the driver's emptiness check works."""

    @property
    def size(self) -> int:
        return len(self)


class FakeCapture:
    def __init__(self, world: World, **callbacks: Any) -> None:
        self.world = world
        self.on_started = callbacks.get("on_started")
        self.stopped = False
        self.cancelled = False

    def start(self) -> None:
        self.world.sessions.append(self)

    def stop(self, *, tail_ms: int = 200) -> Samples:
        self.stopped = True
        return Samples([0] * self.world.captured_samples)

    def cancel(self) -> None:
        self.cancelled = True


class StubClipboard:
    def __init__(self) -> None:
        self.writes: list[str] = []
        self.restores: list[ClipboardSnapshot] = []
        self._seq = 10

    def write(self, text: str) -> ClipboardSnapshot:
        self.writes.append(text)
        self._seq += 1
        return ClipboardSnapshot(text=None, seq_after_write=self._seq, had_text=False)

    def restore(self, snapshot: ClipboardSnapshot) -> bool:
        self.restores.append(snapshot)
        return True


class StubInserter:
    def __init__(self) -> None:
        self.reports: list[InsertReport] = []
        self.calls: list[Any] = []

    def insert(
        self, target: Any, snapshot: ClipboardSnapshot, text: str | None = None
    ) -> InsertReport:
        self.calls.append((target, snapshot, text))
        if self.reports:
            return self.reports.pop(0)
        return InsertReport(ok=True)


@dataclass
class World:
    """One assembled driver with every seam observable."""

    driver: Driver = None  # type: ignore[assignment]
    store: HistoryStore = None  # type: ignore[assignment]
    gate: CleanupGate = None  # type: ignore[assignment]
    provider: FakeProvider = None  # type: ignore[assignment]
    clipboard: StubClipboard = None  # type: ignore[assignment]
    inserter: StubInserter = None  # type: ignore[assignment]
    clock: FakeClock = None  # type: ignore[assignment]
    jobs: list = field(default_factory=list)
    cues: list = field(default_factory=list)
    notices: list = field(default_factory=list)
    snapshots: list = field(default_factory=list)
    sessions: list = field(default_factory=list)
    reinstalls: int = 0
    captured_samples: int = 8000  # half a second at 16 kHz
    trim_empty: bool = False

    def run_jobs(self) -> None:
        while self.jobs:
            self.jobs.pop(0)()
        self.driver.drain()

    def advance(self, ms: int) -> None:
        self.clock.advance(ms)
        self.driver.pump_timers()
        self.driver.drain()

    def row(self, row_id: int = 1) -> Any:
        row = self.store._conn.execute(
            "SELECT * FROM history WHERE id = ?", (row_id,)
        ).fetchone()
        assert row is not None, f"history row {row_id} was never written"
        return row


def build_world(tmp_path: Path, *, outcomes: list | None = None) -> World:
    world = World()
    conn = connect(":memory:")
    ensure_schema(conn)
    world.store = HistoryStore(conn)
    world.gate = CleanupGate()
    world.provider = FakeProvider(outcomes)
    world.clipboard = StubClipboard()
    world.inserter = StubInserter()
    world.clock = FakeClock(1_000)

    def trim(samples: Any) -> Any:
        return SimpleNamespace(samples=samples, is_empty=world.trim_empty)

    def encode(samples: Any, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fLaC-fake")
        return path

    deps = DriverDeps(
        store=world.store,
        gate=world.gate,
        provider=world.provider,
        dictionary=Dictionary(DEFAULT_RULES),
        clipboard=world.clipboard,
        inserter=world.inserter,
        capture_factory=lambda **kw: FakeCapture(world, **kw),
        capture_target=lambda: SimpleNamespace(
            hwnd=0x111, process_name="notepad.exe", window_class="Notepad"
        ),
        play_cue=world.cues.append,
        notify=world.notices.append,
        trim=trim,
        encode=encode,
        audio_dir=tmp_path / "audio",
        set_hook_snapshot=world.snapshots.append,
        request_hook_reinstall=lambda: setattr(world, "reinstalls", world.reinstalls + 1),
        submit_transcription=world.jobs.append,
        max_hold_ms=5 * 60 * 1000,
        now_ms=lambda: world.clock.now,
        wall_ms=lambda: 1_700_000_000_000 + world.clock.now,
    )
    world.driver = Driver(deps)
    return world


def dictate(world: World, *, hold_ms: int = 600) -> None:
    """Press, first frame, hold, release — machine lands in Finalizing."""
    world.driver.dispatch(PressPTT())
    world.driver.drain()
    world.driver.dispatch(CaptureStarted())
    world.clock.advance(hold_ms)
    world.driver.dispatch(ReleasePTT())
    world.driver.drain()


# --------------------------------------------------------------------------- #
# the happy path
# --------------------------------------------------------------------------- #


def test_happy_path_from_press_to_inserted_row(tmp_path: Path) -> None:
    world = build_world(tmp_path, outcomes=[FakeProvider.ok("выкати на прот")])
    dictate(world)

    row = world.row()
    assert row["delivery_status"] == "pending_transcribe", "durable before the network"
    assert row["audio_path"], "the FLAC is on disk before any request"
    assert row["target_app"] == "notepad.exe"

    world.run_jobs()  # the transcription worker

    row = world.row()
    assert row["delivery_status"] == "inserted"
    assert row["text_raw"] == "выкати на прот"
    assert row["text_final"] == "выкати на прод", "the dictionary pass applied"
    assert row["audio_release_at"] is not None, "delivered: the one-hour clock started"
    assert world.clipboard.writes == ["выкати на прод"]
    assert len(world.inserter.calls) == 1
    assert world.driver.state.phase is Phase.Idle
    assert Cue.START in world.cues and Cue.STOP in world.cues


def test_clipboard_restore_is_scheduled_not_immediate(tmp_path: Path) -> None:
    """An immediate restore races the target's WM_PASTE and pastes the restored
    content instead of ours."""
    world = build_world(tmp_path, outcomes=[FakeProvider.ok("текст")])
    dictate(world)
    world.run_jobs()

    assert world.clipboard.restores == [], "not yet"
    world.advance(300)
    assert len(world.clipboard.restores) == 1, "and now, after the delay"


# --------------------------------------------------------------------------- #
# silence and brevity
# --------------------------------------------------------------------------- #


def test_empty_clip_spends_no_request_and_is_recorded_silently(tmp_path: Path) -> None:
    world = build_world(tmp_path)
    world.trim_empty = True
    dictate(world)
    world.run_jobs()

    row = world.row()
    assert row["delivery_status"] == "empty"
    assert world.provider.calls == [], "spec §4: an empty clip is never sent"
    assert Cue.STOP in world.cues, "the stop cue played before emptiness was known"
    assert world.notices == [], "recorded, but silent"


def test_short_press_recorded_without_transcription(tmp_path: Path) -> None:
    world = build_world(tmp_path)
    dictate(world, hold_ms=100)  # under the 250 ms floor
    world.run_jobs()

    row = world.row()
    assert row["delivery_status"] == "too_short"
    assert world.provider.calls == []
    assert Cue.STOP not in world.cues, "too short: the cue itself stays silent"


# --------------------------------------------------------------------------- #
# the fuse and the tray
# --------------------------------------------------------------------------- #


def test_max_hold_fuse_delivers_to_history_not_paste(tmp_path: Path) -> None:
    world = build_world(tmp_path, outcomes=[FakeProvider.ok("длинная речь")])
    world.driver.dispatch(PressPTT())
    world.driver.drain()
    world.driver.dispatch(CaptureStarted())

    world.advance(5 * 60 * 1000 + 1)  # the fuse fires via the scheduler
    assert world.driver.state.phase is Phase.Finalizing

    world.run_jobs()
    row = world.row()
    assert row["delivery_status"] == "withheld", "transcribed, deliberately not pasted"
    assert world.clipboard.writes == ["длинная речь"], "the clipboard still carries it"
    assert world.inserter.calls == [], "the fuse forbids the paste, not the text"
    assert Cue.CLIPBOARD in world.cues


# --------------------------------------------------------------------------- #
# cancellation
# --------------------------------------------------------------------------- #


def test_cancel_in_finalizing_drops_the_late_result(tmp_path: Path) -> None:
    """OPEN-QUESTIONS §5/§19: no CancelTranscribe verb — the row's status is
    the mark, and the late worker result must be dropped at the door."""
    world = build_world(tmp_path, outcomes=[FakeProvider.ok("отменённые слова")])
    dictate(world)

    world.driver.dispatch(DoubleEsc())
    world.driver.drain()
    assert world.row()["delivery_status"] == "cancelled"

    world.run_jobs()  # the in-flight transcription lands AFTER the cancel

    assert world.row()["delivery_status"] == "cancelled", "a late result changes nothing"
    assert world.row()["text_final"] is None
    assert world.clipboard.writes == []
    assert world.driver.state.phase is Phase.Idle


def test_stale_seq_event_is_dropped_before_the_machine(tmp_path: Path) -> None:
    world = build_world(tmp_path)
    world.driver.dispatch(TranscribeOk(999))  # no active dictation at all
    assert world.driver.state.phase is Phase.Idle, "dropped, not applied"


# --------------------------------------------------------------------------- #
# network failure and the retry track
# --------------------------------------------------------------------------- #


def test_network_error_enqueues_retry_and_retry_arrival_is_withheld(tmp_path: Path) -> None:
    world = build_world(
        tmp_path, outcomes=[NetworkDown("timeout"), FakeProvider.ok("дошло со второго раза")]
    )
    dictate(world)
    world.run_jobs()  # first attempt: NetworkDown -> NetworkError event

    row = world.row()
    assert row["delivery_status"] == "pending_retry"
    assert world.gate.offline, "cleanup is paused the moment the network fails"
    assert world.driver.state.phase is Phase.Idle, "the ordering slot is released"

    world.advance(1_000)  # first rung of the ladder
    world.run_jobs()

    row = world.row()
    assert row["delivery_status"] == "withheld", "retry arrivals never auto-insert (spec §6)"
    assert row["text_final"] == "дошло со второго раза"
    assert row["retry_count"] == 1, "the Ctrl+Alt+Z exclusion mark (spec §9)"
    assert world.clipboard.writes == [], "and never touch the clipboard minutes later"
    assert not world.gate.offline


def test_invalid_key_stops_retrying_and_notifies(tmp_path: Path) -> None:
    world = build_world(tmp_path, outcomes=[NetworkDown("timeout"), KeyInvalid(401)])
    dictate(world)
    world.run_jobs()

    world.advance(1_000)
    world.run_jobs()  # retry hits the 401

    row = world.row()
    assert row["delivery_status"] == "transcribe_failed"
    assert row["error_code"] == "key_invalid"
    assert ErrorCode.KEY_INVALID in world.notices
    world.advance(60_000)
    world.run_jobs()
    assert len(world.provider.calls) == 2, "never retried again"


def test_startup_with_pending_rows_begins_offline(tmp_path: Path) -> None:
    """The gate cannot remember an outage across a restart; a non-empty queue
    is its evidence. Cleanup stays paused until the first successful retry
    reopens it through the usual ten-minute delay (review round 1)."""
    world = build_world(tmp_path, outcomes=[FakeProvider.ok("вернулась связь")])
    clip = tmp_path / "audio" / "waiting.flac"
    clip.parent.mkdir(parents=True, exist_ok=True)
    clip.write_bytes(b"fLaC-w")
    world.store.add(
        seq=1, created_at=500, now=600, duration_ms=900,
        status=DeliveryStatus.PENDING_RETRY, audio_path=str(clip),
    )

    assert world.driver.enqueue_startup_pending() == 1
    assert world.gate.offline, "unfinished rows: assume the outage persists"

    world.advance(0)
    world.run_jobs()
    assert not world.gate.offline, "the successful retry is the proof of connectivity"


def test_startup_pending_rows_go_back_to_the_queue(tmp_path: Path) -> None:
    world = build_world(tmp_path, outcomes=[FakeProvider.ok("из прошлой жизни")])
    clip = tmp_path / "audio" / "old.flac"
    clip.parent.mkdir(parents=True, exist_ok=True)
    clip.write_bytes(b"fLaC-old")
    world.store.add(
        seq=1, created_at=500, now=600, duration_ms=900,
        status=DeliveryStatus.PENDING_TRANSCRIBE, audio_path=str(clip),
    )

    assert world.driver.enqueue_startup_pending() == 1
    world.advance(0)
    world.run_jobs()

    row = world.row()
    assert row["delivery_status"] == "withheld"
    assert row["text_final"] == "из прошлой жизни"


# --------------------------------------------------------------------------- #
# insert failures carry their precise status (OPEN-QUESTIONS §17)
# --------------------------------------------------------------------------- #


def test_focus_lost_writes_focus_lost_not_left_on_clipboard(tmp_path: Path) -> None:
    world = build_world(tmp_path, outcomes=[FakeProvider.ok("текст")])
    world.inserter.reports.append(
        InsertReport(ok=False, failure=InsertFailure(
            ErrorCode.FOCUS_LOST, DeliveryStatus.FOCUS_LOST, "user clicked away"
        ))
    )
    dictate(world)
    world.run_jobs()

    row = world.row()
    assert row["delivery_status"] == "focus_lost"
    assert row["error_code"] == "focus_lost"
    assert Cue.CLIPBOARD in world.cues, "the loud unmissable cue (spec §5)"
    assert ErrorCode.FOCUS_LOST in world.notices


def test_secure_field_writes_blocked_secure(tmp_path: Path) -> None:
    world = build_world(tmp_path, outcomes=[FakeProvider.ok("пароль не сюда")])
    world.inserter.reports.append(
        InsertReport(ok=False, failure=InsertFailure(
            ErrorCode.SECURE_FIELD, DeliveryStatus.BLOCKED_SECURE, "password field"
        ))
    )
    dictate(world)
    world.run_jobs()

    assert world.row()["delivery_status"] == "blocked_secure"


# --------------------------------------------------------------------------- #
# suppression, suspend, failure timeout
# --------------------------------------------------------------------------- #


def test_hook_snapshot_tracks_recording_and_suppression(tmp_path: Path) -> None:
    world = build_world(tmp_path)
    world.driver.dispatch(PressPTT())
    assert world.snapshots[-1].recording, "Initialized counts as recording for Esc"

    world.driver.dispatch(CaptureStarted())
    world.clock.advance(600)
    world.driver.dispatch(ReleasePTT())
    world.driver.drain()
    assert world.snapshots[-1].recording, "Finalizing still owns the double Esc"


def test_suspend_finalizes_and_releases_the_button(tmp_path: Path) -> None:
    world = build_world(tmp_path)
    world.driver.dispatch(PressPTT())
    world.driver.drain()
    world.driver.dispatch(CaptureStarted())
    world.clock.advance(600)
    world.driver.dispatch(Suspend())
    world.driver.drain()

    row = world.row()
    assert row["delivery_status"] == "pending_transcribe", "the words are on disk"
    assert world.snapshots[-1].suppress is False, "Mouse 4 is Back again during sleep"


def test_mic_error_enters_failed_and_times_out_back_to_idle(tmp_path: Path) -> None:
    world = build_world(tmp_path)

    class RefusingCapture(FakeCapture):
        def start(self) -> None:
            raise RuntimeError("device gone")

    world.driver.deps.capture_factory = lambda **kw: RefusingCapture(world, **kw)
    world.driver.dispatch(PressPTT())
    world.driver.drain()

    assert world.driver.state.phase is Phase.Failed
    assert world.snapshots[-1].suppress is False, "a failed machine frees the button"

    world.advance(FAILED_TIMEOUT_MS + 1)
    assert world.driver.state.phase is Phase.Idle
    assert world.snapshots[-1].suppress is True, "and takes it back on recovery"


# --------------------------------------------------------------------------- #
# housekeeping respects the gate
# --------------------------------------------------------------------------- #


def test_housekeeping_skips_cleanup_while_offline(tmp_path: Path) -> None:
    world = build_world(tmp_path)
    clip = tmp_path / "delivered.flac"
    clip.write_bytes(b"fLaC")
    rid = world.store.add(
        seq=9, created_at=100, now=100, duration_ms=500,
        status=DeliveryStatus.PENDING_TRANSCRIBE, audio_path=str(clip),
    )
    world.store.set_status(rid, DeliveryStatus.INSERTED, now=100)

    world.gate.network_lost()
    world.advance(61_000)  # housekeeping tick fires
    assert clip.exists(), "offline: cleanup must not run at all (spec §3)"

    world.gate.transcribe_succeeded(world.clock.now)
    world.advance(11 * 60 * 1000)
    assert not clip.exists(), "ten minutes after the first success, cleanup resumed"


# --------------------------------------------------------------------------- #
# verification statuses ride the report into the row (spec §8, cycle-2 M1)
# --------------------------------------------------------------------------- #


def test_verify_impossible_lands_in_the_row_and_stays_silent(tmp_path: Path) -> None:
    """The machine's InsertOk cell says INSERTED; the report's word is final
    (the ok twin of OPEN-QUESTIONS §17). And silence means silence: no cue
    beyond the ordinary stop, no notification — the customer's decision."""
    world = build_world(tmp_path, outcomes=[FakeProvider.ok("текст")])
    world.inserter.reports.append(
        InsertReport(ok=True, status=DeliveryStatus.VERIFY_IMPOSSIBLE)
    )
    dictate(world)
    world.run_jobs()

    row = world.row()
    assert row["delivery_status"] == "verify_impossible"
    assert row["audio_release_at"] is not None, (
        "delivered to the user via the clipboard: the one-hour shelf clock runs"
    )
    assert world.notices == []
    assert Cue.CLIPBOARD not in world.cues and Cue.ERROR not in world.cues
    assert world.driver.state.phase is Phase.Idle


def test_the_inserter_receives_the_prepared_text_as_the_needle(tmp_path: Path) -> None:
    """What verification searches for must be exactly what the clipboard got —
    the post-prepare_text text, newline flattening included."""
    world = build_world(tmp_path, outcomes=[FakeProvider.ok("текст")])
    dictate(world)
    world.run_jobs()

    _target_, _snapshot, needle = world.inserter.calls[0]
    assert needle == world.clipboard.writes[0]
