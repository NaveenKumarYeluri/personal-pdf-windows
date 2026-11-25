"""Top bar, 7-mode ribbon, contextual sub-toolbar, and right quick-tools panel."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.core.fonts import available_ui_fonts
from app.ui.theme import ACCENT_PRESETS


class RibbonHeader(QWidget):
    """Unified header containing File Bar, Mode Tabs, and Contextual Sub-Toolbar."""

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

    MODES = [
        ("view", "View & Read"),
        ("annotate", "Annotate & Markup"),
        ("organize", "Organize Pages"),
        ("edit", "Edit & Watermark"),
        ("forms", "Fill & Sign"),
        ("protect", "Protect & Redact"),
        ("export", "Export"),
    ]

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 1. Top Menu / File Bar
        top_bar = QFrame()
        top_bar.setObjectName("TopBar")
        tb = QHBoxLayout(top_bar)
        tb.setContentsMargins(10, 6, 10, 6)
        tb.setSpacing(6)

        brand = QLabel("Personal PDF Suite")
        brand.setStyleSheet("font-weight:700; font-size:14px;")
        tb.addWidget(brand)
        tb.addStretch()

        self.btn_open = QPushButton("Open")
        self.btn_open.clicked.connect(self.openClicked.emit)
        self.btn_save = QPushButton("Save")
        self.btn_save.setObjectName("PrimaryBtn")
        self.btn_save.clicked.connect(self.saveClicked.emit)
        self.btn_save_as = QPushButton("Save As")
        self.btn_save_as.clicked.connect(self.saveAsClicked.emit)
        self.btn_print = QPushButton("Print")
        self.btn_print.clicked.connect(self.printClicked.emit)
        self.btn_theme = QPushButton("Theme")
        self.btn_theme.clicked.connect(self.themeToggled.emit)
        self.btn_accent = QPushButton("Accent")
        self._build_accent_menu()
        self.btn_props = QPushButton("Properties")
        self.btn_props.clicked.connect(self.propsClicked.emit)
        self.btn_tools_pane = QPushButton("Tools")
        self.btn_tools_pane.clicked.connect(self.toggleToolsPaneClicked.emit)

        for b in (
            self.btn_open,
            self.btn_save,
            self.btn_save_as,
            self.btn_print,
            self.btn_theme,
            self.btn_accent,
            self.btn_props,
            self.btn_tools_pane,
        ):
            tb.addWidget(b)
        root.addWidget(top_bar)

        # 2. Mode Ribbon Tabs
        mode_bar = QFrame()
        mode_bar.setObjectName("ModeRibbon")
        mb = QHBoxLayout(mode_bar)
        mb.setContentsMargins(8, 0, 8, 0)
        mb.setSpacing(2)
        self.mode_group = QButtonGroup(self)
        self.mode_group.setExclusive(True)
        self.mode_buttons: dict[str, QPushButton] = {}
        for key, title in self.MODES:
            btn = QPushButton(title)
            btn.setObjectName("ModeTabBtn")
            btn.setCheckable(True)
            btn.clicked.connect(lambda _chk, k=key: self._select_mode(k))
            self.mode_group.addButton(btn)
            self.mode_buttons[key] = btn
            mb.addWidget(btn)
        mb.addStretch()
        root.addWidget(mode_bar)

        # 3. Sub-Toolbar (universal navigation + dynamic mode controls + search)
        sub_bar = QFrame()
        sub_bar.setObjectName("SubToolbar")
        sb = QHBoxLayout(sub_bar)
        sb.setContentsMargins(8, 5, 8, 5)
        sb.setSpacing(5)

        self.btn_sidebar = QPushButton("Sidebar")
        self.btn_sidebar.setCheckable(True)
        self.btn_sidebar.setChecked(True)
        self.btn_sidebar.clicked.connect(self.toggleSidebarClicked.emit)
        self.btn_undo = QPushButton("Undo")
        self.btn_undo.clicked.connect(self.undoClicked.emit)
        self.btn_redo = QPushButton("Redo")
        self.btn_redo.clicked.connect(self.redoClicked.emit)

        self.btn_prev = QPushButton("◀")
        self.btn_prev.setFixedWidth(30)
        self.btn_prev.clicked.connect(self.prevPageClicked.emit)
        self.page_spin = QSpinBox()
        self.page_spin.setRange(1, 1)
        self.page_spin.setFixedWidth(56)
        self.page_spin.valueChanged.connect(lambda v: self.pageJumpRequested.emit(v - 1))
        self.lbl_page_total = QLabel("/ 0")
        self.btn_next = QPushButton("▶")
        self.btn_next.setFixedWidth(30)
        self.btn_next.clicked.connect(self.nextPageClicked.emit)

        self.btn_zoom_out = QPushButton("−")
        self.btn_zoom_out.setFixedWidth(28)
        self.btn_zoom_out.clicked.connect(self.zoomOutClicked.emit)
        self.zoom_combo = QComboBox()
        self.zoom_combo.addItems(["Fit Width", "Fit Page", "75%", "100%", "125%", "150%", "200%", "300%"])
        self.zoom_combo.currentTextChanged.connect(self.zoomModeChanged.emit)
        self.btn_zoom_in = QPushButton("+")
        self.btn_zoom_in.setFixedWidth(28)
        self.btn_zoom_in.clicked.connect(self.zoomInClicked.emit)
        self.btn_rotate = QPushButton("↻")
        self.btn_rotate.setToolTip("Rotate 90° CW")
        self.btn_rotate.setFixedWidth(30)
        self.btn_rotate.clicked.connect(self.rotateCWClicked.emit)

        for w in (
            self.btn_sidebar,
            self.btn_undo,
            self.btn_redo,
            self.btn_prev,
            self.page_spin,
            self.lbl_page_total,
            self.btn_next,
            self.btn_zoom_out,
            self.zoom_combo,
            self.btn_zoom_in,
            self.btn_rotate,
        ):
            sb.addWidget(w)

        # Dynamic controls area
        self.dynamic_host = QWidget()
        self.dynamic_layout = QHBoxLayout(self.dynamic_host)
        self.dynamic_layout.setContentsMargins(6, 0, 6, 0)
        self.dynamic_layout.setSpacing(4)
        sb.addWidget(self.dynamic_host, stretch=1)

        # Search box
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Find (Ctrl+F)...")
        self.search_input.setFixedWidth(155)
        self.search_input.textChanged.connect(self.searchChanged.emit)
        self.lbl_search_count = QLabel("0/0")
        self.btn_search_prev = QPushButton("▲")
        self.btn_search_prev.setFixedWidth(26)
        self.btn_search_prev.clicked.connect(self.searchPrevClicked.emit)
        self.btn_search_next = QPushButton("▼")
        self.btn_search_next.setFixedWidth(26)
        self.btn_search_next.clicked.connect(self.searchNextClicked.emit)
        sb.addWidget(self.search_input)
        sb.addWidget(self.lbl_search_count)
        sb.addWidget(self.btn_search_prev)
        sb.addWidget(self.btn_search_next)

        root.addWidget(sub_bar)

        self.current_mode = "view"
        self.mode_buttons["view"].setChecked(True)
        self._build_dynamic_tools("view")

    def _build_accent_menu(self) -> None:
        menu = QMenu(self)
        for name, col, hov in ACCENT_PRESETS:
            act = menu.addAction(name)
            act.triggered.connect(lambda _=False, c=col, h=hov: self.accentChanged.emit(c, h))
        self.btn_accent.setMenu(menu)

    def _select_mode(self, mode: str) -> None:
        self.current_mode = mode
        if mode in self.mode_buttons:
            self.mode_buttons[mode].setChecked(True)
        self._build_dynamic_tools(mode)
        self.modeChanged.emit(mode)

    def set_mode(self, mode: str) -> None:
        self._select_mode(mode)

    def update_page_info(self, current_zero: int, total: int) -> None:
        self.page_spin.blockSignals(True)
        self.page_spin.setRange(1, max(1, total))
        self.page_spin.setValue(min(max(1, current_zero + 1), max(1, total)))
        self.page_spin.blockSignals(False)
        self.lbl_page_total.setText(f"/ {total}")

    def _clear_dynamic_tools(self) -> None:
        while self.dynamic_layout.count():
            item = self.dynamic_layout.takeAt(0)
            w = item.widget()
            if w is not None:
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
        btn.clicked.connect(lambda _=False, a=action_name, ex=extra or {}: self.toolAction.emit(a, ex))
        self.dynamic_layout.addWidget(btn)
        return btn

    def _build_dynamic_tools(self, mode: str) -> None:
        self._clear_dynamic_tools()
        if mode == "view":
            grp = QButtonGroup(self.dynamic_host)
            grp.setExclusive(True)
            b_sel = self._add_tool_btn("Select Text", "tool:select", checkable=True, checked=True)
            b_pan = self._add_tool_btn("Pan", "tool:pan", checkable=True)
            grp.addButton(b_sel)
            grp.addButton(b_pan)
            self._add_tool_btn("Copy Text", "copy_selection")

        elif mode == "annotate":
            grp = QButtonGroup(self.dynamic_host)
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
                ("Eraser", "tool:eraser", False),
            ]:
                b = self._add_tool_btn(label, tname, checkable=True, checked=chk)
                grp.addButton(b)
            self._add_tool_btn("Color", "pick_color")
            stamp_combo = QComboBox()
            stamp_combo.addItems(["Stamp...", "APPROVED", "REJECTED", "CONFIDENTIAL", "DRAFT", "FINAL", "PAID", "Custom..."])
            stamp_combo.currentTextChanged.connect(
                lambda val: val != "Stamp..." and self.toolAction.emit("place_stamp", {"label": val})
            )
            self.dynamic_layout.addWidget(stamp_combo)

        elif mode == "organize":
            self._add_tool_btn("Visual Page Grid", "open_organizer")
            self._add_tool_btn("+ Blank Page", "insert_blank")
            self._add_tool_btn("Rotate 90° CW", "rotate_cw")
            self._add_tool_btn("Combine PDFs...", "open_merge")
            self._add_tool_btn("Split PDF...", "open_split")

        elif mode == "edit":
            grp = QButtonGroup(self.dynamic_host)
            grp.setExclusive(True)
            b_in = self._add_tool_btn("In-Place Text", "tool:edit_text", checkable=True, checked=True)
            b_add = self._add_tool_btn("Add Text", "tool:add_text", checkable=True)
            b_wo = self._add_tool_btn("Whiteout", "tool:whiteout", checkable=True)
            grp.addButton(b_in)
            grp.addButton(b_add)
            grp.addButton(b_wo)
            self._add_tool_btn("Image...", "insert_image")
            self._add_tool_btn("Word-Flow Mode", "open_word_flow")
            self._add_tool_btn("Watermark...", "open_watermark")
            self._add_tool_btn("Bates Numbers...", "open_bates")

        elif mode == "forms":
            self._add_tool_btn("Place Signature...", "open_signature")
            self._add_tool_btn("Flatten Form Fields", "flatten_forms")

        elif mode == "protect":
            self._add_tool_btn("Encrypt (AES-256)...", "open_protect")
            self._add_tool_btn("Add Redaction Box", "tool:redact", checkable=True, danger=True)

        elif mode == "export":
            self._add_tool_btn("Export Page (PNG)...", "export_png")
            self._add_tool_btn("Export All Pages...", "export_all_png")
            self._add_tool_btn("Extract Text (TXT)...", "export_text")

        self.dynamic_layout.addStretch()
