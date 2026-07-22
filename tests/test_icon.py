"""``packaging/make_icon.py``: the shipped ``billytalk.ico``.

The icon is generated, not designed, so it can be tested like anything else —
and the committed file is compared against a fresh build, which is the only way
two artefacts that must agree ever stay in agreement.
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packaging"))

from make_icon import ICON_PATH, PNG_FROM, SIZES, build_ico  # noqa: E402


def test_the_committed_icon_matches_the_generator() -> None:
    """If this fails, run ``python packaging/make_icon.py`` — the glyph changed
    and the file did not (or the other way round)."""
    assert ICON_PATH.is_file(), "packaging/billytalk.ico is missing"
    assert ICON_PATH.read_bytes() == build_ico()


def test_the_directory_declares_every_size_once() -> None:
    data = ICON_PATH.read_bytes()
    reserved, kind, count = struct.unpack_from("<HHH", data, 0)
    assert (reserved, kind) == (0, 1), "not an ICO header"
    assert count == len(SIZES)

    seen = []
    for index in range(count):
        (width, height, colours, pad, planes, bits, size,
         offset) = struct.unpack_from("<BBBBHHII", data, 6 + index * 16)
        assert (colours, pad, planes, bits) == (0, 0, 1, 32)
        assert offset + size <= len(data), "an entry points past the file"
        seen.append(width or 256)
        assert (width or 256) == (height or 256), "icons here are square"
    assert seen == list(SIZES)


def test_small_sizes_are_bmp_and_large_ones_are_png() -> None:
    """Old parsers (installers, legacy shells) read BMP entries; Vista+ wants
    PNG at 128 and 256 or the file bloats. Both must be exactly where the
    format expects them."""
    data = ICON_PATH.read_bytes()
    (count,) = struct.unpack_from("<H", data, 4)
    for index in range(count):
        width, _h, _c, _p, _pl, _b, size, offset = struct.unpack_from(
            "<BBBBHHII", data, 6 + index * 16
        )
        payload = data[offset:offset + size]
        if (width or 256) >= PNG_FROM:
            assert payload.startswith(b"\x89PNG\r\n\x1a\n")
        else:
            (header_size,) = struct.unpack_from("<I", payload, 0)
            assert header_size == 40, "a BMP entry starts with BITMAPINFOHEADER"
            _hs, w, doubled_h = struct.unpack_from("<Iii", payload, 0)
            assert doubled_h == 2 * w, "ICO stores colour + mask height"


@pytest.mark.parametrize("size", [16, 32, 256])
def test_the_glyph_is_actually_drawn(size: int) -> None:
    """A plate with no microphone on it, or a fully transparent image, is the
    classic outcome of a drawing bug — and it looks fine in a file listing."""
    from make_icon import _pixels

    pixels = _pixels(size)
    opaque = [p for p in pixels if p[3] > 200]
    assert len(opaque) > 0.5 * len(pixels), "the plate must fill the tile"
    whitish = [p for p in opaque if p[0] > 230 and p[1] > 230 and p[2] > 230]
    assert whitish, "no white pixels: the microphone glyph is missing"
    corner = pixels[0]
    assert corner[3] < 128, "the rounded corner must be transparent"
