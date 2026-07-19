# ADR-0004: Keep the input hooks in Python — no native helper

**Status:** Accepted · **Date:** 2026-07-19
**Supersedes:** an earlier decision, made and never implemented, to move both hooks into
a separate native process.

## Context

A platform review argued that a pure-Python `ctypes` low-level mouse hook was not viable:

> The callback fires on every mouse move. `WH_MOUSE_LL` delivers `WM_MOUSEMOVE`. The
> customer's mouse is a high-polling-rate device — up to 1000 callbacks/sec, each one a
> ctypes closure invocation that must acquire the GIL, run Python bytecode, and return.
> That is a permanent GIL-acquisition point in the whole system's input path.

The proposed fix was a small native process (C or Rust, ~200 lines) owning both hooks and
its own message pump, talking to Python over a named pipe.

The reasoning is sound in shape, the source was credible, and the decision was
provisionally accepted.

**Then it was measured.**

## Measurement

Real hardware, 90 seconds of continuous mouse movement. Razer DeathAdder V2 X HyperSpeed,
Windows 11 26200, Python 3.14.6, `WH_MOUSE_LL` via `ctypes`.

```
events over 90 s   : 11,639   (11,547 moves, 92 buttons/wheel)
average rate       : 129 events/s
peak (5 s window)  : ~460 events/s
callback mean      : 2.5 µs
callback p50       : 1.7 µs
callback p99       : 14.6 µs
callback MAXIMUM   : 497 µs
CPU                : 0.03% of one core
```

`LowLevelHooksTimeout` is 1,000,000 µs. The worst observed callback used **0.05% of the
budget — a 2,000× margin.**

The predicted rate was wrong by an order of magnitude: 129/s, not 1000/s. A mouse's
polling rate and the rate at which the system dispatches low-level hook events are
different quantities; Windows does not hand the hook every hardware report.

Script: [`spikes/s0c_hook_getmsg.py`](../../spikes/s0c_hook_getmsg.py).

## Decision

**The hooks stay in Python, in the core process.** The native helper is cancelled —
saving an entire component, a native toolchain in the build, a third IPC boundary, and
maintenance across two languages.

## Required mitigations

The margin is large but the tail was not exercised — a gen-2 GC pass over a large heap,
or a module import under load, were not reproduced. Therefore:

1. **The callback only enqueues.** The single synchronous decision inside it is
   suppress-or-not, taken from a lock-free snapshot of state.
2. **`gc.freeze()` after initialisation**, moving startup objects to the permanent
   generation and out of collection passes.
3. **PyInstaller `--onedir`, never `--onefile`.**
4. **The liveness watchdog stays.** Silent unhooking is undetectable from inside
   regardless of headroom.

## A trap found by the same spike

**A `PeekMessage` spin loop receives no hook callbacks at all.**

```python
while running:
    while user32.PeekMessageW(byref(msg), None, 0, 0, PM_REMOVE):
        ...
    time.sleep(0.002)
```

Result: **zero events over 30 seconds of active mouse movement**, while
`SetWindowsHookExW` returned a valid handle and `GetLastError` was 0. Everything looks
correct and nothing arrives.

Only a **blocking `GetMessage`** works, exited via
`PostThreadMessageW(tid, WM_QUIT, 0, 0)` from another thread.

This is now a documented implementation requirement. Debugging "hook installed, no
events" without knowing it costs hours: there is no error, the handle is valid, and the
documentation is silent.

## Process note

A plausible technical argument from a credible source was wrong in its central numeric
claim, and would have added a whole native component to the project. One hour of
measurement reversed it.

The same spike round also nearly reversed a *correct* decision: an early 3-run benchmark
suggested WAV beat FLAC by 183 ms. Re-run as 8 interleaved pairs, FLAC won by 39 ms — the
first result was noise. **Three measurements are not a measurement; compare by
interleaving.**

## What would reverse this

A reproducible callback exceeding ~100 ms under realistic load (large heap, GC pressure,
cold imports), or field reports of silent unhooking that the watchdog cannot recover from.
