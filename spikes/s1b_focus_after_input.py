"""S1b: focus restore under BillyTalk's ACTUAL runtime condition.

S1 measured a process that had received no input at all - guaranteed to fail
per Microsoft's documented SetForegroundWindow rules. That is not our case.

In production the sequence is:
    user presses the bound button -> our low-level hook consumes it
    -> we are (possibly) "the process that received the last input event"
    -> we try to restore focus to the window captured at press time

This script reproduces exactly that. It waits for a real physical button press,
captures the foreground window at that instant, parks focus elsewhere, and then
attempts the restore - all within the window where the input credit should apply.

INTERACTIVE. Instructions are printed; you press Mouse 5 a few times.
"""
import sys, ctypes, time, threading
import ctypes.wintypes as wt

sys.stdout.reconfigure(encoding='utf-8', line_buffering=True)
u = ctypes.WinDLL('user32', use_last_error=True)
k = ctypes.WinDLL('kernel32', use_last_error=True)

LRESULT = ctypes.c_ssize_t
ULONG_PTR = ctypes.c_size_t
WM_XBUTTONDOWN, WM_XBUTTONUP, WM_QUIT = 0x020B, 0x020C, 0x0012

class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("pt", wt.POINT), ("mouseData", wt.DWORD), ("flags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ULONG_PTR)]

HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wt.WPARAM, wt.LPARAM)
for f, r, a in [
    ('GetForegroundWindow', wt.HWND, []),
    ('SetForegroundWindow', wt.BOOL, [wt.HWND]),
    ('AttachThreadInput', wt.BOOL, [wt.DWORD, wt.DWORD, wt.BOOL]),
    ('GetWindowThreadProcessId', wt.DWORD, [wt.HWND, ctypes.POINTER(wt.DWORD)]),
    ('SetWindowsHookExW', wt.HHOOK, [ctypes.c_int, HOOKPROC, wt.HINSTANCE, wt.DWORD]),
    ('CallNextHookEx', LRESULT, [wt.HHOOK, ctypes.c_int, wt.WPARAM, wt.LPARAM]),
    ('UnhookWindowsHookEx', wt.BOOL, [wt.HHOOK]),
    ('GetMessageW', wt.BOOL, [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT]),
    ('PostThreadMessageW', wt.BOOL, [wt.DWORD, wt.UINT, wt.WPARAM, wt.LPARAM]),
    ('IsWindowVisible', wt.BOOL, [wt.HWND]),
    ('GetWindowTextW', ctypes.c_int, [wt.HWND, wt.LPWSTR, ctypes.c_int]),
]:
    fn = getattr(u, f); fn.restype = r; fn.argtypes = a
k.GetModuleHandleW.restype = wt.HMODULE
k.GetCurrentThreadId.restype = wt.DWORD
k.OpenProcess.restype = wt.HANDLE
k.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
k.QueryFullProcessImageNameW.argtypes = [wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD)]

def pname(hwnd):
    pid = wt.DWORD(); u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    h = k.OpenProcess(0x1000, False, pid.value)
    if not h: return "?"
    b = ctypes.create_unicode_buffer(512); n = wt.DWORD(512)
    k.QueryFullProcessImageNameW(h, 0, b, ctypes.byref(n)); k.CloseHandle(h)
    return b.value.split('\\')[-1]

def wtitle(hwnd):
    b = ctypes.create_unicode_buffer(120); u.GetWindowTextW(hwnd, b, 120); return b.value

def attach_restore(hwnd):
    me = k.GetCurrentThreadId()
    them = u.GetWindowThreadProcessId(hwnd, None)
    if not them or them == me: return False, "bad tid"
    ok_attach = u.AttachThreadInput(me, them, True)
    try:
        api = bool(u.SetForegroundWindow(hwnd))
    finally:
        if ok_attach: u.AttachThreadInput(me, them, False)
    t0 = time.perf_counter()
    while (time.perf_counter() - t0) * 1000 < 300:
        if u.GetForegroundWindow() == hwnd:
            return True, f"attach={bool(ok_attach)} api={api} in {(time.perf_counter()-t0)*1000:.0f}ms"
        time.sleep(0.01)
    return False, f"attach={bool(ok_attach)} api={api}"

def bare_restore(hwnd):
    api = bool(u.SetForegroundWindow(hwnd))
    t0 = time.perf_counter()
    while (time.perf_counter() - t0) * 1000 < 300:
        if u.GetForegroundWindow() == hwnd:
            return True, f"api={api} in {(time.perf_counter()-t0)*1000:.0f}ms"
        time.sleep(0.01)
    return False, f"api={api}"

results = []
trial = {'n': 0}
tid_main = k.GetCurrentThreadId()

def on_press():
    """Runs on the hook thread, right after a real physical button press."""
    captured = u.GetForegroundWindow()
    if not captured:
        return
    app = pname(captured)
    trial['n'] += 1
    n = trial['n']
    print(f"\n[{n}] captured: {app}  \"{wtitle(captured)[:40]}\"")

    # Park focus somewhere else, simulating the user switching windows.
    # We are the last-input process right now, so if parking works at all,
    # it works here - and if it doesn't, that itself is the answer.
    others = []
    ENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def cb(h, _):
        if u.IsWindowVisible(h) and wtitle(h) and h != captured:
            if pname(h).lower() not in ('explorer.exe', 'textinputhost.exe', '?'):
                others.append(h)
        return True
    u.EnumWindows.argtypes = [ENUMPROC, wt.LPARAM]
    u.EnumWindows(ENUMPROC(cb), 0)
    if not others:
        print("     no other window to park on"); return
    park = others[0]
    attach_restore(park)
    time.sleep(0.25)
    parked = u.GetForegroundWindow()
    if parked == captured:
        print("     PARK FAILED - focus never left the target; trial invalid")
        results.append((app, None, None)); return
    print(f"     parked on: {pname(parked)}")

    ok_b, d_b = bare_restore(captured)
    if not ok_b:
        attach_restore(park); time.sleep(0.2)
    ok_a, d_a = attach_restore(captured)
    print(f"     bare   : {'OK' if ok_b else 'FAIL'}  ({d_b})")
    print(f"     attach : {'OK' if ok_a else 'FAIL'}  ({d_a})")
    results.append((app, ok_b, ok_a))
    if trial['n'] >= 8:
        u.PostThreadMessageW(tid_main, WM_QUIT, 0, 0)

def hookproc(nCode, wParam, lParam):
    if nCode == 0 and wParam == WM_XBUTTONUP:
        ms = ctypes.cast(lParam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
        if ((ms.mouseData >> 16) & 0xFFFF) == 2:      # XBUTTON2 = Mouse 5
            threading.Thread(target=on_press, daemon=True).start()
    return u.CallNextHookEx(None, nCode, wParam, lParam)

cb = HOOKPROC(hookproc)
h = u.SetWindowsHookExW(14, cb, k.GetModuleHandleW(None), 0)
print("=" * 72)
print("S1b - focus restore right after real input, BillyTalk's actual condition")
print("=" * 72)
print(f"hook installed: {bool(h)}\n")
print("WHAT TO DO:")
print("  Click into an app (Chrome, PuTTY/mintty, Claude, Notepad, Telegram),")
print("  then press MOUSE 5 (the FRONT side button) once.")
print("  The script grabs the focused window, moves focus away, and tries to")
print("  bring it back. Watch the screen - it will flicker.")
print("  Repeat in 6-8 different apps. It stops automatically after 8.\n")
print("(Mouse 5, not Mouse 4 - Mouse 4 is Back and would navigate.)\n")

threading.Timer(300, lambda: u.PostThreadMessageW(tid_main, WM_QUIT, 0, 0)).start()
msg = wt.MSG()
while u.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
    u.TranslateMessage(ctypes.byref(msg)); u.DispatchMessageW(ctypes.byref(msg))
if h: u.UnhookWindowsHookEx(h)

print("\n" + "=" * 72)
print("RESULTS")
print("=" * 72)
valid = [r for r in results if r[1] is not None]
if not valid:
    print("  No valid trials.")
else:
    per = {}
    for app, b, a in valid:
        per.setdefault(app, []).append((b, a))
    print(f"  {'app':24s} {'trials':>7s} {'bare':>8s} {'attach':>8s}")
    for app, rows in sorted(per.items()):
        nb = sum(1 for b, _ in rows if b); na = sum(1 for _, a in rows if a)
        print(f"  {app:24s} {len(rows):>7d} {100*nb//len(rows):>7d}% {100*na//len(rows):>7d}%")
    tb = sum(1 for b, _ in valid if b); ta = sum(1 for _, a in valid if a)
    print(f"\n  OVERALL  bare {100*tb//len(valid)}%   attach {100*ta//len(valid)}%   "
          f"({len(valid)} valid trials)")
    best = max(tb, ta) * 100 // len(valid)
    print(f"\n  VERDICT: focus restore is "
          f"{'a viable PRIMARY path' if best >= 70 else 'BEST-EFFORT ONLY - redesign around it'}")
