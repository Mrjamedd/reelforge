#!/usr/bin/env python3

from __future__ import annotations

import math
import struct
import zlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "assets" / "reelpush-studio.ico"


def _png_chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)


def _png(width: int, height: int, rgba: bytes) -> bytes:
    rows = b"".join(b"\x00" + rgba[y * width * 4 : (y + 1) * width * 4] for y in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + _png_chunk(b"IDAT", zlib.compress(rows, 9))
        + _png_chunk(b"IEND", b"")
    )


def _hex(color: str) -> tuple[int, int, int, int]:
    color = color.lstrip("#")
    return int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16), 255


def _inside_round_rect(x: float, y: float, width: int, height: int, radius: float) -> bool:
    if radius <= x <= width - radius or radius <= y <= height - radius:
        return 0 <= x <= width and 0 <= y <= height
    cx = radius if x < radius else width - radius
    cy = radius if y < radius else height - radius
    return math.hypot(x - cx, y - cy) <= radius


def _inside_triangle(px: float, py: float, points: tuple[tuple[float, float], tuple[float, float], tuple[float, float]]) -> bool:
    (x1, y1), (x2, y2), (x3, y3) = points
    denom = (y2 - y3) * (x1 - x3) + (x3 - x2) * (y1 - y3)
    a = ((y2 - y3) * (px - x3) + (x3 - x2) * (py - y3)) / denom
    b = ((y3 - y1) * (px - x3) + (x1 - x3) * (py - y3)) / denom
    c = 1 - a - b
    return a >= 0 and b >= 0 and c >= 0


def _render(size: int) -> bytes:
    bg = _hex("#101216")
    panel = _hex("#181b22")
    border = _hex("#333b48")
    accent = _hex("#d9584a")
    text = _hex("#f1f3f5")
    good = _hex("#6fcf97")
    transparent = (0, 0, 0, 0)
    pixels: list[int] = []
    scale = size / 256

    def put(color: tuple[int, int, int, int]) -> None:
        pixels.extend(color)

    for y in range(size):
        for x in range(size):
            ux = (x + 0.5) / scale
            uy = (y + 0.5) / scale
            color = transparent
            if _inside_round_rect(ux, uy, 256, 256, 48):
                color = bg
            if _inside_round_rect(ux - 34, uy - 42, 188, 172, 28):
                color = panel
            if _inside_round_rect(ux - 34, uy - 42, 188, 172, 28) and not _inside_round_rect(ux - 42, uy - 50, 172, 156, 20):
                color = border
            if 74 <= ux <= 198 and 74 <= uy <= 174:
                right_circle = math.hypot(ux - 148, uy - 124) <= 50
                body = 74 <= ux <= 148 and 74 <= uy <= 174
                if body or right_circle:
                    color = accent
            if _inside_triangle(ux, uy, ((108, 104), (108, 144), (146, 124))):
                color = text
            if _inside_round_rect(ux - 72, uy - 184, 112, 10, 5):
                color = good
            put(color)
    return _png(size, size, bytes(pixels))


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    sizes = (16, 32, 48, 64, 128, 256)
    images = [_render(size) for size in sizes]
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries = []
    for size, image in zip(sizes, images):
        width_byte = 0 if size == 256 else size
        entries.append(struct.pack("<BBBBHHII", width_byte, width_byte, 0, 0, 1, 32, len(image), offset))
        offset += len(image)
    OUT.write_bytes(header + b"".join(entries) + b"".join(images))


if __name__ == "__main__":
    main()
