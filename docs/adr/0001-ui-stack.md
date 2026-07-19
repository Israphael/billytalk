# ADR-0001: Python + wxPython, with the core split from the UI

**Status:** Accepted · **Date:** 2026-07-19

## Context

BillyTalk is a Windows tray app whose roadmap ends at v3.0 — accessibility tooling for
blind and low-vision users. The natural v1 choice for a small cross-platform desktop app
in 2026 is Tauri: small binaries, good tooling, and it is what every comparable
open-source dictation app uses (Handy, Whispering, OpenTypeless, openless).

The UI toolkit is the hardest decision to reverse later, so it was evaluated against v3.0
rather than v1.

## Decision

**Python 3.14 with wxPython for the UI, and a headless core in a separate process,
connected over local JSON-RPC.**

## Why not Tauri

The common claim that "Tauri is inaccessible" traces to
[WebView2Feedback#2330](https://github.com/MicrosoftEdge/WebView2Feedback/issues/2330),
which was **closed as completed in November 2024**. Citing it today is citing a fixed bug.
Tauri was ruled out for different, current reasons:

1. **NVDA treats any WebView2 window as a web document** and drops into browse mode.
   Users must press NVDA+Space before every text entry. The fix is a per-app module
   written by NV Access — VS Code and Skype have hand-written ones. Native UIA apps never
   have this conversation. ([NVDA#19655](https://github.com/nvaccess/nvda/issues/19655))
2. **Tauri does not use AccessKit.** Verified: zero AccessKit dependency in `tao` and
   `wry` on `dev`, including the Windows `platform_impl` tree. Accessibility is delegated
   entirely to the user's Edge runtime; anything outside the webview gets Win32 defaults.
3. **The runtime is not ours.** [NVDA#19329](https://github.com/nvaccess/nvda/issues/19329),
   open since December 2025: `aria-live` regions are not announced in WebView2, while
   "this example worked in Edge and Chrome." For a dictation app `aria-live` *is* the
   status channel — listening, transcribing, inserted. Pinning a version cannot fix it.

## Why wxPython specifically

**NVDA — the world's most-used free screen reader, built by and for blind people — is a
wxPython application.** Its `source/gui/__init__.py` contains `import wx`,
`import wx.adv`, and `class SysTrayIcon(wx.adv.TaskBarIcon)`.

Two consequences follow:

- wx wraps native Win32 controls, so accessibility is **inherited, not implemented**.
- The hotkey-capture dialog can be modelled directly on NVDA's `inputGestures.py`, which
  solves exactly our problem: letting a user assign a chord while a screen reader is
  consuming those same chords. Its approach is an explicit, modal, bounded capture mode
  in which the screen reader deliberately stands down.

Alternatives considered: WinForms/WPF (excellent accessibility, wrong language for our
core), Qt/PySide6 (real UIA bridge, workable, with a tail of NVDA-side quirks),
**WinUI 3 — rejected**, Microsoft's newest stack is its least accessible,
**tkinter — rejected**, no screen reader support before Tk 9.1,
**Flutter — rejected**, text field characters are not read when arrowing, which is fatal
for a dictation app.

If a web UI ever becomes necessary, the answer is **Electron, not Tauri**: Chromium is
pinned by us, accessibility is Chrome-grade, and the `aria-live` gap does not exist.

## Why Python

- **Local transcription (mode 3) lives in Python.** `faster-whisper` / `ctranslate2` are
  native here. In Rust we would be duplicating an ecosystem for the same result.
- **The projects that chose Rust did so for a constraint we do not have.** Handy,
  Whispering and openless all run local inference as the primary mode — compute-bound work
  that must sit next to native ML runtimes. Our primary mode is a thin HTTP client. The
  one project with our architecture, WhisperWriter, is Python.

Verified by installing and running, not by reading a compatibility table: wxPython 4.2.5,
sounddevice 0.5.5, soundfile 0.14.0, pywin32 312, numpy 2.5.1, PyInstaller 6.21 all work
on Python 3.14 / win_amd64. In-process FLAC encoding of a 19.4 s clip takes **9 ms**.

## Why split the core from the UI

One day of work, and it buys the entire v3.0:

- The core has no accessibility surface, so it cannot sabotage one.
- The UI is replaceable without touching dictation logic.
- The core is testable with no display.
- **The hook thread runs its own message pump**, as Windows requires, without competing
  with the UI event loop.

That last point is survival, not style: if a low-level hook callback exceeds
`LowLevelHooksTimeout`, **Windows silently removes the hook and never tells the
application**. Dictation dies mid-session.

## Consequences

Accepted costs:

- Distribution is ~40–60 MB rather than Tauri's ~8 MB.
- PyInstaller bundles attract antivirus false positives more than native builds.
- **PyInstaller must run in `--onedir` mode.** Onefile extracts the whole bundle to
  `%TEMP%` on every launch, which both blows the Windows startup-impact budget and
  triggers module imports holding the GIL while the hook is live.

None of these touch the primary function or block the roadmap.

## What would reverse this

Evidence that wxPython cannot deliver a specific accessibility requirement that Qt or
WinForms can, or a Python-level constraint in the input path that measurement confirms
(see [ADR-0004](0004-keep-hooks-in-python.md), where exactly that claim was made and
disproved).
