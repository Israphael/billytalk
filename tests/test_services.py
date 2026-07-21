"""``core/services.py``: the milestone-2 verbs against a real SQLite file and
an inline ``post_job`` — every driver-thread errand runs synchronously here,
so replies land in the recorder the moment the verb returns.
"""

from __future__ import annotations

import json
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
            has_groq_key=lambda: True,
        )

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
        {"unknown_key": 1},
        {},                              # an empty patch patches nothing
        "not a dict",
    ],
)
def test_set_config_rejects_bad_patches(world: World, patch: object) -> None:
    frame = world.services.handle("set_config", {"id": 3, "patch": patch})
    assert frame == {"type": "reply", "id": 3, "error": "bad_patch"}
    assert world.config.language == "ru", "a rejected patch changes nothing"


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
