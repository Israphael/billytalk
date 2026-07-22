"""The first-run wizard (spec §12): seven steps, in the spec's own order.

1. **Microphone** — a real capture attempt, and on refusal a button straight to
   ``ms-settings:privacy-microphone``.
2. **Language** — dictation language, explicitly (spec §6), plus the program's
   own language while we are here.
3. **Hotkey** — live capture through the core (the M3 dialog), with the «Назад»
   warning.
4. **Unbinding «Back» in the mouse driver** — optional, with the honest price
   and benefit (spec §12's own words: our suppression is the only defence, and
   it has gaps).
5. **Transcription and the network warning** — verbatim, unsoftened. No local
   mode in MVP-0, so the step is the warning.
6. **Groq key** — a link to the console, the field, and a real request to check
   it. Stored in the Credential Manager by the core; the value never touches a
   config file, a log, or this module beyond the control it was typed into.
7. **The live test** — hold, speak, see the text. The step watches the machine
   over ``state_changed`` and then asks the history for the words, because the
   only proof that dictation works is a dictation that worked.

The wizard owns no rules: every check is a core verb (``mic_probe``,
``set_key``, ``test_key``, ``autostart_set``), so what the wizard reports is
what the product will actually do afterwards, not a rehearsal of it.

Leaving early is allowed and remembered honestly: ``wizard_done`` flips only on
«Готово», so an abandoned first run asks again next time — and a first
dictation without a key still says so loudly (spec §12).
"""

from __future__ import annotations

import os
from typing import Any, Final

import wx

from ...i18n import t
from ..controller import UiController
from . import dress
from .hotkey_capture import HotkeyCaptureDialog
from .settings import binding_label

__all__ = ["KeyDialog", "WizardFrame", "mic_line", "STEP_COUNT"]

STEP_COUNT: Final = 7

_GROQ_CONSOLE: Final = "https://console.groq.com/keys"
_MIC_PRIVACY: Final = "ms-settings:privacy-microphone"

_POLL_MS: Final = 2500
"""The live test's backstop. ``state_changed`` normally wakes the step the
moment a dictation ends; the timer covers the case where the interface
connected late and missed the push — a wizard that waits forever on a
dictation that already happened is the worst possible last step."""


def mic_line(result: dict[str, Any]) -> str:
    """The sentence step 1 shows for a probe result — pure, so the wording of
    every outcome is testable without a microphone in the room."""
    if result.get("ok"):
        device = result.get("device") or t("common.system_default")
        return (
            t("wizard.mic.ok", device=device)
            + "  " + t("wizard.mic.level", level=result.get("level", 0))
        )
    return {
        "mic_denied": t("wizard.mic.denied"),
        "mic_busy": t("wizard.mic.busy"),
        "no_device": t("wizard.mic.none"),
        "no_frames": t("wizard.mic.silent"),
        "recording": t("wizard.mic.busy"),
    }.get(str(result.get("code")), t("wizard.mic.busy"))


class _KeyForm(wx.Panel):
    """The key field, the console link and «сохранить и проверить».

    One implementation, two homes: step 6 of the wizard and the settings
    window's «Заменить…». The key lives in the text control and in the verb
    frame, nowhere else — no attribute keeps it, so nothing can later log it
    by accident (spec §13).
    """

    def __init__(self, parent: wx.Window, controller: UiController) -> None:
        super().__init__(parent)
        self._c = controller
        self.accepted = False

        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(_wrapped(self, t("wizard.key.body")), 0, wx.EXPAND | wx.BOTTOM, 8)
        open_console = wx.Button(self, label=t("wizard.key.open"))
        open_console.Bind(
            wx.EVT_BUTTON, lambda _e: wx.LaunchDefaultBrowser(_GROQ_CONSOLE)
        )
        sizer.Add(open_console, 0, wx.BOTTOM, 10)

        row = wx.BoxSizer(wx.HORIZONTAL)
        row.Add(wx.StaticText(self, label=t("wizard.key.field")), 0,
                wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)
        # TE_PASSWORD: the key is a secret and a shoulder is a channel too.
        self._field = wx.TextCtrl(self, style=wx.TE_PASSWORD, size=wx.Size(320, -1))
        row.Add(self._field, 1, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)
        self._save = wx.Button(self, label=t("wizard.key.save"))
        self._save.Bind(wx.EVT_BUTTON, self._on_save)
        row.Add(self._save, 0)
        sizer.Add(row, 0, wx.EXPAND | wx.BOTTOM, 8)

        self.status = _wrapped(self, "")
        sizer.Add(self.status, 0, wx.BOTTOM, 8)
        privacy = _wrapped(self, t("wizard.key.privacy"))
        privacy.SetForegroundColour(wx.SystemSettings.GetColour(wx.SYS_COLOUR_GRAYTEXT))
        sizer.Add(privacy, 0, wx.EXPAND)
        self.SetSizer(sizer)

    def show_stored(self, stored: bool) -> None:
        if stored:
            _relabel(self.status, t("wizard.key.stored"))
            self.accepted = True

    def _on_save(self, _event: wx.CommandEvent) -> None:
        key = self._field.GetValue().strip()
        if not key:
            _relabel(self.status, t("wizard.key.empty"))
            return
        self._save.Disable()
        _relabel(self.status, t("wizard.key.checking"))
        self._c.request({"type": "set_key", "key": key}, self._on_stored)

    # Bound methods, not closures: ``UiController`` drops a reply whose
    # callback belongs to a destroyed wx window, and it recognises that only
    # through ``__self__``. A closure has none, so a ``test_key`` answer —
    # a network round trip, up to the pool's 30 s — landing after the user
    # closed the dialog would touch deleted controls.

    def _on_stored(self, frame: dict[str, Any]) -> None:
        if "error" in frame:
            self._save.Enable()
            _relabel(self.status, t("wizard.key.failed"))
            return
        # Clear the field the moment the core owns the key: nothing is
        # served by leaving it on screen.
        self._field.SetValue("")
        self._c.request({"type": "test_key"}, self._on_checked)

    def _on_checked(self, frame: dict[str, Any]) -> None:
        self._save.Enable()
        if "error" in frame:
            _relabel(self.status, t("wizard.key.network"))
            return
        status = frame["result"].get("status")
        self.accepted = status in ("ok", "network")
        _relabel(self.status, {
            "ok": t("wizard.key.ok"),
            "invalid": t("wizard.key.invalid"),
            "network": t("wizard.key.network"),
            "no_key": t("wizard.key.empty"),
        }.get(str(status), t("wizard.key.network")))


class KeyDialog(wx.Dialog):
    """The key form on its own, for the settings window's «Заменить…»."""

    def __init__(self, controller: UiController, parent: wx.Window | None = None) -> None:
        super().__init__(parent, title=t("wizard.key.title"))
        self.form = _KeyForm(self, controller)
        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(self.form, 1, wx.EXPAND | wx.ALL, 14)
        close = wx.Button(self, wx.ID_CANCEL, label=t("common.close"))
        sizer.Add(close, 0, wx.ALIGN_RIGHT | wx.ALL, 10)
        self.SetSizerAndFit(sizer)
        dress(self)


class WizardFrame(wx.Frame):
    """The seven steps, one window, one page at a time."""

    def __init__(
        self, controller: UiController, parent: wx.Window | None = None,
        *, start_step: int = 0, state: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(parent, title=t("wizard.title"), size=wx.Size(720, 560))
        self._c = controller
        self._step = max(0, min(STEP_COUNT - 1, start_step))
        # Survives a language switch (which rebuilds the window) so the user
        # does not repeat a microphone test to read the same page in Russian.
        self.state: dict[str, Any] = dict(state or {})
        self._test_since_ms = 0
        self._state_seat: Any = None

        root = wx.Panel(self)
        outer = wx.BoxSizer(wx.VERTICAL)

        header = wx.BoxSizer(wx.HORIZONTAL)
        self._title = wx.StaticText(root, label="")
        font = self._title.GetFont()
        font.SetPointSize(font.GetPointSize() + 4)
        self._title.SetFont(font.Bold())
        header.Add(self._title, 1, wx.ALIGN_CENTER_VERTICAL)
        self._language = wx.Choice(root, choices=[t("language.ru"), t("language.en")])
        self._language.Bind(wx.EVT_CHOICE, self._on_ui_language)
        header.Add(self._language, 0, wx.ALIGN_CENTER_VERTICAL)
        outer.Add(header, 0, wx.EXPAND | wx.ALL, 16)

        self._book = wx.Simplebook(root)
        for build_page in (
            self._page_mic, self._page_language, self._page_hotkey,
            self._page_driver, self._page_stt, self._page_key, self._page_test,
        ):
            self._book.AddPage(build_page(self._book), "")
        outer.Add(self._book, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 16)

        footer = wx.BoxSizer(wx.HORIZONTAL)
        self._counter = wx.StaticText(root, label="")
        footer.Add(self._counter, 0, wx.ALIGN_CENTER_VERTICAL)
        footer.AddStretchSpacer()
        self._later = wx.Button(root, label=t("wizard.later"))
        self._later.Bind(wx.EVT_BUTTON, lambda _e: self.Close())
        footer.Add(self._later, 0, wx.RIGHT, 8)
        self._back = wx.Button(root, label=t("common.back"))
        self._back.Bind(wx.EVT_BUTTON, lambda _e: self._go(self._step - 1))
        footer.Add(self._back, 0, wx.RIGHT, 8)
        self._next = wx.Button(root, label=t("common.next"))
        self._next.Bind(wx.EVT_BUTTON, self._on_next)
        footer.Add(self._next, 0)
        outer.Add(footer, 0, wx.EXPAND | wx.ALL, 16)

        root.SetSizer(outer)
        dress(self)

        self._timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, lambda _e: self._poll_test(), self._timer)
        self.Bind(wx.EVT_CLOSE, self._on_close)

        self._c.request({"type": "get_config"}, self._on_config)
        self._go(self._step)

    # ------------------------------------------------------------------ #
    # navigation
    # ------------------------------------------------------------------ #

    _TITLE_KEYS = (
        "wizard.mic.title", "wizard.language.title", "wizard.hotkey.title",
        "wizard.driver.title", "wizard.stt.title", "wizard.key.title",
        "wizard.test.title",
    )

    def _go(self, step: int) -> None:
        step = max(0, min(STEP_COUNT - 1, step))
        self._step = step
        self._book.SetSelection(step)
        self._title.SetLabel(t(self._TITLE_KEYS[step]))
        self._counter.SetLabel(t("wizard.step_of", step=step + 1, total=STEP_COUNT))
        self._back.Enable(step > 0)
        last = step == STEP_COUNT - 1
        self._next.SetLabel(t("wizard.finish") if last else t("common.next"))
        if last:
            self._enter_test()
        else:
            self._release_test()

    def _on_next(self, _event: wx.CommandEvent) -> None:
        if self._step < STEP_COUNT - 1:
            self._go(self._step + 1)
            return
        self._finish()

    def _finish(self) -> None:
        self._c.request(
            {"type": "set_config", "patch": {"wizard_done": True}}, lambda _f: None
        )
        message = t("wizard.done.body")
        if not self.state.get("has_key"):
            message = f"{message}\n\n{t('wizard.done.nokey')}"
        wx.MessageBox(message, t("wizard.done.title"), wx.OK | wx.ICON_INFORMATION, self)
        self.Close()

    def _release_test(self) -> None:
        """Give up the live test's two live wires: the timer and the
        controller's state seat.

        Both point at *this* window. A timer that fires into a destroyed frame
        and a state push that calls a dead frame's method are the same
        «wrapped C/C++ object deleted» the M2 review found in the reply table —
        and the language switch destroys this window on purpose, so the wires
        have to be cut before the twin exists, not after.

        The seat is given up **only if it is still ours**. There is one seat
        and there can be two wizards (the first run's, and one opened from
        «Настройки → Пройти заново»): a blind ``= None`` from the one being
        closed would deafen the one standing on its live-test step.
        """
        self._timer.Stop()
        if self._c.on_state is self._state_seat:
            self._c.on_state = None

    def _on_close(self, event: wx.CloseEvent) -> None:
        self._release_test()
        event.Skip()

    # ------------------------------------------------------------------ #
    # config and the language switch
    # ------------------------------------------------------------------ #

    def _on_config(self, frame: dict[str, Any]) -> None:
        if "error" in frame:
            return
        config = frame["result"]["config"]
        self.state["has_key"] = bool(config.get("has_groq_key"))
        self._key_form.show_stored(self.state["has_key"])
        self._dictation.SetSelection(0 if config.get("language") == "ru" else 1)
        effective = config.get("ui_language_effective")
        self._language.SetSelection(1 if effective == "en" else 0)
        self._ui_language.SetSelection(1 if effective == "en" else 0)
        code = config.get("ptt_code")
        key = binding_label(code) if isinstance(code, int) else t("common.dash")
        _relabel(self._hotkey_label, t("wizard.hotkey.current", key=key))
        _relabel(self._test_body, t("wizard.test.body", key=key))
        self.Layout()

    def _on_ui_language(self, event: wx.CommandEvent) -> None:
        choice = event.GetEventObject()
        code = "ru" if choice.GetSelection() == 0 else "en"
        self._c.request(
            {"type": "set_config", "patch": {"ui_language": code}},
            self._on_language_applied,
        )

    def _on_language_applied(self, frame: dict[str, Any]) -> None:
        if "error" in frame:
            return
        effective = frame["result"]["config"].get("ui_language_effective", "ru")
        if self._c.apply_language is not None:
            self._c.apply_language(effective)  # menu and the other windows
        # Order matters: release the live wires BEFORE the twin exists, or
        # the twin's own seat (taken in its __init__, when it walks to the
        # same step) is the one this window would clear.
        self._release_test()
        # This window is rebuilt rather than relabelled — same reason as
        # the settings window, and the step and answers ride along.
        twin = WizardFrame(self._c, start_step=self._step, state=self.state)
        twin.Show()
        twin.Raise()
        self.Destroy()

    # ------------------------------------------------------------------ #
    # pages
    # ------------------------------------------------------------------ #

    def _page_mic(self, parent: wx.Window) -> wx.Panel:
        page = wx.Panel(parent)
        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(_wrapped(page, t("wizard.mic.body")), 0, wx.EXPAND | wx.BOTTOM, 12)
        self._mic_button = wx.Button(page, label=t("wizard.mic.check"))
        self._mic_button.Bind(wx.EVT_BUTTON, self._on_mic_check)
        sizer.Add(self._mic_button, 0, wx.BOTTOM, 12)
        self._mic_status = _wrapped(page, self.state.get("mic_line", ""))
        sizer.Add(self._mic_status, 0, wx.EXPAND | wx.BOTTOM, 12)
        self._mic_settings = wx.Button(page, label=t("wizard.mic.settings"))
        self._mic_settings.Bind(wx.EVT_BUTTON, lambda _e: _open(_MIC_PRIVACY))
        self._mic_settings.Show(bool(self.state.get("mic_denied")))
        sizer.Add(self._mic_settings, 0)
        page.SetSizer(sizer)
        return page

    def _page_language(self, parent: wx.Window) -> wx.Panel:
        page = wx.Panel(parent)
        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(_wrapped(page, t("wizard.language.body")), 0, wx.EXPAND | wx.BOTTOM, 12)
        self._dictation = wx.Choice(page, choices=[t("language.ru"), t("language.en")])
        self._dictation.Bind(
            wx.EVT_CHOICE,
            lambda _e: self._c.request(
                {"type": "set_config",
                 "patch": {"language": "ru" if self._dictation.GetSelection() == 0
                           else "en"}},
                lambda _f: None,
            ),
        )
        sizer.Add(_labelled(page, t("wizard.language.dictation"), self._dictation),
                  0, wx.EXPAND | wx.BOTTOM, 10)
        self._ui_language = wx.Choice(page, choices=[t("language.ru"), t("language.en")])
        self._ui_language.Bind(wx.EVT_CHOICE, self._on_ui_language)
        sizer.Add(_labelled(page, t("wizard.language.ui"), self._ui_language),
                  0, wx.EXPAND)
        page.SetSizer(sizer)
        return page

    def _page_hotkey(self, parent: wx.Window) -> wx.Panel:
        page = wx.Panel(parent)
        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(_wrapped(page, t("wizard.hotkey.body")), 0, wx.EXPAND | wx.BOTTOM, 12)
        self._hotkey_label = wx.StaticText(page, label=t("wizard.hotkey.current",
                                                         key=t("common.dash")))
        sizer.Add(self._hotkey_label, 0, wx.BOTTOM, 10)
        change = wx.Button(page, label=t("wizard.hotkey.change"))
        change.Bind(wx.EVT_BUTTON, self._on_capture)
        sizer.Add(change, 0, wx.BOTTOM, 12)
        sizer.Add(_wrapped(page, t("wizard.hotkey.note")), 0, wx.EXPAND)
        page.SetSizer(sizer)
        return page

    def _page_driver(self, parent: wx.Window) -> wx.Panel:
        page = wx.Panel(parent)
        sizer = wx.BoxSizer(wx.VERTICAL)
        for key in ("wizard.driver.body", "wizard.driver.why", "wizard.driver.how"):
            sizer.Add(_wrapped(page, t(key)), 0, wx.EXPAND | wx.BOTTOM, 10)
        self._driver_done = wx.CheckBox(page, label=t("wizard.driver.done"))
        self._driver_done.SetValue(bool(self.state.get("driver_done")))
        self._driver_done.Bind(
            wx.EVT_CHECKBOX,
            lambda _e: self.state.__setitem__("driver_done", self._driver_done.GetValue()),
        )
        sizer.Add(self._driver_done, 0, wx.BOTTOM, 8)
        optional = _wrapped(page, t("wizard.driver.optional"))
        optional.SetForegroundColour(wx.SystemSettings.GetColour(wx.SYS_COLOUR_GRAYTEXT))
        sizer.Add(optional, 0, wx.EXPAND)
        page.SetSizer(sizer)
        return page

    def _page_stt(self, parent: wx.Window) -> wx.Panel:
        page = wx.Panel(parent)
        sizer = wx.BoxSizer(wx.VERTICAL)
        # Spec §12 is explicit that this warning is stated plainly and not
        # softened: the customer lives where the connection drops, and finding
        # this out on a plane reads as a broken product.
        warning = _wrapped(page, t("wizard.stt.warning"))
        warning.SetFont(warning.GetFont().Bold())
        sizer.Add(warning, 0, wx.EXPAND | wx.BOTTOM, 12)
        sizer.Add(_wrapped(page, t("wizard.stt.body")), 0, wx.EXPAND)
        page.SetSizer(sizer)
        return page

    def _page_key(self, parent: wx.Window) -> wx.Panel:
        page = wx.Panel(parent)
        sizer = wx.BoxSizer(wx.VERTICAL)
        self._key_form = _KeyForm(page, self._c)
        sizer.Add(self._key_form, 1, wx.EXPAND)
        page.SetSizer(sizer)
        return page

    def _page_test(self, parent: wx.Window) -> wx.Panel:
        page = wx.Panel(parent)
        sizer = wx.BoxSizer(wx.VERTICAL)
        self._test_body = _wrapped(page, t("wizard.test.body", key=t("common.dash")))
        sizer.Add(self._test_body, 0, wx.EXPAND | wx.BOTTOM, 12)
        self._test_status = _wrapped(page, t("wizard.test.waiting"))
        sizer.Add(self._test_status, 0, wx.EXPAND | wx.BOTTOM, 12)
        self._autostart = wx.CheckBox(page, label=t("wizard.test.autostart"))
        self._autostart.Bind(wx.EVT_CHECKBOX, self._on_autostart)
        sizer.Add(self._autostart, 0, wx.BOTTOM, 12)
        tray = _wrapped(page, t("wizard.test.tray"))
        tray.SetForegroundColour(wx.SystemSettings.GetColour(wx.SYS_COLOUR_GRAYTEXT))
        sizer.Add(tray, 0, wx.EXPAND)
        page.SetSizer(sizer)
        return page

    # ------------------------------------------------------------------ #
    # step handlers
    # ------------------------------------------------------------------ #

    def _on_mic_check(self, _event: wx.CommandEvent) -> None:
        self._mic_button.Disable()
        _relabel(self._mic_status, t("settings.mic.checking"))
        self._c.request({"type": "mic_probe"}, self._on_mic_answered)

    def _on_mic_answered(self, frame: dict[str, Any]) -> None:
        self._mic_button.Enable()
        if "error" in frame:
            _relabel(self._mic_status, t("wizard.mic.busy"))
            return
        result = frame["result"]
        line = mic_line(result)
        self.state["mic_line"] = line
        self.state["mic_denied"] = result.get("code") == "mic_denied"
        _relabel(self._mic_status, line)
        # The privacy-settings button appears only where it is the answer.
        self._mic_settings.Show(bool(self.state["mic_denied"]))
        self.Layout()

    def _on_capture(self, _event: wx.CommandEvent) -> None:
        dialog = HotkeyCaptureDialog(self._c, self)
        dialog.ShowModal()
        dialog.Destroy()
        self._c.request({"type": "get_config"}, self._on_config)

    def _on_autostart(self, _event: wx.CommandEvent) -> None:
        self._c.request(
            {"type": "autostart_set", "enabled": self._autostart.GetValue()},
            self._on_autostart_state,
        )

    def _on_autostart_state(self, frame: dict[str, Any]) -> None:
        if "error" in frame:
            self._autostart.Disable()
            return
        state = frame["result"]
        self._autostart.SetValue(bool(state.get("enabled")))
        self._autostart.Enable(bool(state.get("available")))

    # -- the live test --------------------------------------------------- #

    def _enter_test(self) -> None:
        import time

        self._test_since_ms = int(time.time() * 1000)
        self._c.request({"type": "autostart_get"}, self._on_autostart_state)
        # A dictation that ended is the signal; the timer only covers a missed
        # push (see _POLL_MS). The lambda is kept so _release_test can tell
        # our seat from another wizard's.
        self._state_seat = lambda _state: self._poll_test()
        self._c.on_state = self._state_seat
        self._timer.Start(_POLL_MS)
        self._poll_test()

    def _poll_test(self) -> None:
        if self._step != STEP_COUNT - 1:
            return
        self._c.request(
            {"type": "history_search", "query": "", "limit": 1}, self._on_test_rows
        )

    def _on_test_rows(self, frame: dict[str, Any]) -> None:
        if "error" in frame:
            return
        rows = frame["result"].get("rows") or []
        if not rows:
            return
        row = rows[0]
        # Only a dictation made *during* this step proves anything: yesterday's
        # history would congratulate the user for something they did not do.
        if int(row.get("created_at", 0)) < self._test_since_ms:
            return
        text = str(row.get("text", "")).strip()
        if not text:
            return
        self.state["tested"] = True
        self._release_test()  # the test is proven; stop watching
        _relabel(self._test_status, t("wizard.test.got", text=text[:200]))
        self.Layout()


# --------------------------------------------------------------------------- #
# small builders
# --------------------------------------------------------------------------- #

_WRAP: Final = 620


def _wrapped(parent: wx.Window, text: str, width: int = _WRAP) -> wx.StaticText:
    """A paragraph that wraps. The wizard is the one place in the product with
    real prose in it, and an unwrapped StaticText simply runs off the window."""
    label = wx.StaticText(parent, label=text)
    label.Wrap(width)
    return label


def _relabel(label: wx.StaticText, text: str) -> None:
    """Replace a wrapped paragraph's text and wrap it again.

    ``SetLabel`` discards the line breaks ``Wrap`` inserted, so a longer
    sentence set later becomes one endless line clipped by the panel edge —
    which is exactly how the live-test step lost the second half of its own
    instruction. Every dynamic paragraph goes through here.
    """
    label.SetLabel(text)
    label.Wrap(_WRAP)


def _labelled(parent: wx.Window, label: str, control: wx.Window) -> wx.Sizer:
    row = wx.BoxSizer(wx.HORIZONTAL)
    row.Add(wx.StaticText(parent, label=label), 0,
            wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 12)
    row.Add(control, 0, wx.ALIGN_CENTER_VERTICAL)
    return row


def _open(target: str) -> None:
    """Open a Windows settings page. ``os.startfile`` handles the ``ms-settings:``
    scheme; a failure is silent by design — the step's text already says where
    the setting lives, and a stack trace helps nobody standing at step 1."""
    try:
        os.startfile(target)  # noqa: S606 — the user pressed the button that says this
    except OSError:
        pass
