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
    """A press that reached the machine as "press" must reach it as "release"
    too, even if the code was unbound between the edges (an M3 capture rebind
    is the live path) — else the machine stays Recording with the button up,
    hot mic to the fuse (cue-review, veha 3). BOTH the suppression and the
    event pair with what the press did, never the current bound set."""
    edges = EdgeLogic()
    edges.on_edge(PTT, pressed=True, now_ms=0, snapshot=ARMED)
    unbound = HookSnapshot(bound=frozenset(), suppress=True, recording=False)
    up = edges.on_edge(PTT, pressed=False, now_ms=100, snapshot=unbound)
    assert up.suppress, "the press was swallowed; its release must be too"
    assert up.event == "release", "the press reported, so its release must — or hot mic"


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


def test_esc_window_survives_the_49_day_tick_wrap() -> None:
    """now_ms is a DWORD tick. Across the wrap a raw subtraction goes hugely
    negative and would read as inside the window — a phantom double Esc once
    every seven weeks of uptime (review round 1)."""
    edges = EdgeLogic()
    just_before_wrap = 0xFFFF_FF00
    edges.on_edge(CODE_ESC, pressed=True, now_ms=just_before_wrap, snapshot=RECORDING)
    edges.on_edge(CODE_ESC, pressed=False, now_ms=just_before_wrap + 40, snapshot=RECORDING)

    after_wrap = 10_000  # far outside 400 ms even measured across the wrap
    late = edges.on_edge(CODE_ESC, pressed=True, now_ms=after_wrap, snapshot=RECORDING)
    assert late.event == "esc", "an Esc weeks later is a single Esc, not a pair"

    # And a genuine pair that straddles the wrap still coalesces.
    edges2 = EdgeLogic()
    edges2.on_edge(CODE_ESC, pressed=True, now_ms=0xFFFF_FFF0, snapshot=RECORDING)
    edges2.on_edge(CODE_ESC, pressed=False, now_ms=0xFFFF_FFF8, snapshot=RECORDING)
    second = edges2.on_edge(CODE_ESC, pressed=True, now_ms=100, snapshot=RECORDING)
    assert second.event == "double_esc", "116 ms apart, wrap or no wrap"


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


# --------------------------------------------------------------------------- #
# M3: capture mode (spec §12/§14) and the Ctrl+Alt chords (spec §2, §9)
# --------------------------------------------------------------------------- #

CAPTURING = HookSnapshot(
    bound=frozenset({PTT}), suppress=True, recording=False, capture=True
)
CHORDED = HookSnapshot(
    bound=frozenset({PTT}), suppress=True, recording=False,
    chords=frozenset({0x5A, 0x58}),
)


def test_capture_swallows_and_reports_any_press_and_its_release() -> None:
    edges = EdgeLogic()
    down = edges.on_edge(4100, pressed=True, now_ms=0, snapshot=CAPTURING)
    assert down.suppress and down.event == "capture" and down.code == 4100
    # Capture ended before the release — pairing still swallows it.
    up = edges.on_edge(4100, pressed=False, now_ms=50, snapshot=ARMED)
    assert up.suppress, "a swallowed press must swallow its release, capture or not"


def test_capture_swallows_bare_modifiers_silently() -> None:
    edges = EdgeLogic()
    for vk in (0x11, 0xA4, 0x10, 0x5B):  # Ctrl, RAlt, Shift, LWin
        decision = edges.on_edge(vk, pressed=True, now_ms=0, snapshot=CAPTURING)
        assert decision.suppress, f"modifier {vk:#x} leaked through capture"
        assert decision.event is None, "a bare modifier must never BE the capture"


def test_capture_esc_cancels_and_is_swallowed_with_its_release() -> None:
    edges = EdgeLogic()
    down = edges.on_edge(CODE_ESC, pressed=True, now_ms=0, snapshot=CAPTURING)
    assert down.suppress and down.event == "capture_cancel"
    up = edges.on_edge(CODE_ESC, pressed=False, now_ms=30, snapshot=ARMED)
    assert up.suppress and up.event is None


def test_capture_does_not_hijack_the_esc_double_tap_bookkeeping() -> None:
    """An Esc spent on cancelling capture must not arm the 400 ms window: the
    next ordinary Esc is a first press, not a phantom double."""
    edges = EdgeLogic()
    edges.on_edge(CODE_ESC, pressed=True, now_ms=0, snapshot=CAPTURING)
    edges.on_edge(CODE_ESC, pressed=False, now_ms=30, snapshot=ARMED)
    again = edges.on_edge(CODE_ESC, pressed=True, now_ms=100, snapshot=RECORDING)
    assert again.event == "esc", "the capture cancel leaked into the double window"


def test_chord_ready_press_fires_and_only_the_final_key_is_swallowed() -> None:
    edges = EdgeLogic()
    down = edges.on_edge(0x5A, pressed=True, now_ms=0, snapshot=CHORDED, chord_ready=True)
    assert down.suppress and down.event == "chord" and down.code == 0x5A
    up = edges.on_edge(0x5A, pressed=False, now_ms=40, snapshot=CHORDED)
    assert up.suppress and up.event is None, "the release pairs with its press"


def test_chord_key_without_modifiers_passes_untouched() -> None:
    edges = EdgeLogic()
    down = edges.on_edge(0x5A, pressed=True, now_ms=0, snapshot=CHORDED, chord_ready=False)
    assert not down.suppress and down.event is None, "plain Z belongs to the app"
    up = edges.on_edge(0x5A, pressed=False, now_ms=40, snapshot=CHORDED)
    assert not up.suppress


def test_chord_autorepeat_fires_no_second_event() -> None:
    """A held Ctrl+Alt+Z auto-repeats at the keyboard rate; only the first
    press may act, or ten pastes arrive from one gesture."""
    edges = EdgeLogic()
    first = edges.on_edge(0x5A, pressed=True, now_ms=0, snapshot=CHORDED, chord_ready=True)
    assert first.event == "chord"
    repeat = edges.on_edge(0x5A, pressed=True, now_ms=30, snapshot=CHORDED, chord_ready=True)
    assert repeat.event is None and repeat.suppress, "auto-repeat must stay swallowed"


def test_capture_takes_priority_over_a_bound_press() -> None:
    """During capture even the current PTT button is a candidate, not a
    dictation start."""
    edges = EdgeLogic()
    down = edges.on_edge(PTT, pressed=True, now_ms=0, snapshot=CAPTURING)
    assert down.event == "capture" and down.suppress


def test_capturing_a_new_key_mid_hold_does_not_finalise_the_held_dictation() -> None:
    """cue-review, medium: a live rebind during a held-PTT dictation. The tap
    of the just-captured key must NOT emit ReleasePTT — the release event pairs
    with what the press meant, not with the current bound set. And the real
    release of the old, now-unbound key must still emit its release, or the
    machine sits Recording with the button up (hot mic to the fuse)."""
    edges = EdgeLogic()
    old, new = PTT, 4100
    armed_old = HookSnapshot(bound=frozenset({old}), suppress=True, recording=True)
    # 1. hold the old PTT — a real dictation press.
    assert edges.on_edge(old, pressed=True, now_ms=0, snapshot=armed_old).event == "press"
    # 2. capture arms; tap the new key — swallowed as a capture, no machine event.
    capturing = HookSnapshot(bound=frozenset({old}), suppress=True, recording=True, capture=True)
    tap = edges.on_edge(new, pressed=True, now_ms=10, snapshot=capturing)
    assert tap.event == "capture"
    # 3. binding applied → bound is now {new}, capture off.
    armed_new = HookSnapshot(bound=frozenset({new}), suppress=True, recording=True)
    up_new = edges.on_edge(new, pressed=False, now_ms=20, snapshot=armed_new)
    assert up_new.event is None, "the captured key's release must not finalise the dictation"
    # 4. the user finally releases the OLD key they were holding → THAT ends it.
    up_old = edges.on_edge(old, pressed=False, now_ms=30, snapshot=armed_new)
    assert up_old.event == "release", "the held key's release still reaches the machine"
