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
