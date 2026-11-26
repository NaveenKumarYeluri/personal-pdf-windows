"""Native Qt QTextEdit rich-text overlay for stable in-place PDF block editing."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QKeyEvent,
    QTextBlockFormat,
    QTextCharFormat,
    QTextCursor,
)
from PySide6.QtWidgets import QFrame, QTextEdit, QWidget

from app.core.models import TextBlock


class InPlaceTextEditor(QTextEdit):
    """
    Rich-text overlay positioned directly on top of a PDF text block.
    Supports Word-like selection formatting, scoped Ctrl+A, auto-grow height,
    Esc revert, and zero execCommand/DOM hacks.
    """

    commitRequested = Signal(object)
    revertRequested = Signal(object)
    formatStateChanged = Signal()
    heightChanged = Signal(int)

    def __init__(
        self,
        block: TextBlock,
        zoom: float,
        accent_color: str = "#ea2e3f",
        is_new_block: bool = False,
        reflow_enabled: bool = True,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.pdf_block = block
        self.zoom = zoom
        self.is_new_block = is_new_block
        self.reflow_enabled = reflow_enabled
        self._committed = False

        self.setFrameShape(QFrame.Box)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setAcceptRichText(False)  # Paste cleanly as plain text while preserving editor formatting
        self.setLineWrapMode(QTextEdit.WidgetWidth)
        self.setTabChangesFocus(False)

        # Compact margins matching PDF coordinates 1:1
        self.document().setDocumentMargin(2.0)
        self.setStyleSheet(
            f"""
            QTextEdit {{
                background-color: #ffffff;
                color: {block.color_hex};
                border: 2px solid {accent_color};
                border-radius: 2px;
                padding: 0px;
                selection-background-color: #3b82f6;
                selection-color: #ffffff;
            }}
            """
        )

        # Base font scaled visually by zoom
        base_font = QFont(block.font_family)
        base_font.setPointSizeF(max(6.0, block.font_size * zoom))
        base_font.setBold(block.is_bold)
        base_font.setItalic(block.is_italic)
        self.setFont(base_font)

        # Populate rich HTML scaled cleanly for screen display
        self.setHtml(self._scale_html_for_display(block.html, zoom))
        self._apply_compact_block_format()

        self.document().contentsChanged.connect(self._on_contents_changed)
        self.cursorPositionChanged.connect(self.formatStateChanged.emit)
        self.selectionChanged.connect(self.formatStateChanged.emit)

    def _scale_html_for_display(self, html: str, zoom: float) -> str:
        import re

        def _scale_pt(m: re.Match[str]) -> str:
            val = float(m.group(1))
            return f"font-size:{max(2.0, val * zoom):.1f}pt"

        return re.sub(r"font-size\s*:\s*(\d+(?:\.\d+)?)pt", _scale_pt, html)

    def _apply_compact_block_format(self) -> None:
        cur = self.textCursor()
        cur.select(QTextCursor.Document)
        bfmt = QTextBlockFormat()
        bfmt.setTopMargin(0)
        bfmt.setBottomMargin(3)
        bfmt.setLineHeight(self.pdf_block.line_height_ratio * 100.0, QTextBlockFormat.ProportionalHeight.value)
        cur.mergeBlockFormat(bfmt)

    def _on_contents_changed(self) -> None:
        doc_h = int(self.document().size().height()) + 6
        min_h = max(24, int((self.pdf_block.rect[3] - self.pdf_block.rect[1]) * self.zoom) + 6)
        new_h = max(min_h, doc_h)
        if abs(new_h - self.height()) > 1:
            self.setFixedHeight(new_h)
            self.heightChanged.emit(new_h)

    def sync_zoom(self, new_zoom: float) -> None:
        """Update visual font sizing and bounds when user zooms."""
        ratio = new_zoom / max(0.1, self.zoom)
        self.zoom = new_zoom
        cur = self.textCursor()
        cur.select(QTextCursor.Document)
        fmt = cur.charFormat()
        if fmt.fontPointSize() > 0:
            fmt.setFontPointSize(fmt.fontPointSize() * ratio)
            cur.mergeCharFormat(fmt)
        x0, y0, x1, y1 = self.pdf_block.rect
        self.setGeometry(
            int(x0 * new_zoom) - 2,
            int(y0 * new_zoom) - 2,
            max(60, int((x1 - x0) * new_zoom) + 8),
            max(26, int((y1 - y0) * new_zoom) + 6),
        )
        self._on_contents_changed()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key_Escape:
            event.accept()
            self.revertRequested.emit(self)
            return
        if event.modifiers() & Qt.ControlModifier:
            if event.key() in (Qt.Key_Return, Qt.Key_Enter):
                event.accept()
                self.commitRequested.emit(self)
                return
            if event.key() == Qt.Key_A:
                event.accept()
                self.selectAll()
                return
            if event.key() == Qt.Key_B:
                event.accept()
                self.toggle_bold()
                return
            if event.key() == Qt.Key_I:
                event.accept()
                self.toggle_italic()
                return
            if event.key() == Qt.Key_U:
                event.accept()
                self.toggle_underline()
                return
        super().keyPressEvent(event)

    def _target_cursor(self) -> QTextCursor:
        cur = self.textCursor()
        if not cur.hasSelection():
            cur.select(QTextCursor.Document)
        return cur

    def set_font_family(self, family: str) -> None:
        cur = self._target_cursor()
        fmt = QTextCharFormat()
        fmt.setFontFamilies([family])
        cur.mergeCharFormat(fmt)
        self.pdf_block.font_family = family
        self.formatStateChanged.emit()

    def set_font_size_pt(self, size_pt: int) -> None:
        cur = self._target_cursor()
        fmt = QTextCharFormat()
        fmt.setFontPointSize(max(2.0, float(size_pt) * self.zoom))
        cur.mergeCharFormat(fmt)
        self.pdf_block.font_size = float(size_pt)
        self._on_contents_changed()
        self.formatStateChanged.emit()

    def toggle_bold(self, active: bool | None = None) -> None:
        cur = self._target_cursor()
        state = self.current_format_state()
        target = (not state["bold"]) if active is None else active
        fmt = QTextCharFormat()
        fmt.setFontWeight(QFont.Bold if target else QFont.Normal)
        cur.mergeCharFormat(fmt)
        self.formatStateChanged.emit()

    def toggle_italic(self, active: bool | None = None) -> None:
        cur = self._target_cursor()
        state = self.current_format_state()
        target = (not state["italic"]) if active is None else active
        fmt = QTextCharFormat()
        fmt.setFontItalic(target)
        cur.mergeCharFormat(fmt)
        self.formatStateChanged.emit()

    def toggle_underline(self, active: bool | None = None) -> None:
        cur = self._target_cursor()
        state = self.current_format_state()
        target = (not state["underline"]) if active is None else active
        fmt = QTextCharFormat()
        fmt.setFontUnderline(target)
        cur.mergeCharFormat(fmt)
        self.formatStateChanged.emit()

    def toggle_strike(self, active: bool | None = None) -> None:
        cur = self._target_cursor()
        state = self.current_format_state()
        target = (not state["strike"]) if active is None else active
        fmt = QTextCharFormat()
        fmt.setFontStrikeOut(target)
        cur.mergeCharFormat(fmt)
        self.formatStateChanged.emit()

    def set_text_color(self, color_hex: str) -> None:
        cur = self._target_cursor()
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(color_hex))
        cur.mergeCharFormat(fmt)
        self.pdf_block.color_hex = color_hex
        self.formatStateChanged.emit()

    def set_alignment(self, align: str) -> None:
        cur = self.textCursor()
        if not cur.hasSelection():
            cur.select(QTextCursor.BlockUnderCursor)
        mapping = {
            "left": Qt.AlignLeft,
            "center": Qt.AlignHCenter,
            "right": Qt.AlignRight,
            "justify": Qt.AlignJustify,
        }
        bfmt = QTextBlockFormat()
        bfmt.setAlignment(mapping.get(align, Qt.AlignLeft))
        cur.mergeBlockFormat(bfmt)

    def current_format_state(self) -> dict:
        cur = self.textCursor()
        fmt = cur.charFormat()
        fams = fmt.fontFamilies()
        fam = fams[0] if fams else (fmt.font().family() or self.pdf_block.font_family)
        screen_pt = fmt.fontPointSize() or (self.pdf_block.font_size * self.zoom)
        pdf_pt = round(screen_pt / max(0.1, self.zoom))
        col = fmt.foreground().color().name() if fmt.foreground().style() != Qt.NoBrush else self.pdf_block.color_hex
        return {
            "font_family": fam,
            "size_pt": max(6, int(pdf_pt)),
            "bold": fmt.fontWeight() >= QFont.Bold,
            "italic": fmt.fontItalic(),
            "underline": fmt.fontUnderline(),
            "strike": fmt.fontStrikeOut(),
            "color": col,
        }

    def export_unscaled_html(self) -> str:
        """Convert current editor HTML back from screen-zoomed pt to true 1.0x PDF points."""
        import re

        raw = self.toHtml()

        def _unscale_pt(m: re.Match[str]) -> str:
            val = float(m.group(1))
            return f"font-size:{max(6.0, val / max(0.1, self.zoom)):.1f}pt"

        return re.sub(r"font-size\s*:\s*(\d+(?:\.\d+)?)pt", _unscale_pt, raw)
