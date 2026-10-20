"""Background metadata fetch so URL lookups never block the GUI thread."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QObject, QRunnable, Signal

from .. import ytdlp_backend
from ..models import MediaKind


class MetadataSignals(QObject):
    video_ready = Signal(object)  # VideoInfo
    playlist_ready = Signal(object)  # PlaylistInfo
    failed = Signal(str)


class MetadataFetchWorker(QRunnable):
    """Fetches metadata for a single video or a playlist without downloading.

    Playlists are fetched with `flat_playlist=True` first for a fast initial
    listing (staged/lazy loading per the reliability requirements), which is
    enough to populate title/id/index for the selection table.
    """

    def __init__(
        self,
        url: str,
        kind: MediaKind,
        *,
        cookies_browser: str | None = None,
        js_runtime_path: str | None = None,
    ) -> None:
        super().__init__()
        self.url = url
        self.kind = kind
        self.cookies_browser = cookies_browser
        self.js_runtime_path = js_runtime_path
        self.signals = MetadataSignals()
        self.setAutoDelete(True)

    def run(self) -> None:
        network = {"cookies_browser": self.cookies_browser, "js_runtime_path": self.js_runtime_path}
        try:
            if self.kind == MediaKind.PLAYLIST_URL:
                data: dict[str, Any] = ytdlp_backend.extract_info(
                    self.url, download=False, flat_playlist=True, **network
                )
                playlist = ytdlp_backend.playlist_info_from_dict(data)
                self.signals.playlist_ready.emit(playlist)
            else:
                data = ytdlp_backend.extract_info(
                    self.url, download=False, flat_playlist=False, **network
                )
                video = ytdlp_backend.video_info_from_dict(data)
                self.signals.video_ready.emit(video)
        except Exception as exc:  # noqa: BLE001 - report any extraction failure to the UI
            self.signals.failed.emit(str(exc))
