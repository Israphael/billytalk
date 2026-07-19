"""S6b: two candidate watchdogs for a silently-removed low-level hook.

A) synthetic SendInput echo, with a REAL 1px delta (zero-delta may be dropped)
B) GetLastInputInfo vs our own last-seen-event time  (no injection at all)
"""
import sys, ctypes, time, threading
import ctypes.wintypes as wt
sys.stdout.reconfigure(encoding='utf-8', line_buffering=True)

u = ctypes.WinDLL('user32', use_last_error=True)
k = ctypes.WinDLL('kernel32', use_last_error=True)
LRESULT = ctypes.c_ssize_t
ULONG_PTR = ctypes.c_size_t
MAGIC = 0xB1117A1C
WM_QUIT = 0x0012
INPUT_MOUSE = 0
MOUSEEVENTF_MOVE = 0x0001

class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("pt", wt.POINT), ("mouseData", wt.DWORD), ("flags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ULONG_PTR)]
class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wt.LONG), ("dy", wt.LONG), ("mouseData", wt.DWORD),
                ("dwFlags", wt.DWORD), ("time", wt.DWORD), ("dwExtraInfo", ULONG_PTR)]
class INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("mi", MOUSEINPUT)]
    _anonymous_ = ("u",)
    _fields_ = [("type", wt.DWORD), ("u", _U)]
class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wt.UINT), ("dwTime", wt.DWORD)]

HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wt.WPARAM, wt.LPARAM)
k.GetModuleHandleW.restype = wt.HMODULE
k.GetCurrentThreadId.restype = wt.DWORD
k.GetTickCount.restype = wt.DWORD
u.SetWindowsHookExW.restype = wt.HHOOK
u.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wt.HINSTANCE, wt.DWORD]
u.CallNextHookEx.restype = LRESULT
u.CallNextHookEx.argtypes = [wt.HHOOK, ctypes.c_int, wt.WPARAM, wt.LPARAM]
u.UnhookWindowsHookEx.argtypes = [wt.HHOOK]
u.SendInput.argtypes = [wt.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
u.SendInput.restype = wt.UINT
u.GetMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT]
u.PostThreadMessageW.argtypes = [wt.DWORD, wt.UINT, wt.WPARAM, wt.LPARAM]
u.GetLastInputInfo.argtypes = [ctypes.POINTER(LASTINPUTINFO)]

state = {'magic': 0, 'any': 0, 'last_tick': 0}
echo = threading.Event()

def proc(nCode, wParam, lParam):
    if nCode == 0:
        state['any'] += 1
        state['last_tick'] = k.GetTickCount()
        ms = ctypes.cast(lParam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
        if ms.dwExtraInfo == MAGIC:
            state['magic'] += 1
            echo.set()
    return u.CallNextHookEx(None, nCode, wParam, lParam)

cb = HOOKPROC(proc)
h = u.SetWindowsHookExW(14, cb, k.GetModuleHandleW(None), 0)
print(f"hook installed: {bool(h)}\n")
tid = k.GetCurrentThreadId()

def sys_last_input_tick():
    li = LASTINPUTINFO(); li.cbSize = ctypes.sizeof(LASTINPUTINFO)
    u.GetLastInputInfo(ctypes.byref(li))
    return li.dwTime

def ping(dx, label):
    echo.clear()
    n_before = state['any']
    inp = INPUT(type=INPUT_MOUSE)
    inp.mi = MOUSEINPUT(dx, 0, 0, MOUSEEVENTF_MOVE, 0, MAGIC)
    t0 = time.perf_counter()
    sent = u.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))
    got = echo.wait(0.5)
    print(f"  {label}: SendInput returned {sent}, echo={got} "
          f"in {(time.perf_counter()-t0)*1000:.1f} ms, hook saw {state['any']-n_before} event(s)")
    return got

def worker():
    time.sleep(0.4)
    print("A) synthetic echo watchdog")
    ping(0, "dx=0 (zero-delta)")
    ping(1, "dx=1 (real move) ")
    ping(-1, "dx=-1 (move back)")

    print("\nB) GetLastInputInfo divergence watchdog")
    sys_t = sys_last_input_tick()
    print(f"  hook alive : system_last_input={sys_t}  hook_last={state['last_tick']}  "
          f"divergence={sys_t - state['last_tick']} ms")

    u.UnhookWindowsHookEx(h)
    print("\n  --- hook now REMOVED, generating input ---")
    for i in range(3):
        inp = INPUT(type=INPUT_MOUSE)
        inp.mi = MOUSEINPUT(1 if i % 2 == 0 else -1, 0, 0, MOUSEEVENTF_MOVE, 0, 0)
        u.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))
        time.sleep(0.12)
    time.sleep(0.3)
    sys_t2 = sys_last_input_tick()
    div = sys_t2 - state['last_tick']
    print(f"  hook dead  : system_last_input={sys_t2}  hook_last={state['last_tick']}  "
          f"divergence={div} ms")
    print(f"\n  VERDICT A (echo)       : {'VIABLE' if state['magic'] else 'NOT VIABLE'}")
    print(f"  VERDICT B (divergence) : {'VIABLE' if div > 200 else 'NOT VIABLE'} "
          f"(dead hook shows {div} ms of unseen system input)")
    u.PostThreadMessageW(tid, WM_QUIT, 0, 0)

threading.Thread(target=worker, daemon=True).start()
msg = wt.MSG()
while u.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
    u.TranslateMessage(ctypes.byref(msg)); u.DispatchMessageW(ctypes.byref(msg))
