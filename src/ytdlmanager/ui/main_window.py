from __future__ import annotations

import datetime
import sys
import time
from collections import Counter
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import (
    QEvent,
    QItemSelectionModel,
    QModelIndex,
    QRunnable,
    QSize,
    QSortFilterProxyModel,
    Qt,
    QThreadPool,
    QUrl,
)
from PySide6.QtGui import (
    QDesktopServices,
    QGuiApplication,
    QPixmap,
    QStandardItem,
    QStandardItemModel,
)
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QStackedWidget,
    QStatusBar,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from .. import update_check
from ..config import LEGAL_NOTICE, MAX_CONCURRENCY, SUPPORTED_PLATFORMS_LABEL
from ..db import Database
from ..download_manager import DownloadManager
from ..ffmpeg_utils import install_instructions
from ..models import (
    AppSettings,
    DownloadMode,
    DownloadRequest,
    DuplicatePolicy,
    MediaKind,
    PlaylistInfo,
    QueueStatus,
    VideoInfo,
)
from ..url_utils import classify_url, detect_platform
from ..ytdlp_backend import (
    AUDIO_CONTAINER_CHOICES,
    COOKIE_LOAD_ERROR_PREFIX,
    VIDEO_CONTAINER_CHOICES,
    is_bot_check_error,
    parse_subtitle_langs,
)
from . import icons
from . import theme as theme_mod
from .about_dialog import AboutDialog
from .metadata_worker import MetadataFetchWorker
from .models_qt import PlaylistTableModel, QueueTableModel, status_label
from .settings_dialog import SettingsDialog
from .update_check_worker import UpdateCheckWorker, UpdateInstallWorker
from .widgets import (
    PATH_ROLE,
    STATUS_ROLE,
    URL_ROLE,
    FlowLayout,
    ProgressDelegate,
    StatusPillDelegate,
    card,
    field_label,
)

# Several downloads running at once all hit YouTube's bot check within
# seconds of each other; explain it once, not once per item.
_BOT_NOTICE_COOLDOWN_SECONDS = 60.0

PAGE_VIDEO, PAGE_PLAYLIST, PAGE_QUEUE, PAGE_HISTORY = range(4)
_NAV_PAGES = (("Video", "video"), ("Playlist", "playlist"), ("Queue", "download"),
              ("History", "history"))

_AUTO_FORMAT_LABEL = "Automatic (best for the chosen mode)"
_FORMAT_LABELS = {
    DownloadMode.VIDEO_WITH_AUDIO.value: "Video + audio",
    DownloadMode.AUDIO_ONLY.value: "Audio only",
    DownloadMode.BEST_QUALITY.value: "Best quality",
    DownloadMode.SPECIFIC_FORMAT.value: "Specific format",
}
_NOTICE_SUMMARY = (
    "Only download content you own, that's in the public domain, or that you have "
    "permission to save."
)


def _button(
    text: str,
    icon_name: str | None = None,
    *,
    accent: bool = False,
    danger: bool = False,
    big: bool = False,
    tooltip: str | None = None,
) -> QPushButton:
    button = QPushButton(text)
    if accent:
        button.setProperty("accent", True)
    if danger:
        button.setProperty("danger", True)
    if big:
        button.setProperty("big", True)
    if icon_name:
        role = "on_accent" if accent else "danger" if danger else "text"
        icons.set_icon(button, icon_name, role)
    if tooltip:
        button.setToolTip(tooltip)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    return button


def _scrollable(page: QWidget) -> QScrollArea:
    """Wrap a form page so a short window scrolls it instead of squashing or
    clipping its controls."""
    scroll = QScrollArea()
    scroll.setWidget(page)
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    scroll.setStyleSheet(
        "QScrollArea { background: transparent; } "
        "QScrollArea > QWidget > QWidget { background: transparent; }"
    )
    return scroll


class MainWindow(QMainWindow):
    def __init__(self, db: Database, settings: AppSettings) -> None:
        super().__init__()
        self.db = db
        self.settings = settings
        self.setWindowTitle("YTDL Manager")
        self.setWindowIcon(icons.app_icon())
        self.setMinimumSize(900, 600)
        self.resize(1180, 760)
        # Dropping a link (from a browser's address bar, a page, or a text
        # file) anywhere on the window fetches it.
        self.setAcceptDrops(True)

        self.download_manager = DownloadManager(
            db,
            concurrency=settings.concurrency,
            retry_attempts=settings.retry_attempts,
            cookies_browser=settings.cookies_browser,
        )
        self.download_manager.load_persisted_queue()
        self.download_manager.item_progress.connect(self._on_item_progress)
        self.download_manager.item_status_changed.connect(self._on_item_status_changed)
        self.download_manager.item_finished.connect(self._on_item_finished)
        self.download_manager.item_failed.connect(self._on_item_failed)
        self.download_manager.item_skipped.connect(self._on_item_skipped)
        self.download_manager.queue_changed.connect(self._refresh_queue_table)
        self.download_manager.youtube_blocked.connect(self._on_youtube_blocked)
        self.download_manager.queue_finished.connect(self._on_queue_finished)
        self._last_bot_notice = float("-inf")

        self._metadata_pool = QThreadPool()
        self._metadata_pool.setMaxThreadCount(2)
        self._network = QNetworkAccessManager(self)
        self._thumbnail_reply: QNetworkReply | None = None

        # Workers dispatched on `_metadata_pool` each own a private signals
        # QObject (unlike DownloadWorker, which shares one long-lived
        # WorkerSignals owned by DownloadManager). A `worker` local that
        # goes out of scope right after `pool.start(worker)` can have its
        # Python wrapper -- and with it this per-instance signals object --
        # garbage collected before the queued cross-thread signal is
        # delivered, silently dropping it (or worse, a use-after-free).
        # Keeping every in-flight worker referenced here for the session's
        # lifetime sidesteps that entirely; the memory cost is negligible
        # for the handful of fetch/update-check workers a session runs.
        self._active_workers: list[QRunnable] = []

        self._current_video: VideoInfo | None = None
        self._current_playlist: PlaylistInfo | None = None
        # Set when Download is pressed before a video was fetched: fetch
        # first, then queue it as soon as the metadata arrives.
        self._download_after_fetch = False

        self._build_ui()
        self.apply_theme(settings.theme)
        QGuiApplication.styleHints().colorSchemeChanged.connect(self._on_system_scheme_changed)
        self._refresh_queue_table()
        self._refresh_history_table()
        self._refresh_side_status()
        self._check_ffmpeg()

        if self.download_manager.items():
            self.status_bar.showMessage(
                "Restored queue from a previous session. Downloads were NOT resumed "
                "automatically -- use Start all when you're ready.",
                8000,
            )

        if self.settings.check_for_updates:
            self._check_for_updates(manual=False)

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        outer = QHBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._build_sidebar())

        content = QWidget()
        content.setObjectName("content")
        column = QVBoxLayout(content)
        column.setContentsMargins(20, 18, 20, 12)
        column.setSpacing(12)
        column.addWidget(self._build_url_card())
        column.addWidget(self._build_notice())

        self.tabs = QStackedWidget()
        self.tabs.addWidget(_scrollable(self._build_video_tab()))
        self.tabs.addWidget(self._build_playlist_tab())
        self.tabs.addWidget(self._build_queue_tab())
        self.tabs.addWidget(self._build_history_tab())
        column.addWidget(self.tabs, stretch=1)
        outer.addWidget(content, stretch=1)

        self.nav_list.currentRowChanged.connect(self.tabs.setCurrentIndex)
        self.tabs.currentChanged.connect(self.nav_list.setCurrentRow)

        # Drop-downs otherwise insist on the width of their longest entry
        # ("Subtitles only (no video/audio)"), which pushed the playlist's
        # option panel past its own edge and forced the window wider.
        for combo in content.findChildren(QComboBox):
            combo.setSizeAdjustPolicy(
                QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
            )
            combo.setMinimumContentsLength(8)
        for table in content.findChildren(QTableView):
            table.horizontalHeader().setDefaultAlignment(
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
            )

        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)

    def _build_sidebar(self) -> QWidget:
        side = QFrame()
        side.setObjectName("sidebar")
        side.setFixedWidth(206)
        layout = QVBoxLayout(side)
        layout.setContentsMargins(12, 20, 12, 14)
        layout.setSpacing(4)

        title = QLabel("YTDL Manager")
        title.setObjectName("appTitle")
        subtitle = QLabel("Video & audio downloader")
        subtitle.setObjectName("appSubtitle")
        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addSpacing(16)

        self.nav_list = QListWidget()
        self.nav_list.setObjectName("navList")
        self.nav_list.setAccessibleName("Sections")
        self.nav_list.setIconSize(QSize(18, 18))
        self.nav_list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.nav_list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        for name, icon_name in _NAV_PAGES:
            item = QListWidgetItem(name)
            item.setData(Qt.ItemDataRole.UserRole, icon_name)
            self.nav_list.addItem(item)
        self.nav_list.setCurrentRow(PAGE_VIDEO)
        self.nav_list.setFixedHeight(len(_NAV_PAGES) * 42 + 4)
        layout.addWidget(self.nav_list)
        layout.addStretch(1)

        self.ffmpeg_status_btn = QPushButton("Checking FFmpeg…")
        self.ffmpeg_status_btn.setObjectName("sideButton")
        self.ffmpeg_status_btn.clicked.connect(self._show_ffmpeg_help)
        self.signin_btn = QPushButton()
        self.signin_btn.setObjectName("sideButton")
        self.signin_btn.setToolTip(
            "Use your browser's YouTube sign-in when YouTube blocks downloads\n"
            "with \"Sign in to confirm you're not a bot\". Click to change."
        )
        icons.set_icon(self.signin_btn, "user", "muted")
        self.signin_btn.clicked.connect(self._open_settings)
        self.check_updates_btn = QPushButton("Check for updates")
        self.check_updates_btn.setObjectName("sideButton")
        icons.set_icon(self.check_updates_btn, "refresh")
        self.check_updates_btn.clicked.connect(lambda: self._check_for_updates(manual=True))
        settings_btn = QPushButton("Settings")
        settings_btn.setObjectName("sideButton")
        icons.set_icon(settings_btn, "settings")
        settings_btn.clicked.connect(self._open_settings)
        about_btn = QPushButton("About")
        about_btn.setObjectName("sideButton")
        icons.set_icon(about_btn, "info")
        about_btn.clicked.connect(self._open_about)
        for button in (self.ffmpeg_status_btn, self.signin_btn, self.check_updates_btn,
                       settings_btn, about_btn):
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            layout.addWidget(button)
        return side

    def _build_url_card(self) -> QWidget:
        box = card()
        row = QHBoxLayout(box)
        row.setContentsMargins(12, 10, 12, 10)
        row.setSpacing(8)
        link_icon = QLabel()
        icons.set_icon(link_icon, "link", "muted")
        row.addWidget(link_icon)

        self.url_edit = QLineEdit()
        self.url_edit.setObjectName("urlEdit")
        self.url_edit.setPlaceholderText(
            f"Paste a video or playlist link ({SUPPORTED_PLATFORMS_LABEL})…"
        )
        self.url_edit.setAccessibleName("Video or playlist URL")
        self.url_edit.setClearButtonEnabled(True)
        self.url_edit.returnPressed.connect(self._on_fetch_clicked)
        self.url_edit.textChanged.connect(self._on_url_text_changed)
        row.addWidget(self.url_edit, stretch=1)

        self.url_kind_label = QLabel("")
        self.url_kind_label.setObjectName("chip")
        self.url_kind_label.setAccessibleName("Detected platform and content type")
        self.url_kind_label.hide()
        row.addWidget(self.url_kind_label)

        paste_btn = _button("Paste", "paste", tooltip="Paste a link from the clipboard")
        paste_btn.clicked.connect(self._paste_url)
        row.addWidget(paste_btn)
        self.fetch_btn = _button("Fetch", "search", accent=True,
                                 tooltip="Look up the video or playlist (Enter)")
        self.fetch_btn.clicked.connect(self._on_fetch_clicked)
        row.addWidget(self.fetch_btn)
        return box

    def _build_notice(self) -> QWidget:
        box = card("legalNotice")
        box.setAccessibleName("Legal notice")
        layout = QVBoxLayout(box)
        layout.setContentsMargins(12, 7, 10, 7)
        layout.setSpacing(4)
        row = QHBoxLayout()
        row.setSpacing(8)
        info_icon = QLabel()
        icons.set_icon(info_icon, "info", "notice", 16)
        summary = QLabel(_NOTICE_SUMMARY)
        summary.setWordWrap(True)
        self.notice_toggle = QPushButton("Details")
        self.notice_toggle.setProperty("flat", True)
        self.notice_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self.notice_toggle.clicked.connect(self._toggle_notice)
        row.addWidget(info_icon)
        row.addWidget(summary, stretch=1)
        row.addWidget(self.notice_toggle)
        layout.addLayout(row)
        self.notice_details = QLabel(LEGAL_NOTICE)
        self.notice_details.setWordWrap(True)
        self.notice_details.hide()
        layout.addWidget(self.notice_details)
        return box

    def _build_video_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        info = card()
        info_row = QHBoxLayout(info)
        info_row.setContentsMargins(16, 16, 16, 16)
        info_row.setSpacing(18)
        self.thumbnail_label = QLabel("No video loaded")
        self.thumbnail_label.setObjectName("thumbnailFrame")
        self.thumbnail_label.setFixedSize(224, 126)
        self.thumbnail_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.thumbnail_label.setAccessibleName("Video thumbnail")
        info_row.addWidget(self.thumbnail_label)
        details = QVBoxLayout()
        details.setSpacing(6)
        self.video_title_label = QLabel("No video loaded yet")
        self.video_title_label.setObjectName("pageTitle")
        self.video_title_label.setWordWrap(True)
        self.video_channel_label = QLabel(
            "Paste a link above and press Fetch to see its details, or just press Download."
        )
        self.video_channel_label.setObjectName("muted")
        self.video_channel_label.setWordWrap(True)
        self.video_duration_label = QLabel("")
        self.video_duration_label.setObjectName("muted")
        for label in (self.video_title_label, self.video_channel_label, self.video_duration_label):
            details.addWidget(label)
        details.addStretch(1)
        info_row.addLayout(details, stretch=1)
        layout.addWidget(info)

        options = card()
        grid = QGridLayout(options)
        grid.setContentsMargins(16, 14, 16, 16)
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(4)
        heading = QLabel("Download options")
        heading.setObjectName("cardTitle")
        grid.addWidget(heading, 0, 0, 1, 2)

        self.video_mode_combo = QComboBox()
        self.video_mode_combo.addItem("Video with audio", DownloadMode.VIDEO_WITH_AUDIO)
        self.video_mode_combo.addItem("Audio only", DownloadMode.AUDIO_ONLY)
        self.video_mode_combo.addItem("Best available quality", DownloadMode.BEST_QUALITY)
        self.video_mode_combo.addItem("Specific format", DownloadMode.SPECIFIC_FORMAT)
        self.video_mode_combo.setAccessibleName("Download mode")
        self.video_mode_combo.currentIndexChanged.connect(self._on_video_mode_changed)
        self.video_mode_combo.activated.connect(self._on_video_mode_picked)
        self.video_format_combo = QComboBox()
        self.video_format_combo.setAccessibleName("Specific format")
        self.video_format_combo.addItem(_AUTO_FORMAT_LABEL, None)
        self.video_format_combo.setToolTip(
            "Leave on Automatic, or pick one of this video's exact formats\n"
            "(the list fills in once a video is fetched)."
        )
        self.video_format_combo.activated.connect(self._on_video_format_picked)
        grid.addWidget(field_label("Mode"), 1, 0)
        grid.addWidget(field_label("Format"), 1, 1)
        grid.addWidget(self.video_mode_combo, 2, 0)
        grid.addWidget(self.video_format_combo, 2, 1)

        subtitle_row, self.video_subtitle_combo, self.video_subtitle_langs_edit = (
            self._build_subtitle_row(self._on_video_mode_changed)
        )
        grid.addWidget(field_label("Subtitles"), 3, 0, 1, 2)
        grid.addLayout(subtitle_row, 4, 0, 1, 2)

        dest_row, self.video_dest_edit = self._build_destination_row(
            self.settings.default_download_dir, "Destination folder"
        )
        grid.addWidget(field_label("Save to"), 5, 0, 1, 2)
        grid.addLayout(dest_row, 6, 0, 1, 2)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)

        self.video_add_btn = _button("Download", "download", accent=True, big=True)
        self.video_add_btn.setToolTip("Add this video to the queue and start it")
        self.video_add_btn.clicked.connect(self._on_video_download_clicked)
        button_row = QHBoxLayout()
        button_row.setContentsMargins(0, 12, 0, 0)
        button_row.addStretch(1)
        button_row.addWidget(self.video_add_btn)
        grid.addLayout(button_row, 7, 0, 1, 2)
        layout.addWidget(options)
        layout.addStretch(1)
        return page

    def _build_playlist_tab(self) -> QWidget:
        page = QWidget()
        row = QHBoxLayout(page)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(12)

        left = card()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(14, 14, 14, 14)
        left_layout.setSpacing(10)
        head = QHBoxLayout()
        self.playlist_title_label = QLabel("No playlist loaded yet")
        self.playlist_title_label.setObjectName("pageTitle")
        self.playlist_title_label.setWordWrap(True)
        self.playlist_selected_label = QLabel("0 selected")
        self.playlist_selected_label.setObjectName("chip")
        head.addWidget(self.playlist_title_label, stretch=1)
        head.addWidget(self.playlist_selected_label, 0, Qt.AlignmentFlag.AlignTop)
        left_layout.addLayout(head)

        toolbar = QWidget()
        flow = FlowLayout(toolbar)
        self.playlist_search_edit = QLineEdit()
        self.playlist_search_edit.setPlaceholderText("Filter by title…")
        self.playlist_search_edit.setAccessibleName("Filter playlist by title")
        self.playlist_search_edit.setClearButtonEnabled(True)
        self.playlist_search_edit.setMinimumWidth(220)
        self.playlist_search_edit.textChanged.connect(self._on_playlist_filter_changed)
        select_all_btn = _button("Select all", "check_all")
        deselect_btn = _button("Select none", "check_none")
        invert_btn = _button("Invert", "invert")
        select_all_btn.clicked.connect(self._playlist_select_all)
        deselect_btn.clicked.connect(self._playlist_deselect_all)
        invert_btn.clicked.connect(self._playlist_invert_selection)
        for widget in (self.playlist_search_edit, select_all_btn, deselect_btn, invert_btn):
            flow.addWidget(widget)
        left_layout.addWidget(toolbar)

        self.playlist_empty_label = QLabel(
            "Paste a playlist, channel or profile link above and press Fetch,\n"
            "then tick the videos you want."
        )
        self.playlist_empty_label.setObjectName("emptyState")
        self.playlist_empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        left_layout.addWidget(self.playlist_empty_label)

        self.playlist_model = PlaylistTableModel()
        self.playlist_table = QTableView()
        self.playlist_table.setModel(self.playlist_model)
        self.playlist_table.setAccessibleName("Playlist items")
        header = self.playlist_table.horizontalHeader()
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        header.resizeSection(0, 38)
        header.resizeSection(1, 48)
        header.resizeSection(3, 150)
        header.resizeSection(4, 80)
        self.playlist_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.playlist_table.setAlternatingRowColors(True)
        self.playlist_table.setShowGrid(False)
        self.playlist_table.verticalHeader().setVisible(False)
        self.playlist_table.verticalHeader().setDefaultSectionSize(34)
        # QTableView's built-in checkbox-toggle-on-click is unreliable together
        # with SelectRows (the click is often consumed by row selection before
        # it reaches the checkbox delegate) -- toggle explicitly instead.
        self.playlist_table.clicked.connect(self._on_playlist_row_clicked)
        left_layout.addWidget(self.playlist_table, stretch=1)
        row.addWidget(left, stretch=1)

        options = QWidget()
        options.setMinimumWidth(280)
        options.setMaximumWidth(340)
        options_outer = QVBoxLayout(options)
        options_outer.setContentsMargins(0, 0, 0, 0)
        panel = card()
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(16, 14, 16, 16)
        panel_layout.setSpacing(4)
        heading = QLabel("Download options")
        heading.setObjectName("cardTitle")
        panel_layout.addWidget(heading)

        self.playlist_mode_combo = QComboBox()
        self.playlist_mode_combo.addItem("Video with audio", DownloadMode.VIDEO_WITH_AUDIO)
        self.playlist_mode_combo.addItem("Audio only", DownloadMode.AUDIO_ONLY)
        self.playlist_mode_combo.addItem("Best available quality", DownloadMode.BEST_QUALITY)
        self.playlist_mode_combo.setAccessibleName("Batch download mode")
        self.playlist_mode_combo.currentIndexChanged.connect(self._populate_playlist_format_options)
        self.playlist_mode_combo.activated.connect(self._on_playlist_mode_picked)
        panel_layout.addWidget(field_label("Mode"))
        panel_layout.addWidget(self.playlist_mode_combo)

        self.playlist_format_combo = QComboBox()
        self.playlist_format_combo.setAccessibleName("Preferred format for the whole batch")
        self.playlist_format_combo.activated.connect(self._on_playlist_mode_picked)
        panel_layout.addWidget(field_label("Preferred format"))
        panel_layout.addWidget(self.playlist_format_combo)
        self._populate_playlist_format_options()

        subtitle_row, self.playlist_subtitle_combo, self.playlist_subtitle_langs_edit = (
            self._build_subtitle_row(self._on_playlist_subtitles_changed)
        )
        panel_layout.addWidget(field_label("Subtitles"))
        panel_layout.addLayout(subtitle_row)

        dest_row, self.playlist_dest_edit = self._build_destination_row(
            self.settings.default_download_dir, "Playlist destination folder", compact=True
        )
        panel_layout.addWidget(field_label("Save to"))
        panel_layout.addLayout(dest_row)

        self.playlist_duplicate_combo = QComboBox()
        self.playlist_duplicate_combo.addItem("Rename automatically", DuplicatePolicy.RENAME)
        self.playlist_duplicate_combo.addItem("Overwrite existing file", DuplicatePolicy.OVERWRITE)
        self.playlist_duplicate_combo.addItem("Skip download", DuplicatePolicy.SKIP)
        idx = self.playlist_duplicate_combo.findData(self.settings.duplicate_policy)
        self.playlist_duplicate_combo.setCurrentIndex(max(idx, 0))
        self.playlist_duplicate_combo.setAccessibleName("Duplicate file behaviour")
        panel_layout.addWidget(field_label("If a file already exists"))
        panel_layout.addWidget(self.playlist_duplicate_combo)

        self.playlist_preserve_order_check = QCheckBox("Number files in playlist order")
        self.playlist_preserve_order_check.setToolTip('Names files like "001 - Title.mp4"')
        self.playlist_preserve_order_check.setChecked(self.settings.preserve_playlist_order)
        panel_layout.addSpacing(6)
        panel_layout.addWidget(self.playlist_preserve_order_check)
        panel_layout.addStretch(1)

        self.playlist_add_btn = _button("Download selected", "download", accent=True, big=True)
        self.playlist_add_btn.setToolTip("Add every ticked video to the queue and start them")
        self.playlist_add_btn.clicked.connect(self._on_playlist_download_clicked)
        panel_layout.addSpacing(10)
        panel_layout.addWidget(self.playlist_add_btn)
        options_outer.addWidget(_scrollable(panel))
        row.addWidget(options)
        return page

    def _build_queue_tab(self) -> QWidget:
        box = card()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        head = QHBoxLayout()
        title = QLabel("Downloads")
        title.setObjectName("pageTitle")
        self.queue_summary_label = QLabel("")
        self.queue_summary_label.setObjectName("muted")
        self.queue_summary_label.setWordWrap(True)
        parallel_label = QLabel("Parallel downloads")
        parallel_label.setObjectName("muted")
        self.concurrency_spin = QSpinBox()
        self.concurrency_spin.setRange(1, MAX_CONCURRENCY)
        self.concurrency_spin.setValue(self.settings.concurrency)
        self.concurrency_spin.setAccessibleName("Parallel downloads")
        self.concurrency_spin.setToolTip(
            "How many downloads run at once. 1-2 is gentler on YouTube for big playlists."
        )
        self.concurrency_spin.valueChanged.connect(self._on_concurrency_changed)
        head.addWidget(title)
        head.addStretch(1)
        head.addWidget(parallel_label)
        head.addWidget(self.concurrency_spin)
        layout.addLayout(head)
        layout.addWidget(self.queue_summary_label)

        toolbar = QWidget()
        flow = FlowLayout(toolbar)
        start_btn = _button("Start all", "play", accent=True,
                            tooltip="Start every queued, paused or failed download")
        pause_btn = _button("Pause", "pause", tooltip=(
            "Stop the selected transfers but keep the partial files, so Resume\n"
            "continues them later instead of starting over."
        ))
        resume_btn = _button("Resume", "play", tooltip="Continue the selected paused downloads")
        cancel_btn = _button("Cancel", "stop", tooltip=(
            "Stop the selected transfers and delete their partial files. Use Pause\n"
            "instead if you want to continue them later."
        ))
        retry_btn = _button("Retry", "retry", tooltip="Try the selected failed downloads again")
        remove_btn = _button("Remove", "trash", danger=True, tooltip=(
            "Remove the selected items from the list (stops them if running)"
        ))
        up_btn = _button("Move up", "up")
        down_btn = _button("Move down", "down")
        self.open_folder_btn = _button("Show in folder", "folder",
                                       tooltip="Open the folder of the selected download")
        clear_completed_btn = _button("Clear finished", "sweep", tooltip=(
            "Remove completed, cancelled and skipped items from the list.\n"
            "Downloaded files stay on disk."
        ))
        start_btn.clicked.connect(self._queue_start_all)
        pause_btn.clicked.connect(self._queue_pause_selected)
        resume_btn.clicked.connect(self._queue_resume_selected)
        cancel_btn.clicked.connect(self._queue_cancel_selected)
        retry_btn.clicked.connect(self._queue_retry_selected)
        remove_btn.clicked.connect(self._queue_remove_selected)
        up_btn.clicked.connect(lambda: self._queue_move_selected(-1))
        down_btn.clicked.connect(lambda: self._queue_move_selected(1))
        self.open_folder_btn.clicked.connect(self._open_folder_for_selected_queue_item)
        clear_completed_btn.clicked.connect(self._queue_clear_completed)
        for button in (start_btn, pause_btn, resume_btn, cancel_btn, retry_btn, remove_btn,
                       up_btn, down_btn, self.open_folder_btn, clear_completed_btn):
            flow.addWidget(button)
        layout.addWidget(toolbar)

        self.queue_empty_label = QLabel(
            "No downloads yet. Paste a link above, then press Download."
        )
        self.queue_empty_label.setObjectName("emptyState")
        self.queue_empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.queue_empty_label)

        self.queue_model = QueueTableModel()
        self.queue_table = QTableView()
        self.queue_table.setModel(self.queue_model)
        self.queue_table.setAccessibleName("Download queue")
        self.queue_table.setItemDelegateForColumn(1, StatusPillDelegate(self.queue_table))
        self.queue_table.setItemDelegateForColumn(2, ProgressDelegate(self.queue_table))
        header = self.queue_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in range(1, 5):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
        header.setMinimumSectionSize(60)
        # Side columns are re-fitted to the table's width on every resize so
        # the title always keeps a usable share (see _fit_queue_columns).
        self._queue_viewport = self.queue_table.viewport()
        self._queue_viewport.installEventFilter(self)
        self.queue_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.queue_table.setAlternatingRowColors(True)
        self.queue_table.setShowGrid(False)
        self.queue_table.setWordWrap(False)
        self.queue_table.verticalHeader().setVisible(False)
        self.queue_table.verticalHeader().setDefaultSectionSize(36)
        self.queue_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.queue_table.customContextMenuRequested.connect(self._on_queue_context_menu)
        self.queue_table.doubleClicked.connect(self._on_queue_double_clicked)
        layout.addWidget(self.queue_table, stretch=1)
        return box

    def _build_history_tab(self) -> QWidget:
        box = card()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        head = QHBoxLayout()
        title = QLabel("History")
        title.setObjectName("pageTitle")
        self.history_count_label = QLabel("")
        self.history_count_label.setObjectName("muted")
        head.addWidget(title)
        head.addSpacing(12)
        head.addWidget(self.history_count_label, stretch=1)
        layout.addLayout(head)

        toolbar = QWidget()
        flow = FlowLayout(toolbar)
        self.history_search_edit = QLineEdit()
        self.history_search_edit.setPlaceholderText("Search history…")
        self.history_search_edit.setAccessibleName("Search history")
        self.history_search_edit.setClearButtonEnabled(True)
        self.history_search_edit.setMinimumWidth(220)
        self.history_open_folder_btn = _button("Show in folder", "folder")
        open_file_btn = _button("Open file", "file", tooltip="Open the selected download")
        copy_link_btn = _button("Copy link", "copy", tooltip="Copy the original link")
        clear_btn = _button("Clear history", "trash", danger=True,
                            tooltip="Empty this list. Downloaded files are not deleted.")
        self.history_open_folder_btn.clicked.connect(self._open_folder_for_selected_history_item)
        open_file_btn.clicked.connect(self._open_selected_history_file)
        copy_link_btn.clicked.connect(self._copy_selected_history_link)
        clear_btn.clicked.connect(self._clear_history)
        for widget in (self.history_search_edit, self.history_open_folder_btn, open_file_btn,
                       copy_link_btn, clear_btn):
            flow.addWidget(widget)
        layout.addWidget(toolbar)

        self.history_model = QStandardItemModel(0, 5)
        self.history_model.setHorizontalHeaderLabels(
            ["Title", "Saved to", "Format", "Date", "Result"]
        )
        self.history_proxy = QSortFilterProxyModel()
        self.history_proxy.setSourceModel(self.history_model)
        self.history_proxy.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.history_proxy.setFilterKeyColumn(-1)
        self.history_search_edit.textChanged.connect(self.history_proxy.setFilterFixedString)

        self.history_table = QTableView()
        self.history_table.setModel(self.history_proxy)
        self.history_table.setAccessibleName("Download history")
        self.history_table.setItemDelegateForColumn(4, StatusPillDelegate(self.history_table))
        self.history_table.setAlternatingRowColors(True)
        self.history_table.setShowGrid(False)
        self.history_table.setWordWrap(False)
        self.history_table.verticalHeader().setVisible(False)
        self.history_table.verticalHeader().setDefaultSectionSize(34)
        self.history_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.history_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        # Trim long paths in the middle so the file name stays visible.
        self.history_table.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        header = self.history_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for column, width in ((2, 110), (3, 150), (4, 112)):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
            header.resizeSection(column, width)
        self.history_table.doubleClicked.connect(lambda _index: self._open_selected_history_file())
        layout.addWidget(self.history_table, stretch=1)
        return box

    def _build_subtitle_row(self, on_change) -> tuple[QHBoxLayout, QComboBox, QLineEdit]:
        row = QHBoxLayout()
        row.setSpacing(8)
        combo = QComboBox()
        # Plain-string userData on purpose (see the QVariant note in
        # _add_video_to_queue): nothing to downgrade.
        combo.addItem("None", "none")
        combo.addItem("Save alongside the download", "with")
        combo.addItem("Subtitles only (no video/audio)", "only")
        combo.setAccessibleName("Subtitles")
        langs = QLineEdit("en")
        langs.setAccessibleName("Subtitle languages")
        langs.setPlaceholderText("en, fr … or all")
        langs.setFixedWidth(110)
        langs.setToolTip(
            "Language codes separated by commas (e.g. en, fr), or \"all\".\n"
            "Saved as .srt next to the media file. If the video has no human-made\n"
            "track in a language, auto-generated captions are used when available.\n"
            "Typing here turns subtitles on."
        )
        combo.currentIndexChanged.connect(on_change)
        # Typing languages means the user wants subtitles: switch them on
        # rather than silently ignoring the field.
        langs.textEdited.connect(lambda text: self._enable_subtitles_for(combo, text))
        row.addWidget(combo, stretch=1)
        row.addWidget(langs)
        return row, combo, langs

    def _build_destination_row(
        self, initial: str, accessible_name: str, *, compact: bool = False
    ) -> tuple[QHBoxLayout, QLineEdit]:
        row = QHBoxLayout()
        row.setSpacing(6)
        edit = QLineEdit(initial)
        edit.setAccessibleName(accessible_name)
        edit.setToolTip(initial)
        edit.textChanged.connect(edit.setToolTip)
        browse_btn = _button("" if compact else "Browse…", "folder", tooltip="Choose a folder")
        open_btn = _button("" if compact else "Open", "file", tooltip="Open this folder")
        browse_btn.clicked.connect(lambda: self._browse_folder(edit))
        open_btn.clicked.connect(lambda: self._open_folder_path(edit.text().strip()))
        row.addWidget(edit, stretch=1)
        row.addWidget(browse_btn)
        row.addWidget(open_btn)
        return row, edit

    # --------------------------------------------------------- navigation
    def _show_page(self, page: int) -> None:
        self.tabs.setCurrentIndex(page)

    def _toggle_notice(self) -> None:
        showing = self.notice_details.isHidden()
        self.notice_details.setVisible(showing)
        self.notice_toggle.setText("Hide" if showing else "Details")

    def _on_url_text_changed(self, text: str) -> None:
        url = text.strip()
        kind = classify_url(url) if url else MediaKind.INVALID
        if kind == MediaKind.INVALID:
            self.url_kind_label.hide()
            return
        kind_label = "Video" if kind == MediaKind.VIDEO_URL else "Playlist"
        self.url_kind_label.setText(f"{detect_platform(url).display_name} · {kind_label}")
        self.url_kind_label.show()

    def _paste_url(self) -> None:
        text = QGuiApplication.clipboard().text().strip()
        if not text:
            self.status_bar.showMessage("The clipboard is empty", 3000)
            return
        self.url_edit.setText(text)
        self.url_edit.setFocus()

    # ------------------------------------------------------------ fetching
    def _on_fetch_clicked(self) -> None:
        url = self.url_edit.text().strip()
        kind = classify_url(url)
        if kind == MediaKind.INVALID:
            self._download_after_fetch = False
            QMessageBox.warning(
                self,
                "Invalid URL",
                "That doesn't look like a valid video or playlist URL for a supported "
                f"platform ({SUPPORTED_PLATFORMS_LABEL}).",
            )
            return
        self._on_url_text_changed(url)
        # Disable the field too, not just the button: returnPressed stays
        # wired to the URL field regardless of the button's enabled state,
        # so without this a fast double-Enter could fire two concurrent
        # fetches and let a stale response overwrite a newer one's result.
        self._set_fetching(True)
        self.status_bar.showMessage("Fetching details…")

        worker = MetadataFetchWorker(
            url,
            kind,
            cookies_browser=self.settings.cookies_browser,
            js_runtime_path=self.download_manager.js_runtime_path,
        )
        worker.signals.video_ready.connect(self._on_video_ready)
        worker.signals.playlist_ready.connect(self._on_playlist_ready)
        worker.signals.failed.connect(self._on_fetch_failed)
        self._active_workers.append(worker)
        self._metadata_pool.start(worker)

    def _set_fetching(self, fetching: bool) -> None:
        self.fetch_btn.setEnabled(not fetching)
        self.url_edit.setEnabled(not fetching)
        self.fetch_btn.setText("Fetching…" if fetching else "Fetch")

    def _on_fetch_failed(self, message: str) -> None:
        self._set_fetching(False)
        self._download_after_fetch = False
        self.status_bar.showMessage("Couldn't load that link", 5000)
        QMessageBox.critical(
            self,
            "Could not load this URL",
            _friendly_error(message, cookies_browser=self.settings.cookies_browser),
        )

    def _on_video_ready(self, video: VideoInfo) -> None:
        self._set_fetching(False)
        self.status_bar.showMessage(f"Loaded: {video.title}", 5000)
        self._current_video = video
        self._show_page(PAGE_VIDEO)

        self.video_title_label.setText(video.title)
        platform = detect_platform(video.url)
        meta = [video.channel or "Unknown channel"]
        if platform.value != "unknown":
            meta.append(platform.display_name)
        self.video_channel_label.setText(" · ".join(meta))
        duration = video.duration_seconds
        self.video_duration_label.setText(
            f"Duration {duration // 60}:{duration % 60:02d}" if duration else ""
        )

        self.video_format_combo.clear()
        self.video_format_combo.addItem(_AUTO_FORMAT_LABEL, None)
        for fmt in video.formats:
            if fmt.is_video or fmt.is_audio_only:
                self.video_format_combo.addItem(fmt.label(), fmt.format_id)
        self._on_video_mode_changed()

        if video.thumbnail_url:
            self._load_thumbnail(video.thumbnail_url)
        else:
            self.thumbnail_label.setPixmap(QPixmap())
            self.thumbnail_label.setText("No thumbnail")

        download_now, self._download_after_fetch = self._download_after_fetch, False
        if not video.is_available:
            QMessageBox.information(
                self,
                "Video unavailable",
                f"This video cannot be downloaded: {video.unavailable_reason or 'unavailable'}.",
            )
        elif download_now:
            self._add_video_to_queue()

    def _on_playlist_ready(self, playlist: PlaylistInfo) -> None:
        self._set_fetching(False)
        self._download_after_fetch = False
        self.status_bar.showMessage(
            f"Loaded playlist: {playlist.title} ({len(playlist.items)} items)", 5000
        )
        self._current_playlist = playlist
        self._show_page(PAGE_PLAYLIST)
        self.playlist_title_label.setText(f"{playlist.title} — {len(playlist.items)} items")
        self.playlist_model.set_items(playlist.items)
        # Clear any filter text left over from a previously loaded playlist --
        # set_items() above always resets the model to "no filter" (showing
        # everything), so leaving old filter text in the box would visibly
        # disagree with what the table actually shows until the user retyped.
        self.playlist_search_edit.clear()
        self.playlist_empty_label.setVisible(not playlist.items)
        self._update_playlist_selected_label()

    def _load_thumbnail(self, url: str) -> None:
        reply = self._network.get(QNetworkRequest(QUrl(url)))
        self._thumbnail_reply = reply
        reply.finished.connect(lambda: self._on_thumbnail_loaded(reply))

    def _on_thumbnail_loaded(self, reply: QNetworkReply) -> None:
        data = reply.readAll()
        reply.deleteLater()
        if reply is not self._thumbnail_reply:
            return  # a newer video's thumbnail has been requested since
        pixmap = QPixmap()
        if pixmap.loadFromData(data):
            scaled = pixmap.scaled(
                256,
                144,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self.thumbnail_label.setPixmap(scaled)
        else:
            self.thumbnail_label.setText("Thumbnail unavailable")

    def _enable_subtitles_for(self, combo: QComboBox, text: str) -> None:
        if text.strip() and combo.currentData() == "none":
            combo.setCurrentIndex(combo.findData("with"))

    def _subtitle_selection(
        self, combo: QComboBox, langs_edit: QLineEdit
    ) -> tuple[str | None, bool]:
        """Returns (subtitle_langs, subtitles_only), or raises ValueError with
        a user-readable message when subtitles are requested but the language
        field is empty/invalid."""
        choice = combo.currentData()
        if choice == "none":
            return None, False
        langs = parse_subtitle_langs(langs_edit.text())
        if not langs:
            raise ValueError("Enter at least one subtitle language (e.g. en), or choose None.")
        return ", ".join(langs), choice == "only"

    # -------------------------------------------------------------- video
    def _on_video_mode_changed(self) -> None:
        """Keeps Mode and Format consistent: an exact format only applies to
        "Specific format", and choosing that mode picks the first format."""
        mode = DownloadMode(self.video_mode_combo.currentData())
        format_chosen = self.video_format_combo.currentData() is not None
        if mode != DownloadMode.SPECIFIC_FORMAT and format_chosen:
            self.video_format_combo.setCurrentIndex(0)
        elif mode == DownloadMode.SPECIFIC_FORMAT and not format_chosen:
            if self.video_format_combo.count() > 1:
                self.video_format_combo.setCurrentIndex(1)
        if hasattr(self, "video_add_btn"):
            only_subs = self.video_subtitle_combo.currentData() == "only"
            self.video_add_btn.setText("Download subtitles" if only_subs else "Download")

    def _on_video_mode_picked(self) -> None:
        # Choosing a media option means the user wants the media too.
        if self.video_subtitle_combo.currentData() == "only":
            self.video_subtitle_combo.setCurrentIndex(self.video_subtitle_combo.findData("with"))

    def _on_video_format_picked(self) -> None:
        if self.video_format_combo.currentData() is not None:
            self.video_mode_combo.setCurrentIndex(
                self.video_mode_combo.findData(DownloadMode.SPECIFIC_FORMAT)
            )
        self._on_video_mode_picked()

    def _on_video_download_clicked(self) -> None:
        if self._current_video is not None:
            self._add_video_to_queue()
            return
        url = self.url_edit.text().strip()
        kind = classify_url(url) if url else MediaKind.INVALID
        if kind == MediaKind.VIDEO_URL:
            self._download_after_fetch = True
            self._on_fetch_clicked()
        elif kind == MediaKind.PLAYLIST_URL:
            self._on_fetch_clicked()
            self.status_bar.showMessage(
                "That's a playlist: tick the videos you want, then press Download selected.", 8000
            )
        else:
            QMessageBox.information(
                self, "Nothing to download yet",
                "Paste a video link into the box at the top first, then press Download.",
            )

    def _add_video_to_queue(self) -> None:
        if not self._current_video:
            return
        if not self._current_video.is_available:
            QMessageBox.information(
                self,
                "Video unavailable",
                "This video cannot be downloaded: "
                f"{self._current_video.unavailable_reason or 'unavailable'}.",
            )
            return
        dest = self.video_dest_edit.text().strip() or self.settings.default_download_dir
        if not self._ensure_folder(dest):
            return
        # PySide6's QVariant marshalling silently downgrades a `str, Enum`
        # userData value to a plain `str` on the way back out of a QComboBox
        # -- re-wrap it so the rest of the app always sees a real enum.
        mode = DownloadMode(self.video_mode_combo.currentData())
        format_id = (
            self.video_format_combo.currentData() if mode == DownloadMode.SPECIFIC_FORMAT else None
        )
        if mode == DownloadMode.SPECIFIC_FORMAT and not format_id:
            QMessageBox.warning(
                self, "Choose a format",
                "Pick one of this video's formats from the Format list, or choose another Mode.",
            )
            return
        try:
            subtitle_langs, subtitles_only = self._subtitle_selection(
                self.video_subtitle_combo, self.video_subtitle_langs_edit
            )
        except ValueError as exc:
            QMessageBox.warning(self, "Subtitles", str(exc))
            return
        request = DownloadRequest(
            url=self._current_video.url,
            title=self._current_video.title,
            destination_dir=dest,
            mode=mode,
            format_id=format_id,
            duplicate_policy=self.settings.duplicate_policy,
            subtitle_langs=subtitle_langs,
            subtitles_only=subtitles_only,
        )
        item = self.download_manager.enqueue(request)
        assert item.id is not None  # enqueue() always persists and assigns an id
        self.download_manager.start_item(item.id)
        self._show_page(PAGE_QUEUE)
        self.status_bar.showMessage("Download started", 3000)

    # ----------------------------------------------------------- playlist
    def _on_playlist_mode_picked(self) -> None:
        if self.playlist_subtitle_combo.currentData() == "only":
            self.playlist_subtitle_combo.setCurrentIndex(
                self.playlist_subtitle_combo.findData("with")
            )

    def _on_playlist_subtitles_changed(self) -> None:
        only_subs = self.playlist_subtitle_combo.currentData() == "only"
        self.playlist_add_btn.setText("Download subtitles" if only_subs else "Download selected")

    def _populate_playlist_format_options(self) -> None:
        mode = DownloadMode(self.playlist_mode_combo.currentData())
        self.playlist_format_combo.clear()
        self.playlist_format_combo.addItem("Best available (default)", None)
        choices = (
            AUDIO_CONTAINER_CHOICES if mode == DownloadMode.AUDIO_ONLY else VIDEO_CONTAINER_CHOICES
        )
        for ext in choices:
            self.playlist_format_combo.addItem(ext, ext)
        self.playlist_format_combo.setToolTip(
            "Applied to every selected item. If a video doesn't offer this "
            "format, that item automatically falls back to its best "
            "available format instead of failing."
        )

    def _on_playlist_row_clicked(self, index) -> None:
        if not index.isValid():
            return
        check_index = self.playlist_model.index(index.row(), 0)
        current = self.playlist_model.data(check_index, Qt.ItemDataRole.CheckStateRole)
        new_state = (
            Qt.CheckState.Unchecked if current == Qt.CheckState.Checked else Qt.CheckState.Checked
        )
        self.playlist_model.setData(check_index, new_state, Qt.ItemDataRole.CheckStateRole)
        self._update_playlist_selected_label()

    def _on_playlist_filter_changed(self, text: str) -> None:
        self.playlist_model.set_filter(text)
        self._update_playlist_selected_label()

    def _playlist_select_all(self) -> None:
        self.playlist_model.select_all()
        self._update_playlist_selected_label()

    def _playlist_deselect_all(self) -> None:
        self.playlist_model.deselect_all()
        self._update_playlist_selected_label()

    def _playlist_invert_selection(self) -> None:
        self.playlist_model.invert_selection()
        self._update_playlist_selected_label()

    def _update_playlist_selected_label(self) -> None:
        count = self.playlist_model.selected_count()
        self.playlist_selected_label.setText(f"{count} selected")

    def _on_playlist_download_clicked(self) -> None:
        if self._current_playlist is not None:
            self._add_playlist_selection_to_queue()
            return
        url = self.url_edit.text().strip()
        if url and classify_url(url) == MediaKind.PLAYLIST_URL:
            self._on_fetch_clicked()
            self.status_bar.showMessage(
                "Loading the playlist: tick the videos you want, then press Download selected.",
                8000,
            )
        else:
            QMessageBox.information(
                self, "No playlist loaded",
                "Paste a playlist, channel or profile link into the box at the top and press "
                "Fetch, then tick the videos you want.",
            )

    def _add_playlist_selection_to_queue(self) -> None:
        if not self._current_playlist:
            return
        selected = self.playlist_model.selected_items()
        selected = [v for v in selected if v.is_available]
        if not selected:
            QMessageBox.information(self, "Nothing to add", "No available items are selected.")
            return
        dest = self.playlist_dest_edit.text().strip() or self.settings.default_download_dir
        if not self._ensure_folder(dest):
            return
        mode = DownloadMode(self.playlist_mode_combo.currentData())
        duplicate_policy = DuplicatePolicy(self.playlist_duplicate_combo.currentData())
        preserve_order = self.playlist_preserve_order_check.isChecked()
        container_ext = self.playlist_format_combo.currentData()
        try:
            subtitle_langs, subtitles_only = self._subtitle_selection(
                self.playlist_subtitle_combo, self.playlist_subtitle_langs_edit
            )
        except ValueError as exc:
            QMessageBox.warning(self, "Subtitles", str(exc))
            return

        requests = [
            DownloadRequest(
                url=video.url,
                title=video.title,
                destination_dir=dest,
                mode=mode,
                playlist_id=self._current_playlist.playlist_id,
                playlist_index=video.playlist_index,
                duplicate_policy=duplicate_policy,
                preserve_playlist_order=preserve_order,
                container_ext=container_ext,
                subtitle_langs=subtitle_langs,
                subtitles_only=subtitles_only,
            )
            for video in selected
        ]
        items = self.download_manager.enqueue_many(requests)
        for item in items:
            assert item.id is not None  # enqueue_many() always persists and assigns an id
            self.download_manager.start_item(item.id)
        self._show_page(PAGE_QUEUE)
        self.status_bar.showMessage(f"Started {len(requests)} download(s)", 3000)

    # -------------------------------------------------------------- queue
    def _refresh_queue_table(self) -> None:
        self.queue_model.set_items(self.download_manager.items())
        self._update_queue_summary()

    def _update_queue_summary(self) -> None:
        items = self.download_manager.items()
        counts = Counter(item.status for item in items)
        parts = [
            f"{counts[status]} {status_label(status).lower()}"
            for status in (
                QueueStatus.DOWNLOADING,
                QueueStatus.QUEUED,
                QueueStatus.PAUSED,
                QueueStatus.FAILED,
                QueueStatus.COMPLETED,
            )
            if counts[status]
        ]
        self.queue_summary_label.setText(" · ".join(parts))
        self.queue_empty_label.setVisible(not items)
        active = counts[QueueStatus.DOWNLOADING] + counts[QueueStatus.QUEUED]
        nav_item = self.nav_list.item(PAGE_QUEUE)
        nav_item.setText(f"Queue  ({active})" if active else "Queue")

    def _selected_queue_ids(self) -> list[int]:
        rows = sorted({idx.row() for idx in self.queue_table.selectionModel().selectedRows()})
        ids = []
        for row in rows:
            item = self.queue_model.item_at(row)
            if item and item.id is not None:
                ids.append(item.id)
        return ids

    def _selected_queue_ids_or_hint(self) -> list[int]:
        ids = self._selected_queue_ids()
        if not ids:
            self.status_bar.showMessage(
                "Select one or more downloads in the list first "
                "(click a row; hold Shift or ⌘ to pick several).",
                5000,
            )
        return ids

    def _queue_start_all(self) -> None:
        self.download_manager.start_all_queued()

    def _queue_pause_selected(self) -> None:
        for item_id in self._selected_queue_ids_or_hint():
            self.download_manager.pause_item(item_id)

    def _queue_resume_selected(self) -> None:
        # start_item() only launches paused or queued items that don't
        # already have a worker, and is a no-op for anything else, so this is
        # safe to call on a mixed selection. Failed items go through Retry.
        for item_id in self._selected_queue_ids_or_hint():
            self.download_manager.start_item(item_id)

    def _queue_cancel_selected(self) -> None:
        for item_id in self._selected_queue_ids_or_hint():
            self.download_manager.cancel_item(item_id)

    def _queue_retry_selected(self) -> None:
        for item_id in self._selected_queue_ids_or_hint():
            self.download_manager.retry_item(item_id)

    def _queue_remove_selected(self) -> None:
        ids = self._selected_queue_ids_or_hint()
        if ids:
            self.download_manager.remove_items(ids)
            self._refresh_queue_table()

    def _queue_move_selected(self, delta: int) -> None:
        selected = self._selected_queue_ids_or_hint()
        if not selected:
            return
        order = [i.id for i in self.download_manager.items() if i.id is not None]
        # Move the block in the direction of travel without items leapfrogging
        # each other: walk from the leading edge.
        for item_id in sorted(selected, key=order.index, reverse=delta > 0):
            idx = order.index(item_id)
            new_idx = min(max(idx + delta, 0), len(order) - 1)
            if order[new_idx] in selected and new_idx != idx:
                continue
            order.insert(new_idx, order.pop(idx))
        self.download_manager.reorder(order)
        self._refresh_queue_table()
        # Keep the moved items selected so Move up/down can be clicked repeatedly.
        selection = self.queue_table.selectionModel()
        flags = QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows
        for item_id in selected:
            row = self.queue_model.row_for_id(item_id)
            if row is not None:
                selection.select(self.queue_model.index(row, 0), flags)

    def _open_folder_for_selected_queue_item(self) -> None:
        ids = self._selected_queue_ids_or_hint()
        if not ids:
            return
        item = self.download_manager.get_item(ids[0])
        if not item:
            return
        folder = (
            str(Path(item.saved_path).parent) if item.saved_path else item.request.destination_dir
        )
        self._open_folder_path(folder)

    def _queue_clear_completed(self) -> None:
        self.download_manager.clear_terminal_items()

    def _on_queue_double_clicked(self, index: QModelIndex) -> None:
        item = self.queue_model.item_at(index.row())
        if item is None:
            return
        if item.saved_path and Path(item.saved_path).exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(item.saved_path))
        else:
            self._open_folder_path(item.request.destination_dir)

    def _on_queue_context_menu(self, pos) -> None:
        index = self.queue_table.indexAt(pos)
        if not index.isValid():
            return
        if not self.queue_table.selectionModel().isRowSelected(index.row(), QModelIndex()):
            self.queue_table.selectRow(index.row())
        item = self.queue_model.item_at(index.row())
        menu = QMenu(self)
        actions: list[tuple[str, str, Callable[[], None]] | None] = [
            ("Resume", "play", self._queue_resume_selected),
            ("Pause", "pause", self._queue_pause_selected),
            ("Retry", "retry", self._queue_retry_selected),
            ("Cancel", "stop", self._queue_cancel_selected),
            None,
            ("Show in folder", "folder", self._open_folder_for_selected_queue_item),
            ("Copy link", "copy", lambda: self._copy_text(item.request.url if item else "")),
            None,
            ("Move up", "up", lambda: self._queue_move_selected(-1)),
            ("Move down", "down", lambda: self._queue_move_selected(1)),
            None,
            ("Remove", "trash", self._queue_remove_selected),
        ]
        if item and item.saved_path:
            saved_path = item.saved_path

            def open_file() -> None:
                QDesktopServices.openUrl(QUrl.fromLocalFile(saved_path))

            actions.insert(5, ("Open file", "file", open_file))
        for entry in actions:
            if entry is None:
                menu.addSeparator()
                continue
            text, icon_name, slot = entry
            action = menu.addAction(icons.icon(icon_name, "text", 16), text)
            action.triggered.connect(slot)
        menu.exec(self.queue_table.viewport().mapToGlobal(pos))

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        # Type check first: during teardown the table may already be gone.
        if event.type() == QEvent.Type.Resize and watched is self._queue_viewport:
            self._fit_queue_columns()
        return super().eventFilter(watched, event)

    def _fit_queue_columns(self) -> None:
        available = self.queue_table.viewport().width()
        compact = available < 760
        status, progress, speed = 112, (120 if compact else 150), (126 if compact else 132)
        details = max(90, int((available - status - progress - speed) * 0.35))
        header = self.queue_table.horizontalHeader()
        for column, width in ((1, status), (2, progress), (3, speed), (4, details)):
            header.resizeSection(column, width)

    def _on_concurrency_changed(self, value: int) -> None:
        if value == self.settings.concurrency:
            return
        self.settings.concurrency = value
        self.db.save_settings(self.settings)
        self.download_manager.set_concurrency(value)
        self.status_bar.showMessage(f"Up to {value} download(s) at a time", 3000)

    # These handlers update a single existing row in place rather than
    # calling _refresh_queue_table() (a full model reset): status/progress
    # changes are frequent -- every item transitions through several of them
    # -- and a full reset clears the queue table's current row selection
    # each time, which made multi-selecting items for Cancel/Retry/Remove
    # Selected nearly impossible while anything else in the queue was active.
    # _refresh_queue_table() is still correct (and used) when the item *list
    # itself* changes: add, remove, reorder.
    def _on_item_progress(self, item_id: int, percent: float, speed, eta) -> None:
        item = self.download_manager.get_item(item_id)
        if item:
            self.queue_model.update_item(item)

    def _on_item_status_changed(self, item_id: int, status: str) -> None:
        item = self.download_manager.get_item(item_id)
        if item:
            self.queue_model.update_item(item)
        self._update_queue_summary()

    def _on_item_finished(self, item_id: int, saved_path: str) -> None:
        item = self.download_manager.get_item(item_id)
        if item:
            self.queue_model.update_item(item)
        self._refresh_history_table()
        self.status_bar.showMessage(f"Saved: {saved_path}", 6000)

    def _on_item_failed(self, item_id: int, message: str, is_permanent: bool) -> None:
        item = self.download_manager.get_item(item_id)
        if item:
            self.queue_model.update_item(item)
        self._refresh_history_table()
        friendly = _friendly_error(message, cookies_browser=self.settings.cookies_browser)
        self.status_bar.showMessage(f"Download failed: {friendly}", 8000)

    def _on_youtube_blocked(self, paused_count: int) -> None:
        now = time.monotonic()
        if now - self._last_bot_notice < _BOT_NOTICE_COOLDOWN_SECONDS:
            return
        self._last_bot_notice = now
        body = _bot_check_advice(self.settings.cookies_browser)
        if paused_count:
            body += (
                f"\n\n{paused_count} queued YouTube download(s) were paused so the app "
                "doesn't keep hitting YouTube. Select them and use Resume (or Start all) "
                "when you're ready; use Retry for the failed one."
            )
        QMessageBox.warning(self, "YouTube is blocking downloads", body)

    def _on_queue_finished(self, completed: int, failed: int) -> None:
        summary = f"All downloads finished: {completed} completed"
        if failed:
            summary += f", {failed} failed (see the Queue page)"
        self.status_bar.showMessage(summary, 15000)
        # Bounces the Dock icon on macOS / flashes the taskbar button on
        # Windows and Linux -- only when the user is looking elsewhere.
        if not self.isActiveWindow():
            QApplication.alert(self)

    # ------------------------------------------------------- drag and drop
    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if _dropped_link(event.mimeData()):
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: N802
        link = _dropped_link(event.mimeData())
        if not link:
            return
        event.acceptProposedAction()
        self.url_edit.setText(link)
        if classify_url(link) != MediaKind.INVALID:
            self._on_fetch_clicked()
        else:
            self.status_bar.showMessage("That dropped link isn't from a supported site", 5000)

    def _on_item_skipped(self, item_id: int, reason: str) -> None:
        item = self.download_manager.get_item(item_id)
        if item:
            self.queue_model.update_item(item)
        self._refresh_history_table()

    # ------------------------------------------------------------ history
    def _refresh_history_table(self) -> None:
        self.history_model.setRowCount(0)
        entries = self.db.list_history()
        for entry in entries:
            title = QStandardItem(entry.title)
            title.setData(entry.saved_path or "", PATH_ROLE)
            title.setData(entry.source_url, URL_ROLE)
            title.setToolTip(f"{entry.title}\n{entry.source_url}")
            saved = QStandardItem(entry.saved_path or "—")
            saved.setToolTip(entry.saved_path or "")
            outcome = QStandardItem(status_label(entry.outcome))
            outcome.setData(entry.outcome.value, STATUS_ROLE)
            row = [
                title,
                saved,
                QStandardItem(_FORMAT_LABELS.get(entry.format_label, entry.format_label)),
                QStandardItem(_format_timestamp(entry.timestamp_iso)),
                outcome,
            ]
            for cell in row:
                cell.setEditable(False)
            self.history_model.appendRow(row)
        self.history_count_label.setText(
            f"{len(entries)} download(s)" if entries else "Nothing downloaded yet"
        )

    def _selected_history_entry(self) -> tuple[str, str] | None:
        """(saved_path, source_url) of the selected history row, if any."""
        rows = self.history_table.selectionModel().selectedRows()
        if not rows:
            self.status_bar.showMessage("Select an entry in the history list first.", 4000)
            return None
        source = self.history_proxy.mapToSource(rows[0])
        cell = self.history_model.item(source.row(), 0)
        if cell is None:
            return None
        return str(cell.data(PATH_ROLE) or ""), str(cell.data(URL_ROLE) or "")

    def _clear_history(self) -> None:
        reply = QMessageBox.question(
            self,
            "Clear history",
            "Clear download history? This does not delete any downloaded files.",
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.db.clear_history()
            self._refresh_history_table()

    def _open_folder_for_selected_history_item(self) -> None:
        entry = self._selected_history_entry()
        if entry is None:
            return
        saved_path, _url = entry
        if not saved_path:
            QMessageBox.information(
                self, "No saved file", "This entry doesn't have a saved file location."
            )
            return
        self._open_folder_path(str(Path(saved_path).parent))

    def _open_selected_history_file(self) -> None:
        entry = self._selected_history_entry()
        if entry is None:
            return
        saved_path, _url = entry
        if saved_path and Path(saved_path).exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(saved_path))
        elif saved_path:
            QMessageBox.information(
                self, "File not found",
                f"The file is no longer at its saved location:\n{saved_path}",
            )
        else:
            QMessageBox.information(
                self, "No saved file", "This entry doesn't have a saved file location."
            )

    def _copy_selected_history_link(self) -> None:
        entry = self._selected_history_entry()
        if entry is not None:
            self._copy_text(entry[1])

    # ------------------------------------------------------------ helpers
    def _copy_text(self, text: str) -> None:
        if text:
            QGuiApplication.clipboard().setText(text)
            self.status_bar.showMessage("Link copied", 2500)

    def _ensure_folder(self, folder: str) -> bool:
        try:
            Path(folder).expanduser().mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            QMessageBox.warning(
                self, "Can't use that folder",
                f"The download folder can't be created or written to:\n{folder}\n\n{exc}",
            )
            return False
        return True

    def _open_folder_path(self, folder: str) -> None:
        if not folder or not Path(folder).expanduser().is_dir():
            self.status_bar.showMessage(f"Folder not found: {folder or '(none)'}", 4000)
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(folder).expanduser())))

    def _browse_folder(self, target_edit: QLineEdit) -> None:
        chosen = QFileDialog.getExistingDirectory(
            self, "Choose destination folder", target_edit.text()
        )
        if chosen:
            target_edit.setText(chosen)

    def _refresh_side_status(self) -> None:
        browser = self.settings.cookies_browser
        self.signin_btn.setText(
            f"YouTube sign-in: {browser.capitalize()}" if browser else "YouTube sign-in: Off"
        )

    def _open_about(self) -> None:
        AboutDialog(
            self.download_manager.refresh_ffmpeg_status(),
            self.download_manager.js_runtime_path,
            self,
        ).exec()

    def _open_settings(self) -> None:
        dialog = SettingsDialog(self.settings, self)
        if dialog.exec():
            self.settings = dialog.result_settings()
            self.db.save_settings(self.settings)
            self.download_manager.set_concurrency(self.settings.concurrency)
            self.download_manager.retry_attempts = self.settings.retry_attempts
            self.download_manager.cookies_browser = self.settings.cookies_browser
            self.concurrency_spin.blockSignals(True)
            self.concurrency_spin.setValue(self.settings.concurrency)
            self.concurrency_spin.blockSignals(False)
            self.apply_theme(self.settings.theme)
            self._refresh_side_status()

    def apply_theme(self, theme: str) -> None:
        app = QApplication.instance()
        if isinstance(app, QApplication):
            theme_mod.apply(app, theme)
        icons.retint(self)
        for table in (self.queue_table, self.history_table, self.playlist_table):
            table.viewport().update()

    def _on_system_scheme_changed(self, _scheme) -> None:
        if self.settings.theme == "system":
            self.apply_theme("system")

    def _check_ffmpeg(self) -> None:
        # Reuses/refreshes the DownloadManager's cached detection rather than
        # probing separately -- avoids two redundant `ffmpeg -version`
        # subprocess spawns (one here, one in DownloadManager.__init__) on
        # every single startup.
        status = self.download_manager.refresh_ffmpeg_status()
        if status.found:
            self.ffmpeg_status_btn.setText("FFmpeg ready")
            self.ffmpeg_status_btn.setToolTip(status.version or "")
            icons.set_icon(self.ffmpeg_status_btn, "ok", "ok")
            self.status_bar.showMessage(f"FFmpeg detected: {status.version}", 5000)
        else:
            self.ffmpeg_status_btn.setText("FFmpeg missing")
            self.ffmpeg_status_btn.setToolTip("Click for install instructions")
            icons.set_icon(self.ffmpeg_status_btn, "warn", "warn")
            self._show_ffmpeg_help()

    def _show_ffmpeg_help(self) -> None:
        status = self.download_manager.refresh_ffmpeg_status()
        if status.found:
            QMessageBox.information(self, "FFmpeg", f"FFmpeg is installed:\n{status.version}")
            return
        QMessageBox.warning(
            self,
            "FFmpeg not found",
            "FFmpeg is required to merge video/audio streams and to extract audio. "
            "Downloads that need conversion will fail until it is installed.\n\n"
            f"{install_instructions(sys.platform)}",
        )

    def _check_for_updates(self, *, manual: bool) -> None:
        """Looks up the latest yt-dlp version on PyPI in the background.

        `manual=False` (the startup check, gated behind the "Check for
        yt-dlp updates on startup" setting) stays quiet unless there's
        something actionable -- no dialog for "up to date" or for a network
        hiccup, just a status-bar note if an update exists, so the app
        isn't naggy on every single launch. `manual=True` (the button) always
        responds with something, since the user explicitly asked.
        """
        if manual:
            self.check_updates_btn.setEnabled(False)
            self.status_bar.showMessage("Checking for yt-dlp updates…")
        worker = UpdateCheckWorker(manual=manual)
        worker.signals.finished.connect(self._on_update_check_finished)
        self._active_workers.append(worker)
        self._metadata_pool.start(worker)

    def _on_update_check_finished(
        self, result: update_check.UpdateCheckResult, manual: bool
    ) -> None:
        if manual:
            self.check_updates_btn.setEnabled(True)

        if result.error:
            if manual:
                QMessageBox.warning(
                    self,
                    "Couldn't check for updates",
                    f"Couldn't reach PyPI to check for a yt-dlp update:\n\n{result.error}",
                )
            return

        if not result.update_available:
            if manual:
                QMessageBox.information(
                    self,
                    "Up to date",
                    f"yt-dlp is up to date (installed: {result.installed_version}).",
                )
            return

        self.status_bar.showMessage(
            f"A newer yt-dlp is available: {result.latest_version} "
            f"(you have {result.installed_version}). Click 'Check for updates' for details.",
            10000,
        )
        if manual:
            self._offer_yt_dlp_update(result)

    def _offer_yt_dlp_update(self, result: update_check.UpdateCheckResult) -> None:
        if update_check.is_frozen():
            QMessageBox.information(
                self,
                "yt-dlp update available",
                f"A newer yt-dlp is available: {result.latest_version} "
                f"(this build has {result.installed_version}).\n\n"
                "This is a packaged build, so it can't update yt-dlp in place. "
                "Ask for a rebuild with the latest yt-dlp, or run the app from "
                "source if you'd like to update it yourself.",
            )
            return

        reply = QMessageBox.question(
            self,
            "yt-dlp update available",
            f"A newer yt-dlp is available: {result.latest_version} "
            f"(you have {result.installed_version}). yt-dlp needs to stay "
            "current since every supported platform changes often enough "
            "to break it.\n\n"
            "Update now? This runs \"pip install -U yt-dlp\" in this app's "
            "own virtual environment.",
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self.check_updates_btn.setEnabled(False)
        self.status_bar.showMessage("Updating yt-dlp…")
        worker = UpdateInstallWorker()
        worker.signals.finished.connect(self._on_update_install_finished)
        self._active_workers.append(worker)
        self._metadata_pool.start(worker)

    def _on_update_install_finished(self, success: bool, output: str) -> None:
        self.check_updates_btn.setEnabled(True)
        if success:
            self.status_bar.showMessage("yt-dlp updated.", 5000)
            QMessageBox.information(
                self,
                "yt-dlp updated",
                "yt-dlp was updated successfully. Restart the app for the "
                "new version to take effect.",
            )
        else:
            QMessageBox.warning(
                self,
                "Update failed",
                f"Updating yt-dlp failed:\n\n{output[:1000]}",
            )

    def closeEvent(self, event) -> None:  # noqa: N802
        if self.download_manager.has_active_downloads():
            reply = QMessageBox.question(
                self,
                "Downloads in progress",
                "Downloads are still in progress. Exit anyway? Active items will be "
                "marked paused and can be resumed next time you open the app.",
            )
            if reply != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        self.download_manager.shutdown()
        self._metadata_pool.clear()
        event.accept()


def _dropped_link(mime) -> str | None:
    """The first web link in dropped data (a dragged URL or plain text)."""
    if mime.hasUrls():
        for url in mime.urls():
            if url.scheme() in ("http", "https"):
                return url.toString()
    if mime.hasText():
        text = mime.text().strip()
        if text.startswith(("http://", "https://")) and len(text.split()) == 1:
            return text
    return None


def _format_timestamp(iso: str) -> str:
    try:
        return datetime.datetime.fromisoformat(iso).strftime("%d %b %Y, %H:%M")
    except ValueError:
        return iso


def _bot_check_advice(cookies_browser: str | None) -> str:
    if cookies_browser:
        return (
            "YouTube is blocking requests from this network (\"Sign in to confirm you're "
            f"not a bot\"), even with sign-in from {cookies_browser.capitalize()}. Make sure "
            f"you're signed in to YouTube in {cookies_browser.capitalize()}, or wait a few "
            "hours and try again."
        )
    return (
        "YouTube is blocking requests from this network (\"Sign in to confirm you're not "
        "a bot\"). This usually happens after many downloads in a short time. To keep "
        "downloading, choose your browser under Settings > YouTube sign-in from, or wait "
        "a few hours (sometimes a day) and try again."
    )


def _friendly_error(message: str, *, cookies_browser: str | None = None) -> str:
    lowered = message.lower()
    if is_bot_check_error(message):
        return _bot_check_advice(cookies_browser)
    if message.startswith(COOKIE_LOAD_ERROR_PREFIX):
        return (
            f"{message.splitlines()[0][:300]}\n\nIf macOS asked for Keychain access, choose "
            "Always Allow. For Safari, give this app Full Disk Access in System Settings > "
            "Privacy & Security. Or pick another browser under Settings > YouTube sign-in from."
        )
    if "private video" in lowered or "private account" in lowered:
        return "This content is private and cannot be downloaded."
    if any(
        p in lowered
        for p in ("logged-in", "log in to", "login required", "requires authentication")
    ):
        return (
            "This content requires a logged-in account to access. This app doesn't "
            "support logging in to any platform, so it can only download public content."
        )
    if "protected" in lowered and "tweet" in lowered:
        return "This account's posts are protected and cannot be downloaded without authentication."
    # Not for "HTTP Error 503: Service Unavailable", a temporary server error.
    if "unavailable" in lowered and "http error" not in lowered:
        return "This content is unavailable (it may have been removed)."
    if "age" in lowered and "restrict" in lowered:
        return "This content is age-restricted and cannot be downloaded without authentication."
    if "not available in your country" in lowered:
        return "This content is not available in your region."
    if "timed out" in lowered or "timeout" in lowered:
        return "The connection timed out. Check your network and try again."
    return message.splitlines()[0][:300]
