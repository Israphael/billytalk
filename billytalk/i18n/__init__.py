"""Interface strings by key (harness §7: «код стабилен, текст локализуется по ключу»).

Two tables, ``ru`` and ``en``, and one lookup. The module is **pure data plus a
dict read** — no wx, no ctypes, no I/O — because both processes need it: the
interface for its windows, the core for the tray tooltip and the notification
that a failure carries. Putting it under ``ui/`` (the harness §1 layout) would
have forced the core to import from the interface package for a string table,
which is the wrong direction of dependency; the border rule it protects
(ctypes stays out of ``ui/``) is untouched — nothing here imports ctypes.

Resolving «auto» to a real language reads the Windows UI language and therefore
lives in :mod:`billytalk.core.ui_language`, on the core's side of that same
border. The core resolves it once and ships the answer to the interface in
``get_config``; the interface only ever calls :func:`set_language` with a
concrete code.

Lookup never raises and never renders a blank label: a key missing from the
active language falls back to Russian, and a key missing from both returns the
key itself — a visible, greppable defect instead of an empty window.
"""

from __future__ import annotations

from typing import Any, Final

from .en import STRINGS as _EN
from .ru import STRINGS as _RU

__all__ = ["LANGUAGES", "DEFAULT_LANGUAGE", "current_language", "set_language", "t"]

LANGUAGES: Final = ("ru", "en")
DEFAULT_LANGUAGE: Final = "ru"

_TABLES: Final[dict[str, dict[str, str]]] = {"ru": _RU, "en": _EN}

_current = DEFAULT_LANGUAGE


def current_language() -> str:
    return _current


def set_language(language: str | None) -> str:
    """Point the lookup at ``language``; unknown codes keep the default.

    Returns what is actually in effect, so callers can log or display it
    without asking again.
    """
    global _current
    if language in _TABLES:
        _current = language
    return _current


def t(key: str, /, **kwargs: Any) -> str:
    """The string for ``key`` in the active language, ``str.format``-ed.

    ``key`` is positional-only on purpose: templates are free to use ``{key}``
    as a placeholder (the wizard says «Сейчас: {key}» about the dictation
    button), and without the ``/`` that call would collide with this
    parameter's own name.

    A formatting error (a template and a call that disagree on placeholders)
    returns the unformatted template rather than raising: a label with a brace
    in it is a bug to see and fix, a crash inside a window build is a product
    that does not open.
    """
    template = _TABLES.get(_current, _RU).get(key) or _RU.get(key)
    if template is None:
        return key
    if not kwargs:
        return template
    try:
        return template.format(**kwargs)
    except (KeyError, IndexError, ValueError):
        return template
