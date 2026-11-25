"""Collapsible left sidebar: Thumbnails, Bookmarks/Outline, and Annotations list."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QIcon, QImage, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QListWidget,
    QListWidgetItem,
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

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("Sidebar")
        self.setFixedWidth(235)
        self._engine: PDFDocumentEngine | None = None
        self._current_page = 0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)

        # 1. Thumbnails
        self.thumb_list = QListWidget()
        self.thumb_list.setIconSize(QSize(136, 180))
        self.thumb_list.setSpacing(4)
        self.thumb_list.itemClicked.connect(self._on_thumb_clicked)
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
        av.addWidget(self.annot_list)
        self.tabs.addTab(annot_wrap, "Notes")

    def set_engine(self, engine: PDFDocumentEngine | None, current_page: int = 0) -> None:
        self._engine = engine
        self._current_page = current_page
        self.refresh_all()

    def set_current_page(self, page_idx: int) -> None:
        self._current_page = page_idx
        if 0 <= page_idx < self.thumb_list.count():
            self.thumb_list.setCurrentRow(page_idx)

    def refresh_all(self) -> None:
        self.refresh_thumbnails()
        self.refresh_outline()
        self.refresh_annotations()

    def refresh_thumbnails(self) -> None:
        self.thumb_list.clear()
        if self._engine is None or self._engine.needs_password:
            return
        for pno in range(self._engine.page_count):
            samples, w, h = self._engine.render_thumbnail_rgba(pno, max_width=132)
            qimg = QImage(samples, w, h, w * 4, QImage.Format_RGBA8888).copy()
            item = QListWidgetItem(QIcon(QPixmap.fromImage(qimg)), f"Page {pno + 1}")
            item.setData(Qt.UserRole, pno)
            item.setTextAlignment(Qt.AlignHCenter)
            self.thumb_list.addItem(item)
        self.set_current_page(self._current_page)

    def refresh_outline(self) -> None:
        self.outline_list.clear()
        if self._engine is None or self._engine.needs_password:
            return
        for title, pno in self._engine.custom_bookmarks:
            item = QListWidgetItem(f"🔖 {title} (p.{pno + 1})")
            item.setData(Qt.UserRole, pno)
            self.outline_list.addItem(item)
        for lvl, title, pno in self._engine.get_toc():
            indent = "  " * max(0, lvl - 1)
            item = QListWidgetItem(f"{indent}{title} (p.{pno + 1})")
            item.setData(Qt.UserRole, pno)
            self.outline_list.addItem(item)
        if self.outline_list.count() == 0:
            self.outline_list.addItem("No bookmarks or outline")

    def refresh_annotations(self) -> None:
        self.annot_list.clear()
        if self._engine is None:
            return
        for pno, ops in sorted(self._engine.in_place_edits.items()):
            for op in ops.values():
                item = QListWidgetItem(f"✏️ Text Edit p.{pno + 1}: {op.new_text[:32]}")
                item.setData(Qt.UserRole, pno)
                self.annot_list.addItem(item)
        for pno, notes in sorted(self._engine.sticky_notes.items()):
            for nt in notes:
                item = QListWidgetItem(f"💬 Note p.{pno + 1}: {nt.text[:32]}")
                item.setData(Qt.UserRole, pno)
                self.annot_list.addItem(item)
        for pno, stps in sorted(self._engine.stamps.items()):
            for stp in stps:
                lbl = stp.text if stp.kind == "stamp" else "Signature/Image"
                item = QListWidgetItem(f"📌 {lbl} (p.{pno + 1})")
                item.setData(Qt.UserRole, pno)
                self.annot_list.addItem(item)
        for pno, strokes in sorted(self._engine.ink_strokes.items()):
            if strokes:
                item = QListWidgetItem(f"🖍️ {len(strokes)} ink/highlight stroke(s) (p.{pno + 1})")
                item.setData(Qt.UserRole, pno)
                self.annot_list.addItem(item)
        if self.annot_list.count() == 0:
            self.annot_list.addItem("No edits or annotations yet")

    def _on_thumb_clicked(self, item: QListWidgetItem) -> None:
        pno = item.data(Qt.UserRole)
        if isinstance(pno, int):
            self.pageSelected.emit(pno)

    def _on_outline_clicked(self, item: QListWidgetItem) -> None:
        pno = item.data(Qt.UserRole)
        if isinstance(pno, int):
            self.pageSelected.emit(pno)

    def _on_annot_clicked(self, item: QListWidgetItem) -> None:
        pno = item.data(Qt.UserRole)
        if isinstance(pno, int):
            self.pageSelected.emit(pno)

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
