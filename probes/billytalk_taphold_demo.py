#!/usr/bin/env python3
"""
BillyTalk reference implementation: tap-vs-hold on Mouse 4 with pass-through.

Demonstrates the recommended architecture:
  * XBUTTON1 DOWN is ALWAYS suppressed (hook returns 1) -- so the focused app
    never sees it immediately.
  * If released before HOLD_THRESHOLD_MS -> it was a TAP. We replay a synthetic
    XBUTTON1 down+up via SendInput so the browser still navigates Back.
  * If still held at HOLD_THRESHOLD_MS   -> it was a HOLD. Start recording.
    On release, stop recording and replay nothing.

Cost of this design: a genuine "Back" click is delayed by HOLD_THRESHOLD_MS.
That is the unavoidable tradeoff -- you cannot know it was a tap until the
release arrives.

Loop-guard: our own synthetic events carry a magic dwExtraInfo so the hook
ignores them (otherwise infinite recursion).

Run: python billytalk_taphold_demo.py     (ESC three times to quit)
"""

import ctypes
import ctypes.wintypes as wt
import sys
import threading
import time

if sys.platform != "win32":
    raise SystemExit("Windows only.")
try:
    sys.stdout.reconfigure(line_buffering=True)
except Exception:
    pass

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

LRESULT = ctypes.c_ssize_t
ULONG_PTR = ctypes.c_size_t

WH_MOUSE_LL = 14
WH_KEYBOARD_LL = 13
HC_ACTION = 0
WM_XBUTTONDOWN, WM_XBUTTONUP = 0x020B, 0x020C
WM_KEYDOWN = 0x0100
XBUTTON1 = 0x0001

INPUT_MOUSE = 0
MOUSEEVENTF_XDOWN, MOUSEEVENTF_XUP = 0x0080, 0x0100

# --- tunables -------------------------------------------------------------
HOLD_THRESHOLD_MS = 200      # >= this = dictation; < this = pass-through tap
MAGIC = 0xB1117A1C           # marks our own synthetic input
# --------------------------------------------------------------------------


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("pt", wt.POINT), ("mouseData", wt.DWORD), ("flags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ULONG_PTR)]


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wt.DWORD), ("scanCode", wt.DWORD), ("flags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ULONG_PTR)]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wt.LONG), ("dy", wt.LONG), ("mouseData", wt.DWORD),
                ("dwFlags", wt.DWORD), ("time", wt.DWORD), ("dwExtraInfo", ULONG_PTR)]


class INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("mi", MOUSEINPUT)]
    _anonymous_ = ("u",)
    _fields_ = [("type", wt.DWORD), ("u", _U)]


HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wt.WPARAM, wt.LPARAM)

kernel32.GetModuleHandleW.restype = wt.HMODULE
kernel32.GetModuleHandleW.argtypes = [wt.LPCWSTR]
user32.SetWindowsHookExW.restype = wt.HHOOK
user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wt.HINSTANCE, wt.DWORD]
user32.CallNextHookEx.restype = LRESULT
user32.CallNextHookEx.argtypes = [wt.HHOOK, ctypes.c_int, wt.WPARAM, wt.LPARAM]
user32.SendInput.argtypes = [wt.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
user32.SendInput.restype = wt.UINT
user32.UnhookWindowsHookEx.argtypes = [wt.HHOOK]

_state = {"down_at": None, "recording": False, "timer": None}
_lock = threading.Lock()
_esc = 0


def log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}")


def replay_tap():
    """Send a synthetic XBUTTON1 click so the focused app gets its 'Back'."""
    arr = (INPUT * 2)()
    for i, fl in enumerate((MOUSEEVENTF_XDOWN, MOUSEEVENTF_XUP)):
        arr[i].type = INPUT_MOUSE
        arr[i].mi = MOUSEINPUT(0, 0, XBUTTON1, fl, 0, MAGIC)
    user32.SendInput(2, arr, ctypes.sizeof(INPUT))
    log("  -> TAP: replayed synthetic XBUTTON1 click (browser goes Back)")


def start_recording():
    with _lock:
        if _state["down_at"] is None or _state["recording"]:
            return
        _state["recording"] = True
    log("  -> HOLD: START RECORDING (mic would open here)")


def stop_recording():
    log("  -> RELEASE: STOP RECORDING (transcribe + paste here)")


def on_xbutton1_down():
    with _lock:
        _state["down_at"] = time.perf_counter()
        _state["recording"] = False
        t = threading.Timer(HOLD_THRESHOLD_MS / 1000.0, start_recording)
        _state["timer"] = t
    t.daemon = True
    t.start()
    log(f"XBUTTON1 DOWN (suppressed; deciding for {HOLD_THRESHOLD_MS} ms)")


def on_xbutton1_up():
    with _lock:
        down_at = _state["down_at"]
        rec = _state["recording"]
        t = _state["timer"]
        _state["down_at"] = None
        _state["recording"] = False
        _state["timer"] = None
    if t:
        t.cancel()
    if down_at is None:
        return
    held = (time.perf_counter() - down_at) * 1000
    log(f"XBUTTON1 UP (held {held:.0f} ms)")
    if rec:
        stop_recording()
    else:
        replay_tap()


def mouse_proc(nCode, wParam, lParam):
    if nCode == HC_ACTION and wParam in (WM_XBUTTONDOWN, WM_XBUTTONUP):
        ms = ctypes.cast(lParam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
        # Ignore our own replayed input, else we recurse forever.
        if ms.dwExtraInfo != MAGIC:
            xbtn = (ms.mouseData >> 16) & 0xFFFF   # X button index is the HIGH word
            if xbtn == XBUTTON1:
                if wParam == WM_XBUTTONDOWN:
                    on_xbutton1_down()
                else:
                    on_xbutton1_up()
                return 1     # SUPPRESS: focused app does not see this event
    return user32.CallNextHookEx(None, nCode, wParam, lParam)


def kbd_proc(nCode, wParam, lParam):
    global _esc
    if nCode == HC_ACTION and wParam == WM_KEYDOWN:
        ks = ctypes.cast(lParam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
        if ks.vkCode == 0x1B:
            _esc += 1
            if _esc >= 3:
                user32.PostQuitMessage(0)
    return user32.CallNextHookEx(None, nCode, wParam, lParam)


def main():
    hinst = kernel32.GetModuleHandleW(None)
    mp, kp = HOOKPROC(mouse_proc), HOOKPROC(kbd_proc)
    hm = user32.SetWindowsHookExW(WH_MOUSE_LL, mp, hinst, 0)
    hk = user32.SetWindowsHookExW(WH_KEYBOARD_LL, kp, hinst, 0)
    if not hm or not hk:
        raise ctypes.WinError(ctypes.get_last_error())
    log(f"Ready. Threshold={HOLD_THRESHOLD_MS}ms. "
        f"Tap Mouse4 = pass-through Back; hold Mouse4 = record. ESC x3 to quit.")
    msg = wt.MSG()
    try:
        while True:
            r = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if r in (0, -1):
                break
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
    except KeyboardInterrupt:
        pass
    finally:
        user32.UnhookWindowsHookEx(hm)
        user32.UnhookWindowsHookEx(hk)
        log("Unhooked.")


if __name__ == "__main__":
    main()
