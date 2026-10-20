from __future__ import annotations

import pytest

from ytdlmanager.models import MediaKind, Platform
from ytdlmanager.url_utils import (
    classify_url,
    detect_platform,
    extract_playlist_id,
    extract_video_id,
    is_valid_url,
    is_valid_youtube_url,
)


@pytest.mark.parametrize(
    "url,expected_id",
    [
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://youtube.com/watch?v=dQw4w9WgXcQ&t=30s", "dQw4w9WgXcQ"),
        ("https://youtu.be/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://youtu.be/dQw4w9WgXcQ?t=5", "dQw4w9WgXcQ"),
        ("https://m.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://www.youtube.com/shorts/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://www.youtube.com/embed/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ],
)
def test_extract_video_id_valid(url, expected_id):
    assert extract_video_id(url) == expected_id


@pytest.mark.parametrize(
    "url",
    [
        "",
        "not a url",
        "https://example.com/watch?v=dQw4w9WgXcQ",
        "https://www.youtube.com/watch?v=short",
        "https://www.youtube.com/",
        "ftp://youtube.com/watch?v=dQw4w9WgXcQ",
    ],
)
def test_extract_video_id_invalid(url):
    assert extract_video_id(url) is None


def test_extract_playlist_id():
    url = "https://www.youtube.com/playlist?list=PLabcdefghijklmnopqrstuvwx"
    assert extract_playlist_id(url) == "PLabcdefghijklmnopqrstuvwx"


def test_extract_playlist_id_from_watch_url():
    url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PLabc123"
    assert extract_playlist_id(url) == "PLabc123"


def test_extract_playlist_id_none_for_plain_video():
    assert extract_playlist_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ") is None


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", MediaKind.VIDEO_URL),
        ("https://www.youtube.com/playlist?list=PLabc123", MediaKind.PLAYLIST_URL),
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PLabc123", MediaKind.VIDEO_URL),
        ("not a url at all", MediaKind.INVALID),
        ("", MediaKind.INVALID),
        # Not (yet) a supported platform -- must stay INVALID, not silently
        # match some other site's pattern.
        ("https://dailymotion.com/video/x123abc", MediaKind.INVALID),
    ],
)
def test_classify_url(url, expected):
    assert classify_url(url) == expected


def test_is_valid_youtube_url():
    assert is_valid_youtube_url("https://youtu.be/dQw4w9WgXcQ") is True
    assert is_valid_youtube_url("garbage") is False


def test_is_valid_url_generalizes_across_platforms():
    assert is_valid_url("https://youtu.be/dQw4w9WgXcQ") is True
    assert is_valid_url("https://vimeo.com/12345") is True
    assert is_valid_url("garbage") is False


# -- platform detection ---------------------------------------------------


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", Platform.YOUTUBE),
        ("https://youtu.be/dQw4w9WgXcQ", Platform.YOUTUBE),
        ("https://www.tiktok.com/@someone/video/1234567890123456789", Platform.TIKTOK),
        ("https://x.com/someone/status/1234567890123456789", Platform.TWITTER),
        ("https://twitter.com/someone/status/1234567890123456789", Platform.TWITTER),
        ("https://www.instagram.com/p/AbCdEfGhIjK/", Platform.INSTAGRAM),
        ("https://www.facebook.com/watch/?v=1234567890", Platform.FACEBOOK),
        ("https://vimeo.com/12345", Platform.VIMEO),
        ("https://dailymotion.com/video/x123abc", Platform.UNKNOWN),
        ("not a url", Platform.UNKNOWN),
    ],
)
def test_detect_platform(url, expected):
    assert detect_platform(url) == expected


# -- TikTok -----------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://www.tiktok.com/@someone/video/7123456789012345678",
        "https://tiktok.com/@some.one_123/video/7123456789012345678",
        "https://vm.tiktok.com/ZMabcdefg/",
        "https://www.tiktok.com/t/ZTabcdefg/",
    ],
)
def test_tiktok_video_urls_classify_as_video(url):
    assert classify_url(url) == MediaKind.VIDEO_URL


def test_tiktok_profile_url_classifies_as_playlist():
    assert classify_url("https://www.tiktok.com/@someone") == MediaKind.PLAYLIST_URL
    assert classify_url("https://www.tiktok.com/@someone/") == MediaKind.PLAYLIST_URL


def test_tiktok_bare_domain_is_invalid():
    assert classify_url("https://www.tiktok.com/") == MediaKind.INVALID


# -- Twitter/X --------------------------------------------------------------


def test_twitter_status_url_classifies_as_video():
    assert classify_url("https://x.com/someone/status/1234567890123456789") == MediaKind.VIDEO_URL
    assert (
        classify_url("https://twitter.com/someone/status/1234567890123456789")
        == MediaKind.VIDEO_URL
    )


def test_twitter_profile_url_classifies_as_playlist():
    assert classify_url("https://x.com/someone") == MediaKind.PLAYLIST_URL


@pytest.mark.parametrize(
    "url",
    [
        "https://x.com/home",
        "https://x.com/explore",
        "https://x.com/notifications",
        "https://x.com/i/bookmarks",
    ],
)
def test_twitter_reserved_paths_are_not_treated_as_profiles(url):
    assert classify_url(url) == MediaKind.INVALID


# -- Instagram ---------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://www.instagram.com/p/AbCdEfGhIjK/",
        "https://www.instagram.com/reel/AbCdEfGhIjK/",
        "https://www.instagram.com/reels/AbCdEfGhIjK/",
        "https://www.instagram.com/tv/AbCdEfGhIjK/",
    ],
)
def test_instagram_post_urls_classify_as_video(url):
    assert classify_url(url) == MediaKind.VIDEO_URL


def test_instagram_profile_url_classifies_as_playlist():
    assert classify_url("https://www.instagram.com/someone/") == MediaKind.PLAYLIST_URL


def test_instagram_reserved_paths_are_not_treated_as_profiles():
    assert classify_url("https://www.instagram.com/explore/") == MediaKind.INVALID
    assert classify_url("https://www.instagram.com/accounts/") == MediaKind.INVALID


# -- Facebook -----------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://www.facebook.com/watch/?v=1234567890",
        "https://www.facebook.com/someone/videos/1234567890/",
        "https://fb.watch/AbCdEfGhIj/",
    ],
)
def test_facebook_video_urls_classify_as_video(url):
    assert classify_url(url) == MediaKind.VIDEO_URL


def test_facebook_profile_url_has_no_playlist_support():
    # Deliberate scope limit -- see README > Known limitations.
    assert classify_url("https://www.facebook.com/someone/") == MediaKind.INVALID


# -- Vimeo --------------------------------------------------------------------


def test_vimeo_numeric_url_classifies_as_video():
    assert classify_url("https://vimeo.com/76979871") == MediaKind.VIDEO_URL


@pytest.mark.parametrize(
    "url",
    [
        "https://vimeo.com/showcase/1234567",
        "https://vimeo.com/album/1234567",
        "https://vimeo.com/channels/staffpicks",
    ],
)
def test_vimeo_collection_urls_classify_as_playlist(url):
    assert classify_url(url) == MediaKind.PLAYLIST_URL


def test_vimeo_non_numeric_video_path_is_invalid():
    assert classify_url("https://vimeo.com/about") == MediaKind.INVALID
