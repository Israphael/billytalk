"""S0b: isolate WH_MOUSE_LL from Python. No wx, no other imports."""
import sys, ctypes, time
import ctypes.wintypes as wt
sys.stdout.reconfigure(encoding='utf-8', line_buffering=True)

u = ctypes.WinDLL('user32', use_last_error=True)
k = ctypes.WinDLL('kernel32', use_last_error=True)

LRESULT = ctypes.c_ssize_t
ULONG_PTR = ctypes.c_size_t

class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("pt", wt.POINT), ("mouseData", wt.DWORD), ("flags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ULONG_PTR)]

HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wt.WPARAM, wt.LPARAM)

k.GetModuleHandleW.restype = wt.HMODULE
k.GetModuleHandleW.argtypes = [wt.LPCWSTR]
u.SetWindowsHookExW.restype = wt.HHOOK
u.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wt.HINSTANCE, wt.DWORD]
u.CallNextHookEx.restype = LRESULT
u.CallNextHookEx.argtypes = [wt.HHOOK, ctypes.c_int, wt.WPARAM, wt.LPARAM]
u.UnhookWindowsHookEx.argtypes = [wt.HHOOK]
u.GetMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT]
u.PeekMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT, wt.UINT]

stats = {'n': 0, 'moves': 0, 'max_us': 0.0, 'total_us': 0.0}

def proc(nCode, wParam, lParam):
    t0 = time.perf_counter()
    try:
        if nCode == 0:
            stats['n'] += 1
            if wParam == 0x0200:
                stats['moves'] += 1
            else:
                ms = ctypes.cast(lParam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                _ = (ms.mouseData >> 16) & 0xFFFF
    except Exception as e:
        print("CALLBACK EXC:", e)
    dt = (time.perf_counter() - t0) * 1e6
    stats['total_us'] += dt
    if dt > stats['max_us']:
        stats['max_us'] = dt
    return u.CallNextHookEx(None, nCode, wParam, lParam)

cb = HOOKPROC(proc)
h = u.SetWindowsHookExW(14, cb, k.GetModuleHandleW(None), 0)
print("hook handle:", h, "err:", ctypes.get_last_error())
if not h:
    raise SystemExit("SetWindowsHookExW FAILED")

print("Pumping messages for 30 seconds. MOVE THE MOUSE AND CLICK.")
msg = wt.MSG()
t0 = time.perf_counter()
pumped = 0
while time.perf_counter() - t0 < 30.0:
    while u.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
        u.TranslateMessage(ctypes.byref(msg))
        u.DispatchMessageW(ctypes.byref(msg))
        pumped += 1
    time.sleep(0.002)

u.UnhookWindowsHookEx(h)
n = stats['n']
print(f"\nevents seen      : {n}  (moves: {stats['moves']})")
print(f"messages pumped  : {pumped}")
if n:
    print(f"callback avg     : {stats['total_us']/n:.1f} us")
    print(f"callback max     : {stats['max_us']:.1f} us   (budget is 1,000,000 us)")
    print(f"events/sec       : {n/6.0:.0f}")
print("SURVIVED - no crash")

