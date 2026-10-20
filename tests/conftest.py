from __future__ import annotations

import os

import pytest

# All Qt widgets must run headless in CI / this sandbox (no display server).
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture
def tmp_db_path(tmp_path):
    return tmp_path / "test.sqlite3"


@pytest.fixture(scope="session")
def qapp():
    """A single QCoreApplication-derived instance for the whole test session.

    Several Qt model/value classes (QAbstractTableModel, signals) expect an
    application instance to exist even when no widgets are shown.
    """
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app
