"""The tray icon and the core's hidden top-level window (spec §11, harness §2).

The window comes first and is not an implementation detail: the core owns a
**real top-level** hidden window (``hWndParent = NULL``) because a
message-only window never receives ``TaskbarCreated`` — the broadcast that
follows an Explorer crash — and an icon that cannot re-add itself after that
broadcast is an icon that dies with Explorer (spec §11, acceptance:
"убийство проводника → значок вернулся"). The same window is the delivery
point for ``WM_DEVICECHANGE`` and the power/session messages (harness §2);
:class:`HiddenWindow` exposes a handler registry so those consumers plug in
without owning the pump.

Tray rules, verbatim from spec §11 and paid for by other people's bugs:

* ``wx.adv.TaskBarIcon`` is banned — it speaks version 3 and answers
  ``TaskbarCreated`` itself, so a layer above it double-``NIM_ADD``\\ s.
* ``NOTIFYICON_VERSION_4`` via ``NIM_SETVERSION`` after every ``NIM_ADD``.
* Identification is ``hWnd`` + ``uID``; ``NIF_GUID`` binds the identity to
  the executable's path and breaks on the first moved install.
* The tooltip under version 4 requires ``NIF_SHOWTIP`` or it never shows.
* ``RegisterWindowMessageW("TaskbarCreated")`` **before** the window exists,
  and the re-add handler is idempotent: delete (ignoring failure), add,
  set version — in that order, safe to run twice.

Icon states (OPEN-QUESTIONS §23): spec §11 names six and spec §3 demands an
"offline" look; implemented as seven renderings with a priority order in
:func:`tray_state_for`. Icons are drawn at runtime into ARGB bitmaps — GDI
pens write zero alpha, so the pixels are walked once per icon to set opacity
(the classic invisible-icon trap); drawing beats shipping .ico assets that
would need a light and a dark variant anyway.

Menus follow the TrackPopupMenu contract (SetForegroundWindow first,
``WM_NULL`` after — without them the menu refuses to dismiss). The menu
*content* comes from a provider callable: harness §2 wants the interface to
fill the menu over IPC, and until a UI is connected the core serves its own
minimal fallback. TrackPopupMenu blocks the window thread while open — that
is how every tray menu on Windows behaves; nothing dictation-critical runs
on this thread.
"""

from __future__ import annotations

import ctypes as ct
import logging
import threading
from collections.abc import Callable, Sequence
from ctypes import wintypes
from dataclasses import dataclass
from enum import Enum
from typing import Final
from uuid import uuid4

__all__ = [
    "HiddenWindow",
    "TrayEvent",
    "TrayIcon",
    "TrayMenuItem",
    "TrayState",
    "menu_items_from_wire",
    "tray_state_for",
    "tray_tooltip_for",
]

log = logging.getLogger("billytalk.tray")

_user32 = ct.WinDLL("user32", use_last_error=True)
_kernel32 = ct.WinDLL("kernel32", use_last_error=True)
_shell32 = ct.WinDLL("shell32", use_last_error=True)
_gdi32 = ct.WinDLL("gdi32", use_last_error=True)

_LRESULT = ct.c_ssize_t
_WNDPROC = ct.WINFUNCTYPE(_LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

_user32.DefWindowProcW.restype = _LRESULT
_user32.DefWindowProcW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
_user32.CreateWindowExW.restype = wintypes.HWND
_user32.CreateWindowExW.argtypes = (
    wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
    ct.c_int, ct.c_int, ct.c_int, ct.c_int,
    wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID,
)
_user32.RegisterWindowMessageW.restype = wintypes.UINT
_user32.RegisterWindowMessageW.argtypes = (wintypes.LPCWSTR,)
_user32.GetMessageW.argtypes = (ct.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT)
_user32.SendMessageW.restype = _LRESULT
_user32.SendMessageW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
_user32.PostMessageW.restype = wintypes.BOOL
_user32.PostMessageW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
_user32.DestroyWindow.restype = wintypes.BOOL
_user32.DestroyWindow.argtypes = (wintypes.HWND,)
_user32.CreatePopupMenu.restype = wintypes.HMENU
_user32.AppendMenuW.restype = wintypes.BOOL
_user32.AppendMenuW.argtypes = (wintypes.HMENU, wintypes.UINT, ct.c_size_t, wintypes.LPCWSTR)
# No A/W split: the export is TrackPopupMenuEx. With TPM_RETURNCMD the
# "BOOL" return carries the chosen command id.
_user32.TrackPopupMenuEx.restype = ct.c_int
_user32.TrackPopupMenuEx.argtypes = (
    wintypes.HMENU, wintypes.UINT, ct.c_int, ct.c_int, wintypes.HWND, wintypes.LPVOID,
)
_user32.DestroyMenu.restype = wintypes.BOOL
_user32.DestroyMenu.argtypes = (wintypes.HMENU,)
_user32.GetMenuItemCount.restype = ct.c_int
_user32.GetMenuItemCount.argtypes = (wintypes.HMENU,)
_user32.SetForegroundWindow.restype = wintypes.BOOL
_user32.SetForegroundWindow.argtypes = (wintypes.HWND,)
_user32.DestroyIcon.restype = wintypes.BOOL
_user32.DestroyIcon.argtypes = (wintypes.HICON,)
_user32.CreateIconIndirect.restype = wintypes.HICON

_kernel32.GetModuleHandleW.restype = wintypes.HMODULE
_kernel32.GetModuleHandleW.argtypes = (wintypes.LPCWSTR,)
_kernel32.GetCurrentThreadId.restype = wintypes.DWORD

_user32.GetDC.restype = wintypes.HDC
_user32.GetDC.argtypes = (wintypes.HWND,)
_user32.ReleaseDC.argtypes = (wintypes.HWND, wintypes.HDC)
_user32.DispatchMessageW.restype = _LRESULT

# 64-bit handles do not survive ctypes' default c_int in either direction:
# restype for what comes back, argtypes for what goes in (research/02).
_gdi32.CreateDIBSection.restype = wintypes.HBITMAP
_gdi32.CreateDIBSection.argtypes = (
    wintypes.HDC, ct.c_void_p, wintypes.UINT,
    ct.POINTER(ct.c_void_p), wintypes.HANDLE, wintypes.DWORD,
)
_gdi32.CreateCompatibleDC.restype = wintypes.HDC
_gdi32.CreateCompatibleDC.argtypes = (wintypes.HDC,)
_gdi32.DeleteDC.argtypes = (wintypes.HDC,)
_gdi32.SelectObject.restype = wintypes.HGDIOBJ
_gdi32.SelectObject.argtypes = (wintypes.HDC, wintypes.HGDIOBJ)
_gdi32.DeleteObject.restype = wintypes.BOOL
_gdi32.DeleteObject.argtypes = (wintypes.HGDIOBJ,)
_gdi32.CreatePen.restype = wintypes.HGDIOBJ
_gdi32.CreatePen.argtypes = (ct.c_int, ct.c_int, wintypes.COLORREF)
_gdi32.CreateSolidBrush.restype = wintypes.HGDIOBJ
_gdi32.CreateSolidBrush.argtypes = (wintypes.COLORREF,)
_gdi32.CreateBitmap.restype = wintypes.HBITMAP
_gdi32.CreateBitmap.argtypes = (
    ct.c_int, ct.c_int, wintypes.UINT, wintypes.UINT, ct.c_void_p,
)
_gdi32.GetStockObject.restype = wintypes.HGDIOBJ
_gdi32.GetStockObject.argtypes = (ct.c_int,)
_gdi32.RoundRect.argtypes = (wintypes.HDC,) + (ct.c_int,) * 6
_gdi32.Arc.argtypes = (wintypes.HDC,) + (ct.c_int,) * 8
_gdi32.Ellipse.argtypes = (wintypes.HDC,) + (ct.c_int,) * 4
_gdi32.MoveToEx.argtypes = (wintypes.HDC, ct.c_int, ct.c_int, ct.c_void_p)
_gdi32.LineTo.argtypes = (wintypes.HDC, ct.c_int, ct.c_int)

WM_DESTROY: Final = 0x0002
WM_CLOSE: Final = 0x0010
WM_COMMAND: Final = 0x0111
WM_CONTEXTMENU: Final = 0x007B
WM_NULL: Final = 0x0000
WM_APP: Final = 0x8000

TRAY_CALLBACK_MSG: Final = WM_APP + 0x54  # 'T'

NIN_SELECT: Final = 0x0400
NIN_KEYSELECT: Final = 0x0401

NIM_ADD: Final = 0
NIM_MODIFY: Final = 1
NIM_DELETE: Final = 2
NIM_SETVERSION: Final = 4
NOTIFYICON_VERSION_4: Final = 4

NIF_MESSAGE: Final = 0x01
NIF_ICON: Final = 0x02
NIF_TIP: Final = 0x04
NIF_SHOWTIP: Final = 0x80

MF_STRING: Final = 0x0000
MF_SEPARATOR: Final = 0x0800
MF_CHECKED: Final = 0x0008
MF_GRAYED: Final = 0x0001
MF_DEFAULT: Final = 0x1000
TPM_RETURNCMD: Final = 0x0100
TPM_RIGHTBUTTON: Final = 0x0002

_ICON_SIZE: Final = 32
_TRAY_UID: Final = 1


class _GUID(ct.Structure):
    _fields_ = (("bytes", ct.c_byte * 16),)


class _NOTIFYICONDATAW(ct.Structure):
    class _U(ct.Union):
        _fields_ = (("uTimeout", wintypes.UINT), ("uVersion", wintypes.UINT))

    _anonymous_ = ("u",)
    _fields_ = (
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT),
        ("uCallbackMessage", wintypes.UINT),
        ("hIcon", wintypes.HICON),
        ("szTip", wintypes.WCHAR * 128),
        ("dwState", wintypes.DWORD),
        ("dwStateMask", wintypes.DWORD),
        ("szInfo", wintypes.WCHAR * 256),
        ("u", _U),
        ("szInfoTitle", wintypes.WCHAR * 64),
        ("dwInfoFlags", wintypes.DWORD),
        ("guidItem", _GUID),
        ("hBalloonIcon", wintypes.HICON),
    )


class _ICONINFO(ct.Structure):
    _fields_ = (
        ("fIcon", wintypes.BOOL),
        ("xHotspot", wintypes.DWORD),
        ("yHotspot", wintypes.DWORD),
        ("hbmMask", wintypes.HBITMAP),
        ("hbmColor", wintypes.HBITMAP),
    )


_user32.CreateIconIndirect.argtypes = (ct.POINTER(_ICONINFO),)


class _BITMAPINFOHEADER(ct.Structure):
    _fields_ = (
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    )


class _WNDCLASSW(ct.Structure):
    _fields_ = (
        ("style", wintypes.UINT),
        ("lpfnWndProc", _WNDPROC),
        ("cbClsExtra", ct.c_int),
        ("cbWndExtra", ct.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    )


# --------------------------------------------------------------------------- #
# the hidden top-level window
# --------------------------------------------------------------------------- #

class HiddenWindow(threading.Thread):
    """A real top-level window, never shown, plus its message pump.

    Handlers registered with :meth:`on` run **on this thread** and follow the
    house contract: enqueue and return. Anything slow here delays
    ``TaskbarCreated`` recovery and the device-change stream — never the
    dictation path, which lives on other threads entirely.
    """

    def __init__(self, *, class_suffix: str = "") -> None:
        super().__init__(name="billytalk-window", daemon=True)
        self._class_name = f"BillyTalkCore{class_suffix}"
        self._handlers: dict[int, Callable[[int, int], int | None]] = {}
        self._ready = threading.Event()
        self.hwnd: int | None = None
        self._tid: int | None = None
        # Spec §11, verbatim: RegisterWindowMessageW **before** the window is
        # created — the atom exists before the first message could arrive.
        self.taskbar_created_msg: int = _user32.RegisterWindowMessageW("TaskbarCreated")
        # Process-lifetime reference, same reason as the hook thunks: a
        # collected WNDPROC is a crash on the next message.
        self._wndproc = _WNDPROC(self._on_message)

    def on(self, message: int, handler: Callable[[int, int], int | None]) -> None:
        """Register ``handler(wparam, lparam)`` for ``message``. Returning
        ``None`` falls through to ``DefWindowProc``."""
        self._handlers[message] = handler

    def wait_ready(self, timeout_s: float) -> bool:
        return self._ready.wait(timeout_s)

    def stop(self, timeout_s: float = 3.0) -> None:
        if self.hwnd is not None:
            _user32.PostMessageW(self.hwnd, WM_CLOSE, 0, 0)
        self.join(timeout_s)

    # -- the thread ----------------------------------------------------- #

    def _register_class(self, instance: int) -> bool:
        """Bind our class name to *our* WNDPROC. On ``ERROR_CLASS_ALREADY_EXISTS``
        the name belongs to a class someone else registered — an earlier window
        whose class outlived it (``DestroyWindow`` does not unregister), or a
        second instance — and creating a window on it dispatches every message
        to that *foreign* WNDPROC, so ``on()`` handlers never fire (cycle-2
        review tail). Fall back to a name only we hold. Returns ``False`` only on
        an unrecoverable registration error.
        """
        for _ in range(2):
            wndclass = _WNDCLASSW(
                0, self._wndproc, 0, 0, instance, None, None, None, None, self._class_name
            )
            if _user32.RegisterClassW(ct.byref(wndclass)):
                return True
            error = ct.get_last_error()
            if error != 1410:  # not ERROR_CLASS_ALREADY_EXISTS — nothing to retry
                log.error("RegisterClassW failed: %d", error)
                return False
            # The name is taken by a class whose WNDPROC is not ours. Take a
            # private one so this window can only reach our _on_message.
            self._class_name = f"{self._class_name}-{uuid4().hex[:8]}"
        log.error("could not register a private window class")
        return False

    def run(self) -> None:
        instance = _kernel32.GetModuleHandleW(None)
        if not self._register_class(instance):
            self._ready.set()
            return
        # ⚠ hWndParent = NULL — a *real* top-level window. HWND_MESSAGE would
        # be tidier and would never hear TaskbarCreated (spec §11).
        self.hwnd = _user32.CreateWindowExW(
            0, self._class_name, "BillyTalk", 0,
            0, 0, 0, 0, None, None, instance, None,
        )
        self._tid = int(_kernel32.GetCurrentThreadId())
        self._ready.set()
        if not self.hwnd:
            log.error("CreateWindowExW failed: %d", ct.get_last_error())
            return
        msg = wintypes.MSG()
        while _user32.GetMessageW(ct.byref(msg), None, 0, 0) > 0:
            _user32.TranslateMessage(ct.byref(msg))
            _user32.DispatchMessageW(ct.byref(msg))
        self.hwnd = None

    def _on_message(self, hwnd: int, message: int, wparam: int, lparam: int) -> int:
        if message == WM_CLOSE:
            _user32.DestroyWindow(hwnd)
            return 0
        if message == WM_DESTROY:
            _user32.PostQuitMessage(0)
            return 0
        handler = self._handlers.get(message)
        if handler is not None:
            try:
                result = handler(wparam, lparam)
            except Exception:
                # A handler must never take the pump down: the pump is what
                # keeps the icon recoverable and the device stream alive.
                log.exception("window handler failed for message 0x%X", message)
                result = None
            if result is not None:
                return result
        return _user32.DefWindowProcW(hwnd, message, wparam, lparam)


# --------------------------------------------------------------------------- #
# icon states and their runtime-drawn icons
# --------------------------------------------------------------------------- #

class TrayState(Enum):
    IDLE = "idle"
    RECORDING = "recording"
    TRANSCRIBING = "transcribing"
    QUEUE = "queue"
    OFFLINE = "offline"
    STOPPED = "stopped"
    ERROR = "error"


def tray_state_for(
    *, phase_name: str, enabled: bool, offline: bool, queue_len: int
) -> TrayState:
    """Pure priority fold of everything the icon can say into one state.

    Disabled wins over everything — a stopped product must look stopped even
    offline; an in-flight dictation beats the offline hint because the user
    is *doing* something; offline beats idle because §3 demands the icon say
    it before the user finds out by silence.
    """
    if not enabled:
        return TrayState.STOPPED
    if phase_name == "Failed":
        return TrayState.ERROR
    if phase_name in ("Initialized", "Recording"):
        return TrayState.RECORDING
    if phase_name in ("Finalizing", "Delivering"):
        return TrayState.TRANSCRIBING
    if queue_len > 0:
        return TrayState.QUEUE
    if offline:
        return TrayState.OFFLINE
    return TrayState.IDLE


_TOOLTIPS: Final = {
    TrayState.IDLE: "BillyTalk — готов",
    TrayState.RECORDING: "BillyTalk — запись",
    TrayState.TRANSCRIBING: "BillyTalk — расшифровка",
    TrayState.QUEUE: "BillyTalk — записи в очереди",
    TrayState.OFFLINE: "BillyTalk — нет связи, записи ждут",
    TrayState.STOPPED: "BillyTalk — диктовка выключена",
    TrayState.ERROR: "BillyTalk — ошибка",
}

def tray_tooltip_for(state: TrayState, *, waiting: int = 0) -> str:
    """The icon's hover text. ``OFFLINE`` carries the count spec §3 makes
    mandatory — «N записей ждут связи» — so the user learns how many dictations
    are held for the network from the icon alone, not minutes later by silence.
    Every other state's text is fixed, so ``waiting`` is ignored there.
    """
    if state is TrayState.OFFLINE:
        return f"BillyTalk — нет связи, {waiting} записей ждут"
    return _TOOLTIPS[state]


_DOT_COLORS: Final = {
    TrayState.RECORDING: 0xE81123,  # RGB
    TrayState.TRANSCRIBING: 0x0078D7,
    TrayState.QUEUE: 0xD9A521,
    TrayState.OFFLINE: 0x767676,
}


def _system_uses_light_taskbar() -> bool:
    """Light taskbar wants dark glyphs; dark (the default) wants light ones."""
    import winreg

    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        ) as key:
            value, _ = winreg.QueryValueEx(key, "SystemUsesLightTheme")
            return bool(value)
    except OSError:
        return False


def _draw_state_icon(state: TrayState, *, light_taskbar: bool) -> int:
    """One 32×32 ARGB icon: a microphone glyph plus the state's mark.

    GDI pens write BGR and leave alpha at zero — drawn naively the icon is
    100% transparent. The bitmap bits live in our address space (DIB
    section), so after drawing, every non-background pixel gets its alpha
    byte set by hand. 1024 pixels, once per state, cached by the caller.
    """
    size = _ICON_SIZE
    header = _BITMAPINFOHEADER(
        ct.sizeof(_BITMAPINFOHEADER), size, -size, 1, 32, 0, 0, 0, 0, 0, 0
    )
    bits = ct.c_void_p()
    screen_dc = _user32.GetDC(None)
    dc = _gdi32.CreateCompatibleDC(screen_dc)
    _user32.ReleaseDC(None, screen_dc)
    bitmap = _gdi32.CreateDIBSection(dc, ct.byref(header), 0, ct.byref(bits), None, 0)
    old_bitmap = _gdi32.SelectObject(dc, bitmap)

    glyph_bgr = 0x00202020 if light_taskbar else 0x00F5F5F5
    dimmed = state in (TrayState.STOPPED, TrayState.OFFLINE)

    pen = _gdi32.CreatePen(0, 3, glyph_bgr)
    old_pen = _gdi32.SelectObject(dc, pen)
    brush = _gdi32.GetStockObject(5)  # NULL_BRUSH: outlines only
    old_brush = _gdi32.SelectObject(dc, brush)

    # The microphone: capsule, cradle arc, stem, base.
    _gdi32.RoundRect(dc, 11, 2, 21, 18, 9, 9)
    _gdi32.Arc(dc, 7, 6, 25, 24, 2, 22, 30, 22)
    _gdi32.MoveToEx(dc, 16, 24, None)
    _gdi32.LineTo(dc, 16, 28)
    _gdi32.MoveToEx(dc, 10, 29, None)
    _gdi32.LineTo(dc, 22, 29)

    if state is TrayState.STOPPED:
        # Pause bars over the lower right.
        bar = _gdi32.CreatePen(0, 3, 0x00767676)
        _gdi32.SelectObject(dc, bar)
        for x in (22, 28):
            _gdi32.MoveToEx(dc, x, 20, None)
            _gdi32.LineTo(dc, x, 31)
        _gdi32.SelectObject(dc, pen)
        _gdi32.DeleteObject(bar)
    elif state is TrayState.ERROR:
        cross = _gdi32.CreatePen(0, 4, 0x001C2BC4)  # BGR of C42B1C
        _gdi32.SelectObject(dc, cross)
        _gdi32.MoveToEx(dc, 20, 19, None)
        _gdi32.LineTo(dc, 31, 30)
        _gdi32.MoveToEx(dc, 31, 19, None)
        _gdi32.LineTo(dc, 20, 30)
        _gdi32.SelectObject(dc, pen)
        _gdi32.DeleteObject(cross)
    elif state in _DOT_COLORS:
        rgb = _DOT_COLORS[state]
        bgr = ((rgb & 0xFF) << 16) | (rgb & 0x00FF00) | (rgb >> 16)
        dot_brush = _gdi32.CreateSolidBrush(bgr)
        dot_pen = _gdi32.CreatePen(0, 1, bgr)
        _gdi32.SelectObject(dc, dot_brush)
        _gdi32.SelectObject(dc, dot_pen)
        hollow = state is TrayState.OFFLINE
        if hollow:
            _gdi32.SelectObject(dc, brush)  # outline only
        _gdi32.Ellipse(dc, 20, 19, 32, 31)
        _gdi32.SelectObject(dc, pen)
        _gdi32.SelectObject(dc, brush)
        _gdi32.DeleteObject(dot_brush)
        _gdi32.DeleteObject(dot_pen)

    _gdi32.SelectObject(dc, old_pen)
    _gdi32.SelectObject(dc, old_brush)
    _gdi32.DeleteObject(pen)

    # Alpha pass: GDI left every alpha byte at 0 — an all-transparent icon.
    pixel_count = size * size
    pixels = ct.cast(bits, ct.POINTER(ct.c_uint32 * pixel_count)).contents
    opacity = 0x60 if dimmed else 0xFF
    for i in range(pixel_count):
        if pixels[i] & 0x00FFFFFF:
            pixels[i] = (opacity << 24) | (pixels[i] & 0x00FFFFFF)

    _gdi32.SelectObject(dc, old_bitmap)
    mask = _gdi32.CreateBitmap(size, size, 1, 1, None)
    info = _ICONINFO(True, 0, 0, mask, bitmap)
    icon = _user32.CreateIconIndirect(ct.byref(info))
    _gdi32.DeleteObject(mask)
    _gdi32.DeleteObject(bitmap)
    _gdi32.DeleteDC(dc)
    return icon or 0


# --------------------------------------------------------------------------- #
# menu model
# --------------------------------------------------------------------------- #

@dataclass(frozen=True, slots=True)
class TrayMenuItem:
    """One entry of the popup; ``command`` 0 with empty label is a separator."""

    command: int = 0
    label: str = ""
    checked: bool = False
    enabled: bool = True
    default: bool = False

    @property
    def is_separator(self) -> bool:
        return self.command == 0 and not self.label


def menu_items_from_wire(items: object) -> tuple[TrayMenuItem, ...]:
    """Plain wire dicts (the menu the UI fills over IPC, OPEN-QUESTIONS §22) into
    :class:`TrayMenuItem`\\ s. The UI never imports this ctypes module (harness
    §1), so the menu crosses the channel as data; anything that is not a dict is
    skipped rather than trusted."""
    result: list[TrayMenuItem] = []
    if not isinstance(items, (list, tuple)):
        return ()
    for item in items:
        if not isinstance(item, dict):
            continue
        result.append(
            TrayMenuItem(
                command=int(item.get("command", 0)),
                label=str(item.get("label", "")),
                checked=bool(item.get("checked", False)),
                enabled=bool(item.get("enabled", True)),
                default=bool(item.get("default", False)),
            )
        )
    return tuple(result)


def _build_menu(items: Sequence[TrayMenuItem]) -> int:
    menu = _user32.CreatePopupMenu()
    for item in items:
        if item.is_separator:
            _user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
            continue
        flags = MF_STRING
        if item.checked:
            flags |= MF_CHECKED
        if not item.enabled:
            flags |= MF_GRAYED
        if item.default:
            flags |= MF_DEFAULT
        _user32.AppendMenuW(menu, flags, item.command, item.label)
    return menu


@dataclass(frozen=True, slots=True)
class TrayEvent:
    """A user gesture on the icon, delivered on the window thread."""

    kind: str  # "select" | "context"
    x: int
    y: int


# --------------------------------------------------------------------------- #
# the icon itself
# --------------------------------------------------------------------------- #

class TrayIcon:
    """One icon, identified by (hwnd, uID), version 4.

    ``menu_provider`` returns the current menu model when the user opens it;
    ``on_command`` receives the chosen command id (enqueue-only, window
    thread); ``on_select`` fires for a plain left-click activation.
    """

    def __init__(
        self,
        window: HiddenWindow,
        *,
        menu_provider: Callable[[], Sequence[TrayMenuItem]],
        on_command: Callable[[int], None],
        on_select: Callable[[], None] | None = None,
    ) -> None:
        if window.hwnd is None:
            raise RuntimeError("HiddenWindow must be running before TrayIcon")
        self._window = window
        self._menu_provider = menu_provider
        self._on_command = on_command
        self._on_select = on_select
        self._state = TrayState.IDLE
        self._tooltip = _TOOLTIPS[TrayState.IDLE]
        self._added = False
        self._light_taskbar = _system_uses_light_taskbar()
        self._icons: dict[TrayState, int] = {}
        # The atom was registered by HiddenWindow.__init__, before the window
        # existed (spec §11's ordering, to the letter).
        self._taskbar_created = window.taskbar_created_msg
        window.on(TRAY_CALLBACK_MSG, self._on_callback)
        window.on(self._taskbar_created, self._on_taskbar_created)

    # -- public (any thread; Shell_NotifyIcon is thread-safe) ----------- #

    def add(self) -> bool:
        self._added = self._notify(NIM_ADD, self._data()) and self._set_version()
        return self._added

    def set_state(self, state: TrayState, *, tooltip: str | None = None) -> bool:
        self._state = state
        self._tooltip = tooltip if tooltip is not None else _TOOLTIPS[state]
        if not self._added:
            return False
        return self._notify(NIM_MODIFY, self._data())

    def remove(self) -> None:
        if self._added:
            self._notify(NIM_DELETE, self._base())
            self._added = False
        for icon in self._icons.values():
            _user32.DestroyIcon(icon)
        self._icons.clear()

    @property
    def state(self) -> TrayState:
        return self._state

    # -- window-thread handlers ----------------------------------------- #

    def _on_taskbar_created(self, wparam: int, lparam: int) -> int | None:
        """Explorer came back. Idempotent by construction: delete (a failure
        means it was already gone), full add, set version."""
        log.info("TaskbarCreated received; re-adding the tray icon")
        self._notify(NIM_DELETE, self._base())
        self._added = self._notify(NIM_ADD, self._data()) and self._set_version()
        return 0

    def _on_callback(self, wparam: int, lparam: int) -> int | None:
        event = lparam & 0xFFFF
        x = ct.c_short(wparam & 0xFFFF).value
        y = ct.c_short((wparam >> 16) & 0xFFFF).value
        if event == WM_CONTEXTMENU:
            self._show_menu(x, y)
        elif event in (NIN_SELECT, NIN_KEYSELECT):
            if self._on_select is not None:
                self._on_select()
        return 0

    def _show_menu(self, x: int, y: int) -> None:
        items = self._menu_provider()
        menu = _build_menu(items)
        try:
            # The TrackPopupMenu contract: foreground first or the menu will
            # not dismiss on an outside click; WM_NULL after, same reason.
            hwnd = self._window.hwnd
            _user32.SetForegroundWindow(hwnd)
            chosen = _user32.TrackPopupMenuEx(
                menu, TPM_RETURNCMD | TPM_RIGHTBUTTON, x, y, hwnd, None
            )
            _user32.PostMessageW(hwnd, WM_NULL, 0, 0)
        finally:
            _user32.DestroyMenu(menu)
        if chosen:
            self._on_command(int(chosen))

    # -- plumbing -------------------------------------------------------- #

    def _icon_for(self, state: TrayState) -> int:
        icon = self._icons.get(state)
        if icon is None:
            icon = _draw_state_icon(state, light_taskbar=self._light_taskbar)
            self._icons[state] = icon
        return icon

    def _base(self) -> _NOTIFYICONDATAW:
        data = _NOTIFYICONDATAW()
        data.cbSize = ct.sizeof(_NOTIFYICONDATAW)
        data.hWnd = self._window.hwnd
        data.uID = _TRAY_UID
        return data

    def _data(self) -> _NOTIFYICONDATAW:
        data = self._base()
        data.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP | NIF_SHOWTIP
        data.uCallbackMessage = TRAY_CALLBACK_MSG
        data.hIcon = self._icon_for(self._state)
        data.szTip = self._tooltip[:127]
        return data

    def _set_version(self) -> bool:
        data = self._base()
        data.uVersion = NOTIFYICON_VERSION_4
        return self._notify(NIM_SETVERSION, data)

    @staticmethod
    def _notify(action: int, data: _NOTIFYICONDATAW) -> bool:
        return bool(_shell32.Shell_NotifyIconW(action, ct.byref(data)))
