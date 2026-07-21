"""``core/system_events.py``: the pure message classification (spec §3/§5) and
the SystemEvents bindings with every effect faked — the window thread posts,
the driver-thread jobs run inline, the reload is gated on the stream.
"""

from __future__ import annotations

from typing import Any

import pytest

from billytalk.core.machine.events import Exit, Suspend
from billytalk.core.system_events import (
    DBT_DEVICEARRIVAL,
    DBT_DEVNODES_CHANGED,
    PBT_APMRESUMEAUTOMATIC,
    PBT_APMSUSPEND,
    WM_DEVICECHANGE,
    WM_ENDSESSION,
    WM_POWERBROADCAST,
    WM_QUERYENDSESSION,
    Action,
    SystemEvents,
    classify,
)


# --------------------------------------------------------------------------- #
# classify — pure
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "message, wparam, expected",
    [
        (WM_POWERBROADCAST, PBT_APMSUSPEND, Action.SUSPEND),
        (WM_POWERBROADCAST, PBT_APMRESUMEAUTOMATIC, Action.RESUME),
        (WM_POWERBROADCAST, 0x0009, Action.NONE),  # PBT_POWERSETTINGCHANGE
        (WM_QUERYENDSESSION, 0, Action.SUSPEND),    # save now, allow shutdown
        (WM_ENDSESSION, 1, Action.END_SESSION),     # committing
        (WM_ENDSESSION, 0, Action.NONE),            # a cancelled end-session
        (WM_DEVICECHANGE, DBT_DEVNODES_CHANGED, Action.DEVICE_CHANGE),
        (WM_DEVICECHANGE, DBT_DEVICEARRIVAL, Action.DEVICE_CHANGE),
        (WM_DEVICECHANGE, 0x0001, Action.NONE),     # DBT_DEVICEQUERYREMOVE etc.
        (0x0113, 0, Action.NONE),                   # WM_TIMER — not ours
    ],
)
def test_classify(message: int, wparam: int, expected: Action) -> None:
    assert classify(message, wparam) is expected


# --------------------------------------------------------------------------- #
# SystemEvents — the bindings
# --------------------------------------------------------------------------- #


class Bench:
    def __init__(self, *, stream_open: bool = False) -> None:
        self.events: list[Any] = []
        self.reloads = 0
        self.reinstalls = 0
        self.watchdog_resets = 0
        self._stream_open = stream_open
        self.handlers: dict[int, Any] = {}
        self.sys = SystemEvents(
            post_event=self.events.append,
            post_job=lambda fn: fn(),  # inline
            stream_open=lambda: self._stream_open,
            reload_devices=self._reload,
            reinstall_hook=self._reinstall,
            reset_watchdog=self._reset,
        )
        self.sys.register(self)

    # HiddenWindow.on stand-in
    def on(self, message: int, handler: Any) -> None:
        self.handlers[message] = handler

    def fire(self, message: int, wparam: int) -> Any:
        return self.handlers[message](wparam, 0)

    def _reload(self) -> None:
        self.reloads += 1

    def _reinstall(self) -> None:
        self.reinstalls += 1

    def _reset(self) -> None:
        self.watchdog_resets += 1


def test_suspend_posts_the_machine_event() -> None:
    bench = Bench()
    bench.fire(WM_POWERBROADCAST, PBT_APMSUSPEND)
    assert bench.events == [Suspend()]


def test_query_end_session_saves_and_returns_true() -> None:
    bench = Bench()
    result = bench.fire(WM_QUERYENDSESSION, 0)
    assert bench.events == [Suspend()], "everything in flight is saved (spec §3)"
    assert result == 1, "TRUE — the shutdown is allowed to proceed"


def test_committing_end_session_exits() -> None:
    bench = Bench()
    bench.fire(WM_ENDSESSION, 1)
    assert bench.events == [Exit()]


def test_resume_re_enumerates_reinstalls_and_resets_the_watchdog() -> None:
    bench = Bench()
    bench.fire(WM_POWERBROADCAST, PBT_APMRESUMEAUTOMATIC)
    assert bench.reinstalls == 1
    assert bench.watchdog_resets == 1
    assert bench.reloads == 1, "resume re-enumerates devices too"


def test_device_change_reloads_when_no_stream_is_open() -> None:
    bench = Bench(stream_open=False)
    result = bench.fire(WM_DEVICECHANGE, DBT_DEVNODES_CHANGED)
    assert bench.reloads == 1
    assert result == 1


def test_device_change_mid_recording_defers_the_reload() -> None:
    """Spec §5: never reload with a stream open — the running dictation
    finalises on its own device, and the reload waits for idle."""
    bench = Bench(stream_open=True)
    bench.fire(WM_DEVICECHANGE, DBT_DEVNODES_CHANGED)
    assert bench.reloads == 0, "a reload under a live stream would unload PortAudio"
    # still recording: on_idle must not fire it yet
    bench.sys.on_idle()
    assert bench.reloads == 0
    # the stream closes; the next publish observes idle and the reload runs
    bench._stream_open = False
    bench.sys.on_idle()
    assert bench.reloads == 1


def test_on_idle_without_a_pending_reload_does_nothing() -> None:
    bench = Bench(stream_open=False)
    bench.sys.on_idle()
    assert bench.reloads == 0


def test_a_reload_failure_never_escapes_the_job() -> None:
    bench = Bench(stream_open=False)

    def boom() -> None:
        raise RuntimeError("PortAudio exploded")

    bench.sys._reload_devices = boom  # type: ignore[assignment]
    bench.fire(WM_DEVICECHANGE, DBT_DEVNODES_CHANGED)  # must not raise
