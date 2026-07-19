"""The UI's end of the named-pipe channel (harness §3).

The trust burden sits here, not in the handshake: ``hello`` proves nothing,
so after connecting the client asks Windows who the server actually is —
``GetNamedPipeServerProcessId``, then the process image path — and compares
it with the executable it expects. A squatter can occupy the name (it cannot
create it while the core lives, thanks to ``FILE_FLAG_FIRST_PIPE_INSTANCE``,
but it can stand there when the core is down) and speak the protocol; it
cannot be running from our image path.

The connection is also opened with ``SECURITY_IDENTIFICATION`` quality of
service: whatever answers the name may identify the caller but never
impersonate it — the default for pipe clients is full impersonation, which
hands a fake server the user's security context.

The handle is opened ``FILE_FLAG_OVERLAPPED`` for the same measured reason
as the server (see ``core/ipc/channel.py``): the UI thread's ``send`` must
not queue behind the reader thread's blocked ``ReadFile``.

No wx imports here: the reader thread delivers messages to a plain callback,
and the windows marshal to the GUI thread themselves (``wx.CallAfter``).
This keeps the module testable headless, against a real server, in pytest.
"""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Callable
from typing import Any, Final

import pywintypes
import win32api
import win32con
import win32event
import win32file
import win32pipe
import win32process

from ... import __version__
from ...core.ipc.channel import OverlappedPipe, PipeClosed, PipeTimeout
from ...core.ipc.protocol import (
    FrameDecoder,
    FrameError,
    encode_frame,
    hello,
)

__all__ = ["IpcClient", "CoreNotRunning", "ServerUntrusted", "ProtocolMismatch"]

log = logging.getLogger("billytalk.ui.ipc")

# CreateFile dwFlagsAndAttributes bits (not exposed by win32con): request
# explicit SQOS with identification-only level, see the module docstring.
_SECURITY_SQOS_PRESENT: Final = 0x0010_0000
_SECURITY_IDENTIFICATION: Final = 0x0001_0000

HANDSHAKE_TIMEOUT_MS: Final = 5_000


class CoreNotRunning(RuntimeError):
    """No pipe under the expected name: the core is down (or never started).
    The UI shows «остановлен» and offers a restart (harness §2)."""


class ServerUntrusted(RuntimeError):
    """Something serves our name from a foreign image path. Do not talk to it."""


class ProtocolMismatch(RuntimeError):
    """Core and UI are from different builds (an update over a live process).
    The UI shows «нужно перезапустить приложение» (harness §3)."""


class IpcClient:
    """Connects, verifies, handshakes, then pumps messages to ``on_message``
    from a reader thread until the channel dies; ``on_disconnect`` fires once
    on the way out (never during :meth:`close`)."""

    def __init__(
        self,
        name: str,
        *,
        on_message: Callable[[dict[str, Any]], None],
        on_disconnect: Callable[[], None] | None = None,
        expected_image: str | None = None,
        app_version: str = __version__,
    ) -> None:
        self._name = name
        self._on_message = on_message
        self._on_disconnect = on_disconnect
        self._expected_image = expected_image
        self._app_version = app_version
        self._handle: Any = None
        self._channel: OverlappedPipe | None = None
        self._stop_win32 = win32event.CreateEvent(None, True, False, None)  # manual-reset
        self._decoder = FrameDecoder()
        self._closing = threading.Event()
        self._reader: threading.Thread | None = None
        self.core_version: str | None = None

    # ------------------------------------------------------------------ #

    def connect(self, *, timeout_ms: int = 5_000) -> None:
        self._handle = self._open_pipe(timeout_ms)
        self._channel = OverlappedPipe(self._handle, self._stop_win32)
        try:
            if self._expected_image is not None:
                self._verify_server_image(self._expected_image)
            self._channel.write(encode_frame(hello(app_version=self._app_version)))
            ack = self._await_ack()
        except BaseException:
            self._drop_handle()
            raise
        self.core_version = ack.get("core_version")
        self._reader = threading.Thread(
            target=self._read_loop, name="billytalk-ui-ipc", daemon=True
        )
        self._reader.start()

    def send(self, message: dict[str, Any]) -> None:
        channel = self._channel
        if channel is None:
            raise CoreNotRunning(self._name)
        try:
            channel.write(encode_frame(message))
        except (PipeClosed, PipeTimeout) as exc:
            raise CoreNotRunning(self._name) from exc

    def close(self) -> None:
        """Quiet teardown: no ``on_disconnect`` for a close we asked for."""
        self._closing.set()
        win32event.SetEvent(self._stop_win32)
        if self._reader is not None:
            self._reader.join(timeout=2.0)
            self._reader = None
        self._drop_handle()

    # ------------------------------------------------------------------ #

    def _open_pipe(self, timeout_ms: int) -> Any:
        """Retry within the deadline: the core may be mid-start, or the
        previous UI's slot may still be closing (single-instance pipe)."""
        waited = 0
        while True:
            try:
                return win32file.CreateFile(
                    self._name,
                    win32con.GENERIC_READ | win32con.GENERIC_WRITE,
                    0, None, win32con.OPEN_EXISTING,
                    win32con.FILE_FLAG_OVERLAPPED
                    | _SECURITY_SQOS_PRESENT | _SECURITY_IDENTIFICATION,
                    None,
                )
            except pywintypes.error as exc:
                if exc.winerror == 231:  # ERROR_PIPE_BUSY: instance not free yet
                    try:
                        win32pipe.WaitNamedPipe(self._name, 100)
                    except pywintypes.error:
                        pass  # timed out waiting; fall through to the deadline
                    waited += 100
                elif exc.winerror == 2:  # ERROR_FILE_NOT_FOUND: no core (yet)
                    win32api.Sleep(50)
                    waited += 50
                else:
                    raise
                if waited >= timeout_ms:
                    raise CoreNotRunning(self._name) from None

    def _verify_server_image(self, expected: str) -> None:
        pid = win32pipe.GetNamedPipeServerProcessId(self._handle)
        process = win32api.OpenProcess(
            win32con.PROCESS_QUERY_INFORMATION | win32con.PROCESS_VM_READ, False, pid
        )
        try:
            image = win32process.GetModuleFileNameEx(process, 0)
        finally:
            process.Close()
        if os.path.normcase(os.path.realpath(image)) != os.path.normcase(os.path.realpath(expected)):
            raise ServerUntrusted(f"pipe served by {image}, expected {expected}")

    def _await_ack(self) -> dict[str, Any]:
        assert self._channel is not None
        try:
            while True:
                chunk = self._channel.read(timeout_ms=HANDSHAKE_TIMEOUT_MS)
                messages = self._decoder.feed(chunk)
                if not messages:
                    continue
                first = messages[0]
                if first.get("type") == "hello_ack":
                    # Frames right behind the ack (an eager state_changed)
                    # belong to the reader's callback.
                    for extra in messages[1:]:
                        self._on_message(extra)
                    return first
                if first.get("type") == "error" and first.get("code") == "protocol_mismatch":
                    raise ProtocolMismatch("core speaks a different protocol; restart the app")
                raise ProtocolMismatch(f"unexpected first frame type {first.get('type')!r}")
        except PipeTimeout:
            raise ProtocolMismatch("no hello_ack within the handshake deadline") from None
        except PipeClosed as exc:
            raise CoreNotRunning(self._name) from exc

    def _read_loop(self) -> None:
        channel = self._channel
        assert channel is not None
        try:
            while not self._closing.is_set():
                chunk = channel.read()
                for message in self._decoder.feed(chunk):
                    self._on_message(message)
        except PipeClosed:
            pass  # the core went away (or close() raised the stop event)
        except FrameError as exc:
            log.warning("server framing violation: %s", exc)
        finally:
            if not self._closing.is_set():
                self._drop_handle()
                if self._on_disconnect is not None:
                    self._on_disconnect()

    def _drop_handle(self) -> None:
        channel, self._channel = self._channel, None
        if channel is not None:
            channel.cancel_all()
        handle, self._handle = self._handle, None
        if handle is not None:
            try:
                handle.Close()
            except pywintypes.error:
                pass
