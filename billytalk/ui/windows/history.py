"""The history window (spec §10; the approved mockup).

Search over the full text (FTS5 on the core's side), insert in one action —
**executed by the core** over ``history_insert`` (spec §10: the window itself
holds the focus, only the core knows the saved target), copy, export through
the system save dialog whose path the core re-validates (spec §14).

History is the customer's safety net (Wispr Flow lesson 3) — the outcome of
an insert is always spelled out in the footer, never implied.

Statuses arrive as stable codes and are named here by key (harness §7: «код
стабилен, текст локализуется по ключу»), so the same row reads correctly in
either language and neither table can drift from the machine's vocabulary.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Final

import wx

from ...i18n import t
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

_STATUS_KEYS: Final = (
    "inserted", "left_on_clipboard", "withheld", "focus_lost", "verify_impossible",
    "blocked_secure", "pending_transcribe", "pending_retry", "transcribe_failed",
    "cancelled", "too_short", "empty",
)

FILTER_KEYS: Final = (
    ("all", "history.filter.all"),
    ("delivered", "history.filter.delivered"),
    ("clipboard", "history.filter.clipboard"),
    ("waiting", "history.filter.waiting"),
    ("other", "history.filter.other"),
)

_FILTER_SETS: Final = {
    "delivered": {"inserted", "verify_impossible"},
    "clipboard": {"left_on_clipboard", "focus_lost", "withheld"},
    "waiting": {"pending_transcribe", "pending_retry"},
}


def status_label(status: str | None, error_code: str | None = None) -> str:
    if not status:
        return t("common.dash")
    label = t(f"status.{status}") if status in _STATUS_KEYS else status
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
    bare statement (harness §7). Anything unrecognised falls back to the
    clipboard line, which is true of every delivery outcome there is: the text
    is on the clipboard first, always (spec §8)."""
    known = {"inserted", "verify_impossible", "left_on_clipboard", "focus_lost",
             "blocked_secure"}
    if status in known:
        return t(f"outcome.{status}")
    return t("outcome.left_on_clipboard")


_EXPORT_FORMATS: Final = ("txt", "csv", "json")


class HistoryFrame(wx.Frame):
    def __init__(self, controller: UiController, parent: wx.Window | None = None) -> None:
        super().__init__(parent, title=t("history.title"), size=wx.Size(840, 520))
        self._c = controller
        self._rows: list[dict[str, Any]] = []
        self._view: list[dict[str, Any]] = []
        self._total: int | None = None

        root = wx.Panel(self)
        tools = wx.BoxSizer(wx.HORIZONTAL)
        self._search = wx.TextCtrl(root, style=wx.TE_PROCESS_ENTER,
                                   size=wx.Size(300, -1))
        self._search.SetHint(t("history.search.hint"))
        self._search.Bind(wx.EVT_TEXT_ENTER, lambda _e: self.load())
        tools.Add(self._search, 0, wx.ALIGN_CENTER_VERTICAL)
        self._filter = wx.Choice(root, choices=[t(key) for _code, key in FILTER_KEYS])
        self._filter.SetSelection(0)
        self._filter.Bind(wx.EVT_CHOICE, lambda _e: self._refill())
        tools.Add(self._filter, 0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 8)
        tools.AddStretchSpacer()
        find = wx.Button(root, label=t("history.find"))
        find.Bind(wx.EVT_BUTTON, lambda _e: self.load())
        tools.Add(find, 0, wx.RIGHT, 8)
        export = wx.Button(root, label=t("history.export"))
        export.Bind(wx.EVT_BUTTON, self._on_export)
        tools.Add(export, 0)

        self._list = wx.ListCtrl(root, style=wx.LC_REPORT | wx.LC_SINGLE_SEL)
        for index, (key, width) in enumerate((
            ("history.column.time", 90), ("history.column.text", 420),
            ("history.column.app", 110), ("history.column.status", 160),
        )):
            self._list.InsertColumn(index, t(key), width=width)
        self._list.Bind(wx.EVT_LIST_ITEM_ACTIVATED, lambda _e: self._insert_selected())

        actions = wx.BoxSizer(wx.HORIZONTAL)
        insert = wx.Button(root, label=t("history.insert"))
        insert.Bind(wx.EVT_BUTTON, lambda _e: self._insert_selected())
        actions.Add(insert, 0, wx.RIGHT, 8)
        copy = wx.Button(root, label=t("history.copy"))
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
        # «Удалить всё» in settings empties the table under this window. The
        # rows on screen would then be not merely stale but dangerous: ids are
        # reused after a delete, so «Вставить» on row 1 would paste whatever
        # dictation owns id 1 now (cycle-3 review).
        self._c.on_history_cleared = self._on_cleared
        self.Bind(wx.EVT_CLOSE, self._on_close)
        self.load()

    def _on_close(self, event: wx.CloseEvent) -> None:
        if self._c.on_history_cleared == self._on_cleared:
            self._c.on_history_cleared = None
        event.Skip()

    def _on_cleared(self) -> None:
        if not self:  # destroyed: wx windows go falsy
            return
        self._rows = []
        self._total = 0
        self._refill()
        self._footer.SetLabel(t("history.footer.cleared"))

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
            self._footer.SetLabel(t("history.footer.search_failed"))
            return
        result = frame["result"]
        self._rows = list(result.get("rows", []))
        self._total = result.get("total")
        self._refill()

    def _refill(self) -> None:
        key = FILTER_KEYS[max(0, self._filter.GetSelection())][0]
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
                t("history.footer.page", shown=shown, total=self._total)
            )
        else:
            self._footer.SetLabel(t("history.footer.found", shown=shown, page=_PAGE))

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
            self._footer.SetLabel(t("history.footer.select"))
            return
        self._c.request(
            {"type": "history_insert", "row_id": row["id"]}, self._on_inserted
        )

    def _on_inserted(self, frame: dict[str, Any]) -> None:
        if "error" in frame:
            self._footer.SetLabel(t("history.footer.insert_failed"))
            return
        self._footer.SetLabel(insert_outcome_line(frame["result"].get("status")))

    def _copy_selected(self) -> None:
        row = self._selected()
        if row is None:
            self._footer.SetLabel(t("history.footer.select"))
            return
        if wx.TheClipboard.Open():
            try:
                wx.TheClipboard.SetData(wx.TextDataObject(str(row.get("text", ""))))
            finally:
                wx.TheClipboard.Close()
            self._footer.SetLabel(t("history.footer.copied"))
        else:
            self._footer.SetLabel(t("history.footer.clipboard_busy"))

    def _on_export(self, _event: wx.CommandEvent) -> None:
        dialog = wx.FileDialog(
            self, t("history.export.dialog"), wildcard=t("history.export.wildcard"),
            defaultFile=t("history.export.file"),
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
            self._footer.SetLabel(t("history.footer.export_failed"))
            return
        self._footer.SetLabel(
            t("history.footer.exported", rows=frame["result"].get("rows", 0))
        )


def _app_label(target_app: str | None) -> str:
    if not target_app:
        return t("common.dash")
    return target_app.removesuffix(".exe")
