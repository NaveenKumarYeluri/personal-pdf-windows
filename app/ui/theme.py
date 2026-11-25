"""Light & Dark Qt stylesheets with customizable accent colors."""

from __future__ import annotations

ACCENT_PRESETS: list[tuple[str, str, str]] = [
    ("Acrobat Red", "#ea2e3f", "#d22333"),
    ("Royal Blue", "#2563eb", "#1d4ed8"),
    ("Emerald Green", "#059669", "#047857"),
    ("Amethyst Purple", "#7c3aed", "#6d28d9"),
    ("Sunset Amber", "#d97706", "#b45309"),
    ("Ocean Teal", "#0d9488", "#0f766e"),
    ("Slate Gray", "#475569", "#334155"),
]


def build_stylesheet(dark: bool = False, accent: str = "#ea2e3f", accent_hover: str = "#d22333") -> str:
    if dark:
        bg_primary = "#18191c"
        bg_secondary = "#222328"
        bg_tertiary = "#2a2b32"
        bg_canvas = "#2b2d36"
        border = "#383a45"
        text_primary = "#f0f2f5"
        text_secondary = "#a0a4b0"
    else:
        bg_primary = "#f8fafc"
        bg_secondary = "#ffffff"
        bg_tertiary = "#f1f5f9"
        bg_canvas = "#e2e8f0"
        border = "#cbd5e1"
        text_primary = "#0f172a"
        text_secondary = "#475569"

    return f"""
    QMainWindow, QDialog {{
        background-color: {bg_primary};
        color: {text_primary};
        font-family: "Segoe UI", Arial, sans-serif;
        font-size: 13px;
    }}
    QWidget {{
        color: {text_primary};
    }}
    QFrame#TopBar, QFrame#ModeRibbon, QFrame#SubToolbar {{
        background-color: {bg_secondary};
        border-bottom: 1px solid {border};
    }}
    QFrame#Sidebar, QFrame#ToolsPane {{
        background-color: {bg_secondary};
        border-right: 1px solid {border};
    }}
    QFrame#ToolsPane {{
        border-right: none;
        border-left: 1px solid {border};
    }}
    QScrollArea#ViewerScroll {{
        background-color: {bg_canvas};
        border: none;
    }}
    QPushButton {{
        background-color: {bg_tertiary};
        color: {text_primary};
        border: 1px solid {border};
        border-radius: 5px;
        padding: 5px 10px;
        font-weight: 500;
    }}
    QPushButton:hover {{
        background-color: {border};
    }}
    QPushButton:checked, QPushButton[active="true"] {{
        background-color: {accent};
        color: #ffffff;
        border-color: {accent_hover};
    }}
    QPushButton#PrimaryBtn {{
        background-color: {accent};
        color: #ffffff;
        border-color: {accent_hover};
        font-weight: 600;
    }}
    QPushButton#PrimaryBtn:hover {{
        background-color: {accent_hover};
    }}
    QPushButton#DangerBtn {{
        background-color: #dc2626;
        color: #ffffff;
        border-color: #b91c1c;
    }}
    QPushButton#ModeTabBtn {{
        background-color: transparent;
        border: none;
        border-bottom: 2px solid transparent;
        border-radius: 0px;
        padding: 7px 14px;
        font-weight: 600;
        color: {text_secondary};
    }}
    QPushButton#ModeTabBtn:hover {{
        color: {text_primary};
        background-color: {bg_tertiary};
    }}
    QPushButton#ModeTabBtn:checked {{
        color: {accent};
        border-bottom: 3px solid {accent};
        background-color: transparent;
    }}
    QComboBox, QSpinBox, QLineEdit {{
        background-color: {bg_secondary};
        color: {text_primary};
        border: 1px solid {border};
        border-radius: 4px;
        padding: 4px 6px;
        min-height: 24px;
    }}
    QTabWidget::pane {{
        border: none;
        background-color: {bg_canvas};
    }}
    QTabBar::tab {{
        background-color: {bg_tertiary};
        color: {text_secondary};
        border: 1px solid {border};
        border-bottom: none;
        padding: 6px 14px;
        margin-right: 2px;
        border-top-left-radius: 6px;
        border-top-right-radius: 6px;
    }}
    QTabBar::tab:selected {{
        background-color: {bg_secondary};
        color: {text_primary};
        font-weight: 600;
    }}
    QListWidget {{
        background-color: {bg_secondary};
        border: none;
        outline: none;
    }}
    QListWidget::item {{
        padding: 6px;
        border-radius: 4px;
    }}
    QListWidget::item:selected {{
        background-color: {accent};
        color: #ffffff;
    }}
    QStatusBar {{
        background-color: {bg_secondary};
        border-top: 1px solid {border};
        color: {text_secondary};
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
    """
