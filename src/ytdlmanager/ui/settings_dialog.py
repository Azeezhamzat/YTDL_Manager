from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from ..config import MAX_CONCURRENCY
from ..models import AppSettings, DuplicatePolicy
from ..ytdlp_backend import COOKIE_BROWSER_CHOICES
from .widgets import populate_quality_combo, quality_choice

# Plain-string userData rather than None for "off": PySide6's QVariant
# round-trip of None out of a QComboBox isn't reliable.
_COOKIES_OFF = "off"

# (label, bytes per second); 0 = no limit.
_SPEED_LIMITS = (
    ("Unlimited", 0),
    ("500 KB/s", 500 * 1024),
    ("1 MB/s", 1024**2),
    ("2 MB/s", 2 * 1024**2),
    ("5 MB/s", 5 * 1024**2),
    ("10 MB/s", 10 * 1024**2),
    ("25 MB/s", 25 * 1024**2),
)


def _section(title: str) -> tuple[QGroupBox, QFormLayout]:
    box = QGroupBox(title)
    form = QFormLayout(box)
    form.setHorizontalSpacing(14)
    form.setVerticalSpacing(10)
    form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
    return box, form


class SettingsDialog(QDialog):
    def __init__(self, settings: AppSettings, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(600)
        self._settings = settings

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 10, 18, 16)
        layout.setSpacing(6)

        # -- Downloads ---------------------------------------------------
        downloads, form = _section("Downloads")
        dir_row = QHBoxLayout()
        self.dir_edit = QLineEdit(settings.default_download_dir)
        self.dir_edit.setAccessibleName("Default download folder")
        self.dir_edit.setMinimumWidth(300)
        self.dir_edit.setToolTip(settings.default_download_dir)
        browse_btn = QPushButton("Browse…")
        browse_btn.clicked.connect(self._browse)
        dir_row.addWidget(self.dir_edit, stretch=1)
        dir_row.addWidget(browse_btn)
        form.addRow("Default folder:", dir_row)

        self.concurrency_spin = QSpinBox()
        self.concurrency_spin.setRange(1, MAX_CONCURRENCY)
        self.concurrency_spin.setValue(settings.concurrency)
        self.concurrency_spin.setAccessibleName("Concurrent downloads")
        self.concurrency_spin.setToolTip("1-2 is gentler on YouTube for big playlists.")
        self.concurrency_spin.setMaximumWidth(110)
        form.addRow("Parallel downloads:", self.concurrency_spin)

        self.retry_spin = QSpinBox()
        self.retry_spin.setRange(1, 10)
        self.retry_spin.setValue(settings.retry_attempts)
        self.retry_spin.setAccessibleName("Retry attempts for temporary failures")
        self.retry_spin.setMaximumWidth(110)
        form.addRow("Retry attempts:", self.retry_spin)

        self.duplicate_combo = QComboBox()
        self.duplicate_combo.addItem("Rename automatically", DuplicatePolicy.RENAME)
        self.duplicate_combo.addItem("Overwrite existing file", DuplicatePolicy.OVERWRITE)
        self.duplicate_combo.addItem("Skip download", DuplicatePolicy.SKIP)
        idx = self.duplicate_combo.findData(settings.duplicate_policy)
        self.duplicate_combo.setCurrentIndex(max(idx, 0))
        self.duplicate_combo.setAccessibleName("Duplicate file behaviour")
        form.addRow("If a file already exists:", self.duplicate_combo)

        self.quality_combo = QComboBox()
        populate_quality_combo(self.quality_combo, settings.default_max_height)
        self.quality_combo.setAccessibleName("Default maximum video quality")
        form.addRow("Default max quality:", self.quality_combo)

        self.speed_combo = QComboBox()
        for label, limit in _SPEED_LIMITS:
            self.speed_combo.addItem(label, limit)
        idx = self.speed_combo.findData(settings.rate_limit_bps or 0)
        if idx == -1:  # a custom value stored by a future version
            self.speed_combo.addItem(f"{settings.rate_limit_bps} bytes/s", settings.rate_limit_bps)
            idx = self.speed_combo.count() - 1
        self.speed_combo.setCurrentIndex(idx)
        self.speed_combo.setAccessibleName("Download speed limit")
        self.speed_combo.setToolTip(
            "Caps each download's speed so big batches don't use all your\n"
            "bandwidth. Applies to downloads that start after you change it."
        )
        form.addRow("Limit download speed:", self.speed_combo)

        self.preserve_order_check = QCheckBox("Number playlist files in playlist order by default")
        self.preserve_order_check.setChecked(settings.preserve_playlist_order)
        form.addRow("", self.preserve_order_check)

        self.embed_metadata_check = QCheckBox(
            "Add title and artist tags (and cover art to audio) to downloaded files"
        )
        self.embed_metadata_check.setChecked(settings.embed_metadata)
        self.embed_metadata_check.setToolTip(
            "So music players and file browsers show the title, artist and\n"
            "artwork. Cover art is added to MP3, M4A, Opus, Vorbis and FLAC."
        )
        form.addRow("", self.embed_metadata_check)
        layout.addWidget(downloads)

        # -- YouTube -----------------------------------------------------
        youtube, form = _section("YouTube")
        self.cookies_combo = QComboBox()
        self.cookies_combo.addItem("Off (no cookies)", _COOKIES_OFF)
        for browser in COOKIE_BROWSER_CHOICES:
            self.cookies_combo.addItem(browser.capitalize(), browser)
        idx = self.cookies_combo.findData(settings.cookies_browser or _COOKIES_OFF)
        self.cookies_combo.setCurrentIndex(max(idx, 0))
        self.cookies_combo.setAccessibleName("YouTube sign-in from browser")
        form.addRow("YouTube sign-in from:", self.cookies_combo)
        cookies_hint = QLabel(
            "Uses your signed-in YouTube session from this browser, so YouTube doesn't "
            "block downloads with \"Sign in to confirm you're not a bot\". Only sent to "
            "YouTube, never to other sites. macOS asks once for Keychain access (choose "
            "Always Allow); Safari also needs Full Disk Access. YouTube can rate-limit an "
            "account used this way, so a secondary Google account is safer."
        )
        cookies_hint.setObjectName("sectionHint")
        cookies_hint.setWordWrap(True)
        form.addRow("", cookies_hint)
        layout.addWidget(youtube)

        # -- Appearance & updates ------------------------------------------
        general, form = _section("Appearance && updates")
        self.theme_combo = QComboBox()
        self.theme_combo.addItem("Match system", "system")
        self.theme_combo.addItem("Light", "light")
        self.theme_combo.addItem("Dark", "dark")
        idx = self.theme_combo.findData(settings.theme)
        self.theme_combo.setCurrentIndex(max(idx, 0))
        self.theme_combo.setAccessibleName("Theme")
        form.addRow("Theme:", self.theme_combo)

        self.check_updates_check = QCheckBox("Check for yt-dlp updates on startup")
        self.check_updates_check.setChecked(settings.check_for_updates)
        self.check_updates_check.setToolTip(
            "Looks up the latest yt-dlp version on PyPI on startup (a public,\n"
            "anonymous version check -- no personal data is sent). yt-dlp needs\n"
            "to stay current since platforms change often enough to break it."
        )
        form.addRow("", self.check_updates_check)
        layout.addWidget(general)

        button_flags = QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        buttons = QDialogButtonBox(button_flags)
        save_button = buttons.button(QDialogButtonBox.StandardButton.Save)
        save_button.setProperty("accent", True)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addSpacing(8)
        layout.addWidget(buttons)

    def _browse(self) -> None:
        chosen = QFileDialog.getExistingDirectory(
            self, "Choose default download folder", self.dir_edit.text()
        )
        if chosen:
            self.dir_edit.setText(chosen)

    def result_settings(self) -> AppSettings:
        chosen_dir = self.dir_edit.text().strip()
        return AppSettings(
            default_download_dir=chosen_dir or self._settings.default_download_dir,
            concurrency=self.concurrency_spin.value(),
            duplicate_policy=DuplicatePolicy(self.duplicate_combo.currentData()),
            theme=self.theme_combo.currentData(),
            preserve_playlist_order=self.preserve_order_check.isChecked(),
            retry_attempts=self.retry_spin.value(),
            check_for_updates=self.check_updates_check.isChecked(),
            cookies_browser=_cookies_choice(self.cookies_combo.currentData()),
            default_max_height=quality_choice(self.quality_combo),
            embed_metadata=self.embed_metadata_check.isChecked(),
            rate_limit_bps=int(self.speed_combo.currentData() or 0) or None,
        )


def _cookies_choice(data: object) -> str | None:
    return None if data in (None, _COOKIES_OFF) else str(data)
