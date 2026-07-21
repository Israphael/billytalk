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
        (WM_QUERYENDSESSION, 0, Action.QUERY_END_SESSION),  # save under a block
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
        self.blocks = 0
        self.unblocks = 0
        self._stream_open = stream_open
        self.handlers: dict[int, Any] = {}
        self.sys = SystemEvents(
            post_event=self.events.append,
            post_job=lambda fn: fn(),  # inline
            stream_open=lambda: self._stream_open,
            reload_devices=self._reload,
            reinstall_hook=self._reinstall,
            reset_watchdog=self._reset,
            block_shutdown=self._block,
            unblock_shutdown=self._unblock,
        )
        self.sys.register(self)

    def _block(self) -> None:
        self.blocks += 1

    def _unblock(self) -> None:
        self.unblocks += 1

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


def test_query_end_session_blocks_saves_then_allows() -> None:
    bench = Bench()
    result = bench.fire(WM_QUERYENDSESSION, 0)
    assert bench.blocks == 1, "the shutdown is held off while we save (spec §3)"
    assert bench.events == [Suspend()], "everything in flight is finalised"
    assert bench.unblocks == 1, "and the block is released once saved"
    assert result == 1, "TRUE — the shutdown is allowed to proceed"


def test_query_end_session_releases_the_block_even_if_the_save_hangs() -> None:
    """A driver that never confirms must not forfeit the whole logoff: the
    wait is bounded, the block always released."""
    events: list[Any] = []
    blocks = {"n": 0}
    sysev = SystemEvents(
        post_event=events.append,
        post_job=lambda fn: None,  # the marker never runs → save never confirms
        stream_open=lambda: False,
        reload_devices=lambda: None,
        reinstall_hook=lambda: None,
        reset_watchdog=lambda: None,
        block_shutdown=lambda: blocks.__setitem__("n", blocks["n"] + 1),
        unblock_shutdown=lambda: blocks.__setitem__("n", blocks["n"] - 1),
        save_timeout_s=0.05,
    )
    handlers: dict[int, Any] = {}
    sysev.register(type("W", (), {"on": lambda self, m, h: handlers.__setitem__(m, h)})())
    handlers[WM_QUERYENDSESSION](0, 0)
    assert blocks["n"] == 0, "the block is released after the timeout, not leaked"
    assert events == [Suspend()]


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


def test_device_change_burst_collapses_to_one_reload() -> None:
    """Windows fires several DBT_DEVNODES_CHANGED per plug; with inline
    post_job each would reload — but while a job is queued, further changes
    must not pile on (M4 review, low). Here post_job defers so the burst
    lands before the single job runs."""
    events: list[Any] = []
    jobs: list[Any] = []
    reloads = {"n": 0}
    sysev = SystemEvents(
        post_event=events.append,
        post_job=jobs.append,  # deferred, like the real driver queue
        stream_open=lambda: False,
        reload_devices=lambda: reloads.__setitem__("n", reloads["n"] + 1),
        reinstall_hook=lambda: None,
        reset_watchdog=lambda: None,
    )
    handlers: dict[int, Any] = {}
    sysev.register(type("W", (), {"on": lambda self, m, h: handlers.__setitem__(m, h)})())
    for _ in range(5):  # one plug, five messages
        handlers[WM_DEVICECHANGE](DBT_DEVNODES_CHANGED, 0)
    assert len(jobs) == 1, "five messages queued exactly one reload job"
    jobs[0]()
    assert reloads["n"] == 1
    # a change after the job ran queues a fresh one
    handlers[WM_DEVICECHANGE](DBT_DEVNODES_CHANGED, 0)
    assert len(jobs) == 2


def test_a_reload_failure_never_escapes_the_job() -> None:
    bench = Bench(stream_open=False)

    def boom() -> None:
        raise RuntimeError("PortAudio exploded")

    bench.sys._reload_devices = boom  # type: ignore[assignment]
    bench.fire(WM_DEVICECHANGE, DBT_DEVNODES_CHANGED)  # must not raise
