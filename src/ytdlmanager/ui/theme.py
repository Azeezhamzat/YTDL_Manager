"""Light/dark stylesheets.

Both palettes are defined as a token dict fed into one shared QSS template,
so the two themes can't drift out of sync with each other structurally --
only the token values differ. Colours are chosen for WCAG AA contrast
against their paired background (>= 4.5:1 for body text). The "system"
theme leaves palette detection to Qt's native style and applies no QSS.
"""

from __future__ import annotations

_LIGHT_TOKENS = {
    "bg": "#f4f5f7",
    "bg_elevated": "#ffffff",
    "bg_input": "#ffffff",
    "border": "#d7dbe0",
    "border_strong": "#2f6fed",
    "text": "#1a1d21",
    "text_muted": "#5b6470",
    "accent": "#2f6fed",
    "accent_hover": "#255cc4",
    "accent_pressed": "#1e4ba0",
    "accent_text": "#ffffff",
    "disabled_bg": "#e7e9ec",
    "disabled_text": "#a2a8b0",
    "success": "#1e8e5a",
    "error": "#c0392b",
    "warning_bg": "#fff4e0",
    "warning_border": "#f0c274",
    "warning_text": "#7a4b00",
    "header_bg": "#eceef1",
    "selection_bg": "#dbe6fd",
    "selection_text": "#132a63",
    "alt_row": "#f8f9fb",
    "scrollbar": "#c7cbd1",
    "scrollbar_hover": "#a7acb4",
    "tooltip_bg": "#2a2d31",
    "tooltip_text": "#f4f5f7",
}

_DARK_TOKENS = {
    "bg": "#17181b",
    "bg_elevated": "#1f2124",
    "bg_input": "#242629",
    "border": "#34373c",
    "border_strong": "#5b8dff",
    "text": "#eceef0",
    "text_muted": "#9199a3",
    "accent": "#5b8dff",
    "accent_hover": "#7aa2ff",
    "accent_pressed": "#3f6fe0",
    "accent_text": "#0b0d10",
    "disabled_bg": "#2a2c30",
    "disabled_text": "#5c626a",
    "success": "#57c98a",
    "error": "#ff8a7e",
    "warning_bg": "#3a2c06",
    "warning_border": "#6b5400",
    "warning_text": "#ffd479",
    "header_bg": "#222429",
    "selection_bg": "#2c3a5c",
    "selection_text": "#dbe6fd",
    "alt_row": "#1b1d20",
    "scrollbar": "#3c4046",
    "scrollbar_hover": "#4d525a",
    "tooltip_bg": "#eceef0",
    "tooltip_text": "#17181b",
}

_TEMPLATE = """
QWidget {{ background-color: {bg}; color: {text}; font-size: 13px; }}
QMainWindow {{ background-color: {bg}; }}
QToolTip {{
    background-color: {tooltip_bg}; color: {tooltip_text};
    border: none; border-radius: 4px; padding: 6px 8px;
}}

/* -- Inputs ---------------------------------------------------------- */
QLineEdit, QComboBox, QSpinBox, QTextEdit {{
    background-color: {bg_input}; color: {text};
    border: 1px solid {border}; border-radius: 6px;
    padding: 5px 8px; selection-background-color: {selection_bg};
    selection-color: {selection_text};
}}
QLineEdit:hover, QComboBox:hover, QSpinBox:hover {{ border-color: {text_muted}; }}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QTextEdit:focus {{
    border: 1.5px solid {border_strong};
}}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled {{
    background-color: {disabled_bg}; color: {disabled_text}; border-color: {disabled_bg};
}}
QLineEdit::placeholder {{ color: {text_muted}; }}
QComboBox::drop-down {{
    border: none; width: 22px; subcontrol-origin: padding; subcontrol-position: center right;
}}
QComboBox QAbstractItemView {{
    background-color: {bg_elevated}; color: {text}; border: 1px solid {border};
    border-radius: 6px; outline: none; selection-background-color: {selection_bg};
    selection-color: {selection_text}; padding: 2px;
}}

/* -- Buttons ----------------------------------------------------------
   Default buttons are a quiet, outlined "secondary" style; buttons with
   the `accent` dynamic property (the app's main calls to action -- Fetch,
   Download, Download Selected) get a solid, higher-contrast treatment so
   the primary action on each screen is unambiguous at a glance. */
QPushButton {{
    background-color: {bg_elevated}; color: {text};
    border: 1px solid {border}; border-radius: 6px; padding: 7px 14px;
}}
QPushButton:hover {{ border-color: {text_muted}; }}
QPushButton:pressed {{ background-color: {alt_row}; }}
QPushButton:disabled {{
    background-color: {disabled_bg}; color: {disabled_text}; border-color: {disabled_bg};
}}
QPushButton[accent="true"] {{
    background-color: {accent}; color: {accent_text}; border: none; font-weight: 600;
}}
QPushButton[accent="true"]:hover {{ background-color: {accent_hover}; }}
QPushButton[accent="true"]:pressed {{ background-color: {accent_pressed}; }}
QPushButton[accent="true"]:disabled {{ background-color: {disabled_bg}; color: {disabled_text}; }}
QPushButton[flat="true"] {{ background-color: transparent; color: {accent}; border: none; }}
QPushButton[flat="true"]:hover {{ text-decoration: underline; }}
QPushButton:focus {{ border: 1.5px solid {border_strong}; }}

QCheckBox {{ spacing: 8px; padding: 2px 0; }}
QCheckBox::indicator {{
    width: 16px; height: 16px; border-radius: 4px; border: 1.5px solid {border};
    background-color: {bg_input};
}}
QCheckBox::indicator:hover {{ border-color: {accent}; }}
QCheckBox::indicator:checked {{ background-color: {accent}; border-color: {accent}; }}

/* -- Groups / cards ---------------------------------------------------- */
QGroupBox {{
    background-color: {bg_elevated}; border: 1px solid {border}; border-radius: 8px;
    margin-top: 14px; padding: 14px 12px 12px 12px; font-weight: 600;
}}
QGroupBox::title {{
    subcontrol-origin: margin; subcontrol-position: top left;
    left: 10px; top: -2px; padding: 0 4px; color: {text}; background-color: transparent;
}}

/* -- Tabs ---------------------------------------------------------- */
QTabWidget::pane {{
    border: 1px solid {border}; border-radius: 8px; background-color: {bg_elevated}; top: -1px;
}}
QTabBar::tab {{
    background-color: transparent; color: {text_muted};
    padding: 8px 16px; margin-right: 2px; border-top-left-radius: 6px; border-top-right-radius: 6px;
}}
QTabBar::tab:hover {{ color: {text}; }}
QTabBar::tab:selected {{
    background-color: {bg_elevated}; color: {accent}; font-weight: 600;
    border: 1px solid {border}; border-bottom: none;
}}

/* -- Tables ---------------------------------------------------------- */
QTableView {{
    background-color: {bg_elevated}; alternate-background-color: {alt_row};
    color: {text}; border: 1px solid {border}; border-radius: 8px;
    gridline-color: transparent; selection-background-color: {selection_bg};
    selection-color: {selection_text};
}}
QTableView::item {{ padding: 6px 4px; border: none; }}
QTableView::item:selected {{ background-color: {selection_bg}; color: {selection_text}; }}
QHeaderView::section {{
    background-color: {header_bg}; color: {text_muted}; font-weight: 600;
    padding: 8px 6px; border: none; border-bottom: 1px solid {border};
}}
QTableCornerButton::section {{ background-color: {header_bg}; border: none; }}

/* -- Progress / status -------------------------------------------------- */
QProgressBar {{
    border: 1px solid {border}; border-radius: 6px; background-color: {bg_input};
    text-align: center; color: {text}; min-height: 16px;
}}
QProgressBar::chunk {{ background-color: {accent}; border-radius: 5px; }}
QStatusBar {{ background-color: {bg}; color: {text_muted}; border-top: 1px solid {border}; }}
QStatusBar::item {{ border: none; }}

/* -- Misc ---------------------------------------------------------- */
QLabel#pageTitle {{ font-size: 17px; font-weight: 700; }}
QLabel#sectionHint {{ color: {text_muted}; }}
QLabel#thumbnailFrame {{
    background-color: {bg_input}; border: 1px solid {border}; border-radius: 8px;
    color: {text_muted};
}}
#legalNotice {{
    background-color: {warning_bg}; color: {warning_text}; border: 1px solid {warning_border};
    border-radius: 8px; padding: 10px 12px;
}}
#statusError {{ color: {error}; font-weight: 600; }}
#statusOk {{ color: {success}; font-weight: 600; }}

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


def _render(tokens: dict[str, str]) -> str:
    return _TEMPLATE.format(**tokens)


_LIGHT_QSS = _render(_LIGHT_TOKENS)
_DARK_QSS = _render(_DARK_TOKENS)


def stylesheet_for(theme: str) -> str:
    if theme == "dark":
        return _DARK_QSS
    if theme == "light":
        return _LIGHT_QSS
    return ""  # "system": defer to native style
