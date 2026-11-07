"""App icon, About window, drag-and-drop links and the queue-finished notice."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from PySide6.QtCore import QMimeData, QUrl

import ytdlmanager
from ytdlmanager.app import _default_settings
from ytdlmanager.db import Database
from ytdlmanager.ffmpeg_utils import FfmpegStatus
from ytdlmanager.ui.about_dialog import AboutDialog
from ytdlmanager.ui.icons import app_icon, render_app_icon
from ytdlmanager.ui.main_window import _dropped_link


@pytest.fixture
def window(qapp, tmp_db_path, tmp_path, monkeypatch):
    import ytdlmanager.download_manager as download_manager_module
    import ytdlmanager.ui.main_window as main_window_module

    monkeypatch.setattr(
        download_manager_module,
        "find_ffmpeg",
        lambda *a, **k: FfmpegStatus(found=True, path="/usr/bin/ffmpeg", version="fake"),
    )
    db = Database(tmp_db_path)
    settings = _default_settings()
    settings.default_download_dir = str(tmp_path)
    settings.check_for_updates = False
    win = main_window_module.MainWindow(db, settings)
    yield win
    db.close()


def test_app_icon_renders_at_every_size(qapp):
    pixmap = render_app_icon(64)
    assert not pixmap.isNull()
    image = pixmap.toImage()
    # Transparent corner (rounded square inset), opaque centre.
    assert image.pixelColor(1, 1).alpha() == 0
    assert image.pixelColor(32, 32).alpha() == 255
    assert len(app_icon().availableSizes()) >= 6


def test_about_dialog_reports_versions(qapp):
    dialog = AboutDialog(FfmpegStatus(True, "/x/ffmpeg", "ffmpeg version 9.0.1 Copyright"), None)
    details = dialog.details_text()
    assert f"YTDL Manager {ytdlmanager.__version__}" in details
    assert "FFmpeg: 9.0.1" in details
    assert "JavaScript runtime: not found" in details


def _mime(text: str | None = None, urls: list[str] | None = None) -> QMimeData:
    mime = QMimeData()
    if text is not None:
        mime.setText(text)
    if urls:
        mime.setUrls([QUrl(u) for u in urls])
    return mime


@pytest.mark.parametrize(
    ("mime_args", "expected"),
    [
        ({"urls": ["https://youtu.be/dQw4w9WgXcQ"]}, "https://youtu.be/dQw4w9WgXcQ"),
        ({"text": "  https://vimeo.com/76979871 "}, "https://vimeo.com/76979871"),
        ({"urls": ["file:///Users/me/video.mp4"]}, None),
        ({"text": "some words with https://x.com in them"}, None),
        ({"text": "not a link"}, None),
    ],
)
def test_dropped_link_accepts_only_a_single_web_link(qapp, mime_args, expected):
    assert _dropped_link(_mime(**mime_args)) == expected


def test_queue_finished_message_and_alert(window, monkeypatch):
    import ytdlmanager.ui.main_window as main_window_module

    alert = MagicMock()
    monkeypatch.setattr(main_window_module.QApplication, "alert", alert)
    monkeypatch.setattr(window, "isActiveWindow", lambda: False)

    window._on_queue_finished(3, 1)

    assert "3 completed, 1 failed" in window.status_bar.currentMessage()
    alert.assert_called_once()


def _loaded_video():
    from ytdlmanager.models import VideoInfo

    return VideoInfo(
        video_id="abc12345678", title="Q", url="https://www.youtube.com/watch?v=abc12345678",
        formats=[],
    )


def test_max_quality_and_tagging_reach_the_download_request(window):
    window.download_manager.start_item = MagicMock()
    window._on_video_ready(_loaded_video())
    window.video_quality_combo.setCurrentIndex(window.video_quality_combo.findData(720))

    window._add_video_to_queue()

    (item,) = window.download_manager.items()
    assert item.request.max_height == 720
    assert item.request.embed_metadata is True  # the setting's default


def test_best_available_means_no_cap(window):
    window.download_manager.start_item = MagicMock()
    window._on_video_ready(_loaded_video())
    window.video_quality_combo.setCurrentIndex(window.video_quality_combo.findData(0))

    window._add_video_to_queue()

    assert window.download_manager.items()[0].request.max_height is None


def test_window_remembers_its_page(qapp, tmp_db_path, tmp_path, monkeypatch):
    import ytdlmanager.download_manager as download_manager_module
    import ytdlmanager.ui.main_window as main_window_module

    monkeypatch.setattr(
        download_manager_module, "find_ffmpeg",
        lambda *a, **k: FfmpegStatus(found=True, path="/usr/bin/ffmpeg", version="fake"),
    )
    db = Database(tmp_db_path)
    settings = _default_settings()
    settings.default_download_dir = str(tmp_path)
    settings.check_for_updates = False
    try:
        first = main_window_module.MainWindow(db, settings)
        first._show_page(main_window_module.PAGE_HISTORY)
        first._save_window_state()

        second = main_window_module.MainWindow(db, settings)
        assert second.tabs.currentIndex() == main_window_module.PAGE_HISTORY
        # Stored beside the settings without disturbing them.
        assert db.load_settings(settings).concurrency == settings.concurrency
    finally:
        db.close()


def test_menu_bar_has_settings_quit_and_page_shortcuts(window):
    from PySide6.QtGui import QAction

    actions = {a.text(): a for a in window.findChildren(QAction) if a.text()}
    assert actions["Settings…"].menuRole() == QAction.MenuRole.PreferencesRole
    assert not actions["Settings…"].shortcut().isEmpty()
    assert actions["Quit YTDL Manager"].menuRole() == QAction.MenuRole.QuitRole
    assert actions["Queue"].shortcut().toString() in ("Ctrl+3", "⌘3")

    actions["History"].trigger()
    assert window.tabs.currentIndex() == 3


def test_pause_all_button_reports_when_nothing_is_active(window):
    window._queue_pause_all()
    assert "Nothing is downloading" in window.status_bar.currentMessage()


def test_settings_dialog_round_trips_new_options(qapp):
    from ytdlmanager.ui.settings_dialog import SettingsDialog

    settings = _default_settings()
    settings.default_max_height = 1080
    settings.rate_limit_bps = 2 * 1024**2
    settings.embed_metadata = False
    result = SettingsDialog(settings).result_settings()

    assert result.default_max_height == 1080
    assert result.rate_limit_bps == 2 * 1024**2
    assert result.embed_metadata is False


def test_new_settings_persist(tmp_db_path):
    settings = _default_settings()
    settings.default_max_height = 480
    settings.rate_limit_bps = 1024**2
    settings.embed_metadata = False
    db = Database(tmp_db_path)
    try:
        db.save_settings(settings)
        loaded = db.load_settings(_default_settings())
    finally:
        db.close()
    assert (loaded.default_max_height, loaded.rate_limit_bps, loaded.embed_metadata) == (
        480, 1024**2, False,
    )
