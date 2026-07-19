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
    HiddenWindow,
    TrayIcon,
    TrayMenuItem,
    TrayState,
    _build_menu,
    _draw_state_icon,
    tray_state_for,
)

_user32 = ct.WinDLL("user32", use_last_error=True)
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
