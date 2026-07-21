"""The settings window (spec §11; the approved mockup's six sections).

Windows-Settings layout: navigation list on the left, «label + control» rows
on the right. Everything the window changes travels as a ``set_config`` /
``dictionary_set`` verb and the controls re-fill from the reply — the config
on screen is always the core's word, never a local guess.

What is greyed out is greyed honestly: autostart ships with the installer
(cycle 3), binding capture is milestone M3, device ranking is M4, the key
replacement lives in the wizard. A disabled control with a reason beats a
dead one.
"""

from __future__ import annotations

from typing import Any

import wx

from ... import __version__
from ...core.hooks.keycodes import is_mouse_code, mouse_number
from ..controller import UiController
from . import dress

__all__ = ["SettingsFrame", "binding_label"]

_SECTIONS = ("Общие", "Привязки", "Микрофон", "Расшифровка", "Словарь", "О программе")

_LANGUAGES = (("ru", "Русский"), ("en", "Английский"))

_RULE_TYPES = (("normalize", "написание"), ("replace", "замена"))


def binding_label(code: int) -> str:
    """The human name of a unified-space key code (spec §2): mouse codes are
    offset by 0x1000, so 4099 is the default Mouse 4."""
    if is_mouse_code(code):
        return f"Mouse {mouse_number(code)}"
    return f"Код {code}"


class SettingsFrame(wx.Frame):
    def __init__(self, controller: UiController, parent: wx.Window | None = None) -> None:
        super().__init__(parent, title="BillyTalk — настройки", size=wx.Size(840, 560))
        self._c = controller
        self._config: dict[str, Any] = {}
        self._rules: list[dict[str, Any]] = []

        root = wx.Panel(self)
        self._nav = wx.ListBox(root, choices=list(_SECTIONS))
        self._book = wx.Simplebook(root)
        for build_page in (
            self._page_general, self._page_bindings, self._page_mic,
            self._page_stt, self._page_dictionary, self._page_about,
        ):
            self._book.AddPage(build_page(self._book), "")
        self._nav.Bind(wx.EVT_LISTBOX, self._on_section)
        self._nav.SetSelection(0)

        sizer = wx.BoxSizer(wx.HORIZONTAL)
        sizer.Add(self._nav, 0, wx.EXPAND | wx.ALL, 8)
        sizer.Add(self._book, 1, wx.EXPAND | wx.ALL, 8)
        root.SetSizer(sizer)
        self.CreateStatusBar()
        dress(self)
        self.refresh()

    # ------------------------------------------------------------------ #
    # data flow
    # ------------------------------------------------------------------ #

    def refresh(self) -> None:
        self._c.request({"type": "get_config"}, self._on_config)
        self._c.request({"type": "dictionary_get"}, self._on_rules)

    def _on_config(self, frame: dict[str, Any]) -> None:
        if "error" in frame:
            self.SetStatusText("Ядро отклонило изменение настроек")
            return
        self._config = frame["result"]["config"]
        self._fill_from_config()

    def _on_rules(self, frame: dict[str, Any]) -> None:
        if "error" in frame:
            self.SetStatusText("Словарь не сохранился")
            return
        result = frame["result"]
        if "rules" in result:
            self._rules = list(result["rules"])
        else:  # a dictionary_set reply confirms the count; re-pull the truth
            self._c.request({"type": "dictionary_get"}, self._on_rules)
            return
        self._fill_rules()

    def _patch(self, key: str, value: Any) -> None:
        self._c.request({"type": "set_config", "patch": {key: value}}, self._on_config)

    def _send_rules(self) -> None:
        self._c.request(
            {"type": "dictionary_set", "rules": self._rules}, self._on_rules
        )

    # ------------------------------------------------------------------ #
    # pages
    # ------------------------------------------------------------------ #

    def _row(
        self, parent: wx.Window, sizer: wx.Sizer, label: str, sub: str,
        control: wx.Window | None,
    ) -> None:
        line = wx.BoxSizer(wx.HORIZONTAL)
        text = wx.BoxSizer(wx.VERTICAL)
        text.Add(wx.StaticText(parent, label=label))
        if sub:
            hint = wx.StaticText(parent, label=sub)
            hint.SetForegroundColour(
                wx.SystemSettings.GetColour(wx.SYS_COLOUR_GRAYTEXT)
            )
            text.Add(hint)
        line.Add(text, 1, wx.ALIGN_CENTER_VERTICAL)
        if control is not None:
            line.Add(control, 0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 12)
        sizer.Add(line, 0, wx.EXPAND | wx.ALL, 8)

    def _page_general(self, parent: wx.Window) -> wx.Panel:
        page = wx.Panel(parent)
        sizer = wx.BoxSizer(wx.VERTICAL)
        autostart = wx.CheckBox(page, label="")
        autostart.Disable()
        self._row(page, sizer, "Запускать при входе в Windows",
                  "Появится вместе с установщиком (цикл 3)", autostart)
        plashka = wx.CheckBox(page, label="")
        plashka.SetValue(True)
        plashka.Disable()
        self._row(page, sizer, "Плашка во время записи",
                  "Отключение станет доступно после первого использования Ctrl+Alt+Z",
                  plashka)
        page.SetSizer(sizer)
        return page

    def _page_bindings(self, parent: wx.Window) -> wx.Panel:
        page = wx.Panel(parent)
        sizer = wx.BoxSizer(wx.VERTICAL)
        self._ptt_label = wx.StaticText(page, label="—")
        rows: tuple[tuple[str, wx.StaticText | None, str], ...] = (
            ("Диктовка — удержание", self._ptt_label, ""),
            ("Диктовка — запасная", None, "Ctrl + Alt + D"),
            ("Диктовка — тумблер", None, "Ctrl + Alt + T"),
            ("Вставить последнее", None, "Ctrl + Alt + Z"),
            ("Скопировать последнее", None, "Ctrl + Alt + X"),
            ("Главное окно", None, "Ctrl + Alt + B"),
        )
        for label, dynamic, fixed in rows:
            key = dynamic if dynamic is not None else wx.StaticText(page, label=fixed)
            line = wx.BoxSizer(wx.HORIZONTAL)
            line.Add(wx.StaticText(page, label=label), 1, wx.ALIGN_CENTER_VERTICAL)
            line.Add(key, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 12)
            change = wx.Button(page, label="Изменить")
            if dynamic is self._ptt_label:
                change.Bind(wx.EVT_BUTTON, self._on_capture_ptt)
            else:
                change.Disable()
                change.SetToolTip("Фиксированное сочетание в MVP-0")
            line.Add(change, 0)
            sizer.Add(line, 0, wx.EXPAND | wx.ALL, 8)
        note = wx.StaticText(
            page,
            label="Отмена — двойной Esc во время записи. "
                  "Одиночный Esc всегда проходит в приложение.",
        )
        sizer.Add(note, 0, wx.ALL, 8)
        page.SetSizer(sizer)
        return page

    def _page_mic(self, parent: wx.Window) -> wx.Panel:
        page = wx.Panel(parent)
        sizer = wx.BoxSizer(wx.VERTICAL)
        self._mic_label = wx.StaticText(page, label="Системный по умолчанию")
        self._row(page, sizer, "Устройство записи",
                  "Выбор и ранжирование устройств появятся в этой вехе (M4)",
                  None)
        sizer.Add(self._mic_label, 0, wx.LEFT | wx.BOTTOM, 8)
        page.SetSizer(sizer)
        return page

    def _page_stt(self, parent: wx.Window) -> wx.Panel:
        page = wx.Panel(parent)
        sizer = wx.BoxSizer(wx.VERTICAL)
        self._language = wx.Choice(page, choices=[label for _, label in _LANGUAGES])
        self._language.Bind(wx.EVT_CHOICE, self._on_language)
        self._row(page, sizer, "Язык диктовки", "Определяется явно, не автоматически",
                  self._language)
        self._key_badge = wx.StaticText(page, label="—")
        self._row(page, sizer, "Ключ API",
                  "Хранится в диспетчере учётных данных Windows, в файлы не записывается",
                  self._key_badge)
        self._polish = wx.CheckBox(page, label="")
        self._polish.Bind(
            wx.EVT_CHECKBOX, lambda _e: self._patch("polish_enabled", self._polish.GetValue())
        )
        self._row(page, sizer, "Причёсывание текста",
                  "Отдельный ключ; хранятся оба варианта текста", self._polish)
        self._model_label = wx.StaticText(page, label="—")
        self._row(page, sizer, "Модель", "Облако Groq, ключ пользователя",
                  self._model_label)
        page.SetSizer(sizer)
        return page

    def _page_dictionary(self, parent: wx.Window) -> wx.Panel:
        page = wx.Panel(parent)
        sizer = wx.BoxSizer(wx.VERTICAL)
        self._rules_list = wx.ListCtrl(page, style=wx.LC_REPORT | wx.LC_SINGLE_SEL)
        for index, (title, width) in enumerate(
            (("Тип", 110), ("Слышится", 220), ("Пишется", 180), ("Вкл", 60))
        ):
            self._rules_list.InsertColumn(index, title, width=width)
        sizer.Add(self._rules_list, 1, wx.EXPAND | wx.ALL, 8)
        buttons = wx.BoxSizer(wx.HORIZONTAL)
        for label, handler in (
            ("Добавить правило", self._on_rule_add),
            ("Изменить", self._on_rule_edit),
            ("Удалить", self._on_rule_delete),
            ("Вкл/выкл", self._on_rule_toggle),
        ):
            button = wx.Button(page, label=label)
            button.Bind(wx.EVT_BUTTON, handler)
            buttons.Add(button, 0, wx.RIGHT, 8)
        sizer.Add(buttons, 0, wx.ALL, 8)
        page.SetSizer(sizer)
        return page

    def _page_about(self, parent: wx.Window) -> wx.Panel:
        page = wx.Panel(parent)
        sizer = wx.BoxSizer(wx.VERTICAL)
        self._row(page, sizer, f"BillyTalk {__version__}",
                  "github.com/Israphael/billytalk", None)
        logs = wx.Button(page, label="Открыть папку логов")
        logs.Bind(wx.EVT_BUTTON, self._on_open_logs)
        self._row(page, sizer, "Журнал работы",
                  "Текст диктовок и нажатия клавиш в журнал не попадают никогда", logs)
        clear = wx.Button(page, label="Очистить историю…")
        clear.Disable()
        clear.SetToolTip("Появится вместе с окном подтверждения")
        self._row(page, sizer, "Данные", "История и аудио на этом компьютере", clear)
        page.SetSizer(sizer)
        return page

    # ------------------------------------------------------------------ #
    # handlers
    # ------------------------------------------------------------------ #

    def _on_section(self, _event: wx.CommandEvent) -> None:
        self._book.SetSelection(max(0, self._nav.GetSelection()))

    def _on_capture_ptt(self, _event: wx.CommandEvent) -> None:
        from .hotkey_capture import HotkeyCaptureDialog

        dialog = HotkeyCaptureDialog(self._c, self)
        dialog.ShowModal()
        dialog.Destroy()
        self.refresh()  # the core applied the binding; show its word

    def _on_language(self, _event: wx.CommandEvent) -> None:
        index = self._language.GetSelection()
        if 0 <= index < len(_LANGUAGES):
            self._patch("language", _LANGUAGES[index][0])

    def _on_open_logs(self, _event: wx.CommandEvent) -> None:
        import os
        from pathlib import Path

        logs = Path(os.environ["LOCALAPPDATA"]) / "BillyTalk" / "logs"
        if logs.is_dir():
            os.startfile(str(logs))  # noqa: S606 — the user asked for this folder

    def _fill_from_config(self) -> None:
        config = self._config
        codes = {code: i for i, (code, _label) in enumerate(_LANGUAGES)}
        self._language.SetSelection(codes.get(config.get("language"), 0))
        self._polish.SetValue(bool(config.get("polish_enabled")))
        self._model_label.SetLabel(str(config.get("groq_model", "—")))
        self._key_badge.SetLabel(
            "Сохранён" if config.get("has_groq_key") else "Не сохранён — мастер первого запуска"
        )
        ptt = config.get("ptt_code")
        self._ptt_label.SetLabel(binding_label(ptt) if isinstance(ptt, int) else "—")
        device = config.get("audio_input_device")
        self._mic_label.SetLabel(device if device else "Системный по умолчанию")

    def _fill_rules(self) -> None:
        type_labels = dict(_RULE_TYPES)
        self._rules_list.DeleteAllItems()
        for row, rule in enumerate(self._rules):
            self._rules_list.InsertItem(row, type_labels.get(rule["type"], rule["type"]))
            self._rules_list.SetItem(row, 1, rule["pat"])
            self._rules_list.SetItem(row, 2, rule["repl"])
            self._rules_list.SetItem(row, 3, "✓" if rule.get("enabled", True) else "—")

    def _selected_rule(self) -> int:
        return self._rules_list.GetFirstSelected()

    def _on_rule_add(self, _event: wx.CommandEvent) -> None:
        dialog = RuleDialog(self)
        if dialog.ShowModal() == wx.ID_OK:
            self._rules.append(dialog.rule())
            self._send_rules()
        dialog.Destroy()

    def _on_rule_edit(self, _event: wx.CommandEvent) -> None:
        index = self._selected_rule()
        if index < 0:
            return
        dialog = RuleDialog(self, rule=self._rules[index])
        if dialog.ShowModal() == wx.ID_OK:
            self._rules[index] = dialog.rule()
            self._send_rules()
        dialog.Destroy()

    def _on_rule_delete(self, _event: wx.CommandEvent) -> None:
        index = self._selected_rule()
        if index >= 0:
            del self._rules[index]
            self._send_rules()

    def _on_rule_toggle(self, _event: wx.CommandEvent) -> None:
        index = self._selected_rule()
        if index >= 0:
            rule = dict(self._rules[index])
            rule["enabled"] = not rule.get("enabled", True)
            self._rules[index] = rule
            self._send_rules()


class RuleDialog(wx.Dialog):
    """One dictionary rule: type, spelling(s) heard, spelling written."""

    def __init__(self, parent: wx.Window, rule: dict[str, Any] | None = None) -> None:
        super().__init__(parent, title="Правило словаря")
        rule = rule or {}
        grid = wx.FlexGridSizer(2, gap=wx.Size(8, 8))
        grid.AddGrowableCol(1)
        grid.Add(wx.StaticText(self, label="Тип"), 0, wx.ALIGN_CENTER_VERTICAL)
        self._type = wx.Choice(self, choices=[label for _, label in _RULE_TYPES])
        self._type.SetSelection(
            next((i for i, (code, _l) in enumerate(_RULE_TYPES)
                  if code == rule.get("type")), 0)
        )
        grid.Add(self._type, 1, wx.EXPAND)
        grid.Add(wx.StaticText(self, label="Слышится (варианты через |)"), 0,
                 wx.ALIGN_CENTER_VERTICAL)
        self._pat = wx.TextCtrl(self, value=rule.get("pat", ""), size=wx.Size(260, -1))
        grid.Add(self._pat, 1, wx.EXPAND)
        grid.Add(wx.StaticText(self, label="Пишется"), 0, wx.ALIGN_CENTER_VERTICAL)
        self._repl = wx.TextCtrl(self, value=rule.get("repl", ""))
        grid.Add(self._repl, 1, wx.EXPAND)
        grid.Add(wx.StaticText(self, label="Включено"), 0, wx.ALIGN_CENTER_VERTICAL)
        self._enabled = wx.CheckBox(self)
        self._enabled.SetValue(bool(rule.get("enabled", True)))
        grid.Add(self._enabled, 0)

        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(grid, 1, wx.EXPAND | wx.ALL, 12)
        sizer.Add(self.CreateStdDialogButtonSizer(wx.OK | wx.CANCEL), 0,
                  wx.EXPAND | wx.ALL, 8)
        self.SetSizerAndFit(sizer)

    def rule(self) -> dict[str, Any]:
        index = max(0, self._type.GetSelection())
        return {
            "type": _RULE_TYPES[index][0],
            "pat": self._pat.GetValue().strip(),
            "repl": self._repl.GetValue().strip(),
            "enabled": self._enabled.GetValue(),
        }
