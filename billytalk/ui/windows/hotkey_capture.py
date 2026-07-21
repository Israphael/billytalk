"""The hotkey-capture dialog (spec §12 «живой захват», the NVDA pattern).

The dialog itself does nothing with the keyboard: it asks the CORE to enter
capture (``capture_hotkey_start``), and the core's hook swallows and reports
the next press as a ``hotkey_captured`` push. Everything that must not fail
lives on the core's side — the 30-second timeout and the channel-break
release (spec §14) — so a dead interface can never leave keys suppressed;
this window is just the face.

Escape cancels (the core reports an empty capture), the Cancel button sends
an explicit ``capture_hotkey_stop``, and either way the dialog leaves the
controller's push seat clean behind itself.
"""

from __future__ import annotations

from typing import Any

import wx

from ..controller import UiController
from . import dress

__all__ = ["HotkeyCaptureDialog"]


class HotkeyCaptureDialog(wx.Dialog):
    """Modal «нажмите кнопку». ``captured`` carries the final frame — codes
    empty means cancelled or timed out."""

    def __init__(
        self, controller: UiController, parent: wx.Window | None = None,
        *, action: str = "ptt",
    ) -> None:
        super().__init__(parent, title="BillyTalk — захват кнопки")
        self._c = controller
        self.captured: dict[str, Any] | None = None

        sizer = wx.BoxSizer(wx.VERTICAL)
        prompt = wx.StaticText(
            self, label="Нажмите кнопку мыши или клавишу для диктовки"
        )
        font = prompt.GetFont()
        font.SetPointSize(font.GetPointSize() + 2)
        font = font.Bold()
        prompt.SetFont(font)
        sizer.Add(prompt, 0, wx.ALL, 18)
        hint = wx.StaticText(
            self,
            label="Esc — отмена. Окно закроется само через 30 секунд.\n"
                  "Боковые кнопки мыши подходят лучше всего.",
        )
        hint.SetForegroundColour(wx.SystemSettings.GetColour(wx.SYS_COLOUR_GRAYTEXT))
        sizer.Add(hint, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 18)
        cancel = wx.Button(self, label="Отмена")
        cancel.Bind(wx.EVT_BUTTON, lambda _e: self._cancel())
        sizer.Add(cancel, 0, wx.ALIGN_RIGHT | wx.ALL, 10)
        self.SetSizerAndFit(sizer)
        self.Bind(wx.EVT_CLOSE, lambda _e: self._cancel())
        dress(self)

        self._c.on_hotkey_captured = self._on_captured
        self._c.request(
            {"type": "capture_hotkey_start", "action": action}, self._on_started
        )

    # ------------------------------------------------------------------ #

    def _on_started(self, frame: dict[str, Any]) -> None:
        if "error" in frame:
            # busy / bad_action: nothing is captured, nothing is suppressed.
            self._finish(ok=False)

    def _on_captured(self, frame: dict[str, Any]) -> None:
        self.captured = frame
        self._finish(ok=bool(frame.get("codes")))

    def _cancel(self) -> None:
        # Explicit stop: the core ends capture now rather than at the timeout.
        self._c.request({"type": "capture_hotkey_stop"}, lambda _f: None)
        self._finish(ok=False)

    def _finish(self, *, ok: bool) -> None:
        # `==`, not `is`: each attribute access mints a fresh bound method, so
        # identity is always False; equality compares __self__ and __func__.
        if self._c.on_hotkey_captured == self._on_captured:
            self._c.on_hotkey_captured = None
        code = wx.ID_OK if ok else wx.ID_CANCEL
        if self.IsModal():
            self.EndModal(code)
        else:
            self.SetReturnCode(code)
            self.Hide()
