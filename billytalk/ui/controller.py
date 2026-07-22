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
import secrets
from collections.abc import Callable
from typing import Any

from ..core.ipc.protocol import menu_model, shutdown, toggle_dictation
from ..i18n import t
from .overlay import Plashka, PlashkaLook

log = logging.getLogger("billytalk.ui.controller")

__all__ = ["UiController"]


def _target_alive(callback: Callable[..., Any]) -> bool:
    """False when a reply callback is a bound method of a wx window the user
    already closed. wxPython makes a destroyed window falsy, so touching its
    controls from a late reply would raise «wrapped C/C++ object deleted».

    The falsy check is scoped to ``wx.Window`` on purpose — a bound method of
    an ordinary object (an empty list, say) is falsy for reasons that have
    nothing to do with being deleted. A plain function, a lambda, or a
    non-window owner is always live."""
    owner = getattr(callback, "__self__", None)
    if owner is None:
        return True
    try:
        import wx
    except ImportError:
        return True
    if isinstance(owner, wx.Window):
        return bool(owner)
    return True

# The plashka look each display state calls for; anything else hides it. The
# keys are the TrayState values the core sends in state_changed.
_LOOK_FOR = {
    "recording": PlashkaLook.RECORDING,
    "transcribing": PlashkaLook.TRANSCRIBING,
}

_CMD_SETTINGS, _CMD_TOGGLE, _CMD_HISTORY, _CMD_EXIT = 201, 202, 203, 209


def _menu_items(enabled: bool, *, windows: bool) -> list[dict[str, Any]]:
    return [
        {"command": _CMD_SETTINGS, "label": t("menu.settings"), "enabled": windows},
        {"command": _CMD_HISTORY, "label": t("menu.history"), "enabled": windows},
        {"command": 0, "label": ""},  # separator
        {"command": _CMD_TOGGLE, "label": t("menu.toggle"), "checked": enabled},
        {"command": 0, "label": ""},
        {"command": _CMD_EXIT, "label": t("menu.exit")},
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
        self.on_hotkey_captured: Callable[[dict[str, Any]], None] | None = None
        """The capture dialog parks itself here while it is open —
        ``hotkey_captured`` is a push, not a reply, so it needs its own seat."""
        self.on_devices: Callable[[list[Any]], None] | None = None
        """The settings window watches the recording devices here: plugging a
        headset in while it is open should change the list, not require a
        reopen. ``device_list_changed`` is a push, so it needs its own seat."""
        self.on_state: Callable[[str | None], None] | None = None
        """The wizard's live-test step watches the machine here: it must know
        that a dictation happened before it asks the history for the words."""
        self.apply_language: Callable[[str], None] | None = None
        """Wired by ``__main__``: switch the string table, resend the tray menu
        and rebuild the open windows. Labels are read at build time, so a live
        relabel would mean tracking every static in every window — rebuilding
        is what Windows itself does and is the only version that cannot leave
        half a window in the old language."""
        self._enabled = True
        # A random base, not 1: if the core restarts a UI mid-flight, a
        # driver-thread job posted by the OLD interface answers over the new
        # connection with its old request id. Colliding on a small counter both
        # started at 1 would feed the new window a stranger's payload; 31 random
        # bits make that practically impossible (OPEN-QUESTIONS §31).
        self._next_request = secrets.randbits(31) + 1
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
            if callback is not None and _target_alive(callback):
                callback(message)
        elif kind == "hotkey_captured":
            if self.on_hotkey_captured is not None:
                self.on_hotkey_captured(message)
        elif kind == "device_list_changed":
            if self.on_devices is not None:
                self.on_devices(message.get("inputs") or [])

    def _on_state(self, state: object) -> None:
        if self.on_state is not None:
            self.on_state(state if isinstance(state, str) else None)
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
