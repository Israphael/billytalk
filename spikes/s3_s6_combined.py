"""S3 (audio device hot-swap) + S6 (hook liveness watchdog)."""
import sys, ctypes, time, threading
import ctypes.wintypes as wt
sys.stdout.reconfigure(encoding='utf-8', line_buffering=True)

print("=" * 72)
print("S3 - can we refresh the PortAudio device list without restarting?")
print("=" * 72)
import sounddevice as sd

def dev_names():
    return sorted({d['name'] for d in sd.query_devices() if d['max_input_channels'] > 0})

before = dev_names()
print(f"  inputs at start        : {len(before)}")

# 1. Does the documented-as-useless terminate/initialize work?
try:
    sd._terminate(); sd._initialize()
    print(f"  after _terminate/_init : {len(dev_names())} (expected: unchanged, list is frozen)")
except Exception as e:
    print(f"  _terminate/_init FAILED: {type(e).__name__}: {e}")

# 2. The private DLL reload the spec proposed. Does it survive?
try:
    t0 = time.perf_counter()
    sd._terminate()
    sd._ffi.dlclose(sd._lib)
    sd._lib = sd._ffi.dlopen(sd._libname)
    sd._initialize()
    ms = (time.perf_counter() - t0) * 1000
    after = dev_names()
    print(f"  after DLL reload       : {len(after)} devices, took {ms:.0f} ms  -> SURVIVED")
    print(f"  list identical         : {after == before}")
except Exception as e:
    print(f"  DLL reload FAILED      : {type(e).__name__}: {e}")

# 3. Can we open a stream after the reload? (the real risk)
try:
    with sd.InputStream(samplerate=16000, channels=1, blocksize=1024):
        time.sleep(0.3)
    print("  stream after reload    : OK")
except Exception as e:
    print(f"  stream after reload    : FAILED {type(e).__name__}: {e}")

# 4. Reload WHILE a stream is live - the case the spec says is impossible
print("\n  --- reload while a stream is ACTIVE (expected to be unsafe) ---")
try:
    st = sd.InputStream(samplerate=16000, channels=1, blocksize=1024)
    st.start()
    time.sleep(0.2)
    print("  stream started, now reloading DLL underneath it...")
    sd._terminate()
    sd._ffi.dlclose(sd._lib)
    sd._lib = sd._ffi.dlopen(sd._libname)
    sd._initialize()
    print("  *** SURVIVED the unsafe reload (did not crash) ***")
    try:
        st.stop(); st.close()
    except Exception as e:
        print(f"  (closing the orphaned stream raised {type(e).__name__}, as expected)")
except Exception as e:
    print(f"  raised {type(e).__name__}: {e}")

print("\n" + "=" * 72)
print("S6 - can a watchdog PROVE the hook is still alive?")
print("=" * 72)

u = ctypes.WinDLL('user32', use_last_error=True)
k = ctypes.WinDLL('kernel32', use_last_error=True)
LRESULT = ctypes.c_ssize_t
ULONG_PTR = ctypes.c_size_t
MAGIC = 0xB1117A1C          # our marker in dwExtraInfo
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

HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wt.WPARAM, wt.LPARAM)
k.GetModuleHandleW.restype = wt.HMODULE
k.GetCurrentThreadId.restype = wt.DWORD
u.SetWindowsHookExW.restype = wt.HHOOK
u.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wt.HINSTANCE, wt.DWORD]
u.CallNextHookEx.restype = LRESULT
u.CallNextHookEx.argtypes = [wt.HHOOK, ctypes.c_int, wt.WPARAM, wt.LPARAM]
u.UnhookWindowsHookEx.argtypes = [wt.HHOOK]
u.SendInput.argtypes = [wt.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
u.GetMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT]
u.PostThreadMessageW.argtypes = [wt.DWORD, wt.UINT, wt.WPARAM, wt.LPARAM]

echo = threading.Event()
seen_magic = {'n': 0}

def proc(nCode, wParam, lParam):
    if nCode == 0:
        ms = ctypes.cast(lParam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
        if ms.dwExtraInfo == MAGIC:
            seen_magic['n'] += 1
            echo.set()
    return u.CallNextHookEx(None, nCode, wParam, lParam)

cb = HOOKPROC(proc)
h = u.SetWindowsHookExW(14, cb, k.GetModuleHandleW(None), 0)
print(f"  hook installed: {bool(h)}")
tid = k.GetCurrentThreadId()

def ping(label, expect):
    echo.clear()
    inp = INPUT(type=INPUT_MOUSE)
    inp.mi = MOUSEINPUT(0, 0, 0, MOUSEEVENTF_MOVE, 0, MAGIC)   # zero-pixel move
    t0 = time.perf_counter()
    u.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))
    got = echo.wait(1.0)
    ms = (time.perf_counter() - t0) * 1000
    ok = "PASS" if got == expect else "FAIL"
    print(f"  [{ok}] {label}: echo={got} in {ms:.1f} ms (expected echo={expect})")

def worker():
    time.sleep(0.4)
    ping("hook ALIVE   ", True)
    u.UnhookWindowsHookEx(h)          # simulate Windows silently removing it
    time.sleep(0.2)
    ping("hook REMOVED ", False)
    u.PostThreadMessageW(tid, WM_QUIT, 0, 0)

threading.Thread(target=worker, daemon=True).start()
msg = wt.MSG()
while u.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
    u.TranslateMessage(ctypes.byref(msg)); u.DispatchMessageW(ctypes.byref(msg))

print(f"\n  magic events observed: {seen_magic['n']}")
print("  VERDICT: watchdog by synthetic-event echo is",
      "VIABLE" if seen_magic['n'] == 1 else "NOT VIABLE")
