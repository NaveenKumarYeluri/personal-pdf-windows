"""Deep tests for vector in-place text editing, font/color fidelity, reflow, Word-Flow, and redaction."""

from __future__ import annotations

from pathlib import Path

import pymupdf

from app.core.models import RedactionBox, StampItem, TextEditOp
from app.core.pdf_engine import PDFDocumentEngine, build_pdf_from_word_flow_html, create_sample_pdf_bytes

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SQL_PDF = FIXTURES / "SQL Server Advanced Concepts.pdf"


def test_in_place_edit_preserves_vector_text_fonts_and_colors():
    raw = SQL_PDF.read_bytes()
    eng = PDFDocumentEngine(raw, "SQL.pdf")
    blocks_p1 = eng.extract_text_blocks(0)
    assert len(blocks_p1) >= 4

    # Block starting with "Physically, SQL Server tables"
    target_blk = next(b for b in blocks_p1 if "Physically, SQL Server tables" in b.plain_text)
    assert target_blk.font_family == "Calibri"
    assert "<b>8 KB pages</b>" in target_blk.html

    new_html = (
        '<div style="font-family:Calibri,sans-serif;font-size:12pt;color:#000000;">'
        '<p>Edited <b>Vector Bold</b> and <span style="color:#2563eb">Royal Blue</span> paragraph text.</p>'
        "</div>"
    )
    op = TextEditOp(
        edit_id="e1",
        page_idx=0,
        orig_rect=target_blk.rect,
        target_rect=target_blk.rect,
        original_text=target_blk.plain_text,
        original_html=target_blk.html,
        new_text="Edited Vector Bold and Royal Blue paragraph text.",
        new_html=new_html,
        font_family="Calibri",
        font_size=12.0,
        color_hex="#000000",
        block_id=target_blk.block_id,
    )
    eng.set_in_place_edit(op)
    saved = eng.build_final_pdf_bytes()

    # Re-open saved PDF and verify REAL vector text (no PNG raster flattening, old text removed!)
    doc = pymupdf.open(stream=saved, filetype="pdf")
    p1 = doc[0]
    extracted = p1.get_text("text")
    assert "Edited Vector Bold" in extracted
    assert "Royal Blue" in extracted
    assert "Physically, SQL Server tables" not in extracted

    # Verify vector spans preserve blue color & bold flag
    spans = [
        sp
        for blk in p1.get_text("dict")["blocks"]
        if blk["type"] == 0
        for ln in blk["lines"]
        for sp in ln["spans"]
    ]
    assert any("Royal Blue" in s["text"] and f"#{s['color']:06x}".lower() == "#2563eb" for s in spans)
    assert any("Vector Bold" in s["text"] and (s["flags"] & 16 or "bold" in s["font"].lower()) for s in spans)
    # Diagram image on Page 1 must remain intact
    assert len(p1.get_images()) >= 1
    doc.close()


def test_multi_paragraph_growth_reflows_down_and_cascades_across_pages():
    raw = SQL_PDF.read_bytes()
    eng = PDFDocumentEngine(raw, "SQL.pdf")
    orig_pages = eng.page_count
    blocks_p1 = eng.extract_text_blocks(0)
    b1 = next(b for b in blocks_p1 if "Physically, SQL Server tables" in b.plain_text)
    b_heap = next(b for b in blocks_p1 if "In the Heap table" in b.plain_text)

    # Expand paragraph 1 by 8 long paragraphs so content MUST cascade onto Page 2!
    extra_paras = "".join(
        f"<p>Added paragraph {i}: SQL Server storage engine manages data extents, IAM allocations, "
        f"and page splits reliably across pages without losing text or clipping margins.</p>"
        for i in range(1, 10)
    )
    op1 = TextEditOp(
        edit_id="grow1",
        page_idx=0,
        orig_rect=b1.rect,
        target_rect=b1.rect,
        original_text=b1.plain_text,
        original_html=b1.html,
        new_text="Added paragraphs",
        new_html=f'<div style="font-family:Calibri,sans-serif;font-size:12pt;color:#000000;">{extra_paras}</div>',
        font_family="Calibri",
        font_size=12.0,
        color_hex="#000000",
        reflow_enabled=True,
        block_id=b1.block_id,
    )
    eng.set_in_place_edit(op1)
    saved = eng.build_final_pdf_bytes()

    doc = pymupdf.open(stream=saved, filetype="pdf")
    assert len(doc) >= orig_pages
    p1_text = doc[0].get_text("text")
    p2_text = doc[1].get_text("text")
    assert "Added paragraph 1" in p1_text
    # Overflow paragraphs or shifted Heap Table section must appear cleanly on Page 2
    assert (
        ("Added paragraph 9" in p2_text)
        or ("Heap Table:" in p2_text)
        or ("for the rows in the heap table" in p2_text)
    )

    # Verify Page 1 respects safe bottom margin (no text ink below page_h - 24pt)
    safe_y = doc[0].rect.height - 24.0
    for blk in doc[0].get_text("dict")["blocks"]:
        if blk["type"] == 0 and any(s["text"].strip() for l in blk["lines"] for s in l["spans"]):
            assert blk["bbox"][3] <= safe_y + 4.0, f"Block violated bottom margin: {blk['bbox']}"
    doc.close()

    # Now test sequential edit of a shifted paragraph AFTER Paragraph 1 was expanded (with small expansion keeping b_heap on p1 or p2)
    eng2 = PDFDocumentEngine(raw, "SQL.pdf")
    small_grow = TextEditOp(
        edit_id="small1",
        page_idx=0,
        orig_rect=b1.rect,
        target_rect=b1.rect,
        original_text=b1.plain_text,
        original_html=b1.html,
        new_text="First paragraph slightly taller.\nSecond line added.",
        new_html='<div style="font-family:Calibri,sans-serif;font-size:12pt;color:#000000;"><p>First paragraph slightly taller.</p><p>Second line added.</p><p>Third line added.</p><p>Fourth line added.</p></div>',
        font_family="Calibri",
        font_size=12.0,
        color_hex="#000000",
        reflow_enabled=True,
        block_id=b1.block_id,
    )
    eng2.set_in_place_edit(small_grow)
    # Locate Heap block from preview after first edit
    p1_preview_blocks = eng2.extract_text_blocks(0)
    heap_preview = next((b for b in p1_preview_blocks if "In the Heap table" in b.plain_text), None)
    if heap_preview is None:
        p2_preview_blocks = eng2.extract_text_blocks(1)
        heap_preview = next(b for b in p2_preview_blocks if "In the Heap table" in b.plain_text)
    assert heap_preview.base_block_id == b_heap.block_id

    op2 = TextEditOp(
        edit_id="edit_heap",
        page_idx=heap_preview.base_page_idx,
        orig_rect=heap_preview.base_rect,
        target_rect=heap_preview.base_rect,
        original_text=heap_preview.plain_text,
        original_html=heap_preview.html,
        new_text="Heap table updated cleanly!",
        new_html='<div style="font-family:Calibri,sans-serif;font-size:12pt;color:#000000;"><p>Heap table updated cleanly!</p></div>',
        font_family="Calibri",
        font_size=12.0,
        color_hex="#000000",
        reflow_enabled=True,
        block_id=heap_preview.base_block_id,
    )
    eng2.set_in_place_edit(op2)
    final2 = eng2.build_final_pdf_bytes()
    doc2 = pymupdf.open(stream=final2, filetype="pdf")
    full_txt = "\n".join(pg.get_text("text") for pg in doc2)
    assert "First paragraph slightly taller." in full_txt
    assert "Heap table updated cleanly!" in full_txt
    assert "data rows are not stored in any" not in full_txt
    doc2.close()


def test_word_flow_story_and_true_redaction():
    long_html = "<h2>Document Title</h2>" + "".join(
        f"<p>Paragraph #{i}: Crisp vector Word-Flow output spanning multiple pages cleanly.</p>"
        for i in range(1, 55)
    )
    pdf_bytes = build_pdf_from_word_flow_html(long_html)
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    assert len(doc) >= 2
    assert "Paragraph #1:" in doc[0].get_text("text")
    assert "Paragraph #54:" in doc[-1].get_text("text")
    doc.close()

    # True redaction permanently removes underlying sensitive text from sample PDF
    sample = create_sample_pdf_bytes()
    eng = PDFDocumentEngine(sample, "sample.pdf")
    hits = eng.search_text("Vector In-Place Text Editing")
    assert len(hits) >= 1
    r = hits[0].rect
    eng.redactions.setdefault(0, []).append(RedactionBox(page_idx=0, rect=(r[0] - 2, r[1] - 2, r[2] + 2, r[3] + 2)))
    eng.stamps.setdefault(0, []).append(StampItem(page_idx=0, kind="stamp", rect=(300, 300, 440, 345), text="APPROVED"))
    redacted_bytes = eng.build_final_pdf_bytes()
    d_red = pymupdf.open(stream=redacted_bytes, filetype="pdf")
    assert "Vector In-Place Text Editing" not in d_red[0].get_text("text")
    assert "APPROVED" in d_red[0].get_text("text")
    d_red.close()


def test_add_text_and_reediting_new_block_preserves_and_overflows():
    sample = create_sample_pdf_bytes()
    eng = PDFDocumentEngine(sample, "sample.pdf")

    op1 = TextEditOp(
        edit_id="new1",
        page_idx=0,
        orig_rect=(50.0, 500.0, 290.0, 528.0),
        target_rect=(50.0, 500.0, 290.0, 528.0),
        original_text="",
        original_html="",
        new_text="First Added Text",
        new_html='<div style="font-family:Calibri,sans-serif;font-size:12pt;color:#000000;"><p>First Added Text</p></div>',
        font_family="Calibri",
        font_size=12.0,
        color_hex="#000000",
        is_new_block=True,
        block_id="new_blk001",
    )
    eng.set_in_place_edit(op1)
    preview_blocks = eng.extract_text_blocks(0)
    added_blk = next(b for b in preview_blocks if b.base_block_id == "new_blk001")
    assert added_blk.is_new_block is True
    assert "First Added Text" in added_blk.plain_text

    # Re-editing the newly added block (even with is_new_block=False) must update rather than drop it
    op2 = TextEditOp(
        edit_id="new2",
        page_idx=added_blk.base_page_idx,
        orig_rect=added_blk.base_rect,
        target_rect=added_blk.base_rect,
        original_text=added_blk.plain_text,
        original_html=added_blk.html,
        new_text="Updated Added Text",
        new_html='<div style="font-family:Calibri,sans-serif;font-size:12pt;color:#000000;"><p>Updated Added Text</p></div>',
        font_family="Calibri",
        font_size=12.0,
        color_hex="#000000",
        is_new_block=False,
        block_id=added_blk.base_block_id,
    )
    eng.set_in_place_edit(op2)
    saved = eng.build_final_pdf_bytes()
    doc = pymupdf.open(stream=saved, filetype="pdf")
    p0_txt = doc[0].get_text("text")
    assert "Updated Added Text" in p0_txt
    assert "First Added Text" not in p0_txt
    doc.close()

    # Added text near bottom margin with many lines must overflow cleanly onto next page
    long_paras = "".join(f"<p>Overflow added line {i}: testing bottom margin flow.</p>" for i in range(1, 8))
    op_bottom = TextEditOp(
        edit_id="new_bot",
        page_idx=0,
        orig_rect=(50.0, 760.0, 340.0, 788.0),
        target_rect=(50.0, 760.0, 340.0, 788.0),
        original_text="",
        original_html="",
        new_text="Overflow added line",
        new_html=f'<div style="font-family:Calibri,sans-serif;font-size:12pt;color:#000000;">{long_paras}</div>',
        font_family="Calibri",
        font_size=12.0,
        color_hex="#000000",
        is_new_block=True,
        block_id="new_bot001",
    )
    eng2 = PDFDocumentEngine(sample, "sample.pdf")
    eng2.set_in_place_edit(op_bottom)
    doc2 = pymupdf.open(stream=eng2.build_final_pdf_bytes(), filetype="pdf")
    assert "Overflow added line 1" in doc2[0].get_text("text")
    assert "Overflow added line 7" in doc2[1].get_text("text")
    doc2.close()


def test_clear_block_to_empty_and_vector_find_replace():
    raw = SQL_PDF.read_bytes()
    eng = PDFDocumentEngine(raw, "SQL.pdf")
    blocks_p1 = eng.extract_text_blocks(0)
    target = next(b for b in blocks_p1 if "Physical Implementation" in b.plain_text)
    y_next_before = next(b for b in blocks_p1 if "Physically, SQL Server tables" in b.plain_text).rect[1]

    eng.set_in_place_edit(
        TextEditOp(
            edit_id="clear1",
            page_idx=0,
            orig_rect=target.rect,
            target_rect=target.rect,
            original_text=target.plain_text,
            original_html=target.html,
            new_text="",
            new_html='<div style="font-family:Calibri,sans-serif;font-size:14pt;"><p>&nbsp;</p></div>',
            font_family="Calibri",
            font_size=14.0,
            color_hex="#000000",
            block_id=target.block_id,
        )
    )
    after_blocks = eng.extract_text_blocks(0)
    assert not any(b.base_block_id == target.block_id for b in after_blocks)
    y_next_after = next(b for b in after_blocks if "Physically, SQL Server tables" in b.plain_text).rect[1]
    assert abs(y_next_after - y_next_before) < 1.0

    # Vector Find & Replace preserves inline bold while replacing across spans
    eng2 = PDFDocumentEngine(raw, "SQL.pdf")
    changes, n_hits = eng2.replace_text_in_blocks(
        "8 KB pages", "16 KB extents", case_sensitive=True, whole_word=True, replace_all=True
    )
    assert n_hits >= 1 and len(changes) >= 1
    out = eng2.build_final_pdf_bytes()
    doc = pymupdf.open(stream=out, filetype="pdf")
    assert "16 KB extents" in doc[0].get_text("text")
    assert "8 KB pages" not in doc[0].get_text("text")
    spans = [
        sp
        for b in doc[0].get_text("dict")["blocks"]
        if b["type"] == 0
        for ln in b["lines"]
        for sp in ln["spans"]
    ]
    assert any("16 KB extents" in sp["text"] and (sp["flags"] & 16 or "bold" in sp["font"].lower()) for sp in spans)
    doc.close()


def test_heading_edit_preserves_bold_and_does_not_shift_page():
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    from app.core.pdf_engine import qt_html_to_clean_html
    from app.ui.text_editor import InPlaceTextEditor

    _app = QApplication.instance() or QApplication([])
    raw = SQL_PDF.read_bytes()
    eng = PDFDocumentEngine(raw, "SQL.pdf")
    orig_pages = eng.page_count
    blocks_p1 = eng.extract_text_blocks(0)
    heading = next(b for b in blocks_p1 if "Physical Implementation:" in b.plain_text)
    below_y0 = next(b for b in blocks_p1 if "Physically, SQL Server tables" in b.plain_text).rect[1]
    bottom_y0 = next(b for b in blocks_p1 if "When you store data" in b.plain_text).rect[1]

    ed = InPlaceTextEditor(heading, zoom=1.4)
    ed.selectAll()
    QTest.keyClicks(ed, "Physical Design:")
    clean_html = qt_html_to_clean_html(
        ed.export_unscaled_html(),
        default_font=heading.font_family,
        default_size_pt=heading.font_size,
        default_color=heading.color_hex,
        line_height_ratio=heading.line_height_ratio,
    )
    eng.set_in_place_edit(
        TextEditOp(
            edit_id="head1",
            page_idx=0,
            orig_rect=heading.rect,
            target_rect=heading.rect,
            original_text=heading.plain_text,
            original_html=heading.html,
            new_text="Physical Design:",
            new_html=clean_html,
            font_family=heading.font_family,
            font_size=heading.font_size,
            color_hex=heading.color_hex,
            line_height_ratio=heading.line_height_ratio,
            reflow_enabled=True,
            block_id=heading.block_id,
        )
    )
    out = eng.build_final_pdf_bytes()
    doc = pymupdf.open(stream=out, filetype="pdf")
    assert len(doc) == orig_pages
    spans = [
        sp
        for b in doc[0].get_text("dict")["blocks"]
        if b["type"] == 0
        for ln in b["lines"]
        for sp in ln["spans"]
    ]
    assert any("Physical Design:" in sp["text"] and (sp["flags"] & 16 or "bold" in sp["font"].lower()) for sp in spans)
    doc.close()

    after_p1 = eng.extract_text_blocks(0)
    assert abs(next(b for b in after_p1 if "Physically, SQL Server tables" in b.plain_text).rect[1] - below_y0) < 0.5
    assert abs(next(b for b in after_p1 if "When you store data" in b.plain_text).rect[1] - bottom_y0) < 0.5
