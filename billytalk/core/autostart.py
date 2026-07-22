"""Autostart in ``HKCU\\...\\Run`` (spec §12), read and written honestly.

Three rules from the spec, each paid for by a known Windows trap:

* **The value is written directly**, not through a helper API — the installer
  and this module write the same string, so «установлено» and «включено в
  настройках» cannot disagree.
* **Disabled is ``byte0 & 1``, never ``== 3``.** Windows records the user's
  Startup-page choice in ``Explorer\\StartupApproved\\Run`` as a 12-byte blob
  whose first byte is 2 for enabled and 3 for disabled — *except* that ``06``
  and ``07`` occur in the wild. Comparing to 3 reports a disabled entry as
  enabled and the checkbox lies.
* **We never overrule Windows behind the user's back.** Nothing here runs on
  its own: the state is read for display, and written only from an explicit
  toggle or the wizard's checkbox. An explicit «включить» *does* clear the
  StartupApproved veto — that is the user speaking now, in our window, about
  the same switch — and that is the only path that touches it.

The decision logic is pure functions over bytes and strings; the registry
access is a thin shell around them, with the key paths injectable so the tests
never go near the real ``Run`` key.
"""

from __future__ import annotations

import logging
import sys
import winreg
from dataclasses import dataclass
from pathlib import Path
from typing import Final

log = logging.getLogger("billytalk.autostart")

__all__ = [
    "APPROVED_KEY",
    "AutostartState",
    "RUN_KEY",
    "VALUE_NAME",
    "autostart_state",
    "installed_exe_path",
    "is_disabled_by_windows",
    "quote_command",
    "set_autostart",
]

RUN_KEY: Final = r"Software\Microsoft\Windows\CurrentVersion\Run"
APPROVED_KEY: Final = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run"
VALUE_NAME: Final = "BillyTalk"


def quote_command(exe: str) -> str:
    """The ``Run`` value for an executable path: quoted, because the install
    path contains spaces on any machine whose user name does."""
    return f'"{exe}"'


def is_disabled_by_windows(blob: object) -> bool:
    """Spec §12's check, verbatim: the low bit of byte 0 means «disabled».

    Anything that is not a non-empty bytes-like value means «no veto recorded»,
    which is Windows' own default state.
    """
    if not isinstance(blob, (bytes, bytearray)) or not blob:
        return False
    return bool(blob[0] & 1)


def installed_exe_path() -> str | None:
    """The executable to register, or ``None`` when there is nothing sensible
    to register — a dev checkout runs ``python -m billytalk.core``, and putting
    an interpreter plus arguments into ``Run`` would break the moment the venv
    moved. The settings row says so instead of pretending."""
    if not getattr(sys, "frozen", False):
        return None
    return str(Path(sys.executable).resolve())


@dataclass(frozen=True, slots=True)
class AutostartState:
    """What the settings row and the wizard checkbox display.

    ``available`` False means the concept does not apply to this process (dev
    checkout); ``enabled`` is the honest answer to «will it start with
    Windows», which requires both our value and Windows' consent.
    """

    available: bool
    registered: bool
    disabled_by_windows: bool
    matches_current_exe: bool

    @property
    def enabled(self) -> bool:
        return self.registered and not self.disabled_by_windows

    def as_wire(self) -> dict[str, bool]:
        return {
            "available": self.available,
            "enabled": self.enabled,
            "registered": self.registered,
            "disabled_by_windows": self.disabled_by_windows,
            "matches_current_exe": self.matches_current_exe,
        }


def _read_value(root_key: str, name: str) -> object | None:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, root_key) as key:
            value, _kind = winreg.QueryValueEx(key, name)
            return value
    except OSError:
        return None


def autostart_state(
    *, exe: str | None = None, run_key: str = RUN_KEY, approved_key: str = APPROVED_KEY,
    value_name: str = VALUE_NAME,
) -> AutostartState:
    """Read both keys and fold them into one displayable answer."""
    exe = exe if exe is not None else installed_exe_path()
    registered_value = _read_value(run_key, value_name)
    registered = isinstance(registered_value, str) and bool(registered_value.strip())
    matches = bool(
        registered and exe and str(registered_value).strip().strip('"').lower()
        == exe.lower()
    )
    return AutostartState(
        available=exe is not None,
        registered=registered,
        disabled_by_windows=is_disabled_by_windows(_read_value(approved_key, value_name)),
        matches_current_exe=matches,
    )


def set_autostart(
    enabled: bool, *, exe: str | None = None, run_key: str = RUN_KEY,
    approved_key: str = APPROVED_KEY, value_name: str = VALUE_NAME,
) -> AutostartState:
    """Turn autostart on or off and answer with the state that resulted.

    Enabling writes our value and clears the StartupApproved veto (see the
    module docstring for why that is not overruling the user). Disabling
    removes both: a lone StartupApproved leftover would silently shadow the
    next enable.

    Registry failures are logged and swallowed — the caller re-reads the state
    and shows what actually happened, which is more honest than an exception
    that says what we intended.
    """
    exe = exe if exe is not None else installed_exe_path()
    if exe is None:
        return autostart_state(
            exe=exe, run_key=run_key, approved_key=approved_key, value_name=value_name
        )
    try:
        if enabled:
            with winreg.CreateKeyEx(
                winreg.HKEY_CURRENT_USER, run_key, 0, winreg.KEY_SET_VALUE
            ) as key:
                winreg.SetValueEx(key, value_name, 0, winreg.REG_SZ, quote_command(exe))
            _delete_value(approved_key, value_name)
        else:
            _delete_value(run_key, value_name)
            _delete_value(approved_key, value_name)
    except OSError:
        log.warning("could not %s autostart", "enable" if enabled else "disable")
    return autostart_state(
        exe=exe, run_key=run_key, approved_key=approved_key, value_name=value_name
    )


def _delete_value(root_key: str, name: str) -> None:
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, root_key, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.DeleteValue(key, name)
    except FileNotFoundError:
        pass  # absent is the goal, not a failure
    except OSError:
        log.warning("could not remove the autostart value")
