"""Frozen entry point: one executable, two roles by argv (cycle 3, spec §15).

The shipped build is a single PyInstaller ``--onedir`` bundle. The core and the
interface are two processes (harness §14), but bundling two whole Python
runtimes would double the download, so both live in one ``BillyTalk.exe`` that
dispatches on its first argument:

* ``BillyTalk.exe``               → the core (hooks, audio, tray, IPC server);
* ``BillyTalk.exe --ui <pipe> <image>`` → the interface (wxPython, windows).

The core launches the interface with the ``--ui`` flag (see ``ui_launch`` wiring
in ``core/__main__``); under a dev checkout the same two roles are still
reachable as ``python -m billytalk.core`` and ``python -m billytalk.ui``.
"""

from __future__ import annotations

import sys


def main() -> int:
    # Absolute imports, not relative: as a PyInstaller entry point this module
    # runs as top-level ``__main__`` with no parent package, so ``from .ui …``
    # raises "attempted relative import with no known parent package". The
    # ``billytalk`` package is in the bundle, so absolute imports resolve.
    argv = sys.argv
    if len(argv) > 1 and argv[1] == "--ui":
        from billytalk.ui.__main__ import main as ui_main

        # ui.main parses argv[1:] as <pipe> [<image>]; hand it a vector whose
        # first slot is a placeholder so the pipe lands at argv[1] exactly as
        # the `-m billytalk.ui <pipe> <image>` form delivers it.
        return ui_main([argv[0], *argv[2:]])
    from billytalk.core.__main__ import main as core_main

    return core_main()


if __name__ == "__main__":
    sys.exit(main())
