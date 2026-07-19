"""The core's end of the named-pipe channel (harness §3, spec §14).

Every trap in harness §3 is load-bearing here:

* **The pipe namespace is machine-global, not per-session.** One user in two
  sessions (console + RDP) would collide on a SID-only name, and the second
  session's UI would drive a core that pastes into the first session's
  windows. The name therefore carries both the user SID and the session id
  from ``ProcessIdToSessionId``.
* **``lpSecurityAttributes`` is mandatory.** The default pipe DACL grants
  read to Everyone and to anonymous logons — and transcripts travel this
  pipe. The DACL here names the current user and LocalSystem, nobody else.
* **``FILE_FLAG_FIRST_PIPE_INSTANCE``**: a taken name means a running core —
  or a squatter standing where the server should be. Either way this process
  must not serve; :class:`PipeBusy` says which story to tell the user.
* **``PIPE_REJECT_REMOTE_CLIENTS``**, and the handshake is the first frame or
  the connection dies. Authenticity is the *client's* burden (it checks the
  server's image path; ``hello`` proves nothing) — the server's burden is to
  never speak before a valid ``hello`` and to drop the connection on the
  first framing violation, because a desynchronised stream has no next frame.
* **``FILE_FLAG_OVERLAPPED``, measured, not optional.** On a synchronous
  handle Windows serialises I/O on the file object: with the reader parked
  in ``ReadFile``, the writer thread's 34-byte reply queued forever behind
  it (cycle-2 probe). Full duplex on one handle exists only overlapped —
  see ``channel.py``.

One UI at a time (``nMaxInstances=1``): the interface is a singleton by
design (harness §2 — the core restarts it, at most once per 30 s). When a
client disconnects the server survives and accepts the next one; the
``on_disconnect`` callback is where hotkey capture gets released (spec §14 —
a crashed UI must not leave a button suppressed).

Threads: one accept/read thread for the server's lifetime, plus one writer
per connection. ``send`` never blocks the caller — the driver thread has a
one-second hook budget and must not wait on a UI that stopped reading; a
full outbound queue closes the connection instead (a UI that reads nothing
for hundreds of frames is gone in every way that matters).
"""

from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Callable
from typing import Any, Final

import pywintypes
import win32api
import win32con
import win32event
import win32file
import win32pipe
import win32security
import win32ts
import winerror

from ... import __version__
from .channel import WRITE_TIMEOUT_MS, OverlappedPipe, PipeClosed, PipeTimeout, cancel_io
from .protocol import (
    PROTOCOL_VERSION,
    UI_TO_CORE,
    FrameDecoder,
    FrameError,
    encode_frame,
    hello_ack,
    protocol_mismatch,
    reply,
)

__all__ = ["IpcServer", "PipeBusy", "pipe_name"]

log = logging.getLogger("billytalk.ipc")

HANDSHAKE_TIMEOUT_MS: Final = 5_000
"""A connected client that says nothing is not a UI. Five seconds is enough
for a same-machine process to send one frame; then the slot goes back to the
real interface."""

OUTBOUND_QUEUE_MAX: Final = 256
_PIPE_BUFFER: Final = 64 * 1024

# Values per winbase.h; spelled out because older win32con builds miss them.
_FILE_FLAG_FIRST_PIPE_INSTANCE: Final = getattr(
    win32con, "FILE_FLAG_FIRST_PIPE_INSTANCE", 0x0008_0000
)
_PIPE_REJECT_REMOTE_CLIENTS: Final = getattr(
    win32pipe, "PIPE_REJECT_REMOTE_CLIENTS", 0x0000_0008
)


class PipeBusy(RuntimeError):
    """The name is taken: an already-running core, or something squatting on
    our name. Either way: do not serve, tell the user the core is already
    running."""


def pipe_name() -> str:
    """``\\\\.\\pipe\\billytalk-{sid}-{session_id}`` (harness §3)."""
    token = win32security.OpenProcessToken(
        win32api.GetCurrentProcess(), win32con.TOKEN_QUERY
    )
    try:
        user_sid = win32security.GetTokenInformation(token, win32security.TokenUser)[0]
    finally:
        token.Close()
    sid = win32security.ConvertSidToStringSid(user_sid)
    session = win32ts.ProcessIdToSessionId(win32api.GetCurrentProcessId())
    return f"\\\\.\\pipe\\billytalk-{sid}-{session}"


def _pipe_security() -> pywintypes.SECURITY_ATTRIBUTESType:
    """DACL: current user and LocalSystem, read+write; no Everyone, no
    anonymous. Explicit because the default is the opposite (harness §3)."""
    token = win32security.OpenProcessToken(
        win32api.GetCurrentProcess(), win32con.TOKEN_QUERY
    )
    try:
        user_sid = win32security.GetTokenInformation(token, win32security.TokenUser)[0]
    finally:
        token.Close()
    system_sid = win32security.CreateWellKnownSid(win32security.WinLocalSystemSid)

    dacl = win32security.ACL()
    access = win32con.GENERIC_READ | win32con.GENERIC_WRITE
    dacl.AddAccessAllowedAce(win32security.ACL_REVISION, access, user_sid)
    dacl.AddAccessAllowedAce(win32security.ACL_REVISION, access, system_sid)

    descriptor = win32security.SECURITY_DESCRIPTOR()
    descriptor.SetSecurityDescriptorDacl(True, dacl, False)
    attributes = pywintypes.SECURITY_ATTRIBUTES()
    attributes.SECURITY_DESCRIPTOR = descriptor
    return attributes


class IpcServer:
    """Owns the single pipe instance and the connection lifecycle.

    ``handler`` runs on the server's read thread for every post-handshake
    message; whatever dict it returns is sent back as-is (build it with
    :func:`protocol.reply`). Returning ``None`` answers nothing — the normal
    case for fire-and-forget commands like ``toggle_dictation``.
    """

    def __init__(
        self,
        name: str,
        *,
        handler: Callable[[dict[str, Any]], dict[str, Any] | None],
        on_connect: Callable[[], None] | None = None,
        on_disconnect: Callable[[], None] | None = None,
        core_version: str = __version__,
        handshake_timeout_ms: int = HANDSHAKE_TIMEOUT_MS,
        write_timeout_ms: int | None = None,
    ) -> None:
        self._name = name
        self._handler = handler
        self._on_connect = on_connect
        self._on_disconnect = on_disconnect
        self._core_version = core_version
        self._handshake_timeout_ms = handshake_timeout_ms
        self._write_timeout_ms = (
            write_timeout_ms if write_timeout_ms is not None else WRITE_TIMEOUT_MS
        )
        self._pipe: Any = None
        self._stop = threading.Event()
        self._stop_win32 = win32event.CreateEvent(None, True, False, None)  # manual-reset
        self._connected = threading.Event()
        self._channel: OverlappedPipe | None = None
        self._conn_dead: Any = None
        self._outbound: queue.Queue[bytes | None] | None = None
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------ #
    # lifecycle
    # ------------------------------------------------------------------ #

    def start(self) -> None:
        """Create the pipe (this is where a squatted name fails) and begin
        accepting. Raises :class:`PipeBusy` — before any thread exists — so
        ``__main__`` can refuse to run a second core cleanly."""
        try:
            self._pipe = win32pipe.CreateNamedPipe(
                self._name,
                win32pipe.PIPE_ACCESS_DUPLEX | win32con.FILE_FLAG_OVERLAPPED
                | _FILE_FLAG_FIRST_PIPE_INSTANCE,
                win32pipe.PIPE_TYPE_BYTE | win32pipe.PIPE_READMODE_BYTE
                | win32pipe.PIPE_WAIT | _PIPE_REJECT_REMOTE_CLIENTS,
                1,  # the UI is a singleton (harness §2)
                _PIPE_BUFFER, _PIPE_BUFFER,
                0,
                _pipe_security(),
            )
        except pywintypes.error as exc:
            if exc.winerror in (winerror.ERROR_ACCESS_DENIED, winerror.ERROR_PIPE_BUSY):
                raise PipeBusy(self._name) from None
            raise
        self._thread = threading.Thread(
            target=self._serve_forever, name="billytalk-ipc", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        """Idempotent. The stop event wakes every overlapped wait; pending
        I/O is cancelled and drained before the handle closes."""
        if self._pipe is None:
            return
        self._stop.set()
        win32event.SetEvent(self._stop_win32)
        if self._thread is not None:
            self._thread.join(timeout=3.0)
            self._thread = None
        pipe, self._pipe = self._pipe, None
        try:
            pipe.Close()
        except pywintypes.error:
            pass

    @property
    def connected(self) -> bool:
        return self._connected.is_set()

    def send(self, message: dict[str, Any]) -> bool:
        """Queue a core→UI message. Never blocks and never raises (the driver
        thread lives on a hook-budget clock, and an exception here would ride
        up whatever thread called it — review, cycle 2). Returns False when
        there is no UI to hear it — events are advisory display, the store is
        the durable truth."""
        outbound = self._outbound
        if outbound is None or not self._connected.is_set():
            return False
        try:
            frame = encode_frame(message)
        except FrameError as exc:
            # One oversized reply must cost that reply, not the server: the
            # frame cap protects the stream, dropping the message honours it.
            log.warning("outbound frame refused (%s); type %r dropped",
                        exc, message.get("type"))
            return False
        try:
            outbound.put_nowait(frame)
            return True
        except queue.Full:
            log.warning("outbound queue full; dropping the UI connection")
            self._disconnect_current()
            return False

    # ------------------------------------------------------------------ #
    # the accept/read thread
    # ------------------------------------------------------------------ #

    def _serve_forever(self) -> None:
        while not self._stop.is_set():
            pipe = self._pipe
            if pipe is None:
                return
            try:
                if not self._accept(pipe):
                    return
                self._serve_one(pipe)
            except (PipeClosed, PipeTimeout):
                pass  # client vanished mid-handshake or mid-frame; take the next
            except pywintypes.error as exc:
                if self._stop.is_set():
                    return
                log.info("ipc connection dropped: winerror %d", exc.winerror)
            except Exception:
                # The review's rule: no exception may kill this thread. A dead
                # accept thread leaves the name claimed but never re-armed —
                # every future UI gets ERROR_PIPE_BUSY while the core dictates
                # on, and under console=False the traceback goes nowhere.
                if self._stop.is_set():
                    return
                log.exception("ipc connection handling failed; re-accepting")
            finally:
                self._end_connection(pipe)

    def _accept(self, pipe: Any) -> bool:
        """Overlapped ConnectNamedPipe; False means stop was requested."""
        overlapped = pywintypes.OVERLAPPED()
        overlapped.hEvent = win32event.CreateEvent(None, True, False, None)
        rc = win32pipe.ConnectNamedPipe(pipe, overlapped)
        if rc == winerror.ERROR_PIPE_CONNECTED:
            return True
        if rc in (0, winerror.ERROR_IO_PENDING):
            waited = win32event.WaitForMultipleObjects(
                [self._stop_win32, overlapped.hEvent], False, win32event.INFINITE
            )
            if waited == win32event.WAIT_OBJECT_0:  # stop
                cancel_io(pipe)
                try:
                    win32file.GetOverlappedResult(pipe, overlapped, True)
                except pywintypes.error:
                    pass
                return False
            try:
                win32file.GetOverlappedResult(pipe, overlapped, True)
            except pywintypes.error as exc:
                # ERROR_NO_DATA and friends: the client connected and died
                # before we looked; the outer loop disconnects and re-accepts.
                raise PipeClosed(f"accept: winerror {exc.winerror}") from None
            return True
        raise pywintypes.error(rc, "ConnectNamedPipe", "unexpected return")

    def _serve_one(self, pipe: Any) -> None:
        # Level-triggered death signal for THIS connection: CancelIoEx alone
        # is edge-triggered and misses a reader that is between reads (inside
        # a handler) at the moment the writer dies (review, confirmed live).
        dead = win32event.CreateEvent(None, True, False, None)
        channel = OverlappedPipe(pipe, self._stop_win32, dead_event=dead)
        self._channel = channel
        self._conn_dead = dead
        decoder = FrameDecoder()

        messages = self._await_handshake(channel, decoder)
        if not messages:
            return
        first = messages[0]

        if first.get("type") != "hello" or first.get("role") != "ui":
            log.warning("first frame was not a UI hello; dropping connection")
            return
        if first.get("protocol") != PROTOCOL_VERSION:
            # The real case: an updated UI against a live old core (or the
            # reverse). Say so on the wire — the UI shows "restart the app".
            channel.write(encode_frame(protocol_mismatch()))
            try:
                # DisconnectNamedPipe discards unread buffered data, and the
                # whole point of this frame is to be read. FlushFileBuffers
                # blocks until the client has taken it; a client that never
                # reads would wedge this thread, which stop() resolves by
                # closing the handle under the flush.
                win32file.FlushFileBuffers(pipe)
            except pywintypes.error:
                pass  # the client is gone; nobody was waiting for the answer
            log.warning(
                "protocol mismatch: ui speaks %r, core speaks %d",
                first.get("protocol"), PROTOCOL_VERSION,
            )
            return

        channel.write(encode_frame(hello_ack(core_version=self._core_version)))

        self._outbound = queue.Queue(maxsize=OUTBOUND_QUEUE_MAX)
        writer = threading.Thread(
            target=self._write_loop, args=(channel, self._outbound, dead),
            name="billytalk-ipc-writer", daemon=True,
        )
        writer.start()
        self._connected.set()
        if self._on_connect is not None:
            try:
                self._on_connect()
            except Exception:
                log.exception("on_connect callback failed")
        try:
            # Requests the client pipelined in the same chunk as hello are
            # real traffic, not garbage (review): dispatch them now that the
            # ack is written and replies have somewhere to go.
            for message in messages[1:]:
                self._dispatch(message)
            self._read_loop(channel, decoder)
        finally:
            self._connected.clear()
            outbound, self._outbound = self._outbound, None
            if outbound is not None:
                # ⚠ Never a blocking put: in the overflow path the writer died
                # with the queue still full, and a blocking sentinel deadlocked
                # this thread forever (review, confirmed live by three probes).
                while True:
                    try:
                        outbound.put_nowait(None)
                        break
                    except queue.Full:
                        try:
                            outbound.get_nowait()
                        except queue.Empty:
                            continue
            writer.join(timeout=1.0)
            self._conn_dead = None
            if self._on_disconnect is not None:
                # Spec §14: hotkey capture is released on channel break —
                # this callback is that release point.
                try:
                    self._on_disconnect()
                except Exception:
                    log.exception("on_disconnect callback failed")

    def _await_handshake(
        self, channel: OverlappedPipe, decoder: FrameDecoder
    ) -> list[dict[str, Any]] | None:
        """Every message of the first complete chunk within the deadline, or
        nothing. The whole list travels back because a client may legally
        pipeline its first requests behind hello in one write."""
        try:
            while True:
                chunk = channel.read(timeout_ms=self._handshake_timeout_ms)
                messages = decoder.feed(chunk)
                if messages:
                    return messages
                # a partial frame arrived; keep the same deadline discipline
        except PipeTimeout:
            log.warning("client connected but sent no hello; dropping")
            return None
        except FrameError as exc:
            log.warning("handshake framing violation: %s", exc)
            return None

    def _read_loop(self, channel: OverlappedPipe, decoder: FrameDecoder) -> None:
        while not self._stop.is_set():
            try:
                chunk = channel.read()
            except PipeClosed:
                return
            try:
                messages = decoder.feed(chunk)
            except FrameError as exc:
                log.warning("framing violation, dropping connection: %s", exc)
                return
            for message in messages:
                self._dispatch(message)

    def _dispatch(self, message: dict[str, Any]) -> None:
        kind = message.get("type")
        if kind not in UI_TO_CORE or kind == "hello":
            # Log the type only — payloads are never logged on this channel.
            log.warning("unknown ipc message type %r", kind)
            request_id = message.get("id")
            if isinstance(request_id, int):
                self.send(reply(request_id, error="unknown_type"))
            return
        try:
            response = self._handler(message)
        except Exception:
            log.exception("ipc handler failed for type %r", kind)
            request_id = message.get("id")
            if isinstance(request_id, int):
                self.send(reply(request_id, error="internal_error"))
            return
        if response is not None:
            self.send(response)

    # ------------------------------------------------------------------ #
    # plumbing
    # ------------------------------------------------------------------ #

    def _write_loop(
        self, channel: OverlappedPipe, outbound: queue.Queue[bytes | None], dead: Any
    ) -> None:
        try:
            while True:
                frame = outbound.get()
                if frame is None:
                    return
                try:
                    channel.write(frame, timeout_ms=self._write_timeout_ms)
                except (PipeClosed, PipeTimeout):
                    # The write side saw the death (or a peer that stopped
                    # reading, which is death by policy).
                    return
        finally:
            # Level-triggered: wakes a reader parked in ReadFile *and* catches
            # one that is currently inside a handler (review). Setting it on
            # the normal sentinel exit is harmless — teardown is already on.
            win32event.SetEvent(dead)
            channel.cancel_all()

    def _disconnect_current(self) -> None:
        """Force the current client off without stopping the server: raise
        the connection's death event and abort pending I/O; the woken reader
        tears the connection down and the accept loop takes the next client."""
        dead = self._conn_dead
        if dead is not None:
            win32event.SetEvent(dead)
        channel = self._channel
        if channel is not None:
            channel.cancel_all()

    def _end_connection(self, pipe: Any) -> None:
        self._connected.clear()
        self._channel = None
        if self._stop.is_set():
            return
        try:
            win32pipe.DisconnectNamedPipe(pipe)
        except pywintypes.error:
            pass
