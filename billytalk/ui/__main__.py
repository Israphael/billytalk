"""``python -m billytalk.ui <pipe_name> [<expected_image>]`` — the interface process.

The core launches this on demand (harness §2), passing the channel name and its
own image path so the client can prove which server it is talking to. wxPython
owns the main thread; the ``IpcClient`` reader thread marshals every message onto
it with ``wx.CallAfter``. Only ``chrome.py`` and ``overlay.py`` touch ctypes
(harness §1's border rule) — nothing here does.

Cycle-2 milestone-2 scope is the scaffold: connect, take messages, tear down
cleanly. The plashka, the tray-menu provider and the settings/history windows
land in the following steps; ``«остановлен» + restart`` on a core death is
milestone 3 (harness §2), so for now a lost core simply ends the process.
"""

from __future__ import annotations

import logging
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import wx

from ..core.ipc.protocol import menu_model, shutdown, toggle_dictation
from ..core.logging_setup import configure_logging
from .ipc.client import IpcClient
from .overlay import Plashka, PlashkaLook

log = logging.getLogger("billytalk.ui.main")

# The plashka look each display state calls for; anything else hides it. The
# keys are the TrayState values the core sends in state_changed.
_LOOK_FOR = {
    "recording": PlashkaLook.RECORDING,
    "transcribing": PlashkaLook.TRANSCRIBING,
}

# The interface owns the tray menu (OPEN-QUESTIONS §22); the core renders it and
# forwards clicks back as menu_command. Settings/history are present but disabled
# until their windows land in milestone 3.
_CMD_SETTINGS, _CMD_TOGGLE, _CMD_HISTORY, _CMD_EXIT = 201, 202, 203, 209


def _menu_items(enabled: bool) -> list[dict[str, Any]]:
    return [
        {"command": _CMD_SETTINGS, "label": "Открыть настройки", "enabled": False},
        {"command": _CMD_HISTORY, "label": "История", "enabled": False},
        {"command": 0, "label": ""},  # separator
        {"command": _CMD_TOGGLE, "label": "Диктовка включена", "checked": enabled},
        {"command": 0, "label": ""},
        {"command": _CMD_EXIT, "label": "Выход"},
    ]


class UiController:
    """The interface's brain: it drives the plashka and owns the tray menu over
    a ``send`` set once the client exists. Every method runs on the GUI thread."""

    def __init__(self, plashka: Plashka) -> None:
        self._plashka = plashka
        self.send: Callable[[dict[str, Any]], None] | None = None
        self._enabled = True

    def push_menu(self) -> None:
        """Send the current tray menu to the core to render."""
        if self.send is not None:
            self.send(menu_model(_menu_items(self._enabled)))

    def dispatch(self, message: dict[str, Any]) -> None:
        kind = message.get("type")
        log.info("core message %s", kind)  # payloads never logged (spec §13)
        if kind == "state_changed":
            self._on_state(message.get("state"))
        elif kind == "menu_command":
            self._on_menu_command(message.get("command"))

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
        if self.send is None:
            return
        if command == _CMD_TOGGLE:
            self.send(toggle_dictation(not self._enabled))
        elif command == _CMD_EXIT:
            self.send(shutdown())
        # _CMD_SETTINGS / _CMD_HISTORY open windows — milestone 3.


def _parse_args(argv: list[str]) -> tuple[str, str | None]:
    if len(argv) < 2:
        raise SystemExit("usage: python -m billytalk.ui <pipe_name> [<expected_image>]")
    name = argv[1]
    # "-" is the explicit skip-verification token: a test, or a core that could
    # not resolve its own image. Anything else is the path the client verifies.
    image = argv[2] if len(argv) > 2 and argv[2] != "-" else None
    return name, image


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    name, expected_image = _parse_args(argv)

    local = Path(os.environ["LOCALAPPDATA"]) / "BillyTalk"
    configure_logging(local / "logs", filename="ui.log")

    # wx.App must exist before any wx.CallAfter can queue onto its loop, and
    # before the plashka's window can be created.
    app = wx.App()
    plashka = Plashka()
    controller = UiController(plashka)

    client = IpcClient(
        name,
        on_message=lambda m: wx.CallAfter(controller.dispatch, m),  # reader → GUI
        expected_image=expected_image,
        on_disconnect=lambda: wx.CallAfter(app.ExitMainLoop),  # core gone → quit
    )
    try:
        client.connect()
    except Exception:
        log.exception("could not connect to the core")
        plashka.destroy()
        return 2
    controller.send = client.send
    controller.push_menu()  # fill the tray menu straight away
    log.info("connected to core %s", client.core_version)

    try:
        app.MainLoop()
    finally:
        client.close()
        plashka.destroy()
    return 0


if __name__ == "__main__":
    sys.exit(main())
