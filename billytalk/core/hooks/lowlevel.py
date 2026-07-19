"""The low-level hook thread: ``WH_MOUSE_LL`` + ``WH_KEYBOARD_LL`` (spec §2).

Everything dangerous about this file is known and measured:

* **Only a blocking ``GetMessage`` pump receives hook callbacks.** A
  ``PeekMessage`` spin loop receives zero events while every API reports
  success (research/07, S0b) — the handle is valid, ``GetLastError`` is 0,
  nothing arrives. Exit is ``PostThreadMessageW(tid, WM_QUIT)`` from outside.
* **The callback budget is 1000 ms**, after which Windows silently unhooks us
  forever. Measured p50 is 1.7 µs precisely because the callback does one
  dictionary-free check, one ``EdgeLogic`` call and one queue put — no audio,
  no IPC, no disk, ever (ADR-0004).
* **The ``WINFUNCTYPE`` objects are attributes of the thread object** and the
  thread object lives for the process: if the GC collects the callback thunk,
  the hook dies silently. ``gc.freeze()`` does not save a later-created one.
* **`restype` is set explicitly** on every handle-returning function: ctypes'
  default ``c_int`` truncates 64-bit handles into a baffling WinError 126
  (research/02).

Marks in ``dwExtraInfo`` (research/02: the demo's loop guard is mandatory):

* ``SELF_MARK`` — our own synthetic input (the Ctrl+V of delivery). The hook
  passes it through untouched, or our paste would re-enter our own hook.
* ``ECHO_MARK`` — the watchdog's ±1 px probe. Recorded as liveness proof and
  passed through. A zero-offset echo would be silently dropped by the system
  while ``SendInput`` reports success (research/07, S6) — the offset is ±1 by
  contract.
"""

from __future__ import annotations

import ctypes
import ctypes as ct
import threading
from collections.abc import Callable
from ctypes import wintypes
from dataclasses import dataclass
from typing import Final

from .edges import EdgeLogic, HookSnapshot
from .keycodes import CODE_ESC, hiword, is_mouse_code, xbutton_to_code

__all__ = ["ECHO_MARK", "SELF_MARK", "HookEvent", "HookThread", "send_echo", "send_xbutton"]

_user32 = ct.WinDLL("user32", use_last_error=True)
_kernel32 = ct.WinDLL("kernel32", use_last_error=True)

WH_KEYBOARD_LL: Final = 13
WH_MOUSE_LL: Final = 14

WM_KEYDOWN: Final = 0x0100
WM_KEYUP: Final = 0x0101
WM_SYSKEYDOWN: Final = 0x0104
WM_SYSKEYUP: Final = 0x0105
WM_MOUSEMOVE: Final = 0x0200
WM_XBUTTONDOWN: Final = 0x020B
WM_XBUTTONUP: Final = 0x020C
WM_QUIT: Final = 0x0012
WM_APP_REINSTALL: Final = 0x8000 + 0x0042  # WM_APP + 0x42, our reinstall request

VK_XBUTTON1: Final = 0x05
VK_XBUTTON2: Final = 0x06

SELF_MARK: Final = 0x42544B53  # "BTKS": BillyTalk synthetic (paste, test input)
ECHO_MARK: Final = 0x42544B45  # "BTKE": BillyTalk echo (watchdog probe)

_LRESULT = ct.c_ssize_t
_ULONG_PTR = ct.c_size_t
_HOOKPROC = ct.WINFUNCTYPE(_LRESULT, ct.c_int, wintypes.WPARAM, wintypes.LPARAM)

_kernel32.GetModuleHandleW.restype = wintypes.HMODULE  # the 64-bit truncation trap
_kernel32.GetModuleHandleW.argtypes = (wintypes.LPCWSTR,)
_kernel32.GetCurrentThreadId.restype = wintypes.DWORD
_kernel32.GetTickCount.restype = wintypes.DWORD
_user32.SetWindowsHookExW.restype = wintypes.HHOOK
_user32.SetWindowsHookExW.argtypes = (ct.c_int, _HOOKPROC, wintypes.HMODULE, wintypes.DWORD)
_user32.UnhookWindowsHookEx.restype = wintypes.BOOL
_user32.UnhookWindowsHookEx.argtypes = (wintypes.HHOOK,)
_user32.CallNextHookEx.restype = _LRESULT
_user32.CallNextHookEx.argtypes = (wintypes.HHOOK, ct.c_int, wintypes.WPARAM, wintypes.LPARAM)
_user32.GetMessageW.argtypes = (ct.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT)
_user32.PostThreadMessageW.restype = wintypes.BOOL
_user32.PostThreadMessageW.argtypes = (wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
_user32.GetAsyncKeyState.restype = ct.c_short
_user32.GetAsyncKeyState.argtypes = (ct.c_int,)


class _MSLLHOOKSTRUCT(ct.Structure):
    _fields_ = (
        ("pt", wintypes.POINT),
        ("mouseData", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", _ULONG_PTR),
    )


class _KBDLLHOOKSTRUCT(ct.Structure):
    _fields_ = (
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", _ULONG_PTR),
    )


class _MOUSEINPUT(ct.Structure):
    _fields_ = (
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", _ULONG_PTR),
    )


class _INPUT(ct.Structure):
    class _U(ct.Union):
        _fields_ = (("mi", _MOUSEINPUT),)

    _anonymous_ = ("u",)
    _fields_ = (("type", wintypes.DWORD), ("u", _U))


_INPUT_MOUSE: Final = 0
_MOUSEEVENTF_MOVE: Final = 0x0001
_MOUSEEVENTF_XDOWN: Final = 0x0080
_MOUSEEVENTF_XUP: Final = 0x0100


@dataclass(frozen=True, slots=True)
class HookEvent:
    """One raw edge, delivered on the hook thread. The consumer only enqueues."""

    kind: str  # "press" | "release" | "esc" | "double_esc"
    code: int
    tick_ms: int


def _vk_for_code(code: int) -> int:
    """Unified code → the VK ``GetAsyncKeyState`` understands."""
    if not is_mouse_code(code):
        return code
    return {4099: VK_XBUTTON1, 4100: VK_XBUTTON2}.get(code, 0)


def send_echo() -> None:
    """The watchdog probe: ±1 px, marked, back again (research/07 S6).

    Zero offset is silently discarded by the system while ``SendInput`` reports
    success — the ±1 is what makes the probe observable at all.
    """
    moves = (_INPUT * 2)()
    for i, dx in enumerate((1, -1)):
        moves[i].type = _INPUT_MOUSE
        moves[i].mi = _MOUSEINPUT(dx, 0, 0, _MOUSEEVENTF_MOVE, 0, ECHO_MARK)
    _user32.SendInput(2, moves, ct.sizeof(_INPUT))


def send_xbutton(number: int, *, pressed: bool, marked: bool = False) -> None:
    """Synthesise an X-button edge. Test and E2E helper.

    ``marked=False`` lets our own hook see it as a real button — that is the
    point of the end-to-end smoke; ``marked=True`` makes the hook ignore it.
    """
    xbutton = 1 if number == 4 else 2
    flag = _MOUSEEVENTF_XDOWN if pressed else _MOUSEEVENTF_XUP
    extra = SELF_MARK if marked else 0
    event = (_INPUT * 1)()
    event[0].type = _INPUT_MOUSE
    # MOUSEINPUT.mouseData takes the XBUTTON value UNSHIFTED (1 or 2); the
    # high-word placement is the *hook struct's* format, not SendInput's.
    # Measured the hard way: with the value shifted, SendInput returns 1 and
    # no event exists anywhere — the project's recurring lesson that a success
    # code proves nothing.
    event[0].mi = _MOUSEINPUT(0, 0, xbutton, flag, 0, extra)
    _user32.SendInput(1, event, ct.sizeof(_INPUT))


class HookThread(threading.Thread):
    """Owns both hooks and their message pump. One per process.

    The consumer contract: ``on_event`` runs on this thread and must only
    enqueue. The snapshot is replaced wholesale via :meth:`set_snapshot` —
    attribute rebinding is atomic in CPython, no lock on the hot path.
    """

    def __init__(
        self,
        on_event: Callable[[HookEvent], None],
        snapshot: HookSnapshot,
    ) -> None:
        super().__init__(name="billytalk-hooks", daemon=True)
        self._on_event = on_event
        self._snapshot = snapshot
        self._edges: EdgeLogic | None = None
        self._tid: int | None = None
        self._ready = threading.Event()
        self.echo_seen = threading.Event()
        self.last_event_tick: int = 0
        self._mouse_hook: int | None = None
        self._kbd_hook: int | None = None
        # Bound C thunks. Attributes of a process-lifetime object on purpose:
        # if these are collected, the hook dies with no error anywhere.
        self._mouse_proc = _HOOKPROC(self._on_mouse)
        self._kbd_proc = _HOOKPROC(self._on_key)
        self.install_failed: str | None = None

    # ------------------------------------------------------------------ #
    # control surface (any thread)
    # ------------------------------------------------------------------ #

    def set_snapshot(self, snapshot: HookSnapshot) -> None:
        self._snapshot = snapshot

    @property
    def snapshot(self) -> HookSnapshot:
        return self._snapshot

    def wait_ready(self, timeout_s: float) -> bool:
        return self._ready.wait(timeout_s)

    def request_reinstall(self) -> None:
        """Reinstallation happens on the owner thread, via its own pump (spec §2)."""
        if self._tid is not None:
            _user32.PostThreadMessageW(self._tid, WM_APP_REINSTALL, 0, 0)

    def stop(self, timeout_s: float = 2.0) -> None:
        if self._tid is not None:
            _user32.PostThreadMessageW(self._tid, WM_QUIT, 0, 0)
        self.join(timeout_s)

    def note_probe_sent(self) -> None:
        """After an echo, the divergence base resets: the echo itself updated
        ``GetLastInputInfo`` and would mask a dead hook on the next read
        (spec §2 — the divergence is never re-read after an echo)."""
        self.last_event_tick = int(_kernel32.GetTickCount())

    # ------------------------------------------------------------------ #
    # the thread
    # ------------------------------------------------------------------ #

    def run(self) -> None:
        # Keys already held at install are foreign (spec §2): their release
        # must neither be suppressed nor become an event.
        already_down = frozenset(
            code
            for code in self._snapshot.bound | {CODE_ESC}
            if _vk_for_code(code) and (_user32.GetAsyncKeyState(_vk_for_code(code)) & 0x8000)
        )
        self._edges = EdgeLogic(already_down=already_down)
        self._tid = int(_kernel32.GetCurrentThreadId())
        # Baseline for the watchdog: initialised to now, not 0 — against a
        # zero base the very first divergence read spans the whole uptime and
        # fires a pointless echo before any input has happened.
        self.last_event_tick = int(_kernel32.GetTickCount())

        if not self._install():
            self.install_failed = f"SetWindowsHookExW failed, error {ct.get_last_error()}"
            self._ready.set()
            return
        self._ready.set()

        msg = wintypes.MSG()
        while _user32.GetMessageW(ct.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_APP_REINSTALL:
                # Same thread that owns the hooks; the edge state survives in
                # self._edges so a press/release pair spans the reinstall.
                self._uninstall()
                if not self._install():
                    self.install_failed = (
                        f"reinstall failed, error {ct.get_last_error()}"
                    )
                    break
        self._uninstall()

    def _install(self) -> bool:
        module = _kernel32.GetModuleHandleW(None)
        self._mouse_hook = _user32.SetWindowsHookExW(WH_MOUSE_LL, self._mouse_proc, module, 0)
        self._kbd_hook = _user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._kbd_proc, module, 0)
        if not self._mouse_hook or not self._kbd_hook:
            self._uninstall()
            return False
        return True

    def _uninstall(self) -> None:
        for attr in ("_mouse_hook", "_kbd_hook"):
            handle = getattr(self, attr)
            if handle:
                _user32.UnhookWindowsHookEx(handle)
                setattr(self, attr, None)

    # ------------------------------------------------------------------ #
    # callbacks — the 1000 ms budget lives here; measured p50 is 1.7 µs
    # ------------------------------------------------------------------ #

    def _on_mouse(self, n_code: int, w_param: int, l_param: int) -> int:
        if n_code < 0:
            return _user32.CallNextHookEx(None, n_code, w_param, l_param)
        data = ct.cast(l_param, ct.POINTER(_MSLLHOOKSTRUCT)).contents
        self.last_event_tick = data.time
        extra = int(data.dwExtraInfo)
        if extra == ECHO_MARK:
            self.echo_seen.set()
            return _user32.CallNextHookEx(None, n_code, w_param, l_param)
        if extra == SELF_MARK:
            return _user32.CallNextHookEx(None, n_code, w_param, l_param)
        if w_param in (WM_XBUTTONDOWN, WM_XBUTTONUP):
            xbutton = hiword(data.mouseData)
            if xbutton in (1, 2):
                code = xbutton_to_code(xbutton)
                snapshot = self._snapshot
                # Bound OR tracked: a release whose code was unbound mid-hold
                # must still reach EdgeLogic, or the pairing rule dies at this
                # gate and the app gets an orphaned WM_XBUTTONUP — browser Back.
                if code in snapshot.bound or (self._edges is not None and self._edges.tracks(code)):
                    return self._decide(
                        code, pressed=(w_param == WM_XBUTTONDOWN), tick=data.time,
                        snapshot=snapshot,
                        n_code=n_code, w_param=w_param, l_param=l_param,
                    )
        return _user32.CallNextHookEx(None, n_code, w_param, l_param)

    def _on_key(self, n_code: int, w_param: int, l_param: int) -> int:
        if n_code < 0:
            return _user32.CallNextHookEx(None, n_code, w_param, l_param)
        data = ct.cast(l_param, ct.POINTER(_KBDLLHOOKSTRUCT)).contents
        self.last_event_tick = data.time
        if int(data.dwExtraInfo) == SELF_MARK:
            return _user32.CallNextHookEx(None, n_code, w_param, l_param)
        vk = int(data.vkCode)
        snapshot = self._snapshot
        if vk == CODE_ESC or vk in snapshot.bound or (
            self._edges is not None and self._edges.tracks(vk)
        ):
            if w_param in (WM_KEYDOWN, WM_SYSKEYDOWN):
                pressed = True
            elif w_param in (WM_KEYUP, WM_SYSKEYUP):
                pressed = False
            else:
                return _user32.CallNextHookEx(None, n_code, w_param, l_param)
            return self._decide(
                vk, pressed=pressed, tick=data.time,
                snapshot=snapshot,
                n_code=n_code, w_param=w_param, l_param=l_param,
            )
        return _user32.CallNextHookEx(None, n_code, w_param, l_param)

    def _decide(
        self, code: int, *, pressed: bool, tick: int, snapshot: HookSnapshot,
        n_code: int, w_param: int, l_param: int,
    ) -> int:
        # The snapshot travels from the gate rather than being re-read: one
        # edge, one snapshot — re-reading here could judge the gate and the
        # decision by two different worlds (review round 1).
        assert self._edges is not None
        decision = self._edges.on_edge(
            code, pressed=pressed, now_ms=tick, snapshot=snapshot
        )
        if decision.event is not None:
            self._on_event(HookEvent(kind=decision.event, code=code, tick_ms=tick))
        if decision.suppress:
            return 1  # non-zero: swallowed, CallNextHookEx not called
        return _user32.CallNextHookEx(None, n_code, w_param, l_param)
