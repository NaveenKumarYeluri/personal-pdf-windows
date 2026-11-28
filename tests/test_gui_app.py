"""Automated Qt6 GUI end-to-end verification tests using pytest-qt."""

from __future__ import annotations

from pathlib import Path

import pymupdf
import shiboken6
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QComboBox, QPushButton

from app.ui.main_window import MainWindow

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SQL_PDF = FIXTURES / "SQL Server Advanced Concepts.pdf"


def test_gui_load_search_theme_and_sequential_in_place_editing(qtbot, tmp_path):
    win = MainWindow()
    qtbot.addWidget(win)
    win.show()

    # 1. Open real PDF document and verify compact Acrobat-style header + uniform white paper
    tab = win.open_pdf_path(str(SQL_PDF))
    assert tab is not None
    assert tab.engine.page_count == 13
    assert win.tabs.count() == 1
    assert win.ribbon.top_bar.height() <= 48
    assert not win.ribbon.context_bar.isVisible()

    # Render page widget and assert background margins are pure white (255,255,255), NOT grey
    pw1 = tab.page_widgets[0]
    rendered_img = pw1.grab().toImage()
    margin_px = rendered_img.pixelColor(12, 12)
    body_gap_px = rendered_img.pixelColor(12, int(pw1.height() * 0.45))
    assert (margin_px.red(), margin_px.green(), margin_px.blue()) == (255, 255, 255)
    assert (body_gap_px.red(), body_gap_px.green(), body_gap_px.blue()) == (255, 255, 255)

    # 1b. Modern page navigation (Down/Next & Up/Prev) and Fit Width zoom pill
    assert win.ribbon.page_input.text() == "1"
    assert win.ribbon.btn_zoom_mode.text() == "Fit Width"
    win.ribbon.btn_next.click()
    qtbot.wait(20)
    assert tab.current_page == 1
    assert win.ribbon.page_input.text() == "2"
    assert abs(tab.verticalScrollBar().value() - (tab.page_widgets[1].y() - 12)) <= 2

    win.ribbon.btn_next.click()
    qtbot.wait(20)
    assert tab.current_page == 2
    assert win.ribbon.page_input.text() == "3"

    # Clicking Up (btn_prev) MUST go to previous page (Page 2, index 1), NOT forward!
    win.ribbon.btn_prev.click()
    qtbot.wait(20)
    assert tab.current_page == 1
    assert win.ribbon.page_input.text() == "2"
    assert abs(tab.verticalScrollBar().value() - (tab.page_widgets[1].y() - 12)) <= 2

    win.ribbon.btn_prev.click()
    qtbot.wait(20)
    assert tab.current_page == 0
    assert win.ribbon.page_input.text() == "1"

    # Typing page number + Enter jumps cleanly, then reset back to Page 1
    win.ribbon.page_input.setText("4")
    QTest.keyClick(win.ribbon.page_input, Qt.Key_Return)
    qtbot.wait(20)
    assert tab.current_page == 3
    win.go_to_page(0)

    # Zoom menu pill updates when stepping zoom and switching back to Fit Width
    win.ribbon.btn_zoom_in.click()
    assert win.ribbon.btn_zoom_mode.text().endswith("%")
    win.ribbon._zoom_actions["Fit Width"].trigger()
    assert win.ribbon.btn_zoom_mode.text() == "Fit Width"
    assert tab.zoom_mode == "Fit Width"

    # 2. Search functionality
    win.ribbon.search_input.setText("Heap")
    assert len(tab.search_hits) > 0
    assert "/" in win.ribbon.lbl_search_count.text()

    # 3. Theme & accent toggling
    win.toggle_theme()
    assert win.dark_theme is True
    win.set_accent_theme("#2563eb", "#1d4ed8")
    assert win.accent_color == "#2563eb"

    # 4. Switch to Edit mode and click Paragraph 1 directly on PDFPageWidget
    win.ribbon.set_mode("edit")
    pw1 = tab.page_widgets[0]
    blk1 = next(b for b in tab.engine.extract_text_blocks(0) if "Physically, SQL Server tables" in b.plain_text)
    cx = int(((blk1.rect[0] + blk1.rect[2]) / 2.0) * pw1.zoom)
    cy = int(((blk1.rect[1] + blk1.rect[3]) / 2.0) * pw1.zoom)

    QTest.mouseClick(pw1, Qt.LeftButton, Qt.NoModifier, QPoint(cx, cy))
    assert pw1.active_editor is not None
    assert win.floating_bar.isVisible()

    # 5. Scoped Ctrl+A inside editor must NOT move scrollbars
    scroll_before = tab.verticalScrollBar().value()
    QTest.keyClick(pw1.active_editor, Qt.Key_A, Qt.ControlModifier)
    assert tab.verticalScrollBar().value() == scroll_before
    assert pw1.active_editor.textCursor().selectedText().strip() != ""

    # Test Esc revert cleanly cancels without modifying PDF and hides floating bar
    QTest.keyClick(pw1.active_editor, Qt.Key_Escape)
    QApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    qtbot.wait(10)
    assert pw1.active_editor is None
    assert win._active_editor is None
    assert not win.floating_bar.isVisible()
    assert not tab.engine.in_place_edits.get(0)

    # Scrolling right after Esc must not raise on deleted InPlaceTextEditor
    tab.verticalScrollBar().setValue(scroll_before + 40)
    tab.verticalScrollBar().setValue(scroll_before)

    # 6. Click again, edit text + bold formatting via FloatingFormatBar, then click Paragraph 2 directly
    QTest.mouseClick(pw1, Qt.LeftButton, Qt.NoModifier, QPoint(cx, cy))
    ed1 = pw1.active_editor
    assert ed1 is not None
    ed1.selectAll()
    QTest.keyClicks(ed1, "GUI Edited Paragraph One")
    win.floating_bar.btn_bold.click()

    blk2 = next(b for b in tab.engine.extract_text_blocks(0) if "In the Heap table" in b.plain_text)
    cx2 = int(((blk2.rect[0] + blk2.rect[2]) / 2.0) * pw1.zoom)
    cy2 = int(((blk2.rect[1] + blk2.rect[3]) / 2.0) * pw1.zoom)
    QTest.mouseClick(pw1, Qt.LeftButton, Qt.NoModifier, QPoint(cx2, cy2))

    # Previous edit must have committed automatically and new editor opened on the live page widget
    assert tab.page_widgets[0] is pw1
    assert len(tab.engine.in_place_edits.get(0, {})) == 1
    assert pw1.active_editor is not None
    pw1.active_editor.selectAll()
    QTest.keyClicks(pw1.active_editor, "GUI Edited Paragraph Two")
    win.floating_bar.btn_done.click()
    QApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    assert len(tab.engine.in_place_edits.get(0, {})) == 2
    assert not win.floating_bar.isVisible()

    # 7. Save final vector PDF to disk and verify contents
    out_pdf = tmp_path / "gui_saved.pdf"
    out_bytes = tab.engine.build_final_pdf_bytes()
    out_pdf.write_bytes(out_bytes)
    doc = pymupdf.open(str(out_pdf))
    txt = doc[0].get_text("text")
    assert "GUI Edited Paragraph One" in txt
    assert "GUI Edited Paragraph Two" in txt
    assert "Physically, SQL Server tables" not in txt
    doc.close()

    # 8. Test Undo reverts latest edit
    win.undo()
    assert len(tab.engine.in_place_edits.get(0, {})) == 1


def test_scroll_after_editor_cancel_unchanged_commit_and_deletion(qtbot):
    win = MainWindow()
    qtbot.addWidget(win)
    win.show()

    tab = win.load_sample_pdf()
    assert tab is not None
    win.ribbon.set_mode("edit")

    pw0 = tab.page_widgets[0]
    pw1 = tab.page_widgets[1]
    blk0 = tab.engine.extract_text_blocks(0)[0]
    cx0 = int(((blk0.rect[0] + blk0.rect[2]) / 2.0) * pw0.zoom)
    cy0 = int(((blk0.rect[1] + blk0.rect[3]) / 2.0) * pw0.zoom)

    # 1. Cancel via FloatingFormatBar revert button, flush deleteLater, then scroll
    QTest.mouseClick(pw0, Qt.LeftButton, Qt.NoModifier, QPoint(cx0, cy0))
    assert pw0.active_editor is not None
    assert win.floating_bar.isVisible()
    win.floating_bar.btn_revert.click()
    QApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    qtbot.wait(10)
    assert pw0.active_editor is None
    assert win._active_editor is None
    assert not win.floating_bar.isVisible()
    tab.verticalScrollBar().setValue(120)
    tab.verticalScrollBar().setValue(0)

    # 2. Commit unchanged text via btn_done, flush deleteLater, then scroll
    QTest.mouseClick(pw0, Qt.LeftButton, Qt.NoModifier, QPoint(cx0, cy0))
    assert pw0.active_editor is not None
    win.floating_bar.btn_done.click()
    QApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    qtbot.wait(10)
    assert pw0.active_editor is None
    assert win._active_editor is None
    assert not win.floating_bar.isVisible()
    tab.verticalScrollBar().setValue(140)
    tab.verticalScrollBar().setValue(0)

    # 3. Edit block on Page 1, then click on Page 2 to commit Page 1 cleanly
    QTest.mouseClick(pw0, Qt.LeftButton, Qt.NoModifier, QPoint(cx0, cy0))
    assert pw0.active_editor is not None
    pw0.active_editor.selectAll()
    QTest.keyClicks(pw0.active_editor, "Modified Heading On Page One")
    QTest.mouseClick(pw1, Qt.LeftButton, Qt.NoModifier, QPoint(10, 10))
    QApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    assert pw0.active_editor is None
    assert len(tab.engine.in_place_edits.get(0, {})) == 1
    assert not win.floating_bar.isVisible()

    # 4. Defense-in-depth: even if C++ InPlaceTextEditor is deleted externally, scroll and bar actions do not crash
    QTest.mouseClick(pw0, Qt.LeftButton, Qt.NoModifier, QPoint(cx0, cy0))
    ed = win._active_editor
    assert ed is not None
    shiboken6.delete(ed)
    tab.verticalScrollBar().setValue(80)
    win.floating_bar.btn_bold.click()
    assert win._active_editor is None
    assert not win.floating_bar.isVisible()

    # 5. Closing a tab while an editor is open cleanly clears active editor & floating bar
    QTest.mouseClick(pw0, Qt.LeftButton, Qt.NoModifier, QPoint(cx0, cy0))
    assert win._active_editor is not None
    win._close_tab(0)
    QApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    assert win._active_editor is None
    assert not win.floating_bar.isVisible()


def test_pan_add_text_shortcuts_tabs_and_annotation_undo(qtbot):
    win = MainWindow()
    qtbot.addWidget(win)
    win.show()

    tab0 = win.load_sample_pdf()
    tab1 = win.open_pdf_path(str(SQL_PDF))
    assert tab0 is not None and tab1 is not None
    assert win.tabs.count() == 2

    # 1. Search label syncs across tab switch & reordering tabs keeps doc_stack aligned
    win.ribbon.search_input.setText("Heap")
    assert len(tab1.search_hits) > 0
    assert win.ribbon.lbl_search_count.text().startswith("1/")
    win.tabs.setCurrentIndex(0)
    assert win.current_tab() is tab0
    assert win.ribbon.lbl_search_count.text() == "0/0"

    win.tabs.moveTab(0, 1)
    assert win.tabs.currentIndex() == 1
    assert win.current_tab() is tab0
    win.tabs.setCurrentIndex(0)
    assert win.current_tab() is tab1

    # Close tab1 (SQL_PDF at index 0), leaving tab0 (Sample)
    win._close_tab(0)
    assert win.tabs.count() == 1
    assert win.current_tab() is tab0

    # 2. Pan tool drags scrollbars smoothly and unchecks when switching tools
    win.left_rail.btn_pan.click()
    assert win.left_rail.btn_pan.isChecked()
    pw0 = tab0.page_widgets[0]
    assert pw0.active_tool == "pan"
    start_scroll = tab0.verticalScrollBar().value()
    QTest.mousePress(pw0, Qt.LeftButton, Qt.NoModifier, QPoint(100, 220))
    QTest.mouseMove(pw0, QPoint(100, 120))
    QTest.mouseRelease(pw0, Qt.LeftButton, Qt.NoModifier, QPoint(100, 120))
    assert tab0.verticalScrollBar().value() > start_scroll
    tab0.verticalScrollBar().setValue(0)

    # 3. Add Text -> Ctrl+Enter commit -> Re-edit newly added text -> Ctrl+Z inside editor
    win.ribbon.set_mode("edit")
    assert not win.left_rail.btn_pan.isChecked()
    win._set_active_tool("add_text")
    QTest.mouseClick(pw0, Qt.LeftButton, Qt.NoModifier, QPoint(int(60 * pw0.zoom), int(500 * pw0.zoom)))
    assert pw0.active_editor is not None
    pw0.active_editor.selectAll()
    QTest.keyClicks(pw0.active_editor, "First Added Note")
    QTest.keyClick(pw0.active_editor, Qt.Key_Return, Qt.ControlModifier)
    QApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    assert pw0.active_editor is None

    # Click newly added block in edit_text mode, test Ctrl+Z stays inside editor, then commit update
    win._set_active_tool("edit_text")
    added_blk = next(b for b in tab0.engine.extract_text_blocks(0) if "First Added Note" in b.plain_text)
    ax = int(((added_blk.rect[0] + added_blk.rect[2]) / 2.0) * pw0.zoom)
    ay = int(((added_blk.rect[1] + added_blk.rect[3]) / 2.0) * pw0.zoom)
    QTest.mouseClick(pw0, Qt.LeftButton, Qt.NoModifier, QPoint(ax, ay))
    ed = pw0.active_editor
    assert ed is not None
    ed.selectAll()
    QTest.keyClicks(ed, "Updated Added Note")
    # Insert a distinct undo step via cursor edit block so Ctrl+Z undoes only "XYZ"
    cur = ed.textCursor()
    cur.beginEditBlock()
    cur.insertText("XYZ")
    cur.endEditBlock()
    ed.setFocus()
    win._shortcut_undo()
    assert pw0.active_editor is ed  # editor remained open and active!
    assert "Updated Added Note" in ed.toPlainText()
    assert "XYZ" not in ed.toPlainText()
    win.floating_bar.btn_done.click()
    QApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    saved_doc = pymupdf.open(stream=tab0.engine.build_final_pdf_bytes(), filetype="pdf")
    p0_txt = saved_doc[0].get_text("text")
    assert "Updated Added Note" in p0_txt
    assert "First Added Note" not in p0_txt
    saved_doc.close()

    # 4. Low zoom (75%) must not distort font size on commit
    win.set_zoom_mode("75%")
    blk0 = tab0.engine.extract_text_blocks(0)[1]
    orig_size = blk0.font_size
    bx = int(((blk0.rect[0] + blk0.rect[2]) / 2.0) * pw0.zoom)
    by = int(((blk0.rect[1] + blk0.rect[3]) / 2.0) * pw0.zoom)
    QTest.mouseClick(pw0, Qt.LeftButton, Qt.NoModifier, QPoint(bx, by))
    assert pw0.active_editor is not None
    assert abs(pw0.active_editor.current_format_state()["size_pt"] - round(orig_size)) <= 1
    win.floating_bar.btn_revert.click()
    QApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    # 5. Annotate mode: repeat stamp placement, delete with Undo/Redo support
    win.ribbon.set_mode("annotate")
    stamp_cb = win.ribbon.context_bar.findChild(QComboBox)
    assert stamp_cb is not None
    stamp_cb.setCurrentText("APPROVED")
    assert stamp_cb.currentIndex() == 0
    stamp_cb.setCurrentText("APPROVED")
    assert len(tab0.engine.stamps.get(0, [])) == 2
    pw0.selected_item = tab0.engine.stamps[0][-1]
    QTest.keyClick(pw0, Qt.Key_Delete)
    assert len(tab0.engine.stamps.get(0, [])) == 1
    win.undo()
    assert len(tab0.engine.stamps.get(0, [])) == 2
    win.redo()
    assert len(tab0.engine.stamps.get(0, [])) == 1

    # 6. View rotation maps widget coordinates accurately & Organize rotate_cw persists
    win.set_zoom_mode("100%")
    win.rotate_clockwise()
    assert tab0.rotation == 90
    w_pt, h_pt = tab0.engine.get_page_size(0)
    # Widget (wx, wy) = (h_pt - py, px) at 100% zoom -> inverse gives (py, h_pt - wx)
    mapped_x, mapped_y = pw0._to_pdf_pt(QPointF(h_pt - 50.0, 30.0))
    assert abs(mapped_x - 30.0) < 0.5 and abs(mapped_y - 50.0) < 0.5

    win.ribbon.set_mode("organize")
    win._on_tool_action("rotate_cw", {})
    rot_doc = pymupdf.open(stream=tab0.engine.build_final_pdf_bytes(), filetype="pdf")
    assert rot_doc[0].rotation == 90
    rot_doc.close()


def test_context_bar_replaces_buttons_cleanly_on_mode_switch(qtbot):
    win = MainWindow()
    qtbot.addWidget(win)
    win.show()

    expected = {
        "edit": ["Edit Text", "Add Text", "Whiteout", "Insert Image", "Word-Flow Editor", "Watermark", "Bates Numbers"],
        "annotate": ["Select", "Pen", "Highlight", "Underline", "Strike", "Note", "Rect", "Circle", "Line", "Arrow", "Eraser", "Color"],
        "organize": ["Visual Page Grid", "+ Blank Page", "Rotate 90° CW", "Combine PDFs", "Split PDF"],
        "forms": ["Place Signature", "Flatten Forms"],
        "protect": ["Encrypt (AES-256)", "Redact Area"],
        "export": ["Export Page (PNG)", "Export All Pages", "Extract Text (TXT)"],
    }
    for mode, labels in expected.items():
        win.set_mode(mode)
        actual = [b.text() for b in win.ribbon.context_bar.findChildren(QPushButton)]
        assert actual == labels
        assert win.ribbon.context_bar.isVisible()

    win.set_mode("view")
    assert win.ribbon.context_bar.findChildren(QPushButton) == []
    assert not win.ribbon.context_bar.isVisible()


def test_stamp_pixmap_cached_across_repaints(qtbot):
    import io
    from PIL import Image
    from app.core.models import StampItem

    win = MainWindow()
    qtbot.addWidget(win)
    win.show()
    tab = win.load_sample_pdf()
    pw0 = tab.page_widgets[0]

    buf = io.BytesIO()
    Image.new("RGBA", (600, 300), (220, 38, 38, 255)).save(buf, format="PNG")
    stp = StampItem(page_idx=0, kind="image", rect=(40, 40, 200, 120), opacity=1.0, rotation=0.0, image_bytes=buf.getvalue())
    tab.engine.stamps.setdefault(0, []).append(stp)

    pw0.grab()
    key = (stp.item_id, len(stp.image_bytes))
    assert key in pw0._stamp_pixmap_cache
    first_cache_key = pw0._stamp_pixmap_cache[key].cacheKey()
    pw0.grab()
    assert pw0._stamp_pixmap_cache[key].cacheKey() == first_cache_key


def test_insert_image_preserves_aspect_ratio_and_rejects_corrupt(qtbot, tmp_path, monkeypatch):
    from PIL import Image
    from PySide6.QtWidgets import QFileDialog, QMessageBox

    win = MainWindow()
    qtbot.addWidget(win)
    win.show()
    tab = win.load_sample_pdf()

    portrait_path = tmp_path / "portrait.png"
    Image.new("RGB", (200, 400), (37, 99, 235)).save(portrait_path)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **kw: (str(portrait_path), "Images (*.png)"))
    win._on_tool_action("insert_image", {})
    stp = tab.engine.stamps[0][-1]
    w_pt = stp.rect[2] - stp.rect[0]
    h_pt = stp.rect[3] - stp.rect[1]
    assert abs((w_pt / h_pt) - 0.5) < 0.02

    wide_path = tmp_path / "wide.png"
    Image.new("RGB", (400, 100), (22, 163, 74)).save(wide_path)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **kw: (str(wide_path), "Images (*.png)"))
    win._on_tool_action("insert_image", {})
    stp2 = tab.engine.stamps[0][-1]
    w2 = stp2.rect[2] - stp2.rect[0]
    h2 = stp2.rect[3] - stp2.rect[1]
    assert abs((w2 / h2) - 4.0) < 0.02

    bad_path = tmp_path / "bad.png"
    bad_path.write_bytes(b"not an image")
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **kw: warnings.append(a))
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **kw: (str(bad_path), "Images (*.png)"))
    win._on_tool_action("insert_image", {})
    assert len(warnings) == 1
    assert len(tab.engine.stamps[0]) == 2


def test_word_flow_guards_image_only_pdf(qtbot, monkeypatch):
    import io
    from PIL import Image
    from PySide6.QtWidgets import QMessageBox

    img_buf = io.BytesIO()
    Image.new("RGB", (200, 200), (240, 240, 240)).save(img_buf, format="PNG")
    doc = pymupdf.open()
    for _ in range(2):
        p = doc.new_page(width=400, height=600)
        p.insert_image(pymupdf.Rect(20, 20, 380, 580), stream=img_buf.getvalue())
    scanned_bytes = doc.tobytes()
    doc.close()

    win = MainWindow()
    qtbot.addWidget(win)
    win.show()
    tab = win.open_pdf_bytes(scanned_bytes, "scanned.pdf")
    assert tab is not None and tab.engine.page_count == 2

    notices = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **kw: notices.append(a))
    win._on_tool_action("open_word_flow", {})
    assert len(notices) == 1
    assert tab.engine.page_count == 2


def test_lazy_thumbnails_and_selective_page_rerender(qtbot, monkeypatch):
    win = MainWindow()
    qtbot.addWidget(win)
    win.show()

    tab = win.load_sample_pdf()
    assert tab is not None
    # Sidebar starts hidden -> thumbnails are deferred until revealed
    assert win.sidebar._thumbs_dirty is True
    assert win.sidebar.thumb_list.count() == 0

    win._toggle_sidebar_tab(0)
    assert win.sidebar.isVisible()
    assert win.sidebar._thumbs_dirty is False
    assert win.sidebar.thumb_list.count() == tab.engine.page_count

    # Editing Page 1 without overflow must NOT re-render untouched Page 2
    rendered_pages = []
    orig_render = tab.engine.render_page_rgb

    def _spy_render(pno, zoom=1.0, rotation=0):
        rendered_pages.append(pno)
        return orig_render(pno, zoom=zoom, rotation=rotation)

    monkeypatch.setattr(tab.engine, "render_page_rgb", _spy_render)
    win.ribbon.set_mode("edit")
    pw0 = tab.page_widgets[0]
    blk = tab.engine.extract_text_blocks(0)[0]
    ed = pw0.open_editor_for_block(blk)
    ed.selectAll()
    QTest.keyClicks(ed, "Selective Rerender Test")
    pw0.commit_active_editor()

    assert 0 in rendered_pages
    assert 1 not in rendered_pages


def test_new_blank_recent_dirty_and_shortcuts(qtbot, tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QFileDialog

    settings_store: dict[str, object] = {}

    class _FakeSettings:
        def __init__(self, *a, **kw):
            pass

        def value(self, key, default=None):
            return settings_store.get(key, default)

        def setValue(self, key, val):
            settings_store[key] = val

    monkeypatch.setattr("app.ui.main_window.QSettings", _FakeSettings)

    win = MainWindow()
    qtbot.addWidget(win)
    win.show()

    # 1. Create a blank PDF, verify clean title, add text -> dirty '*' appears, undo -> cleared
    tab = win.new_blank_pdf()
    assert tab is not None
    assert tab.engine.page_count == 1
    assert win.tabs.tabText(0) == "Untitled.pdf"
    assert not tab.dirty

    win.ribbon.set_mode("edit")
    win._set_active_tool("add_text")
    pw0 = tab.page_widgets[0]
    QTest.mouseClick(pw0, Qt.LeftButton, Qt.NoModifier, QPoint(int(80 * pw0.zoom), int(80 * pw0.zoom)))
    pw0.active_editor.selectAll()
    QTest.keyClicks(pw0.active_editor, "Blank Page Note")
    win.floating_bar.btn_done.click()
    QApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    assert tab.dirty is True
    assert win.tabs.tabText(0) == "* Untitled.pdf"

    win.undo()
    assert tab.dirty is False
    assert win.tabs.tabText(0) == "Untitled.pdf"
    win.redo()
    assert tab.dirty is True
    assert win.tabs.tabText(0) == "* Untitled.pdf"

    # 2. Save As clears '*' and updates Recent Files submenu
    out_pdf = tmp_path / "blank_saved.pdf"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **kw: (str(out_pdf), "PDF Files (*.pdf)"))
    win.save_as_current_pdf()
    assert out_pdf.exists()
    assert tab.dirty is False
    assert win.tabs.tabText(0) == "blank_saved.pdf"
    recent_texts = [a.text() for a in win.ribbon.recent_menu.actions()]
    assert str(out_pdf.resolve()) in recent_texts

    # 3. Zoom presets & close tab via shortcut helpers
    win.set_zoom_mode("100%")
    assert abs(tab.zoom - 1.0) < 0.01
    win._close_tab(win.tabs.currentIndex())
    assert win.tabs.count() == 0

    # Re-open from Recent Files menu action
    win.ribbon.recent_menu.actions()[0].trigger()
    assert win.tabs.count() == 1
    assert "Blank Page Note" in pymupdf.open(stream=win.current_tab().engine.build_final_pdf_bytes(), filetype="pdf")[0].get_text("text")


def test_canvas_context_menu_and_sticky_note_hover(qtbot, monkeypatch):
    from PySide6.QtWidgets import QToolTip
    from app.core.models import StampItem, StickyNote

    win = MainWindow()
    qtbot.addWidget(win)
    win.show()

    tab = win.load_sample_pdf()
    pw0 = tab.page_widgets[0]

    # 1. Selection context menu -> Highlight Selection creates undoable markup
    blk0 = tab.engine.extract_text_blocks(0)[0]
    p0 = QPoint(int(blk0.rect[0] * pw0.zoom), int(blk0.rect[1] * pw0.zoom))
    p1 = QPoint(int(blk0.rect[2] * pw0.zoom), int(blk0.rect[3] * pw0.zoom))
    QTest.mousePress(pw0, Qt.LeftButton, Qt.NoModifier, p0)
    QTest.mouseMove(pw0, p1)
    QTest.mouseRelease(pw0, Qt.LeftButton, Qt.NoModifier, p1)
    assert pw0.get_selected_text() != ""

    menu_sel = pw0.build_context_menu(QPointF(p0))
    act_hl = next(a for a in menu_sel.actions() if a.text() == "Highlight Selection")
    act_hl.trigger()
    assert len(tab.engine.markups.get(0, [])) == 1
    win.undo()
    assert len(tab.engine.markups.get(0, [])) == 0

    # 2. Overlay context menu -> Rotate, Bring to Front, Lock, Delete
    s1 = StampItem(page_idx=0, kind="stamp", rect=(80, 120, 220, 170), text="FIRST")
    s2 = StampItem(page_idx=0, kind="stamp", rect=(80, 120, 220, 170), text="SECOND")
    tab.engine.stamps[0] = [s1, s2]
    center = QPointF(150 * pw0.zoom, 145 * pw0.zoom)
    menu_ov = pw0.build_context_menu(center)
    next(a for a in menu_ov.actions() if a.text() == "Rotate Overlay 90° CW").trigger()
    assert s2.rotation == (360 - 8 + 90) % 360

    act_lock = next(a for a in menu_ov.actions() if a.text() == "Lock Position")
    act_lock.trigger()
    assert s2.locked is True
    menu_ov2 = pw0.build_context_menu(center)
    assert not next(a for a in menu_ov2.actions() if a.text() == "Delete Overlay").isEnabled()
    next(a for a in menu_ov2.actions() if a.text() == "Lock Position").trigger()
    assert s2.locked is False
    assert next(a for a in menu_ov2.actions() if a.text() == "Delete Overlay").isEnabled()
    next(a for a in menu_ov2.actions() if a.text() == "Delete Overlay").trigger()
    assert len(tab.engine.stamps[0]) == 1

    # 3. Sticky note hover shows tooltip & context menu deletes note cleanly
    from PySide6.QtGui import QMouseEvent

    nt = StickyNote(page_idx=0, x=300.0, y=300.0, text="Review this figure")
    tab.engine.sticky_notes[0] = [nt]
    tips = []
    monkeypatch.setattr(QToolTip, "showText", lambda pos, txt, w=None: tips.append(txt))
    hover_pt = QPointF(300.0 * pw0.zoom, 300.0 * pw0.zoom)
    ev = QMouseEvent(
        QEvent.MouseMove,
        hover_pt,
        QPointF(pw0.mapToGlobal(hover_pt.toPoint())),
        Qt.NoButton,
        Qt.NoButton,
        Qt.NoModifier,
    )
    QApplication.sendEvent(pw0, ev)
    assert any("Review this figure" in t for t in tips)

    menu_nt = pw0.build_context_menu(QPointF(300 * pw0.zoom, 300 * pw0.zoom))
    next(a for a in menu_nt.actions() if a.text() == "Delete Sticky Note").trigger()
    assert len(tab.engine.sticky_notes[0]) == 0
    win.undo()
    assert len(tab.engine.sticky_notes[0]) == 1


def test_organizer_rotate_merge_reorder_and_bookmark_delete(qtbot, tmp_path):
    from app.core.pdf_engine import create_sample_pdf_bytes
    from app.ui.dialogs import MergeDialog, OrganizerDialog

    sample = create_sample_pdf_bytes()
    org = OrganizerDialog(sample)
    qtbot.addWidget(org)
    item0 = org.grid.item(0)
    pm_before = item0.icon().pixmap(130, 170)
    assert pm_before.height() > pm_before.width()
    item0.setSelected(True)
    org._rotate(90)
    pm_after = item0.icon().pixmap(130, 170)
    assert pm_after.width() > pm_after.height()

    p1 = tmp_path / "one.pdf"
    p2 = tmp_path / "two.pdf"
    p1.write_bytes(sample)
    p2.write_bytes(sample)
    md = MergeDialog(current_name=None)
    qtbot.addWidget(md)
    md.file_paths = [str(p1), str(p2)]
    md.list_widget.addItems(md.file_paths)
    md.list_widget.setCurrentRow(1)
    md._move(-1)
    assert md.file_paths == [str(p2), str(p1)]
    md.list_widget.setCurrentRow(0)
    md._remove_selected()
    assert md.file_paths == [str(p1)]

    # Custom bookmark deletion via SidebarPanel
    win = MainWindow()
    qtbot.addWidget(win)
    win.show()
    tab = win.open_pdf_bytes(sample, "bm.pdf")
    tab.engine.custom_bookmarks.append(("Chapter 1", 0))
    win.sidebar.refresh_outline()
    assert len(tab.engine.custom_bookmarks) == 1
    title, pno = tab.engine.custom_bookmarks.pop(0)
    win.sidebar.refresh_outline()
    win.sidebar.bookmarkRemoved.emit(title, pno)
    assert tab.dirty is True
    assert tab.engine.custom_bookmarks == []


def test_unsaved_changes_prompt_tab_and_window_close(qtbot, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    win = MainWindow()
    qtbot.addWidget(win)
    win.show()

    pdf_path = tmp_path / "dirty_test.pdf"
    tab = win.new_blank_pdf()
    tab.engine.file_path = str(pdf_path)
    tab.engine.name = pdf_path.name
    win._set_tab_dirty(tab, True)
    tab.base_modified = True

    # 1. Cancel aborts closing tab and window
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **kw: QMessageBox.Cancel)
    win._request_close_tab(0)
    assert win.tabs.count() == 1

    win._prompt_on_close = True
    assert win.close() is False
    assert win.tabs.count() == 1

    # 2. Save persists changes before closing tab cleanly
    from app.core.models import StickyNote

    tab.engine.sticky_notes[0] = [StickyNote(page_idx=0, x=50, y=50, text="Saved before close")]
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **kw: QMessageBox.Save)
    win._request_close_tab(0)
    assert win.tabs.count() == 0
    assert pdf_path.exists()
    d = pymupdf.open(str(pdf_path))
    assert len(list(d[0].annots())) == 1
    d.close()

    # 3. Discard closes dirty window without saving
    tab2 = win.new_blank_pdf()
    win._set_tab_dirty(tab2, True)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **kw: QMessageBox.Discard)
    assert win.close() is True


def test_overlay_move_rotate_sticky_edit_undo_and_double_click_word(qtbot, monkeypatch):
    from PySide6.QtGui import QColor
    from PySide6.QtWidgets import QColorDialog, QInputDialog
    from app.core.models import StampItem, StickyNote

    win = MainWindow()
    qtbot.addWidget(win)
    win.show()

    tab = win.load_sample_pdf()
    pw0 = tab.page_widgets[0]

    # 1. Double-clicking in select mode selects word without needing drag
    blk0 = tab.engine.extract_text_blocks(0)[0]
    word_pt = QPoint(int((blk0.rect[0] + 15) * pw0.zoom), int(((blk0.rect[1] + blk0.rect[3]) / 2) * pw0.zoom))
    QTest.mouseDClick(pw0, Qt.LeftButton, Qt.NoModifier, word_pt)
    assert pw0.get_selected_text().strip() != ""
    pw0.clear_text_selection()

    # 2. Drag-moving an overlay records dirty + undoable rect change; double-clicking text stamp edits text
    stp = StampItem(page_idx=0, kind="stamp", rect=(80.0, 120.0, 220.0, 170.0), text="DRAFT")
    tab.engine.stamps[0] = [stp]
    p_center = QPoint(int(150 * pw0.zoom), int(145 * pw0.zoom))
    p_moved = QPoint(int(210 * pw0.zoom), int(195 * pw0.zoom))
    QTest.mousePress(pw0, Qt.LeftButton, Qt.NoModifier, p_center)
    QTest.mouseMove(pw0, p_moved)
    QTest.mouseRelease(pw0, Qt.LeftButton, Qt.NoModifier, p_moved)
    assert tab.dirty is True
    moved_rect = stp.rect
    assert moved_rect[0] > 100.0

    # Double-click stamp edits text rather than deleting it!
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **kw: ("FINAL", True))
    QTest.mouseDClick(pw0, Qt.LeftButton, Qt.NoModifier, p_moved)
    assert len(tab.engine.stamps[0]) == 1
    assert stp.text == "FINAL"

    win.undo()  # undo text edit
    assert stp.text == "DRAFT"
    win.undo()  # undo move
    assert stp.rect == (80.0, 120.0, 220.0, 170.0)
    win.redo()
    assert stp.rect == moved_rect

    # 3. Rotate overlay via context menu is undoable & dirty
    menu = pw0.build_context_menu(QPointF(p_moved))
    next(a for a in menu.actions() if a.text() == "Rotate Overlay 90° CW").trigger()
    assert stp.rotation == 82.0
    win.undo()
    assert stp.rotation == -8.0

    # 4. Sticky note edit & color change syncs sidebar & supports undo
    nt = StickyNote(page_idx=0, x=280.0, y=280.0, text="Initial note")
    tab.engine.sticky_notes[0] = [nt]
    win.sidebar.refresh_annotations()
    monkeypatch.setattr(QInputDialog, "getMultiLineText", lambda *a, **kw: ("Updated note", True))
    monkeypatch.setattr(QColorDialog, "getColor", lambda *a, **kw: QColor("#22c55e"))
    nt_menu = pw0.build_context_menu(QPointF(280.0 * pw0.zoom, 280.0 * pw0.zoom))
    next(a for a in nt_menu.actions() if a.text() == "Edit Sticky Note...").trigger()
    next(a for a in nt_menu.actions() if a.text() == "Note Color...").trigger()
    assert nt.text == "Updated note"
    assert nt.color_hex == "#22c55e"
    assert any("Updated note" in win.sidebar.annot_list.item(i).text() for i in range(win.sidebar.annot_list.count()))
    win.undo()
    assert nt.color_hex == "#facc15"
    win.undo()
    assert nt.text == "Initial note"


def test_interactive_forms_and_pdf_links(qtbot):
    from PySide6.QtWidgets import QLineEdit

    doc = pymupdf.open()
    doc.new_page(width=500, height=600)
    doc.new_page(width=500, height=600)
    p1 = doc[0]
    p2 = doc[1]
    p1.insert_text((40, 40), "Jump target test", fontsize=12)
    p2.insert_text((40, 40), "Page two target", fontsize=12)
    p1.insert_link({"kind": pymupdf.LINK_GOTO, "from": pymupdf.Rect(40, 60, 180, 85), "page": 1})

    w_txt = pymupdf.Widget()
    w_txt.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
    w_txt.field_name = "user_name"
    w_txt.rect = pymupdf.Rect(40, 110, 220, 138)
    p1.add_widget(w_txt)

    w_chk = pymupdf.Widget()
    w_chk.field_type = pymupdf.PDF_WIDGET_TYPE_CHECKBOX
    w_chk.field_name = "opt_in"
    w_chk.rect = pymupdf.Rect(40, 160, 65, 185)
    p1.add_widget(w_chk)

    w_cb = pymupdf.Widget()
    w_cb.field_type = pymupdf.PDF_WIDGET_TYPE_COMBOBOX
    w_cb.field_name = "plan"
    w_cb.choice_values = ["Basic", "Pro"]
    w_cb.field_value = "Basic"
    w_cb.rect = pymupdf.Rect(40, 210, 180, 238)
    p1.add_widget(w_cb)

    pdf_bytes = doc.tobytes()
    doc.close()

    win = MainWindow()
    qtbot.addWidget(win)
    win.show()
    tab = win.open_pdf_bytes(pdf_bytes, "forms_links.pdf")
    pw0 = tab.page_widgets[0]

    # Clicking internal PDF link jumps to target page
    lnk_pt = QPoint(int(100 * pw0.zoom), int(72 * pw0.zoom))
    QTest.mouseClick(pw0, Qt.LeftButton, Qt.NoModifier, lnk_pt)
    assert tab.current_page == 1
    win.go_to_page(0)

    # Interactive form fields: checkbox toggle, text field edit, and Undo back to clean
    chk_pt = QPoint(int(52 * pw0.zoom), int(172 * pw0.zoom))
    QTest.mouseClick(pw0, Qt.LeftButton, Qt.NoModifier, chk_pt)
    assert tab.engine.form_values[(0, 1)] == "Yes"
    assert tab.dirty is True

    txt_pt = QPoint(int(100 * pw0.zoom), int(124 * pw0.zoom))
    QTest.mouseClick(pw0, Qt.LeftButton, Qt.NoModifier, txt_pt)
    le = pw0.findChild(QLineEdit)
    assert le is not None
    le.setText("Bob Smith")
    QTest.keyClick(le, Qt.Key_Return)
    QApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    assert tab.engine.form_values[(0, 0)] == "Bob Smith"

    out_doc = pymupdf.open(stream=tab.engine.build_final_pdf_bytes(), filetype="pdf")
    assert next(w for w in out_doc[0].widgets() if w.field_name == "user_name").field_value == "Bob Smith"
    assert next(w for w in out_doc[0].widgets() if w.field_name == "opt_in").field_value == "Yes"
    out_doc.close()

    win.undo()
    win.undo()
    assert tab.engine.form_values == {}
    assert tab.dirty is False


def test_annotate_tool_isolation_width_and_search_shortcuts(qtbot, monkeypatch):
    from PySide6.QtGui import QColor
    from PySide6.QtWidgets import QColorDialog, QComboBox

    doc = pymupdf.open()
    doc.new_page(width=500, height=600)
    doc.new_page(width=500, height=600)
    doc[0].insert_text((40, 40), "Page one alpha match", fontsize=12)
    doc[1].insert_text((40, 40), "Page two alpha match", fontsize=12)
    pdf_bytes = doc.tobytes()
    doc.close()

    win = MainWindow()
    qtbot.addWidget(win)
    win.show()
    tab = win.open_pdf_bytes(pdf_bytes, "search_annotate.pdf")

    # 1. Search Enter / Shift+Enter navigation, deep-paragraph scroll, typing debounce, and Escape clear
    win.ribbon.search_input.setText("alpha")
    assert win.ribbon.lbl_search_count.text() == "1/2"
    QTest.keyClick(win.ribbon.search_input, Qt.Key_Return)
    assert win.ribbon.lbl_search_count.text() == "2/2"
    assert tab.current_page == 1
    QTest.keyClick(win.ribbon.search_input, Qt.Key_Return, Qt.ShiftModifier)
    assert win.ribbon.lbl_search_count.text() == "1/2"
    assert tab.current_page == 0
    QTest.keyClick(win.ribbon.search_input, Qt.Key_Escape)
    assert win.ribbon.search_input.text() == ""
    assert win.ribbon.lbl_search_count.text() == "0/0"

    # Verify search scrolls down within a page past the 1st paragraph to lower matches
    sql_tab = win.open_pdf_path(str(SQL_PDF))
    assert sql_tab is not None
    win.ribbon.search_input.clear()
    win.ribbon.search_input.setFocus()
    QTest.keyClicks(win.ribbon.search_input, "Heap")
    QTest.keyClick(win.ribbon.search_input, Qt.Key_Return)  # flushes debounced typing search
    assert len(sql_tab.search_hits) > 2
    assert win.ribbon.lbl_search_count.text().startswith("1/")
    scroll_first = sql_tab.verticalScrollBar().value()
    # Step forward to a lower match on page 0 (beyond 1st para)
    p0_hits = [h for h in sql_tab.search_hits if h.page_idx == 0]
    assert len(p0_hits) >= 2 and p0_hits[-1].rect[1] > p0_hits[0].rect[1] + 200
    for _ in range(len(p0_hits) - 1):
        win.step_search(1)
    assert sql_tab.verticalScrollBar().value() > scroll_first + 100
    # Step once more into page 1
    win.step_search(1)
    assert sql_tab.current_page == 1

    # 2. Annotate mode: Pen color/width isolated from Highlighter color/width
    win.set_mode("annotate")
    monkeypatch.setattr(QColorDialog, "getColor", lambda *a, **kw: QColor("#1d4ed8"))
    win._on_tool_action("pick_color", {})
    combos = win.ribbon.context_bar.findChildren(QComboBox)
    assert len(combos) == 2
    width_cb = combos[1]
    width_cb.setCurrentIndex(width_cb.findText("5 pt"))
    assert win.tool_state["color"] == "#1d4ed8"
    assert win.tool_state["width"] == 5.0

    # Switch to Highlighter (yellow, 14pt, 0.35), then back to Rect -> restores #1d4ed8, 5pt, 1.0
    win._on_tool_action("tool:highlighter", {})
    assert win.tool_state["color"] == "#eab308"
    assert win.tool_state["width"] == 14.0
    win._on_tool_action("tool:rect", {})
    assert win.tool_state["color"] == "#1d4ed8"
    assert win.tool_state["width"] == 5.0
    assert win.tool_state["opacity"] == 1.0


def test_pdf_print_dialog_preview_ranges_and_pdf_output(qtbot, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog
    from app.core.pdf_engine import create_sample_pdf_bytes
    from app.ui.dialogs import PdfPrintDialog

    sample = create_sample_pdf_bytes()
    dlg = PdfPrintDialog(sample, current_page_zero=1, doc_name="sample.pdf")
    qtbot.addWidget(dlg)
    dlg.show()
    qtbot.wait(20)
    assert dlg.preview.pageCount() == 2

    dlg.rb_custom.setChecked(True)
    dlg.range_input.setText("2")
    assert dlg.selected_page_indices() == [1]

    out_pdf = tmp_path / "printed_out.pdf"
    dlg.printer_combo.setCurrentText(PdfPrintDialog.PDF_FILE_OPTION)
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **kw: (str(out_pdf), "PDF Files (*.pdf)"))
    dlg.btn_print.click()
    assert dlg.result() == PdfPrintDialog.Accepted
    dlg.render_to_printer(dlg.printer)
    dlg.close_doc()

    assert out_pdf.exists()
    printed = pymupdf.open(str(out_pdf))
    assert len(printed) == 1
    printed.close()


def test_sidebar_active_indicators_and_lazy_expand_collapse(qtbot, monkeypatch):
    win = MainWindow()
    qtbot.addWidget(win)
    win.show()

    tab = win.open_pdf_path(str(SQL_PDF))
    assert tab is not None

    # Opening a 13-page PDF should only render visible pages initially, not all 13 off-screen pages
    unrendered_initial = sum(1 for pw in tab.page_widgets if pw.needs_render())
    assert unrendered_initial >= 10

    # Right-rail buttons reflect active sidebar tab cleanly
    btn_pages = win._right_rail_tab_buttons[0][0]
    btn_outline = win._right_rail_tab_buttons[1][0]
    btn_notes = win._right_rail_tab_buttons[2][0]
    assert not btn_pages.isChecked()

    win._toggle_sidebar_tab(0)
    assert win.sidebar.isVisible()
    assert btn_pages.isChecked() and not btn_outline.isChecked() and not btn_notes.isChecked()
    assert win.sidebar.tabs.currentIndex() == 0

    # Switching tabs inside sidebar updates active right-rail button immediately
    win.sidebar.tabs.setCurrentIndex(1)
    assert btn_outline.isChecked() and not btn_pages.isChecked() and not btn_notes.isChecked()

    # Track page renders during expand/collapse: must only re-render visible page(s), never off-screen pages
    rendered_pages = []
    orig_render = tab.engine.render_page_rgb

    def _spy(pno, zoom=1.0, rotation=0):
        rendered_pages.append(pno)
        return orig_render(pno, zoom=zoom, rotation=rotation)

    monkeypatch.setattr(tab.engine, "render_page_rgb", _spy)
    win._toggle_sidebar_tab(2)  # switch to Notes while open
    assert btn_notes.isChecked() and not btn_outline.isChecked()
    win._toggle_sidebar_tab(2)  # collapse sidebar
    assert not win.sidebar.isVisible()
    assert not btn_pages.isChecked() and not btn_outline.isChecked() and not btn_notes.isChecked()
    assert len(rendered_pages) <= 2
    assert 12 not in rendered_pages


def test_partial_editor_formatting_preserves_block_defaults(qtbot):
    from PySide6.QtGui import QTextCursor

    win = MainWindow()
    qtbot.addWidget(win)
    win.show()

    tab = win.open_pdf_path(str(SQL_PDF))
    assert tab is not None
    win.ribbon.set_mode("edit")
    pw0 = tab.page_widgets[0]
    blk = next(b for b in tab.engine.extract_text_blocks(0) if "Physically, SQL Server tables" in b.plain_text)
    ed = pw0.open_editor_for_block(blk)
    orig_size = blk.font_size
    cur = ed.textCursor()
    cur.setPosition(0)
    cur.movePosition(QTextCursor.Right, QTextCursor.KeepAnchor, 10)
    ed.setTextCursor(cur)
    ed.set_font_size_pt(22)
    ed.set_text_color("#dc2626")
    assert blk.font_size == orig_size
    assert blk.color_hex == "#000000"
    pw0.commit_active_editor()

    saved_doc = pymupdf.open(stream=tab.engine.build_final_pdf_bytes(), filetype="pdf")
    spans = [
        sp
        for b in saved_doc[0].get_text("dict")["blocks"]
        if b["type"] == 0
        for ln in b["lines"]
        for sp in ln["spans"]
    ]
    assert any("Physically" in s["text"] and s["size"] > 18.0 for s in spans)
    assert any("SQL Server tables" in s["text"] and s["size"] < 15.0 for s in spans)
    saved_doc.close()


def test_sticky_note_drag_and_vector_annotation_select_recolor_delete(qtbot, monkeypatch):
    from PySide6.QtGui import QColor
    from PySide6.QtWidgets import QColorDialog
    from app.core.models import ShapeItem, StickyNote, TextMarkup

    win = MainWindow()
    qtbot.addWidget(win)
    win.show()

    tab = win.open_pdf_path(str(SQL_PDF))
    assert tab is not None
    pw0 = tab.page_widgets[0]
    win.set_mode("view")

    nt = StickyNote(page_idx=0, x=140.0, y=220.0, text="Drag me")
    tab.engine.sticky_notes[0] = [nt]
    QTest.mousePress(pw0, Qt.LeftButton, Qt.NoModifier, QPoint(int(140 * pw0.zoom), int(220 * pw0.zoom)))
    QTest.mouseMove(pw0, QPoint(int(210 * pw0.zoom), int(260 * pw0.zoom)))
    QTest.mouseRelease(pw0, Qt.LeftButton, Qt.NoModifier, QPoint(int(210 * pw0.zoom), int(260 * pw0.zoom)))
    assert nt.x > 180.0 and nt.y > 240.0
    win.undo()
    assert abs(nt.x - 140.0) < 0.5 and abs(nt.y - 220.0) < 0.5

    sh = ShapeItem(page_idx=0, shape_type="rect", rect=(60.0, 300.0, 180.0, 360.0), color_hex="#ef4444")
    mk = TextMarkup(page_idx=0, markup_type="highlight", rects=[(200.0, 300.0, 260.0, 316.0)])
    tab.engine.shapes[0] = [sh]
    tab.engine.markups[0] = [mk]
    QTest.mouseClick(pw0, Qt.LeftButton, Qt.NoModifier, QPoint(int(120 * pw0.zoom), int(300 * pw0.zoom)))
    assert pw0.selected_item is sh
    QTest.keyClick(pw0, Qt.Key_Delete)
    assert tab.engine.shapes[0] == []
    win.undo()
    assert len(tab.engine.shapes[0]) == 1

    mk_menu = pw0.build_context_menu(QPointF(230 * pw0.zoom, 308 * pw0.zoom))
    monkeypatch.setattr(QColorDialog, "getColor", lambda *a, **kw: QColor("#10b981"))
    next(a for a in mk_menu.actions() if a.text() == "Change Color...").trigger()
    assert mk.color_hex == "#10b981"
    QTest.keyClick(pw0, Qt.Key_Delete)
    assert tab.engine.markups[0] == []
    win.undo()
    assert len(tab.engine.markups[0]) == 1


def test_find_replace_dialog_replaces_vector_text_and_undoes(qtbot):
    win = MainWindow()
    qtbot.addWidget(win)
    win.show()
    tab = win.load_sample_pdf()
    fr = win.open_find_replace_dialog()
    assert fr is not None
    fr.find_input.setText("Edit & Watermark")
    fr.replace_input.setText("Inline Vector")
    fr.btn_replace_all.click()
    assert "Inline Vector" in pymupdf.open(stream=tab.engine.build_final_pdf_bytes(), filetype="pdf")[0].get_text("text")
    assert "Replaced" in fr.lbl_status.text()

    win.undo()
    assert "Edit & Watermark" in pymupdf.open(stream=tab.engine.build_final_pdf_bytes(), filetype="pdf")[0].get_text("text")
    fr.close()


def test_document_snapshot_undo_redo_and_sidebar_actions(qtbot):
    from app.core.models import StickyNote

    win = MainWindow()
    qtbot.addWidget(win)
    win.show()

    tab = win.open_pdf_path(str(SQL_PDF))
    assert tab is not None
    orig_pages = tab.engine.page_count

    win._on_sidebar_page_action("insert_blank_after", 0)
    assert tab.engine.page_count == orig_pages + 1
    win.undo()
    assert tab.engine.page_count == orig_pages
    win.redo()
    assert tab.engine.page_count == orig_pages + 1
    win.undo()
    assert tab.engine.page_count == orig_pages

    nt_sb = StickyNote(page_idx=1, x=120.0, y=180.0, text="Sidebar note check")
    tab.engine.sticky_notes[1] = [nt_sb]
    win.sidebar.refresh_annotations()
    win._on_sidebar_annot_jump(1, (104.0, 164.0, 136.0, 196.0))
    assert tab.current_page == 1
    win._on_sidebar_annot_delete(1, "note", nt_sb)
    assert tab.engine.sticky_notes[1] == []
    win.undo()
    assert len(tab.engine.sticky_notes[1]) == 1

    tab.engine.custom_bookmarks.append(("Old Name", 0))
    win.sidebar.refresh_outline()
    tab.engine.custom_bookmarks[0] = ("New Name", 0)
    win.sidebar.bookmarkRenamed.emit("Old Name", "New Name", 0)
    assert tab.dirty is True


def test_split_every_n_signature_reuse_print_label_jpeg_and_ui_sync(qtbot, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog
    from app.core.pdf_engine import create_sample_pdf_bytes
    from app.ui.dialogs import PdfPrintDialog, SignatureDialog, SplitDialog

    # 1. Print dialog live page count label updates cleanly
    dlg_print = PdfPrintDialog(create_sample_pdf_bytes())
    qtbot.addWidget(dlg_print)
    dlg_print.show()
    qtbot.wait(30)
    assert dlg_print.lbl_prev_page.text() == "Page 1 / 2"
    dlg_print.close_doc()
    dlg_print.close()

    # 2. SplitDialog every-N mode
    dlg_split = SplitDialog(10)
    qtbot.addWidget(dlg_split)
    dlg_split.rb_every_n.setChecked(True)
    dlg_split.every_n_spin.setValue(4)
    dlg_split._validate_and_accept()
    assert dlg_split.result() == SplitDialog.Accepted
    assert dlg_split.is_every_n_mode() and dlg_split.every_n_value() == 4

    # 3. SignatureDialog session reuse + undo_stroke
    SignatureDialog._SAVED_SIGNATURES.clear()
    sig1 = SignatureDialog()
    qtbot.addWidget(sig1)
    sig1.pad._strokes.append([QPoint(10, 10), QPoint(50, 30)])
    assert len(sig1.pad._strokes) == 1
    sig1.pad.undo_stroke()
    assert len(sig1.pad._strokes) == 0
    sig1.tabs.setCurrentIndex(1)
    sig1.type_input.setText("Alice Reviewer")
    sig1._on_accept()
    assert sig1.result() == SignatureDialog.Accepted and sig1.result_png_bytes

    sig2 = SignatureDialog()
    qtbot.addWidget(sig2)
    assert sig2.saved_list.count() == 1
    sig2.tabs.setCurrentIndex(3)
    sig2._on_accept()
    assert sig2.result_png_bytes == sig1.result_png_bytes

    # 4. Duplicate tab guard, Pan contextual tool indicator sync, and JPEG page export
    win = MainWindow()
    qtbot.addWidget(win)
    win.show()
    tab1 = win.open_pdf_path(str(SQL_PDF))
    tab2 = win.open_pdf_path(str(SQL_PDF))
    assert tab2 is tab1 and win.tabs.count() == 1

    win.set_mode("annotate")
    assert win.ribbon._mode_tool_buttons["pen"].isChecked()
    win.left_rail.btn_pan.click()
    assert win.tool_state["tool"] == "pan"
    assert not win.ribbon._mode_tool_buttons["pen"].isChecked()

    jpg_out = tmp_path / "page1.jpg"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **kw: (str(jpg_out), "JPEG Image (*.jpg *.jpeg)"))
    win._on_tool_action("export_png", {})
    assert jpg_out.exists() and jpg_out.read_bytes().startswith(b"\xff\xd8")


def test_left_rail_and_context_ribbon_active_highlighting(qtbot):
    win = MainWindow()
    qtbot.addWidget(win)
    win.show()
    qtbot.wait(10)

    def _bg_hex(widget) -> str:
        return widget.grab().toImage().pixelColor(4, 4).name().lower()

    def _has_white_icon_pixel(btn, icon_sz: int = 18) -> bool:
        pm = btn.icon().pixmap(icon_sz, icon_sz)
        img = pm.toImage()
        return any(
            img.pixelColor(x, y).alpha() > 200 and img.pixelColor(x, y).red() > 240 and img.pixelColor(x, y).blue() > 240
            for x in range(img.width())
            for y in range(img.height())
        )

    accent_hex = win.accent_color.lower()

    # Initial View mode: View button active with accent fill and white icon
    btn_view = win.left_rail.buttons["view"][0]
    btn_edit = win.left_rail.buttons["edit"][0]
    assert btn_view.isChecked() and not btn_edit.isChecked()
    assert _bg_hex(btn_view) == accent_hex
    assert _bg_hex(btn_edit) != accent_hex
    assert _has_white_icon_pixel(btn_view)
    assert not _has_white_icon_pixel(btn_edit)

    # Switch to Edit mode: Left rail Edit highlighted + Edit Text pill highlighted vs Add Text pill
    win.set_mode("edit")
    qtbot.wait(10)
    assert btn_edit.isChecked() and not btn_view.isChecked()
    assert _bg_hex(btn_edit) == accent_hex
    assert _has_white_icon_pixel(btn_edit)
    assert not _has_white_icon_pixel(btn_view)

    b_edit_text = win.ribbon._mode_tool_buttons["edit_text"]
    b_add_text = win.ribbon._mode_tool_buttons["add_text"]
    assert b_edit_text.isChecked() and not b_add_text.isChecked()
    assert _bg_hex(b_edit_text) == accent_hex
    assert _bg_hex(b_add_text) != accent_hex

    b_add_text.click()
    qtbot.wait(10)
    assert b_add_text.isChecked() and not b_edit_text.isChecked()
    assert _bg_hex(b_add_text) == accent_hex
    assert _bg_hex(b_edit_text) != accent_hex

    # Annotate mode: Pen active by default, switching to Highlighter updates pill color
    win.set_mode("annotate")
    qtbot.wait(10)
    b_pen = win.ribbon._mode_tool_buttons["pen"]
    b_hl = win.ribbon._mode_tool_buttons["highlighter"]
    assert _bg_hex(b_pen) == accent_hex
    assert _bg_hex(b_hl) != accent_hex
    b_hl.click()
    qtbot.wait(10)
    assert _bg_hex(b_hl) == accent_hex
    assert _bg_hex(b_pen) != accent_hex

    # Protect mode: Redact Area distinguishes unchecked vs active state
    win.set_mode("protect")
    qtbot.wait(10)
    b_redact = win.ribbon._mode_tool_buttons["redact"]
    assert not b_redact.isChecked()
    assert _bg_hex(b_redact) != "#dc2626"
    b_redact.click()
    qtbot.wait(10)
    assert b_redact.isChecked()
    assert _bg_hex(b_redact) == "#dc2626"

    # Dark theme + Pan toggle preserve active white icons and accent highlights
    win.toggle_theme()
    win.left_rail.btn_pan.click()
    qtbot.wait(10)
    assert _bg_hex(win.left_rail.btn_pan) == accent_hex
    assert _has_white_icon_pixel(win.left_rail.btn_pan, 16)


def test_unedited_edit_text_click_does_not_commit_or_rearrange(qtbot):
    win = MainWindow()
    qtbot.addWidget(win)
    win.show()

    tab = win.open_pdf_path(str(SQL_PDF))
    assert tab is not None
    orig_pages = tab.engine.page_count
    orig_p0 = [(b.plain_text, b.rect) for b in tab.engine.extract_text_blocks(0)]
    orig_p1 = [(b.plain_text, b.rect) for b in tab.engine.extract_text_blocks(1)]

    win.set_mode("edit")
    pw0 = tab.page_widgets[0]

    # Click bold heading and body paragraph without editing, including Ctrl+A and zoom while editor open
    for needle in ("Physical Implementation:", "Physically, SQL Server tables"):
        blk = next(b for b in tab.engine.extract_text_blocks(0) if needle in b.plain_text)
        cx = int(((blk.rect[0] + blk.rect[2]) / 2.0) * pw0.zoom)
        cy = int(((blk.rect[1] + blk.rect[3]) / 2.0) * pw0.zoom)
        QTest.mouseClick(pw0, Qt.LeftButton, Qt.NoModifier, QPoint(cx, cy))
        assert pw0.active_editor is not None
        QTest.keyClick(pw0.active_editor, Qt.Key_A, Qt.ControlModifier)
        win.step_zoom(1.1)

    win.floating_bar.btn_done.click()
    QApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    assert pw0.active_editor is None

    # Opening Add Text and clicking Done without typing leaves no placeholder behind
    win._set_active_tool("add_text")
    QTest.mouseClick(pw0, Qt.LeftButton, Qt.NoModifier, QPoint(int(80 * pw0.zoom), int(480 * pw0.zoom)))
    assert pw0.active_editor is not None
    win.floating_bar.btn_done.click()
    QApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    assert tab.engine.in_place_edits == {}
    assert tab.dirty is False
    assert tab.undo_stack == []
    assert tab.engine.page_count == orig_pages
    assert [(b.plain_text, b.rect) for b in tab.engine.extract_text_blocks(0)] == orig_p0
    assert [(b.plain_text, b.rect) for b in tab.engine.extract_text_blocks(1)] == orig_p1
