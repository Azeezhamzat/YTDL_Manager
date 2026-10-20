from __future__ import annotations

from pathlib import Path

from ytdlmanager.download_manager import (
    DownloadWorker,
    WorkerSignals,
    _CancelToken,
    _PathRegistry,
    expected_final_path,
)
from ytdlmanager.ffmpeg_utils import FfmpegStatus
from ytdlmanager.models import (
    DownloadMode,
    DownloadRequest,
    DuplicatePolicy,
    QueueItem,
    QueueStatus,
)

_FAKE_FFMPEG = FfmpegStatus(found=True, path="/usr/bin/ffmpeg", version="fake")


def _make_worker(
    qapp, tmp_path: Path, duplicate_policy: DuplicatePolicy, registry: _PathRegistry | None = None
) -> DownloadWorker:
    request = DownloadRequest(
        url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        title="My Video",
        destination_dir=str(tmp_path),
        mode=DownloadMode.VIDEO_WITH_AUDIO,
        duplicate_policy=duplicate_policy,
    )
    item = QueueItem(id=1, request=request, status=QueueStatus.QUEUED, position=0)
    return DownloadWorker(
        item,
        WorkerSignals(),
        max_attempts=3,
        cancel_token=_CancelToken(),
        ffmpeg_status=_FAKE_FFMPEG,
        path_registry=registry or _PathRegistry(),
    )


def test_expected_final_path_uses_sanitized_title(qapp, tmp_path):
    request = DownloadRequest(
        url="https://www.youtube.com/watch?v=x",
        title="Weird: Title?.mp4",
        destination_dir=str(tmp_path),
        mode=DownloadMode.VIDEO_WITH_AUDIO,
    )
    item = QueueItem(id=1, request=request, status=QueueStatus.QUEUED, position=0)
    path = expected_final_path(item)
    assert path.parent == tmp_path
    assert path.suffix == ".mp4"
    assert ":" not in path.name and "?" not in path.name


def test_overwrite_policy_never_skips_even_if_file_exists(qapp, tmp_path):
    worker = _make_worker(qapp, tmp_path, DuplicatePolicy.OVERWRITE)
    final_path = expected_final_path(worker.item)
    final_path.write_text("existing")

    resolved, skip = worker._resolve_duplicate(final_path, worker.item.request)
    assert skip is False
    assert resolved == final_path


def test_skip_policy_skips_when_file_exists(qapp, tmp_path):
    worker = _make_worker(qapp, tmp_path, DuplicatePolicy.SKIP)
    final_path = expected_final_path(worker.item)
    final_path.write_text("existing")

    resolved, skip = worker._resolve_duplicate(final_path, worker.item.request)
    assert skip is True


def test_skip_policy_does_not_skip_when_file_absent(qapp, tmp_path):
    worker = _make_worker(qapp, tmp_path, DuplicatePolicy.SKIP)
    final_path = expected_final_path(worker.item)

    resolved, skip = worker._resolve_duplicate(final_path, worker.item.request)
    assert skip is False
    assert resolved == final_path


def test_rename_policy_produces_unique_path_when_file_exists(qapp, tmp_path):
    worker = _make_worker(qapp, tmp_path, DuplicatePolicy.RENAME)
    final_path = expected_final_path(worker.item)
    final_path.write_text("existing")

    resolved, skip = worker._resolve_duplicate(final_path, worker.item.request)
    assert skip is False
    assert resolved != final_path
    assert resolved.name == "My Video (1).mp4"


def test_rename_policy_unchanged_when_no_collision(qapp, tmp_path):
    worker = _make_worker(qapp, tmp_path, DuplicatePolicy.RENAME)
    final_path = expected_final_path(worker.item)

    resolved, skip = worker._resolve_duplicate(final_path, worker.item.request)
    assert skip is False
    assert resolved == final_path


# -- concurrent duplicate-title races -----------------------------------
# Two queue items with identical sanitised titles can legitimately run at
# the same time (duplicate uploads, generic titles). Neither file exists on
# disk until its download finishes, so resolving duplicates against the
# filesystem alone is a time-of-check/time-of-use race: both workers could
# pick the same "free" name and one would silently clobber the other's
# output. A shared _PathRegistry closes that window -- these tests simulate
# the race by having a *second* worker resolve against a registry the first
# worker already claimed a path in, without either file existing on disk yet
# (mirroring the moment both downloads are still in flight).


def test_rename_policy_second_concurrent_item_gets_different_path(qapp, tmp_path):
    registry = _PathRegistry()
    worker_a = _make_worker(qapp, tmp_path, DuplicatePolicy.RENAME, registry=registry)
    worker_b = _make_worker(qapp, tmp_path, DuplicatePolicy.RENAME, registry=registry)
    final_path = expected_final_path(worker_a.item)
    assert not final_path.exists()  # neither download has actually written anything yet

    resolved_a, skip_a = worker_a._resolve_duplicate(final_path, worker_a.item.request)
    resolved_b, skip_b = worker_b._resolve_duplicate(final_path, worker_b.item.request)

    assert not skip_a and not skip_b
    assert resolved_a == final_path
    assert resolved_b != final_path
    assert resolved_b.name == "My Video (1).mp4"


def test_skip_policy_second_concurrent_item_is_skipped(qapp, tmp_path):
    registry = _PathRegistry()
    worker_a = _make_worker(qapp, tmp_path, DuplicatePolicy.SKIP, registry=registry)
    worker_b = _make_worker(qapp, tmp_path, DuplicatePolicy.SKIP, registry=registry)
    final_path = expected_final_path(worker_a.item)

    resolved_a, skip_a = worker_a._resolve_duplicate(final_path, worker_a.item.request)
    resolved_b, skip_b = worker_b._resolve_duplicate(final_path, worker_b.item.request)

    assert skip_a is False
    assert skip_b is True  # the path was already claimed by worker_a


def test_releasing_a_reservation_makes_it_available_again(qapp, tmp_path):
    registry = _PathRegistry()
    worker_a = _make_worker(qapp, tmp_path, DuplicatePolicy.RENAME, registry=registry)
    final_path = expected_final_path(worker_a.item)

    resolved_a, _ = worker_a._resolve_duplicate(final_path, worker_a.item.request)
    registry.release(resolved_a)  # e.g. worker_a's download failed/was cancelled

    worker_b = _make_worker(qapp, tmp_path, DuplicatePolicy.RENAME, registry=registry)
    resolved_b, skip_b = worker_b._resolve_duplicate(final_path, worker_b.item.request)
    assert skip_b is False
    assert resolved_b == final_path  # freed up, so it's available again
