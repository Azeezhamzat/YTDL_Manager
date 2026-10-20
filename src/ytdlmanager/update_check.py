"""Check whether a newer yt-dlp release is available on PyPI.

yt-dlp needs to stay current because every supported platform changes its
site often enough to break extraction (see README > "Keeping yt-dlp up to
date"). This is a deliberate, disclosed network call -- not analytics: no
user data is sent, it's a public "what's the latest version" query -- and
it's user-togglable in Settings (default on, since checking for this was an
explicit request, but a user who wants zero non-essential network calls can
turn it off).

No new dependency needed for version comparison: yt-dlp's versioning is
plain CalVer (`YYYY.MM.DD` or `YYYY.MM.DD.N` for a same-day re-release),
which compares correctly as a tuple of ints without needing `packaging`.
"""

from __future__ import annotations

import json
import subprocess
import sys
import urllib.request
from dataclasses import dataclass

_PYPI_URL = "https://pypi.org/pypi/yt-dlp/json"
_TIMEOUT_SECONDS = 5


@dataclass
class UpdateCheckResult:
    installed_version: str
    latest_version: str | None
    update_available: bool
    error: str | None = None


def get_installed_yt_dlp_version() -> str:
    import yt_dlp

    return yt_dlp.version.__version__


def is_frozen() -> bool:
    """True when running as a PyInstaller-frozen build, where yt-dlp can't
    be updated in place with pip -- a frozen bundle has no live
    site-packages to install into. See README > Known limitations.
    """
    return bool(getattr(sys, "frozen", False))


def _version_tuple(value: str) -> tuple[int, ...] | None:
    try:
        return tuple(int(part) for part in value.split("."))
    except ValueError:
        return None


def is_newer_version(latest: str, current: str) -> bool:
    latest_t = _version_tuple(latest)
    current_t = _version_tuple(current)
    if latest_t is not None and current_t is not None:
        return latest_t > current_t
    # Fall back to a plain inequality check if either string doesn't parse
    # as the expected dotted-integer scheme -- still reports "different"
    # rather than silently assuming "up to date".
    return latest != current


def check_for_yt_dlp_update() -> UpdateCheckResult:
    """Never raises: any network or parsing failure comes back as a result
    with `error` set rather than an exception, since a background version
    check should never be able to crash or hang the app that calls it.
    """
    installed = get_installed_yt_dlp_version()
    try:
        with urllib.request.urlopen(_PYPI_URL, timeout=_TIMEOUT_SECONDS) as response:
            data = json.loads(response.read().decode("utf-8"))
        latest = data["info"]["version"]
    except Exception as exc:  # noqa: BLE001 - must never raise into the caller
        return UpdateCheckResult(
            installed_version=installed, latest_version=None, update_available=False, error=str(exc)
        )
    return UpdateCheckResult(
        installed_version=installed,
        latest_version=latest,
        update_available=is_newer_version(latest, installed),
    )


def update_yt_dlp_in_current_environment(timeout: float = 60.0) -> tuple[bool, str]:
    """Runs `pip install -U yt-dlp` in the *current* Python environment.

    Only meaningful for a source/dev install (a live venv with pip and a
    writable site-packages) -- never call this when is_frozen() is True.
    Returns (success, captured pip output or error message).
    """
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-U", "yt-dlp"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)
    if result.returncode != 0:
        return False, result.stderr or result.stdout or "pip install failed"
    return True, result.stdout
