"""Application bootstrap: logging, database, settings, and the Qt event loop."""

from __future__ import annotations

import logging
import sys

from PySide6.QtWidgets import QApplication

from . import __version__
from .config import (
    DEFAULT_CONCURRENCY,
    DEFAULT_RETRY_ATTEMPTS,
    get_db_path,
    get_default_download_dir,
    get_log_path,
)
from .db import Database
from .models import AppSettings, DuplicatePolicy
from .ui.icons import app_icon
from .ui.main_window import MainWindow


def _build_log_handlers(log_path) -> list[logging.Handler]:
    handlers: list[logging.Handler] = [logging.FileHandler(log_path, encoding="utf-8")]
    # A windowed (console=False) PyInstaller build on Windows has no real
    # console attached, so sys.stderr can be None rather than a stream --
    # logging.StreamHandler() defaults to sys.stderr at construction time,
    # and would otherwise be a handler that can never actually write. The
    # file handler above is the log's real source of truth either way.
    if sys.stderr is not None:
        handlers.append(logging.StreamHandler())
    return handlers


def _setup_logging() -> None:
    log_path = get_log_path()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=_build_log_handlers(log_path),
    )
    logging.getLogger(__name__).info("Starting YTDLManager, logging to %s", log_path)


def _default_settings() -> AppSettings:
    return AppSettings(
        default_download_dir=str(get_default_download_dir()),
        concurrency=DEFAULT_CONCURRENCY,
        duplicate_policy=DuplicatePolicy.RENAME,
        theme="system",
        preserve_playlist_order=True,
        retry_attempts=DEFAULT_RETRY_ATTEMPTS,
    )


def run() -> int:
    _setup_logging()
    app = QApplication(sys.argv)
    app.setApplicationName("YTDLManager")
    app.setApplicationDisplayName("YTDL Manager")
    app.setApplicationVersion(__version__)
    app.setOrganizationName("YTDLManager")
    # Linux desktops match the running window to its .desktop entry (and so
    # its icon in the dock/taskbar) by this name; see packaging/linux.
    app.setDesktopFileName("ytdlmanager")
    # Same base style on every OS so the app's stylesheet (ui/theme.py)
    # renders consistently; set before any widget exists.
    app.setStyle("Fusion")
    app.setWindowIcon(app_icon())

    db = Database(get_db_path())
    try:
        settings = db.load_settings(_default_settings())
        window = MainWindow(db, settings)
        window.show()
        return app.exec()
    finally:
        db.close()
