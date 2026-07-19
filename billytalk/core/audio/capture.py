"""Microphone capture (spec §5): 16 kHz mono ``int16``, default device in MVP-0.

The one subtle contract is *when the recording has started*. ``CaptureStarted``
is the event a deferred release waits for and the moment the 250 ms clock
starts, so it must mean "the first frame actually arrived" — not "the stream
object was constructed". A Bluetooth headset takes 100–300 ms to wake, and the
start cue played before the first frame would confirm a recording that is not
happening (spec §5). Hence ``on_started`` fires from the callback, on the first
frame, exactly once.

``stop`` records a further 200 ms tail: spec §5 counts the tail after the
release as part of the padding, because trailing consonants die without it.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

import numpy as np

from ..machine.effects import ErrorCode

__all__ = ["CaptureError", "CaptureSession", "TAIL_MS"]

TAIL_MS = 200


class CaptureError(RuntimeError):
    """The stream could not be opened, with the taxonomy code to notify."""

    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


class CaptureSession:
    """One recording, from ``StartCapture`` to ``StopCapture``/``CancelCapture``.

    Frames accumulate under a lock; the sounddevice callback does nothing but
    copy and append — the same discipline as the input hook, and for the same
    reason: the callback runs on somebody else's time.
    """

    def __init__(
        self,
        *,
        device: int | str | None = None,
        sample_rate: int = 16_000,
        on_started: Callable[[], None] | None = None,
        on_error: Callable[[ErrorCode], None] | None = None,
    ) -> None:
        self._device = device
        self._sample_rate = sample_rate
        self._on_started = on_started
        self._on_error = on_error
        self._frames: list[np.ndarray] = []
        self._lock = threading.Lock()
        self._started_fired = False
        self._stream: object | None = None

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    def start(self) -> None:
        """Open the stream. ``on_started`` fires later, from the first frame."""
        import sounddevice as sd

        try:
            stream = sd.InputStream(
                device=self._device,
                samplerate=self._sample_rate,
                channels=1,
                dtype="int16",
                callback=self._callback,
            )
            stream.start()
        except sd.PortAudioError as exc:
            # Distinguishing a privacy block from a busy device out of a
            # PortAudio error is unreliable; MVP-0 reports mic_busy, whose
            # user action ("pick another device") is the safer of the two.
            raise CaptureError(ErrorCode.MIC_BUSY, str(exc)) from exc
        self._stream = stream

    def _callback(self, indata: np.ndarray, frames: int, time_info: object, status: object) -> None:
        with self._lock:
            self._frames.append(indata.copy())
        if not self._started_fired:
            self._started_fired = True
            if self._on_started is not None:
                self._on_started()
        if status and self._on_error is not None:
            # Overflow drops frames but the recording lives; report, don't stop.
            self._on_error(ErrorCode.MIC_BUSY)

    def stop(self, *, tail_ms: int = TAIL_MS) -> np.ndarray:
        """Record the tail, close the stream, hand back everything captured.

        Blocks for ``tail_ms`` — the caller is the driver's audio executor, and
        the block is the tail being recorded, not idleness.
        """
        time.sleep(tail_ms / 1000)
        self._close()
        with self._lock:
            if not self._frames:
                return np.empty(0, dtype=np.int16)
            data = np.concatenate([f.reshape(-1) for f in self._frames])
            self._frames.clear()
        return data.astype(np.int16, copy=False)

    def cancel(self) -> None:
        """Close and discard. The capture ledger's ``CancelCapture``."""
        self._close()
        with self._lock:
            self._frames.clear()

    def _close(self) -> None:
        stream = self._stream
        self._stream = None
        if stream is None:
            return
        try:
            stream.stop()  # type: ignore[attr-defined]
            stream.close()  # type: ignore[attr-defined]
        except Exception:
            # A device yanked mid-recording raises here; the frames we already
            # hold are the recording, and spec §5 says finalise with them.
            pass
