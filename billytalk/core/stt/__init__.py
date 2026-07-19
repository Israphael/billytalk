"""Transcription: the provider interface and the one real provider, Groq.

The shape (spec §6): ``TranscriptionProvider`` is declared for three
implementations and MVP-0 ships one. A provider makes **one attempt** and raises
a typed error that says how to retry; scheduling retries is the driver's job,
because a provider that sleeps through a backoff ladder cannot be cancelled by
a double Esc.
"""

from .base import (
    AudioClip,
    Capabilities,
    TranscriptionOptions,
    TranscriptionProvider,
    TranscriptionResult,
    build_prompt,
)
from .errors import (
    AudioUnreadable,
    KeyInvalid,
    NetworkDown,
    NoApiKey,
    ProviderUnavailable,
    RateLimited,
    RetryAdvice,
    TranscriptionError,
)
from .groq import GroqProvider
from .pool import WarmConnectionPool

__all__ = [
    "AudioClip",
    "Capabilities",
    "TranscriptionOptions",
    "TranscriptionProvider",
    "TranscriptionResult",
    "build_prompt",
    "TranscriptionError",
    "RetryAdvice",
    "AudioUnreadable",
    "NoApiKey",
    "KeyInvalid",
    "RateLimited",
    "NetworkDown",
    "ProviderUnavailable",
    "GroqProvider",
    "WarmConnectionPool",
]
