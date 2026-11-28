"""Collapsible left sidebar: Thumbnails, Bookmarks/Outline, and Annotations list."""

from __future__ import annotations

from PySide6.QtCore import QPoint, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from app.core.pdf_engine import PDFDocumentEngine


class SidebarPanel(QFrame):
    """Left panel housing Thumbnails, Bookmarks/Outline, and Annotations."""

    pageSelected = Signal(int)
    bookmarkAdded = Signal(str, int)
    bookmarkRemoved = Signal(str, int)
    bookmarkRenamed = Signal(str, str, int)
    pageActionRequested = Signal(str, int)
    annotationJumpRequested = Signal(int, object)
    annotationDeleteRequested = Signal(int, str, object)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("Sidebar")
        self.setFixedWidth(235)
        self._engine: PDFDocumentEngine | None = None
        self._current_page = 0
        self._thumbs_dirty = False
        self._rendered_thumbs: set[int] = set()
        self._placeholder_icon = self._make_placeholder_icon()
        self._thumb_timer = QTimer(self)
        self._thumb_timer.setInterval(10)
        self._thumb_timer.timeout.connect(self._render_next_thumb_batch)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        self.tabs = QTabWidget()
        self.tabs.currentChanged.connect(self._on_sidebar_tab_changed)
        layout.addWidget(self.tabs)

        # 1. Thumbnails
        self.thumb_list = QListWidget()
        self.thumb_list.setIconSize(QSize(136, 180))
        self.thumb_list.setSpacing(4)
        self.thumb_list.itemClicked.connect(self._on_thumb_clicked)
        self.thumb_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.thumb_list.customContextMenuRequested.connect(self._on_thumb_context_menu)
        self.thumb_list.verticalScrollBar().valueChanged.connect(self._render_visible_thumbnails)
        self.tabs.addTab(self.thumb_list, "Pages")

        # 2. Bookmarks / Outline
        outline_wrap = QWidget()
        ov = QVBoxLayout(outline_wrap)
        ov.setContentsMargins(2, 2, 2, 2)
        oh = QHBoxLayout()
        self.btn_add_bookmark = QPushButton("+ Add Bookmark")
        self.btn_add_bookmark.clicked.connect(self._on_add_bookmark)
        oh.addWidget(self.btn_add_bookmark)
        ov.addLayout(oh)
        self.outline_list = QListWidget()
        self.outline_list.itemClicked.connect(self._on_outline_clicked)
        self.outline_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.outline_list.customContextMenuRequested.connect(self._on_outline_context_menu)
        ov.addWidget(self.outline_list)
        self.tabs.addTab(outline_wrap, "Outline")

        # 3. Annotations summary
        annot_wrap = QWidget()
        av = QVBoxLayout(annot_wrap)
        av.setContentsMargins(2, 2, 2, 2)
        self.btn_refresh_annots = QPushButton("⟳ Refresh List")
        self.btn_refresh_annots.clicked.connect(self.refresh_annotations)
        av.addWidget(self.btn_refresh_annots)
        self.annot_list = QListWidget()
        self.annot_list.itemClicked.connect(self._on_annot_clicked)
        self.annot_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.annot_list.customContextMenuRequested.connect(self._on_annot_context_menu)
        av.addWidget(self.annot_list)
        self.tabs.addTab(annot_wrap, "Notes")

    @staticmethod
    def _make_placeholder_icon() -> QIcon:
        pm = QPixmap(110, 146)
        pm.fill(QColor("#ffffff"))
        p = QPainter(pm)
        p.setPen(QPen(QColor("#cbd5e1"), 1))
        p.drawRect(0, 0, pm.width() - 1, pm.height() - 1)
        p.end()
        return QIcon(pm)

    def set_engine(self, engine: PDFDocumentEngine | None, current_page: int = 0) -> None:
        self._engine = engine
        self._current_page = current_page
        self.refresh_all()

    def set_current_page(self, page_idx: int) -> None:
        self._current_page = page_idx
        if 0 <= page_idx < self.thumb_list.count():
            self.thumb_list.setCurrentRow(page_idx)
            if self.isVisible() and self.tabs.currentIndex() == 0:
                self.thumb_list.scrollToItem(self.thumb_list.item(page_idx))
                self._render_visible_thumbnails()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self._thumbs_dirty and self.tabs.currentIndex() == 0:
            self.refresh_thumbnails(force=True)
        elif self.tabs.currentIndex() == 0 and self._engine is not None:
            self._render_visible_thumbnails()

    def _on_sidebar_tab_changed(self, idx: int) -> None:
        if idx == 0 and self.isVisible():
            if self._thumbs_dirty:
                self.refresh_thumbnails(force=True)
            else:
                self._render_visible_thumbnails()

    def refresh_all(self) -> None:
        self.refresh_thumbnails()
        self.refresh_outline()
        self.refresh_annotations()

    def refresh_thumbnails(self, force: bool = False) -> None:
        self._thumb_timer.stop()
        if self._engine is None or self._engine.needs_password:
            self.thumb_list.clear()
            self._rendered_thumbs.clear()
            self._thumbs_dirty = False
            return
        if not force and (not self.isVisible() or self.tabs.currentIndex() != 0):
            self._thumbs_dirty = True
            return
        self._thumbs_dirty = False
        self._rendered_thumbs.clear()
        self.thumb_list.clear()
        for pno in range(self._engine.page_count):
            item = QListWidgetItem(self._placeholder_icon, f"Page {pno + 1}")
            item.setData(Qt.UserRole, pno)
            item.setTextAlignment(Qt.AlignHCenter)
            self.thumb_list.addItem(item)
        self.set_current_page(self._current_page)
        self._render_visible_thumbnails()
        if len(self._rendered_thumbs) < self._engine.page_count:
            self._thumb_timer.start()

    def _render_thumb_for_page(self, pno: int) -> None:
        if self._engine is None or pno in self._rendered_thumbs:
            return
        if not (0 <= pno < self.thumb_list.count()):
            return
        samples, w, h, stride = self._engine.render_thumbnail_rgb(pno, max_width=132)
        qimg = QImage(samples, w, h, stride, QImage.Format_RGB888).copy()
        self.thumb_list.item(pno).setIcon(QIcon(QPixmap.fromImage(qimg)))
        self._rendered_thumbs.add(pno)

    def _render_visible_thumbnails(self) -> None:
        if self._engine is None or not self.isVisible() or self.tabs.currentIndex() != 0:
            return
        count = self.thumb_list.count()
        if count == 0:
            return
        vp = self.thumb_list.viewport().rect()
        top_item = self.thumb_list.itemAt(vp.topLeft() + QPoint(4, 4))
        bot_item = self.thumb_list.itemAt(vp.bottomLeft() + QPoint(4, -4))
        start = self.thumb_list.row(top_item) if top_item else max(0, self._current_page - 2)
        end = (self.thumb_list.row(bot_item) + 2) if bot_item else min(count - 1, start + 6)
        start = max(0, start - 1)
        end = min(count - 1, end)
        for pno in range(start, end + 1):
            self._render_thumb_for_page(pno)

    def _render_next_thumb_batch(self) -> None:
        if self._engine is None or self._engine.needs_password or self._thumbs_dirty:
            self._thumb_timer.stop()
            return
        if not self.isVisible() or self.tabs.currentIndex() != 0:
            return
        rendered = 0
        for pno in range(self.thumb_list.count()):
            if pno not in self._rendered_thumbs:
                self._render_thumb_for_page(pno)
                rendered += 1
                if rendered >= 4:
                    break
        if len(self._rendered_thumbs) >= self.thumb_list.count():
            self._thumb_timer.stop()

    def refresh_outline(self) -> None:
        self.outline_list.clear()
        if self._engine is None or self._engine.needs_password:
            return
        for idx, (title, pno) in enumerate(self._engine.custom_bookmarks):
            item = QListWidgetItem(f"🔖 {title} (p.{pno + 1})")
            item.setData(Qt.UserRole, pno)
            item.setData(Qt.UserRole + 1, idx)
            self.outline_list.addItem(item)
        for lvl, title, pno in self._engine.get_toc():
            indent = "  " * max(0, lvl - 1)
            item = QListWidgetItem(f"{indent}{title} (p.{pno + 1})")
            item.setData(Qt.UserRole, pno)
            self.outline_list.addItem(item)
        if self.outline_list.count() == 0:
            empty = QListWidgetItem("No bookmarks or outline")
            empty.setFlags(Qt.NoItemFlags)
            self.outline_list.addItem(empty)

    @staticmethod
    def _rect_for_payload(kind: str, item: object) -> tuple[float, float, float, float] | None:
        if kind == "in_place_edit":
            return getattr(item, "target_rect", None) or getattr(item, "orig_rect", None)
        if kind == "note":
            return (item.x - 16.0, item.y - 16.0, item.x + 16.0, item.y + 16.0)
        if kind == "ink" and getattr(item, "points", None):
            xs = [p[0] for p in item.points]
            ys = [p[1] for p in item.points]
            return (min(xs), min(ys), max(xs), max(ys))
        if kind == "markup" and getattr(item, "rects", None):
            return (
                min(r[0] for r in item.rects),
                min(r[1] for r in item.rects),
                max(r[2] for r in item.rects),
                max(r[3] for r in item.rects),
            )
        return getattr(item, "rect", None)

    def _add_annot_item(self, label: str, pno: int, kind: str = "", payload: object = None, rect=None) -> None:
        item = QListWidgetItem(label)
        item.setData(Qt.UserRole, pno)
        item.setData(Qt.UserRole + 1, {"kind": kind, "payload": payload, "rect": rect})
        self.annot_list.addItem(item)

    def refresh_annotations(self) -> None:
        self.annot_list.clear()
        if self._engine is None:
            return
        for pno, ops in sorted(self._engine.in_place_edits.items()):
            for op in ops.values():
                self._add_annot_item(
                    f"✏️ Text Edit p.{pno + 1}: {op.new_text[:32]}",
                    pno,
                    "in_place_edit",
                    op,
                    self._rect_for_payload("in_place_edit", op),
                )
        for pno, notes in sorted(self._engine.sticky_notes.items()):
            for nt in notes:
                self._add_annot_item(
                    f"💬 Note p.{pno + 1}: {nt.text[:32]}",
                    pno,
                    "note",
                    nt,
                    self._rect_for_payload("note", nt),
                )
        for pno, stps in sorted(self._engine.stamps.items()):
            for stp in stps:
                if stp.kind == "watermark":
                    lbl = f"Watermark: {stp.text}"
                    prefix = "💧"
                elif stp.kind == "stamp":
                    lbl = stp.text
                    prefix = "📌"
                else:
                    lbl = "Signature/Image"
                    prefix = "📌"
                self._add_annot_item(f"{prefix} {lbl} (p.{pno + 1})", pno, "stamp", stp, stp.rect)
        for pno, tbs in sorted(self._engine.text_boxes.items()):
            for tb in tbs:
                self._add_annot_item(
                    f"📝 Text Box p.{pno + 1}: {tb.plain_text[:28]}",
                    pno,
                    "textbox",
                    tb,
                    tb.rect,
                )
        for pno, strokes in sorted(self._engine.ink_strokes.items()):
            if strokes:
                rect = self._rect_for_payload("ink", strokes[0])
                self._add_annot_item(
                    f"🖍️ {len(strokes)} ink/highlight stroke(s) (p.{pno + 1})",
                    pno,
                    "ink_group",
                    strokes,
                    rect,
                )
        for pno, shapes in sorted(self._engine.shapes.items()):
            for sh in shapes:
                self._add_annot_item(
                    f"📐 {sh.shape_type.title()} (p.{pno + 1})",
                    pno,
                    "shape",
                    sh,
                    self._rect_for_payload("shape", sh),
                )
        for pno, markups in sorted(self._engine.markups.items()):
            for mk in markups:
                self._add_annot_item(
                    f"🔖 {mk.markup_type.title()} markup (p.{pno + 1})",
                    pno,
                    "markup",
                    mk,
                    self._rect_for_payload("markup", mk),
                )
        for pno, wbs in sorted(self._engine.whiteouts.items()):
            for wb in wbs:
                self._add_annot_item(f"⬜ Whiteout box (p.{pno + 1})", pno, "whiteout", wb, wb.rect)
        for pno, rbs in sorted(self._engine.redactions.items()):
            for rb in rbs:
                self._add_annot_item(f"⬛ Redaction area (p.{pno + 1})", pno, "redact", rb, rb.rect)
        for (pno, w_idx), val in sorted(self._engine.form_values.items()):
            self._add_annot_item(
                f"📝 Form Field p.{pno + 1}: {str(val)[:32]}",
                pno,
                "form_value",
                w_idx,
                None,
            )
        if self.annot_list.count() == 0:
            empty = QListWidgetItem("No edits or annotations yet")
            empty.setFlags(Qt.NoItemFlags)
            self.annot_list.addItem(empty)

    def _on_thumb_clicked(self, item: QListWidgetItem) -> None:
        pno = item.data(Qt.UserRole)
        if isinstance(pno, int):
            self.pageSelected.emit(pno)

    def _on_thumb_context_menu(self, pos: QPoint) -> None:
        if self._engine is None or self._engine.needs_password:
            return
        item = self.thumb_list.itemAt(pos)
        if item is None:
            return
        pno = item.data(Qt.UserRole)
        if not isinstance(pno, int):
            return
        menu = QMenu(self)
        act_rcw = menu.addAction("Rotate 90° CW")
        act_rccw = menu.addAction("Rotate 90° CCW")
        menu.addSeparator()
        act_blank = menu.addAction("Insert Blank Page After")
        act_dup = menu.addAction("Duplicate Page")
        act_del = menu.addAction("Delete Page")
        act_del.setEnabled(self._engine.page_count > 1)
        chosen = menu.exec(self.thumb_list.mapToGlobal(pos))
        mapping = {
            act_rcw: "rotate_cw",
            act_rccw: "rotate_ccw",
            act_blank: "insert_blank_after",
            act_dup: "duplicate",
            act_del: "delete",
        }
        if chosen in mapping:
            self.pageActionRequested.emit(mapping[chosen], pno)

    def _on_outline_clicked(self, item: QListWidgetItem) -> None:
        pno = item.data(Qt.UserRole)
        if isinstance(pno, int):
            self.pageSelected.emit(pno)

    def _on_annot_clicked(self, item: QListWidgetItem) -> None:
        pno = item.data(Qt.UserRole)
        meta = item.data(Qt.UserRole + 1) or {}
        if isinstance(pno, int):
            rect = meta.get("rect") if isinstance(meta, dict) else None
            if rect is not None:
                self.annotationJumpRequested.emit(pno, rect)
            else:
                self.pageSelected.emit(pno)

    def _on_annot_context_menu(self, pos: QPoint) -> None:
        if self._engine is None:
            return
        item = self.annot_list.itemAt(pos)
        if item is None:
            return
        pno = item.data(Qt.UserRole)
        meta = item.data(Qt.UserRole + 1)
        if not isinstance(pno, int) or not isinstance(meta, dict) or not meta.get("kind"):
            return
        kind = meta["kind"]
        payload = meta.get("payload")
        rect = meta.get("rect")
        menu = QMenu(self)
        act_jump = menu.addAction("Go to Item")
        act_del = menu.addAction("Delete")
        if kind in ("stamp", "textbox", "whiteout", "redact") and getattr(payload, "locked", False):
            act_del.setEnabled(False)
        chosen = menu.exec(self.annot_list.mapToGlobal(pos))
        if chosen is act_jump:
            if rect is not None:
                self.annotationJumpRequested.emit(pno, rect)
            else:
                self.pageSelected.emit(pno)
        elif chosen is act_del:
            self.annotationDeleteRequested.emit(pno, kind, payload)

    def _on_add_bookmark(self) -> None:
        if self._engine is None:
            return
        title, ok = QInputDialog.getText(
            self, "Add Bookmark", f"Title for Page {self._current_page + 1}:", text=f"Page {self._current_page + 1}"
        )
        if ok and title.strip():
            self._engine.custom_bookmarks.append((title.strip(), self._current_page))
            self.refresh_outline()
            self.bookmarkAdded.emit(title.strip(), self._current_page)

    def _on_outline_context_menu(self, pos: QPoint) -> None:
        if self._engine is None:
            return
        item = self.outline_list.itemAt(pos)
        if item is None:
            return
        bm_idx = item.data(Qt.UserRole + 1)
        if not isinstance(bm_idx, int) or not (0 <= bm_idx < len(self._engine.custom_bookmarks)):
            return
        menu = QMenu(self)
        act_ren = menu.addAction("Rename Bookmark...")
        act_del = menu.addAction("Delete Bookmark")
        chosen = menu.exec(self.outline_list.mapToGlobal(pos))
        if chosen is act_ren:
            old_title, pno = self._engine.custom_bookmarks[bm_idx]
            new_title, ok = QInputDialog.getText(self, "Rename Bookmark", "Bookmark title:", text=old_title)
            if ok and new_title.strip() and new_title.strip() != old_title:
                self._engine.custom_bookmarks[bm_idx] = (new_title.strip(), pno)
                self.refresh_outline()
                self.bookmarkRenamed.emit(old_title, new_title.strip(), pno)
        elif chosen is act_del:
            title, pno = self._engine.custom_bookmarks.pop(bm_idx)
            self.refresh_outline()
            self.bookmarkRemoved.emit(title, pno)
