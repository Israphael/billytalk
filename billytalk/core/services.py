"""The milestone-2 UI verbs (harness §3): config, dictionary, history.

Threading is the whole design here. Verbs arrive on the IPC server's read
thread; SQLite's write connection, the clipboard and the inserter all belong
to the driver thread. So:

- **Read verbs** (``get_config``, ``dictionary_get``, ``history_search``,
  ``history_export``) answer synchronously on the read thread. History reads
  go through a private **read-only** SQLite connection (WAL: readers never
  block the driver's writer) — the driver's connection is never touched.
- **Write and delivery verbs** (``set_config`` file save aside,
  ``dictionary_set``, ``history_insert``) marshal onto the driver thread via
  ``post_job`` and send their reply from there. ``post_job`` rides the event
  queue, so a history insert is serialised with ordinary delivery — which is
  exactly spec §8's «операция с буфером — глобальный мьютекс», by
  construction rather than by lock.

Privacy: replies carry transcript text (the history window shows it — that is
the «интерфейс запрашивает содержимое отдельно» of harness §2), but nothing
here ever logs it; log lines carry verbs, ids and counts only (spec §13).

``history_export`` validates its path (spec §14: the path must come from the
system file dialog, and the core validates it — otherwise the verb is an
arbitrary-file-write primitive): absolute, an existing parent directory, an
extension that matches the declared format, written atomically.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import os
import sqlite3
import threading
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final

from .autostart import autostart_state, set_autostart
from .insert.apprules import rule_for
from .insert.inserter import prepare_text
from .ipc.protocol import reply
from .machine.effects import Cue
from .store.config import Config, save_config
from .text.dictionary import Dictionary, Rule
from .ui_language import UI_LANGUAGE_SETTINGS, resolve_ui_language

log = logging.getLogger("billytalk.services")

__all__ = ["UiServices", "SERVICE_VERBS"]

SERVICE_VERBS: Final = frozenset({
    "get_config", "set_config",
    "history_search", "history_insert", "history_export",
    "dictionary_get", "dictionary_set",
    "capture_hotkey_start", "capture_hotkey_stop",
    "mic_probe", "set_key", "test_key",
    "autostart_get", "autostart_set",
    "audio_devices", "history_clear",
})

_SEARCH_CAP: Final = 200
"""One reply frame must stay far under MAX_FRAME_BYTES; 200 rows of history
are tens of kilobytes."""

_RULE_CAP: Final = 500
"""More custom rules than transcripts is a runaway client, not a dictionary."""

_EXPORT_FORMATS: Final = frozenset({"txt", "csv", "json"})

# What set_config may touch, with a validator each: everything else in Config
# is either wizard/cycle-3 territory (ptt_code changes ride the M3 capture
# verbs, not a raw patch) or internal (schema_version, usage, caps).
#
# groq_model is deliberately NOT here: GroqProvider binds its model at
# construction and apply_config_to_deps does not refresh it, so patching it
# would drift the running provider (old model) from what get_config shows
# (new) until restart. No shipped control sends it (the model row is a label);
# a live model switch is cycle-3 work with a provider refresh (cue-review §33).
_PATCHABLE: Final[dict[str, Callable[[Any], bool]]] = {
    "language": lambda v: v in ("ru", "en"),
    "ui_language": lambda v: v in UI_LANGUAGE_SETTINGS,
    "wizard_done": lambda v: isinstance(v, bool),
    "audio_input_device": lambda v: v is None or (isinstance(v, str) and len(v) < 256),
    "polish_enabled": lambda v: isinstance(v, bool),
    "press_enter_after": lambda v: isinstance(v, bool),
    "retention_minutes": lambda v: isinstance(v, int) and not isinstance(v, bool)
    and 1 <= v <= 7 * 24 * 60,
    "max_hold_ms": lambda v: isinstance(v, int) and not isinstance(v, bool)
    and 1_000 <= v <= 60 * 60 * 1000,
}

_CONFIG_VIEW: Final = (
    "language", "ui_language", "wizard_done", "ptt_code", "audio_input_device",
    "groq_model", "polish_enabled", "press_enter_after", "retention_minutes",
    "max_hold_ms",
)
"""What get_config shows: the patchable set plus the read-only bindings the
settings window displays (ptt_code — changed via M3 capture, shown here)."""


class UiServices:
    """The verb executor behind :class:`~billytalk.core.wiring.UiMessageRouter`.

    Every collaborator is injected as the narrowest callable that serves, so
    the whole thing tests against an in-memory world; ``__main__`` hands in
    the real ones.
    """

    def __init__(
        self,
        *,
        config: Config,
        config_path: Path,
        db_path: Path,
        post_job: Callable[[Callable[[], None]], None],
        send: Callable[[dict[str, Any]], bool],
        # driver-thread collaborators — used ONLY inside post_job closures:
        store_get: Callable[[int], sqlite3.Row | None],
        clipboard_write: Callable[[str], Any],
        insert: Callable[[Any, Any, str], Any],
        last_target: Callable[[], Any],
        play_cue: Callable[[Cue], None],
        swap_dictionary: Callable[[Dictionary], None],
        save_dictionary: Callable[[Dictionary], None],
        # read-thread collaborators:
        current_dictionary: Callable[[], Dictionary],
        apply_config_to_deps: Callable[[], None],
        has_groq_key: Callable[[], bool],
        hotkey_capture: Any | None = None,
        # cycle-3 (wizard) collaborators — all optional so the milestone-2
        # tests build UiServices exactly as they did:
        submit_background: Callable[[Callable[[], None]], None] | None = None,
        probe_microphone: Callable[[], dict[str, Any]] | None = None,
        write_groq_key: Callable[[str], None] | None = None,
        check_groq_key: Callable[[], str] | None = None,
        list_input_devices: Callable[[], list[str]] | None = None,
        clear_history: Callable[[], tuple[int, int]] | None = None,
    ) -> None:
        self._config = config
        self._config_path = config_path
        self._db_path = db_path
        self._post_job = post_job
        self._send = send
        self._store_get = store_get
        self._clipboard_write = clipboard_write
        self._insert = insert
        self._last_target = last_target
        self._play_cue = play_cue
        self._swap_dictionary = swap_dictionary
        self._save_dictionary = save_dictionary
        self._current_dictionary = current_dictionary
        self._apply_config_to_deps = apply_config_to_deps
        self._has_groq_key = has_groq_key
        self._hotkey_capture = hotkey_capture
        self._submit_background = submit_background
        self._probe_microphone = probe_microphone
        self._write_groq_key = write_groq_key
        self._check_groq_key = check_groq_key
        self._list_input_devices = list_input_devices
        self._clear_history = clear_history
        self._ro_lock = threading.Lock()
        self._ro_conn: sqlite3.Connection | None = None

    # ------------------------------------------------------------------ #
    # entry (server read thread)
    # ------------------------------------------------------------------ #

    def handle(self, kind: str, message: dict[str, Any]) -> dict[str, Any] | None:
        request_id = message.get("id")
        rid = request_id if isinstance(request_id, int) else None
        if kind == "get_config":
            return self._reply_frame(rid, self._config_view())
        if kind == "set_config":
            return self._set_config(rid, message.get("patch"))
        if kind == "dictionary_get":
            return self._reply_frame(rid, self._dictionary_view())
        if kind == "dictionary_set":
            return self._dictionary_set(rid, message.get("rules"))
        if kind == "history_search":
            return self._history_search(rid, message)
        if kind == "history_insert":
            # "row_id", not the harness §3 literal "{id}": "id" is the request
            # id since OPEN-QUESTIONS §21 claimed it — recorded as OQ §28.
            return self._history_insert(rid, message.get("row_id"))
        if kind == "history_export":
            return self._history_export(rid, message.get("format"), message.get("path"))
        if kind == "mic_probe":
            return self._mic_probe(rid)
        if kind == "set_key":
            return self._set_key(rid, message.get("key"))
        if kind == "test_key":
            return self._test_key(rid)
        if kind == "audio_devices":
            return self._reply_frame(rid, {
                "inputs": list(self._list_input_devices())
                if self._list_input_devices is not None else [],
            })
        if kind == "history_clear":
            return self._history_clear(rid)
        if kind == "autostart_get":
            return self._reply_frame(rid, autostart_state().as_wire())
        if kind == "autostart_set":
            return self._autostart_set(rid, message.get("enabled"))
        if kind == "capture_hotkey_start" and self._hotkey_capture is not None:
            self._hotkey_capture.start(rid, message.get("action"))
            return None  # the reply comes from the driver-thread job
        if kind == "capture_hotkey_stop" and self._hotkey_capture is not None:
            self._hotkey_capture.stop(rid)
            return None
        return reply(rid, error="unimplemented") if rid is not None else None

    def _reply_frame(
        self, rid: int | None, result: dict[str, Any]
    ) -> dict[str, Any] | None:
        return reply(rid, result) if rid is not None else None

    def _send_reply(self, rid: int | None, *, result: dict[str, Any] | None = None,
                    error: str | None = None) -> None:
        """From a driver-thread job: answer over the channel, if asked to."""
        if rid is not None:
            self._send(reply(rid, result, error=error))

    # ------------------------------------------------------------------ #
    # config
    # ------------------------------------------------------------------ #

    def _config_view(self) -> dict[str, Any]:
        view = {name: getattr(self._config, name) for name in _CONFIG_VIEW}
        # The key itself never crosses the channel (spec §13) — presence does.
        view["has_groq_key"] = bool(self._has_groq_key())
        # «auto» is resolved HERE, on the core's side of harness §1's border:
        # reading the Windows UI language is a ctypes call, and the interface
        # must never make one for a label. It receives an answer, not a rule.
        view["ui_language_effective"] = resolve_ui_language(self._config.ui_language)
        return {"config": view}

    def _set_config(self, rid: int | None, patch: object) -> dict[str, Any] | None:
        if not isinstance(patch, dict) or not patch:
            return reply(rid, error="bad_patch") if rid is not None else None
        for key, value in patch.items():
            validator = _PATCHABLE.get(key)
            if validator is None or not validator(value):
                log.warning("set_config rejected key %r", key)  # key only, no value
                return reply(rid, error="bad_patch") if rid is not None else None
        # Atomic against a failed write: mutate live state only if it stuck —
        # otherwise memory holds the new values while disk keeps the old, and
        # since save_config serialises the WHOLE Config the rejected patch would
        # ride the next successful save (silent late commit). Mirror
        # apply_ptt_binding: snapshot, restore on failure, re-raise so _dispatch
        # still answers internal_error but every copy stays consistent.
        previous = {key: getattr(self._config, key) for key in patch}
        for key, value in patch.items():
            setattr(self._config, key, value)
        try:
            save_config(self._config_path, self._config)
        except Exception:
            for key, value in previous.items():
                setattr(self._config, key, value)
            raise
        # The deps copies (language, fuses) refresh on the driver thread; the
        # config object itself is already live for every closure that reads it.
        self._post_job(self._apply_config_to_deps)
        return self._reply_frame(rid, self._config_view())

    # ------------------------------------------------------------------ #
    # the wizard's verbs (spec §12)
    # ------------------------------------------------------------------ #

    def _background(self, job: Callable[[], None], rid: int | None) -> None:
        """Run ``job`` off the server's read thread.

        Everything below can block for a noticeable time — opening an audio
        device wakes a Bluetooth headset, checking the key is a network round
        trip — and the read thread is the single lane every other verb arrives
        on. Blocking it would freeze the whole interface behind a wizard step.
        """
        if self._submit_background is None:
            self._send_reply(rid, error="unavailable")
            return
        self._submit_background(job)

    def _mic_probe(self, rid: int | None) -> None:
        """Wizard step 1: does the microphone actually open (spec §12)?

        The probe is the injected collaborator's job; this method only decides
        who runs it and how the answer travels. A probe that raises answers
        ``mic_busy`` — the wizard's «занято другим приложением» branch — rather
        than leaving the step waiting forever on a reply that never comes.
        """
        if self._probe_microphone is None:
            self._send_reply(rid, error="unavailable")
            return None

        def job() -> None:
            try:
                result = self._probe_microphone()
            except Exception:
                log.exception("microphone probe failed")
                result = {"ok": False, "code": "mic_busy", "device": None, "inputs": []}
            self._send_reply(rid, result=result)

        self._background(job, rid)
        return None

    def _set_key(self, rid: int | None, key: object) -> None:
        """Wizard step 6: store the Groq key in the Credential Manager.

        The key exists in this method as a parameter and nowhere else: it is
        never logged, never echoed back in the reply, never put into an
        exception message. The reply says «stored», and a later ``get_config``
        says «has_groq_key» — presence, never the value (spec §13).
        """
        if not isinstance(key, str) or not key.strip():
            self._send_reply(rid, error="bad_key")
            return None
        key = key.strip()
        if not (key.isascii() and key.isprintable()):
            # An API key is printable ASCII. Anything else is a mangled paste —
            # a newline that came along with the copy, a Cyrillic character
            # from a mis-typed one — and it is refused HERE, at the boundary,
            # because further down `http.client.putheader` raises with the
            # whole header value (the key) inside its message (security
            # review, medium). The provider has its own guard; this one means
            # the key never gets that far in the first place.
            log.warning("set_config rejected a key that is not printable ASCII")
            self._send_reply(rid, error="bad_key")
            return None
        if self._write_groq_key is None:
            self._send_reply(rid, error="unavailable")
            return None
        try:
            self._write_groq_key(key)
        except Exception:
            # No exc_info: a credential-write failure can carry the value in
            # its arguments, and this line goes to a file (spec §13).
            log.warning("storing the API key failed")
            self._send_reply(rid, error="store_failed")
            return None
        log.info("api key stored")
        self._send_reply(rid, result={"stored": True})
        return None

    def _test_key(self, rid: int | None) -> None:
        """Wizard step 6, «проверка запросом»: ask Groq whether the stored key
        is any good. Answers a status word, never a payload."""
        if self._check_groq_key is None:
            self._send_reply(rid, error="unavailable")
            return None

        def job() -> None:
            try:
                status = self._check_groq_key()
            except Exception:
                # log.warning, never log.exception: the only exception this
                # call can raise which is not already handled inside the
                # provider is one whose message quotes the key (security
                # review, medium). A traceback here could describe nothing
                # else useful, and could describe the secret.
                log.warning("key check failed")
                status = "network"
            self._send_reply(rid, result={"status": status})

        self._background(job, rid)
        return None

    def _history_clear(self, rid: int | None) -> None:
        """Spec §13's «очистить историю и аудио» — the one deliberate delete.

        On the driver thread, like every other write: it owns the SQLite
        connection, and riding ``post_job`` also serialises the delete with
        delivery, so a dictation cannot be half-written into a table being
        emptied underneath it.
        """
        if self._clear_history is None:
            self._send_reply(rid, error="unavailable")
            return None

        def job() -> None:
            try:
                rows, files = self._clear_history()
            except Exception:
                log.exception("history clear failed")  # counts only, no text
                self._send_reply(rid, error="clear_failed")
                return
            log.info("history cleared: %d rows, %d audio files", rows, files)
            self._send_reply(rid, result={"rows": rows, "files": files})

        self._post_job(job)
        return None

    def _autostart_set(self, rid: int | None, enabled: object) -> dict[str, Any] | None:
        """Spec §12's switch. Registry writes are microseconds, so this one
        stays on the read thread; the state that comes back is re-read, not
        assumed — Windows may have vetoed us (StartupApproved)."""
        if not isinstance(enabled, bool):
            return reply(rid, error="bad_value") if rid is not None else None
        state = set_autostart(enabled)
        log.info("autostart set to %s → %s", enabled, state.enabled)
        return self._reply_frame(rid, state.as_wire())

    # ------------------------------------------------------------------ #
    # dictionary
    # ------------------------------------------------------------------ #

    def _dictionary_view(self) -> dict[str, Any]:
        rules = self._current_dictionary().rules
        return {"rules": [
            {"type": r.type, "pat": r.pat, "repl": r.repl, "enabled": r.enabled}
            for r in rules
        ]}

    def _dictionary_set(self, rid: int | None, rules_wire: object) -> None:
        parsed = _parse_rules(rules_wire)
        if parsed is None:
            self._send_reply(rid, error="bad_rules")
            return None
        dictionary = Dictionary(parsed)

        def job() -> None:
            self._swap_dictionary(dictionary)
            try:
                self._save_dictionary(dictionary)
            except Exception:
                log.exception("dictionary save failed")
                self._send_reply(rid, error="save_failed")
                return
            self._send_reply(rid, result={"count": len(dictionary.rules)})

        self._post_job(job)
        return None

    # ------------------------------------------------------------------ #
    # history: search and export (read thread, read-only connection)
    # ------------------------------------------------------------------ #

    def _ro(self) -> sqlite3.Connection:
        if self._ro_conn is None:
            uri = self._db_path.resolve().as_uri() + "?mode=ro"
            conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA busy_timeout=5000")
            self._ro_conn = conn
        return self._ro_conn

    def _history_search(
        self, rid: int | None, message: dict[str, Any]
    ) -> dict[str, Any] | None:
        if rid is None:
            return None  # a search nobody awaits is dead weight
        query = message.get("query")
        query = query.strip() if isinstance(query, str) else ""
        limit = _int_in(message.get("limit"), 1, _SEARCH_CAP, default=50)
        offset = _int_in(message.get("offset"), 0, 10_000_000, default=0)
        with self._ro_lock:
            conn = self._ro()
            if query:
                rows = _fts_search(conn, query, limit=limit, offset=offset)
                total = None  # a match count would cost a second FTS pass
            else:
                rows = conn.execute(
                    "SELECT * FROM history ORDER BY created_at DESC LIMIT ? OFFSET ?",
                    (limit, offset),
                ).fetchall()
                (total,) = conn.execute("SELECT COUNT(*) FROM history").fetchone()
        return reply(rid, {"rows": [_row_view(r) for r in rows], "total": total})

    def _history_export(
        self, rid: int | None, format_: object, path_wire: object
    ) -> dict[str, Any] | None:
        target = _validated_export_path(format_, path_wire)
        if target is None:
            log.warning("history_export rejected (format or path)")  # never the path itself
            return reply(rid, error="bad_path") if rid is not None else None
        with self._ro_lock:
            rows = self._ro().execute(
                "SELECT * FROM history ORDER BY created_at"
            ).fetchall()
        try:
            payload = _render_export(rows, str(format_))
            tmp = target.with_name(f"{target.name}.tmp-{os.getpid()}")
            tmp.write_text(payload, encoding="utf-8", newline="")
            os.replace(tmp, target)
        except OSError:
            log.exception("history export failed")  # exception carries no text rows
            return reply(rid, error="export_failed") if rid is not None else None
        return self._reply_frame(rid, {"rows": len(rows)})

    # ------------------------------------------------------------------ #
    # history: insert (driver thread — the clipboard mutex by construction)
    # ------------------------------------------------------------------ #

    def _history_insert(self, rid: int | None, row_id: object) -> None:
        if not isinstance(row_id, int) or isinstance(row_id, bool):
            self._send_reply(rid, error="bad_row")
            return None

        def job() -> None:
            row = self._store_get(row_id)
            if row is None:
                self._send_reply(rid, error="not_found")
                return
            text = row["text_final"] or row["text_raw"]
            if not text:
                self._send_reply(rid, error="no_text")
                return
            target = self._last_target()
            rule = rule_for(
                getattr(target, "process_name", None),
                getattr(target, "window_class", None),
            )
            prepared = prepare_text(text, rule)
            # clipboard.write really raises — the OpenClipboard retries run out
            # when another app owns the board, or the sequence guard trips. An
            # unanswered request leaves the history window's «Вставить» button
            # dead: spec §8 forbids silence, so any failure past here answers
            # with an error the window turns into «текст можно скопировать».
            try:
                snapshot = self._clipboard_write(prepared)
                # The user's clipboard is NOT restored afterwards: they asked
                # for this text — leaving it there is the safety net behaving
                # like one (OPEN-QUESTIONS §27).
                if target is None:
                    # Nothing to aim at: the text is in the clipboard, said
                    # aloud, never silence (spec §8).
                    self._play_cue(Cue.CLIPBOARD)
                    self._send_reply(rid, result={"status": "left_on_clipboard"})
                    return
                report = self._insert(target, snapshot, prepared)
            except Exception:
                log.exception("history insert failed")  # no transcript in the trace
                self._play_cue(Cue.CLIPBOARD)
                self._send_reply(rid, error="insert_failed")
                return
            if report.ok:
                self._send_reply(rid, result={"status": report.status.value})
                return
            failure = report.failure
            self._play_cue(Cue.CLIPBOARD)
            self._send_reply(rid, result={
                "status": failure.status.value if failure else "left_on_clipboard",
            })

        self._post_job(job)
        return None


# --------------------------------------------------------------------------- #
# pure helpers
# --------------------------------------------------------------------------- #


def _int_in(value: object, lo: int, hi: int, *, default: int) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return max(lo, min(hi, value))
    return default


def _parse_rules(rules_wire: object) -> tuple[Rule, ...] | None:
    if not isinstance(rules_wire, list) or len(rules_wire) > _RULE_CAP:
        return None
    parsed: list[Rule] = []
    for item in rules_wire:
        if not isinstance(item, dict):
            return None
        type_ = item.get("type")
        pat = item.get("pat")
        repl = item.get("repl")
        enabled = item.get("enabled", True)
        if type_ not in ("normalize", "replace"):
            return None
        if not isinstance(pat, str) or not pat.strip():
            return None
        if not isinstance(repl, str) or not isinstance(enabled, bool):
            return None
        parsed.append(Rule(type_, pat.strip(), repl, enabled))
    return tuple(parsed)


def _row_view(row: sqlite3.Row) -> dict[str, Any]:
    """A history row for the wire: what the window shows, nothing it must not
    (no audio path — a filesystem detail; no seq — an internal)."""
    return {
        "id": row["id"],
        "created_at": row["created_at"],
        "text": row["text_final"] or row["text_raw"] or "",
        "status": row["delivery_status"],
        "error_code": row["error_code"],
        "target_app": row["target_app"],
        "duration_ms": row["duration_ms"],
        "retry_count": row["retry_count"],
    }


def _fts_search(
    conn: sqlite3.Connection, query: str, *, limit: int, offset: int
) -> list[sqlite3.Row]:
    """The same neutralised FTS5 match as HistoryStore.search (its docstring
    carries the why: raw FTS syntax raises OperationalErrors whose MESSAGE
    quotes the user's words, which spec §13 bans from any log or except path)."""
    tokens = query.split()
    if not tokens:
        return []
    safe = " ".join('"' + token.replace('"', '""') + '"' for token in tokens)
    try:
        return conn.execute(
            """
            SELECT h.* FROM history_fts f JOIN history h ON h.id = f.rowid
             WHERE history_fts MATCH ?
             ORDER BY h.created_at DESC LIMIT ? OFFSET ?
            """,
            (safe, limit, offset),
        ).fetchall()
    except sqlite3.OperationalError:
        return []


def _validated_export_path(format_: object, path_wire: object) -> Path | None:
    """Spec §14's gate. The dialog gave the UI a real, absolute, existing-dir
    path with the format's own extension; anything else is refused, because
    this verb writes files with the core's rights."""
    if format_ not in _EXPORT_FORMATS:
        return None
    if not isinstance(path_wire, str) or "\x00" in path_wire or not path_wire.strip():
        return None
    try:
        path = Path(path_wire)
        if not path.is_absolute():
            return None
        # A local lettered drive only. A UNC path ("\\\\host\\share\\x.json")
        # is absolute and passes the suffix check, and parent.is_dir() would
        # then make a blocking SMB call — writing the whole (perpetual) history
        # to an attacker's share AND leaking an NTLM handshake, the exact
        # arbitrary-write primitive spec §14 demands the core refuse. Path.drive
        # is "C:" for a local path, "\\\\host\\share" for UNC, "" for driveless.
        drive = path.drive
        if len(drive) != 2 or drive[1] != ":":
            return None
        if path.suffix.lower().lstrip(".") != format_:
            return None
        if not path.parent.is_dir():
            return None
        if path.exists() and not path.is_file():
            return None
    except OSError:
        return None
    return path


def _render_export(rows: list[sqlite3.Row], format_: str) -> str:
    """The whole history, text included — text is бессрочен and this is the
    user taking their own words with them."""
    records = [
        {
            "created_at": datetime.fromtimestamp(
                row["created_at"] / 1000, tz=timezone.utc
            ).isoformat(),
            "status": row["delivery_status"],
            "target_app": row["target_app"] or "",
            "text": row["text_final"] or row["text_raw"] or "",
        }
        for row in rows
    ]
    if format_ == "json":
        return json.dumps(records, ensure_ascii=False, indent=2)
    if format_ == "csv":
        out = io.StringIO()
        writer = csv.DictWriter(
            out, fieldnames=["created_at", "status", "target_app", "text"],
            lineterminator="\r\n",
        )
        writer.writeheader()
        writer.writerows(records)
        return out.getvalue()
    lines = [
        f"[{r['created_at']}] {r['target_app']} ({r['status']})\n{r['text']}\n"
        for r in records
    ]
    return "\n".join(lines)
