"""``hooks/``: the edge rules without Windows, the watchdog logic with fakes,
and two live checks that install a real ``WH_MOUSE_LL`` hook for a second.

The pairing rule gets the heaviest coverage because it outranks every other
rule (spec §2): a release is suppressed iff its paired press was suppressed,
no matter what changed in between. A press swallowed with its release delivered
leaves the target application holding a stuck button.
"""

from __future__ import annotations

import ctypes
import threading
import time

from billytalk.core.hooks.edges import EdgeDecision, EdgeLogic, HookSnapshot
from billytalk.core.hooks.keycodes import CODE_ESC
from billytalk.core.hooks.watchdog import HookWatchdog, signed_tick_delta

PTT = 4099

ARMED = HookSnapshot(bound=frozenset({PTT}), suppress=True, recording=False)
RECORDING = HookSnapshot(bound=frozenset({PTT}), suppress=True, recording=True)
RELEASED_SUPPRESSION = HookSnapshot(bound=frozenset({PTT}), suppress=False, recording=False)


# --------------------------------------------------------------------------- #
# pairing — the rule above every rule
# --------------------------------------------------------------------------- #


def test_release_suppressed_iff_press_was_suppressed() -> None:
    edges = EdgeLogic()
    down = edges.on_edge(PTT, pressed=True, now_ms=0, snapshot=ARMED)
    assert down.suppress and down.event == "press"

    # Suppression is dropped between the edges (Failed, tray-off) — the release
    # still follows its press, or the target app sees an orphaned release.
    up = edges.on_edge(PTT, pressed=False, now_ms=500, snapshot=RELEASED_SUPPRESSION)
    assert up.suppress, "the pairing rule outranks the state change"
    assert up.event == "release"


def test_unsuppressed_press_keeps_its_release_unsuppressed() -> None:
    edges = EdgeLogic()
    down = edges.on_edge(PTT, pressed=True, now_ms=0, snapshot=RELEASED_SUPPRESSION)
    assert not down.suppress, "suppression is off: the button is Back again"

    up = edges.on_edge(PTT, pressed=False, now_ms=500, snapshot=ARMED)
    assert not up.suppress, "suppressing only the release would stick the button"


def test_release_after_unbinding_still_follows_pairing() -> None:
    edges = EdgeLogic()
    edges.on_edge(PTT, pressed=True, now_ms=0, snapshot=ARMED)
    unbound = HookSnapshot(bound=frozenset(), suppress=True, recording=False)
    up = edges.on_edge(PTT, pressed=False, now_ms=100, snapshot=unbound)
    assert up.suppress, "the press was swallowed; its release must be too"
    assert up.event is None, "but an unbound code no longer reports"


def test_tracks_reflects_pending_edge_state() -> None:
    """The ctypes gate routes an edge to EdgeLogic when the code is bound OR
    tracked — gating on the current bound set alone let a mid-hold rebinding
    deliver an orphaned release to the app (review round 1)."""
    edges = EdgeLogic()
    assert not edges.tracks(PTT)

    edges.on_edge(PTT, pressed=True, now_ms=0, snapshot=ARMED)
    assert edges.tracks(PTT), "a swallowed press is pending state"

    edges.on_edge(PTT, pressed=False, now_ms=50, snapshot=ARMED)
    assert not edges.tracks(PTT), "the pair closed; nothing pending"

    foreign = EdgeLogic(already_down=frozenset({PTT}))
    assert foreign.tracks(PTT), "a foreign mark is pending state too"


# --------------------------------------------------------------------------- #
# foreign keys and auto-repeat
# --------------------------------------------------------------------------- #


def test_key_held_at_install_is_foreign() -> None:
    """Spec §2: already down when the hook arrives — its release is not ours to
    swallow and not ours to report."""
    edges = EdgeLogic(already_down=frozenset({PTT}))
    up = edges.on_edge(PTT, pressed=False, now_ms=10, snapshot=ARMED)
    assert not up.suppress and up.event is None

    # The next press is a normal press: the foreign mark died with the release.
    down = edges.on_edge(PTT, pressed=True, now_ms=20, snapshot=ARMED)
    assert down.suppress and down.event == "press"


def test_stale_foreign_mark_cleared_by_a_press() -> None:
    edges = EdgeLogic(already_down=frozenset({PTT}))
    down = edges.on_edge(PTT, pressed=True, now_ms=10, snapshot=ARMED)
    assert down.event == "press", "a press while marked foreign means we missed the release"


def test_autorepeat_never_reports_a_second_press() -> None:
    """A held hotkey repeats at ~30 Hz; without the squelch the recording would
    restart thirty times a second (research/02)."""
    edges = EdgeLogic()
    edges.on_edge(PTT, pressed=True, now_ms=0, snapshot=ARMED)
    for tick in range(30, 300, 30):
        repeat = edges.on_edge(PTT, pressed=True, now_ms=tick, snapshot=ARMED)
        assert repeat.event is None
        assert repeat.suppress, "repeats of a swallowed press are swallowed too"


def test_autorepeat_of_a_passed_press_passes() -> None:
    edges = EdgeLogic()
    edges.on_edge(PTT, pressed=True, now_ms=0, snapshot=RELEASED_SUPPRESSION)
    repeat = edges.on_edge(PTT, pressed=True, now_ms=30, snapshot=RELEASED_SUPPRESSION)
    assert repeat.event is None and not repeat.suppress


# --------------------------------------------------------------------------- #
# Esc (spec §2: single passes, double within 400 ms cancels)
# --------------------------------------------------------------------------- #


def test_single_esc_passes_through_with_its_event() -> None:
    edges = EdgeLogic()
    down = edges.on_edge(CODE_ESC, pressed=True, now_ms=1000, snapshot=RECORDING)
    assert not down.suppress, "the first Esc belongs to the application"
    assert down.event == "esc"


def test_double_esc_within_the_window_coalesces() -> None:
    edges = EdgeLogic()
    edges.on_edge(CODE_ESC, pressed=True, now_ms=1000, snapshot=RECORDING)
    edges.on_edge(CODE_ESC, pressed=False, now_ms=1050, snapshot=RECORDING)
    second = edges.on_edge(CODE_ESC, pressed=True, now_ms=1399, snapshot=RECORDING)
    assert second.event == "double_esc"
    assert second.suppress, "while recording, the second Esc is ours"
    up = edges.on_edge(CODE_ESC, pressed=False, now_ms=1450, snapshot=RECORDING)
    assert up.suppress, "pairing: its release goes with it"


def test_double_esc_outside_recording_passes_through() -> None:
    edges = EdgeLogic()
    edges.on_edge(CODE_ESC, pressed=True, now_ms=1000, snapshot=ARMED)
    edges.on_edge(CODE_ESC, pressed=False, now_ms=1050, snapshot=ARMED)
    second = edges.on_edge(CODE_ESC, pressed=True, now_ms=1300, snapshot=ARMED)
    assert second.event == "double_esc", "the machine decides what to do with it"
    assert not second.suppress, "Esc only during dictation — nothing to cancel here"


def test_esc_after_the_window_is_single_again() -> None:
    edges = EdgeLogic()
    edges.on_edge(CODE_ESC, pressed=True, now_ms=1000, snapshot=RECORDING)
    edges.on_edge(CODE_ESC, pressed=False, now_ms=1050, snapshot=RECORDING)
    late = edges.on_edge(CODE_ESC, pressed=True, now_ms=1401, snapshot=RECORDING)
    assert late.event == "esc"
    assert not late.suppress


def test_a_third_esc_starts_a_new_window() -> None:
    edges = EdgeLogic()
    edges.on_edge(CODE_ESC, pressed=True, now_ms=0, snapshot=RECORDING)
    edges.on_edge(CODE_ESC, pressed=False, now_ms=30, snapshot=RECORDING)
    assert edges.on_edge(CODE_ESC, pressed=True, now_ms=100, snapshot=RECORDING).event == "double_esc"
    edges.on_edge(CODE_ESC, pressed=False, now_ms=130, snapshot=RECORDING)
    third = edges.on_edge(CODE_ESC, pressed=True, now_ms=200, snapshot=RECORDING)
    assert third.event == "esc", "a pair consumes its presses; the third starts fresh"


# --------------------------------------------------------------------------- #
# watchdog logic with fakes (the live echo is below)
# --------------------------------------------------------------------------- #


class StubHook:
    def __init__(self, last_event_tick: int) -> None:
        self.last_event_tick = last_event_tick
        self.echo_seen = threading.Event()
        self.base_resets = 0

    def note_probe_sent(self) -> None:
        self.base_resets += 1


def test_signed_tick_delta_wraps_and_signs() -> None:
    assert signed_tick_delta(1000, 400) == 600
    assert signed_tick_delta(400, 1000) == -600, "behind means 'no input', not divergence"
    assert signed_tick_delta(5, 0xFFFF_FFFF) == 6, "across the 49.7-day wrap"
    assert signed_tick_delta(0xFFFF_FFFF, 5) == -6


def test_no_divergence_sends_no_echo() -> None:
    hook = StubHook(last_event_tick=10_000)
    echoes: list[int] = []
    dog = HookWatchdog(hook, last_input=lambda: 10_150, echo=lambda: echoes.append(1))  # type: ignore[arg-type]
    assert dog.probe() is True
    assert echoes == [], "150 ms is inside the 200 ms allowance"


def test_input_behind_the_hook_is_health() -> None:
    hook = StubHook(last_event_tick=10_000)
    dog = HookWatchdog(hook, last_input=lambda: 9_000, echo=lambda: None)  # type: ignore[arg-type]
    assert dog.probe() is True, "signed comparison: no input since our last event"


def test_divergence_with_echo_reply_is_alive() -> None:
    hook = StubHook(last_event_tick=10_000)

    def echo() -> None:
        hook.echo_seen.set()  # the hook saw its own mark

    dog = HookWatchdog(hook, last_input=lambda: 11_000, echo=echo)  # type: ignore[arg-type]
    assert dog.probe() is True
    assert hook.base_resets == 1, "the base resets so the echo cannot mask a later death"


def test_divergence_with_silent_echo_is_dead() -> None:
    hook = StubHook(last_event_tick=10_000)
    dog = HookWatchdog(hook, last_input=lambda: 11_000, echo=lambda: None)  # type: ignore[arg-type]
    assert dog.probe() is False, "diverged and the probe went unseen: confirmed dead"
    assert hook.base_resets == 1


# --------------------------------------------------------------------------- #
# live: a real WH_MOUSE_LL for about a second (safe: only our own synthetic
# XBUTTON edges are involved, and they are suppressed before reaching any app)
# --------------------------------------------------------------------------- #


def _collecting_hook() -> tuple[object, list]:
    from billytalk.core.hooks.lowlevel import HookEvent, HookThread

    events: list[HookEvent] = []
    hook = HookThread(events.append, ARMED)
    return hook, events


def test_live_hook_sees_and_swallows_a_synthetic_xbutton() -> None:
    from billytalk.core.hooks.lowlevel import send_xbutton

    hook, events = _collecting_hook()
    hook.start()  # type: ignore[attr-defined]
    try:
        assert hook.wait_ready(3.0), "hook thread never became ready"  # type: ignore[attr-defined]
        assert hook.install_failed is None, hook.install_failed  # type: ignore[attr-defined]

        send_xbutton(4, pressed=True)
        send_xbutton(4, pressed=False)

        deadline = time.monotonic() + 2.0
        while len(events) < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
    finally:
        hook.stop()  # type: ignore[attr-defined]

    kinds = [(e.kind, e.code) for e in events]
    assert kinds == [("press", 4099), ("release", 4099)], (
        f"the real hook pipeline must deliver both edges, got {kinds}"
    )


def test_live_watchdog_echo_round_trip() -> None:
    """The S6 echo against the real SendInput and the real hook: divergence is
    faked, the ±1 px probe and its detection are not."""
    hook, _ = _collecting_hook()
    hook.start()  # type: ignore[attr-defined]
    try:
        assert hook.wait_ready(3.0)  # type: ignore[attr-defined]
        assert hook.install_failed is None  # type: ignore[attr-defined]

        tick = int(ctypes.WinDLL("kernel32").GetTickCount())
        hook.last_event_tick = tick - 5_000  # fabricate a 5 s divergence
        dog = HookWatchdog(hook)  # real last_input, real send_echo
        assert dog.probe() is True, "a live hook must see its own echo mark"
    finally:
        hook.stop()  # type: ignore[attr-defined]
