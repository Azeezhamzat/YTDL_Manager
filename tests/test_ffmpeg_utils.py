"""FFmpeg detection, including the fallback-location regression.

Real bug this guards against: a packaged macOS app launched via Finder/
Spotlight/Dock doesn't inherit the shell PATH a `brew install ffmpeg` made
visible in Terminal (GUI-launched processes don't go through a login shell,
so /etc/paths.d entries like Homebrew's never apply to them). find_ffmpeg()
used to rely solely on `shutil.which`, so it reported "not found" -- and
every download needing FFmpeg failed -- even with FFmpeg correctly
installed. Confirmed live: `shutil.which` failed in that exact scenario
while `/opt/homebrew/bin/ffmpeg` was present and executable.
"""

from __future__ import annotations

from ytdlmanager import ffmpeg_utils


def _stub_version_ok(monkeypatch):
    class _Result:
        returncode = 0
        stdout = "ffmpeg version 6.0 Copyright (c) 2000-2023\n"

    monkeypatch.setattr(
        ffmpeg_utils.subprocess, "run", lambda *a, **k: _Result()
    )


def test_find_ffmpeg_uses_path_lookup_when_available(monkeypatch):
    monkeypatch.setattr(ffmpeg_utils.shutil, "which", lambda name: "/usr/bin/ffmpeg")
    _stub_version_ok(monkeypatch)

    status = ffmpeg_utils.find_ffmpeg()

    assert status.found is True
    assert status.path == "/usr/bin/ffmpeg"


def test_find_ffmpeg_falls_back_to_known_location_when_path_lookup_fails(monkeypatch):
    # Simulate exactly the GUI-launch scenario: PATH-based lookup fails
    # (shutil.which returns None, as it does when /opt/homebrew/bin isn't in
    # the process's PATH) but the binary genuinely exists on disk.
    monkeypatch.setattr(ffmpeg_utils.shutil, "which", lambda name: None)
    monkeypatch.setattr(ffmpeg_utils.sys, "platform", "darwin")
    monkeypatch.setattr(
        ffmpeg_utils.os.path, "isfile", lambda p: p == "/opt/homebrew/bin/ffmpeg"
    )
    monkeypatch.setattr(ffmpeg_utils.os, "access", lambda p, mode: True)
    _stub_version_ok(monkeypatch)

    status = ffmpeg_utils.find_ffmpeg()

    assert status.found is True
    assert status.path == "/opt/homebrew/bin/ffmpeg"


def test_find_ffmpeg_tries_fallback_locations_in_order(monkeypatch):
    monkeypatch.setattr(ffmpeg_utils.shutil, "which", lambda name: None)
    monkeypatch.setattr(ffmpeg_utils.sys, "platform", "darwin")
    # Only the second candidate (Intel Homebrew) actually exists.
    monkeypatch.setattr(
        ffmpeg_utils.os.path, "isfile", lambda p: p == "/usr/local/bin/ffmpeg"
    )
    monkeypatch.setattr(ffmpeg_utils.os, "access", lambda p, mode: True)
    _stub_version_ok(monkeypatch)

    status = ffmpeg_utils.find_ffmpeg()

    assert status.found is True
    assert status.path == "/usr/local/bin/ffmpeg"


def test_find_ffmpeg_not_found_when_neither_path_nor_fallback_exist(monkeypatch):
    monkeypatch.setattr(ffmpeg_utils.shutil, "which", lambda name: None)
    monkeypatch.setattr(ffmpeg_utils.sys, "platform", "darwin")
    monkeypatch.setattr(ffmpeg_utils.os.path, "isfile", lambda p: False)

    status = ffmpeg_utils.find_ffmpeg()

    assert status.found is False
    assert status.path is None


def test_find_ffmpeg_fallback_requires_executable_bit(monkeypatch):
    # A file existing at the candidate path but not executable (e.g. a
    # stray non-binary file) must not be treated as a valid FFmpeg.
    monkeypatch.setattr(ffmpeg_utils.shutil, "which", lambda name: None)
    monkeypatch.setattr(ffmpeg_utils.sys, "platform", "darwin")
    monkeypatch.setattr(
        ffmpeg_utils.os.path, "isfile", lambda p: p == "/opt/homebrew/bin/ffmpeg"
    )
    monkeypatch.setattr(ffmpeg_utils.os, "access", lambda p, mode: False)

    status = ffmpeg_utils.find_ffmpeg()

    assert status.found is False


def test_find_ffmpeg_explicit_path_takes_precedence(monkeypatch):
    monkeypatch.setattr(ffmpeg_utils.shutil, "which", lambda name: "/usr/bin/ffmpeg")
    _stub_version_ok(monkeypatch)

    status = ffmpeg_utils.find_ffmpeg(explicit_path="/custom/ffmpeg")

    assert status.path == "/custom/ffmpeg"


def test_find_ffmpeg_no_fallback_candidates_on_unknown_platform(monkeypatch):
    monkeypatch.setattr(ffmpeg_utils.shutil, "which", lambda name: None)
    monkeypatch.setattr(ffmpeg_utils.sys, "platform", "some-exotic-os")

    status = ffmpeg_utils.find_ffmpeg()

    assert status.found is False


def test_find_ffmpeg_suppresses_console_window_on_windows(monkeypatch):
    # Regression: a windowed (console=False) PyInstaller build on Windows
    # briefly flashes a console window when it spawns a child process,
    # unless creationflags=CREATE_NO_WINDOW is passed. Every app startup
    # calls find_ffmpeg(), so this fired on every single launch.
    captured = {}

    def fake_run(*args, **kwargs):
        captured.update(kwargs)

        class _Result:
            returncode = 0
            stdout = "ffmpeg version 6.0\n"

        return _Result()

    monkeypatch.setattr(ffmpeg_utils.shutil, "which", lambda name: "/usr/bin/ffmpeg")
    monkeypatch.setattr(ffmpeg_utils.subprocess, "run", fake_run)

    ffmpeg_utils.find_ffmpeg()

    assert "creationflags" in captured
    # On non-Windows test hosts CREATE_NO_WINDOW doesn't exist, so the
    # getattr fallback (0, a harmless no-op) is the expected value here;
    # on a real Windows run this would be the actual flag.
    expected = getattr(ffmpeg_utils.subprocess, "CREATE_NO_WINDOW", 0)
    assert captured["creationflags"] == expected
