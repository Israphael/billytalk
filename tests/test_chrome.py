"""ui/chrome.py: the four OS-branch points, decided by build number (spec §05).

The pure branch table is checked on any machine by passing the build in; the
wx-backed helpers run against a real (headless) session ``wx.App``.
"""

from __future__ import annotations

import pytest

from billytalk.ui.chrome import (
    FALLBACK_GLYPH_FONT,
    FALLBACK_UI_FONT,
    dark_title_attr,
    is_win11,
    preferred_glyph_font,
    preferred_ui_font,
    resolve_face,
    supports_mica,
    system_is_dark,
    windows_build,
)

_WIN10 = 19045          # last Windows 10
_WIN11_22H2 = 22621     # first Mica-capable Windows 11


@pytest.mark.parametrize(
    ("build", "expected"),
    [(17763, False), (19045, False), (21999, False), (22000, True), (26200, True)],
)
def test_is_win11_flips_at_22000(build, expected) -> None:
    assert is_win11(build) is expected


@pytest.mark.parametrize(
    ("build", "expected"),
    [(22000, False), (22620, False), (22621, True), (26200, True)],
)
def test_mica_needs_22h2(build, expected) -> None:
    assert supports_mica(build) is expected


@pytest.mark.parametrize(
    ("build", "attr"),
    [(17763, 19), (18984, 19), (18985, 20), (19041, 20), (22000, 20), (26200, 20)],
)
def test_dark_title_attr_by_build(build, attr) -> None:
    assert dark_title_attr(build) == attr


def test_fonts_branch_by_os() -> None:
    assert preferred_ui_font(_WIN11_22H2) == "Segoe UI Variable"
    assert preferred_ui_font(_WIN10) == FALLBACK_UI_FONT
    assert preferred_glyph_font(_WIN11_22H2) == "Segoe Fluent Icons"
    assert preferred_glyph_font(_WIN10) == FALLBACK_GLYPH_FONT


def test_windows_build_reads_a_sane_number() -> None:
    assert windows_build() >= 10000  # any supported Windows


# -- wx-backed helpers, against a real headless app ------------------------- #

def test_resolve_face_falls_back_when_the_preferred_is_absent(wx_app) -> None:
    # Segoe UI ships on every supported Windows; a nonsense face must fall back
    # rather than let wx silently substitute an unresolved font.
    assert resolve_face("Segoe UI", "Arial") == "Segoe UI"
    assert resolve_face("No Such Face 12345", FALLBACK_UI_FONT) == FALLBACK_UI_FONT


def test_system_is_dark_returns_a_bool(wx_app) -> None:
    assert isinstance(system_is_dark(), bool)
