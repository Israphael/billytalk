"""ui/overlay.py: the plashka is unfocusable, click-through, topmost (spec §11).

Live wx windows on the session app. The *look* (rounded corners, colours) is a
manual check on the customer's screen; what is asserted here is the Win32
contract the acceptance «плашка не крадёт фокус» rests on — and that the paint
path runs without raising, which otherwise only shows up on that screen.
"""

from __future__ import annotations

import ctypes as ct
from ctypes import wintypes

import pytest

from billytalk.ui.overlay import (
    _GWL_EXSTYLE,
    _OVERLAY_EX,
    _WS_EX_LAYERED,
    _WS_EX_NOACTIVATE,
    _WS_EX_TOOLWINDOW,
    _WS_EX_TOPMOST,
    _WS_EX_TRANSPARENT,
    Plashka,
    PlashkaLook,
    apply_overlay_styles,
)

_user32 = ct.WinDLL("user32", use_last_error=True)
_LONG_PTR = ct.c_ssize_t
_user32.GetWindowLongPtrW.restype = _LONG_PTR
_user32.GetWindowLongPtrW.argtypes = (wintypes.HWND, ct.c_int)
_user32.GetForegroundWindow.restype = wintypes.HWND
_user32.IsWindowVisible.restype = wintypes.BOOL
_user32.IsWindowVisible.argtypes = (wintypes.HWND,)


@pytest.fixture
def plashka(wx_app):
    p = Plashka()
    yield p
    p.destroy()


def test_plashka_carries_the_five_no_activate_ex_styles(plashka) -> None:
    ex = _user32.GetWindowLongPtrW(plashka.hwnd, _GWL_EXSTYLE)
    for bit, name in (
        (_WS_EX_NOACTIVATE, "NOACTIVATE"),
        (_WS_EX_TRANSPARENT, "TRANSPARENT"),
        (_WS_EX_LAYERED, "LAYERED"),
        (_WS_EX_TOOLWINDOW, "TOOLWINDOW"),
        (_WS_EX_TOPMOST, "TOPMOST"),
    ):
        assert ex & bit, f"WS_EX_{name} missing from the plashka"


def test_showing_the_plashka_never_takes_the_foreground(plashka) -> None:
    plashka.show(PlashkaLook.TRANSCRIBING)
    assert _user32.IsWindowVisible(plashka.hwnd), "SW_SHOWNOACTIVATE did not show it"
    # The whole point: a shown plashka is never the foreground window.
    assert _user32.GetForegroundWindow() != plashka.hwnd, "the plashka grabbed focus"
    plashka.hide()
    assert not _user32.IsWindowVisible(plashka.hwnd), "hide left it on screen"


def test_both_looks_paint_without_raising(plashka) -> None:
    # Update() runs _on_paint synchronously; a wx/font/graphics misuse would
    # raise here instead of only failing to draw on the customer's screen.
    for look in PlashkaLook:
        plashka.show(look)
        plashka._frame.Update()
    plashka.hide()


def test_apply_overlay_styles_is_idempotent(plashka) -> None:
    apply_overlay_styles(plashka.hwnd)
    apply_overlay_styles(plashka.hwnd)
    ex = _user32.GetWindowLongPtrW(plashka.hwnd, _GWL_EXSTYLE)
    assert ex & _OVERLAY_EX == _OVERLAY_EX
