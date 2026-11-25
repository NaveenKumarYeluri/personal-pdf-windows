"""Application entry point for Personal PDF Suite (Windows Edition)."""

from __future__ import annotations

import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from app.ui.main_window import MainWindow


def main() -> int:
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    qt_app = QApplication(sys.argv)
    qt_app.setApplicationName("Personal PDF Suite")
    qt_app.setOrganizationName("Personal")

    initial_file = sys.argv[1] if len(sys.argv) > 1 else None
    win = MainWindow(initial_file=initial_file)
    win.show()
    return qt_app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
