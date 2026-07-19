"""FLAC in-process via ``soundfile`` (spec §5).

Measured, not assumed (research/07): 9 ms to encode 19.4 s of audio in-process
against 152 ms through an external ffmpeg, and FLAC beats WAV by 39 ms on the
full network round trip — a result that survived only because the comparison was
re-run as 8 interleaved pairs after a 3-run benchmark said the opposite.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf

__all__ = ["encode_flac"]


def encode_flac(samples: np.ndarray, path: Path, *, sample_rate: int = 16_000) -> Path:
    """Write mono ``int16`` samples as FLAC and return the path.

    The parent directory is created because this call sits on the durability
    path (spec §3): the very first dictation after install must not die on a
    missing ``audio\\`` folder.
    """
    if samples.dtype != np.int16:
        raise TypeError(f"expected int16 samples, got {samples.dtype}")
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(path, samples.reshape(-1), sample_rate, format="FLAC", subtype="PCM_16")
    return path
