"""Audio: capture, silence trimming, FLAC encoding, cues (spec §5).

16 kHz mono ``int16`` end to end. The dangerous part is not the DSP — it is the
two measured rules: the adaptive trim threshold must be capped by an absolute
floor or a short «да» disappears whole, and the PortAudio device list is frozen
at initialisation, so refreshing it means reloading the library with no stream
running (research/07, S3).
"""

from .capture import CaptureError, CaptureSession
from .cues import cue_wave, play_cue
from .encode import encode_flac
from .trim import TrimResult, trim_silence

__all__ = [
    "CaptureError",
    "CaptureSession",
    "cue_wave",
    "play_cue",
    "encode_flac",
    "TrimResult",
    "trim_silence",
]
