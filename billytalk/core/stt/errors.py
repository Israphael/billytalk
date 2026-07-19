"""The error taxonomy of spec §6, as types the driver can schedule from.

Each error carries the stable :class:`ErrorCode` (harness §7) and a
:class:`RetryAdvice` — the spec's retry table turned into data:

=============  ==================  =====================================
HTTP / cause   advice              spec §6 row
=============  ==================  =====================================
timeout        ``ENQUEUE``         «Таймаут → в очередь повтора»
401 / 403      ``NEVER``           «401 — не повторять»
429            ``AFTER_DELAY``     «повтор по Retry-After»
5xx, network   ``BACKOFF``         «повтор 1→2→4→8 с, до 5 попыток»
no key saved   ``NEVER``           first dictation shows "нужен ключ"
=============  ==================  =====================================

The provider never sleeps a ladder itself: a blocking retry loop could not be
cancelled by a double Esc, and ``CancelTranscribe`` must always have something
to cancel *now*.
"""

from __future__ import annotations

from enum import Enum

from ..machine.effects import ErrorCode

__all__ = [
    "KeyInvalid",
    "NetworkDown",
    "NoApiKey",
    "ProviderUnavailable",
    "RateLimited",
    "RetryAdvice",
    "TranscriptionError",
]


class RetryAdvice(Enum):
    NEVER = "never"
    ENQUEUE = "enqueue"          # straight to the retry track
    AFTER_DELAY = "after_delay"  # honour retry_after_s first
    BACKOFF = "backoff"          # driver's 1→2→4→8 s ladder, 5 attempts max


class TranscriptionError(Exception):
    """Base. The message must never contain the request body or the key —
    it ends up in logs, and request bodies are the user's speech."""

    def __init__(
        self,
        code: ErrorCode,
        advice: RetryAdvice,
        message: str,
        *,
        retry_after_s: float | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.advice = advice
        self.retry_after_s = retry_after_s


class NoApiKey(TranscriptionError):
    def __init__(self) -> None:
        super().__init__(ErrorCode.NO_API_KEY, RetryAdvice.NEVER, "no API key saved")


class KeyInvalid(TranscriptionError):
    def __init__(self, status: int) -> None:
        super().__init__(ErrorCode.KEY_INVALID, RetryAdvice.NEVER, f"HTTP {status}")


class RateLimited(TranscriptionError):
    def __init__(self, retry_after_s: float | None) -> None:
        super().__init__(
            ErrorCode.RATE_LIMITED,
            RetryAdvice.AFTER_DELAY,
            "HTTP 429",
            retry_after_s=retry_after_s,
        )


class NetworkDown(TranscriptionError):
    """Timeout or a dead socket: the network, not the service."""

    def __init__(self, message: str) -> None:
        super().__init__(ErrorCode.NETWORK_DOWN, RetryAdvice.ENQUEUE, message)


class ProviderUnavailable(TranscriptionError):
    """5xx, or a 4xx we cannot act on: the service, not the network."""

    def __init__(self, status: int, *, advice: RetryAdvice = RetryAdvice.BACKOFF) -> None:
        super().__init__(ErrorCode.PROVIDER_ERROR, advice, f"HTTP {status}")
        self.status = status
