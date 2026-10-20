"""Typed data structures shared across the application."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class MediaKind(str, Enum):
    VIDEO_URL = "video"
    PLAYLIST_URL = "playlist"
    INVALID = "invalid"


class Platform(str, Enum):
    """Which site a URL belongs to. Purely informational (drives the "Loaded
    from: X" label and platform-specific error copy) -- yt-dlp does its own
    site detection internally and doesn't need this to actually extract or
    download anything, so an UNKNOWN platform doesn't block a fetch attempt.
    """

    YOUTUBE = "youtube"
    TIKTOK = "tiktok"
    TWITTER = "twitter"
    INSTAGRAM = "instagram"
    FACEBOOK = "facebook"
    VIMEO = "vimeo"
    UNKNOWN = "unknown"

    @property
    def display_name(self) -> str:
        return _PLATFORM_DISPLAY_NAMES[self]


_PLATFORM_DISPLAY_NAMES = {
    Platform.YOUTUBE: "YouTube",
    Platform.TIKTOK: "TikTok",
    Platform.TWITTER: "Twitter/X",
    Platform.INSTAGRAM: "Instagram",
    Platform.FACEBOOK: "Facebook",
    Platform.VIMEO: "Vimeo",
    Platform.UNKNOWN: "Unknown",
}


class DownloadMode(str, Enum):
    VIDEO_WITH_AUDIO = "video_audio"
    AUDIO_ONLY = "audio_only"
    BEST_QUALITY = "best"
    SPECIFIC_FORMAT = "specific_format"


class DuplicatePolicy(str, Enum):
    OVERWRITE = "overwrite"
    SKIP = "skip"
    RENAME = "rename"


class QueueStatus(str, Enum):
    QUEUED = "queued"
    DOWNLOADING = "downloading"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"
    FAILED = "failed"


# Transitions that are legal for a queue item to move through. Used by the
# queue manager to reject nonsensical state changes (e.g. resuming a
# completed item) rather than silently corrupting state.
ALLOWED_TRANSITIONS: dict[QueueStatus, set[QueueStatus]] = {
    QueueStatus.QUEUED: {
        QueueStatus.DOWNLOADING,
        QueueStatus.CANCELLED,
        QueueStatus.SKIPPED,
        QueueStatus.PAUSED,
        # A worker can fail before the transfer starts (destination folder
        # can't be created, not enough free space).
        QueueStatus.FAILED,
    },
    QueueStatus.DOWNLOADING: {
        QueueStatus.PAUSED,
        QueueStatus.COMPLETED,
        QueueStatus.CANCELLED,
        QueueStatus.FAILED,
    },
    QueueStatus.PAUSED: {QueueStatus.QUEUED, QueueStatus.DOWNLOADING, QueueStatus.CANCELLED},
    QueueStatus.COMPLETED: set(),
    QueueStatus.CANCELLED: {QueueStatus.QUEUED},
    QueueStatus.SKIPPED: {QueueStatus.QUEUED},
    QueueStatus.FAILED: {QueueStatus.QUEUED},
}


def can_transition(current: QueueStatus, target: QueueStatus) -> bool:
    if current == target:
        return True
    return target in ALLOWED_TRANSITIONS.get(current, set())


@dataclass
class FormatInfo:
    format_id: str
    ext: str
    resolution: str | None = None
    fps: float | None = None
    vcodec: str | None = None
    acodec: str | None = None
    abr: float | None = None
    filesize_approx: int | None = None
    note: str | None = None

    @property
    def is_audio_only(self) -> bool:
        return self.vcodec in (None, "none") and self.acodec not in (None, "none")

    @property
    def is_video(self) -> bool:
        return self.vcodec not in (None, "none")

    def label(self) -> str:
        parts = []
        if self.resolution:
            parts.append(self.resolution)
        if self.fps:
            parts.append(f"{int(self.fps)}fps")
        if self.is_audio_only and self.abr:
            parts.append(f"{int(self.abr)}kbps audio")
        parts.append(self.ext)
        if self.note:
            parts.append(f"({self.note})")
        return " ".join(parts) if parts else self.format_id


@dataclass
class VideoInfo:
    video_id: str
    title: str
    url: str
    channel: str | None = None
    duration_seconds: int | None = None
    thumbnail_url: str | None = None
    formats: list[FormatInfo] = field(default_factory=list)
    is_available: bool = True
    unavailable_reason: str | None = None
    playlist_index: int | None = None


@dataclass
class PlaylistInfo:
    playlist_id: str
    title: str
    url: str
    items: list[VideoInfo] = field(default_factory=list)


@dataclass
class DownloadRequest:
    """What the user has asked for, independent of queue bookkeeping."""

    url: str
    title: str
    destination_dir: str
    mode: DownloadMode
    format_id: str | None = None
    playlist_id: str | None = None
    playlist_index: int | None = None
    duplicate_policy: DuplicatePolicy = DuplicatePolicy.RENAME
    preserve_playlist_order: bool = False
    # Preferred container/codec extension (e.g. "mp4", "webm", "3gp" for video;
    # "mp3", "opus" for audio). Unlike `format_id` (an exact yt-dlp format id
    # valid only for one already-inspected video), this is an extension filter
    # that yt-dlp can apply per-item at download time -- the only option that
    # works across a playlist's videos without fetching full format lists for
    # every item up front. None means "best available, no format preference".
    container_ext: str | None = None
    # Comma-separated subtitle languages to also save next to the media
    # (e.g. "en" or "en,fr"; "all" for every available track). None/empty
    # means no subtitles -- the default, so existing behavior is untouched.
    subtitle_langs: str | None = None
    # When True, ONLY the subtitle file(s) are fetched; no video/audio is
    # downloaded. Requires `subtitle_langs`.
    subtitles_only: bool = False


@dataclass
class QueueItem:
    id: int | None
    request: DownloadRequest
    status: QueueStatus = QueueStatus.QUEUED
    progress_percent: float = 0.0
    speed_bps: float | None = None
    eta_seconds: int | None = None
    error_message: str | None = None
    attempts: int = 0
    saved_path: str | None = None
    position: int = 0
    # The exact resolved destination path (post duplicate-handling) once a
    # download has started -- kept so a Paused item resumes into the *same*
    # partial file (letting yt-dlp continue via HTTP range requests) rather
    # than re-running duplicate resolution and picking a different name.
    # Cleared once the item reaches a terminal state.
    working_path: str | None = None


@dataclass
class HistoryEntry:
    id: int | None
    title: str
    source_url: str
    saved_path: str | None
    format_label: str
    timestamp_iso: str
    outcome: QueueStatus


@dataclass
class AppSettings:
    default_download_dir: str
    concurrency: int
    duplicate_policy: DuplicatePolicy
    theme: str = "system"
    preserve_playlist_order: bool = True
    retry_attempts: int = 3
    check_for_updates: bool = True
    # Browser to borrow YouTube sign-in cookies from (e.g. "chrome"), or
    # None -- the default -- for no cookies at all. Only applied to YouTube
    # URLs; see ytdlp_backend._network_opts.
    cookies_browser: str | None = None
