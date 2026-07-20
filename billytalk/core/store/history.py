"""History rows, the audio-release clock, and the cleanup that knows when not to run.

Three spec rules shape everything here:

* **Durability first** (spec §3): the row is born at ``StopCapture``, before any
  network call, and all three escape hatches grow out of it.
* **Text is forever** (spec §10): cleanup releases *audio* — it sets
  ``audio_path`` to NULL and removes the file. It never deletes a row. Early
  harness §4 wrote the cleanup as ``DELETE FROM history``, which would have
  destroyed the permanent text history (OPEN-QUESTIONS §15).
* **Never while offline** (spec §3): while the network is down, cleanup does not
  run *at all* — not even for delivered rows — because a growing offline queue is
  exactly when an audio file is likeliest to be the only copy of the words.
  :class:`CleanupGate` owns that decision.

``seq`` numbers restart with the process, so they are not unique across the
table's lifetime. Rows are addressed by ``id``; the driver keeps its own
``seq -> id`` map for the dictations of the current run.
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from ..machine.effects import DeliveryStatus

__all__ = [
    "CleanupGate",
    "HistoryStore",
    "RELEASES_AUDIO",
    "HOLDS_AUDIO",
]

RELEASES_AUDIO: Final = frozenset(
    {
        DeliveryStatus.INSERTED,
        DeliveryStatus.LEFT_ON_CLIPBOARD,
        DeliveryStatus.WITHHELD,
        DeliveryStatus.FOCUS_LOST,
        DeliveryStatus.VERIFY_IMPOSSIBLE,
        DeliveryStatus.BLOCKED_SECURE,
        DeliveryStatus.CANCELLED,
        DeliveryStatus.TOO_SHORT,
        DeliveryStatus.EMPTY,
    }
)
"""Statuses that start the one-hour audio clock.

The delivered ones are harness §4's list plus ``withheld`` (OPEN-QUESTIONS §13).
``cancelled`` is the "explicit refusal by the user" of spec §3's holding rule, and
``too_short``/``empty`` will never be transcribed at all — holding their audio
forever would be a slow disk leak with no path to release (OPEN-QUESTIONS §15).
"""

HOLDS_AUDIO: Final = frozenset(
    {
        DeliveryStatus.PENDING_TRANSCRIBE,
        DeliveryStatus.PENDING_RETRY,
        DeliveryStatus.TRANSCRIBE_FAILED,
    }
)
"""NULL ``audio_release_at``: the audio may be the only copy of the words.

``transcribe_failed`` holds too — an invalid key is fixable, and the user retries
from the history window once it is.
"""


class CleanupGate:
    """Decides whether cleanup may run at all (spec §3).

    The rule, verbatim: while the network is down, cleanup does not start — even
    for delivered rows — and it resumes ten minutes after the first successful
    transcription. A successful transcription is the only proof of connectivity
    this process trusts; a winsock flag is not it.

    The gate starts open: a fresh process assumes the network works until a
    transcription says otherwise, because the alternative — never cleaning until
    the user dictates — lets audio of a heavy user pile up for no reason.
    """

    def __init__(self, resume_delay_ms: int = 10 * 60 * 1000) -> None:
        self._resume_delay_ms = resume_delay_ms
        self._offline = False
        self._resume_at: int | None = None

    @property
    def offline(self) -> bool:
        return self._offline

    def network_lost(self) -> None:
        """A transcription failed with a network error. Cleanup stops immediately."""
        self._offline = True
        self._resume_at = None

    def transcribe_succeeded(self, now: int) -> None:
        """Connectivity proven. If we were offline, cleanup resumes after the delay.

        Only the *first* success after an outage arms the delay; steady-state
        successes must not keep pushing cleanup into the future.
        """
        if self._offline:
            self._offline = False
            self._resume_at = now + self._resume_delay_ms


    def should_run(self, now: int) -> bool:
        if self._offline:
            return False
        return self._resume_at is None or now >= self._resume_at


@dataclass(frozen=True, slots=True)
class PendingRow:
    """A row found in a non-terminal status at startup (spec §3)."""

    id: int
    seq: int
    created_at: int
    duration_ms: int
    audio_path: str | None
    delivery_status: str
    retry_count: int


class HistoryStore:
    """Every query the core runs against ``history``.

    All times are unix milliseconds passed in by the caller — this module never
    reads a clock, for the same reason the state machine never does: the tests
    turn the dial by hand and never sleep (harness §8).
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    # ------------------------------------------------------------------ #
    # writing
    # ------------------------------------------------------------------ #

    def add(
        self,
        *,
        seq: int,
        created_at: int,
        now: int,
        duration_ms: int,
        status: DeliveryStatus,
        audio_path: str | None,
        target_app: str | None = None,
        target_window_cls: str | None = None,
    ) -> int:
        """Create the row ``WriteHistory`` asked for and return its ``id``.

        ``created_at`` is the moment of the press; ``now`` is the moment of this
        write, used only to start the audio clock for statuses that are already
        terminal at birth (``too_short``, ``empty``, ``cancelled``).
        """
        release_at = now if status in RELEASES_AUDIO else None
        with self._conn:
            cur = self._conn.execute(
                """
                INSERT INTO history (seq, created_at, duration_ms, delivery_status,
                                     audio_path, audio_release_at, target_app,
                                     target_window_cls)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    seq,
                    created_at,
                    duration_ms,
                    status.value,
                    audio_path,
                    release_at,
                    target_app,
                    target_window_cls,
                ),
            )
        rowid = cur.lastrowid
        assert rowid is not None
        return rowid

    def record_transcription(
        self,
        row_id: int,
        *,
        text_raw: str,
        text_final: str,
        language: str | None,
        provider_id: str,
        billed_seconds: float | None,
        latency_ms: int | None,
    ) -> None:
        """Attach the transcription. The FTS trigger fires on ``text_final``."""
        with self._conn:
            self._conn.execute(
                """
                UPDATE history
                   SET text_raw = ?, text_final = ?, language = ?, provider_id = ?,
                       billed_seconds = ?, latency_ms = ?
                 WHERE id = ?
                """,
                (text_raw, text_final, language, provider_id, billed_seconds, latency_ms, row_id),
            )

    def set_polished_text(self, row_id: int, text_final: str) -> None:
        """Replace the final text after polishing; the raw text stays untouched."""
        with self._conn:
            self._conn.execute(
                "UPDATE history SET text_final = ?, polished = 1 WHERE id = ?",
                (text_final, row_id),
            )

    def set_status(
        self,
        row_id: int,
        status: DeliveryStatus,
        *,
        now: int,
        error_code: str | None = None,
    ) -> None:
        """Move the row to ``status``; start the audio clock if it just became due.

        ``COALESCE`` keeps an already-running clock: a status can lawfully change
        after delivery (history re-insert, say) and must not grant the audio a
        fresh hour every time.
        """
        release_at = now if status in RELEASES_AUDIO else None
        with self._conn:
            self._conn.execute(
                """
                UPDATE history
                   SET delivery_status = ?,
                       error_code = ?,
                       audio_release_at = COALESCE(audio_release_at, ?)
                 WHERE id = ?
                """,
                (status.value, error_code, release_at, row_id),
            )

    def increment_retry(self, row_id: int) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE history SET retry_count = retry_count + 1 WHERE id = ?", (row_id,)
            )

    # ------------------------------------------------------------------ #
    # reading
    # ------------------------------------------------------------------ #

    def pending_at_startup(self) -> list[PendingRow]:
        """Rows the last run never finished (spec §3): queue them for retry.

        Ordered by press time — delivery order is press order, and that rule does
        not stop applying just because the process restarted.
        """
        rows = self._conn.execute(
            """
            SELECT id, seq, created_at, duration_ms, audio_path, delivery_status, retry_count
              FROM history
             WHERE delivery_status IN ('pending_transcribe', 'pending_retry')
             ORDER BY created_at
            """
        ).fetchall()
        return [PendingRow(*row) for row in rows]

    def count_waiting(self) -> int:
        """How many rows wait for the network — the ``N`` in spec §3's offline
        tooltip «N записей ждут связи». The same non-terminal statuses the
        startup scan re-queues (:meth:`pending_at_startup`), counted without
        materialising the rows."""
        (count,) = self._conn.execute(
            "SELECT COUNT(*) FROM history "
            "WHERE delivery_status IN ('pending_transcribe', 'pending_retry')"
        ).fetchone()
        return int(count)

    def search(self, query: str, *, limit: int = 50, offset: int = 0) -> list[sqlite3.Row]:
        """FTS5 search over the final text (harness §4: FTS, not LIKE).

        The query is neutralised before ``MATCH``: raw FTS5 syntax turns
        everyday input into an ``OperationalError`` whose *message* carries the
        user's words — «кое-что» raises ``no such column: что`` — and search
        terms are transcript substrings, which spec §13 forbids in any log.
        The RedactionFilter is structurally blind to words inside a stdlib
        exception string, so the words must never enter one. Users type words,
        not FTS operators; each whitespace token becomes a quoted phrase.
        """
        tokens = query.split()
        if not tokens:
            return []
        safe = " ".join('"' + token.replace('"', '""') + '"' for token in tokens)
        try:
            return self._conn.execute(
                """
                SELECT h.* FROM history_fts f JOIN history h ON h.id = f.rowid
                 WHERE history_fts MATCH ?
                 ORDER BY h.created_at DESC LIMIT ? OFFSET ?
                """,
                (safe, limit, offset),
            ).fetchall()
        except sqlite3.OperationalError:
            # Belt and braces: whatever still slipped past the quoting must
            # not escape carrying the user's words. No results is a safe answer.
            return []

    def recent(self, *, limit: int = 50, offset: int = 0) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM history ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()

    # ------------------------------------------------------------------ #
    # audio lifecycle
    # ------------------------------------------------------------------ #

    def cleanup(self, *, now: int, retention_minutes: int) -> list[str]:
        """Release expired audio. Returns the paths whose files were removed.

        Never deletes a row (spec §10: text is forever). The row's ``audio_path``
        is cleared inside the transaction *before* the file is unlinked: if the
        process dies between the two, the file is an orphan and the startup sweep
        collects it — the reverse order would leave a path pointing at nothing.

        The caller consults :class:`CleanupGate` first; this method does not know
        about the network and should not (one reason to skip must not hide the
        other).
        """
        cutoff = now - retention_minutes * 60_000
        rows = self._conn.execute(
            """
            SELECT id, audio_path FROM history
             WHERE audio_path IS NOT NULL
               AND audio_release_at IS NOT NULL
               AND audio_release_at < ?
            """,
            (cutoff,),
        ).fetchall()
        if not rows:
            return []
        with self._conn:
            self._conn.executemany(
                "UPDATE history SET audio_path = NULL WHERE id = ?",
                [(row["id"],) for row in rows],
            )
        released: list[str] = []
        for row in rows:
            path = row["audio_path"]
            _remove_quietly(path)
            released.append(path)
        return released

    def evict_over_cap(
        self,
        *,
        max_rows: int = 500,
        max_bytes: int = 2 * 1024**3,
    ) -> list[str]:
        """Enforce the retention ceiling (spec §3): 500 clips or 2 GB.

        Oldest audio goes first, newest survives. Returns the released paths so
        the driver can notify — eviction is the one cleanup the user must hear
        about, because it can take audio that was still being held for retry.
        """
        rows = self._conn.execute(
            """
            SELECT id, audio_path FROM history
             WHERE audio_path IS NOT NULL
             ORDER BY created_at DESC
            """
        ).fetchall()
        keep_budget = max_bytes
        victims: list[sqlite3.Row] = []
        for i, row in enumerate(rows):
            size = _size_quietly(row["audio_path"])
            if i >= max_rows or size > keep_budget:
                victims.append(row)
            else:
                keep_budget -= size
        if not victims:
            return []
        with self._conn:
            self._conn.executemany(
                "UPDATE history SET audio_path = NULL WHERE id = ?",
                [(row["id"],) for row in victims],
            )
        released = []
        for row in victims:
            _remove_quietly(row["audio_path"])
            released.append(row["audio_path"])
        return released

    def sweep_orphan_audio(self, audio_dir: Path) -> list[Path]:
        """Startup GC (spec §3): a file no row points at is deleted.

        The reverse — a row whose file is gone — is left alone: the row still
        holds text or a status worth keeping, and ``audio_path`` pointing at
        nothing is handled wherever audio is read.
        """
        if not audio_dir.is_dir():
            return []
        # Windows paths are case-insensitive; a raw string comparison would
        # call a referenced file an orphan over a case difference and delete
        # the only copy of the user's words. normcase+abspath on both sides.
        referenced = {
            os.path.normcase(os.path.abspath(row[0]))
            for row in self._conn.execute(
                "SELECT audio_path FROM history WHERE audio_path IS NOT NULL"
            )
        }
        removed: list[Path] = []
        for file in audio_dir.iterdir():
            if not file.is_file():
                continue
            if os.path.normcase(os.path.abspath(file)) in referenced:
                continue
            _remove_quietly(str(file))
            removed.append(file)
        return removed


def _remove_quietly(path: str) -> None:
    """Missing is fine — the sweep and the cleanup may race a manual delete."""
    try:
        os.remove(path)
    except OSError:
        pass


def _size_quietly(path: str) -> int:
    try:
        return os.path.getsize(path)
    except OSError:
        return 0
