# ADR-0005: v3.0 is "making dictation errors audible" — a free NVDA add-on

**Status:** Accepted · **Date:** 2026-07-19

## Context

The roadmap has always ended at v3.0, described as **"an accessibility product for blind
users."** [ADR-0001](0001-ui-stack.md) rejected Tauri largely on that basis — the UI
toolkit was evaluated against v3.0 rather than v1, because it is the hardest decision to
reverse.

That scope has now been researched properly. The evidence is in
[`../ru/research/09-версия-3-разведка.md`](../ru/research/09-версия-3-разведка.md);
this record states what it changes.

The prior thesis was flagged in an earlier pass as **the worst-sourced section of the
whole project**: it rested on one paper — [arXiv 2410.22324](https://arxiv.org/abs/2410.22324),
12 blind participants, **mobile** input, no physical keyboard — plus a blog post. As a
basis for a Windows product that is not enough, and the sceptical question was exactly
that.

### The load-bearing study, which the earlier pass missed

> **Jonggi Hong, Christine Vaing, Hernisa Kacorri, Leah Findlater.
> "Reviewing Speech Input with Audio."**
> ACM Transactions on Accessible Computing, 2020. [DOI 10.1145/3382039](https://doi.org/10.1145/3382039).
> Lab study, **12 blind and 12 sighted participants** — the paired design is the point.
>
> **Blind participants identified only 40% of errors** when reviewing transcribed text by
> audio, showing **no significant difference from sighted participants** doing the same —
> contrary to the researchers' expectations.
>
> Their own conclusion: *"the need for future work on how to support blind users in
> confidently using speech input to generate accurate, error-free text."*

The paired design matters more than the headline number. Had only blind participants been
tested, 40% would say little. Testing sighted people on the *same* audio-only review is
what isolates the modality from the disability — and the authors expected a gap that did
not appear.

Two things follow, and both invalidate the original scope.

**The bottleneck is error *detection*, not error correction.** A blind Windows user with
a full keyboard and a review cursor fixes a word cheaply — *once they know it is wrong*.
They do not know about 60% of them. A correction tool solves the half that is not broken.

**Sighted listeners are equally bad at hearing errors.** They simply switch to their
eyes. Blind users have no second modality. That, not the mechanics of editing, is the
real structural asymmetry — and it means the problem is a property of sequential
listening, not of touchscreens.

The earlier pass missed this because it searched arXiv. Accessibility literature lives in
ACM TACCESS and ASSETS, which arXiv does not index. The field was not under-researched;
it was under-searched.

## Decision

**v3.0 is re-scoped from "an accessibility product for blind users" to "making dictation
errors audible": a free, open-source NVDA add-on, distributed through the official
add-on store.**

It is **not a paid product** and **not a competitor to NVDA or JAWS**. It complements the
screen reader the user already has.

Functionally: read the finished result back as a block, mark the spans where the model
was uncertain *by voice*, and offer alternatives behind a single NVDA gesture.

This is narrow, defensible on craft, complementary rather than competitive, and it grows
naturally out of v1.0 — same providers, same audio capture, same vocabulary.

## Why this is not a business

Not because the product is bad. Because of how the market is shaped.

**[WebAIM Screen Reader Survey #10](https://webaim.org/projects/screenreadersurvey10/)**
(n=1539), on why users pick a screen reader:

| Reason | Share |
|---|---|
| **Existing comfort and expertise** | **45.8%** |
| Features | 26.5% |
| Availability | 11.7% |
| **Cost** | **9.2%** |
| Support | 6.9% |

**This market does not switch on features.** Nearly half is pure muscle memory. And
**78.1% consider free screen readers a viable alternative.**

**The structural proof on willingness to pay:** NV Access — the organisation behind the
most-satisfying product in the category — is a **charity**, living on grants and
donations, whose entire commercial catalogue is training material and certification.

**No precedent exists for a paid NVDA feature add-on.** The only commercially successful
paid add-ons wrap **licensed TTS voices** — third-party IP that cannot be cloned for
free. A feature can be cloned.

**OrCam is a warning, not an analogy.** Its vision and reading division closed in **July
2024**, with the company itself stating that progress in language models made further
development of low-vision products unnecessary. Headcount went from 400+ to 10–49. A
company with a hardware moat, licences and institutional channels was gone in two years.

## Why the niche is nevertheless real

| | |
|---|---|
| **DictationBridge** (the direct predecessor) | dead since 2020 — last release v1.1, 43 commits, domain parked |
| **Its function** | absorbed by **NVDA 2025.2** ([#17384](https://github.com/nvaccess/nvda/issues/17384), [#16862](https://github.com/nvaccess/nvda/issues/16862)) |
| **Dictation in the NVDA add-on store** | **none at all** |
| **NVDA itself** | will not build it — [#12489](https://github.com/nvaccess/nvda/issues/12489) closed at priority **P4** |
| **AI-Hub** ([nvda-OpenAI](https://github.com/aaclause/nvda-OpenAI)) | alive, June 2026. Whisper already wired into NVDA — **and no error-handling workflow whatsoever** |

The asymmetry is the strategic signal: AI on the **output** side for blind users is
saturated (AI Content Describer: 68 stars, 39 forks, 18 releases, five model families).
AI on the **input** side for the same people is nearly empty. Same audience, same screen
readers, same APIs, an order of magnitude less activity.

**ASR error correction is a crowded ML field** (ED-CEC, Generative ASR Error Correction,
ICASSP 2025) and **not one paper targets blind users.** The model layer is commoditising;
the interaction layer — how to convey residual uncertainty *by ear* — is untouched.

**But the window is narrow.** AI-Hub is one motivated maintainer decision away from
closing it.

## Consequences that land in v1.0 now, not v3.0

This is the actionable part. Three requirements move forward.

**1. Route the synthesiser's output and the microphone's input to different devices.**

Three independent sources found the same problem: an NVDA + Word user in Chile reporting
that NVDA's verbosity contaminates dictated text; an NVDA user whose screen reader bleeds
into an outgoing Zoom feed; and `sight-free-talon` issue #41, where the tool's own TTS
output is picked up and interpreted as voice commands
(see [`../ru/research/11-голосовые-командные-центры.md`](../ru/research/11-голосовые-командные-центры.md)).

Output-device selection is already in the core. We now know what it is actually for:
**for a user with a screen reader, the microphone hears the synthesiser.**

**2. Read the result back as a finished block — never streaming word by word as it
arrives.**

A user reported that iOS 26 changed to streaming and made it *"virtually impossible to
verify immediately that what you've dictated has been interpreted correctly"* — you have
to go back and re-listen. That is the lab finding, described from the outside by someone
who had never seen it.

**3. Suppress screen-reader speech while recording, and restore it afterwards.**

NVDA carries this as an acknowledged **won't-fix**:
[#12938 "Silence NVDA while Dictation is actively listening"](https://github.com/nvaccess/nvda/issues/12938).
Documented, recognised, open friction.

## Integration shape

**A thin NVDA globalPlugin talking to our own process over IPC. Never everything
in-process.** A crash in our code would take down the user's screen reader, which for a
blind user is catastrophic rather than inconvenient.

- **Expose actions as `@script` gestures.** They then appear in NVDA's Input Gestures
  dialog automatically, the user rebinds them themselves, bindings survive our updates,
  and they work from a braille display. Better than our own hotkey system.
- **Use `ui.message()` rather than our own TTS.** It speaks and outputs to braille while
  respecting the voice, rate and speech mode the user has already configured. Blind users
  do not tolerate two competing voices.
- **Budget for one API-breaking NVDA release per year** (the `.1`). A standing maintenance
  tax, not a one-off cost.

The integration layer itself is 2–4 weeks. The real cost is elsewhere: reading and editing
text through UIA in Word, Chrome, Outlook, Electron and terminals — differently in each
family.

## Expectations about money

**None.** Free, open, in the add-on store. The value is portfolio, reputation in a
community that notices this kind of work, and no commoditisation risk because there is
nothing to defend.

If v3.0 was needed as revenue, the answer is no — and it is better to know that now than
after building it.

## What this record does not know

Stated plainly, because the previous version of this scope was wrong precisely by not
saying so.

- **The demand side is unsized.** The lab finding is solid. How many people would use such
  an add-on, and whether they want it, is not established. In current `r/Blind` traffic
  dictation is discussed rarely and almost always about iOS, not Windows. That does not
  contradict the study, but it means this product has to be *explained*, not merely
  offered in answer to a loud demand.
- **Community sentiment is thinly sourced.** Reddit was read directly; **AppleVis, Blind
  Bargains and the NVDA mailing-list archives were not** — they blocked automated access.
  The more technical part of the community is exactly the part not heard from.
- **Two figures quoted in the supporting research are unverified** and are deliberately
  not repeated here: Vispero's revenue (every public source is an algorithmic estimate,
  and they disagree by multiples) and a claimed 12% cut in US federal AT funding.
- **No blind user has been consulted.** Every conclusion above is inference from
  literature, forums and source code. That is the single largest gap, and it should be
  closed before any code is written for this version.

## What would reverse this

Evidence that blind Windows users will in fact pay; a funded institutional buyer
(UK Access to Work is the one channel found that is friendly to a single developer); or
NVDA or Microsoft shipping error detection natively, which would close the niche
altogether.

Conversely, the cheapest thing that would *confirm* it: show the audible-uncertainty idea
to a handful of blind NVDA users and see whether it lands. That has not been done.
