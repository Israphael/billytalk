"""The milestone-2 windows (spec §11; style spec §05, the approved mockup).

**Native Win32 controls only** — ADR-0001: drawn Fluent imitations destroy
the UIA tree, and v3.0 is the screen-reader release. The layout is one; the
OS supplies the look. What is themed by hand is exactly the §05 hand-list:
the DWM dark title bar, Mica on Win11 22H2+, ``DarkMode_Explorer`` on list
controls, panel colours in dark mode, and the Segoe UI Variable face with
its Windows-10 fallback.

Known and accepted until wxWidgets 3.3 reaches PyPI (measured in spec §05):
buttons, checkboxes and dropdowns stay system-light in dark mode — re-check
PyPI before cycle 3.
"""

from __future__ import annotations

import sys
from pathlib import Path

import wx

from .. import chrome

__all__ = ["dress", "app_icon"]

_DARK_BG = wx.Colour(32, 32, 32)      # the mockup's --bg
_DARK_INPUT = wx.Colour(36, 36, 36)   # --in
_DARK_FG = wx.Colour(255, 255, 255)


def app_icon() -> wx.Icon | None:
    """The window icon — Alt+Tab, the taskbar button, the title bar.

    The file is bundled next to the frozen executable (``billytalk.spec``'s
    ``datas``) and lives in ``packaging/`` in a dev checkout. Missing is not an
    error: an iconless window is ugly, a window that refuses to open because it
    could not find a picture is broken.
    """
    root = Path(getattr(sys, "_MEIPASS", "")) if getattr(sys, "frozen", False) else None
    candidates = (
        [root / "billytalk.ico"] if root
        else [Path(__file__).resolve().parents[3] / "packaging" / "billytalk.ico"]
    )
    for path in candidates:
        if path.is_file():
            icon = wx.Icon(str(path), wx.BITMAP_TYPE_ICO)
            if icon.IsOk():
                return icon
    return None


def dress(frame: wx.Frame) -> bool:
    """Apply the §05 branch points to a fully built window; returns whether
    the system is dark so callers can tune their own painting."""
    icon = app_icon()
    if icon is not None and isinstance(frame, wx.TopLevelWindow):
        frame.SetIcon(icon)
    build = chrome.windows_build()
    dark = chrome.system_is_dark()
    face = chrome.resolve_face(
        chrome.preferred_ui_font(build), chrome.FALLBACK_UI_FONT
    )
    font = frame.GetFont()
    font.SetFaceName(face)
    frame.SetFont(font)
    chrome.apply_dark_titlebar(frame.GetHandle(), dark=dark, build=build)
    chrome.apply_mica(frame.GetHandle(), build=build)
    if dark:
        frame.SetBackgroundColour(_DARK_BG)
        frame.SetForegroundColour(_DARK_FG)
        _paint_dark(frame)
        frame.Refresh()
    return dark


def _paint_dark(window: wx.Window) -> None:
    for child in window.GetChildren():
        if isinstance(child, wx.ListCtrl):
            chrome.apply_explorer_theme(child.GetHandle(), dark=True)
            child.SetBackgroundColour(_DARK_INPUT)
            child.SetForegroundColour(_DARK_FG)
        elif isinstance(child, wx.ListBox):
            chrome.apply_explorer_theme(child.GetHandle(), dark=True)
            child.SetBackgroundColour(_DARK_BG)
            child.SetForegroundColour(_DARK_FG)
        elif isinstance(child, wx.TextCtrl):
            child.SetBackgroundColour(_DARK_INPUT)
            child.SetForegroundColour(_DARK_FG)
        elif not isinstance(child, (wx.Button, wx.Choice)):
            # panels, statics, checkboxes, books: our colours; buttons and
            # dropdowns keep the system look (the accepted 3.2.9 limitation).
            child.SetBackgroundColour(_DARK_BG)
            child.SetForegroundColour(_DARK_FG)
        _paint_dark(child)
