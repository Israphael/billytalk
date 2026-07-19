"""Overlapped pipe I/O shared by the core's server and the UI's client.

Why this file exists — measured, not guessed: on a **synchronous** pipe
handle Windows serialises I/O on the file object, so a thread blocked in
``ReadFile`` makes every ``WriteFile`` on the same handle queue behind it.
The first probe of cycle 2 caught the writer thread stalled inside a
34-byte reply while the reader sat in its blocking read — the ack before it
had gone through only because the same thread wrote it. Full-duplex traffic
on one handle therefore requires ``FILE_FLAG_OVERLAPPED`` on **both** ends;
this module is that requirement, packaged.

The stop story falls out for free and is better than the synchronous one:
every wait is ``WaitForMultipleObjects(stop_event, io_event)``, so shutdown
is an event, not a poke-connection or a handle closed under a live read.
After a cancel the pending operation is always drained with
``GetOverlappedResult`` before the buffer is reused — the kernel writes into
that buffer until the I/O truly completes, cancelled or not.
"""

from __future__ import annotations

import ctypes
import threading
from typing import Any, Final

import pywintypes
import win32event
import win32file

__all__ = ["OverlappedPipe", "PipeClosed", "PipeTimeout", "cancel_io"]

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
"""pywin32 312 wraps ``CancelIo`` (thread-bound, useless from a stop path on
another thread) but not ``CancelIoEx``; the real thing is one ctypes call."""


def cancel_io(handle: Any) -> None:
    """Abort every pending operation on ``handle``, from any thread.
    Zero return with ERROR_NOT_FOUND just means nothing was pending."""
    _kernel32.CancelIoEx(ctypes.c_void_p(int(handle)), None)

_READ_CHUNK: Final = 64 * 1024

WRITE_TIMEOUT_MS: Final = 5_000
"""A peer that cannot absorb a frame in five seconds stopped reading; by
policy that connection is dead — the writer must never wedge the process."""

_CLOSED_WINERRORS: Final = frozenset({
    109,  # ERROR_BROKEN_PIPE: the other end is gone
    232,  # ERROR_NO_DATA: pipe close in progress
    233,  # ERROR_PIPE_NOT_CONNECTED: server disconnected this instance
    995,  # ERROR_OPERATION_ABORTED: our own cancel
    6,    # ERROR_INVALID_HANDLE: closed under us during shutdown
})

_ERROR_IO_PENDING: Final = 997


class PipeClosed(Exception):
    """The channel is over — peer gone, cancelled, or shut down."""


class PipeTimeout(Exception):
    """The deadline passed with the operation still pending (it was cancelled
    and drained before this was raised; the handle stays usable)."""


class OverlappedPipe:
    """One connected pipe handle, full-duplex.

    One reader at a time by contract (a single reader thread); writes are
    serialised by an internal lock so any thread may :meth:`write`. The
    ``stop_event`` is a manual-reset win32 event owned by the caller — set
    it and every blocked operation raises :class:`PipeClosed`.
    """

    def __init__(self, handle: Any, stop_event: Any) -> None:
        self._handle = handle
        self._stop_event = stop_event
        self._read_event = win32event.CreateEvent(None, True, False, None)
        self._write_event = win32event.CreateEvent(None, True, False, None)
        self._read_buffer = win32file.AllocateReadBuffer(_READ_CHUNK)
        self._write_lock = threading.Lock()

    # ------------------------------------------------------------------ #

    def read(self, timeout_ms: int | None = None) -> bytes:
        """One chunk, however the kernel slices it. Raises :class:`PipeClosed`
        on disconnect or stop, :class:`PipeTimeout` past the deadline."""
        overlapped = pywintypes.OVERLAPPED()
        overlapped.hEvent = self._read_event
        win32event.ResetEvent(self._read_event)
        try:
            rc, _ = win32file.ReadFile(self._handle, self._read_buffer, overlapped)
        except pywintypes.error as exc:
            raise self._translate(exc) from None
        if rc == _ERROR_IO_PENDING:
            self._await(overlapped, self._read_event, timeout_ms)
        count = self._drain(overlapped)
        if count == 0:
            raise PipeClosed("zero-byte read: peer closed")
        return bytes(self._read_buffer[:count])

    def write(self, data: bytes, timeout_ms: int = WRITE_TIMEOUT_MS) -> None:
        with self._write_lock:
            overlapped = pywintypes.OVERLAPPED()
            overlapped.hEvent = self._write_event
            win32event.ResetEvent(self._write_event)
            try:
                rc, _ = win32file.WriteFile(self._handle, data, overlapped)
            except pywintypes.error as exc:
                raise self._translate(exc) from None
            if rc == _ERROR_IO_PENDING:
                self._await(overlapped, self._write_event, timeout_ms)
            self._drain(overlapped)

    def cancel_all(self) -> None:
        """Best-effort abort of whatever is pending on this handle, from any
        thread — which is exactly why it must be CancelIo**Ex**."""
        cancel_io(self._handle)

    # ------------------------------------------------------------------ #

    def _await(self, overlapped: Any, io_event: Any, timeout_ms: int | None) -> None:
        """Wait for completion, stop, or deadline — in that priority order."""
        timeout = win32event.INFINITE if timeout_ms is None else max(0, timeout_ms)
        rc = win32event.WaitForMultipleObjects(
            [self._stop_event, io_event], False, timeout
        )
        if rc == win32event.WAIT_OBJECT_0:  # stop
            self._abort(overlapped)
            raise PipeClosed("stopped")
        if rc == win32event.WAIT_TIMEOUT:
            self._abort(overlapped)
            raise PipeTimeout(f"pipe operation still pending after {timeout_ms} ms")
        # WAIT_OBJECT_0 + 1: the I/O completed; _drain collects it.

    def _abort(self, overlapped: Any) -> None:
        """Cancel and *complete* the operation. The kernel owns the buffer
        until GetOverlappedResult says otherwise; returning earlier would let
        a cancelled read scribble into a reused buffer."""
        self.cancel_all()
        try:
            win32file.GetOverlappedResult(self._handle, overlapped, True)
        except pywintypes.error:
            pass  # aborted (995) is the expected answer here

    def _drain(self, overlapped: Any) -> int:
        try:
            return win32file.GetOverlappedResult(self._handle, overlapped, True)
        except pywintypes.error as exc:
            raise self._translate(exc) from None

    @staticmethod
    def _translate(exc: pywintypes.error) -> Exception:
        if exc.winerror in _CLOSED_WINERRORS:
            return PipeClosed(f"winerror {exc.winerror}")
        return exc
