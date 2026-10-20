"""Windows-specific bootstrap fix: a windowed (console=False) PyInstaller
build has no real console, so sys.stderr can be None. A plain
logging.StreamHandler() defaults to sys.stderr at construction time and
would be a handler that can never write -- _build_log_handlers() must skip
it in that case, leaving the file handler as the log's source of truth.
"""

from __future__ import annotations

import logging

from ytdlmanager.app import _build_log_handlers


def test_includes_stream_handler_when_stderr_present(monkeypatch, tmp_path):
    monkeypatch.setattr("sys.stderr", object())  # any non-None stand-in
    handlers = _build_log_handlers(tmp_path / "app.log")

    kinds = {type(h) for h in handlers}
    assert logging.FileHandler in kinds
    assert logging.StreamHandler in kinds


def test_omits_stream_handler_when_stderr_is_none(monkeypatch, tmp_path):
    monkeypatch.setattr("sys.stderr", None)
    handlers = _build_log_handlers(tmp_path / "app.log")

    kinds = {type(h) for h in handlers}
    assert logging.FileHandler in kinds
    assert logging.StreamHandler not in kinds


def test_file_handler_present_regardless_of_stderr(monkeypatch, tmp_path):
    log_path = tmp_path / "app.log"
    for stderr_value in (None, object()):
        monkeypatch.setattr("sys.stderr", stderr_value)
        handlers = _build_log_handlers(log_path)
        assert any(isinstance(h, logging.FileHandler) for h in handlers)
