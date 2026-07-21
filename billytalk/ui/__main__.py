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
from pathlib import Path
from typing import Any

import wx

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

    def dispatch(message: dict[str, Any]) -> None:
        """GUI thread. Payloads are never logged (spec §13) — the type only."""
        kind = message.get("type")
        log.info("core message %s", kind)
        if kind == "state_changed":
            look = _LOOK_FOR.get(message.get("state"))
            if look is not None:
                plashka.show(look)
            else:
                plashka.hide()

    client = IpcClient(
        name,
        on_message=lambda m: wx.CallAfter(dispatch, m),   # reader thread → GUI
        expected_image=expected_image,
        on_disconnect=lambda: wx.CallAfter(app.ExitMainLoop),  # core gone → quit
    )
    try:
        client.connect()
    except Exception:
        log.exception("could not connect to the core")
        plashka.destroy()
        return 2
    log.info("connected to core %s", client.core_version)

    try:
        app.MainLoop()
    finally:
        client.close()
        plashka.destroy()
    return 0


if __name__ == "__main__":
    sys.exit(main())
