"""Wire protocol of the core↔UI channel (harness §3, OPEN-QUESTIONS §21).

Framing is 4 bytes of little-endian payload length, then UTF-8 JSON — one
object per frame. Messages are typed by a ``type`` field, exactly as the
harness §3 examples spell them; there is no JSON-RPC 2.0 envelope
(OPEN-QUESTIONS §21). UI requests may carry an integer ``id``; the core
answers with ``{"type": "reply", "id": …}``. Core events carry no ``id``.

The handshake is the first exchange on every connection and nothing else is
legal before it: ``hello`` states the protocol number, ``hello_ack`` accepts
it, and a mismatch is answered with ``{"type":"error","code":
"protocol_mismatch"}`` before the core hangs up — the real case being a UI
and a core from different builds after an update over a live process.

The frame cap is defence in depth behind the pipe's DACL: a desynchronised
or hostile stream must die by exception, never by a 4 GiB allocation. History
pages are the largest legitimate frames and sit in the tens of kilobytes.

This module is pure computation — no pipes, no threads — so both processes
share it and tests cover it without Windows in the room.
"""

from __future__ import annotations

import json
import struct
from typing import Any, Final

from ... import __version__

__all__ = [
    "PROTOCOL_VERSION",
    "MAX_FRAME_BYTES",
    "CORE_TO_UI",
    "UI_TO_CORE",
    "FrameError",
    "FrameTooLarge",
    "FrameCorrupt",
    "encode_frame",
    "FrameDecoder",
    "hello",
    "hello_ack",
    "protocol_mismatch",
    "reply",
    "state_changed",
]

PROTOCOL_VERSION: Final = 1

MAX_FRAME_BYTES: Final = 1 << 20
"""1 MiB. Generous for history pages, fatal for a desynchronised stream."""

_LENGTH = struct.Struct("<I")

UI_TO_CORE: Final = frozenset({
    "hello",
    "get_config", "set_config",
    "history_search", "history_insert", "history_export",
    "dictionary_get", "dictionary_set",
    "capture_hotkey_start", "capture_hotkey_stop",
    "test_key", "toggle_dictation", "shutdown",
})

CORE_TO_UI: Final = frozenset({
    "hello_ack", "reply", "error",
    "state_changed", "transcription_ready",
    "usage_updated", "device_list_changed", "hotkey_captured",
})


class FrameError(ValueError):
    """The stream is unusable past this point; the connection must close."""


class FrameTooLarge(FrameError):
    def __init__(self, declared: int) -> None:
        super().__init__(f"frame declares {declared} bytes, cap is {MAX_FRAME_BYTES}")
        self.declared = declared


class FrameCorrupt(FrameError):
    """Not UTF-8, not JSON, or not a JSON object."""


def encode_frame(message: dict[str, Any]) -> bytes:
    """One message → length prefix + UTF-8 JSON.

    ``ensure_ascii=False`` keeps Cyrillic payloads at one byte-per-letter
    honesty in size checks instead of six (``\\uXXXX``).
    """
    payload = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(payload) > MAX_FRAME_BYTES:
        raise FrameTooLarge(len(payload))
    return _LENGTH.pack(len(payload)) + payload


class FrameDecoder:
    """Reassembles messages from an arbitrarily-chunked byte stream.

    ``feed`` returns every complete message the new chunk finished, in order.
    Framing violations raise :class:`FrameError` — after which the decoder is
    junk by design, because a byte stream with a broken length prefix has no
    recoverable frame boundary.
    """

    def __init__(self) -> None:
        self._buffer = bytearray()

    def feed(self, chunk: bytes) -> list[dict[str, Any]]:
        self._buffer.extend(chunk)
        messages: list[dict[str, Any]] = []
        while True:
            if len(self._buffer) < _LENGTH.size:
                return messages
            (length,) = _LENGTH.unpack_from(self._buffer)
            if length > MAX_FRAME_BYTES:
                raise FrameTooLarge(length)
            if length == 0:
                # No JSON object serialises to zero bytes; a zero prefix is
                # stream damage, not an empty message.
                raise FrameCorrupt("zero-length frame")
            end = _LENGTH.size + length
            if len(self._buffer) < end:
                return messages
            payload = bytes(self._buffer[_LENGTH.size:end])
            del self._buffer[:end]
            try:
                message = json.loads(payload.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise FrameCorrupt(str(exc)) from None
            if not isinstance(message, dict):
                raise FrameCorrupt("frame payload is not a JSON object")
            messages.append(message)


# --------------------------------------------------------------------------- #
# handshake and envelopes (harness §3, verbatim field names)
# --------------------------------------------------------------------------- #

def hello(*, role: str = "ui", app_version: str = __version__) -> dict[str, Any]:
    return {"type": "hello", "protocol": PROTOCOL_VERSION, "role": role,
            "app_version": app_version}


def hello_ack(*, core_version: str = __version__) -> dict[str, Any]:
    return {"type": "hello_ack", "protocol": PROTOCOL_VERSION,
            "core_version": core_version}


def protocol_mismatch() -> dict[str, Any]:
    return {"type": "error", "code": "protocol_mismatch"}


def reply(request_id: int, result: dict[str, Any] | None = None,
          *, error: str | None = None) -> dict[str, Any]:
    """Answer to a UI request that carried an ``id`` (OPEN-QUESTIONS §21)."""
    message: dict[str, Any] = {"type": "reply", "id": request_id}
    if error is not None:
        message["error"] = error
    else:
        message["result"] = result if result is not None else {}
    return message


def state_changed(state: str, *, queue_len: int = 0,
                  detail: str | None = None) -> dict[str, Any]:
    """Core → UI display push (harness §3): the display state the tray and the
    plashka read, the queue depth, and an optional human detail. It carries no
    transcript — the words travel only in ``transcription_ready``, keyed by
    ``id`` (spec §13); this event is safe to log by type."""
    message: dict[str, Any] = {"type": "state_changed", "state": state,
                               "queue_len": queue_len}
    if detail is not None:
        message["detail"] = detail
    return message
