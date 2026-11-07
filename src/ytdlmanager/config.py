"""Application directories and constants.

We avoid pulling in an extra dependency (e.g. `platformdirs`) for something
this small: a handful of `if sys.platform` branches is easy to read, easy to
test, and keeps the dependency list minimal per the project requirements.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "YTDLManager"


def _windows_app_data() -> Path:
    base = os.environ.get("APPDATA")
    if base:
        return Path(base) / APP_NAME
    return Path.home() / "AppData" / "Roaming" / APP_NAME


def _macos_app_support() -> Path:
    return Path.home() / "Library" / "Application Support" / APP_NAME


def _linux_data_home() -> Path:
    base = os.environ.get("XDG_DATA_HOME")
    if base:
        return Path(base) / "ytdlmanager"
    return Path.home() / ".local" / "share" / "ytdlmanager"


def get_app_data_dir() -> Path:
    """Return the per-OS directory for this app's local data (db, logs, config)."""
    if sys.platform == "win32":
        d = _windows_app_data()
    elif sys.platform == "darwin":
        d = _macos_app_support()
    else:
        d = _linux_data_home()
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_default_download_dir() -> Path:
    """Return a sensible default download destination (~/Downloads/YTDLManager)."""
    downloads = Path.home() / "Downloads"
    target = downloads if downloads.is_dir() else Path.home()
    target = target / "YTDLManager"
    target.mkdir(parents=True, exist_ok=True)
    return target


def get_db_path() -> Path:
    return get_app_data_dir() / "ytdlmanager.sqlite3"


def get_log_path() -> Path:
    return get_app_data_dir() / "ytdlmanager.log"


DEFAULT_CONCURRENCY = 3
MAX_CONCURRENCY = 8
DEFAULT_RETRY_ATTEMPTS = 3
DEFAULT_RETRY_BASE_DELAY_SECONDS = 2.0
DEFAULT_RETRY_MAX_DELAY_SECONDS = 30.0
MAX_FILENAME_LENGTH = 150
# UTF-8 byte budget for a sanitised title; Linux allows 255 bytes per name
# and later additions (playlist number, duplicate counter, yt-dlp's temp
# suffix) need about 30 more. See sanitize.sanitize_filename.
MAX_FILENAME_BYTES = 200
LEGAL_NOTICE = (
    "Only download videos you own, that are in the public domain, that carry a "
    "licence permitting download, or for which you otherwise have the rights "
    "holder's explicit permission. This application does not bypass DRM, "
    "CAPTCHAs, paywalls, or other access controls. By default it uses no "
    "logins or cookies; if you turn on YouTube sign-in in Settings, YouTube "
    "requests use your browser's signed-in YouTube session (and nothing is "
    "sent to other sites). It cannot guarantee that every item on YouTube, "
    "TikTok, Twitter/X, Instagram, Facebook, or Vimeo is legally or "
    "technically downloadable."
)

SUPPORTED_PLATFORMS_LABEL = "YouTube, TikTok, Twitter/X, Instagram, Facebook, Vimeo"
