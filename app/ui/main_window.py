"""Main application window with Adobe Acrobat-inspired space-saving UI, inline tabs, and native printing."""

from __future__ import annotations

import os
from dataclasses import dataclass

import pymupdf
from PySide6.QtCore import QPoint, QSize, Qt
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QIcon, QImage, QKeySequence, QPainter, QShortcut
from PySide6.QtPrintSupport import QPrintDialog, QPrinter
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from app.core.document_ops import (
    PageOp,
    apply_bates_numbering,
    apply_watermark,
    encrypt_pdf_aes256,
    export_page_image,
    extract_clean_text,
    flatten_form_fields,
    get_metadata,
    merge_pdfs,
    organize_pages,
    split_pdf_by_ranges,
    update_metadata,
    write_bytes_atomic,
)
from app.core.ligatures import heal_pdf_text
from app.core.models import SearchHit, StampItem, TextEditOp
from app.core.pdf_engine import (
    PDFDocumentEngine,
    build_pdf_from_word_flow_html,
    create_sample_pdf_bytes,
)
from app.ui.dialogs import (
    BatesDialog,
    MergeDialog,
    MetadataDialog,
    OrganizerDialog,
    ProtectDialog,
    SignatureDialog,
    SplitDialog,
    WatermarkDialog,
    WordFlowDialog,
)
from app.ui.floating_bar import FloatingFormatBar
from app.ui.icons import make_icon
from app.ui.page_widget import PDFPageWidget
from app.ui.ribbon import LeftToolsRail, RibbonHeader
from app.ui.sidebar import SidebarPanel
from app.ui.text_editor import InPlaceTextEditor
from app.ui.theme import build_stylesheet


@dataclass
class _UndoEntry:
    kind: str
    page_idx: int
    payload: object
    previous_payload: object | None = None


class DocumentTab(QScrollArea):
    """Scrollable continuous multi-page view for one open PDF document."""

    def __init__(self, engine: PDFDocumentEngine, accent_color: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("ViewerScroll")
        self.setWidgetResizable(True)
        self.engine = engine
        self.accent_color = accent_color

        self.zoom: float = 1.0
        self.zoom_mode: str = "Fit Width"
        self.rotation: int = 0
        self.current_page: int = 0

        self.undo_stack: list[_UndoEntry] = []
        self.redo_stack: list[_UndoEntry] = []
        self.search_hits: list[SearchHit] = []
        self.search_idx: int = -1

        self.pages_container = QWidget()
        self.pages_container.setStyleSheet("background: transparent;")
        self.pages_layout = QVBoxLayout(self.pages_container)
        self.pages_layout.setContentsMargins(20, 16, 20, 28)
        self.pages_layout.setSpacing(16)
        self.pages_layout.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        self.setWidget(self.pages_container)

        self.page_widgets: list[PDFPageWidget] = []

    def calculate_fit_zoom(self) -> float:
        if self.engine.page_count == 0:
            return 1.0
        pw, ph = self.engine.get_page_size(self.current_page)
        if self.rotation % 180 == 90:
            pw, ph = ph, pw
        vw = max(320, self.viewport().width() - 48)
        vh = max(320, self.viewport().height() - 48)
        if self.zoom_mode == "Fit Width":
            return max(0.3, min(4.0, vw / max(1.0, pw)))
        if self.zoom_mode == "Fit Page":
            return max(0.3, min(4.0, min(vw / max(1.0, pw), vh / max(1.0, ph))))
        return self.zoom

    def rebuild_pages(self, tool_state: dict, callbacks: dict) -> None:
        for pw in self.page_widgets:
            pw.setParent(None)
            pw.deleteLater()
        self.page_widgets.clear()

        if self.zoom_mode in ("Fit Width", "Fit Page"):
            self.zoom = self.calculate_fit_zoom()

        for pno in range(self.engine.page_count):
            pw = PDFPageWidget(
                engine=self.engine,
                page_idx=pno,
                zoom=self.zoom,
                rotation=self.rotation,
                accent_color=self.accent_color,
                parent=self.pages_container,
            )
            pw.set_tool_state(**tool_state)
            pw.inPlaceEditorOpened.connect(callbacks["editor_opened"])
            pw.inPlaceEditCommitted.connect(callbacks["edit_committed"])
            pw.annotationAdded.connect(callbacks["annot_added"])
            pw.annotationRemoved.connect(callbacks["annot_removed"])
            pw.pageInteracted.connect(callbacks["page_interacted"])
            self.pages_layout.addWidget(pw)
            self.page_widgets.append(pw)

        self._apply_search_highlights()

    def refresh_all_pages(self) -> None:
        if self.zoom_mode in ("Fit Width", "Fit Page"):
            self.zoom = self.calculate_fit_zoom()
        for pw in self.page_widgets:
            pw.set_view_params(self.zoom, self.rotation)

    def _apply_search_highlights(self) -> None:
        by_page: dict[int, list[tuple[int, SearchHit]]] = {}
        for g_idx, hit in enumerate(self.search_hits):
            by_page.setdefault(hit.page_idx, []).append((g_idx, hit))
        for pw in self.page_widgets:
            items = by_page.get(pw.page_idx, [])
            pw.search_hits = [it[1] for it in items]
            pw.active_hit_idx = next(
                (local_i for local_i, (g_i, _) in enumerate(items) if g_i == self.search_idx), -1
            )
            pw.update()

    def scroll_to_page(self, page_idx: int) -> None:
        if 0 <= page_idx < len(self.page_widgets):
            self.current_page = page_idx
            self.ensureWidgetVisible(self.page_widgets[page_idx], 0, 20)

    def commit_any_active_editor(self) -> None:
        for pw in self.page_widgets:
            if pw.active_editor is not None:
                pw.commit_active_editor()

    def wheelEvent(self, event) -> None:
        if event.modifiers() & Qt.ControlModifier:
            win = self.window()
            if hasattr(win, "step_zoom"):
                win.step_zoom(1.1 if event.angleDelta().y() > 0 else 0.9)
                event.accept()
                return
        super().wheelEvent(event)


class MainWindow(QMainWindow):
    """Native Windows Personal PDF Suite main window (Acrobat-style compact layout)."""

    def __init__(self, initial_file: str | None = None):
        super().__init__()
        self.setWindowTitle("Personal PDF Suite — Windows Edition")
        self.resize(1320, 860)
        self.setAcceptDrops(True)

        icon_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "assets",
            "app-icon.ico",
        )
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))

        self.dark_theme = False
        self.accent_color = "#ea2e3f"
        self.accent_hover = "#d22333"

        self.tool_state = {
            "tool": "select",
            "color": "#ef4444",
            "width": 3.0,
            "opacity": 1.0,
            "font_family": "Calibri",
            "font_size": 12,
        }
        self._active_editor: InPlaceTextEditor | None = None
        self._active_editor_page: PDFPageWidget | None = None
        self._right_rail_buttons: list[tuple[QPushButton, str]] = []

        self._build_ui()
        self._bind_shortcuts()
        self._apply_theme()

        if initial_file and os.path.isfile(initial_file):
            self.open_pdf_path(initial_file)

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Single compact top header + optional contextual sub-bar
        self.ribbon = RibbonHeader(self)
        root.addWidget(self.ribbon)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)

        # Left Acrobat-style All-Tools vertical rail
        self.left_rail = LeftToolsRail(self)
        body.addWidget(self.left_rail)

        # Center area: Welcome Screen + Stacked Document Views (tabs live inside top bar!)
        self.center_host = QWidget()
        center_v = QVBoxLayout(self.center_host)
        center_v.setContentsMargins(0, 0, 0, 0)
        center_v.setSpacing(0)

        self.welcome_widget = self._build_welcome_widget()
        center_v.addWidget(self.welcome_widget)

        self.doc_stack = QStackedWidget()
        self.doc_stack.hide()
        center_v.addWidget(self.doc_stack)

        # Keep `.tabs` reference pointed to the inline tab bar for clean compatibility
        self.tabs: QTabBar = self.ribbon.inline_tabs
        self.tabs.tabCloseRequested.connect(self._close_tab)
        self.tabs.currentChanged.connect(self._on_tab_changed)

        body.addWidget(self.center_host, stretch=1)

        # Right collapsible Sidebar (Pages / Outline / Notes) + Acrobat-style Right Icon Rail
        self.sidebar = SidebarPanel(self)
        self.sidebar.hide()  # Hidden by default to maximize document area; toggled via right rail
        body.addWidget(self.sidebar)

        self.right_rail = self._build_right_nav_rail()
        body.addWidget(self.right_rail)

        root.addLayout(body, stretch=1)

        self.floating_bar = FloatingFormatBar(self)
        self.floating_bar.fontChanged.connect(lambda f: self._active_editor and self._active_editor.set_font_family(f))
        self.floating_bar.sizeChanged.connect(lambda s: self._active_editor and self._active_editor.set_font_size_pt(s))
        self.floating_bar.boldToggled.connect(lambda b: self._active_editor and self._active_editor.toggle_bold(b))
        self.floating_bar.italicToggled.connect(lambda i: self._active_editor and self._active_editor.toggle_italic(i))
        self.floating_bar.underlineToggled.connect(lambda u: self._active_editor and self._active_editor.toggle_underline(u))
        self.floating_bar.strikeToggled.connect(lambda s: self._active_editor and self._active_editor.toggle_strike(s))
        self.floating_bar.alignChanged.connect(lambda a: self._active_editor and self._active_editor.set_alignment(a))
        self.floating_bar.colorChanged.connect(lambda c: self._active_editor and self._active_editor.set_text_color(c))
        self.floating_bar.reflowToggled.connect(self._on_reflow_toggled)
        self.floating_bar.revertRequested.connect(lambda: self._active_editor_page and self._active_editor_page.cancel_active_editor())
        self.floating_bar.doneRequested.connect(lambda: self._active_editor_page and self._active_editor_page.commit_active_editor())

        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("Ready — Open a PDF or load the Sample PDF")

        # Wire header & left rail signals
        self.left_rail.modeSelected.connect(self.set_mode)
        self.left_rail.toolAction.connect(self._on_tool_action)
        self.ribbon.openClicked.connect(self.open_file_dialog)
        self.ribbon.saveClicked.connect(self.save_current_pdf)
        self.ribbon.saveAsClicked.connect(self.save_as_current_pdf)
        self.ribbon.printClicked.connect(self.print_current_pdf)
        self.ribbon.themeToggled.connect(self.toggle_theme)
        self.ribbon.accentChanged.connect(self.set_accent_theme)
        self.ribbon.propsClicked.connect(self.open_metadata_dialog)
        self.ribbon.undoClicked.connect(self.undo)
        self.ribbon.redoClicked.connect(self.redo)
        self.ribbon.prevPageClicked.connect(lambda: self.jump_page_delta(-1))
        self.ribbon.nextPageClicked.connect(lambda: self.jump_page_delta(1))
        self.ribbon.pageJumpRequested.connect(self.go_to_page)
        self.ribbon.zoomOutClicked.connect(lambda: self.step_zoom(0.85))
        self.ribbon.zoomInClicked.connect(lambda: self.step_zoom(1.15))
        self.ribbon.zoomModeChanged.connect(self.set_zoom_mode)
        self.ribbon.rotateCWClicked.connect(self.rotate_clockwise)
        self.ribbon.searchChanged.connect(self.run_search)
        self.ribbon.searchPrevClicked.connect(lambda: self.step_search(-1))
        self.ribbon.searchNextClicked.connect(lambda: self.step_search(1))
        self.ribbon.modeChanged.connect(self._on_mode_changed)
        self.ribbon.toolAction.connect(self._on_tool_action)

        self.sidebar.pageSelected.connect(self.go_to_page)
        self.sidebar.bookmarkAdded.connect(lambda t, p: self.status_bar.showMessage(f"Bookmark added: {t} (Page {p + 1})"))

    def _build_right_nav_rail(self) -> QFrame:
        rail = QFrame()
        rail.setObjectName("RightNavRail")
        rail.setFixedWidth(40)
        v = QVBoxLayout(rail)
        v.setContentsMargins(4, 8, 4, 8)
        v.setSpacing(6)

        for icon_name, tip, tab_idx in [
            ("pages", "Page Thumbnails", 0),
            ("bookmark", "Bookmarks & Outline", 1),
            ("notes", "Annotations & Edits", 2),
        ]:
            btn = QPushButton()
            btn.setFixedSize(32, 32)
            btn.setToolTip(tip)
            btn.clicked.connect(lambda _=False, ti=tab_idx: self._toggle_sidebar_tab(ti))
            self._right_rail_buttons.append((btn, icon_name))
            v.addWidget(btn)

        v.addStretch()

        btn_theme = QPushButton()
        btn_theme.setFixedSize(32, 32)
        btn_theme.setToolTip("Toggle Light / Dark Theme")
        btn_theme.clicked.connect(self.toggle_theme)
        self._right_rail_buttons.append((btn_theme, "theme"))
        v.addWidget(btn_theme)
        return rail

    def _toggle_sidebar_tab(self, tab_idx: int) -> None:
        if self.sidebar.isVisible() and self.sidebar.tabs.currentIndex() == tab_idx:
            self.sidebar.hide()
        else:
            self.sidebar.tabs.setCurrentIndex(tab_idx)
            self.sidebar.show()

    def _build_welcome_widget(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setAlignment(Qt.AlignCenter)
        title = QLabel("Personal PDF Suite — Windows Edition")
        title.setStyleSheet("font-size: 24px; font-weight: 700;")
        title.setAlignment(Qt.AlignCenter)
        sub = QLabel("Native Vector PDF Editing, Reflow, Annotations, Signatures, Redaction & Organization")
        sub.setAlignment(Qt.AlignCenter)
        sub.setStyleSheet("font-size: 13px; color: #64748b; margin-bottom: 14px;")

        row = QHBoxLayout()
        row.setAlignment(Qt.AlignCenter)
        btn_open = QPushButton("Open PDF Document")
        btn_open.setObjectName("PrimaryBtn")
        btn_open.setMinimumHeight(38)
        btn_open.clicked.connect(self.open_file_dialog)
        btn_sample = QPushButton("Load Sample PDF")
        btn_sample.setObjectName("PillBtn")
        btn_sample.setMinimumHeight(38)
        btn_sample.clicked.connect(self.load_sample_pdf)
        row.addWidget(btn_open)
        row.addWidget(btn_sample)

        hint = QLabel("Tip: You can also drag and drop a PDF file anywhere into this window.")
        hint.setAlignment(Qt.AlignCenter)
        hint.setStyleSheet("font-size: 12px; color: #64748b; margin-top: 10px;")

        v.addWidget(title)
        v.addWidget(sub)
        v.addLayout(row)
        v.addWidget(hint)
        return w

    def _bind_shortcuts(self) -> None:
        QShortcut(QKeySequence("Ctrl+O"), self, activated=self.open_file_dialog)
        QShortcut(QKeySequence("Ctrl+S"), self, activated=self.save_current_pdf)
        QShortcut(QKeySequence("Ctrl+Shift+S"), self, activated=self.save_as_current_pdf)
        QShortcut(QKeySequence("Ctrl+P"), self, activated=self.print_current_pdf)
        QShortcut(QKeySequence("Ctrl+F"), self, activated=lambda: self.ribbon.search_input.setFocus())
        QShortcut(QKeySequence("Ctrl+Z"), self, activated=self.undo)
        QShortcut(QKeySequence("Ctrl+Y"), self, activated=self.redo)
        QShortcut(QKeySequence("Ctrl+="), self, activated=lambda: self.step_zoom(1.15))
        QShortcut(QKeySequence("Ctrl+-"), self, activated=lambda: self.step_zoom(0.85))

    def _apply_theme(self) -> None:
        self.setStyleSheet(build_stylesheet(self.dark_theme, self.accent_color, self.accent_hover))
        self.ribbon.refresh_icon_theme(self.dark_theme)
        self.left_rail.refresh_icon_theme(self.dark_theme)
        col = "#e2e8f0" if self.dark_theme else "#334155"
        for btn, icon_name in self._right_rail_buttons:
            btn.setIcon(make_icon(icon_name, col, 17))
            btn.setIconSize(QSize(17, 17))

    def set_mode(self, mode: str) -> None:
        self.left_rail.set_active_mode(mode)
        self.ribbon.set_mode(mode)

    def toggle_theme(self) -> None:
        self.dark_theme = not self.dark_theme
        self._apply_theme()
        self.status_bar.showMessage("Dark theme active" if self.dark_theme else "Light theme active")

    def set_accent_theme(self, color: str, hover: str) -> None:
        self.accent_color = color
        self.accent_hover = hover
        self._apply_theme()

    def current_tab(self) -> DocumentTab | None:
        w = self.doc_stack.currentWidget()
        return w if isinstance(w, DocumentTab) else None

    def open_file_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open PDF Document", "", "PDF Files (*.pdf)")
        if path:
            self.open_pdf_path(path)

    def open_pdf_path(self, path: str) -> DocumentTab | None:
        try:
            with open(path, "rb") as f:
                data = f.read()
        except OSError as err:
            QMessageBox.critical(self, "Open PDF", f"Could not read file:\n{err}")
            return None
        return self.open_pdf_bytes(data, os.path.basename(path), file_path=path)

    def open_pdf_bytes(self, data: bytes, name: str, file_path: str | None = None) -> DocumentTab | None:
        engine = PDFDocumentEngine(data, name=name, file_path=file_path)
        while engine.needs_password:
            pw, ok = QInputDialog.getText(
                self, "Encrypted PDF", f"Enter password for {name}:", echo=QLineEdit_Password()
            )
            if not ok:
                return None
            if not engine.authenticate(pw):
                QMessageBox.warning(self, "Incorrect Password", "The password you entered is incorrect.")

        tab = DocumentTab(engine, self.accent_color, self)
        tab.verticalScrollBar().valueChanged.connect(lambda _v, t=tab: self._on_scroll(t))
        self.welcome_widget.hide()
        self.doc_stack.show()
        self.doc_stack.addWidget(tab)
        idx = self.tabs.addTab(name)
        self.tabs.setTabToolTip(idx, file_path or name)
        self.tabs.setCurrentIndex(idx)
        self.doc_stack.setCurrentWidget(tab)
        self._rebuild_tab_pages(tab)
        self.sidebar.set_engine(engine, 0)
        self.ribbon.update_page_info(0, engine.page_count)
        self.status_bar.showMessage(f"Opened {name} ({engine.page_count} pages)")
        return tab

    def load_sample_pdf(self) -> DocumentTab | None:
        return self.open_pdf_bytes(create_sample_pdf_bytes(), "Sample_Document.pdf")

    def _callbacks_for(self, tab: DocumentTab) -> dict:
        return {
            "editor_opened": lambda pw, ed: self._on_editor_opened(tab, pw, ed),
            "edit_committed": lambda pno, op: self._on_in_place_edit_committed(tab, pno, op),
            "annot_added": lambda pno, kind, item: self._on_annot_added(tab, pno, kind, item),
            "annot_removed": lambda pno, kind, item: self._on_annot_removed(tab, pno, kind, item),
            "page_interacted": lambda pno: self._on_page_interacted(tab, pno),
        }

    def _rebuild_tab_pages(self, tab: DocumentTab) -> None:
        tab.rebuild_pages(self.tool_state, self._callbacks_for(tab))
        self.ribbon.update_page_info(tab.current_page, tab.engine.page_count)

    def _on_tab_changed(self, idx: int) -> None:
        self._hide_floating_bar()
        if idx < 0 or idx >= self.doc_stack.count():
            self.sidebar.set_engine(None)
            self.ribbon.update_page_info(0, 0)
            self.doc_stack.hide()
            self.welcome_widget.show()
            return
        self.doc_stack.setCurrentIndex(idx)
        tab = self.current_tab()
        if tab is None:
            return
        self.sidebar.set_engine(tab.engine, tab.current_page)
        self.ribbon.update_page_info(tab.current_page, tab.engine.page_count)

    def _close_tab(self, idx: int) -> None:
        if idx < 0 or idx >= self.doc_stack.count():
            return
        w = self.doc_stack.widget(idx)
        self.doc_stack.removeWidget(w)
        self.tabs.removeTab(idx)
        if w:
            w.deleteLater()
        if self.tabs.count() == 0:
            self._hide_floating_bar()
            self.sidebar.set_engine(None)
            self.ribbon.update_page_info(0, 0)
            self.doc_stack.hide()
            self.welcome_widget.show()

    def _on_scroll(self, tab: DocumentTab) -> None:
        if tab is not self.current_tab() or not tab.page_widgets:
            return
        if self._active_editor is not None:
            self._position_floating_bar()
        v_top = tab.verticalScrollBar().value()
        v_mid = v_top + tab.viewport().height() // 3
        best = tab.current_page
        for pw in tab.page_widgets:
            y = pw.y()
            if y <= v_mid <= y + pw.height():
                best = pw.page_idx
                break
        if best != tab.current_page:
            tab.current_page = best
            self.ribbon.update_page_info(best, tab.engine.page_count)
            self.sidebar.set_current_page(best)

    def _on_page_interacted(self, tab: DocumentTab, page_idx: int) -> None:
        if tab.current_page != page_idx:
            tab.current_page = page_idx
            self.ribbon.update_page_info(page_idx, tab.engine.page_count)
            self.sidebar.set_current_page(page_idx)

    def _on_editor_opened(self, tab: DocumentTab, page_widget: PDFPageWidget, editor: InPlaceTextEditor) -> None:
        self._active_editor = editor
        self._active_editor_page = page_widget
        editor.formatStateChanged.connect(self._sync_floating_bar)
        editor.heightChanged.connect(lambda _h: self._position_floating_bar())
        self._sync_floating_bar()
        self._position_floating_bar()
        self.floating_bar.show()
        self.floating_bar.raise_()

    def _sync_floating_bar(self) -> None:
        ed = self._active_editor
        if ed is None:
            return
        st = ed.current_format_state()
        self.floating_bar.sync_from_state(
            font_family=st["font_family"],
            size_pt=st["size_pt"],
            bold=st["bold"],
            italic=st["italic"],
            underline=st["underline"],
            strike=st["strike"],
            color_hex=st["color"],
            reflow_enabled=ed.reflow_enabled,
        )

    def _position_floating_bar(self) -> None:
        ed = self._active_editor
        if ed is None or not ed.isVisible():
            return
        top_left = ed.mapTo(self, QPoint(0, 0))
        bar_w = self.floating_bar.sizeHint().width()
        bar_h = self.floating_bar.sizeHint().height()
        x = max(8, min(self.width() - bar_w - 8, top_left.x() + (ed.width() - bar_w) // 2))
        y = top_left.y() - bar_h - 6
        if y < 50:
            y = top_left.y() + ed.height() + 6
        self.floating_bar.move(x, y)

    def _hide_floating_bar(self) -> None:
        self._active_editor = None
        self._active_editor_page = None
        self.floating_bar.hide()

    def _on_reflow_toggled(self, enabled: bool) -> None:
        if self._active_editor is not None:
            self._active_editor.reflow_enabled = enabled

    def _on_in_place_edit_committed(self, tab: DocumentTab, page_idx: int, op: TextEditOp) -> None:
        self._hide_floating_bar()
        prev = tab.engine.in_place_edits.get(page_idx, {}).get(op.block_id)
        tab.undo_stack.append(_UndoEntry(kind="in_place_edit", page_idx=page_idx, payload=op, previous_payload=prev))
        tab.redo_stack.clear()
        tab.engine.set_in_place_edit(op)
        self._rebuild_tab_pages(tab)
        self.sidebar.refresh_annotations()
        self.sidebar.refresh_thumbnails()
        self.status_bar.showMessage(f"Vector text edit applied on Page {page_idx + 1}")

    def _on_annot_added(self, tab: DocumentTab, page_idx: int, kind: str, item: object) -> None:
        tab.undo_stack.append(_UndoEntry(kind=kind, page_idx=page_idx, payload=item))
        tab.redo_stack.clear()
        self.sidebar.refresh_annotations()

    def _on_annot_removed(self, tab: DocumentTab, page_idx: int, kind: str, item: object) -> None:
        self.sidebar.refresh_annotations()

    def undo(self) -> None:
        tab = self.current_tab()
        if tab is None or not tab.undo_stack:
            return
        tab.commit_any_active_editor()
        entry = tab.undo_stack.pop()
        tab.redo_stack.append(entry)
        if entry.kind == "in_place_edit":
            op: TextEditOp = entry.payload
            if entry.previous_payload is not None:
                tab.engine.set_in_place_edit(entry.previous_payload)
            else:
                tab.engine.remove_in_place_edit(entry.page_idx, op.block_id)
            self._rebuild_tab_pages(tab)
        else:
            store = self._store_for_kind(tab.engine, entry.kind)
            lst = store.get(entry.page_idx, [])
            if entry.payload in lst:
                lst.remove(entry.payload)
            if entry.page_idx < len(tab.page_widgets):
                tab.page_widgets[entry.page_idx].update()
        self.sidebar.refresh_annotations()
        self.status_bar.showMessage("Undone")

    def redo(self) -> None:
        tab = self.current_tab()
        if tab is None or not tab.redo_stack:
            return
        entry = tab.redo_stack.pop()
        tab.undo_stack.append(entry)
        if entry.kind == "in_place_edit":
            tab.engine.set_in_place_edit(entry.payload)
            self._rebuild_tab_pages(tab)
        else:
            store = self._store_for_kind(tab.engine, entry.kind)
            store.setdefault(entry.page_idx, []).append(entry.payload)
            if entry.page_idx < len(tab.page_widgets):
                tab.page_widgets[entry.page_idx].update()
        self.sidebar.refresh_annotations()
        self.status_bar.showMessage("Redone")

    def _store_for_kind(self, eng: PDFDocumentEngine, kind: str) -> dict:
        return {
            "ink": eng.ink_strokes,
            "shape": eng.shapes,
            "markup": eng.markups,
            "note": eng.sticky_notes,
            "textbox": eng.text_boxes,
            "stamp": eng.stamps,
            "whiteout": eng.whiteouts,
            "redact": eng.redactions,
        }[kind]

    def _on_mode_changed(self, mode: str) -> None:
        tab = self.current_tab()
        if tab:
            tab.commit_any_active_editor()
        self._hide_floating_bar()
        self.left_rail.set_active_mode(mode)
        if mode == "view":
            self._set_active_tool("select")
        elif mode == "annotate":
            self._set_active_tool("pen")
        elif mode == "edit":
            self._set_active_tool("edit_text")
        else:
            self._set_active_tool("select")

    def _set_active_tool(self, tool: str) -> None:
        self.tool_state["tool"] = tool
        if tool == "highlighter":
            self.tool_state["color"] = "#eab308"
            self.tool_state["width"] = 14.0
            self.tool_state["opacity"] = 0.35
        elif tool == "pen":
            self.tool_state["width"] = 3.0
            self.tool_state["opacity"] = 1.0
        tab = self.current_tab()
        if tab:
            for pw in tab.page_widgets:
                pw.set_tool_state(**self.tool_state)

    def _on_tool_action(self, action: str, extra: dict) -> None:
        tab = self.current_tab()
        if action.startswith("tool:"):
            self._set_active_tool(action.split(":", 1)[1])
            return
        if tab is None:
            QMessageBox.information(self, "Personal PDF Suite", "Please open a PDF document first.")
            return

        if action == "copy_selection":
            texts = [pw.get_selected_text() for pw in tab.page_widgets if pw.get_selected_text()]
            joined = "\n".join(texts).strip()
            if joined:
                QApplication.clipboard().setText(heal_pdf_text(joined))
                self.status_bar.showMessage(f"Copied {len(joined)} characters to clipboard")
            else:
                self.status_bar.showMessage("Select text on a page first to copy")

        elif action == "pick_color":
            from PySide6.QtGui import QColor
            from PySide6.QtWidgets import QColorDialog

            col = QColorDialog.getColor(QColor(self.tool_state["color"]), self, "Pick Tool Color")
            if col.isValid():
                self.tool_state["color"] = col.name()
                for pw in tab.page_widgets:
                    pw.set_tool_state(**self.tool_state)

        elif action == "place_stamp":
            label = extra.get("label", "APPROVED")
            if label == "Custom...":
                txt, ok = QInputDialog.getText(self, "Custom Stamp", "Stamp text:", text="REVIEWED")
                if not ok or not txt.strip():
                    return
                label = txt.strip().upper()
            pw_pt, ph_pt = tab.engine.get_page_size(tab.current_page)
            cx, cy = pw_pt / 2.0, ph_pt / 2.0
            stp = StampItem(
                page_idx=tab.current_page,
                kind="stamp",
                rect=(cx - 90, cy - 26, cx + 90, cy + 26),
                text=label,
                color_hex="#dc2626",
            )
            tab.engine.stamps.setdefault(tab.current_page, []).append(stp)
            self._on_annot_added(tab, tab.current_page, "stamp", stp)
            tab.page_widgets[tab.current_page].update()

        elif action == "insert_image":
            path, _ = QFileDialog.getOpenFileName(self, "Insert Image", "", "Images (*.png *.jpg *.jpeg)")
            if path:
                with open(path, "rb") as f:
                    img_bytes = f.read()
                pw_pt, ph_pt = tab.engine.get_page_size(tab.current_page)
                stp = StampItem(
                    page_idx=tab.current_page,
                    kind="image",
                    rect=(pw_pt / 2 - 90, ph_pt / 2 - 60, pw_pt / 2 + 90, ph_pt / 2 + 60),
                    opacity=1.0,
                    rotation=0.0,
                    image_bytes=img_bytes,
                )
                tab.engine.stamps.setdefault(tab.current_page, []).append(stp)
                self._on_annot_added(tab, tab.current_page, "stamp", stp)
                tab.page_widgets[tab.current_page].update()

        elif action == "open_signature":
            dlg = SignatureDialog(self)
            if dlg.exec() and dlg.result_png_bytes:
                pw_pt, ph_pt = tab.engine.get_page_size(tab.current_page)
                stp = StampItem(
                    page_idx=tab.current_page,
                    kind="image",
                    rect=(pw_pt / 2 - 95, ph_pt / 2 - 38, pw_pt / 2 + 95, ph_pt / 2 + 38),
                    opacity=1.0,
                    rotation=0.0,
                    image_bytes=dlg.result_png_bytes,
                )
                tab.engine.stamps.setdefault(tab.current_page, []).append(stp)
                self._on_annot_added(tab, tab.current_page, "stamp", stp)
                tab.page_widgets[tab.current_page].update()

        elif action == "flatten_forms":
            cur_bytes = tab.engine.build_final_pdf_bytes()
            new_bytes, cnt = flatten_form_fields(cur_bytes)
            if cnt == 0:
                QMessageBox.information(self, "Flatten Form", "No interactive form fields found in this document.")
            else:
                self._replace_tab_document(tab, new_bytes)
                QMessageBox.information(self, "Flatten Form", f"Flattened {cnt} form field(s) permanently.")

        elif action == "open_organizer":
            cur_bytes = tab.engine.build_final_pdf_bytes()
            dlg = OrganizerDialog(cur_bytes, self)
            if dlg.exec():
                if dlg.extract_indices is not None:
                    extracted = organize_pages(cur_bytes, [PageOp(original_index=i) for i in dlg.extract_indices])
                    self.open_pdf_bytes(extracted, f"Extracted_{tab.engine.name}")
                else:
                    new_bytes = organize_pages(cur_bytes, dlg.get_operations())
                    self._replace_tab_document(tab, new_bytes)

        elif action == "insert_blank":
            cur_bytes = tab.engine.build_final_pdf_bytes()
            ops = [PageOp(original_index=i) for i in range(tab.engine.page_count)]
            ops.insert(tab.current_page + 1, PageOp(original_index=None))
            self._replace_tab_document(tab, organize_pages(cur_bytes, ops))

        elif action == "rotate_cw":
            self.rotate_clockwise()

        elif action == "open_merge":
            dlg = MergeDialog(tab.engine.name, self)
            if dlg.exec():
                parts = []
                if dlg.chk_include_current and dlg.chk_include_current.isChecked():
                    parts.append(tab.engine.build_final_pdf_bytes())
                for fp in dlg.file_paths:
                    with open(fp, "rb") as f:
                        parts.append(f.read())
                if parts:
                    merged = merge_pdfs(parts)
                    self.open_pdf_bytes(merged, "Merged_Document.pdf")

        elif action == "open_split":
            cur_bytes = tab.engine.build_final_pdf_bytes()
            dlg = SplitDialog(tab.engine.page_count, self)
            if dlg.exec():
                out_dir = QFileDialog.getExistingDirectory(self, "Select Output Folder for Split PDFs")
                if not out_dir:
                    return
                results = split_pdf_by_ranges(cur_bytes, dlg.ranges_input.text(), tab.engine.name)
                for fname, fbytes in results:
                    write_bytes_atomic(os.path.join(out_dir, fname), fbytes)
                QMessageBox.information(
                    self, "Split PDF", f"Saved {len(results)} split PDF file(s) to:\n{out_dir}"
                )

        elif action == "open_watermark":
            dlg = WatermarkDialog(self)
            if dlg.exec():
                cur_bytes = tab.engine.build_final_pdf_bytes()
                new_bytes = apply_watermark(
                    cur_bytes,
                    text=dlg.txt.text(),
                    angle=int(dlg.angle.currentText()),
                    opacity=dlg.opacity.value() / 100.0,
                    font_size=dlg.size.value(),
                    color_hex=dlg.color_hex,
                )
                self._replace_tab_document(tab, new_bytes)

        elif action == "open_bates":
            dlg = BatesDialog(self)
            if dlg.exec():
                cur_bytes = tab.engine.build_final_pdf_bytes()
                new_bytes = apply_bates_numbering(
                    cur_bytes,
                    fmt=dlg.fmt.currentText(),
                    position=dlg.pos.currentText(),
                    start_number=dlg.start.value(),
                    prefix=dlg.prefix.text(),
                )
                self._replace_tab_document(tab, new_bytes)

        elif action == "open_protect":
            dlg = ProtectDialog(self)
            if dlg.exec():
                path, _ = QFileDialog.getSaveFileName(
                    self, "Save Encrypted PDF", f"Protected_{tab.engine.name}", "PDF Files (*.pdf)"
                )
                if path:
                    enc_bytes = encrypt_pdf_aes256(
                        tab.engine.build_final_pdf_bytes(),
                        dlg.pw1.text(),
                        allow_print=dlg.allow_print.isChecked(),
                        allow_copy=dlg.allow_copy.isChecked(),
                    )
                    write_bytes_atomic(path, enc_bytes)
                    self.status_bar.showMessage(f"Saved AES-256 encrypted PDF: {path}")

        elif action == "open_word_flow":
            tab.commit_any_active_editor()
            blocks_html = []
            for pno in range(tab.engine.page_count):
                for blk in tab.engine.extract_text_blocks(pno):
                    blocks_html.append(blk.html)
            dlg = WordFlowDialog("".join(blocks_html) or "<p>Type document here...</p>", self)
            if dlg.exec():
                pw_pt, ph_pt = tab.engine.get_page_size(0)
                new_pdf = build_pdf_from_word_flow_html(dlg.get_html(), page_size=(pw_pt, ph_pt))
                self._replace_tab_document(tab, new_pdf)

        elif action == "export_png":
            cur_bytes = tab.engine.build_final_pdf_bytes()
            path, _ = QFileDialog.getSaveFileName(
                self, "Export Page as PNG", f"page_{tab.current_page + 1}.png", "PNG Image (*.png)"
            )
            if path:
                write_bytes_atomic(path, export_page_image(cur_bytes, tab.current_page, dpi=200, fmt="png"))
                self.status_bar.showMessage(f"Exported page {tab.current_page + 1} to {path}")

        elif action == "export_all_png":
            out_dir = QFileDialog.getExistingDirectory(self, "Select Output Folder for Page Images")
            if out_dir:
                cur_bytes = tab.engine.build_final_pdf_bytes()
                for pno in range(tab.engine.page_count):
                    write_bytes_atomic(
                        os.path.join(out_dir, f"page_{pno + 1}.png"),
                        export_page_image(cur_bytes, pno, dpi=200, fmt="png"),
                    )
                QMessageBox.information(self, "Export Complete", f"Exported {tab.engine.page_count} PNG file(s).")

        elif action == "export_text":
            cur_bytes = tab.engine.build_final_pdf_bytes()
            base = os.path.splitext(tab.engine.name)[0]
            path, _ = QFileDialog.getSaveFileName(self, "Export Text", f"{base}.txt", "Text Files (*.txt)")
            if path:
                write_bytes_atomic(path, extract_clean_text(cur_bytes).encode("utf-8"))
                self.status_bar.showMessage(f"Extracted clean text to {path}")

    def _replace_tab_document(self, tab: DocumentTab, new_bytes: bytes) -> None:
        old_path = tab.engine.file_path
        old_name = tab.engine.name
        tab.engine = PDFDocumentEngine(new_bytes, name=old_name, file_path=old_path)
        tab.undo_stack.clear()
        tab.redo_stack.clear()
        self._rebuild_tab_pages(tab)
        self.sidebar.set_engine(tab.engine, min(tab.current_page, max(0, tab.engine.page_count - 1)))

    def open_metadata_dialog(self) -> None:
        tab = self.current_tab()
        if not tab:
            return
        cur_bytes = tab.engine.build_final_pdf_bytes()
        dlg = MetadataDialog(get_metadata(cur_bytes), self)
        if dlg.exec():
            new_bytes = update_metadata(
                cur_bytes,
                {
                    "title": dlg.title.text(),
                    "author": dlg.author.text(),
                    "subject": dlg.subject.text(),
                    "keywords": dlg.keywords.text(),
                },
            )
            self._replace_tab_document(tab, new_bytes)

    def save_current_pdf(self) -> None:
        tab = self.current_tab()
        if not tab:
            return
        tab.commit_any_active_editor()
        if not tab.engine.file_path:
            self.save_as_current_pdf()
            return
        out_bytes = tab.engine.build_final_pdf_bytes()
        write_bytes_atomic(tab.engine.file_path, out_bytes)
        self._replace_tab_document(tab, out_bytes)
        self.status_bar.showMessage(f"Saved {tab.engine.file_path}")

    def save_as_current_pdf(self) -> None:
        tab = self.current_tab()
        if not tab:
            return
        tab.commit_any_active_editor()
        suggested = tab.engine.file_path or tab.engine.name
        path, _ = QFileDialog.getSaveFileName(self, "Save PDF As", suggested, "PDF Files (*.pdf)")
        if not path:
            return
        if not path.lower().endswith(".pdf"):
            path += ".pdf"
        out_bytes = tab.engine.build_final_pdf_bytes()
        write_bytes_atomic(path, out_bytes)
        tab.engine.file_path = path
        tab.engine.name = os.path.basename(path)
        self.tabs.setTabText(self.tabs.currentIndex(), tab.engine.name)
        self._replace_tab_document(tab, out_bytes)
        self.status_bar.showMessage(f"Saved as {path}")

    def print_current_pdf(self) -> None:
        tab = self.current_tab()
        if not tab:
            return
        tab.commit_any_active_editor()
        printer = QPrinter(QPrinter.HighResolution)
        dlg = QPrintDialog(printer, self)
        if not dlg.exec():
            return
        final_bytes = tab.engine.build_final_pdf_bytes()
        doc = pymupdf.open(stream=final_bytes, filetype="pdf")
        painter = QPainter(printer)
        try:
            page_rect = printer.pageRect(QPrinter.DevicePixel)
            for idx, page in enumerate(doc):
                if idx > 0:
                    printer.newPage()
                pix = page.get_pixmap(dpi=200, alpha=False)
                img = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format_RGB888).copy()
                scaled = img.scaled(
                    int(page_rect.width()),
                    int(page_rect.height()),
                    Qt.KeepAspectRatio,
                    Qt.SmoothTransformation,
                )
                painter.drawImage(0, 0, scaled)
        finally:
            painter.end()
            doc.close()

    def jump_page_delta(self, delta: int) -> None:
        tab = self.current_tab()
        if tab:
            self.go_to_page(max(0, min(tab.engine.page_count - 1, tab.current_page + delta)))

    def go_to_page(self, page_idx: int) -> None:
        tab = self.current_tab()
        if not tab or tab.engine.page_count == 0:
            return
        target = max(0, min(tab.engine.page_count - 1, page_idx))
        tab.scroll_to_page(target)
        self.ribbon.update_page_info(target, tab.engine.page_count)
        self.sidebar.set_current_page(target)

    def set_zoom_mode(self, label: str) -> None:
        tab = self.current_tab()
        if not tab:
            return
        if label in ("Fit Width", "Fit Page"):
            tab.zoom_mode = label
        elif label.endswith("%"):
            tab.zoom_mode = "custom"
            tab.zoom = max(0.25, min(4.0, int(label[:-1]) / 100.0))
        tab.refresh_all_pages()

    def step_zoom(self, factor: float) -> None:
        tab = self.current_tab()
        if not tab:
            return
        tab.zoom_mode = "custom"
        tab.zoom = max(0.25, min(4.0, tab.zoom * factor))
        tab.refresh_all_pages()

    def rotate_clockwise(self) -> None:
        tab = self.current_tab()
        if not tab:
            return
        tab.rotation = (tab.rotation + 90) % 360
        tab.refresh_all_pages()

    def run_search(self, query: str) -> None:
        tab = self.current_tab()
        if not tab:
            return
        tab.search_hits = tab.engine.search_text(query)
        tab.search_idx = 0 if tab.search_hits else -1
        tab._apply_search_highlights()
        self._update_search_label(tab)
        if tab.search_hits:
            self.go_to_page(tab.search_hits[0].page_idx)

    def step_search(self, delta: int) -> None:
        tab = self.current_tab()
        if not tab or not tab.search_hits:
            return
        tab.search_idx = (tab.search_idx + delta) % len(tab.search_hits)
        tab._apply_search_highlights()
        self._update_search_label(tab)
        self.go_to_page(tab.search_hits[tab.search_idx].page_idx)

    def _update_search_label(self, tab: DocumentTab) -> None:
        total = len(tab.search_hits)
        cur = (tab.search_idx + 1) if total > 0 and tab.search_idx >= 0 else 0
        self.ribbon.lbl_search_count.setText(f"{cur}/{total}")

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        tab = self.current_tab()
        if tab and tab.zoom_mode in ("Fit Width", "Fit Page"):
            tab.refresh_all_pages()

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            if any(u.toLocalFile().lower().endswith(".pdf") for u in event.mimeData().urls()):
                event.acceptProposedAction()
                return
        event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if path.lower().endswith(".pdf"):
                self.open_pdf_path(path)


def QLineEdit_Password():
    from PySide6.QtWidgets import QLineEdit
    return QLineEdit.Password
