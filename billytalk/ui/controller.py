"""The interface's brain, factored out of ``__main__`` so tests reach it.

It drives the plashka from ``state_changed`` pushes, owns the tray menu
(OPEN-QUESTIONS §22): the core renders the model sent from here and forwards
clicks back as ``menu_command`` — and correlates the request/reply verbs the
milestone-2 windows live on (OPEN-QUESTIONS §21: requests carry an integer
``id``, the core answers ``{"type":"reply","id":…}``). Every method runs on
the GUI thread — the reader thread marshals messages over with
``wx.CallAfter`` in ``__main__``, so the pending-reply table needs no lock.

No ctypes here (harness §1's border rule): the plashka is injected, the menu
crosses the channel as plain dicts, the windows come as injected openers.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from ..core.ipc.protocol import menu_model, shutdown, toggle_dictation
from .overlay import Plashka, PlashkaLook

log = logging.getLogger("billytalk.ui.controller")

__all__ = ["UiController"]

# The plashka look each display state calls for; anything else hides it. The
# keys are the TrayState values the core sends in state_changed.
_LOOK_FOR = {
    "recording": PlashkaLook.RECORDING,
    "transcribing": PlashkaLook.TRANSCRIBING,
}

_CMD_SETTINGS, _CMD_TOGGLE, _CMD_HISTORY, _CMD_EXIT = 201, 202, 203, 209


def _menu_items(enabled: bool, *, windows: bool) -> list[dict[str, Any]]:
    return [
        {"command": _CMD_SETTINGS, "label": "Открыть настройки", "enabled": windows},
        {"command": _CMD_HISTORY, "label": "История", "enabled": windows},
        {"command": 0, "label": ""},  # separator
        {"command": _CMD_TOGGLE, "label": "Диктовка включена", "checked": enabled},
        {"command": 0, "label": ""},
        {"command": _CMD_EXIT, "label": "Выход"},
    ]


class UiController:
    """Drives the plashka and owns the tray menu over a ``send`` set once the
    client exists. Every method runs on the GUI thread.

    ``open_settings`` / ``open_history`` are wired by ``__main__`` after
    construction (the openers close over this controller); while they are
    ``None`` the menu offers the windows greyed out — never a dead click.
    """

    def __init__(self, plashka: Plashka) -> None:
        self._plashka = plashka
        self.send: Callable[[dict[str, Any]], None] | None = None
        self.open_settings: Callable[[], None] | None = None
        self.open_history: Callable[[], None] | None = None
        self._enabled = True
        self._next_request = 1
        self._pending: dict[int, Callable[[dict[str, Any]], None]] = {}

    def push_menu(self) -> None:
        """Send the current tray menu to the core to render."""
        if self.send is not None:
            windows = self.open_settings is not None and self.open_history is not None
            self.send(menu_model(_menu_items(self._enabled, windows=windows)))

    def request(
        self, message: dict[str, Any], on_reply: Callable[[dict[str, Any]], None]
    ) -> None:
        """Send a verb with a fresh ``id``; ``on_reply`` gets the whole reply
        frame (``result`` or ``error`` — the caller reads which). If the core
        dies first, the client's disconnect ends this process; undelivered
        callbacks die with it, which is the right amount of ceremony."""
        if self.send is None:
            return
        request_id = self._next_request
        self._next_request += 1
        self._pending[request_id] = on_reply
        self.send({**message, "id": request_id})

    def dispatch(self, message: dict[str, Any]) -> None:
        kind = message.get("type")
        log.info("core message %s", kind)  # payloads never logged (spec §13)
        if kind == "state_changed":
            self._on_state(message.get("state"))
        elif kind == "menu_command":
            self._on_menu_command(message.get("command"))
        elif kind == "reply":
            callback = self._pending.pop(message.get("id"), None)
            if callback is not None:
                callback(message)

    def _on_state(self, state: object) -> None:
        look = _LOOK_FOR.get(state)
        if look is not None:
            self._plashka.show(look)
        else:
            self._plashka.hide()
        # "stopped" is the only state that means dictation is off; keep the
        # toggle's check mark honest by resending the menu when it flips.
        enabled = state != "stopped"
        if enabled != self._enabled:
            self._enabled = enabled
            self.push_menu()

    def _on_menu_command(self, command: object) -> None:
        if command == _CMD_SETTINGS and self.open_settings is not None:
            self.open_settings()
            return
        if command == _CMD_HISTORY and self.open_history is not None:
            self.open_history()
            return
        if self.send is None:
            return
        if command == _CMD_TOGGLE:
            self.send(toggle_dictation(not self._enabled))
        elif command == _CMD_EXIT:
            self.send(shutdown())
