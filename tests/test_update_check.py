"""yt-dlp update check: version comparison, the PyPI query (mocked -- no
real network in routine tests), and the dev-install self-update helper.
"""

from __future__ import annotations

import json
from urllib.error import URLError

import pytest

from ytdlmanager import update_check

# -- version comparison -------------------------------------------------


@pytest.mark.parametrize(
    "latest,current,expected",
    [
        ("2026.9.1", "2026.8.19", True),
        ("2026.8.19", "2026.8.19", False),
        ("2026.8.18", "2026.8.19", False),
        ("2027.1.1", "2026.12.31", True),
        ("2026.8.19.1", "2026.8.19", True),  # same-day patch release
        ("2026.8.19", "2026.8.19.1", False),
    ],
)
def test_is_newer_version(latest, current, expected):
    assert update_check.is_newer_version(latest, current) is expected


def test_is_newer_version_falls_back_to_inequality_for_unparseable_strings():
    assert update_check.is_newer_version("not-a-version", "2026.8.19") is True
    assert update_check.is_newer_version("2026.8.19", "2026.8.19") is False


# -- is_frozen ------------------------------------------------------------


def test_is_frozen_false_by_default(monkeypatch):
    monkeypatch.delattr(update_check.sys, "frozen", raising=False)
    assert update_check.is_frozen() is False


def test_is_frozen_true_when_pyinstaller_sets_it(monkeypatch):
    monkeypatch.setattr(update_check.sys, "frozen", True, raising=False)
    assert update_check.is_frozen() is True


# -- check_for_yt_dlp_update (network mocked) -----------------------------


class _FakeResponse:
    def __init__(self, payload: dict):
        self._body = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_check_for_update_reports_update_available(monkeypatch):
    monkeypatch.setattr(update_check, "get_installed_yt_dlp_version", lambda: "2026.8.19")
    monkeypatch.setattr(
        update_check.urllib.request,
        "urlopen",
        lambda *a, **k: _FakeResponse({"info": {"version": "2026.9.1"}}),
    )

    result = update_check.check_for_yt_dlp_update()

    assert result.installed_version == "2026.8.19"
    assert result.latest_version == "2026.9.1"
    assert result.update_available is True
    assert result.error is None


def test_check_for_update_reports_up_to_date(monkeypatch):
    monkeypatch.setattr(update_check, "get_installed_yt_dlp_version", lambda: "2026.9.1")
    monkeypatch.setattr(
        update_check.urllib.request,
        "urlopen",
        lambda *a, **k: _FakeResponse({"info": {"version": "2026.9.1"}}),
    )

    result = update_check.check_for_yt_dlp_update()

    assert result.update_available is False
    assert result.error is None


def test_check_for_update_never_raises_on_network_failure(monkeypatch):
    monkeypatch.setattr(update_check, "get_installed_yt_dlp_version", lambda: "2026.8.19")

    def raise_network_error(*a, **k):
        raise URLError("no internet")

    monkeypatch.setattr(update_check.urllib.request, "urlopen", raise_network_error)

    result = update_check.check_for_yt_dlp_update()  # must not raise

    assert result.installed_version == "2026.8.19"
    assert result.latest_version is None
    assert result.update_available is False
    assert result.error is not None


def test_check_for_update_never_raises_on_malformed_response(monkeypatch):
    monkeypatch.setattr(update_check, "get_installed_yt_dlp_version", lambda: "2026.8.19")
    monkeypatch.setattr(
        update_check.urllib.request,
        "urlopen",
        lambda *a, **k: _FakeResponse({"unexpected": "shape"}),
    )

    result = update_check.check_for_yt_dlp_update()  # must not raise (KeyError swallowed)

    assert result.error is not None
    assert result.update_available is False


# -- update_yt_dlp_in_current_environment ---------------------------------


def test_update_yt_dlp_success(monkeypatch):
    class _Result:
        returncode = 0
        stdout = "Successfully installed yt-dlp-2026.9.1\n"
        stderr = ""

    monkeypatch.setattr(update_check.subprocess, "run", lambda *a, **k: _Result())

    success, output = update_check.update_yt_dlp_in_current_environment()

    assert success is True
    assert "Successfully installed" in output


def test_update_yt_dlp_failure(monkeypatch):
    class _Result:
        returncode = 1
        stdout = ""
        stderr = "ERROR: could not find a version that satisfies the requirement"

    monkeypatch.setattr(update_check.subprocess, "run", lambda *a, **k: _Result())

    success, output = update_check.update_yt_dlp_in_current_environment()

    assert success is False
    assert "ERROR" in output


def test_update_yt_dlp_handles_subprocess_exception(monkeypatch):
    def raise_oserror(*a, **k):
        raise OSError("pip not found")

    monkeypatch.setattr(update_check.subprocess, "run", raise_oserror)

    success, output = update_check.update_yt_dlp_in_current_environment()

    assert success is False
    assert "pip not found" in output


def test_update_yt_dlp_suppresses_console_window_on_windows(monkeypatch):
    # Same Windows console-flash concern as ffmpeg_utils.find_ffmpeg().
    captured = {}

    def fake_run(*args, **kwargs):
        captured.update(kwargs)

        class _Result:
            returncode = 0
            stdout = "ok"
            stderr = ""

        return _Result()

    monkeypatch.setattr(update_check.subprocess, "run", fake_run)

    update_check.update_yt_dlp_in_current_environment()

    expected = getattr(update_check.subprocess, "CREATE_NO_WINDOW", 0)
    assert captured.get("creationflags") == expected
