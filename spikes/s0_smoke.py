"""S0: does the stack actually work, not just install."""
import sys, ctypes, time
sys.stdout.reconfigure(encoding='utf-8', line_buffering=True)
print(f"python {sys.version}")
print(f"free-threaded: {not getattr(sys, '_is_gil_enabled', lambda: True)()}")

ok = True

def check(name, fn):
    global ok
    try:
        r = fn()
        print(f"  OK   {name}: {r}")
    except Exception as e:
        ok = False
        print(f"  FAIL {name}: {type(e).__name__}: {e}")

def wx_check():
    import wx
    app = wx.App(False)
    f = wx.Frame(None, title="probe")
    # the overlay requirement: WS_EX_NOACTIVATE applied post-create
    hwnd = f.GetHandle()
    GWL_EXSTYLE = -20
    WS_EX_NOACTIVATE = 0x08000000
    u = ctypes.WinDLL('user32', use_last_error=True)
    u.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    u.GetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int]
    u.SetWindowLongPtrW.restype = ctypes.c_ssize_t
    u.SetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t]
    cur = u.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
    u.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, cur | WS_EX_NOACTIVATE)
    new = u.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
    applied = bool(new & WS_EX_NOACTIVATE)
    f.Destroy()
    return f"wx {wx.version()}, NOACTIVATE applied={applied}"

def sd_check():
    import sounddevice as sd
    ins = [d for d in sd.query_devices() if d['max_input_channels'] > 0]
    return f"{len(ins)} input devices, default={sd.default.device}"

def sf_check():
    import soundfile as sf, numpy as np, io, time
    x = (np.random.rand(16000 * 15) * 0.1).astype('float32')  # 15s @16k
    buf = io.BytesIO()
    t = time.perf_counter()
    sf.write(buf, x, 16000, format='FLAC', subtype='PCM_16')
    ms = (time.perf_counter() - t) * 1000
    return f"libsndfile {sf.__libsndfile_version__}, 15s->FLAC {len(buf.getvalue())//1024}KB in {ms:.0f}ms"

def w32_check():
    import win32clipboard, win32cred
    return "win32clipboard + win32cred import OK"

def hook_check():
    """Can we install a real WH_MOUSE_LL from Python and see events?"""
    u = ctypes.WinDLL('user32', use_last_error=True)
    k = ctypes.WinDLL('kernel32', use_last_error=True)
    LRESULT = ctypes.c_ssize_t
    HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, ctypes.c_size_t, ctypes.c_void_p)
    k.GetModuleHandleW.restype = ctypes.c_void_p
    u.SetWindowsHookExW.restype = ctypes.c_void_p
    u.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, ctypes.c_void_p, ctypes.c_ulong]
    u.CallNextHookEx.restype = LRESULT
    u.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_size_t, ctypes.c_void_p]
    u.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
    counter = {'n': 0, 'max_us': 0.0}
    def proc(n, w, l):
        t = time.perf_counter()
        counter['n'] += 1
        dt = (time.perf_counter() - t) * 1e6
        if dt > counter['max_us']:
            counter['max_us'] = dt
        return u.CallNextHookEx(None, n, w, l)
    cb = HOOKPROC(proc)
    h = u.SetWindowsHookExW(14, cb, k.GetModuleHandleW(None), 0)
    if not h:
        raise OSError(f"SetWindowsHookExW failed: {ctypes.get_last_error()}")
    import ctypes.wintypes as wt
    msg = wt.MSG()
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < 3.0:
        if u.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
            u.TranslateMessage(ctypes.byref(msg)); u.DispatchMessageW(ctypes.byref(msg))
        time.sleep(0.001)
    u.UnhookWindowsHookEx(h)
    return f"{counter['n']} events in 3s (move the mouse to raise this), max callback {counter['max_us']:.1f}us"

check("wxPython + WS_EX_NOACTIVATE", wx_check)
check("sounddevice", sd_check)
check("soundfile FLAC encode", sf_check)
check("pywin32", w32_check)
check("WH_MOUSE_LL from Python", hook_check)

print("\nRESULT:", "STACK VIABLE" if ok else "STACK HAS FAILURES")
