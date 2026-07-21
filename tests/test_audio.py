"""``audio/``: the trim policy on synthesised signals, FLAC round-trips, cue
distinctness, and two live checks against the real device stack.

The trim tests are the ones that matter (spec §5): the absolute floor is what
keeps a short «да» alive, and it was nearly designed out once already.

The live checks (capture smoke, PortAudio reload) touch real hardware and are
skipped, not failed, on a machine with no input device — на машине заказчика
они обязаны проходить.
"""

from __future__ import annotations

import threading
import time
from itertools import combinations
from pathlib import Path

import numpy as np
import pytest

from billytalk.core.audio.capture import CaptureError, CaptureSession
from billytalk.core.audio.cues import cue_wave
from billytalk.core.audio.encode import encode_flac
from billytalk.core.audio.trim import trim_silence
from billytalk.core.machine.effects import Cue

RATE = 16_000


def _dbfs(db: float) -> float:
    return 32767.0 * 10.0 ** (db / 20.0)


def _tone(ms: int, *, db: float, freq: float = 300.0) -> np.ndarray:
    t = np.arange(int(RATE * ms / 1000)) / RATE
    return (_dbfs(db) * np.sin(2 * np.pi * freq * t)).astype(np.int16)


def _noise(ms: int, *, db: float, seed: int = 42) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return (rng.normal(0.0, _dbfs(db), int(RATE * ms / 1000))).astype(np.int16)


# --------------------------------------------------------------------------- #
# trimming (spec §5)
# --------------------------------------------------------------------------- #


def test_short_all_speech_clip_survives_whole() -> None:
    """The reason the absolute floor exists. On a clip that is entirely speech
    the quietest decile IS speech, the adaptive bar lands above the words, and
    without the −35 dBFS cap a short «да» would be trimmed to nothing."""
    clip = _tone(150, db=-12.0)
    result = trim_silence(clip)
    assert not result.is_empty
    assert np.array_equal(result.samples, clip), "nothing may be cut from it"


def test_leading_and_trailing_silence_cut_with_padding() -> None:
    clip = np.concatenate(
        [_noise(500, db=-70.0, seed=1), _tone(400, db=-12.0), _noise(500, db=-70.0, seed=2)]
    )
    result = trim_silence(clip)
    assert not result.is_empty
    length_ms = len(result.samples) * 1000 // RATE
    # 400 ms of speech plus 180 ms padding each side, give or take a 20 ms frame.
    assert 700 <= length_ms <= 820, f"got {length_ms} ms"
    assert int(np.max(np.abs(result.samples))) >= int(_dbfs(-13.0)), "the tone is inside"


def test_pure_silence_is_empty_and_kept_verbatim() -> None:
    """Empty is a verdict about speech, not permission to discard: spec §4 says
    every press is recorded, so the file keeps what the microphone heard."""
    clip = _noise(600, db=-70.0)
    result = trim_silence(clip)
    assert result.is_empty
    assert np.array_equal(result.samples, clip)


def test_quiet_speech_above_the_floor_is_not_empty() -> None:
    clip = np.concatenate([_noise(200, db=-70.0, seed=3), _tone(300, db=-30.0), _noise(200, db=-70.0, seed=4)])
    result = trim_silence(clip)
    assert not result.is_empty


def test_a_click_above_the_floor_is_never_discarded() -> None:
    clip = _noise(400, db=-70.0, seed=5)
    clip[3200] = np.int16(16000)  # one loud click
    result = trim_silence(clip)
    assert not result.is_empty, "peak above −35 dBFS: never discarded (spec §5)"


def test_trim_preserves_dtype_and_rejects_wrong_dtype() -> None:
    assert trim_silence(_tone(100, db=-12.0)).samples.dtype == np.int16
    with pytest.raises(TypeError):
        trim_silence(_tone(100, db=-12.0).astype(np.float32))


def test_sub_frame_clips_classify_instead_of_crashing() -> None:
    """Review round 1, critical: a clip shorter than one 20 ms frame sits on
    the spec §3 durability path (device yanked after its first PortAudio
    block) and used to raise ValueError, unwinding the whole driver thread."""
    for n in (1, 159, 160, 319):
        quiet = np.zeros(n, dtype=np.int16)
        result = trim_silence(quiet)
        assert result.is_empty, f"{n} silent samples must be empty, not a crash"
        assert len(result.samples) == n, "kept verbatim, like every empty clip"

    loud = np.zeros(200, dtype=np.int16)
    loud[50] = np.int16(16000)
    result = trim_silence(loud)
    assert not result.is_empty, "peak above the floor: never discarded, even sub-frame"
    assert len(result.samples) == 200


def test_trim_result_repr_never_renders_samples() -> None:
    """Spec §13: audio heads the never-log list, and a dataclass auto-repr of
    an ndarray prints actual sample values past the RedactionFilter."""
    result = trim_silence(_tone(100, db=-12.0))
    assert "samples" not in repr(result)


def test_padding_never_reaches_outside_the_clip() -> None:
    clip = _tone(100, db=-12.0)  # speech starts at sample 0
    result = trim_silence(clip)
    assert len(result.samples) <= len(clip)


# --------------------------------------------------------------------------- #
# encoding
# --------------------------------------------------------------------------- #


def test_flac_round_trip_is_lossless(tmp_path: Path) -> None:
    import soundfile as sf

    clip = _tone(200, db=-12.0)
    path = encode_flac(clip, tmp_path / "audio" / "clip.flac")
    assert path.exists(), "parent directories are created — durability path"

    decoded, rate = sf.read(path, dtype="int16")
    assert rate == RATE
    assert np.array_equal(decoded, clip), "FLAC is lossless; anything else is a bug"


def test_encode_rejects_wrong_dtype(tmp_path: Path) -> None:
    with pytest.raises(TypeError):
        encode_flac(_tone(50, db=-12.0).astype(np.float64), tmp_path / "x.flac")


# --------------------------------------------------------------------------- #
# cues (spec §5)
# --------------------------------------------------------------------------- #


def test_all_six_cues_exist_and_are_pairwise_distinct() -> None:
    waves = {kind: cue_wave(kind) for kind in Cue}
    assert len(waves) == 6
    for wave in waves.values():
        assert wave.dtype == np.int16 and int(np.max(np.abs(wave))) > 0
    for a, b in combinations(Cue, 2):
        wa, wb = waves[a], waves[b]
        assert len(wa) != len(wb) or not np.array_equal(wa, wb), f"{a} == {b}"


def test_clipboard_cue_is_the_loudest() -> None:
    """Spec §5: it is the one channel that survives focus assist and a hidden
    tray icon, and it must be loud and unmistakable."""
    clipboard = int(np.max(np.abs(cue_wave(Cue.CLIPBOARD))))
    for kind in Cue:
        if kind is not Cue.CLIPBOARD:
            assert clipboard > int(np.max(np.abs(cue_wave(kind))))


# --------------------------------------------------------------------------- #
# live device checks — skip without hardware, mandatory on the customer's box
# --------------------------------------------------------------------------- #


def test_capture_smoke_on_the_real_default_device() -> None:
    """Opens the actual default microphone for a third of a second.

    The one behavioural contract worth hardware time: ``on_started`` fires from
    the *first frame*, because CaptureStarted is what a deferred release waits
    for and when the 250 ms clock starts. (This test waits on real audio
    hardware; the no-sleep rule of harness §8 governs the machine tests, not a
    smoke test whose subject is the passage of frames.)
    """
    from billytalk.core.audio.devices import default_input_device

    if default_input_device() is None:
        pytest.skip("no default input device on this machine")

    started = threading.Event()
    session = CaptureSession(on_started=started.set)
    try:
        session.start()
    except CaptureError as exc:
        pytest.skip(f"input stream refused: {exc.code.value}")

    try:
        assert started.wait(2.0), "first frame must arrive; CaptureStarted depends on it"
        time.sleep(0.15)
    finally:
        data = session.stop(tail_ms=100)

    assert data.dtype == np.int16 and data.ndim == 1
    assert len(data) >= int(0.15 * RATE), "the tail and the body were both captured"


def test_zz_portaudio_reload_survives_with_no_stream_open() -> None:
    """research/07 S3: the reload works (42 ms) but only with streams stopped.
    Runs last in this file so no capture session is alive. Also pins the private
    sounddevice names the reload depends on — an upstream rename must fail here,
    not in the field."""
    sd = pytest.importorskip("sounddevice")
    for name in ("_terminate", "_ffi", "_lib", "_libname", "_initialize"):
        assert hasattr(sd, name), f"sounddevice.{name} gone — reload recipe broke"

    from billytalk.core.audio.devices import reload_portaudio

    reload_portaudio()
    assert len(sd.query_devices()) >= 0  # the library is alive and answering


def test_reload_stops_a_playing_stream_before_unloading(monkeypatch) -> None:
    """cue-review, medium: a fire-and-forget cue is a second PortAudio stream;
    the reload must stop it (through the still-loaded library) BEFORE it
    unloads, or it frees the DLL under the playing cue. Pins the order:
    sd.stop() precedes the terminate/dlclose. A fake sounddevice module records
    the call order — the real _ffi.dlclose is a read-only cffi attribute."""
    import sys
    from types import SimpleNamespace

    order: list[str] = []
    fake = SimpleNamespace(
        _lib="lib-old",
        _libname="portaudio",
        _ffi=SimpleNamespace(
            dlclose=lambda lib: order.append("dlclose"),
            dlopen=lambda name: (order.append("dlopen"), "lib-new")[1],
        ),
        stop=lambda: order.append("stop"),
        _terminate=lambda: order.append("terminate"),
        _initialize=lambda: order.append("init"),
    )
    monkeypatch.setitem(sys.modules, "sounddevice", fake)

    from billytalk.core.audio.devices import reload_portaudio

    reload_portaudio()

    assert order and order[0] == "stop", "the play stream closes before the library unloads"
    assert order.index("stop") < order.index("terminate") < order.index("dlclose")
    assert fake._lib == "lib-new", "the freshly dlopened handle replaced the old one"


# --------------------------------------------------------------------------- #
# ranked microphone with auto-fallback (spec §5) — pure
# --------------------------------------------------------------------------- #


def test_resolve_input_prefers_the_first_available_ranked_name() -> None:
    from billytalk.core.audio.devices import resolve_input_device

    available = ["Realtek Array", "USB Mic"]
    ranking = ["Razer BlackShark V2", "USB Mic", "Realtek Array"]
    # The top preference is unplugged; the next one present wins (auto-fallback).
    assert resolve_input_device(ranking, available) == "USB Mic"


def test_resolve_input_keeps_the_current_pick_when_ranking_is_silent() -> None:
    from billytalk.core.audio.devices import resolve_input_device

    available = ["USB Mic", "Realtek Array"]
    assert resolve_input_device([], available, current="Realtek Array") == "Realtek Array"


def test_resolve_input_never_returns_a_vanished_device() -> None:
    from billytalk.core.audio.devices import resolve_input_device

    available = ["USB Mic"]
    # ranking and current both name a device that is gone → system default.
    got = resolve_input_device(["Razer"], available, current="Realtek Array")
    assert got is None


def test_resolve_input_falls_back_to_the_default_when_nothing_matches() -> None:
    from billytalk.core.audio.devices import resolve_input_device

    assert resolve_input_device(["A", "B"], ["C", "D"]) is None
