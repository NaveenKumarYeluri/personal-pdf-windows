"""Modal dialogs: Visual Page Organizer, Signature Studio, Watermark, Bates, Split, Merge, Protect, Metadata, Word-Flow Editor."""

from __future__ import annotations

import io
import sys
import pymupdf
from PySide6.QtCore import QPoint, QSize, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QIcon,
    QImage,
    QPageLayout,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QTransform,
)
from PySide6.QtPrintSupport import QPrintDialog, QPrintPreviewWidget, QPrinter, QPrinterInfo
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QSlider,
    QSpinBox,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app.core.document_ops import PageOp, parse_page_ranges
from app.core.fonts import available_ui_fonts
from app.core.pdf_engine import PDFDocumentEngine
from app.core.signature import extract_signature_ink_bytes, render_typed_signature_png


class OrganizerDialog(QDialog):
    """Visual Page Grid organizer supporting reorder, rotate, delete, duplicate, blank page, extract."""

    def __init__(self, pdf_bytes: bytes, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Organize Pages")
        self.resize(860, 580)
        self._pdf_bytes = pdf_bytes
        self._engine = PDFDocumentEngine(pdf_bytes)
        self.extract_indices: list[int] | None = None

        root = QVBoxLayout(self)

        bar = QHBoxLayout()
        for label, slot in [
            ("◀ Move Left", lambda: self._move(-1)),
            ("▶ Move Right", lambda: self._move(1)),
            ("↺ Rotate Left", lambda: self._rotate(-90)),
            ("↻ Rotate Right", lambda: self._rotate(90)),
            ("⧉ Duplicate", self._duplicate),
            ("+ Blank Page", self._insert_blank),
            ("⤓ Extract Selected", self._extract),
            ("🗑 Delete", self._delete),
        ]:
            btn = QPushButton(label)
            if "Delete" in label:
                btn.setObjectName("DangerBtn")
            btn.clicked.connect(slot)
            bar.addWidget(btn)
        root.addLayout(bar)

        self.grid = QListWidget()
        self.grid.setViewMode(QListWidget.IconMode)
        self.grid.setIconSize(QSize(130, 170))
        self.grid.setGridSize(QSize(160, 210))
        self.grid.setResizeMode(QListWidget.Adjust)
        self.grid.setSelectionMode(QListWidget.ExtendedSelection)
        self.grid.setDragDropMode(QListWidget.InternalMove)
        self.grid.setSpacing(8)
        root.addWidget(self.grid)

        self._populate()

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.button(QDialogButtonBox.Ok).setText("Apply Page Changes")
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        root.addWidget(btns)

    def _populate(self) -> None:
        self.grid.clear()
        for pno in range(self._engine.page_count):
            samples, w, h = self._engine.render_thumbnail_rgba(pno, max_width=130)
            qimg = QImage(samples, w, h, w * 4, QImage.Format_RGBA8888).copy()
            pm = QPixmap.fromImage(qimg)
            item = QListWidgetItem(QIcon(pm), f"Page {pno + 1}")
            item.setData(Qt.UserRole, {"orig_idx": pno, "rot": 0})
            item.setData(Qt.UserRole + 1, pm)
            self.grid.addItem(item)

    def _selected_rows(self) -> list[int]:
        return sorted({self.grid.row(it) for it in self.grid.selectedItems()})

    def _move(self, delta: int) -> None:
        rows = self._selected_rows()
        if not rows:
            return
        if delta < 0:
            for r in rows:
                if r > 0:
                    it = self.grid.takeItem(r)
                    self.grid.insertItem(r - 1, it)
                    it.setSelected(True)
        else:
            for r in reversed(rows):
                if r < self.grid.count() - 1:
                    it = self.grid.takeItem(r)
                    self.grid.insertItem(r + 1, it)
                    it.setSelected(True)

    def _rotate(self, delta: int) -> None:
        for r in self._selected_rows():
            it = self.grid.item(r)
            data = dict(it.data(Qt.UserRole))
            data["rot"] = (data["rot"] + delta) % 360
            it.setData(Qt.UserRole, data)
            orig_pm = it.data(Qt.UserRole + 1)
            if isinstance(orig_pm, QPixmap) and not orig_pm.isNull():
                rot_pm = orig_pm.transformed(QTransform().rotate(data["rot"]), Qt.SmoothTransformation)
                it.setIcon(QIcon(rot_pm.scaled(130, 170, Qt.KeepAspectRatio, Qt.SmoothTransformation)))
            base_lbl = f"Page {data['orig_idx'] + 1}" if data["orig_idx"] is not None else "Blank Page"
            suffix = f" ({data['rot']}°)" if data["rot"] else ""
            it.setText(base_lbl + suffix)

    def _duplicate(self) -> None:
        for r in reversed(self._selected_rows()):
            src = self.grid.item(r)
            clone = QListWidgetItem(src.icon(), src.text() + " (Copy)")
            clone.setData(Qt.UserRole, dict(src.data(Qt.UserRole)))
            clone.setData(Qt.UserRole + 1, src.data(Qt.UserRole + 1))
            self.grid.insertItem(r + 1, clone)

    def _insert_blank(self) -> None:
        rows = self._selected_rows()
        insert_at = (rows[-1] + 1) if rows else self.grid.count()
        pm = QPixmap(110, 155)
        pm.fill(Qt.white)
        it = QListWidgetItem(QIcon(pm), "Blank Page")
        it.setData(Qt.UserRole, {"orig_idx": None, "rot": 0})
        it.setData(Qt.UserRole + 1, pm)
        self.grid.insertItem(insert_at, it)

    def _delete(self) -> None:
        rows = self._selected_rows()
        if not rows:
            return
        if len(rows) >= self.grid.count():
            QMessageBox.warning(self, "Organize Pages", "At least one page must remain in the document.")
            return
        for r in reversed(rows):
            self.grid.takeItem(r)

    def _extract(self) -> None:
        indices = []
        for r in self._selected_rows():
            data = self.grid.item(r).data(Qt.UserRole)
            if data["orig_idx"] is not None:
                indices.append(data["orig_idx"])
        if not indices:
            QMessageBox.information(self, "Extract Pages", "Select one or more existing pages first.")
            return
        self.extract_indices = indices
        self.accept()

    def get_operations(self) -> list[PageOp]:
        ops: list[PageOp] = []
        for i in range(self.grid.count()):
            data = self.grid.item(i).data(Qt.UserRole)
            ops.append(PageOp(original_index=data["orig_idx"], rotation_delta=data["rot"]))
        return ops


class _SignaturePad(QWidget):
    """Canvas widget for drawing electronic signatures."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFixedSize(480, 170)
        self._strokes: list[list[QPoint]] = []
        self._cur: list[QPoint] = []
        self.pen_color = QColor("#000000")

    def clear(self) -> None:
        self._strokes.clear()
        self._cur.clear()
        self.update()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._cur = [event.position().toPoint()]
            self.update()

    def mouseMoveEvent(self, event) -> None:
        if self._cur:
            self._cur.append(event.position().toPoint())
            self.update()

    def mouseReleaseEvent(self, event) -> None:
        if self._cur:
            self._strokes.append(self._cur)
            self._cur = []
            self.update()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), Qt.white)
        p.setPen(QPen(QColor("#cbd5e1"), 1))
        p.drawRect(0, 0, self.width() - 1, self.height() - 1)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setPen(QPen(self.pen_color, 2.8, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        for st in self._strokes + ([self._cur] if self._cur else []):
            if len(st) >= 2:
                path = QPainterPath(st[0])
                for pt in st[1:]:
                    path.lineTo(pt)
                p.drawPath(path)

    def to_png_bytes(self) -> bytes | None:
        if not self._strokes:
            return None
        img = QImage(self.size(), QImage.Format_RGBA8888)
        img.fill(Qt.transparent)
        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setPen(QPen(self.pen_color, 3.0, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        for st in self._strokes:
            if len(st) >= 2:
                path = QPainterPath(st[0])
                for pt in st[1:]:
                    path.lineTo(pt)
                p.drawPath(path)
        p.end()
        buf = io.BytesIO()
        from PySide6.QtCore import QBuffer, QIODevice

        qbuf = QBuffer()
        qbuf.open(QIODevice.WriteOnly)
        img.save(qbuf, "PNG")
        return bytes(qbuf.data())


class SignatureDialog(QDialog):
    """Draw, type, or upload signature with adaptive paper background removal."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Create Signature")
        self.resize(540, 380)
        self.result_png_bytes: bytes | None = None
        self._raw_upload_bytes: bytes | None = None

        root = QVBoxLayout(self)
        self.tabs = QTabWidget()
        root.addWidget(self.tabs)

        # Tab 1: Draw
        w_draw = QWidget()
        ld = QVBoxLayout(w_draw)
        self.pad = _SignaturePad()
        ld.addWidget(self.pad, alignment=Qt.AlignCenter)
        dh = QHBoxLayout()
        btn_clear = QPushButton("Clear")
        btn_clear.clicked.connect(self.pad.clear)
        btn_black = QPushButton("Black Ink")
        btn_black.clicked.connect(lambda: self._set_ink("#000000"))
        btn_blue = QPushButton("Blue Ink")
        btn_blue.clicked.connect(lambda: self._set_ink("#0033aa"))
        dh.addWidget(btn_clear)
        dh.addWidget(btn_black)
        dh.addWidget(btn_blue)
        dh.addStretch()
        ld.addLayout(dh)
        self.tabs.addTab(w_draw, "Draw")

        # Tab 2: Type
        w_type = QWidget()
        lt = QVBoxLayout(w_type)
        self.type_input = QLineEdit()
        self.type_input.setPlaceholderText("Type your signature name...")
        self.type_preview = QLabel("Your Signature")
        self.type_preview.setAlignment(Qt.AlignCenter)
        self.type_preview.setFont(QFont("Segoe Script", 24, QFont.Bold))
        self.type_input.textChanged.connect(lambda t: self.type_preview.setText(t or "Your Signature"))
        lt.addWidget(self.type_input)
        lt.addWidget(self.type_preview, stretch=1)
        self.tabs.addTab(w_type, "Type")

        # Tab 3: Upload with paper removal
        w_img = QWidget()
        li = QVBoxLayout(w_img)
        btn_pick = QPushButton("Choose Signature Image (PNG/JPG)...")
        btn_pick.clicked.connect(self._pick_image)
        li.addWidget(btn_pick)
        self.chk_rm_bg = QCheckBox("Remove paper background (Extract transparent ink)")
        self.chk_rm_bg.setChecked(True)
        self.chk_rm_bg.toggled.connect(lambda _: self._refresh_upload_preview())
        li.addWidget(self.chk_rm_bg)
        th = QHBoxLayout()
        th.addWidget(QLabel("Ink Sensitivity:"))
        self.slider_thresh = QSlider(Qt.Horizontal)
        self.slider_thresh.setRange(140, 250)
        self.slider_thresh.setValue(215)
        self.slider_thresh.valueChanged.connect(lambda _: self._refresh_upload_preview())
        th.addWidget(self.slider_thresh)
        li.addLayout(th)
        self.upload_preview = QLabel("No image loaded")
        self.upload_preview.setAlignment(Qt.AlignCenter)
        self.upload_preview.setMinimumHeight(130)
        li.addWidget(self.upload_preview, stretch=1)
        self.tabs.addTab(w_img, "Upload Image")

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.button(QDialogButtonBox.Ok).setText("Place Signature")
        btns.accepted.connect(self._on_accept)
        btns.rejected.connect(self.reject)
        root.addWidget(btns)

    def _set_ink(self, hex_col: str) -> None:
        self.pad.pen_color = QColor(hex_col)
        self.pad.update()

    def _pick_image(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Signature Image", "", "Images (*.png *.jpg *.jpeg *.bmp *.webp)"
        )
        if not path:
            return
        try:
            with open(path, "rb") as f:
                raw = f.read()
            png = extract_signature_ink_bytes(
                raw,
                remove_bg=self.chk_rm_bg.isChecked(),
                threshold=self.slider_thresh.value(),
            )
        except Exception as err:
            QMessageBox.warning(self, "Signature Image", f"Could not load image:\n{err}")
            return
        self._raw_upload_bytes = raw
        self._display_upload_png(png)

    def _refresh_upload_preview(self) -> None:
        if not self._raw_upload_bytes:
            return
        try:
            png = extract_signature_ink_bytes(
                self._raw_upload_bytes,
                remove_bg=self.chk_rm_bg.isChecked(),
                threshold=self.slider_thresh.value(),
            )
        except Exception as err:
            QMessageBox.warning(self, "Signature Image", f"Could not process image:\n{err}")
            return
        self._display_upload_png(png)

    def _display_upload_png(self, png: bytes) -> None:
        self._processed_upload_png = png
        pm = QPixmap()
        pm.loadFromData(png, "PNG")
        self.upload_preview.setPixmap(pm.scaled(420, 140, Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def _on_accept(self) -> None:
        tab_idx = self.tabs.currentIndex()
        if tab_idx == 0:
            self.result_png_bytes = self.pad.to_png_bytes()
        elif tab_idx == 1:
            txt = self.type_input.text().strip()
            if txt:
                self.result_png_bytes = render_typed_signature_png(txt, self.pad.pen_color.name())
        else:
            self.result_png_bytes = getattr(self, "_processed_upload_png", None)

        if not self.result_png_bytes:
            QMessageBox.warning(self, "Signature", "Please draw, type, or upload a signature first.")
            return
        self.accept()


class WatermarkDialog(QDialog):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Add Watermark")
        self.color_hex = "#ff0000"
        form = QFormLayout(self)

        self.txt = QLineEdit("CONFIDENTIAL")
        self.angle = QComboBox()
        self.angle.addItems(["45", "0", "-45", "90"])
        self.opacity = QSpinBox()
        self.opacity.setRange(5, 100)
        self.opacity.setValue(25)
        self.opacity.setSuffix("%")
        self.size = QSpinBox()
        self.size.setRange(12, 140)
        self.size.setValue(48)
        self.btn_col = QPushButton(self.color_hex)
        self.btn_col.clicked.connect(self._pick_col)

        form.addRow("Watermark Text:", self.txt)
        form.addRow("Angle (deg):", self.angle)
        form.addRow("Opacity:", self.opacity)
        form.addRow("Font Size:", self.size)
        form.addRow("Color:", self.btn_col)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        form.addRow(btns)

    def _pick_col(self) -> None:
        c = QColorDialog.getColor(QColor(self.color_hex), self, "Watermark Color")
        if c.isValid():
            self.color_hex = c.name()
            self.btn_col.setText(self.color_hex)


class BatesDialog(QDialog):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Headers, Footers & Bates Numbering")
        form = QFormLayout(self)

        self.prefix = QLineEdit()
        self.prefix.setPlaceholderText("e.g. DOC- or CONF-")
        self.start = QSpinBox()
        self.start.setRange(1, 99999)
        self.start.setValue(1)
        self.fmt = QComboBox()
        self.fmt.addItems(["Page {page} of {total}", "{page}", "Page {page}"])
        self.pos = QComboBox()
        self.pos.addItems(["bottom-center", "bottom-right", "bottom-left", "top-center", "top-right", "top-left"])

        form.addRow("Prefix:", self.prefix)
        form.addRow("Start Number:", self.start)
        form.addRow("Format:", self.fmt)
        form.addRow("Position:", self.pos)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        form.addRow(btns)


class SplitDialog(QDialog):
    def __init__(self, total_pages: int, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Split PDF Document")
        self.total_pages = total_pages
        form = QFormLayout(self)

        self.ranges_input = QLineEdit()
        self.ranges_input.setPlaceholderText("e.g. 1-3, 5, 7-10")
        hint = QLabel(f"Document has {total_pages} page(s). Each range creates a separate PDF file.")
        hint.setWordWrap(True)

        form.addRow("Page Ranges:", self.ranges_input)
        form.addRow(hint)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.button(QDialogButtonBox.Ok).setText("Split & Save")
        btns.accepted.connect(self._validate_and_accept)
        btns.rejected.connect(self.reject)
        form.addRow(btns)

    def _validate_and_accept(self) -> None:
        try:
            parse_page_ranges(self.ranges_input.text(), self.total_pages)
        except ValueError as err:
            QMessageBox.warning(self, "Invalid Page Range", str(err))
            return
        self.accept()


class MergeDialog(QDialog):
    def __init__(self, current_name: str | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Combine & Merge PDFs")
        self.resize(520, 380)
        self.file_paths: list[str] = []

        root = QVBoxLayout(self)
        if current_name:
            self.chk_include_current = QCheckBox(f"Include currently open document ({current_name}) first")
            self.chk_include_current.setChecked(True)
            root.addWidget(self.chk_include_current)
        else:
            self.chk_include_current = None

        bar = QHBoxLayout()
        btn_add = QPushButton("+ Add PDF Files...")
        btn_add.clicked.connect(self._add_files)
        btn_up = QPushButton("▲ Move Up")
        btn_up.clicked.connect(lambda: self._move(-1))
        btn_down = QPushButton("▼ Move Down")
        btn_down.clicked.connect(lambda: self._move(1))
        btn_rm = QPushButton("✕ Remove")
        btn_rm.clicked.connect(self._remove_selected)
        for b in (btn_add, btn_up, btn_down, btn_rm):
            bar.addWidget(b)
        root.addLayout(bar)

        self.list_widget = QListWidget()
        root.addWidget(self.list_widget)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.button(QDialogButtonBox.Ok).setText("Merge PDFs")
        btns.accepted.connect(self._validate_and_accept)
        btns.rejected.connect(self.reject)
        root.addWidget(btns)

    def _add_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Select PDF Files to Merge", "", "PDF Files (*.pdf)")
        for p in paths:
            self.file_paths.append(p)
            self.list_widget.addItem(p)

    def _move(self, delta: int) -> None:
        row = self.list_widget.currentRow()
        target = row + delta
        if row < 0 or not (0 <= target < self.list_widget.count()):
            return
        it = self.list_widget.takeItem(row)
        self.list_widget.insertItem(target, it)
        self.list_widget.setCurrentRow(target)
        path = self.file_paths.pop(row)
        self.file_paths.insert(target, path)

    def _remove_selected(self) -> None:
        row = self.list_widget.currentRow()
        if row < 0:
            return
        self.list_widget.takeItem(row)
        self.file_paths.pop(row)

    def _validate_and_accept(self) -> None:
        has_current = bool(self.chk_include_current and self.chk_include_current.isChecked())
        if not has_current and not self.file_paths:
            QMessageBox.warning(self, "Combine PDFs", "Please include or add at least one PDF document to merge.")
            return
        self.accept()


class ProtectDialog(QDialog):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Encrypt PDF (AES-256)")
        form = QFormLayout(self)

        self.pw1 = QLineEdit()
        self.pw1.setEchoMode(QLineEdit.Password)
        self.pw2 = QLineEdit()
        self.pw2.setEchoMode(QLineEdit.Password)
        self.allow_print = QCheckBox("Allow high-resolution printing")
        self.allow_print.setChecked(True)
        self.allow_copy = QCheckBox("Allow copying text and graphics")
        self.allow_copy.setChecked(True)

        form.addRow("Password:", self.pw1)
        form.addRow("Confirm Password:", self.pw2)
        form.addRow(self.allow_print)
        form.addRow(self.allow_copy)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.button(QDialogButtonBox.Ok).setText("Encrypt & Save")
        btns.accepted.connect(self._validate)
        btns.rejected.connect(self.reject)
        form.addRow(btns)

    def _validate(self) -> None:
        if not self.pw1.text().strip():
            QMessageBox.warning(self, "Encrypt PDF", "Please enter a password.")
            return
        if self.pw1.text() != self.pw2.text():
            QMessageBox.warning(self, "Encrypt PDF", "Passwords do not match.")
            return
        self.accept()


class MetadataDialog(QDialog):
    def __init__(self, meta: dict, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Document Properties")
        self.resize(420, 260)
        form = QFormLayout(self)

        self.title = QLineEdit(str(meta.get("title", "")))
        self.author = QLineEdit(str(meta.get("author", "")))
        self.subject = QLineEdit(str(meta.get("subject", "")))
        self.keywords = QLineEdit(str(meta.get("keywords", "")))
        pages = QLabel(str(meta.get("pageCount", 0)))

        form.addRow("Title:", self.title)
        form.addRow("Author:", self.author)
        form.addRow("Subject:", self.subject)
        form.addRow("Keywords:", self.keywords)
        form.addRow("Page Count:", pages)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        form.addRow(btns)


class WordFlowDialog(QDialog):
    """Continuous MS-Word-like rich document editor producing multi-page vector PDF via MuPDF Story."""

    def __init__(self, initial_html: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Word-Flow Document Editor (Continuous Multi-Page Reflow)")
        self.resize(860, 680)
        self._syncing = False
        root = QVBoxLayout(self)

        bar = QHBoxLayout()
        self.font_combo = QComboBox()
        self.font_combo.addItems(available_ui_fonts())
        self.font_combo.currentTextChanged.connect(self._set_font_family)
        self.btn_bold = QPushButton("B")
        self.btn_bold.setCheckable(True)
        self.btn_bold.setStyleSheet("font-weight:bold;")
        self.btn_bold.clicked.connect(self._toggle_bold)
        self.btn_italic = QPushButton("I")
        self.btn_italic.setCheckable(True)
        self.btn_italic.setStyleSheet("font-style:italic;")
        self.btn_italic.clicked.connect(self._toggle_italic)
        self.btn_under = QPushButton("U")
        self.btn_under.setCheckable(True)
        self.btn_under.setStyleSheet("text-decoration:underline;")
        self.btn_under.clicked.connect(self._toggle_under)
        self.size_spin = QSpinBox()
        self.size_spin.setRange(8, 72)
        self.size_spin.setValue(12)
        self.size_spin.valueChanged.connect(self._set_size)
        self.btn_col = QPushButton("Color")
        self.btn_col.clicked.connect(self._pick_color)
        self.btn_align_l = QPushButton("L")
        self.btn_align_l.setToolTip("Align Left")
        self.btn_align_l.clicked.connect(lambda: self.editor.setAlignment(Qt.AlignLeft))
        self.btn_align_c = QPushButton("C")
        self.btn_align_c.setToolTip("Align Center")
        self.btn_align_c.clicked.connect(lambda: self.editor.setAlignment(Qt.AlignHCenter))
        self.btn_align_r = QPushButton("R")
        self.btn_align_r.setToolTip("Align Right")
        self.btn_align_r.clicked.connect(lambda: self.editor.setAlignment(Qt.AlignRight))
        for w in (
            self.font_combo,
            self.btn_bold,
            self.btn_italic,
            self.btn_under,
            self.size_spin,
            self.btn_col,
            self.btn_align_l,
            self.btn_align_c,
            self.btn_align_r,
        ):
            bar.addWidget(w)
        bar.addStretch()
        root.addLayout(bar)

        self.editor = QTextEdit()
        self.editor.setHtml(initial_html)
        self.editor.currentCharFormatChanged.connect(self._sync_toolbar)
        self.editor.cursorPositionChanged.connect(self._sync_toolbar)
        root.addWidget(self.editor, stretch=1)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.button(QDialogButtonBox.Ok).setText("Apply to Document (Vector PDF)")
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        root.addWidget(btns)
        self._sync_toolbar()

    def _set_font_family(self, family: str) -> None:
        if not self._syncing and family:
            self.editor.setFontFamily(family)

    def _toggle_bold(self, checked: bool) -> None:
        if not self._syncing:
            self.editor.setFontWeight(QFont.Bold if checked else QFont.Normal)

    def _toggle_italic(self, checked: bool) -> None:
        if not self._syncing:
            self.editor.setFontItalic(checked)

    def _toggle_under(self, checked: bool) -> None:
        if not self._syncing:
            self.editor.setFontUnderline(checked)

    def _set_size(self, sz: int) -> None:
        if not self._syncing:
            self.editor.setFontPointSize(float(sz))

    def _pick_color(self) -> None:
        c = QColorDialog.getColor(self.editor.textColor(), self, "Text Color")
        if c.isValid():
            self.editor.setTextColor(c)

    def _sync_toolbar(self, *_args) -> None:
        self._syncing = True
        try:
            fmt = self.editor.currentCharFormat()
            fams = fmt.fontFamilies()
            fam = fams[0] if fams else fmt.font().family()
            idx = self.font_combo.findText(fam, Qt.MatchFixedString)
            if idx >= 0:
                self.font_combo.setCurrentIndex(idx)
            pt = fmt.fontPointSize()
            if pt >= 8:
                self.size_spin.setValue(int(round(pt)))
            self.btn_bold.setChecked(fmt.fontWeight() >= QFont.Bold)
            self.btn_italic.setChecked(fmt.fontItalic())
            self.btn_under.setChecked(fmt.fontUnderline())
        finally:
            self._syncing = False

    def get_html(self) -> str:
        return self.editor.toHtml()


class PdfPrintDialog(QDialog):
    """Native Qt Print Preview & Print Dialog bypassing Windows 11 legacy PrintDlgEx limitations."""

    PDF_FILE_OPTION = "Save as PDF File..."

    def __init__(
        self,
        pdf_bytes: bytes,
        current_page_zero: int = 0,
        doc_name: str = "document.pdf",
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Print Document")
        self.resize(920, 640)
        self._doc: pymupdf.Document | None = pymupdf.open(stream=pdf_bytes, filetype="pdf")
        self._total_pages = len(self._doc)
        self._current_page_zero = max(0, min(current_page_zero, max(0, self._total_pages - 1)))
        self._doc_name = doc_name
        self.used_system_dialog = False

        if sys.platform == "win32":
            try:
                import ctypes

                ctypes.windll.ole32.OleInitialize(None)
            except Exception:
                pass

        self.printer = QPrinter(QPrinter.HighResolution)
        default_name = QPrinterInfo.defaultPrinterName()
        if default_name:
            self.printer.setPrinterName(default_name)

        root = QHBoxLayout(self)

        # Left: Live Print Preview
        prev_col = QVBoxLayout()
        pbar = QHBoxLayout()
        self.btn_prev_p = QPushButton("◀ Prev")
        self.btn_next_p = QPushButton("Next ▶")
        self.lbl_prev_page = QLabel("Preview")
        self.btn_z_out = QPushButton("−")
        self.btn_z_fit = QPushButton("Fit")
        self.btn_z_in = QPushButton("+")
        for b in (self.btn_prev_p, self.btn_next_p, self.btn_z_out, self.btn_z_fit, self.btn_z_in):
            b.setObjectName("PillBtn")
        pbar.addWidget(self.btn_prev_p)
        pbar.addWidget(self.btn_next_p)
        pbar.addWidget(self.lbl_prev_page)
        pbar.addStretch()
        pbar.addWidget(self.btn_z_out)
        pbar.addWidget(self.btn_z_fit)
        pbar.addWidget(self.btn_z_in)
        prev_col.addLayout(pbar)

        self.preview = QPrintPreviewWidget(self.printer, self)
        self.preview.paintRequested.connect(self._on_preview_paint)
        prev_col.addWidget(self.preview, stretch=1)
        root.addLayout(prev_col, stretch=1)

        self.btn_prev_p.clicked.connect(lambda: self._step_preview_page(-1))
        self.btn_next_p.clicked.connect(lambda: self._step_preview_page(1))
        self.btn_z_out.clicked.connect(lambda: self.preview.zoomOut(1.2))
        self.btn_z_in.clicked.connect(lambda: self.preview.zoomIn(1.2))
        self.btn_z_fit.clicked.connect(self.preview.fitInView)

        # Right: Print Settings
        ctrl_wrap = QWidget()
        ctrl_wrap.setFixedWidth(290)
        cv = QVBoxLayout(ctrl_wrap)

        grp_printer = QGroupBox("Printer")
        fp = QFormLayout(grp_printer)
        self.printer_combo = QComboBox()
        names = [p.printerName() for p in QPrinterInfo.availablePrinters() if p.printerName()]
        for n in names:
            self.printer_combo.addItem(n)
        self.printer_combo.addItem(self.PDF_FILE_OPTION)
        if default_name and default_name in names:
            self.printer_combo.setCurrentText(default_name)
        fp.addRow("Destination:", self.printer_combo)
        self.copies_spin = QSpinBox()
        self.copies_spin.setRange(1, 99)
        self.copies_spin.setValue(1)
        fp.addRow("Copies:", self.copies_spin)
        cv.addWidget(grp_printer)

        grp_pages = QGroupBox("Pages")
        vp = QVBoxLayout(grp_pages)
        self.rb_all = QRadioButton(f"All Pages (1–{self._total_pages})")
        self.rb_current = QRadioButton(f"Current Page ({self._current_page_zero + 1})")
        self.rb_custom = QRadioButton("Custom Range:")
        self.rb_all.setChecked(True)
        self.range_input = QLineEdit(f"1-{self._total_pages}" if self._total_pages > 1 else "1")
        self.range_input.setPlaceholderText("e.g. 1-3, 5")
        self.range_input.setEnabled(False)
        self.rb_custom.toggled.connect(self.range_input.setEnabled)
        for rb in (self.rb_all, self.rb_current, self.rb_custom):
            rb.toggled.connect(self._refresh_preview)
            vp.addWidget(rb)
        self.range_input.textChanged.connect(self._refresh_preview)
        vp.addWidget(self.range_input)
        cv.addWidget(grp_pages)

        grp_layout = QGroupBox("Layout")
        fl = QFormLayout(grp_layout)
        self.orient_combo = QComboBox()
        self.orient_combo.addItems(["Auto (Match Page)", "Portrait", "Landscape"])
        self.orient_combo.currentIndexChanged.connect(self._refresh_preview)
        self.fit_check = QCheckBox("Fit to printable page")
        self.fit_check.setChecked(True)
        self.fit_check.toggled.connect(self._refresh_preview)
        fl.addRow("Orientation:", self.orient_combo)
        fl.addRow(self.fit_check)
        cv.addWidget(grp_layout)

        cv.addStretch()

        self.btn_print = QPushButton("Print")
        self.btn_print.setObjectName("PrimaryBtn")
        self.btn_print.setMinimumHeight(34)
        self.btn_print.clicked.connect(self._on_print_clicked)
        self.btn_sys = QPushButton("System Print Dialog...")
        self.btn_sys.setObjectName("PillBtn")
        self.btn_sys.clicked.connect(self._on_system_dialog)
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.clicked.connect(self.reject)
        cv.addWidget(self.btn_print)
        cv.addWidget(self.btn_sys)
        cv.addWidget(self.btn_cancel)
        root.addWidget(ctrl_wrap)

        self._update_preview_label()

    def _step_preview_page(self, delta: int) -> None:
        cnt = max(1, self.preview.pageCount())
        nxt = max(1, min(cnt, self.preview.currentPage() + delta))
        self.preview.setCurrentPage(nxt)
        self._update_preview_label()

    def _update_preview_label(self) -> None:
        self.lbl_prev_page.setText(f"Page {self.preview.currentPage()} / {max(1, self.preview.pageCount())}")

    def _refresh_preview(self, *_args) -> None:
        self._apply_orientation_to_printer(self.printer, self._safe_page_indices()[0])
        self.preview.updatePreview()
        self._update_preview_label()

    def _safe_page_indices(self) -> list[int]:
        if self._total_pages <= 0:
            return []
        if self.rb_current.isChecked():
            return [self._current_page_zero]
        if self.rb_custom.isChecked():
            try:
                return self._resolve_custom_indices()
            except ValueError:
                return [self._current_page_zero]
        return list(range(self._total_pages))

    def _resolve_custom_indices(self) -> list[int]:
        ranges = parse_page_ranges(self.range_input.text(), self._total_pages)
        out: list[int] = []
        for s, e in ranges:
            out.extend(range(s - 1, e))
        return out

    def selected_page_indices(self) -> list[int]:
        if self._total_pages <= 0:
            return []
        if self.rb_current.isChecked():
            return [self._current_page_zero]
        if self.rb_custom.isChecked():
            return self._resolve_custom_indices()
        return list(range(self._total_pages))

    def _apply_orientation_to_printer(self, printer: QPrinter, page_idx: int) -> None:
        mode = self.orient_combo.currentText()
        if mode == "Landscape":
            printer.setPageOrientation(QPageLayout.Landscape)
        elif mode == "Portrait":
            printer.setPageOrientation(QPageLayout.Portrait)
        elif self._doc is not None and 0 <= page_idx < len(self._doc):
            r = self._doc[page_idx].rect
            printer.setPageOrientation(QPageLayout.Landscape if r.width > r.height else QPageLayout.Portrait)

    def _on_preview_paint(self, printer: QPrinter) -> None:
        if self._doc is None:
            return
        self.render_to_printer(printer, indices=self._safe_page_indices(), preview_dpi=120)

    def render_to_printer(
        self,
        printer: QPrinter,
        indices: list[int] | None = None,
        preview_dpi: int | None = None,
    ) -> None:
        if self._doc is None:
            return
        pages = indices if indices is not None else self.selected_page_indices()
        if not pages:
            return
        self._apply_orientation_to_printer(printer, pages[0])
        painter = QPainter()
        if not painter.begin(printer):
            raise RuntimeError("Unable to start print job on the selected printer.")
        try:
            dpi = preview_dpi if preview_dpi is not None else min(300, max(150, int(printer.resolution() or 200)))
            fit = self.fit_check.isChecked()
            for out_i, p_idx in enumerate(pages):
                page = self._doc[p_idx]
                if out_i > 0:
                    self._apply_orientation_to_printer(printer, p_idx)
                    printer.newPage()
                page_rect = printer.pageRect(QPrinter.DevicePixel)
                pix = page.get_pixmap(dpi=dpi, alpha=False)
                img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format_RGB888).copy()
                if fit:
                    scaled = img.scaled(
                        max(1, int(page_rect.width())),
                        max(1, int(page_rect.height())),
                        Qt.KeepAspectRatio,
                        Qt.SmoothTransformation,
                    )
                else:
                    scale_factor = float(printer.resolution() or dpi) / float(dpi)
                    scaled = img.scaled(
                        max(1, int(round(img.width() * scale_factor))),
                        max(1, int(round(img.height() * scale_factor))),
                        Qt.KeepAspectRatio,
                        Qt.SmoothTransformation,
                    )
                dx = max(0, int((page_rect.width() - scaled.width()) // 2))
                dy = max(0, int((page_rect.height() - scaled.height()) // 2))
                painter.drawImage(dx, dy, scaled)
        finally:
            painter.end()

    def _on_print_clicked(self) -> None:
        try:
            indices = self.selected_page_indices()
        except ValueError as err:
            QMessageBox.warning(self, "Invalid Page Range", str(err))
            return
        if not indices:
            QMessageBox.warning(self, "Print Document", "No pages selected to print.")
            return
        dest = self.printer_combo.currentText()
        if dest == self.PDF_FILE_OPTION:
            base = self._doc_name[:-4] if self._doc_name.lower().endswith(".pdf") else self._doc_name
            out_path, _ = QFileDialog.getSaveFileName(
                self, "Save Print Output as PDF", f"{base}_printed.pdf", "PDF Files (*.pdf)"
            )
            if not out_path:
                return
            if not out_path.lower().endswith(".pdf"):
                out_path += ".pdf"
            self.printer.setOutputFormat(QPrinter.PdfFormat)
            self.printer.setOutputFileName(out_path)
        else:
            self.printer.setOutputFormat(QPrinter.NativeFormat)
            self.printer.setOutputFileName("")
            self.printer.setPrinterName(dest)
        self.printer.setCopyCount(self.copies_spin.value())
        self.accept()

    def _on_system_dialog(self) -> None:
        try:
            self.selected_page_indices()
        except ValueError as err:
            QMessageBox.warning(self, "Invalid Page Range", str(err))
            return
        dlg = QPrintDialog(self.printer, self)
        if dlg.exec():
            self.used_system_dialog = True
            self.accept()

    def close_doc(self) -> None:
        if self._doc is not None:
            self._doc.close()
            self._doc = None

    def done(self, result: int) -> None:
        if result == QDialog.Rejected:
            self.close_doc()
        super().done(result)
