"""Schema-level tests (harness §8).

The DDL now lives in ``billytalk.core.store.db`` and these tests execute the real
thing, not a copy — the earlier redaction of this file carried the harness §4
listing verbatim because ``store/`` did not exist yet.

The part worth guarding is the external-content FTS5 pair of traps (harness §4):
a plain UPDATE or DELETE against ``history_fts`` silently does nothing — no
error, a passing integrity check, and search that keeps returning deleted rows.
The triggers must use the special ``'delete'`` form with the OLD values, and only
a test that actually searches can prove they do.

Cleanup policy tests live in ``test_store.py``: cleanup is code now, not a
property of the schema.
"""

from __future__ import annotations

import sqlite3

import pytest

from billytalk.core.machine.effects import DeliveryStatus
from billytalk.core.store.db import SCHEMA_VERSION, SchemaTooNew, connect, ensure_schema


def _fresh() -> sqlite3.Connection:
    conn = connect(":memory:")
    ensure_schema(conn)
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
    return [
        r[0]
        for r in conn.execute(
            "SELECT rowid FROM history_fts WHERE history_fts MATCH ?", (term,)
        )
    ]


def test_ddl_executes_whole_and_stamps_the_version() -> None:
    """The script must run end to end — the bug of OPEN-QUESTIONS §7 was an index
    on a column that does not exist, which kills the whole script, not one index."""
    conn = _fresh()
    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    tables = {
        r[0]
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert {"history", "dictionary", "history_fts"} <= tables


def test_fts_delete_removes_row_from_search() -> None:
    conn = _fresh()
    _add(conn, 1, "перезапусти впээс в Бразилии")
    assert _search(conn, "Бразилии") == [1]

    conn.execute("DELETE FROM history WHERE id = 1")
    assert _search(conn, "Бразилии") == [], "search must not return a deleted row"


def test_fts_update_removes_old_tokens() -> None:
    """The failure mode harness §4 warns about: old tokens surviving an update."""
    conn = _fresh()
    _add(conn, 1, "выкати на прот")

    conn.execute("UPDATE history SET text_final = ? WHERE id = 1", ("выкати на прод",))
    assert _search(conn, "прод") == [1]
    assert _search(conn, "прот") == [], "the pre-update token must be gone from the index"


@pytest.mark.parametrize("status", list(DeliveryStatus))
def test_every_delivery_status_is_accepted_by_the_check(status: DeliveryStatus) -> None:
    """The enum and the CHECK are two spellings of one list; drift between them
    turns a lawful state transition into an IntegrityError at delivery time."""
    conn = _fresh()
    _add(conn, 1, "текст", delivery_status=status.value)


def test_unknown_delivery_status_is_rejected() -> None:
    conn = _fresh()
    with pytest.raises(sqlite3.IntegrityError):
        _add(conn, 1, "текст", delivery_status="misdelivered")


def test_schema_from_the_future_is_refused() -> None:
    """Same policy as the config (harness §5): a downgrade must not guess."""
    conn = connect(":memory:")
    conn.execute(f"PRAGMA user_version={SCHEMA_VERSION + 1}")
    with pytest.raises(SchemaTooNew):
        ensure_schema(conn)


def test_version_stamp_is_the_final_statement_of_the_ddl() -> None:
    """A script interrupted mid-way must leave version 0 so the next start
    re-runs the DDL. Stamped first, a half-created schema reads as version 1
    and is never repaired (review round 1)."""
    from billytalk.core.store.db import DDL

    stamp = DDL.rindex("PRAGMA user_version")
    assert stamp > DDL.rindex("CREATE TABLE")
    assert stamp > DDL.rindex("CREATE TRIGGER")
    assert stamp > DDL.rindex("CREATE INDEX")
