"""Main application window with Adobe Acrobat-inspired space-saving UI, inline tabs, and native printing."""

from __future__ import annotations

import os
import weakref
from dataclasses import dataclass

import pymupdf
import shiboken6
from PySide6.QtCore import QPoint, QSettings, QSize, Qt, QTimer, QUrl
from PySide6.QtGui import (
    QCloseEvent,
    QDesktopServices,
    QDragEnterEvent,
    QDropEvent,
    QIcon,
    QImage,
    QKeySequence,
    QPainter,
    QShortcut,
)
from PySide6.QtPrintSupport import QPrinter
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QStatusBar,
    QTextEdit,
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
    create_blank_pdf_bytes,
    create_sample_pdf_bytes,
)
from app.ui.dialogs import (
    BatesDialog,
    FindReplaceDialog,
    MergeDialog,
    MetadataDialog,
    OrganizerDialog,
    PdfPrintDialog,
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
    action: str = "add"
    prop_name: str = ""
    new_value: object = None


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
        self._programmatic_scroll: bool = False
        self.dirty: bool = False
        self.base_modified: bool = False

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
        if self.zoom_mode in ("Fit Width", "Fit Page"):
            self.zoom = self.calculate_fit_zoom()

        target_count = self.engine.page_count

        while len(self.page_widgets) > target_count:
            pw = self.page_widgets.pop()
            if shiboken6.isValid(pw):
                if pw.active_editor is not None:
                    pw.cancel_active_editor()
                pw.setParent(None)
                pw.deleteLater()

        for pno, pw in enumerate(self.page_widgets):
            if pw.active_editor is not None:
                pw.cancel_active_editor()
            pw.close_active_form_editor()
            pw.engine = self.engine
            pw.page_idx = pno
            pw.accent_color = self.accent_color
            pw.selected_item = None
            pw._hover_block = None
            pw._hover_widget = None
            pw.clear_text_selection()
            pw.set_tool_state(**tool_state)
            pw.set_view_params(self.zoom, self.rotation, render_now=False)

        for pno in range(len(self.page_widgets), target_count):
            pw = PDFPageWidget(
                engine=self.engine,
                page_idx=pno,
                zoom=self.zoom,
                rotation=self.rotation,
                accent_color=self.accent_color,
                defer_render=True,
                parent=self.pages_container,
            )
            pw.set_tool_state(**tool_state)
            pw.inPlaceEditorOpened.connect(callbacks["editor_opened"])
            pw.inPlaceEditorClosed.connect(callbacks["editor_closed"])
            pw.inPlaceEditCommitted.connect(callbacks["edit_committed"])
            pw.annotationAdded.connect(callbacks["annot_added"])
            pw.annotationRemoved.connect(callbacks["annot_removed"])
            pw.annotationModified.connect(callbacks["annot_modified"])
            pw.linkClicked.connect(callbacks["link_clicked"])
            pw.formFieldEdited.connect(callbacks["form_edited"])
            pw.pageInteracted.connect(callbacks["page_interacted"])
            self.pages_layout.addWidget(pw)
            pw.show()
            self.page_widgets.append(pw)

        self._ensure_scroll_layout()
        self.refresh_visible_pages(buffer_px=0)
        self._apply_search_highlights()

    def refresh_all_pages(self, defer_render: bool = False) -> None:
        if self.zoom_mode in ("Fit Width", "Fit Page"):
            self.zoom = self.calculate_fit_zoom()
        for pw in self.page_widgets:
            pw.set_view_params(self.zoom, self.rotation, render_now=False, defer_paint=defer_render)
        self._ensure_scroll_layout()
        if not defer_render:
            self.refresh_visible_pages(buffer_px=0)

    def refresh_visible_pages(self, buffer_px: int = 200) -> None:
        if not self.page_widgets:
            return
        vbar = self.verticalScrollBar()
        v_top = vbar.value() - buffer_px
        v_bot = vbar.value() + max(200, self.viewport().height()) + buffer_px
        any_rendered = False
        for pw in self.page_widgets:
            y0 = pw.y()
            y1 = y0 + pw.height()
            if y1 >= v_top and y0 <= v_bot:
                pw._defer_paint_render = False
                if pw.needs_render():
                    pw.refresh_render()
                any_rendered = True
        if not any_rendered:
            idx = max(0, min(self.current_page, len(self.page_widgets) - 1))
            pw = self.page_widgets[idx]
            pw._defer_paint_render = False
            if pw.needs_render():
                pw.refresh_render()

    def _apply_search_highlights(self) -> None:
        by_page: dict[int, list[tuple[int, SearchHit]]] = {}
        for g_idx, hit in enumerate(self.search_hits):
            by_page.setdefault(hit.page_idx, []).append((g_idx, hit))
        for pw in self.page_widgets:
            items = by_page.get(pw.page_idx, [])
            new_hits = [it[1] for it in items]
            new_active = next(
                (local_i for local_i, (g_i, _) in enumerate(items) if g_i == self.search_idx), -1
            )
            if pw.search_hits != new_hits or pw.active_hit_idx != new_active:
                pw.search_hits = new_hits
                pw.active_hit_idx = new_active
                pw.update()

    def _ensure_scroll_layout(self) -> None:
        for pw in self.page_widgets:
            if pw.isHidden():
                pw.show()
        self.pages_layout.activate()
        hint = self.pages_layout.sizeHint()
        vw = max(1, self.viewport().width())
        vh = max(1, self.viewport().height())
        target_w = max(vw, hint.width())
        target_h = max(vh, hint.height())
        if self.pages_container.width() != target_w or self.pages_container.height() != target_h:
            self.pages_container.resize(target_w, target_h)
        vbar = self.verticalScrollBar()
        if target_h > vh and vbar.maximum() < (target_h - vh):
            vbar.setRange(0, target_h - vh)
        hbar = self.horizontalScrollBar()
        if target_w > vw and hbar.maximum() < (target_w - vw):
            hbar.setRange(0, target_w - vw)

    def scroll_to_page(self, page_idx: int) -> None:
        if not (0 <= page_idx < len(self.page_widgets)):
            return
        self._ensure_scroll_layout()
        pw = self.page_widgets[page_idx]
        target_y = max(0, pw.y() - 12)
        self._programmatic_scroll = True
        try:
            self.current_page = page_idx
            self.verticalScrollBar().setValue(target_y)
        finally:
            self._programmatic_scroll = False

    def scroll_to_rect(self, page_idx: int, rect: tuple[float, float, float, float]) -> None:
        if not (0 <= page_idx < len(self.page_widgets)):
            return
        self._ensure_scroll_layout()
        pw = self.page_widgets[page_idx]
        hit_qrect = pw._pdf_rect_to_display_qrect(rect)
        vp_h = max(100, self.viewport().height())
        vp_w = max(100, self.viewport().width())
        vbar = self.verticalScrollBar()
        hbar = self.horizontalScrollBar()
        target_y = max(0, min(vbar.maximum(), pw.y() + hit_qrect.center().y() - vp_h // 2))
        target_x = max(0, min(hbar.maximum(), pw.x() + hit_qrect.center().x() - vp_w // 2))
        self._programmatic_scroll = True
        try:
            self.current_page = page_idx
            vbar.setValue(target_y)
            if hbar.maximum() > 0:
                hbar.setValue(target_x)
        finally:
            self._programmatic_scroll = False

    def commit_any_active_editor(self) -> None:
        for pw in list(self.page_widgets):
            if shiboken6.isValid(pw) and pw.active_editor is not None:
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

        self._ink_color = "#ef4444"
        self._ink_width = 3.0
        self._hl_color = "#eab308"
        self._hl_width = 14.0
        self._hl_opacity = 0.35
        self.tool_state = {
            "tool": "select",
            "color": self._ink_color,
            "width": self._ink_width,
            "opacity": 1.0,
            "font_family": "Calibri",
            "font_size": 12,
        }
        self._active_editor: InPlaceTextEditor | None = None
        self._active_editor_page: PDFPageWidget | None = None
        self._find_replace_dlg: FindReplaceDialog | None = None
        self._right_rail_buttons: list[tuple[QPushButton, str]] = []
        self._right_rail_tab_buttons: dict[int, tuple[QPushButton, str]] = {}
        self._prompt_on_close: bool = False
        self._resize_fit_timer = QTimer(self)
        self._resize_fit_timer.setSingleShot(True)
        self._resize_fit_timer.setInterval(55)
        self._resize_fit_timer.timeout.connect(self._flush_deferred_fit_render)

        self._build_ui()
        self._bind_shortcuts()
        self._apply_theme()
        self.ribbon.update_recent_files(self._load_recent_files())

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
        self.tabs.tabCloseRequested.connect(self._request_close_tab)
        self.tabs.currentChanged.connect(self._on_tab_changed)
        self.tabs.tabMoved.connect(self._on_tab_moved)

        body.addWidget(self.center_host, stretch=1)

        # Right collapsible Sidebar (Pages / Outline / Notes) + Acrobat-style Right Icon Rail
        self.sidebar = SidebarPanel(self)
        self.sidebar.hide()  # Hidden by default to maximize document area; toggled via right rail
        body.addWidget(self.sidebar)

        self.right_rail = self._build_right_nav_rail()
        body.addWidget(self.right_rail)

        root.addLayout(body, stretch=1)

        self.floating_bar = FloatingFormatBar(self)
        self.floating_bar.fontChanged.connect(lambda f: self._apply_to_active_editor(lambda ed: ed.set_font_family(f)))
        self.floating_bar.sizeChanged.connect(lambda s: self._apply_to_active_editor(lambda ed: ed.set_font_size_pt(s)))
        self.floating_bar.boldToggled.connect(lambda b: self._apply_to_active_editor(lambda ed: ed.toggle_bold(b)))
        self.floating_bar.italicToggled.connect(lambda i: self._apply_to_active_editor(lambda ed: ed.toggle_italic(i)))
        self.floating_bar.underlineToggled.connect(lambda u: self._apply_to_active_editor(lambda ed: ed.toggle_underline(u)))
        self.floating_bar.strikeToggled.connect(lambda s: self._apply_to_active_editor(lambda ed: ed.toggle_strike(s)))
        self.floating_bar.alignChanged.connect(lambda a: self._apply_to_active_editor(lambda ed: ed.set_alignment(a)))
        self.floating_bar.colorChanged.connect(lambda c: self._apply_to_active_editor(lambda ed: ed.set_text_color(c)))
        self.floating_bar.reflowToggled.connect(self._on_reflow_toggled)
        self.floating_bar.revertRequested.connect(lambda: self._finish_active_editor(commit=False))
        self.floating_bar.doneRequested.connect(lambda: self._finish_active_editor(commit=True))

        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("Ready — Open a PDF or load the Sample PDF")

        # Wire header & left rail signals
        self.left_rail.modeSelected.connect(self.set_mode)
        self.left_rail.toolAction.connect(self._on_tool_action)
        self.ribbon.newClicked.connect(self.new_blank_pdf)
        self.ribbon.openClicked.connect(self.open_file_dialog)
        self.ribbon.recentFileClicked.connect(self.open_pdf_path)
        self.ribbon.saveClicked.connect(self.save_current_pdf)
        self.ribbon.saveAsClicked.connect(self.save_as_current_pdf)
        self.ribbon.printClicked.connect(self.print_current_pdf)
        self.ribbon.findReplaceClicked.connect(self.open_find_replace_dialog)
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
        self.ribbon.searchReturnPressed.connect(lambda back: self.step_search(-1 if back else 1))
        self.ribbon.searchCleared.connect(self._clear_search)
        self.ribbon.modeChanged.connect(self._on_mode_changed)
        self.ribbon.toolAction.connect(self._on_tool_action)

        self.sidebar.pageSelected.connect(self.go_to_page)
        self.sidebar.bookmarkAdded.connect(self._on_bookmark_added)
        self.sidebar.bookmarkRemoved.connect(self._on_bookmark_removed)
        self.sidebar.tabs.currentChanged.connect(lambda _idx: self._sync_right_rail_buttons())

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
            btn.setObjectName("RailBtn")
            btn.setCheckable(True)
            btn.setFixedSize(32, 32)
            btn.setToolTip(tip)
            btn.clicked.connect(lambda _=False, ti=tab_idx: self._toggle_sidebar_tab(ti))
            self._right_rail_buttons.append((btn, icon_name))
            self._right_rail_tab_buttons[tab_idx] = (btn, icon_name)
            v.addWidget(btn)

        v.addStretch()

        btn_theme = QPushButton()
        btn_theme.setObjectName("RailBtn")
        btn_theme.setFixedSize(32, 32)
        btn_theme.setToolTip("Toggle Light / Dark Theme")
        btn_theme.clicked.connect(self.toggle_theme)
        self._right_rail_buttons.append((btn_theme, "theme"))
        v.addWidget(btn_theme)
        return rail

    def _sync_right_rail_buttons(self) -> None:
        is_open = self.sidebar.isVisible()
        cur_idx = self.sidebar.tabs.currentIndex()
        base_col = "#e2e8f0" if self.dark_theme else "#334155"
        for ti, (btn, icon_name) in self._right_rail_tab_buttons.items():
            active = is_open and ti == cur_idx
            btn.blockSignals(True)
            btn.setChecked(active)
            btn.blockSignals(False)
            btn.setIcon(make_icon(icon_name, "#ffffff" if active else base_col, 17))
            btn.setIconSize(QSize(17, 17))

    def _toggle_sidebar_tab(self, tab_idx: int) -> None:
        if self.sidebar.isVisible() and self.sidebar.tabs.currentIndex() == tab_idx:
            self.sidebar.hide()
        else:
            self.sidebar.tabs.setCurrentIndex(tab_idx)
            self.sidebar.show()
        self._sync_right_rail_buttons()
        if self.centralWidget() and self.centralWidget().layout():
            self.centralWidget().layout().activate()
        tab = self.current_tab()
        if tab and tab.zoom_mode in ("Fit Width", "Fit Page"):
            tab.refresh_all_pages(defer_render=False)
            self._position_floating_bar()

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
        btn_new = QPushButton("New Blank PDF")
        btn_new.setObjectName("PillBtn")
        btn_new.setMinimumHeight(38)
        btn_new.clicked.connect(self.new_blank_pdf)
        btn_sample = QPushButton("Load Sample PDF")
        btn_sample.setObjectName("PillBtn")
        btn_sample.setMinimumHeight(38)
        btn_sample.clicked.connect(self.load_sample_pdf)
        row.addWidget(btn_open)
        row.addWidget(btn_new)
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
        QShortcut(QKeySequence("Ctrl+N"), self, activated=self.new_blank_pdf)
        QShortcut(QKeySequence("Ctrl+O"), self, activated=self.open_file_dialog)
        QShortcut(QKeySequence("Ctrl+S"), self, activated=self.save_current_pdf)
        QShortcut(QKeySequence("Ctrl+Shift+S"), self, activated=self.save_as_current_pdf)
        QShortcut(QKeySequence("Ctrl+P"), self, activated=self.print_current_pdf)
        QShortcut(QKeySequence("Ctrl+W"), self, activated=lambda: self._request_close_tab(self.tabs.currentIndex()))
        QShortcut(QKeySequence("Ctrl+F"), self, activated=self._focus_search)
        QShortcut(QKeySequence("Ctrl+H"), self, activated=self.open_find_replace_dialog)
        QShortcut(QKeySequence("F3"), self, activated=lambda: self.step_search(1))
        QShortcut(QKeySequence("Shift+F3"), self, activated=lambda: self.step_search(-1))
        QShortcut(QKeySequence("Ctrl+C"), self, activated=self._shortcut_copy)
        QShortcut(QKeySequence("Ctrl+Z"), self, activated=self._shortcut_undo)
        QShortcut(QKeySequence("Ctrl+Y"), self, activated=self._shortcut_redo)
        QShortcut(QKeySequence("Ctrl+Shift+Z"), self, activated=self._shortcut_redo)
        QShortcut(QKeySequence("Ctrl+="), self, activated=lambda: self.step_zoom(1.15))
        QShortcut(QKeySequence("Ctrl+-"), self, activated=lambda: self.step_zoom(0.85))
        QShortcut(QKeySequence("Ctrl+0"), self, activated=lambda: self.set_zoom_mode("Fit Width"))
        QShortcut(QKeySequence("Ctrl+1"), self, activated=lambda: self.set_zoom_mode("100%"))

    def _focus_search(self) -> None:
        self.ribbon.search_input.setFocus()
        self.ribbon.search_input.selectAll()

    def _focused_text_input(self):
        """Return the focused text/line editor inside the window when visible."""
        fw = QApplication.focusWidget() or self.focusWidget()
        if isinstance(fw, (QTextEdit, QLineEdit)) and fw.isVisible():
            return fw
        return None

    def _shortcut_undo(self) -> None:
        fw = self._focused_text_input()
        if fw is not None:
            fw.undo()
            return
        self.undo()

    def _shortcut_redo(self) -> None:
        fw = self._focused_text_input()
        if fw is not None:
            fw.redo()
            return
        self.redo()

    def _shortcut_copy(self) -> None:
        fw = self._focused_text_input()
        if fw is not None:
            fw.copy()
            return
        tab = self.current_tab()
        if tab is not None:
            texts = [pw.get_selected_text() for pw in tab.page_widgets if pw.get_selected_text()]
            joined = "\n".join(texts).strip()
            if joined:
                QApplication.clipboard().setText(heal_pdf_text(joined))
                self.status_bar.showMessage(f"Copied {len(joined)} characters to clipboard")

    def _apply_theme(self) -> None:
        self.setStyleSheet(build_stylesheet(self.dark_theme, self.accent_color, self.accent_hover))
        self.ribbon.refresh_icon_theme(self.dark_theme)
        self.left_rail.refresh_icon_theme(self.dark_theme)
        col = "#e2e8f0" if self.dark_theme else "#334155"
        for btn, icon_name in self._right_rail_buttons:
            btn.setIcon(make_icon(icon_name, col, 17))
            btn.setIconSize(QSize(17, 17))
        self._sync_right_rail_buttons()

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
        for i in range(self.doc_stack.count()):
            w = self.doc_stack.widget(i)
            if isinstance(w, DocumentTab):
                w.accent_color = color
                for pw in w.page_widgets:
                    pw.accent_color = color
                    pw.update()

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
            if not os.path.isfile(path):
                self._remove_recent_file(path)
            QMessageBox.critical(self, "Open PDF", f"Could not read file:\n{err}")
            return None
        return self.open_pdf_bytes(data, os.path.basename(path), file_path=path)

    def open_pdf_bytes(self, data: bytes, name: str, file_path: str | None = None) -> DocumentTab | None:
        try:
            engine = PDFDocumentEngine(data, name=name, file_path=file_path)
        except Exception as err:
            QMessageBox.critical(self, "Open PDF", f"Could not open PDF document:\n{err}")
            return None
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
        self.tabs.setTabData(idx, tab)
        self.tabs.setTabToolTip(idx, file_path or name)
        self.tabs.setCurrentIndex(idx)
        self.doc_stack.setCurrentWidget(tab)
        self._rebuild_tab_pages(tab)
        self.sidebar.set_engine(engine, 0)
        self.ribbon.update_page_info(0, engine.page_count)
        self.ribbon.set_zoom_label(tab.zoom_mode)
        self._update_search_label(tab)
        if file_path:
            self._record_recent_file(file_path)
        self.status_bar.showMessage(f"Opened {name} ({engine.page_count} pages)")
        return tab

    def new_blank_pdf(self) -> DocumentTab | None:
        return self.open_pdf_bytes(create_blank_pdf_bytes(), "Untitled.pdf")

    def load_sample_pdf(self) -> DocumentTab | None:
        return self.open_pdf_bytes(create_sample_pdf_bytes(), "Sample_Document.pdf")

    def _load_recent_files(self) -> list[str]:
        raw = QSettings("Personal", "Personal PDF Suite").value("recentFiles", [])
        if isinstance(raw, str):
            raw = [raw] if raw else []
        return [p for p in (raw or []) if isinstance(p, str) and os.path.isfile(p)][:8]

    def _record_recent_file(self, path: str) -> None:
        norm = os.path.abspath(path)
        if not os.path.isfile(norm):
            return
        cur = [p for p in self._load_recent_files() if os.path.normcase(p) != os.path.normcase(norm)]
        updated = [norm] + cur[:7]
        QSettings("Personal", "Personal PDF Suite").setValue("recentFiles", updated)
        self.ribbon.update_recent_files(updated)

    def _remove_recent_file(self, path: str) -> None:
        norm = os.path.normcase(os.path.abspath(path))
        updated = [p for p in self._load_recent_files() if os.path.normcase(p) != norm]
        QSettings("Personal", "Personal PDF Suite").setValue("recentFiles", updated)
        self.ribbon.update_recent_files(updated)

    def _set_tab_dirty(self, tab: DocumentTab, dirty: bool = True) -> None:
        tab.dirty = dirty
        title = f"* {tab.engine.name}" if dirty else tab.engine.name
        for i in range(self.tabs.count()):
            if self.tabs.tabData(i) is tab:
                self.tabs.setTabText(i, title)
                break

    def _on_bookmark_added(self, title: str, page_idx: int) -> None:
        tab = self.current_tab()
        if tab is not None:
            tab.base_modified = True
            self._set_tab_dirty(tab, True)
        self.status_bar.showMessage(f"Bookmark added: {title} (Page {page_idx + 1})")

    def _on_bookmark_removed(self, title: str, page_idx: int) -> None:
        tab = self.current_tab()
        if tab is not None:
            tab.base_modified = True
            self._set_tab_dirty(tab, True)
        self.status_bar.showMessage(f"Bookmark removed: {title} (Page {page_idx + 1})")

    def _callbacks_for(self, tab: DocumentTab) -> dict:
        return {
            "editor_opened": lambda pw, ed: self._on_editor_opened(tab, pw, ed),
            "editor_closed": lambda pw, ed: self._on_editor_closed(pw, ed),
            "edit_committed": lambda pno, op: self._on_in_place_edit_committed(tab, pno, op),
            "annot_added": lambda pno, kind, item: self._on_annot_added(tab, pno, kind, item),
            "annot_removed": lambda pno, kind, item: self._on_annot_removed(tab, pno, kind, item),
            "annot_modified": lambda pno, kind, item, prop, old_v, new_v: self._on_annot_modified(
                tab, pno, kind, item, prop, old_v, new_v
            ),
            "link_clicked": lambda lnk: self._on_link_clicked(tab, lnk),
            "form_edited": lambda pno, w_idx, old_v, new_v: self._on_form_field_edited(
                tab, pno, w_idx, old_v, new_v
            ),
            "page_interacted": lambda pno: self._on_page_interacted(tab, pno),
        }

    def _rebuild_tab_pages(self, tab: DocumentTab) -> None:
        tab.rebuild_pages(self.tool_state, self._callbacks_for(tab))
        self.ribbon.update_page_info(tab.current_page, tab.engine.page_count)

    def _on_tab_moved(self, from_idx: int, to_idx: int) -> None:
        w = self.doc_stack.widget(from_idx)
        if w is None:
            return
        cur_tab = self.tabs.tabData(self.tabs.currentIndex())
        self.doc_stack.blockSignals(True)
        try:
            self.doc_stack.removeWidget(w)
            self.doc_stack.insertWidget(to_idx, w)
            if isinstance(cur_tab, DocumentTab):
                self.doc_stack.setCurrentWidget(cur_tab)
        finally:
            self.doc_stack.blockSignals(False)

    def _on_tab_changed(self, idx: int) -> None:
        page = self._get_active_editor_page()
        if page is not None:
            page.commit_active_editor()
        self._hide_floating_bar()
        if idx < 0 or idx >= self.doc_stack.count():
            self.sidebar.set_engine(None)
            self.ribbon.update_page_info(0, 0)
            self.ribbon.lbl_search_count.setText("0/0")
            self.doc_stack.hide()
            self.welcome_widget.show()
            return
        tab = self.tabs.tabData(idx)
        if not isinstance(tab, DocumentTab):
            w = self.doc_stack.widget(idx)
            tab = w if isinstance(w, DocumentTab) else None
        if tab is None:
            return
        self.doc_stack.setCurrentWidget(tab)
        self.sidebar.set_engine(tab.engine, tab.current_page)
        self.ribbon.update_page_info(tab.current_page, tab.engine.page_count)
        self.ribbon.set_zoom_label(
            tab.zoom_mode if tab.zoom_mode in ("Fit Width", "Fit Page") else f"{int(round(tab.zoom * 100))}%"
        )
        self._update_search_label(tab)

    def _confirm_save_tab(self, tab: DocumentTab) -> bool:
        """Prompt to save unsaved changes in tab; return True if safe to close."""
        tab.commit_any_active_editor()
        if not tab.dirty:
            return True
        for i in range(self.tabs.count()):
            if self.tabs.tabData(i) is tab:
                self.tabs.setCurrentIndex(i)
                break
        res = QMessageBox.question(
            self,
            "Unsaved Changes",
            f'Save changes to "{tab.engine.name}" before closing?',
            QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
            QMessageBox.Save,
        )
        if res == QMessageBox.Cancel:
            return False
        if res == QMessageBox.Save:
            return self.save_current_pdf()
        return True

    def _request_close_tab(self, idx: int) -> None:
        if idx < 0 or idx >= self.doc_stack.count():
            return
        w = self.tabs.tabData(idx)
        if isinstance(w, DocumentTab) and not self._confirm_save_tab(w):
            return
        self._close_tab(idx)

    def _close_tab(self, idx: int) -> None:
        if idx < 0 or idx >= self.doc_stack.count():
            return
        w = self.tabs.tabData(idx)
        if not isinstance(w, QWidget):
            w = self.doc_stack.widget(idx)
        page = self._get_active_editor_page()
        if isinstance(w, DocumentTab) and page is not None and page in w.page_widgets:
            page.cancel_active_editor()
            self._hide_floating_bar()
        self.doc_stack.removeWidget(w)
        self.tabs.removeTab(idx)
        if w:
            w.deleteLater()
        if self.tabs.count() == 0:
            self._hide_floating_bar()
            self.sidebar.set_engine(None)
            self.ribbon.update_page_info(0, 0)
            self.ribbon.lbl_search_count.setText("0/0")
            self.doc_stack.hide()
            self.welcome_widget.show()

    def _on_scroll(self, tab: DocumentTab) -> None:
        if tab is not self.current_tab() or not tab.page_widgets:
            return
        tab.refresh_visible_pages(buffer_px=250)
        if self._get_active_editor() is not None:
            self._position_floating_bar()
        if tab._programmatic_scroll:
            return
        vbar = tab.verticalScrollBar()
        v_top = vbar.value()
        v_mid = v_top + tab.viewport().height() // 3
        best = tab.current_page
        for pw in tab.page_widgets:
            y = pw.y()
            if y - 8 <= v_mid <= y + pw.height() + 8:
                best = pw.page_idx
                break
        if vbar.maximum() > 0 and v_top >= vbar.maximum() - 4:
            best = len(tab.page_widgets) - 1
        if best != tab.current_page:
            tab.current_page = best
            self.ribbon.update_page_info(best, tab.engine.page_count)
            self.sidebar.set_current_page(best)

    def _on_page_interacted(self, tab: DocumentTab, page_idx: int) -> None:
        active_page = self._get_active_editor_page()
        if active_page is not None and active_page.page_idx != page_idx and active_page in tab.page_widgets:
            active_page.commit_active_editor()
        if tab.current_page != page_idx:
            tab.current_page = page_idx
            self.ribbon.update_page_info(page_idx, tab.engine.page_count)
            self.sidebar.set_current_page(page_idx)

    def _get_active_editor(self) -> InPlaceTextEditor | None:
        ed = self._active_editor
        if ed is None:
            return None
        if not shiboken6.isValid(ed):
            self._hide_floating_bar()
            return None
        return ed

    def _get_active_editor_page(self) -> PDFPageWidget | None:
        page = self._active_editor_page
        if page is None:
            return None
        if not shiboken6.isValid(page):
            self._hide_floating_bar()
            return None
        return page

    def _apply_to_active_editor(self, fn) -> None:
        ed = self._get_active_editor()
        if ed is not None:
            fn(ed)

    def _finish_active_editor(self, commit: bool) -> None:
        page = self._get_active_editor_page()
        if page is None:
            self._hide_floating_bar()
            return
        if commit:
            page.commit_active_editor()
        else:
            page.cancel_active_editor()

    def _on_editor_opened(self, tab: DocumentTab, page_widget: PDFPageWidget, editor: InPlaceTextEditor) -> None:
        prev_page = self._get_active_editor_page()
        if prev_page is not None and prev_page is not page_widget:
            prev_page.commit_active_editor()
        self._active_editor = editor
        self._active_editor_page = page_widget
        ed_ref = weakref.ref(editor)
        editor.destroyed.connect(lambda _obj=None, r=ed_ref: self._on_editor_destroyed(r()))
        editor.formatStateChanged.connect(self._sync_floating_bar)
        editor.heightChanged.connect(lambda _h: self._position_floating_bar())
        self._sync_floating_bar()
        self._position_floating_bar()
        self.floating_bar.show()
        self.floating_bar.raise_()

    def _on_editor_closed(self, page_widget: PDFPageWidget, editor: InPlaceTextEditor) -> None:
        if self._active_editor is editor or self._active_editor_page is page_widget:
            self._hide_floating_bar()

    def _on_editor_destroyed(self, editor: object | None) -> None:
        if self._active_editor is None:
            return
        if editor is self._active_editor or not shiboken6.isValid(self._active_editor):
            self._hide_floating_bar()

    def _sync_floating_bar(self) -> None:
        ed = self._get_active_editor()
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
        ed = self._get_active_editor()
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
        ed = self._get_active_editor()
        if ed is not None:
            ed.reflow_enabled = enabled

    def _on_in_place_edit_committed(self, tab: DocumentTab, page_idx: int, op: TextEditOp) -> None:
        self._hide_floating_bar()
        prev = tab.engine.in_place_edits.get(page_idx, {}).get(op.block_id)
        tab.undo_stack.append(_UndoEntry(kind="in_place_edit", page_idx=page_idx, payload=op, previous_payload=prev))
        tab.redo_stack.clear()
        tab.engine.set_in_place_edit(op)
        self._rebuild_tab_pages(tab)
        self._set_tab_dirty(tab, True)
        if tab is self.current_tab():
            self.sidebar.refresh_annotations()
            self.sidebar.refresh_thumbnails()
            self.status_bar.showMessage(f"Vector text edit applied on Page {page_idx + 1}")

    def _on_annot_added(self, tab: DocumentTab, page_idx: int, kind: str, item: object) -> None:
        tab.undo_stack.append(_UndoEntry(kind=kind, page_idx=page_idx, payload=item))
        tab.redo_stack.clear()
        self._set_tab_dirty(tab, True)
        self.sidebar.refresh_annotations()

    def _on_annot_removed(self, tab: DocumentTab, page_idx: int, kind: str, item: object) -> None:
        tab.undo_stack.append(_UndoEntry(kind=kind, page_idx=page_idx, payload=item, action="remove"))
        tab.redo_stack.clear()
        self._set_tab_dirty(tab, True)
        self.sidebar.refresh_annotations()

    def _on_annot_modified(
        self,
        tab: DocumentTab,
        page_idx: int,
        kind: str,
        item: object,
        prop_name: str,
        old_val: object,
        new_val: object,
    ) -> None:
        tab.undo_stack.append(
            _UndoEntry(
                kind=kind,
                page_idx=page_idx,
                payload=item,
                previous_payload=old_val,
                action="modify",
                prop_name=prop_name,
                new_value=new_val,
            )
        )
        tab.redo_stack.clear()
        self._set_tab_dirty(tab, True)
        self.sidebar.refresh_annotations()

    def _on_form_field_edited(
        self, tab: DocumentTab, page_idx: int, widget_idx: int, old_val: object, new_val: object
    ) -> None:
        had_prev = any(
            e.action == "form_value" and e.page_idx == page_idx and e.payload == widget_idx
            for e in tab.undo_stack
        )
        tab.undo_stack.append(
            _UndoEntry(
                kind="form_value",
                page_idx=page_idx,
                payload=widget_idx,
                previous_payload=(had_prev, old_val),
                action="form_value",
                new_value=new_val,
            )
        )
        tab.redo_stack.clear()
        self._set_tab_dirty(tab, True)
        self.sidebar.refresh_annotations()
        self.status_bar.showMessage(f"Updated form field on Page {page_idx + 1}")

    def _on_link_clicked(self, tab: DocumentTab, lnk: dict) -> None:
        page = lnk.get("page")
        if isinstance(page, int) and 0 <= page < tab.engine.page_count:
            self.go_to_page(page)
            self.status_bar.showMessage(f"Jumped to Page {page + 1}")
            return
        uri = str(lnk.get("uri") or "").strip()
        if uri:
            url = QUrl(uri)
            if url.isValid() and url.scheme().lower() in ("http", "https", "mailto"):
                QDesktopServices.openUrl(url)
                self.status_bar.showMessage(f"Opened link: {uri}")

    def undo(self) -> None:
        tab = self.current_tab()
        if tab is None or not tab.undo_stack:
            return
        tab.commit_any_active_editor()
        if not tab.undo_stack:
            return
        entry = tab.undo_stack.pop()
        tab.redo_stack.append(entry)
        if entry.kind == "batch_in_place_edits":
            changes = entry.previous_payload if entry.action == "batch_in_place_edits" else entry.payload
            for new_op, prev_op in changes:
                if prev_op is not None:
                    tab.engine.in_place_edits.setdefault(prev_op.page_idx, {})[prev_op.block_id] = prev_op
                elif new_op is not None:
                    tab.engine.in_place_edits.get(new_op.page_idx, {}).pop(new_op.block_id, None)
            tab.engine.invalidate_cache()
            self._rebuild_tab_pages(tab)
            self.sidebar.refresh_thumbnails()
        elif entry.kind == "in_place_edit":
            op: TextEditOp = entry.payload
            if entry.previous_payload is not None:
                tab.engine.set_in_place_edit(entry.previous_payload)
            else:
                tab.engine.remove_in_place_edit(entry.page_idx, op.block_id)
            self._rebuild_tab_pages(tab)
            self.sidebar.refresh_thumbnails()
        elif entry.action == "form_value":
            w_idx = int(entry.payload)
            had_prev, prev_val = entry.previous_payload
            if had_prev:
                tab.engine.set_form_value(entry.page_idx, w_idx, prev_val)
            else:
                tab.engine.remove_form_value(entry.page_idx, w_idx)
            if 0 <= entry.page_idx < len(tab.page_widgets):
                tab.page_widgets[entry.page_idx].refresh_render()
            self.sidebar.refresh_thumbnails()
        elif entry.action == "modify":
            store = self._store_for_kind(tab.engine, entry.kind)
            lst = store.setdefault(entry.page_idx, [])
            if entry.prop_name == "z_order":
                if entry.payload in lst and isinstance(entry.previous_payload, int):
                    lst.remove(entry.payload)
                    idx = max(0, min(len(lst), entry.previous_payload))
                    lst.insert(idx, entry.payload)
            elif entry.prop_name:
                setattr(entry.payload, entry.prop_name, entry.previous_payload)
            if 0 <= entry.page_idx < len(tab.page_widgets):
                tab.page_widgets[entry.page_idx].update()
        else:
            store = self._store_for_kind(tab.engine, entry.kind)
            lst = store.setdefault(entry.page_idx, [])
            if entry.action == "remove":
                if entry.payload not in lst:
                    lst.append(entry.payload)
            else:
                if entry.payload in lst:
                    lst.remove(entry.payload)
                    if (
                        0 <= entry.page_idx < len(tab.page_widgets)
                        and tab.page_widgets[entry.page_idx].selected_item is entry.payload
                    ):
                        tab.page_widgets[entry.page_idx].selected_item = None
            if entry.page_idx < len(tab.page_widgets):
                tab.page_widgets[entry.page_idx].update()
        self._set_tab_dirty(tab, tab.base_modified or bool(tab.undo_stack))
        self.sidebar.refresh_annotations()
        self.status_bar.showMessage("Undone")

    def redo(self) -> None:
        tab = self.current_tab()
        if tab is None or not tab.redo_stack:
            return
        tab.commit_any_active_editor()
        if not tab.redo_stack:
            return
        entry = tab.redo_stack.pop()
        tab.undo_stack.append(entry)
        if entry.kind == "batch_in_place_edits":
            changes = entry.previous_payload if entry.action == "batch_in_place_edits" else entry.payload
            for new_op, prev_op in changes:
                if new_op is not None:
                    tab.engine.in_place_edits.setdefault(new_op.page_idx, {})[new_op.block_id] = new_op
                elif prev_op is not None:
                    tab.engine.in_place_edits.get(prev_op.page_idx, {}).pop(prev_op.block_id, None)
            tab.engine.invalidate_cache()
            self._rebuild_tab_pages(tab)
            self.sidebar.refresh_thumbnails()
        elif entry.kind == "in_place_edit":
            tab.engine.set_in_place_edit(entry.payload)
            self._rebuild_tab_pages(tab)
            self.sidebar.refresh_thumbnails()
        elif entry.action == "form_value":
            w_idx = int(entry.payload)
            tab.engine.set_form_value(entry.page_idx, w_idx, entry.new_value)
            if 0 <= entry.page_idx < len(tab.page_widgets):
                tab.page_widgets[entry.page_idx].refresh_render()
            self.sidebar.refresh_thumbnails()
        elif entry.action == "modify":
            store = self._store_for_kind(tab.engine, entry.kind)
            lst = store.setdefault(entry.page_idx, [])
            if entry.prop_name == "z_order":
                if entry.payload in lst:
                    lst.remove(entry.payload)
                    lst.append(entry.payload)
            elif entry.prop_name:
                setattr(entry.payload, entry.prop_name, entry.new_value)
            if 0 <= entry.page_idx < len(tab.page_widgets):
                tab.page_widgets[entry.page_idx].update()
        else:
            store = self._store_for_kind(tab.engine, entry.kind)
            lst = store.setdefault(entry.page_idx, [])
            if entry.action == "remove":
                if entry.payload in lst:
                    lst.remove(entry.payload)
                    if (
                        0 <= entry.page_idx < len(tab.page_widgets)
                        and tab.page_widgets[entry.page_idx].selected_item is entry.payload
                    ):
                        tab.page_widgets[entry.page_idx].selected_item = None
            else:
                if entry.payload not in lst:
                    lst.append(entry.payload)
            if entry.page_idx < len(tab.page_widgets):
                tab.page_widgets[entry.page_idx].update()
        self._set_tab_dirty(tab, tab.base_modified or bool(tab.undo_stack))
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
        tab = self.current_tab()
        if tab and tool != self.tool_state["tool"]:
            tab.commit_any_active_editor()
            self._hide_floating_bar()
        self.tool_state["tool"] = tool
        self.left_rail.btn_pan.setChecked(tool == "pan")
        if tool == "highlighter":
            self.tool_state["color"] = self._hl_color
            self.tool_state["width"] = self._hl_width
            self.tool_state["opacity"] = self._hl_opacity
        else:
            self.tool_state["color"] = self._ink_color
            self.tool_state["width"] = self._ink_width
            self.tool_state["opacity"] = 1.0
        self.ribbon.set_width_indicator(self.tool_state["width"])
        if tab:
            for pw in tab.page_widgets:
                pw.set_tool_state(**self.tool_state)

    def _on_tool_action(self, action: str, extra: dict) -> None:
        tab = self.current_tab()
        if action.startswith("tool:"):
            self._set_active_tool(action.split(":", 1)[1])
            return
        if action == "set_width":
            w_val = float(extra.get("width", 3.0))
            if self.tool_state["tool"] == "highlighter":
                self._hl_width = w_val
            else:
                self._ink_width = w_val
            self.tool_state["width"] = w_val
            if tab:
                for pw in tab.page_widgets:
                    pw.set_tool_state(**self.tool_state)
            return
        if tab is None:
            QMessageBox.information(self, "Personal PDF Suite", "Please open a PDF document first.")
            return
        if action not in ("copy_selection", "pick_color"):
            tab.commit_any_active_editor()

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
                if self.tool_state["tool"] == "highlighter":
                    self._hl_color = col.name()
                else:
                    self._ink_color = col.name()
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
            path, _ = QFileDialog.getOpenFileName(self, "Insert Image", "", "Images (*.png *.jpg *.jpeg *.bmp *.webp)")
            if path:
                try:
                    with open(path, "rb") as f:
                        img_bytes = f.read()
                except OSError as err:
                    QMessageBox.critical(self, "Insert Image", f"Could not read image:\n{err}")
                    return
                self._place_image_overlay(tab, img_bytes, max_w=220.0, max_h=220.0)

        elif action == "open_signature":
            dlg = SignatureDialog(self)
            if dlg.exec() and dlg.result_png_bytes:
                self._place_image_overlay(tab, dlg.result_png_bytes, max_w=190.0, max_h=90.0)

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
            cur_page = tab.current_page
            cur_bytes = tab.engine.build_final_pdf_bytes()
            ops = [
                PageOp(original_index=i, rotation_delta=(90 if i == cur_page else 0))
                for i in range(tab.engine.page_count)
            ]
            self._replace_tab_document(tab, organize_pages(cur_bytes, ops))
            self.go_to_page(cur_page)
            self.status_bar.showMessage(f"Rotated Page {cur_page + 1} 90° clockwise")

        elif action == "open_merge":
            dlg = MergeDialog(tab.engine.name, self)
            if dlg.exec():
                try:
                    parts = []
                    if dlg.chk_include_current and dlg.chk_include_current.isChecked():
                        parts.append(tab.engine.build_final_pdf_bytes())
                    for fp in dlg.file_paths:
                        with open(fp, "rb") as f:
                            parts.append(f.read())
                    if parts:
                        merged = merge_pdfs(parts)
                        self.open_pdf_bytes(merged, "Merged_Document.pdf")
                except Exception as err:
                    QMessageBox.critical(self, "Combine PDFs", f"Could not merge PDF files:\n{err}")

        elif action == "open_split":
            cur_bytes = tab.engine.build_final_pdf_bytes()
            dlg = SplitDialog(tab.engine.page_count, self)
            if dlg.exec():
                out_dir = QFileDialog.getExistingDirectory(self, "Select Output Folder for Split PDFs")
                if not out_dir:
                    return
                try:
                    results = split_pdf_by_ranges(cur_bytes, dlg.ranges_input.text(), tab.engine.name)
                    for fname, fbytes in results:
                        write_bytes_atomic(os.path.join(out_dir, fname), fbytes)
                except Exception as err:
                    QMessageBox.critical(self, "Split PDF", f"Could not split PDF:\n{err}")
                    return
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
                    try:
                        enc_bytes = encrypt_pdf_aes256(
                            tab.engine.build_final_pdf_bytes(),
                            dlg.pw1.text(),
                            allow_print=dlg.allow_print.isChecked(),
                            allow_copy=dlg.allow_copy.isChecked(),
                        )
                        write_bytes_atomic(path, enc_bytes)
                    except Exception as err:
                        QMessageBox.critical(self, "Encrypt PDF", f"Could not save encrypted PDF:\n{err}")
                        return
                    self.status_bar.showMessage(f"Saved AES-256 encrypted PDF: {path}")

        elif action == "open_word_flow":
            tab.commit_any_active_editor()
            blocks_html = []
            for pno in range(tab.engine.page_count):
                for blk in tab.engine.extract_text_blocks(pno):
                    blocks_html.append(blk.html)
            if not blocks_html and tab.engine.page_count > 0:
                doc = tab.engine._get_preview_doc()
                if any(pg.get_images() for pg in doc):
                    QMessageBox.information(
                        self,
                        "Word-Flow Editor",
                        "No editable vector text blocks were found in this document (it appears to be a scanned or image-only PDF).\n\n"
                        "Use Add Text or Annotation tools to add overlays without replacing scanned pages.",
                    )
                    return
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
                try:
                    write_bytes_atomic(path, export_page_image(cur_bytes, tab.current_page, dpi=200, fmt="png"))
                except Exception as err:
                    QMessageBox.critical(self, "Export Page", f"Could not export PNG:\n{err}")
                    return
                self.status_bar.showMessage(f"Exported page {tab.current_page + 1} to {path}")

        elif action == "export_all_png":
            out_dir = QFileDialog.getExistingDirectory(self, "Select Output Folder for Page Images")
            if out_dir:
                cur_bytes = tab.engine.build_final_pdf_bytes()
                try:
                    for pno in range(tab.engine.page_count):
                        write_bytes_atomic(
                            os.path.join(out_dir, f"page_{pno + 1}.png"),
                            export_page_image(cur_bytes, pno, dpi=200, fmt="png"),
                        )
                except Exception as err:
                    QMessageBox.critical(self, "Export Pages", f"Could not export PNG images:\n{err}")
                    return
                QMessageBox.information(self, "Export Complete", f"Exported {tab.engine.page_count} PNG file(s).")

        elif action == "export_text":
            cur_bytes = tab.engine.build_final_pdf_bytes()
            base = os.path.splitext(tab.engine.name)[0]
            path, _ = QFileDialog.getSaveFileName(self, "Export Text", f"{base}.txt", "Text Files (*.txt)")
            if path:
                try:
                    write_bytes_atomic(path, extract_clean_text(cur_bytes).encode("utf-8"))
                except Exception as err:
                    QMessageBox.critical(self, "Export Text", f"Could not export text:\n{err}")
                    return
                self.status_bar.showMessage(f"Extracted clean text to {path}")

    def _place_image_overlay(
        self, tab: DocumentTab, img_bytes: bytes, max_w: float, max_h: float
    ) -> bool:
        """Place a centered image/signature overlay preserving natural aspect ratio."""
        img = QImage.fromData(img_bytes)
        if img.isNull() or img.width() <= 0 or img.height() <= 0:
            QMessageBox.warning(self, "Insert Image", "The selected image could not be decoded.")
            return False
        scale = min(max_w / float(img.width()), max_h / float(img.height()))
        w_pt = max(24.0, img.width() * scale)
        h_pt = max(24.0, img.height() * scale)
        pw_pt, ph_pt = tab.engine.get_page_size(tab.current_page)
        stp = StampItem(
            page_idx=tab.current_page,
            kind="image",
            rect=(
                pw_pt / 2.0 - w_pt / 2.0,
                ph_pt / 2.0 - h_pt / 2.0,
                pw_pt / 2.0 + w_pt / 2.0,
                ph_pt / 2.0 + h_pt / 2.0,
            ),
            opacity=1.0,
            rotation=0.0,
            image_bytes=img_bytes,
        )
        tab.engine.stamps.setdefault(tab.current_page, []).append(stp)
        self._on_annot_added(tab, tab.current_page, "stamp", stp)
        tab.page_widgets[tab.current_page].update()
        return True

    def _refresh_tab_search_after_doc_change(self, tab: DocumentTab) -> None:
        if tab is not self.current_tab():
            return
        query = self.ribbon.search_input.text().strip()
        if query:
            self.run_search(query)
        else:
            tab.search_hits = []
            tab.search_idx = -1
            tab._apply_search_highlights()
            self._update_search_label(tab)

    def _replace_tab_document(self, tab: DocumentTab, new_bytes: bytes, mark_dirty: bool = True) -> None:
        tab.commit_any_active_editor()
        if tab is self.current_tab():
            self._hide_floating_bar()
        old_path = tab.engine.file_path
        old_name = tab.engine.name
        tab.engine = PDFDocumentEngine(new_bytes, name=old_name, file_path=old_path)
        tab.current_page = min(tab.current_page, max(0, tab.engine.page_count - 1))
        tab.undo_stack.clear()
        tab.redo_stack.clear()
        tab.base_modified = mark_dirty
        self._set_tab_dirty(tab, mark_dirty)
        for i in range(self.tabs.count()):
            if self.tabs.tabData(i) is tab:
                self.tabs.setTabToolTip(i, old_path or old_name)
                break
        self._rebuild_tab_pages(tab)
        if tab is self.current_tab():
            self.sidebar.set_engine(tab.engine, tab.current_page)
            query = self.ribbon.search_input.text().strip()
            if query:
                self.run_search(query)
            else:
                tab.search_hits = []
                tab.search_idx = -1
                tab._apply_search_highlights()
                self._update_search_label(tab)

    def open_metadata_dialog(self) -> None:
        tab = self.current_tab()
        if not tab:
            return
        tab.commit_any_active_editor()
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

    def save_current_pdf(self) -> bool:
        tab = self.current_tab()
        if not tab:
            return False
        tab.commit_any_active_editor()
        if not tab.engine.file_path:
            return self.save_as_current_pdf()
        try:
            out_bytes = tab.engine.build_final_pdf_bytes()
            write_bytes_atomic(tab.engine.file_path, out_bytes)
        except OSError as err:
            QMessageBox.critical(self, "Save PDF", f"Could not save file:\n{err}")
            return False
        self._replace_tab_document(tab, out_bytes, mark_dirty=False)
        self.status_bar.showMessage(f"Saved {tab.engine.file_path}")
        return True

    def save_as_current_pdf(self) -> bool:
        tab = self.current_tab()
        if not tab:
            return False
        tab.commit_any_active_editor()
        suggested = tab.engine.file_path or tab.engine.name
        path, _ = QFileDialog.getSaveFileName(self, "Save PDF As", suggested, "PDF Files (*.pdf)")
        if not path:
            return False
        if not path.lower().endswith(".pdf"):
            path += ".pdf"
        try:
            out_bytes = tab.engine.build_final_pdf_bytes()
            write_bytes_atomic(path, out_bytes)
        except OSError as err:
            QMessageBox.critical(self, "Save PDF As", f"Could not save file:\n{err}")
            return False
        tab.engine.file_path = path
        tab.engine.name = os.path.basename(path)
        self._replace_tab_document(tab, out_bytes, mark_dirty=False)
        self._record_recent_file(path)
        self.status_bar.showMessage(f"Saved as {path}")
        return True

    def open_find_replace_dialog(self) -> FindReplaceDialog | None:
        tab = self.current_tab()
        if not tab:
            QMessageBox.information(self, "Find & Replace", "Please open a PDF document first.")
            return None
        dlg = self._find_replace_dlg
        if dlg is None or not shiboken6.isValid(dlg):
            dlg = FindReplaceDialog(self.ribbon.search_input.text().strip(), self)
            dlg.findNextRequested.connect(self._on_fr_find_next)
            dlg.replaceNextRequested.connect(self._on_fr_replace_next)
            dlg.replaceAllRequested.connect(self._on_fr_replace_all)
            self._find_replace_dlg = dlg
        elif self.ribbon.search_input.text().strip() and not dlg.find_input.text().strip():
            dlg.find_input.setText(self.ribbon.search_input.text().strip())
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()
        dlg.find_input.setFocus()
        dlg.find_input.selectAll()
        return dlg

    def _on_fr_find_next(self, query: str, case_sensitive: bool, whole_word: bool) -> None:
        tab = self.current_tab()
        if not tab or not query:
            return
        if case_sensitive or whole_word:
            tab.search_hits = tab.engine.search_text(
                query, case_sensitive=case_sensitive, whole_word=whole_word
            )
            tab.search_idx = (tab.search_idx + 1) % len(tab.search_hits) if tab.search_hits else -1
            tab._apply_search_highlights()
            self._update_search_label(tab)
            if tab.search_hits and tab.search_idx >= 0:
                self._scroll_to_search_hit(tab, tab.search_hits[tab.search_idx])
        elif self.ribbon.search_input.text() != query:
            self.ribbon.search_input.setText(query)
        else:
            self.step_search(1)
        if self._find_replace_dlg is not None and shiboken6.isValid(self._find_replace_dlg):
            total = len(tab.search_hits)
            cur = (tab.search_idx + 1) if total > 0 and tab.search_idx >= 0 else 0
            self._find_replace_dlg.lbl_status.setText(
                f"Match {cur} of {total}" if total else "No matches found"
            )

    def _on_fr_replace_next(self, query: str, repl: str, case_sensitive: bool, whole_word: bool) -> None:
        tab = self.current_tab()
        if not tab or not query:
            return
        tab.commit_any_active_editor()
        changes, count = tab.engine.replace_text_in_blocks(
            query,
            repl,
            case_sensitive=case_sensitive,
            whole_word=whole_word,
            replace_all=False,
            start_page=tab.current_page,
        )
        if not changes:
            if self._find_replace_dlg is not None and shiboken6.isValid(self._find_replace_dlg):
                self._find_replace_dlg.lbl_status.setText("No editable matches found")
            return
        tab.undo_stack.append(
            _UndoEntry(
                kind="batch_in_place_edits",
                page_idx=changes[0][0].page_idx,
                payload=changes,
                previous_payload=changes,
                action="batch_in_place_edits",
            )
        )
        tab.redo_stack.clear()
        self._rebuild_tab_pages(tab)
        self._set_tab_dirty(tab, True)
        self.sidebar.refresh_annotations()
        self.sidebar.refresh_thumbnails()
        self._refresh_tab_search_after_doc_change(tab)
        if self._find_replace_dlg is not None and shiboken6.isValid(self._find_replace_dlg):
            rem = tab.engine.count_block_matches(query, case_sensitive=case_sensitive, whole_word=whole_word)
            self._find_replace_dlg.lbl_status.setText(f"Replaced {count} occurrence ({rem} remaining)")
        self.status_bar.showMessage(f"Replaced {count} occurrence on Page {changes[0][0].page_idx + 1}")

    def _on_fr_replace_all(self, query: str, repl: str, case_sensitive: bool, whole_word: bool) -> None:
        tab = self.current_tab()
        if not tab or not query:
            return
        tab.commit_any_active_editor()
        changes, count = tab.engine.replace_text_in_blocks(
            query,
            repl,
            case_sensitive=case_sensitive,
            whole_word=whole_word,
            replace_all=True,
            start_page=0,
        )
        if not changes:
            if self._find_replace_dlg is not None and shiboken6.isValid(self._find_replace_dlg):
                self._find_replace_dlg.lbl_status.setText("No editable matches found")
            return
        tab.undo_stack.append(
            _UndoEntry(
                kind="batch_in_place_edits",
                page_idx=changes[0][0].page_idx,
                payload=changes,
                previous_payload=changes,
                action="batch_in_place_edits",
            )
        )
        tab.redo_stack.clear()
        self._rebuild_tab_pages(tab)
        self._set_tab_dirty(tab, True)
        self.sidebar.refresh_annotations()
        self.sidebar.refresh_thumbnails()
        self._refresh_tab_search_after_doc_change(tab)
        if self._find_replace_dlg is not None and shiboken6.isValid(self._find_replace_dlg):
            self._find_replace_dlg.lbl_status.setText(
                f"Replaced {count} occurrence(s) across {len(changes)} text block(s)"
            )
        self.status_bar.showMessage(f"Replaced {count} occurrence(s) in {len(changes)} block(s)")

    def print_current_pdf(self) -> None:
        tab = self.current_tab()
        if not tab:
            return
        tab.commit_any_active_editor()
        final_bytes = tab.engine.build_final_pdf_bytes()
        dlg = PdfPrintDialog(
            final_bytes,
            current_page_zero=tab.current_page,
            doc_name=tab.engine.name,
            parent=self,
        )
        try:
            if not dlg.exec():
                return
            dlg.render_to_printer(dlg.printer)
            out_file = dlg.printer.outputFileName()
            if out_file:
                self.status_bar.showMessage(f"Printed to PDF: {out_file}")
            else:
                self.status_bar.showMessage(f"Sent print job to {dlg.printer.printerName() or 'printer'}")
        except Exception as err:
            QMessageBox.critical(self, "Print Document", f"Could not complete print job:\n{err}")
        finally:
            dlg.close_doc()

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
        self.ribbon.set_zoom_label(label)
        self._position_floating_bar()

    def step_zoom(self, factor: float) -> None:
        tab = self.current_tab()
        if not tab:
            return
        tab.zoom_mode = "custom"
        tab.zoom = max(0.25, min(4.0, tab.zoom * factor))
        tab.refresh_all_pages()
        self.ribbon.set_zoom_label(f"{int(round(tab.zoom * 100))}%")
        self._position_floating_bar()

    def rotate_clockwise(self) -> None:
        tab = self.current_tab()
        if not tab:
            return
        tab.commit_any_active_editor()
        tab.rotation = (tab.rotation + 90) % 360
        tab.refresh_all_pages()

    def _scroll_to_search_hit(self, tab: DocumentTab, hit: SearchHit) -> None:
        tab.scroll_to_rect(hit.page_idx, hit.rect)
        self.ribbon.update_page_info(hit.page_idx, tab.engine.page_count)
        self.sidebar.set_current_page(hit.page_idx)

    def run_search(self, query: str) -> None:
        tab = self.current_tab()
        if not tab:
            return
        tab.search_hits = tab.engine.search_text(query)
        tab.search_idx = 0 if tab.search_hits else -1
        tab._apply_search_highlights()
        self._update_search_label(tab)
        if tab.search_hits:
            self._scroll_to_search_hit(tab, tab.search_hits[0])

    def step_search(self, delta: int) -> None:
        tab = self.current_tab()
        if not tab or not tab.search_hits:
            return
        tab.search_idx = (tab.search_idx + delta) % len(tab.search_hits)
        tab._apply_search_highlights()
        self._update_search_label(tab)
        self._scroll_to_search_hit(tab, tab.search_hits[tab.search_idx])

    def _clear_search(self) -> None:
        tab = self.current_tab()
        if not tab:
            return
        tab.search_hits = []
        tab.search_idx = -1
        tab._apply_search_highlights()
        self._update_search_label(tab)

    def _update_search_label(self, tab: DocumentTab) -> None:
        total = len(tab.search_hits)
        cur = (tab.search_idx + 1) if total > 0 and tab.search_idx >= 0 else 0
        self.ribbon.lbl_search_count.setText(f"{cur}/{total}")

    def closeEvent(self, event: QCloseEvent) -> None:
        if event.spontaneous() or self._prompt_on_close:
            for i in range(self.doc_stack.count()):
                w = self.doc_stack.widget(i)
                if isinstance(w, DocumentTab) and not self._confirm_save_tab(w):
                    event.ignore()
                    return
        super().closeEvent(event)

    def _flush_deferred_fit_render(self) -> None:
        tab = self.current_tab()
        if tab and tab.page_widgets:
            tab.refresh_visible_pages(buffer_px=200)
            self._position_floating_bar()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        tab = self.current_tab()
        if tab and tab.zoom_mode in ("Fit Width", "Fit Page"):
            tab.refresh_all_pages(defer_render=True)
            self._resize_fit_timer.start()
        self._position_floating_bar()

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
