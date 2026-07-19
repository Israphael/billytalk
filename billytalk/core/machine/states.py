"""The dictation state machine: ``step(state, event, now) -> (state, effects)``.

This is the correctness argument of the whole product, so it is a pure function.
It reads no clock, spawns no thread, touches no device and returns no ``None``
surprises: give it the same state, event and ``now`` and it gives the same answer
forever. Everything that can actually fail — the microphone, the network, the
clipboard — reaches it as an *event*, and everything it wants done leaves it as
*data* (see ``effects.py``).

The reason for that discipline is in spec §4. Wispr Flow logs 234 ``StartCapture``
against 244 ``StopCapture``, and its user-visible symptom is a line reading
``Release event while dictation is in Initialized state, dismissing dictation`` —
a race between the button coming up and the audio stream finishing opening. A
race is a thing you cannot test out of a threaded design and cannot get wrong in
a pure one: here the early release is simply a *field*, and ``CaptureStarted``
applies it.

Implements the state x event table of spec §4 cell by cell. Where a cell was
under-specified the reading chosen is recorded in
``docs/ru/spec/OPEN-QUESTIONS.md`` rather than being decided silently.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum

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
    Exit,
    FailedTimeout,
    HistoryWriteFailed,
    HookDied,
    InsertFailed,
    InsertOk,
    MaxHoldReached,
    MicError,
    NetworkError,
    PressFallback,
    PressPTT,
    PressToggle,
    ReleaseFallback,
    ReleasePTT,
    ReleaseToggle,
    SetDictationEnabled,
    Suspend,
    TranscribeOk,
)

__all__ = [
    "MAX_QUEUE",
    "MIN_CLIP_MS",
    "ActiveDictation",
    "Phase",
    "QueuedPress",
    "State",
    "initial_state",
    "step",
]

MAX_QUEUE = 3
"""Spec §4: at most three presses may wait. The fourth is refused at press time."""

MIN_CLIP_MS = 250
"""Spec §4: measured from the first captured frame, never from the press."""


class Phase(Enum):
    """The six phases of spec §4.

    ``Failed`` is transient by design: it exists so the user gets one notification,
    and the machine leaves it on its own. A button that stays in "does nothing" is
    worse than a button that fails loudly.
    """

    Idle = "idle"
    Initialized = "initialized"
    Recording = "recording"
    Finalizing = "finalizing"
    Delivering = "delivering"
    Failed = "failed"


@dataclass(frozen=True, slots=True)
class ActiveDictation:
    """The one dictation currently occupying the machine.

    ``started_at`` is ``None`` until :class:`CaptureStarted` — that is precisely the
    window in which a release must be deferred rather than obeyed.

    ``deliver`` is not in the spec's field list but the table demands it: the
    max-hold fuse and "dictation switched off from the tray" both say *write it to
    history but do NOT paste*, and that verdict is reached in ``Recording`` while
    the decision it constrains is taken later, on ``TranscribeOk``. Without the
    flag those two cells are unrepresentable (OPEN-QUESTIONS §3).
    """

    seq: int
    started_at: int | None = None
    target_hwnd: int | None = None
    toggle: bool = False
    deliver: bool = True


@dataclass(frozen=True, slots=True)
class QueuedPress:
    """A press that arrived while an earlier dictation was still finishing.

    ``seq`` is assigned here, at press time, because ordered delivery is ordered by
    *press*, not by whichever transcription happens to come back first.
    """

    seq: int
    target_hwnd: int | None = None
    toggle: bool = False
    released: bool = False
    """Set when the button came up while this press was still waiting in the queue.

    Without it the release is simply lost: the user holds Mouse 4 during an earlier
    delivery, lets go, and by the time this entry is popped the button is already
    up — so no second release will ever arrive and the recording runs to the
    five-minute fuse. That is the orphaned-input-edge defect this whole product
    exists to prevent, and the capture-balance invariant does not catch it because
    the ledger stays balanced (spec §4, OPEN-QUESTIONS §9).
    """


@dataclass(frozen=True, slots=True)
class State:
    """Spec §4. A record, not a scalar — the phase alone cannot hold a deferred
    release, a queue and a sequence counter at the same time."""

    phase: Phase = Phase.Idle
    enabled: bool = True
    pending_release: bool = False
    queue: tuple[QueuedPress, ...] = ()
    active: ActiveDictation | None = None
    next_seq: int = 1


def initial_state() -> State:
    """A freshly started core: dictation on, nothing recorded, sequence at 1."""
    return State()


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _start(state: State, *, target_hwnd: int | None, toggle: bool) -> tuple[State, tuple[Effect, ...]]:
    """Begin a new dictation, consuming the next sequence number."""
    active = ActiveDictation(seq=state.next_seq, target_hwnd=target_hwnd, toggle=toggle)
    new = replace(
        state,
        phase=Phase.Initialized,
        active=active,
        next_seq=state.next_seq + 1,
        pending_release=False,
    )
    return new, (StartCapture(active.seq),)


def _release_slot(state: State) -> tuple[State, tuple[Effect, ...]]:
    """Finish with the active dictation and hand the machine to the next press.

    Spec §4, "delivery order": a dictation that fails releases its ordering slot
    exactly like one that succeeds, so a dead network cannot wedge the queue.
    """
    if state.queue:
        head, *rest = state.queue
        # A press whose button already came up while it waited carries its release
        # forward, so the deferred-release path closes it the moment the stream
        # opens. Dropping it here is what would leave the button silent until the
        # max-hold fuse (OPEN-QUESTIONS §9).
        handed = replace(state, queue=tuple(rest), active=None, pending_release=head.released)
        active = ActiveDictation(seq=head.seq, target_hwnd=head.target_hwnd, toggle=head.toggle)
        return replace(handed, phase=Phase.Initialized, active=active), (StartCapture(active.seq),)
    return replace(state, phase=Phase.Idle, active=None, pending_release=False), ()


def _finalize(
    state: State, now: int, *, deliver: bool, measure: bool = True
) -> tuple[State, tuple[Effect, ...]]:
    """Close the capture and make the speech durable (spec §3).

    Order is load-bearing and is asserted by the tests: stop, encode to disk, write
    the history row, and only then touch the network. A crash, a sleep or a Windows
    Update reboot at any point after this returns still leaves the user's words on
    disk, and all three escape hatches — Ctrl+V, Win+V, Ctrl+Alt+Z — grow out of
    that row.

    ``measure=False`` suppresses the 250 ms floor. It is used for exactly one case:
    a deferred release, where the wall-clock hold the machine can see is zero but
    the clip is not — the stream opened, and spec §5's 200 ms tail is captured
    after the release. Whether that clip actually contains speech is a question
    only the audio layer can answer, and it answers it with ``ClipEmpty``.
    """
    active = state.active
    assert active is not None
    effects: list[Effect] = [StopCapture(active.seq), PersistAudio(active.seq)]

    # "Too short" is measured from the first captured frame, so a capture that
    # never started is too short by definition — which is also what makes a
    # hook death or a suspend during Initialized safe to treat this way.
    too_short = measure and (active.started_at is None or (now - active.started_at) < MIN_CLIP_MS)
    if too_short:
        effects.append(WriteHistory(active.seq, DeliveryStatus.TOO_SHORT))
        # Recorded, but silent (spec §4): no cue, no notification. The row exists
        # only so that "BillyTalk lost it" stays distinguishable from "I never
        # pressed the button" — without it the acceptance criterion is unfalsifiable.
        moved, tail = _release_slot(state)
        return moved, tuple(effects) + tail

    effects.append(WriteHistory(active.seq, DeliveryStatus.PENDING_TRANSCRIBE))
    effects.append(PlayCue(Cue.STOP))
    effects.append(Transcribe(active.seq))
    return (
        replace(
            state,
            phase=Phase.Finalizing,
            active=replace(active, deliver=deliver),
            pending_release=False,
        ),
        tuple(effects),
    )


def _close_without_recording(state: State) -> tuple[State, tuple[Effect, ...]]:
    """Discard a capture that was opened but produced nothing worth keeping.

    Emits ``CancelCapture`` so the capture ledger stays balanced: every
    ``StartCapture`` is closed exactly once, by a ``StopCapture`` or a
    ``CancelCapture`` and never by both.
    """
    active = state.active
    if active is None:
        return state, ()
    return replace(state, active=None, pending_release=False), (CancelCapture(active.seq),)


def _enter_failed(state: State, code: ErrorCode) -> tuple[State, tuple[Effect, ...]]:
    """Fail loudly, and let the button be a button again.

    Suppression comes off on the way in (spec §2: it is released whenever a
    recording cannot start) and goes back on when ``Failed`` times out.
    """
    closed, effects = _close_without_recording(state)
    return (
        replace(closed, phase=Phase.Failed, queue=()),
        effects + (Notify(code), SetSuppression(False)),
    )


def _queue_press(
    state: State, *, target_hwnd: int | None, toggle: bool
) -> tuple[State, tuple[Effect, ...]]:
    """Queue a press, or refuse it now.

    Spec §4: the fourth press is rejected **at press time, before the user has
    started speaking**. Refusing after the speech is worthless — the words are
    already gone.
    """
    if len(state.queue) >= MAX_QUEUE:
        return state, (PlayCue(Cue.REJECT),)
    entry = QueuedPress(seq=state.next_seq, target_hwnd=target_hwnd, toggle=toggle)
    return replace(state, queue=state.queue + (entry,), next_seq=state.next_seq + 1), ()


# --------------------------------------------------------------------------- #
# event handlers, one per row of the spec §4 table
# --------------------------------------------------------------------------- #


def _on_press(
    state: State, now: int, *, target_hwnd: int | None, toggle: bool
) -> tuple[State, tuple[Effect, ...]]:
    if not state.enabled:
        return state, ()
    match state.phase:
        case Phase.Idle | Phase.Failed:
            return _start(state, target_hwnd=target_hwnd, toggle=toggle)
        case Phase.Initialized:
            return state, ()  # auto-repeat and double press: ignored
        case Phase.Recording:
            if toggle:
                return _finalize(state, now, deliver=True)
            return state, ()
        case Phase.Finalizing | Phase.Delivering:
            return _queue_press(state, target_hwnd=target_hwnd, toggle=toggle)
    return state, ()


def _on_release_ptt(state: State, now: int) -> tuple[State, tuple[Effect, ...]]:
    active = state.active
    if active is not None and active.toggle:
        return state, ()  # a toggle dictation ignores releases in every phase
    match state.phase:
        case Phase.Initialized:
            # The bug this product exists to fix. The button came up before the
            # audio stream finished opening; that is not a cancellation, it is a
            # very short dictation that has not started yet.
            return replace(state, pending_release=True), ()
        case Phase.Recording:
            return _finalize(state, now, deliver=True)
        case Phase.Finalizing | Phase.Delivering:
            # The press this release belongs to is still queued. Mark it so the
            # release survives the wait; ignoring it here is the orphaned-edge
            # defect described on QueuedPress.released.
            return _mark_queue_released(state), ()
    return state, ()


def _mark_queue_released(state: State) -> State:
    """Flag the most recent still-held queue entry as released.

    Most recent, not oldest: presses queue in order and the user is releasing the
    button they are holding now, which is the last one they pressed.
    """
    for i in range(len(state.queue) - 1, -1, -1):
        entry = state.queue[i]
        if not entry.released and not entry.toggle:
            marked = replace(entry, released=True)
            return replace(state, queue=state.queue[:i] + (marked,) + state.queue[i + 1 :])
    return state


def _on_capture_started(state: State, now: int) -> tuple[State, tuple[Effect, ...]]:
    if state.phase is not Phase.Initialized or state.active is None:
        return state, ()
    recording = replace(
        state, phase=Phase.Recording, active=replace(state.active, started_at=now)
    )
    cue: tuple[Effect, ...] = (PlayCue(Cue.START),)
    if state.pending_release:
        # The deferred release applies now, and the start cue still plays: it is
        # the confirmation that the stream really opened (spec §5).
        finalized, effects = _finalize(
            replace(recording, pending_release=False), now, deliver=True, measure=False
        )
        return finalized, cue + effects
    return recording, cue


def _on_double_esc(state: State) -> tuple[State, tuple[Effect, ...]]:
    active = state.active
    match state.phase:
        case Phase.Initialized:
            closed, effects = _close_without_recording(state)
            return replace(closed, phase=Phase.Idle, queue=()), effects
        case Phase.Recording:
            assert active is not None
            closed, effects = _close_without_recording(state)
            return replace(closed, phase=Phase.Idle, queue=()), effects + (
                WriteHistory(active.seq, DeliveryStatus.CANCELLED),
            )
        case Phase.Finalizing:
            assert active is not None
            # The capture is already closed; what is cancelled here is the network
            # request. The effect vocabulary has no verb for that (OPEN-QUESTIONS §5).
            return replace(state, phase=Phase.Idle, active=None, queue=()), (
                WriteHistory(active.seq, DeliveryStatus.CANCELLED),
            )
        case Phase.Failed:
            return replace(state, phase=Phase.Idle, active=None, queue=()), (
                SetSuppression(True),
            )
    return state, ()  # Idle and Delivering: ignored


def _on_hook_died(state: State, now: int) -> tuple[State, tuple[Effect, ...]]:
    if state.phase in (Phase.Initialized, Phase.Recording):
        finalized, effects = _finalize(state, now, deliver=True)
        return finalized, effects + (ReinstallHook(),)
    return state, (ReinstallHook(),)


def _on_suspend(state: State, now: int) -> tuple[State, tuple[Effect, ...]]:
    # Spec §3: finalise, save, close the stream, and reset suppression — the
    # suppressed-key set does not survive a sleep, and an orphaned release would.
    if state.phase in (Phase.Initialized, Phase.Recording):
        finalized, effects = _finalize(state, now, deliver=True)
        return finalized, effects + (SetSuppression(False),)
    return state, (SetSuppression(False),)


def _on_set_enabled(state: State, event: SetDictationEnabled, now: int) -> tuple[State, tuple[Effect, ...]]:
    if event.enabled:
        if state.enabled:
            return state, ()
        return replace(state, enabled=True), (SetSuppression(True),)

    off: tuple[Effect, ...] = (SetSuppression(False),)
    match state.phase:
        case Phase.Initialized:
            closed, effects = _close_without_recording(state)
            return replace(closed, phase=Phase.Idle, enabled=False, queue=()), effects + off
        case Phase.Recording:
            # Keep what was said, but never paste it: the user has just told us to
            # stop, and pasting into whatever now has focus would be a surprise.
            finalized, effects = _finalize(state, now, deliver=False)
            return replace(finalized, enabled=False, queue=()), effects + off
        case Phase.Finalizing | Phase.Delivering:
            return replace(state, enabled=False, queue=()), off  # let it finish
        case Phase.Failed:
            return replace(state, phase=Phase.Idle, active=None, enabled=False, queue=()), off
    return replace(state, enabled=False, queue=()), off


def _on_exit(state: State, now: int) -> tuple[State, tuple[Effect, ...]]:
    """Save whatever is in flight and stop. Windows gives about five seconds."""
    active = state.active
    effects: list[Effect] = []
    match state.phase:
        case Phase.Initialized if active is not None:
            effects.append(CancelCapture(active.seq))
        case Phase.Recording:
            assert active is not None
            effects += [
                StopCapture(active.seq),
                PersistAudio(active.seq),
                WriteHistory(active.seq, DeliveryStatus.PENDING_TRANSCRIBE),
            ]
        case Phase.Finalizing:
            assert active is not None
            # The row and the audio are already on disk; hand the network call to
            # the retry track so the restart picks it up.
            effects.append(EnqueueRetry(active.seq))
    effects.append(Shutdown())
    stopped = replace(
        state, phase=Phase.Idle, active=None, queue=(), pending_release=False, enabled=False
    )
    return stopped, tuple(effects)


def _terminal(state: State, effects: tuple[Effect, ...]) -> tuple[State, tuple[Effect, ...]]:
    """Finish the active dictation and let the queue advance."""
    moved, tail = _release_slot(state)
    return moved, effects + tail


# --------------------------------------------------------------------------- #
# the function itself
# --------------------------------------------------------------------------- #


def step(state: State, event: Event, now: int) -> tuple[State, tuple[Effect, ...]]:
    """Advance the machine by one event.

    Args:
        state: the current record. Never mutated.
        event: something that has already happened.
        now: monotonic milliseconds. A parameter and not a call, so that the fuse,
            the 250 ms floor and every test are all driven by the same dial.

    Returns:
        The next state and the effects to perform, in the order they must be
        performed. An event with no cell in the table returns the state unchanged
        and no effects — silence is a valid answer and never an error.
    """
    match event:
        case PressPTT() | PressFallback():
            return _on_press(state, now, target_hwnd=event.target_hwnd, toggle=False)
        case PressToggle():
            return _on_press(state, now, target_hwnd=event.target_hwnd, toggle=True)
        case ReleasePTT() | ReleaseFallback():
            return _on_release_ptt(state, now)
        case ReleaseToggle():
            return state, ()
        case CaptureStarted():
            return _on_capture_started(state, now)

        case TranscribeOk():
            if state.phase is not Phase.Finalizing or state.active is None:
                return state, ()
            active = state.active
            if not active.deliver:
                # Max hold, or dictation switched off mid-recording. The text still
                # reaches the user through the clipboard and the history — it just
                # never lands in a window on its own. Status is `withheld` (spec §10):
                # this is deliberate non-delivery, not a paste that failed.
                return _terminal(
                    state,
                    (
                        WriteClipboard(active.seq),
                        WriteHistory(active.seq, DeliveryStatus.WITHHELD),
                        PlayCue(Cue.CLIPBOARD),
                    ),
                )
            return replace(state, phase=Phase.Delivering), (
                WriteClipboard(active.seq),
                Insert(active.seq),
            )

        case InsertOk():
            if state.phase is not Phase.Delivering or state.active is None:
                return state, ()
            return _terminal(state, (WriteHistory(state.active.seq, DeliveryStatus.INSERTED),))

        case InsertFailed():
            if state.phase is not Phase.Delivering or state.active is None:
                return state, ()
            # The clipboard cue is the one channel that survives focus assist, a
            # disabled overlay and a hidden tray icon (spec §5).
            return _terminal(
                state,
                (
                    WriteHistory(state.active.seq, DeliveryStatus.LEFT_ON_CLIPBOARD),
                    PlayCue(Cue.CLIPBOARD),
                    Notify(event.code),
                ),
            )

        case NetworkError():
            if state.phase is not Phase.Finalizing or state.active is None:
                return state, ()
            return _terminal(
                state,
                (
                    WriteHistory(state.active.seq, DeliveryStatus.PENDING_RETRY),
                    EnqueueRetry(state.active.seq),
                ),
            )

        case ClipEmpty():
            if state.phase is not Phase.Finalizing or state.active is None:
                return state, ()
            return _terminal(state, (WriteHistory(state.active.seq, DeliveryStatus.EMPTY),))

        case HistoryWriteFailed():
            if state.phase in (Phase.Recording, Phase.Finalizing):
                return state, (PlayCue(Cue.WARN),)  # carry on: the clipboard path is intact
            return state, ()

        case EscPressed():
            return state, ()  # passes through to the application, always

        case DoubleEsc():
            return _on_double_esc(state)

        case MaxHoldReached():
            if state.phase is not Phase.Recording:
                return state, ()
            return _finalize(state, now, deliver=False)

        case MicError():
            match state.phase:
                case Phase.Idle | Phase.Initialized:
                    return _enter_failed(state, event.code)
                case Phase.Recording:
                    return _finalize(state, now, deliver=True)  # keep what was captured
            return state, ()

        case FailedTimeout():
            if state.phase is not Phase.Failed:
                return state, ()
            return replace(state, phase=Phase.Idle, active=None), (SetSuppression(True),)

        case HookDied():
            return _on_hook_died(state, now)

        case Suspend():
            return _on_suspend(state, now)

        case SetDictationEnabled():
            return _on_set_enabled(state, event, now)

        case Exit():
            return _on_exit(state, now)

    return state, ()
