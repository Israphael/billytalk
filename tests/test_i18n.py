"""``billytalk/i18n``: the two tables and the one lookup.

The point of these tests is drift. A product with two string tables fails in a
way nothing else catches: a key added to one language and forgotten in the
other shows a Russian sentence inside an English window, months later, in a
screenshot from a user. Comparing the key sets is the whole defence.
"""

from __future__ import annotations

import pytest

from billytalk.core.ui_language import resolve_ui_language
from billytalk.i18n import (
    DEFAULT_LANGUAGE,
    LANGUAGES,
    current_language,
    set_language,
    t,
)
from billytalk.i18n.en import STRINGS as EN
from billytalk.i18n.ru import STRINGS as RU


@pytest.fixture(autouse=True)
def _restore_language():
    before = current_language()
    yield
    set_language(before)


def test_the_two_tables_carry_exactly_the_same_keys() -> None:
    missing_in_en = sorted(set(RU) - set(EN))
    missing_in_ru = sorted(set(EN) - set(RU))
    assert missing_in_en == [], f"keys без английского перевода: {missing_in_en}"
    assert missing_in_ru == [], f"keys без русского оригинала: {missing_in_ru}"


def test_no_string_is_empty() -> None:
    for language, table in (("ru", RU), ("en", EN)):
        empty = sorted(key for key, value in table.items() if not value.strip())
        assert empty == [], f"{language}: пустые строки {empty}"


def test_placeholders_match_between_the_languages() -> None:
    """A template whose braces differ between languages formats fine in one and
    silently loses a number in the other — the offline tooltip's «N записей»
    being exactly the case spec §3 makes mandatory."""
    import string

    def fields(template: str) -> set[str]:
        return {
            name for _lit, name, _spec, _conv in string.Formatter().parse(template)
            if name
        }

    for key, ru_value in RU.items():
        assert fields(ru_value) == fields(EN[key]), f"{key}: разные подстановки"


@pytest.mark.parametrize("language", LANGUAGES)
def test_every_key_answers_in_every_language(language: str) -> None:
    set_language(language)
    for key in RU:
        assert t(key) != "" and t(key) is not None


def test_an_unknown_key_returns_itself_visibly() -> None:
    assert t("no.such.key") == "no.such.key"


def test_an_unknown_language_keeps_the_current_one() -> None:
    set_language("ru")
    assert set_language("de") == "ru"
    assert set_language(None) == "ru"


def test_formatting_is_applied_and_a_mismatch_never_raises() -> None:
    set_language("ru")
    assert "7" in t("tray.offline", waiting=7)
    # The wrong keyword must not take a window down mid-build.
    assert t("tray.offline", wrong=7) == RU["tray.offline"]


def test_the_default_language_is_the_fallback_table() -> None:
    assert DEFAULT_LANGUAGE == "ru"
    set_language("en")
    assert t("app.name") == "BillyTalk"


# --------------------------------------------------------------------------- #
# resolving «auto» (core side of harness §1's border)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "setting,system,expected",
    [
        ("ru", "en", "ru"),          # an explicit choice never asks Windows
        ("en", "ru", "en"),
        ("auto", "ru", "ru"),
        ("auto", "en", "en"),
        ("auto", "pt", "ru"),        # a system language we have no table for
        ("de", "en", "ru"),          # a setting we do not understand
        (None, "en", "ru"),
    ],
)
def test_resolve_ui_language(setting: object, system: str, expected: str) -> None:
    assert resolve_ui_language(setting, system=system) == expected


# --------------------------------------------------------------------------- #
# the crash guards (spec §13's other two defences)
# --------------------------------------------------------------------------- #


def test_the_top_level_handler_logs_the_place_and_never_the_message(caplog) -> None:
    """Security review, low: §13 asks for SetErrorMode and a top-level handler
    beside the installer's WER exclusion, and console=False makes them
    load-bearing. The handler must not log the message — an exception whose
    message IS the API key is exactly what the same review found."""
    import sys
    import threading

    from billytalk.core.crash import install_crash_guards, where

    before_sys, before_thread = sys.excepthook, threading.excepthook
    try:
        install_crash_guards("test")
        assert sys.excepthook is not before_sys
        caplog.set_level("DEBUG")
        try:
            raise ValueError("Invalid header value b'Bearer gsk_SECRET'")
        except ValueError as exc:
            sys.excepthook(type(exc), exc, exc.__traceback__)
        assert "gsk_SECRET" not in caplog.text
        assert "ValueError" in caplog.text, "the type is what makes it actionable"
        assert "test_i18n.py" in caplog.text, "and so is the place"
    finally:
        sys.excepthook, threading.excepthook = before_sys, before_thread

    assert where(None) == "?"
