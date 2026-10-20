"""JavaScript runtime (Deno) detection.

yt-dlp needs a JavaScript runtime to solve the challenges in YouTube's
player code. Without one it falls back to a single limited YouTube client
that YouTube increasingly refuses, and warns that runtime-less YouTube
extraction is deprecated. Deno is yt-dlp's default runtime and is installed
as a Python package by the `yt-dlp[deno]` extra (see requirements.txt); a
frozen build carries its own copy (see packaging/pyinstaller.spec).

Like FFmpeg (see ffmpeg_utils), we look the binary up ourselves and hand
yt-dlp an explicit path, because a GUI-launched or frozen app doesn't see
the PATH a terminal does.
"""

from __future__ import annotations

import os
import shutil
import sys

# Where packaging/pyinstaller.spec puts the bundled binary, relative to the
# frozen app's resource directory (sys._MEIPASS).
BUNDLED_DENO_DIR = "deno_bin"

_DENO_EXE = "deno.exe" if sys.platform == "win32" else "deno"

_COMMON_DENO_LOCATIONS: dict[str, list[str]] = {
    "darwin": [
        "/opt/homebrew/bin/deno",  # Homebrew, Apple Silicon
        "/usr/local/bin/deno",  # Homebrew, Intel
        "~/.deno/bin/deno",  # Deno's own install script
    ],
    "linux": [
        "/usr/local/bin/deno",
        "/usr/bin/deno",
        "~/.deno/bin/deno",
    ],
    "win32": [
        r"~\.deno\bin\deno.exe",
    ],
}


def _is_executable(path: str) -> bool:
    return os.path.isfile(path) and os.access(path, os.X_OK)


def _bundled_deno() -> str | None:
    if not getattr(sys, "frozen", False):
        return None
    base = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    candidate = os.path.join(base, BUNDLED_DENO_DIR, _DENO_EXE)
    return candidate if _is_executable(candidate) else None


def _pip_installed_deno() -> str | None:
    try:
        import deno  # the `deno` PyPI package, pulled in by yt-dlp[deno]

        path = deno.find_deno_bin()
    except (ImportError, FileNotFoundError, OSError):
        return None
    return path if _is_executable(path) else None


def find_deno() -> str | None:
    """Return a usable Deno binary path, or None if there isn't one.

    The bundled copy comes first in a frozen build so it never depends on
    the build machine's paths.
    """
    for found in (_bundled_deno(), _pip_installed_deno(), shutil.which("deno")):
        if found:
            return found
    for candidate in _COMMON_DENO_LOCATIONS.get(sys.platform, []):
        expanded = os.path.expanduser(candidate)
        if _is_executable(expanded):
            return expanded
    return None
