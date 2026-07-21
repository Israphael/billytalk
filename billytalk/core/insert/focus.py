"""Target capture and the one bare focus attempt (spec §8).

The target — window, focused control, process, class — is captured **at press
time**, because by delivery time the user may have clicked elsewhere and the
window we owe the text to is the one they were dictating into. At delivery the
capture is compared, never trusted.

``AttachThreadInput`` is struck from the project (research/08): zero successes
in eight trials, and it couples our message queue to a stranger's — a hung
target would hang us, on a process whose hook has a one-second budget. What
remains is a single bare ``SetForegroundWindow``, measured at 25%, whose
failure is a normal outcome, not an error.

Success is judged by the observable effect — ``GetForegroundWindow`` afterwards
— never by the call's return value (the project's recurring lesson).
"""

from __future__ import annotations

import ctypes as ct
from ctypes import wintypes
from dataclasses import dataclass
from typing import Final

__all__ = [
    "Target",
    "capture_target",
    "current_focus_hwnd",
    "is_still_focused",
    "try_restore_focus",
]

_user32 = ct.WinDLL("user32", use_last_error=True)
_kernel32 = ct.WinDLL("kernel32", use_last_error=True)
_advapi32 = ct.WinDLL("advapi32", use_last_error=True)

_user32.GetForegroundWindow.restype = wintypes.HWND
_user32.SetForegroundWindow.argtypes = (wintypes.HWND,)
_user32.GetWindowThreadProcessId.restype = wintypes.DWORD
_user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ct.POINTER(wintypes.DWORD))
_user32.GetClassNameW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ct.c_int)
_kernel32.OpenProcess.restype = wintypes.HANDLE
_kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
_kernel32.QueryFullProcessImageNameW.argtypes = (
    wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ct.POINTER(wintypes.DWORD),
)
_kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
# LONG_PTR return: the default c_int restype truncates 64-bit values — the trap
# of research/02, declared explicitly everywhere in this project.
_user32.GetWindowLongPtrW.restype = ct.c_ssize_t
_user32.GetWindowLongPtrW.argtypes = (wintypes.HWND, ct.c_int)
_user32.GetGUIThreadInfo.restype = wintypes.BOOL
_advapi32.OpenProcessToken.restype = wintypes.BOOL
_advapi32.OpenProcessToken.argtypes = (
    wintypes.HANDLE, wintypes.DWORD, ct.POINTER(wintypes.HANDLE),
)
_advapi32.GetTokenInformation.restype = wintypes.BOOL

_PROCESS_QUERY_LIMITED_INFORMATION: Final = 0x1000
_TOKEN_QUERY: Final = 0x0008
_TOKEN_ELEVATION: Final = 20  # TokenElevation
_ES_PASSWORD: Final = 0x0020
_GWL_STYLE: Final = -16

_SECURE_WINDOW_CLASSES: Final = frozenset({"Credential Dialog Xaml Host"})


class _GUITHREADINFO(ct.Structure):
    _fields_ = (
        ("cbSize", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("hwndActive", wintypes.HWND),
        ("hwndFocus", wintypes.HWND),
        ("hwndCapture", wintypes.HWND),
        ("hwndMenuOwner", wintypes.HWND),
        ("hwndMoveSize", wintypes.HWND),
        ("hwndCaret", wintypes.HWND),
        ("rcCaret", wintypes.RECT),
    )


@dataclass(frozen=True, slots=True)
class Target:
    """Where the text is owed. ``None`` fields mean the capture partly failed —
    delivery degrades gracefully rather than refusing outright."""

    hwnd: int
    focus_hwnd: int | None
    pid: int
    process_name: str | None
    window_class: str | None
    focus_class: str | None
    secure: bool
    elevated: bool


def current_focus_hwnd() -> int | None:
    """The control focused *right now* in the foreground thread, or ``None``
    when it cannot be read (no foreground window, a console, access denied).

    Spec §8: the press-time capture is **compared** before the paste — the
    window check alone misses a click into another field of the same form,
    where the chord would land in the wrong control and verification would
    read the old one (cycle-2 M1 review, the confirmed high finding).
    """
    foreground = _user32.GetForegroundWindow()
    if not foreground:
        return None
    tid = _user32.GetWindowThreadProcessId(foreground, None)
    info = _GUITHREADINFO()
    info.cbSize = ct.sizeof(_GUITHREADINFO)
    if not _user32.GetGUIThreadInfo(tid, ct.byref(info)):
        return None
    return int(info.hwndFocus or 0) or None


def _class_name(hwnd: int | None) -> str | None:
    if not hwnd:
        return None
    buffer = ct.create_unicode_buffer(256)
    if _user32.GetClassNameW(hwnd, buffer, 256) == 0:
        return None
    return buffer.value


def _process_name(pid: int) -> str | None:
    handle = _kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        size = wintypes.DWORD(1024)
        buffer = ct.create_unicode_buffer(size.value)
        if not _kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ct.byref(size)):
            return None
        return buffer.value.rsplit("\\", 1)[-1].lower()
    finally:
        _kernel32.CloseHandle(handle)


def _is_elevated(pid: int) -> bool:
    """Best effort: is the target running elevated? (spec §8: an elevated window
    cannot receive our paste, so no Groq request should be spent on it.)

    Access denied on the token read is itself the signal — a limited process
    can open its peers' tokens but not an administrator's.
    """
    handle = _kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return True  # cannot even open it: treat as out of reach
    try:
        token = wintypes.HANDLE()
        if not _advapi32.OpenProcessToken(handle, _TOKEN_QUERY, ct.byref(token)):
            return True
        try:
            elevation = wintypes.DWORD()
            size = wintypes.DWORD()
            ok = _advapi32.GetTokenInformation(
                token, _TOKEN_ELEVATION, ct.byref(elevation),
                ct.sizeof(elevation), ct.byref(size),
            )
            return bool(elevation.value) if ok else True
        finally:
            _kernel32.CloseHandle(token)
    finally:
        _kernel32.CloseHandle(handle)


def _is_secure(focus_hwnd: int | None, focus_class: str | None, window_class: str | None) -> bool:
    """Password fields and credential dialogs (spec §8): never paste, loudly.

    Cycle-1 detection is the classic pair — ``ES_PASSWORD`` style on an Edit
    control, and the credential dialog's window class. The UIA ``IsPassword``
    check joins in cycle 2 with the rest of UIA.
    """
    if window_class in _SECURE_WINDOW_CLASSES or focus_class in _SECURE_WINDOW_CLASSES:
        return True
    if focus_hwnd and focus_class in ("Edit", "RichEdit20W", "RichEdit50W"):
        style = _user32.GetWindowLongPtrW(focus_hwnd, _GWL_STYLE)
        return bool(style & _ES_PASSWORD)
    return False


def capture_target() -> Target | None:
    """Snapshot the foreground window at press time. ``None``: no foreground
    window at all (rare; the desktop during a transition)."""
    hwnd = _user32.GetForegroundWindow()
    if not hwnd:
        return None
    pid = wintypes.DWORD()
    tid = _user32.GetWindowThreadProcessId(hwnd, ct.byref(pid))

    info = _GUITHREADINFO()
    info.cbSize = ct.sizeof(_GUITHREADINFO)
    focus_hwnd: int | None = None
    if _user32.GetGUIThreadInfo(tid, ct.byref(info)) and info.hwndFocus:
        focus_hwnd = int(info.hwndFocus)  # the control, for cycle 2's rung "б"

    window_class = _class_name(int(hwnd))
    focus_class = _class_name(focus_hwnd)
    return Target(
        hwnd=int(hwnd),
        focus_hwnd=focus_hwnd,
        pid=int(pid.value),
        process_name=_process_name(int(pid.value)),
        window_class=window_class,
        focus_class=focus_class,
        secure=_is_secure(focus_hwnd, focus_class, window_class),
        elevated=_is_elevated(int(pid.value)),
    )


def is_still_focused(target: Target) -> bool:
    return int(_user32.GetForegroundWindow() or 0) == target.hwnd


def try_restore_focus(target: Target) -> bool:
    """One bare attempt, judged by effect (measured 25% — a failure is normal)."""
    _user32.SetForegroundWindow(target.hwnd)  # return value proves nothing
    return is_still_focused(target)
