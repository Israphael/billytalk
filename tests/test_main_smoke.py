"""Import smoke for the two process entry points.

Neither ``__main__`` has a unit harness — they install global hooks and open
real pipes — so this at least fails loudly on an import-time regression (a
missing import, a renamed symbol) instead of only at first run on the
customer's machine. Importing runs module-level code but not ``main()`` (it is
guarded by ``if __name__ == "__main__"``), so there are no side effects.
"""

from __future__ import annotations

import importlib


def test_core_main_imports() -> None:
    importlib.import_module("billytalk.core.__main__")


def test_ui_main_imports() -> None:
    importlib.import_module("billytalk.ui.__main__")


def test_frozen_entry_imports_and_dispatches() -> None:
    """The single frozen entry point (cycle 3): importable, and its dispatch
    routes --ui to the interface and everything else to the core without
    running either (both main()s are guarded)."""
    mod = importlib.import_module("billytalk.__main__")
    assert hasattr(mod, "main")
    import inspect

    source = inspect.getsource(mod.main)
    assert "--ui" in source and "ui_main" in source and "core_main" in source
