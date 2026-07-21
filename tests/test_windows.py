"""``ui/windows/``: the pure display logic, and the frames built live against
a scripted controller — no IPC, no core; the windows' whole world is the
``request`` seam.
"""

from __future__ import annotations

from typing import Any

import pytest

from billytalk.ui.windows.history import (
    insert_outcome_line,
    matches_filter,
    status_label,
    time_label,
)
from billytalk.ui.windows.settings import binding_label


# --------------------------------------------------------------------------- #
# pure display logic
# --------------------------------------------------------------------------- #


def test_status_labels_speak_the_mockups_language() -> None:
    assert status_label("inserted") == "Вставлено"
    assert status_label("left_on_clipboard") == "В буфере · Ctrl+V"
    assert status_label("pending_retry") == "Ждёт связи"
    assert status_label("verify_impossible") == "Без подтверждения"
    assert status_label("transcribe_failed", "key_invalid") == (
        "Ошибка расшифровки · key_invalid"
    )
    assert status_label(None) == "—"


def test_filters_partition_the_statuses() -> None:
    assert matches_filter("inserted", "all")
    assert matches_filter("inserted", "delivered")
    assert matches_filter("verify_impossible", "delivered")
    assert matches_filter("focus_lost", "clipboard")
    assert matches_filter("pending_retry", "waiting")
    assert matches_filter("cancelled", "other")
    assert not matches_filter("inserted", "other")
    assert not matches_filter("cancelled", "delivered")


def test_insert_outcome_always_carries_an_action() -> None:
    for status in ("inserted", "verify_impossible", "left_on_clipboard",
                   "focus_lost", "blocked_secure", "who_knows", None):
        line = insert_outcome_line(status)
        assert line, f"no line for {status}"
    assert "Ctrl+V" in insert_outcome_line("left_on_clipboard")


def test_binding_label_names_the_unified_codes() -> None:
    assert binding_label(4099) == "Mouse 4"
    assert binding_label(4100) == "Mouse 5"
    assert binding_label(65) == "Код 65"


def test_time_label_is_short_for_today() -> None:
    import time

    now_ms = int(time.time() * 1000)
    assert len(time_label(now_ms)) == 5  # HH:MM
    week_ago = now_ms - 7 * 24 * 3600 * 1000
    assert len(time_label(week_ago)) > 5  # DD.MM HH:MM


# --------------------------------------------------------------------------- #
# the frames, live, over a scripted controller
# --------------------------------------------------------------------------- #


class FakeController:
    """Answers every request instantly from a script — the windows never know."""

    def __init__(self, replies: dict[str, Any]) -> None:
        self.replies = replies
        self.sent: list[dict[str, Any]] = []

    def request(self, message: dict[str, Any], on_reply) -> None:
        self.sent.append(message)
        result = self.replies.get(message["type"])
        if result is not None:
            on_reply({"type": "reply", "id": 0, "result": result})


_CONFIG_REPLY = {
    "config": {
        "language": "ru", "ptt_code": 4099, "audio_input_device": None,
        "groq_model": "whisper-large-v3-turbo", "polish_enabled": False,
        "press_enter_after": False, "retention_minutes": 60,
        "max_hold_ms": 300000, "has_groq_key": True,
    }
}

_RULES_REPLY = {"rules": [
    {"type": "replace", "pat": "прот", "repl": "прод", "enabled": True},
]}

_ROWS_REPLY = {"rows": [
    {"id": 7, "created_at": 1_700_000_000_000, "text": "выкати на прод",
     "status": "inserted", "error_code": None, "target_app": "notepad.exe",
     "duration_ms": 900, "retry_count": 0},
    {"id": 8, "created_at": 1_700_000_100_000, "text": "черновик письма",
     "status": "pending_retry", "error_code": None, "target_app": "chrome.exe",
     "duration_ms": 1200, "retry_count": 2},
], "total": 12}


def test_settings_frame_fills_from_the_core_replies(wx_app) -> None:
    from billytalk.ui.windows.settings import SettingsFrame

    fake = FakeController({"get_config": _CONFIG_REPLY, "dictionary_get": _RULES_REPLY})
    frame = SettingsFrame(fake)  # type: ignore[arg-type]
    try:
        assert frame._ptt_label.GetLabel() == "Mouse 4"
        assert frame._key_badge.GetLabel() == "Сохранён"
        assert frame._model_label.GetLabel() == "whisper-large-v3-turbo"
        assert frame._rules_list.GetItemCount() == 1
        assert frame._rules_list.GetItemText(0, 1) == "прот"
        assert [m["type"] for m in fake.sent] == ["get_config", "dictionary_get"]
    finally:
        frame.Destroy()


def test_settings_language_change_sends_a_patch(wx_app) -> None:
    from billytalk.ui.windows.settings import SettingsFrame

    fake = FakeController({"get_config": _CONFIG_REPLY, "dictionary_get": _RULES_REPLY})
    frame = SettingsFrame(fake)  # type: ignore[arg-type]
    try:
        frame._language.SetSelection(1)  # Английский
        frame._on_language(None)  # type: ignore[arg-type]
        patch = next(m for m in fake.sent if m["type"] == "set_config")
        assert patch["patch"] == {"language": "en"}
    finally:
        frame.Destroy()


def test_settings_rule_delete_sends_the_full_new_list(wx_app) -> None:
    from billytalk.ui.windows.settings import SettingsFrame

    fake = FakeController({"get_config": _CONFIG_REPLY, "dictionary_get": _RULES_REPLY})
    frame = SettingsFrame(fake)  # type: ignore[arg-type]
    try:
        frame._rules_list.Select(0)
        frame._on_rule_delete(None)  # type: ignore[arg-type]
        sent = next(m for m in fake.sent if m["type"] == "dictionary_set")
        assert sent["rules"] == []
    finally:
        frame.Destroy()


def test_history_frame_lists_rows_and_the_footer_counts(wx_app) -> None:
    from billytalk.ui.windows.history import HistoryFrame

    fake = FakeController({"history_search": _ROWS_REPLY})
    frame = HistoryFrame(fake)  # type: ignore[arg-type]
    try:
        assert frame._list.GetItemCount() == 2
        assert frame._list.GetItemText(0, 1) == "выкати на прод"
        assert frame._list.GetItemText(0, 2) == "notepad"
        assert "12 записей" in frame._footer.GetLabel()
        assert fake.sent[0] == {"type": "history_search", "query": "", "limit": 100}
    finally:
        frame.Destroy()


def test_history_filter_narrows_the_visible_page(wx_app) -> None:
    from billytalk.ui.windows.history import HistoryFrame

    fake = FakeController({"history_search": _ROWS_REPLY})
    frame = HistoryFrame(fake)  # type: ignore[arg-type]
    try:
        frame._filter.SetSelection(3)  # «Ждёт связи»
        frame._refill()
        assert frame._list.GetItemCount() == 1
        assert frame._list.GetItemText(0, 1) == "черновик письма"
    finally:
        frame.Destroy()


def test_history_insert_sends_the_row_id_and_reports_the_outcome(wx_app) -> None:
    from billytalk.ui.windows.history import HistoryFrame

    fake = FakeController({
        "history_search": _ROWS_REPLY,
        "history_insert": {"status": "left_on_clipboard"},
    })
    frame = HistoryFrame(fake)  # type: ignore[arg-type]
    try:
        frame._list.Select(0)
        frame._insert_selected()
        sent = next(m for m in fake.sent if m["type"] == "history_insert")
        assert sent["row_id"] == 7
        assert "Ctrl+V" in frame._footer.GetLabel()
    finally:
        frame.Destroy()


def test_hotkey_capture_dialog_asks_the_core_and_closes_on_the_push(wx_app) -> None:
    import wx

    from billytalk.ui.windows.hotkey_capture import HotkeyCaptureDialog

    fake = FakeController({})
    dialog = HotkeyCaptureDialog(fake)  # type: ignore[arg-type]
    try:
        first = fake.sent[0]
        assert first["type"] == "capture_hotkey_start" and first["action"] == "ptt"
        assert fake.on_hotkey_captured is not None, "the dialog must take the push seat"
        fake.on_hotkey_captured(
            {"type": "hotkey_captured", "codes": [4100], "display": "Mouse 5"}
        )
        assert dialog.captured is not None and dialog.captured["codes"] == [4100]
        assert dialog.GetReturnCode() == wx.ID_OK
        assert fake.on_hotkey_captured is None, "the push seat is left clean"
    finally:
        dialog.Destroy()


def test_hotkey_capture_dialog_cancel_sends_the_stop_verb(wx_app) -> None:
    import wx

    from billytalk.ui.windows.hotkey_capture import HotkeyCaptureDialog

    fake = FakeController({})
    dialog = HotkeyCaptureDialog(fake)  # type: ignore[arg-type]
    try:
        dialog._cancel()
        assert [m["type"] for m in fake.sent] == [
            "capture_hotkey_start", "capture_hotkey_stop",
        ]
        assert dialog.GetReturnCode() == wx.ID_CANCEL
    finally:
        dialog.Destroy()
