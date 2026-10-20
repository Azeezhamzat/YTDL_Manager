"""SQLite persistence for settings, download history, and the queue.

A single small file-based database is enough here: this is a local, single-
user desktop app with modest write volume, so we avoid the operational
overhead of a client/server database. `sqlite3` is stdlib, so it adds no
dependency.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path

from .models import (
    AppSettings,
    DownloadMode,
    DownloadRequest,
    DuplicatePolicy,
    HistoryEntry,
    QueueItem,
    QueueStatus,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    source_url TEXT NOT NULL,
    saved_path TEXT,
    format_label TEXT NOT NULL,
    timestamp_iso TEXT NOT NULL,
    outcome TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS queue (
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

# Columns added after the initial release. `CREATE TABLE IF NOT EXISTS`
# above only creates the table on a brand-new database -- an existing
# database file (from before a column was added) keeps its old schema
# forever unless we explicitly ALTER it. Each entry is (column, DDL type).
_QUEUE_COLUMN_MIGRATIONS: list[tuple[str, str]] = [
    ("working_path", "TEXT"),
]


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._conn = sqlite3.connect(str(path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        with self._conn:
            self._conn.executescript(SCHEMA)
            self._migrate_queue_columns()

    def _migrate_queue_columns(self) -> None:
        existing = {row["name"] for row in self._conn.execute("PRAGMA table_info(queue)")}
        with self._conn:
            for column, ddl_type in _QUEUE_COLUMN_MIGRATIONS:
                if column not in existing:
                    self._conn.execute(f"ALTER TABLE queue ADD COLUMN {column} {ddl_type}")

    def close(self) -> None:
        self._conn.close()

    # -- settings -----------------------------------------------------
    def save_settings(self, settings: AppSettings) -> None:
        rows = [
            ("default_download_dir", settings.default_download_dir),
            ("concurrency", str(settings.concurrency)),
            ("duplicate_policy", settings.duplicate_policy.value),
            ("theme", settings.theme),
            ("preserve_playlist_order", json.dumps(settings.preserve_playlist_order)),
            ("retry_attempts", str(settings.retry_attempts)),
            ("check_for_updates", json.dumps(settings.check_for_updates)),
            ("cookies_browser", settings.cookies_browser or ""),
        ]
        with self._conn:
            self._conn.executemany(
                "INSERT INTO settings(key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                rows,
            )

    def load_settings(self, defaults: AppSettings) -> AppSettings:
        with closing(self._conn.execute("SELECT key, value FROM settings")) as cur:
            rows = {row["key"]: row["value"] for row in cur.fetchall()}
        if not rows:
            return defaults
        return AppSettings(
            default_download_dir=rows.get("default_download_dir", defaults.default_download_dir),
            concurrency=int(rows.get("concurrency", defaults.concurrency)),
            duplicate_policy=DuplicatePolicy(
                rows.get("duplicate_policy", defaults.duplicate_policy.value)
            ),
            theme=rows.get("theme", defaults.theme),
            preserve_playlist_order=json.loads(
                rows.get(
                    "preserve_playlist_order", json.dumps(defaults.preserve_playlist_order)
                )
            ),
            retry_attempts=int(rows.get("retry_attempts", defaults.retry_attempts)),
            check_for_updates=json.loads(
                rows.get("check_for_updates", json.dumps(defaults.check_for_updates))
            ),
            cookies_browser=rows.get("cookies_browser") or defaults.cookies_browser,
        )

    # -- history --------------------------------------------------------
    def add_history_entry(self, entry: HistoryEntry) -> int:
        with self._conn:
            cur = self._conn.execute(
                "INSERT INTO history(title, source_url, saved_path, format_label, "
                "timestamp_iso, outcome) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    entry.title,
                    entry.source_url,
                    entry.saved_path,
                    entry.format_label,
                    entry.timestamp_iso,
                    entry.outcome.value,
                ),
            )
            assert cur.lastrowid is not None  # guaranteed right after an INSERT
            return cur.lastrowid

    def list_history(self) -> list[HistoryEntry]:
        with closing(
            self._conn.execute("SELECT * FROM history ORDER BY id DESC")
        ) as cur:
            return [_row_to_history(row) for row in cur.fetchall()]

    def clear_history(self) -> None:
        """Delete history rows only. Never touches files on disk."""
        with self._conn:
            self._conn.execute("DELETE FROM history")

    # -- queue ------------------------------------------------------------
    def add_queue_item(self, item: QueueItem) -> int:
        with self._conn:
            cur = self._conn.execute(
                "INSERT INTO queue(request_json, status, progress_percent, "
                "error_message, attempts, saved_path, position, working_path) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    _request_to_json(item.request),
                    item.status.value,
                    item.progress_percent,
                    item.error_message,
                    item.attempts,
                    item.saved_path,
                    item.position,
                    item.working_path,
                ),
            )
            assert cur.lastrowid is not None  # guaranteed right after an INSERT
            return cur.lastrowid

    def update_queue_item(self, item: QueueItem) -> None:
        if item.id is None:
            raise ValueError("Cannot update a queue item without an id")
        with self._conn:
            self._conn.execute(
                "UPDATE queue SET request_json = ?, status = ?, progress_percent = ?, "
                "error_message = ?, attempts = ?, saved_path = ?, position = ?, "
                "working_path = ? WHERE id = ?",
                (
                    _request_to_json(item.request),
                    item.status.value,
                    item.progress_percent,
                    item.error_message,
                    item.attempts,
                    item.saved_path,
                    item.position,
                    item.working_path,
                    item.id,
                ),
            )

    def remove_queue_item(self, item_id: int) -> None:
        self.remove_queue_items([item_id])

    def remove_queue_items(self, item_ids: list[int]) -> None:
        with self._conn:
            self._conn.executemany("DELETE FROM queue WHERE id = ?", [(i,) for i in item_ids])

    def update_queue_positions(self, positions: list[tuple[int, int]]) -> None:
        """Set many items' positions in one transaction. `positions` is a
        list of (item_id, position). A commit per row (update_queue_item in a
        loop) took ~20s to remove 100 rows from a 500-item queue."""
        with self._conn:
            self._conn.executemany(
                "UPDATE queue SET position = ? WHERE id = ?",
                [(position, item_id) for item_id, position in positions],
            )

    def list_queue(self) -> list[QueueItem]:
        with closing(
            self._conn.execute("SELECT * FROM queue ORDER BY position ASC, id ASC")
        ) as cur:
            return [_row_to_queue_item(row) for row in cur.fetchall()]

    def clear_terminal_queue_items(self) -> None:
        """Remove completed/cancelled/skipped items, keeping active/failed ones."""
        with self._conn:
            self._conn.execute(
                "DELETE FROM queue WHERE status IN (?, ?, ?)",
                (
                    QueueStatus.COMPLETED.value,
                    QueueStatus.CANCELLED.value,
                    QueueStatus.SKIPPED.value,
                ),
            )


def _request_to_json(request: DownloadRequest) -> str:
    return json.dumps(
        {
            "url": request.url,
            "title": request.title,
            "destination_dir": request.destination_dir,
            "mode": request.mode.value,
            "format_id": request.format_id,
            "playlist_id": request.playlist_id,
            "playlist_index": request.playlist_index,
            "duplicate_policy": request.duplicate_policy.value,
            "preserve_playlist_order": request.preserve_playlist_order,
            "container_ext": request.container_ext,
            "subtitle_langs": request.subtitle_langs,
            "subtitles_only": request.subtitles_only,
        }
    )


def _request_from_json(raw: str) -> DownloadRequest:
    data = json.loads(raw)
    return DownloadRequest(
        url=data["url"],
        title=data["title"],
        destination_dir=data["destination_dir"],
        mode=DownloadMode(data["mode"]),
        format_id=data.get("format_id"),
        playlist_id=data.get("playlist_id"),
        playlist_index=data.get("playlist_index"),
        duplicate_policy=DuplicatePolicy(data.get("duplicate_policy", "rename")),
        preserve_playlist_order=data.get("preserve_playlist_order", False),
        container_ext=data.get("container_ext"),
        subtitle_langs=data.get("subtitle_langs"),
        subtitles_only=data.get("subtitles_only", False),
    )


def _row_to_history(row: sqlite3.Row) -> HistoryEntry:
    return HistoryEntry(
        id=row["id"],
        title=row["title"],
        source_url=row["source_url"],
        saved_path=row["saved_path"],
        format_label=row["format_label"],
        timestamp_iso=row["timestamp_iso"],
        outcome=QueueStatus(row["outcome"]),
    )


def _row_to_queue_item(row: sqlite3.Row) -> QueueItem:
    # Anything left "downloading" from a previous session that crashed or was
    # killed is restored as "paused" rather than "downloading", so the queue
    # never auto-resumes a transfer without the user explicitly starting it.
    status = QueueStatus(row["status"])
    if status == QueueStatus.DOWNLOADING:
        status = QueueStatus.PAUSED

    return QueueItem(
        id=row["id"],
        request=_request_from_json(row["request_json"]),
        status=status,
        progress_percent=row["progress_percent"],
        error_message=row["error_message"],
        attempts=row["attempts"],
        saved_path=row["saved_path"],
        position=row["position"],
        working_path=row["working_path"],
    )
