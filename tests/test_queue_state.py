from __future__ import annotations

from ytdlmanager.db import Database
from ytdlmanager.models import (
    DownloadMode,
    DownloadRequest,
    DuplicatePolicy,
    QueueItem,
    QueueStatus,
    can_transition,
)


def _request() -> DownloadRequest:
    return DownloadRequest(
        url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        title="Sample",
        destination_dir="/tmp/downloads",
        mode=DownloadMode.VIDEO_WITH_AUDIO,
        duplicate_policy=DuplicatePolicy.RENAME,
    )


def test_queued_can_move_to_downloading():
    assert can_transition(QueueStatus.QUEUED, QueueStatus.DOWNLOADING) is True


def test_completed_is_terminal():
    assert can_transition(QueueStatus.COMPLETED, QueueStatus.DOWNLOADING) is False
    assert can_transition(QueueStatus.COMPLETED, QueueStatus.QUEUED) is False


def test_downloading_cannot_jump_directly_to_queued():
    # Must go through paused/cancelled/failed first, not skip straight back.
    assert can_transition(QueueStatus.DOWNLOADING, QueueStatus.QUEUED) is False


def test_failed_can_be_retried_by_requeueing():
    assert can_transition(QueueStatus.FAILED, QueueStatus.QUEUED) is True


def test_paused_can_resume_or_cancel():
    assert can_transition(QueueStatus.PAUSED, QueueStatus.DOWNLOADING) is True
    assert can_transition(QueueStatus.PAUSED, QueueStatus.CANCELLED) is True


def test_same_state_is_always_allowed():
    for status in QueueStatus:
        assert can_transition(status, status) is True


def test_queue_persistence_round_trip(tmp_db_path):
    db = Database(tmp_db_path)
    try:
        item = QueueItem(id=None, request=_request(), status=QueueStatus.QUEUED, position=0)
        item.id = db.add_queue_item(item)

        loaded = db.list_queue()
        assert len(loaded) == 1
        assert loaded[0].request.title == "Sample"
        assert loaded[0].status == QueueStatus.QUEUED
    finally:
        db.close()


def test_interrupted_downloading_item_restored_as_paused(tmp_db_path):
    """A session that crashed mid-download must not silently resume: the item
    should come back as PAUSED, requiring an explicit user action to restart.
    """
    db = Database(tmp_db_path)
    try:
        item = QueueItem(id=None, request=_request(), status=QueueStatus.QUEUED, position=0)
        item.id = db.add_queue_item(item)
        item.status = QueueStatus.DOWNLOADING
        db.update_queue_item(item)
        db.close()

        db2 = Database(tmp_db_path)
        try:
            loaded = db2.list_queue()
            assert loaded[0].status == QueueStatus.PAUSED
        finally:
            db2.close()
    finally:
        pass


def test_remove_queue_item(tmp_db_path):
    db = Database(tmp_db_path)
    try:
        item = QueueItem(id=None, request=_request(), status=QueueStatus.QUEUED, position=0)
        item.id = db.add_queue_item(item)
        db.remove_queue_item(item.id)
        assert db.list_queue() == []
    finally:
        db.close()


def test_clear_terminal_queue_items_keeps_active_and_failed(tmp_db_path):
    db = Database(tmp_db_path)
    try:
        completed = QueueItem(id=None, request=_request(), status=QueueStatus.COMPLETED, position=0)
        failed = QueueItem(id=None, request=_request(), status=QueueStatus.FAILED, position=1)
        queued = QueueItem(id=None, request=_request(), status=QueueStatus.QUEUED, position=2)
        for item in (completed, failed, queued):
            item.id = db.add_queue_item(item)

        db.clear_terminal_queue_items()

        remaining_statuses = {i.status for i in db.list_queue()}
        assert remaining_statuses == {QueueStatus.FAILED, QueueStatus.QUEUED}
    finally:
        db.close()
