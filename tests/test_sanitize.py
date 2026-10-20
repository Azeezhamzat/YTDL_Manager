from __future__ import annotations

from pathlib import Path

from ytdlmanager.sanitize import numbered_filename, sanitize_filename, unique_destination


def test_sanitize_strips_illegal_characters():
    # 9 illegal characters (: / \ * ? " < > |) each become a single "_".
    assert sanitize_filename('Bad:/\\*?"<>|Name.mp4') == "Bad_________Name.mp4"


def test_sanitize_collapses_whitespace():
    assert sanitize_filename("Too    many   spaces.mp4") == "Too many spaces.mp4"


def test_sanitize_strips_leading_trailing_dots_and_spaces():
    assert sanitize_filename("  ...weird name...  .mp4") == "weird name.mp4"


def test_sanitize_windows_reserved_name():
    assert sanitize_filename("CON.mp4") == "_CON.mp4"
    assert sanitize_filename("con.mp4") == "_con.mp4"
    # Non-reserved names are untouched.
    assert sanitize_filename("CONcert.mp4") == "CONcert.mp4"


def test_sanitize_empty_falls_back_to_untitled():
    assert sanitize_filename("") == "untitled"
    assert sanitize_filename("....") == "untitled"


def test_sanitize_truncates_long_names_preserving_extension():
    long_name = ("a" * 300) + ".mp4"
    result = sanitize_filename(long_name, max_length=20)
    assert len(result) <= 20
    assert result.endswith(".mp4")


def test_unique_destination_no_collision(tmp_path: Path):
    result = unique_destination(tmp_path, "video.mp4")
    assert result == tmp_path / "video.mp4"


def test_unique_destination_appends_counter(tmp_path: Path):
    (tmp_path / "video.mp4").write_text("x")
    result = unique_destination(tmp_path, "video.mp4")
    assert result == tmp_path / "video (1).mp4"


def test_unique_destination_skips_multiple_collisions(tmp_path: Path):
    (tmp_path / "video.mp4").write_text("x")
    (tmp_path / "video (1).mp4").write_text("x")
    result = unique_destination(tmp_path, "video.mp4")
    assert result == tmp_path / "video (2).mp4"


def test_numbered_filename_zero_pads():
    assert numbered_filename(7, "Title.mp4") == "007 - Title.mp4"
    assert numbered_filename(123, "Title.mp4") == "123 - Title.mp4"


# -- windows_long_path ---------------------------------------------------
# Real WindowsPath/MAX_PATH semantics can only be verified on an actual
# Windows host (pathlib picks PosixPath vs WindowsPath based on the running
# OS, not a monkeypatched sys.platform), so these tests exercise the
# function's branching logic -- platform gate, absolute-path gate, length
# threshold, prefix idempotency -- rather than claiming full Windows
# verification. Run on Windows to confirm end-to-end.


def test_windows_long_path_noop_off_windows(monkeypatch):
    from ytdlmanager.sanitize import windows_long_path

    monkeypatch.setattr("sys.platform", "darwin")
    long_path = Path("/" + "a" * 300)
    assert windows_long_path(long_path) == long_path


def test_windows_long_path_noop_for_relative_path(monkeypatch):
    from ytdlmanager.sanitize import windows_long_path

    monkeypatch.setattr("sys.platform", "win32")
    relative = Path("a" * 300)
    assert windows_long_path(relative) == relative


def test_windows_long_path_noop_for_short_absolute_path(monkeypatch):
    from ytdlmanager.sanitize import windows_long_path

    monkeypatch.setattr("sys.platform", "win32")
    short_path = Path("/short/path.mp4")
    assert windows_long_path(short_path) == short_path


def test_windows_long_path_adds_prefix_for_long_absolute_path(monkeypatch):
    from ytdlmanager.sanitize import windows_long_path

    monkeypatch.setattr("sys.platform", "win32")
    long_path = Path("/" + "a" * 300 + "/video.mp4")
    result = windows_long_path(long_path)
    assert str(result).startswith("\\\\?\\")
    assert str(long_path) in str(result)


def test_windows_long_path_does_not_double_prefix(monkeypatch):
    from ytdlmanager.sanitize import windows_long_path

    monkeypatch.setattr("sys.platform", "win32")
    long_path = Path("/" + "a" * 300 + "/video.mp4")
    once = windows_long_path(long_path)
    twice = windows_long_path(once)
    assert once == twice
