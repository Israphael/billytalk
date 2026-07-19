"""The provider interface (spec §6): declared for three, shipped with one.

``TranscriptionResult.text`` is a :class:`~billytalk.core.logging_setup.Transcript`,
not a ``str`` — the redaction invariant is carried by the type from the moment
the words exist, so a result object reaching a log line is dropped by the
filter instead of leaking.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from ..logging_setup import Transcript

__all__ = [
    "AudioClip",
    "Capabilities",
    "TranscriptionOptions",
    "TranscriptionProvider",
    "TranscriptionResult",
    "build_prompt",
]

PROMPT_CHAR_BUDGET_CYRILLIC = 420
PROMPT_CHAR_BUDGET_LATIN = 500
"""Spec §6 caps the prompt at 224 tokens. Character budgets, measured against
the real Whisper multilingual tokenizer (review round 1): 492 Cyrillic chars
already encode to 239 tokens (~2.06 chars/token → 224 ≈ 461 chars), a Latin
term list runs ~2.43 chars/token (224 ≈ 545 chars). Whisper silently keeps the
FINAL 224 tokens, so an over-budget prompt loses its sentence opening and
degrades into the bare comma list that measurably strips punctuation — the
budget must err low. No tokenizer is shipped; these margins carry the slack."""


@dataclass(frozen=True, slots=True)
class AudioClip:
    """A finished, durable clip — the FLAC already on disk (spec §3).

    The provider reads the file rather than taking bytes: by the time a
    transcription happens (or is retried tomorrow) the file is the one source
    of truth, and two copies of the user's speech in memory is one too many.
    """

    path: Path
    duration_ms: int
    sample_rate: int = 16_000


@dataclass(frozen=True, slots=True)
class TranscriptionOptions:
    """Explicit language always (spec §6): autodetection is off by design."""

    language: str
    prompt: str | None = None
    timeout_s: float = 30.0


@dataclass(frozen=True, slots=True)
class TranscriptionResult:
    text: Transcript
    language: str | None
    billed_seconds: float | None
    latency_ms: int
    provider_id: str
    model: str

    @property
    def is_empty(self) -> bool:
        """Spec §6: an empty transcript becomes an ``empty`` history row, silently."""
        return not self.text.value.strip()


@dataclass(frozen=True, slots=True)
class Capabilities:
    languages: tuple[str, ...]
    streaming: bool
    max_clip_seconds: int
    supports_prompt: bool
    cost_tier: Literal["free", "metered", "flat"]


class TranscriptionProvider(Protocol):
    """One attempt per call; retry scheduling belongs to the driver (errors.py)."""

    provider_id: str

    def capabilities(self) -> Capabilities: ...

    def transcribe(self, clip: AudioClip, options: TranscriptionOptions) -> TranscriptionResult: ...

    def health(self) -> Literal["ok", "no_key"]: ...


_PROMPT_SENTENCES = {
    "ru": "Рабочая диктовка про серверы и разработку; встречаются термины {terms}.",
    "en": "A work dictation about servers and development; it mentions {terms}.",
}


def build_prompt(terms: list[str], language: str) -> str | None:
    """A normal sentence with the terms inside — never a bare comma list.

    Measured (research/07): a bare list as the prompt made the full stops
    disappear from the answer — Whisper copies the prompt's style as if it were
    preceding context. A sentence keeps the punctuation and still teaches the
    proper names («реалити» → Reality only worked with a prompt).

    When over budget, terms are dropped from the *front*: spec §6 puts the
    valuable ones at the end, where Whisper's context window weighs most.
    """
    if not terms:
        return None
    template = _PROMPT_SENTENCES.get(language, _PROMPT_SENTENCES["en"])
    kept = list(terms)
    while kept:
        sentence = template.format(terms=", ".join(kept))
        budget = (
            PROMPT_CHAR_BUDGET_CYRILLIC
            if any("Ѐ" <= ch <= "ӿ" for ch in sentence)
            else PROMPT_CHAR_BUDGET_LATIN
        )
        if len(sentence) <= budget:
            return sentence
        kept.pop(0)
    return None
