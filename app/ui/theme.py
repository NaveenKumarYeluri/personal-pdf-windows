"""Modern Adobe Acrobat-inspired Light & Dark Qt stylesheets."""

from __future__ import annotations

import sys
from pathlib import Path

ACCENT_PRESETS: list[tuple[str, str, str]] = [
    ("Acrobat Red", "#ea2e3f", "#d22333"),
    ("Royal Blue", "#2563eb", "#1d4ed8"),
    ("Emerald Green", "#059669", "#047857"),
    ("Amethyst Purple", "#7c3aed", "#6d28d9"),
    ("Sunset Amber", "#d97706", "#b45309"),
    ("Ocean Teal", "#0d9488", "#0f766e"),
    ("Slate Gray", "#475569", "#334155"),
]


def _icon_url(filename: str) -> str:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
    return (base / "assets" / "icons" / filename).as_posix()


def build_stylesheet(dark: bool = False, accent: str = "#ea2e3f", accent_hover: str = "#d22333") -> str:
    mode = "dark" if dark else "light"
    chev_down = _icon_url(f"chevron-down-{mode}.svg")
    chev_up = _icon_url(f"chevron-up-{mode}.svg")

    if dark:
        bg_app = "#1b1c20"
        bg_bar = "#25262c"
        bg_rail = "#202127"
        bg_hover = "#32343d"
        bg_canvas = "#2f313a"
        bg_input = "#1d1e24"
        border = "#383b46"
        text_primary = "#f1f5f9"
        text_secondary = "#94a3b8"
        tab_active = "#32343d"
    else:
        bg_app = "#f4f5f7"
        bg_bar = "#ffffff"
        bg_rail = "#f8fafc"
        bg_hover = "#e2e8f0"
        bg_canvas = "#dfe3ea"
        bg_input = "#f1f5f9"
        border = "#d4d8e1"
        text_primary = "#0f172a"
        text_secondary = "#475569"
        tab_active = "#f1f5f9"

    return f"""
    QMainWindow, QDialog {{
        background-color: {bg_app};
        color: {text_primary};
        font-family: "Segoe UI", Arial, sans-serif;
        font-size: 12.5px;
    }}
    QWidget {{
        color: {text_primary};
    }}
    QFrame#UnifiedTopBar {{
        background-color: {bg_bar};
        border-bottom: 1px solid {border};
    }}
    QFrame#ContextBar {{
        background-color: {bg_rail};
        border-bottom: 1px solid {border};
    }}
    QFrame#LeftToolsRail {{
        background-color: {bg_rail};
        border-right: 1px solid {border};
    }}
    QFrame#RightNavRail {{
        background-color: {bg_rail};
        border-left: 1px solid {border};
    }}
    QFrame#Sidebar {{
        background-color: {bg_bar};
        border-left: 1px solid {border};
    }}
    QScrollArea#ViewerScroll {{
        background-color: {bg_canvas};
        border: none;
    }}
    QPushButton {{
        background-color: transparent;
        color: {text_primary};
        border: 1px solid transparent;
        border-radius: 6px;
        padding: 4px 9px;
        font-weight: 500;
    }}
    QPushButton:hover {{
        background-color: {bg_hover};
    }}
    QPushButton:checked {{
        background-color: {accent};
        color: #ffffff;
    }}
    QPushButton#RailBtn, QToolButton#RailBtn {{
        background-color: transparent;
        color: {text_primary};
        border: none;
        border-radius: 8px;
        padding: 5px 2px;
        font-size: 10.5px;
        font-weight: 500;
    }}
    QPushButton#RailBtn:hover, QToolButton#RailBtn:hover {{
        background-color: {bg_hover};
    }}
    QPushButton#RailBtn:checked, QToolButton#RailBtn:checked {{
        background-color: {accent};
        color: #ffffff;
    }}
    QPushButton#PillBtn {{
        background-color: {bg_input};
        border: 1px solid {border};
        border-radius: 6px;
        padding: 4px 10px;
    }}
    QPushButton#PillBtn:hover {{
        background-color: {bg_hover};
    }}
    QPushButton#PrimaryBtn {{
        background-color: {accent};
        color: #ffffff;
        border: none;
        border-radius: 6px;
        padding: 5px 12px;
        font-weight: 600;
    }}
    QPushButton#PrimaryBtn:hover {{
        background-color: {accent_hover};
    }}
    QPushButton#DangerBtn {{
        background-color: #dc2626;
        color: #ffffff;
        border-radius: 6px;
        padding: 4px 10px;
    }}
    QFrame#TopDivider {{
        background-color: {border};
        max-width: 1px;
        min-width: 1px;
        margin: 6px 4px;
    }}
    QPushButton#MenuBtn {{
        background-color: transparent;
        border: 1px solid transparent;
        border-radius: 6px;
        padding: 4px 20px 4px 8px;
        font-weight: 600;
    }}
    QPushButton#MenuBtn:hover {{
        background-color: {bg_hover};
    }}
    QPushButton#MenuBtn::menu-indicator,
    QPushButton#ZoomMenuBtn::menu-indicator {{
        image: url({chev_down});
        width: 12px;
        height: 12px;
        subcontrol-position: right center;
        subcontrol-origin: padding;
        right: 6px;
    }}
    QPushButton#ZoomMenuBtn {{
        background-color: {bg_input};
        color: {text_primary};
        border: 1px solid {border};
        border-radius: 6px;
        padding: 0px 22px 0px 8px;
        min-height: 26px;
        max-height: 28px;
        font-weight: 500;
        text-align: left;
    }}
    QPushButton#ZoomMenuBtn:hover {{
        background-color: {bg_hover};
        border-color: {accent};
    }}
    QLineEdit#PageInput {{
        background-color: {bg_input};
        color: {text_primary};
        border: 1px solid {border};
        border-radius: 6px;
        padding: 0px 4px;
        min-height: 24px;
        max-height: 26px;
        font-weight: 600;
    }}
    QLineEdit#PageInput:focus {{
        border-color: {accent};
        background-color: {bg_bar};
    }}
    QLineEdit#SearchInput {{
        background-color: {bg_input};
        color: {text_primary};
        border: 1px solid {border};
        border-radius: 13px;
        padding: 0px 10px;
        min-height: 24px;
        max-height: 26px;
    }}
    QLineEdit#SearchInput:focus {{
        border-color: {accent};
        background-color: {bg_bar};
    }}
    QComboBox, QSpinBox, QLineEdit {{
        background-color: {bg_input};
        color: {text_primary};
        border: 1px solid {border};
        border-radius: 6px;
        padding: 3px 8px;
        min-height: 24px;
    }}
    QComboBox {{
        padding-right: 24px;
    }}
    QComboBox::drop-down {{
        subcontrol-origin: padding;
        subcontrol-position: top right;
        width: 20px;
        border: none;
        background: transparent;
    }}
    QComboBox::down-arrow {{
        image: url({chev_down});
        width: 12px;
        height: 12px;
    }}
    QComboBox QAbstractItemView {{
        background-color: {bg_bar};
        color: {text_primary};
        border: 1px solid {border};
        border-radius: 6px;
        selection-background-color: {accent};
        selection-color: #ffffff;
        padding: 4px;
    }}
    QSpinBox {{
        padding-right: 20px;
    }}
    QSpinBox::up-button, QSpinBox::down-button {{
        subcontrol-origin: border;
        width: 16px;
        border: none;
        background: transparent;
    }}
    QSpinBox::up-button {{
        subcontrol-position: top right;
        margin-top: 2px;
        margin-right: 2px;
    }}
    QSpinBox::down-button {{
        subcontrol-position: bottom right;
        margin-bottom: 2px;
        margin-right: 2px;
    }}
    QSpinBox::up-button:hover, QSpinBox::down-button:hover {{
        background-color: {bg_hover};
        border-radius: 3px;
    }}
    QSpinBox::up-arrow {{
        image: url({chev_up});
        width: 10px;
        height: 10px;
    }}
    QSpinBox::down-arrow {{
        image: url({chev_down});
        width: 10px;
        height: 10px;
    }}
    QComboBox:focus, QLineEdit:focus, QSpinBox:focus {{
        border-color: {accent};
    }}
    QTabBar#InlineDocTabs {{
        background: transparent;
        border: none;
    }}
    QTabBar#InlineDocTabs::tab {{
        background-color: transparent;
        color: {text_secondary};
        border: 1px solid transparent;
        border-radius: 6px;
        padding: 4px 10px;
        margin: 0px 2px;
        max-width: 190px;
    }}
    QTabBar#InlineDocTabs::tab:hover {{
        background-color: {bg_hover};
        color: {text_primary};
    }}
    QTabBar#InlineDocTabs::tab:selected {{
        background-color: {tab_active};
        color: {text_primary};
        border: 1px solid {border};
        font-weight: 600;
    }}
    QListWidget {{
        background-color: {bg_bar};
        border: none;
        outline: none;
    }}
    QListWidget::item {{
        padding: 6px;
        border-radius: 6px;
    }}
    QListWidget::item:selected {{
        background-color: {accent};
        color: #ffffff;
    }}
    QStatusBar {{
        background-color: {bg_bar};
        border-top: 1px solid {border};
        color: {text_secondary};
        font-size: 11.5px;
    }}
    QMenu {{
        background-color: {bg_bar};
        color: {text_primary};
        border: 1px solid {border};
        border-radius: 8px;
        padding: 4px;
    }}
    QMenu::item {{
        padding: 6px 18px;
        border-radius: 4px;
    }}
    QMenu::item:selected {{
        background-color: {accent};
        color: #ffffff;
    }}
    QFrame#FloatingFormatBar {{
        background-color: #ffffff;
        color: #0f172a;
        border: 1px solid #cbd5e1;
        border-radius: 8px;
    }}
    QFrame#FloatingFormatBar QPushButton {{
        background-color: #f8fafc;
        color: #0f172a;
        border: 1px solid #e2e8f0;
        padding: 3px 7px;
        min-width: 26px;
    }}
    QFrame#FloatingFormatBar QPushButton:checked {{
        background-color: {accent};
        color: #ffffff;
        border-color: {accent_hover};
    }}
    QFrame#FloatingFormatBar QComboBox, QFrame#FloatingFormatBar QSpinBox {{
        background-color: #ffffff;
        color: #0f172a;
        border: 1px solid #cbd5e1;
    }}
    QFrame#FloatingFormatBar QComboBox::down-arrow {{
        image: url({_icon_url("chevron-down-light.svg")});
    }}
    QFrame#FloatingFormatBar QSpinBox::up-arrow {{
        image: url({_icon_url("chevron-up-light.svg")});
    }}
    QFrame#FloatingFormatBar QSpinBox::down-arrow {{
        image: url({_icon_url("chevron-down-light.svg")});
    }}
    """
