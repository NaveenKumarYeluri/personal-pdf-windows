"""Unit tests for core PDF operations, ligatures, signatures, security, and organizer."""

from __future__ import annotations

import io
from pathlib import Path

import pymupdf
import pytest
from PIL import Image

from app.core.document_ops import (
    PageOp,
    apply_bates_numbering,
    apply_watermark,
    encrypt_pdf_aes256,
    export_page_image,
    extract_clean_text,
    get_metadata,
    merge_pdfs,
    organize_pages,
    parse_page_ranges,
    split_pdf_by_ranges,
    update_metadata,
    write_bytes_atomic,
)
from app.core.ligatures import heal_pdf_text
from app.core.pdf_engine import PDFDocumentEngine, create_sample_pdf_bytes
from app.core.signature import extract_signature_ink_bytes, render_typed_signature_png

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SQL_PDF = FIXTURES / "SQL Server Advanced Concepts.pdf"
SIGN_JPG = FIXTURES / "Sign.jpg"


def test_ligature_healing():
    assert heal_pdf_text("Physical Implementa\u019fon:") == "Physical Implementation:"
    assert heal_pdf_text("single par\u019f\u019fon in ﬁlegroup") == "single partition in filegroup"
    assert heal_pdf_text("mul\u019fple speci\ufb01c di\ufb00erent") == "multiple specific different"
    assert heal_pdf_text("a\ufffder o\ufffden") == "after often"
    assert heal_pdf_text("a\u014cer swi\u014c spli\u01abng") == "after swift splitting"
    assert heal_pdf_text("Alloca\u019fon Op\u019fmizer Analy\u019fcal") == "Allocation Optimizer Analytical"

    eng = PDFDocumentEngine(SQL_PDF.read_bytes(), "SQL.pdf")
    for pno in range(eng.page_count):
        for blk in eng.extract_text_blocks(pno):
            for bad in ("\u019f", "\u014c", "\u014d", "\u01ab"):
                assert bad not in blk.plain_text
                assert bad not in blk.html


def test_signature_extraction_and_typed_signature():
    raw = SIGN_JPG.read_bytes()
    png = extract_signature_ink_bytes(raw, remove_bg=True, threshold=215)
    im = Image.open(io.BytesIO(png)).convert("RGBA")
    # Border pixel should be transparent paper background
    assert im.getpixel((0, 0))[3] == 0
    # Must retain non-transparent ink pixels
    import numpy as np

    alpha_arr = np.array(im)[:, :, 3]
    assert bool(np.any(alpha_arr > 120))

    typed = render_typed_signature_png("Naveen Kumar", "#0033aa")
    im2 = Image.open(io.BytesIO(typed)).convert("RGBA")
    assert im2.width > 40 and im2.height > 20
    assert bool(np.any(np.array(im2)[:, :, 3] > 120))


def test_split_range_validation_and_execution():
    pdf_bytes = SQL_PDF.read_bytes()
    for bad in ("", "-", "--", "1-", "-2", "abc", "0", "0-2", "99", "1-50", "5-2"):
        with pytest.raises(ValueError):
            parse_page_ranges(bad, 13)

    parts = split_pdf_by_ranges(pdf_bytes, "1-2, 5", base_name="SQL.pdf")
    assert len(parts) == 2
    assert parts[0][0] == "SQL_pages_1-2.pdf"
    assert parts[1][0] == "SQL_page_5.pdf"

    d1 = pymupdf.open(stream=parts[0][1], filetype="pdf")
    d2 = pymupdf.open(stream=parts[1][1], filetype="pdf")
    assert len(d1) == 2
    assert len(d2) == 1
    d1.close()
    d2.close()


def test_merge_and_organize_pages():
    sample = create_sample_pdf_bytes()
    merged = merge_pdfs([sample, sample])
    d = pymupdf.open(stream=merged, filetype="pdf")
    assert len(d) == 4
    d.close()

    organized = organize_pages(
        sample,
        [PageOp(1, 90), PageOp(None, 0), PageOp(0, 0)],
    )
    d2 = pymupdf.open(stream=organized, filetype="pdf")
    assert len(d2) == 3
    assert d2[0].rotation == 90
    d2.close()


def test_watermark_bates_metadata_and_encryption(tmp_path):
    sample = create_sample_pdf_bytes()
    wm = apply_watermark(sample, text="CONFIDENTIAL", angle=45, opacity=0.3, font_size=44, color_hex="#ea2e3f")
    numbered = apply_bates_numbering(wm, fmt="Page {page} of {total}", position="bottom-right", start_number=10, prefix="DOC-")
    meta_bytes = update_metadata(numbered, {"title": "Test PDF", "author": "Personal Suite", "subject": "Audit", "keywords": "pdf,win"})

    meta = get_metadata(meta_bytes)
    assert meta["title"] == "Test PDF"
    assert meta["author"] == "Personal Suite"
    assert "DOC- Page 10 of 11" in extract_clean_text(meta_bytes)

    enc = encrypt_pdf_aes256(meta_bytes, "SecretPass123!")
    eng = PDFDocumentEngine(enc, "enc.pdf")
    assert eng.needs_password is True
    assert eng.authenticate("Wrong") is False
    assert eng.authenticate("SecretPass123!") is True
    assert eng.page_count == 2

    # Atomic save + PNG export
    out_file = tmp_path / "saved.pdf"
    write_bytes_atomic(str(out_file), enc)
    assert out_file.exists() and out_file.read_bytes().startswith(b"%PDF")
    png = export_page_image(meta_bytes, 0, dpi=120, fmt="png")
    assert png.startswith(b"\x89PNG")


def test_rejects_non_pdf_bytes():
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("word/document.xml", "<w:document/>")

    for bad_bytes in (b"not a pdf at all", buf.getvalue()):
        with pytest.raises(Exception):
            PDFDocumentEngine(bad_bytes, "fake.docx")


def test_signature_extraction_downscales_oversized_photo():
    from PIL import ImageDraw

    im = Image.new("RGB", (3200, 2400), (248, 248, 244))
    draw = ImageDraw.Draw(im)
    draw.line([(400, 1200), (2800, 1100)], fill=(15, 23, 42), width=28)
    raw_buf = io.BytesIO()
    im.save(raw_buf, format="JPEG", quality=85)

    png = extract_signature_ink_bytes(raw_buf.getvalue(), remove_bg=True, threshold=215)
    out = Image.open(io.BytesIO(png)).convert("RGBA")
    assert max(out.width, out.height) <= 1400
    assert out.getpixel((0, 0))[3] == 0
    import numpy as np

    assert bool(np.any(np.array(out)[:, :, 3] > 150))


def test_rotated_image_overlay_and_sticky_note_color_export():
    from app.core.models import StampItem, StickyNote
    from app.core.pdf_engine import create_blank_pdf_bytes

    im = Image.new("RGB", (200, 100), (255, 255, 255))
    for x in range(200):
        for y in range(20):
            im.putpixel((x, y), (255, 0, 0))
    buf = io.BytesIO()
    im.save(buf, format="PNG")

    eng = PDFDocumentEngine(create_blank_pdf_bytes(), "rot.pdf")
    eng.stamps[0] = [
        StampItem(
            page_idx=0,
            kind="image",
            rect=(100.0, 200.0, 300.0, 300.0),
            opacity=1.0,
            rotation=90.0,
            image_bytes=buf.getvalue(),
        )
    ]
    eng.sticky_notes[0] = [
        StickyNote(page_idx=0, x=80.0, y=90.0, text="Green note", color_hex="#22c55e")
    ]
    out_bytes = eng.build_final_pdf_bytes()
    doc = pymupdf.open(stream=out_bytes, filetype="pdf")
    page = doc[0]
    pix = page.get_pixmap(dpi=72, alpha=False)
    # 90 deg CW moves top red stripe to right side of rotated box (center 200,250 -> x~240, y~250)
    assert pix.pixel(240, 250)[0] > 200 and pix.pixel(240, 250)[1] < 50
    assert pix.pixel(160, 250)[1] > 200

    annots = list(page.annots())
    assert len(annots) == 1
    assert abs(annots[0].colors["stroke"][1] - (0xC5 / 255.0)) < 0.03
    doc.close()


def test_form_widgets_engine_and_links():
    from app.core.document_ops import flatten_form_fields

    doc = pymupdf.open()
    doc.new_page(width=400, height=500)
    doc.new_page(width=400, height=500)
    p1 = doc[0]
    p1.insert_link({"kind": pymupdf.LINK_GOTO, "from": pymupdf.Rect(20, 20, 120, 40), "page": 1})

    w_txt = pymupdf.Widget()
    w_txt.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
    w_txt.field_name = "applicant"
    w_txt.rect = pymupdf.Rect(40, 80, 200, 105)
    p1.add_widget(w_txt)

    w_chk = pymupdf.Widget()
    w_chk.field_type = pymupdf.PDF_WIDGET_TYPE_CHECKBOX
    w_chk.field_name = "terms"
    w_chk.rect = pymupdf.Rect(40, 120, 60, 140)
    p1.add_widget(w_chk)

    raw = doc.tobytes()
    doc.close()

    eng = PDFDocumentEngine(raw, "form.pdf")
    lnk = eng.find_link_at(0, 50, 30)
    assert lnk is not None and lnk["page"] == 1
    assert len(eng.get_form_widgets(0)) == 2
    assert eng.find_form_widget_at(0, 100, 90)["field_name"] == "applicant"

    eng.set_form_value(0, 0, "Jane Doe")
    eng.set_form_value(0, 1, "Yes")
    assert eng.get_page_render_revision(0) > 0
    assert eng.get_page_render_revision(1) == 0

    final = eng.build_final_pdf_bytes()
    flat, cnt = flatten_form_fields(final)
    assert cnt == 2
    d_flat = pymupdf.open(stream=flat, filetype="pdf")
    assert "Jane Doe" in d_flat[0].get_text("text")
    d_flat.close()

    eng.remove_form_value(0, 0)
    eng.remove_form_value(0, 1)
    assert eng.get_page_render_revision(0) == 0
