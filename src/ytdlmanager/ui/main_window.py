from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import QRunnable, Qt, QThreadPool, QUrl
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkRequest
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStatusBar,
    QTableView,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .. import update_check
from ..config import LEGAL_NOTICE, SUPPORTED_PLATFORMS_LABEL
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
from .metadata_worker import MetadataFetchWorker
from .models_qt import PlaylistTableModel, QueueTableModel
from .settings_dialog import SettingsDialog
from .theme import stylesheet_for
from .update_check_worker import UpdateCheckWorker, UpdateInstallWorker

# Several downloads running at once all hit YouTube's bot check within
# seconds of each other; explain it once, not once per item.
_BOT_NOTICE_COOLDOWN_SECONDS = 60.0


class MainWindow(QMainWindow):
    def __init__(self, db: Database, settings: AppSettings) -> None:
        super().__init__()
        self.db = db
        self.settings = settings
        self.setWindowTitle("Social Media Download Manager")
        self.resize(1100, 720)

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
        self._last_bot_notice = float("-inf")

        self._metadata_pool = QThreadPool()
        self._metadata_pool.setMaxThreadCount(2)
        self._network = QNetworkAccessManager(self)

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

        self._build_ui()
        self.apply_theme(settings.theme)
        self._refresh_queue_table()
        self._refresh_history_table()
        self._check_ffmpeg()

        if self.download_manager.items():
            self.status_bar.showMessage(
                "Restored queue from a previous session. Downloads were NOT resumed "
                "automatically -- use Start All when you're ready.",
                8000,
            )

        if self.settings.check_for_updates:
            self._check_for_updates(manual=False)

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(18, 16, 18, 14)
        root.setSpacing(12)

        notice = QLabel(LEGAL_NOTICE)
        notice.setObjectName("legalNotice")
        notice.setWordWrap(True)
        notice.setAccessibleName("Legal notice")
        root.addWidget(notice)

        url_row = QHBoxLayout()
        url_row.setSpacing(8)
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText(
            f"Paste a video or playlist URL ({SUPPORTED_PLATFORMS_LABEL})…"
        )
        self.url_edit.setAccessibleName("Video or playlist URL")
        self.url_edit.returnPressed.connect(self._on_fetch_clicked)
        self.fetch_btn = QPushButton("Fetch")
        self.fetch_btn.setProperty("accent", True)
        self.fetch_btn.clicked.connect(self._on_fetch_clicked)
        self.url_kind_label = QLabel("")
        self.url_kind_label.setObjectName("sectionHint")
        self.url_kind_label.setAccessibleName("Detected platform and content type")
        url_row.addWidget(QLabel("URL:"))
        url_row.addWidget(self.url_edit, stretch=1)
        url_row.addWidget(self.url_kind_label)
        url_row.addWidget(self.fetch_btn)
        root.addLayout(url_row)

        self.tabs = QTabWidget()
        root.addWidget(self.tabs, stretch=1)
        self.tabs.addTab(self._build_video_tab(), "Video")
        self.tabs.addTab(self._build_playlist_tab(), "Playlist")
        self.tabs.addTab(self._build_queue_tab(), "Queue")
        self.tabs.addTab(self._build_history_tab(), "History")

        bottom_row = QHBoxLayout()
        self.check_updates_btn = QPushButton("Check for Updates")
        self.check_updates_btn.clicked.connect(lambda: self._check_for_updates(manual=True))
        settings_btn = QPushButton("Settings…")
        settings_btn.clicked.connect(self._open_settings)
        bottom_row.addWidget(self.check_updates_btn)
        bottom_row.addStretch(1)
        bottom_row.addWidget(settings_btn)
        root.addLayout(bottom_row)

        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)

    def _build_video_tab(self) -> QWidget:
        tab = QWidget()
        layout = QHBoxLayout(tab)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(18)

        self.thumbnail_label = QLabel("No video loaded")
        self.thumbnail_label.setObjectName("thumbnailFrame")
        self.thumbnail_label.setFixedSize(240, 135)
        self.thumbnail_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.thumbnail_label.setAccessibleName("Video thumbnail")
        layout.addWidget(self.thumbnail_label)

        details = QVBoxLayout()
        details.setSpacing(6)
        layout.addLayout(details, stretch=1)

        self.video_title_label = QLabel("No video loaded yet")
        self.video_title_label.setObjectName("pageTitle")
        self.video_title_label.setWordWrap(True)
        self.video_channel_label = QLabel("")
        self.video_channel_label.setObjectName("sectionHint")
        self.video_duration_label = QLabel("")
        self.video_duration_label.setObjectName("sectionHint")
        for lbl in (self.video_title_label, self.video_channel_label, self.video_duration_label):
            details.addWidget(lbl)
        details.addSpacing(6)

        form = QGroupBox("Download options")
        form_layout = QVBoxLayout(form)
        form_layout.setSpacing(10)

        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("Mode:"))
        self.video_mode_combo = QComboBox()
        self.video_mode_combo.addItem("Video with audio", DownloadMode.VIDEO_WITH_AUDIO)
        self.video_mode_combo.addItem("Audio only", DownloadMode.AUDIO_ONLY)
        self.video_mode_combo.addItem("Best available quality", DownloadMode.BEST_QUALITY)
        self.video_mode_combo.addItem("Specific format", DownloadMode.SPECIFIC_FORMAT)
        self.video_mode_combo.setAccessibleName("Download mode")
        self.video_mode_combo.currentIndexChanged.connect(self._on_video_mode_changed)
        mode_row.addWidget(self.video_mode_combo, stretch=1)
        form_layout.addLayout(mode_row)

        format_row = QHBoxLayout()
        format_row.addWidget(QLabel("Format:"))
        self.video_format_combo = QComboBox()
        self.video_format_combo.setEnabled(False)
        self.video_format_combo.setAccessibleName("Specific format")
        format_row.addWidget(self.video_format_combo, stretch=1)
        form_layout.addLayout(format_row)

        subtitle_row, self.video_subtitle_combo, self.video_subtitle_langs_edit = (
            self._build_subtitle_row(self._on_video_mode_changed)
        )
        form_layout.addLayout(subtitle_row)

        dest_row = QHBoxLayout()
        dest_row.addWidget(QLabel("Save to:"))
        self.video_dest_edit = QLineEdit(self.settings.default_download_dir)
        self.video_dest_edit.setAccessibleName("Destination folder")
        browse_btn = QPushButton("Browse…")
        browse_btn.clicked.connect(lambda: self._browse_folder(self.video_dest_edit))
        dest_row.addWidget(self.video_dest_edit, stretch=1)
        dest_row.addWidget(browse_btn)
        form_layout.addLayout(dest_row)

        self.video_add_btn = QPushButton("Download")
        self.video_add_btn.setProperty("accent", True)
        self.video_add_btn.setEnabled(False)
        self.video_add_btn.clicked.connect(self._add_video_to_queue)
        form_layout.addWidget(self.video_add_btn)

        details.addWidget(form)
        details.addStretch(1)
        return tab

    def _build_playlist_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        self.playlist_title_label = QLabel("No playlist loaded yet")
        self.playlist_title_label.setObjectName("pageTitle")
        layout.addWidget(self.playlist_title_label)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)
        self.playlist_search_edit = QLineEdit()
        self.playlist_search_edit.setPlaceholderText("Filter by title…")
        self.playlist_search_edit.setAccessibleName("Filter playlist by title")
        self.playlist_search_edit.textChanged.connect(self._on_playlist_filter_changed)
        toolbar.addWidget(self.playlist_search_edit, stretch=1)

        select_all_btn = QPushButton("Select All")
        deselect_btn = QPushButton("Deselect All")
        invert_btn = QPushButton("Invert Selection")
        select_all_btn.clicked.connect(self._playlist_select_all)
        deselect_btn.clicked.connect(self._playlist_deselect_all)
        invert_btn.clicked.connect(self._playlist_invert_selection)
        toolbar.addWidget(select_all_btn)
        toolbar.addWidget(deselect_btn)
        toolbar.addWidget(invert_btn)
        layout.addLayout(toolbar)

        self.playlist_model = PlaylistTableModel()
        self.playlist_table = QTableView()
        self.playlist_table.setModel(self.playlist_model)
        self.playlist_table.setAccessibleName("Playlist items")
        header = self.playlist_table.horizontalHeader()
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.playlist_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.playlist_table.setAlternatingRowColors(True)
        self.playlist_table.setShowGrid(False)
        self.playlist_table.verticalHeader().setVisible(False)
        self.playlist_table.verticalHeader().setDefaultSectionSize(30)
        # QTableView's built-in checkbox-toggle-on-click is unreliable together
        # with SelectRows (the click is often consumed by row selection before
        # it reaches the checkbox delegate) -- toggle explicitly instead.
        self.playlist_table.clicked.connect(self._on_playlist_row_clicked)
        layout.addWidget(self.playlist_table, stretch=1)

        options = QGroupBox("Batch download options")
        options_layout = QVBoxLayout(options)
        options_layout.setSpacing(10)

        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("Mode:"))
        self.playlist_mode_combo = QComboBox()
        self.playlist_mode_combo.addItem("Video with audio", DownloadMode.VIDEO_WITH_AUDIO)
        self.playlist_mode_combo.addItem("Audio only", DownloadMode.AUDIO_ONLY)
        self.playlist_mode_combo.addItem("Best available quality", DownloadMode.BEST_QUALITY)
        self.playlist_mode_combo.setAccessibleName("Batch download mode")
        self.playlist_mode_combo.currentIndexChanged.connect(self._on_playlist_mode_changed)
        mode_row.addWidget(self.playlist_mode_combo, stretch=1)
        options_layout.addLayout(mode_row)

        format_row = QHBoxLayout()
        format_row.addWidget(QLabel("Preferred format:"))
        self.playlist_format_combo = QComboBox()
        self.playlist_format_combo.setAccessibleName("Preferred format for the whole batch")
        format_row.addWidget(self.playlist_format_combo, stretch=1)
        options_layout.addLayout(format_row)
        self._populate_playlist_format_options()

        subtitle_row, self.playlist_subtitle_combo, self.playlist_subtitle_langs_edit = (
            self._build_subtitle_row(self._on_playlist_mode_changed)
        )
        options_layout.addLayout(subtitle_row)

        dest_row = QHBoxLayout()
        dest_row.addWidget(QLabel("Save to:"))
        self.playlist_dest_edit = QLineEdit(self.settings.default_download_dir)
        self.playlist_dest_edit.setAccessibleName("Playlist destination folder")
        browse_btn = QPushButton("Browse…")
        browse_btn.clicked.connect(lambda: self._browse_folder(self.playlist_dest_edit))
        dest_row.addWidget(self.playlist_dest_edit, stretch=1)
        dest_row.addWidget(browse_btn)
        options_layout.addLayout(dest_row)

        dup_row = QHBoxLayout()
        dup_row.addWidget(QLabel("Duplicates:"))
        self.playlist_duplicate_combo = QComboBox()
        self.playlist_duplicate_combo.addItem("Rename automatically", DuplicatePolicy.RENAME)
        self.playlist_duplicate_combo.addItem("Overwrite existing file", DuplicatePolicy.OVERWRITE)
        self.playlist_duplicate_combo.addItem("Skip download", DuplicatePolicy.SKIP)
        idx = self.playlist_duplicate_combo.findData(self.settings.duplicate_policy)
        self.playlist_duplicate_combo.setCurrentIndex(max(idx, 0))
        self.playlist_duplicate_combo.setAccessibleName("Duplicate file behaviour")
        dup_row.addWidget(self.playlist_duplicate_combo)

        self.playlist_preserve_order_check = QCheckBox("Preserve playlist order in filenames")
        self.playlist_preserve_order_check.setChecked(self.settings.preserve_playlist_order)
        dup_row.addWidget(self.playlist_preserve_order_check)
        options_layout.addLayout(dup_row)

        self.playlist_selected_label = QLabel("0 selected")
        options_layout.addWidget(self.playlist_selected_label)

        self.playlist_add_btn = QPushButton("Download Selected")
        self.playlist_add_btn.setProperty("accent", True)
        self.playlist_add_btn.setEnabled(False)
        self.playlist_add_btn.clicked.connect(self._add_playlist_selection_to_queue)
        options_layout.addWidget(self.playlist_add_btn)

        layout.addWidget(options)
        return tab

    def _build_queue_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)
        start_btn = QPushButton("Start All")
        pause_btn = QPushButton("Pause Selected")
        resume_btn = QPushButton("Resume Selected")
        cancel_btn = QPushButton("Cancel Selected")
        retry_btn = QPushButton("Retry Selected")
        remove_btn = QPushButton("Remove Selected")
        clear_completed_btn = QPushButton("Clear Completed")
        up_btn = QPushButton("Move Up")
        down_btn = QPushButton("Move Down")
        pause_btn.setToolTip(
            "Stop the transfer but keep the partial file, so Resume Selected\n"
            "continues it later instead of starting over."
        )
        cancel_btn.setToolTip(
            "Stop the transfer and delete the partial file. Use Pause Selected\n"
            "instead if you want to continue it later."
        )
        start_btn.clicked.connect(self._queue_start_all)
        pause_btn.clicked.connect(self._queue_pause_selected)
        resume_btn.clicked.connect(self._queue_resume_selected)
        cancel_btn.clicked.connect(self._queue_cancel_selected)
        retry_btn.clicked.connect(self._queue_retry_selected)
        remove_btn.clicked.connect(self._queue_remove_selected)
        clear_completed_btn.clicked.connect(self._queue_clear_completed)
        up_btn.clicked.connect(lambda: self._queue_move_selected(-1))
        down_btn.clicked.connect(lambda: self._queue_move_selected(1))
        queue_buttons = (
            start_btn,
            pause_btn,
            resume_btn,
            cancel_btn,
            retry_btn,
            remove_btn,
            clear_completed_btn,
            up_btn,
            down_btn,
        )
        for b in queue_buttons:
            toolbar.addWidget(b)
        toolbar.addStretch(1)
        layout.addLayout(toolbar)

        self.queue_model = QueueTableModel()
        self.queue_table = QTableView()
        self.queue_table.setModel(self.queue_model)
        self.queue_table.setAccessibleName("Download queue")
        self.queue_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.queue_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.queue_table.setAlternatingRowColors(True)
        self.queue_table.setShowGrid(False)
        self.queue_table.verticalHeader().setVisible(False)
        self.queue_table.verticalHeader().setDefaultSectionSize(30)
        layout.addWidget(self.queue_table, stretch=1)

        self.open_folder_btn = QPushButton("Open Folder for Selected")
        self.open_folder_btn.clicked.connect(self._open_folder_for_selected_queue_item)
        layout.addWidget(self.open_folder_btn)

        return tab

    def _build_history_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        toolbar = QHBoxLayout()
        clear_btn = QPushButton("Clear History")
        clear_btn.clicked.connect(self._clear_history)
        toolbar.addWidget(clear_btn)
        toolbar.addStretch(1)
        layout.addLayout(toolbar)

        from PySide6.QtGui import QStandardItemModel

        self.history_model = QStandardItemModel(0, 5)
        self.history_model.setHorizontalHeaderLabels(
            ["Title", "Saved Location", "Format", "Date", "Outcome"]
        )
        self.history_table = QTableView()
        self.history_table.setAlternatingRowColors(True)
        self.history_table.setShowGrid(False)
        self.history_table.verticalHeader().setVisible(False)
        self.history_table.verticalHeader().setDefaultSectionSize(30)
        self.history_table.setModel(self.history_model)
        self.history_table.setAccessibleName("Download history")
        history_header = self.history_table.horizontalHeader()
        history_header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.history_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        layout.addWidget(self.history_table, stretch=1)

        self.history_open_folder_btn = QPushButton("Open Folder for Selected")
        self.history_open_folder_btn.clicked.connect(self._open_folder_for_selected_history_item)
        layout.addWidget(self.history_open_folder_btn)

        return tab

    # ------------------------------------------------------------ fetching
    def _on_fetch_clicked(self) -> None:
        url = self.url_edit.text().strip()
        kind = classify_url(url)
        if kind == MediaKind.INVALID:
            QMessageBox.warning(
                self,
                "Invalid URL",
                "That doesn't look like a valid video or playlist URL for a supported "
                f"platform ({SUPPORTED_PLATFORMS_LABEL}).",
            )
            return
        platform = detect_platform(url)
        kind_label = "Video" if kind == MediaKind.VIDEO_URL else "Playlist"
        self.url_kind_label.setText(f"{platform.display_name} · {kind_label}")
        # Disable the field too, not just the button: returnPressed stays
        # wired to the URL field regardless of the button's enabled state,
        # so without this a fast double-Enter could fire two concurrent
        # fetches and let a stale response overwrite a newer one's result.
        self.fetch_btn.setEnabled(False)
        self.url_edit.setEnabled(False)
        self.status_bar.showMessage("Fetching metadata…")

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

    def _on_fetch_failed(self, message: str) -> None:
        self.fetch_btn.setEnabled(True)
        self.url_edit.setEnabled(True)
        self.status_bar.showMessage("Metadata fetch failed", 5000)
        QMessageBox.critical(
            self,
            "Could not load this URL",
            _friendly_error(message, cookies_browser=self.settings.cookies_browser),
        )

    def _on_video_ready(self, video: VideoInfo) -> None:
        self.fetch_btn.setEnabled(True)
        self.url_edit.setEnabled(True)
        self.status_bar.showMessage(f"Loaded: {video.title}", 5000)
        self._current_video = video
        self.tabs.setCurrentIndex(0)

        self.video_title_label.setText(video.title)
        self.video_channel_label.setText(f"Channel: {video.channel or '—'}")
        duration = video.duration_seconds
        self.video_duration_label.setText(
            f"Duration: {duration // 60}:{duration % 60:02d}" if duration else "Duration: —"
        )
        self.video_add_btn.setEnabled(video.is_available)
        if not video.is_available:
            QMessageBox.information(
                self,
                "Video unavailable",
                f"This video cannot be downloaded: {video.unavailable_reason or 'unavailable'}.",
            )

        self.video_format_combo.clear()
        for fmt in video.formats:
            if fmt.is_video or fmt.is_audio_only:
                self.video_format_combo.addItem(fmt.label(), fmt.format_id)

        if video.thumbnail_url:
            self._load_thumbnail(video.thumbnail_url)
        else:
            self.thumbnail_label.setText("No thumbnail")

    def _on_playlist_ready(self, playlist: PlaylistInfo) -> None:
        self.fetch_btn.setEnabled(True)
        self.url_edit.setEnabled(True)
        self.status_bar.showMessage(
            f"Loaded playlist: {playlist.title} ({len(playlist.items)} items)", 5000
        )
        self._current_playlist = playlist
        self.tabs.setCurrentIndex(1)
        self.playlist_title_label.setText(f"{playlist.title} — {len(playlist.items)} items")
        self.playlist_model.set_items(playlist.items)
        # Clear any filter text left over from a previously loaded playlist --
        # set_items() above always resets the model to "no filter" (showing
        # everything), so leaving old filter text in the box would visibly
        # disagree with what the table actually shows until the user retyped.
        self.playlist_search_edit.clear()
        self.playlist_add_btn.setEnabled(bool(playlist.items))
        self._update_playlist_selected_label()

    def _load_thumbnail(self, url: str) -> None:
        request = QNetworkRequest(QUrl(url))
        reply = self._network.get(request)
        reply.finished.connect(lambda: self._on_thumbnail_loaded(reply))

    def _on_thumbnail_loaded(self, reply) -> None:
        data = reply.readAll()
        reply.deleteLater()
        pixmap = QPixmap()
        if pixmap.loadFromData(data):
            scaled = pixmap.scaled(
                240,
                135,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self.thumbnail_label.setPixmap(scaled)
        else:
            self.thumbnail_label.setText("Thumbnail unavailable")

    def _build_subtitle_row(self, on_change) -> tuple[QHBoxLayout, QComboBox, QLineEdit]:
        row = QHBoxLayout()
        row.addWidget(QLabel("Subtitles:"))
        combo = QComboBox()
        # Plain-string userData on purpose (see the QVariant note in
        # _add_video_to_queue): nothing to downgrade.
        combo.addItem("None", "none")
        combo.addItem("Save alongside the download", "with")
        combo.addItem("Subtitles only (no video/audio)", "only")
        combo.setAccessibleName("Subtitles")
        langs = QLineEdit("en")
        langs.setAccessibleName("Subtitle languages")
        langs.setPlaceholderText("en, fr, … or all")
        langs.setToolTip(
            "Language codes separated by commas (e.g. en, fr), or \"all\".\n"
            "Saved as .srt next to the media file. If the video has no human-made\n"
            "track in a language, auto-generated captions are used when available."
        )
        langs.setEnabled(False)
        combo.currentIndexChanged.connect(on_change)
        row.addWidget(combo, stretch=1)
        row.addWidget(langs)
        return row, combo, langs

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

    def _on_video_mode_changed(self) -> None:
        mode = DownloadMode(self.video_mode_combo.currentData())
        subs = self.video_subtitle_combo.currentData()
        self.video_subtitle_langs_edit.setEnabled(subs != "none")
        # Media options are meaningless when only subtitles are fetched.
        self.video_mode_combo.setEnabled(subs != "only")
        self.video_format_combo.setEnabled(subs != "only" and mode == DownloadMode.SPECIFIC_FORMAT)

    # -------------------------------------------------------------- video
    def _add_video_to_queue(self) -> None:
        if not self._current_video:
            return
        dest = self.video_dest_edit.text().strip() or self.settings.default_download_dir
        Path(dest).mkdir(parents=True, exist_ok=True)
        # PySide6's QVariant marshalling silently downgrades a `str, Enum`
        # userData value to a plain `str` on the way back out of a QComboBox
        # -- re-wrap it so the rest of the app always sees a real enum.
        mode = DownloadMode(self.video_mode_combo.currentData())
        format_id = (
            self.video_format_combo.currentData() if mode == DownloadMode.SPECIFIC_FORMAT else None
        )
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
        self.tabs.setCurrentIndex(2)
        self.status_bar.showMessage("Download started", 3000)

    # ----------------------------------------------------------- playlist
    def _on_playlist_mode_changed(self) -> None:
        self._populate_playlist_format_options()
        subs = self.playlist_subtitle_combo.currentData()
        self.playlist_subtitle_langs_edit.setEnabled(subs != "none")
        self.playlist_mode_combo.setEnabled(subs != "only")
        self.playlist_format_combo.setEnabled(subs != "only")

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

    def _add_playlist_selection_to_queue(self) -> None:
        if not self._current_playlist:
            return
        selected = self.playlist_model.selected_items()
        selected = [v for v in selected if v.is_available]
        if not selected:
            QMessageBox.information(self, "Nothing to add", "No available items are selected.")
            return
        dest = self.playlist_dest_edit.text().strip() or self.settings.default_download_dir
        Path(dest).mkdir(parents=True, exist_ok=True)
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
        self.tabs.setCurrentIndex(2)
        self.status_bar.showMessage(f"Started {len(requests)} download(s)", 3000)

    # -------------------------------------------------------------- queue
    def _refresh_queue_table(self) -> None:
        self.queue_model.set_items(self.download_manager.items())

    def _selected_queue_ids(self) -> list[int]:
        rows = {idx.row() for idx in self.queue_table.selectionModel().selectedRows()}
        ids = []
        for row in rows:
            item = self.queue_model.item_at(row)
            if item and item.id is not None:
                ids.append(item.id)
        return ids

    def _queue_start_all(self) -> None:
        self.download_manager.start_all_queued()

    def _queue_pause_selected(self) -> None:
        for item_id in self._selected_queue_ids():
            self.download_manager.pause_item(item_id)

    def _queue_resume_selected(self) -> None:
        # start_item() only launches paused or queued items that don't
        # already have a worker, and is a no-op for anything else, so this is
        # safe to call on a mixed selection. Failed items go through Retry.
        for item_id in self._selected_queue_ids():
            self.download_manager.start_item(item_id)

    def _queue_cancel_selected(self) -> None:
        for item_id in self._selected_queue_ids():
            self.download_manager.cancel_item(item_id)

    def _queue_retry_selected(self) -> None:
        for item_id in self._selected_queue_ids():
            self.download_manager.retry_item(item_id)

    def _queue_remove_selected(self) -> None:
        self.download_manager.remove_items(self._selected_queue_ids())
        self._refresh_queue_table()

    def _queue_move_selected(self, delta: int) -> None:
        items = self.download_manager.items()
        order = [i.id for i in items if i.id is not None]
        for item_id in self._selected_queue_ids():
            idx = order.index(item_id)
            new_idx = min(max(idx + delta, 0), len(order) - 1)
            order.insert(new_idx, order.pop(idx))
        self.download_manager.reorder(order)
        self._refresh_queue_table()

    def _open_folder_for_selected_queue_item(self) -> None:
        ids = self._selected_queue_ids()
        if not ids:
            return
        item = self.download_manager.get_item(ids[0])
        if not item:
            return
        folder = (
            str(Path(item.saved_path).parent) if item.saved_path else item.request.destination_dir
        )
        QDesktopServices.openUrl(QUrl.fromLocalFile(folder))

    def _queue_clear_completed(self) -> None:
        self.download_manager.clear_terminal_items()

    # These four handlers update a single existing row in place rather than
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
                "doesn't keep hitting YouTube. Select them and use Resume Selected (or "
                "Start All) when you're ready; use Retry Selected for the failed one."
            )
        QMessageBox.warning(self, "YouTube is blocking downloads", body)

    def _on_item_skipped(self, item_id: int, reason: str) -> None:
        item = self.download_manager.get_item(item_id)
        if item:
            self.queue_model.update_item(item)
        self._refresh_history_table()

    # ------------------------------------------------------------ history
    def _refresh_history_table(self) -> None:
        from PySide6.QtGui import QStandardItem

        self.history_model.setRowCount(0)
        for entry in self.db.list_history():
            row = [
                QStandardItem(entry.title),
                QStandardItem(entry.saved_path or "—"),
                QStandardItem(entry.format_label),
                QStandardItem(entry.timestamp_iso),
                QStandardItem(entry.outcome.value),
            ]
            for cell in row:
                cell.setEditable(False)
            self.history_model.appendRow(row)

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
        rows = {idx.row() for idx in self.history_table.selectionModel().selectedRows()}
        if not rows:
            return
        saved_path_item = self.history_model.item(next(iter(rows)), 1)
        saved_path = saved_path_item.text() if saved_path_item else ""
        if not saved_path or saved_path == "—":
            QMessageBox.information(
                self, "No saved file", "This entry doesn't have a saved file location."
            )
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(saved_path).parent)))

    # ------------------------------------------------------------ helpers
    def _browse_folder(self, target_edit: QLineEdit) -> None:
        chosen = QFileDialog.getExistingDirectory(
            self, "Choose destination folder", target_edit.text()
        )
        if chosen:
            target_edit.setText(chosen)

    def _open_settings(self) -> None:
        dialog = SettingsDialog(self.settings, self)
        if dialog.exec():
            self.settings = dialog.result_settings()
            self.db.save_settings(self.settings)
            self.download_manager.set_concurrency(self.settings.concurrency)
            self.download_manager.retry_attempts = self.settings.retry_attempts
            self.download_manager.cookies_browser = self.settings.cookies_browser
            self.apply_theme(self.settings.theme)

    def apply_theme(self, theme: str) -> None:
        app = self._app()
        if app:
            app.setStyleSheet(stylesheet_for(theme))

    def _app(self):
        from PySide6.QtWidgets import QApplication

        return QApplication.instance()

    def _check_ffmpeg(self) -> None:
        # Reuses/refreshes the DownloadManager's cached detection rather than
        # probing separately -- avoids two redundant `ffmpeg -version`
        # subprocess spawns (one here, one in DownloadManager.__init__) on
        # every single startup.
        status = self.download_manager.refresh_ffmpeg_status()
        if status.found:
            self.status_bar.showMessage(f"FFmpeg detected: {status.version}", 5000)
        else:
            import sys

            instructions = install_instructions(sys.platform)
            QMessageBox.warning(
                self,
                "FFmpeg not found",
                "FFmpeg is required to merge video/audio streams and to extract audio. "
                "Downloads that need conversion will fail until it is installed.\n\n"
                f"{instructions}",
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
            f"(you have {result.installed_version}). Click 'Check for Updates' for details.",
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
