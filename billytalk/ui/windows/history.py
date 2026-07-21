"""The history window (spec §10; the approved mockup).

Search over the full text (FTS5 on the core's side), insert in one action —
**executed by the core** over ``history_insert`` (spec §10: the window itself
holds the focus, only the core knows the saved target), copy, export through
the system save dialog whose path the core re-validates (spec §14).

History is the customer's safety net (Wispr Flow lesson 3) — the outcome of
an insert is always spelled out in the footer, never implied.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Final

import wx

from ..controller import UiController
from . import dress

__all__ = [
    "HistoryFrame",
    "insert_outcome_line",
    "matches_filter",
    "status_label",
    "time_label",
]

_PAGE: Final = 100

_STATUS_LABELS: Final = {
    "inserted": "Вставлено",
    "left_on_clipboard": "В буфере · Ctrl+V",
    "withheld": "Без вставки",
    "focus_lost": "В буфере · фокус ушёл",
    "verify_impossible": "Без подтверждения",
    "blocked_secure": "Поле пароля",
    "pending_transcribe": "Расшифровка…",
    "pending_retry": "Ждёт связи",
    "transcribe_failed": "Ошибка расшифровки",
    "cancelled": "Отменено",
    "too_short": "Слишком коротко",
    "empty": "Пусто",
}

FILTERS: Final = (
    ("all", "Все статусы"),
    ("delivered", "Вставлено"),
    ("clipboard", "В буфере"),
    ("waiting", "Ждёт связи"),
    ("other", "Прочее"),
)

_FILTER_SETS: Final = {
    "delivered": {"inserted", "verify_impossible"},
    "clipboard": {"left_on_clipboard", "focus_lost", "withheld"},
    "waiting": {"pending_transcribe", "pending_retry"},
}


def status_label(status: str | None, error_code: str | None = None) -> str:
    label = _STATUS_LABELS.get(status or "", status or "—")
    if status == "transcribe_failed" and error_code:
        return f"{label} · {error_code}"
    return label


def matches_filter(status: str | None, key: str) -> bool:
    if key == "all":
        return True
    listed = _FILTER_SETS.get(key)
    if listed is not None:
        return (status or "") in listed
    known = set().union(*_FILTER_SETS.values())
    return (status or "") not in known


def time_label(created_at_ms: int) -> str:
    """Local wall time, mockup-style; the day only when it is not today."""
    stamp = datetime.fromtimestamp(created_at_ms / 1000)
    if stamp.date() == datetime.now().date():
        return stamp.strftime("%H:%M")
    return stamp.strftime("%d.%m %H:%M")


def insert_outcome_line(status: str | None) -> str:
    """What the footer says after an insert reply — always an action, never a
    bare statement (harness §7)."""
    return {
        "inserted": "Вставлено.",
        "verify_impossible": "Отправлено; подтверждения нет — текст и в буфере (Ctrl+V).",
        "left_on_clipboard": "Текст в буфере — вставьте Ctrl+V.",
        "focus_lost": "Окно ушло из фокуса — текст в буфере, вставьте Ctrl+V.",
        "blocked_secure": "Поле пароля: вставьте из буфера вручную.",
    }.get(status or "", "Текст в буфере — вставьте Ctrl+V.")


_EXPORT_WILDCARD: Final = (
    "Текст (*.txt)|*.txt|Таблица CSV (*.csv)|*.csv|JSON (*.json)|*.json"
)
_EXPORT_FORMATS: Final = ("txt", "csv", "json")


class HistoryFrame(wx.Frame):
    def __init__(self, controller: UiController, parent: wx.Window | None = None) -> None:
        super().__init__(parent, title="BillyTalk — история", size=wx.Size(840, 520))
        self._c = controller
        self._rows: list[dict[str, Any]] = []
        self._view: list[dict[str, Any]] = []
        self._total: int | None = None

        root = wx.Panel(self)
        tools = wx.BoxSizer(wx.HORIZONTAL)
        self._search = wx.TextCtrl(root, style=wx.TE_PROCESS_ENTER,
                                   size=wx.Size(300, -1))
        self._search.SetHint("Поиск по истории…")
        self._search.Bind(wx.EVT_TEXT_ENTER, lambda _e: self.load())
        tools.Add(self._search, 0, wx.ALIGN_CENTER_VERTICAL)
        self._filter = wx.Choice(root, choices=[label for _key, label in FILTERS])
        self._filter.SetSelection(0)
        self._filter.Bind(wx.EVT_CHOICE, lambda _e: self._refill())
        tools.Add(self._filter, 0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 8)
        tools.AddStretchSpacer()
        find = wx.Button(root, label="Найти")
        find.Bind(wx.EVT_BUTTON, lambda _e: self.load())
        tools.Add(find, 0, wx.RIGHT, 8)
        export = wx.Button(root, label="Экспорт…")
        export.Bind(wx.EVT_BUTTON, self._on_export)
        tools.Add(export, 0)

        self._list = wx.ListCtrl(root, style=wx.LC_REPORT | wx.LC_SINGLE_SEL)
        for index, (title, width) in enumerate(
            (("Время", 90), ("Текст", 420), ("Приложение", 110), ("Статус", 160))
        ):
            self._list.InsertColumn(index, title, width=width)
        self._list.Bind(wx.EVT_LIST_ITEM_ACTIVATED, lambda _e: self._insert_selected())

        actions = wx.BoxSizer(wx.HORIZONTAL)
        insert = wx.Button(root, label="Вставить")
        insert.Bind(wx.EVT_BUTTON, lambda _e: self._insert_selected())
        actions.Add(insert, 0, wx.RIGHT, 8)
        copy = wx.Button(root, label="Копировать")
        copy.Bind(wx.EVT_BUTTON, lambda _e: self._copy_selected())
        actions.Add(copy, 0)
        actions.AddStretchSpacer()
        self._footer = wx.StaticText(root, label="")
        actions.Add(self._footer, 0, wx.ALIGN_CENTER_VERTICAL)

        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(tools, 0, wx.EXPAND | wx.ALL, 10)
        sizer.Add(self._list, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 10)
        sizer.Add(actions, 0, wx.EXPAND | wx.ALL, 10)
        root.SetSizer(sizer)
        dress(self)
        self.load()

    # ------------------------------------------------------------------ #
    # data flow
    # ------------------------------------------------------------------ #

    def load(self) -> None:
        query = self._search.GetValue().strip()
        self._c.request(
            {"type": "history_search", "query": query, "limit": _PAGE},
            self._on_rows,
        )

    def _on_rows(self, frame: dict[str, Any]) -> None:
        if "error" in frame:
            self._footer.SetLabel("Поиск не ответил — попробуйте ещё раз")
            return
        result = frame["result"]
        self._rows = list(result.get("rows", []))
        self._total = result.get("total")
        self._refill()

    def _refill(self) -> None:
        key = FILTERS[max(0, self._filter.GetSelection())][0]
        self._view = [r for r in self._rows if matches_filter(r.get("status"), key)]
        self._list.DeleteAllItems()
        for index, row in enumerate(self._view):
            self._list.InsertItem(index, time_label(int(row.get("created_at", 0))))
            self._list.SetItem(index, 1, str(row.get("text", "")))
            self._list.SetItem(index, 2, _app_label(row.get("target_app")))
            self._list.SetItem(
                index, 3, status_label(row.get("status"), row.get("error_code"))
            )
        shown = len(self._view)
        if self._total is not None:
            self._footer.SetLabel(
                f"{shown} на экране · {self._total} записей · текст хранится бессрочно"
            )
        else:
            self._footer.SetLabel(f"Найдено: {shown} (первые {_PAGE})")

    def _selected(self) -> dict[str, Any] | None:
        index = self._list.GetFirstSelected()
        if 0 <= index < len(self._view):
            return self._view[index]
        return None

    # ------------------------------------------------------------------ #
    # actions
    # ------------------------------------------------------------------ #

    def _insert_selected(self) -> None:
        row = self._selected()
        if row is None:
            self._footer.SetLabel("Выберите запись")
            return
        self._c.request(
            {"type": "history_insert", "row_id": row["id"]}, self._on_inserted
        )

    def _on_inserted(self, frame: dict[str, Any]) -> None:
        if "error" in frame:
            self._footer.SetLabel("Вставка не удалась — текст можно скопировать")
            return
        self._footer.SetLabel(insert_outcome_line(frame["result"].get("status")))

    def _copy_selected(self) -> None:
        row = self._selected()
        if row is None:
            self._footer.SetLabel("Выберите запись")
            return
        if wx.TheClipboard.Open():
            try:
                wx.TheClipboard.SetData(wx.TextDataObject(str(row.get("text", ""))))
            finally:
                wx.TheClipboard.Close()
            self._footer.SetLabel("Скопировано — вставьте Ctrl+V.")
        else:
            self._footer.SetLabel("Буфер занят другим приложением — попробуйте ещё раз")

    def _on_export(self, _event: wx.CommandEvent) -> None:
        dialog = wx.FileDialog(
            self, "Экспорт истории", wildcard=_EXPORT_WILDCARD,
            defaultFile="billytalk-история",
            style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT,
        )
        if dialog.ShowModal() != wx.ID_OK:
            dialog.Destroy()
            return
        format_ = _EXPORT_FORMATS[dialog.GetFilterIndex()]
        path = dialog.GetPath()
        dialog.Destroy()
        if not path.lower().endswith(f".{format_}"):
            path = f"{path}.{format_}"
        self._c.request(
            {"type": "history_export", "format": format_, "path": path},
            self._on_exported,
        )

    def _on_exported(self, frame: dict[str, Any]) -> None:
        if "error" in frame:
            self._footer.SetLabel("Экспорт не удался — проверьте путь и попробуйте ещё раз")
            return
        self._footer.SetLabel(f"Экспортировано записей: {frame['result'].get('rows', 0)}")


def _app_label(target_app: str | None) -> str:
    if not target_app:
        return "—"
    return target_app.removesuffix(".exe")
