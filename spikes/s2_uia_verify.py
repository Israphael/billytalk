"""Spike S2: is there a reliable signal that a paste actually landed?

Budgeted at three hours (harness §10); a failure does not block — the
"stay silent but always leave an escape hatch" policy is already accepted.

Method: spawn our OWN Notepad windows (never touching the user's), paste with
the product's real `Inserter` machinery, and read the document back through
UIA `TextPattern` — the explicit lesson of spike S7: **never `WM_GETTEXT`**,
which answers nothing in the new Notepad (XAML/Direct2D), Word or Chromium.

Measured on the side, because the polling loop yields it for free: the delay
from `SendInput` to the marker appearing in the document — the number
`RESTORE_DELAY_MS` currently guesses at.

Then the S7 leftover: with the target in the BACKGROUND, which of the three
delivery routes actually lands — `WM_PASTE`, `WM_CHAR`, or a posted Ctrl+V —
judged by the same UIA read instead of the customer's eyeballs.

Safety: the synthetic Ctrl+V is sent only after verifying our notepad still
owns the foreground; any round where the user grabbed focus is skipped, not
guessed. The user's clipboard is snapshotted by the product's own sequence
guard and restored at the end.
"""

from __future__ import annotations

import ctypes as ct
import subprocess
import sys
import time
import uuid
from ctypes import wintypes

sys.path.insert(0, r"C:\BillyTalk")

import comtypes  # noqa: E402
import comtypes.client  # noqa: E402

from billytalk.core.insert.apprules import PasteChord  # noqa: E402
from billytalk.core.insert.clipboard import Clipboard  # noqa: E402
from billytalk.core.insert.inserter import send_paste_chord  # noqa: E402

_user32 = ct.WinDLL("user32", use_last_error=True)
_user32.GetForegroundWindow.restype = wintypes.HWND
_user32.SetForegroundWindow.argtypes = (wintypes.HWND,)
_user32.PostMessageW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
_user32.FindWindowExW.restype = wintypes.HWND
_user32.FindWindowExW.argtypes = (wintypes.HWND, wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR)

WM_CHAR = 0x0102
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_PASTE = 0x0302
VK_CONTROL = 0x11
VK_MENU = 0x12
VK_V = 0x56
VK_A = 0x41
VK_DELETE = 0x2E
KEYEVENTF_KEYUP = 0x0002
SW_MINIMIZE = 6
SW_RESTORE = 9

ROUNDS = 8  # the project rule: comparisons need eight, not three


def _tap(vk: int) -> None:
    _user32.keybd_event(vk, 0, 0, 0)
    _user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)


def _force_foreground(hwnd: int) -> bool:
    """Bring OUR spawned window forward despite the background-credit rule.

    This is the S1 defect deliberately worked around — in the SPIKE only, never
    the product: an Alt tap hands this thread the foreground-change right, and a
    minimise/restore cycle is the belt-and-braces. The product treats a failed
    SetForegroundWindow as a normal outcome; the spike must force it so the
    verification signal can be measured at all.
    """
    for _ in range(3):
        if int(_user32.GetForegroundWindow() or 0) == hwnd:
            return True
        _tap(VK_MENU)
        _user32.ShowWindow(hwnd, SW_MINIMIZE)
        _user32.ShowWindow(hwnd, SW_RESTORE)
        _user32.SetForegroundWindow(hwnd)
        time.sleep(0.12)
    return int(_user32.GetForegroundWindow() or 0) == hwnd


def _clear_document() -> None:
    """Win11 Notepad restores the previous session; start each run empty."""
    _tap(VK_A) if False else None
    _user32.keybd_event(VK_CONTROL, 0, 0, 0)
    _tap(VK_A)
    _user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)
    _tap(VK_DELETE)
    time.sleep(0.1)


def _notepad_windows() -> set[int]:
    found: set[int] = set()

    @ct.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def enum(h: int, _l: int) -> bool:
        if _user32.IsWindowVisible(h):
            buf = ct.create_unicode_buffer(64)
            _user32.GetClassNameW(h, buf, 64)
            if buf.value == "Notepad":
                found.add(h)
        return True

    _user32.EnumWindows(enum, 0)
    return found


def _spawn_notepad() -> int:
    """Returns the hwnd of a freshly spawned Notepad.

    Win11's notepad.exe is a repackaged app: the launched pid is a broker that
    exits while the real window belongs to another process — so the window is
    identified as "a Notepad window that did not exist before launch", and it
    is closed later by resolving the pid from the hwnd, not from Popen.
    """
    before = _notepad_windows()
    subprocess.Popen(["notepad.exe"])
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        fresh = _notepad_windows() - before
        if fresh:
            return fresh.pop()
        time.sleep(0.05)
    raise RuntimeError("notepad window did not appear")


def _kill_window_process(hwnd: int) -> None:
    pid = wintypes.DWORD()
    _user32.GetWindowThreadProcessId(hwnd, ct.byref(pid))
    if pid.value:
        subprocess.run(
            ["taskkill", "/PID", str(pid.value), "/F"],
            capture_output=True, check=False,
        )


class UiaReader:
    """The candidate verification signal: TextPattern over the document."""

    def __init__(self) -> None:
        comtypes.client.GetModule("UIAutomationCore.dll")
        from comtypes.gen import UIAutomationClient as UIA

        self.UIA = UIA
        self.uia = comtypes.client.CreateObject(
            UIA.CUIAutomation, interface=UIA.IUIAutomation
        )

    def document_text(self, hwnd: int) -> str | None:
        """The document's full text, or None if the signal is unavailable —
        None is itself an S2 result (verify_impossible), never an exception."""
        UIA = self.UIA
        try:
            root = self.uia.ElementFromHandle(hwnd)
            condition = self.uia.CreatePropertyCondition(
                UIA.UIA_ControlTypePropertyId, UIA.UIA_DocumentControlTypeId
            )
            doc = root.FindFirst(UIA.TreeScope_Descendants, condition)
            if doc is None:
                condition = self.uia.CreatePropertyCondition(
                    UIA.UIA_ControlTypePropertyId, UIA.UIA_EditControlTypeId
                )
                doc = root.FindFirst(UIA.TreeScope_Descendants, condition)
            if doc is None:
                return None
            pattern = doc.GetCurrentPattern(UIA.UIA_TextPatternId)
            if not pattern:
                return None
            text_pattern = pattern.QueryInterface(UIA.IUIAutomationTextPattern)
            return text_pattern.DocumentRange.GetText(-1)
        except (OSError, comtypes.COMError):
            return None

    def wait_for(self, hwnd: int, marker: str, *, timeout_s: float = 2.0) -> float | None:
        """Poll until the marker is visible; the latency IS the measurement."""
        started = time.perf_counter()
        while time.perf_counter() - started < timeout_s:
            text = self.document_text(hwnd)
            if text is not None and marker in text:
                return (time.perf_counter() - started) * 1000
            time.sleep(0.005)
        return None


def main() -> None:
    reader = UiaReader()
    clipboard = Clipboard()
    results: list[float] = []
    skipped = 0

    print("== S2: foreground paste, verified by UIA ==")
    hwnd = _spawn_notepad()
    snapshot = None
    try:
        time.sleep(0.5)  # let it settle and take foreground
        _force_foreground(hwnd)
        _clear_document()
        baseline = reader.document_text(hwnd)
        print(f"UIA TextPattern readable: {baseline is not None} "
              f"(cleared doc reads {baseline!r}) — S7 used WM_GETTEXT, which never was")

        for i in range(ROUNDS):
            marker = f"BTS2{i}{uuid.uuid4().hex[:8]}"
            snapshot = clipboard.write(marker + " ")
            if not _force_foreground(hwnd):
                skipped += 1  # cannot bring it forward: never paste blind
                continue
            send_paste_chord(PasteChord.CTRL_V)
            latency = reader.wait_for(hwnd, marker)
            if latency is None:
                print(f"round {i}: marker NOT seen within 2 s (paste did not verify)")
            else:
                results.append(latency)
                print(f"round {i}: verified visible after {latency:.0f} ms")
        if results:
            results.sort()
            mid = results[len(results) // 2]
            print(f"VERIFIED {len(results)}/{ROUNDS}, skipped {skipped}: "
                  f"visible-after median {mid:.0f} ms, min {results[0]:.0f}, max {results[-1]:.0f}")
        else:
            print(f"nothing verified; skipped {skipped}")

        # ---- background routes (S7 leftover), judged by the same signal ----
        print("== background delivery into an unfocused Notepad ==")
        edit = _user32.FindWindowExW(hwnd, None, "RichEditD2DPT", None) or hwnd
        hwnd2 = _spawn_notepad()  # takes the foreground away
        try:
            time.sleep(0.4)
            for name, send in (
                ("WM_PASTE", lambda m: (
                    clipboard.write(m + " "),
                    _user32.PostMessageW(edit, WM_PASTE, 0, 0),
                )),
                ("WM_CHAR", lambda m: [
                    _user32.PostMessageW(edit, WM_CHAR, ord(ch), 0) for ch in m
                ]),
                ("posted Ctrl+V", lambda m: (
                    clipboard.write(m + " "),
                    _user32.PostMessageW(edit, WM_KEYDOWN, VK_CONTROL, 0),
                    _user32.PostMessageW(edit, WM_KEYDOWN, VK_V, 0),
                    _user32.PostMessageW(edit, WM_KEYUP, VK_V, 0),
                    _user32.PostMessageW(edit, WM_KEYUP, VK_CONTROL, 0),
                )),
            ):
                marker = f"BG-{name.replace(' ', '')}-{uuid.uuid4().hex[:6]}"
                send(marker)
                latency = reader.wait_for(hwnd, marker, timeout_s=1.5)
                stole = int(_user32.GetForegroundWindow() or 0) == hwnd
                verdict = f"landed in {latency:.0f} ms" if latency is not None else "did NOT land"
                print(f"{name:14s}: {verdict}; stole focus: {stole}")
        finally:
            _kill_window_process(hwnd2)
    finally:
        _kill_window_process(hwnd)
        restored = clipboard.restore(snapshot)
        print(f"user clipboard restore attempted: {restored} "
              "(False = someone wrote after us; their copy wins)")


if __name__ == "__main__":
    main()
