"""Modal dialogs: Visual Page Organizer, Signature Studio, Watermark, Bates, Split, Merge, Protect, Metadata, Word-Flow Editor."""

from __future__ import annotations

import io
from PySide6.QtCore import QPoint, QSize, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QImage, QPainter, QPainterPath, QPen, QPixmap, QTransform
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
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
