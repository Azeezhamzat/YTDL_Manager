"""Render the app icon into the files each platform's packaging needs.

Source of truth: ytdlmanager.ui.icons.APP_ICON_SVG (the running app draws
its window icon from the same SVG). Outputs, committed to the repository so
CI on Windows/Linux doesn't need macOS tools:

    packaging/icons/YTDLManager.icns   macOS app bundle (needs `iconutil`)
    packaging/icons/YTDLManager.ico    Windows executable and installer
    packaging/icons/ytdlmanager.png    Linux (.desktop entries, AppImage)

Re-run after changing the icon:  python packaging/make_icons.py
"""

from __future__ import annotations

import os
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QBuffer, QIODevice  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from ytdlmanager.ui.icons import render_app_icon  # noqa: E402

ICONS = Path(__file__).resolve().parent / "icons"


def png_bytes(size: int) -> bytes:
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    render_app_icon(size).save(buffer, "PNG")
    return bytes(buffer.data().data())


def write_ico(path: Path, sizes: tuple[int, ...] = (16, 24, 32, 48, 64, 128, 256)) -> None:
    """A multi-size .ico with PNG-compressed images (Windows Vista and later)."""
    images = [png_bytes(size) for size in sizes]
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = len(header) + 16 * len(images)
    entries = b""
    for size, data in zip(sizes, images, strict=True):
        dim = 0 if size >= 256 else size  # 0 means 256 in the ICO format
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    path.write_bytes(header + entries + b"".join(images))


def write_icns(path: Path) -> bool:
    if not shutil.which("iconutil"):
        print("iconutil not found (macOS only) -- skipping .icns", file=sys.stderr)
        return False
    with tempfile.TemporaryDirectory() as tmp:
        iconset = Path(tmp) / "YTDLManager.iconset"
        iconset.mkdir()
        for base in (16, 32, 128, 256, 512):
            (iconset / f"icon_{base}x{base}.png").write_bytes(png_bytes(base))
            (iconset / f"icon_{base}x{base}@2x.png").write_bytes(png_bytes(base * 2))
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(path)], check=True)
    return True


def main() -> None:
    _app = QApplication.instance() or QApplication([])  # QPixmap needs one
    ICONS.mkdir(exist_ok=True)
    (ICONS / "ytdlmanager.png").write_bytes(png_bytes(512))
    write_ico(ICONS / "YTDLManager.ico")
    write_icns(ICONS / "YTDLManager.icns")
    for item in sorted(ICONS.iterdir()):
        print(f"{item.name:22} {item.stat().st_size:>8} bytes")


if __name__ == "__main__":
    main()
