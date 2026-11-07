"""Light/dark themes.

Both palettes are defined as a token dict fed into one shared QSS template,
so the two themes can't drift out of sync with each other structurally --
only the token values differ. Colours are chosen for WCAG AA contrast
against their paired background (>= 4.5:1 for body text).

"system" follows the OS light/dark setting but still applies the app's own
styling: leaving it to the native macOS style made every button the same
flat grey (no visible primary action) and rendered disabled controls so
faintly they read as missing features. The app runs on Qt's Fusion style
so the stylesheet renders the same on every OS.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QGuiApplication, QPalette
from PySide6.QtWidgets import QApplication

_LIGHT_TOKENS = {
    "bg": "#f3f4f7",
    "surface": "#ffffff",
    "sidebar": "#eceef3",
    "sidebar_selected": "#dfe7fb",
    "bg_input": "#ffffff",
    "border": "#dcdfe5",
    "border_strong": "#2f6fed",
    "text": "#16181d",
    "text_muted": "#5d6573",
    "accent": "#2f6fed",
    "accent_hover": "#2559c9",
    "accent_pressed": "#1d48a3",
    "accent_text": "#ffffff",
    "accent_soft": "#e6eeff",
    "danger": "#c0392b",
    "danger_soft": "#fdecea",
    "disabled_bg": "#eceef1",
    "disabled_text": "#9aa1ab",
    "header_bg": "#f6f7f9",
    "selection_bg": "#dbe6fd",
    "selection_text": "#132a63",
    "alt_row": "#fafbfc",
    "scrollbar": "#c7cbd1",
    "scrollbar_hover": "#a7acb4",
    "tooltip_bg": "#2a2d31",
    "tooltip_text": "#f4f5f7",
    "notice_bg": "#eef3ff",
    "notice_border": "#cfdcff",
    "notice_text": "#2a3d6b",
    # Status pills: (text, background) per QueueStatus value.
    "st_downloading": "#2257c9",
    "st_downloading_bg": "#e6eeff",
    "st_queued": "#555d6a",
    "st_queued_bg": "#eceef2",
    "st_paused": "#8a5300",
    "st_paused_bg": "#fff0d6",
    "st_completed": "#1b7448",
    "st_completed_bg": "#e1f4ea",
    "st_failed": "#b3261e",
    "st_failed_bg": "#fdeaea",
    "st_skipped": "#5b4bc4",
    "st_skipped_bg": "#eeebfd",
}

_DARK_TOKENS = {
    "bg": "#141518",
    "surface": "#1c1d21",
    "sidebar": "#18191c",
    "sidebar_selected": "#25304a",
    "bg_input": "#232529",
    "border": "#2f3237",
    "border_strong": "#6b9bff",
    "text": "#eceef1",
    "text_muted": "#9aa2ad",
    "accent": "#6b9bff",
    "accent_hover": "#86adff",
    "accent_pressed": "#4f7fe6",
    "accent_text": "#0b0d10",
    "accent_soft": "#22304f",
    "danger": "#ff8a7e",
    "danger_soft": "#3a1e1c",
    "disabled_bg": "#26282c",
    "disabled_text": "#626872",
    "header_bg": "#202125",
    "selection_bg": "#2a3756",
    "selection_text": "#e3ebff",
    "alt_row": "#1f2024",
    "scrollbar": "#3c4046",
    "scrollbar_hover": "#4d525a",
    "tooltip_bg": "#eceef1",
    "tooltip_text": "#17181b",
    "notice_bg": "#1b2233",
    "notice_border": "#2c3a5c",
    "notice_text": "#bccdfa",
    "st_downloading": "#9dbdff",
    "st_downloading_bg": "#22304f",
    "st_queued": "#b0b7c2",
    "st_queued_bg": "#2a2d33",
    "st_paused": "#ffcf7a",
    "st_paused_bg": "#3a2f14",
    "st_completed": "#74d8a4",
    "st_completed_bg": "#163326",
    "st_failed": "#ff9a8f",
    "st_failed_bg": "#3a1e1c",
    "st_skipped": "#bcafff",
    "st_skipped_bg": "#2a2547",
}

_TEMPLATE = """
QWidget {{ color: {text}; font-size: 13px; }}
QMainWindow, QDialog, #content {{ background-color: {bg}; }}
QToolTip {{
    background-color: {tooltip_bg}; color: {tooltip_text};
    border: none; border-radius: 4px; padding: 6px 8px;
}}
QLabel {{ background: transparent; }}

/* -- Sidebar ---------------------------------------------------------- */
#sidebar {{ background-color: {sidebar}; border-right: 1px solid {border}; }}
#appTitle {{ font-size: 17px; font-weight: 700; }}
#appSubtitle {{ color: {text_muted}; font-size: 12px; }}
#navList {{ background: transparent; border: none; outline: none; }}
#navList::item {{
    padding: 9px 10px; margin: 1px 0; border-radius: 8px; color: {text};
}}
#navList::item:hover {{ background-color: {header_bg}; }}
#navList::item:selected {{
    background-color: {sidebar_selected}; color: {accent}; font-weight: 600;
}}
#sideStatus {{ color: {text_muted}; font-size: 12px; }}
QPushButton#sideButton {{
    text-align: left; padding: 8px 10px; border: none; background: transparent;
}}
QPushButton#sideButton:hover {{ background-color: {header_bg}; }}

/* -- Cards and text ------------------------------------------------------ */
#card {{ background-color: {surface}; border: 1px solid {border}; border-radius: 10px; }}
#pageTitle {{ font-size: 19px; font-weight: 700; }}
#cardTitle {{ font-size: 14px; font-weight: 600; }}
#muted, #sectionHint {{ color: {text_muted}; }}
#fieldLabel {{ color: {text_muted}; font-size: 12px; font-weight: 600; padding-top: 8px; }}
#chip {{
    background-color: {accent_soft}; color: {accent}; border-radius: 10px;
    padding: 3px 10px; font-weight: 600; font-size: 12px;
}}
#emptyState {{ color: {text_muted}; font-size: 14px; padding: 24px; }}
#legalNotice {{
    background-color: {notice_bg}; border: 1px solid {notice_border}; border-radius: 8px;
}}
#legalNotice QLabel {{ color: {notice_text}; }}
#thumbnailFrame {{
    background-color: {bg_input}; border: 1px solid {border}; border-radius: 8px;
    color: {text_muted};
}}

/* -- Inputs ---------------------------------------------------------- */
QLineEdit, QComboBox, QSpinBox {{
    background-color: {bg_input}; color: {text};
    border: 1px solid {border}; border-radius: 7px;
    padding: 6px 9px; selection-background-color: {selection_bg};
    selection-color: {selection_text}; min-height: 18px;
}}
#urlEdit {{ font-size: 14px; padding: 8px 11px; }}
QLineEdit:hover, QComboBox:hover, QSpinBox:hover {{ border-color: {text_muted}; }}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{ border: 1.5px solid {border_strong}; }}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled {{
    background-color: {disabled_bg}; color: {disabled_text}; border-color: {disabled_bg};
}}
QComboBox {{ padding-right: 26px; }}
QComboBox::drop-down {{
    border: none; width: 24px; subcontrol-origin: padding; subcontrol-position: center right;
}}
QComboBox::down-arrow {{ image: url("{chevron}"); width: 11px; height: 11px; }}
QComboBox QAbstractItemView {{
    background-color: {surface}; color: {text}; border: 1px solid {border};
    outline: none; selection-background-color: {selection_bg};
    selection-color: {selection_text}; padding: 4px;
}}
QSpinBox {{ padding-right: 22px; }}
QSpinBox::up-button, QSpinBox::down-button {{
    subcontrol-origin: border; width: 20px; border: none; background: transparent;
}}
QSpinBox::up-button {{ subcontrol-position: top right; }}
QSpinBox::down-button {{ subcontrol-position: bottom right; }}
QSpinBox::up-arrow {{ image: url("{chevron_up}"); width: 9px; height: 9px; }}
QSpinBox::down-arrow {{ image: url("{chevron}"); width: 9px; height: 9px; }}

/* -- Buttons ----------------------------------------------------------
   Default buttons are a quiet, outlined "secondary" style; `accent`
   buttons (Fetch, Download, Download selected, Start all) are the primary
   action on each screen; `danger` ones remove or clear things. */
QPushButton {{
    background-color: {surface}; color: {text};
    border: 1px solid {border}; border-radius: 7px; padding: 7px 13px;
}}
QPushButton:hover {{ border-color: {text_muted}; background-color: {header_bg}; }}
QPushButton:pressed {{ background-color: {alt_row}; }}
QPushButton:disabled {{
    background-color: {disabled_bg}; color: {disabled_text}; border-color: {disabled_bg};
}}
QPushButton[accent="true"] {{
    background-color: {accent}; color: {accent_text}; border: 1px solid {accent};
    font-weight: 600;
}}
QPushButton[accent="true"]:hover {{ background-color: {accent_hover}; }}
QPushButton[accent="true"]:pressed {{ background-color: {accent_pressed}; }}
QPushButton[danger="true"] {{ color: {danger}; }}
QPushButton[danger="true"]:hover {{ background-color: {danger_soft}; border-color: {danger}; }}
QPushButton[flat="true"] {{
    background-color: transparent; color: {accent}; border: none; padding: 2px 4px;
    font-weight: 600;
}}
QPushButton[flat="true"]:hover {{ text-decoration: underline; background: transparent; }}
QPushButton[big="true"] {{ padding: 10px 18px; font-size: 14px; }}

QCheckBox {{ spacing: 8px; padding: 2px 0; background: transparent; }}
QCheckBox::indicator {{
    width: 16px; height: 16px; border-radius: 4px; border: 1.5px solid {border};
    background-color: {bg_input};
}}
QCheckBox::indicator:hover {{ border-color: {accent}; }}
QCheckBox::indicator:checked {{
    background-color: {accent}; border-color: {accent}; image: url("{check}");
}}

/* -- Tables ---------------------------------------------------------- */
QTableView {{
    background-color: {surface}; alternate-background-color: {alt_row};
    color: {text}; border: 1px solid {border}; border-radius: 8px;
    gridline-color: transparent; selection-background-color: {selection_bg};
    selection-color: {selection_text};
}}
QTableView::item {{ padding: 4px 6px; border: none; }}
QTableView::item:selected {{ background-color: {selection_bg}; color: {selection_text}; }}
QTableView::indicator {{
    width: 16px; height: 16px; border-radius: 4px; border: 1.5px solid {border};
    background-color: {bg_input};
}}
QTableView::indicator:checked {{
    background-color: {accent}; border-color: {accent}; image: url("{check}");
}}
QHeaderView {{ background-color: transparent; }}
QHeaderView::section {{
    background-color: {header_bg}; color: {text_muted}; font-weight: 600; font-size: 12px;
    padding: 7px 8px; border: none; border-bottom: 1px solid {border};
}}
QTableCornerButton::section {{ background-color: {header_bg}; border: none; }}

/* -- Menus / status ---------------------------------------------------- */
QMenuBar {{ background-color: {bg}; border-bottom: 1px solid {border}; }}
QMenuBar::item {{ padding: 5px 10px; background: transparent; border-radius: 5px; }}
QMenuBar::item:selected {{ background-color: {header_bg}; }}
QMenu {{
    background-color: {surface}; color: {text}; border: 1px solid {border};
    border-radius: 8px; padding: 4px;
}}
QMenu::item {{ padding: 6px 22px 6px 12px; border-radius: 5px; }}
QMenu::item:selected {{ background-color: {selection_bg}; color: {selection_text}; }}
QMenu::separator {{ height: 1px; background: {border}; margin: 4px 6px; }}
QStatusBar {{ background-color: {bg}; color: {text_muted}; border-top: 1px solid {border}; }}
QStatusBar::item {{ border: none; }}
QGroupBox {{
    background-color: {surface}; border: 1px solid {border}; border-radius: 10px;
    margin-top: 16px; padding: 16px 14px 12px 14px; font-weight: 600;
}}
QGroupBox::title {{
    subcontrol-origin: margin; subcontrol-position: top left;
    left: 12px; top: -1px; padding: 0 4px; color: {text}; background-color: transparent;
}}

QScrollBar:vertical {{ background: transparent; width: 12px; margin: 2px; }}
QScrollBar::handle:vertical {{
    background: {scrollbar}; border-radius: 5px; min-height: 24px;
}}
QScrollBar::handle:vertical:hover {{ background: {scrollbar_hover}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 12px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {scrollbar}; border-radius: 5px; min-width: 24px; }}
QScrollBar::handle:horizontal:hover {{ background: {scrollbar_hover}; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
"""

# Small SVGs the stylesheet references by file path (QSS can't inline them).
_ASSET_SVGS = {
    "chevron": '<path d="M6 9l6 6 6-6"/>',
    "chevron_up": '<path d="M6 15l6-6 6 6"/>',
    "check": '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
}

_current: dict[str, str] = dict(_LIGHT_TOKENS)


def resolve(theme: str) -> str:
    """Map a stored theme setting ("system", "light", "dark") to the palette
    actually shown."""
    if theme in ("light", "dark"):
        return theme
    scheme = QGuiApplication.styleHints().colorScheme()
    if scheme == Qt.ColorScheme.Dark:
        return "dark"
    if scheme == Qt.ColorScheme.Light:
        return "light"
    return "light"


def tokens(resolved: str) -> dict[str, str]:
    return _DARK_TOKENS if resolved == "dark" else _LIGHT_TOKENS


def current_tokens() -> dict[str, str]:
    """Tokens of the most recently applied theme, for custom-painted parts
    (status pills, progress bars, icons)."""
    return _current


def status_colors(status_value: str) -> tuple[str, str]:
    """(text, background) colours for a QueueStatus value's pill."""
    key = "queued" if status_value == "cancelled" else status_value
    t = _current
    return t.get(f"st_{key}", t["st_queued"]), t.get(f"st_{key}_bg", t["st_queued_bg"])


def _write_assets(resolved: str, t: dict[str, str]) -> dict[str, str]:
    folder = Path(tempfile.gettempdir()) / "ytdlmanager-theme"
    folder.mkdir(exist_ok=True)
    paths = {}
    for name, body in _ASSET_SVGS.items():
        color = t["accent_text"] if name == "check" else t["text_muted"]
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
            f'stroke="{color}" stroke-width="3" stroke-linecap="round" '
            f'stroke-linejoin="round">{body}</svg>'
        )
        path = folder / f"{name}-{resolved}.svg"
        path.write_text(svg, encoding="utf-8")
        paths[name] = path.as_posix()
    return paths


def stylesheet_for(theme: str) -> str:
    resolved = resolve(theme)
    t = tokens(resolved)
    return _TEMPLATE.format(**t, **_write_assets(resolved, t))


def _palette(t: dict[str, str]) -> QPalette:
    p = QPalette()
    role = QPalette.ColorRole
    for r, key in (
        (role.Window, "bg"),
        (role.WindowText, "text"),
        (role.Base, "bg_input"),
        (role.AlternateBase, "alt_row"),
        (role.Text, "text"),
        (role.Button, "surface"),
        (role.ButtonText, "text"),
        (role.Highlight, "accent"),
        (role.HighlightedText, "accent_text"),
        (role.ToolTipBase, "tooltip_bg"),
        (role.ToolTipText, "tooltip_text"),
        (role.PlaceholderText, "text_muted"),
        (role.Link, "accent"),
    ):
        p.setColor(r, QColor(t[key]))
    for r in (role.Text, role.WindowText, role.ButtonText):
        p.setColor(QPalette.ColorGroup.Disabled, r, QColor(t["disabled_text"]))
    return p


def apply(app: QApplication, theme: str) -> str:
    """Apply `theme` to the whole application; returns "light" or "dark"."""
    resolved = resolve(theme)
    _current.clear()
    _current.update(tokens(resolved))
    if app.style().name().lower() != "fusion":
        app.setStyle("Fusion")
    app.setPalette(_palette(_current))
    app.setStyleSheet(stylesheet_for(theme))
    return resolved
