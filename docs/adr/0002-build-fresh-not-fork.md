# ADR-0002: Build fresh, borrow patterns — do not fork

**Status:** Accepted · **Date:** 2026-07-19

## Context

Four MIT-licensed Windows dictation apps were cloned and read at source level to decide
whether BillyTalk should start as a fork.

A future paid tier makes licensing a hard filter, so the dependency trees were audited,
not just the top-level `LICENSE` files.

## License audit

| Project | License | Dependency tree | CLA | Verdict |
|---|---|---|---|---|
| opentypeless | MIT | clean (only `lightningcss` MPL-2.0, a build tool, never linked) | none | eligible |
| openless | MIT | clean | none | eligible |
| Handy | MIT | clean — the `rdev` fork is MIT, not AGPL | none | eligible |
| whisperi | MIT | fully permissive | none | eligible |
| VoiceTypr | **AGPL-3.0** | — | — | **excluded** |
| Whispering / Epicenter | **AGPL-3.0** | — | — | **excluded** |
| WhisperWriter | **GPL-3.0** | — | — | **excluded** |

Two model-weight hazards, noted for mode 3: Handy ships a checked-in Silero VAD `.onnx`
whose release provenance could not be confirmed, and openless downloads
SenseVoice-Small/Paraformer at runtime under unconfirmed commercial terms. **Model weights
are licensed separately from the code that loads them.**

## Decision

**Build fresh. Read the others as reference. Copy no code.**

## Why not fork

- **Whispering** — commit `2d0d3ed` (2026-07-10, *"retire standalone desktop runtime"*)
  deleted the desktop app entirely: `src-tauri/`, the release workflow, all Windows
  packaging. Current `main` has `"targets": ["app"]` — a macOS bundle. Forking a deleted
  build target is also a weak portfolio story. Plus AGPL.
- **Handy** — the most popular and best-governed option, and the wrong one. Its STT layer
  is a closed enum where each variant holds a live in-process model handle, matched
  exhaustively in four-plus places, built around file paths, quantisation and GPU device
  selection. There is **zero HTTP transcription**. You would keep the shell and rebuild
  the engine.
- **openless** — genuinely good code, and 72k lines of Rust with `polish` referenced 120
  times in one file, a 3,520-line TSF IME, Android, a marketplace, and zh-CN comments
  throughout.
- **opentypeless** — closest architectural fit, and eliminated with the stack decision
  ([ADR-0001](0001-ui-stack.md)): every candidate is Rust/Tauri. Also worth recording:
  one author under five git identities, no external contributions across 65 forks,
  a `VISION.md` stating the project was *"built in a single day with the help of Claude
  Code"*, and an accidentally-committed handoff file admitting *"Windows/Linux real-device
  smoke tests still need real-environment verification."*

**No project supports mouse buttons as a hotkey.** That work exists regardless of base,
so it cannot discriminate between candidates — and it means a clean, minimal, mouse-capable
Windows dictation tool is an open niche.

## What we borrow instead

Patterns, with attribution, reimplemented rather than copied:

| Source | What | Problem it solves |
|---|---|---|
| **whisperi** `clipboard/mod.rs` | secure-window-class detection, nine terminal families needing `Ctrl+Shift+V`, `SendInput` count verification | the best Windows insertion code found anywhere |
| **opentypeless** `windows_modifier_guard.rs` | poll `GetAsyncKeyState`, wait up to 500 ms for physical modifier release before injecting | injected characters inheriting a held modifier and firing as shortcuts — worse by definition in hold-to-talk |
| **openless** `hotkey.rs` | side-aware `WH_KEYBOARD_LL` (`VK_LCONTROL` vs `VK_RCONTROL`) | something `RegisterHotKey` cannot express |
| **openless** `insertion.rs` | compare actual vs expected typed characters, demote partial writes | reporting success on a failed paste |
| **Handy** `transcription_coordinator.rs` | push-to-talk state-machine simulator and its test names | the exact defect class this product exists to fix |
| **Handy** `input.rs` | paste via raw VK `0x56`, not the character `'v'` | paste surviving Cyrillic, AZERTY and DVORAK layouts |
| **Wispr Flow** (commercial) | unified keyboard/mouse keycode space, mouse offset `0x1000` | a solved, production-tested hotkey schema |

MIT requires retaining copyright notices in third-party attributions even in a closed
tier. None of these projects ships a `THIRD-PARTY-NOTICES` file; ours will be generated,
and a license gate belongs in CI from the start.

## Consequences

More work up front, no inherited baggage, no copyleft obligations, and full freedom over
the licensing of a future commercial tier. The reference reading is what makes the fresh
build cheap: the hard parts are already mapped, with file paths.

## What would reverse this

Nothing plausible mid-project. If a permissively-licensed Python/wx dictation app with
mouse-button support appears, re-evaluate.
