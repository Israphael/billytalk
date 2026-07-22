"""Device enumeration, ranking, and the PortAudio reload (spec §5, research/07 S3).

PortAudio freezes its device list at initialisation: plug in a headset and the
list simply does not contain it. The measured fix (42 ms) is a full library
reload — terminate, ``dlclose``, ``dlopen``, initialise. The spike also showed
the reload *surviving* under a live stream, and research/07 is explicit that
surviving once is not the same as being correct: the library is unloaded from
under a running callback. **Never reload with a stream open.** The machine's
phase used to stand for «a stream is open» — true while the machine owned the
only stream. Cycle 3's microphone probe (wizard step 1) opens a second one,
from a background thread, outside the phases entirely, so the discipline now
lives **here**, next to the dangerous call: :func:`hold_for_probe` claims the
library, :func:`probe_active` lets the caller's gate defer instead of racing,
and :func:`reload_portaudio` refuses rather than unload under a live stream.

Ranking (spec §5, «ранжированный список с автопадением»): the user's ordered
preferences are matched against what is actually present; the first available
wins, and ``None`` (system default) is the floor. The matching is a pure
function of two name lists, so the whole policy tests without PortAudio.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

__all__ = [
    "default_input_device",
    "hold_for_probe",
    "input_device_names",
    "probe_active",
    "resolve_input_device",
    "reload_portaudio",
]

_lock = threading.Lock()
_probe_open = threading.Event()


def probe_active() -> bool:
    """Is a diagnostic probe holding a PortAudio stream right now?

    Read by the core's stream gate, so a device change arriving during a probe
    **defers** — exactly as one arriving mid-recording does — instead of
    racing the lock below.
    """
    return _probe_open.is_set()


@contextmanager
def hold_for_probe(*, timeout: float = 0.5) -> Iterator[bool]:
    """Claim PortAudio for a probe. Yields whether the claim succeeded.

    False means a reload (or another probe) holds it. Both are short, and the
    caller answers «занято» rather than parking a background thread somebody
    is watching a spinner for.
    """
    if not _lock.acquire(timeout=timeout):
        yield False
        return
    _probe_open.set()
    try:
        yield True
    finally:
        _probe_open.clear()
        _lock.release()


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


def reload_portaudio(*, timeout: float = 2.0) -> bool:
    """Refresh the frozen device list. Measured at 42 ms (research/07 S3).

    Returns whether it ran. ``False`` means a probe holds the library and the
    reload was refused: a stale device list costs the user one re-plug, while
    unloading the library under a live stream costs them the process.

    The caller gates on the **capture** stream being closed (machine phase),
    but two other streams exist that the phase cannot see, and both are
    handled here rather than trusted to the caller:

    * a fire-and-forget **cue** (``play_cue`` → ``sd.play``) is a PortAudio
      output stream that can be live in Finalizing/Delivering/Idle — exactly
      when a deferred reload fires (cue-review, veha 3). ``sd.stop()`` closes
      it through the still-loaded library first: a no-op when nothing plays,
      at worst a clipped cue, cheap against a crash;
    * the wizard's **microphone probe** opens an input stream from a
      background thread. It holds :func:`hold_for_probe`, and this lock is the
      other half of that handshake.

    ``sounddevice`` has no public reload API, so the private names are pinned
    by the test suite instead of an upstream promise.
    """
    if not _lock.acquire(timeout=timeout):
        return False
    try:
        import sounddevice as sd

        sd.stop()
        sd._terminate()
        sd._ffi.dlclose(sd._lib)
        sd._lib = sd._ffi.dlopen(sd._libname)
        sd._initialize()
        return True
    finally:
        _lock.release()
