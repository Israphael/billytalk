"""The core↔UI channel (harness §3, OPEN-QUESTIONS §21).

Framing is tested as pure computation; everything else runs against real
named pipes on the machine — the DACL, the first-instance flag and the
server-image check are Windows behaviours, and faking Windows here would
test the fake.
"""

from __future__ import annotations

import re
import threading
import time
from typing import Any
from uuid import uuid4

import pytest
import pywintypes
import win32api
import win32con
import win32file
import win32pipe
import win32process
import win32security
import winerror

from billytalk.core.ipc.protocol import (
    MAX_FRAME_BYTES,
    PROTOCOL_VERSION,
    FrameCorrupt,
    FrameDecoder,
    FrameTooLarge,
    encode_frame,
    hello,
    reply,
)
from billytalk.core.ipc.server import IpcServer, PipeBusy, pipe_name
from billytalk.ui.ipc.client import (
    CoreNotRunning,
    IpcClient,
    ProtocolMismatch,
    ServerUntrusted,
)

DEADLINE_S = 3.0


def _test_name() -> str:
    return f"\\\\.\\pipe\\billytalk-test-{uuid4().hex}"


def _echo_handler(message: dict[str, Any]) -> dict[str, Any] | None:
    request_id = message.get("id")
    if isinstance(request_id, int):
        return reply(request_id, {"echo": message["type"]})
    return None


def _connect_raw(name: str) -> Any:
    """CreateFile with a retry on ERROR_PIPE_BUSY: after a disconnect the
    server's instance is busy until its ConnectNamedPipe is re-armed — the
    real client rides WaitNamedPipe for this; a raw test just polls."""
    deadline = time.monotonic() + DEADLINE_S
    while True:
        try:
            return win32file.CreateFile(
                name,
                win32con.GENERIC_READ | win32con.GENERIC_WRITE,
                0, None, win32con.OPEN_EXISTING, 0, None,
            )
        except pywintypes.error as exc:
            if exc.winerror not in (231, 2) or time.monotonic() >= deadline:
                raise
            time.sleep(0.02)


def _current_image() -> str:
    """The image path of THIS process — deliberately not ``sys.executable``:
    under a uv venv the ``.venv\\Scripts\\python.exe`` trampoline execs the
    base interpreter, so the running image is ``C:\\Python314\\python.exe``
    while ``sys.executable`` still names the venv. The product compares
    against its installed exe path, which has no such split."""
    process = win32api.GetCurrentProcess()
    return win32process.GetModuleFileNameEx(process, 0)


def _read_messages(handle: Any, decoder: FrameDecoder, count: int) -> list[dict[str, Any]]:
    """Poll until `count` messages arrive or the deadline passes."""
    messages: list[dict[str, Any]] = []
    deadline = time.monotonic() + DEADLINE_S
    while len(messages) < count and time.monotonic() < deadline:
        _, available, _ = win32pipe.PeekNamedPipe(handle, 0)
        if not available:
            time.sleep(0.01)
            continue
        _, chunk = win32file.ReadFile(handle, 65536)
        messages.extend(decoder.feed(chunk))
    assert len(messages) >= count, f"got {len(messages)} messages before the deadline"
    return messages


def _handshake_raw(handle: Any) -> dict[str, Any]:
    win32file.WriteFile(handle, encode_frame(hello()))
    ack = _read_messages(handle, FrameDecoder(), 1)[0]
    assert ack["type"] == "hello_ack"
    return ack


def _pipe_is_dead(handle: Any) -> bool:
    """True once the server end hung up."""
    deadline = time.monotonic() + DEADLINE_S
    while time.monotonic() < deadline:
        try:
            _, available, _ = win32pipe.PeekNamedPipe(handle, 0)
        except pywintypes.error:
            return True
        if available:  # drain whatever was in flight; death comes after
            win32file.ReadFile(handle, 65536)
            continue
        time.sleep(0.01)
    return False


@pytest.fixture
def server_name():
    name = _test_name()
    server = IpcServer(name, handler=_echo_handler)
    server.start()
    yield name, server
    server.stop()


# --------------------------------------------------------------------------- #
# framing (pure)
# --------------------------------------------------------------------------- #

def test_frames_survive_byte_by_byte_delivery() -> None:
    first = {"type": "state_changed", "state": "Recording", "queue_len": 0}
    second = {"type": "transcription_ready", "id": 7, "delivery_status": "inserted",
              "target_app": "notepad.exe", "текст": "нет — по каналу идёт id"}
    stream = encode_frame(first) + encode_frame(second)
    decoder = FrameDecoder()
    got: list[dict[str, Any]] = []
    for i in range(len(stream)):
        got.extend(decoder.feed(stream[i:i + 1]))
    assert got == [first, second]


def test_two_frames_in_one_chunk() -> None:
    a, b = {"type": "usage_updated"}, {"type": "device_list_changed"}
    assert FrameDecoder().feed(encode_frame(a) + encode_frame(b)) == [a, b]


def test_oversized_declaration_kills_the_stream() -> None:
    decoder = FrameDecoder()
    with pytest.raises(FrameTooLarge):
        decoder.feed((MAX_FRAME_BYTES + 1).to_bytes(4, "little"))


def test_zero_length_frame_is_corrupt() -> None:
    with pytest.raises(FrameCorrupt):
        FrameDecoder().feed((0).to_bytes(4, "little"))


def test_non_object_payload_is_corrupt() -> None:
    payload = b"[1,2]"
    frame = len(payload).to_bytes(4, "little") + payload
    with pytest.raises(FrameCorrupt):
        FrameDecoder().feed(frame)


def test_broken_utf8_is_corrupt() -> None:
    payload = b"\xff\xfe{}"
    frame = len(payload).to_bytes(4, "little") + payload
    with pytest.raises(FrameCorrupt):
        FrameDecoder().feed(frame)


def test_encode_refuses_a_frame_over_the_cap() -> None:
    with pytest.raises(FrameTooLarge):
        encode_frame({"type": "reply", "result": "x" * (MAX_FRAME_BYTES + 1)})


def test_pipe_name_carries_sid_and_session() -> None:
    name = pipe_name()
    assert re.fullmatch(r"\\\\\.\\pipe\\billytalk-S-1-[\d-]+-\d+", name), name


# --------------------------------------------------------------------------- #
# server against real pipes
# --------------------------------------------------------------------------- #

def test_handshake_then_request_reply(server_name) -> None:
    name, _ = server_name
    handle = _connect_raw(name)
    try:
        ack = _handshake_raw(handle)
        assert ack["protocol"] == PROTOCOL_VERSION
        assert "core_version" in ack
        win32file.WriteFile(handle, encode_frame({"type": "toggle_dictation", "id": 7}))
        response = _read_messages(handle, FrameDecoder(), 1)[0]
        assert response == {"type": "reply", "id": 7, "result": {"echo": "toggle_dictation"}}
    finally:
        handle.Close()


def test_protocol_mismatch_is_answered_then_hung_up(server_name) -> None:
    name, _ = server_name
    handle = _connect_raw(name)
    try:
        bad = dict(hello())
        bad["protocol"] = PROTOCOL_VERSION + 1
        win32file.WriteFile(handle, encode_frame(bad))
        answer = _read_messages(handle, FrameDecoder(), 1)[0]
        assert answer == {"type": "error", "code": "protocol_mismatch"}
        assert _pipe_is_dead(handle)
    finally:
        handle.Close()
    # the server survives a mismatched client and accepts the next one
    handle = _connect_raw(name)
    try:
        _handshake_raw(handle)
    finally:
        handle.Close()


def test_first_frame_other_than_hello_is_dropped(server_name) -> None:
    name, _ = server_name
    handle = _connect_raw(name)
    try:
        win32file.WriteFile(handle, encode_frame({"type": "get_config", "id": 1}))
        assert _pipe_is_dead(handle)
    finally:
        handle.Close()


def test_silent_client_is_dropped_after_the_handshake_deadline() -> None:
    name = _test_name()
    server = IpcServer(name, handler=_echo_handler, handshake_timeout_ms=150)
    server.start()
    try:
        handle = _connect_raw(name)
        try:
            assert _pipe_is_dead(handle)
        finally:
            handle.Close()
        handle = _connect_raw(name)  # the slot is free again
        try:
            _handshake_raw(handle)
        finally:
            handle.Close()
    finally:
        server.stop()


def test_dacl_admits_only_this_user_and_system(server_name) -> None:
    """Harness §3: the default DACL grants Everyone read — ours must not."""
    name, _ = server_name
    handle = _connect_raw(name)
    try:
        descriptor = win32security.GetSecurityInfo(
            handle, win32security.SE_KERNEL_OBJECT, win32security.DACL_SECURITY_INFORMATION
        )
        dacl = descriptor.GetSecurityDescriptorDacl()
        assert dacl is not None, "NULL DACL would mean everyone-full-control"
        granted = [dacl.GetAce(i)[2] for i in range(dacl.GetAceCount())]

        token = win32security.OpenProcessToken(
            win32api.GetCurrentProcess(), win32con.TOKEN_QUERY
        )
        me = win32security.GetTokenInformation(token, win32security.TokenUser)[0]
        token.Close()
        system = win32security.CreateWellKnownSid(win32security.WinLocalSystemSid)
        everyone = win32security.CreateWellKnownSid(win32security.WinWorldSid)
        anonymous = win32security.CreateWellKnownSid(win32security.WinAnonymousSid)

        assert me in granted
        assert system in granted
        assert everyone not in granted
        assert anonymous not in granted
        assert len(granted) == 2, "no third principal on a transcript channel"
    finally:
        handle.Close()


def test_taken_name_refuses_a_second_server(server_name) -> None:
    name, _ = server_name
    second = IpcServer(name, handler=_echo_handler)
    with pytest.raises(PipeBusy):
        second.start()
    with pytest.raises(pywintypes.error) as raw:
        win32pipe.CreateNamedPipe(
            name,
            win32pipe.PIPE_ACCESS_DUPLEX | 0x0008_0000,  # FILE_FLAG_FIRST_PIPE_INSTANCE
            win32pipe.PIPE_TYPE_BYTE, 1, 4096, 4096, 0, None,
        )
    assert raw.value.winerror == winerror.ERROR_ACCESS_DENIED


def test_disconnect_fires_callback_and_frees_the_slot() -> None:
    name = _test_name()
    connected = threading.Event()
    disconnected = threading.Event()
    server = IpcServer(
        name, handler=_echo_handler,
        on_connect=connected.set, on_disconnect=disconnected.set,
    )
    server.start()
    try:
        handle = _connect_raw(name)
        _handshake_raw(handle)
        assert connected.wait(DEADLINE_S)
        handle.Close()
        # spec §14: this callback is where hotkey capture must be released
        assert disconnected.wait(DEADLINE_S)
        handle = _connect_raw(name)
        try:
            _handshake_raw(handle)
        finally:
            handle.Close()
    finally:
        server.stop()


def test_unknown_type_with_id_gets_an_error_reply(server_name) -> None:
    name, _ = server_name
    handle = _connect_raw(name)
    try:
        _handshake_raw(handle)
        win32file.WriteFile(handle, encode_frame({"type": "make_coffee", "id": 3}))
        response = _read_messages(handle, FrameDecoder(), 1)[0]
        assert response == {"type": "reply", "id": 3, "error": "unknown_type"}
    finally:
        handle.Close()


def test_server_push_reaches_the_client(server_name) -> None:
    name, server = server_name
    handle = _connect_raw(name)
    try:
        _handshake_raw(handle)
        assert server.send({"type": "state_changed", "state": "Idle", "queue_len": 0})
        message = _read_messages(handle, FrameDecoder(), 1)[0]
        assert message["type"] == "state_changed"
    finally:
        handle.Close()


def test_send_without_a_client_reports_false(server_name) -> None:
    _, server = server_name
    assert server.send({"type": "usage_updated", "words_this_week": 5}) is False


# --------------------------------------------------------------------------- #
# the UI client against the real server
# --------------------------------------------------------------------------- #

def test_client_full_loop(server_name) -> None:
    name, server = server_name
    inbox: list[dict[str, Any]] = []
    got_event = threading.Event()

    def on_message(message: dict[str, Any]) -> None:
        inbox.append(message)
        got_event.set()

    client = IpcClient(
        name, on_message=on_message, expected_image=_current_image()
    )
    client.connect()
    try:
        assert client.core_version
        # The client returns from connect() on receiving the ack, which the
        # server writes moments before it flips its connected flag — poll
        # until the push is actually accepted.
        deadline = time.monotonic() + DEADLINE_S
        while not server.send(
            {"type": "hotkey_captured", "codes": [4099], "display": "Mouse 4"}
        ):
            assert time.monotonic() < deadline, "server never became connected"
            time.sleep(0.01)
        assert got_event.wait(DEADLINE_S)
        assert inbox[0]["type"] == "hotkey_captured"

        got_event.clear()
        client.send({"type": "history_search", "id": 11, "query": "", "limit": 1, "offset": 0})
        assert got_event.wait(DEADLINE_S)
        assert {"type": "reply", "id": 11, "result": {"echo": "history_search"}} in inbox
    finally:
        client.close()


def test_client_rejects_a_server_from_a_foreign_image(server_name) -> None:
    name, _ = server_name
    client = IpcClient(
        name, on_message=lambda m: None,
        expected_image=r"C:\Windows\System32\notepad.exe",
    )
    with pytest.raises(ServerUntrusted):
        client.connect()


def test_client_reports_a_missing_core() -> None:
    client = IpcClient(_test_name(), on_message=lambda m: None)
    with pytest.raises(CoreNotRunning):
        client.connect(timeout_ms=200)


def test_client_raises_protocol_mismatch(server_name, monkeypatch) -> None:
    name, _ = server_name
    monkeypatch.setattr(
        "billytalk.ui.ipc.client.hello",
        lambda app_version: {"type": "hello", "protocol": 99, "role": "ui",
                             "app_version": app_version},
    )
    client = IpcClient(name, on_message=lambda m: None)
    with pytest.raises(ProtocolMismatch):
        client.connect()


def test_client_disconnect_callback_fires_when_the_core_dies() -> None:
    name = _test_name()
    server = IpcServer(name, handler=_echo_handler)
    server.start()
    dropped = threading.Event()
    client = IpcClient(name, on_message=lambda m: None, on_disconnect=dropped.set)
    client.connect()
    try:
        server.stop()
        assert dropped.wait(DEADLINE_S)
    finally:
        client.close()


def test_client_close_is_quiet() -> None:
    """No on_disconnect for a teardown the UI asked for itself."""
    name = _test_name()
    server = IpcServer(name, handler=_echo_handler)
    server.start()
    dropped = threading.Event()
    client = IpcClient(name, on_message=lambda m: None, on_disconnect=dropped.set)
    client.connect()
    client.close()
    assert not dropped.wait(0.3)
    server.stop()
