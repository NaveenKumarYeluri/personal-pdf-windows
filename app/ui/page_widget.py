"""Interactive high-DPI PDF page canvas supporting editing, selection, ink, stamps, and overlays."""

from __future__ import annotations

import math
import uuid

import pymupdf
import shiboken6
from PySide6.QtCore import QPointF, QRect, QRectF, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QImage,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import QInputDialog, QScrollArea, QWidget

from app.core.ligatures import heal_pdf_text
from app.core.models import (
    FloatingTextBox,
    InkStroke,
    RedactionBox,
    SearchHit,
    ShapeItem,
    StampItem,
    StickyNote,
    TextBlock,
    TextEditOp,
    TextMarkup,
    WhiteoutBox,
)
from app.core.pdf_engine import PDFDocumentEngine, qt_html_to_clean_html
from app.ui.text_editor import InPlaceTextEditor


def _qcolor(hex_str: str, alpha: float = 1.0) -> QColor:
    c = QColor(hex_str or "#000000")
    c.setAlphaF(max(0.0, min(1.0, alpha)))
    return c


class PDFPageWidget(QWidget):
    """Single PDF page display with interactive annotation & vector text-edit overlays."""

    inPlaceEditorOpened = Signal(object, object)  # (page_widget, editor)
    inPlaceEditorClosed = Signal(object, object)  # (page_widget, editor)
    inPlaceEditCommitted = Signal(int, object)  # (page_idx, TextEditOp)
    annotationAdded = Signal(int, str, object)  # (page_idx, kind, item)
    annotationRemoved = Signal(int, str, object)
    itemSelected = Signal(object, object)  # (page_widget, item)
    pageInteracted = Signal(int)

    def __init__(
        self,
        engine: PDFDocumentEngine,
        page_idx: int,
        zoom: float = 1.0,
        rotation: int = 0,
        accent_color: str = "#ea2e3f",
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.engine = engine
        self.page_idx = page_idx
        self.zoom = zoom
        self.rotation = rotation
        self.accent_color = accent_color

        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)

        self._pixmap: QPixmap | None = None
        self._page_w_pt = 595.28
        self._page_h_pt = 841.89

        # Current tool state (synchronized by MainWindow)
        self.active_tool: str = "select"  # select, pan, edit_text, add_text, pen, highlighter, eraser, note, rect, ellipse, line, arrow, whiteout, redact, underline, strikethrough, highlight_sel
        self.tool_color: str = "#ef4444"
        self.tool_width: float = 3.0
        self.tool_opacity: float = 1.0
        self.tool_font: str = "Calibri"
        self.tool_font_size: int = 12

        # Search hits on this page
        self.search_hits: list[SearchHit] = []
        self.active_hit_idx: int = -1

        # Transient interaction state
        self._hover_block: TextBlock | None = None
        self._drawing_points: list[tuple[float, float]] = []
        self._shape_start: tuple[float, float] | None = None
        self._shape_cur: tuple[float, float] | None = None
        self._sel_start: tuple[float, float] | None = None
        self._sel_cur: tuple[float, float] | None = None
        self._selected_quads: list[tuple[float, float, float, float]] = []
        self._selected_text: str = ""

        # Selected overlay object (stamp, whiteout, redaction, floating textbox)
        self.selected_item: object | None = None
        self._drag_mode: str = ""  # "move" or "resize"
        self._drag_start_pt: tuple[float, float] = (0.0, 0.0)
        self._drag_orig_rect: tuple[float, float, float, float] = (0, 0, 0, 0)
        self._panning: bool = False
        self._pan_last_global: QPointF | None = None
        self._stamp_pixmap_cache: dict[tuple[str, int], QPixmap] = {}
        self._last_render_sig: tuple | None = None

        self.active_editor: InPlaceTextEditor | None = None
        self.refresh_render()

    def refresh_render(self) -> None:
        """Re-render PDF page pixmap from MuPDF onto crisp white paper at HiDPI resolution."""
        if self.page_idx >= self.engine.page_count:
            return
        dpr = max(1.0, float(self.devicePixelRatioF()))
        page_rev = self.engine.get_page_render_revision(self.page_idx)
        sig = (id(self.engine), page_rev, self.page_idx, round(self.zoom, 4), self.rotation % 360, round(dpr, 2))
        if self._pixmap is not None and self._last_render_sig == sig:
            if self.active_editor is not None:
                if shiboken6.isValid(self.active_editor):
                    self.active_editor.sync_zoom(self.zoom)
                else:
                    self.active_editor = None
            self.update()
            return

        w_pt, h_pt = self.engine.get_page_size(self.page_idx)
        self._page_w_pt, self._page_h_pt = w_pt, h_pt

        samples, px_w, px_h, stride = self.engine.render_page_rgb(
            self.page_idx, zoom=self.zoom * dpr, rotation=self.rotation
        )
        img = QImage(samples, px_w, px_h, stride, QImage.Format_RGB888).copy()
        img.setDevicePixelRatio(dpr)
        self._pixmap = QPixmap.fromImage(img)
        self._last_render_sig = sig

        disp_w = max(50, int(round(px_w / dpr)))
        disp_h = max(50, int(round(px_h / dpr)))
        self.setFixedSize(disp_w, disp_h)

        if self.active_editor is not None:
            if shiboken6.isValid(self.active_editor):
                self.active_editor.sync_zoom(self.zoom)
            else:
                self.active_editor = None
        self.update()

    def set_view_params(self, zoom: float, rotation: int) -> None:
        self.zoom = zoom
        self.rotation = rotation
        self.refresh_render()

    def set_tool_state(
        self,
        tool: str,
        color: str | None = None,
        width: float | None = None,
        opacity: float | None = None,
        font_family: str | None = None,
        font_size: int | None = None,
    ) -> None:
        self.active_tool = tool
        if color is not None:
            self.tool_color = color
        if width is not None:
            self.tool_width = width
        if opacity is not None:
            self.tool_opacity = opacity
        if font_family is not None:
            self.tool_font = font_family
        if font_size is not None:
            self.tool_font_size = font_size

        if tool != "edit_text":
            self._hover_block = None
        self._panning = False
        self._pan_last_global = None
        if tool == "pan":
            self.setCursor(Qt.OpenHandCursor)
        elif tool in ("edit_text", "select"):
            self.setCursor(Qt.IBeamCursor if tool == "select" else Qt.ArrowCursor)
        elif tool == "add_text":
            self.setCursor(Qt.IBeamCursor)
        elif tool in ("pen", "highlighter", "rect", "ellipse", "line", "arrow", "whiteout", "redact"):
            self.setCursor(Qt.CrossCursor)
        else:
            self.setCursor(Qt.ArrowCursor)
        self.update()

    def _find_scroll_area(self) -> QScrollArea | None:
        parent = self.parentWidget()
        while parent is not None:
            if isinstance(parent, QScrollArea):
                return parent
            parent = parent.parentWidget()
        return None

    def _to_pdf_pt(self, pos: QPointF) -> tuple[float, float]:
        wx, wy = float(pos.x()) / self.zoom, float(pos.y()) / self.zoom
        rot = self.rotation % 360
        if rot == 90:
            return wy, self._page_h_pt - wx
        if rot == 180:
            return self._page_w_pt - wx, self._page_h_pt - wy
        if rot == 270:
            return self._page_w_pt - wy, wx
        return wx, wy

    def _to_widget_rect(self, r: tuple[float, float, float, float]) -> QRectF:
        return QRectF(
            r[0] * self.zoom,
            r[1] * self.zoom,
            (r[2] - r[0]) * self.zoom,
            (r[3] - r[1]) * self.zoom,
        )

    def _hit_overlay_item(self, px: float, py: float) -> tuple[object | None, str]:
        """Return (item, handle_mode) if (px, py) hits a stamp, textbox, whiteout, or redaction."""
        candidates = (
            list(self.engine.stamps.get(self.page_idx, []))
            + list(self.engine.text_boxes.get(self.page_idx, []))
            + list(self.engine.whiteouts.get(self.page_idx, []))
            + list(self.engine.redactions.get(self.page_idx, []))
        )
        for item in reversed(candidates):
            x0, y0, x1, y1 = item.rect
            if abs(px - x1) <= 10 / self.zoom and abs(py - y1) <= 10 / self.zoom:
                return item, "resize"
            if x0 <= px <= x1 and y0 <= py <= y1:
                return item, "move"
        return None, ""

    def open_editor_for_block(self, blk: TextBlock, is_new: bool = False) -> InPlaceTextEditor:
        """Open a native QTextEdit directly over blk."""
        if self.active_editor is not None:
            self.commit_active_editor()

        display_blk = TextBlock(
            block_id=blk.block_id,
            page_idx=blk.page_idx,
            rect=blk.rect,
            plain_text=blk.full_edit_text or blk.plain_text,
            html=blk.full_edit_html or blk.html,
            font_family=blk.font_family,
            font_size=blk.font_size,
            is_bold=blk.is_bold,
            is_italic=blk.is_italic,
            color_hex=blk.color_hex,
            line_height_ratio=blk.line_height_ratio,
            base_page_idx=blk.base_page_idx,
            base_block_id=blk.base_block_id,
            base_rect=blk.base_rect,
        )
        effective_is_new = is_new or getattr(blk, "is_new_block", False) or blk.block_id.startswith("new_")
        editor = InPlaceTextEditor(
            block=display_blk,
            zoom=self.zoom,
            accent_color=self.accent_color,
            is_new_block=effective_is_new,
            reflow_enabled=True,
            parent=self,
        )
        x0, y0, x1, y1 = blk.rect
        editor.setGeometry(
            int(x0 * self.zoom) - 2,
            int(y0 * self.zoom) - 2,
            max(80, int((x1 - x0) * self.zoom) + 8),
            max(28, int((y1 - y0) * self.zoom) + 6),
        )
        editor.commitRequested.connect(lambda _ed: self.commit_active_editor())
        editor.revertRequested.connect(lambda _ed: self.cancel_active_editor())
        editor.show()
        editor.setFocus()
        self.active_editor = editor
        self.inPlaceEditorOpened.emit(self, editor)
        return editor

    def commit_active_editor(self) -> TextEditOp | None:
        ed = self.active_editor
        if ed is None:
            return None
        self.active_editor = None
        if not shiboken6.isValid(ed):
            self.inPlaceEditorClosed.emit(self, ed)
            return None

        blk = ed.pdf_block
        new_text = ed.toPlainText().strip()
        raw_unscaled = ed.export_unscaled_html()
        clean_html = qt_html_to_clean_html(
            raw_unscaled,
            default_font=blk.font_family,
            default_size_pt=blk.font_size,
            default_color=blk.color_hex,
            line_height_ratio=blk.line_height_ratio,
        )
        reflow_flag = ed.reflow_enabled
        is_new = ed.is_new_block
        ed.hide()
        ed.deleteLater()
        self.inPlaceEditorClosed.emit(self, ed)

        target_page_idx = blk.base_page_idx if blk.base_page_idx >= 0 else self.page_idx
        target_block_id = blk.base_block_id or blk.block_id
        target_base_rect = blk.base_rect or blk.rect
        already_committed = target_block_id in self.engine.in_place_edits.get(target_page_idx, {})

        # Skip empty newly created blocks that were never committed
        if is_new and not new_text and not already_committed:
            return None

        # Skip committing unchanged blocks (including already-committed added blocks)
        if (not is_new or already_committed) and new_text == blk.plain_text.strip() and clean_html == blk.html:
            return None

        op = TextEditOp(
            edit_id=uuid.uuid4().hex[:10],
            page_idx=target_page_idx,
            orig_rect=target_base_rect,
            target_rect=target_base_rect,
            original_text=blk.plain_text,
            original_html=blk.html,
            new_text=new_text,
            new_html=clean_html,
            font_family=blk.font_family,
            font_size=blk.font_size,
            color_hex=blk.color_hex,
            line_height_ratio=blk.line_height_ratio,
            reflow_enabled=reflow_flag,
            is_new_block=is_new,
            block_id=target_block_id,
        )
        self.inPlaceEditCommitted.emit(target_page_idx, op)
        return op

    def cancel_active_editor(self) -> None:
        ed = self.active_editor
        if ed is None:
            return
        self.active_editor = None
        if shiboken6.isValid(ed):
            ed.hide()
            ed.deleteLater()
        self.inPlaceEditorClosed.emit(self, ed)

    def clear_text_selection(self) -> None:
        self._selected_quads = []
        self._selected_text = ""
        self.update()

    def get_selected_text(self) -> str:
        return self._selected_text

    def get_selected_quads(self) -> list[tuple[float, float, float, float]]:
        return list(self._selected_quads)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        self.pageInteracted.emit(self.page_idx)
        if not shiboken6.isValid(self) or self.parent() is None:
            return
        if event.button() != Qt.LeftButton:
            return super().mousePressEvent(event)

        px, py = self._to_pdf_pt(event.position())

        # Clicking outside active editor commits it first
        if self.active_editor is not None:
            self.commit_active_editor()
            if not shiboken6.isValid(self) or self.parent() is None:
                return

        if self.active_tool == "pan":
            self._panning = True
            self._pan_last_global = event.globalPosition()
            self.setCursor(Qt.ClosedHandCursor)
            event.accept()
            return

        if self.active_tool in ("edit_text", "add_text") and (self.rotation % 360) != 0:
            win = self.window()
            if hasattr(win, "statusBar") and win.statusBar():
                win.statusBar().showMessage("Reset view rotation to 0° to edit or add text in-place")
            return

        # Check overlay item drag/resize in interactive modes
        if self.active_tool in ("select", "edit_text", "move", "stamp", "whiteout", "redact"):
            hit, mode = self._hit_overlay_item(px, py)
            if hit is not None:
                self.selected_item = hit
                self.itemSelected.emit(self, hit)
                if not getattr(hit, "locked", False):
                    self._drag_mode = mode
                    self._drag_start_pt = (px, py)
                    self._drag_orig_rect = hit.rect
                self.update()
                return
            elif self.selected_item is not None:
                self.selected_item = None
                self.itemSelected.emit(self, None)
                self.update()

        if self.active_tool == "edit_text":
            blk = self.engine.find_block_at(self.page_idx, px, py)
            if blk is not None:
                self.open_editor_for_block(blk, is_new=False)
                return

        if self.active_tool == "add_text":
            new_id = f"new_{uuid.uuid4().hex[:8]}"
            w_pt = min(240.0, max(120.0, self._page_w_pt - px - 24.0))
            blk = TextBlock(
                block_id=new_id,
                page_idx=self.page_idx,
                rect=(px, py, px + w_pt, py + 28.0),
                plain_text="",
                html=f'<div style="font-family:{self.tool_font};font-size:{self.tool_font_size}pt;color:{self.tool_color};"><p>Type text here</p></div>',
                font_family=self.tool_font,
                font_size=float(self.tool_font_size),
                is_bold=False,
                is_italic=False,
                color_hex=self.tool_color,
            )
            ed = self.open_editor_for_block(blk, is_new=True)
            ed.selectAll()
            return

        if self.active_tool in ("pen", "highlighter"):
            self._drawing_points = [(px, py)]
            self.update()
            return

        if self.active_tool in ("rect", "ellipse", "line", "arrow", "whiteout", "redact"):
            self._shape_start = (px, py)
            self._shape_cur = (px, py)
            self.update()
            return

        if self.active_tool == "note":
            txt, ok = QInputDialog.getMultiLineText(self, "Sticky Note", "Comment text:")
            if ok and txt.strip():
                note = StickyNote(page_idx=self.page_idx, x=px, y=py, text=txt.strip())
                self.engine.sticky_notes.setdefault(self.page_idx, []).append(note)
                self.annotationAdded.emit(self.page_idx, "note", note)
                self.update()
            return

        if self.active_tool == "eraser":
            self._erase_at(px, py)
            return

        if self.active_tool in ("select", "underline", "strikethrough", "highlight_sel"):
            self._sel_start = (px, py)
            self._sel_cur = (px, py)
            self._selected_quads = []
            self._selected_text = ""
            self.update()
            return

        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._panning and self._pan_last_global is not None:
            cur_g = event.globalPosition()
            dx = int(round(cur_g.x() - self._pan_last_global.x()))
            dy = int(round(cur_g.y() - self._pan_last_global.y()))
            self._pan_last_global = cur_g
            sa = self._find_scroll_area()
            if sa is not None:
                sa.horizontalScrollBar().setValue(sa.horizontalScrollBar().value() - dx)
                sa.verticalScrollBar().setValue(sa.verticalScrollBar().value() - dy)
            event.accept()
            return

        px, py = self._to_pdf_pt(event.position())

        if self._drag_mode and self.selected_item is not None:
            dx = px - self._drag_start_pt[0]
            dy = py - self._drag_start_pt[1]
            ox0, oy0, ox1, oy1 = self._drag_orig_rect
            if self._drag_mode == "move":
                w, h = ox1 - ox0, oy1 - oy0
                nx0 = max(0.0, min(self._page_w_pt - w, ox0 + dx))
                ny0 = max(0.0, min(self._page_h_pt - h, oy0 + dy))
                self.selected_item.rect = (nx0, ny0, nx0 + w, ny0 + h)
            elif self._drag_mode == "resize":
                nx1 = max(ox0 + 20.0, min(self._page_w_pt, ox1 + dx))
                ny1 = max(oy0 + 16.0, min(self._page_h_pt, oy1 + dy))
                self.selected_item.rect = (ox0, oy0, nx1, ny1)
            self.update()
            return

        if self._drawing_points:
            self._drawing_points.append((px, py))
            self.update()
            return

        if self._shape_start is not None:
            self._shape_cur = (px, py)
            self.update()
            return

        if self._sel_start is not None:
            self._sel_cur = (px, py)
            self._update_text_selection()
            self.update()
            return

        if self.active_tool == "eraser" and (event.buttons() & Qt.LeftButton):
            self._erase_at(px, py)
            return

        if self.active_tool == "edit_text" and (self.rotation % 360) == 0:
            blk = self.engine.find_block_at(self.page_idx, px, py)
            if blk != self._hover_block:
                self._hover_block = blk
                self.setCursor(Qt.IBeamCursor if blk else Qt.ArrowCursor)
                self.update()
            return

        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._panning:
            self._panning = False
            self._pan_last_global = None
            self.setCursor(Qt.OpenHandCursor if self.active_tool == "pan" else Qt.ArrowCursor)
            event.accept()
            return

        if self._drag_mode:
            self._drag_mode = ""
            self.update()
            return

        if self._drawing_points:
            if len(self._drawing_points) >= 2:
                is_hl = self.active_tool == "highlighter"
                stroke = InkStroke(
                    page_idx=self.page_idx,
                    points=list(self._drawing_points),
                    color_hex=self.tool_color,
                    stroke_width=self.tool_width,
                    opacity=self.tool_opacity if is_hl else 1.0,
                    is_highlighter=is_hl,
                )
                self.engine.ink_strokes.setdefault(self.page_idx, []).append(stroke)
                self.annotationAdded.emit(self.page_idx, "ink", stroke)
            self._drawing_points = []
            self.update()
            return

        if self._shape_start is not None and self._shape_cur is not None:
            sx, sy = self._shape_start
            ex, ey = self._shape_cur
            self._shape_start = None
            self._shape_cur = None
            if abs(ex - sx) >= 4 or abs(ey - sy) >= 4:
                norm_rect = (min(sx, ex), min(sy, ey), max(sx, ex), max(sy, ey))
                if self.active_tool == "whiteout":
                    box = WhiteoutBox(page_idx=self.page_idx, rect=norm_rect)
                    self.engine.whiteouts.setdefault(self.page_idx, []).append(box)
                    self.selected_item = box
                    self.annotationAdded.emit(self.page_idx, "whiteout", box)
                elif self.active_tool == "redact":
                    box = RedactionBox(page_idx=self.page_idx, rect=norm_rect)
                    self.engine.redactions.setdefault(self.page_idx, []).append(box)
                    self.selected_item = box
                    self.annotationAdded.emit(self.page_idx, "redact", box)
                else:
                    rect = (sx, sy, ex, ey) if self.active_tool in ("line", "arrow") else norm_rect
                    shape = ShapeItem(
                        page_idx=self.page_idx,
                        shape_type=self.active_tool,
                        rect=rect,
                        color_hex=self.tool_color,
                        stroke_width=self.tool_width,
                        opacity=self.tool_opacity,
                    )
                    self.engine.shapes.setdefault(self.page_idx, []).append(shape)
                    self.annotationAdded.emit(self.page_idx, "shape", shape)
            self.update()
            return

        if self._sel_start is not None:
            self._sel_cur = self._to_pdf_pt(event.position())
            self._update_text_selection()
            self._sel_start = None
            self._sel_cur = None
            if self._selected_quads and self.active_tool in ("underline", "strikethrough", "highlight_sel"):
                mtype = "highlight" if self.active_tool == "highlight_sel" else self.active_tool
                mk = TextMarkup(
                    page_idx=self.page_idx,
                    markup_type=mtype,
                    rects=list(self._selected_quads),
                    color_hex=self.tool_color,
                    opacity=0.35 if mtype == "highlight" else 1.0,
                )
                self.engine.markups.setdefault(self.page_idx, []).append(mk)
                self.annotationAdded.emit(self.page_idx, "markup", mk)
                self._selected_quads = []
                self._selected_text = ""
            self.update()
            return

        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        px, py = self._to_pdf_pt(event.position())
        # Check sticky notes
        for note in self.engine.sticky_notes.get(self.page_idx, []):
            if abs(px - note.x) <= 14 and abs(py - note.y) <= 14:
                txt, ok = QInputDialog.getMultiLineText(self, "Edit Sticky Note", "Comment:", note.text)
                if ok:
                    note.text = txt.strip() or note.text
                    self.update()
                return
        # Check removable overlays
        hit, _ = self._hit_overlay_item(px, py)
        if hit is not None and not getattr(hit, "locked", False):
            self._remove_overlay_item(hit)
            return
        super().mouseDoubleClickEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key_Delete, Qt.Key_Backspace) and self.selected_item is not None:
            if not getattr(self.selected_item, "locked", False):
                self._remove_overlay_item(self.selected_item)
                return
        super().keyPressEvent(event)

    def _remove_overlay_item(self, item: object) -> None:
        for kind, store in [
            ("stamp", self.engine.stamps),
            ("textbox", self.engine.text_boxes),
            ("whiteout", self.engine.whiteouts),
            ("redact", self.engine.redactions),
        ]:
            lst = store.get(self.page_idx, [])
            if item in lst:
                lst.remove(item)
                if self.selected_item is item:
                    self.selected_item = None
                self.annotationRemoved.emit(self.page_idx, kind, item)
                self.update()
                return

    def _erase_at(self, px: float, py: float) -> None:
        tol = 10.0
        strokes = self.engine.ink_strokes.get(self.page_idx, [])
        for st in list(strokes):
            if any(math.hypot(x - px, y - py) <= tol + st.stroke_width for x, y in st.points):
                strokes.remove(st)
                self.annotationRemoved.emit(self.page_idx, "ink", st)
                self.update()
                return
        shapes = self.engine.shapes.get(self.page_idx, [])
        for sh in list(shapes):
            x0, y0, x1, y1 = min(sh.rect[0], sh.rect[2]), min(sh.rect[1], sh.rect[3]), max(sh.rect[0], sh.rect[2]), max(sh.rect[1], sh.rect[3])
            if (x0 - tol) <= px <= (x1 + tol) and (y0 - tol) <= py <= (y1 + tol):
                shapes.remove(sh)
                self.annotationRemoved.emit(self.page_idx, "shape", sh)
                self.update()
                return
        markups = self.engine.markups.get(self.page_idx, [])
        for mk in list(markups):
            if any((r[0] - tol) <= px <= (r[2] + tol) and (r[1] - tol) <= py <= (r[3] + tol) for r in mk.rects):
                markups.remove(mk)
                self.annotationRemoved.emit(self.page_idx, "markup", mk)
                self.update()
                return
        notes = self.engine.sticky_notes.get(self.page_idx, [])
        for nt in list(notes):
            if math.hypot(nt.x - px, nt.y - py) <= 16:
                notes.remove(nt)
                self.annotationRemoved.emit(self.page_idx, "note", nt)
                self.update()
                return
        hit, _ = self._hit_overlay_item(px, py)
        if hit is not None and not getattr(hit, "locked", False):
            self._remove_overlay_item(hit)

    def _update_text_selection(self) -> None:
        if self._sel_start is None or self._sel_cur is None:
            return
        doc = self.engine._get_preview_doc()
        if self.page_idx >= len(doc):
            return
        page = doc[self.page_idx]
        x0, y0 = self._sel_start
        x1, y1 = self._sel_cur
        sel_rect = pymupdf.Rect(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
        if sel_rect.width < 2 and sel_rect.height < 2:
            self._selected_quads = []
            self._selected_text = ""
            return
        words = page.get_text("words")
        matched = [w for w in words if pymupdf.Rect(w[:4]).intersects(sel_rect)]
        matched.sort(key=lambda w: (round(w[1] / 6.0), w[0]))
        self._selected_quads = [(float(w[0]), float(w[1]), float(w[2]), float(w[3])) for w in matched]
        self._selected_text = heal_pdf_text(" ".join(w[4] for w in matched))

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)

        # Always paint uniform solid white paper before drawing PDF content
        painter.fillRect(self.rect(), QColor("#ffffff"))
        if self._pixmap is not None:
            painter.drawPixmap(0, 0, self._pixmap)
        painter.setPen(QPen(QColor("#cbd5e1"), 1))
        painter.drawRect(0, 0, self.width() - 1, self.height() - 1)

        rot = self.rotation % 360
        if rot == 90:
            painter.translate(self.width(), 0)
            painter.rotate(90)
        elif rot == 180:
            painter.translate(self.width(), self.height())
            painter.rotate(180)
        elif rot == 270:
            painter.translate(0, self.height())
            painter.rotate(270)

        # 1. Search highlights
        for idx, hit in enumerate(self.search_hits):
            col = QColor("#f97316") if idx == self.active_hit_idx else QColor("#facc15")
            col.setAlphaF(0.45)
            painter.fillRect(self._to_widget_rect(hit.rect), col)

        # 2. Text selection highlight
        for qr in self._selected_quads:
            col = QColor("#3b82f6")
            col.setAlphaF(0.28)
            painter.fillRect(self._to_widget_rect(qr), col)

        # 3. Hover frame for editable text blocks
        if self._hover_block is not None and self.active_tool == "edit_text":
            pen = QPen(_qcolor(self.accent_color, 0.85), 1.5, Qt.DashLine)
            painter.setPen(pen)
            painter.drawRect(self._to_widget_rect(self._hover_block.rect))

        # 4. Text markups (highlight / underline / strikethrough)
        for mk in self.engine.markups.get(self.page_idx, []):
            for r in mk.rects:
                wr = self._to_widget_rect(r)
                if mk.markup_type == "highlight":
                    painter.fillRect(wr, _qcolor(mk.color_hex, mk.opacity))
                elif mk.markup_type == "underline":
                    painter.setPen(QPen(_qcolor(mk.color_hex), 2.0))
                    painter.drawLine(wr.bottomLeft(), wr.bottomRight())
                elif mk.markup_type == "strikethrough":
                    painter.setPen(QPen(_qcolor(mk.color_hex), 2.0))
                    ym = (wr.top() + wr.bottom()) / 2.0
                    painter.drawLine(QPointF(wr.left(), ym), QPointF(wr.right(), ym))

        # 5. Whiteout & redaction boxes
        for wb in self.engine.whiteouts.get(self.page_idx, []):
            wr = self._to_widget_rect(wb.rect)
            painter.fillRect(wr, QColor("#ffffff"))
            painter.setPen(QPen(QColor("#94a3b8"), 1, Qt.DotLine))
            painter.drawRect(wr)
        for rb in self.engine.redactions.get(self.page_idx, []):
            wr = self._to_widget_rect(rb.rect)
            painter.fillRect(wr, QColor("#000000"))

        # 6. Ink & highlighter strokes
        for st in self.engine.ink_strokes.get(self.page_idx, []):
            self._paint_ink(painter, st.points, st.color_hex, st.stroke_width, st.opacity)
        if self._drawing_points:
            op = self.tool_opacity if self.active_tool == "highlighter" else 1.0
            self._paint_ink(painter, self._drawing_points, self.tool_color, self.tool_width, op)

        # 7. Geometric shapes
        for sh in self.engine.shapes.get(self.page_idx, []):
            self._paint_shape(painter, sh.shape_type, sh.rect, sh.color_hex, sh.stroke_width, sh.opacity)
        if self._shape_start and self._shape_cur:
            sx, sy = self._shape_start
            ex, ey = self._shape_cur
            if self.active_tool == "whiteout":
                painter.fillRect(self._to_widget_rect((min(sx, ex), min(sy, ey), max(sx, ex), max(sy, ey))), QColor(255, 255, 255, 220))
            elif self.active_tool == "redact":
                painter.fillRect(self._to_widget_rect((min(sx, ex), min(sy, ey), max(sx, ex), max(sy, ey))), QColor(0, 0, 0, 210))
            else:
                r = (sx, sy, ex, ey) if self.active_tool in ("line", "arrow") else (min(sx, ex), min(sy, ey), max(sx, ex), max(sy, ey))
                self._paint_shape(painter, self.active_tool, r, self.tool_color, self.tool_width, self.tool_opacity)

        # 8. Stamps, signatures, and inserted images
        for stp in self.engine.stamps.get(self.page_idx, []):
            wr = self._to_widget_rect(stp.rect)
            painter.save()
            painter.setOpacity(stp.opacity)
            painter.translate(wr.center())
            painter.rotate(stp.rotation)
            local_rect = QRectF(-wr.width() / 2.0, -wr.height() / 2.0, wr.width(), wr.height())
            if stp.kind == "image" and stp.image_bytes:
                pm = self._get_stamp_pixmap(stp)
                if pm is not None:
                    painter.drawPixmap(local_rect, pm, QRectF(pm.rect()))
            elif stp.kind == "stamp":
                pen = QPen(_qcolor(stp.color_hex), 2.5)
                painter.setPen(pen)
                painter.drawRect(local_rect)
                f = QFont("Arial", max(8, int(stp.font_size * self.zoom)), QFont.Bold)
                painter.setFont(f)
                painter.drawText(local_rect, Qt.AlignCenter, stp.text)
            painter.restore()

        # 9. Sticky note markers
        for nt in self.engine.sticky_notes.get(self.page_idx, []):
            cx, cy = nt.x * self.zoom, nt.y * self.zoom
            marker = QRectF(cx - 10, cy - 10, 20, 20)
            painter.setBrush(QColor("#facc15"))
            painter.setPen(QPen(QColor("#854d0e"), 1.5))
            painter.drawRoundedRect(marker, 4, 4)
            painter.drawText(marker, Qt.AlignCenter, "💬")

        # 10. Selection handles on selected overlay item
        if self.selected_item is not None and hasattr(self.selected_item, "rect"):
            wr = self._to_widget_rect(self.selected_item.rect)
            painter.setPen(QPen(QColor("#2563eb"), 1.5, Qt.DashLine))
            painter.drawRect(wr)
            grip = QRectF(wr.right() - 5, wr.bottom() - 5, 10, 10)
            painter.fillRect(grip, QColor("#2563eb"))

        painter.end()

    def _get_stamp_pixmap(self, stp: StampItem) -> QPixmap | None:
        if not stp.image_bytes:
            return None
        key = (stp.item_id, len(stp.image_bytes))
        pm = self._stamp_pixmap_cache.get(key)
        if pm is not None:
            return pm if not pm.isNull() else None
        pm = QPixmap()
        pm.loadFromData(stp.image_bytes)
        if len(self._stamp_pixmap_cache) >= 32:
            self._stamp_pixmap_cache.clear()
        self._stamp_pixmap_cache[key] = pm
        return pm if not pm.isNull() else None

    def _paint_ink(
        self,
        painter: QPainter,
        pts: list[tuple[float, float]],
        color_hex: str,
        width: float,
        opacity: float,
    ) -> None:
        if len(pts) < 2:
            return
        path = QPainterPath(QPointF(pts[0][0] * self.zoom, pts[0][1] * self.zoom))
        for x, y in pts[1:]:
            path.lineTo(x * self.zoom, y * self.zoom)
        pen = QPen(_qcolor(color_hex, opacity), max(1.0, width * self.zoom), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(path)

    def _paint_shape(
        self,
        painter: QPainter,
        shape_type: str,
        rect: tuple[float, float, float, float],
        color_hex: str,
        width: float,
        opacity: float,
    ) -> None:
        pen = QPen(_qcolor(color_hex, opacity), max(1.0, width * self.zoom))
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        wr = self._to_widget_rect(rect)
        if shape_type == "rect":
            painter.drawRect(wr)
        elif shape_type == "ellipse":
            painter.drawEllipse(wr)
        elif shape_type in ("line", "arrow"):
            p0 = QPointF(rect[0] * self.zoom, rect[1] * self.zoom)
            p1 = QPointF(rect[2] * self.zoom, rect[3] * self.zoom)
            painter.drawLine(p0, p1)
            if shape_type == "arrow":
                ang = math.atan2(p1.y() - p0.y(), p1.x() - p0.x())
                head = max(8.0, width * self.zoom * 3.0)
                for sign in (-1, 1):
                    painter.drawLine(
                        p1,
                        QPointF(p1.x() - head * math.cos(ang + sign * 0.45), p1.y() - head * math.sin(ang + sign * 0.45)),
                    )
