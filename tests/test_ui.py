"""ui/__main__: the interface process connects to a real core and exits clean.

A live subprocess against a real ``IpcServer`` — the scaffold's whole job in
cycle-2 milestone 2 is this lifecycle: launch, connect, and end when the core
goes. The subprocess's ``LOCALAPPDATA`` is redirected to a tmp dir so its
``ui.log`` never touches the real app data.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from billytalk.core.ipc.protocol import reply
from billytalk.core.ipc.server import IpcServer
from billytalk.core.ui_launch import UiHost

_REPO_ROOT = Path(__file__).resolve().parents[1]
DEADLINE_S = 15.0


def _test_name() -> str:
    return f"\\\\.\\pipe\\billytalk-uitest-{uuid4().hex}"


def _echo_handler(message: dict[str, Any]) -> dict[str, Any] | None:
    rid = message.get("id")
    return reply(rid, {"echo": message["type"]}) if isinstance(rid, int) else None


def _spawn_ui(name: str, localappdata: Path) -> subprocess.Popen:
    env = dict(os.environ)
    env["LOCALAPPDATA"] = str(localappdata)
    # "-" skips image verification: the scaffold's lifecycle is the subject here;
    # the image check runs against the real server in test_ipc. cwd = repo root so
    # `-m billytalk.ui` resolves the package.
    return subprocess.Popen(
        [sys.executable, "-m", "billytalk.ui", name, "-"],
        cwd=str(_REPO_ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def test_ui_process_connects_then_exits_when_the_core_goes(tmp_path: Path) -> None:
    name = _test_name()
    connected = threading.Event()
    server = IpcServer(name, handler=_echo_handler, on_connect=connected.set)
    server.start()
    proc = _spawn_ui(name, tmp_path)
    try:
        if not connected.wait(DEADLINE_S):
            proc.kill()
            _, err = proc.communicate(timeout=5)
            pytest.fail(f"ui never connected. stderr:\n{err.decode('utf-8', 'replace')}")
        # The core goes away; the UI's reader sees the disconnect and quits.
        server.stop()
        assert proc.wait(DEADLINE_S) == 0, "ui did not exit cleanly on core loss"
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(5)
        server.stop()


# --------------------------------------------------------------------------- #
# UiHost: launch once, relaunch throttled to once per 30 s (harness §2)
# --------------------------------------------------------------------------- #

class _FakeProc:
    def __init__(self) -> None:
        self._alive = True

    def poll(self) -> int | None:
        return None if self._alive else 0

    def die(self) -> None:
        self._alive = False


def test_ui_host_launches_once_while_alive_and_throttles_relaunch() -> None:
    launched: list[_FakeProc] = []
    clock = {"t": 1000.0}

    def spawn(argv: Any) -> _FakeProc:
        proc = _FakeProc()
        launched.append(proc)
        return proc

    host = UiHost(["x"], spawn=spawn, now=lambda: clock["t"], min_relaunch_s=30.0)

    host.ensure_running()
    assert len(launched) == 1
    host.ensure_running()
    assert len(launched) == 1, "a live interface must not be relaunched"

    launched[0].die()
    clock["t"] += 10  # 10 s < the 30 s budget
    host.ensure_running()
    assert len(launched) == 1, "relaunch within 30 s must be throttled"

    clock["t"] += 25  # 35 s since the launch
    host.ensure_running()
    assert len(launched) == 2, "past the budget, a dead interface relaunches"
