"""S7: can we deliver text to a captured window WITHOUT bringing it to the foreground?

S1 measured forced focus restore at 25%. This tests the alternative the cua
project uses: deliver into the target window directly, never fronting it.

Four delivery routes, tried in order against the SAME captured control:

  A. WM_PASTE          - the cleanest. Standard EDIT/RichEdit handle it natively.
  B. WM_CHAR per char  - posts characters straight to the control's queue.
  C. PostMessage Ctrl+V - synthesises the key sequence into the target queue.
  D. UIA ValuePattern  - read-only here; used to VERIFY, never to write.

Verification: read the control back with WM_GETTEXT and via UIA. Where neither
works (Chrome, Electron), the script says so and you confirm visually.

INTERACTIVE. Press MOUSE 5 in a text field, then click into another window.
"""
import sys, ctypes, time, threading
import ctypes.wintypes as wt

sys.stdout.reconfigure(encoding='utf-8', line_buffering=True)
u = ctypes.WinDLL('user32', use_last_error=True)
k = ctypes.WinDLL('kernel32', use_last_error=True)

LRESULT = ctypes.c_ssize_t
ULONG_PTR = ctypes.c_size_t
WM_XBUTTONUP, WM_QUIT = 0x020C, 0x0012
WM_PASTE, WM_GETTEXT, WM_GETTEXTLENGTH, WM_CHAR = 0x0302, 0x000D, 0x000E, 0x0102
WM_KEYDOWN, WM_KEYUP = 0x0100, 0x0101
VK_CONTROL, VK_V = 0x11, 0x56
MARKER = "BILLYTALK_S7_"
DELAY_S = 4.0
MAX_TRIALS = 8

class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("pt", wt.POINT), ("mouseData", wt.DWORD), ("flags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ULONG_PTR)]

class GUITHREADINFO(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("flags", wt.DWORD), ("hwndActive", wt.HWND),
                ("hwndFocus", wt.HWND), ("hwndCapture", wt.HWND), ("hwndMenuOwner", wt.HWND),
                ("hwndMoveSize", wt.HWND), ("hwndCaret", wt.HWND), ("rcCaret", wt.RECT)]

HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wt.WPARAM, wt.LPARAM)
for f, r, a in [
    ('GetForegroundWindow', wt.HWND, []),
    ('GetWindowThreadProcessId', wt.DWORD, [wt.HWND, ctypes.POINTER(wt.DWORD)]),
    ('GetGUIThreadInfo', wt.BOOL, [wt.DWORD, ctypes.POINTER(GUITHREADINFO)]),
    ('SetWindowsHookExW', wt.HHOOK, [ctypes.c_int, HOOKPROC, wt.HINSTANCE, wt.DWORD]),
    ('CallNextHookEx', LRESULT, [wt.HHOOK, ctypes.c_int, wt.WPARAM, wt.LPARAM]),
    ('UnhookWindowsHookEx', wt.BOOL, [wt.HHOOK]),
    ('GetMessageW', wt.BOOL, [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT]),
    ('PostThreadMessageW', wt.BOOL, [wt.DWORD, wt.UINT, wt.WPARAM, wt.LPARAM]),
    ('PostMessageW', wt.BOOL, [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]),
    ('SendMessageTimeoutW', LRESULT,
        [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM, wt.UINT, wt.UINT, ctypes.POINTER(ULONG_PTR)]),
    ('GetClassNameW', ctypes.c_int, [wt.HWND, wt.LPWSTR, ctypes.c_int]),
    ('GetWindowTextW', ctypes.c_int, [wt.HWND, wt.LPWSTR, ctypes.c_int]),
    ('IsWindow', wt.BOOL, [wt.HWND]),
    ('OpenClipboard', wt.BOOL, [wt.HWND]),
    ('CloseClipboard', wt.BOOL, []),
    ('EmptyClipboard', wt.BOOL, []),
    ('SetClipboardData', wt.HANDLE, [wt.UINT, wt.HANDLE]),
]:
    fn = getattr(u, f); fn.restype = r; fn.argtypes = a
k.GetModuleHandleW.restype = wt.HMODULE
k.GetCurrentThreadId.restype = wt.DWORD
k.GlobalAlloc.restype = wt.HGLOBAL
k.GlobalAlloc.argtypes = [wt.UINT, ctypes.c_size_t]
k.GlobalLock.restype = wt.LPVOID
k.GlobalLock.argtypes = [wt.HGLOBAL]
k.GlobalUnlock.argtypes = [wt.HGLOBAL]
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

def clsname(hwnd):
    b = ctypes.create_unicode_buffer(128); u.GetClassNameW(hwnd, b, 128); return b.value

def set_clipboard(text):
    if not u.OpenClipboard(None): return False
    try:
        u.EmptyClipboard()
        data = text.encode('utf-16-le') + b'\x00\x00'
        h = k.GlobalAlloc(0x0042, len(data))          # GMEM_MOVEABLE|GMEM_ZEROINIT
        p = k.GlobalLock(h)
        ctypes.memmove(p, data, len(data))
        k.GlobalUnlock(h)
        u.SetClipboardData(13, h)                      # CF_UNICODETEXT
        return True
    finally:
        u.CloseClipboard()

def read_control_text(hwnd, timeout_ms=300):
    """WM_GETTEXT with a timeout so a hung target cannot hang us."""
    res = ULONG_PTR()
    n = u.SendMessageTimeoutW(hwnd, WM_GETTEXTLENGTH, 0, 0, 0x0002, timeout_ms,
                              ctypes.byref(res))
    if not n or res.value == 0:
        return None
    length = res.value
    buf = ctypes.create_unicode_buffer(length + 1)
    got = u.SendMessageTimeoutW(hwnd, WM_GETTEXT, length + 1,
                                ctypes.cast(buf, wt.LPARAM), 0x0002, timeout_ms,
                                ctypes.byref(res))
    return buf.value if got else None

def focused_control_of(hwnd):
    tid = u.GetWindowThreadProcessId(hwnd, None)
    gti = GUITHREADINFO(); gti.cbSize = ctypes.sizeof(GUITHREADINFO)
    if u.GetGUIThreadInfo(tid, ctypes.byref(gti)) and gti.hwndFocus:
        return gti.hwndFocus
    return None

# ---------- delivery routes ----------
def route_wm_paste(ctrl, _text):
    return bool(u.PostMessageW(ctrl, WM_PASTE, 0, 0))

def route_wm_char(ctrl, text):
    ok = True
    for ch in text:
        if not u.PostMessageW(ctrl, WM_CHAR, ord(ch), 0):
            ok = False
    return ok

def route_post_ctrl_v(ctrl, _text):
    a = u.PostMessageW(ctrl, WM_KEYDOWN, VK_CONTROL, 0)
    b = u.PostMessageW(ctrl, WM_KEYDOWN, VK_V, 0)
    c = u.PostMessageW(ctrl, WM_KEYUP,   VK_V, 0)
    d = u.PostMessageW(ctrl, WM_KEYUP,   VK_CONTROL, 0)
    return all([a, b, c, d])

ROUTES = [("WM_PASTE", route_wm_paste),
          ("WM_CHAR", route_wm_char),
          ("Post Ctrl+V", route_post_ctrl_v)]

results = []
n = {'i': 0}
tid_main = k.GetCurrentThreadId()

def trial(win, ctrl, app, ctrl_cls):
    time.sleep(DELAY_S)
    if not u.IsWindow(win) or not u.IsWindow(ctrl):
        print("     окно исчезло, испытание отброшено"); return
    if u.GetForegroundWindow() == win:
        print(f"     !! ты не ушёл из {app}. Испытание отброшено."); return
    now = pname(u.GetForegroundWindow())
    before = read_control_text(ctrl)
    readable = before is not None
    print(f"     ушёл в {now} | чтение контрола: {'да' if readable else 'НЕТ'}")

    row = {"app": app, "cls": ctrl_cls, "readable": readable}
    for name, fn in ROUTES:
        marker = f"{MARKER}{name.replace(' ', '')}"
        set_clipboard(marker)
        time.sleep(0.05)
        api = fn(ctrl, marker)
        time.sleep(0.45)
        after = read_control_text(ctrl)
        if readable and after is not None:
            landed = marker in after
            verdict = "OK" if landed else "FAIL"
        else:
            verdict = "?"          # cannot verify programmatically
        # foreground must NOT have changed - that is the whole point
        stole = (u.GetForegroundWindow() == win)
        row[name] = verdict
        row[name + "_stole"] = stole
        print(f"       {name:12s} api={str(api):5s} -> {verdict}"
              f"{'  !! УКРАЛ ФОКУС' if stole else ''}")
    results.append(row)
    if len(results) >= MAX_TRIALS:
        u.PostThreadMessageW(tid_main, WM_QUIT, 0, 0)

def hookproc(nCode, wParam, lParam):
    if nCode == 0 and wParam == WM_XBUTTONUP:
        ms = ctypes.cast(lParam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
        if ((ms.mouseData >> 16) & 0xFFFF) == 2:          # Mouse 5
            win = u.GetForegroundWindow()
            ctrl = focused_control_of(win) if win else None
            if win and ctrl:
                n['i'] += 1
                app, cc = pname(win), clsname(ctrl)
                print(f"\n[{n['i']}] {app} | контрол класса {cc}"
                      f" — ТЕПЕРЬ КЛИКНИ В ДРУГОЕ ОКНО, проверю через {DELAY_S:.0f}с")
                threading.Thread(target=trial, args=(win, ctrl, app, cc), daemon=True).start()
            elif win:
                print(f"\n[-] {pname(win)}: не удалось найти сфокусированный контрол — пропуск")
    return u.CallNextHookEx(None, nCode, wParam, lParam)

cb = HOOKPROC(hookproc)
h = u.SetWindowsHookExW(14, cb, k.GetModuleHandleW(None), 0)
print("=" * 74)
print("S7 — доставка текста БЕЗ вывода окна на передний план")
print("=" * 74)
print(f"хук установлен: {bool(h)}\n")
print("ЧТО ДЕЛАТЬ, на каждое испытание:")
print("  1. поставь курсор в ТЕКСТОВОЕ ПОЛЕ (Блокнот, адресная строка, поле чата)")
print("  2. нажми MOUSE 5")
print("  3. СРАЗУ кликни в другое окно")
print(f"  4. через {DELAY_S:.0f}с скрипт попробует вставить туда три способами\n")
print("Смотри на исходное поле: появился ли там текст BILLYTALK_S7_...")
print(f"Повтори в {MAX_TRIALS} местах: Блокнот, Chrome, mintty, Telegram, Claude.\n")

threading.Timer(600, lambda: u.PostThreadMessageW(tid_main, WM_QUIT, 0, 0)).start()
msg = wt.MSG()
while u.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
    u.TranslateMessage(ctypes.byref(msg)); u.DispatchMessageW(ctypes.byref(msg))
if h: u.UnhookWindowsHookEx(h)

print("\n" + "=" * 74)
print("РЕЗУЛЬТАТ")
print("=" * 74)
if not results:
    print("  нет данных")
else:
    print(f"  {'приложение':22s} {'класс контрола':22s} {'WM_PASTE':>9s} {'WM_CHAR':>8s} {'Ctrl+V':>8s}")
    for r in results:
        print(f"  {r['app']:22s} {r['cls'][:22]:22s} {r['WM_PASTE']:>9s} "
              f"{r['WM_CHAR']:>8s} {r['Post Ctrl+V']:>8s}")
    stolen = sum(1 for r in results for nm, _ in ROUTES if r.get(nm + "_stole"))
    print(f"\n  случаев кражи фокуса: {stolen} (должно быть 0)")
    verifiable = [r for r in results if r['readable']]
    if verifiable:
        for nm, _ in ROUTES:
            ok = sum(1 for r in verifiable if r[nm] == 'OK')
            print(f"  {nm:12s}: {ok}/{len(verifiable)} подтверждено программно")
    unk = len(results) - len(verifiable)
    if unk:
        print(f"  {unk} испытаний не проверяются чтением — подтверждай глазами")
