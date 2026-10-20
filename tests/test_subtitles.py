"""Subtitle downloads: option building, persistence (including rows saved
before this feature existed), duplicate-path reservation, and UI wiring."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from ytdlmanager import ytdlp_backend
from ytdlmanager.app import _default_settings
from ytdlmanager.db import Database, _request_from_json, _request_to_json
from ytdlmanager.download_manager import expected_final_path
from ytdlmanager.ffmpeg_utils import FfmpegStatus
from ytdlmanager.models import DownloadMode, DownloadRequest, QueueItem, VideoInfo


def _request(**overrides) -> DownloadRequest:
    base = dict(
        url="https://www.youtube.com/watch?v=abc12345678",
        title="My Video",
        destination_dir="/tmp/out",
        mode=DownloadMode.VIDEO_WITH_AUDIO,
    )
    base.update(overrides)
    return DownloadRequest(**base)


# -- language parsing ------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        (None, []),
        ("", []),
        ("en", ["en"]),
        ("en, fr", ["en", "fr"]),
        ("en fr  de", ["en", "fr", "de"]),
        ("all", ["all"]),
        ("en-US,pt-BR", ["en-US", "pt-BR"]),
    ],
)
def test_parse_subtitle_langs(text, expected):
    assert ytdlp_backend.parse_subtitle_langs(text) == expected


@pytest.mark.parametrize("bad", ["en;rm", "e n(", "fr/de", "$(x)"])
def test_parse_subtitle_langs_rejects_junk(bad):
    with pytest.raises(ValueError):
        ytdlp_backend.parse_subtitle_langs(bad)


# -- yt-dlp options ----------------------------------------------------------


def test_default_request_has_no_subtitle_options():
    opts = ytdlp_backend.build_ydl_opts(_request(), output_template="x")
    assert "writesubtitles" not in opts
    assert "skip_download" not in opts
    assert opts["merge_output_format"] == "mp4"


def test_subtitles_alongside_keeps_normal_download():
    opts = ytdlp_backend.build_ydl_opts(_request(subtitle_langs="en, fr"), output_template="x")
    assert opts["writesubtitles"] is True
    assert opts["writeautomaticsub"] is True
    assert opts["subtitleslangs"] == ["en", "fr"]
    assert "skip_download" not in opts
    assert opts["merge_output_format"] == "mp4"


def test_all_languages_does_not_pull_auto_translations():
    opts = ytdlp_backend.build_ydl_opts(_request(subtitle_langs="all"), output_template="x")
    assert opts["writeautomaticsub"] is False


def test_subtitles_only_skips_the_media_download():
    opts = ytdlp_backend.build_ydl_opts(
        _request(subtitle_langs="en", subtitles_only=True), output_template="x"
    )
    assert opts["skip_download"] is True
    assert "merge_output_format" not in opts
    keys = [pp["key"] for pp in opts["postprocessors"]]
    assert keys == ["FFmpegSubtitlesConvertor"]


def test_audio_only_with_subtitles_keeps_both_postprocessors():
    opts = ytdlp_backend.build_ydl_opts(
        _request(mode=DownloadMode.AUDIO_ONLY, subtitle_langs="en"), output_template="x"
    )
    keys = [pp["key"] for pp in opts["postprocessors"]]
    assert "FFmpegExtractAudio" in keys
    assert "FFmpegSubtitlesConvertor" in keys


def test_subtitles_only_without_languages_is_rejected():
    with pytest.raises(ValueError):
        ytdlp_backend.build_ydl_opts(_request(subtitles_only=True), output_template="x")


# -- persistence ---------------------------------------------------------------


def test_request_roundtrip_preserves_subtitle_fields():
    original = _request(subtitle_langs="en, fr", subtitles_only=True)
    restored = _request_from_json(_request_to_json(original))
    assert restored.subtitle_langs == "en, fr"
    assert restored.subtitles_only is True


def test_request_saved_before_this_feature_still_loads():
    old = json.dumps(
        {
            "url": "https://www.youtube.com/watch?v=abc12345678",
            "title": "Old",
            "destination_dir": "/tmp/out",
            "mode": "video_audio",
            "format_id": None,
            "playlist_id": None,
            "playlist_index": None,
            "duplicate_policy": "rename",
            "preserve_playlist_order": False,
            "container_ext": None,
        }
    )
    restored = _request_from_json(old)
    assert restored.subtitle_langs is None
    assert restored.subtitles_only is False


def test_queue_item_with_subtitles_survives_the_database(tmp_db_path):
    db = Database(tmp_db_path)
    try:
        db.add_queue_item(
            QueueItem(id=None, request=_request(subtitle_langs="en", subtitles_only=True))
        )
        (item,) = db.list_queue()
        assert item.request.subtitle_langs == "en"
        assert item.request.subtitles_only is True
    finally:
        db.close()


# -- duplicate-path reservation ---------------------------------------------------


def test_subtitles_only_reserves_an_srt_path():
    item = QueueItem(id=1, request=_request(subtitle_langs="en", subtitles_only=True))
    assert expected_final_path(item).name == "My Video.srt"


def test_normal_item_still_reserves_media_path():
    item = QueueItem(id=1, request=_request(subtitle_langs="en"))
    assert expected_final_path(item).name == "My Video.mp4"


# -- UI wiring -----------------------------------------------------------------------


@pytest.fixture
def window(qapp, monkeypatch, tmp_db_path, tmp_path):
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
    win.download_manager.start_item = MagicMock()
    win._on_video_ready(
        VideoInfo(
            video_id="abc12345678",
            title="T",
            url="https://www.youtube.com/watch?v=abc12345678",
            channel="C",
            duration_seconds=5,
            is_available=True,
            formats=[],
        )
    )
    yield win
    db.close()


def test_default_ui_selection_adds_no_subtitles(window):
    window._add_video_to_queue()
    (item,) = window.download_manager.items()
    assert item.request.subtitle_langs is None
    assert item.request.subtitles_only is False


def test_ui_subtitles_alongside(window):
    window.video_subtitle_combo.setCurrentIndex(window.video_subtitle_combo.findData("with"))
    window.video_subtitle_langs_edit.setText("en, fr")
    window._add_video_to_queue()
    (item,) = window.download_manager.items()
    assert item.request.subtitle_langs == "en, fr"
    assert item.request.subtitles_only is False


def test_ui_subtitles_only_disables_media_options(window):
    assert not window.video_subtitle_langs_edit.isEnabled()
    window.video_subtitle_combo.setCurrentIndex(window.video_subtitle_combo.findData("only"))
    assert window.video_subtitle_langs_edit.isEnabled()
    assert not window.video_mode_combo.isEnabled()
    window._add_video_to_queue()
    (item,) = window.download_manager.items()
    assert item.request.subtitles_only is True

    window.video_subtitle_combo.setCurrentIndex(window.video_subtitle_combo.findData("none"))
    assert window.video_mode_combo.isEnabled()


def test_ui_empty_language_field_warns_and_enqueues_nothing(window, monkeypatch):
    import ytdlmanager.ui.main_window as main_window_module

    warned = []
    monkeypatch.setattr(
        main_window_module.QMessageBox, "warning", staticmethod(lambda *a, **k: warned.append(a))
    )
    window.video_subtitle_combo.setCurrentIndex(window.video_subtitle_combo.findData("with"))
    window.video_subtitle_langs_edit.setText("  ")
    window._add_video_to_queue()
    assert warned
    assert window.download_manager.items() == []


def test_ui_playlist_subtitles_apply_to_every_item(window):
    from ytdlmanager.models import PlaylistInfo

    def vid(i):
        return VideoInfo(
            video_id=f"vid{i:08d}",
            title=f"V{i}",
            url=f"https://www.youtube.com/watch?v=vid{i:08d}",
            channel="C",
            duration_seconds=5,
            is_available=True,
            formats=[],
        )

    window._on_playlist_ready(
        PlaylistInfo(
            playlist_id="PLx",
            title="P",
            url="https://www.youtube.com/playlist?list=PLx",
            items=[vid(1), vid(2)],
        )
    )
    window.playlist_subtitle_combo.setCurrentIndex(window.playlist_subtitle_combo.findData("only"))
    assert not window.playlist_mode_combo.isEnabled()
    window.playlist_subtitle_langs_edit.setText("en")
    window._add_playlist_selection_to_queue()
    items = window.download_manager.items()
    assert len(items) == 2
    assert all(i.request.subtitles_only and i.request.subtitle_langs == "en" for i in items)
