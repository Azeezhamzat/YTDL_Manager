"""Regression tests for the queue's worker lifecycle: at most one worker per
item, and Pause/Cancel/Remove reaching workers that are still waiting in the
pool for a free slot.

Each test drives a real DownloadManager (real QThreadPool, real signals)
against a fake `download_one` that ticks the progress hook until released,
so a "transfer" stays in flight for as long as a test needs it to.
"""

from __future__ import annotations

import threading
import time
from collections import Counter
from pathlib import Path

import pytest

from ytdlmanager import download_manager as dm
from ytdlmanager import ytdlp_backend
from ytdlmanager.db import Database
from ytdlmanager.download_manager import DownloadManager, DownloadWorker
from ytdlmanager.ffmpeg_utils import FfmpegStatus
from ytdlmanager.models import DownloadMode, DownloadRequest, QueueStatus

_YOUTUBE_URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
_VIMEO_URL = "https://vimeo.com/76979871"
_BOT_MESSAGE = (
    "ERROR: [youtube] dQw4w9WgXcQ: Sign in to confirm you’re not a bot. "
    "Use --cookies-from-browser or --cookies for the authentication."
)


class FakeTransfers:
    """Stands in for ytdlp_backend.download_one, counting calls per title."""

    def __init__(self) -> None:
        self.calls: Counter[str] = Counter()
        self.release = threading.Event()
        self.fail_with: dict[str, Exception] = {}
        self._lock = threading.Lock()

    def __call__(self, request, *, output_template, progress_hook=None, **_kwargs):
        with self._lock:
            self.calls[request.title] += 1
        if request.title in self.fail_with:
            raise self.fail_with[request.title]
        while not self.release.is_set():
            progress_hook({"status": "downloading", "downloaded_bytes": 1, "total_bytes": 10})
            time.sleep(0.01)
        return output_template.replace("%(ext)s", "mp4")


@pytest.fixture
def transfers(monkeypatch):
    fake = FakeTransfers()
    monkeypatch.setattr(ytdlp_backend, "download_one", fake)
    monkeypatch.setattr(dm, "find_ffmpeg", lambda: FfmpegStatus(True, "/usr/bin/ffmpeg", "x"))
    monkeypatch.setattr(dm, "find_deno", lambda: None)
    yield fake
    fake.release.set()


@pytest.fixture
def manager(tmp_db_path, qapp, transfers):
    db = Database(tmp_db_path)
    mgr = DownloadManager(db, concurrency=1)
    yield mgr
    transfers.release.set()
    mgr.pool.waitForDone(5000)
    db.close()


def pump(qapp, until=None, timeout=3.0) -> None:
    """Process queued cross-thread signals until `until()` or the timeout."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        qapp.processEvents()
        if until is not None and until():
            return
        time.sleep(0.01)


def _request(title: str, tmp_path: Path, url: str = _YOUTUBE_URL) -> DownloadRequest:
    return DownloadRequest(
        url=url, title=title, destination_dir=str(tmp_path), mode=DownloadMode.VIDEO_WITH_AUDIO
    )


def _status(manager: DownloadManager, item_id: int) -> QueueStatus:
    item = manager.get_item(item_id)
    assert item is not None
    return item.status


def _finish_all(manager, transfers, qapp) -> None:
    transfers.release.set()
    manager.pool.waitForDone(5000)
    pump(qapp, timeout=0.3)


def test_start_all_while_items_wait_for_a_slot_downloads_each_once(
    manager, transfers, tmp_path, qapp
):
    items = manager.enqueue_many([_request(f"A{i}", tmp_path) for i in range(3)])
    for item in items:
        manager.start_item(item.id)
    pump(qapp, lambda: transfers.calls["A0"] == 1)

    manager.start_all_queued()
    _finish_all(manager, transfers, qapp)

    assert transfers.calls == Counter({"A0": 1, "A1": 1, "A2": 1})
    assert all(_status(manager, i.id) == QueueStatus.COMPLETED for i in items)


def test_resume_on_a_downloading_item_does_not_start_a_second_worker(
    manager, transfers, tmp_path, qapp
):
    item = manager.enqueue(_request("B", tmp_path))
    manager.start_item(item.id)
    pump(qapp, lambda: _status(manager, item.id) == QueueStatus.DOWNLOADING)

    manager.start_item(item.id)  # "Resume Selected" on a running item
    pump(qapp, timeout=0.2)
    manager.cancel_item(item.id)
    manager.pool.waitForDone(3000)  # would time out if an orphaned worker kept going
    pump(qapp, timeout=0.2)

    assert transfers.calls["B"] == 1
    assert manager.pool.activeThreadCount() == 0
    assert _status(manager, item.id) == QueueStatus.CANCELLED


def test_pausing_an_item_waiting_for_a_slot_stops_it_from_downloading(
    manager, transfers, tmp_path, qapp
):
    running = manager.enqueue(_request("C-running", tmp_path))
    waiting = manager.enqueue(_request("C-waiting", tmp_path))
    manager.start_item(running.id)
    manager.start_item(waiting.id)
    pump(qapp, lambda: transfers.calls["C-running"] == 1)

    manager.pause_item(waiting.id)
    manager.cancel_item(running.id)  # frees the only slot
    pump(qapp, lambda: manager.pool.activeThreadCount() == 0)
    pump(qapp, timeout=0.2)

    assert transfers.calls["C-waiting"] == 0
    assert _status(manager, waiting.id) == QueueStatus.PAUSED

    # ...and resuming it later downloads it exactly once.
    manager.start_item(waiting.id)
    _finish_all(manager, transfers, qapp)
    assert transfers.calls["C-waiting"] == 1
    assert _status(manager, waiting.id) == QueueStatus.COMPLETED


def test_resume_during_a_pending_pause_restarts_once_the_worker_exits(
    manager, transfers, tmp_path, qapp
):
    item = manager.enqueue(_request("H", tmp_path))
    manager.start_item(item.id)
    pump(qapp, lambda: _status(manager, item.id) == QueueStatus.DOWNLOADING)

    manager.pause_item(item.id)
    manager.start_item(item.id)  # resumed before the worker acknowledged the pause
    pump(qapp, lambda: transfers.calls["H"] == 2)
    _finish_all(manager, transfers, qapp)

    assert transfers.calls["H"] == 2  # the paused transfer, then its resumption
    assert _status(manager, item.id) == QueueStatus.COMPLETED


def test_removing_a_downloading_item_stops_its_worker(manager, transfers, tmp_path, qapp):
    item = manager.enqueue(_request("D", tmp_path))
    manager.start_item(item.id)
    pump(qapp, lambda: _status(manager, item.id) == QueueStatus.DOWNLOADING)

    manager.remove_items([item.id])

    # The transfer is never released: only cancellation can end it.
    assert manager.pool.waitForDone(3000)
    pump(qapp, timeout=0.2)
    assert manager.items() == []
    assert manager.db.list_history() == []


def test_removing_an_item_waiting_for_a_slot_never_downloads_it(
    manager, transfers, tmp_path, qapp
):
    running = manager.enqueue(_request("D-running", tmp_path))
    waiting = manager.enqueue(_request("D-waiting", tmp_path))
    manager.start_item(running.id)
    manager.start_item(waiting.id)
    pump(qapp, lambda: transfers.calls["D-running"] == 1)

    manager.remove_items([waiting.id])
    _finish_all(manager, transfers, qapp)

    assert transfers.calls["D-waiting"] == 0


def test_remove_items_keeps_positions_contiguous(manager, tmp_path):
    items = manager.enqueue_many([_request(f"R{i}", tmp_path) for i in range(6)])

    manager.remove_items([items[1].id, items[4].id])

    remaining = manager.db.list_queue()
    assert [i.position for i in remaining] == [0, 1, 2, 3]
    assert [i.request.title for i in remaining] == ["R0", "R2", "R3", "R5"]


def test_worker_crash_before_download_reports_failure_instead_of_hanging(
    manager, tmp_path, qapp, monkeypatch
):
    item = manager.enqueue(_request("E", tmp_path))

    def unwritable(*_args, **_kwargs):
        raise PermissionError(13, "Permission denied", "/Volumes/Unplugged")

    monkeypatch.setattr(Path, "mkdir", unwritable)
    manager.start_item(item.id)
    pump(qapp, lambda: _status(manager, item.id) == QueueStatus.FAILED)

    failed = manager.get_item(item.id)
    assert failed is not None and failed.status == QueueStatus.FAILED
    assert "Permission denied" in (failed.error_message or "")
    assert not manager._path_registry.is_claimed(tmp_path / "E.mp4")


def test_bot_check_pauses_queued_youtube_items_only(manager, transfers, tmp_path, qapp):
    transfers.fail_with["Y-blocked"] = ytdlp_backend.DownloadError(
        _BOT_MESSAGE, is_permanent=ytdlp_backend.classify_error(_BOT_MESSAGE)
    )
    blocked_events: list[int] = []
    manager.youtube_blocked.connect(blocked_events.append)

    first = manager.enqueue(_request("Y-blocked", tmp_path))
    youtube_waiting = manager.enqueue(_request("Y-waiting", tmp_path))
    vimeo_waiting = manager.enqueue(_request("V-waiting", tmp_path, url=_VIMEO_URL))
    for item in (first, youtube_waiting, vimeo_waiting):
        manager.start_item(item.id)
    pump(qapp, lambda: _status(manager, first.id) == QueueStatus.FAILED)
    _finish_all(manager, transfers, qapp)

    assert transfers.calls["Y-blocked"] == 1  # permanent: no retries
    assert blocked_events == [1]
    assert _status(manager, youtube_waiting.id) == QueueStatus.PAUSED
    assert transfers.calls["Y-waiting"] == 0
    assert _status(manager, vimeo_waiting.id) == QueueStatus.COMPLETED


def test_cleanup_partial_handles_brackets_in_titles(tmp_path):
    stem = "Artist - Song [Official Video]"
    partials = [f"{stem}.f137.mp4.part", f"{stem}.f251.webm.part", f"{stem}.mp4.ytdl"]
    for name in partials:
        (tmp_path / name).write_text("x")
    (tmp_path / f"{stem}.mp4").write_text("finished file of another download")

    DownloadWorker._cleanup_partial(tmp_path, stem)

    assert sorted(p.name for p in tmp_path.iterdir()) == [f"{stem}.mp4"]


class _OrderRecorder(FakeTransfers):
    def __init__(self) -> None:
        super().__init__()
        self.started: list[str] = []

    def __call__(self, request, **kwargs):
        self.started.append(request.title)
        return super().__call__(request, **kwargs)


def test_queue_order_decides_what_downloads_next(tmp_db_path, qapp, monkeypatch, tmp_path):
    # Move up/down used to change only the display: every item had already
    # been handed to the thread pool, which ran them in the order added.
    recorder = _OrderRecorder()
    monkeypatch.setattr(ytdlp_backend, "download_one", recorder)
    monkeypatch.setattr(dm, "find_ffmpeg", lambda: FfmpegStatus(True, "/usr/bin/ffmpeg", "x"))
    monkeypatch.setattr(dm, "find_deno", lambda: None)
    db = Database(tmp_db_path)
    manager = DownloadManager(db, concurrency=1)
    try:
        items = manager.enqueue_many([_request(f"O{i}", tmp_path) for i in range(4)])
        for item in items:
            manager.start_item(item.id)
        pump(qapp, lambda: recorder.started == ["O0"])

        # While O0 runs, move O3 to the front of what's left.
        ids = [i.id for i in items]
        manager.reorder([ids[0], ids[3], ids[1], ids[2]])
        recorder.release.set()
        manager.pool.waitForDone(5000)
        pump(qapp, lambda: len(recorder.started) == 4, timeout=5)

        assert recorder.started == ["O0", "O3", "O1", "O2"]
    finally:
        recorder.release.set()
        manager.pool.waitForDone(5000)
        db.close()


def test_raising_parallel_downloads_starts_waiting_items_now(manager, transfers, tmp_path, qapp):
    items = manager.enqueue_many([_request(f"P{i}", tmp_path) for i in range(3)])
    for item in items:
        manager.start_item(item.id)
    pump(qapp, lambda: transfers.calls["P0"] == 1)
    assert transfers.calls["P1"] == 0  # concurrency is 1

    manager.set_concurrency(3)
    pump(qapp, lambda: transfers.calls["P1"] == 1 and transfers.calls["P2"] == 1)

    assert manager.pool.activeThreadCount() == 3
    _finish_all(manager, transfers, qapp)


def test_queue_finished_reports_counts_once_the_queue_runs_dry(
    manager, transfers, tmp_path, qapp
):
    transfers.fail_with["F-bad"] = ytdlp_backend.DownloadError("HTTP Error 404", is_permanent=True)
    finished: list[tuple[int, int]] = []
    manager.queue_finished.connect(lambda done, failed: finished.append((done, failed)))
    items = manager.enqueue_many([_request("F-good", tmp_path), _request("F-bad", tmp_path)])
    for item in items:
        manager.start_item(item.id)

    _finish_all(manager, transfers, qapp)
    pump(qapp, lambda: bool(finished))

    assert finished == [(1, 1)]


def test_pausing_everything_is_not_reported_as_finished(manager, transfers, tmp_path, qapp):
    finished: list[tuple[int, int]] = []
    manager.queue_finished.connect(lambda done, failed: finished.append((done, failed)))
    item = manager.enqueue(_request("Z", tmp_path))
    manager.start_item(item.id)
    pump(qapp, lambda: _status(manager, item.id) == QueueStatus.DOWNLOADING)

    manager.pause_item(item.id)
    pump(qapp, lambda: _status(manager, item.id) == QueueStatus.PAUSED)

    assert finished == []
