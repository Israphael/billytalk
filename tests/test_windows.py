"""``ui/windows/``: the pure display logic, and the frames built live against
a scripted controller — no IPC, no core; the windows' whole world is the
``request`` seam.
"""

from __future__ import annotations

from typing import Any

import pytest
import wx

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
        self.on_devices = None
        self.on_history_cleared = None
        self.apply_language = None

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
        assert [m["type"] for m in fake.sent] == [
            "get_config", "dictionary_get", "autostart_get", "audio_devices",
        ]
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


class _ReplyFrame(wx.Frame):
    def __init__(self, sink: list[Any]) -> None:
        super().__init__(None)
        self._sink = sink

    def on_reply(self, message: Any) -> None:
        self._sink.append(message)  # must not run once the frame is destroyed


def test_reply_to_a_destroyed_window_is_dropped(wx_app) -> None:
    """M2 review, medium: a reply arriving after the user closed the window
    must not touch its dead controls («wrapped C/C++ object deleted»)."""
    import wx

    from billytalk.ui.controller import UiController

    ctl = UiController(_FakePlashka())  # type: ignore[arg-type]
    ctl.send = lambda m: None
    hits: list[Any] = []
    frame = _ReplyFrame(hits)
    ctl._pending[123] = frame.on_reply  # a genuine bound method of the frame
    frame.Destroy()
    wx.Yield()  # let the destroy settle so the frame goes falsy
    ctl.dispatch({"type": "reply", "id": 123, "result": {}})
    assert hits == [], "a reply for a destroyed frame must be dropped"


class _FakePlashka:
    def show(self, look: Any) -> None: ...
    def hide(self) -> None: ...


# --------------------------------------------------------------------------- #
# the first-run wizard (spec §12)
# --------------------------------------------------------------------------- #


class WizardController(FakeController):
    """A controller with the seats the wizard actually uses."""

    def __init__(self, replies: dict[str, Any]) -> None:
        super().__init__(replies)
        self.on_hotkey_captured = None
        self.on_state = None
        self.apply_language = None


_WIZARD_CONFIG = {
    "config": {
        "language": "ru", "ui_language": "auto", "ui_language_effective": "ru",
        "wizard_done": False, "ptt_code": 4099, "audio_input_device": None,
        "groq_model": "whisper-large-v3-turbo", "polish_enabled": False,
        "press_enter_after": False, "retention_minutes": 60, "max_hold_ms": 300000,
        "has_groq_key": False,
    }
}

_AUTOSTART_REPLY = {
    "available": True, "enabled": False, "registered": False,
    "disabled_by_windows": False, "matches_current_exe": True,
}


def _wizard(replies: dict[str, Any] | None = None):
    from billytalk.ui.windows.wizard import WizardFrame

    base: dict[str, Any] = {
        "get_config": _WIZARD_CONFIG,
        "autostart_get": _AUTOSTART_REPLY,
        "autostart_set": _AUTOSTART_REPLY,
    }
    base.update(replies or {})
    controller = WizardController(base)
    return WizardFrame(controller), controller  # type: ignore[arg-type]


def test_mic_line_names_every_measured_outcome(wx_app) -> None:
    """Every probe code the core can answer with has its own sentence — a
    silent microphone and a denied one need different actions from the user."""
    from billytalk.ui.windows.wizard import mic_line

    ok = mic_line({"ok": True, "device": "Микрофон (USB)", "level": 42})
    assert "Микрофон (USB)" in ok and "42" in ok
    lines = {
        code: mic_line({"ok": False, "code": code})
        for code in ("mic_denied", "mic_busy", "no_device", "no_frames")
    }
    assert len(set(lines.values())) == 4, f"outcomes share wording: {lines}"
    assert all(line for line in lines.values())
    # An unknown code still says something actionable rather than nothing.
    assert mic_line({"ok": False, "code": "who_knows"})


def test_wizard_walks_the_seven_steps_of_the_spec(wx_app) -> None:
    from billytalk.ui.windows.wizard import STEP_COUNT

    frame, _c = _wizard()
    try:
        assert STEP_COUNT == 7, "spec §12 names seven steps"
        assert frame._step == 0 and not frame._back.IsEnabled()
        for expected in range(1, STEP_COUNT):
            frame._on_next(None)  # type: ignore[arg-type]
            assert frame._step == expected
        assert frame._next.GetLabel() == "Готово", "the last step finishes"
        assert frame._back.IsEnabled()
    finally:
        frame.Destroy()


def test_wizard_fills_the_bindings_from_the_core(wx_app) -> None:
    frame, _c = _wizard()
    try:
        assert "Mouse 4" in frame._hotkey_label.GetLabel()
        assert "Mouse 4" in frame._test_body.GetLabel()
    finally:
        frame.Destroy()


def test_wizard_mic_step_offers_the_privacy_settings_only_when_denied(wx_app) -> None:
    frame, controller = _wizard({
        "mic_probe": {"ok": False, "code": "mic_denied", "device": None,
                      "inputs": [], "level": 0},
    })
    try:
        assert not frame._mic_settings.IsShown(), "no button before we know"
        frame._on_mic_check(None)  # type: ignore[arg-type]
        assert frame._mic_settings.IsShown()
        assert "микрофон" in frame._mic_status.GetLabel().lower()
        assert any(m["type"] == "mic_probe" for m in controller.sent)
    finally:
        frame.Destroy()


def test_wizard_key_step_saves_then_checks_and_clears_the_field(wx_app) -> None:
    frame, controller = _wizard({
        "set_key": {"stored": True},
        "test_key": {"status": "ok"},
    })
    try:
        form = frame._key_form
        form._field.SetValue("gsk_typed_by_hand")
        form._on_save(None)  # type: ignore[arg-type]
        verbs = [m["type"] for m in controller.sent]
        assert verbs.count("set_key") == 1 and verbs.count("test_key") == 1
        sent_key = next(m for m in controller.sent if m["type"] == "set_key")
        assert sent_key["key"] == "gsk_typed_by_hand"
        assert form._field.GetValue() == "", "the key does not stay on screen"
        assert form.accepted is True
        assert form.status.GetLabel() == "Ключ принят."
    finally:
        frame.Destroy()


def test_wizard_key_step_reports_a_rejected_key_without_pretending(wx_app) -> None:
    frame, _c = _wizard({"set_key": {"stored": True}, "test_key": {"status": "invalid"}})
    try:
        form = frame._key_form
        form._field.SetValue("gsk_wrong")
        form._on_save(None)  # type: ignore[arg-type]
        assert form.accepted is False
        assert "отклонил" in form.status.GetLabel().lower()
    finally:
        frame.Destroy()


def test_wizard_key_step_refuses_an_empty_field_without_asking_the_core(wx_app) -> None:
    frame, controller = _wizard()
    try:
        frame._key_form._on_save(None)  # type: ignore[arg-type]
        assert not any(m["type"] == "set_key" for m in controller.sent)
        assert frame._key_form.status.GetLabel()
    finally:
        frame.Destroy()


def test_wizard_live_test_ignores_dictations_older_than_the_step(wx_app) -> None:
    """The proof is a dictation made *now*; yesterday's history would
    congratulate the user for something they did not just do."""
    import time

    now_ms = int(time.time() * 1000)
    frame, _c = _wizard()
    try:
        frame._go(6)
        stale = {"result": {"rows": [
            {"id": 1, "created_at": now_ms - 86_400_000, "text": "вчерашняя диктовка"},
        ]}, "type": "reply", "id": 1}
        frame._on_test_rows(stale)
        assert not frame.state.get("tested")
        assert "Жду" in frame._test_status.GetLabel()

        fresh = {"result": {"rows": [
            {"id": 2, "created_at": now_ms + 5_000, "text": "проверка связи"},
        ]}, "type": "reply", "id": 2}
        frame._on_test_rows(fresh)
        assert frame.state["tested"] is True
        assert "проверка связи" in frame._test_status.GetLabel()
    finally:
        frame.Destroy()


def test_wizard_leaves_the_state_seat_clean_when_it_closes(wx_app) -> None:
    """A dead window's callback firing on the next state push is the same
    «wrapped C/C++ object deleted» the M2 review found in the reply table."""
    frame, controller = _wizard()
    frame._go(6)
    assert controller.on_state is not None, "the live test takes the seat"
    frame.Close()
    wx.Yield()
    assert controller.on_state is None


def test_wizard_finish_marks_the_wizard_done(wx_app, monkeypatch) -> None:
    import wx as wx_module

    frame, controller = _wizard()
    monkeypatch.setattr(wx_module, "MessageBox", lambda *a, **k: wx_module.OK)
    try:
        frame._go(6)
        frame._on_next(None)  # type: ignore[arg-type]
        patch = next(
            m for m in controller.sent
            if m["type"] == "set_config" and "wizard_done" in m.get("patch", {})
        )
        assert patch["patch"] == {"wizard_done": True}
    finally:
        if frame:
            frame.Destroy()


def test_wizard_autostart_checkbox_speaks_to_the_core(wx_app) -> None:
    frame, controller = _wizard()
    try:
        frame._go(6)
        frame._autostart.SetValue(True)
        frame._on_autostart(None)  # type: ignore[arg-type]
        sent = next(m for m in controller.sent if m["type"] == "autostart_set")
        assert sent["enabled"] is True
    finally:
        frame.Destroy()


def test_wizard_language_switch_on_the_last_step_hands_the_seat_to_the_twin(
    wx_app,
) -> None:
    """The language switch destroys this window and builds a twin. If the old
    frame released the state seat AFTER the twin took it, the live test would
    be deaf; if it never released it, a state push would call a destroyed
    frame's method («wrapped C/C++ object deleted»)."""
    frame, controller = _wizard({
        "set_config": _WIZARD_CONFIG,
    })
    applied: list[str] = []
    controller.apply_language = applied.append
    try:
        frame._go(6)
        seat_before = controller.on_state
        assert seat_before is not None
        frame._language.SetSelection(1)  # English

        event = wx.CommandEvent(wx.EVT_CHOICE.typeId, frame._language.GetId())
        event.SetEventObject(frame._language)
        frame._on_ui_language(event)
        wx.Yield()

        assert applied == ["ru"], "the menu and the other windows are told once"
        assert controller.on_state is not None, "the twin holds the seat"
        assert controller.on_state is not seat_before, "and it is not the old one"
        patch = next(
            m for m in controller.sent
            if m["type"] == "set_config" and "ui_language" in m.get("patch", {})
        )
        assert patch["patch"] == {"ui_language": "en"}
    finally:
        for window in list(wx.GetTopLevelWindows()):
            if window and window.GetTitle().startswith("BillyTalk"):
                window.Destroy()
        wx.Yield()


# --------------------------------------------------------------------------- #
# cycle-3 review fixes
# --------------------------------------------------------------------------- #


def test_key_form_callbacks_are_bound_methods_so_a_closed_dialog_is_dropped(
    wx_app,
) -> None:
    """Review, low: a test_key answer is a network round trip (the pool waits
    up to 30 s). The controller drops a reply for a destroyed window — but it
    recognises one only through __self__, which a closure does not have."""
    from billytalk.ui.controller import UiController
    from billytalk.ui.windows.wizard import KeyDialog

    frame, controller = _wizard()
    try:
        form = frame._key_form
        for callback in (form._on_stored, form._on_checked, frame._on_mic_answered,
                         frame._on_language_applied):
            assert getattr(callback, "__self__", None) is not None, (
                f"{callback} is a closure; a late reply would touch dead controls"
            )
    finally:
        frame.Destroy()

    # And the guard really drops it: the reply for a destroyed dialog must not
    # reach its controls (before the fix this raised «wrapped C/C++ object
    # deleted» instead of being dropped).
    real = UiController(_FakePlashka())  # type: ignore[arg-type]
    real.send = lambda _m: None
    dialog = KeyDialog(real)
    real._pending[7] = dialog.form._on_checked
    dialog.Destroy()
    wx.Yield()
    real.dispatch({"type": "reply", "id": 7, "result": {"status": "ok"}})


def test_release_test_does_not_steal_another_wizards_seat(wx_app) -> None:
    """Review, low: one seat, two possible wizards (first run + «Пройти
    заново»). Closing the second must not deafen the first."""
    first, controller = _wizard()
    second = None
    try:
        first._go(6)
        seat = controller.on_state
        assert seat is not None

        from billytalk.ui.windows.wizard import WizardFrame

        second = WizardFrame(controller)  # type: ignore[arg-type]
        second._go(0)      # step 1: it never takes the seat
        second._release_test()
        assert controller.on_state is seat, "the live wizard keeps its seat"

        first._release_test()
        assert controller.on_state is None, "its owner does give it up"
    finally:
        if second:
            second.Destroy()
        first.Destroy()


def test_settings_microphone_list_offers_the_system_default_first(wx_app) -> None:
    """Spec §5's ranked pick: index 0 is «system default», which the config
    spells as None — the same value the fallback lands on when a chosen device
    disappears, so the two agree by construction."""
    from billytalk.ui.windows.settings import SettingsFrame

    config = {"config": {**_CONFIG_REPLY["config"], "audio_input_device": "Realtek"}}
    fake = FakeController({
        "get_config": config,
        "dictionary_get": _RULES_REPLY,
        "audio_devices": {"inputs": ["Микрофон (USB)", "Realtek"]},
    })
    frame = SettingsFrame(fake)  # type: ignore[arg-type]
    try:
        assert frame._mic_choice.GetString(0) == "Системный по умолчанию"
        assert frame._mic_choice.GetStringSelection() == "Realtek"

        frame._mic_choice.SetSelection(0)
        frame._on_mic_choice(None)  # type: ignore[arg-type]
        patch = next(m for m in reversed(fake.sent) if m["type"] == "set_config")
        assert patch["patch"] == {"audio_input_device": None}

        frame._mic_choice.SetSelection(1)
        frame._on_mic_choice(None)  # type: ignore[arg-type]
        patch = next(m for m in reversed(fake.sent) if m["type"] == "set_config")
        assert patch["patch"] == {"audio_input_device": "Микрофон (USB)"}
    finally:
        frame.Destroy()


def test_settings_updates_the_device_list_on_the_push(wx_app) -> None:
    """A headset plugged in with the window open changes the list here and
    now — the core pushes device_list_changed for exactly this."""
    from billytalk.ui.windows.settings import SettingsFrame

    fake = FakeController({
        "get_config": _CONFIG_REPLY, "dictionary_get": _RULES_REPLY,
        "audio_devices": {"inputs": ["Realtek"]},
    })
    frame = SettingsFrame(fake)  # type: ignore[arg-type]
    try:
        assert fake.on_devices is not None, "the window takes the seat"
        assert frame._mic_choice.GetCount() == 2
        fake.on_devices(["Realtek", "Гарнитура Bluetooth"])
        assert frame._mic_choice.GetCount() == 3
        assert frame._mic_choice.GetString(2) == "Гарнитура Bluetooth"
    finally:
        frame.Destroy()
    wx.Yield()
    # A push arriving after the window died must not touch its controls.
    if fake.on_devices is not None:
        fake.on_devices(["Realtek"])


def test_settings_hints_wrap_instead_of_being_clipped(wx_app) -> None:
    """The grey sub-labels are where the window explains itself («что попадает
    в журнал», «что Windows решает за нас»); the control on the right takes
    its natural width, so an unwrapped hint was cut mid-sentence. The dynamic
    one — autostart — must survive SetLabel too, which discards wrapping."""
    from billytalk.ui.windows.settings import _HINT_WRAP, SettingsFrame

    fake = FakeController({"get_config": _CONFIG_REPLY, "dictionary_get": _RULES_REPLY})
    frame = SettingsFrame(fake)  # type: ignore[arg-type]
    try:
        hint = frame._autostart_hint
        assert hint is not None
        for state in (
            {"available": True, "enabled": False, "disabled_by_windows": True},
            {"available": False, "enabled": False, "disabled_by_windows": False},
            {"available": True, "enabled": True, "disabled_by_windows": False},
        ):
            frame._on_autostart({"type": "reply", "id": 0, "result": state})
            assert hint.GetSize().width <= _HINT_WRAP + 8, (
                f"the hint runs past its column: {hint.GetLabel()!r}"
            )
            assert hint.GetLabel(), "and it still says something"
    finally:
        frame.Destroy()


def test_history_window_empties_itself_when_the_core_says_it_cleared(wx_app) -> None:
    """Cycle-3 review, medium: ids are reused after a delete, so a stale row on
    screen is not merely out of date — «Вставить» on it would paste whatever
    dictation owns that id now, and report «Вставлено»."""
    from billytalk.ui.windows.history import HistoryFrame

    fake = FakeController({"history_search": _ROWS_REPLY})
    fake.on_history_cleared = None
    frame = HistoryFrame(fake)  # type: ignore[arg-type]
    try:
        assert frame._list.GetItemCount() == 2
        assert fake.on_history_cleared is not None, "the window takes the seat"

        fake.on_history_cleared()

        assert frame._list.GetItemCount() == 0, "nothing stale is left to click"
        assert frame._rows == []
        assert "очищена" in frame._footer.GetLabel().lower()
    finally:
        frame.Destroy()
    wx.Yield()
    if fake.on_history_cleared is not None:
        fake.on_history_cleared()  # a push after death must not touch controls


def test_settings_tells_busy_apart_from_broken_when_clearing(wx_app) -> None:
    """Cycle-3 review: telling a broken database that it is busy leaves the
    user pressing the button forever."""
    from billytalk.ui.windows.settings import SettingsFrame

    fake = FakeController({"get_config": _CONFIG_REPLY, "dictionary_get": _RULES_REPLY})
    frame = SettingsFrame(fake)  # type: ignore[arg-type]
    try:
        frame._on_history_cleared({"type": "reply", "id": 1, "error": "busy"})
        busy = frame.GetStatusBar().GetStatusText()
        frame._on_history_cleared({"type": "reply", "id": 2, "error": "clear_failed"})
        broken = frame.GetStatusBar().GetStatusText()
        assert busy != broken, "one message for two different situations"
        assert "диктовка" in busy.lower()

        frame._on_history_cleared(
            {"type": "reply", "id": 3, "result": {"rows": 4, "files": 2}}
        )
        done = frame.GetStatusBar().GetStatusText()
        assert "4" in done and "2" in done
    finally:
        frame.Destroy()


def test_settings_keeps_showing_a_device_that_went_away(wx_app) -> None:
    """Cycle-3 review, low: unplugging the chosen headset must not make the
    window claim «system default» is selected — the setting did not change,
    and wxMSW sends no EVT_CHOICE for an already-selected item, so the user
    could not have put it back."""
    from billytalk.ui.windows.settings import SettingsFrame

    config = {"config": {**_CONFIG_REPLY["config"],
                         "audio_input_device": "Гарнитура Bluetooth"}}
    fake = FakeController({
        "get_config": config, "dictionary_get": _RULES_REPLY,
        "audio_devices": {"inputs": ["Гарнитура Bluetooth", "Realtek"]},
    })
    frame = SettingsFrame(fake)  # type: ignore[arg-type]
    try:
        assert frame._mic_choice.GetStringSelection() == "Гарнитура Bluetooth"

        fake.on_devices(["Realtek"])  # the headset is unplugged

        shown = frame._mic_choice.GetStringSelection()
        assert "Гарнитура Bluetooth" in shown, "the choice is still the choice"
        assert "недоступно" in shown, "and it is honest about being away"

        # And «system default» is reachable: selecting it is a real change.
        frame._mic_choice.SetSelection(0)
        frame._on_mic_choice(None)  # type: ignore[arg-type]
        patch = next(m for m in reversed(fake.sent) if m["type"] == "set_config")
        assert patch["patch"] == {"audio_input_device": None}
    finally:
        frame.Destroy()
