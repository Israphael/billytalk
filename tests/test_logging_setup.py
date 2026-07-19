"""The redaction invariant (harness §6, spec §13)."""

from __future__ import annotations

import logging
from pathlib import Path

from billytalk.core.logging_setup import (
    REDACTED,
    AudioBuffer,
    RedactionFilter,
    Sensitive,
    Transcript,
    configure_logging,
)


def _record(msg: object, *args: object) -> logging.LogRecord:
    return logging.LogRecord("billytalk.test", logging.INFO, __file__, 1, msg, args, None)


def test_sensitive_renders_as_a_constant_and_never_raises() -> None:
    """Spec §13 is explicit that this must not raise.

    A raising ``__repr__`` fires from inside ``except`` blocks, where it turns a
    recoverable paste failure into an unhandled crash — and by spec §3 that crash
    takes the in-flight dictation with it. The privacy mechanism is not allowed to
    lose the user's words.
    """
    secret = Transcript("перезапусти впээс")
    assert repr(secret) == REDACTED
    assert str(secret) == REDACTED
    assert f"{secret}" == REDACTED
    assert "{}".format(secret) == REDACTED  # noqa: UP032
    assert f"{secret!r}" == REDACTED


def test_format_spec_cannot_leak_a_prefix_or_a_length() -> None:
    secret = Transcript("перезапусти впээс в Бразилии")
    assert f"{secret:.3}" == REDACTED
    assert f"{secret:>80}" == REDACTED
    assert REDACTED not in secret.value  # the real text is still reachable by attribute


def test_filter_drops_records_carrying_sensitive_values() -> None:
    f = RedactionFilter()
    assert f.filter(_record("inserted in %s after %d ms", "notepad.exe", 412)) is True
    assert f.filter(_record("transcript: %s", Transcript("hello"))) is False
    assert f.filter(_record(Transcript("hello"))) is False
    assert f.filter(_record("audio %s", AudioBuffer(b"\x00\x01"))) is False


def test_filter_drops_dict_style_arguments_too() -> None:
    """``log.info("%(text)s", {...})`` is a second, easy-to-miss way in."""
    record = logging.LogRecord(
        "billytalk.test", logging.INFO, __file__, 1, "%(text)s",
        ({"text": Transcript("hello")},), None,
    )
    assert isinstance(record.args, dict), "logging unwraps a single mapping argument"
    assert RedactionFilter().filter(record) is False


def test_subclasses_of_sensitive_are_covered() -> None:
    class Polished(Sensitive):
        pass

    assert RedactionFilter().filter(_record("%s", Polished())) is False


def test_configure_logging_attaches_the_filter_and_quiets_third_parties(
    tmp_path: Path,
) -> None:
    root = logging.getLogger()
    saved_handlers, saved_level = root.handlers[:], root.level
    try:
        root.handlers.clear()
        configure_logging(tmp_path / "logs")
        handler = root.handlers[-1]
        assert any(isinstance(f, RedactionFilter) for f in handler.filters), (
            "the filter belongs on the handler: a filter on a logger is not "
            "consulted for records propagating up from its children"
        )
        assert logging.getLogger("urllib3").level == logging.WARNING

        logging.getLogger("billytalk.test").error("text=%s", Transcript("секрет"))
        handler.flush()
        written = (tmp_path / "logs" / "core.log").read_text(encoding="utf-8")
        assert "секрет" not in written
        assert written.strip() == ""
    finally:
        for h in root.handlers:
            h.close()
        root.handlers[:] = saved_handlers
        root.setLevel(saved_level)
