"""Regression test for a real crash: PySide6's QVariant marshalling silently
downgrades a `str, Enum` value (DownloadMode, DuplicatePolicy) stored as a
QComboBox's userData back to a plain `str` when read via `currentData()`.

That meant every combo box populated with `addItem(label, DownloadMode.X)`
handed back a plain string instead of the enum, and the first place that
called `.value` on it (`db._request_to_json`, reached by clicking "Download")
raised an uncaught `AttributeError` -- the button appeared to do nothing.

These tests exercise the UI code paths that build a `DownloadRequest` from
combo box selections and persist it, without touching the network, so a
regression here fails fast instead of surfacing as "the download button
doesn't do anything."
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from ytdlmanager.app import _default_settings
from ytdlmanager.db import Database
from ytdlmanager.ffmpeg_utils import FfmpegStatus
from ytdlmanager.models import DownloadMode, DuplicatePolicy, MediaKind, VideoInfo
from ytdlmanager.ui.settings_dialog import SettingsDialog


@pytest.fixture
def make_main_window(qapp, monkeypatch):
    """Build a MainWindow without depending on FFmpeg actually being
    installed on the machine running the tests -- `_check_ffmpeg()` would
    otherwise pop a blocking modal dialog and hang a headless test run.
    """
    import ytdlmanager.download_manager as download_manager_module
    import ytdlmanager.ui.main_window as main_window_module

    fake_status = FfmpegStatus(found=True, path="/usr/bin/ffmpeg", version="fake")
    monkeypatch.setattr(download_manager_module, "find_ffmpeg", lambda *a, **k: fake_status)
    return main_window_module.MainWindow


def _video(**overrides) -> VideoInfo:
    base = dict(
        video_id="abc12345678",
        title="Regression Test Video",
        url="https://www.youtube.com/watch?v=abc12345678",
        channel="Someone",
        duration_seconds=120,
        is_available=True,
        formats=[],
    )
    base.update(overrides)
    return VideoInfo(**base)


def test_video_mode_combo_survives_roundtrip_into_a_persisted_request(
    make_main_window, tmp_db_path, tmp_path
):
    db = Database(tmp_db_path)
    try:
        settings = _default_settings()
        settings.default_download_dir = str(tmp_path)
        window = make_main_window(db, settings)
        # Don't actually hit the network: replace start_item with a no-op,
        # same as clicking "Download" would trigger, minus the real transfer.
        window.download_manager.start_item = MagicMock()

        window._on_video_ready(_video())
        window.video_dest_edit.setText(str(tmp_path))
        window._add_video_to_queue()

        items = window.download_manager.items()
        assert len(items) == 1
        assert items[0].request.mode == DownloadMode.VIDEO_WITH_AUDIO
        assert isinstance(items[0].request.mode, DownloadMode)

        # The original crash happened one layer down, inside JSON persistence
        # (DownloadMode.value / DuplicatePolicy.value) -- confirm it survived.
        reloaded = db.list_queue()
        assert reloaded[0].request.mode == DownloadMode.VIDEO_WITH_AUDIO
        window.download_manager.start_item.assert_called_once()
    finally:
        db.close()


def test_playlist_mode_and_duplicate_combos_survive_roundtrip(
    make_main_window, tmp_db_path, tmp_path
):
    from ytdlmanager.models import PlaylistInfo

    db = Database(tmp_db_path)
    try:
        settings = _default_settings()
        settings.default_download_dir = str(tmp_path)
        window = make_main_window(db, settings)
        window.download_manager.start_item = MagicMock()

        playlist = PlaylistInfo(
            playlist_id="PLxyz",
            title="Regression Playlist",
            url="https://www.youtube.com/playlist?list=PLxyz",
            items=[_video(), _video(video_id="def45678901", title="Second")],
        )
        window._on_playlist_ready(playlist)
        window.playlist_dest_edit.setText(str(tmp_path))

        # Pick a specific container format for the whole batch, e.g. "3gp".
        format_idx = window.playlist_format_combo.findData("3gp")
        assert format_idx != -1, "3gp should be offered for video mode"
        window.playlist_format_combo.setCurrentIndex(format_idx)

        window._add_playlist_selection_to_queue()

        items = window.download_manager.items()
        assert len(items) == 2
        for item in items:
            assert isinstance(item.request.mode, DownloadMode)
            assert isinstance(item.request.duplicate_policy, DuplicatePolicy)
            assert item.request.container_ext == "3gp"

        reloaded = db.list_queue()
        assert all(isinstance(i.request.duplicate_policy, DuplicatePolicy) for i in reloaded)
        assert all(i.request.container_ext == "3gp" for i in reloaded)
    finally:
        db.close()


def test_playlist_format_options_change_with_mode(make_main_window, tmp_db_path, tmp_path):
    from ytdlmanager.models import DownloadMode as DM

    db = Database(tmp_db_path)
    try:
        settings = _default_settings()
        window = make_main_window(db, settings)

        video_idx = window.playlist_mode_combo.findData(DM.VIDEO_WITH_AUDIO)
        window.playlist_mode_combo.setCurrentIndex(video_idx)
        video_choices = {
            window.playlist_format_combo.itemData(i)
            for i in range(window.playlist_format_combo.count())
        }
        assert "3gp" in video_choices
        assert "mp3" not in video_choices

        audio_idx = window.playlist_mode_combo.findData(DM.AUDIO_ONLY)
        window.playlist_mode_combo.setCurrentIndex(audio_idx)
        audio_choices = {
            window.playlist_format_combo.itemData(i)
            for i in range(window.playlist_format_combo.count())
        }
        assert "mp3" in audio_choices
        assert "3gp" not in audio_choices
    finally:
        db.close()


def test_settings_dialog_duplicate_combo_survives_roundtrip(qapp):
    settings = _default_settings()
    dialog = SettingsDialog(settings)
    idx = dialog.duplicate_combo.findData(DuplicatePolicy.SKIP)
    dialog.duplicate_combo.setCurrentIndex(idx)

    result = dialog.result_settings()

    assert result.duplicate_policy == DuplicatePolicy.SKIP
    assert isinstance(result.duplicate_policy, DuplicatePolicy)


def test_settings_dialog_exposes_preserve_playlist_order(qapp):
    # Regression: this AppSettings field was persisted but had no control in
    # the Settings dialog to ever change it -- the default was permanently
    # stuck at whatever _default_settings() hardcoded.
    settings = _default_settings()
    settings.preserve_playlist_order = True
    dialog = SettingsDialog(settings)
    assert dialog.preserve_order_check.isChecked() is True

    dialog.preserve_order_check.setChecked(False)
    result = dialog.result_settings()
    assert result.preserve_playlist_order is False


def test_settings_dialog_exposes_check_for_updates(qapp):
    settings = _default_settings()
    settings.check_for_updates = True
    dialog = SettingsDialog(settings)
    assert dialog.check_updates_check.isChecked() is True

    dialog.check_updates_check.setChecked(False)
    result = dialog.result_settings()
    assert result.check_for_updates is False


def test_classify_url_still_used_for_video_kind_labeling():
    # Cheap sanity check that MediaKind (also a str, Enum) compares correctly
    # wherever it's used directly rather than round-tripped through Qt.
    assert MediaKind.VIDEO_URL == "video"
