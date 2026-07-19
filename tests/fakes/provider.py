"""``FakeProvider`` (harness §8): scripted outcomes, no network, no sleep.

Feed it a list of outcomes — results or exceptions — and it plays them back in
order, recording every call. Latency is a number it *reports*, never a delay it
takes: a suite that sleeps is a suite that flakes.
"""

from __future__ import annotations

from typing import Literal

from billytalk.core.logging_setup import Transcript
from billytalk.core.stt.base import (
    AudioClip,
    Capabilities,
    TranscriptionOptions,
    TranscriptionResult,
)

__all__ = ["FakeProvider"]


class FakeProvider:
    provider_id = "fake"

    def __init__(self, outcomes: list[TranscriptionResult | Exception] | None = None) -> None:
        self.outcomes = list(outcomes or [])
        self.calls: list[tuple[AudioClip, TranscriptionOptions]] = []
        self.warmed = 0

    @staticmethod
    def ok(text: str, *, latency_ms: int = 368) -> TranscriptionResult:
        """A successful result, defaulting to the measured warm-pool latency."""
        return TranscriptionResult(
            text=Transcript(text),
            language="ru",
            billed_seconds=1.9,
            latency_ms=latency_ms,
            provider_id="fake",
            model="fake-whisper",
        )

    def capabilities(self) -> Capabilities:
        return Capabilities(
            languages=("ru", "en"),
            streaming=False,
            max_clip_seconds=20 * 60,
            supports_prompt=True,
            cost_tier="free",
        )

    def health(self) -> Literal["ok", "no_key"]:
        return "ok"

    def warm(self) -> None:
        self.warmed += 1

    def transcribe(self, clip: AudioClip, options: TranscriptionOptions) -> TranscriptionResult:
        self.calls.append((clip, options))
        if not self.outcomes:
            return self.ok("")
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome
