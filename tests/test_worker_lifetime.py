"""Regression test for a real, reproducible Qt/PySide6 lifetime bug found
while wiring up the auto-update-check feature: a QRunnable worker that owns
its *own* signals QObject (rather than sharing one long-lived signals object,
the way DownloadWorker shares DownloadManager's `_signals`) can have its
Python wrapper garbage collected the instant the `worker` local variable
goes out of scope -- which happens right after `pool.start(worker)` returns,
well before the background thread has actually run and emitted anything.

When that happens, the cross-thread queued signal is silently dropped (in
the worst case, forcing a GC collection at just the wrong moment segfaults
the interpreter via a use-after-free). This looks identical, from the user's
side, to the very first bug fixed in this app: "the button doesn't do
anything." MetadataFetchWorker, UpdateCheckWorker, and UpdateInstallWorker
all create a private per-instance signals object and are dispatched from a
local `worker` variable in a MainWindow method -- so MainWindow keeps every
in-flight worker referenced in `self._active_workers` for the app session's
lifetime specifically to prevent this.
"""

from __future__ import annotations

from PySide6.QtCore import QEventLoop, QObject, QRunnable, QThreadPool, QTimer, Signal


class _Signals(QObject):
    finished = Signal(int)


class _InstantWorker(QRunnable):
    """Emits immediately -- the shortest possible window for the GC race."""

    def __init__(self, value: int) -> None:
        super().__init__()
        self.signals = _Signals()
        self.value = value
        self.setAutoDelete(True)

    def run(self) -> None:
        self.signals.finished.emit(self.value)


def _dispatch_with_keepalive(
    pool: QThreadPool, value: int, received: list, keepalive: list
) -> None:
    worker = _InstantWorker(value)
    worker.signals.finished.connect(received.append)
    keepalive.append(worker)  # <-- the fix under test
    pool.start(worker)


def _run_event_loop_for(qapp, ms: int) -> None:
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def test_active_workers_keepalive_prevents_lost_signal(qapp):
    """The actual fix: holding a reference in a list until (at least) after
    the worker has run guarantees the signal survives to be delivered.
    """
    pool = QThreadPool()
    received: list[int] = []
    keepalive: list[QRunnable] = []

    for i in range(10):
        _dispatch_with_keepalive(pool, i, received, keepalive)

    _run_event_loop_for(qapp, 500)

    assert sorted(received) == list(range(10)), (
        "every dispatched worker's signal must be delivered when the worker "
        "is kept alive -- lost signals here would reproduce the exact "
        "silent-failure class this test guards against"
    )


def test_main_window_update_check_dispatch_registers_worker_in_active_workers(
    qapp, monkeypatch, tmp_db_path
):
    """Wires the fix into the real dispatch path: `_check_for_updates` must
    append its worker to `self._active_workers` before starting it on
    `self._metadata_pool`, exactly like the metadata-fetch and
    update-install dispatch sites.

    Dispatched with `manual=False` (the quiet startup-check path) and an
    "up to date" result deliberately -- `manual=True` would pop a real
    `QMessageBox.information` dialog on completion, which blocks forever
    under the offscreen Qt platform this test suite runs under (there's no
    display to click "OK" on).
    """
    import ytdlmanager.download_manager as download_manager_module
    from ytdlmanager import update_check
    from ytdlmanager.app import _default_settings
    from ytdlmanager.db import Database
    from ytdlmanager.ffmpeg_utils import FfmpegStatus
    from ytdlmanager.ui import main_window as main_window_module

    monkeypatch.setattr(
        download_manager_module,
        "find_ffmpeg",
        lambda *a, **k: FfmpegStatus(found=True, path="/usr/bin/ffmpeg", version="fake"),
    )
    # No real network in the routine test suite -- check_for_yt_dlp_update()
    # is called from the worker thread via `update_check.check_for_yt_dlp_update`,
    # so patch the module attribute rather than an import binding.
    monkeypatch.setattr(
        update_check,
        "check_for_yt_dlp_update",
        lambda: update_check.UpdateCheckResult(
            installed_version="1.0", latest_version="1.0", update_available=False, error=None
        ),
    )

    db = Database(tmp_db_path)
    try:
        settings = _default_settings()
        settings.check_for_updates = False
        window = main_window_module.MainWindow(db, settings)

        assert window._active_workers == []
        window._check_for_updates(manual=False)
        assert len(window._active_workers) == 1

        _run_event_loop_for(qapp, 500)
    finally:
        db.close()
