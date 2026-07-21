"""Device enumeration, ranking, and the PortAudio reload (spec §5, research/07 S3).

PortAudio freezes its device list at initialisation: plug in a headset and the
list simply does not contain it. The measured fix (42 ms) is a full library
reload — terminate, ``dlclose``, ``dlopen``, initialise. The spike also showed
the reload *surviving* under a live stream, and research/07 is explicit that
surviving once is not the same as being correct: the library is unloaded from
under a running callback. **Never reload with a stream open.** The driver owns
that discipline; this module just performs the steps.

Ranking (spec §5, «ранжированный список с автопадением»): the user's ordered
preferences are matched against what is actually present; the first available
wins, and ``None`` (system default) is the floor. The matching is a pure
function of two name lists, so the whole policy tests without PortAudio.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "default_input_device",
    "input_device_names",
    "resolve_input_device",
    "reload_portaudio",
]


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


def input_device_names() -> list[str]:
    """Every input-capable device's name, in PortAudio order, deduplicated —
    the raw material for the settings list and for :func:`resolve_input_device`.
    An enumeration failure is an empty list, never an exception (the caller
    then falls back to the system default)."""
    import sounddevice as sd

    try:
        seen: dict[str, None] = {}
        for info in sd.query_devices():
            if info.get("max_input_channels", 0) > 0:
                name = str(info.get("name", "")).strip()
                if name:
                    seen.setdefault(name, None)
        return list(seen)
    except (sd.PortAudioError, ValueError, OSError):
        return []


def resolve_input_device(
    ranking: list[str], available: list[str], *, current: str | None = None
) -> str | None:
    """Spec §5's auto-fallback: the first ranked preference that is present,
    else the current pick if it is still present, else ``None`` (the system
    default). Never returns a name that is not in ``available`` — a device that
    vanished must not be handed to a stream that would fail to open on it.
    """
    for name in ranking:
        if name in available:
            return name
    if current is not None and current in available:
        return current
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
