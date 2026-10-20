from __future__ import annotations

from ytdlmanager.db import Database
from ytdlmanager.models import HistoryEntry, QueueStatus


def _entry(**overrides) -> HistoryEntry:
    base = dict(
        id=None,
        title="My Video",
        source_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        saved_path="/tmp/downloads/My Video.mp4",
        format_label="video_audio",
        timestamp_iso="2026-01-01T12:00:00+00:00",
        outcome=QueueStatus.COMPLETED,
    )
    base.update(overrides)
    return HistoryEntry(**base)


def test_add_and_list_history_entry(tmp_db_path):
    db = Database(tmp_db_path)
    try:
        entry_id = db.add_history_entry(_entry())
        assert entry_id > 0
        history = db.list_history()
        assert len(history) == 1
        assert history[0].title == "My Video"
        assert history[0].outcome == QueueStatus.COMPLETED
    finally:
        db.close()


def test_history_ordered_most_recent_first(tmp_db_path):
    db = Database(tmp_db_path)
    try:
        db.add_history_entry(_entry(title="First"))
        db.add_history_entry(_entry(title="Second"))
        history = db.list_history()
        assert [e.title for e in history] == ["Second", "First"]
    finally:
        db.close()


def test_clear_history_does_not_touch_files(tmp_db_path, tmp_path):
    real_file = tmp_path / "video.mp4"
    real_file.write_text("not really a video")

    db = Database(tmp_db_path)
    try:
        db.add_history_entry(_entry(saved_path=str(real_file)))
        assert len(db.list_history()) == 1

        db.clear_history()

        assert db.list_history() == []
        assert real_file.exists()  # the file itself must survive clearing history
    finally:
        db.close()


def test_history_persists_failed_and_skipped_outcomes(tmp_db_path):
    db = Database(tmp_db_path)
    try:
        db.add_history_entry(_entry(title="Failed one", outcome=QueueStatus.FAILED))
        db.add_history_entry(_entry(title="Skipped one", outcome=QueueStatus.SKIPPED))
        outcomes = {e.title: e.outcome for e in db.list_history()}
        assert outcomes["Failed one"] == QueueStatus.FAILED
        assert outcomes["Skipped one"] == QueueStatus.SKIPPED
    finally:
        db.close()
