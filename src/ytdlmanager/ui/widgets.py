"""Reusable UI pieces: a wrapping toolbar layout, card frames, and the
custom-painted status pill / progress bar cells used in the tables."""

from __future__ import annotations

from PySide6.QtCore import QModelIndex, QPersistentModelIndex, QPoint, QRect, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QLabel,
    QLayout,
    QLayoutItem,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QWidget,
)

from ..ytdlp_backend import MAX_HEIGHT_CHOICES
from . import theme

# Custom item-data roles shared by the table models and these delegates.
STATUS_ROLE = Qt.ItemDataRole.UserRole + 1  # QueueStatus value
PROGRESS_ROLE = Qt.ItemDataRole.UserRole + 2  # percent, 0-100
PATH_ROLE = Qt.ItemDataRole.UserRole + 3  # saved file path (history)
URL_ROLE = Qt.ItemDataRole.UserRole + 4  # source URL (history)

_ModelIndex = QModelIndex | QPersistentModelIndex


class FlowLayout(QLayout):
    """Lays widgets out left to right and wraps onto further rows when the
    width runs out, so a toolbar never clips buttons off the edge or forces
    the window wider (the queue's nine-button row used to do both)."""

    def __init__(self, parent: QWidget | None = None, spacing: int = 8) -> None:
        super().__init__(parent)
        self._items: list[QLayoutItem] = []
        self._spacing = spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item: QLayoutItem) -> None:  # noqa: N802
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int) -> QLayoutItem | None:  # noqa: N802
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int) -> QLayoutItem | None:  # noqa: N802
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self) -> Qt.Orientation:  # noqa: N802
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        return self._arrange(QRect(0, 0, width, 0), apply=False)

    def setGeometry(self, rect: QRect) -> None:  # noqa: N802
        super().setGeometry(rect)
        self._arrange(rect, apply=True)

    def sizeHint(self) -> QSize:  # noqa: N802
        return self.minimumSize()

    def minimumSize(self) -> QSize:  # noqa: N802
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        return size + QSize(margins.left() + margins.right(), margins.top() + margins.bottom())

    def _arrange(self, rect: QRect, *, apply: bool) -> int:
        margins = self.contentsMargins()
        area = rect.adjusted(margins.left(), margins.top(), -margins.right(), -margins.bottom())
        x, y, row_height = area.x(), area.y(), 0
        for item in self._items:
            widget = item.widget()
            if widget is not None and widget.isHidden():
                continue
            hint = item.sizeHint()
            if x + hint.width() > area.right() + 1 and row_height > 0:
                x = area.x()
                y += row_height + self._spacing
                row_height = 0
            if apply:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + self._spacing
            row_height = max(row_height, hint.height())
        return y + row_height - rect.y() + margins.bottom()


_QUALITY_LABELS = {
    2160: "2160p (4K)",
    1440: "1440p (2K)",
    1080: "1080p (Full HD)",
    720: "720p (HD)",
    480: "480p",
    360: "360p",
}


def populate_quality_combo(combo: QComboBox, selected: int | None) -> None:
    """Fill a "Max quality" drop-down. Item data is the height in pixels,
    or 0 for no limit (QVariant doesn't round-trip None reliably)."""
    combo.clear()
    combo.addItem("Best available", 0)
    for height in MAX_HEIGHT_CHOICES:
        if height:
            combo.addItem(_QUALITY_LABELS.get(height, f"{height}p"), height)
    combo.setCurrentIndex(max(combo.findData(selected or 0), 0))
    combo.setToolTip(
        "Highest video resolution to download. Smaller is faster and saves disk\n"
        "space; if a video has nothing at or below it, the best available is used.\n"
        "Doesn't apply to audio-only downloads."
    )


def quality_choice(combo: QComboBox) -> int | None:
    value = combo.currentData()
    return int(value) if value else None


def card(name: str = "card") -> QFrame:
    frame = QFrame()
    frame.setObjectName(name)
    return frame


def field_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("fieldLabel")
    return label


def _draw_cell_background(option: QStyleOptionViewItem, index: _ModelIndex, painter: QPainter,
                          delegate: QStyledItemDelegate) -> None:
    """Selection/alternating-row background with the cell's own text removed."""
    opt = QStyleOptionViewItem(option)
    delegate.initStyleOption(opt, index)
    opt.text = ""
    style = opt.widget.style() if opt.widget else QApplication.style()
    style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, opt.widget)


class StatusPillDelegate(QStyledItemDelegate):
    """Draws a status as a rounded, colour-coded pill (Downloading, Failed...)."""

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: _ModelIndex) -> None:
        _draw_cell_background(option, index, painter, self)
        text = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
        if not text:
            return
        fg, bg = theme.status_colors(str(index.data(STATUS_ROLE) or "queued"))
        font = QFont(option.font)
        font.setPointSizeF(max(font.pointSizeF() - 1, 9))
        font.setWeight(QFont.Weight.DemiBold)
        metrics = QFontMetrics(font)
        height = metrics.height() + 6
        width = metrics.horizontalAdvance(text) + 20
        rect = QRectF(option.rect.x() + 6, option.rect.center().y() - height / 2 + 1, width, height)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(bg))
        painter.drawRoundedRect(rect, height / 2, height / 2)
        painter.setFont(font)
        painter.setPen(QColor(fg))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)
        painter.restore()


class ProgressDelegate(QStyledItemDelegate):
    """Draws a thin progress bar plus the percentage, coloured by status."""

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: _ModelIndex) -> None:
        _draw_cell_background(option, index, painter, self)
        value = float(index.data(PROGRESS_ROLE) or 0.0)
        status = str(index.data(STATUS_ROLE) or "queued")
        tokens = theme.current_tokens()
        bar_color = tokens["accent"] if status in ("downloading", "queued") else (
            theme.status_colors(status)[0]
        )
        label = f"{value:.0f}%"
        label_width = option.fontMetrics.horizontalAdvance("100%") + 10
        area = option.rect.adjusted(8, 0, -8, 0)
        track = QRectF(area.x(), area.center().y() - 3, max(area.width() - label_width, 20), 6)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(tokens["border"]))
        painter.drawRoundedRect(track, 3, 3)
        if value > 0:
            filled = QRectF(track)
            filled.setWidth(max(track.width() * min(value, 100.0) / 100.0, 6))
            painter.setBrush(QColor(bar_color))
            painter.drawRoundedRect(filled, 3, 3)
        painter.setPen(QColor(tokens["text_muted"]))
        text_rect = QRectF(track.right() + 6, area.y(), label_width, area.height())
        align = Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft
        painter.drawText(text_rect, align, label)
        painter.restore()
