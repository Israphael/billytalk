"""S1c: focus restore in the REAL sequence, with the user switching windows.

Why v3. The rule that governs SetForegroundWindow is: the process that received
the LAST input event may set the foreground. Our hook consumes the button press,
so at press time the credit is ours.

But in production the user then CLICKS INTO ANOTHER WINDOW while transcription
runs - and that click hands the credit to the other application. By delivery time
we are no longer the last-input process.

v1 tested a process with no input at all (guaranteed fail).
v2 parked focus programmatically, which never spent our credit (unrealistically
easy).
v3 makes the USER switch windows, which is the only faithful reproduction.

SEQUENCE PER TRIAL
  1. click into app A, press MOUSE 5      -> we capture A and start a 4 s timer
  2. immediately click into app B          -> B now owns the input credit
  3. at t+4 s we try to restore A, and report whether it actually landed
"""
import sys, ctypes, time, threading
import ctypes.wintypes as wt

sys.stdout.reconfigure(encoding='utf-8', line_buffering=True)
u = ctypes.WinDLL('user32', use_last_error=True)
k = ctypes.WinDLL('kernel32', use_last_error=True)

LRESULT = ctypes.c_ssize_t
ULONG_PTR = ctypes.c_size_t
WM_XBUTTONUP, WM_QUIT = 0x020C, 0x0012
DELAY_S = 4.0
MAX_TRIALS = 8

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
    ('IsWindow', wt.BOOL, [wt.HWND]),
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
    b = ctypes.create_unicode_buffer(90); u.GetWindowTextW(hwnd, b, 90); return b.value

def try_restore(hwnd, use_attach):
    me = k.GetCurrentThreadId()
    them = u.GetWindowThreadProcessId(hwnd, None)
    att = False
    if use_attach and them and them != me:
        att = bool(u.AttachThreadInput(me, them, True))
    try:
        api = bool(u.SetForegroundWindow(hwnd))
    finally:
        if att:
            u.AttachThreadInput(me, them, False)
    t0 = time.perf_counter()
    while (time.perf_counter() - t0) * 1000 < 400:
        if u.GetForegroundWindow() == hwnd:
            return True, api, att
        time.sleep(0.01)
    return False, api, att

results = []
n = {'i': 0}
tid_main = k.GetCurrentThreadId()

def trial(captured, app):
    time.sleep(DELAY_S)
    if not u.IsWindow(captured):
        print("     target window gone; trial dropped"); return
    now_fg = u.GetForegroundWindow()
    if now_fg == captured:
        print(f"     !! you did not switch away - still on {app}. Trial dropped.")
        return
    switched_to = pname(now_fg)
    ok_b, api_b, _ = try_restore(captured, use_attach=False)
    if not ok_b:
        ok_a, api_a, att = try_restore(captured, use_attach=True)
    else:
        ok_a, api_a, att = True, api_b, False
    print(f"     switched to {switched_to} -> restore {app}: "
          f"bare {'OK' if ok_b else 'FAIL'} (api={api_b}), "
          f"attach {'OK' if ok_a else 'FAIL'} (api={api_a})")
    results.append((app, switched_to, ok_b, ok_a))
    if len(results) >= MAX_TRIALS:
        u.PostThreadMessageW(tid_main, WM_QUIT, 0, 0)

def hookproc(nCode, wParam, lParam):
    if nCode == 0 and wParam == WM_XBUTTONUP:
        ms = ctypes.cast(lParam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
        if ((ms.mouseData >> 16) & 0xFFFF) == 2:
            fg = u.GetForegroundWindow()
            if fg:
                n['i'] += 1
                app = pname(fg)
                print(f"\n[{n['i']}] captured {app} \"{wtitle(fg)[:38]}\" "
                      f"- NOW CLICK ANOTHER WINDOW, checking in {DELAY_S:.0f}s")
                threading.Thread(target=trial, args=(fg, app), daemon=True).start()
    return u.CallNextHookEx(None, nCode, wParam, lParam)

cb = HOOKPROC(hookproc)
h = u.SetWindowsHookExW(14, cb, k.GetModuleHandleW(None), 0)
print("=" * 72)
print("S1c - focus restore after the USER switches windows (the real case)")
print("=" * 72)
print(f"hook installed: {bool(h)}\n")
print("PER TRIAL:")
print("  1. click into an app, press MOUSE 5 (front side button)")
print("  2. IMMEDIATELY click into a DIFFERENT app")
print(f"  3. wait ~{DELAY_S:.0f}s - the first app should jump back if restore works\n")
print(f"Do this {MAX_TRIALS} times across Chrome, mintty, Claude, Telegram, Notepad.\n")

threading.Timer(420, lambda: u.PostThreadMessageW(tid_main, WM_QUIT, 0, 0)).start()
msg = wt.MSG()
while u.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
    u.TranslateMessage(ctypes.byref(msg)); u.DispatchMessageW(ctypes.byref(msg))
if h: u.UnhookWindowsHookEx(h)

print("\n" + "=" * 72)
print("RESULTS - restore after the user handed input credit to another app")
print("=" * 72)
if not results:
    print("  no valid trials")
else:
    print(f"  {'from':>20s} -> {'switched to':<20s} {'bare':>6s} {'attach':>7s}")
    for app, to, b, a in results:
        print(f"  {app:>20s} -> {to:<20s} {'OK' if b else 'FAIL':>6s} {'OK' if a else 'FAIL':>7s}")
    nb = sum(1 for _, _, b, _ in results if b)
    na = sum(1 for _, _, _, a in results if a)
    t = len(results)
    print(f"\n  bare   {100*nb//t}%   attach {100*na//t}%   ({t} trials)")
    best = max(nb, na) * 100 // t
    print(f"\n  VERDICT: {'PRIMARY path is viable' if best >= 70 else 'BEST-EFFORT ONLY - clipboard + hotkey must be the primary path'}")
