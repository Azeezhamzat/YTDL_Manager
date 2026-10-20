from __future__ import annotations

from pathlib import Path

import pytest

from ytdlmanager.download_manager import (
    DownloadWorker,
    WorkerSignals,
    _CancelToken,
    _PathRegistry,
)
from ytdlmanager.ffmpeg_utils import FfmpegStatus
from ytdlmanager.models import DownloadMode, DownloadRequest, QueueItem, QueueStatus

_FAKE_FFMPEG = FfmpegStatus(found=True, path="/usr/bin/ffmpeg", version="fake")


def _worker(qapp, tmp_path: Path) -> tuple[DownloadWorker, _CancelToken]:
    request = DownloadRequest(
        url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        title="Cancel Me",
        destination_dir=str(tmp_path),
        mode=DownloadMode.VIDEO_WITH_AUDIO,
    )
    item = QueueItem(id=42, request=request, status=QueueStatus.QUEUED, position=0)
    token = _CancelToken()
    worker = DownloadWorker(
        item,
        WorkerSignals(),
        max_attempts=3,
        cancel_token=token,
        ffmpeg_status=_FAKE_FFMPEG,
        path_registry=_PathRegistry(),
    )
    return worker, token


def test_progress_hook_raises_when_cancelled(qapp, tmp_path):
    import yt_dlp.utils

    worker, token = _worker(qapp, tmp_path)
    hook = worker._make_progress_hook(item_id=42)
    token.cancelled = True

    with pytest.raises(yt_dlp.utils.DownloadCancelled):
        hook({"status": "downloading", "downloaded_bytes": 10, "total_bytes": 100})


def test_progress_hook_does_not_raise_when_not_cancelled(qapp, tmp_path):
    worker, token = _worker(qapp, tmp_path)
    hook = worker._make_progress_hook(item_id=42)
    received = []
    worker.signals.progress.connect(lambda *args: received.append(args))

    hook({"status": "downloading", "downloaded_bytes": 50, "total_bytes": 100})

    assert received == [(42, 50.0, None, None)]


def test_cleanup_partial_removes_part_files(qapp, tmp_path):
    (tmp_path / "My Video.mp4.part").write_text("partial")
    (tmp_path / "My Video.f137.mp4.part").write_text("partial")
    (tmp_path / "My Video.mp4.ytdl").write_text("meta")
    keep = tmp_path / "unrelated.mp4"
    keep.write_text("do not touch")

    DownloadWorker._cleanup_partial(tmp_path, "My Video")

    remaining = {p.name for p in tmp_path.iterdir()}
    assert remaining == {"unrelated.mp4"}
    assert keep.exists()


def test_cancel_before_start_marks_queued_item_cancelled(qapp, tmp_path):
    from ytdlmanager.db import Database
    from ytdlmanager.download_manager import DownloadManager

    db = Database(tmp_path / "db.sqlite3")
    try:
        manager = DownloadManager(db, concurrency=1)
        request = DownloadRequest(
            url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            title="Never Started",
            destination_dir=str(tmp_path),
            mode=DownloadMode.VIDEO_WITH_AUDIO,
        )
        item = manager.enqueue(request)
        manager.cancel_item(item.id)
        updated = next(i for i in manager.items() if i.id == item.id)
        assert updated.status == QueueStatus.CANCELLED
    finally:
        db.close()
