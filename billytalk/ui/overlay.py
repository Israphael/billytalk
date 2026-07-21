"""The plashka — the always-on-top status pill (spec §11).

It exists so the ~400 ms between releasing the button and the text landing is
not silent: without it the user, seeing nothing, presses again (spec §11). It
**lives through Finalizing and Delivering** with the "transcribing" look, and
shows the "recording" look while the button is held.

Two rules make it a plashka and not a window, and both are load-bearing:

* The five extended styles ``WS_EX_NOACTIVATE | TRANSPARENT | LAYERED |
  TOOLWINDOW | TOPMOST``, set with ``SetWindowLongPtrW`` — ``SetWindowLongW``
  truncates a 64-bit style word (harness §13). NOACTIVATE and TRANSPARENT make
  it click-through and unfocusable; a layered window must have its attributes
  set or it never paints, so the alpha goes on in the same breath.
* It is shown only with ``ShowWindow(SW_SHOWNOACTIVATE)``, **never**
  ``wx.Window.Show`` — that would activate it and steal the foreground, which
  is exactly what a dictation overlay must never do (spec §11, acceptance
  "плашка не крадёт фокус").

This is the second and last place ``ui/`` speaks ctypes, and only for window
styling (harness §1's border rule; ``chrome.py`` is the first).

The pill is drawn by us, identically on Windows 10 and 11 (spec §05 mockup:
``rgba(24,24,26,.94)`` fill, white text, a red dot for recording, blue dots
for transcribing). The rounded shape is a window region plus a uniform layer
alpha; if a live pass shows aliased corners, the upgrade is
``UpdateLayeredWindow`` with a per-pixel-alpha bitmap (recorded for the
milestone-2 review). The **cannot-be-disabled-before-first-Ctrl+Alt+Z** rule
(spec §11) is a settings toggle, so it lands with the settings window in
milestone 3; here the pill simply shows whenever the core says to.
"""

from __future__ import annotations

import ctypes as ct
from ctypes import wintypes
from enum import Enum
from typing import Final

import wx

from . import chrome

__all__ = ["Plashka", "PlashkaLook", "apply_overlay_styles"]

# Extended-style bits and show commands (winuser.h; not all in wx).
_GWL_EXSTYLE: Final = -20
_WS_EX_TOPMOST: Final = 0x0000_0008
_WS_EX_TRANSPARENT: Final = 0x0000_0020
_WS_EX_TOOLWINDOW: Final = 0x0000_0080
_WS_EX_LAYERED: Final = 0x0008_0000
_WS_EX_NOACTIVATE: Final = 0x0800_0000
_OVERLAY_EX: Final = (
    _WS_EX_NOACTIVATE | _WS_EX_TRANSPARENT | _WS_EX_LAYERED
    | _WS_EX_TOOLWINDOW | _WS_EX_TOPMOST
)
_SW_HIDE: Final = 0
_SW_SHOWNOACTIVATE: Final = 4
_LWA_ALPHA: Final = 0x02
_PILL_ALPHA: Final = 240  # spec §05 mockup: rgba(24,24,26,.94) ≈ 0.94 * 255

_user32 = ct.WinDLL("user32", use_last_error=True)
_gdi32 = ct.WinDLL("gdi32", use_last_error=True)

# LONG_PTR everywhere the style word crosses ctypes — a 64-bit HWND or style
# overflows the default c_int in either direction (harness §13, research/02).
_LONG_PTR = ct.c_ssize_t
_user32.GetWindowLongPtrW.restype = _LONG_PTR
_user32.GetWindowLongPtrW.argtypes = (wintypes.HWND, ct.c_int)
_user32.SetWindowLongPtrW.restype = _LONG_PTR
_user32.SetWindowLongPtrW.argtypes = (wintypes.HWND, ct.c_int, _LONG_PTR)
_user32.ShowWindow.restype = wintypes.BOOL
_user32.ShowWindow.argtypes = (wintypes.HWND, ct.c_int)
_user32.SetLayeredWindowAttributes.restype = wintypes.BOOL
_user32.SetLayeredWindowAttributes.argtypes = (
    wintypes.HWND, wintypes.COLORREF, wintypes.BYTE, wintypes.DWORD,
)
_user32.SetWindowRgn.restype = ct.c_int
_user32.SetWindowRgn.argtypes = (wintypes.HWND, wintypes.HRGN, wintypes.BOOL)
_gdi32.CreateRoundRectRgn.restype = wintypes.HRGN
_gdi32.CreateRoundRectRgn.argtypes = (ct.c_int,) * 6


class PlashkaLook(Enum):
    RECORDING = "recording"
    TRANSCRIBING = "transcribing"


# look → (accent RGB, label). The mockup's red dot / blue dots (spec §05).
_LOOKS: Final = {
    PlashkaLook.RECORDING: ((0xE8, 0x11, 0x23), "Запись"),
    PlashkaLook.TRANSCRIBING: ((0x8E, 0xCD, 0xF5), "Расшифровка…"),
}
_PILL_BG: Final = wx.Colour(24, 24, 26)
_PILL_FG: Final = wx.Colour(255, 255, 255)
_SIZE: Final = (210, 46)
_PAD: Final = 18
_GAP: Final = 9
_MARGIN_BELOW: Final = 90  # gap above the taskbar


def apply_overlay_styles(hwnd: int) -> None:
    """Set spec §11's five extended styles and arm the layer alpha. Idempotent —
    the styles are OR-ed in, so re-applying is a no-op."""
    ex = _user32.GetWindowLongPtrW(hwnd, _GWL_EXSTYLE)
    _user32.SetWindowLongPtrW(hwnd, _GWL_EXSTYLE, ex | _OVERLAY_EX)
    _user32.SetLayeredWindowAttributes(hwnd, 0, _PILL_ALPHA, _LWA_ALPHA)


class Plashka:
    """The status pill. A wx.Frame that is **never** ``Show``\\ n — it appears
    only through ``SW_SHOWNOACTIVATE`` so it can never take the foreground."""

    def __init__(self) -> None:
        self._frame = wx.Frame(
            None,
            style=wx.FRAME_NO_TASKBAR | wx.STAY_ON_TOP | wx.BORDER_NONE,
        )
        self._frame.SetBackgroundStyle(wx.BG_STYLE_PAINT)  # we paint every pixel
        self._frame.SetSize(_SIZE)
        self._hwnd = int(self._frame.GetHandle())
        apply_overlay_styles(self._hwnd)
        self._round_corners()
        self._position()
        self._look: PlashkaLook | None = None
        self._face = chrome.resolve_face(
            chrome.preferred_ui_font(chrome.windows_build()), chrome.FALLBACK_UI_FONT
        )
        self._frame.Bind(wx.EVT_PAINT, self._on_paint)

    @property
    def hwnd(self) -> int:
        return self._hwnd

    def show(self, look: PlashkaLook) -> None:
        """Display the pill with ``look``, without ever taking the foreground."""
        self._look = look
        self._frame.Refresh()
        self._frame.Update()
        _user32.ShowWindow(self._hwnd, _SW_SHOWNOACTIVATE)  # never frame.Show()

    def hide(self) -> None:
        _user32.ShowWindow(self._hwnd, _SW_HIDE)

    def destroy(self) -> None:
        self._frame.Destroy()

    # -- internals ------------------------------------------------------- #

    def _position(self) -> None:
        w, h = _SIZE
        dw, dh = wx.DisplaySize()
        self._frame.SetPosition(wx.Point((dw - w) // 2, dh - h - _MARGIN_BELOW))

    def _round_corners(self) -> None:
        w, h = _SIZE
        region = _gdi32.CreateRoundRectRgn(0, 0, w + 1, h + 1, h, h)  # radius = h → a pill
        _user32.SetWindowRgn(self._hwnd, region, True)  # the window now owns the region

    def _on_paint(self, event: wx.PaintEvent) -> None:
        dc = wx.AutoBufferedPaintDC(self._frame)
        gc = wx.GraphicsContext.Create(dc)
        if gc is None:
            return
        w, h = self._frame.GetClientSize()
        gc.SetPen(wx.TRANSPARENT_PEN)
        gc.SetBrush(wx.Brush(_PILL_BG))
        gc.DrawRectangle(0, 0, w, h)  # the region already rounds the edges
        if self._look is None:
            return

        accent_rgb, label = _LOOKS[self._look]
        accent = wx.Colour(*accent_rgb)
        font = wx.Font(wx.FontInfo(11).FaceName(self._face))
        cy = h / 2

        if self._look is PlashkaLook.RECORDING:
            radius = 5
            gc.SetBrush(wx.Brush(accent))
            gc.DrawEllipse(_PAD, cy - radius, radius * 2, radius * 2)
            lead_width = radius * 2
        else:
            gc.SetFont(font, accent)
            dots = "•••"
            lead_width, dots_h = gc.GetTextExtent(dots)
            gc.DrawText(dots, _PAD, cy - dots_h / 2)

        gc.SetFont(font, _PILL_FG)
        _, label_h = gc.GetTextExtent(label)
        gc.DrawText(label, _PAD + lead_width + _GAP, cy - label_h / 2)
