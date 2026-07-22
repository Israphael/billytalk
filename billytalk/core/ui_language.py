"""Which language the windows and notifications speak (config ``ui_language``).

The setting holds ``"auto"``, ``"ru"`` or ``"en"``. Only the **core** resolves
``"auto"`` — reading the Windows UI language is a ctypes call, and harness §1
keeps ctypes out of ``ui/``. The resolved code travels to the interface inside
``get_config`` (``ui_language_effective``), so the interface only ever hands
:func:`billytalk.i18n.set_language` a concrete code.

Unknown system languages resolve to English, not to Russian: a table with two
languages and a user whose Windows is neither is far more likely to read
English. The owner of an English-language Windows who wants Russian says so
once, in the wizard or in settings, and the answer is stored.
"""

from __future__ import annotations

from typing import Final

from ..i18n import DEFAULT_LANGUAGE, LANGUAGES

__all__ = ["UI_LANGUAGE_SETTINGS", "resolve_ui_language", "system_ui_language"]

UI_LANGUAGE_SETTINGS: Final = ("auto", *LANGUAGES)

_LANGID_RUSSIAN: Final = 0x19
"""Primary language id of every Russian sublanguage (LANGID low 10 bits)."""


def system_ui_language() -> str:
    """``"ru"`` when Windows itself is Russian, otherwise ``"en"``.

    A failure to ask (a non-Windows test runner, a stripped API set) is not an
    error worth raising for a label: it answers with the fallback.
    """
    try:
        import ctypes as ct

        langid = int(ct.windll.kernel32.GetUserDefaultUILanguage())
    except Exception:
        return "en"
    return "ru" if (langid & 0x3FF) == _LANGID_RUSSIAN else "en"


def resolve_ui_language(setting: object, *, system: str | None = None) -> str:
    """Fold the stored setting into a language the tables actually have.

    ``system`` is injected by the tests; production leaves it out and the
    Windows answer is read on demand — and only for ``"auto"``, so an explicit
    choice never pays a syscall.
    """
    if setting in LANGUAGES:
        return str(setting)
    if setting == "auto":
        resolved = system if system is not None else system_ui_language()
        return resolved if resolved in LANGUAGES else DEFAULT_LANGUAGE
    return DEFAULT_LANGUAGE
