"""Thin wrapper around yt-dlp for metadata extraction and downloading.

We deliberately do not reimplement extraction, throttling, or format
negotiation ourselves — yt-dlp already does this safely and is kept up to
date against YouTube's changes. This module only adapts yt-dlp's dict-based
API to our typed models and wires progress/error handling into the rest of
the app.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .models import (
    DownloadMode,
    DownloadRequest,
    DuplicatePolicy,
    FormatInfo,
    Platform,
    PlaylistInfo,
    VideoInfo,
)
from .url_utils import detect_platform

ProgressCallback = Callable[[dict[str, Any]], None]

# YouTube's anti-bot gate ("Sign in to confirm you're not a bot"). It's
# tied to the network address, not to one video, and typically appears after
# a burst of downloads -- retrying only adds more requests from the address
# YouTube is already throttling. The apostrophe is a typographic one (’) in
# current yt-dlp output, hence the single-character wildcard.
_BOT_CHECK_RE = re.compile(r"confirm you.?re not a bot")

# Browsers yt-dlp can read cookies from (yt_dlp.cookies.SUPPORTED_BROWSERS),
# in the order they're offered in Settings.
COOKIE_BROWSER_CHOICES = (
    "chrome",
    "safari",
    "firefox",
    "edge",
    "brave",
    "chromium",
    "opera",
    "vivaldi",
)

# Prefix of the message raised when yt-dlp can't read a browser's cookies;
# the UI keys its recovery advice off this.
COOKIE_LOAD_ERROR_PREFIX = "Couldn't read YouTube sign-in cookies from"

# Error message fragments yt-dlp raises for conditions that will not resolve
# on retry: no amount of backoff fixes a private/removed/geo-blocked video.
_PERMANENT_ERROR_PATTERNS = [
    r"private video",
    r"video (is )?unavailable",
    r"has been removed",
    r"account associated with this video has been terminated",
    r"this video is not available",
    r"not available in your country",
    r"age[- ]restrict",
    r"sign in to confirm your age",
    r"copyright",
    r"members-only",
    r"this live event will begin",
    r"unsupported url",
    r"video is no longer available",
    # Filesystem problems: retrying won't free disk space or fix permissions.
    r"no space left on device",
    r"disk quota exceeded",
    r"errno 28",
    r"permission denied",
    r"errno 13",
    r"read-only file system",
    # Content that needs a logged-in session. This app deliberately doesn't
    # support authentication (see README > Known limitations), so these are
    # permanent for us even though the content might be technically
    # downloadable with a login yt-dlp itself could use. Common across
    # Instagram, Vimeo, Facebook, and restricted/NSFW content on Twitter/X.
    r"logged-in",
    r"log in to",
    r"login required",
    r"requires authentication",
    r"rate-limit reached or login required",
    r"private account",
    r"restricted video",
    r"this account's tweets are protected",
    _BOT_CHECK_RE.pattern,
    # Retrying can't fix a browser we can't read cookies from (missing
    # Keychain/Full Disk Access permission, browser not installed).
    re.escape(COOKIE_LOAD_ERROR_PREFIX.lower()),
]

_TRANSIENT_ERROR_PATTERNS = [
    r"timed out",
    r"timeout",
    r"connection reset",
    r"temporary failure",
    r"unable to download webpage",
    r"http error 5\d\d",
    r"network is unreachable",
    r"read timed out",
    r"remote end closed connection",
]


class DownloadError(Exception):
    def __init__(self, message: str, *, is_permanent: bool) -> None:
        super().__init__(message)
        self.is_permanent = is_permanent


class DownloadCancelledError(Exception):
    """Raised when a download is aborted by the user via the cancel token.

    yt-dlp's own `DownloadCancelled` subclasses `KeyboardInterrupt` (a
    `BaseException`, not `Exception`), so we translate it to this ordinary
    `Exception` subclass immediately -- otherwise a plain `except Exception`
    in the worker thread would not catch it and the cancellation would
    propagate as an unhandled interrupt instead of a clean, reportable state.
    """


def classify_error(message: str) -> bool:
    """Return True if `message` describes a permanent (non-retryable) failure."""
    lowered = message.lower()
    for pattern in _PERMANENT_ERROR_PATTERNS:
        if re.search(pattern, lowered):
            return True
    for pattern in _TRANSIENT_ERROR_PATTERNS:
        if re.search(pattern, lowered):
            return False
    # Unknown errors default to transient so we at least retry a bounded
    # number of times rather than giving up on the first unrecognised message.
    return False


def is_bot_check_error(message: str) -> bool:
    """True if `message` is YouTube's "Sign in to confirm you're not a bot"."""
    return bool(_BOT_CHECK_RE.search(message.lower()))


def _network_opts(
    url: str, *, cookies_browser: str | None, js_runtime_path: str | None
) -> dict[str, Any]:
    """yt-dlp options shared by metadata fetches and downloads.

    Browser cookies are only ever attached to YouTube URLs, and only when
    the user has picked a browser in Settings (off by default): they exist
    to get past YouTube's network-level bot check, not to reach logged-in
    content on the other platforms.
    """
    opts: dict[str, Any] = {"cookiesfrombrowser": None}
    if cookies_browser and detect_platform(url) == Platform.YOUTUBE:
        opts["cookiesfrombrowser"] = (cookies_browser,)
    if js_runtime_path:
        # yt-dlp needs a JavaScript runtime to solve YouTube's player
        # challenges; without one it falls back to a single limited client.
        # Passing the path explicitly matters for GUI launches and frozen
        # builds, where yt-dlp's own PATH lookup can't see it.
        opts["js_runtimes"] = {"deno": {"path": js_runtime_path}}
    return opts


def _cookie_load_error(exc: BaseException, cookies_browser: str | None) -> DownloadError:
    cause = exc.__context__ or exc
    return DownloadError(
        f"{COOKIE_LOAD_ERROR_PREFIX} {cookies_browser or 'the browser'}: {cause}",
        is_permanent=True,
    )


def _format_from_dict(data: dict[str, Any]) -> FormatInfo:
    height = data.get("height")
    width = data.get("width")
    resolution = None
    if height:
        resolution = f"{width}x{height}" if width else f"{height}p"
    elif data.get("resolution"):
        resolution = data["resolution"]

    return FormatInfo(
        format_id=str(data.get("format_id", "")),
        ext=data.get("ext", ""),
        resolution=resolution,
        fps=data.get("fps"),
        vcodec=data.get("vcodec"),
        acodec=data.get("acodec"),
        abr=data.get("abr"),
        filesize_approx=data.get("filesize") or data.get("filesize_approx"),
        note=data.get("format_note"),
    )


def video_info_from_dict(data: dict[str, Any]) -> VideoInfo:
    formats = [_format_from_dict(f) for f in data.get("formats", []) or []]
    availability = data.get("availability")
    is_available = availability not in ("private", "premium_only", "subscriber_only", "needs_auth")
    return VideoInfo(
        video_id=data.get("id", ""),
        # Playlist entries fetched with extract_flat often carry no title
        # (TikTok/Instagram profiles) -- or an explicit None, which used to
        # crash filename sanitising. The id still makes a distinct name.
        title=data.get("title") or data.get("fulltitle") or data.get("id") or "Untitled",
        url=data.get("webpage_url") or data.get("original_url") or data.get("url", ""),
        channel=data.get("channel") or data.get("uploader"),
        duration_seconds=data.get("duration"),
        thumbnail_url=data.get("thumbnail"),
        formats=formats,
        is_available=is_available,
        unavailable_reason=None if is_available else str(availability),
        playlist_index=data.get("playlist_index"),
    )


def playlist_info_from_dict(data: dict[str, Any]) -> PlaylistInfo:
    entries = data.get("entries") or []
    items: list[VideoInfo] = []
    for idx, entry in enumerate(entries, start=1):
        if entry is None:
            continue
        video = video_info_from_dict(entry)
        if video.playlist_index is None:
            video.playlist_index = idx
        items.append(video)
    return PlaylistInfo(
        playlist_id=data.get("id", ""),
        title=data.get("title", "Untitled playlist"),
        url=data.get("webpage_url") or data.get("original_url", ""),
        items=items,
    )


# Formats YouTube serves already muxed (single combined stream, no separate
# video/audio to merge) -- legacy feature-phone formats being the main case.
# For these we select the pre-muxed stream directly rather than asking
# FFmpeg to remux modern codecs into a container that never carries them.
_PROGRESSIVE_ONLY_EXTS = {"3gp"}

# Containers FFmpeg can reliably mux arbitrary modern video+audio streams
# into via yt-dlp's `merge_output_format`.
_MERGE_CAPABLE_EXTS = {"mp4", "webm", "mkv", "avi", "mov", "flv"}

# yt-dlp's FFmpegExtractAudio `--audio-format` choices (excluding "best",
# which is what `container_ext=None` already means for us).
AUDIO_CONTAINER_CHOICES = ("m4a", "mp3", "opus", "vorbis", "wav", "flac", "aac", "alac")
VIDEO_CONTAINER_CHOICES = ("mp4", "webm", "mkv", "3gp")


# The audio stream to pair with a video container. YouTube's AAC audio is
# "m4a", never "mp4": asking for bestaudio[ext=mp4] matched nothing, so an
# "mp4" batch quietly fell back to the pre-muxed file -- usually 360p.
# Containers not listed (mkv) hold any codec; merge_output_format alone
# decides the file type for them.
_AUDIO_EXT_FOR_CONTAINER = {"mp4": "m4a", "webm": "webm"}

# Audio codecs whose files yt-dlp can embed cover art into (EmbedThumbnail
# supports mp3, m4a/mp4, mkv/mka and ogg/opus/flac; alac lands in .m4a and
# vorbis in .ogg). Raw .aac and .wav can't carry a picture.
_COVER_ART_CODECS = {"mp3", "m4a", "opus", "vorbis", "flac", "alac"}

# Resolution caps offered in the UI ("Max quality"); None means no limit.
MAX_HEIGHT_CHOICES = (None, 2160, 1440, 1080, 720, 480, 360)


def build_format_selector(
    mode: DownloadMode,
    format_id: str | None,
    container_ext: str | None = None,
    max_height: int | None = None,
) -> str:
    """Translate our high-level mode into a yt-dlp format-selection string."""
    if mode == DownloadMode.AUDIO_ONLY:
        # The container for audio-only is decided by the FFmpegExtractAudio
        # postprocessor's preferredcodec, not the source format selector.
        return "bestaudio/best"
    if mode == DownloadMode.SPECIFIC_FORMAT:
        if not format_id:
            raise ValueError("SPECIFIC_FORMAT mode requires a format_id")
        return f"{format_id}+bestaudio/{format_id}/best"
    if mode not in (DownloadMode.BEST_QUALITY, DownloadMode.VIDEO_WITH_AUDIO):
        raise ValueError(f"Unhandled download mode: {mode}")

    # "<=?" also accepts formats that don't report a height at all.
    cap = f"[height<=?{max_height}]" if max_height else ""
    default = f"bestvideo*{cap}+bestaudio/best{cap}"
    if cap:
        # Nothing at or under the cap: still download the best there is
        # rather than failing the item.
        default += "/bestvideo*+bestaudio/best"
    if not container_ext:
        return default
    if container_ext in _PROGRESSIVE_ONLY_EXTS:
        # Prefer an already-muxed stream in this container; fall back to the
        # normal best video+audio combo (a different container) if this
        # particular video doesn't offer one.
        return f"best[ext={container_ext}]/{default}"
    audio_ext = _AUDIO_EXT_FOR_CONTAINER.get(container_ext)
    if audio_ext is None:
        return default
    # Try the requested container for both streams, then just the requested
    # container for a combined stream, then fall back to best-overall so one
    # video's missing format doesn't fail the whole batch.
    both = f"bestvideo*[ext={container_ext}]{cap}+bestaudio[ext={audio_ext}]"
    combined = f"best[ext={container_ext}]{cap}"
    return f"{both}/{combined}/{default}"


_SUBTITLE_LANG_RE = re.compile(r"^[A-Za-z0-9_*.\-]+$")


def parse_subtitle_langs(text: str | None) -> list[str]:
    """Split a user-typed language list ("en, fr", "en fr", "all") into the
    list yt-dlp wants. Raises ValueError on characters that can't be part of
    a language code (yt-dlp treats entries as regexes, so stray punctuation
    would silently match the wrong tracks)."""
    if not text:
        return []
    langs = [part for part in re.split(r"[,\s]+", text.strip()) if part]
    for lang in langs:
        if not _SUBTITLE_LANG_RE.match(lang):
            raise ValueError(f"Invalid subtitle language code: {lang!r}")
    return langs


def build_ydl_opts(
    request: DownloadRequest,
    *,
    output_template: str,
    progress_hook: ProgressCallback | None = None,
    ffmpeg_location: str | None = None,
    cookies_browser: str | None = None,
    js_runtime_path: str | None = None,
    rate_limit_bps: int | None = None,
) -> dict[str, Any]:
    opts: dict[str, Any] = {
        "outtmpl": output_template,
        "format": build_format_selector(
            request.mode, request.format_id, request.container_ext, request.max_height
        ),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "overwrites": request.duplicate_policy == DuplicatePolicy.OVERWRITE,
        "retries": 0,  # we own retry/backoff at the application level
        "fragment_retries": 3,
        "ignoreerrors": False,
        # Never use browser cookies unless the user opted in (Settings), and
        # then only for YouTube: no silent credential access.
        **_network_opts(
            request.url, cookies_browser=cookies_browser, js_runtime_path=js_runtime_path
        ),
    }
    if ffmpeg_location:
        opts["ffmpeg_location"] = ffmpeg_location
    if progress_hook:
        opts["progress_hooks"] = [progress_hook]
    if rate_limit_bps:
        opts["ratelimit"] = rate_limit_bps  # bytes per second, per download
    sub_langs = parse_subtitle_langs(request.subtitle_langs)
    if request.subtitles_only and not sub_langs:
        raise ValueError("Subtitles-only downloads need at least one language")
    if sub_langs:
        opts["writesubtitles"] = True
        # Fall back to auto-generated captions when no human-made track
        # exists -- but not for "all", which would pull dozens of
        # auto-translated tracks.
        opts["writeautomaticsub"] = "all" not in sub_langs
        opts["subtitleslangs"] = sub_langs
        opts["subtitlesformat"] = "srt/best"
        opts.setdefault("postprocessors", []).append(
            {"key": "FFmpegSubtitlesConvertor", "format": "srt", "when": "before_dl"}
        )
    if request.subtitles_only:
        opts["skip_download"] = True
        opts["ignore_no_formats_error"] = True
        return opts
    postprocessors = opts.setdefault("postprocessors", [])
    if request.mode == DownloadMode.AUDIO_ONLY:
        codec = request.container_ext if request.container_ext in AUDIO_CONTAINER_CHOICES else "m4a"
        postprocessors.append(
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": codec,
                "preferredquality": "0",
            }
        )
    elif request.container_ext in _MERGE_CAPABLE_EXTS:
        opts["merge_output_format"] = request.container_ext
    else:
        opts["merge_output_format"] = "mp4"
    if request.embed_metadata:
        # Title, artist, date etc. (and chapters) written into the file, so
        # music players and file browsers show more than a filename.
        postprocessors.append({"key": "FFmpegMetadata", "add_metadata": True,
                               "add_chapters": True})
        # Cover art only where the output type is known to support it:
        # yt-dlp fails the whole download for anything else (e.g. a webm
        # video that skipped merging), so video files get tags but no art.
        if request.mode == DownloadMode.AUDIO_ONLY and codec in _COVER_ART_CODECS:
            opts["writethumbnail"] = True
            postprocessors.append({"key": "EmbedThumbnail", "already_have_thumbnail": False})
    if not postprocessors:
        del opts["postprocessors"]
    return opts


def extract_info(
    url: str,
    *,
    download: bool = False,
    flat_playlist: bool = False,
    cookies_browser: str | None = None,
    js_runtime_path: str | None = None,
) -> dict[str, Any]:
    import yt_dlp
    from yt_dlp.cookies import CookieLoadError

    opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": not download,
        "extract_flat": "in_playlist" if flat_playlist else False,
        # A /watch?v=X&list=Y URL carries both a video id and a playlist id.
        # yt-dlp's own default (noplaylist unset/False) prefers the playlist
        # in that case -- but url_utils.classify_url() deliberately treats
        # such a URL as "just this video" (see its docstring), so a single-
        # video fetch (flat_playlist=False) must force noplaylist=True or it
        # silently returns playlist metadata instead of the one video's.
        "noplaylist": not flat_playlist,
        **_network_opts(url, cookies_browser=cookies_browser, js_runtime_path=js_runtime_path),
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(url, download=download)
    except CookieLoadError as exc:
        raise _cookie_load_error(exc, cookies_browser) from exc


def download_one(
    request: DownloadRequest,
    *,
    output_template: str,
    progress_hook: ProgressCallback | None = None,
    ffmpeg_location: str | None = None,
    cookies_browser: str | None = None,
    js_runtime_path: str | None = None,
    rate_limit_bps: int | None = None,
) -> str:
    """Download a single item and return the final saved file path."""
    import yt_dlp
    from yt_dlp.cookies import CookieLoadError

    opts = build_ydl_opts(
        request,
        output_template=output_template,
        progress_hook=progress_hook,
        ffmpeg_location=ffmpeg_location,
        cookies_browser=cookies_browser,
        js_runtime_path=js_runtime_path,
        rate_limit_bps=rate_limit_bps,
    )
    downloaded_path: list[str] = []
    final_path: list[str] = []

    def _capture_downloaded_path(d: dict[str, Any]) -> None:
        # Fires when the raw download finishes -- for audio extraction or
        # video merging this is a *pre-conversion* filename (e.g. the source
        # .webm before FFmpegExtractAudio turns it into .m4a), so it's only
        # a fallback, never the primary source of truth for the saved path.
        if d.get("status") == "finished":
            path = d.get("info_dict", {}).get("filepath") or d.get("filename")
            if path:
                downloaded_path.append(path)
        if progress_hook:
            progress_hook(d)

    def _capture_final_path(d: dict[str, Any]) -> None:
        # Fires after each postprocessor step (audio extraction, merging,
        # the final move-into-place); the *last* "finished" event here is
        # the actual file left on disk.
        if d.get("status") == "finished":
            path = d.get("info_dict", {}).get("filepath")
            if path:
                final_path.append(path)

    opts["progress_hooks"] = [_capture_downloaded_path]
    opts["postprocessor_hooks"] = [_capture_final_path]

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(request.url, download=True)
    except yt_dlp.utils.DownloadCancelled as exc:
        raise DownloadCancelledError(str(exc)) from exc
    except CookieLoadError as exc:
        raise _cookie_load_error(exc, cookies_browser) from exc
    except yt_dlp.utils.DownloadError as exc:
        is_permanent = classify_error(str(exc))
        raise DownloadError(str(exc), is_permanent=is_permanent) from exc

    if request.subtitles_only:
        tracks = (info or {}).get("requested_subtitles") or {}
        paths = [t["filepath"] for t in tracks.values() if t.get("filepath")]
        if not paths or not Path(paths[0]).exists():
            # Permanent: retrying can't conjure a track the uploader never made.
            raise DownloadError(
                "No subtitles are available for this video in the requested "
                f"language(s): {request.subtitle_langs}",
                is_permanent=True,
            )
        return paths[0]
    if final_path:
        return final_path[-1]
    # Fallback: yt-dlp can also report the final path via requested_downloads.
    requested = (info or {}).get("requested_downloads") or []
    if requested:
        return requested[-1].get("filepath", "")
    if downloaded_path:
        return downloaded_path[-1]
    return ydl.prepare_filename(info) if info else ""


def guess_output_path(destination_dir: Path, filename: str) -> str:
    """Build an output template rooted at destination_dir with a fixed filename.

    yt-dlp appends the real extension itself; we pass the sanitised stem and
    let it fill in `.%(ext)s`.
    """
    stem = filename.rsplit(".", 1)[0] if "." in filename else filename
    return str(destination_dir / f"{stem}.%(ext)s")
