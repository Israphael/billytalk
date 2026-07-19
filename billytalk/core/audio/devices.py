"""Device enumeration and the PortAudio reload (spec §5, research/07 S3).

PortAudio freezes its device list at initialisation: plug in a headset and the
list simply does not contain it. The measured fix (42 ms) is a full library
reload — terminate, ``dlclose``, ``dlopen``, initialise. The spike also showed
the reload *surviving* under a live stream, and research/07 is explicit that
surviving once is not the same as being correct: the library is unloaded from
under a running callback. **Never reload with a stream open.** The driver owns
that discipline; this module just performs the steps.

MVP-0 cycle 1 uses the default device only; ranking and hot-swap arrive in
cycle 2 with the UI.
"""

from __future__ import annotations

from typing import Any

__all__ = ["default_input_device", "reload_portaudio"]


def default_input_device() -> dict[str, Any] | None:
    """The default input device's info, or ``None`` if there is none."""
    import sounddevice as sd

    try:
        index = sd.default.device[0]
        if index is None or index < 0:
            return None
        info: dict[str, Any] = sd.query_devices(index)
        return info
    except (sd.PortAudioError, ValueError):
        return None


def reload_portaudio() -> None:
    """Refresh the frozen device list. Measured at 42 ms (research/07 S3).

    Preconditions are the caller's: **no stream may be open.** The sequence is
    the one the spike verified on this exact stack; sounddevice has no public
    API for it, so the private names are pinned by the test suite instead of an
    upstream promise.
    """
    import sounddevice as sd

    sd._terminate()
    sd._ffi.dlclose(sd._lib)
    sd._lib = sd._ffi.dlopen(sd._libname)
    sd._initialize()
