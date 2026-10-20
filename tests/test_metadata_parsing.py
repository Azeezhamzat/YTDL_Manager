from __future__ import annotations

import json
from pathlib import Path

from ytdlmanager.models import DownloadMode
from ytdlmanager.ytdlp_backend import (
    build_format_selector,
    playlist_info_from_dict,
    video_info_from_dict,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def test_video_info_from_dict_basic_fields():
    video = video_info_from_dict(_load("sample_video.json"))
    assert video.video_id == "dQw4w9WgXcQ"
    assert video.title == "Sample Video Title"
    assert video.channel == "Sample Channel"
    assert video.duration_seconds == 212
    assert video.is_available is True
    assert video.thumbnail_url.startswith("https://")


def test_video_info_formats_parsed():
    video = video_info_from_dict(_load("sample_video.json"))
    assert len(video.formats) == 3

    video_only = next(f for f in video.formats if f.format_id == "137")
    assert video_only.is_video is True
    assert video_only.is_audio_only is False
    assert video_only.resolution == "1920x1080"

    audio_only = next(f for f in video.formats if f.format_id == "140")
    assert audio_only.is_audio_only is True
    assert audio_only.is_video is False
    assert audio_only.abr == 128.0


def test_video_info_unavailable_flag():
    data = _load("sample_video.json")
    data["availability"] = "private"
    video = video_info_from_dict(data)
    assert video.is_available is False
    assert video.unavailable_reason == "private"


def test_playlist_info_from_dict_parses_entries_and_skips_none():
    playlist = playlist_info_from_dict(_load("sample_playlist.json"))
    assert playlist.playlist_id == "PLsampleplaylist12345"
    assert playlist.title == "Sample Playlist"
    # The `null` entry (a deleted/unlisted item yt-dlp reports as None) is skipped.
    assert len(playlist.items) == 3
    assert [v.title for v in playlist.items] == ["Episode 1", "Episode 2 (Private)", "Episode 4"]


def test_playlist_info_marks_private_item_unavailable():
    playlist = playlist_info_from_dict(_load("sample_playlist.json"))
    private_item = next(v for v in playlist.items if v.video_id == "vid002")
    assert private_item.is_available is False


def test_playlist_info_assigns_sequential_index_when_missing():
    playlist = playlist_info_from_dict(_load("sample_playlist.json"))
    assert [v.playlist_index for v in playlist.items] == [1, 2, 4]


def test_build_format_selector_audio_only():
    assert build_format_selector(DownloadMode.AUDIO_ONLY, None) == "bestaudio/best"


def test_build_format_selector_best_quality():
    assert build_format_selector(DownloadMode.BEST_QUALITY, None) == "bestvideo*+bestaudio/best"


def test_build_format_selector_specific_format_requires_id():
    import pytest

    with pytest.raises(ValueError):
        build_format_selector(DownloadMode.SPECIFIC_FORMAT, None)


def test_build_format_selector_specific_format():
    selector = build_format_selector(DownloadMode.SPECIFIC_FORMAT, "137")
    assert "137" in selector


def test_build_format_selector_container_ext_none_is_unchanged():
    selector = build_format_selector(DownloadMode.BEST_QUALITY, None, None)
    assert selector == "bestvideo*+bestaudio/best"


def test_build_format_selector_merge_capable_container_ext():
    selector = build_format_selector(DownloadMode.VIDEO_WITH_AUDIO, None, "mp4")
    assert selector == (
        "bestvideo*[ext=mp4]+bestaudio[ext=mp4]/best[ext=mp4]/bestvideo*+bestaudio/best"
    )


def test_build_format_selector_progressive_only_container_ext():
    # 3gp is a legacy pre-muxed format -- no bestvideo+bestaudio merge attempt.
    selector = build_format_selector(DownloadMode.BEST_QUALITY, None, "3gp")
    assert selector == "best[ext=3gp]/bestvideo*+bestaudio/best"


def test_build_format_selector_container_ext_ignored_for_audio_only():
    # Audio container choice is applied via the FFmpegExtractAudio
    # postprocessor, not the source format selector.
    assert build_format_selector(DownloadMode.AUDIO_ONLY, None, "mp3") == "bestaudio/best"


def test_build_ydl_opts_uses_requested_audio_codec():
    from ytdlmanager.models import DownloadRequest
    from ytdlmanager.ytdlp_backend import build_ydl_opts

    request = DownloadRequest(
        url="https://www.youtube.com/watch?v=x",
        title="t",
        destination_dir="/tmp",
        mode=DownloadMode.AUDIO_ONLY,
        container_ext="mp3",
    )
    opts = build_ydl_opts(request, output_template="/tmp/t.%(ext)s")
    assert opts["postprocessors"][0]["preferredcodec"] == "mp3"


def test_build_ydl_opts_falls_back_to_m4a_for_unsupported_audio_codec():
    from ytdlmanager.models import DownloadRequest
    from ytdlmanager.ytdlp_backend import build_ydl_opts

    request = DownloadRequest(
        url="https://www.youtube.com/watch?v=x",
        title="t",
        destination_dir="/tmp",
        mode=DownloadMode.AUDIO_ONLY,
        container_ext="not-a-real-codec",
    )
    opts = build_ydl_opts(request, output_template="/tmp/t.%(ext)s")
    assert opts["postprocessors"][0]["preferredcodec"] == "m4a"


def test_build_ydl_opts_sets_merge_output_format_for_merge_capable_ext():
    from ytdlmanager.models import DownloadRequest
    from ytdlmanager.ytdlp_backend import build_ydl_opts

    request = DownloadRequest(
        url="https://www.youtube.com/watch?v=x",
        title="t",
        destination_dir="/tmp",
        mode=DownloadMode.BEST_QUALITY,
        container_ext="webm",
    )
    opts = build_ydl_opts(request, output_template="/tmp/t.%(ext)s")
    assert opts["merge_output_format"] == "webm"


# -- extract_info's noplaylist handling -----------------------------------
# Regression for a real bug: a /watch?v=X&list=Y URL (the URL shape YouTube
# shows for any video opened from within a playlist -- extremely common)
# was classified as "just this video" by url_utils.classify_url(), but the
# metadata fetch ignored that and asked yt-dlp for the URL with
# noplaylist=False, which makes yt-dlp prefer the *playlist* over the single
# video whenever a URL carries both ids. The app would silently display the
# wrong metadata (the playlist's) for what the user asked to be a single
# video. Verified here by capturing the opts passed to yt_dlp.YoutubeDL
# without touching the network.


class _FakeYoutubeDL:
    last_opts: dict | None = None

    def __init__(self, opts):
        type(self).last_opts = opts

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def extract_info(self, url, download=False):
        return {"id": "fake", "title": "fake"}


def test_extract_info_forces_noplaylist_for_single_video_fetch(monkeypatch):
    import yt_dlp

    from ytdlmanager.ytdlp_backend import extract_info

    monkeypatch.setattr(yt_dlp, "YoutubeDL", _FakeYoutubeDL)

    extract_info(
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PLabc123",
        download=False,
        flat_playlist=False,
    )

    assert _FakeYoutubeDL.last_opts["noplaylist"] is True


def test_extract_info_allows_playlist_for_playlist_fetch(monkeypatch):
    import yt_dlp

    from ytdlmanager.ytdlp_backend import extract_info

    monkeypatch.setattr(yt_dlp, "YoutubeDL", _FakeYoutubeDL)

    extract_info(
        "https://www.youtube.com/playlist?list=PLabc123",
        download=False,
        flat_playlist=True,
    )

    assert _FakeYoutubeDL.last_opts["noplaylist"] is False
