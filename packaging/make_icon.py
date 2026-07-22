"""Generate ``packaging/billytalk.ico`` — the executable's and the shortcut's icon.

Run: ``python packaging/make_icon.py`` (no dependencies).

Why draw it in code rather than ship a designed asset: the stack has no imaging
library (Pillow is not a dependency and one file is not worth its supply chain),
the tray already draws its own icons at runtime for the same reason, and a
generated icon is diffable — a change to the glyph is a change to these numbers,
reviewable like any other code. The test suite regenerates the bytes and
compares them with the committed file, so the two can never drift.

Format choices, both deliberate:

* sizes ≤ 64 are stored as **BMP** (BITMAPINFOHEADER with the doubled height and
  an AND mask, exactly as the ICO format has always meant it). Every icon parser
  ever written reads those, including the ones inside installers and old shells.
* 128 and 256 are stored as **PNG**, which is what Vista+ expects at those sizes
  and what keeps the file from reaching a megabyte.

Anti-aliasing is 4×4 supersampling: each output pixel is the average of sixteen
coverage samples. Nearest-neighbour edges on a 16-pixel mic look like damage.
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path
from typing import Final

SIZES: Final = (16, 20, 24, 32, 40, 48, 64, 128, 256)
PNG_FROM: Final = 128
"""Sizes at and above this are stored as PNG; below, as BMP."""

SUPERSAMPLE: Final = 4

# Colours (R, G, B). The plate is the product's blue — the same family as the
# tray's «transcribing» dot — and the glyph is white, so the icon reads on both
# a light and a dark taskbar without needing two variants.
PLATE_TOP: Final = (0x2B, 0x7C, 0xD6)
PLATE_BOTTOM: Final = (0x12, 0x4F, 0x9E)
GLYPH: Final = (0xFF, 0xFF, 0xFF)


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


def _glyph(x: float, y: float) -> bool:
    """The microphone, in unit coordinates: capsule, cradle, stem, base.

    The same shape the tray draws with GDI — one product, one silhouette.
    """
    # Strokes are deliberately fat: at 16 and 20 pixels a hairline arc turns
    # into three grey dots. The shape is tuned so the smallest size still reads
    # as a microphone, and the largest still looks drawn rather than swollen.
    capsule = _rounded_rect(x, y, (0.385, 0.140, 0.615, 0.555), 0.115)
    cradle = _ring(x, y, (0.500, 0.430), 0.275, 0.340, below=0.460)
    stem = _rounded_rect(x, y, (0.478, 0.735, 0.522, 0.855), 0.020)
    base = _rounded_rect(x, y, (0.350, 0.845, 0.650, 0.895), 0.022)
    return capsule or cradle or stem or base


def _plate(x: float, y: float) -> bool:
    return _rounded_rect(x, y, (0.020, 0.020, 0.980, 0.980), 0.220)


def _pixels(size: int) -> list[tuple[int, int, int, int]]:
    """One RGBA image, top-down, anti-aliased by supersampling."""
    out: list[tuple[int, int, int, int]] = []
    step = 1.0 / (size * SUPERSAMPLE)
    samples = SUPERSAMPLE * SUPERSAMPLE
    for row in range(size):
        for column in range(size):
            plate_hits = glyph_hits = 0
            for sub_y in range(SUPERSAMPLE):
                y = (row * SUPERSAMPLE + sub_y + 0.5) * step
                for sub_x in range(SUPERSAMPLE):
                    x = (column * SUPERSAMPLE + sub_x + 0.5) * step
                    if _plate(x, y):
                        plate_hits += 1
                        if _glyph(x, y):
                            glyph_hits += 1
            if not plate_hits:
                out.append((0, 0, 0, 0))
                continue
            # The plate's vertical gradient, then the glyph composited over it
            # by coverage — so a half-covered glyph pixel is half white.
            mix = row / max(1, size - 1)
            plate = tuple(
                round(top + (bottom - top) * mix)
                for top, bottom in zip(PLATE_TOP, PLATE_BOTTOM)
            )
            coverage = glyph_hits / plate_hits
            colour = tuple(
                round(base + (glyph - base) * coverage)
                for base, glyph in zip(plate, GLYPH)
            )
            out.append((*colour, round(255 * plate_hits / samples)))
    return out


def _png(size: int, pixels: list[tuple[int, int, int, int]]) -> bytes:
    def chunk(tag: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload)) + tag + payload
            + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
        )

    raw = bytearray()
    for row in range(size):
        raw.append(0)  # filter type 0 (None): the image is tiny, filters buy little
        for column in range(size):
            raw.extend(pixels[row * size + column])
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + chunk(b"IEND", b"")
    )


def _bmp(size: int, pixels: list[tuple[int, int, int, int]]) -> bytes:
    """A BITMAPINFOHEADER DIB as an ICO wants it: doubled height (colour plus
    mask), BGRA, bottom-up, and an AND mask that is all-zero because the alpha
    channel already carries the transparency."""
    header = struct.pack(
        "<IiiHHIIiiII", 40, size, size * 2, 1, 32, 0, size * size * 4, 0, 0, 0, 0
    )
    colour = bytearray()
    for row in range(size - 1, -1, -1):
        for column in range(size):
            r, g, b, a = pixels[row * size + column]
            colour.extend((b, g, r, a))
    mask_row = ((size + 31) // 32) * 4  # 1 bpp, rows padded to 4 bytes
    return header + bytes(colour) + bytes(mask_row * size)


def build_ico() -> bytes:
    images: list[tuple[int, bytes]] = []
    for size in SIZES:
        pixels = _pixels(size)
        images.append((size, _png(size, pixels) if size >= PNG_FROM
                       else _bmp(size, pixels)))

    header = struct.pack("<HHH", 0, 1, len(images))
    offset = len(header) + 16 * len(images)
    directory = bytearray()
    body = bytearray()
    for size, payload in images:
        directory.extend(struct.pack(
            "<BBBBHHII",
            size if size < 256 else 0,   # 0 means 256 in the ICO directory
            size if size < 256 else 0,
            0, 0, 1, 32, len(payload), offset,
        ))
        body.extend(payload)
        offset += len(payload)
    return header + bytes(directory) + bytes(body)


ICON_PATH: Final = Path(__file__).with_name("billytalk.ico")


def main() -> None:
    data = build_ico()
    ICON_PATH.write_bytes(data)
    print(f"{ICON_PATH} — {len(data)} bytes, sizes {', '.join(map(str, SIZES))}")


if __name__ == "__main__":
    main()
