"""The four storage tests named in harness §8.

``store/db.py`` is cycle-1 item 8 and is not built yet, but three of these four
names describe the *schema*, not the code — and the schema is where the trap is.
Harness §4 records it: with an external-content FTS5 table, a plain UPDATE or
DELETE against ``history_fts`` silently does nothing. No error, no failed
integrity check, and deleted rows keep coming back from search. Verifying that
the triggers really carry the special ``'delete'`` form is worth doing before any
code depends on them.

The DDL below is copied from harness §4 verbatim, except for one correction noted
inline and in OPEN-QUESTIONS §7.
"""

from __future__ import annotations

import sqlite3

import pytest

DDL = """
PRAGMA journal_mode=WAL;
PRAGMA user_version=1;

CREATE TABLE history (
  id                INTEGER PRIMARY KEY,
  seq               INTEGER NOT NULL,
  created_at        INTEGER NOT NULL,
  text_raw          TEXT,
  text_final        TEXT,
  language          TEXT,
  provider_id       TEXT,
  duration_ms       INTEGER NOT NULL,
  billed_seconds    REAL,
  latency_ms        INTEGER,
  target_app        TEXT,
  target_window_cls TEXT,
  delivery_status   TEXT    NOT NULL,
  error_code        TEXT,
  retry_count       INTEGER NOT NULL DEFAULT 0,
  polished          INTEGER NOT NULL DEFAULT 0,
  audio_path        TEXT,
  audio_release_at  INTEGER,
  CHECK (delivery_status IN (
    'pending_transcribe','pending_retry','inserted','left_on_clipboard',
    'focus_lost','verify_impossible','blocked_secure','transcribe_failed',
    'cancelled','too_short','empty'))
);

CREATE INDEX idx_history_created ON history(created_at DESC);
-- harness §4 indexes `audio_delivered_at`, a column that does not exist in the
-- table it indexes. Corrected to `audio_release_at` (OPEN-QUESTIONS §7).
CREATE INDEX idx_history_audio   ON history(audio_release_at)
       WHERE audio_path IS NOT NULL;

CREATE VIRTUAL TABLE history_fts USING fts5(
  text_final, content='history', content_rowid='id', tokenize='unicode61'
);

CREATE TRIGGER history_ai AFTER INSERT ON history BEGIN
  INSERT INTO history_fts(rowid, text_final) VALUES (new.id, new.text_final);
END;
CREATE TRIGGER history_ad AFTER DELETE ON history BEGIN
  INSERT INTO history_fts(history_fts, rowid, text_final)
    VALUES('delete', old.id, old.text_final);
END;
CREATE TRIGGER history_au AFTER UPDATE OF text_final ON history BEGIN
  INSERT INTO history_fts(history_fts, rowid, text_final)
    VALUES('delete', old.id, old.text_final);
  INSERT INTO history_fts(rowid, text_final) VALUES (new.id, new.text_final);
END;
"""

CLEANUP_SQL = """
DELETE FROM history
 WHERE audio_release_at IS NOT NULL
   AND audio_release_at < :now - :retention_minutes * 60000
"""


@pytest.fixture
def db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript(DDL)
    return conn


def _add(conn: sqlite3.Connection, rid: int, text: str, **cols: object) -> None:
    row = {
        "id": rid,
        "seq": rid,
        "created_at": 1000 * rid,
        "text_final": text,
        "duration_ms": 5000,
        "delivery_status": "inserted",
        "audio_path": f"audio/{rid}.flac",
        "audio_release_at": None,
    }
    row.update(cols)
    keys = ", ".join(row)
    binds = ", ".join(f":{k}" for k in row)
    conn.execute(f"INSERT INTO history ({keys}) VALUES ({binds})", row)


def _search(conn: sqlite3.Connection, term: str) -> list[int]:
    return [r[0] for r in conn.execute("SELECT rowid FROM history_fts WHERE history_fts MATCH ?", (term,))]


def test_fts_delete_removes_row_from_search() -> None:
    conn = sqlite3.connect(":memory:")
    conn.executescript(DDL)
    _add(conn, 1, "перезапусти впээс в Бразилии")
    assert _search(conn, "Бразилии") == [1]

    conn.execute("DELETE FROM history WHERE id = 1")
    assert _search(conn, "Бразилии") == [], "search must not return a deleted row"


def test_fts_update_removes_old_tokens() -> None:
    """The failure mode harness §4 warns about: old tokens surviving an update."""
    conn = sqlite3.connect(":memory:")
    conn.executescript(DDL)
    _add(conn, 1, "выкати на прот")

    conn.execute("UPDATE history SET text_final = ? WHERE id = 1", ("выкати на прод",))
    assert _search(conn, "прод") == [1]
    assert _search(conn, "прот") == [], "the pre-update token must be gone from the index"


def test_cleanup_skips_rows_with_null_release_at() -> None:
    """``NULL`` means hold (spec §3): the dictation has not been transcribed yet, so
    the audio file is the only copy of what the user said."""
    conn = sqlite3.connect(":memory:")
    conn.executescript(DDL)
    now = 10_000_000
    _add(conn, 1, "delivered long ago", audio_release_at=now - 3 * 3_600_000)
    _add(conn, 2, "still waiting", audio_release_at=None,
         delivery_status="pending_transcribe")

    conn.execute(CLEANUP_SQL, {"now": now, "retention_minutes": 60})
    survivors = [r[0] for r in conn.execute("SELECT id FROM history ORDER BY id")]
    assert survivors == [2]


@pytest.mark.skip(
    reason="Needs store/db.py — cycle 1 item 8, deliberately out of scope for this "
           "module. The policy under test (spec §3: cleanup does not run at all "
           "while offline, and resumes ten minutes after the first successful "
           "transcription) is a scheduling decision, not a schema property, so "
           "unlike the three above it cannot be expressed against the DDL alone."
)
def test_cleanup_paused_while_offline() -> None:
    raise NotImplementedError
