"""Pause/resume: pausing keeps partial files and the path reservation so a
later resume continues the same transfer (via yt-dlp's HTTP range-request
resume) instead of restarting from zero -- unlike cancel, which always
deletes partial files. These tests cover the worker-level mechanics (what
actually gets kept vs cleaned up, and which signal fires) and the
manager-level bookkeeping (working_path persistence, cleanup when a paused
item is cancelled/removed instead of resumed, and the app-shutdown path).
"""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import patch

from ytdlmanager import ytdlp_backend
from ytdlmanager.db import Database
from ytdlmanager.download_manager import (
    DownloadManager,
    DownloadWorker,
    WorkerSignals,
    _CancelToken,
    _PathRegistry,
)
from ytdlmanager.ffmpeg_utils import FfmpegStatus
from ytdlmanager.models import DownloadMode, DownloadRequest, QueueItem, QueueStatus

_FAKE_FFMPEG = FfmpegStatus(found=True, path="/usr/bin/ffmpeg", version="fake")


def _request(**overrides) -> DownloadRequest:
    base = dict(
        url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        title="Pause Me",
        destination_dir="/tmp",
        mode=DownloadMode.VIDEO_WITH_AUDIO,
    )
    base.update(overrides)
    return DownloadRequest(**base)


def _worker(
    tmp_path: Path,
    *,
    working_path: str | None = None,
    registry: _PathRegistry | None = None,
):
    request = _request(destination_dir=str(tmp_path))
    item = QueueItem(
        id=42, request=request, status=QueueStatus.QUEUED, position=0, working_path=working_path
    )
    token = _CancelToken()
    worker = DownloadWorker(
        item,
        WorkerSignals(),
        max_attempts=3,
        cancel_token=token,
        ffmpeg_status=_FAKE_FFMPEG,
        path_registry=registry or _PathRegistry(),
    )
    return worker, token


# -- _CancelToken -----------------------------------------------------------


def test_cancel_token_sleep_wakes_on_pause_requested():
    token = _CancelToken()

    def pause_soon():
        time.sleep(0.2)
        token.pause_requested = True

    import threading

    threading.Thread(target=pause_soon).start()
    start = time.monotonic()
    token.sleep(10.0)
    assert time.monotonic() - start < 2.0


# -- _PathRegistry.claim_exact ------------------------------------------------


def test_claim_exact_succeeds_even_if_path_exists_on_disk(tmp_path):
    registry = _PathRegistry()
    path = tmp_path / "video.mp4.part"
    path.write_text("partial data")

    registry.claim_exact(path)  # must not raise, must not check existence first

    assert registry.is_claimed(path)


def test_claim_exact_makes_path_unavailable_to_a_plain_reserve(tmp_path):
    registry = _PathRegistry()
    path = tmp_path / "video.mp4"
    registry.claim_exact(path)

    # A different item trying to claim the exact same (non-existent-on-disk)
    # path via the normal atomic reserve() must be turned away -- it's held
    # by the resuming item, not actually free.
    assert registry.reserve(path, allow_rename=False) is None


def test_claim_exact_is_idempotent(tmp_path):
    registry = _PathRegistry()
    path = tmp_path / "video.mp4"
    registry.claim_exact(path)
    registry.claim_exact(path)  # must not raise
    registry.release(path)
    # After a single release, it should be free again (not double-counted).
    reserved = registry.reserve(path, allow_rename=False)
    assert reserved == path


# -- worker-level pause behaviour --------------------------------------------


def test_pause_requested_before_start_keeps_files_and_emits_paused(tmp_path):
    worker, token = _worker(tmp_path)
    final_path = tmp_path / "Pause Me.mp4"
    (tmp_path / "Pause Me.mp4.part").write_text("partial")

    events = {"paused": [], "cancelled": []}
    worker.signals.paused.connect(lambda item_id: events["paused"].append(item_id))
    worker.signals.cancelled.connect(lambda item_id: events["cancelled"].append(item_id))

    token.pause_requested = True
    paused = worker._run_attempts(worker.item.request, tmp_path, "Pause Me", str(final_path))

    assert paused is True
    assert events["paused"] == [42]
    assert events["cancelled"] == []
    assert (tmp_path / "Pause Me.mp4.part").exists()  # NOT cleaned up


def test_cancelled_before_start_deletes_files_and_emits_cancelled(tmp_path):
    worker, token = _worker(tmp_path)
    final_path = tmp_path / "Pause Me.mp4"
    (tmp_path / "Pause Me.mp4.part").write_text("partial")

    events = {"paused": [], "cancelled": []}
    worker.signals.paused.connect(lambda item_id: events["paused"].append(item_id))
    worker.signals.cancelled.connect(lambda item_id: events["cancelled"].append(item_id))

    token.cancelled = True
    paused = worker._run_attempts(worker.item.request, tmp_path, "Pause Me", str(final_path))

    assert paused is False
    assert events["cancelled"] == [42]
    assert events["paused"] == []
    assert not (tmp_path / "Pause Me.mp4.part").exists()  # cleaned up


def test_cancel_wins_when_both_pause_and_cancel_are_set(tmp_path):
    worker, token = _worker(tmp_path)
    final_path = tmp_path / "Pause Me.mp4"

    events = {"paused": [], "cancelled": []}
    worker.signals.paused.connect(lambda item_id: events["paused"].append(item_id))
    worker.signals.cancelled.connect(lambda item_id: events["cancelled"].append(item_id))

    token.pause_requested = True
    token.cancelled = True
    paused = worker._run_attempts(worker.item.request, tmp_path, "Pause Me", str(final_path))

    assert paused is False
    assert events["cancelled"] == [42]
    assert events["paused"] == []


def test_download_cancelled_error_mid_transfer_treated_as_pause(tmp_path):
    worker, token = _worker(tmp_path)
    final_path = tmp_path / "Pause Me.mp4"
    (tmp_path / "Pause Me.mp4.part").write_text("partial")

    def fake_download_one(*args, **kwargs):
        # Simulate the progress hook raising mid-transfer because a pause
        # was requested while data was actively being written.
        token.pause_requested = True
        raise ytdlp_backend.DownloadCancelledError("paused by user")

    events = {"paused": [], "cancelled": []}
    worker.signals.paused.connect(lambda item_id: events["paused"].append(item_id))
    worker.signals.cancelled.connect(lambda item_id: events["cancelled"].append(item_id))

    with patch.object(ytdlp_backend, "download_one", fake_download_one):
        paused = worker._run_attempts(worker.item.request, tmp_path, "Pause Me", str(final_path))

    assert paused is True
    assert events["paused"] == [42]
    assert (tmp_path / "Pause Me.mp4.part").exists()


def test_working_path_resolved_signal_emitted_on_fresh_start(tmp_path):
    worker, _token = _worker(tmp_path)
    resolved_paths = []
    worker.signals.working_path_resolved.connect(
        lambda item_id, path: resolved_paths.append((item_id, path))
    )

    # Fake the transfer so run() doesn't actually try to hit the network.
    with patch.object(ytdlp_backend, "download_one", lambda *a, **k: str(tmp_path / "x.mp4")):
        worker.run()

    assert len(resolved_paths) == 1
    item_id, path = resolved_paths[0]
    assert item_id == 42
    assert path == str(tmp_path / "Pause Me.mp4")


def test_worker_paused_before_it_starts_claims_no_path(tmp_path):
    # Paused while still waiting in the pool for a slot: the worker must exit
    # as paused without reserving a filename it will never use.
    worker, token = _worker(tmp_path)
    resolved_paths, paused = [], []
    worker.signals.working_path_resolved.connect(lambda i, p: resolved_paths.append(p))
    worker.signals.paused.connect(paused.append)
    token.pause_requested = True

    worker.run()

    assert paused == [42]
    assert resolved_paths == []
    assert not worker.path_registry.is_claimed(tmp_path / "Pause Me.mp4")


def test_resuming_reuses_working_path_without_reresolving(tmp_path):
    existing_partial = tmp_path / "Pause Me.mp4.part"
    existing_partial.write_text("partial data from before")
    working_path = str(tmp_path / "Pause Me.mp4")

    registry = _PathRegistry()
    worker, _token = _worker(tmp_path, working_path=working_path, registry=registry)
    worker.cancel_token.pause_requested = True  # pause again immediately, don't hit network

    resolved_paths = []
    worker.signals.working_path_resolved.connect(
        lambda item_id, path: resolved_paths.append((item_id, path))
    )

    worker.run()

    # Reusing a known working_path must NOT re-run duplicate resolution --
    # so no fresh working_path_resolved signal fires, and the exact same
    # partial file is left untouched (not renamed to "Pause Me (1).mp4" or
    # similar, which _resolve_duplicate would do if it ran again while the
    # registry still shows the path as reserved).
    assert resolved_paths == []
    assert existing_partial.exists()


# -- manager-level bookkeeping ------------------------------------------------


def test_pause_item_on_queued_item_is_synchronous(tmp_db_path, tmp_path, qapp):
    db = Database(tmp_db_path)
    try:
        manager = DownloadManager(db, concurrency=1)
        item = manager.enqueue(_request(destination_dir=str(tmp_path)))

        manager.pause_item(item.id)

        assert manager.get_item(item.id).status == QueueStatus.PAUSED
    finally:
        db.close()


def test_pause_item_on_downloading_item_only_sets_token(tmp_db_path, tmp_path, qapp):
    db = Database(tmp_db_path)
    try:
        manager = DownloadManager(db, concurrency=1)
        item = manager.enqueue(_request(destination_dir=str(tmp_path)))
        manager._set_status(item, QueueStatus.DOWNLOADING)
        token = _CancelToken()
        manager._cancel_tokens[item.id] = token

        manager.pause_item(item.id)

        # Status does NOT flip synchronously -- only the worker's own
        # `paused` signal (via _on_paused) does that, once it acknowledges.
        assert manager.get_item(item.id).status == QueueStatus.DOWNLOADING
        assert token.pause_requested is True
    finally:
        db.close()


def test_on_paused_transitions_status_without_history_entry(tmp_db_path, tmp_path, qapp):
    db = Database(tmp_db_path)
    try:
        manager = DownloadManager(db, concurrency=1)
        item = manager.enqueue(_request(destination_dir=str(tmp_path)))
        manager._set_status(item, QueueStatus.DOWNLOADING)

        manager._on_paused(item.id)

        assert manager.get_item(item.id).status == QueueStatus.PAUSED
        assert db.list_history() == []  # pausing is not a terminal outcome
    finally:
        db.close()


def test_cancel_item_on_paused_item_cleans_up_partial_file(tmp_db_path, tmp_path, qapp):
    db = Database(tmp_db_path)
    try:
        manager = DownloadManager(db, concurrency=1)
        item = manager.enqueue(_request(destination_dir=str(tmp_path)))
        manager._set_status(item, QueueStatus.DOWNLOADING)
        working_path = tmp_path / "Pause Me.mp4"
        (tmp_path / "Pause Me.mp4.part").write_text("partial")
        item.working_path = str(working_path)
        manager._path_registry.claim_exact(working_path)
        manager._on_paused(item.id)

        manager.cancel_item(item.id)

        assert manager.get_item(item.id).status == QueueStatus.CANCELLED
        assert manager.get_item(item.id).working_path is None
        assert not (tmp_path / "Pause Me.mp4.part").exists()
        assert not manager._path_registry.is_claimed(working_path)
        # Consistent with cancelling any other non-actively-downloading item
        # (e.g. a QUEUED one): this cancel is synchronous, on the main
        # thread, not driven by the worker's async _on_cancelled -- and only
        # that path records history. Not a pause-specific behavior.
        assert db.list_history() == []
    finally:
        db.close()


def test_remove_item_on_paused_item_cleans_up_partial_file(tmp_db_path, tmp_path, qapp):
    db = Database(tmp_db_path)
    try:
        manager = DownloadManager(db, concurrency=1)
        item = manager.enqueue(_request(destination_dir=str(tmp_path)))
        manager._set_status(item, QueueStatus.DOWNLOADING)
        working_path = tmp_path / "Pause Me.mp4"
        (tmp_path / "Pause Me.mp4.part").write_text("partial")
        item.working_path = str(working_path)
        manager._path_registry.claim_exact(working_path)
        manager._on_paused(item.id)

        manager.remove_item(item.id)

        assert manager.get_item(item.id) is None
        assert not (tmp_path / "Pause Me.mp4.part").exists()
    finally:
        db.close()


def test_on_working_path_resolved_persists_to_db(tmp_db_path, tmp_path, qapp):
    db = Database(tmp_db_path)
    try:
        manager = DownloadManager(db, concurrency=1)
        item = manager.enqueue(_request(destination_dir=str(tmp_path)))

        manager._on_working_path_resolved(item.id, str(tmp_path / "Pause Me.mp4"))

        assert manager.get_item(item.id).working_path == str(tmp_path / "Pause Me.mp4")
        reloaded = Database(tmp_db_path)
        try:
            reloaded_item = next(i for i in reloaded.list_queue() if i.id == item.id)
            assert reloaded_item.working_path == str(tmp_path / "Pause Me.mp4")
        finally:
            reloaded.close()
    finally:
        db.close()


def test_on_finished_clears_working_path(tmp_db_path, tmp_path, qapp):
    db = Database(tmp_db_path)
    try:
        manager = DownloadManager(db, concurrency=1)
        item = manager.enqueue(_request(destination_dir=str(tmp_path)))
        item.working_path = str(tmp_path / "Pause Me.mp4")

        manager._on_finished(item.id, str(tmp_path / "Pause Me.mp4"))

        assert manager.get_item(item.id).working_path is None
    finally:
        db.close()


def test_on_failed_clears_working_path(tmp_db_path, tmp_path, qapp):
    db = Database(tmp_db_path)
    try:
        manager = DownloadManager(db, concurrency=1)
        item = manager.enqueue(_request(destination_dir=str(tmp_path)))
        item.working_path = str(tmp_path / "Pause Me.mp4")

        manager._on_failed(item.id, "some error", True)

        assert manager.get_item(item.id).working_path is None
    finally:
        db.close()


def test_shutdown_pauses_active_downloads_without_deleting_files(tmp_db_path, tmp_path, qapp):
    """Regression: shutdown() used to set `cancelled = True` for every
    in-flight item, which deletes partial files -- directly contradicting
    the close-confirmation dialog's promise that active items "will be
    marked paused and can be resumed". Verifies the fix: shutdown marks
    DOWNLOADING items PAUSED (not CANCELLED) and never touches their files.
    """
    db = Database(tmp_db_path)
    try:
        manager = DownloadManager(db, concurrency=1)
        item = manager.enqueue(_request(destination_dir=str(tmp_path)))
        manager._set_status(item, QueueStatus.DOWNLOADING)
        token = _CancelToken()
        manager._cancel_tokens[item.id] = token
        partial = tmp_path / "Pause Me.mp4.part"
        partial.write_text("partial data")
        item.working_path = str(tmp_path / "Pause Me.mp4")

        manager.shutdown(wait_ms=100)

        assert manager.get_item(item.id).status == QueueStatus.PAUSED
        assert token.pause_requested is True
        assert token.cancelled is False
        assert partial.exists()  # the actual point of the fix
    finally:
        db.close()
