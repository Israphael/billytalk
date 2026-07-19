# Contributing

**The core works; the UI does not exist yet.** Cycle 1 shipped a working dictation core
(`python -m billytalk.core`, 213 tests); cycles 2–3 add the tray, windows and installer.
What is most useful from outside has not changed, though:

## The most useful thing you can do today

**Run the mouse probe and tell me what your mouse reports.**

```bash
python probes/billytalk_mouse_probe.py
```

Pure `ctypes`, no dependencies, no install. Press your side buttons; it prints exactly
what Windows sees.

BillyTalk's whole premise is binding push-to-talk to a mouse side button, and the
ecosystem has a documented problem here: vendor software (Razer Synapse, Logitech
Options+, Corsair iCUE) can remap side buttons at the driver or firmware level, at which
point a standard low-level hook goes blind. AutoHotkey and Wispr Flow independently
arrived at the same workaround, which suggests it is common.

I have verified exactly one mouse: a Razer DeathAdder V2 X HyperSpeed with Synapse not
running. That is a sample size of one.

**Open an issue with:**

- mouse model, and whether the vendor software was running
- whether the side buttons produced `HOOK MOUSE WM_XBUTTONDOWN` lines
- whether any line was flagged `<<< INJECTED`
- keyboard events instead of mouse events (means the vendor software remapped it)
- nothing at all (means the button is consumed in firmware)

Logitech MX with Options+ is the known-bad case and I would especially like data on it.

⚠️ The probe logs **all keystrokes** while running — that is inherent to a keyboard hook.
Strip keyboard lines before pasting anything, as I did for the committed sample in
`docs/ru/research/05a-mouse-probe-raw-events.log`.

## Also welcome

- **Rerun a spike and disagree with it.** `spikes/` holds the measurement scripts. If you
  get different numbers on different hardware or a different network, that is valuable —
  three of my own estimates have already been overturned by measurement.
- **Challenge a decision.** Every ADR has a "what would reverse this" section. If you can
  satisfy one, open an issue.
- **Windows API corrections.** The specification is dense with platform detail and some
  of it is inevitably wrong.

## Running the spikes

```bash
uv venv --python 3.14 .venv
uv pip install -r spikes/requirements.txt
.venv/Scripts/python spikes/s0_smoke.py
```

Scripts needing an API key read it from the environment (`GROQ_API_KEY`). Nothing is
hardcoded and nothing is written to disk.

## Language

ADRs and this file are English and stand alone. `docs/ru/` is my working research in
Russian — longer, messier, the raw material the ADRs summarise. **You do not need it to
understand or contribute to the project.** If something in an ADR is unclear because its
reasoning lives only in the Russian docs, that is a documentation bug worth reporting.

## Code style, once there is code

Python 3.14, type annotations on public functions, `pytest`. The state machine is a pure
function and its tests are the most important in the project — see
`docs/ru/spec/03-harness-слой-для-сборки.md` §8.

One rule is not negotiable: **audio and transcript types must never be loggable.** They
carry a redacting `__repr__`, a logging filter rejects records containing them, and CI
lints for it. A privacy mechanism that raises exceptions is not acceptable either — it
would turn a recoverable paste failure into a crash that loses the user's words.

## Licence

MIT. By contributing you agree your work is licensed the same way.
