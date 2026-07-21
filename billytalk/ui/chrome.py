"""Window chrome: the four points where the look branches by OS (spec §05).

The customer's rule is "one layout, the OS supplies the look": wxPython draws
native Win32 controls, so corners, control themes and the system palette come
from Windows itself. We touch exactly four things by hand — the title-bar font,
the Mica backdrop, the dark title bar, and the glyph font — and everything in
this module is one of those four or the OS check they depend on.

**This is the one module in ``ui/`` allowed to import ctypes** (harness §1's
border rule: ``ui/`` speaks ctypes only for window styling; the plashka in
``overlay.py`` is the other styling exception). No input, clipboard or audio
API is ever reached from here.

Measured facts under this build (spec §05, re-verified 2026-07-21):

* wxPython 4.2.5 = wxWidgets 3.2.9; ``wx.App.MSWEnableDarkMode`` does **not**
  exist (it arrived in wxWidgets 3.3, not on PyPI yet). Dark mode is therefore
  hand-built: the DWM title attribute here, plus ``DarkMode_Explorer`` on the
  lists and our own panel colours in the windows (cycle-2, later).
* Theme *detection* is available — ``wx.SystemSettings.GetAppearance().IsDark()``
  — and live theme changes arrive as ``WM_SETTINGCHANGE``.

Every decision is a pure function of the Windows build number, passed in the
way the state machine takes ``now_ms`` — so the whole branch table is testable
on any machine, and only :func:`windows_build` reads the real system.
"""

from __future__ import annotations

import ctypes as ct
import sys
from ctypes import wintypes
from typing import Final

__all__ = [
    "windows_build",
    "is_win11",
    "supports_mica",
    "dark_title_attr",
    "preferred_ui_font",
    "preferred_glyph_font",
    "FALLBACK_UI_FONT",
    "FALLBACK_GLYPH_FONT",
    "apply_dark_titlebar",
    "apply_mica",
    "resolve_face",
    "system_is_dark",
]

# --------------------------------------------------------------------------- #
# build thresholds — the only magic numbers, named once
# --------------------------------------------------------------------------- #

_WIN11_BUILD: Final = 22000
"""First Windows 11 build. Windows 10 tops out at 19045; 22000 is 21H2."""

_MICA_BUILD: Final = 22621
"""Windows 11 22H2. Mica via DWMWA_SYSTEMBACKDROP_TYPE is stable from here; the
earlier 22000 DWMWA_MICA_EFFECT (1029) path is not worth carrying (spec §05)."""

_DARK_TITLE_20H1_BUILD: Final = 18985
"""DWMWA_USE_IMMERSIVE_DARK_MODE became attribute 20 here; the 1809 line
(build 17763) used an undocumented 19 (spec §05: "20 (19 на сборке 1809)")."""

# DwmSetWindowAttribute attribute ids (dwmapi.h; not in win32con).
_DWMWA_USE_IMMERSIVE_DARK_MODE: Final = 20
_DWMWA_USE_IMMERSIVE_DARK_MODE_1809: Final = 19
_DWMWA_SYSTEMBACKDROP_TYPE: Final = 38
_DWMSBT_MAINWINDOW: Final = 2  # Mica

FALLBACK_UI_FONT: Final = "Segoe UI"
"""Present on both Windows 10 and 11 — the safe floor when Segoe UI Variable
is missing (it ships only on Windows 11)."""

FALLBACK_GLYPH_FONT: Final = "Segoe MDL2 Assets"
"""The Windows 10 glyph font; its code points are a subset of Segoe Fluent
Icons, so the shared glyphs render on either (spec §05)."""

# --------------------------------------------------------------------------- #
# ctypes: dwmapi only, for the two hand-set window attributes
# --------------------------------------------------------------------------- #

_dwmapi = ct.WinDLL("dwmapi", use_last_error=True)
# HRESULT DwmSetWindowAttribute(HWND, DWORD, LPCVOID, DWORD). The value is
# passed by pointer; argtypes are as mandatory as restype here — a 64-bit HWND
# overflows the default c_int (research/02, the same trap the tray icons hit).
_dwmapi.DwmSetWindowAttribute.restype = ct.c_long  # HRESULT
_dwmapi.DwmSetWindowAttribute.argtypes = (
    wintypes.HWND, wintypes.DWORD, ct.c_void_p, wintypes.DWORD,
)

# --------------------------------------------------------------------------- #
# pure decisions — functions of the build number, testable on any machine
# --------------------------------------------------------------------------- #


def windows_build() -> int:
    """The running Windows build (the impure reader; logic takes it as data)."""
    return int(sys.getwindowsversion().build)


def is_win11(build: int) -> bool:
    return build >= _WIN11_BUILD


def supports_mica(build: int) -> bool:
    """Mica via the stable backdrop attribute — Windows 11 22H2 and up."""
    return build >= _MICA_BUILD


def dark_title_attr(build: int) -> int:
    """Which DWMWA_USE_IMMERSIVE_DARK_MODE id this build understands: 20 from
    20H1 on, 19 on the 1809 line where it was undocumented (spec §05)."""
    if build >= _DARK_TITLE_20H1_BUILD:
        return _DWMWA_USE_IMMERSIVE_DARK_MODE
    return _DWMWA_USE_IMMERSIVE_DARK_MODE_1809


def preferred_ui_font(build: int) -> str:
    """Segoe UI Variable is the Windows 11 UI face; Windows 10 has plain Segoe
    UI. The caller resolves the actual face through :func:`resolve_face`."""
    return "Segoe UI Variable" if is_win11(build) else FALLBACK_UI_FONT


def preferred_glyph_font(build: int) -> str:
    return "Segoe Fluent Icons" if is_win11(build) else FALLBACK_GLYPH_FONT


# --------------------------------------------------------------------------- #
# appliers — the two hand-set attributes (ctypes; live-tested with a real HWND)
# --------------------------------------------------------------------------- #


def apply_dark_titlebar(hwnd: int, *, dark: bool, build: int) -> bool:
    """Paint the title bar dark (or light). Tries the build's attribute id and
    then the other, so a wrong build guess degrades to a no-op rather than a
    lie. Returns whether DWM accepted it."""
    value = wintypes.BOOL(bool(dark))
    primary = dark_title_attr(build)
    other = (
        _DWMWA_USE_IMMERSIVE_DARK_MODE_1809
        if primary == _DWMWA_USE_IMMERSIVE_DARK_MODE
        else _DWMWA_USE_IMMERSIVE_DARK_MODE
    )
    for attr in (primary, other):
        if _dwmapi.DwmSetWindowAttribute(hwnd, attr, ct.byref(value), ct.sizeof(value)) == 0:
            return True
    return False


def apply_mica(hwnd: int, *, build: int) -> bool:
    """Request the Mica backdrop. A no-op (returns False) below Windows 11 22H2,
    where the attribute is unknown and the window keeps the flat system fill."""
    if not supports_mica(build):
        return False
    value = wintypes.DWORD(_DWMSBT_MAINWINDOW)
    return (
        _dwmapi.DwmSetWindowAttribute(
            hwnd, _DWMWA_SYSTEMBACKDROP_TYPE, ct.byref(value), ct.sizeof(value)
        )
        == 0
    )


# --------------------------------------------------------------------------- #
# wx-dependent helpers — imported lazily so the pure logic needs no wx/app
# --------------------------------------------------------------------------- #


def resolve_face(preferred: str, fallback: str) -> str:
    """The preferred face if the system has it installed, else the fallback.
    Segoe UI Variable is absent on Windows 10, so asking for it there must not
    yield an unresolved (silently substituted) font."""
    import wx

    faces = set(wx.FontEnumerator.GetFacenames())
    return preferred if preferred in faces else fallback


def system_is_dark() -> bool:
    """Whether Windows is in dark mode right now (spec §05: wx.SystemAppearance,
    confirmed present on wxWidgets 3.2.9)."""
    import wx

    return bool(wx.SystemSettings.GetAppearance().IsDark())
