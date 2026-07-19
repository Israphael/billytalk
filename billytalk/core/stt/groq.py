"""Groq: the one real provider of MVP-0 (spec §6).

``POST https://api.groq.com/openai/v1/audio/transcriptions``,
``model=whisper-large-v3-turbo``, ``response_format=json``, over a warm
connection from ``pool.py``.

Two measured rules from research/07 are load-bearing here:

* **User-Agent is mandatory.** Cloudflare rejects clients without one by
  signature (403, code 1010). The header is not cosmetic.
* **One attempt per call.** The retry ladder lives in the driver; a provider
  that sleeps cannot be cancelled, and ``CancelTranscribe`` must have something
  to cancel immediately. The single exception: a request on a *reused*
  connection that dies before a response is retried once on a fresh one —
  that is the ordinary death of an idle keep-alive, not an outage.

Nothing in this module logs. Request bodies are the user's speech; even a
debug line with the payload size next to a timestamp says more than a log
should. The driver logs latencies and error codes — nothing else.
"""

from __future__ import annotations

import email.parser
import http.client
import json
import socket
import ssl
import uuid
from collections.abc import Callable
from time import monotonic
from typing import Final, Literal

from ..logging_setup import Transcript
from .base import (
    AudioClip,
    Capabilities,
    TranscriptionOptions,
    TranscriptionResult,
)
from .errors import (
    AudioUnreadable,
    KeyInvalid,
    NetworkDown,
    NoApiKey,
    ProviderUnavailable,
    RateLimited,
    RetryAdvice,
)
from .pool import WarmConnectionPool

__all__ = ["GROQ_HOST", "GroqProvider", "USER_AGENT", "build_multipart"]

GROQ_HOST: Final = "api.groq.com"
_PATH: Final = "/openai/v1/audio/transcriptions"

USER_AGENT: Final = "BillyTalk/0.1.0 (+https://github.com/Israphael/billytalk)"
"""Cloudflare rejects clients without a User-Agent by signature (research/07)."""


def build_multipart(
    *,
    flac: bytes,
    filename: str,
    model: str,
    language: str,
    prompt: str | None,
    boundary: str | None = None,
) -> tuple[bytes, str]:
    """The request body and its Content-Type.

    Hand-rolled because the standard library has no multipart *writer* and a
    dependency for four fields is not worth its supply chain. The tests parse
    the output back with ``email.parser`` to keep this honest.
    """
    boundary = boundary or uuid.uuid4().hex
    lines: list[bytes] = []

    def field(name: str, value: str) -> None:
        lines.extend(
            (
                f"--{boundary}\r\n".encode(),
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
                value.encode("utf-8"),
                b"\r\n",
            )
        )

    field("model", model)
    field("language", language)
    field("response_format", "json")
    if prompt:
        field("prompt", prompt)
    lines.extend(
        (
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'.encode(),
            b"Content-Type: audio/flac\r\n\r\n",
            flac,
            b"\r\n",
            f"--{boundary}--\r\n".encode(),
        )
    )
    return b"".join(lines), f"multipart/form-data; boundary={boundary}"


class GroqProvider:
    """One warm-pooled attempt per call. See the module docstring for why."""

    provider_id = "groq"

    def __init__(
        self,
        key_source: Callable[[], str | None],
        *,
        model: str = "whisper-large-v3-turbo",
        pool: WarmConnectionPool | None = None,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self._key_source = key_source
        self._model = model
        self._pool = pool or WarmConnectionPool(GROQ_HOST)
        self._clock = clock

    def capabilities(self) -> Capabilities:
        return Capabilities(
            languages=("ru", "en", "es", "pt"),
            streaming=False,
            max_clip_seconds=20 * 60,  # spec §6: rejection at press time is the driver's
            supports_prompt=True,
            cost_tier="metered",
        )

    def health(self) -> Literal["ok", "no_key"]:
        return "ok" if self._key_source() else "no_key"

    def warm(self) -> None:
        self._pool.warm()

    def transcribe(self, clip: AudioClip, options: TranscriptionOptions) -> TranscriptionResult:
        key = self._key_source()
        if not key:
            raise NoApiKey()

        try:
            flac = clip.path.read_bytes()
        except OSError:
            # Evicted at the cap, deleted by hand, or a transient sharing
            # violation: a raw OSError would be swallowed by the worker pool
            # and wedge the row in pending_* for the session (harness §12 —
            # every failure path returns a taxonomy code).
            raise AudioUnreadable() from None
        body, content_type = build_multipart(
            flac=flac,
            filename=clip.path.name,
            model=self._model,
            language=options.language,
            prompt=options.prompt,
        )
        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": content_type,
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        }

        started = self._clock()
        pooled = self._pool.acquire()
        try:
            status, payload, response_headers = self._send(pooled.raw, body, headers)
        except NetworkDown:
            self._pool.discard(pooled.raw)
            if not pooled.reused:
                raise
            # A reused keep-alive died mid-request: the ordinary end of an idle
            # connection, not an outage. One silent retry on a genuinely fresh
            # socket — acquire() would pop another idle keep-alive that likely
            # died with the first (VPN switch kills them all), turning routine
            # socket death into a false outage.
            fresh = self._pool.acquire_fresh()
            try:
                status, payload, response_headers = self._send(fresh.raw, body, headers)
            except NetworkDown:
                self._pool.discard(fresh.raw)
                raise
            else:
                self._pool.release(fresh.raw)
        else:
            self._pool.release(pooled.raw)

        latency_ms = int((self._clock() - started) * 1000)
        self._raise_for_status(status, response_headers)

        parse_failed = False
        try:
            text = str(json.loads(payload.decode("utf-8")).get("text", ""))
        except (ValueError, UnicodeDecodeError):
            # Never chain: at status 200 the payload IS the transcript, and a
            # UnicodeDecodeError/JSONDecodeError object carries it whole in
            # .object/.doc — chained onto the raised error it would ride into
            # every repr of the exception (spec §13). Raised outside the
            # except block so __context__ stays empty too.
            parse_failed = True
        if parse_failed:
            raise ProviderUnavailable(status)

        return TranscriptionResult(
            text=Transcript(text),
            language=options.language,
            # Groq bills by audio duration; the response carries no charge field,
            # so the honest number available on day one is the clip length.
            billed_seconds=clip.duration_ms / 1000.0,
            latency_ms=latency_ms,
            provider_id=self.provider_id,
            model=self._model,
        )

    # ------------------------------------------------------------------ #

    def _send(
        self,
        conn: http.client.HTTPSConnection,
        body: bytes,
        headers: dict[str, str],
    ) -> tuple[int, bytes, email.parser.Message | dict[str, str]]:
        try:
            conn.request("POST", _PATH, body=body, headers=headers)
            response = conn.getresponse()
            payload = response.read()  # read fully: required before keep-alive reuse
            return response.status, payload, dict(response.getheaders())
        except (TimeoutError, socket.timeout) as exc:
            raise NetworkDown("timeout") from exc
        except (
            ConnectionError,
            http.client.HTTPException,
            ssl.SSLError,
            OSError,
        ) as exc:
            raise NetworkDown(type(exc).__name__) from exc

    @staticmethod
    def _raise_for_status(status: int, headers: dict[str, str]) -> None:
        if status == 200:
            return
        if status in (401, 403):
            raise KeyInvalid(status)
        if status == 429:
            retry_after = headers.get("Retry-After") or headers.get("retry-after")
            try:
                seconds = float(retry_after) if retry_after is not None else None
            except ValueError:
                seconds = None
            raise RateLimited(seconds)
        if status >= 500:
            raise ProviderUnavailable(status)
        # A 4xx we cannot act on (malformed clip, bad params): retrying the same
        # request would fail the same way.
        raise ProviderUnavailable(status, advice=RetryAdvice.NEVER)
