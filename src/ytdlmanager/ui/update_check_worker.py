"""Background workers so the yt-dlp version check and the "Update Now"
pip install never block the GUI thread. The former is a network call
(seconds, sometimes); the latter can easily take tens of seconds.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, QRunnable, Signal

from .. import update_check


class UpdateCheckSignals(QObject):
    finished = Signal(object, bool)  # update_check.UpdateCheckResult, manual


class UpdateCheckWorker(QRunnable):
    def __init__(self, *, manual: bool) -> None:
        super().__init__()
        # `manual` rides along on the signal itself (rather than being
        # captured in a lambda at the call site) so the connection can go
        # straight to a bound method -- that's what lets PySide6 detect the
        # cross-thread signal and auto-upgrade it to a queued connection.
        # A lambda has no QObject thread affinity for Qt to inspect, so it
        # would silently fall back to a same-thread direct call, running the
        # slot (and its GUI widget access) on this background thread instead
        # of the main/GUI thread.
        self.manual = manual
        self.signals = UpdateCheckSignals()
        self.setAutoDelete(True)

    def run(self) -> None:
        # check_for_yt_dlp_update() never raises -- any network/parsing
        # failure comes back as a result with `error` set instead.
        result = update_check.check_for_yt_dlp_update()
        self.signals.finished.emit(result, self.manual)


class UpdateInstallSignals(QObject):
    finished = Signal(bool, str)  # success, output_or_error_message


class UpdateInstallWorker(QRunnable):
    def __init__(self) -> None:
        super().__init__()
        self.signals = UpdateInstallSignals()
        self.setAutoDelete(True)

    def run(self) -> None:
        success, output = update_check.update_yt_dlp_in_current_environment()
        self.signals.finished.emit(success, output)
