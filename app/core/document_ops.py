"""Document-level PDF operations: split, merge, organize, watermark, Bates, security, export."""

from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass

import pymupdf

from app.core.fonts import resolve_mupdf_font
from app.core.ligatures import heal_pdf_text

A4_WIDTH = 595.28
A4_HEIGHT = 841.89


@dataclass
class PageOp:
    """Single page operation for organizer."""

    original_index: int | None  # None means insert blank A4 page
    rotation_delta: int = 0


def parse_page_ranges(range_str: str, total_pages: int) -> list[tuple[int, int]]:
    """
    Validate and parse comma-separated 1-based page ranges like '1-3, 5, 7-10'.
    Raises ValueError on malformed or out-of-bounds input.
    """
    if not range_str or not range_str.strip():
        raise ValueError("Please enter page ranges (e.g. 1-2, 3-5).")
    if total_pages <= 0:
        raise ValueError("The PDF document contains no pages.")

    segments = [seg.strip() for seg in range_str.split(",") if seg.strip()]
    if not segments:
        raise ValueError("Please enter valid page ranges (e.g. 1-2, 3-5).")

    parsed: list[tuple[int, int]] = []
    for seg in segments:
        single_m = re.match(r"^(\d+)$", seg)
        range_m = re.match(r"^(\d+)\s*-\s*(\d+)$", seg)
        if not single_m and not range_m:
            raise ValueError(
                f'Invalid page range "{seg}". Use numbers and hyphens (e.g. 1-3, 5).'
            )
        if single_m:
            start = end = int(single_m.group(1))
        else:
            start = int(range_m.group(1))
            end = int(range_m.group(2))

        if start < 1 or end < 1:
            raise ValueError("Page numbers must be 1 or greater.")
        if start > total_pages or end > total_pages:
            raise ValueError(
                f"Range {start}-{end} exceeds document total pages ({total_pages})."
            )
        if start > end:
            raise ValueError(
                f'Invalid range "{seg}": start page ({start}) cannot exceed end page ({end}).'
            )
        parsed.append((start, end))
    return parsed


def split_pdf_by_ranges(
    pdf_bytes: bytes, range_str: str, base_name: str = "document"
) -> list[tuple[str, bytes]]:
    """Split PDF into separate PDF byte buffers matching the validated page ranges."""
    src = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    total_pages = len(src)
    ranges = parse_page_ranges(range_str, total_pages)

    outputs: list[tuple[str, bytes]] = []
    clean_base = re.sub(r"\.pdf$", "", base_name or "document", flags=re.I)
    for start, end in ranges:
        out_doc = pymupdf.open()
        out_doc.insert_pdf(src, from_page=start - 1, to_page=end - 1)
        label = f"page_{start}" if start == end else f"pages_{start}-{end}"
        outputs.append((f"{clean_base}_{label}.pdf", out_doc.tobytes(deflate=True, garbage=3)))
        out_doc.close()
    src.close()
    return outputs


def split_pdf_every_n(
    pdf_bytes: bytes, pages_per_file: int = 1, base_name: str = "document"
) -> list[tuple[str, bytes]]:
    """Split PDF into consecutive chunks of at most pages_per_file pages each."""
    n = int(pages_per_file)
    if n < 1:
        raise ValueError("Pages per split file must be 1 or greater.")
    src = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    total_pages = len(src)
    if total_pages <= 0:
        src.close()
        raise ValueError("The PDF document contains no pages.")
    outputs: list[tuple[str, bytes]] = []
    clean_base = re.sub(r"\.pdf$", "", base_name or "document", flags=re.I)
    for start0 in range(0, total_pages, n):
        end0 = min(total_pages - 1, start0 + n - 1)
        out_doc = pymupdf.open()
        out_doc.insert_pdf(src, from_page=start0, to_page=end0)
        label = f"page_{start0 + 1}" if start0 == end0 else f"pages_{start0 + 1}-{end0 + 1}"
        outputs.append((f"{clean_base}_{label}.pdf", out_doc.tobytes(deflate=True, garbage=3)))
        out_doc.close()
    src.close()
    return outputs


def merge_pdfs(pdf_bytes_list: list[bytes]) -> bytes:
    """Concatenate multiple PDFs into one document."""
    if not pdf_bytes_list:
        raise ValueError("At least one PDF is required to merge.")
    merged = pymupdf.open()
    for raw in pdf_bytes_list:
        part = pymupdf.open(stream=raw, filetype="pdf")
        merged.insert_pdf(part)
        part.close()
    out = merged.tobytes(deflate=True, garbage=3)
    merged.close()
    return out


def organize_pages(pdf_bytes: bytes, operations: list[PageOp]) -> bytes:
    """Rebuild PDF according to reorder, rotation, deletion, duplication, and blank-page ops."""
    src = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    out_doc = pymupdf.open()

    for op in operations:
        if op.original_index is None:
            page = out_doc.new_page(width=A4_WIDTH, height=A4_HEIGHT)
            if op.rotation_delta:
                page.set_rotation(op.rotation_delta % 360)
        elif 0 <= op.original_index < len(src):
            out_doc.insert_pdf(src, from_page=op.original_index, to_page=op.original_index)
            page = out_doc[-1]
            if op.rotation_delta:
                page.set_rotation((page.rotation + op.rotation_delta) % 360)

    if len(out_doc) == 0:
        out_doc.new_page(width=A4_WIDTH, height=A4_HEIGHT)

    data = out_doc.tobytes(deflate=True, garbage=3)
    out_doc.close()
    src.close()
    return data


def _hex_to_rgb01(hex_color: str) -> tuple[float, float, float]:
    clean = (hex_color or "#000000").lstrip("#")
    if len(clean) != 6:
        return (0.0, 0.0, 0.0)
    return (
        int(clean[0:2], 16) / 255.0,
        int(clean[2:4], 16) / 255.0,
        int(clean[4:6], 16) / 255.0,
    )


def apply_watermark(
    pdf_bytes: bytes,
    text: str = "CONFIDENTIAL",
    angle: int = 45,
    opacity: float = 0.25,
    font_size: int = 48,
    color_hex: str = "#ff0000",
    font_family: str = "Helvetica",
) -> bytes:
    """Apply a centered vector text watermark across every page of the document."""
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    label = (text or "CONFIDENTIAL").strip() or "CONFIDENTIAL"
    rgb = _hex_to_rgb01(color_hex)
    opac = max(0.05, min(1.0, float(opacity)))
    fsize = max(10, min(160, int(font_size)))
    rad = math.radians(angle)

    font = (
        pymupdf.Font("helv")
        if (font_family or "").lower() in ("helv", "helvetica")
        else resolve_mupdf_font(font_family, bold=True)
    )
    text_w = font.text_length(label, fontsize=fsize)
    text_h = fsize * 0.72

    for page in doc:
        rect = page.rect
        cx = rect.width / 2.0
        cy = rect.height / 2.0
        # Counter-clockwise angle in PDF coordinates; y increases downward in MuPDF
        dx = -(text_w / 2.0) * math.cos(rad) + (text_h / 2.0) * math.sin(rad)
        dy = (text_w / 2.0) * math.sin(rad) + (text_h / 2.0) * math.cos(rad)
        origin = pymupdf.Point(cx + dx, cy + dy)
        mat = pymupdf.Matrix(math.cos(-rad), math.sin(-rad), -math.sin(-rad), math.cos(-rad), 0, 0)
        tw = pymupdf.TextWriter(page.rect)
        tw.append(origin, label, font=font, fontsize=fsize)
        tw.write_text(page, color=rgb, opacity=opac, morph=(origin, mat))

    out = doc.tobytes(deflate=True, garbage=3)
    doc.close()
    return out


def apply_bates_numbering(
    pdf_bytes: bytes,
    fmt: str = "Page {page} of {total}",
    position: str = "bottom-center",
    start_number: int = 1,
    prefix: str = "",
    font_size: int = 10,
    color_hex: str = "#333333",
) -> bytes:
    """Apply dynamic header/footer page or Bates numbering to every page."""
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    total = len(doc)
    start_num = max(1, int(start_number))
    fsize = max(7, min(36, int(font_size)))
    rgb = _hex_to_rgb01(color_hex)
    pref = (prefix or "").strip()
    font = pymupdf.Font("helv")

    for idx, page in enumerate(doc):
        num = start_num + idx
        last_num = start_num + total - 1
        label = (fmt or "Page {page} of {total}").replace("{page}", str(num)).replace(
            "{total}", str(last_num)
        )
        if pref:
            label = f"{pref} {label}"
        tw = font.text_length(label, fontsize=fsize)
        w, h = page.rect.width, page.rect.height

        if position == "bottom-left":
            x, y = 35.0, h - 24.0
        elif position == "bottom-right":
            x, y = max(20.0, w - tw - 35.0), h - 24.0
        elif position == "top-left":
            x, y = 35.0, 28.0
        elif position == "top-center":
            x, y = max(20.0, (w - tw) / 2.0), 28.0
        elif position == "top-right":
            x, y = max(20.0, w - tw - 35.0), 28.0
        else:  # bottom-center
            x, y = max(20.0, (w - tw) / 2.0), h - 24.0

        page.insert_text((x, y), label, fontname="helv", fontsize=fsize, color=rgb)

    out = doc.tobytes(deflate=True, garbage=3)
    doc.close()
    return out


def get_metadata(pdf_bytes: bytes) -> dict[str, str | int]:
    """Read document metadata dictionary and page count."""
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    meta = doc.metadata or {}
    res = {
        "title": meta.get("title") or "",
        "author": meta.get("author") or "",
        "subject": meta.get("subject") or "",
        "keywords": meta.get("keywords") or "",
        "creator": meta.get("creator") or "",
        "producer": meta.get("producer") or "",
        "pageCount": len(doc),
    }
    doc.close()
    return res


def update_metadata(pdf_bytes: bytes, meta: dict[str, str]) -> bytes:
    """Update PDF Title, Author, Subject, and Keywords."""
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    current = dict(doc.metadata or {})
    for key in ("title", "author", "subject", "keywords", "creator"):
        if key in meta:
            current[key] = meta[key]
    doc.set_metadata(current)
    out = doc.tobytes(deflate=True, garbage=3)
    doc.close()
    return out


def encrypt_pdf_aes256(
    pdf_bytes: bytes,
    password: str,
    allow_print: bool = True,
    allow_copy: bool = True,
) -> bytes:
    """Password-protect a PDF using AES-256 encryption."""
    pw = (password or "").strip()
    if not pw:
        raise ValueError("A non-empty password is required to encrypt the PDF.")
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    perms = pymupdf.PDF_PERM_ACCESSIBILITY
    if allow_print:
        perms |= pymupdf.PDF_PERM_PRINT | pymupdf.PDF_PERM_PRINT_HQ
    if allow_copy:
        perms |= pymupdf.PDF_PERM_COPY
    out = doc.tobytes(
        encryption=pymupdf.PDF_ENCRYPT_AES_256,
        user_pw=pw,
        owner_pw=pw,
        permissions=perms,
        deflate=True,
        garbage=3,
    )
    doc.close()
    return out


def flatten_form_fields(pdf_bytes: bytes) -> tuple[bytes, int]:
    """Bake interactive PDF form widgets permanently into page graphics."""
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    widget_count = sum(1 for page in doc for _ in page.widgets())
    if widget_count == 0:
        doc.close()
        return pdf_bytes, 0
    doc.bake()
    out = doc.tobytes(deflate=True, garbage=3)
    doc.close()
    return out, widget_count


def export_page_image(
    pdf_bytes: bytes, page_idx: int, dpi: int = 200, fmt: str = "png"
) -> bytes:
    """Render a single PDF page to PNG or JPEG bytes."""
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    page = doc[max(0, min(page_idx, len(doc) - 1))]
    zoom = dpi / 72.0
    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
    data = pix.tobytes(output=fmt.lower())
    doc.close()
    return data


def extract_clean_text(pdf_bytes: bytes) -> str:
    """Extract healed plain text from all pages of the PDF."""
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    chunks: list[str] = []
    for idx, page in enumerate(doc):
        txt = heal_pdf_text(page.get_text("text"))
        chunks.append(f"--- Page {idx + 1} ---\n{txt.strip()}\n")
    doc.close()
    return "\n".join(chunks)


def write_bytes_atomic(dest_path: str, data: bytes) -> None:
    """Write PDF safely through a sibling temp file before replacing the destination."""
    parent = os.path.dirname(os.path.abspath(dest_path))
    os.makedirs(parent, exist_ok=True)
    base = os.path.basename(dest_path)
    tmp_path = os.path.join(parent, f".{base}.{os.getpid()}.tmp")
    with open(tmp_path, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    try:
        os.replace(tmp_path, dest_path)
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise
