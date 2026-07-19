"""``stt/``: the multipart body, the warm pool, the error taxonomy, the prompt.

No test here touches the network. The Groq provider is exercised against a fake
``HTTPSConnection`` injected through the pool's factory — the same seam the real
pool uses — so status handling, the stale-keep-alive retry and the mandatory
User-Agent are all pinned without a single socket.
"""

from __future__ import annotations

import email.parser
import email.policy
from pathlib import Path

import pytest

from billytalk.core.logging_setup import REDACTED
from billytalk.core.machine.effects import ErrorCode
from billytalk.core.stt.base import AudioClip, TranscriptionOptions, build_prompt
from billytalk.core.stt.errors import (
    KeyInvalid,
    NetworkDown,
    NoApiKey,
    ProviderUnavailable,
    RateLimited,
    RetryAdvice,
)
from billytalk.core.stt.groq import GroqProvider, USER_AGENT, build_multipart
from billytalk.core.stt.pool import WarmConnectionPool

# --------------------------------------------------------------------------- #
# multipart
# --------------------------------------------------------------------------- #


def test_multipart_parses_back_with_the_stdlib_parser() -> None:
    flac = b"fLaC\x00\x01\x02binary\r\n--not-a-boundary"
    body, content_type = build_multipart(
        flac=flac, filename="clip.flac", model="whisper-large-v3-turbo",
        language="ru", prompt="Рабочая диктовка про VPS.",
    )
    message = email.parser.BytesParser(policy=email.policy.default).parsebytes(
        b"Content-Type: " + content_type.encode() + b"\r\n\r\n" + body
    )
    parts = {p.get_param("name", header="content-disposition"): p for p in message.iter_parts()}

    assert set(parts) == {"model", "language", "response_format", "prompt", "file"}
    assert parts["model"].get_content().strip() == "whisper-large-v3-turbo"
    assert parts["language"].get_content().strip() == "ru"
    assert parts["response_format"].get_content().strip() == "json"
    assert parts["file"].get_filename() == "clip.flac"
    assert parts["file"].get_content() == flac, "binary audio must survive verbatim"


def test_multipart_without_prompt_omits_the_field() -> None:
    body, _ = build_multipart(
        flac=b"x", filename="c.flac", model="m", language="ru", prompt=None
    )
    assert b'name="prompt"' not in body


# --------------------------------------------------------------------------- #
# prompt (spec §6, measured in research/07)
# --------------------------------------------------------------------------- #


def test_prompt_is_a_sentence_not_a_bare_list() -> None:
    """A bare comma list measurably strips the punctuation from the answer."""
    prompt = build_prompt(["VPS", "Reality", "BillyTalk"], "ru")
    assert prompt is not None
    assert prompt.endswith(".")
    assert "VPS, Reality, BillyTalk" in prompt
    assert prompt != "VPS, Reality, BillyTalk", "the terms sit inside a sentence"


def test_prompt_over_budget_drops_terms_from_the_front() -> None:
    """Spec §6: the valuable terms go at the end, so the end must survive."""
    terms = [f"term{i:03d}" for i in range(200)] + ["BillyTalk"]
    prompt = build_prompt(terms, "en")
    assert prompt is not None
    assert len(prompt) <= 500, "Latin budget: ~2.43 chars/token against the 224 cap"
    assert "BillyTalk" in prompt, "the tail is the valuable part"
    assert "term000" not in prompt


def test_cyrillic_prompt_gets_the_tighter_budget() -> None:
    """Measured with the real Whisper tokenizer (review round 1): 492 Cyrillic
    chars already exceed 224 tokens — the old 700-char proxy silently handed
    Whisper a decapitated prompt ending in a bare list."""
    terms = [f"сервер{i:03d}" for i in range(100)] + ["БиллиТолк"]
    prompt = build_prompt(terms, "ru")
    assert prompt is not None
    assert len(prompt) <= 420
    assert "БиллиТолк" in prompt


def test_no_terms_no_prompt() -> None:
    assert build_prompt([], "ru") is None


# --------------------------------------------------------------------------- #
# the warm pool
# --------------------------------------------------------------------------- #


class FakeRawConnection:
    """Stands in for ``http.client.HTTPSConnection`` under the pool."""

    def __init__(self) -> None:
        self.connected = False
        self.closed = False

    def connect(self) -> None:
        self.connected = True

    def close(self) -> None:
        self.closed = True


def test_warm_pool_reuses_a_fresh_connection() -> None:
    made: list[FakeRawConnection] = []
    clock = [0.0]

    def factory() -> FakeRawConnection:
        conn = FakeRawConnection()
        made.append(conn)
        return conn

    pool = WarmConnectionPool("example.test", factory=factory, clock=lambda: clock[0])  # type: ignore[arg-type]
    pool.warm()
    assert len(made) == 1 and made[0].connected, "warm pays the handshake up front"

    pooled = pool.acquire()
    assert pooled.raw is made[0] and pooled.reused, "the warm connection is the one reused"
    pool.release(pooled.raw)

    clock[0] += 30.0
    again = pool.acquire()
    assert again.raw is made[0], "still fresh at 30 s idle"


def test_warm_pool_drops_a_stale_connection() -> None:
    made: list[FakeRawConnection] = []
    clock = [0.0]

    def factory() -> FakeRawConnection:
        conn = FakeRawConnection()
        made.append(conn)
        return conn

    pool = WarmConnectionPool(
        "example.test", max_idle_s=60.0, factory=factory, clock=lambda: clock[0]  # type: ignore[arg-type]
    )
    pool.warm()
    clock[0] += 61.0

    pooled = pool.acquire()
    assert pooled.raw is not made[0], "an idle keep-alive older than the TTL is not trusted"
    assert made[0].closed
    assert not pooled.reused


# --------------------------------------------------------------------------- #
# the Groq provider against a scripted connection
# --------------------------------------------------------------------------- #


class ScriptedResponse:
    def __init__(self, status: int, payload: bytes, headers: dict[str, str]) -> None:
        self.status = status
        self._payload = payload
        self._headers = headers

    def read(self) -> bytes:
        return self._payload

    def getheaders(self) -> list[tuple[str, str]]:
        return list(self._headers.items())


class ScriptedConnection:
    """Plays one scripted outcome; records the request it saw."""

    def __init__(self, outcome: ScriptedResponse | Exception) -> None:
        self.outcome = outcome
        self.requests: list[tuple[str, str, bytes, dict[str, str]]] = []
        self.closed = False

    def connect(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True

    def request(self, method: str, path: str, body: bytes, headers: dict[str, str]) -> None:
        self.requests.append((method, path, body, headers))
        if isinstance(self.outcome, Exception):
            raise self.outcome

    def getresponse(self) -> ScriptedResponse:
        assert not isinstance(self.outcome, Exception)
        return self.outcome


def _provider(
    outcomes: list[ScriptedResponse | Exception],
    tmp_path: Path,
    *,
    key: str | None = "gsk_test",
) -> tuple[GroqProvider, list[ScriptedConnection], AudioClip]:
    connections: list[ScriptedConnection] = []

    def factory() -> ScriptedConnection:
        conn = ScriptedConnection(outcomes[len(connections)])
        connections.append(conn)
        return conn

    pool = WarmConnectionPool("api.groq.test", factory=factory)  # type: ignore[arg-type]
    provider = GroqProvider(lambda: key, pool=pool)
    clip_file = tmp_path / "clip.flac"
    clip_file.write_bytes(b"fLaC-not-really")
    return provider, connections, AudioClip(path=clip_file, duration_ms=1900)


OPTIONS = TranscriptionOptions(language="ru", prompt=None)


def test_success_carries_text_latency_and_billed_seconds(tmp_path: Path) -> None:
    ok = ScriptedResponse(200, b'{"text": "\\u0432\\u044b\\u043a\\u0430\\u0442\\u0438"}', {})
    provider, connections, clip = _provider([ok], tmp_path)

    result = provider.transcribe(clip, OPTIONS)

    assert result.text.value == "выкати"
    assert result.billed_seconds == pytest.approx(1.9)
    assert result.provider_id == "groq"
    method, path, body, headers = connections[0].requests[0]
    assert method == "POST" and path == "/openai/v1/audio/transcriptions"
    assert headers["User-Agent"] == USER_AGENT, "Cloudflare rejects clients without one"
    assert headers["Authorization"].startswith("Bearer ")


def test_result_text_is_a_sensitive_type(tmp_path: Path) -> None:
    """The transcript is born redacted: even repr of the result must not leak it."""
    ok = ScriptedResponse(200, b'{"text": "secret words"}', {})
    provider, _, clip = _provider([ok], tmp_path)
    result = provider.transcribe(clip, OPTIONS)
    assert "secret words" not in repr(result)
    assert REDACTED in repr(result)


def test_missing_key_raises_before_any_network(tmp_path: Path) -> None:
    provider, connections, clip = _provider([], tmp_path, key=None)
    with pytest.raises(NoApiKey) as excinfo:
        provider.transcribe(clip, OPTIONS)
    assert excinfo.value.advice is RetryAdvice.NEVER
    assert connections == [], "no connection is even created"


def test_401_is_key_invalid_and_never_retried(tmp_path: Path) -> None:
    provider, _, clip = _provider([ScriptedResponse(401, b"{}", {})], tmp_path)
    with pytest.raises(KeyInvalid) as excinfo:
        provider.transcribe(clip, OPTIONS)
    assert excinfo.value.code is ErrorCode.KEY_INVALID
    assert excinfo.value.advice is RetryAdvice.NEVER


def test_429_carries_retry_after(tmp_path: Path) -> None:
    provider, _, clip = _provider(
        [ScriptedResponse(429, b"{}", {"Retry-After": "7"})], tmp_path
    )
    with pytest.raises(RateLimited) as excinfo:
        provider.transcribe(clip, OPTIONS)
    assert excinfo.value.advice is RetryAdvice.AFTER_DELAY
    assert excinfo.value.retry_after_s == 7.0


def test_5xx_advises_the_backoff_ladder(tmp_path: Path) -> None:
    provider, _, clip = _provider([ScriptedResponse(503, b"", {})], tmp_path)
    with pytest.raises(ProviderUnavailable) as excinfo:
        provider.transcribe(clip, OPTIONS)
    assert excinfo.value.advice is RetryAdvice.BACKOFF


def test_timeout_is_network_down_enqueue(tmp_path: Path) -> None:
    provider, _, clip = _provider([TimeoutError()], tmp_path)
    with pytest.raises(NetworkDown) as excinfo:
        provider.transcribe(clip, OPTIONS)
    assert excinfo.value.code is ErrorCode.NETWORK_DOWN
    assert excinfo.value.advice is RetryAdvice.ENQUEUE


def test_dead_reused_keepalive_gets_one_silent_retry(tmp_path: Path) -> None:
    """The ordinary death of an idle keep-alive must not surface as an outage."""
    ok = ScriptedResponse(200, b'{"text": "ok"}', {})
    provider, connections, clip = _provider([ConnectionResetError(), ok], tmp_path)
    provider.warm()  # makes the first acquire a REUSED connection

    result = provider.transcribe(clip, OPTIONS)

    assert result.text.value == "ok"
    assert len(connections) == 2, "one silent retry on a fresh connection"
    assert connections[0].closed, "the dead connection is discarded, not repooled"


def test_dead_fresh_connection_is_a_real_network_error(tmp_path: Path) -> None:
    provider, connections, clip = _provider(
        [ConnectionResetError(), ScriptedResponse(200, b'{"text": "x"}', {})], tmp_path
    )
    # No warm(): the first acquire builds a FRESH connection. Its death is real.
    with pytest.raises(NetworkDown):
        provider.transcribe(clip, OPTIONS)
    assert len(connections) == 1, "no retry: a fresh socket dying is an outage"


def test_acquire_fresh_bypasses_the_idle_list() -> None:
    """The stale-keep-alive retry must not pop another idle connection: a VPN
    switch kills every idle socket at once, and a second reused corpse would
    turn routine socket death into a false outage (review round 1)."""
    made: list[FakeRawConnection] = []

    def factory() -> FakeRawConnection:
        conn = FakeRawConnection()
        made.append(conn)
        return conn

    pool = WarmConnectionPool("example.test", factory=factory, clock=lambda: 0.0)  # type: ignore[arg-type]
    pool.warm()
    pool.release(FakeRawConnection())  # a second idle keep-alive

    fresh = pool.acquire_fresh()
    assert fresh.raw is made[-1], "factory-built, not popped from idle"
    assert not fresh.reused


def test_missing_audio_file_is_a_taxonomy_error_not_an_oserror(tmp_path: Path) -> None:
    """harness §12: a raw OSError would be swallowed by the worker pool and
    wedge the row in pending_* for the whole session."""
    from billytalk.core.stt.errors import AudioUnreadable

    provider, connections, _clip = _provider([], tmp_path)
    ghost = AudioClip(path=tmp_path / "vanished.flac", duration_ms=500)
    with pytest.raises(AudioUnreadable) as excinfo:
        provider.transcribe(ghost, OPTIONS)
    assert excinfo.value.advice is RetryAdvice.NEVER, "re-reading can never succeed"
    assert "vanished" not in str(excinfo.value), "the path stays out of the message"
    assert connections == [], "no connection was spent on it"


def test_parse_failure_never_chains_the_transcript_payload(tmp_path: Path) -> None:
    """At status 200 the payload IS the transcript; a chained UnicodeDecodeError
    carries it whole in .object, riding into every repr (spec §13)."""
    mangled = "секретные слова".encode()[:-1]  # truncated multi-byte tail
    provider, _, clip = _provider([ScriptedResponse(200, mangled, {})], tmp_path)
    with pytest.raises(ProviderUnavailable) as excinfo:
        provider.transcribe(clip, OPTIONS)
    assert excinfo.value.__cause__ is None
    assert excinfo.value.__context__ is None
    assert "секретные" not in repr(excinfo.value)
