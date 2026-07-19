"""S0c: same hook, but blocking GetMessage instead of a PeekMessage spin loop.

Hypothesis: LL hook callbacks are NOT dispatched to a thread that sits in
time.sleep(); the thread must be blocked in GetMessage.
A watchdog thread posts WM_QUIT after N seconds to end the loop.
"""
import sys, ctypes, time, threading
import ctypes.wintypes as wt
sys.stdout.reconfigure(encoding='utf-8', line_buffering=True)

SECONDS = 90

u = ctypes.WinDLL('user32', use_last_error=True)
k = ctypes.WinDLL('kernel32', use_last_error=True)

LRESULT = ctypes.c_ssize_t
ULONG_PTR = ctypes.c_size_t
WM_QUIT = 0x0012

class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("pt", wt.POINT), ("mouseData", wt.DWORD), ("flags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ULONG_PTR)]

HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wt.WPARAM, wt.LPARAM)

k.GetModuleHandleW.restype = wt.HMODULE
k.GetModuleHandleW.argtypes = [wt.LPCWSTR]
k.GetCurrentThreadId.restype = wt.DWORD
u.SetWindowsHookExW.restype = wt.HHOOK
u.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wt.HINSTANCE, wt.DWORD]
u.CallNextHookEx.restype = LRESULT
u.CallNextHookEx.argtypes = [wt.HHOOK, ctypes.c_int, wt.WPARAM, wt.LPARAM]
u.UnhookWindowsHookEx.argtypes = [wt.HHOOK]
u.GetMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT]
u.PostThreadMessageW.argtypes = [wt.DWORD, wt.UINT, wt.WPARAM, wt.LPARAM]

stats = {'n': 0, 'moves': 0, 'buttons': 0, 'max_us': 0.0, 'total_us': 0.0}
lat = []

def proc(nCode, wParam, lParam):
    t0 = time.perf_counter()
    if nCode == 0:
        stats['n'] += 1
        if wParam == 0x0200:
            stats['moves'] += 1
        else:
            stats['buttons'] += 1
            ms = ctypes.cast(lParam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
            _ = (ms.mouseData >> 16) & 0xFFFF
    dt = (time.perf_counter() - t0) * 1e6
    stats['total_us'] += dt
    lat.append(dt)
    if dt > stats['max_us']:
        stats['max_us'] = dt
    return u.CallNextHookEx(None, nCode, wParam, lParam)

cb = HOOKPROC(proc)
h = u.SetWindowsHookExW(14, cb, k.GetModuleHandleW(None), 0)
print("hook handle:", h, "err:", ctypes.get_last_error())
if not h:
    raise SystemExit("SetWindowsHookExW FAILED")

tid = k.GetCurrentThreadId()
threading.Timer(SECONDS, lambda: u.PostThreadMessageW(tid, WM_QUIT, 0, 0)).start()

def _heartbeat():
    while True:
        time.sleep(5)
        print(f"  ...{stats['n']} events so far (moves {stats['moves']}, btn {stats['buttons']})",
              flush=True)
threading.Thread(target=_heartbeat, daemon=True).start()

print(f"BLOCKING GetMessage for {SECONDS}s. MOVE THE MOUSE AND CLICK.")
msg = wt.MSG()
t0 = time.perf_counter()
while u.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
    u.TranslateMessage(ctypes.byref(msg))
    u.DispatchMessageW(ctypes.byref(msg))
elapsed = time.perf_counter() - t0

u.UnhookWindowsHookEx(h)
n = stats['n']
print(f"\nelapsed          : {elapsed:.1f}s")
print(f"events seen      : {n}   (moves {stats['moves']}, buttons/wheel {stats['buttons']})")
if n:
    lat.sort()
    print(f"events/sec       : {n/elapsed:.0f}")
    print(f"callback avg     : {stats['total_us']/n:.1f} us")
    print(f"callback p50     : {lat[len(lat)//2]:.1f} us")
    print(f"callback p99     : {lat[int(len(lat)*0.99)]:.1f} us")
    print(f"callback MAX     : {stats['max_us']:.1f} us    <-- budget 1,000,000 us")
    print(f"CPU in callbacks : {100*stats['total_us']/1e6/elapsed:.2f}% of one core")
    print(f"\nVERDICT: {'PYTHON HOOK VIABLE' if stats['max_us'] < 50000 else 'TOO RISKY'}")
else:
    print("STILL ZERO -> hypothesis wrong, something else blocks delivery")
