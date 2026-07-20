"""The tray icon and the hidden window (spec §11, harness §2, OQ §22–§23).

The window and icon tests run live: they create a real top-level window and
a real (briefly visible) tray icon on the machine. Explorer's own recovery
broadcast cannot be faked without spamming every process, so idempotency is
exercised by sending ``TaskbarCreated`` directly to our window — same
handler, same path, nobody else disturbed.
"""

from __future__ import annotations

import ctypes as ct
import threading
from ctypes import wintypes
from uuid import uuid4

import pytest

from billytalk.core.tray import (
    NIN_SELECT,
    TRAY_CALLBACK_MSG,
    WM_CONTEXTMENU,
    HiddenWindow,
    TrayIcon,
    TrayMenuItem,
    TrayState,
    _BITMAPINFOHEADER,
    _ICON_SIZE,
    _ICONINFO,
    _TOOLTIPS,
    _build_menu,
    _draw_state_icon,
    tray_state_for,
    tray_tooltip_for,
)

_user32 = ct.WinDLL("user32", use_last_error=True)
_gdi32 = ct.WinDLL("gdi32", use_last_error=True)
_user32.SendMessageW.restype = ct.c_ssize_t
_user32.SendMessageW.argtypes = (
    wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
)
_user32.IsWindow.restype = wintypes.BOOL
_user32.IsWindowVisible.restype = wintypes.BOOL
_user32.GetParent.restype = wintypes.HWND
_user32.GetMenuItemCount.restype = ct.c_int
_user32.DestroyMenu.restype = wintypes.BOOL
_user32.DestroyIcon.restype = wintypes.BOOL
_user32.GetIconInfo.restype = wintypes.BOOL
_user32.GetIconInfo.argtypes = (wintypes.HICON, ct.POINTER(_ICONINFO))
_user32.GetDC.restype = wintypes.HDC
_user32.GetDC.argtypes = (wintypes.HWND,)
_user32.ReleaseDC.argtypes = (wintypes.HWND, wintypes.HDC)
# 64-bit HBITMAP/HDC overflow ctypes' default c_int (research/02) — spell the
# GDI prototypes out, same as the production module does.
_gdi32.GetDIBits.restype = ct.c_int
_gdi32.GetDIBits.argtypes = (
    wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT,
    ct.c_void_p, ct.POINTER(_BITMAPINFOHEADER), wintypes.UINT,
)
_gdi32.DeleteObject.restype = wintypes.BOOL
_gdi32.DeleteObject.argtypes = (wintypes.HGDIOBJ,)

WM_TEST = 0x8000 + 0x99


@pytest.fixture
def window():
    w = HiddenWindow(class_suffix=f"-t{uuid4().hex[:8]}")
    w.start()
    assert w.wait_ready(5.0) and w.hwnd, "hidden window failed to start"
    yield w
    w.stop()


# --------------------------------------------------------------------------- #
# the pure priority fold (OPEN-QUESTIONS §23: seven renderings)
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    ("phase", "enabled", "offline", "queue_len", "expected"),
    [
        ("Idle", True, False, 0, TrayState.IDLE),
        ("Idle", True, True, 0, TrayState.OFFLINE),
        ("Idle", True, False, 2, TrayState.QUEUE),
        ("Idle", True, True, 2, TrayState.QUEUE),  # doing beats waiting
        ("Initialized", True, True, 0, TrayState.RECORDING),
        ("Recording", True, False, 0, TrayState.RECORDING),
        ("Finalizing", True, True, 1, TrayState.TRANSCRIBING),
        ("Delivering", True, False, 0, TrayState.TRANSCRIBING),
        ("Failed", True, True, 3, TrayState.ERROR),
        # Disabled wins over absolutely everything: the user switched it off,
        # the icon must say so even while the tail of a recording finalises.
        ("Recording", False, False, 0, TrayState.STOPPED),
        ("Failed", False, True, 3, TrayState.STOPPED),
    ],
)
def test_tray_state_priority(phase, enabled, offline, queue_len, expected) -> None:
    got = tray_state_for(
        phase_name=phase, enabled=enabled, offline=offline, queue_len=queue_len
    )
    assert got is expected


def test_seven_distinct_states_exist() -> None:
    assert len(TrayState) == 7  # six of spec §11 plus §3's offline (OQ §23)


# --------------------------------------------------------------------------- #
# the hidden window
# --------------------------------------------------------------------------- #

def test_window_is_a_real_invisible_top_level(window) -> None:
    """Spec §11: TaskbarCreated never reaches a message-only window, so the
    window must be genuinely top-level (no parent) — and never visible."""
    assert _user32.IsWindow(window.hwnd)
    assert not _user32.GetParent(window.hwnd)
    assert not _user32.IsWindowVisible(window.hwnd)


def test_registered_handler_answers_and_fallback_is_defwindowproc(window) -> None:
    window.on(WM_TEST, lambda wparam, lparam: 42)
    assert _user32.SendMessageW(window.hwnd, WM_TEST, 0, 0) == 42
    assert _user32.SendMessageW(window.hwnd, WM_TEST + 1, 0, 0) == 0


def test_handler_exception_does_not_kill_the_pump(window) -> None:
    def bad(wparam: int, lparam: int) -> int:
        raise RuntimeError("handler bug")

    window.on(WM_TEST, bad)
    assert _user32.SendMessageW(window.hwnd, WM_TEST, 0, 0) == 0
    window.on(WM_TEST, lambda wparam, lparam: 7)
    assert _user32.SendMessageW(window.hwnd, WM_TEST, 0, 0) == 7


# --------------------------------------------------------------------------- #
# icons and menus
# --------------------------------------------------------------------------- #

def test_an_icon_is_drawn_for_every_state_in_both_themes() -> None:
    for state in TrayState:
        for light in (False, True):
            handle = _draw_state_icon(state, light_taskbar=light)
            assert handle, f"no icon for {state} light={light}"
            _user32.DestroyIcon(handle)


def test_menu_is_built_from_the_model() -> None:
    items = (
        TrayMenuItem(101, "Открыть настройки", enabled=False),
        TrayMenuItem(),
        TrayMenuItem(102, "Диктовка включена", checked=True),
        TrayMenuItem(109, "Выход"),
    )
    menu = _build_menu(items)
    try:
        assert _user32.GetMenuItemCount(menu) == 4
    finally:
        _user32.DestroyMenu(menu)


# --------------------------------------------------------------------------- #
# the live icon
# --------------------------------------------------------------------------- #

def test_tray_icon_lifecycle_and_taskbar_created_idempotency(window) -> None:
    selected = threading.Event()
    icon = TrayIcon(
        window,
        menu_provider=lambda: (TrayMenuItem(1, "Тест"),),
        on_command=lambda command: None,
        on_select=selected.set,
    )
    try:
        assert icon.add()
        for state in TrayState:
            assert icon.set_state(state), f"NIM_MODIFY failed for {state}"

        # Explorer restart, twice — the handler must be idempotent (spec §11).
        for _ in range(2):
            _user32.SendMessageW(window.hwnd, icon._taskbar_created, 0, 0)
            assert icon.set_state(TrayState.IDLE), "icon lost after re-add"

        # A v4 select gesture (lParam low word carries the event).
        _user32.SendMessageW(window.hwnd, TRAY_CALLBACK_MSG, 0, NIN_SELECT)
        assert selected.wait(2.0)
    finally:
        icon.remove()


def test_set_state_before_add_reports_false(window) -> None:
    icon = TrayIcon(
        window,
        menu_provider=lambda: (),
        on_command=lambda command: None,
    )
    assert icon.set_state(TrayState.RECORDING) is False


# --------------------------------------------------------------------------- #
# the alpha pass — the invisible-icon trap (cycle-2 review tail)
# --------------------------------------------------------------------------- #

def _icon_alpha_bytes(handle: int) -> set[int]:
    """The distinct alpha bytes of an HICON's colour bitmap — how the runtime
    icon actually looks, read back through GetIconInfo + GetDIBits, not how we
    hoped to draw it."""
    info = _ICONINFO()
    assert _user32.GetIconInfo(handle, ct.byref(info)), "GetIconInfo failed"
    try:
        header = _BITMAPINFOHEADER(
            ct.sizeof(_BITMAPINFOHEADER), _ICON_SIZE, -_ICON_SIZE, 1, 32, 0, 0, 0, 0, 0, 0
        )
        buffer = (ct.c_uint32 * (_ICON_SIZE * _ICON_SIZE))()
        dc = _user32.GetDC(None)
        try:
            scanned = _gdi32.GetDIBits(
                dc, info.hbmColor, 0, _ICON_SIZE, buffer, ct.byref(header), 0  # DIB_RGB_COLORS
            )
        finally:
            _user32.ReleaseDC(None, dc)
        assert scanned == _ICON_SIZE, "GetDIBits did not read the icon"
        return {(pixel >> 24) & 0xFF for pixel in buffer}
    finally:
        _gdi32.DeleteObject(info.hbmColor)
        _gdi32.DeleteObject(info.hbmMask)


@pytest.mark.parametrize("state", list(TrayState))
def test_drawn_icon_carries_alpha_and_is_not_the_invisible_trap(state) -> None:
    """GDI pens leave alpha at zero; without the hand-written alpha pass the
    icon is a fully transparent handle that passes every 'was it created?'
    check. Assert the pixels the runtime shows are actually opaque, at the
    dimmed or full opacity the state calls for."""
    for light in (False, True):
        handle = _draw_state_icon(state, light_taskbar=light)
        try:
            alphas = _icon_alpha_bytes(handle)
        finally:
            _user32.DestroyIcon(handle)
        assert alphas != {0}, f"{state} light={light}: icon is fully transparent"
        expected = 0x60 if state in (TrayState.OFFLINE, TrayState.STOPPED) else 0xFF
        assert expected in alphas, f"{state} light={light}: opacity {expected:#x} absent"


# --------------------------------------------------------------------------- #
# menu dispatch: gesture -> menu -> on_command (cycle-2 review tail)
# --------------------------------------------------------------------------- #

def test_context_menu_gesture_opens_the_menu_at_decoded_coordinates(window) -> None:
    """A v4 icon packs the anchor point in wParam and the notification in
    lParam's low word — trivial to get backwards. The context gesture must
    reach _show_menu with the decoded screen coordinates."""
    opened: list[tuple[int, int]] = []
    icon = TrayIcon(
        window,
        menu_provider=lambda: (TrayMenuItem(1, "Тест"),),
        on_command=lambda command: None,
    )
    icon._show_menu = lambda x, y: opened.append((x, y))  # seam: no real popup
    wparam = (37 & 0xFFFF) | ((19 & 0xFFFF) << 16)
    _user32.SendMessageW(window.hwnd, TRAY_CALLBACK_MSG, wparam, WM_CONTEXTMENU)
    assert opened == [(37, 19)]


def test_a_menu_choice_is_dispatched_to_on_command(window, monkeypatch) -> None:
    """TrackPopupMenuEx with TPM_RETURNCMD returns the chosen command id; the
    icon must forward it to on_command. The modal call is stubbed (it would
    block on real UI); the build, the foreground handshake and the dispatch
    around it are the code under test."""
    chosen: list[int] = []
    icon = TrayIcon(
        window,
        menu_provider=lambda: (TrayMenuItem(109, "Выход"),),
        on_command=chosen.append,
    )
    monkeypatch.setattr("billytalk.core.tray._user32.TrackPopupMenuEx", lambda *a: 109)
    icon._show_menu(12, 34)
    assert chosen == [109]


def test_a_dismissed_menu_dispatches_nothing(window, monkeypatch) -> None:
    """TrackPopupMenuEx returns 0 when the user clicks away — no command fires."""
    chosen: list[int] = []
    icon = TrayIcon(
        window,
        menu_provider=lambda: (TrayMenuItem(109, "Выход"),),
        on_command=chosen.append,
    )
    monkeypatch.setattr("billytalk.core.tray._user32.TrackPopupMenuEx", lambda *a: 0)
    icon._show_menu(12, 34)
    assert chosen == []


# --------------------------------------------------------------------------- #
# the offline tooltip carries N (spec §3) and windows never share a WNDPROC
# --------------------------------------------------------------------------- #

def test_offline_tooltip_carries_the_waiting_count() -> None:
    """Spec §3: the offline tooltip is «N записей ждут связи», not a bare 'no
    connection'. Other states keep their fixed text, no phantom number."""
    tip = tray_tooltip_for(TrayState.OFFLINE, waiting=3)
    assert "3" in tip and "связи" in tip
    assert tray_tooltip_for(TrayState.IDLE, waiting=3) == _TOOLTIPS[TrayState.IDLE]


def test_a_second_window_on_a_shared_class_keeps_its_own_wndproc() -> None:
    """ERROR_CLASS_ALREADY_EXISTS (cycle-2 review tail): a naive second window
    binds to the first's WNDPROC and its own on() handlers never fire. Each
    window must dispatch to its own _on_message."""
    suffix = f"-share{uuid4().hex[:8]}"
    first = HiddenWindow(class_suffix=suffix)
    second = HiddenWindow(class_suffix=suffix)  # identical base class name
    first.start()
    second.start()
    try:
        assert first.wait_ready(5.0) and first.hwnd
        assert second.wait_ready(5.0) and second.hwnd
        assert first.hwnd != second.hwnd
        first.on(WM_TEST, lambda wparam, lparam: 11)
        second.on(WM_TEST, lambda wparam, lparam: 22)
        assert _user32.SendMessageW(first.hwnd, WM_TEST, 0, 0) == 11
        assert _user32.SendMessageW(second.hwnd, WM_TEST, 0, 0) == 22
    finally:
        first.stop()
        second.stop()
