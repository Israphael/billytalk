"""Core↔UI channel: named pipe with a mandatory DACL (harness §3)."""

from .protocol import PROTOCOL_VERSION, FrameDecoder, encode_frame, reply
from .server import IpcServer, PipeBusy, pipe_name

__all__ = [
    "PROTOCOL_VERSION",
    "FrameDecoder",
    "encode_frame",
    "reply",
    "IpcServer",
    "PipeBusy",
    "pipe_name",
]
