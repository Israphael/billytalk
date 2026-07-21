"""``python -m billytalk.ui <pipe_name> [<expected_image>]`` — the interface process.

The core launches this on demand (harness §2), passing the channel name and its
own image path so the client can prove which server it is talking to. wxPython
owns the main thread; the ``IpcClient`` reader thread marshals every message onto
it with ``wx.CallAfter``. Only ``chrome.py`` and ``overlay.py`` touch ctypes
(harness §1's border rule) — nothing here does.

This module is assembly only: parse argv, build the real collaborators, run the
loop. The logic lives in ``controller.py`` where tests reach it; ``«остановлен»
+ restart`` on a core death is a later milestone (harness §2), so for now a
lost core simply ends the process.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import wx

from ..core.logging_setup import configure_logging
from .controller import UiController
from .ipc.client import IpcClient
from .overlay import Plashka

log = logging.getLogger("billytalk.ui.main")


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
