#!/usr/bin/env python3

from __future__ import annotations

import shutil
import struct
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "packaging" / "assets"
ICO = ASSETS / "reelpush-studio.ico"
BASE_PNG = ASSETS / "reelpush-studio-base.png"
ICONSET = ASSETS / "reelpush-studio.iconset"
ICNS = ASSETS / "reelpush-studio.icns"


def _extract_largest_png_from_ico() -> None:
    data = ICO.read_bytes()
    if len(data) < 6:
        raise RuntimeError(f"{ICO} is not a valid ICO file.")
    _reserved, icon_type, count = struct.unpack_from("<HHH", data, 0)
    if icon_type != 1 or count < 1:
        raise RuntimeError(f"{ICO} is not a Windows icon file.")

    best: tuple[int, bytes] | None = None
    for index in range(count):
        offset = 6 + index * 16
        width_byte, height_byte, _colors, _reserved, _planes, _bits, size, image_offset = struct.unpack_from(
            "<BBBBHHII",
            data,
            offset,
        )
        width = 256 if width_byte == 0 else width_byte
        height = 256 if height_byte == 0 else height_byte
        image = data[image_offset : image_offset + size]
        if not image.startswith(b"\x89PNG\r\n\x1a\n"):
            continue
        score = width * height
        if best is None or score > best[0]:
            best = (score, image)

    if best is None:
        raise RuntimeError(f"{ICO} does not contain PNG icon data.")
    BASE_PNG.write_bytes(best[1])


def _make_resized_icon(source: Path, target: Path, size: int) -> None:
    subprocess.run(
        ["sips", "-z", str(size), str(size), str(source), "--out", str(target)],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def main() -> int:
    if not ICO.exists():
        raise RuntimeError(f"Missing source icon: {ICO}")
    if shutil.which("sips") is None or shutil.which("iconutil") is None:
        raise RuntimeError("macOS sips and iconutil are required to build the app icon.")

    ASSETS.mkdir(parents=True, exist_ok=True)
    _extract_largest_png_from_ico()

    if ICONSET.exists():
        shutil.rmtree(ICONSET)
    ICONSET.mkdir(parents=True)

    targets = {
        "icon_16x16.png": 16,
        "icon_16x16@2x.png": 32,
        "icon_32x32.png": 32,
        "icon_32x32@2x.png": 64,
        "icon_128x128.png": 128,
        "icon_128x128@2x.png": 256,
        "icon_256x256.png": 256,
        "icon_256x256@2x.png": 512,
        "icon_512x512.png": 512,
        "icon_512x512@2x.png": 1024,
    }
    for filename, size in targets.items():
        _make_resized_icon(BASE_PNG, ICONSET / filename, size)

    subprocess.run(["iconutil", "-c", "icns", str(ICONSET), "-o", str(ICNS)], check=True)
    print(f"Created {ICNS}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"Icon build failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
