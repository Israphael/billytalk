"""S1: does forced focus restore actually work, and in which apps?

This is THE gating question for BillyTalk's insertion design. The spec assumes
we can bring a previously-captured window back to the foreground after the user
switched away. Microsoft documents that SetForegroundWindow is refused for
background processes - with a list of exceptions, one of which is "the calling
process received the last input event".

We test three strategies against every real window the user has open:
  A. bare SetForegroundWindow
  B. AttachThreadInput + SetForegroundWindow  (the classic workaround)
  C. same as B, but with a low-level mouse hook installed and having just
     consumed an event - which is BillyTalk's actual runtime condition
"""
import sys, ctypes, time, threading
import ctypes.wintypes as wt

sys.stdout.reconfigure(encoding='utf-8', line_buffering=True)
u = ctypes.WinDLL('user32', use_last_error=True)
k = ctypes.WinDLL('kernel32', use_last_error=True)
psapi = ctypes.WinDLL('psapi', use_last_error=True)

LRESULT = ctypes.c_ssize_t
u.GetForegroundWindow.restype = wt.HWND
u.SetForegroundWindow.argtypes = [wt.HWND]; u.SetForegroundWindow.restype = wt.BOOL
u.AttachThreadInput.argtypes = [wt.DWORD, wt.DWORD, wt.BOOL]; u.AttachThreadInput.restype = wt.BOOL
u.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
u.GetWindowThreadProcessId.restype = wt.DWORD
u.IsWindowVisible.argtypes = [wt.HWND]
u.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
u.GetWindowTextLengthW.argtypes = [wt.HWND]
u.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
k.GetCurrentThreadId.restype = wt.DWORD
k.OpenProcess.restype = wt.HANDLE
k.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]

ENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
u.EnumWindows.argtypes = [ENUMPROC, wt.LPARAM]

def proc_name(hwnd):
    pid = wt.DWORD()
    u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    h = k.OpenProcess(0x1000, False, pid.value)      # QUERY_LIMITED_INFORMATION
    if not h:
        return "?"
    buf = ctypes.create_unicode_buffer(512)
    n = wt.DWORD(512)
    if hasattr(k, 'QueryFullProcessImageNameW'):
        k.QueryFullProcessImageNameW.argtypes = [wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD)]
        k.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n))
    k.CloseHandle(h)
    return buf.value.split('\\')[-1] or "?"

def title(hwnd):
    n = u.GetWindowTextLengthW(hwnd)
    if n <= 0:
        return ""
    b = ctypes.create_unicode_buffer(n + 1)
    u.GetWindowTextW(hwnd, b, n + 1)
    return b.value

def cls(hwnd):
    b = ctypes.create_unicode_buffer(256)
    u.GetClassNameW(hwnd, b, 256)
    return b.value

def enumerate_targets():
    out = []
    def cb(hwnd, _):
        if u.IsWindowVisible(hwnd) and title(hwnd):
            p = proc_name(hwnd)
            if p.lower() not in ('explorer.exe', 'textinputhost.exe', 'searchhost.exe',
                                 'shellexperiencehost.exe', 'systemsettings.exe', '?'):
                out.append((hwnd, p, title(hwnd)[:48], cls(hwnd)))
        return True
    u.EnumWindows(ENUMPROC(cb), 0)
    return out

# ---------- the three strategies ----------
def strat_bare(hwnd):
    return bool(u.SetForegroundWindow(hwnd))

def strat_attach(hwnd):
    tid_me = k.GetCurrentThreadId()
    tid_them = u.GetWindowThreadProcessId(hwnd, None)
    if not tid_them or tid_them == tid_me:
        return False
    attached = u.AttachThreadInput(tid_me, tid_them, True)
    try:
        return bool(u.SetForegroundWindow(hwnd))
    finally:
        if attached:
            u.AttachThreadInput(tid_me, tid_them, False)

def landed(hwnd, budget_ms=250):
    """Poll: did the foreground actually become our target?"""
    t0 = time.perf_counter()
    while (time.perf_counter() - t0) * 1000 < budget_ms:
        if u.GetForegroundWindow() == hwnd:
            return True, (time.perf_counter() - t0) * 1000
        time.sleep(0.01)
    return False, budget_ms

def run(strategy, name, targets, park):
    print(f"\n--- {name} ---")
    res = {}
    for hwnd, p, t, c in targets:
        # park focus somewhere else first, so each attempt is a real restore
        strat_attach(park)
        time.sleep(0.15)
        if u.GetForegroundWindow() == hwnd:
            res[p] = res.get(p, []) + [None]
            print(f"  {p:22s} SKIP (already foreground)")
            continue
        api_ok = strategy(hwnd)
        ok, ms = landed(hwnd)
        res.setdefault(p, []).append(ok)
        mark = "OK  " if ok else "FAIL"
        note = "" if api_ok == ok else f"   <-- API returned {api_ok}, reality {ok}"
        print(f"  [{mark}] {p:22s} {ms:5.0f} ms  {t[:34]}{note}")
        time.sleep(0.1)
    return res

def main():
    targets = enumerate_targets()
    print("=" * 74)
    print(f"S1 - focus restore. {len(targets)} candidate windows.")
    print("=" * 74)
    for hwnd, p, t, c in targets:
        print(f"  {p:22s} [{c[:28]:28s}] {t}")
    if len(targets) < 2:
        raise SystemExit("\nNeed at least 2 open windows to test restore.")

    park = targets[0][0]
    others = targets[1:]

    r_bare = run(strat_bare, "A. bare SetForegroundWindow", others, park)
    r_att = run(strat_attach, "B. AttachThreadInput + SetForegroundWindow", others, park)

    # C: same as B but with a live low-level mouse hook that just consumed input
    HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wt.WPARAM, wt.LPARAM)
    u.SetWindowsHookExW.restype = wt.HHOOK
    u.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wt.HINSTANCE, wt.DWORD]
    u.CallNextHookEx.restype = LRESULT
    seen = {'n': 0}
    def hp(n, w, l):
        if n == 0:
            seen['n'] += 1
        return u.CallNextHookEx(None, n, w, l)
    cb = HOOKPROC(hp)
    k.GetModuleHandleW.restype = wt.HMODULE
    h = u.SetWindowsHookExW(14, cb, k.GetModuleHandleW(None), 0)
    pump = threading.Thread(target=lambda: [
        u.DispatchMessageW(ctypes.byref(m)) for m in iter(lambda: None, 1)], daemon=True)
    r_hook = run(strat_attach, f"C. same, with LL hook installed (hook={bool(h)})", others, park)
    if h:
        u.UnhookWindowsHookEx(h)

    print("\n" + "=" * 74)
    print("SUMMARY - success rate per application")
    print("=" * 74)
    apps = sorted({p for _, p, _, _ in others})
    print(f"  {'app':24s} {'bare':>8s} {'attach':>8s} {'+hook':>8s}")
    for a in apps:
        def rate(r):
            v = [x for x in r.get(a, []) if x is not None]
            return f"{100*sum(v)/len(v):.0f}%" if v else "n/a"
        print(f"  {a:24s} {rate(r_bare):>8s} {rate(r_att):>8s} {rate(r_hook):>8s}")

    def overall(r):
        v = [x for vals in r.values() for x in vals if x is not None]
        return 100 * sum(v) / len(v) if v else 0
    print(f"\n  OVERALL   bare {overall(r_bare):.0f}%   "
          f"attach {overall(r_att):.0f}%   +hook {overall(r_hook):.0f}%")
    best = overall(r_hook)
    print(f"\n  VERDICT: {'focus restore is a viable PRIMARY path' if best >= 70 else 'focus restore must be treated as BEST-EFFORT ONLY'}")

main()
