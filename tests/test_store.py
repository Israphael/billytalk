"""``store/``: the audio clock, cleanup policy, config file and secrets (harness §8).

The two mandatory storage names that could not exist while ``store/`` was
schema-only — ``cleanup_skips_rows_with_null_release_at`` (moved here from
``test_store_ddl.py``, now against real code) and ``cleanup_paused_while_offline``
(formerly skipped) — both live here.

Times are integers passed by hand; nothing here sleeps or reads a clock.
"""

from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path

import pytest

from billytalk.core.machine.effects import DeliveryStatus
from billytalk.core.store.config import (
    CONFIG_SCHEMA_VERSION,
    Config,
    ConfigTooNew,
    load_config,
    save_config,
)
from billytalk.core.store.db import connect, ensure_schema
from billytalk.core.store.history import CleanupGate, HistoryStore

HOUR_MS = 3_600_000


@pytest.fixture
def store() -> HistoryStore:
    conn = connect(":memory:")
    ensure_schema(conn)
    return HistoryStore(conn)


def _clip(tmp_path: Path, name: str, size: int = 64) -> Path:
    file = tmp_path / name
    file.write_bytes(b"\x00" * size)
    return file


def _row(store: HistoryStore, row_id: int) -> sqlite3.Row:
    row = store._conn.execute("SELECT * FROM history WHERE id = ?", (row_id,)).fetchone()
    assert row is not None
    return row


# --------------------------------------------------------------------------- #
# the audio release clock
# --------------------------------------------------------------------------- #


def test_delivered_status_starts_the_release_clock(store: HistoryStore, tmp_path: Path) -> None:
    clip = _clip(tmp_path, "a.flac")
    rid = store.add(
        seq=1, created_at=1000, now=1500, duration_ms=800,
        status=DeliveryStatus.PENDING_TRANSCRIBE, audio_path=str(clip),
    )
    assert _row(store, rid)["audio_release_at"] is None, "undelivered audio is held"

    store.set_status(rid, DeliveryStatus.INSERTED, now=9000)
    assert _row(store, rid)["audio_release_at"] == 9000


def test_release_clock_is_not_restarted_by_a_later_status(
    store: HistoryStore, tmp_path: Path
) -> None:
    """A history re-insert must not grant the audio a fresh hour every time."""
    clip = _clip(tmp_path, "a.flac")
    rid = store.add(
        seq=1, created_at=1000, now=1500, duration_ms=800,
        status=DeliveryStatus.PENDING_TRANSCRIBE, audio_path=str(clip),
    )
    store.set_status(rid, DeliveryStatus.INSERTED, now=9000)
    store.set_status(rid, DeliveryStatus.INSERTED, now=500_000)
    assert _row(store, rid)["audio_release_at"] == 9000


def test_terminal_at_birth_statuses_start_the_clock_immediately(
    store: HistoryStore, tmp_path: Path
) -> None:
    """``too_short``/``empty``/``cancelled`` will never be transcribed; holding
    their audio forever would be a slow disk leak (OPEN-QUESTIONS §15)."""
    for i, status in enumerate(
        (DeliveryStatus.TOO_SHORT, DeliveryStatus.EMPTY, DeliveryStatus.CANCELLED), start=1
    ):
        clip = _clip(tmp_path, f"{status.value}.flac")
        rid = store.add(
            seq=i, created_at=i * 1000, now=i * 1000 + 40, duration_ms=100,
            status=status, audio_path=str(clip),
        )
        assert _row(store, rid)["audio_release_at"] == i * 1000 + 40


def test_hold_statuses_keep_audio_unconditionally(store: HistoryStore, tmp_path: Path) -> None:
    """``transcribe_failed`` holds too: an invalid key is fixable, and until it is
    the audio may be the only copy of the words (spec §3)."""
    clip = _clip(tmp_path, "a.flac")
    rid = store.add(
        seq=1, created_at=1000, now=1500, duration_ms=800,
        status=DeliveryStatus.PENDING_TRANSCRIBE, audio_path=str(clip),
    )
    store.set_status(rid, DeliveryStatus.PENDING_RETRY, now=9000)
    assert _row(store, rid)["audio_release_at"] is None
    store.set_status(rid, DeliveryStatus.TRANSCRIBE_FAILED, now=10_000, error_code="key_invalid")
    assert _row(store, rid)["audio_release_at"] is None
    assert _row(store, rid)["error_code"] == "key_invalid"


# --------------------------------------------------------------------------- #
# cleanup
# --------------------------------------------------------------------------- #


def test_cleanup_skips_rows_with_null_release_at(store: HistoryStore, tmp_path: Path) -> None:
    """``NULL`` means hold (spec §3): the dictation has not been transcribed yet,
    so the audio file is the only copy of what the user said."""
    old = _clip(tmp_path, "old.flac")
    waiting = _clip(tmp_path, "waiting.flac")
    now = 10 * HOUR_MS
    delivered = store.add(
        seq=1, created_at=1000, now=1500, duration_ms=800,
        status=DeliveryStatus.PENDING_TRANSCRIBE, audio_path=str(old),
    )
    store.set_status(delivered, DeliveryStatus.INSERTED, now=now - 3 * HOUR_MS)
    held = store.add(
        seq=2, created_at=2000, now=2500, duration_ms=800,
        status=DeliveryStatus.PENDING_TRANSCRIBE, audio_path=str(waiting),
    )

    released = store.cleanup(now=now, retention_minutes=60)

    assert released == [str(old)]
    assert not old.exists()
    assert waiting.exists(), "held audio must survive any number of cleanups"
    assert _row(store, held)["audio_path"] == str(waiting)


def test_cleanup_releases_audio_but_never_deletes_the_row(
    store: HistoryStore, tmp_path: Path
) -> None:
    """Spec §10: text is forever. Early harness §4 wrote cleanup as ``DELETE FROM
    history``, which would have destroyed the permanent history
    (OPEN-QUESTIONS §15)."""
    clip = _clip(tmp_path, "a.flac")
    now = 10 * HOUR_MS
    rid = store.add(
        seq=1, created_at=1000, now=1500, duration_ms=800,
        status=DeliveryStatus.PENDING_TRANSCRIBE, audio_path=str(clip),
    )
    store.record_transcription(
        rid, text_raw="выкати на прод", text_final="выкати на прод",
        language="ru", provider_id="groq", billed_seconds=1.2, latency_ms=370,
    )
    store.set_status(rid, DeliveryStatus.INSERTED, now=now - 3 * HOUR_MS)

    store.cleanup(now=now, retention_minutes=60)

    row = _row(store, rid)
    assert row["audio_path"] is None
    assert row["text_final"] == "выкати на прод", "the text outlives its audio"
    assert store.search("прод"), "and stays searchable"


def test_cleanup_paused_while_offline(store: HistoryStore, tmp_path: Path) -> None:
    """Spec §3, the customer's rule: while the network is down cleanup does not
    run **at all** — even for delivered rows — and it resumes ten minutes after
    the first successful transcription."""
    clip = _clip(tmp_path, "delivered-long-ago.flac")
    now = 10 * HOUR_MS
    rid = store.add(
        seq=1, created_at=1000, now=1500, duration_ms=800,
        status=DeliveryStatus.PENDING_TRANSCRIBE, audio_path=str(clip),
    )
    store.set_status(rid, DeliveryStatus.INSERTED, now=now - 5 * HOUR_MS)

    gate = CleanupGate()
    assert gate.should_run(now), "a fresh process assumes the network works"

    gate.network_lost()
    for tick in range(0, 4 * HOUR_MS, HOUR_MS):
        assert not gate.should_run(now + tick), "offline: cleanup must not run at all"
    assert clip.exists(), "the driver contract: no should_run, no cleanup call"

    back_online = now + 4 * HOUR_MS
    gate.transcribe_succeeded(back_online)
    assert not gate.should_run(back_online), "not immediately"
    assert not gate.should_run(back_online + 9 * 60_000), "not at nine minutes"
    assert gate.should_run(back_online + 10 * 60_000), "at ten, cleanup resumes"

    # Steady-state successes must not keep pushing cleanup into the future.
    gate.transcribe_succeeded(back_online + 11 * 60_000)
    assert gate.should_run(back_online + 12 * 60_000)

    released = store.cleanup(now=back_online + 10 * 60_000, retention_minutes=60)
    assert released == [str(clip)]
    assert not clip.exists()


# --------------------------------------------------------------------------- #
# the retention ceiling
# --------------------------------------------------------------------------- #


def test_evict_over_row_cap_releases_oldest_audio_first(
    store: HistoryStore, tmp_path: Path
) -> None:
    clips = [_clip(tmp_path, f"{i}.flac") for i in range(1, 5)]
    for i, clip in enumerate(clips, start=1):
        store.add(
            seq=i, created_at=i * 1000, now=i * 1000, duration_ms=800,
            status=DeliveryStatus.PENDING_TRANSCRIBE, audio_path=str(clip),
        )

    released = store.evict_over_cap(max_rows=2, max_bytes=10 * 1024**2)

    assert sorted(released) == sorted([str(clips[0]), str(clips[1])])
    assert not clips[0].exists() and not clips[1].exists()
    assert clips[2].exists() and clips[3].exists(), "the newest survive"


def test_evict_over_byte_cap(store: HistoryStore, tmp_path: Path) -> None:
    big_old = _clip(tmp_path, "big-old.flac", size=900)
    small_new = _clip(tmp_path, "small-new.flac", size=100)
    store.add(
        seq=1, created_at=1000, now=1000, duration_ms=800,
        status=DeliveryStatus.PENDING_TRANSCRIBE, audio_path=str(big_old),
    )
    store.add(
        seq=2, created_at=2000, now=2000, duration_ms=800,
        status=DeliveryStatus.PENDING_TRANSCRIBE, audio_path=str(small_new),
    )

    released = store.evict_over_cap(max_rows=500, max_bytes=500)

    assert released == [str(big_old)], "newest kept, the budget spent on it first"
    assert small_new.exists()


# --------------------------------------------------------------------------- #
# startup
# --------------------------------------------------------------------------- #


def test_pending_rows_are_queued_at_startup_in_press_order(
    store: HistoryStore, tmp_path: Path
) -> None:
    """Spec §3: the last run's unfinished rows go to the retry queue; the user
    sees 'N records waiting'. Press order, because delivery order is press order
    and a restart does not repeal it."""
    for i, status in enumerate(
        (
            DeliveryStatus.PENDING_RETRY,
            DeliveryStatus.INSERTED,
            DeliveryStatus.PENDING_TRANSCRIBE,
        ),
        start=1,
    ):
        clip = _clip(tmp_path, f"{i}.flac")
        store.add(
            seq=i, created_at=i * 1000, now=i * 1000, duration_ms=800,
            status=status, audio_path=str(clip),
        )

    pending = store.pending_at_startup()
    assert [p.seq for p in pending] == [1, 3]
    assert all(p.audio_path for p in pending)


def test_count_waiting_counts_only_rows_awaiting_the_network(store: HistoryStore) -> None:
    """The N in spec §3's offline tooltip «N записей ждут связи»: only the two
    non-terminal statuses, nothing delivered or failed."""
    for status in (
        DeliveryStatus.PENDING_TRANSCRIBE,
        DeliveryStatus.PENDING_RETRY,
        DeliveryStatus.PENDING_RETRY,
        DeliveryStatus.INSERTED,
        DeliveryStatus.WITHHELD,
        DeliveryStatus.TRANSCRIBE_FAILED,
    ):
        store.add(
            seq=1, created_at=1000, now=1000, duration_ms=800,
            status=status, audio_path=None,
        )
    assert store.count_waiting() == 3


def test_sweep_is_case_insensitive_like_the_filesystem(
    store: HistoryStore, tmp_path: Path
) -> None:
    """Windows paths are case-insensitive; a raw string comparison would call a
    referenced file an orphan over a case difference and delete the only copy
    of the user's words (review round 1)."""
    clip = _clip(tmp_path, "Referenced.flac")
    store.add(
        seq=1, created_at=1000, now=1000, duration_ms=800,
        status=DeliveryStatus.PENDING_TRANSCRIBE,
        audio_path=str(clip).upper(),  # stored in a different case
    )

    removed = store.sweep_orphan_audio(tmp_path)

    assert removed == []
    assert clip.exists(), "same file, different case: not an orphan"


def test_orphan_audio_swept_at_startup(store: HistoryStore, tmp_path: Path) -> None:
    """Spec §3: a file no row points at is deleted; a row whose file is gone is
    left alone — it still holds text worth keeping."""
    referenced = _clip(tmp_path, "referenced.flac")
    orphan = _clip(tmp_path, "orphan.flac")
    store.add(
        seq=1, created_at=1000, now=1000, duration_ms=800,
        status=DeliveryStatus.PENDING_TRANSCRIBE, audio_path=str(referenced),
    )

    removed = store.sweep_orphan_audio(tmp_path)

    assert removed == [orphan]
    assert not orphan.exists()
    assert referenced.exists()


# --------------------------------------------------------------------------- #
# transcription and search
# --------------------------------------------------------------------------- #


def test_transcription_makes_the_row_searchable(store: HistoryStore, tmp_path: Path) -> None:
    clip = _clip(tmp_path, "a.flac")
    rid = store.add(
        seq=1, created_at=1000, now=1500, duration_ms=800,
        status=DeliveryStatus.PENDING_TRANSCRIBE, audio_path=str(clip),
    )
    assert store.search("Бразилии") == []

    store.record_transcription(
        rid, text_raw="перезапусти впс в бразилии", text_final="перезапусти VPS в Бразилии",
        language="ru", provider_id="groq", billed_seconds=1.9, latency_ms=368,
    )
    found = store.search("Бразилии")
    assert [r["id"] for r in found] == [rid]
    assert found[0]["text_raw"] == "перезапусти впс в бразилии"


def test_search_survives_fts_operator_looking_queries(
    store: HistoryStore, tmp_path: Path
) -> None:
    """Review round 1, privacy: raw FTS5 MATCH turned «кое-что» into an
    OperationalError whose *message* carried the user's words — transcript
    substrings, which spec §13 forbids in any log. Quoted tokens keep everyday
    hyphens and colons searchable and word-free on failure."""
    clip = _clip(tmp_path, "a.flac")
    rid = store.add(
        seq=1, created_at=1000, now=1500, duration_ms=800,
        status=DeliveryStatus.PENDING_TRANSCRIBE, audio_path=str(clip),
    )
    store.record_transcription(
        rid, text_raw="сделай кое-что важное", text_final="сделай кое-что важное",
        language="ru", provider_id="groq", billed_seconds=1.0, latency_ms=300,
    )

    assert [r["id"] for r in store.search("кое-что")] == [rid]
    assert store.search("пароль:staging") == []       # no crash, no leak
    assert store.search('NEAR AND OR ("') == []       # operators are just words
    assert store.search("") == []
    assert store.search("важное") == [store.search("важное")[0]]


def test_retry_count_survives_in_the_row(store: HistoryStore, tmp_path: Path) -> None:
    clip = _clip(tmp_path, "a.flac")
    rid = store.add(
        seq=1, created_at=1000, now=1500, duration_ms=800,
        status=DeliveryStatus.PENDING_RETRY, audio_path=str(clip),
    )
    store.increment_retry(rid)
    store.increment_retry(rid)
    assert _row(store, rid)["retry_count"] == 2


# --------------------------------------------------------------------------- #
# config (harness §5)
# --------------------------------------------------------------------------- #


def test_missing_config_created_from_defaults(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    loaded = load_config(path, now_ms=1000)
    assert loaded.created
    assert loaded.config == Config()
    assert path.exists(), "the defaults are written back so the file exists from day one"


def test_corrupt_config_renamed_and_replaced_with_defaults(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text("{not json", encoding="utf-8")

    loaded = load_config(path, now_ms=777)

    assert loaded.corrupt_backup == tmp_path / "config.corrupt-777.json"
    assert loaded.corrupt_backup.exists(), "the broken file is kept for inspection"
    assert loaded.config == Config()
    assert load_config(path, now_ms=778).config == Config(), "and the new file parses"


def test_config_from_the_future_refuses_to_start(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    save_config(path, Config(schema_version=CONFIG_SCHEMA_VERSION + 5))
    with pytest.raises(ConfigTooNew):
        load_config(path, now_ms=1000)


def test_non_integer_schema_version_is_corrupt_not_a_bypass(tmp_path: Path) -> None:
    """'"99"' as a string used to slip past the newer-version gate entirely
    (review round 1): a version field that cannot be trusted means the file
    cannot be trusted — corrupt path, kept for inspection."""
    path = tmp_path / "config.json"
    path.write_text('{"schema_version": "99", "language": "en"}', encoding="utf-8")

    loaded = load_config(path, now_ms=555)

    assert loaded.corrupt_backup == tmp_path / "config.corrupt-555.json"
    assert loaded.config == Config()


def test_saved_config_round_trips_and_tolerates_unknown_keys(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    config = Config(language="en", retention_minutes=120)
    save_config(path, config)

    raw = path.read_text(encoding="utf-8")
    path.write_text(raw.replace('{', '{"from_the_future": true,', 1), encoding="utf-8")

    loaded = load_config(path, now_ms=1000)
    assert loaded.config == config
    assert not list(tmp_path.glob("*.tmp-*")), "atomic write leaves no droppings"


# --------------------------------------------------------------------------- #
# secrets (Credential Manager, live)
# --------------------------------------------------------------------------- #


def test_undecodable_credential_raises_sanitised_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Review round 1, privacy: a blob written by other tooling in UTF-8 used
    to escape as UnicodeDecodeError carrying the complete key in .object —
    rendered whole by repr(). The sanitised error carries the target only,
    and no blob-bearing exception survives on __context__."""
    from billytalk.core.store import secrets

    monkeypatch.setattr(
        secrets.win32cred,
        "CredRead",
        lambda target, ctype: {"CredentialBlob": b"gsk_secret!"},  # odd length
    )
    with pytest.raises(secrets.SecretUndecodable) as excinfo:
        secrets.read_secret("BillyTalk/test-broken")
    assert "gsk" not in repr(excinfo.value)
    assert excinfo.value.__context__ is None, "the blob-carrying exception is gone"
    assert "BillyTalk/test-broken" in str(excinfo.value)


def test_secret_roundtrip_against_the_real_credential_manager() -> None:
    """Round-trips through the actual Windows Credential Manager — the designated
    store (spec §13) — under a throwaway target that is deleted either way."""
    from billytalk.core.store import secrets

    target = f"BillyTalk/test-{uuid.uuid4()}"
    try:
        assert secrets.read_secret(target) is None
        secrets.write_secret(target, "gsk_не-настоящий-ключ")
        assert secrets.read_secret(target) == "gsk_не-настоящий-ключ"
    finally:
        secrets.delete_secret(target)
    assert secrets.read_secret(target) is None
    secrets.delete_secret(target)  # deleting the absent is success, not an error


# --------------------------------------------------------------------------- #
# the deliberate delete (spec §13)
# --------------------------------------------------------------------------- #


def test_clear_all_removes_rows_audio_and_the_search_index(tmp_path) -> None:
    """Spec §13's «очистить историю и аудио». Everywhere else text is forever
    (spec §10) — which is exactly why this action must be complete: a safety
    net the user cannot empty is one they cannot trust with anything private."""
    from billytalk.core.store.db import connect, ensure_schema
    from billytalk.core.store.history import HistoryStore

    audio_dir = tmp_path / "audio"
    audio_dir.mkdir()
    conn = connect(tmp_path / "history.db")
    ensure_schema(conn)
    store = HistoryStore(conn)

    kept_words = "секретная фраза про прод"
    for index in range(3):
        clip = audio_dir / f"clip{index}.flac"
        clip.write_bytes(b"fLaC")
        row_id = store.add(
            seq=index, created_at=1_700_000_000_000 + index, now=1_700_000_000_000,
            duration_ms=1000, status=DeliveryStatus.PENDING_TRANSCRIBE,
            audio_path=str(clip),
        )
        store.record_transcription(
            row_id, text_raw=kept_words, text_final=kept_words, language="ru",
            provider_id="groq", billed_seconds=1.0, latency_ms=100,
        )
    orphan = audio_dir / "nobody-points-here.flac"
    orphan.write_bytes(b"fLaC")

    assert store.search(kept_words), "the text is findable before"

    rows, files = store.clear_all(audio_dir)

    assert rows == 3
    assert files == 4, "three referenced clips plus the orphan"
    assert list(audio_dir.iterdir()) == [], "«и аудио» means all of it"
    assert store.recent() == []
    assert store.search(kept_words) == [], "the FTS index went with the rows"
    assert store.last_shown() is None
    # The table still works afterwards — this is a clear, not a teardown.
    store.add(seq=9, created_at=1_700_000_100_000, now=1_700_000_100_000,
              duration_ms=500, status=DeliveryStatus.PENDING_TRANSCRIBE,
              audio_path=None)
    assert len(store.recent()) == 1


def test_clear_all_on_an_empty_history_is_a_no_op(tmp_path) -> None:
    from billytalk.core.store.db import connect, ensure_schema
    from billytalk.core.store.history import HistoryStore

    conn = connect(tmp_path / "history.db")
    ensure_schema(conn)
    assert HistoryStore(conn).clear_all(tmp_path / "missing-audio-dir") == (0, 0)
