"""The settings window (spec §11; the approved mockup's six sections).

Windows-Settings layout: navigation list on the left, «label + control» rows
on the right. Everything the window changes travels as a ``set_config`` /
``dictionary_set`` verb and the controls re-fill from the reply — the config
on screen is always the core's word, never a local guess.

Every string comes from :mod:`billytalk.i18n` by key. Changing the program
language rebuilds the window rather than relabelling it in place: labels are
read once at build time, and tracking every static control for a live relabel
is exactly the kind of bookkeeping that leaves half a window in the old
language after the next edit.

What is greyed out is greyed honestly: the plashka switch waits on the first
Ctrl+Alt+Z (spec §11), clearing the history waits on its confirmation window.
Autostart and the key are live as of cycle 3.
"""

from __future__ import annotations

from typing import Any

import wx

from ... import __version__
from ...core.hooks.keycodes import is_mouse_code, mouse_number
from ...i18n import t
from ..controller import UiController
from . import dress

__all__ = ["SettingsFrame", "binding_label"]

_SECTION_KEYS = (
    "settings.section.general", "settings.section.bindings", "settings.section.mic",
    "settings.section.stt", "settings.section.dictionary", "settings.section.about",
)

_LANGUAGES = (("ru", "language.ru"), ("en", "language.en"))

_UI_LANGUAGES = (
    ("auto", "settings.ui_language.auto"),
    ("ru", "language.ru"),
    ("en", "language.en"),
)

_RULE_TYPES = (
    ("normalize", "settings.rule.type.normalize"),
    ("replace", "settings.rule.type.replace"),
)


def binding_label(code: int) -> str:
    """The human name of a unified-space key code (spec §2): mouse codes are
    offset by 0x1000, so 4099 is the default Mouse 4."""
    if is_mouse_code(code):
        return f"Mouse {mouse_number(code)}"
    return t("binding.code", code=code)


class SettingsFrame(wx.Frame):
    def __init__(self, controller: UiController, parent: wx.Window | None = None) -> None:
        super().__init__(parent, title=t("settings.title"), size=wx.Size(840, 560))
        self._c = controller
        self._config: dict[str, Any] = {}
        self._rules: list[dict[str, Any]] = []

        root = wx.Panel(self)
        self._nav = wx.ListBox(root, choices=[t(key) for key in _SECTION_KEYS])
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
        self._c.request({"type": "autostart_get"}, self._on_autostart)

    def _on_config(self, frame: dict[str, Any]) -> None:
        if "error" in frame:
            self.SetStatusText(t("settings.rejected"))
            return
        self._config = frame["result"]["config"]
        self._fill_from_config()

    def _on_rules(self, frame: dict[str, Any]) -> None:
        if "error" in frame:
            # A rejected dictionary_set left self._rules holding the bad edit
            # (optimistically applied). Re-pull the core's truth, or that one
            # poisoned rule rides along in every later save and every save
            # fails until the window is reopened (M2 review, medium finding).
            self.SetStatusText(t("settings.rule.rejected"))
            self._c.request({"type": "dictionary_get"}, self._on_rules)
            return
        result = frame["result"]
        if "rules" in result:
            self._rules = list(result["rules"])
        else:  # a dictionary_set reply confirms the count; re-pull the truth
            self._c.request({"type": "dictionary_get"}, self._on_rules)
            return
        self._fill_rules()

    def _on_autostart(self, frame: dict[str, Any]) -> None:
        if "error" in frame:
            self._autostart.Disable()
            return
        state = frame["result"]
        self._autostart.SetValue(bool(state.get("enabled")))
        self._autostart.Enable(bool(state.get("available")))
        if not state.get("available"):
            self._autostart_hint.SetLabel(t("settings.autostart.unavailable"))
        elif state.get("disabled_by_windows"):
            # Spec §12: Windows' own Startup page wins and we say so instead of
            # quietly re-enabling ourselves behind the user's back.
            self._autostart_hint.SetLabel(t("settings.autostart.disabled_by_windows"))
        else:
            self._autostart_hint.SetLabel(t("settings.autostart.hint"))

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
    ) -> wx.StaticText | None:
        line = wx.BoxSizer(wx.HORIZONTAL)
        text = wx.BoxSizer(wx.VERTICAL)
        text.Add(wx.StaticText(parent, label=label))
        hint = None
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
        return hint

    def _page_general(self, parent: wx.Window) -> wx.Panel:
        page = wx.Panel(parent)
        sizer = wx.BoxSizer(wx.VERTICAL)
        self._autostart = wx.CheckBox(page, label="")
        self._autostart.Disable()  # enabled by the autostart_get reply
        self._autostart.Bind(wx.EVT_CHECKBOX, self._on_autostart_toggle)
        self._autostart_hint = self._row(
            page, sizer, t("settings.autostart"), t("settings.autostart.hint"),
            self._autostart,
        )
        self._ui_language = wx.Choice(
            page, choices=[t(key) for _code, key in _UI_LANGUAGES]
        )
        self._ui_language.Bind(wx.EVT_CHOICE, self._on_ui_language)
        self._row(page, sizer, t("settings.ui_language"),
                  t("settings.ui_language.hint"), self._ui_language)
        plashka = wx.CheckBox(page, label="")
        plashka.SetValue(True)
        plashka.Disable()
        self._row(page, sizer, t("settings.plashka"), t("settings.plashka.hint"),
                  plashka)
        page.SetSizer(sizer)
        return page

    def _page_bindings(self, parent: wx.Window) -> wx.Panel:
        page = wx.Panel(parent)
        sizer = wx.BoxSizer(wx.VERTICAL)
        self._ptt_label = wx.StaticText(page, label=t("common.dash"))
        rows: tuple[tuple[str, wx.StaticText | None, str], ...] = (
            (t("settings.binding.ptt"), self._ptt_label, ""),
            (t("settings.binding.fallback"), None, "Ctrl + Alt + D"),
            (t("settings.binding.toggle"), None, "Ctrl + Alt + T"),
            (t("settings.binding.paste"), None, "Ctrl + Alt + Z"),
            (t("settings.binding.copy"), None, "Ctrl + Alt + X"),
            (t("settings.binding.window"), None, "Ctrl + Alt + B"),
        )
        for label, dynamic, fixed in rows:
            key = dynamic if dynamic is not None else wx.StaticText(page, label=fixed)
            line = wx.BoxSizer(wx.HORIZONTAL)
            line.Add(wx.StaticText(page, label=label), 1, wx.ALIGN_CENTER_VERTICAL)
            line.Add(key, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 12)
            change = wx.Button(page, label=t("common.change"))
            if dynamic is self._ptt_label:
                change.Bind(wx.EVT_BUTTON, self._on_capture_ptt)
            else:
                change.Disable()
                change.SetToolTip(t("settings.binding.fixed"))
            line.Add(change, 0)
            sizer.Add(line, 0, wx.EXPAND | wx.ALL, 8)
        note = wx.StaticText(page, label=t("settings.binding.note"))
        sizer.Add(note, 0, wx.ALL, 8)
        page.SetSizer(sizer)
        return page

    def _page_mic(self, parent: wx.Window) -> wx.Panel:
        page = wx.Panel(parent)
        sizer = wx.BoxSizer(wx.VERTICAL)
        self._mic_label = wx.StaticText(page, label=t("common.system_default"))
        check = wx.Button(page, label=t("settings.mic.check"))
        check.Bind(wx.EVT_BUTTON, self._on_mic_check)
        self._row(page, sizer, t("settings.mic.device"), t("settings.mic.hint"), check)
        sizer.Add(self._mic_label, 0, wx.LEFT | wx.BOTTOM, 8)
        page.SetSizer(sizer)
        return page

    def _page_stt(self, parent: wx.Window) -> wx.Panel:
        page = wx.Panel(parent)
        sizer = wx.BoxSizer(wx.VERTICAL)
        self._language = wx.Choice(page, choices=[t(key) for _code, key in _LANGUAGES])
        self._language.Bind(wx.EVT_CHOICE, self._on_language)
        self._row(page, sizer, t("settings.language"), t("settings.language.hint"),
                  self._language)
        # One row, two controls: the badge and the «replace» button live in
        # their own panel so they can be the single control ``_row`` takes.
        # Both are built with that panel as parent — wxSizer refuses to manage
        # windows whose parent is not the panel it belongs to.
        key_slot = wx.Panel(page)
        self._key_badge = wx.StaticText(key_slot, label=t("common.dash"))
        replace = wx.Button(key_slot, label=t("settings.key.replace"))
        replace.Bind(wx.EVT_BUTTON, self._on_replace_key)
        key_line = wx.BoxSizer(wx.HORIZONTAL)
        key_line.Add(self._key_badge, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)
        key_line.Add(replace, 0)
        key_slot.SetSizer(key_line)
        key_line.Fit(key_slot)
        self._row(page, sizer, t("settings.key"), t("settings.key.hint"), key_slot)
        self._polish = wx.CheckBox(page, label="")
        self._polish.Bind(
            wx.EVT_CHECKBOX,
            lambda _e: self._patch("polish_enabled", self._polish.GetValue()),
        )
        self._row(page, sizer, t("settings.polish"), t("settings.polish.hint"),
                  self._polish)
        self._model_label = wx.StaticText(page, label=t("common.dash"))
        self._row(page, sizer, t("settings.model"), t("settings.model.hint"),
                  self._model_label)
        page.SetSizer(sizer)
        return page

    def _page_dictionary(self, parent: wx.Window) -> wx.Panel:
        page = wx.Panel(parent)
        sizer = wx.BoxSizer(wx.VERTICAL)
        self._rules_list = wx.ListCtrl(page, style=wx.LC_REPORT | wx.LC_SINGLE_SEL)
        for index, (key, width) in enumerate((
            ("settings.rule.type", 110), ("settings.rule.heard", 220),
            ("settings.rule.written", 180), ("settings.rule.enabled", 60),
        )):
            self._rules_list.InsertColumn(index, t(key), width=width)
        sizer.Add(self._rules_list, 1, wx.EXPAND | wx.ALL, 8)
        buttons = wx.BoxSizer(wx.HORIZONTAL)
        for key, handler in (
            ("settings.rule.add", self._on_rule_add),
            ("settings.rule.edit", self._on_rule_edit),
            ("settings.rule.delete", self._on_rule_delete),
            ("settings.rule.toggle", self._on_rule_toggle),
        ):
            button = wx.Button(page, label=t(key))
            button.Bind(wx.EVT_BUTTON, handler)
            buttons.Add(button, 0, wx.RIGHT, 8)
        sizer.Add(buttons, 0, wx.ALL, 8)
        page.SetSizer(sizer)
        return page

    def _page_about(self, parent: wx.Window) -> wx.Panel:
        page = wx.Panel(parent)
        sizer = wx.BoxSizer(wx.VERTICAL)
        self._row(page, sizer, t("settings.about.version", version=__version__),
                  t("settings.about.repo"), None)
        logs = wx.Button(page, label=t("settings.about.logs.open"))
        logs.Bind(wx.EVT_BUTTON, self._on_open_logs)
        self._row(page, sizer, t("settings.about.logs"), t("settings.about.logs.hint"),
                  logs)
        wizard = wx.Button(page, label=t("settings.about.wizard.run"))
        wizard.Bind(wx.EVT_BUTTON, self._on_run_wizard)
        self._row(page, sizer, t("settings.about.wizard"),
                  t("settings.about.wizard.hint"), wizard)
        clear = wx.Button(page, label=t("settings.about.data.clear"))
        clear.Disable()
        clear.SetToolTip(t("settings.about.data.soon"))
        self._row(page, sizer, t("settings.about.data"), t("settings.about.data.hint"),
                  clear)
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

    def _on_ui_language(self, _event: wx.CommandEvent) -> None:
        index = self._ui_language.GetSelection()
        if not 0 <= index < len(_UI_LANGUAGES):
            return
        setting = _UI_LANGUAGES[index][0]

        def applied(frame: dict[str, Any]) -> None:
            self._on_config(frame)
            if "error" in frame:
                return
            # The core resolved «auto» for us — it is the side of the border
            # that may ask Windows (harness §1). Applying it rebuilds this
            # window, so nothing below this line may touch self.
            effective = frame["result"]["config"].get("ui_language_effective")
            if self._c.apply_language is not None and isinstance(effective, str):
                self._c.apply_language(effective)

        self._c.request({"type": "set_config", "patch": {"ui_language": setting}},
                        applied)

    def _on_autostart_toggle(self, _event: wx.CommandEvent) -> None:
        self._c.request(
            {"type": "autostart_set", "enabled": self._autostart.GetValue()},
            self._on_autostart,
        )

    def _on_mic_check(self, event: wx.CommandEvent) -> None:
        button = event.GetEventObject()
        button.Disable()
        self.SetStatusText(t("settings.mic.checking"))

        def answered(frame: dict[str, Any]) -> None:
            button.Enable()
            if "error" in frame:
                self.SetStatusText(t("settings.rejected"))
                return
            result = frame["result"]
            device = result.get("device") or t("common.system_default")
            if result.get("ok"):
                self.SetStatusText(
                    t("wizard.mic.ok", device=device) + "  "
                    + t("wizard.mic.level", level=result.get("level", 0))
                )
            else:
                self.SetStatusText(_mic_failure_line(result.get("code")))

        self._c.request({"type": "mic_probe"}, answered)

    def _on_replace_key(self, _event: wx.CommandEvent) -> None:
        from .wizard import KeyDialog

        dialog = KeyDialog(self._c, self)
        dialog.ShowModal()
        dialog.Destroy()
        self.refresh()

    def _on_run_wizard(self, _event: wx.CommandEvent) -> None:
        from .wizard import WizardFrame

        wizard = WizardFrame(self._c)
        wizard.Show()
        wizard.Raise()

    def _on_open_logs(self, _event: wx.CommandEvent) -> None:
        import os
        from pathlib import Path

        logs = Path(os.environ["LOCALAPPDATA"]) / "BillyTalk" / "logs"
        if logs.is_dir():
            os.startfile(str(logs))  # noqa: S606 — the user asked for this folder

    def _fill_from_config(self) -> None:
        config = self._config
        codes = {code: i for i, (code, _key) in enumerate(_LANGUAGES)}
        self._language.SetSelection(codes.get(config.get("language"), 0))
        ui_codes = {code: i for i, (code, _key) in enumerate(_UI_LANGUAGES)}
        self._ui_language.SetSelection(ui_codes.get(config.get("ui_language"), 0))
        self._polish.SetValue(bool(config.get("polish_enabled")))
        self._model_label.SetLabel(str(config.get("groq_model", t("common.dash"))))
        self._key_badge.SetLabel(
            t("settings.key.saved") if config.get("has_groq_key")
            else t("settings.key.missing")
        )
        ptt = config.get("ptt_code")
        self._ptt_label.SetLabel(
            binding_label(ptt) if isinstance(ptt, int) else t("common.dash")
        )
        device = config.get("audio_input_device")
        self._mic_label.SetLabel(device if device else t("common.system_default"))

    def _fill_rules(self) -> None:
        type_labels = {code: t(key) for code, key in _RULE_TYPES}
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


def _mic_failure_line(code: object) -> str:
    """The wizard's microphone verdicts, reused by the settings «Проверить»
    button so one measured outcome never gets two different explanations."""
    return {
        "mic_denied": t("wizard.mic.denied"),
        "mic_busy": t("wizard.mic.busy"),
        "no_device": t("wizard.mic.none"),
        "no_frames": t("wizard.mic.silent"),
        "recording": t("wizard.mic.busy"),
    }.get(str(code), t("wizard.mic.busy"))


class RuleDialog(wx.Dialog):
    """One dictionary rule: type, spelling(s) heard, spelling written."""

    def __init__(self, parent: wx.Window, rule: dict[str, Any] | None = None) -> None:
        super().__init__(parent, title=t("settings.rule.dialog"))
        rule = rule or {}
        grid = wx.FlexGridSizer(2, gap=wx.Size(8, 8))
        grid.AddGrowableCol(1)
        grid.Add(wx.StaticText(self, label=t("settings.rule.type")), 0,
                 wx.ALIGN_CENTER_VERTICAL)
        self._type = wx.Choice(self, choices=[t(key) for _code, key in _RULE_TYPES])
        self._type.SetSelection(
            next((i for i, (code, _k) in enumerate(_RULE_TYPES)
                  if code == rule.get("type")), 0)
        )
        grid.Add(self._type, 1, wx.EXPAND)
        grid.Add(wx.StaticText(self, label=t("settings.rule.heard_field")), 0,
                 wx.ALIGN_CENTER_VERTICAL)
        self._pat = wx.TextCtrl(self, value=rule.get("pat", ""), size=wx.Size(260, -1))
        grid.Add(self._pat, 1, wx.EXPAND)
        grid.Add(wx.StaticText(self, label=t("settings.rule.written")), 0,
                 wx.ALIGN_CENTER_VERTICAL)
        self._repl = wx.TextCtrl(self, value=rule.get("repl", ""))
        grid.Add(self._repl, 1, wx.EXPAND)
        grid.Add(wx.StaticText(self, label=t("settings.rule.enabled_field")), 0,
                 wx.ALIGN_CENTER_VERTICAL)
        self._enabled = wx.CheckBox(self)
        self._enabled.SetValue(bool(rule.get("enabled", True)))
        grid.Add(self._enabled, 0)

        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(grid, 1, wx.EXPAND | wx.ALL, 12)
        buttons = self.CreateStdDialogButtonSizer(wx.OK | wx.CANCEL)
        sizer.Add(buttons, 0, wx.EXPAND | wx.ALL, 8)
        self.SetSizerAndFit(sizer)
        # Guard OK: an empty «Слышится» would be rejected by the core and, since
        # the list is applied optimistically, would jam every later save until
        # the window reopened (M2 review). Refuse it at the source instead.
        ok = self.FindWindow(wx.ID_OK)
        if ok is not None:
            ok.Bind(wx.EVT_BUTTON, self._on_ok)

    def _on_ok(self, event: wx.CommandEvent) -> None:
        if not self._pat.GetValue().strip() or not self._repl.GetValue().strip():
            wx.MessageBox(
                t("settings.rule.incomplete"), t("settings.rule.dialog"),
                wx.OK | wx.ICON_WARNING, self,
            )
            return
        event.Skip()  # let the standard OK close the dialog

    def rule(self) -> dict[str, Any]:
        index = max(0, self._type.GetSelection())
        return {
            "type": _RULE_TYPES[index][0],
            "pat": self._pat.GetValue().strip(),
            "repl": self._repl.GetValue().strip(),
            "enabled": self._enabled.GetValue(),
        }
