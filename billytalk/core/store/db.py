"""The schema and the connection discipline (harness §4).

``DDL`` below is the single source of truth. The listing in harness §4 documents
it; ``tests/test_store_ddl.py`` executes it and exercises the one genuinely
dangerous part — the external-content FTS5 triggers, where a plain UPDATE or
DELETE silently does nothing and search keeps returning deleted rows.

Two corrections against early redactions of harness §4, both recorded in
OPEN-QUESTIONS: the audio index is on ``audio_release_at`` (§7 — the original
column name did not exist, and SQLite refuses the whole script), and the CHECK
list carries ``withheld`` (spec §10).

The schema is created whole, including the fields mode 2 will need, so that
MVP-0 never has to migrate. The migration machinery still exists — an empty
ladder is cheap, and retrofitting one under live databases is not.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Final

__all__ = ["DDL", "SCHEMA_VERSION", "SchemaTooNew", "connect", "ensure_schema"]

SCHEMA_VERSION: Final = 1

DDL: Final = """
PRAGMA journal_mode=WAL;
PRAGMA user_version=1;

CREATE TABLE history (
  id                INTEGER PRIMARY KEY,
  seq               INTEGER NOT NULL,          -- press order within one process run
  created_at        INTEGER NOT NULL,          -- unix ms, the moment of the press
  -- NULL until transcription: the row is created at StopCapture (spec §3)
  text_raw          TEXT,
  text_final        TEXT,
  language          TEXT,
  provider_id       TEXT,
  duration_ms       INTEGER NOT NULL,          -- known immediately
  billed_seconds    REAL,
  latency_ms        INTEGER,
  target_app        TEXT,                      -- process name, NEVER a window title
  target_window_cls TEXT,
  delivery_status   TEXT    NOT NULL,
  error_code        TEXT,                      -- taxonomy, harness §7
  retry_count       INTEGER NOT NULL DEFAULT 0,-- survives a process restart
  polished          INTEGER NOT NULL DEFAULT 0,
  audio_path        TEXT,                      -- NULL after cleanup released it
  audio_release_at  INTEGER,                   -- start of the one-hour clock; NULL = hold
  CHECK (delivery_status IN (
    'pending_transcribe','pending_retry','inserted','left_on_clipboard','withheld',
    'focus_lost','verify_impossible','blocked_secure','transcribe_failed',
    'cancelled','too_short','empty'))
);

CREATE INDEX idx_history_created ON history(created_at DESC);
-- Column corrected from audio_delivered_at, which does not exist (OPEN-QUESTIONS §7).
CREATE INDEX idx_history_audio   ON history(audio_release_at)
       WHERE audio_path IS NOT NULL;

CREATE VIRTUAL TABLE history_fts USING fts5(
  text_final, content='history', content_rowid='id', tokenize='unicode61'
);
-- EXTERNAL CONTENT: plain UPDATE/DELETE against history_fts silently do nothing
-- (verified on SQLite 3.50.4 — no error, integrity-check passes, search returns
-- deleted rows). The special 'delete' form with the OLD values is mandatory.
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

CREATE TABLE dictionary (
  id    INTEGER PRIMARY KEY,
  type  TEXT NOT NULL CHECK(type IN ('normalize','replace')),
  pat   TEXT NOT NULL,
  repl  TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1
);
"""

_MIGRATIONS: Final[dict[int, Callable[[sqlite3.Connection], None]]] = {}
"""``{from_version: migrate}`` — ``migrate_1_to_2`` will live here when mode 2 needs
it. Empty on purpose: the schema was created whole so MVP-0 never migrates."""


class SchemaTooNew(RuntimeError):
    """The database was written by a newer BillyTalk.

    Same policy as the config file (harness §5): refuse to run rather than guess
    what a future schema means. Guessing wrong risks the one table that holds the
    user's words.
    """

    def __init__(self, found: int) -> None:
        super().__init__(
            f"history.db has schema version {found}, this build understands {SCHEMA_VERSION}"
        )
        self.found = found


def connect(path: str | Path) -> sqlite3.Connection:
    """Open a connection with the per-connection pragmas applied.

    ``journal_mode=WAL`` lives in the DDL because it is a property of the file;
    ``busy_timeout`` and ``secure_delete`` reset with every connection, so they
    belong here. ``secure_delete`` is part of the privacy story (spec §13):
    cleared history must not survive in freed pages.
    """
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA secure_delete=ON")
    return conn


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Create the schema on a fresh database, migrate an old one, refuse a newer one."""
    version: int = conn.execute("PRAGMA user_version").fetchone()[0]
    if version == 0:
        conn.executescript(DDL)
        version = conn.execute("PRAGMA user_version").fetchone()[0]

    while version < SCHEMA_VERSION:
        migrate = _MIGRATIONS.get(version)
        if migrate is None:  # pragma: no cover - impossible until a migration exists
            raise RuntimeError(f"no migration from schema version {version}")
        migrate(conn)
        version += 1
        conn.execute(f"PRAGMA user_version={version}")
        conn.commit()

    if version > SCHEMA_VERSION:
        raise SchemaTooNew(version)
