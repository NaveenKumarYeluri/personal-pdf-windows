"""Floating rich-text formatting toolbar attached above active text editors."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QPushButton,
    QSpinBox,
    QWidget,
)

from app.core.fonts import available_ui_fonts


class FloatingFormatBar(QFrame):
    """Floating Word-style toolbar for active in-place text edits and text boxes."""

    fontChanged = Signal(str)
    sizeChanged = Signal(int)
    boldToggled = Signal(bool)
    italicToggled = Signal(bool)
    underlineToggled = Signal(bool)
    strikeToggled = Signal(bool)
    alignChanged = Signal(str)
    colorChanged = Signal(str)
    reflowToggled = Signal(bool)
    revertRequested = Signal()
    doneRequested = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("FloatingFormatBar")
        self.setWindowFlags(Qt.Widget)
        self._syncing = False
        self._current_color = "#000000"

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 5, 8, 5)
        layout.setSpacing(4)

        self.font_combo = QComboBox()
        self.font_combo.addItems(available_ui_fonts())
        self.font_combo.setMinimumWidth(120)
        self.font_combo.setFocusPolicy(Qt.NoFocus)
        self.font_combo.currentTextChanged.connect(self._on_font)
        layout.addWidget(self.font_combo)

        self.size_spin = QSpinBox()
        self.size_spin.setRange(6, 96)
        self.size_spin.setValue(12)
        self.size_spin.setSuffix("pt")
        self.size_spin.setFocusPolicy(Qt.ClickFocus)
        self.size_spin.valueChanged.connect(self._on_size)
        layout.addWidget(self.size_spin)

        self.btn_bold = self._make_toggle("B", "Bold (Ctrl+B)")
        self.btn_bold.setStyleSheet("font-weight:bold;")
        self.btn_bold.clicked.connect(lambda chk: not self._syncing and self.boldToggled.emit(chk))
        layout.addWidget(self.btn_bold)

        self.btn_italic = self._make_toggle("I", "Italic (Ctrl+I)")
        self.btn_italic.setStyleSheet("font-style:italic;")
        self.btn_italic.clicked.connect(lambda chk: not self._syncing and self.italicToggled.emit(chk))
        layout.addWidget(self.btn_italic)

        self.btn_underline = self._make_toggle("U", "Underline (Ctrl+U)")
        self.btn_underline.setStyleSheet("text-decoration:underline;")
        self.btn_underline.clicked.connect(lambda chk: not self._syncing and self.underlineToggled.emit(chk))
        layout.addWidget(self.btn_underline)

        self.btn_strike = self._make_toggle("S", "Strikethrough")
        self.btn_strike.setStyleSheet("text-decoration:line-through;")
        self.btn_strike.clicked.connect(lambda chk: not self._syncing and self.strikeToggled.emit(chk))
        layout.addWidget(self.btn_strike)

        self.btn_color = QPushButton("A")
        self.btn_color.setToolTip("Text Color")
        self.btn_color.setFocusPolicy(Qt.NoFocus)
        self.btn_color.clicked.connect(self._pick_color)
        layout.addWidget(self.btn_color)

        self.btn_align_l = self._make_action("L", "Align Left", lambda: self.alignChanged.emit("left"))
        self.btn_align_c = self._make_action("C", "Align Center", lambda: self.alignChanged.emit("center"))
        self.btn_align_r = self._make_action("R", "Align Right", lambda: self.alignChanged.emit("right"))
        layout.addWidget(self.btn_align_l)
        layout.addWidget(self.btn_align_c)
        layout.addWidget(self.btn_align_r)

        self.chk_reflow = QCheckBox("Reflow")
        self.chk_reflow.setChecked(True)
        self.chk_reflow.setToolTip("Automatically shift lower paragraphs down and cascade across pages")
        self.chk_reflow.setFocusPolicy(Qt.NoFocus)
        self.chk_reflow.toggled.connect(lambda v: not self._syncing and self.reflowToggled.emit(v))
        layout.addWidget(self.chk_reflow)

        self.btn_revert = self._make_action("↩", "Revert Edit (Esc)", self.revertRequested.emit)
        self.btn_done = self._make_action("✓", "Commit Edit", self.doneRequested.emit)
        self.btn_done.setStyleSheet("color:#16a34a; font-weight:700;")
        layout.addWidget(self.btn_revert)
        layout.addWidget(self.btn_done)

        self.adjustSize()
        self.hide()

    def _make_toggle(self, text: str, tip: str) -> QPushButton:
        btn = QPushButton(text)
        btn.setCheckable(True)
        btn.setToolTip(tip)
        btn.setFocusPolicy(Qt.NoFocus)
        return btn

    def _make_action(self, text: str, tip: str, slot) -> QPushButton:
        btn = QPushButton(text)
        btn.setToolTip(tip)
        btn.setFocusPolicy(Qt.NoFocus)
        btn.clicked.connect(slot)
        return btn

    def _on_font(self, font_name: str) -> None:
        if not self._syncing and font_name:
            self.fontChanged.emit(font_name)

    def _on_size(self, val: int) -> None:
        if not self._syncing:
            self.sizeChanged.emit(val)

    def _pick_color(self) -> None:
        col = QColorDialog.getColor(QColor(self._current_color), self, "Select Text Color")
        if col.isValid():
            self.set_color_preview(col.name())
            self.colorChanged.emit(col.name())

    def set_color_preview(self, color_hex: str) -> None:
        self._current_color = color_hex
        self.btn_color.setStyleSheet(
            f"font-weight:700; color:{color_hex}; border-bottom: 3px solid {color_hex};"
        )

    def sync_from_state(
        self,
        font_family: str,
        size_pt: int,
        bold: bool,
        italic: bool,
        underline: bool,
        strike: bool,
        color_hex: str,
        reflow_enabled: bool = True,
    ) -> None:
        self._syncing = True
        try:
            idx = self.font_combo.findText(font_family, Qt.MatchFixedString)
            if idx >= 0:
                self.font_combo.setCurrentIndex(idx)
            self.size_spin.setValue(max(6, min(96, int(round(size_pt)))))
            self.btn_bold.setChecked(bold)
            self.btn_italic.setChecked(italic)
            self.btn_underline.setChecked(underline)
            self.btn_strike.setChecked(strike)
            self.set_color_preview(color_hex)
            self.chk_reflow.setChecked(reflow_enabled)
        finally:
            self._syncing = False
