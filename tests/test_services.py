"""``core/services.py``: the milestone-2 verbs against a real SQLite file and
an inline ``post_job`` — every driver-thread errand runs synchronously here,
so replies land in the recorder the moment the verb returns.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from billytalk.core.insert.inserter import InsertFailure, InsertReport
from billytalk.core.machine.effects import Cue, DeliveryStatus, ErrorCode
from billytalk.core.services import UiServices
from billytalk.core.store.config import Config, load_config
from billytalk.core.store.db import connect, ensure_schema
from billytalk.core.store.history import HistoryStore
from billytalk.core.text.dictionary import DEFAULT_RULES, Dictionary


class World:
    def __init__(self, tmp_path: Path) -> None:
        self.db_path = tmp_path / "history.db"
        self.conn = connect(self.db_path)
        ensure_schema(self.conn)
        self.store = HistoryStore(self.conn)
        self.config = Config()
        self.config_path = tmp_path / "config.json"
        self.sent: list[dict[str, Any]] = []
        self.jobs_ran = 0
        self.clip_writes: list[str] = []
        self.cues: list[Cue] = []
        self.swapped: list[Dictionary] = []
        self.saved: list[Dictionary] = []
        self.deps_applied = 0
        self.target: Any = SimpleNamespace(
            hwnd=0x111, focus_hwnd=0x222, process_name="notepad.exe",
            window_class="Notepad", secure=False, elevated=False,
        )
        self.report: InsertReport = InsertReport(ok=True)
        self.insert_calls: list[tuple[Any, Any, str]] = []
        self.dictionary = Dictionary(DEFAULT_RULES)
        self.save_raises = False
        # cycle-3 (wizard) world
        self.key_present = True
        self.keys_written: list[str] = []
        self.key_write_raises = False
        self.key_check = "ok"
        self.background_ran = 0
        self.probe_result: dict[str, Any] = {
            "ok": True, "code": "ok", "device": "Микрофон (USB)",
            "inputs": ["Микрофон (USB)"], "level": 42,
        }

        def post_job(fn) -> None:
            self.jobs_ran += 1
            fn()

        def save_dictionary(d: Dictionary) -> None:
            if self.save_raises:
                raise OSError("disk gone")
            self.saved.append(d)

        self.services = UiServices(
            config=self.config,
            config_path=self.config_path,
            db_path=self.db_path,
            post_job=post_job,
            send=lambda frame: (self.sent.append(frame), True)[-1],
            store_get=self.store.get,
            clipboard_write=lambda text: (self.clip_writes.append(text), "SNAP")[-1],
            insert=self._insert,
            last_target=lambda: self.target,
            play_cue=self.cues.append,
            swap_dictionary=self.swapped.append,
            save_dictionary=save_dictionary,
            current_dictionary=lambda: self.dictionary,
            apply_config_to_deps=self._apply,
            has_groq_key=lambda: self.key_present,
            submit_background=self._background,
            probe_microphone=lambda: self.probe_result,
            write_groq_key=self._write_key,
            check_groq_key=lambda: self.key_check,
        )

    def _background(self, job) -> None:
        # Inline, like post_job: the wizard's slow errands answer synchronously
        # in the tests so a reply is always in `sent` when the verb returns.
        self.background_ran += 1
        job()

    def _write_key(self, key: str) -> None:
        if self.key_write_raises:
            raise RuntimeError("credential store refused")
        self.keys_written.append(key)

    def _insert(self, target: Any, snapshot: Any, text: str) -> InsertReport:
        self.insert_calls.append((target, snapshot, text))
        return self.report

    def _apply(self) -> None:
        self.deps_applied += 1

    def add_row(self, *, text: str | None = "текст записи", status: str = "inserted",
                seq: int = 1) -> int:
        row_id = self.store.add(
            seq=seq, created_at=1_700_000_000_000 + seq, now=1_700_000_000_000 + seq,
            duration_ms=1200, status=DeliveryStatus.PENDING_TRANSCRIBE,
            audio_path=None, target_app="notepad.exe", target_window_cls="Notepad",
        )
        if text is not None:
            self.store.record_transcription(
                row_id, text_raw=text, text_final=text, language="ru",
                provider_id="groq", billed_seconds=1.0, latency_ms=400,
            )
        self.store.set_status(
            row_id, DeliveryStatus(status), now=1_700_000_000_500 + seq
        )
        return row_id


@pytest.fixture
def world(tmp_path: Path) -> World:
    return World(tmp_path)


def _result(frame: dict[str, Any]) -> dict[str, Any]:
    assert frame["type"] == "reply" and "error" not in frame, frame
    return frame["result"]


# --------------------------------------------------------------------------- #
# config
# --------------------------------------------------------------------------- #


def test_get_config_shows_the_view_and_key_presence_never_the_key(world: World) -> None:
    frame = world.services.handle("get_config", {"id": 1})
    view = _result(frame)["config"]
    assert view["language"] == "ru" and view["ptt_code"] == 4099
    assert view["has_groq_key"] is True
    assert "usage" not in view and "schema_version" not in view
    assert not any("key" in k and k != "has_groq_key" for k in view)


def test_set_config_patches_saves_and_refreshes_deps(world: World) -> None:
    frame = world.services.handle(
        "set_config", {"id": 2, "patch": {"language": "en", "retention_minutes": 120}}
    )
    assert _result(frame)["config"]["language"] == "en"
    assert world.config.language == "en" and world.config.retention_minutes == 120
    assert world.deps_applied == 1, "the deps copies refresh on the driver thread"
    saved = load_config(world.config_path, now_ms=0).config
    assert saved.language == "en" and saved.retention_minutes == 120


@pytest.mark.parametrize(
    "patch",
    [
        {"ptt_code": 4100},              # binding changes ride M3 capture, not a patch
        {"language": "de"},              # not an MVP-0 language
        {"retention_minutes": True},     # bool is not an int here
        {"retention_minutes": 0},        # out of range
        {"groq_model": "whisper-x"},     # not patchable: provider binds it at start
        {"unknown_key": 1},
        {},                              # an empty patch patches nothing
        "not a dict",
    ],
)
def test_set_config_rejects_bad_patches(world: World, patch: object) -> None:
    frame = world.services.handle("set_config", {"id": 3, "patch": patch})
    assert frame == {"type": "reply", "id": 3, "error": "bad_patch"}
    assert world.config.language == "ru", "a rejected patch changes nothing"


def test_set_config_save_failure_rolls_the_config_back(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ultrareview, normal: _set_config mutated self._config BEFORE the save, so
    a raising save_config (OneDrive/AV lock on %APPDATA%, full disk) left memory
    holding the new value while disk kept the old — and since save_config writes
    the whole Config, the rejected patch would ride the next successful save. The
    save now runs under a snapshot/rollback so the raise leaves every copy old."""
    import billytalk.core.services as services_mod

    def boom(_path: Path, _config: Config) -> None:
        raise OSError("disk gone")

    monkeypatch.setattr(services_mod, "save_config", boom)
    with pytest.raises(OSError):
        world.services.handle("set_config", {"id": 34, "patch": {"language": "en"}})
    assert world.config.language == "ru", "the failed save left the config untouched"
    assert world.deps_applied == 0, "apply_config_to_deps never ran"


def test_set_config_does_not_patch_the_groq_model(world: World) -> None:
    """cue-review, low: GroqProvider binds its model at construction and
    apply_config_to_deps does not refresh it, so accepting the patch would
    drift the running provider from what get_config reports. Refused."""
    frame = world.services.handle(
        "set_config", {"id": 33, "patch": {"groq_model": "whisper-large-v3"}}
    )
    assert frame == {"type": "reply", "id": 33, "error": "bad_patch"}
    assert world.config.groq_model == "whisper-large-v3-turbo", "the model is untouched"


# --------------------------------------------------------------------------- #
# dictionary
# --------------------------------------------------------------------------- #


def test_dictionary_get_returns_the_wire_rules(world: World) -> None:
    frame = world.services.handle("dictionary_get", {"id": 4})
    rules = _result(frame)["rules"]
    assert {"type": "replace", "pat": "прот", "repl": "прод", "enabled": True} in rules


def test_dictionary_set_swaps_saves_and_replies(world: World) -> None:
    wire = [{"type": "replace", "pat": "тест", "repl": "test", "enabled": True}]
    frame = world.services.handle("dictionary_set", {"id": 5, "rules": wire})
    assert frame is None, "the reply comes from the driver-thread job"
    assert world.jobs_ran == 1
    assert [r.pat for r in world.swapped[0].rules] == ["тест"]
    assert world.saved == world.swapped
    assert world.sent[-1] == {"type": "reply", "id": 5, "result": {"count": 1}}


def test_dictionary_set_save_failure_is_reported_not_swallowed(world: World) -> None:
    world.save_raises = True
    world.services.handle("dictionary_set", {"id": 6, "rules": []})
    assert world.sent[-1] == {"type": "reply", "id": 6, "error": "save_failed"}
    assert world.swapped, "the in-memory dictionary DID swap; only the disk failed"


@pytest.mark.parametrize(
    "rules",
    [
        "not a list",
        [{"type": "bogus", "pat": "a", "repl": "b"}],
        [{"type": "replace", "pat": "   ", "repl": "b"}],
        [{"type": "replace", "pat": "a", "repl": 5}],
        [{"type": "replace", "pat": "a", "repl": "b", "enabled": "yes"}],
    ],
)
def test_dictionary_set_rejects_bad_rules(world: World, rules: object) -> None:
    world.services.handle("dictionary_set", {"id": 7, "rules": rules})
    assert world.sent[-1] == {"type": "reply", "id": 7, "error": "bad_rules"}
    assert world.jobs_ran == 0 and not world.swapped


# --------------------------------------------------------------------------- #
# history: search
# --------------------------------------------------------------------------- #


def test_history_search_empty_query_pages_recent_with_total(world: World) -> None:
    for i in range(3):
        world.add_row(text=f"запись номер {i}", seq=i + 1)
    frame = world.services.handle("history_search", {"id": 8, "query": "", "limit": 2})
    result = _result(frame)
    assert result["total"] == 3
    assert [r["text"] for r in result["rows"]] == ["запись номер 2", "запись номер 1"]
    assert "audio_path" not in result["rows"][0], "filesystem details stay home"


def test_history_search_fts_finds_words_and_horrors_stay_inert(world: World) -> None:
    world.add_row(text="выкати сводку на прод", seq=1)
    world.add_row(text="совсем другое", seq=2)
    frame = world.services.handle("history_search", {"id": 9, "query": "сводку"})
    rows = _result(frame)["rows"]
    assert len(rows) == 1 and "сводку" in rows[0]["text"]
    # raw FTS5 syntax must neither raise nor leak the user's words
    frame = world.services.handle("history_search", {"id": 10, "query": 'кое-что AND "'})
    assert _result(frame)["rows"] == []


def test_history_search_caps_the_page_and_needs_an_id(world: World) -> None:
    world.add_row(seq=1)
    assert world.services.handle("history_search", {"query": ""}) is None
    frame = world.services.handle("history_search", {"id": 11, "query": "", "limit": 99999})
    assert _result(frame)["rows"], "an absurd limit clamps, it does not error"


# --------------------------------------------------------------------------- #
# history: insert (the driver-thread job)
# --------------------------------------------------------------------------- #


def test_history_insert_pastes_by_the_saved_target(world: World) -> None:
    row_id = world.add_row(text="выкати на прод")
    world.report = InsertReport(ok=True, status=DeliveryStatus.INSERTED)
    world.services.handle("history_insert", {"id": 12, "row_id": row_id})
    assert world.clip_writes == ["выкати на прод"]
    target, snapshot, text = world.insert_calls[0]
    assert target is world.target and snapshot == "SNAP" and text == "выкати на прод"
    assert world.sent[-1] == {"type": "reply", "id": 12, "result": {"status": "inserted"}}
    assert world.cues == [], "success is silent; the text also stays in the clipboard"


def test_history_insert_into_a_terminal_flattens_newlines(world: World) -> None:
    row_id = world.add_row(text="строка раз\nстрока два")
    world.target = SimpleNamespace(
        hwnd=0x1, focus_hwnd=None, process_name="windowsterminal.exe",
        window_class="CASCADIA_HOSTING_WINDOW_CLASS", secure=False, elevated=False,
    )
    world.services.handle("history_insert", {"id": 13, "row_id": row_id})
    assert world.clip_writes == ["строка раз строка два"], "a \\n into a live SSH executes"


def test_history_insert_without_a_target_leaves_the_clipboard_loudly(world: World) -> None:
    row_id = world.add_row()
    world.target = None
    world.services.handle("history_insert", {"id": 14, "row_id": row_id})
    assert world.clip_writes and world.insert_calls == []
    assert world.cues == [Cue.CLIPBOARD]
    assert world.sent[-1]["result"] == {"status": "left_on_clipboard"}


def test_history_insert_failure_reports_the_precise_status(world: World) -> None:
    row_id = world.add_row()
    world.report = InsertReport(
        ok=False,
        failure=InsertFailure(
            ErrorCode.FOCUS_LOST, DeliveryStatus.FOCUS_LOST, "gone"
        ),
    )
    world.services.handle("history_insert", {"id": 15, "row_id": row_id})
    assert world.sent[-1]["result"] == {"status": "focus_lost"}
    assert world.cues == [Cue.CLIPBOARD]


def test_history_insert_refuses_missing_textless_and_bogus_rows(world: World) -> None:
    world.services.handle("history_insert", {"id": 16, "row_id": 999})
    assert world.sent[-1] == {"type": "reply", "id": 16, "error": "not_found"}
    pending = world.add_row(text=None, status="pending_transcribe")
    world.services.handle("history_insert", {"id": 17, "row_id": pending})
    assert world.sent[-1] == {"type": "reply", "id": 17, "error": "no_text"}
    world.services.handle("history_insert", {"id": 18, "row_id": True})
    assert world.sent[-1] == {"type": "reply", "id": 18, "error": "bad_row"}


# --------------------------------------------------------------------------- #
# history: export (spec §14's validated path)
# --------------------------------------------------------------------------- #


def test_history_export_writes_the_whole_story(world: World, tmp_path: Path) -> None:
    world.add_row(text="первая", seq=1)
    world.add_row(text="вторая", seq=2)
    target = tmp_path / "экспорт.json"
    frame = world.services.handle(
        "history_export", {"id": 19, "format": "json", "path": str(target)}
    )
    assert _result(frame) == {"rows": 2}
    records = json.loads(target.read_text(encoding="utf-8"))
    assert [r["text"] for r in records] == ["первая", "вторая"]
    assert not list(tmp_path.glob("*.tmp-*")), "atomic: no droppings"


def test_history_export_txt_and_csv_render(world: World, tmp_path: Path) -> None:
    world.add_row(text="строка текста")
    txt = tmp_path / "история.txt"
    world.services.handle("history_export", {"id": 20, "format": "txt", "path": str(txt)})
    assert "строка текста" in txt.read_text(encoding="utf-8")
    csv_path = tmp_path / "история.csv"
    world.services.handle("history_export", {"id": 21, "format": "csv", "path": str(csv_path)})
    assert "строка текста" in csv_path.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "format_, path",
    [
        ("json", "relative.json"),
        ("json", None),
        ("exe", "C:/whatever.exe"),
        ("json", "C:/no/such/dir/export.json"),
        ("json", "{tmp}/wrong_ext.txt"),
        ("json", "{tmp}"),  # a directory, not a file
    ],
)
def test_history_export_refuses_arbitrary_writes(
    world: World, tmp_path: Path, format_: str, path: str | None
) -> None:
    wire = path.format(tmp=tmp_path) if isinstance(path, str) else path
    frame = world.services.handle(
        "history_export", {"id": 22, "format": format_, "path": wire}
    )
    assert frame == {"type": "reply", "id": 22, "error": "bad_path"}


# --------------------------------------------------------------------------- #
# capture verbs delegate to the hotkey capture (M3)
# --------------------------------------------------------------------------- #


def test_capture_verbs_delegate_to_the_hotkey_capture(world: World) -> None:
    calls: list[tuple[Any, ...]] = []
    world.services._hotkey_capture = SimpleNamespace(
        start=lambda rid, action: calls.append(("start", rid, action)),
        stop=lambda rid: calls.append(("stop", rid)),
    )
    assert world.services.handle(
        "capture_hotkey_start", {"id": 30, "action": "ptt"}
    ) is None
    assert world.services.handle("capture_hotkey_stop", {"id": 31}) is None
    assert calls == [("start", 30, "ptt"), ("stop", 31)]


def test_capture_verbs_without_a_capture_service_answer_unimplemented(world: World) -> None:
    frame = world.services.handle("capture_hotkey_start", {"id": 32, "action": "ptt"})
    assert frame == {"type": "reply", "id": 32, "error": "unimplemented"}


# --------------------------------------------------------------------------- #
# M2 review fixes
# --------------------------------------------------------------------------- #


def test_history_export_refuses_unc_paths(world: World) -> None:
    """M2 review, medium: a UNC path is absolute and would make the core write
    the whole (perpetual) transcript history to a remote share and leak an
    NTLM handshake — spec §14's arbitrary-write primitive."""
    frame = world.services.handle("history_export", {
        "id": 40, "format": "json", "path": r"\attacker\share\hist.json",
    })
    assert frame == {"type": "reply", "id": 40, "error": "bad_path"}
    frame = world.services.handle("history_export", {
        "id": 41, "format": "json", "path": r"\127.0.0.1\C$\Users\Public\h.json",
    })
    assert frame == {"type": "reply", "id": 41, "error": "bad_path"}


def test_history_insert_reports_a_clipboard_failure_never_silence(world: World) -> None:
    """M2 review, medium: clipboard.write really raises; without a reply the
    «Вставить» button dies silently, which spec §8 forbids."""
    world.add_row(text="важный текст")

    def boom(_text: str) -> Any:
        raise RuntimeError("clipboard owned by another app")

    world.services._clipboard_write = boom  # type: ignore[assignment]
    world.services.handle("history_insert", {"id": 42, "row_id": 1})
    assert world.sent[-1] == {"type": "reply", "id": 42, "error": "insert_failed"}
    assert world.cues == [Cue.CLIPBOARD], "a failure is always audible"


# --------------------------------------------------------------------------- #
# cycle 3: the wizard's verbs (spec §12)
# --------------------------------------------------------------------------- #


def test_config_view_carries_the_wizard_flag_and_the_resolved_ui_language(
    world: World,
) -> None:
    """The interface must never resolve «auto» itself: reading the Windows UI
    language is a ctypes call and harness §1 keeps those out of ui/."""
    view = _result(world.services.handle("get_config", {"id": 50}))["config"]
    assert view["wizard_done"] is False
    assert view["ui_language"] == "auto"
    assert view["ui_language_effective"] in ("ru", "en")


def test_wizard_done_and_ui_language_are_patchable_and_persist(world: World) -> None:
    frame = world.services.handle("set_config", {
        "id": 51, "patch": {"wizard_done": True, "ui_language": "en"},
    })
    view = _result(frame)["config"]
    assert view["wizard_done"] is True and view["ui_language"] == "en"
    assert view["ui_language_effective"] == "en"
    on_disk = json.loads(world.config_path.read_text(encoding="utf-8"))
    assert on_disk["wizard_done"] is True and on_disk["ui_language"] == "en"


def test_ui_language_rejects_anything_outside_the_tables(world: World) -> None:
    frame = world.services.handle("set_config", {"id": 52, "patch": {"ui_language": "de"}})
    assert frame == {"type": "reply", "id": 52, "error": "bad_patch"}
    assert world.config.ui_language == "auto"


def test_mic_probe_answers_off_the_read_thread(world: World) -> None:
    assert world.services.handle("mic_probe", {"id": 53}) is None, "no sync reply"
    assert world.background_ran == 1, "opening a device must not block the read thread"
    assert world.sent[-1]["result"]["ok"] is True
    assert world.sent[-1]["result"]["level"] == 42


def test_mic_probe_turns_a_raising_probe_into_an_answer(world: World) -> None:
    """A step waiting forever on a reply that never comes is worse than a
    wrong-but-honest «занято»."""
    def boom() -> dict[str, Any]:
        raise RuntimeError("PortAudio exploded")

    world.services._probe_microphone = boom  # type: ignore[assignment]
    world.services.handle("mic_probe", {"id": 54})
    assert world.sent[-1]["result"] == {
        "ok": False, "code": "mic_busy", "device": None, "inputs": [],
    }


def test_set_key_stores_it_and_never_echoes_it(world: World) -> None:
    world.services.handle("set_key", {"id": 55, "key": "  gsk_secret_value  "})
    assert world.keys_written == ["gsk_secret_value"], "trimmed, stored once"
    reply = world.sent[-1]
    assert reply["result"] == {"stored": True}
    assert "gsk_secret_value" not in json.dumps(reply, ensure_ascii=False)


def test_set_key_refuses_an_empty_value_and_reports_a_store_failure(world: World) -> None:
    world.services.handle("set_key", {"id": 56, "key": "   "})
    assert world.sent[-1] == {"type": "reply", "id": 56, "error": "bad_key"}
    world.key_write_raises = True
    world.services.handle("set_key", {"id": 57, "key": "gsk_x"})
    assert world.sent[-1] == {"type": "reply", "id": 57, "error": "store_failed"}


def test_set_key_keeps_the_value_out_of_the_log(world: World, caplog) -> None:
    """Spec §13: the key never appears in a log line, an exception or a repr."""
    caplog.set_level("DEBUG")
    world.services.handle("set_key", {"id": 58, "key": "gsk_do_not_log_me"})
    world.key_write_raises = True
    world.services.handle("set_key", {"id": 59, "key": "gsk_do_not_log_me"})
    assert "gsk_do_not_log_me" not in caplog.text


def test_test_key_reports_the_status_word(world: World) -> None:
    world.key_check = "invalid"
    world.services.handle("test_key", {"id": 60})
    assert world.sent[-1]["result"] == {"status": "invalid"}
    assert world.background_ran == 1, "a network round trip runs in the background"


def test_test_key_turns_a_raising_check_into_network(world: World) -> None:
    def boom() -> str:
        raise OSError("socket died")

    world.services._check_groq_key = boom  # type: ignore[assignment]
    world.services.handle("test_key", {"id": 61})
    assert world.sent[-1]["result"] == {"status": "network"}


def test_autostart_verbs_answer_the_state_and_refuse_a_non_boolean(world: World) -> None:
    frame = world.services.handle("autostart_get", {"id": 62})
    state = _result(frame)
    assert set(state) == {
        "available", "enabled", "registered", "disabled_by_windows",
        "matches_current_exe",
    }
    # A dev checkout has no installed exe to register, so the switch is honest
    # about being unavailable rather than pretending to work.
    assert state["available"] is False
    bad = world.services.handle("autostart_set", {"id": 63, "enabled": "yes"})
    assert bad == {"type": "reply", "id": 63, "error": "bad_value"}


def test_set_key_refuses_a_key_that_is_not_printable_ascii(
    world: World, caplog
) -> None:
    """Security review, medium: the boundary refuses what would later make
    http.client raise with the whole key inside the message. An API key is
    printable ASCII; a newline or a Cyrillic character means a mangled paste."""
    caplog.set_level("DEBUG")
    for bad in ("gsk_secret\nZZZ", "gsk_секрет", "gsk_a\tb"):
        world.services.handle("set_key", {"id": 70, "key": bad})
        assert world.sent[-1] == {"type": "reply", "id": 70, "error": "bad_key"}
    assert world.keys_written == [], "nothing reached the Credential Manager"
    assert "secret" not in caplog.text and "секрет" not in caplog.text


def test_history_clear_runs_on_the_driver_thread_and_reports_counts(
    world: World,
) -> None:
    """Spec §13's clear rides post_job like every other write: the driver owns
    the connection, and the same lane serialises the delete with delivery."""
    cleared: list[bool] = []
    world.services._clear_history = lambda: (cleared.append(True), (7, 3))[-1]  # type: ignore[assignment]

    assert world.services.handle("history_clear", {"id": 80}) is None
    assert cleared == [True] and world.jobs_ran > 0
    assert world.sent[-1]["result"] == {"rows": 7, "files": 3}


def test_history_clear_reports_a_refusal_instead_of_pretending(world: World) -> None:
    """The core refuses while a dictation is in flight — deleting the row of a
    recording still being written would lose words the user just spoke."""
    def busy() -> tuple[int, int]:
        raise RuntimeError("a dictation is in flight")

    world.services._clear_history = busy  # type: ignore[assignment]
    world.services.handle("history_clear", {"id": 81})
    assert world.sent[-1] == {"type": "reply", "id": 81, "error": "clear_failed"}


def test_audio_devices_answers_from_the_cache(world: World) -> None:
    """Listing devices must not touch PortAudio on the read thread: a reload
    may be dlclose-ing the library at that moment (cycle-3 review, high)."""
    world.services._list_input_devices = lambda: ["Микрофон (USB)", "Realtek"]  # type: ignore[assignment]
    frame = world.services.handle("audio_devices", {"id": 82})
    assert _result(frame) == {"inputs": ["Микрофон (USB)", "Realtek"]}

    world.services._list_input_devices = None  # type: ignore[assignment]
    frame = world.services.handle("audio_devices", {"id": 83})
    assert _result(frame) == {"inputs": []}, "no collaborator is an empty list, not a crash"


def test_history_clear_pushes_so_an_open_history_window_hears_it(world: World) -> None:
    """Cycle-3 review, medium: ids are reused after a delete, so a window still
    showing the old rows would paste a different dictation's text."""
    world.services._clear_history = lambda: (2, 1)  # type: ignore[assignment]
    world.services.handle("history_clear", {"id": 84})
    kinds = [frame.get("type") for frame in world.sent]
    assert "history_cleared" in kinds, "every window hears it, not just the asker"
    assert kinds.index("history_cleared") < kinds.index("reply"), (
        "the push goes out before the reply, so no window can act on stale rows"
    )


def test_a_refused_clear_is_busy_not_a_failure(world: World, caplog) -> None:
    """Cycle-3 review: «подождите диктовку» and «база сломалась» send the user
    to do completely different things, so they cannot share one code — and the
    refusal is a normal event, not an ERROR with a traceback."""
    from billytalk.core.services import DictationInFlight

    def busy() -> tuple[int, int]:
        raise DictationInFlight("a dictation is in flight")

    caplog.set_level("DEBUG")
    world.services._clear_history = busy  # type: ignore[assignment]
    world.services.handle("history_clear", {"id": 85})
    assert world.sent[-1] == {"type": "reply", "id": 85, "error": "busy"}
    assert "Traceback" not in caplog.text

    def broken() -> tuple[int, int]:
        raise sqlite3.DatabaseError("database disk image is malformed")

    world.services._clear_history = broken  # type: ignore[assignment]
    world.services.handle("history_clear", {"id": 86})
    assert world.sent[-1] == {"type": "reply", "id": 86, "error": "clear_failed"}
