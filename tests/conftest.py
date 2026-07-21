"""Shared fixtures for the cycle-2 UI tests.

A single ``wx.App`` for the whole session: wxPython allows one per process, and
GUI queries (font enumeration, system appearance, real frames) need it live.
It is never destroyed by hand — teardown of the last wx.App is finicky and the
process is about to exit anyway.
"""

from __future__ import annotations

import pytest


@pytest.fixture(scope="session")
def wx_app():
    import wx

    app = wx.App.Get()
    if app is None:
        app = wx.App()
    yield app
