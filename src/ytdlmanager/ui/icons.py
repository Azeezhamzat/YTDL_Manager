"""Small line-icon set, drawn from inline SVG and tinted to the current theme.

Widgets record which icon they show (and in which colour role) as Qt
properties, so retint() can redraw every icon when the theme switches
between light and dark.
"""

from __future__ import annotations

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QGuiApplication, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QAbstractButton, QLabel, QListWidget, QWidget

from . import theme

# 24x24 viewBox, stroked with the tint colour; "FILL" marks solid shapes.
_SVGS = {
    "video": '<rect x="3" y="5" width="18" height="14" rx="3"/>'
    '<path d="M10 9.5v5l4.5-2.5z" fill="FILL"/>',
    "playlist": '<path d="M4 6h11M4 11h11M4 16h7"/><path d="M15 14v6l5-3z" fill="FILL"/>',
    "download": '<path d="M12 4v11M7 10l5 5 5-5M5 20h14"/>',
    "history": '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
    "settings": '<path d="M4 7h9M17 7h3M4 17h3M11 17h9"/><circle cx="15" cy="7" r="2"/>'
    '<circle cx="9" cy="17" r="2"/>',
    "refresh": '<path d="M19.5 11A7.5 7.5 0 0 0 6 7.2M4.5 13A7.5 7.5 0 0 0 18 16.8"/>'
    '<path d="M5 3.5V8h4.5M19 20.5V16h-4.5"/>',
    "play": '<path d="M7.5 5.5v13l11-6.5z" fill="FILL"/>',
    "pause": '<rect x="6.5" y="5.5" width="3.5" height="13" rx="1" fill="FILL"/>'
    '<rect x="14" y="5.5" width="3.5" height="13" rx="1" fill="FILL"/>',
    "stop": '<rect x="6" y="6" width="12" height="12" rx="2.5" fill="FILL"/>',
    "retry": '<path d="M19.5 12a7.5 7.5 0 1 1-2.2-5.3"/><path d="M19.5 4v4.5H15"/>',
    "trash": '<path d="M4.5 7h15M10 11v6M14 11v6"/><path d="M6.5 7l1 12.5h9l1-12.5"/>'
    '<path d="M9.5 7V4.5h5V7"/>',
    "sweep": '<path d="M5 12.5l4 4L19 6.5"/><path d="M4 20h16"/>',
    "up": '<path d="M12 19V5M6 11l6-6 6 6"/>',
    "down": '<path d="M12 5v14M6 13l6 6 6-6"/>',
    "folder": '<path d="M3.5 7.5a2 2 0 0 1 2-2h4l2 2h7a2 2 0 0 1 2 2v7.5a2 2 0 0 1-2 2h-13'
    'a2 2 0 0 1-2-2z"/>',
    "search": '<circle cx="11" cy="11" r="6.5"/><path d="M20 20l-4.4-4.4"/>',
    "paste": '<rect x="5.5" y="4.5" width="13" height="16" rx="2"/>'
    '<path d="M9 4.5h6v3H9z"/>',
    "link": '<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1"/>'
    '<path d="M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/>',
    "info": '<circle cx="12" cy="12" r="8.5"/><path d="M12 11v5.5"/>'
    '<circle cx="12" cy="7.8" r="0.6" fill="FILL"/>',
    "check_all": '<rect x="4" y="4" width="16" height="16" rx="3.5"/><path d="M8 12.5l3 3 5-6"/>',
    "check_none": '<rect x="4" y="4" width="16" height="16" rx="3.5"/>',
    "invert": '<rect x="4" y="4" width="16" height="16" rx="3.5"/><path d="M4 20L20 4"/>',
    "file": '<path d="M6.5 3.5h7.5l4 4v13h-11.5z"/><path d="M14 3.5v4h4"/>',
    "copy": '<rect x="8.5" y="8.5" width="11" height="11" rx="2"/>'
    '<path d="M15.5 8.5v-3a1.5 1.5 0 0 0-1.5-1.5h-8.5a1.5 1.5 0 0 0-1.5 1.5v8.5'
    'a1.5 1.5 0 0 0 1.5 1.5h3"/>',
    "user": '<circle cx="12" cy="8.5" r="3.5"/><path d="M5 19.5a7 7 0 0 1 14 0"/>',
    "ok": '<circle cx="12" cy="12" r="8.5"/><path d="M8.5 12.3l2.4 2.4 4.6-5"/>',
    "warn": '<path d="M12 4l8.5 15h-17z"/><path d="M12 10v4"/>'
    '<circle cx="12" cy="16.6" r="0.6" fill="FILL"/>',
}

# Which theme token each colour role draws with.
_ROLE_TOKENS = {
    "text": "text",
    "muted": "text_muted",
    "accent": "accent",
    "on_accent": "accent_text",
    "danger": "danger",
    "ok": "st_completed",
    "warn": "st_paused",
    "notice": "notice_text",
}


def icon(name: str, role: str = "text", size: int = 18) -> QIcon:
    color = theme.current_tokens()[_ROLE_TOKENS[role]]
    body = _SVGS[name].replace("FILL", color)
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        f'stroke="{color}" stroke-width="1.9" stroke-linecap="round" '
        f'stroke-linejoin="round">{body}</svg>'
    )
    ratio = max(2.0, QGuiApplication.instance().devicePixelRatio())  # type: ignore[union-attr]
    pixels = int(size * ratio)
    pixmap = QPixmap(pixels, pixels)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    QSvgRenderer(QByteArray(svg.encode())).render(painter)
    painter.end()
    pixmap.setDevicePixelRatio(ratio)
    return QIcon(pixmap)


# The application icon: a white download arrow on a blue-violet rounded
# square, inset like macOS app icons. packaging/make_icons.py renders the
# platform icon files (.icns, .ico, .png) from this same source.
APP_ICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1024 1024">
<defs>
<linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
<stop offset="0" stop-color="#4f8cff"/><stop offset="1" stop-color="#6a3df0"/>
</linearGradient>
<linearGradient id="shine" x1="0" y1="0" x2="0" y2="1">
<stop offset="0" stop-color="#ffffff" stop-opacity="0.18"/>
<stop offset="0.5" stop-color="#ffffff" stop-opacity="0"/>
</linearGradient>
</defs>
<rect x="100" y="100" width="824" height="824" rx="186" fill="url(#bg)"/>
<rect x="100" y="100" width="824" height="824" rx="186" fill="url(#shine)"/>
<g fill="none" stroke="#ffffff" stroke-width="78" stroke-linecap="round" stroke-linejoin="round">
<path d="M512 270v330"/><path d="M362 460l150 150 150-150"/><path d="M322 742h380"/>
</g>
</svg>"""


def render_app_icon(size: int) -> QPixmap:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    QSvgRenderer(QByteArray(APP_ICON_SVG.encode())).render(painter)
    painter.end()
    return pixmap


def app_icon() -> QIcon:
    themed = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256, 512):
        themed.addPixmap(render_app_icon(size))
    return themed


def set_icon(widget: QWidget, name: str, role: str = "text", size: int = 18) -> None:
    """Give a button (or a label) a themed icon that follows theme changes."""
    widget.setProperty("iconName", name)
    widget.setProperty("iconRole", role)
    widget.setProperty("themedIconSize", size)
    _draw(widget)


def _draw(widget: QWidget) -> None:
    name = widget.property("iconName")
    role = widget.property("iconRole") or "text"
    size = widget.property("themedIconSize") or 18
    themed = icon(name, role, size)
    if isinstance(widget, QAbstractButton):
        widget.setIcon(themed)
    elif isinstance(widget, QLabel):
        widget.setPixmap(themed.pixmap(size, size))


def retint(root: QWidget) -> None:
    """Redraw every themed icon under `root` in the current theme's colours."""
    for widget in root.findChildren(QWidget):
        if widget.property("iconName"):
            _draw(widget)
    for nav in root.findChildren(QListWidget):
        for row in range(nav.count()):
            item = nav.item(row)
            name = item.data(Qt.ItemDataRole.UserRole)
            if name:
                item.setIcon(icon(name, "text"))
