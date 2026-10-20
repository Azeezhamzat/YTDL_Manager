from __future__ import annotations

from ytdlmanager.db import Database
from ytdlmanager.models import AppSettings, DuplicatePolicy


def _sample_settings(**overrides) -> AppSettings:
    base = dict(
        default_download_dir="/tmp/downloads",
        concurrency=4,
        duplicate_policy=DuplicatePolicy.SKIP,
        theme="dark",
        preserve_playlist_order=False,
        retry_attempts=5,
    )
    base.update(overrides)
    return AppSettings(**base)


def test_settings_round_trip(tmp_db_path):
    db = Database(tmp_db_path)
    try:
        original = _sample_settings()
        db.save_settings(original)
        loaded = db.load_settings(_sample_settings(concurrency=99))
        assert loaded == original
    finally:
        db.close()


def test_settings_persist_across_reconnect(tmp_db_path):
    db1 = Database(tmp_db_path)
    original = _sample_settings(theme="light", concurrency=2)
    db1.save_settings(original)
    db1.close()

    db2 = Database(tmp_db_path)
    try:
        loaded = db2.load_settings(_sample_settings(theme="system"))
        assert loaded.theme == "light"
        assert loaded.concurrency == 2
    finally:
        db2.close()


def test_load_settings_returns_defaults_when_empty(tmp_db_path):
    db = Database(tmp_db_path)
    try:
        defaults = _sample_settings()
        loaded = db.load_settings(defaults)
        assert loaded == defaults
    finally:
        db.close()


def test_save_settings_overwrites_previous_values(tmp_db_path):
    db = Database(tmp_db_path)
    try:
        db.save_settings(_sample_settings(concurrency=1))
        db.save_settings(_sample_settings(concurrency=7))
        loaded = db.load_settings(_sample_settings())
        assert loaded.concurrency == 7
    finally:
        db.close()
