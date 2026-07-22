"""Generate the installer's sidebar artwork — ``welcome.bmp``.

Run: ``python packaging/make_installer_art.py`` (no dependencies).

NSIS ships a 1990s dithered blue bitmap and shows it on the first page every
user sees. The page header is left alone on purpose: MUI already draws our icon
there, on the system's white, and a blue block beside it would read as a
sticker. Drawn here for the same reasons the icon is (``make_icon.py``): no
imaging dependency for two files, the shapes are diffable like code, and a
test can regenerate the bytes and compare.

BMP, not PNG: the MUI pages take a Windows bitmap and nothing else. 24-bit,
bottom-up, rows padded to four bytes — the format's own rules, and the reason
the default artwork is dithered at all is that it is 8-bit.
"""

from __future__ import annotations

import struct
from pathlib import Path
from typing import Final

# MUI's fixed sizes. Anything else is stretched by Windows and looks it.
WELCOME_SIZE: Final = (164, 314)

# The product's blue, top to bottom — the same family as the icon's plate.
TOP: Final = (0x2B, 0x7C, 0xD6)
BOTTOM: Final = (0x0E, 0x3E, 0x7E)
GLYPH: Final = (0xFF, 0xFF, 0xFF)

SUPERSAMPLE: Final = 3


def _rounded_rect(x: float, y: float, box: tuple[float, float, float, float],
                  radius: float) -> bool:
    left, top, right, bottom = box
    if not (left <= x <= right and top <= y <= bottom):
        return False
    cx = min(max(x, left + radius), right - radius)
    cy = min(max(y, top + radius), bottom - radius)
    dx, dy = x - cx, y - cy
    return dx * dx + dy * dy <= radius * radius


def _ring(x: float, y: float, centre: tuple[float, float],
          inner: float, outer: float, *, below: float) -> bool:
    if y < below:
        return False
    dx, dy = x - centre[0], y - centre[1]
    distance = (dx * dx + dy * dy) ** 0.5
    return inner <= distance <= outer


def _mic(x: float, y: float) -> bool:
    """The same microphone the icon and the tray draw, in unit coordinates."""
    capsule = _rounded_rect(x, y, (0.385, 0.140, 0.615, 0.555), 0.115)
    cradle = _ring(x, y, (0.500, 0.430), 0.275, 0.340, below=0.460)
    stem = _rounded_rect(x, y, (0.478, 0.735, 0.522, 0.855), 0.020)
    base = _rounded_rect(x, y, (0.350, 0.845, 0.650, 0.895), 0.022)
    return capsule or cradle or stem or base


def _welcome_pixel(x: float, y: float) -> bool:
    """The glyph sits in the upper third, large and quiet — the sidebar is
    decoration beside real text, not a poster competing with it."""
    size = 0.62
    left, top = 0.19, 0.10
    if not (left <= x <= left + size and top <= y <= top + size):
        return False
    return _mic((x - left) / size, (y - top) / size)


def _render(width: int, height: int, glyph, *, plain: bool = False) -> list[bytes]:
    """Rows of BGR bytes, top-down; the writer flips them."""
    rows: list[bytes] = []
    for row in range(height):
        mix = row / max(1, height - 1)
        plate = tuple(
            round(top + (bottom - top) * mix) for top, bottom in zip(TOP, BOTTOM)
        )
        line = bytearray()
        for column in range(width):
            if plain:
                colour = plate
            else:
                hits = 0
                for sub_y in range(SUPERSAMPLE):
                    y = (row + (sub_y + 0.5) / SUPERSAMPLE) / height
                    for sub_x in range(SUPERSAMPLE):
                        x = (column + (sub_x + 0.5) / SUPERSAMPLE) / width
                        if glyph(x, y):
                            hits += 1
                coverage = hits / (SUPERSAMPLE * SUPERSAMPLE)
                colour = tuple(
                    round(base + (mark - base) * coverage)
                    for base, mark in zip(plate, GLYPH)
                )
            line.extend((colour[2], colour[1], colour[0]))  # BMP is BGR
        line.extend(b"\x00" * ((4 - len(line) % 4) % 4))    # rows pad to 4 bytes
        rows.append(bytes(line))
    return rows


def build_bmp(width: int, height: int, glyph) -> bytes:
    rows = _render(width, height, glyph)
    pixels = b"".join(reversed(rows))  # BMP stores the bottom row first
    header = struct.pack(
        "<IiiHHIIiiII", 40, width, height, 1, 24, 0, len(pixels), 2835, 2835, 0, 0
    )
    offset = 14 + len(header)
    file_header = struct.pack("<2sIHHI", b"BM", offset + len(pixels), 0, 0, offset)
    return file_header + header + pixels


WELCOME_PATH: Final = Path(__file__).with_name("welcome.bmp")


def main() -> None:
    WELCOME_PATH.write_bytes(build_bmp(*WELCOME_SIZE, _welcome_pixel))
    print(f"{WELCOME_PATH.name}: {WELCOME_PATH.stat().st_size} bytes")


if __name__ == "__main__":
    main()
