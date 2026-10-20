from __future__ import annotations

from PySide6.QtCore import Qt

from ytdlmanager.models import VideoInfo
from ytdlmanager.ui.models_qt import PlaylistTableModel


def _videos(n=5) -> list[VideoInfo]:
    return [
        VideoInfo(
            video_id=f"id{i}",
            title=f"Video {i}",
            url=f"https://www.youtube.com/watch?v=id{i}",
            playlist_index=i + 1,
        )
        for i in range(n)
    ]


def test_all_items_selected_by_default(qapp):
    model = PlaylistTableModel(_videos(3))
    assert model.selected_count() == 3


def test_deselect_all_clears_selection(qapp):
    model = PlaylistTableModel(_videos(3))
    model.deselect_all()
    assert model.selected_count() == 0


def test_select_all_after_deselect(qapp):
    model = PlaylistTableModel(_videos(4))
    model.deselect_all()
    model.select_all()
    assert model.selected_count() == 4


def test_invert_selection(qapp):
    model = PlaylistTableModel(_videos(4))
    model.deselect_all()
    index = model.index(1, 0)
    model.setData(index, Qt.Checked, Qt.CheckStateRole)
    model.invert_selection()
    assert model.selected_count() == 3
    titles = {v.title for v in model.selected_items()}
    assert "Video 1" not in titles


def test_individual_checkbox_toggle(qapp):
    model = PlaylistTableModel(_videos(3))
    model.deselect_all()
    index = model.index(0, 0)
    model.setData(index, Qt.Checked, Qt.CheckStateRole)
    assert model.selected_count() == 1
    assert model.selected_items()[0].title == "Video 0"


def test_filter_by_title_restricts_visible_rows(qapp):
    model = PlaylistTableModel(_videos(3))
    model.set_filter("Video 1")
    assert model.rowCount() == 1


def test_filter_does_not_change_underlying_selection(qapp):
    model = PlaylistTableModel(_videos(3))
    model.set_filter("Video 1")
    # Selection is over the full dataset, not just the filtered view.
    assert model.selected_count() == 3


def test_select_all_only_affects_filtered_rows(qapp):
    model = PlaylistTableModel(_videos(5))
    model.deselect_all()
    model.set_filter("Video 1")  # matches only "Video 1"
    model.select_all()
    assert model.selected_count() == 1
    model.set_filter("")
    assert model.selected_count() == 1


def test_select_all_preserves_checks_outside_the_current_filter(qapp):
    # Regression: select_all() used to *replace* the checked set with just
    # the filtered rows, silently unchecking anything checked outside the
    # current search filter. E.g.: search "Part 1", deselect it, clear the
    # filter, re-search "Part 1", click Select All -- the other 4 videos
    # (never touched by the user) must still be checked afterwards.
    model = PlaylistTableModel(_videos(5))  # all 5 checked by default
    model.set_filter("Video 1")
    model.deselect_all()  # only "Video 1" unchecked; 0,2,3,4 remain checked
    model.set_filter("")
    assert model.selected_count() == 4

    model.set_filter("Video 1")
    model.select_all()  # re-check "Video 1" -- must not disturb 0,2,3,4
    model.set_filter("")
    assert model.selected_count() == 5
