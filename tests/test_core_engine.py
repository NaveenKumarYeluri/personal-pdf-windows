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
