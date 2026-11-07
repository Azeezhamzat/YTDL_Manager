"""About window: app and component versions, plus where data and logs live.

Also the first place to look when something works on one machine and not
another, so "Copy details" puts the same facts on the clipboard for a bug
report.
"""

from __future__ import annotations

import platform
import sys
from pathlib import Path

from PySide6 import __version__ as pyside_version
from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from .. import __version__
from ..config import get_app_data_dir, get_log_path
from ..ffmpeg_utils import FfmpegStatus
from .icons import render_app_icon


def _yt_dlp_version() -> str:
    try:
        import yt_dlp

        return str(yt_dlp.version.__version__)
    except Exception:  # noqa: BLE001 - informational only
        return "unknown"


class AboutDialog(QDialog):
    def __init__(
        self, ffmpeg_status: FfmpegStatus, js_runtime_path: str | None, parent=None
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("About YTDL Manager")
        self.setMinimumWidth(520)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 16)
        layout.setSpacing(14)

        header = QHBoxLayout()
        header.setSpacing(16)
        logo = QLabel()
        icon = render_app_icon(144)
        icon.setDevicePixelRatio(2.0)
        logo.setPixmap(icon)
        title_box = QVBoxLayout()
        name = QLabel("YTDL Manager")
        name.setObjectName("pageTitle")
        version = QLabel(f"Version {__version__}")
        version.setObjectName("muted")
        tagline = QLabel("Video & audio downloader built on yt-dlp and FFmpeg.")
        tagline.setObjectName("muted")
        tagline.setWordWrap(True)
        for widget in (name, version, tagline):
            title_box.addWidget(widget)
        title_box.addStretch(1)
        header.addWidget(logo, 0, Qt.AlignmentFlag.AlignTop)
        header.addLayout(title_box, stretch=1)
        layout.addLayout(header)

        ffmpeg = (
            (ffmpeg_status.version or "found").replace("ffmpeg version ", "").split(" ")[0]
            if ffmpeg_status.found
            else "not found — see the FFmpeg item in the sidebar"
        )
        self._facts = [
            ("yt-dlp", _yt_dlp_version()),
            ("FFmpeg", ffmpeg),
            ("JavaScript runtime", "Deno (bundled)" if js_runtime_path and "deno_bin" in
             js_runtime_path else js_runtime_path or "not found — YouTube may fail"),
            ("Python", platform.python_version()),
            ("Qt (PySide6)", pyside_version),
            ("System", platform.platform(terse=True)),
            ("Data folder", str(get_app_data_dir())),
        ]
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(6)
        for row, (label, value) in enumerate(self._facts):
            key = QLabel(label)
            key.setObjectName("muted")
            text = QLabel(value)
            text.setWordWrap(True)
            text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            grid.addWidget(key, row, 0, Qt.AlignmentFlag.AlignTop)
            grid.addWidget(text, row, 1)
        grid.setColumnStretch(1, 1)
        layout.addLayout(grid)

        actions = QHBoxLayout()
        open_data = QPushButton("Open data folder")
        open_data.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(get_app_data_dir())))
        )
        open_log = QPushButton("Open log file")
        open_log.clicked.connect(self._open_log)
        copy = QPushButton("Copy details")
        copy.setToolTip("Copy these versions to paste into a bug report")
        copy.clicked.connect(self._copy_details)
        for button in (open_data, open_log, copy):
            actions.addWidget(button)
        actions.addStretch(1)
        layout.addLayout(actions)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def details_text(self) -> str:
        lines = [f"YTDL Manager {__version__}"]
        lines += [f"{label}: {value}" for label, value in self._facts]
        lines.append(f"Frozen build: {bool(getattr(sys, 'frozen', False))}")
        return "\n".join(lines)

    def _copy_details(self) -> None:
        QGuiApplication.clipboard().setText(self.details_text())

    def _open_log(self) -> None:
        log = Path(get_log_path())
        target = log if log.exists() else log.parent
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))
