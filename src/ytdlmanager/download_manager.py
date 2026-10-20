"""Queue orchestration: bounded concurrency, retry, duplicate handling.

Uses QThreadPool/QRunnable so downloads run off the GUI thread while still
integrating cleanly with Qt's signal/slot event loop for progress updates.
"""

from __future__ import annotations

import glob
import logging
import shutil
import threading
import time
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from . import ytdlp_backend
from .config import DEFAULT_RETRY_ATTEMPTS
from .db import Database
from .ffmpeg_utils import FfmpegStatus, find_ffmpeg
from .js_runtime import find_deno
from .models import (
    DownloadMode,
    DownloadRequest,
    DuplicatePolicy,
    HistoryEntry,
    Platform,
    QueueItem,
    QueueStatus,
    can_transition,
)
from .retry import backoff_delay_seconds, should_retry
from .sanitize import numbered_filename, sanitize_filename, windows_long_path
from .url_utils import detect_platform

_EXPECTED_EXT = {
    DownloadMode.AUDIO_ONLY: "m4a",
    DownloadMode.VIDEO_WITH_AUDIO: "mp4",
    DownloadMode.BEST_QUALITY: "mp4",
    DownloadMode.SPECIFIC_FORMAT: "mp4",
}

# Cancellation must feel immediate even while a worker is asleep between
# retries (that sleep can be up to DEFAULT_RETRY_MAX_DELAY_SECONDS long) --
# sleep in short slices and re-check the cancel token between each one.
_CANCEL_POLL_INTERVAL_SECONDS = 0.25

# Below this much free space on the destination volume, don't even attempt a
# download -- fail fast with a clear message instead of letting FFmpeg/yt-dlp
# hit ENOSPC partway through a multi-minute transfer.
_MIN_FREE_BYTES = 100 * 1024 * 1024


class _CancelToken:
    def __init__(self) -> None:
        self.cancelled = False
        # Distinct from `cancelled`: a pause also aborts the in-flight
        # transfer (there's no other way to interrupt a blocking network
        # read), but keeps partial files and the resolved working_path so
        # the item resumes into the *same* file via HTTP range requests
        # instead of restarting from zero. `cancelled` always wins if both
        # are ever set (see DownloadWorker._run_attempts).
        self.pause_requested = False

    def _stop_requested(self) -> bool:
        return self.cancelled or self.pause_requested

    def sleep(self, seconds: float) -> None:
        """Sleep up to `seconds`, but wake early and return as soon as this
        token is cancelled or paused -- so a user acting mid-backoff doesn't
        have to wait out the full delay (up to 30s) before it takes effect.
        """
        remaining = seconds
        while remaining > 0 and not self._stop_requested():
            step = min(_CANCEL_POLL_INTERVAL_SECONDS, remaining)
            time.sleep(step)
            remaining -= step


class _PathRegistry:
    """Tracks destination paths claimed by an in-flight download.

    Two queue items with identical sanitised titles (a real possibility --
    duplicate uploads, generic titles like "Live") can run concurrently.
    Checking the filesystem alone ("does this file exist yet?") is a
    time-of-check/time-of-use race: neither file exists until its download
    finishes, so both workers could resolve the same free filename and one
    would silently overwrite the other's output. This registry closes that
    window by having workers atomically claim a path before starting.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._reserved: set[Path] = set()

    def reserve(self, final_path: Path, *, allow_rename: bool) -> Path | None:
        """Atomically find and claim a usable path derived from `final_path`.

        The whole "is it free? then claim it" check runs under one lock
        acquisition so a second worker can never observe the gap between
        those two steps. With `allow_rename=False`, returns None if
        `final_path` is unavailable (existing policy is SKIP). With
        `allow_rename=True`, always returns a path by counting up
        " (1)", " (2)", ... until one is free (RENAME policy) -- this
        duplicates the small amount of naming logic in
        `sanitize.unique_destination` rather than sharing it, because that
        function's check has to run *inside* this lock to be race-free.
        """
        with self._lock:
            if not windows_long_path(final_path).exists() and final_path not in self._reserved:
                self._reserved.add(final_path)
                return final_path
            if not allow_rename:
                return None

            stem, _, ext = final_path.name.rpartition(".")
            if not stem:
                stem, ext = final_path.name, ""
            counter = 1
            while True:
                name = f"{stem} ({counter}).{ext}" if ext else f"{stem} ({counter})"
                candidate = final_path.parent / name
                if not windows_long_path(candidate).exists() and candidate not in self._reserved:
                    self._reserved.add(candidate)
                    return candidate
                counter += 1

    def release(self, path: Path) -> None:
        with self._lock:
            self._reserved.discard(path)

    def is_claimed(self, path: Path) -> bool:
        with self._lock:
            return path in self._reserved

    def claim_exact(self, path: Path) -> None:
        """Unconditionally mark `path` as reserved, regardless of whether it
        exists on disk or is already reserved. Used when *resuming* a paused
        item into its own already-known working_path: that path was never
        released while paused (see DownloadWorker.run), so it's still
        "reserved" -- by this same item. A plain reserve() would treat that
        as someone else's claim and refuse or rename; this just re-affirms
        ownership so a fresh in-memory registry (e.g. after an app restart)
        also protects the file mid-resume.
        """
        with self._lock:
            self._reserved.add(path)


class WorkerSignals(QObject):
    progress = Signal(int, float, object, object)  # item_id, percent, speed_bps, eta_seconds
    finished = Signal(int, str)  # item_id, saved_path
    failed = Signal(int, str, bool)  # item_id, message, is_permanent
    skipped = Signal(int, str)  # item_id, reason
    cancelled = Signal(int)
    paused = Signal(int)  # item_id
    working_path_resolved = Signal(int, str)  # item_id, resolved absolute path
    status_changed = Signal(int, str)  # item_id, QueueStatus.value


def expected_final_path(item: QueueItem) -> Path:
    request = item.request
    destination = Path(request.destination_dir)
    base_name = sanitize_filename(request.title)
    if request.preserve_playlist_order and request.playlist_index:
        base_name = numbered_filename(request.playlist_index, base_name)
    # container_ext is a best-effort guess here (yt-dlp may fall back to a
    # different container if this video doesn't offer the requested one);
    # the real saved file's extension always comes from yt-dlp itself.
    # Subtitles-only items reserve "<title>.srt"; yt-dlp then writes
    # "<title>.<lang>.srt" next to it, so the reservation still de-duplicates
    # concurrent same-title items without needing to know the languages.
    ext = "srt" if request.subtitles_only else request.container_ext or _EXPECTED_EXT[request.mode]
    return destination / f"{base_name}.{ext}"


class DownloadWorker(QRunnable):
    """Runs one queue item's download (with retries) on a background thread."""

    def __init__(
        self,
        item: QueueItem,
        signals: WorkerSignals,
        *,
        max_attempts: int,
        cancel_token: _CancelToken,
        ffmpeg_status: FfmpegStatus,
        path_registry: _PathRegistry,
        cookies_browser: str | None = None,
        js_runtime_path: str | None = None,
        youtube_block: threading.Event | None = None,
    ) -> None:
        super().__init__()
        if item.id is None:
            raise ValueError("DownloadWorker requires a persisted queue item (item.id is None)")
        # Shared by every worker; set (from the worker thread, so the next
        # worker the pool starts sees it immediately) when any download hits
        # YouTube's bot check. See DownloadManager._pause_pending_youtube_items.
        self.youtube_block = youtube_block
        self.item = item
        self.item_id: int = item.id
        self.signals = signals
        self.max_attempts = max_attempts
        self.cancel_token = cancel_token
        self.ffmpeg_status = ffmpeg_status
        self.path_registry = path_registry
        self.cookies_browser = cookies_browser
        self.js_runtime_path = js_runtime_path
        self._reserved_path: Path | None = None
        self.setAutoDelete(True)

    def run(self) -> None:
        # Every exit must emit exactly one terminal signal (finished, failed,
        # skipped, cancelled or paused): DownloadManager treats the worker as
        # live until then, and an item whose worker died silently would sit
        # in "Queued" forever with no error shown.
        try:
            self._run()
        except Exception as exc:  # noqa: BLE001 - e.g. the destination can't be created
            if self._reserved_path is not None:
                self.path_registry.release(self._reserved_path)
            message = f"Couldn't start the download: {exc}"
            self.signals.failed.emit(self.item_id, message, ytdlp_backend.classify_error(message))

    def _run(self) -> None:
        item = self.item
        request = item.request

        # The user may have cancelled or paused the item while this worker
        # was still waiting in the pool for a free slot.
        if self.cancel_token.cancelled:
            if item.working_path:
                # A resumed item cancelled before it got a slot: nothing else
                # will delete its partial file or release its reservation.
                resumed = Path(item.working_path)
                self._cleanup_partial(resumed.parent, resumed.name.rsplit(".", 1)[0])
                self.path_registry.release(resumed)
            self.signals.cancelled.emit(self.item_id)
            return
        if self.cancel_token.pause_requested:
            self.signals.paused.emit(self.item_id)
            return
        if (
            self.youtube_block is not None
            and self.youtube_block.is_set()
            and detect_platform(request.url) == Platform.YOUTUBE
        ):
            # Another download just hit YouTube's bot check: don't send yet
            # another request from the address YouTube is blocking.
            self.signals.paused.emit(self.item_id)
            return

        if item.working_path:
            # Resuming a previously paused/interrupted item: reuse the exact
            # same resolved path so yt-dlp finds the existing partial file
            # and continues via HTTP range requests instead of re-running
            # duplicate resolution (which could pick a different renamed
            # path and abandon the partial data already on disk).
            resolved_path = Path(item.working_path)
            self.path_registry.claim_exact(resolved_path)
            skip = False
        else:
            final_path = expected_final_path(item)
            resolved_path, skip = self._resolve_duplicate(final_path, request)
            if not skip:
                self.signals.working_path_resolved.emit(self.item_id, str(resolved_path))

        if skip:
            self.signals.skipped.emit(self.item_id, f"File already exists: {resolved_path}")
            return
        self._reserved_path = resolved_path

        destination = Path(request.destination_dir)
        # windows_long_path() is a no-op except on Windows with a path near
        # or past the legacy 260-char MAX_PATH limit -- see its docstring.
        windows_long_path(destination).mkdir(parents=True, exist_ok=True)

        if not check_free_space(str(destination), _MIN_FREE_BYTES):
            self.path_registry.release(resolved_path)
            self.signals.failed.emit(
                self.item_id,
                f"Insufficient free disk space on the destination volume ({destination}).",
                True,
            )
            return

        output_stem = resolved_path.name.rsplit(".", 1)[0]
        output_template = ytdlp_backend.guess_output_path(
            windows_long_path(destination), output_stem
        )

        paused = self._run_attempts(request, destination, output_stem, output_template)
        if not paused:
            self.path_registry.release(resolved_path)

    def _run_attempts(
        self, request: DownloadRequest, destination: Path, output_stem: str, output_template: str
    ) -> bool:
        """Returns True if the download stopped because of a pause request
        (partial files kept, path registry reservation left in place for the
        eventual resume) -- False for every other exit (completed, failed,
        cancelled, skipped), where the caller releases the reservation.
        """
        attempt = 0
        while True:
            attempt += 1
            if self.cancel_token.cancelled:
                self._cleanup_partial(destination, output_stem)
                self.signals.cancelled.emit(self.item_id)
                return False
            if self.cancel_token.pause_requested:
                self.signals.paused.emit(self.item_id)
                return True

            self.signals.status_changed.emit(self.item_id, QueueStatus.DOWNLOADING.value)
            try:
                saved_path = ytdlp_backend.download_one(
                    request,
                    output_template=output_template,
                    progress_hook=self._make_progress_hook(self.item_id),
                    ffmpeg_location=self.ffmpeg_status.path,
                    cookies_browser=self.cookies_browser,
                    js_runtime_path=self.js_runtime_path,
                )
                self.signals.finished.emit(self.item_id, saved_path)
                return False
            except ytdlp_backend.DownloadCancelledError:
                # Both cancel and pause abort the transfer the same way (via
                # DownloadCancelled raised from the progress hook) -- cancel
                # always wins if somehow both got requested, since it's the
                # more final of the two actions.
                if self.cancel_token.pause_requested and not self.cancel_token.cancelled:
                    self.signals.paused.emit(self.item_id)
                    return True
                self._cleanup_partial(destination, output_stem)
                self.signals.cancelled.emit(self.item_id)
                return False
            except ytdlp_backend.DownloadError as exc:
                message = str(exc)
                if self.youtube_block is not None and ytdlp_backend.is_bot_check_error(message):
                    self.youtube_block.set()
                if should_retry(attempt, self.max_attempts, exc.is_permanent):
                    self.cancel_token.sleep(backoff_delay_seconds(attempt))
                    continue
                self._cleanup_partial(destination, output_stem)
                self.signals.failed.emit(self.item_id, message, exc.is_permanent)
                return False
            except Exception as exc:  # noqa: BLE001 - surface any unexpected backend error
                message = str(exc)
                is_permanent = ytdlp_backend.classify_error(message)
                if should_retry(attempt, self.max_attempts, is_permanent):
                    self.cancel_token.sleep(backoff_delay_seconds(attempt))
                    continue
                self._cleanup_partial(destination, output_stem)
                self.signals.failed.emit(self.item_id, message, is_permanent)
                return False

    def _resolve_duplicate(
        self, final_path: Path, request: DownloadRequest
    ) -> tuple[Path, bool]:
        if request.duplicate_policy == DuplicatePolicy.OVERWRITE:
            # Overwrite never needs a unique name; nothing to reserve.
            return final_path, False

        allow_rename = request.duplicate_policy == DuplicatePolicy.RENAME
        reserved = self.path_registry.reserve(final_path, allow_rename=allow_rename)
        if reserved is None:
            # SKIP policy: the path exists on disk, or another concurrent
            # worker already claimed it first.
            return final_path, True
        return reserved, False

    def _make_progress_hook(self, item_id: int):
        def hook(data: dict) -> None:
            if self.cancel_token._stop_requested():
                import yt_dlp.utils

                reason = "cancelled" if self.cancel_token.cancelled else "paused"
                raise yt_dlp.utils.DownloadCancelled(f"{reason} by user")
            if data.get("status") != "downloading":
                return
            total = data.get("total_bytes") or data.get("total_bytes_estimate")
            downloaded = data.get("downloaded_bytes") or 0
            percent = (downloaded / total * 100.0) if total else 0.0
            speed = data.get("speed")
            eta = data.get("eta")
            self.signals.progress.emit(item_id, percent, speed, eta)

        return hook

    @staticmethod
    def _cleanup_partial(destination: Path, output_stem: str) -> None:
        """Remove incomplete artifacts left by a cancelled/failed download.

        Policy: partial (.part / .ytdl) files are always removed since they
        are not usable media.
        """
        # Escaped because titles routinely contain glob syntax -- "Song
        # [Official Video]" would otherwise be read as a character class and
        # match nothing, leaving every partial file behind.
        stem = glob.escape(output_stem)
        for pattern in (f"{stem}.*.part", f"{stem}.*.ytdl", f"{stem}.part"):
            for path in destination.glob(pattern):
                path.unlink(missing_ok=True)


class DownloadManager(QObject):
    item_progress = Signal(int, float, object, object)
    item_status_changed = Signal(int, str)
    item_finished = Signal(int, str)
    item_failed = Signal(int, str, bool)
    item_skipped = Signal(int, str)
    item_cancelled = Signal(int)
    queue_changed = Signal()
    # A download hit YouTube's bot check; carries how many queued YouTube
    # items were paused in response (see _pause_pending_youtube_items).
    youtube_blocked = Signal(int)

    def __init__(
        self,
        db: Database,
        concurrency: int = 3,
        retry_attempts: int = DEFAULT_RETRY_ATTEMPTS,
        cookies_browser: str | None = None,
    ) -> None:
        super().__init__()
        self.db = db
        self.retry_attempts = retry_attempts
        self.cookies_browser = cookies_browser
        self.pool = QThreadPool()
        self.pool.setMaxThreadCount(max(1, concurrency))
        self._items: dict[int, QueueItem] = {}
        self._cancel_tokens: dict[int, _CancelToken] = {}
        # Items whose worker has been handed to the pool and hasn't reported
        # a terminal signal yet -- including workers still waiting for a free
        # slot. There must never be two workers for one item: the second
        # would download the same file again (or into the same .part at the
        # same time) and replace the first one's cancel token, so Pause and
        # Cancel could no longer reach the first.
        self._live_workers: set[int] = set()
        # Items resumed while their worker was still acknowledging a pause;
        # restarted as soon as that worker reports `paused`.
        self._restart_after_exit: set[int] = set()
        # Set by a worker that hits YouTube's bot check; cleared whenever the
        # user starts or resumes something, i.e. decides to try again.
        self._youtube_block = threading.Event()
        self._path_registry = _PathRegistry()
        self.js_runtime_path = find_deno()
        logging.getLogger(__name__).info(
            "JavaScript runtime for YouTube: %s", self.js_runtime_path or "none found"
        )
        # Detected once (a subprocess spawn) rather than per download -- a
        # large playlist would otherwise re-probe FFmpeg for every single
        # item. refresh_ffmpeg_status() re-checks on demand (e.g. after the
        # user installs FFmpeg without restarting the app).
        self._ffmpeg_status = find_ffmpeg()
        self._signals = WorkerSignals()
        self._signals.progress.connect(self._on_progress)
        self._signals.finished.connect(self._on_finished)
        self._signals.failed.connect(self._on_failed)
        self._signals.skipped.connect(self._on_skipped)
        self._signals.cancelled.connect(self._on_cancelled)
        self._signals.paused.connect(self._on_paused)
        self._signals.working_path_resolved.connect(self._on_working_path_resolved)
        self._signals.status_changed.connect(self._on_status_changed)

    def set_concurrency(self, concurrency: int) -> None:
        self.pool.setMaxThreadCount(max(1, concurrency))

    def refresh_ffmpeg_status(self) -> FfmpegStatus:
        self._ffmpeg_status = find_ffmpeg()
        return self._ffmpeg_status

    def load_persisted_queue(self) -> None:
        for item in self.db.list_queue():
            assert item.id is not None  # rows loaded from the db always have an id
            self._items[item.id] = item
        self.queue_changed.emit()

    def items(self) -> list[QueueItem]:
        return sorted(self._items.values(), key=lambda i: i.position)

    def get_item(self, item_id: int) -> QueueItem | None:
        """O(1) lookup by id -- prefer this over scanning items() when you
        only need one item, since items() sorts the whole queue every call.
        """
        return self._items.get(item_id)

    def enqueue(self, request: DownloadRequest) -> QueueItem:
        position = len(self._items)
        item = QueueItem(id=None, request=request, status=QueueStatus.QUEUED, position=position)
        item.id = self.db.add_queue_item(item)
        self._items[item.id] = item
        self.queue_changed.emit()
        return item

    def enqueue_many(self, requests: list[DownloadRequest]) -> list[QueueItem]:
        return [self.enqueue(req) for req in requests]

    def start_item(self, item_id: int) -> None:
        """Hand a queued or paused item to the pool. A no-op for an item that
        already has a live worker (waiting or running) or any other status,
        so it's safe to call on a mixed selection."""
        item = self._items.get(item_id)
        if item is None:
            return
        if item_id in self._live_workers:
            token = self._cancel_tokens.get(item_id)
            if token and token.pause_requested and not token.cancelled:
                # Resumed before the worker finished acknowledging a pause:
                # restart it once it has (see _on_paused).
                self._restart_after_exit.add(item_id)
                if item.status == QueueStatus.PAUSED:
                    self._set_status(item, QueueStatus.QUEUED)
            return
        if item.status == QueueStatus.PAUSED:
            # Shown as waiting for a slot from now on -- an item that still
            # read "Paused" while queued couldn't be paused again.
            self._set_status(item, QueueStatus.QUEUED)
        if item.status != QueueStatus.QUEUED:
            return
        self._youtube_block.clear()
        token = _CancelToken()
        self._cancel_tokens[item_id] = token
        worker = DownloadWorker(
            item,
            self._signals,
            max_attempts=self.retry_attempts,
            cancel_token=token,
            ffmpeg_status=self._ffmpeg_status,
            path_registry=self._path_registry,
            cookies_browser=self.cookies_browser,
            js_runtime_path=self.js_runtime_path,
            youtube_block=self._youtube_block,
        )
        self._live_workers.add(item_id)
        self.pool.start(worker)

    def start_all_queued(self) -> None:
        for item in self.items():
            if item.status in (QueueStatus.QUEUED, QueueStatus.PAUSED, QueueStatus.FAILED):
                assert item.id is not None  # every tracked item is persisted
                self._set_status(item, QueueStatus.QUEUED)
                self.start_item(item.id)

    def cancel_item(self, item_id: int) -> None:
        self._restart_after_exit.discard(item_id)
        token = self._cancel_tokens.get(item_id)
        if token:
            token.cancelled = True
        item = self._items.get(item_id)
        if item is None:
            return
        if item.status == QueueStatus.PAUSED:
            # The worker that owned this item's partial file has already
            # exited (that's what "paused" means) -- nothing else will clean
            # it up or release its path reservation, so do it here.
            self._cleanup_paused_artifacts(item)
        if item.status != QueueStatus.DOWNLOADING:
            self._set_status(item, QueueStatus.CANCELLED)

    def pause_item(self, item_id: int) -> None:
        """Pause a queued or actively-downloading item. Unlike cancel, this
        keeps partial files (and the path reservation) so start_item() later
        resumes the same transfer via HTTP range requests instead of
        restarting from zero.
        """
        item = self._items.get(item_id)
        if item is None or item.status not in (QueueStatus.DOWNLOADING, QueueStatus.QUEUED):
            return
        self._restart_after_exit.discard(item_id)
        token = self._cancel_tokens.get(item_id)
        if token:
            # Also reaches a worker still waiting in the pool for a slot: it
            # checks the token before doing anything else and exits as
            # paused. Without this it would start downloading anyway.
            token.pause_requested = True
        # A downloading item flips to PAUSED once the worker acknowledges
        # (via the `paused` signal -> _on_paused) -- an in-flight network
        # read can't be interrupted synchronously from here. A queued one
        # hasn't started, so there's nothing to unwind: pause it now.
        if item.status == QueueStatus.QUEUED:
            self._set_status(item, QueueStatus.PAUSED)

    def _cleanup_paused_artifacts(self, item: QueueItem) -> None:
        if not item.working_path:
            return
        resolved = Path(item.working_path)
        output_stem = resolved.name.rsplit(".", 1)[0]
        DownloadWorker._cleanup_partial(resolved.parent, output_stem)
        self._path_registry.release(resolved)
        item.working_path = None
        self.db.update_queue_item(item)

    def retry_item(self, item_id: int) -> None:
        item = self._items.get(item_id)
        if item is None or item.status != QueueStatus.FAILED:
            return
        item.attempts = 0
        item.error_message = None
        self._set_status(item, QueueStatus.QUEUED)
        self.start_item(item_id)

    def remove_item(self, item_id: int) -> None:
        self.remove_items([item_id])

    def remove_items(self, item_ids: list[int]) -> None:
        for item_id in item_ids:
            item = self._items.get(item_id)
            if item and item.status == QueueStatus.PAUSED:
                # Removing a paused item directly (without cancelling it
                # first) would otherwise leak its partial file and path
                # reservation.
                self._cleanup_paused_artifacts(item)
            token = self._cancel_tokens.pop(item_id, None)
            if token:
                # Stop its worker too, running or still waiting for a slot --
                # otherwise it keeps downloading an item that's no longer in
                # the queue, invisibly. The worker deletes its own partials.
                token.cancelled = True
            self._restart_after_exit.discard(item_id)
            self._items.pop(item_id, None)
        self.db.remove_queue_items(item_ids)
        self._reindex_positions()
        self.queue_changed.emit()

    def clear_terminal_items(self) -> None:
        """Remove completed/cancelled/skipped items from the queue (keeping
        active and failed ones), so a long session doesn't leave the queue
        table cluttered with dozens of finished downloads with no bulk way
        to clear them.
        """
        terminal = (QueueStatus.COMPLETED, QueueStatus.CANCELLED, QueueStatus.SKIPPED)
        done_ids = [item_id for item_id, item in self._items.items() if item.status in terminal]
        for item_id in done_ids:
            self._cancel_tokens.pop(item_id, None)
            self._items.pop(item_id, None)
        self.db.clear_terminal_queue_items()
        self._reindex_positions()
        self.queue_changed.emit()

    def reorder(self, item_ids_in_order: list[int]) -> None:
        positions: list[tuple[int, int]] = []
        for position, item_id in enumerate(item_ids_in_order):
            item = self._items.get(item_id)
            if item:
                item.position = position
                positions.append((item_id, position))
        self.db.update_queue_positions(positions)
        self.queue_changed.emit()

    def _reindex_positions(self) -> None:
        positions: list[tuple[int, int]] = []
        for position, item in enumerate(self.items()):
            assert item.id is not None  # every tracked item is persisted
            item.position = position
            positions.append((item.id, position))
        self.db.update_queue_positions(positions)

    def _set_status(self, item: QueueItem, status: QueueStatus) -> None:
        if not can_transition(item.status, status):
            return
        item.status = status
        self.db.update_queue_item(item)
        self.item_status_changed.emit(item.id, status.value)

    def _on_status_changed(self, item_id: int, status_value: str) -> None:
        # Routed through _set_status() -- not a direct assignment -- so this
        # respects can_transition(). Without that check, a worker's queued
        # "now downloading" signal can be delivered (Qt auto-queues it across
        # threads) *after* the main thread has already cancelled the same
        # item, silently flipping its displayed status back from Cancelled
        # to Downloading until the worker's own cancellation catches up.
        item = self._items.get(item_id)
        if item:
            self._set_status(item, QueueStatus(status_value))

    def _on_progress(self, item_id: int, percent: float, speed, eta) -> None:
        item = self._items.get(item_id)
        if item:
            item.progress_percent = percent
            item.speed_bps = speed
            item.eta_seconds = eta
        self.item_progress.emit(item_id, percent, speed, eta)

    def _on_finished(self, item_id: int, saved_path: str) -> None:
        self._live_workers.discard(item_id)
        item = self._items.get(item_id)
        if not item:
            return
        item.saved_path = saved_path
        item.progress_percent = 100.0
        item.working_path = None  # done -- saved_path is the source of truth now
        self._set_status(item, QueueStatus.COMPLETED)
        self._record_history(item, QueueStatus.COMPLETED)
        self.item_finished.emit(item_id, saved_path)

    def _on_failed(self, item_id: int, message: str, is_permanent: bool) -> None:
        self._live_workers.discard(item_id)
        item = self._items.get(item_id)
        if not item:
            return
        item.error_message = message
        item.attempts += 1
        # The worker already deleted the partial file for a failure (see
        # DownloadWorker._cleanup_partial), so nothing is left to resume --
        # clear working_path so a later retry re-resolves duplicates fresh
        # rather than trying to reuse a path that no longer has data in it.
        item.working_path = None
        self._set_status(item, QueueStatus.FAILED)
        self._record_history(item, QueueStatus.FAILED)
        self.item_failed.emit(item_id, message, is_permanent)
        if ytdlp_backend.is_bot_check_error(message):
            self.youtube_blocked.emit(self._pause_pending_youtube_items())

    def _pause_pending_youtube_items(self) -> int:
        """YouTube's bot check applies to the whole network address, not one
        video: once an item hits it, every queued YouTube item would too,
        each one adding requests from an address YouTube is already
        throttling. Pause them instead, so the user can resume after waiting
        or turning on browser sign-in. Returns how many were paused."""
        paused = 0
        for item in self.items():
            if (
                item.id is not None
                and item.status == QueueStatus.QUEUED
                and detect_platform(item.request.url) == Platform.YOUTUBE
            ):
                self.pause_item(item.id)
                paused += 1
        return paused

    def _on_skipped(self, item_id: int, reason: str) -> None:
        self._live_workers.discard(item_id)
        item = self._items.get(item_id)
        if not item:
            return
        item.error_message = reason
        self._set_status(item, QueueStatus.SKIPPED)
        self._record_history(item, QueueStatus.SKIPPED)
        self.item_skipped.emit(item_id, reason)

    def _on_cancelled(self, item_id: int) -> None:
        self._live_workers.discard(item_id)
        item = self._items.get(item_id)
        if not item:
            return
        item.working_path = None  # the worker already deleted the partial file
        self._set_status(item, QueueStatus.CANCELLED)
        self._record_history(item, QueueStatus.CANCELLED)
        self.item_cancelled.emit(item_id)

    def _on_paused(self, item_id: int) -> None:
        self._live_workers.discard(item_id)
        item = self._items.get(item_id)
        if not item:
            return
        # No history entry: unlike the other worker outcomes, a pause isn't
        # a terminal result -- it's a resumable in-progress state, exactly
        # like a queued item that hasn't started yet.
        self._set_status(item, QueueStatus.PAUSED)
        if item_id in self._restart_after_exit:
            self._restart_after_exit.discard(item_id)
            self.start_item(item_id)

    def _on_working_path_resolved(self, item_id: int, path: str) -> None:
        item = self._items.get(item_id)
        if item:
            item.working_path = path
            self.db.update_queue_item(item)

    def _record_history(self, item: QueueItem, outcome: QueueStatus) -> None:
        entry = HistoryEntry(
            id=None,
            title=item.request.title,
            source_url=item.request.url,
            saved_path=item.saved_path,
            format_label=item.request.mode.value,
            timestamp_iso=_now_iso(),
            outcome=outcome,
        )
        self.db.add_history_entry(entry)

    def has_active_downloads(self) -> bool:
        return any(i.status == QueueStatus.DOWNLOADING for i in self._items.values())

    def shutdown(self, wait_ms: int = 3000) -> None:
        """Called when the app is closing with downloads still active (see
        MainWindow.closeEvent, which tells the user active items "will be
        marked paused and can be resumed"). That promise requires actually
        pausing, not cancelling: setting `cancelled` here (as an earlier
        version of this method did) would delete every in-flight item's
        partial file on the way out, silently breaking that promise.

        The status is set to PAUSED directly and synchronously (main
        thread, right now) rather than waiting for the worker's own queued
        `paused` signal, because that signal needs this thread's Qt event
        loop to still be pumping to be delivered -- and pool.waitForDone()
        below blocks this thread, so it isn't. working_path was already
        persisted earlier in the download (via working_path_resolved,
        processed during normal operation before shutdown started), so the
        in-memory item here already has what it needs to resume correctly
        next launch.
        """
        self._restart_after_exit.clear()
        for item_id, token in self._cancel_tokens.items():
            token.pause_requested = True
            item = self._items.get(item_id)
            if item and item.status == QueueStatus.DOWNLOADING:
                self._set_status(item, QueueStatus.PAUSED)
        self.pool.waitForDone(wait_ms)


def _now_iso() -> str:
    import datetime

    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def check_free_space(destination_dir: str, required_bytes: int | None) -> bool:
    """Best-effort storage check; returns True if space looks sufficient or
    unknown (we don't block a download just because we couldn't estimate size).
    """
    if not required_bytes:
        return True
    try:
        usage = shutil.disk_usage(destination_dir)
    except OSError:
        return True
    return usage.free > required_bytes
