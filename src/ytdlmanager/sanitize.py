"""Filename and path sanitisation for cross-platform safety.

Handles: reserved Windows device names, illegal characters on Windows/macOS/
Linux, trailing dots/spaces (Windows rejects these), and overlong names
(Windows MAX_PATH and common filesystem limits).
"""

from __future__ import annotations

import re
import sys
from collections.abc import Callable
from pathlib import Path

from .config import MAX_FILENAME_LENGTH

_WINDOWS_MAX_PATH = 260
_WINDOWS_EXTENDED_PREFIX = "\\\\?\\"

_ILLEGAL_CHARS_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *{f"COM{i}" for i in range(1, 10)},
    *{f"LPT{i}" for i in range(1, 10)},
}


def sanitize_filename(name: str, *, max_length: int = MAX_FILENAME_LENGTH) -> str:
    """Return a filename safe to create on Windows, macOS, and Linux.

    - Strips characters illegal on any of the three platforms.
    - Collapses whitespace, strips leading/trailing dots and spaces.
    - Renames Windows-reserved device names (e.g. "CON") to avoid failures.
    - Truncates to `max_length` characters, preserving the file extension.
    - Falls back to "untitled" if nothing usable remains.
    """
    name = name.strip()
    name = _ILLEGAL_CHARS_RE.sub("_", name)
    name = re.sub(r"\s+", " ", name)

    # Split before trimming trailing dots/spaces: they can legitimately sit
    # right before the extension (e.g. "Title... .mp4"), and a plain
    # whole-string strip only reaches ones at the very end of the extension.
    stem, _, ext = name.rpartition(".")
    if not stem:
        stem, ext = name, ""

    stem = stem.strip(". ")
    ext = ext.strip(". ")

    if not stem:
        stem = "untitled"

    if stem.upper() in _WINDOWS_RESERVED_NAMES:
        stem = f"_{stem}"

    if ext:
        full = f"{stem}.{ext}"
    else:
        full = stem

    if len(full) > max_length:
        if ext:
            keep = max_length - len(ext) - 1
            keep = max(keep, 1)
            full = f"{stem[:keep]}.{ext}"
        else:
            full = full[:max_length]

    return full or "untitled"


def unique_destination(
    directory: Path,
    filename: str,
    *,
    exists_fn: Callable[[Path], bool] | None = None,
) -> Path:
    """Return a path in `directory` guaranteed not to collide, by appending
    " (1)", " (2)", etc. before the extension. Used for the RENAME duplicate
    policy.
    """
    exists = exists_fn or (lambda p: p.exists())
    stem, _, ext = filename.rpartition(".")
    if not stem:
        stem, ext = filename, ""

    candidate = directory / filename
    if not exists(candidate):
        return candidate

    counter = 1
    while True:
        new_name = f"{stem} ({counter}).{ext}" if ext else f"{stem} ({counter})"
        candidate = directory / new_name
        if not exists(candidate):
            return candidate
        counter += 1


def numbered_filename(index: int, name: str, *, width: int = 3) -> str:
    """Prefix a filename with a zero-padded playlist index, e.g. '007 - Title.mp4'."""
    return f"{str(index).zfill(width)} - {name}"


def windows_long_path(path: Path) -> Path:
    """Prefix an absolute path with Windows' `\\\\?\\` extended-length marker
    when it's close to or over the legacy 260-character MAX_PATH limit.

    A deeply nested destination folder combined with a long (but otherwise
    valid, already-truncated-to-MAX_FILENAME_LENGTH) title can still exceed
    260 characters even though no single component is unreasonable. This
    prefix is the documented way to bypass that limit on Windows without
    requiring the user to enable long-path support at the OS level; it's a
    no-op everywhere else, on relative paths, and on paths already short
    enough that there's nothing to work around.
    """
    if sys.platform != "win32" or not path.is_absolute():
        return path
    raw = str(path)
    if raw.startswith(_WINDOWS_EXTENDED_PREFIX):
        return path
    if len(raw) < _WINDOWS_MAX_PATH:
        return path
    return Path(_WINDOWS_EXTENDED_PREFIX + raw)
