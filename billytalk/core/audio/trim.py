"""Silence trimming (spec §5), with the floor that keeps short answers alive.

The adaptive threshold is 4 × the median RMS of the quietest decile of 20 ms
frames. On a clip that is *entirely* speech — «да», «нет», «ок» — the quietest
decile IS speech, so the adaptive bar lands about 12 dB above the clip's own
floor and every frame reads as silence. That is how a short answer gets trimmed
to nothing, and why the absolute floor is mandatory: **a clip whose peak exceeds
−35 dBFS is never discarded**, and the threshold never rises above that level.

Frames are 20 ms. Padding is 180 ms on each side of the detected speech span;
the 200 ms post-release tail recorded by the capture layer is part of what this
padding preserves.

Everything here is a pure function over an ``int16`` array — no stream, no
clock — so the whole policy is testable with synthesised signals.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

import numpy as np

__all__ = ["ABSOLUTE_FLOOR_DBFS", "FRAME_MS", "PAD_MS", "TrimResult", "trim_silence"]

FRAME_MS: Final = 20
PAD_MS: Final = 180
ABSOLUTE_FLOOR_DBFS: Final = -35.0

_INT16_FULL_SCALE: Final = 32767.0


def _amplitude(dbfs: float) -> float:
    return _INT16_FULL_SCALE * 10.0 ** (dbfs / 20.0)


@dataclass(frozen=True, slots=True)
class TrimResult:
    """What the trimmer decided.

    ``is_empty`` is the fact behind the machine's ``ClipEmpty`` event: no frame
    crossed the threshold and the peak stayed under the absolute floor. An empty
    clip keeps its **original** samples — spec §4 records every press, and the
    audio file should hold what the microphone actually heard, not a zero-length
    artefact of our own thresholding.
    """

    samples: np.ndarray
    is_empty: bool
    speech_span_ms: tuple[int, int] | None


def trim_silence(
    samples: np.ndarray,
    *,
    sample_rate: int = 16_000,
    frame_ms: int = FRAME_MS,
    pad_ms: int = PAD_MS,
) -> TrimResult:
    """Cut leading and trailing silence, never the words.

    Args:
        samples: mono ``int16``. Anything else is a programming error upstream.
    """
    if samples.dtype != np.int16:
        raise TypeError(f"expected int16 samples, got {samples.dtype}")
    flat = samples.reshape(-1)
    if flat.size == 0:
        return TrimResult(samples=flat, is_empty=True, speech_span_ms=None)

    frame_len = max(1, sample_rate * frame_ms // 1000)
    frame_count = max(1, len(flat) // frame_len)
    usable = flat[: frame_count * frame_len].astype(np.float64)
    frames = usable.reshape(frame_count, frame_len)
    rms = np.sqrt(np.mean(frames * frames, axis=1))

    quiet_count = max(1, frame_count // 10)
    noise = float(np.median(np.sort(rms)[:quiet_count]))
    adaptive = 4.0 * noise

    floor = _amplitude(ABSOLUTE_FLOOR_DBFS)
    # The cap is the whole point (spec §5): on an all-speech clip the adaptive
    # bar exceeds the speech itself; clamping it to the −35 dBFS floor keeps
    # every audible frame classified as speech.
    threshold = min(adaptive, floor)

    speech = rms > threshold
    peak = float(np.max(np.abs(flat)))
    if not bool(np.any(speech)) and peak <= floor:
        return TrimResult(samples=flat, is_empty=True, speech_span_ms=None)
    if not bool(np.any(speech)):
        # Peak above the floor but frame RMS below threshold: a lone click or a
        # very short burst. Never discarded — keep the clip whole.
        return TrimResult(samples=flat, is_empty=False, speech_span_ms=(0, len(flat) * 1000 // sample_rate))

    first = int(np.argmax(speech))
    last = int(len(speech) - 1 - np.argmax(speech[::-1]))
    pad = sample_rate * pad_ms // 1000
    start = max(0, first * frame_len - pad)
    stop = min(len(flat), (last + 1) * frame_len + pad)
    return TrimResult(
        samples=flat[start:stop],
        is_empty=False,
        speech_span_ms=(start * 1000 // sample_rate, stop * 1000 // sample_rate),
    )
