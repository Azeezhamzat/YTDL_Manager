"""URL validation and video-vs-collection classification, across every
platform this app recognises (YouTube, TikTok, Twitter/X, Instagram,
Facebook, Vimeo).

This module does purely syntactic checks (no network access) so it can give
instant feedback in the UI before any metadata is fetched. yt-dlp itself
does the real extraction and doesn't need any of this to work -- it has its
own, far more thorough site detection -- but a fast local guess lets the app
validate the URL and pick the right tab (Video vs Playlist) without waiting
on a network round trip first.

Each platform gets its own small classifier below rather than one shared
regex, because "is this a single item or a collection?" means something
different per site (a playlist ID query param on YouTube, a profile handle
on TikTok/Instagram/Twitter, an album/showcase path on Vimeo) and forcing
them into one pattern would make each one harder to get right.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

from .models import MediaKind, Platform

_VIDEO_ID_LENGTH = 11  # YouTube-specific: its video ids are always 11 chars


def _is_web_scheme(scheme: str) -> bool:
    # Reject non-web schemes (ftp://, javascript:, file://, ...) even if the
    # host portion happens to parse as a recognised platform.
    return scheme.lower() in ("http", "https", "")


def _safe_parse(url: str):
    try:
        return urlparse(url.strip())
    except ValueError:
        return None


# ============================================================ YouTube ====
_YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
    "www.youtu.be",
}


def is_youtube_host(host: str | None) -> bool:
    if not host:
        return False
    return host.lower() in _YOUTUBE_HOSTS


def extract_video_id(url: str) -> str | None:
    """Return the 11-character video id if `url` points at a single YouTube video."""
    parsed = _safe_parse(url)
    if parsed is None:
        return None
    if not _is_web_scheme(parsed.scheme) or not is_youtube_host(parsed.netloc):
        return None

    candidate: str | None

    if parsed.netloc.lower() in {"youtu.be", "www.youtu.be"}:
        candidate = parsed.path.strip("/").split("/")[0]
        return candidate if _looks_like_youtube_id(candidate) else None

    if parsed.path in ("/watch",):
        qs = parse_qs(parsed.query)
        candidate = qs.get("v", [None])[0]
        return candidate if candidate and _looks_like_youtube_id(candidate) else None

    if parsed.path.startswith("/shorts/") or parsed.path.startswith("/live/"):
        candidate = parsed.path.split("/")[2] if len(parsed.path.split("/")) > 2 else None
        return candidate if candidate and _looks_like_youtube_id(candidate) else None

    if parsed.path.startswith("/embed/"):
        candidate = parsed.path.split("/")[2] if len(parsed.path.split("/")) > 2 else None
        return candidate if candidate and _looks_like_youtube_id(candidate) else None

    return None


def _looks_like_youtube_id(candidate: str) -> bool:
    if len(candidate) != _VIDEO_ID_LENGTH:
        return False
    return all(c.isalnum() or c in "-_" for c in candidate)


def extract_playlist_id(url: str) -> str | None:
    """Return the playlist id if `url` points at a YouTube playlist."""
    parsed = _safe_parse(url)
    if parsed is None:
        return None
    if not _is_web_scheme(parsed.scheme) or not is_youtube_host(parsed.netloc):
        return None

    if parsed.path == "/playlist":
        qs = parse_qs(parsed.query)
        pid = qs.get("list", [None])[0]
        return pid or None

    # A /watch URL can carry a `list=` parameter for "video within playlist".
    if parsed.path == "/watch":
        qs = parse_qs(parsed.query)
        pid = qs.get("list", [None])[0]
        return pid or None

    return None


def _classify_youtube(url: str) -> MediaKind:
    """A `/watch?v=...&list=...` URL is treated as a video (matching user
    intent of "download this one video"); use `extract_playlist_id`
    separately if the caller wants to offer "or did you mean the playlist?".
    """
    if extract_video_id(url):
        return MediaKind.VIDEO_URL
    if extract_playlist_id(url):
        return MediaKind.PLAYLIST_URL
    return MediaKind.INVALID


def is_valid_youtube_url(url: str) -> bool:
    return _classify_youtube(url) != MediaKind.INVALID


# ============================================================= TikTok ====
_TIKTOK_HOSTS = {"tiktok.com", "www.tiktok.com", "m.tiktok.com", "vm.tiktok.com"}
_TIKTOK_VIDEO_RE = re.compile(r"/@[\w.\-]+/video/\d+$")
_TIKTOK_PROFILE_RE = re.compile(r"/@[\w.\-]+$")


def _classify_tiktok(url: str) -> MediaKind:
    parsed = _safe_parse(url)
    if parsed is None:
        return MediaKind.INVALID
    path = parsed.path.rstrip("/")
    host = parsed.netloc.lower()

    if _TIKTOK_VIDEO_RE.search(path):
        return MediaKind.VIDEO_URL

    # Shortened share links (vm.tiktok.com/XXXXX, tiktok.com/t/XXXXX) always
    # point at one video -- there is no shortened form for a profile.
    if host == "vm.tiktok.com" and path.strip("/"):
        return MediaKind.VIDEO_URL
    if path.startswith("/t/") and len(path) > 3:
        return MediaKind.VIDEO_URL

    if _TIKTOK_PROFILE_RE.fullmatch(path):
        return MediaKind.PLAYLIST_URL

    return MediaKind.INVALID


# =========================================================== Twitter/X ====
_TWITTER_HOSTS = {"twitter.com", "www.twitter.com", "x.com", "www.x.com", "mobile.twitter.com"}
_TWITTER_STATUS_RE = re.compile(r"/[\w]+/status/\d+$")
_TWITTER_HANDLE_RE = re.compile(r"/(\w+)$")
_TWITTER_RESERVED_PATHS = {
    "home",
    "explore",
    "notifications",
    "messages",
    "i",
    "search",
    "settings",
    "hashtag",
    "compose",
}


def _classify_twitter(url: str) -> MediaKind:
    parsed = _safe_parse(url)
    if parsed is None:
        return MediaKind.INVALID
    path = parsed.path.rstrip("/")

    if _TWITTER_STATUS_RE.search(path):
        return MediaKind.VIDEO_URL

    match = _TWITTER_HANDLE_RE.fullmatch(path)
    if match and match.group(1).lower() not in _TWITTER_RESERVED_PATHS:
        return MediaKind.PLAYLIST_URL  # a user's timeline

    return MediaKind.INVALID


# ========================================================== Instagram ====
_INSTAGRAM_HOSTS = {"instagram.com", "www.instagram.com"}
_INSTAGRAM_POST_RE = re.compile(r"/(p|reel|reels|tv)/[\w-]+$")
_INSTAGRAM_PROFILE_RE = re.compile(r"/([\w.]+)$")
_INSTAGRAM_RESERVED_PATHS = {
    "p",
    "reel",
    "reels",
    "tv",
    "stories",
    "explore",
    "accounts",
    "direct",
    "about",
    "legal",
    "developer",
}


def _classify_instagram(url: str) -> MediaKind:
    parsed = _safe_parse(url)
    if parsed is None:
        return MediaKind.INVALID
    path = parsed.path.rstrip("/")

    if _INSTAGRAM_POST_RE.search(path):
        return MediaKind.VIDEO_URL

    match = _INSTAGRAM_PROFILE_RE.fullmatch(path)
    if match and match.group(1).lower() not in _INSTAGRAM_RESERVED_PATHS:
        return MediaKind.PLAYLIST_URL  # a user's public posts

    return MediaKind.INVALID


# ========================================================== Facebook ====
# Facebook offers no reliably scrapable "download this profile/page's videos
# in bulk" surface, so this app only supports single Facebook videos, not a
# playlist-equivalent -- see README > Known limitations.
_FACEBOOK_HOSTS = {"facebook.com", "www.facebook.com", "m.facebook.com", "fb.watch"}
_FACEBOOK_VIDEO_ID_RE = re.compile(r"/(videos|reel)/(\d+)")


def _classify_facebook(url: str) -> MediaKind:
    parsed = _safe_parse(url)
    if parsed is None:
        return MediaKind.INVALID
    host = parsed.netloc.lower()
    path = parsed.path.rstrip("/")

    if host == "fb.watch" and path.strip("/"):
        return MediaKind.VIDEO_URL
    if path == "/watch" and parse_qs(parsed.query).get("v"):
        return MediaKind.VIDEO_URL
    if _FACEBOOK_VIDEO_ID_RE.search(path):
        return MediaKind.VIDEO_URL

    return MediaKind.INVALID


# ============================================================= Vimeo ====
_VIMEO_HOSTS = {"vimeo.com", "www.vimeo.com", "player.vimeo.com"}
_VIMEO_VIDEO_RE = re.compile(r"^/(\d+)$")
_VIMEO_COLLECTION_RE = re.compile(r"^/(album|showcase|channels)/")


def _classify_vimeo(url: str) -> MediaKind:
    parsed = _safe_parse(url)
    if parsed is None:
        return MediaKind.INVALID
    path = parsed.path.rstrip("/")

    if _VIMEO_VIDEO_RE.match(path):
        return MediaKind.VIDEO_URL
    if _VIMEO_COLLECTION_RE.match(path):
        return MediaKind.PLAYLIST_URL

    return MediaKind.INVALID


# ============================================================ dispatch ====
_HOST_TO_PLATFORM: dict[str, Platform] = {
    **dict.fromkeys(_YOUTUBE_HOSTS, Platform.YOUTUBE),
    **dict.fromkeys(_TIKTOK_HOSTS, Platform.TIKTOK),
    **dict.fromkeys(_TWITTER_HOSTS, Platform.TWITTER),
    **dict.fromkeys(_INSTAGRAM_HOSTS, Platform.INSTAGRAM),
    **dict.fromkeys(_FACEBOOK_HOSTS, Platform.FACEBOOK),
    **dict.fromkeys(_VIMEO_HOSTS, Platform.VIMEO),
}

_CLASSIFIERS = {
    Platform.YOUTUBE: _classify_youtube,
    Platform.TIKTOK: _classify_tiktok,
    Platform.TWITTER: _classify_twitter,
    Platform.INSTAGRAM: _classify_instagram,
    Platform.FACEBOOK: _classify_facebook,
    Platform.VIMEO: _classify_vimeo,
}


def detect_platform(url: str) -> Platform:
    """Which supported platform `url` belongs to, or UNKNOWN."""
    parsed = _safe_parse(url)
    if parsed is None:
        return Platform.UNKNOWN
    return _HOST_TO_PLATFORM.get(parsed.netloc.lower(), Platform.UNKNOWN)


def classify_url(url: str) -> MediaKind:
    """Classify a URL as a single video, a playlist/collection, or invalid,
    across every platform this app recognises.
    """
    if not url or not url.strip():
        return MediaKind.INVALID

    parsed = _safe_parse(url)
    if parsed is None or not _is_web_scheme(parsed.scheme):
        return MediaKind.INVALID

    platform = _HOST_TO_PLATFORM.get(parsed.netloc.lower())
    if platform is None:
        return MediaKind.INVALID
    return _CLASSIFIERS[platform](url)


def is_valid_url(url: str) -> bool:
    return classify_url(url) != MediaKind.INVALID
