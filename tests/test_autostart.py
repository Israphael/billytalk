"""``core/autostart.py``: spec §12's Run key, read and written.

The registry is real here, but never the real ``Run`` key: every test drives
the module against a private ``HKCU\\Software\\BillyTalk-tests-*`` path, so a
test run can neither register nor unregister anything at logon.
"""

from __future__ import annotations

import winreg
from uuid import uuid4

import pytest

from billytalk.core.autostart import (
    AutostartState,
    autostart_state,
    is_disabled_by_windows,
    quote_command,
    set_autostart,
)


@pytest.fixture
def keys():
    """A private pair of keys, deleted afterwards whatever the test did."""
    suffix = uuid4().hex[:8]
    run = rf"Software\BillyTalk-tests-{suffix}\Run"
    approved = rf"Software\BillyTalk-tests-{suffix}\Approved"
    yield run, approved
    for path in (run, approved, rf"Software\BillyTalk-tests-{suffix}"):
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path)
        except OSError:
            pass


def _write(path: str, name: str, value, kind=winreg.REG_SZ) -> None:
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, name, 0, kind, value)


# --------------------------------------------------------------------------- #
# the byte-0 rule (spec §12, verbatim)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("first_byte,disabled", [
    (0x02, False),  # enabled, the documented value
    (0x03, True),   # disabled, the documented value
    (0x06, False),  # seen in the wild: even → enabled
    (0x07, True),   # seen in the wild: `== 3` would call this ENABLED
    (0x00, False),
])
def test_disabled_is_the_low_bit_never_equality_with_three(
    first_byte: int, disabled: bool
) -> None:
    blob = bytes([first_byte]) + b"\x00" * 11
    assert is_disabled_by_windows(blob) is disabled


@pytest.mark.parametrize("blob", [None, b"", "not bytes", 3])
def test_a_missing_or_odd_veto_means_no_veto(blob: object) -> None:
    assert is_disabled_by_windows(blob) is False


# --------------------------------------------------------------------------- #
# state
# --------------------------------------------------------------------------- #


def test_state_without_an_installed_exe_is_unavailable(keys) -> None:
    run, approved = keys
    state = autostart_state(exe=None, run_key=run, approved_key=approved)
    assert state.available is False and state.enabled is False


def test_enabled_needs_both_our_value_and_windows_consent(keys) -> None:
    run, approved = keys
    exe = r"C:\Users\Test\AppData\Local\Programs\BillyTalk\BillyTalk.exe"
    _write(run, "BillyTalk", quote_command(exe))
    state = autostart_state(exe=exe, run_key=run, approved_key=approved)
    assert state.registered and state.matches_current_exe and state.enabled

    _write(approved, "BillyTalk", bytes([0x07]) + b"\x00" * 11, winreg.REG_BINARY)
    vetoed = autostart_state(exe=exe, run_key=run, approved_key=approved)
    assert vetoed.registered is True
    assert vetoed.disabled_by_windows is True
    assert vetoed.enabled is False, "Windows' Startup page wins over our value"


def test_a_value_pointing_elsewhere_is_registered_but_not_ours(keys) -> None:
    """An install that moved (or another build's leftover) must not be reported
    as «this exe starts with Windows» — the checkbox would be lying."""
    run, approved = keys
    _write(run, "BillyTalk", r'"C:\Old\Path\BillyTalk.exe"')
    state = autostart_state(
        exe=r"C:\New\Path\BillyTalk.exe", run_key=run, approved_key=approved
    )
    assert state.registered is True and state.matches_current_exe is False


# --------------------------------------------------------------------------- #
# writing
# --------------------------------------------------------------------------- #


def test_enabling_writes_the_quoted_path_and_clears_the_veto(keys) -> None:
    run, approved = keys
    exe = r"C:\Users\Ivan Petrov\AppData\Local\Programs\BillyTalk\BillyTalk.exe"
    _write(approved, "BillyTalk", bytes([0x03]) + b"\x00" * 11, winreg.REG_BINARY)

    state = set_autostart(True, exe=exe, run_key=run, approved_key=approved)
    assert state.enabled is True
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, run) as k:
        value, _kind = winreg.QueryValueEx(k, "BillyTalk")
    assert value == f'"{exe}"', "the path has spaces; unquoted it would not launch"
    assert state.disabled_by_windows is False


def test_disabling_removes_both_values(keys) -> None:
    """A leftover StartupApproved entry would silently shadow the next enable."""
    run, approved = keys
    exe = r"C:\Programs\BillyTalk\BillyTalk.exe"
    set_autostart(True, exe=exe, run_key=run, approved_key=approved)
    _write(approved, "BillyTalk", bytes([0x03]) + b"\x00" * 11, winreg.REG_BINARY)

    state = set_autostart(False, exe=exe, run_key=run, approved_key=approved)
    assert state.registered is False and state.enabled is False
    assert state.disabled_by_windows is False

    again = set_autostart(True, exe=exe, run_key=run, approved_key=approved)
    assert again.enabled is True, "a re-enable must not stay shadowed"


def test_setting_without_an_exe_changes_nothing(keys) -> None:
    run, approved = keys
    state = set_autostart(True, exe=None, run_key=run, approved_key=approved)
    assert state == AutostartState(
        available=False, registered=False, disabled_by_windows=False,
        matches_current_exe=False,
    )
