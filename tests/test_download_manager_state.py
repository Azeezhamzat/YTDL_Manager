"""DownloadManager-level bug fixes: O(1) lookup, bulk cleanup of finished
items, the worker-status-vs-cancel race, and cancellable retry backoff.
"""

from __future__ import annotations

import time

from ytdlmanager.db import Database
from ytdlmanager.download_manager import DownloadManager, _CancelToken
from ytdlmanager.models import DownloadMode, DownloadRequest, QueueStatus


def _request(title="Item", **overrides) -> DownloadRequest:
    base = dict(
        url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        title=title,
        destination_dir="/tmp",
        mode=DownloadMode.VIDEO_WITH_AUDIO,
    )
    base.update(overrides)
    return DownloadRequest(**base)


def test_get_item_returns_none_for_unknown_id(qapp, tmp_db_path):
    db = Database(tmp_db_path)
    try:
        manager = DownloadManager(db, concurrency=1)
        assert manager.get_item(999) is None
    finally:
        db.close()


def test_get_item_returns_the_enqueued_item(qapp, tmp_db_path):
    db = Database(tmp_db_path)
    try:
        manager = DownloadManager(db, concurrency=1)
        item = manager.enqueue(_request("Findable"))
        found = manager.get_item(item.id)
        assert found is not None
        assert found.request.title == "Findable"
    finally:
        db.close()


def test_clear_terminal_items_removes_completed_cancelled_skipped(qapp, tmp_db_path):
    db = Database(tmp_db_path)
    try:
        manager = DownloadManager(db, concurrency=1)
        completed = manager.enqueue(_request("Completed"))
        cancelled = manager.enqueue(_request("Cancelled"))
        skipped = manager.enqueue(_request("Skipped"))
        failed = manager.enqueue(_request("Failed"))
        manager.enqueue(_request("Queued"))  # left QUEUED, must survive the clear

        manager._set_status(completed, QueueStatus.DOWNLOADING)
        manager._set_status(completed, QueueStatus.COMPLETED)
        manager._set_status(cancelled, QueueStatus.CANCELLED)
        manager._set_status(skipped, QueueStatus.SKIPPED)
        manager._set_status(failed, QueueStatus.DOWNLOADING)
        manager._set_status(failed, QueueStatus.FAILED)

        manager.clear_terminal_items()

        remaining_titles = {i.request.title for i in manager.items()}
        assert remaining_titles == {"Failed", "Queued"}
        # And the removal is actually persisted, not just in-memory.
        reloaded = DownloadManager(db, concurrency=1)
        reloaded.load_persisted_queue()
        assert {i.request.title for i in reloaded.items()} == {"Failed", "Queued"}
    finally:
        db.close()


def test_clear_terminal_items_keeps_active_downloads(qapp, tmp_db_path):
    db = Database(tmp_db_path)
    try:
        manager = DownloadManager(db, concurrency=1)
        active = manager.enqueue(_request("Active"))
        manager._set_status(active, QueueStatus.DOWNLOADING)

        manager.clear_terminal_items()

        assert manager.get_item(active.id) is not None
        assert manager.get_item(active.id).status == QueueStatus.DOWNLOADING
    finally:
        db.close()


def test_worker_status_signal_cannot_override_a_cancelled_item(qapp, tmp_db_path):
    """Regression: _on_status_changed used to assign item.status directly,
    bypassing can_transition(). A worker's queued "now downloading" signal
    delivered *after* the main thread already cancelled the same item would
    silently flip the displayed status back from Cancelled to Downloading.

    This is exactly cancel_item()'s QUEUED-item path: cancelling an item
    that hasn't started downloading yet (its worker is still between the
    cancel-token check and the "downloading" signal emit) transitions it to
    Cancelled synchronously on the main thread, while the worker's own
    queued status_changed signal can still be delivered afterwards.
    """
    db = Database(tmp_db_path)
    try:
        manager = DownloadManager(db, concurrency=1)
        item = manager.enqueue(_request("Raced"))
        manager.cancel_item(item.id)  # still QUEUED -> cancels synchronously
        assert manager.get_item(item.id).status == QueueStatus.CANCELLED

        # Simulate the worker's queued "downloading" signal arriving late,
        # exactly as it would via WorkerSignals.status_changed after Qt
        # delivers a cross-thread queued connection.
        manager._on_status_changed(item.id, QueueStatus.DOWNLOADING.value)

        assert manager.get_item(item.id).status == QueueStatus.CANCELLED
    finally:
        db.close()


def test_worker_status_signal_still_applies_valid_transitions(qapp, tmp_db_path):
    db = Database(tmp_db_path)
    try:
        manager = DownloadManager(db, concurrency=1)
        item = manager.enqueue(_request("Normal"))
        manager._on_status_changed(item.id, QueueStatus.DOWNLOADING.value)
        assert manager.get_item(item.id).status == QueueStatus.DOWNLOADING
    finally:
        db.close()


def test_cancel_token_sleep_returns_promptly_once_cancelled():
    token = _CancelToken()
    token.cancelled = True
    start = time.monotonic()
    token.sleep(30.0)  # would block 30s with a plain time.sleep()
    elapsed = time.monotonic() - start
    assert elapsed < 1.0


def test_cancel_token_sleep_wakes_early_when_cancelled_mid_sleep():
    import threading

    token = _CancelToken()

    def cancel_soon():
        time.sleep(0.3)
        token.cancelled = True

    threading.Thread(target=cancel_soon).start()
    start = time.monotonic()
    token.sleep(10.0)
    elapsed = time.monotonic() - start
    assert elapsed < 2.0  # woke up because of cancellation, not the full 10s


def test_cancel_token_sleep_runs_full_duration_when_not_cancelled():
    token = _CancelToken()
    start = time.monotonic()
    token.sleep(0.3)
    elapsed = time.monotonic() - start
    assert elapsed >= 0.25
