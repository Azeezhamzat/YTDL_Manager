"""YouTube's "Sign in to confirm you're not a bot" handling, the opt-in
browser-cookie setting (YouTube URLs only), and the Deno JavaScript runtime
that yt-dlp needs for YouTube."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
import yt_dlp
from yt_dlp.cookies import CookieLoadError

from ytdlmanager import js_runtime, ytdlp_backend
from ytdlmanager.db import Database
from ytdlmanager.models import AppSettings, DownloadMode, DownloadRequest, DuplicatePolicy
from ytdlmanager.ui.main_window import _friendly_error

_BOT_MESSAGE = (
    "ERROR: [youtube] 2RDElRfEVV0: Sign in to confirm you’re not a bot. Use "
    "--cookies-from-browser or --cookies for the authentication. See  https://github.com/"
    "yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pass-cookies-to-yt-dlp  for how to manually pass cookies."
)


def _request(url: str) -> DownloadRequest:
    return DownloadRequest(
        url=url, title="t", destination_dir="/tmp", mode=DownloadMode.VIDEO_WITH_AUDIO
    )


# -- classification ----------------------------------------------------------


@pytest.mark.parametrize("apostrophe", ["’", "'"])
def test_bot_check_is_detected_and_never_retried(apostrophe):
    message = _BOT_MESSAGE.replace("’", apostrophe)
    assert ytdlp_backend.is_bot_check_error(message)
    assert ytdlp_backend.classify_error(message) is True


def test_ordinary_errors_are_not_bot_checks():
    assert not ytdlp_backend.is_bot_check_error("HTTP Error 503: Service Unavailable")


# -- yt-dlp options ------------------------------------------------------------


def test_no_cookies_by_default():
    opts = ytdlp_backend.build_ydl_opts(
        _request("https://www.youtube.com/watch?v=dQw4w9WgXcQ"), output_template="x.%(ext)s"
    )
    assert opts["cookiesfrombrowser"] is None


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://youtu.be/dQw4w9WgXcQ",
        "https://music.youtube.com/watch?v=dQw4w9WgXcQ",
    ],
)
def test_cookies_are_used_for_youtube_when_opted_in(url):
    opts = ytdlp_backend.build_ydl_opts(
        _request(url), output_template="x.%(ext)s", cookies_browser="chrome"
    )
    assert opts["cookiesfrombrowser"] == ("chrome",)


@pytest.mark.parametrize(
    "url",
    [
        "https://www.instagram.com/p/abc123/",
        "https://vimeo.com/76979871",
        "https://x.com/someone/status/123",
    ],
)
def test_cookies_are_never_sent_to_other_platforms(url):
    opts = ytdlp_backend.build_ydl_opts(
        _request(url), output_template="x.%(ext)s", cookies_browser="chrome"
    )
    assert opts["cookiesfrombrowser"] is None


def test_js_runtime_path_is_passed_to_yt_dlp():
    opts = ytdlp_backend.build_ydl_opts(
        _request("https://youtu.be/dQw4w9WgXcQ"),
        output_template="x.%(ext)s",
        js_runtime_path="/opt/deno",
    )
    assert opts["js_runtimes"] == {"deno": {"path": "/opt/deno"}}


def test_extract_info_passes_cookies_and_runtime():
    captured = {}

    class FakeYDL:
        def __init__(self, opts):
            captured.update(opts)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def extract_info(self, url, download):
            return {"id": "x"}

    with patch.object(yt_dlp, "YoutubeDL", FakeYDL):
        ytdlp_backend.extract_info(
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            cookies_browser="firefox",
            js_runtime_path="/opt/deno",
        )
    assert captured["cookiesfrombrowser"] == ("firefox",)
    assert captured["js_runtimes"] == {"deno": {"path": "/opt/deno"}}


def test_unreadable_browser_cookies_become_a_permanent_clear_error():
    class FakeYDL:
        def __init__(self, opts):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def extract_info(self, url, download):
            try:
                raise PermissionError("Keychain access denied")
            except PermissionError as cause:
                raise CookieLoadError("failed to load cookies") from cause

    with patch.object(yt_dlp, "YoutubeDL", FakeYDL):
        with pytest.raises(ytdlp_backend.DownloadError) as info:
            ytdlp_backend.extract_info(
                "https://www.youtube.com/watch?v=dQw4w9WgXcQ", cookies_browser="chrome"
            )
    assert info.value.is_permanent
    assert str(info.value).startswith(ytdlp_backend.COOKIE_LOAD_ERROR_PREFIX)
    assert "Keychain access denied" in str(info.value)


# -- settings persistence --------------------------------------------------------


def test_cookies_browser_setting_round_trips(tmp_db_path):
    defaults = AppSettings(
        default_download_dir="/tmp", concurrency=3, duplicate_policy=DuplicatePolicy.RENAME
    )
    db = Database(tmp_db_path)
    try:
        db.save_settings(defaults)
        assert db.load_settings(defaults).cookies_browser is None

        db.save_settings(AppSettings(**{**defaults.__dict__, "cookies_browser": "safari"}))
        assert db.load_settings(defaults).cookies_browser == "safari"
    finally:
        db.close()


# -- user-facing messages ---------------------------------------------------------


def test_bot_check_message_points_to_the_setting():
    text = _friendly_error(_BOT_MESSAGE)
    assert "--cookies-from-browser" not in text  # CLI advice is useless in a GUI
    assert "Settings" in text


def test_bot_check_message_when_sign_in_is_already_on():
    text = _friendly_error(_BOT_MESSAGE, cookies_browser="chrome")
    assert "signed in to YouTube in Chrome" in text


def test_server_unavailable_is_not_reported_as_removed_content():
    text = _friendly_error("ERROR: Unable to download webpage: HTTP Error 503: Service Unavailable")
    assert "removed" not in text


# -- Deno detection -----------------------------------------------------------------


def test_find_deno_prefers_the_copy_bundled_in_a_frozen_build(tmp_path, monkeypatch):
    bundled = tmp_path / js_runtime.BUNDLED_DENO_DIR / ("deno.exe" if os.name == "nt" else "deno")
    bundled.parent.mkdir()
    bundled.write_text("")
    bundled.chmod(0o755)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)

    assert js_runtime.find_deno() == str(bundled)


def test_find_deno_returns_none_when_nothing_is_installed(monkeypatch):
    monkeypatch.setattr(js_runtime, "_bundled_deno", lambda: None)
    monkeypatch.setattr(js_runtime, "_pip_installed_deno", lambda: None)
    monkeypatch.setattr(js_runtime.shutil, "which", lambda _name: None)
    monkeypatch.setattr(js_runtime, "_COMMON_DENO_LOCATIONS", {})

    assert js_runtime.find_deno() is None


# -- Linux installer --------------------------------------------------------------


def test_linux_installer_never_targets_the_app_data_folder():
    # install_linux.sh `rm -rf`s its INSTALL_DIR on every run; if that were the
    # data folder (config._linux_data_home), reinstalling would erase the
    # user's settings, history and queue.
    script = (Path(__file__).parents[1] / "packaging" / "install_linux.sh").read_text()
    match = re.search(r'^INSTALL_DIR="([^"]+)"', script, re.MULTILINE)
    assert match is not None
    install_dir = match.group(1).replace("$HOME", "~")
    assert install_dir.rstrip("/") != "~/.local/share/ytdlmanager"
