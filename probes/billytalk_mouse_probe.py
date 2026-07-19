#!/usr/bin/env python3
"""
BillyTalk hotkey probe -- Windows 11 mouse/keyboard event diagnostic.

Tells you EXACTLY what your Razer (or any) mouse side buttons generate:
  * whether they arrive as real mouse XBUTTON events,
  * whether Synapse/G HUB has silently turned them into keystrokes,
  * whether Raw Input sees them at all,
  * and whether the event was hardware-generated or software-INJECTED
    (injected == a driver/daemon synthesized it -> vendor remapping is active).

No dependencies. Pure ctypes. Run:  python mouse_probe.py
Press Ctrl+C in the console, or press ESC three times, to quit.
"""

import ctypes
import ctypes.wintypes as wt
import sys
import time

if sys.platform != "win32":
    raise SystemExit("Windows only.")

# Unbuffered: we print from inside hook callbacks and want it immediate.
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
WM_QUIT = 0x0012
WM_INPUT = 0x00FF

WM_MOUSEMOVE = 0x0200
WM_XBUTTONDOWN = 0x020B
WM_XBUTTONUP = 0x020C
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105

MOUSE_MSG = {
    0x0201: "WM_LBUTTONDOWN", 0x0202: "WM_LBUTTONUP",
    0x0204: "WM_RBUTTONDOWN", 0x0205: "WM_RBUTTONUP",
    0x0207: "WM_MBUTTONDOWN", 0x0208: "WM_MBUTTONUP",
    WM_XBUTTONDOWN: "WM_XBUTTONDOWN", WM_XBUTTONUP: "WM_XBUTTONUP",
    0x020A: "WM_MOUSEWHEEL", 0x020E: "WM_MOUSEHWHEEL",
}

LLMHF_INJECTED = 0x01
LLMHF_LOWER_IL_INJECTED = 0x02
LLKHF_INJECTED = 0x10
LLKHF_LOWER_IL_INJECTED = 0x02

RIDEV_INPUTSINK = 0x00000100
RID_INPUT = 0x10000003
RIM_TYPEMOUSE = 0
RIM_TYPEHID = 2
HWND_MESSAGE = wt.HWND(-3)

RI_FLAGS = [
    (0x0001, "LEFT_DOWN"), (0x0002, "LEFT_UP"),
    (0x0004, "RIGHT_DOWN"), (0x0008, "RIGHT_UP"),
    (0x0010, "MIDDLE_DOWN"), (0x0020, "MIDDLE_UP"),
    (0x0040, "BUTTON_4_DOWN (XBUTTON1 / 'Mouse 4')"),
    (0x0080, "BUTTON_4_UP   (XBUTTON1 / 'Mouse 4')"),
    (0x0100, "BUTTON_5_DOWN (XBUTTON2 / 'Mouse 5')"),
    (0x0200, "BUTTON_5_UP   (XBUTTON2 / 'Mouse 5')"),
    (0x0400, "WHEEL"), (0x0800, "HWHEEL"),
]


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("pt", wt.POINT), ("mouseData", wt.DWORD), ("flags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ULONG_PTR)]


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wt.DWORD), ("scanCode", wt.DWORD), ("flags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ULONG_PTR)]


class RAWINPUTDEVICE(ctypes.Structure):
    _fields_ = [("usUsagePage", wt.USHORT), ("usUsage", wt.USHORT),
                ("dwFlags", wt.DWORD), ("hwndTarget", wt.HWND)]


class RAWINPUTHEADER(ctypes.Structure):
    _fields_ = [("dwType", wt.DWORD), ("dwSize", wt.DWORD),
                ("hDevice", wt.HANDLE), ("wParam", ctypes.c_size_t)]


class RAWMOUSE(ctypes.Structure):
    _fields_ = [("usFlags", wt.USHORT), ("usButtonFlags", wt.USHORT),
                ("usButtonData", wt.SHORT), ("ulRawButtons", wt.ULONG),
                ("lLastX", wt.LONG), ("lLastY", wt.LONG),
                ("ulExtraInformation", wt.ULONG)]


class RAWHID(ctypes.Structure):
    _fields_ = [("dwSizeHid", wt.DWORD), ("dwCount", wt.DWORD),
                ("bRawData", ctypes.c_ubyte * 1)]


class RAWINPUTDEVICELIST(ctypes.Structure):
    _fields_ = [("hDevice", wt.HANDLE), ("dwType", wt.DWORD)]


HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wt.WPARAM, wt.LPARAM)
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", wt.UINT), ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", wt.HINSTANCE), ("hIcon", wt.HICON),
                ("hCursor", wt.HANDLE), ("hbrBackground", wt.HBRUSH),
                ("lpszMenuName", wt.LPCWSTR), ("lpszClassName", wt.LPCWSTR)]


# NOTE: these restypes are mandatory on 64-bit Python. ctypes defaults to c_int,
# which silently TRUNCATES 64-bit HANDLE/HWND/HMODULE values and produces
# baffling "WinError 126 module not found" / invalid-window failures.
kernel32.GetModuleHandleW.restype = wt.HMODULE
kernel32.GetModuleHandleW.argtypes = [wt.LPCWSTR]
user32.CreateWindowExW.restype = wt.HWND
user32.CreateWindowExW.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                   wt.HWND, wt.HMENU, wt.HINSTANCE, wt.LPVOID]
user32.UnhookWindowsHookEx.argtypes = [wt.HHOOK]
user32.SetWindowsHookExW.restype = wt.HHOOK
user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wt.HINSTANCE, wt.DWORD]
user32.CallNextHookEx.restype = LRESULT
user32.CallNextHookEx.argtypes = [wt.HHOOK, ctypes.c_int, wt.WPARAM, wt.LPARAM]
user32.DefWindowProcW.restype = LRESULT
user32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.GetRawInputData.argtypes = [wt.HANDLE, wt.UINT, wt.LPVOID,
                                   ctypes.POINTER(wt.UINT), wt.UINT]
user32.GetRawInputData.restype = wt.UINT
user32.RegisterRawInputDevices.argtypes = [ctypes.POINTER(RAWINPUTDEVICE), wt.UINT, wt.UINT]
user32.RegisterRawInputDevices.restype = wt.BOOL
user32.GetRawInputDeviceList.argtypes = [ctypes.POINTER(RAWINPUTDEVICELIST),
                                         ctypes.POINTER(wt.UINT), wt.UINT]
user32.GetRawInputDeviceList.restype = wt.UINT
user32.GetRawInputDeviceInfoW.argtypes = [wt.HANDLE, wt.UINT, wt.LPVOID,
                                          ctypes.POINTER(wt.UINT)]
user32.GetRawInputDeviceInfoW.restype = wt.UINT

T0 = time.perf_counter()
_esc_count = 0
_down_times = {}


def ts():
    return f"[{(time.perf_counter() - T0) * 1000:9.1f} ms]"


def inject_note(flags, injected_bit):
    if flags & injected_bit:
        extra = " (from LOWER integrity process)" if flags & LLMHF_LOWER_IL_INJECTED else ""
        return f"  <<< INJECTED{extra} -- synthesized by software/driver, NOT raw hardware"
    return ""


def enumerate_devices():
    count = wt.UINT(0)
    sz = ctypes.sizeof(RAWINPUTDEVICELIST)
    user32.GetRawInputDeviceList(None, ctypes.byref(count), sz)
    if not count.value:
        return
    arr = (RAWINPUTDEVICELIST * count.value)()
    got = user32.GetRawInputDeviceList(arr, ctypes.byref(count), sz)
    if got == wt.UINT(-1).value:
        return
    names = {0: "MOUSE", 1: "KEYBOARD", 2: "HID"}
    print("=" * 78)
    print("INPUT DEVICES PRESENT (look for your Razer VID 1532 / Logitech VID 046D):")
    print("=" * 78)
    for i in range(got):
        d = arr[i]
        need = wt.UINT(0)
        user32.GetRawInputDeviceInfoW(d.hDevice, 0x20000007, None, ctypes.byref(need))
        buf = ctypes.create_unicode_buffer(need.value + 1)
        user32.GetRawInputDeviceInfoW(d.hDevice, 0x20000007, buf, ctypes.byref(need))
        name = buf.value or "<unnamed>"
        tag = names.get(d.dwType, str(d.dwType))
        mark = ""
        up = name.upper()
        if "VID_1532" in up:
            mark = "   <== RAZER"
        elif "VID_046D" in up:
            mark = "   <== LOGITECH"
        print(f"  {tag:9s} {name}{mark}")
    print()


def mouse_hook_proc(nCode, wParam, lParam):
    if nCode == HC_ACTION and wParam != WM_MOUSEMOVE:
        ms = ctypes.cast(lParam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
        name = MOUSE_MSG.get(wParam, f"0x{wParam:04X}")
        detail = ""
        if wParam in (WM_XBUTTONDOWN, WM_XBUTTONUP):
            # THE bit extraction people get wrong: X button index is the HIGH word.
            xbtn = (ms.mouseData >> 16) & 0xFFFF
            label = {1: "XBUTTON1 = 'Mouse 4' (Back)",
                     2: "XBUTTON2 = 'Mouse 5' (Forward)"}.get(xbtn, f"unknown X button {xbtn}")
            detail = f"  mouseData=0x{ms.mouseData:08X} -> HIWORD={xbtn} -> {label}"
            if wParam == WM_XBUTTONDOWN:
                _down_times[xbtn] = time.perf_counter()
            else:
                t = _down_times.pop(xbtn, None)
                if t is not None:
                    detail += f"   held {(time.perf_counter() - t) * 1000:.0f} ms"
        print(f"{ts()} HOOK  MOUSE     {name:16s}{detail}"
              f"{inject_note(ms.flags, LLMHF_INJECTED)}")
    return user32.CallNextHookEx(None, nCode, wParam, lParam)


def kbd_hook_proc(nCode, wParam, lParam):
    global _esc_count
    if nCode == HC_ACTION:
        ks = ctypes.cast(lParam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
        kind = {WM_KEYDOWN: "KEYDOWN", WM_KEYUP: "KEYUP",
                WM_SYSKEYDOWN: "SYSKEYDOWN", WM_SYSKEYUP: "SYSKEYUP"}.get(wParam, hex(wParam))
        print(f"{ts()} HOOK  KEYBOARD  {kind:16s}  vk=0x{ks.vkCode:02X} "
              f"scan=0x{ks.scanCode:02X}{inject_note(ks.flags, LLKHF_INJECTED)}")
        if ks.vkCode == 0x1B and wParam == WM_KEYDOWN:
            _esc_count += 1
            if _esc_count >= 3:
                user32.PostQuitMessage(0)
    return user32.CallNextHookEx(None, nCode, wParam, lParam)


_rawbuf = ctypes.create_string_buffer(1024)


def handle_raw_input(hRawInput):
    size = wt.UINT(ctypes.sizeof(_rawbuf))
    got = user32.GetRawInputData(hRawInput, RID_INPUT, _rawbuf,
                                 ctypes.byref(size), ctypes.sizeof(RAWINPUTHEADER))
    if got == wt.UINT(-1).value or got == 0:
        return
    hdr = ctypes.cast(_rawbuf, ctypes.POINTER(RAWINPUTHEADER)).contents
    off = ctypes.sizeof(RAWINPUTHEADER)
    if hdr.dwType == RIM_TYPEMOUSE:
        m = ctypes.cast(ctypes.byref(_rawbuf, off), ctypes.POINTER(RAWMOUSE)).contents
        if not m.usButtonFlags:
            return
        hits = [n for bit, n in RI_FLAGS if m.usButtonFlags & bit]
        print(f"{ts()} RAW   MOUSE     flags=0x{m.usButtonFlags:04X}  {', '.join(hits)}"
              f"  ulRawButtons=0x{m.ulRawButtons:08X}")
    elif hdr.dwType == RIM_TYPEHID:
        h = ctypes.cast(ctypes.byref(_rawbuf, off), ctypes.POINTER(RAWHID)).contents
        n = min(h.dwSizeHid * h.dwCount, 32)
        raw = bytes(ctypes.cast(ctypes.byref(h, RAWHID.bRawData.offset),
                                ctypes.POINTER(ctypes.c_ubyte * n)).contents)
        print(f"{ts()} RAW   HID       size={h.dwSizeHid} count={h.dwCount} "
              f"data={raw.hex(' ')}   <== vendor-specific usage (button 6+ likely)")


def wnd_proc(hwnd, msg, wParam, lParam):
    if msg == WM_INPUT:
        handle_raw_input(ctypes.c_void_p(lParam))
        return 0
    return user32.DefWindowProcW(hwnd, msg, wParam, lParam)


def main():
    enumerate_devices()

    hinst = kernel32.GetModuleHandleW(None)
    wp = WNDPROC(wnd_proc)
    wc = WNDCLASSW()
    wc.lpfnWndProc = wp
    wc.hInstance = hinst
    wc.lpszClassName = "BillyTalkProbeWnd"
    if not user32.RegisterClassW(ctypes.byref(wc)):
        raise ctypes.WinError(ctypes.get_last_error())
    hwnd = user32.CreateWindowExW(0, "BillyTalkProbeWnd", "probe", 0, 0, 0, 0, 0,
                                  HWND_MESSAGE, None, hinst, None)
    if not hwnd:
        raise ctypes.WinError(ctypes.get_last_error())

    # Mouse collection + generic-desktop "multi-axis"/vendor pages we can legally ask for.
    rids = (RAWINPUTDEVICE * 2)()
    rids[0].usUsagePage, rids[0].usUsage = 0x01, 0x02   # generic desktop / mouse
    rids[0].dwFlags, rids[0].hwndTarget = RIDEV_INPUTSINK, hwnd
    rids[1].usUsagePage, rids[1].usUsage = 0x01, 0x06   # generic desktop / keyboard
    rids[1].dwFlags, rids[1].hwndTarget = RIDEV_INPUTSINK, hwnd
    if not user32.RegisterRawInputDevices(rids, 2, ctypes.sizeof(RAWINPUTDEVICE)):
        print("WARNING: RegisterRawInputDevices failed:",
              ctypes.WinError(ctypes.get_last_error()))

    mh = HOOKPROC(mouse_hook_proc)
    kh = HOOKPROC(kbd_hook_proc)
    hm = user32.SetWindowsHookExW(WH_MOUSE_LL, mh, hinst, 0)
    hk = user32.SetWindowsHookExW(WH_KEYBOARD_LL, kh, hinst, 0)
    if not hm or not hk:
        raise ctypes.WinError(ctypes.get_last_error())

    print("=" * 78)
    print("LISTENING.  Now do this, slowly, one at a time:")
    print("  1. Press and RELEASE mouse button 4 (the rear side button).")
    print("  2. Press and HOLD it ~1 second, then release.")
    print("  3. Press mouse button 5.")
    print("  4. Press buttons 6+ if your mouse has them.")
    print("  5. Press a normal key (e.g. 'a') to confirm keyboard capture.")
    print()
    print("READING THE OUTPUT:")
    print("  HOOK MOUSE WM_XBUTTONDOWN + RAW MOUSE BUTTON_4  -> clean, standard. Best case.")
    print("  HOOK KEYBOARD only, no mouse line               -> Synapse/G HUB remapped it")
    print("                                                      to a keystroke.")
    print("  '<<< INJECTED'                                  -> vendor driver synthesized it.")
    print("  RAW HID lines only                              -> vendor-specific usage; a")
    print("                                                      standard mouse hook is blind.")
    print("  Nothing at all                                  -> button is consumed in firmware.")
    print()
    print("Press ESC three times to quit.")
    print("=" * 78)

    msg = wt.MSG()
    try:
        while True:
            r = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if r == 0 or r == -1:
                break
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
    except KeyboardInterrupt:
        pass
    finally:
        user32.UnhookWindowsHookEx(hm)
        user32.UnhookWindowsHookEx(hk)
        print("\nDone.")


if __name__ == "__main__":
    main()
