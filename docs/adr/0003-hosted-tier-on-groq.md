# ADR-0003: The hosted free tier proxies Groq — no GPU rental

**Status:** Accepted · **Date:** 2026-07-19

## Context

Mode 2 is a free hosted tier: 5,000 words per week, no signup, no API key. The original
plan was to rent a GPU and run inference ourselves, both to control cost and to be able
to promise that audio never reaches a third party.

## Decision

**Mode 2 launches as a thin proxy holding our own Groq key, with per-device quota
accounting. No rented GPU.** Migration to owned hardware later is one implementation
behind `TranscriptionProvider`, not a rewrite.

## The economics

5,000 words/week at a normal dictation rate of ~140 wpm is ≈36 minutes of audio per week,
**≈2.6 hours per active user per month**. Groq `whisper-large-v3-turbo` is $0.04/audio-hour:

| Active users | Audio-hours/month | Groq cost |
|---|---|---|
| 100 | 260 | **$10** |
| 1,000 | 2,600 | **$104** |
| 2,000 | 5,200 | $208 |
| 10,000 | 26,000 | $1,040 |

**Break-even against owned hardware is ~5,000 audio-hours/month — roughly 2,000 active
users.** Below that, a single Hetzner GEX44 at ~$200/month costs more than Groq charges to
serve the first thousand users.

Serverless GPU does not rescue this. The problem is not cold start but billing
granularity: a 12-second clip needs ~0.15 s of compute and bills 0.7–1.5 s, landing at or
above Groq's price while we own the operations.

And below the crossover, **one engineer-hour per week erases the entire saving** — on-call,
driver upgrades, model pinning, queue backpressure, burst planning.

## Latency, measured

19.4 seconds of speech, from the developer's machine in Brazil:

```
warm pooled connection : 368 ms
cold connection        : 623 ms
connection setup       : dns 121 ms + tcp 367 ms + tls 540 ms cumulative
```

Groq is also *faster* than a self-hosted L4 would realistically sustain (80–120× realtime
against Groq's 216×). At launch scale, owning hardware would be slower **and** more
expensive.

**Keeping a warm connection pool is mandatory, not an optimisation** — it saves 255 ms on
every dictation.

## An aggregator was evaluated and rejected

AIMLAPI was tested as a way to reach Groq without a direct account:

| | |
|---|---|
| Groq availability | **not offered under any model id** |
| Architecture | `POST /v1/stt/create` → poll `GET /v1/stt/{id}` — **an async job queue** |
| `deepgram/nova-3` | **5,638 ms** median |
| `deepgram/whisper-large` | **9,615 ms** median |
| Best-model price | $0.26–0.46/hour vs $0.04 |
| Privacy | an **additional third party** in the audio path |

Against a 500–800 ms budget, 5.6–9.6 seconds fails on every axis at once. Viable as a
fallback provider behind the interface; not as the basis of mode 2.

## Honest consequence

Research on this category found that self-hosted inference sells on **privacy and control,
not price**. While we proxy Groq, **we do not have that argument** and must not make it.

Mode 2's UI will state plainly that transcription runs through a third-party provider.
The privacy claim arrives with owned hardware, not before. Users who want it today have
mode 3 — fully local, nothing leaves the machine. That is what makes three modes a
coherent set rather than three ways to do the same thing.

## Token model

One global Groq key on the server; per-user tokens issued individually.

```
client ──[short-lived user token]──► our proxy ──[global Groq key]──► Groq
                                          └── accounting, quota, caps, revocation
```

- **Access token: 15-minute TTL, memory only.** A leaked device fingerprint cannot mean an
  uncapped compute bill; a compromised token expires on its own.
- Refresh token in Windows Credential Manager, rotated on every use.
- **Quota binds to the device, not the token** — otherwise minting tokens multiplies the
  allowance.
- Device fingerprint **excludes disk serials**, so an SSD swap does not reset a user.
- Metering is **by audio-seconds**, displayed to users in words. `billed_seconds` is in
  `TranscriptionResult` from day one so this needs no schema migration.
- Overflow **queues rather than 429s** — materially better for dictation.
- Zero retention server-side: no audio, no transcripts, request ids only.

Because enforcement lives on the server, a cracked client gains nothing. Client-side
licensing code therefore optimises for **honest users' experience** — graceful
degradation, clear expiry messaging, one-click device reset — not for resisting cracks.

## Local mode is offered at 2,000 words

At 40% of the weekly quota, users are offered mode 3 — before they hit the wall, and
framed as their benefit (unlimited, offline, audio never leaves the machine), not our cost
saving. **An offer, never a block or a degradation**, shown once, and suppressed entirely
if a hardware check says the machine cannot run a local model well.

## What would reverse this

Sustained volume above ~5,000 audio-hours/month; privacy becoming a demanded selling
point; or Groq raising prices or restricting access.
