"""``keycodes`` (harness §8): the round trip and the HIWORD extraction."""

from __future__ import annotations

import pytest

from billytalk.core.hooks.keycodes import (
    CODE_ESC,
    MOUSE_CODE_BASE,
    code_for_mouse,
    hiword,
    is_mouse_code,
    mouse_number,
    xbutton_to_code,
)


def test_mouse_codes_round_trip() -> None:
    for number in range(1, 11):
        code = code_for_mouse(number)
        assert is_mouse_code(code)
        assert mouse_number(code) == number


def test_the_scheme_matches_the_spec_constants() -> None:
    """Spec §2 names them outright: 4098 middle, 4099 Mouse 4 … 4105 Mouse 10."""
    assert code_for_mouse(3) == 4098
    assert code_for_mouse(4) == 4099
    assert code_for_mouse(5) == 4100
    assert code_for_mouse(10) == 4105


def test_hiword_extracts_the_xbutton_number() -> None:
    """The place where everyone gets it wrong (research/02): the number is in
    the high word of mouseData; masking the whole DWORD silently fails."""
    assert hiword(0x0001_0000) == 1  # XBUTTON1 → Mouse 4
    assert hiword(0x0002_0000) == 2  # XBUTTON2 → Mouse 5
    assert hiword(0x0001_FFFF) == 1, "the reserved low word must not leak in"
    assert hiword(0x0000_0001) == 0, "a naive &1 mask would wrongly see XBUTTON1 here"


def test_xbutton_to_code() -> None:
    assert xbutton_to_code(1) == 4099
    assert xbutton_to_code(2) == 4100
    with pytest.raises(ValueError):
        xbutton_to_code(3)


def test_keyboard_codes_are_untouched() -> None:
    assert CODE_ESC == 0x1B
    assert not is_mouse_code(CODE_ESC)
    with pytest.raises(ValueError):
        mouse_number(CODE_ESC)


def test_out_of_range_mouse_numbers_refused() -> None:
    for bad in (0, 11, -1):
        with pytest.raises(ValueError):
            code_for_mouse(bad)
    assert not is_mouse_code(MOUSE_CODE_BASE + 10)
