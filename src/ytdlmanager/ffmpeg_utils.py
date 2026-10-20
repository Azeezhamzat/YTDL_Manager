"""FFmpeg detection.

yt-dlp needs FFmpeg on PATH (or an explicit location) to merge separate
video+audio streams and to extract/convert audio. We check for it up front
so the UI can show clear recovery instructions instead of a cryptic
mid-download failure.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass


@dataclass
class FfmpegStatus:
    found: bool
    path: str | None
    version: str | None


# GUI-launched apps (double-click, Spotlight, Dock, Launchpad) don't run
# through a login shell, so they never see PATH entries a shell profile adds
# -- notably Homebrew's `/opt/homebrew/bin` on Apple Silicon Macs. macOS's
# /etc/paths.d mechanism only feeds `path_helper`, which only login shells
# invoke; `launchd`'s own environment (what it hands to GUI apps) doesn't
# pick it up. So `ffmpeg` can be correctly installed and work fine from a
# terminal while a double-clicked build of this app still reports it
# missing. Checking these well-known install locations directly, after a
# plain PATH lookup fails, closes that gap without requiring the user to
# relaunch Finder or log out and back in.
_COMMON_FFMPEG_LOCATIONS: dict[str, list[str]] = {
    "darwin": [
        "/opt/homebrew/bin/ffmpeg",  # Homebrew, Apple Silicon
        "/usr/local/bin/ffmpeg",  # Homebrew, Intel
        "/opt/local/bin/ffmpeg",  # MacPorts
    ],
    "linux": [
        "/usr/bin/ffmpeg",
        "/usr/local/bin/ffmpeg",
        "/snap/bin/ffmpeg",
    ],
    "win32": [
        r"C:\ffmpeg\bin\ffmpeg.exe",
        r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
        r"C:\Program Files (x86)\ffmpeg\bin\ffmpeg.exe",
    ],
}


def _fallback_candidates() -> list[str]:
    return _COMMON_FFMPEG_LOCATIONS.get(sys.platform, [])


def find_ffmpeg(explicit_path: str | None = None) -> FfmpegStatus:
    path = explicit_path or shutil.which("ffmpeg")

    if not path:
        for candidate in _fallback_candidates():
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                path = candidate
                break

    if not path:
        return FfmpegStatus(found=False, path=None, version=None)

    version = None
    try:
        result = subprocess.run(
            [path, "-version"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
            # On Windows, a windowed (console=False) PyInstaller build still
            # gets a console window flashed on screen by a child process
            # unless this is set -- CREATE_NO_WINDOW only exists on Windows,
            # hence getattr with a fallback that's a harmless no-op elsewhere
            # (creationflags is ignored on POSIX regardless of its value).
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode == 0 and result.stdout:
            version = result.stdout.splitlines()[0].strip()
    except (OSError, subprocess.SubprocessError):
        return FfmpegStatus(found=False, path=path, version=None)

    return FfmpegStatus(found=True, path=path, version=version)


INSTALL_INSTRUCTIONS = {
    "darwin": "Install with Homebrew:  brew install ffmpeg",
    "win32": "Install with winget:  winget install Gyan.FFmpeg\n"
    "  or with Chocolatey:  choco install ffmpeg\n"
    "  then restart the application so it is picked up on PATH.",
    "linux": "Install with your package manager, e.g.:\n"
    "  Debian/Ubuntu:  sudo apt install ffmpeg\n"
    "  Fedora:         sudo dnf install ffmpeg\n"
    "  Arch:           sudo pacman -S ffmpeg",
}


def install_instructions(platform: str) -> str:
    return INSTALL_INSTRUCTIONS.get(platform, INSTALL_INSTRUCTIONS["linux"])
