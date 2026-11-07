"""Qt table models for the playlist selector and the download queue."""

from __future__ import annotations

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QPersistentModelIndex, Qt

from ..models import QueueItem, QueueStatus, VideoInfo
from .widgets import PROGRESS_ROLE, STATUS_ROLE

# PySide6's stubs declare index parameters as this union (a QModelIndex or a
# QPersistentModelIndex may both be passed by Qt internals).
_ModelIndex = QModelIndex | QPersistentModelIndex


def _format_duration(seconds: int | None) -> str:
    if not seconds:
        return "--:--"
    minutes, secs = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


class PlaylistTableModel(QAbstractTableModel):
    HEADERS = ["", "#", "Title", "Channel", "Duration"]

    def __init__(self, items: list[VideoInfo] | None = None) -> None:
        super().__init__()
        self._all_items: list[VideoInfo] = items or []
        self._filtered_indices: list[int] = list(range(len(self._all_items)))
        self._checked: set[int] = set(self._filtered_indices)  # indices into _all_items

    def set_items(self, items: list[VideoInfo]) -> None:
        self.beginResetModel()
        self._all_items = items
        self._filtered_indices = list(range(len(items)))
        self._checked = set(self._filtered_indices)
        self.endResetModel()

    def set_filter(self, text: str) -> None:
        self.beginResetModel()
        text = text.strip().lower()
        if not text:
            self._filtered_indices = list(range(len(self._all_items)))
        else:
            self._filtered_indices = [
                i for i, v in enumerate(self._all_items) if text in v.title.lower()
            ]
        self.endResetModel()

    def rowCount(self, parent: _ModelIndex = QModelIndex()) -> int:  # noqa: N803, B008
        return len(self._filtered_indices)

    def columnCount(self, parent: _ModelIndex = QModelIndex()) -> int:  # noqa: N803, B008
        return len(self.HEADERS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if orientation == Qt.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self.HEADERS[section]
        return None

    def _video_at(self, row: int) -> VideoInfo:
        return self._all_items[self._filtered_indices[row]]

    def data(self, index: _ModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        row, col = index.row(), index.column()
        video = self._video_at(row)
        real_index = self._filtered_indices[row]

        if col == 0 and role == Qt.ItemDataRole.CheckStateRole:
            return Qt.CheckState.Checked if real_index in self._checked else Qt.CheckState.Unchecked
        if role == Qt.ItemDataRole.DisplayRole:
            if col == 1:
                return video.playlist_index or (row + 1)
            if col == 2:
                title = video.title
                if not video.is_available:
                    title += "  (unavailable)"
                return title
            if col == 3:
                return video.channel or ""
            if col == 4:
                return _format_duration(video.duration_seconds)
        if role == Qt.ItemDataRole.ToolTipRole and col == 2:
            if not video.is_available:
                return video.unavailable_reason or "This item is unavailable"
            return video.title  # full title; the column may cut it short
        return None

    def setData(  # noqa: N802
        self, index: _ModelIndex, value, role: int = Qt.ItemDataRole.EditRole
    ) -> bool:
        if role == Qt.ItemDataRole.CheckStateRole and index.column() == 0:
            real_index = self._filtered_indices[index.row()]
            if value == Qt.CheckState.Checked:
                self._checked.add(real_index)
            else:
                self._checked.discard(real_index)
            self.dataChanged.emit(index, index, [Qt.ItemDataRole.CheckStateRole])
            return True
        return False

    def flags(self, index: _ModelIndex):  # noqa: N802
        base = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if index.column() == 0:
            base |= Qt.ItemFlag.ItemIsUserCheckable
        return base

    def select_all(self) -> None:
        # Union, not replace: items checked outside the current search filter
        # (e.g. before the user typed a query) must survive "Select All" on
        # the filtered subset, symmetrically with how deselect_all() below
        # only subtracts the filtered subset rather than clearing everything.
        self._checked |= set(self._filtered_indices)
        self._notify_check_changed()

    def deselect_all(self) -> None:
        self._checked -= set(self._filtered_indices)
        self._notify_check_changed()

    def invert_selection(self) -> None:
        for i in self._filtered_indices:
            if i in self._checked:
                self._checked.discard(i)
            else:
                self._checked.add(i)
        self._notify_check_changed()

    def _notify_check_changed(self) -> None:
        if self._filtered_indices:
            top_left = self.index(0, 0)
            bottom_right = self.index(len(self._filtered_indices) - 1, 0)
            self.dataChanged.emit(top_left, bottom_right, [Qt.ItemDataRole.CheckStateRole])

    def selected_items(self) -> list[VideoInfo]:
        return [self._all_items[i] for i in sorted(self._checked)]

    def selected_count(self) -> int:
        return len(self._checked)


_STATUS_LABELS = {
    QueueStatus.QUEUED: "Queued",
    QueueStatus.DOWNLOADING: "Downloading",
    QueueStatus.PAUSED: "Paused",
    QueueStatus.COMPLETED: "Completed",
    QueueStatus.CANCELLED: "Cancelled",
    QueueStatus.SKIPPED: "Skipped",
    QueueStatus.FAILED: "Failed",
}


def status_label(status: QueueStatus) -> str:
    return _STATUS_LABELS.get(status, status.value)


class QueueTableModel(QAbstractTableModel):
    HEADERS = ["Title", "Status", "Progress", "Speed · ETA", "Details"]

    def __init__(self) -> None:
        super().__init__()
        self._items: list[QueueItem] = []
        self._id_to_row: dict[int, int] = {}

    def set_items(self, items: list[QueueItem]) -> None:
        self.beginResetModel()
        self._items = items
        self._id_to_row = {item.id: row for row, item in enumerate(items) if item.id is not None}
        self.endResetModel()

    def item_at(self, row: int) -> QueueItem | None:
        if 0 <= row < len(self._items):
            return self._items[row]
        return None

    def row_for_id(self, item_id: int) -> int | None:
        # O(1) via the index built in set_items(), instead of scanning every
        # item -- this is called on every progress tick from every active
        # download, so an O(n) scan here turns busy queues sluggish.
        return self._id_to_row.get(item_id)

    def update_item(self, item: QueueItem) -> None:
        if item.id is None:
            return
        row = self.row_for_id(item.id)
        if row is None:
            return
        self._items[row] = item
        left = self.index(row, 0)
        right = self.index(row, len(self.HEADERS) - 1)
        self.dataChanged.emit(left, right)

    def rowCount(self, parent: _ModelIndex = QModelIndex()) -> int:  # noqa: N803, B008
        return len(self._items)

    def columnCount(self, parent: _ModelIndex = QModelIndex()) -> int:  # noqa: N803, B008
        return len(self.HEADERS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if orientation == Qt.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self.HEADERS[section]
        return None

    def data(self, index: _ModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        item = self._items[index.row()]
        col = index.column()
        if role == STATUS_ROLE:
            return item.status.value
        if role == PROGRESS_ROLE:
            return item.progress_percent
        if role == Qt.ItemDataRole.ToolTipRole:
            # Full text for cells the column width would otherwise cut off.
            if col == 0:
                return f"{item.request.title}\nSaving to: {item.request.destination_dir}"
            if col == 4 and item.error_message:
                return item.error_message
            return None
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if col == 0:
            return item.request.title
        if col == 1:
            return _STATUS_LABELS.get(item.status, item.status.value)
        if col == 2:
            return f"{item.progress_percent:.0f}%"
        if col == 3:
            parts = (_format_speed(item.speed_bps), _format_eta(item.eta_seconds))
            return " · ".join(part for part in parts if part)
        if col == 4:
            return item.error_message or ""
        return None


def _format_speed(speed_bps: float | None) -> str:
    if not speed_bps:
        return ""
    mb = speed_bps / (1024 * 1024)
    if mb >= 1:
        return f"{mb:.1f} MB/s"
    return f"{speed_bps / 1024:.0f} KB/s"


def _format_eta(seconds: int | None) -> str:
    if not seconds:
        return ""
    return _format_duration(int(seconds))
