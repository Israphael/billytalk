"""The six audible cues (spec §5), synthesised — no asset files to lose.

Distinguishability is the requirement, so each cue differs in contour, not just
pitch: rising pair for start, falling pair for stop, an insistent triple for
clipboard, a flat low double for reject, a long low tone for error, a single mid
blip for warn.

``clipboard`` is deliberately the loudest and is played at full amplitude: it is
the only channel that survives focus assist, a disabled overlay and a tray icon
hidden in the overflow (spec §5) — if this one is missed, the user gets the
exact failure the product exists to prevent: said something, nothing happened,
no explanation.

``start`` must be played only after the stream has actually opened (spec §5);
that sequencing lives in the driver — this module just makes sounds.
"""

from __future__ import annotations

from typing import Final

import numpy as np

from ..machine.effects import Cue

__all__ = ["SAMPLE_RATE", "cue_wave", "play_cue"]

SAMPLE_RATE: Final = 16_000

_FULL_SCALE: Final = 32767.0


def _tone(freq: float, ms: int, *, amplitude: float) -> np.ndarray:
    t = np.arange(int(SAMPLE_RATE * ms / 1000)) / SAMPLE_RATE
    wave = np.sin(2 * np.pi * freq * t)
    # 5 ms linear fade at both ends kills the click of a hard edge.
    fade = max(1, int(SAMPLE_RATE * 0.005))
    envelope = np.ones_like(wave)
    envelope[:fade] = np.linspace(0.0, 1.0, fade)
    envelope[-fade:] = np.linspace(1.0, 0.0, fade)
    return (wave * envelope * amplitude * _FULL_SCALE).astype(np.int16)


def _silence(ms: int) -> np.ndarray:
    return np.zeros(int(SAMPLE_RATE * ms / 1000), dtype=np.int16)


def _sequence(*parts: np.ndarray) -> np.ndarray:
    return np.concatenate(parts)


def _build() -> dict[Cue, np.ndarray]:
    quiet = 0.35
    return {
        Cue.START: _sequence(_tone(440, 70, amplitude=quiet), _tone(660, 90, amplitude=quiet)),
        Cue.STOP: _sequence(_tone(660, 70, amplitude=quiet), _tone(440, 90, amplitude=quiet)),
        Cue.CLIPBOARD: _sequence(
            _tone(880, 90, amplitude=0.95), _silence(60),
            _tone(880, 90, amplitude=0.95), _silence(60),
            _tone(1100, 130, amplitude=0.95),
        ),
        Cue.REJECT: _sequence(_tone(220, 80, amplitude=0.5), _silence(50), _tone(220, 80, amplitude=0.5)),
        Cue.ERROR: _tone(180, 280, amplitude=0.6),
        Cue.WARN: _tone(520, 130, amplitude=0.45),
    }


_WAVES: Final[dict[Cue, np.ndarray]] = _build()


def cue_wave(kind: Cue) -> np.ndarray:
    """The raw samples, exposed separately so tests never have to make noise."""
    return _WAVES[kind]


def play_cue(kind: Cue) -> None:
    """Fire and forget on the default output device.

    Import is local: ``sounddevice`` loads PortAudio, and the one module every
    test imports for waveforms must not drag a DLL along.
    """
    import sounddevice as sd

    sd.play(_WAVES[kind], SAMPLE_RATE, blocking=False)
