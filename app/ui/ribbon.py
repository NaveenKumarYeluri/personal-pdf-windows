"""Modern Adobe Acrobat-inspired space-saving Unified Top Bar, Left Tools Rail, and Contextual Strip."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QAction, QActionGroup, QIntValidator
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QTabBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from app.ui.icons import make_icon
from app.ui.theme import ACCENT_PRESETS


class RibbonHeader(QWidget):
    """
    Space-saving Acrobat-style top bar:
    - Single 44px Unified Top Bar containing Menu, quick icons, inline document tabs, page nav, zoom, and search.
    - Slim 34px Contextual Bar shown only when active mode has sub-tools (hidden in View mode).
    """

    openClicked = Signal()
    saveClicked = Signal()
    saveAsClicked = Signal()
    printClicked = Signal()
    themeToggled = Signal()
    accentChanged = Signal(str, str)
    propsClicked = Signal()
    toggleToolsPaneClicked = Signal()
    toggleSidebarClicked = Signal()
    undoClicked = Signal()
    redoClicked = Signal()
    prevPageClicked = Signal()
    nextPageClicked = Signal()
    pageJumpRequested = Signal(int)
    zoomOutClicked = Signal()
    zoomInClicked = Signal()
    zoomModeChanged = Signal(str)
    rotateCWClicked = Signal()
    searchChanged = Signal(str)
    searchPrevClicked = Signal()
    searchNextClicked = Signal()
    modeChanged = Signal(str)
    toolAction = Signal(str, dict)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._icon_color = "#334155"
        self.current_mode = "view"
        self._tool_buttons: list[tuple[QPushButton, str]] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 1. Single 44px Unified Acrobat-style Top Bar
        self.top_bar = QFrame()
        self.top_bar.setObjectName("UnifiedTopBar")
        self.top_bar.setFixedHeight(44)
        tb = QHBoxLayout(self.top_bar)
        tb.setContentsMargins(8, 4, 8, 4)
        tb.setSpacing(4)

        # Brand pill + Menu dropdown
        badge = QLabel("PDF")
        badge.setFixedSize(28, 24)
        badge.setAlignment(Qt.AlignCenter)
        badge.setStyleSheet(
            "background-color:#ea2e3f; color:#ffffff; font-weight:800; font-size:10px; border-radius:5px;"
        )
        tb.addWidget(badge)

        self.btn_menu = QPushButton("Menu")
        self.btn_menu.setObjectName("MenuBtn")
        self.btn_menu.setFixedHeight(28)
        self.btn_menu.setToolTip("File, View & Preferences")
        self._build_main_menu()
        tb.addWidget(self.btn_menu)

        self.btn_open = self._icon_btn("open", "Open PDF (Ctrl+O)", self.openClicked.emit)
        self.btn_save = self._icon_btn("save", "Save PDF (Ctrl+S)", self.saveClicked.emit)
        self.btn_print = self._icon_btn("print", "Print (Ctrl+P)", self.printClicked.emit)
        self.btn_undo = self._icon_btn("undo", "Undo (Ctrl+Z)", self.undoClicked.emit)
        self.btn_redo = self._icon_btn("redo", "Redo (Ctrl+Y)", self.redoClicked.emit)
        for b in (self.btn_open, self.btn_save, self.btn_print, self.btn_undo, self.btn_redo):
            tb.addWidget(b)

        # Inline Document Tabs right inside the Top Bar
        self.inline_tabs = QTabBar()
        self.inline_tabs.setObjectName("InlineDocTabs")
        self.inline_tabs.setDrawBase(False)
        self.inline_tabs.setTabsClosable(True)
        self.inline_tabs.setMovable(True)
        self.inline_tabs.setExpanding(False)
        self.inline_tabs.setElideMode(Qt.ElideRight)
        tb.addWidget(self.inline_tabs, stretch=1)

        # Compact Acrobat Page Navigation (↑ Prev Page | ↓ Next Page | [page] / total)
        self._total_pages = 0
        self._current_page_one = 1
        self.btn_prev = self._icon_btn("page_up", "Previous Page (Up)", self.prevPageClicked.emit, size=15)
        self.btn_next = self._icon_btn("page_down", "Next Page (Down)", self.nextPageClicked.emit, size=15)
        self.page_input = QLineEdit("1")
        self.page_input.setObjectName("PageInput")
        self.page_input.setValidator(QIntValidator(1, 99999, self))
        self.page_input.setAlignment(Qt.AlignCenter)
        self.page_input.setFixedSize(40, 26)
        self.page_input.setToolTip("Type a page number and press Enter")
        self.page_input.returnPressed.connect(self._commit_page_input)
        self.lbl_page_total = QLabel("/ 0")
        self.lbl_page_total.setStyleSheet("padding: 0 3px; font-size:12px;")
        for w in (self.btn_prev, self.btn_next, self.page_input, self.lbl_page_total):
            tb.addWidget(w)

        tb.addWidget(self._make_divider())

        # Modern Acrobat Zoom Menu Pill & Rotate
        self.btn_zoom_out = self._icon_btn("zoom_out", "Zoom Out (Ctrl+-)", self.zoomOutClicked.emit, size=15)
        self.btn_zoom_mode = QPushButton("Fit Width")
        self.btn_zoom_mode.setObjectName("ZoomMenuBtn")
        self.btn_zoom_mode.setFixedSize(110, 28)
        self.btn_zoom_mode.setIcon(make_icon("fit_width", self._icon_color, 14))
        self.btn_zoom_mode.setIconSize(QSize(14, 14))
        self.btn_zoom_mode.setToolTip("Zoom Level & Page Fit")
        self._zoom_actions: dict[str, QAction] = {}
        self._build_zoom_menu()
        self.btn_zoom_in = self._icon_btn("zoom_in", "Zoom In (Ctrl+=)", self.zoomInClicked.emit, size=15)
        self.btn_rotate = self._icon_btn("rotate", "Rotate 90° CW", self.rotateCWClicked.emit, size=15)
        for w in (self.btn_zoom_out, self.btn_zoom_mode, self.btn_zoom_in, self.btn_rotate):
            tb.addWidget(w)

        tb.addWidget(self._make_divider())

        # Compact Search Pill
        self.search_input = QLineEdit()
        self.search_input.setObjectName("SearchInput")
        self.search_input.setPlaceholderText("Find (Ctrl+F)...")
        self.search_input.setFixedSize(135, 26)
        self.search_input.textChanged.connect(self.searchChanged.emit)
        self.lbl_search_count = QLabel("0/0")
        self.lbl_search_count.setStyleSheet("font-size:11px; padding:0 2px;")
        self.btn_search_prev = self._icon_btn("up", "Previous Match", self.searchPrevClicked.emit, size=13)
        self.btn_search_next = self._icon_btn("down", "Next Match", self.searchNextClicked.emit, size=13)
        for w in (self.search_input, self.lbl_search_count, self.btn_search_prev, self.btn_search_next):
            tb.addWidget(w)

        root.addWidget(self.top_bar)

        # 2. Slim 36px Contextual Bar (only visible when sub-tools are active)
        self.context_bar = QFrame()
        self.context_bar.setObjectName("ContextBar")
        self.context_bar.setFixedHeight(36)
        self.dynamic_layout = QHBoxLayout(self.context_bar)
        self.dynamic_layout.setContentsMargins(10, 3, 10, 3)
        self.dynamic_layout.setSpacing(5)
        root.addWidget(self.context_bar)
        self.context_bar.hide()

    def _icon_btn(self, icon_name: str, tip: str, slot, size: int = 16) -> QPushButton:
        btn = QPushButton()
        btn.setIcon(make_icon(icon_name, self._icon_color, size))
        btn.setIconSize(QSize(size, size))
        btn.setFixedSize(28, 28)
        btn.setToolTip(tip)
        btn.clicked.connect(slot)
        self._tool_buttons.append((btn, icon_name))
        return btn

    def _build_main_menu(self) -> None:
        menu = QMenu(self)
        menu.addAction("Open PDF... (Ctrl+O)", self.openClicked.emit)
        menu.addAction("Save (Ctrl+S)", self.saveClicked.emit)
        menu.addAction("Save As... (Ctrl+Shift+S)", self.saveAsClicked.emit)
        menu.addAction("Print... (Ctrl+P)", self.printClicked.emit)
        menu.addSeparator()
        menu.addAction("Document Properties...", self.propsClicked.emit)
        menu.addAction("Toggle Dark / Light Theme", self.themeToggled.emit)
        accent_sub = menu.addMenu("Accent Color")
        for name, col, hov in ACCENT_PRESETS:
            accent_sub.addAction(name, lambda c=col, h=hov: self.accentChanged.emit(c, h))
        self.btn_menu.setMenu(menu)

    @staticmethod
    def _make_divider() -> QFrame:
        sep = QFrame()
        sep.setObjectName("TopDivider")
        sep.setFixedSize(1, 20)
        return sep

    def _build_zoom_menu(self) -> None:
        menu = QMenu(self)
        grp = QActionGroup(menu)
        grp.setExclusive(True)
        presets = ["Fit Width", "Fit Page", None, "75%", "100%", "125%", "150%", "200%", "300%"]
        for item in presets:
            if item is None:
                menu.addSeparator()
                continue
            act = QAction(item, menu)
            act.setCheckable(True)
            act.setChecked(item == "Fit Width")
            act.triggered.connect(lambda _chk=False, lbl=item: self._select_zoom_preset(lbl))
            grp.addAction(act)
            menu.addAction(act)
            self._zoom_actions[item] = act
        self.btn_zoom_mode.setMenu(menu)

    def _select_zoom_preset(self, label: str) -> None:
        self.set_zoom_label(label)
        self.zoomModeChanged.emit(label)

    def set_zoom_label(self, label: str) -> None:
        self.btn_zoom_mode.setText(label)
        for key, act in self._zoom_actions.items():
            act.setChecked(key == label)

    def _commit_page_input(self) -> None:
        txt = self.page_input.text().strip()
        if not txt or self._total_pages <= 0:
            self.page_input.setText(str(self._current_page_one))
            return
        target = min(max(1, int(txt)), self._total_pages)
        self.page_input.setText(str(target))
        self.pageJumpRequested.emit(target - 1)
        self.page_input.clearFocus()

    def refresh_icon_theme(self, dark: bool) -> None:
        self._icon_color = "#e2e8f0" if dark else "#334155"
        self.btn_menu.setIcon(make_icon("menu", self._icon_color, 15))
        self.btn_zoom_mode.setIcon(make_icon("fit_width", self._icon_color, 14))
        for btn, icon_name in self._tool_buttons:
            btn.setIcon(make_icon(icon_name, self._icon_color, btn.iconSize().width()))

    def set_mode(self, mode: str) -> None:
        self.current_mode = mode
        self._build_dynamic_tools(mode)
        self.modeChanged.emit(mode)

    def update_page_info(self, current_zero: int, total: int) -> None:
        self._total_pages = max(0, total)
        cur = min(max(1, current_zero + 1), max(1, total)) if total > 0 else 0
        self._current_page_one = cur
        self.page_input.setText(str(cur) if total > 0 else "0")
        self.lbl_page_total.setText(f"/ {total}")
        self.btn_prev.setEnabled(total > 0 and current_zero > 0)
        self.btn_next.setEnabled(total > 0 and current_zero < total - 1)

    def _clear_dynamic_tools(self) -> None:
        while self.dynamic_layout.count():
            item = self.dynamic_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.hide()
                w.setParent(None)
                w.deleteLater()

    def _add_tool_btn(
        self,
        text: str,
        action_name: str,
        checkable: bool = False,
        checked: bool = False,
        danger: bool = False,
        extra: dict | None = None,
    ) -> QPushButton:
        btn = QPushButton(text)
        if checkable:
            btn.setCheckable(True)
            btn.setChecked(checked)
        if danger:
            btn.setObjectName("DangerBtn")
        else:
            btn.setObjectName("PillBtn")
        btn.clicked.connect(lambda _=False, a=action_name, ex=extra or {}: self.toolAction.emit(a, ex))
        self.dynamic_layout.addWidget(btn)
        return btn

    def _build_dynamic_tools(self, mode: str) -> None:
        self._clear_dynamic_tools()
        if mode == "view":
            # In View mode, hide the contextual strip completely to maximize top space
            self.context_bar.hide()
            return

        self.context_bar.show()
        if mode == "edit":
            grp = QButtonGroup(self.context_bar)
            grp.setExclusive(True)
            b_in = self._add_tool_btn("Edit Text", "tool:edit_text", checkable=True, checked=True)
            b_add = self._add_tool_btn("Add Text", "tool:add_text", checkable=True)
            b_wo = self._add_tool_btn("Whiteout", "tool:whiteout", checkable=True)
            grp.addButton(b_in)
            grp.addButton(b_add)
            grp.addButton(b_wo)
            self._add_tool_btn("Insert Image", "insert_image")
            self._add_tool_btn("Word-Flow Editor", "open_word_flow")
            self._add_tool_btn("Watermark", "open_watermark")
            self._add_tool_btn("Bates Numbers", "open_bates")

        elif mode == "annotate":
            grp = QButtonGroup(self.context_bar)
            grp.setExclusive(True)
            for label, tname, chk in [
                ("Select", "tool:select", False),
                ("Pen", "tool:pen", True),
                ("Highlight", "tool:highlighter", False),
                ("Underline", "tool:underline", False),
                ("Strike", "tool:strikethrough", False),
                ("Note", "tool:note", False),
                ("Rect", "tool:rect", False),
                ("Circle", "tool:ellipse", False),
                ("Line", "tool:line", False),
                ("Arrow", "tool:arrow", False),
                ("Eraser", "tool:eraser", False),
            ]:
                b = self._add_tool_btn(label, tname, checkable=True, checked=chk)
                grp.addButton(b)
            self._add_tool_btn("Color", "pick_color")
            stamp_combo = QComboBox()
            stamp_combo.addItems(["Stamp...", "APPROVED", "REJECTED", "CONFIDENTIAL", "DRAFT", "FINAL", "PAID", "Custom..."])
            def _on_stamp_picked(val: str, cb: QComboBox = stamp_combo) -> None:
                if val == "Stamp...":
                    return
                self.toolAction.emit("place_stamp", {"label": val})
                cb.blockSignals(True)
                cb.setCurrentIndex(0)
                cb.blockSignals(False)

            stamp_combo.currentTextChanged.connect(_on_stamp_picked)
            self.dynamic_layout.addWidget(stamp_combo)

        elif mode == "organize":
            self._add_tool_btn("Visual Page Grid", "open_organizer")
            self._add_tool_btn("+ Blank Page", "insert_blank")
            self._add_tool_btn("Rotate 90° CW", "rotate_cw")
            self._add_tool_btn("Combine PDFs", "open_merge")
            self._add_tool_btn("Split PDF", "open_split")

        elif mode == "forms":
            self._add_tool_btn("Place Signature", "open_signature")
            self._add_tool_btn("Flatten Forms", "flatten_forms")

        elif mode == "protect":
            self._add_tool_btn("Encrypt (AES-256)", "open_protect")
            self._add_tool_btn("Redact Area", "tool:redact", checkable=True, danger=True)

        elif mode == "export":
            self._add_tool_btn("Export Page (PNG)", "export_png")
            self._add_tool_btn("Export All Pages", "export_all_png")
            self._add_tool_btn("Extract Text (TXT)", "export_text")

        self.dynamic_layout.addStretch()


class LeftToolsRail(QFrame):
    """Adobe Acrobat-style compact left vertical tool rail with stacked SVG icon + label."""

    modeSelected = Signal(str)
    toolAction = Signal(str, dict)

    ITEMS = [
        ("view", "view", "View"),
        ("edit", "edit", "Edit"),
        ("annotate", "annotate", "Draw"),
        ("organize", "organize", "Pages"),
        ("forms", "forms", "Sign"),
        ("protect", "protect", "Protect"),
        ("export", "export", "Export"),
    ]

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("LeftToolsRail")
        self.setFixedWidth(58)
        self._icon_color = "#334155"

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 8, 4, 8)
        layout.setSpacing(4)

        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.buttons: dict[str, tuple[QToolButton, str]] = {}

        for key, icon_name, label in self.ITEMS:
            btn = QToolButton()
            btn.setObjectName("RailBtn")
            btn.setText(label)
            btn.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
            btn.setCheckable(True)
            btn.setToolTip(label)
            btn.setIcon(make_icon(icon_name, self._icon_color, 18))
            btn.setIconSize(QSize(18, 18))
            btn.setFixedSize(50, 48)
            btn.clicked.connect(lambda _chk, k=key: self.modeSelected.emit(k))
            self.group.addButton(btn)
            self.buttons[key] = (btn, icon_name)
            layout.addWidget(btn)

        layout.addSpacing(8)
        self.btn_pan = QToolButton()
        self.btn_pan.setObjectName("RailBtn")
        self.btn_pan.setText("Pan")
        self.btn_pan.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
        self.btn_pan.setCheckable(True)
        self.btn_pan.setToolTip("Pan / Hand Scroll")
        self.btn_pan.setIcon(make_icon("pan", self._icon_color, 16))
        self.btn_pan.setIconSize(QSize(16, 16))
        self.btn_pan.setFixedSize(50, 42)
        self.btn_pan.clicked.connect(
            lambda chk: self.toolAction.emit("tool:pan" if chk else "tool:select", {})
        )
        layout.addWidget(self.btn_pan)

        self.btn_copy = QToolButton()
        self.btn_copy.setObjectName("RailBtn")
        self.btn_copy.setText("Copy")
        self.btn_copy.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
        self.btn_copy.setToolTip("Copy Selected Text")
        self.btn_copy.setIcon(make_icon("copy", self._icon_color, 16))
        self.btn_copy.setIconSize(QSize(16, 16))
        self.btn_copy.setFixedSize(50, 42)
        self.btn_copy.clicked.connect(lambda: self.toolAction.emit("copy_selection", {}))
        layout.addWidget(self.btn_copy)

        layout.addStretch()
        self.buttons["view"][0].setChecked(True)

    def set_active_mode(self, mode: str) -> None:
        if mode in self.buttons:
            self.buttons[mode][0].setChecked(True)
        if mode != "view":
            self.btn_pan.setChecked(False)

    def refresh_icon_theme(self, dark: bool) -> None:
        self._icon_color = "#e2e8f0" if dark else "#334155"
        for btn, icon_name in self.buttons.values():
            btn.setIcon(make_icon(icon_name, self._icon_color, 18))
        self.btn_pan.setIcon(make_icon("pan", self._icon_color, 16))
        self.btn_copy.setIcon(make_icon("copy", self._icon_color, 16))
