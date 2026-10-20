"""Schema migration for existing databases created before `working_path`
was added to the `queue` table.

This matters for real users, not just fresh installs: `CREATE TABLE IF NOT
EXISTS` only creates the table the first time a database file is made -- an
existing database (e.g. one already sitting in a user's real app-data
folder from before this column existed) keeps its old schema forever unless
the app explicitly ALTERs it. Simulated here by hand-building a database
with the pre-migration schema, exactly like a real pre-existing install.
"""

from __future__ import annotations

import sqlite3

from ytdlmanager.db import Database
from ytdlmanager.models import DownloadMode, DownloadRequest, QueueItem, QueueStatus

_OLD_SCHEMA = """
CREATE TABLE queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_json TEXT NOT NULL,
    status TEXT NOT NULL,
    progress_percent REAL NOT NULL DEFAULT 0,
    error_message TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    saved_path TEXT,
    position INTEGER NOT NULL DEFAULT 0
);
"""


def _make_pre_migration_db(path) -> None:
    conn = sqlite3.connect(str(path))
    try:
        conn.executescript(_OLD_SCHEMA)
        conn.commit()
    finally:
        conn.close()


def test_opening_a_pre_existing_old_schema_db_does_not_crash(tmp_db_path):
    _make_pre_migration_db(tmp_db_path)

    db = Database(tmp_db_path)  # must not raise
    try:
        assert db.list_queue() == []
    finally:
        db.close()


def test_migration_adds_working_path_column(tmp_db_path):
    _make_pre_migration_db(tmp_db_path)

    db = Database(tmp_db_path)
    try:
        columns = {row["name"] for row in db._conn.execute("PRAGMA table_info(queue)")}
        assert "working_path" in columns
    finally:
        db.close()


def test_pre_existing_queue_rows_survive_migration_with_null_working_path(tmp_db_path):
    _make_pre_migration_db(tmp_db_path)
    conn = sqlite3.connect(str(tmp_db_path))
    conn.execute(
        "INSERT INTO queue(request_json, status, progress_percent, attempts, position) "
        "VALUES ('{\"url\":\"https://x\",\"title\":\"Old Row\",\"destination_dir\":\"/tmp\","
        "\"mode\":\"video_audio\"}', 'queued', 0, 0, 0)"
    )
    conn.commit()
    conn.close()

    db = Database(tmp_db_path)
    try:
        items = db.list_queue()
        assert len(items) == 1
        assert items[0].request.title == "Old Row"
        assert items[0].working_path is None  # backfilled as NULL, not a crash
    finally:
        db.close()


def test_new_field_round_trips_after_migration(tmp_db_path):
    _make_pre_migration_db(tmp_db_path)

    db = Database(tmp_db_path)
    try:
        request = DownloadRequest(
            url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            title="Resume Me",
            destination_dir="/tmp",
            mode=DownloadMode.VIDEO_WITH_AUDIO,
        )
        item = QueueItem(id=None, request=request, status=QueueStatus.QUEUED, position=0)
        item.id = db.add_queue_item(item)
        item.working_path = "/tmp/Resume Me.mp4"
        db.update_queue_item(item)

        reloaded = db.list_queue()
        assert reloaded[0].working_path == "/tmp/Resume Me.mp4"
    finally:
        db.close()


def test_reopening_an_already_migrated_db_is_a_no_op(tmp_db_path):
    # Migrating twice (e.g. across app restarts) must not error or duplicate
    # columns.
    db1 = Database(tmp_db_path)
    db1.close()
    db2 = Database(tmp_db_path)  # must not raise "duplicate column"
    db2.close()
