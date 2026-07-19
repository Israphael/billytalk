# Architecture Decision Records

Each record states a decision, the reasoning behind it, the consequences accepted, and
**what evidence would reverse it**. That last section is the point: a decision you cannot
describe how to falsify is a preference, not a decision.

| # | Decision | Status |
|---|---|---|
| [0001](0001-ui-stack.md) | Python + wxPython, core split from UI. Not Tauri | Accepted |
| [0002](0002-build-fresh-not-fork.md) | Build fresh, borrow patterns, do not fork | Accepted |
| [0003](0003-hosted-tier-on-groq.md) | Hosted tier proxies Groq; no GPU rental | Accepted |
| [0004](0004-keep-hooks-in-python.md) | Hooks stay in Python — supersedes a native helper | Accepted |
| [0005](0005-v3-scope.md) | v3.0 is a free NVDA add-on for error detection, not a product | Accepted |

## Two of these were reversed by measurement

ADR-0004 exists because a credible platform review argued a pure-Python input hook was
unviable, that argument was provisionally accepted, and then one hour of measurement
showed it was wrong by an order of magnitude. The native component it called for was
cancelled before a line of it was written.

The same spike round nearly reversed a *correct* decision in the other direction: three
benchmark runs suggested dropping FLAC for WAV. Eight interleaved pairs showed the
opposite. Three measurements are not a measurement.

Both are recorded rather than quietly corrected, because the reversals are more
informative than the decisions.

## Full research

These records are summaries. The underlying work — competitor forensics, platform
research, hardware probes, spike results — is in [`../ru/`](../ru/) (Russian) and
[`../../spikes/`](../../spikes/) (runnable scripts).
