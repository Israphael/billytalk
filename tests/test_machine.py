"""The state machine, cell by cell (harness §8, spec §4).

Test names are the specification restated. Every name listed under "Обязательные
имена тестов" in harness §8 that concerns the machine appears here; the four
storage names live in ``test_store_ddl.py``, where the schema they describe is.

Nothing here sleeps or reads a clock — ``step`` takes ``now`` as a parameter, so
time in these tests is just an integer someone chose.
"""

from __future__ import annotations

from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from billytalk.core.machine import (
    MAX_QUEUE,
    MIN_CLIP_MS,
    CancelCapture,
    CaptureStarted,
    ClipEmpty,
    Cue,
    DeliveryStatus,
    DoubleEsc,
    Effect,
    EnqueueRetry,
    ErrorCode,
    EscPressed,
    Event,
    Exit,
    FailedTimeout,
    HistoryWriteFailed,
    HookDied,
    Insert,
    InsertFailed,
    InsertOk,
    MaxHoldReached,
    MicError,
    NetworkError,
    Notify,
    PersistAudio,
    Phase,
    PlayCue,
    PressFallback,
    PressPTT,
    PressToggle,
    ReinstallHook,
    ReleaseFallback,
    ReleasePTT,
    ReleaseToggle,
    SetDictationEnabled,
    SetSuppression,
    Shutdown,
    StartCapture,
    State,
    StopCapture,
    Suspend,
    Transcribe,
    TranscribeOk,
    WriteClipboard,
    WriteHistory,
    initial_state,
    step,
)
from tests.fakes import FakeClock

LONG_ENOUGH = MIN_CLIP_MS + 250


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def types(effects: tuple[Effect, ...]) -> list[type]:
    return [type(fx) for fx in effects]


def of(effects: tuple[Effect, ...], kind: type) -> list[Any]:
    return [fx for fx in effects if isinstance(fx, kind)]


class Sim:
    """Feeds events to ``step`` and keeps the whole effect trace.

    The invariants of spec §4 are statements about a trace, not about a state, so
    the trace is the thing worth keeping.
    """

    def __init__(self, state: State | None = None, start: int = 0) -> None:
        self.state = state if state is not None else initial_state()
        self.clock = FakeClock(start)
        self.trace: list[Effect] = []

    def send(self, event: Event, *, after: int = 0) -> tuple[Effect, ...]:
        now = self.clock.advance(after)
        self.state, effects = step(self.state, event, now)
        self.trace.extend(effects)
        return effects

    def dictate(self, *, hold: int = LONG_ENOUGH, hwnd: int | None = 1) -> None:
        """Press, start capturing, speak, release — leaving the machine in Finalizing."""
        self.send(PressPTT(target_hwnd=hwnd))
        self.send(CaptureStarted())
        self.send(ReleasePTT(), after=hold)

    def deliver(self, seq: int) -> None:
        """Take a finalizing dictation all the way to a successful insertion."""
        self.send(TranscribeOk(seq))
        self.send(InsertOk(seq))

    def count(self, kind: type) -> int:
        return sum(1 for fx in self.trace if isinstance(fx, kind))

    def index_of(self, kind: type, seq: int) -> int:
        for i, fx in enumerate(self.trace):
            if isinstance(fx, kind) and getattr(fx, "seq", None) == seq:
                return i
        raise AssertionError(f"{kind.__name__}(seq={seq}) never happened")


# --------------------------------------------------------------------------- #
# deferred release and the recording modes
# --------------------------------------------------------------------------- #


def test_release_while_initialized_defers_not_cancels() -> None:
    """The defect the product exists to fix.

    Wispr Flow logs "Release event while dictation is in Initialized state,
    dismissing dictation" and the dictation is gone. Here the early release is a
    field, and the dictation survives to be finalised properly.
    """
    sim = Sim()
    sim.send(PressPTT())
    assert sim.state.phase is Phase.Initialized

    sim.send(ReleasePTT(), after=10)  # too early: the stream is still opening
    assert sim.state.phase is Phase.Initialized, "the dictation must not be dismissed"
    assert sim.state.pending_release is True

    effects = sim.send(CaptureStarted(), after=40)
    assert sim.state.phase is Phase.Finalizing, "the deferred release applied"
    assert StopCapture in types(effects)


def test_capture_started_applies_deferred_release() -> None:
    """Without a pending release, CaptureStarted just starts recording."""
    sim = Sim()
    sim.send(PressPTT())
    effects = sim.send(CaptureStarted(), after=40)
    assert sim.state.phase is Phase.Recording
    assert PlayCue(Cue.START) in effects
    assert StopCapture not in types(effects)
    assert sim.state.active is not None and sim.state.active.started_at == 40


def test_toggle_ignores_release_in_all_states() -> None:
    """Spec §4: the deferred-release rule is ptt-only. A toggle release is inert."""
    for reach, expected in (
        (lambda s: None, Phase.Idle),
        (lambda s: s.send(PressToggle()), Phase.Initialized),
        (lambda s: (s.send(PressToggle()), s.send(CaptureStarted())), Phase.Recording),
    ):
        sim = Sim()
        reach(sim)
        before = sim.state
        effects = sim.send(ReleaseToggle(), after=LONG_ENOUGH)
        assert effects == ()
        assert sim.state == before
        assert sim.state.phase is expected


def test_double_press_ignored() -> None:
    """Auto-repeat and an impatient second press must not open a second capture."""
    sim = Sim()
    sim.send(PressPTT())
    assert sim.send(PressPTT()) == ()
    assert sim.state.phase is Phase.Initialized

    sim.send(CaptureStarted())
    assert sim.send(PressPTT()) == ()
    assert sim.state.phase is Phase.Recording
    assert sim.count(StartCapture) == 1


def test_release_without_press_ignored() -> None:
    """A key already held when the hook was installed is marked foreign (spec §2);
    its release must reach an Idle machine harmlessly."""
    sim = Sim()
    assert sim.send(ReleasePTT()) == ()
    assert sim.state == initial_state()


def test_fallback_binding_behaves_as_ptt() -> None:
    sim = Sim()
    sim.send(PressFallback())
    sim.send(CaptureStarted())
    effects = sim.send(ReleaseFallback(), after=LONG_ENOUGH)
    assert sim.state.phase is Phase.Finalizing
    assert StopCapture in types(effects)


def test_toggle_press_while_recording_finalizes() -> None:
    sim = Sim()
    sim.send(PressToggle())
    sim.send(CaptureStarted())
    effects = sim.send(PressToggle(), after=LONG_ENOUGH)
    assert sim.state.phase is Phase.Finalizing
    assert types(effects)[:3] == [StopCapture, PersistAudio, WriteHistory]


# --------------------------------------------------------------------------- #
# the happy path
# --------------------------------------------------------------------------- #


def test_transcribe_ok_enters_delivering() -> None:
    sim = Sim()
    sim.dictate()
    effects = sim.send(TranscribeOk(1))
    assert sim.state.phase is Phase.Delivering
    assert types(effects) == [WriteClipboard, Insert]


def test_insert_ok_returns_to_idle_or_next_queued() -> None:
    sim = Sim()
    sim.dictate()
    sim.send(TranscribeOk(1))
    effects = sim.send(InsertOk(1))
    assert sim.state.phase is Phase.Idle
    assert sim.state.active is None
    assert WriteHistory(1, DeliveryStatus.INSERTED) in effects

    # ...and with something queued, the same event starts the next dictation.
    sim = Sim()
    sim.dictate()
    sim.send(PressPTT())  # arrives during Finalizing -> queued
    sim.send(TranscribeOk(1))
    effects = sim.send(InsertOk(1))
    assert sim.state.phase is Phase.Initialized
    assert sim.state.active is not None and sim.state.active.seq == 2
    assert StartCapture(2) in effects


def test_insert_failed_leaves_text_on_clipboard_audibly() -> None:
    """Spec §5: the clipboard cue is the only channel that survives focus assist,
    a disabled overlay and a tray icon hidden in the overflow."""
    sim = Sim()
    sim.dictate()
    sim.send(TranscribeOk(1))
    effects = sim.send(InsertFailed(1))
    assert sim.state.phase is Phase.Idle
    assert WriteHistory(1, DeliveryStatus.LEFT_ON_CLIPBOARD) in effects
    assert PlayCue(Cue.CLIPBOARD) in effects
    assert Notify(ErrorCode.PASTE_FAILED) in effects


# --------------------------------------------------------------------------- #
# cancellation
# --------------------------------------------------------------------------- #


def test_single_esc_passes_through() -> None:
    """A single Esc has to stay usable for closing an autocomplete popup, so the
    machine must not consume it in any phase (spec §2)."""
    sim = Sim()
    for reach in (
        lambda s: None,
        lambda s: s.send(PressPTT()),
        lambda s: (s.send(PressPTT()), s.send(CaptureStarted())),
    ):
        sim = Sim()
        reach(sim)
        before = sim.state
        assert sim.send(EscPressed()) == ()
        assert sim.state == before


def test_double_esc_in_initialized_cancels() -> None:
    sim = Sim()
    sim.send(PressPTT())
    sim.send(ReleasePTT(), after=10)  # a deferred release is pending
    effects = sim.send(DoubleEsc())
    assert sim.state.phase is Phase.Idle
    assert CancelCapture(1) in effects
    assert sim.state.pending_release is False, "the deferred release is extinguished"


def test_double_esc_in_recording_cancels() -> None:
    sim = Sim()
    sim.send(PressPTT())
    sim.send(CaptureStarted())
    effects = sim.send(DoubleEsc(), after=LONG_ENOUGH)
    assert sim.state.phase is Phase.Idle
    assert CancelCapture(1) in effects
    assert WriteHistory(1, DeliveryStatus.CANCELLED) in effects


def test_double_esc_in_finalizing_cancels() -> None:
    sim = Sim()
    sim.dictate()
    effects = sim.send(DoubleEsc())
    assert sim.state.phase is Phase.Idle
    assert WriteHistory(1, DeliveryStatus.CANCELLED) in effects
    assert CancelCapture not in types(effects), "the capture was already closed"


def test_double_esc_in_delivering_ignored() -> None:
    """The text is already in the clipboard and the history; there is nothing left
    to cancel, and Esc belongs to the application again."""
    sim = Sim()
    sim.dictate()
    sim.send(TranscribeOk(1))
    before = sim.state
    assert sim.send(DoubleEsc()) == ()
    assert sim.state == before


# --------------------------------------------------------------------------- #
# failures and survivability
# --------------------------------------------------------------------------- #


def test_max_hold_delivers_to_history_not_paste() -> None:
    """The fuse fired, so the words are kept and reachable — but nothing lands in
    a window on its own after a hold that long."""
    sim = Sim()
    sim.send(PressPTT())
    sim.send(CaptureStarted())
    effects = sim.send(MaxHoldReached(), after=20 * 60 * 1000)
    assert sim.state.phase is Phase.Finalizing
    assert types(effects)[:3] == [StopCapture, PersistAudio, WriteHistory]

    effects = sim.send(TranscribeOk(1))
    assert sim.state.phase is Phase.Idle
    assert WriteClipboard(1) in effects
    assert WriteHistory(1, DeliveryStatus.WITHHELD) in effects, (
        "spec §10: deliberate non-delivery is `withheld`, not `left_on_clipboard` — "
        "conflating it with a failed paste makes delivery debugging impossible"
    )
    assert Insert not in types(effects), "the fuse forbids the paste, not the text"


def test_hook_death_finalizes_recording() -> None:
    sim = Sim()
    sim.send(PressPTT())
    sim.send(CaptureStarted())
    effects = sim.send(HookDied(), after=LONG_ENOUGH)
    assert sim.state.phase is Phase.Finalizing
    assert StopCapture(1) in effects
    assert PersistAudio(1) in effects
    assert ReinstallHook() in effects


def test_hook_death_when_idle_only_reinstalls() -> None:
    sim = Sim()
    effects = sim.send(HookDied())
    assert sim.state.phase is Phase.Idle
    assert effects == (ReinstallHook(),)


def test_suspend_finalizes_recording() -> None:
    """Spec §3: the machine has a few seconds. Finalise, save, drop suppression —
    the suppressed-key set does not survive a sleep and an orphaned release would."""
    sim = Sim()
    sim.send(PressPTT())
    sim.send(CaptureStarted())
    effects = sim.send(Suspend(), after=LONG_ENOUGH)
    assert sim.state.phase is Phase.Finalizing
    assert StopCapture(1) in effects
    assert PersistAudio(1) in effects
    assert SetSuppression(False) in effects


def test_mic_error_enters_failed_then_idle() -> None:
    sim = Sim()
    effects = sim.send(MicError(ErrorCode.MIC_BUSY))
    assert sim.state.phase is Phase.Failed
    assert Notify(ErrorCode.MIC_BUSY) in effects

    effects = sim.send(FailedTimeout(), after=3000)
    assert sim.state.phase is Phase.Idle, "Failed is transient by design"


def test_failed_clears_suppression() -> None:
    """A bound button must never mean "nothing happens" (spec §2). If dictation
    cannot start, Mouse 4 has to go back to being Back."""
    sim = Sim()
    effects = sim.send(MicError())
    assert SetSuppression(False) in effects

    effects = sim.send(FailedTimeout(), after=3000)
    assert SetSuppression(True) in effects, "and it becomes ours again on recovery"


def test_mic_error_in_recording_keeps_what_was_captured() -> None:
    sim = Sim()
    sim.send(PressPTT())
    sim.send(CaptureStarted())
    effects = sim.send(MicError(), after=LONG_ENOUGH)
    assert sim.state.phase is Phase.Finalizing
    assert PersistAudio(1) in effects


def test_press_in_failed_starts_a_new_dictation() -> None:
    sim = Sim()
    sim.send(MicError())
    effects = sim.send(PressPTT(), after=100)
    assert sim.state.phase is Phase.Initialized
    assert StartCapture(1) in effects


def test_history_write_failure_still_writes_clipboard() -> None:
    """Losing the history row is bad; losing the words is worse. The delivery path
    carries on, with a warning cue so the failure is not silent."""
    sim = Sim()
    sim.dictate()
    effects = sim.send(HistoryWriteFailed(1))
    assert effects == (PlayCue(Cue.WARN),)
    assert sim.state.phase is Phase.Finalizing

    effects = sim.send(TranscribeOk(1))
    assert WriteClipboard(1) in effects


def test_network_error_enqueues_retry_and_never_auto_inserts() -> None:
    sim = Sim()
    sim.dictate()
    effects = sim.send(NetworkError(1))
    assert sim.state.phase is Phase.Idle
    assert WriteHistory(1, DeliveryStatus.PENDING_RETRY) in effects
    assert EnqueueRetry(1) in effects
    assert Insert not in types(effects)


# --------------------------------------------------------------------------- #
# the queue
# --------------------------------------------------------------------------- #


def test_second_dictation_queues_and_delivers_in_order() -> None:
    """Transcriptions may finish out of order; delivery may not (spec §4)."""
    sim = Sim()
    sim.dictate()
    sim.send(PressPTT(target_hwnd=2))
    assert len(sim.state.queue) == 1
    assert sim.state.queue[0].seq == 2, "seq is assigned at press time, not at delivery"

    sim.send(TranscribeOk(1))
    sim.send(InsertOk(1))
    assert sim.state.active is not None and sim.state.active.seq == 2

    sim.send(CaptureStarted())
    sim.send(ReleasePTT(), after=LONG_ENOUGH)
    sim.deliver(2)
    assert sim.state.phase is Phase.Idle

    inserted = [fx.seq for fx in sim.trace if isinstance(fx, Insert)]
    assert inserted == [1, 2]


def test_release_while_press_is_queued_is_not_lost() -> None:
    """The orphaned-edge defect: let go while queued, and the release must survive.

    The user holds Mouse 4 while an earlier dictation is still being delivered, so
    the press queues. Then they let go — and by the time this press is popped the
    button is already up, so no second release will ever arrive. Drop that release
    and the recording runs until the five-minute fuse, with the button dead.

    Nothing else catches this. The capture ledger stays perfectly balanced: one
    StartCapture, one StopCapture, eventually. It is visible only as a user holding
    a button that does nothing — exactly the class of bug this product exists to
    fix (spec §4, OPEN-QUESTIONS §9).
    """
    sim = Sim()
    sim.dictate()
    sim.send(PressPTT(target_hwnd=2))
    assert len(sim.state.queue) == 1

    sim.send(ReleasePTT())
    assert sim.state.queue[0].released, "the release must be recorded against the queued press"

    sim.send(TranscribeOk(1))
    sim.send(InsertOk(1))
    assert sim.state.phase is Phase.Initialized
    assert sim.state.pending_release, "it must arrive as a deferred release"

    effects = sim.send(CaptureStarted())
    assert sim.state.phase is Phase.Finalizing, "closes itself, without a second release"
    assert StopCapture(2) in effects


def test_queued_toggle_ignores_a_release() -> None:
    """A queued toggle must not be closed by the release of the button that made it."""
    sim = Sim()
    sim.dictate()
    sim.send(PressToggle(target_hwnd=2))
    sim.send(ReleasePTT())
    assert not sim.state.queue[0].released


def test_failed_transcription_releases_its_ordering_slot() -> None:
    """A dead network must not wedge the queue behind it."""
    sim = Sim()
    sim.dictate()
    sim.send(PressPTT())
    effects = sim.send(NetworkError(1))
    assert EnqueueRetry(1) in effects
    assert StartCapture(2) in effects, "the next press takes the slot immediately"
    assert sim.state.phase is Phase.Initialized
    assert sim.state.queue == ()


def test_fourth_press_rejected_at_press_time_with_cue() -> None:
    """Refusing after the user has spoken is worthless — the words are gone. So the
    refusal happens on the press."""
    sim = Sim()
    sim.dictate()
    for _ in range(MAX_QUEUE):
        assert sim.send(PressPTT()) == ()
    assert len(sim.state.queue) == MAX_QUEUE

    before = sim.state
    effects = sim.send(PressPTT())
    assert effects == (PlayCue(Cue.REJECT),)
    assert sim.state == before, "nothing was queued and no seq was consumed"


# --------------------------------------------------------------------------- #
# durability ordering
# --------------------------------------------------------------------------- #


def test_persist_audio_precedes_transcribe() -> None:
    """Spec §3: the speech is on disk before the network is touched. Otherwise a
    crash, a sleep or a Windows Update reboot destroys it along with all three
    escape hatches, which all grow out of the history row."""
    sim = Sim()
    sim.dictate()
    assert sim.index_of(PersistAudio, 1) < sim.index_of(Transcribe, 1)


def test_write_history_precedes_insert() -> None:
    sim = Sim()
    sim.dictate()
    sim.deliver(1)
    assert sim.index_of(WriteHistory, 1) < sim.index_of(Insert, 1)


def test_write_clipboard_precedes_insert() -> None:
    """Spec §8: the clipboard is the primary path, not a fallback. Whatever the
    insertion does, the text is already reachable with Ctrl+V."""
    sim = Sim()
    sim.dictate()
    effects = sim.send(TranscribeOk(1))
    assert types(effects).index(WriteClipboard) < types(effects).index(Insert)


def test_short_press_recorded_but_silent() -> None:
    """Under 250 ms from the first captured frame: a history row, no cue, no toast.

    The row is what keeps the acceptance criterion falsifiable — without it
    "BillyTalk lost my text" and "I never pressed the button" look identical.
    """
    sim = Sim()
    sim.send(PressPTT())
    sim.send(CaptureStarted())
    effects = sim.send(ReleasePTT(), after=MIN_CLIP_MS - 1)

    assert sim.state.phase is Phase.Idle
    assert WriteHistory(1, DeliveryStatus.TOO_SHORT) in effects
    assert PersistAudio(1) in effects
    assert Transcribe not in types(effects), "no request is spent on it"
    assert PlayCue not in types(effects)
    assert Notify not in types(effects)


def test_short_press_measured_from_first_frame_not_from_press() -> None:
    """Spec §4 measures the hold from the first captured frame: the Bluetooth path
    takes 100–300 ms to wake, and the first syllables are physically lost."""
    sim = Sim()
    sim.send(PressPTT())
    sim.send(CaptureStarted(), after=300)  # slow headset
    effects = sim.send(ReleasePTT(), after=MIN_CLIP_MS + 10)
    assert WriteHistory(1, DeliveryStatus.PENDING_TRANSCRIBE) in effects


def test_empty_clip_recorded_but_silent() -> None:
    sim = Sim()
    sim.dictate()
    effects = sim.send(ClipEmpty(1))
    assert sim.state.phase is Phase.Idle
    assert effects == (WriteHistory(1, DeliveryStatus.EMPTY),)


# --------------------------------------------------------------------------- #
# tray switch and shutdown
# --------------------------------------------------------------------------- #


def test_disabling_dictation_releases_the_button() -> None:
    sim = Sim()
    effects = sim.send(SetDictationEnabled(False))
    assert effects == (SetSuppression(False),)
    assert sim.send(PressPTT()) == (), "a disabled machine starts nothing"

    effects = sim.send(SetDictationEnabled(True))
    assert effects == (SetSuppression(True),)


def test_disabling_during_recording_saves_but_does_not_paste() -> None:
    sim = Sim()
    sim.send(PressPTT())
    sim.send(CaptureStarted())
    effects = sim.send(SetDictationEnabled(False), after=LONG_ENOUGH)
    assert sim.state.phase is Phase.Finalizing
    assert PersistAudio(1) in effects

    effects = sim.send(TranscribeOk(1))
    assert Insert not in types(effects)
    assert WriteClipboard(1) in effects
    assert WriteHistory(1, DeliveryStatus.WITHHELD) in effects, (
        "switched off from the tray mid-recording: transcribed, kept, deliberately not pasted"
    )


def test_exit_during_recording_saves_first() -> None:
    """Windows gives about five seconds at WM_QUERYENDSESSION (spec §3)."""
    sim = Sim()
    sim.send(PressPTT())
    sim.send(CaptureStarted())
    effects = sim.send(Exit(), after=LONG_ENOUGH)
    assert types(effects) == [StopCapture, PersistAudio, WriteHistory, Shutdown]
    assert effects[-1] == Shutdown()


def test_exit_during_finalizing_hands_the_request_to_the_retry_track() -> None:
    sim = Sim()
    sim.dictate()
    effects = sim.send(Exit())
    assert effects == (EnqueueRetry(1), Shutdown())


# --------------------------------------------------------------------------- #
# properties over random event sequences (spec §4, invariants)
# --------------------------------------------------------------------------- #

# Sequence-bearing events must refer to a dictation that exists, so the pool holds
# factories rather than values: the seq is filled in from the state at send time.
# A pool of pre-built events would spend most of its draws on events the machine
# ignores outright, and would never reach the interesting interleavings.

def _active_seq(state: State) -> int:
    return state.active.seq if state.active is not None else 0


EVENT_FACTORIES: list[tuple[str, Any]] = [
    ("press_ptt", lambda s: PressPTT(target_hwnd=7)),
    ("release_ptt", lambda s: ReleasePTT()),
    ("press_fallback", lambda s: PressFallback()),
    ("release_fallback", lambda s: ReleaseFallback()),
    ("press_toggle", lambda s: PressToggle()),
    ("release_toggle", lambda s: ReleaseToggle()),
    ("capture_started", lambda s: CaptureStarted()),
    ("transcribe_ok", lambda s: TranscribeOk(_active_seq(s))),
    ("network_error", lambda s: NetworkError(_active_seq(s))),
    ("clip_empty", lambda s: ClipEmpty(_active_seq(s))),
    ("insert_ok", lambda s: InsertOk(_active_seq(s))),
    ("insert_failed", lambda s: InsertFailed(_active_seq(s))),
    ("history_write_failed", lambda s: HistoryWriteFailed(_active_seq(s))),
    ("esc", lambda s: EscPressed()),
    ("double_esc", lambda s: DoubleEsc()),
    ("max_hold", lambda s: MaxHoldReached()),
    ("mic_error", lambda s: MicError()),
    ("failed_timeout", lambda s: FailedTimeout()),
    ("hook_died", lambda s: HookDied()),
    ("suspend", lambda s: Suspend()),
    ("dictation_off", lambda s: SetDictationEnabled(False)),
    ("dictation_on", lambda s: SetDictationEnabled(True)),
]

CANCELLING = {"double_esc", "mic_error", "dictation_off"}
"""Factories that can close a capture with CancelCapture rather than StopCapture."""

ALL_EVENTS = [name for name, _ in EVENT_FACTORIES]
NON_CANCELLING_EVENTS = [name for name in ALL_EVENTS if name not in CANCELLING]


def _run(names: list[str], *, tick: int = 120) -> Sim:
    sim = Sim()
    factories = dict(EVENT_FACTORIES)
    for name in names:
        sim.send(factories[name](sim.state), after=tick)
    return sim


def _drain(sim: Sim) -> None:
    """Take whatever is in flight to a natural conclusion.

    Used instead of a terminating ``Exit`` so that the capture ledger closes the
    way it does in normal use, rather than through the shutdown path.
    """
    for _ in range(64):
        state = sim.state
        if state.active is None and not state.queue:
            return
        seq = _active_seq(state)
        match state.phase:
            case Phase.Initialized:
                sim.send(CaptureStarted(), after=10)
            case Phase.Recording:
                sim.send(ReleasePTT(), after=400)
                if sim.state.phase is Phase.Recording:  # a toggle dictation
                    sim.send(PressToggle(), after=10)
            case Phase.Finalizing:
                sim.send(TranscribeOk(seq), after=10)
            case Phase.Delivering:
                sim.send(InsertOk(seq), after=10)
            case _:
                return
    raise AssertionError("the machine would not come to rest")


def _assert_invariants(sim: Sim) -> None:
    """The five invariants of spec §4, checked over the whole effect trace."""
    open_captures: set[int] = set()
    closed: set[int] = set()
    transcribed: set[int] = set()
    persisted: set[int] = set()
    history_written: set[int] = set()
    clipboard_written: set[int] = set()

    for fx in sim.trace:
        match fx:
            case StartCapture(seq=seq):
                assert not open_captures, "never two concurrent captures"
                assert seq not in closed, f"seq {seq} reopened after being closed"
                open_captures.add(seq)
            case StopCapture(seq=seq) | CancelCapture(seq=seq):
                assert seq in open_captures, f"capture {seq} closed without being opened"
                open_captures.discard(seq)
                closed.add(seq)
            case PersistAudio(seq=seq):
                persisted.add(seq)
            case WriteHistory(seq=seq):
                history_written.add(seq)
            case WriteClipboard(seq=seq):
                assert seq in persisted, "never WriteClipboard without a preceding PersistAudio"
                clipboard_written.add(seq)
            case Transcribe(seq=seq):
                assert seq in persisted, "the speech is on disk before the network is touched"
                transcribed.add(seq)
            case Insert(seq=seq):
                assert seq in clipboard_written, "the clipboard is the primary path"
                assert seq in history_written, "WriteHistory always precedes Insert"
            case _:
                pass

    assert len(open_captures) <= 1


def _closers(sim: Sim) -> int:
    return sim.count(StopCapture) + sim.count(CancelCapture)


@settings(max_examples=400, suppress_health_check=[HealthCheck.too_slow])
@given(st.lists(st.sampled_from(ALL_EVENTS), max_size=200))
def test_invariants_hold_over_arbitrary_event_sequences(names: list[str]) -> None:
    sim = _run(names)
    _drain(sim)
    sim.send(Exit(), after=120)
    _assert_invariants(sim)
    assert sim.count(StartCapture) == _closers(sim), (
        "every capture opened is closed exactly once"
    )


@settings(max_examples=400, suppress_health_check=[HealthCheck.too_slow])
@given(st.lists(st.sampled_from(NON_CANCELLING_EVENTS), max_size=200))
def test_start_capture_count_equals_stop_capture_count(names: list[str]) -> None:
    """The metric the competitor fails: 234 StartCapture against 244 StopCapture.

    Restricted to sequences containing no cancellation, because a cancelled
    capture is closed by ``CancelCapture`` and would make the literal equality
    false for a correct machine. The general form — every capture closed exactly
    once, by exactly one of the two — is asserted above. See OPEN-QUESTIONS §1.
    """
    sim = _run(names)
    _drain(sim)
    assert sim.count(CancelCapture) == 0
    assert sim.count(StartCapture) == sim.count(StopCapture)


@settings(max_examples=400, suppress_health_check=[HealthCheck.too_slow])
@given(st.lists(st.sampled_from(ALL_EVENTS), max_size=200))
def test_queue_never_exceeds_three(names: list[str]) -> None:
    sim = Sim()
    factories = dict(EVENT_FACTORIES)
    for name in names:
        sim.send(factories[name](sim.state), after=120)
        assert len(sim.state.queue) <= MAX_QUEUE


@settings(max_examples=400, suppress_health_check=[HealthCheck.too_slow])
@given(st.lists(st.sampled_from(ALL_EVENTS), max_size=200))
def test_state_is_never_internally_inconsistent(names: list[str]) -> None:
    """Phase and the active dictation must agree, or the driver cannot trust either."""
    sim = Sim()
    factories = dict(EVENT_FACTORIES)
    for name in names:
        sim.send(factories[name](sim.state), after=120)
        s = sim.state
        if s.phase in (Phase.Initialized, Phase.Recording, Phase.Finalizing, Phase.Delivering):
            assert s.active is not None, f"{s.phase} without an active dictation"
        else:
            assert s.active is None, f"{s.phase} still holding a dictation"
        if s.pending_release:
            assert s.phase is Phase.Initialized
        seqs = [q.seq for q in s.queue]
        assert seqs == sorted(seqs), "the queue is in press order"


@pytest.mark.parametrize("seed_length", [0, 1, 5, 50])
def test_step_never_mutates_the_state_it_was_given(seed_length: int) -> None:
    """``step`` is pure: the caller's record survives the call unchanged."""
    sim = _run(ALL_EVENTS[:seed_length])
    before = sim.state
    snapshot = (before.phase, before.enabled, before.pending_release, before.queue,
                before.active, before.next_seq)
    step(before, PressPTT(), 10_000)
    step(before, DoubleEsc(), 10_001)
    assert (before.phase, before.enabled, before.pending_release, before.queue,
            before.active, before.next_seq) == snapshot
