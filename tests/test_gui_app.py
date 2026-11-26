"""Automated Qt6 GUI end-to-end verification tests using pytest-qt."""

from __future__ import annotations

from pathlib import Path

import pymupdf
import shiboken6
from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

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
