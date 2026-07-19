# BillyTalk

**Push-to-talk dictation for Windows.** Hold a key — or a mouse side button — speak,
and the text lands in whatever field your cursor was in.

> **Status: cycle 1 complete — the core dictates.** Hold Mouse 4 in any window, speak,
> release: the text appears. Verified live on real hardware (nine dictations end to end,
> including a ten-second clip; the log finished the session at zero lines, by design).
> 213 tests, zero skipped. Still no installer and no UI — that is cycle 2 (tray, overlay,
> settings) and cycle 3 (packaging); today it runs as `python -m billytalk.core`.
>
> Building it found **five defects in the specification that described it**, and an
> adversarial multi-lens review confirmed eleven more in the code — every one recorded
> with both readings in `docs/ru/spec/OPEN-QUESTIONS.md`, because that ledger is the
> most useful thing here.

> **How this was built.** I wrote the research, ADRs and specification here with Claude
> Code as a working partner; the commit trailers record it. Every empirical claim was
> produced by running something on this machine — the scripts in `spikes/` and `probes/`
> are what generated the numbers, and you can rerun them.

---

## Why another dictation app

There are good open-source dictation tools. None of them fit this shape:

| | BillyTalk *(planned)* | [Handy](https://github.com/cjpais/Handy) | [Whispering](https://github.com/EpicenterHQ/epicenter) | [OpenTypeless](https://github.com/tover0314-w/opentypeless) |
|---|---|---|---|---|
| Mouse buttons as hotkey | planned | no | no | no |
| Cloud STT | planned | local only | yes | yes |
| Windows desktop build | planned | yes | discontinued 2026-07 | yes |
| License | MIT | MIT | AGPL | MIT |

**None of the projects I surveyed supports mouse buttons for push-to-talk.** The library
ecosystem reflects it: [`tauri-plugin-global-shortcut`](https://v2.tauri.app/plugin/global-shortcut/)
and [`global-hotkey`](https://github.com/tauri-apps/global-hotkey) are keyboard-only, and
[`handy-keys`](https://github.com/handy-computer/handy-keys) — which *does* name
`MouseX1`/`MouseX2` — states plainly that pointer devices are never grabbed, so mouse
hotkeys are detected but cannot be suppressed.

That gap is the reason this project exists.

## Design goals

1. **Never lose text.** Every failure path ends with the transcript recoverable in one
   keystroke, not in a support ticket.
2. **Bring your own everything.** Your API key, your model, your machine — or a hosted
   free tier when you don't want to think about it.
3. **Accessible from v1.** The UI toolkit was chosen so that a future screen-reader
   release is an extension, not a rewrite.

## Roadmap

| | |
|---|---|
| **MVP-0** | One transcription mode (user's own Groq key). Hooks, state machine, insertion, dictionary, history. Console-driven, no UI. |
| **1.0** | Adds a free hosted tier and fully local transcription. Tray, settings, installer, five UI locales. |
| **2.0** | Text-to-speech; reading screen content aloud. |
| **3.0** | Sight-free dictation and correction, integrated with the screen reader the user already runs. |

Three transcription modes are planned behind one interface:

1. **Your own API key** — Groq `whisper-large-v3-turbo` *(MVP-0)*
2. **Hosted free tier** — target 5,000 words/week, no signup, no key *(1.0, not running yet)*
3. **Fully local** — model sized to your hardware, unlimited, nothing leaves the machine *(1.0)*

---

## What's interesting here

This repository documents the design phase in full, including the parts that went wrong —
and the parts that were only found by writing the code.

### Decisions that measurement reversed

**A native input helper was designed, then cancelled.** A platform review argued that a
pure-Python `ctypes` low-level mouse hook was unviable — a high-polling-rate mouse would
fire up to 1,000 callbacks per second, each acquiring the GIL, permanently, inside the
system input path. The argument was plausible and the decision was made to move both hooks
into a small native process.

Then it was measured on the actual hardware:

```
events over 90 s   : 11,639   (129/s average, ~460/s at peak)
callback p50       : 1.7 µs
callback p99       : 14.6 µs
callback MAXIMUM   : 497 µs        ← budget is 1,000,000 µs
CPU                : 0.03% of one core
```

**A 2,000× margin.** The native component was cancelled. ([ADR-0004](docs/adr/0004-keep-hooks-in-python.md))

**FLAC was nearly dropped on three noisy samples.** An early benchmark showed WAV beating
FLAC by 183 ms despite being twice the size. Re-run as 8 interleaved pairs to cancel
network drift, FLAC won by 39 ms. The first result was noise. Three measurements are not
a measurement.

### A recurring Windows trap

Three times in one day, in three different APIs, a success return value did not mean the
action happened:

- `SetWindowsHookExW` returned a valid handle and `GetLastError` was 0 — and the hook
  received **zero events**, because the thread used a `PeekMessage` spin loop instead of
  blocking `GetMessage`.
- `SendInput` returned 1 for a zero-delta mouse move — the event was silently discarded.
- `SendInput` can report a successful paste that never lands. Whispering has
  [an open issue](https://github.com/EpicenterHQ/epicenter/issues/841) tracking this since
  2025, where users report losing a fraction of pastes — a good illustration of how hard
  this failure is to catch, since the API says it worked.

**Project rule: in Windows input APIs, a success code is not evidence. Verify the
observable effect.**

### Learning from a competitor's logs

I was a paying Wispr Flow user with two complaints: dictation cutting off, and text
landing in the wrong window. Reading that app's own logs found both root causes.

> **Scope of that analysis.** On my own machine, on my own data, I read what the app
> writes to disk — file layout, config schema, database schema, and its own log output.
> I did not read transcript contents, token values, or DPAPI-protected data, did not
> intercept traffic, and did not modify the installation. Observations about how that
> product protects user data are deliberately **not** published here; they belong to the
> vendor first.

**Cut-off dictation is a startup race, not a timeout:**

```
[warn] [Keyboard Service] Release event while dictation is in Initialized state,
       dismissing dictation.
```

234 "starting dictation" against 244 "stopping dictation" — ten stops with no matching
start. The button release arrives while the state machine is still initialising and is
treated as an abort. A bare mouse button is the worst case, because a click has no
modifier-hold latency to mask the initialisation window.

BillyTalk queues the release instead, and the balance between captures opened and
captures closed is a property test — checkable over random event sequences with no
hardware at all.

### The specification was wrong, and only the code found out

Building that state machine surfaced four defects in the document that specified it.
The worst is the same class of bug as the competitor's, one layer deeper: hold the button
while an earlier dictation is still delivering, and the press queues. Let go, and the
spec said to ignore the release — so by the time that press is popped the button is
already up, no second release will ever arrive, and the recording runs to the five-minute
fuse with the button dead in your hand.

**Nothing catches it.** The capture ledger stays perfectly balanced. It is invisible to
every invariant and shows up only as a user holding a button that does nothing.

And the headline invariant itself was wrong. Written literally,
`count(StartCapture) == count(StopCapture)` is *unsatisfiable by a correct machine*,
because cancelling a dictation closes its capture with a different effect. The real
property is a balance — every capture opened is closed exactly once, by exactly one
closer — and the literal equality now belongs to a narrower test over sequences that
contain no cancellations.

**Wrong-window insertion is a skipped focus restore.** The competitor captures the focused
element reliably at dictation start, then declines to return to it before pasting.

## Documentation

| | |
|---|---|
| [Architecture Decision Records](docs/adr/) | English. Every significant decision, with the reasoning and what would reverse it |
| [Design documents](docs/ru/) | Russian. Full research, competitor analysis, specification, spike results |
| [`spikes/`](spikes/) | Standalone measurement scripts. No dependencies beyond the stack; run them yourself |
| [`probes/`](probes/) | Hardware diagnostics — what your mouse actually reports to Windows |

If you have a gaming mouse and are curious what its side buttons emit,
`probes/billytalk_mouse_probe.py` is pure `ctypes` and tells you in about ten seconds.

## Stack

Python 3.14 · wxPython · `sounddevice` · `soundfile` · raw `ctypes` for hooks, tray and
overlay · SQLite · PyInstaller · NSIS

Every dependency was verified by installing and running it, not by reading a compatibility
table. wxPython was chosen for one specific reason: **NVDA, the world's most-used free
screen reader, is itself a wxPython application** — accessibility is inherited from native
Win32 controls rather than reimplemented. Tauri was ruled out because NVDA treats a
WebView2 window as a web document and drops into browse mode, which would sabotage the 3.0
accessibility release. ([ADR-0001](docs/adr/0001-ui-stack.md))

## License

MIT — see [LICENSE](LICENSE).

## Acknowledgements

This design borrows openly from work done in public:

- **[Wispr Flow](https://wisprflow.ai/)** — the commercial product this replaces. Its
  unified keyboard/mouse keycode space (mouse buttons offset at `0x1000`) is a solved
  design worth copying rather than reinventing.
- **[NVDA](https://github.com/nvaccess/nvda)** — `SynthDriver` and the Input Gestures
  dialog are the reference for speech abstraction and for capturing a hotkey while a
  screen reader is consuming the same chords.
- **whisperi** (MIT), **[OpenTypeless](https://github.com/tover0314-w/opentypeless)**,
  **[openless](https://github.com/Open-Less/openless)**, **[Handy](https://github.com/cjpais/Handy)** —
  read for their Windows insertion, held-modifier guard, side-aware hooks, and
  push-to-talk state-machine test harness respectively. Patterns studied, code not copied.
  All four are single- or small-team projects doing careful work; the notes in
  [ADR-0002](docs/adr/0002-build-fresh-not-fork.md) are about fit, not quality.
