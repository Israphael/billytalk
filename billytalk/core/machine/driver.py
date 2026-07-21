"""The driver: performs the machine's effects against the real world.

The machine is a pure function; this is everything it refused to be. The
division of labour is strict — the machine decides, the driver performs — and
every place where the driver must *interpret* a decision is recorded in
OPEN-QUESTIONS (§5 cancellation, §16 clipboard replacement, §17 insert
statuses, §18 retry-track arrivals, §19 stale sequence guard).

Design for testability: the driver core is synchronous. Events go in through
:meth:`Driver.dispatch`; effects are performed through injected collaborators;
timers are data (:class:`Scheduler`) that a test fires by hand and production
drives from the event loop in ``run()``. Nothing in this file sleeps.

Threading in production is two lanes:

* the **driver thread** runs ``run()``: pops events, fires due timers, steps
  the machine, performs effects — including every store write (one SQLite
  connection, one thread) and the audio work. ``StopCapture`` blocks ~200 ms
  for the tail (spec §5); delivery is strictly ordered anyway, and whatever
  the user pressed meanwhile is already queued by the machine.
* **transcription workers** (a small pool) carry the only genuinely slow I/O —
  the network — and report back as events via :meth:`post`. The retry ladder
  is scheduled through the same Scheduler, never slept inside a provider.

Two effects need interpretation at execution time:

* ``Transcribe`` means "transcribe if there is anything to": emptiness is
  knowable only after trimming, so the driver answers ``ClipEmpty`` instead of
  spending a request (spec §4's "no Transcribe for empty", honoured where the
  knowledge exists).
* There is no ``CancelTranscribe`` verb arriving from the machine
  (OPEN-QUESTIONS §5): a ``WriteHistory(seq, cancelled)`` marks the dictation,
  and a transcription result for a cancelled or superseded seq is dropped at
  the door (§19) — the words are already on disk, nothing is lost.
"""

from __future__ import annotations

import heapq
import logging
import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Protocol
from uuid import uuid4

from ..hooks.edges import HookSnapshot
from ..insert.apprules import rule_for
from ..insert.inserter import RESTORE_DELAY_MS, InsertFailure, prepare_text
from ..logging_setup import Transcript
from ..stt.base import AudioClip, TranscriptionOptions, TranscriptionResult, build_prompt
from ..stt.errors import RetryAdvice, TranscriptionError
from .effects import (
    CancelCapture,
    Cue,
    DeliveryStatus,
    Effect,
    EnqueueRetry,
    ErrorCode,
    Insert,
    Notify,
    PersistAudio,
    PlayCue,
    ReinstallHook,
    SetSuppression,
    Shutdown,
    StartCapture,
    StopCapture,
    Transcribe,
    WriteClipboard,
    WriteHistory,
)
from .events import (
    CaptureStarted,
    ClipEmpty,
    DoubleEsc,
    EscPressed,
    Event,
    FailedTimeout,
    HistoryWriteFailed,
    InsertFailed,
    InsertOk,
    MaxHoldReached,
    MicError,
    NetworkError,
    PressPTT,
    ReleasePTT,
    TranscribeOk,
)
from .states import Phase, State, initial_state, step

__all__ = ["Driver", "DriverDeps", "Scheduler", "FAILED_TIMEOUT_MS", "RETRY_LADDER_S"]

log = logging.getLogger("billytalk.driver")

FAILED_TIMEOUT_MS: Final = 3_000
"""How long ``Failed`` stays before the machine returns to Idle. The spec says
"transient" without a number; three seconds is long enough to hear the cue,
short enough that the button is back before the user retries."""

RETRY_LADDER_S: Final = (1.0, 2.0, 4.0, 8.0, 8.0)
"""Spec §6: 1→2→4→8 s, five attempts. The fifth keeps the last rung."""

HOUSEKEEPING_INTERVAL_MS: Final = 60_000

SAMPLE_RATE: Final = 16_000


class CaptureLike(Protocol):
    def start(self) -> None: ...
    def stop(self, *, tail_ms: int = ...) -> Any: ...
    def cancel(self) -> None: ...


@dataclass
class DriverDeps:
    """Every touchpoint, injectable. Production wiring lives in ``__main__``."""

    store: Any  # HistoryStore
    gate: Any  # CleanupGate
    provider: Any  # TranscriptionProvider
    dictionary: Any  # Dictionary
    clipboard: Any  # Clipboard
    inserter: Any  # Inserter
    capture_factory: Callable[..., CaptureLike]
    capture_target: Callable[[], Any]  # focus.capture_target
    play_cue: Callable[[Cue], None]
    notify: Callable[[ErrorCode], None]
    trim: Callable[..., Any]  # audio.trim_silence
    encode: Callable[..., Any]  # audio.encode_flac
    audio_dir: Path
    set_hook_snapshot: Callable[[HookSnapshot], None]
    request_hook_reinstall: Callable[[], None]
    submit_transcription: Callable[[Callable[[], None]], None]
    """Runs a transcription job off the driver thread. Tests pass an inline
    runner; production passes a small thread pool's submit."""
    language: str = "ru"
    max_hold_ms: int = 5 * 60 * 1000
    retention_minutes: int = 60
    audio_cap_rows: int = 500
    audio_cap_bytes: int = 2 * 1024**3
    bound_codes: frozenset[int] = frozenset({4099})
    publish_state: Callable[[State], None] | None = None
    """Observer for cycle 2's display surfaces (tray icon, IPC
    ``state_changed``): called on the driver thread after every step with the
    new state. Display only — must be cheap and must never raise."""
    now_ms: Callable[[], int] = lambda: int(time.monotonic() * 1000)
    wall_ms: Callable[[], int] = lambda: int(time.time() * 1000)


class Scheduler:
    """Timers as data, on the monotonic clock (spec §3).

    Entries self-validate at fire time instead of being cancelled: each guard
    closure re-checks reality, so a stale timer is a no-op rather than a race.
    A clock jump larger than the fuse reads as sleep, and the suspend path
    already finalised everything — the stale fuse then finds no Recording to
    close and does nothing, which is exactly the spec §3 semantics.
    """

    def __init__(self) -> None:
        self._heap: list[tuple[int, int, Callable[[], None]]] = []
        self._counter = 0

    def at(self, when_ms: int, fn: Callable[[], None]) -> None:
        self._counter += 1
        heapq.heappush(self._heap, (when_ms, self._counter, fn))

    def next_deadline(self) -> int | None:
        return self._heap[0][0] if self._heap else None

    def due(self, now_ms: int) -> list[Callable[[], None]]:
        fired: list[Callable[[], None]] = []
        while self._heap and self._heap[0][0] <= now_ms:
            _, _, fn = heapq.heappop(self._heap)
            fired.append(fn)
        return fired


@dataclass
class _DictationState:
    """Everything the driver holds per seq while a dictation is in flight."""

    seq: int
    pressed_wall_ms: int
    row_id: int | None = None
    target: Any = None
    session: CaptureLike | None = None
    samples: Any = None
    duration_ms: int = 0
    clip_path: Path | None = None
    is_empty: bool = False
    text: Transcript | None = None
    result: TranscriptionResult | None = None
    snapshot: Any = None  # ClipboardSnapshot
    pasted_text: str | None = None
    """What _write_clipboard actually wrote (post prepare_text) — the needle
    the insert verification searches the target document for (spec §8)."""
    cancelled: bool = False
    last_insert_failure: InsertFailure | None = None
    last_insert_status: DeliveryStatus | None = None
    """The ok-path twin of last_insert_failure (OPEN-QUESTIONS §17): INSERTED
    when the paste verified, VERIFY_IMPOSSIBLE when the signal was silent."""


@dataclass
class _RetryItem:
    row_id: int
    clip_path: Path | None
    duration_ms: int
    attempts: int = 0


_SEQ_EVENTS = (TranscribeOk, ClipEmpty, NetworkError, InsertOk, InsertFailed)


class Driver:
    """Owns the machine state and performs its effects. One instance; the
    production loop is one thread, tests call :meth:`dispatch` directly."""

    def __init__(self, deps: DriverDeps) -> None:
        self.deps = deps
        self.state: State = initial_state()
        self.scheduler = Scheduler()
        self.events: queue.Queue[Event] = queue.Queue()
        self._dictations: dict[int, _DictationState] = {}
        self._pending_target: Any = None
        self._suppress = True
        self._shutdown = threading.Event()
        self.scheduler.at(self.deps.now_ms() + HOUSEKEEPING_INTERVAL_MS, self._housekeeping)

    # ------------------------------------------------------------------ #
    # inbound
    # ------------------------------------------------------------------ #

    def post(self, event: Event) -> None:
        """Thread-safe entry: workers and hooks put, the driver thread pops."""
        self.events.put(event)

    def on_hook_event(self, hook_event: Any) -> None:
        """Adapter for ``HookThread``. Runs on the hook thread — translation
        only; the capture-target lookup waits for the driver thread."""
        kind = hook_event.kind
        if kind == "press":
            self.post(PressPTT())
        elif kind == "release":
            self.post(ReleasePTT())
        elif kind == "esc":
            self.post(EscPressed())
        elif kind == "double_esc":
            self.post(DoubleEsc())

    # ------------------------------------------------------------------ #
    # the loop (production) and the step (tests)
    # ------------------------------------------------------------------ #

    def run(self) -> None:
        while not self._shutdown.is_set():
            now = self.deps.now_ms()
            for fn in self.scheduler.due(now):
                fn()
            deadline = self.scheduler.next_deadline()
            timeout = 0.5 if deadline is None else min(0.5, max(0.0, (deadline - now) / 1000))
            try:
                event = self.events.get(timeout=timeout)
            except queue.Empty:
                continue
            self.dispatch(event)

    def drain(self) -> None:
        """Handle everything already queued. Tests use it after worker posts."""
        while True:
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                return
            self.dispatch(event)

    def pump_timers(self) -> None:
        """Fire everything due now. Tests advance a fake clock and call this."""
        for fn in self.scheduler.due(self.deps.now_ms()):
            fn()

    def dispatch(self, event: Event) -> None:
        """One machine step plus its consequences. Driver thread only."""
        if self._is_stale(event):
            return
        if isinstance(event, TranscribeOk):
            self.deps.gate.transcribe_succeeded(self.deps.now_ms())
        elif isinstance(event, NetworkError):
            self.deps.gate.network_lost()

        event = self._enrich(event)
        before = self.state.phase
        now = self.deps.now_ms()
        self.state, effects = step(self.state, event, now)
        self._after_step(event, before, now)
        for effect in effects:
            self._perform(effect, event)
        self._sync_hook_snapshot()
        if self.deps.publish_state is not None:
            self.deps.publish_state(self.state)

    def _is_stale(self, event: Event) -> bool:
        """§19: the machine trusts phase, not the event's seq — a late result
        for a cancelled or superseded dictation must be dropped here, or it
        would be applied to whoever holds the slot now."""
        if not isinstance(event, _SEQ_EVENTS):
            return False
        active = self.state.active
        if active is None or active.seq != event.seq:
            log.info("dropping stale %s for seq %d", type(event).__name__, event.seq)
            return True
        return False

    # ------------------------------------------------------------------ #
    # bookkeeping around the step
    # ------------------------------------------------------------------ #

    def _enrich(self, event: Event) -> Event:
        """Attach press-time data the pure machine cannot gather itself."""
        if isinstance(event, PressPTT) and event.target_hwnd is None:
            self._pending_target = self.deps.capture_target()
            if self._pending_target is not None:
                return PressPTT(target_hwnd=self._pending_target.hwnd)
        return event

    def _after_step(self, event: Event, phase_before: Phase, now: int) -> None:
        active = self.state.active
        if active is not None and active.seq not in self._dictations:
            dictation = _DictationState(seq=active.seq, pressed_wall_ms=self.deps.wall_ms())
            dictation.target = self._pending_target
            self._dictations[active.seq] = dictation

        if isinstance(event, CaptureStarted) and self.state.phase is Phase.Recording:
            seq = active.seq if active else 0
            self.scheduler.at(now + self.deps.max_hold_ms, self._fuse_guard(seq))

        if self.state.phase is Phase.Failed and phase_before is not Phase.Failed:
            self.scheduler.at(now + FAILED_TIMEOUT_MS, self._failed_guard())

    def _fuse_guard(self, seq: int) -> Callable[[], None]:
        def fire() -> None:
            active = self.state.active
            if self.state.phase is Phase.Recording and active is not None and active.seq == seq:
                self.dispatch(MaxHoldReached())
        return fire

    def _failed_guard(self) -> Callable[[], None]:
        def fire() -> None:
            if self.state.phase is Phase.Failed:
                self.dispatch(FailedTimeout())
        return fire

    def _sync_hook_snapshot(self) -> None:
        recording = self.state.phase in (Phase.Initialized, Phase.Recording, Phase.Finalizing)
        self.deps.set_hook_snapshot(
            HookSnapshot(
                bound=self.deps.bound_codes,
                suppress=self._suppress and self.state.enabled,
                recording=recording,
            )
        )

    # ------------------------------------------------------------------ #
    # effects
    # ------------------------------------------------------------------ #

    def _perform(self, effect: Effect, cause: Event) -> None:
        match effect:
            case StartCapture(seq=seq):
                self._start_capture(seq)
            case StopCapture(seq=seq):
                self._stop_capture(seq)
            case CancelCapture(seq=seq):
                self._cancel_capture(seq)
            case PersistAudio(seq=seq):
                self._persist_audio(seq)
            case WriteHistory(seq=seq, status=status):
                self._write_history(seq, status, cause)
            case Transcribe(seq=seq):
                self._transcribe(seq)
            case WriteClipboard(seq=seq):
                self._write_clipboard(seq)
            case Insert(seq=seq):
                self._insert(seq)
            case PlayCue(kind=kind):
                self.deps.play_cue(kind)
            case Notify(code=code):
                self.deps.notify(code)
            case EnqueueRetry(seq=seq):
                self._enqueue_retry(seq)
            case ReinstallHook():
                self.deps.request_hook_reinstall()
            case SetSuppression(on=on):
                self._suppress = on
            case Shutdown():
                self._shutdown.set()

    # -- capture ------------------------------------------------------- #

    def _start_capture(self, seq: int) -> None:
        dictation = self._dictations.setdefault(
            seq, _DictationState(seq=seq, pressed_wall_ms=self.deps.wall_ms())
        )
        if dictation.target is None:
            dictation.target = self._pending_target
        session = self.deps.capture_factory(
            on_started=lambda: self.post(CaptureStarted()),
            on_error=lambda code: self.post(MicError(code)),
        )
        dictation.session = session
        try:
            session.start()
        except Exception as exc:
            code = getattr(exc, "code", ErrorCode.MIC_BUSY)
            log.warning("capture failed to open: %s", getattr(code, "value", code))
            self.post(MicError(code if isinstance(code, ErrorCode) else ErrorCode.MIC_BUSY))

    def _stop_capture(self, seq: int) -> None:
        dictation = self._dictations.get(seq)
        if dictation is None or dictation.session is None:
            return
        # Blocks ~200 ms for the tail (spec §5). Delivery is strictly ordered,
        # so the driver thread paying the tail reorders nothing.
        dictation.samples = dictation.session.stop()
        dictation.session = None

    def _cancel_capture(self, seq: int) -> None:
        dictation = self._dictations.get(seq)
        if dictation is not None and dictation.session is not None:
            dictation.session.cancel()
            dictation.session = None

    def _persist_audio(self, seq: int) -> None:
        dictation = self._dictations.get(seq)
        if dictation is None:
            return
        samples = dictation.samples
        if samples is None or getattr(samples, "size", 0) == 0:
            # A capture that never produced a frame (Initialized + hook death,
            # say). Nothing to write; the machine already chose too_short.
            dictation.is_empty = True
            dictation.duration_ms = 0
            return
        result = self.deps.trim(samples)
        dictation.is_empty = result.is_empty
        dictation.duration_ms = int(len(result.samples) * 1000 / SAMPLE_RATE)
        path = self.deps.audio_dir / f"{uuid4()}.flac"
        self.deps.encode(result.samples, path)
        dictation.clip_path = path

    # -- store --------------------------------------------------------- #

    def _write_history(self, seq: int, status: DeliveryStatus, cause: Event) -> None:
        dictation = self._dictations.setdefault(
            seq, _DictationState(seq=seq, pressed_wall_ms=self.deps.wall_ms())
        )
        # OPEN-QUESTIONS §17: on InsertFailed the machine says left_on_clipboard;
        # the insert report knows the precise status (blocked_secure, focus_lost).
        error_code: str | None = None
        if isinstance(cause, InsertFailed) and dictation.last_insert_failure is not None:
            status = dictation.last_insert_failure.status
            error_code = dictation.last_insert_failure.code.value
        # Same mechanism for the ok path (cycle-2 M1): the machine's InsertOk
        # cell says INSERTED; the verification may have downgraded that to the
        # silent VERIFY_IMPOSSIBLE (spec §8) — the report's word is final.
        if isinstance(cause, InsertOk) and dictation.last_insert_status is not None:
            status = dictation.last_insert_status
        if status is DeliveryStatus.CANCELLED:
            # OPEN-QUESTIONS §5: no CancelTranscribe verb exists; the mark is
            # what a late transcription result checks before delivering.
            dictation.cancelled = True
        target = dictation.target
        try:
            if dictation.row_id is None:
                dictation.row_id = self.deps.store.add(
                    seq=seq,
                    created_at=dictation.pressed_wall_ms,
                    now=self.deps.wall_ms(),
                    duration_ms=dictation.duration_ms,
                    status=status,
                    audio_path=str(dictation.clip_path) if dictation.clip_path else None,
                    target_app=getattr(target, "process_name", None),
                    target_window_cls=getattr(target, "window_class", None),
                )
            else:
                self.deps.store.set_status(
                    dictation.row_id, status, now=self.deps.wall_ms(), error_code=error_code
                )
        except Exception:
            log.exception("history write failed for seq %d", seq)
            self.dispatch(HistoryWriteFailed(seq))

    # -- transcription ------------------------------------------------- #

    def _transcribe(self, seq: int) -> None:
        dictation = self._dictations.get(seq)
        if dictation is None:
            return
        if dictation.is_empty or dictation.clip_path is None:
            # Spec §4: an empty clip is never sent to the provider. The table
            # could not know emptiness; execution can.
            self.post(ClipEmpty(seq))
            return
        clip = AudioClip(path=dictation.clip_path, duration_ms=dictation.duration_ms)
        options = TranscriptionOptions(
            language=self.deps.language,
            prompt=build_prompt(self.deps.dictionary.prompt_terms(), self.deps.language),
        )

        def job() -> None:
            try:
                result = self.deps.provider.transcribe(clip, options)
            except TranscriptionError as exc:
                self.post(NetworkError(seq, code=exc.code))
                return
            self._on_transcription(seq, result)

        self.deps.submit_transcription(job)

    def _on_transcription(self, seq: int, result: TranscriptionResult) -> None:
        """Worker thread: package the outcome as data and post it."""
        dictation = self._dictations.get(seq)
        if dictation is None or dictation.cancelled:
            return  # cancelled while in flight: the row already says cancelled
        final = self.deps.dictionary.apply(result.text.value)
        dictation.result = result
        dictation.text = Transcript(final)
        if not final.strip():
            self.post(ClipEmpty(seq))
            return
        self.post(TranscribeOk(seq))

    # -- delivery ------------------------------------------------------ #

    def _write_clipboard(self, seq: int) -> None:
        dictation = self._dictations.get(seq)
        if dictation is None or dictation.text is None:
            return
        self._record_transcription_row(dictation)
        rule = rule_for(
            getattr(dictation.target, "process_name", None),
            getattr(dictation.target, "window_class", None),
        )
        text = prepare_text(dictation.text.value, rule)
        dictation.pasted_text = text
        dictation.snapshot = self.deps.clipboard.write(text)

    def _insert(self, seq: int) -> None:
        dictation = self._dictations.get(seq)
        if dictation is None:
            return
        if dictation.target is None or dictation.snapshot is None:
            dictation.last_insert_failure = InsertFailure(
                ErrorCode.FOCUS_LOST, DeliveryStatus.FOCUS_LOST, "no target captured"
            )
            self.post(InsertFailed(seq, code=ErrorCode.FOCUS_LOST))
            return
        report = self.deps.inserter.insert(
            dictation.target, dictation.snapshot, dictation.pasted_text
        )
        if report.ok:
            dictation.last_insert_status = report.status
            snapshot = dictation.snapshot
            # Restore later, not now: an immediate restore races the target's
            # WM_PASTE handling and pastes the restored content instead of ours.
            self.scheduler.at(
                self.deps.now_ms() + RESTORE_DELAY_MS,
                lambda: self.deps.clipboard.restore(snapshot),
            )
            self.post(InsertOk(seq))
        else:
            failure = report.failure or InsertFailure(
                ErrorCode.PASTE_FAILED, DeliveryStatus.LEFT_ON_CLIPBOARD, "unspecified"
            )
            dictation.last_insert_failure = failure
            self.post(InsertFailed(seq, code=failure.code))

    def _record_transcription_row(self, dictation: _DictationState) -> None:
        result = dictation.result
        if result is None or dictation.row_id is None or dictation.text is None:
            return
        self.deps.store.record_transcription(
            dictation.row_id,
            text_raw=result.text.value,
            text_final=dictation.text.value,
            language=result.language,
            provider_id=result.provider_id,
            billed_seconds=result.billed_seconds,
            latency_ms=result.latency_ms,
        )

    # -- retry track (spec §6: never auto-inserts) ---------------------- #

    def _enqueue_retry(self, seq: int) -> None:
        dictation = self._dictations.get(seq)
        if dictation is None or dictation.row_id is None or dictation.cancelled:
            return
        self._schedule_retry(
            _RetryItem(
                row_id=dictation.row_id,
                clip_path=dictation.clip_path,
                duration_ms=dictation.duration_ms,
            )
        )

    def enqueue_startup_pending(self) -> int:
        """Spec §3: rows the last run never finished go back to the queue.
        Returns how many — the "N записей ждут расшифровки" number.

        A non-empty queue is evidence of a past outage the gate cannot
        remember across a restart, so cleanup starts paused: the first
        successful retry reopens it through the usual ten-minute delay —
        exactly the spec's resumption rule, now surviving a restart too."""
        pending = self.deps.store.pending_at_startup()
        if pending:
            self.deps.gate.network_lost()
        for row in pending:
            self._schedule_retry(
                _RetryItem(
                    row_id=row.id,
                    clip_path=Path(row.audio_path) if row.audio_path else None,
                    duration_ms=row.duration_ms,
                ),
                immediate=True,
            )
        return len(pending)

    def _schedule_retry(self, item: _RetryItem, *, immediate: bool = False) -> None:
        delay_s = 0.0 if immediate else RETRY_LADDER_S[min(item.attempts, len(RETRY_LADDER_S) - 1)]
        self.scheduler.at(self.deps.now_ms() + int(delay_s * 1000), lambda: self._retry_job(item))

    def _retry_job(self, item: _RetryItem) -> None:
        if item.clip_path is None or not item.clip_path.exists():
            # The audio is gone (evicted at the cap, or the file vanished).
            # Nothing can ever transcribe this row again; say so.
            self.deps.store.set_status(
                item.row_id, DeliveryStatus.TRANSCRIBE_FAILED,
                now=self.deps.wall_ms(), error_code=ErrorCode.PROVIDER_ERROR.value,
            )
            return
        clip = AudioClip(path=item.clip_path, duration_ms=item.duration_ms)
        options = TranscriptionOptions(
            language=self.deps.language,
            prompt=build_prompt(self.deps.dictionary.prompt_terms(), self.deps.language),
        )

        def job() -> None:
            try:
                result = self.deps.provider.transcribe(clip, options)
            except TranscriptionError as exc:
                self._on_retry_error(item, exc)
                return
            self._on_retry_success(item, result)

        self.deps.submit_transcription(job)

    def _on_retry_success(self, item: _RetryItem, result: TranscriptionResult) -> None:
        """A retry-track arrival: history only — no clipboard, no paste
        (spec §6). Status ``withheld`` (OPEN-QUESTIONS §18); ``retry_count>0``
        marks the arrival so Ctrl+Alt+Z can exclude it (spec §9)."""
        self.deps.gate.transcribe_succeeded(self.deps.now_ms())
        final = self.deps.dictionary.apply(result.text.value)
        self.deps.store.record_transcription(
            item.row_id,
            text_raw=result.text.value,
            text_final=final,
            language=result.language,
            provider_id=result.provider_id,
            billed_seconds=result.billed_seconds,
            latency_ms=result.latency_ms,
        )
        self.deps.store.increment_retry(item.row_id)
        status = DeliveryStatus.EMPTY if not final.strip() else DeliveryStatus.WITHHELD
        self.deps.store.set_status(item.row_id, status, now=self.deps.wall_ms())

    def _on_retry_error(self, item: _RetryItem, exc: TranscriptionError) -> None:
        item.attempts += 1
        if exc.advice is RetryAdvice.NEVER:
            self.deps.store.set_status(
                item.row_id, DeliveryStatus.TRANSCRIBE_FAILED,
                now=self.deps.wall_ms(), error_code=exc.code.value,
            )
            self.deps.notify(exc.code)
            return
        self.deps.gate.network_lost()
        if exc.advice is RetryAdvice.BACKOFF and item.attempts >= len(RETRY_LADDER_S):
            self.deps.store.set_status(
                item.row_id, DeliveryStatus.TRANSCRIBE_FAILED,
                now=self.deps.wall_ms(), error_code=exc.code.value,
            )
            self.deps.notify(exc.code)
            return
        if exc.advice is RetryAdvice.AFTER_DELAY and exc.retry_after_s is not None:
            self.scheduler.at(
                self.deps.now_ms() + int(exc.retry_after_s * 1000),
                lambda: self._retry_job(item),
            )
            return
        self._schedule_retry(item)

    # -- housekeeping --------------------------------------------------- #

    def _housekeeping(self) -> None:
        now = self.deps.now_ms()
        self.scheduler.at(now + HOUSEKEEPING_INTERVAL_MS, self._housekeeping)
        if not self.deps.gate.should_run(now):
            return
        released = self.deps.store.cleanup(
            now=self.deps.wall_ms(), retention_minutes=self.deps.retention_minutes
        )
        if released:
            log.info("cleanup released %d audio files", len(released))
        evicted = self.deps.store.evict_over_cap(
            max_rows=self.deps.audio_cap_rows, max_bytes=self.deps.audio_cap_bytes
        )
        if evicted:
            # Eviction can take audio still held for retry — the one cleanup
            # the user must hear about (spec §3).
            log.warning("retention cap evicted %d audio files", len(evicted))
            self.deps.notify(ErrorCode.PROVIDER_ERROR)
