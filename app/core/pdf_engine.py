"""Core PDF rendering, structural block extraction, true vector text editing, and burn engine."""

from __future__ import annotations

import html as html_lib
import math
import re
import uuid
from dataclasses import dataclass

import pymupdf

from app.core.fonts import (
    build_mupdf_font_css,
    get_windows_font_archive,
    resolve_pdf_font_name,
)
from app.core.ligatures import heal_pdf_text
from app.core.models import (
    FloatingTextBox,
    InkStroke,
    RedactionBox,
    SearchHit,
    ShapeItem,
    StampItem,
    StickyNote,
    TextBlock,
    TextEditOp,
    TextMarkup,
    WhiteoutBox,
)

SAFE_TOP_MARGIN = 40.0
SAFE_BOTTOM_MARGIN = 36.0
PARA_GAP = 8.0


def _hex_to_rgb01(hex_color: str) -> tuple[float, float, float]:
    clean = (hex_color or "#000000").lstrip("#")
    if len(clean) != 6:
        return (0.0, 0.0, 0.0)
    return (
        int(clean[0:2], 16) / 255.0,
        int(clean[2:4], 16) / 255.0,
        int(clean[4:6], 16) / 255.0,
    )


def qt_html_to_clean_html(
    raw_html: str,
    default_font: str = "Calibri",
    default_size_pt: float = 12.0,
    default_color: str = "#000000",
    line_height_ratio: float = 1.25,
) -> str:
    """
    Convert Qt QTextEdit HTML into clean, compact HTML+CSS suitable for MuPDF's Story/htmlbox
    while preserving bold, italic, underline, strikethrough, colors, font sizes, and alignment.
    """
    body_match = re.search(r"<body[^>]*>(.*)</body>", raw_html or "", re.S | re.I)
    inner = body_match.group(1).strip() if body_match else (raw_html or "").strip()

    # Strip Qt-specific inline CSS properties and huge default <p> margins
    inner = re.sub(r"-qt-[a-zA-Z0-9-]+\s*:[^;\"]*;?", "", inner)
    inner = re.sub(r"margin-(?:top|bottom|left|right)\s*:\s*\d+(?:\.\d+)?px\s*;?", "", inner)
    inner = re.sub(r"text-indent\s*:\s*0(?:px|pt)?\s*;?", "", inner)
    # Never allow double quotes inside double-quoted style="..." attributes
    inner = re.sub(r'font-family\s*:\s*"([^"]+)"', r"font-family:'\1'", inner)
    # Replace nbsp-only paragraphs with explicit blank spacer
    inner = re.sub(r"<p([^>]*)>\s*(?:<br\s*/?>)?\s*</p>", r"<p\1>&nbsp;</p>", inner, flags=re.I)

    return (
        f'<div style="font-family:\'{html_lib.escape(default_font)}\',sans-serif;'
        f'font-size:{default_size_pt:.1f}pt;color:{html_lib.escape(default_color)};'
        f'line-height:{line_height_ratio:.2f};">{inner}</div>'
    )


def measure_html_height(clean_html: str, width_pt: float) -> float:
    """Measure the exact height in PDF points required to render clean_html at width_pt."""
    if not clean_html or not clean_html.strip():
        return 14.0
    css = build_mupdf_font_css()
    arch = get_windows_font_archive()
    story = pymupdf.Story(html=clean_html, user_css=css, archive=arch)
    body = story.body
    # Place inside a tall virtual box and see where content ends
    rect = pymupdf.Rect(0, 0, max(30.0, width_pt), 100000.0)
    more, filled = story.place(rect)
    if filled[3] <= filled[1]:
        return 14.0
    return float(filled[3] - filled[1]) + 2.0


def split_html_to_fit(
    clean_html: str,
    plain_text: str,
    width_pt: float,
    avail_height_pt: float,
    default_font: str,
    default_size_pt: float,
    default_color: str,
    line_height_ratio: float,
) -> tuple[str, str]:
    """
    Split HTML/text cleanly into (fitting_html, overflow_html) when content exceeds
    available page height. Splits at paragraph or word boundaries while preserving styling.
    """
    if avail_height_pt < default_size_pt * 1.1:
        return "", clean_html
    if measure_html_height(clean_html, width_pt) <= avail_height_pt:
        return clean_html, ""

    # Split paragraphs / tokens cleanly and binary-search the prefix that fits
    # Extract outer wrapper style if present
    paras = [p for p in re.findall(r"<p\b[^>]*>.*?</p>", clean_html, re.S | re.I)]
    if len(paras) > 1:
        # Try splitting at paragraph boundary first
        fitting_paras: list[str] = []
        for idx, p_html in enumerate(paras):
            cand = (
                f"<div style=\"font-family:'{html_lib.escape(default_font)}',sans-serif;"
                f'font-size:{default_size_pt:.1f}pt;color:{html_lib.escape(default_color)};'
                f'line-height:{line_height_ratio:.2f};">'
                + "".join(fitting_paras + [p_html])
                + "</div>"
            )
            if measure_html_height(cand, width_pt) <= avail_height_pt:
                fitting_paras.append(p_html)
            else:
                # Try splitting inside this paragraph by plain-text words
                rem_h = avail_height_pt - (
                    measure_html_height(
                        f"<div style=\"font-family:'{html_lib.escape(default_font)}',sans-serif;"
                        f'font-size:{default_size_pt:.1f}pt;color:{html_lib.escape(default_color)};'
                        f'line-height:{line_height_ratio:.2f};">'
                        + "".join(fitting_paras)
                        + "</div>",
                        width_pt,
                    )
                    if fitting_paras
                    else 0.0
                )
                p_text = html_lib.unescape(re.sub(r"<[^>]+>", "", p_html)).strip()
                p_fit, p_over = _split_plain_words(
                    p_text, width_pt, max(0.0, rem_h), default_font, default_size_pt, default_color, line_height_ratio
                )
                fit_parts = list(fitting_paras)
                if p_fit:
                    fit_parts.append(f"<p>{html_lib.escape(p_fit)}</p>")
                over_parts = []
                if p_over:
                    over_parts.append(f"<p>{html_lib.escape(p_over)}</p>")
                over_parts.extend(paras[idx + 1 :])
                wrap = (
                    f"<div style=\"font-family:'{html_lib.escape(default_font)}',sans-serif;"
                    f'font-size:{default_size_pt:.1f}pt;color:{html_lib.escape(default_color)};'
                    f'line-height:{line_height_ratio:.2f};">{{}}</div>'
                )
                return (wrap.format("".join(fit_parts)) if fit_parts else ""), (
                    wrap.format("".join(over_parts)) if over_parts else ""
                )

    p_fit, p_over = _split_plain_words(
        plain_text, width_pt, avail_height_pt, default_font, default_size_pt, default_color, line_height_ratio
    )
    wrap = (
        f"<div style=\"font-family:'{html_lib.escape(default_font)}',sans-serif;"
        f'font-size:{default_size_pt:.1f}pt;color:{html_lib.escape(default_color)};'
        f'line-height:{line_height_ratio:.2f};"><p>{{}}</p></div>'
    )
    return (wrap.format(html_lib.escape(p_fit)) if p_fit else ""), (
        wrap.format(html_lib.escape(p_over)) if p_over else ""
    )


def _split_plain_words(
    text: str,
    width_pt: float,
    avail_h: float,
    font: str,
    size_pt: float,
    color: str,
    line_ratio: float,
) -> tuple[str, str]:
    tokens = re.findall(r"\S+\s*|\s+", text or "")
    if not tokens:
        return "", ""
    lo, hi, best = 0, len(tokens), 0
    while lo <= hi:
        mid = (lo + hi) // 2
        cand_txt = "".join(tokens[:mid]).rstrip()
        cand_html = (
            f"<div style=\"font-family:'{html_lib.escape(font)}',sans-serif;"
            f'font-size:{size_pt:.1f}pt;color:{html_lib.escape(color)};'
            f'line-height:{line_ratio:.2f};"><p>{html_lib.escape(cand_txt)}</p></div>'
        )
        if not cand_txt or measure_html_height(cand_html, width_pt) <= avail_h:
            best = mid
            lo = mid + 1
        else:
            hi = mid - 1
    fit = "".join(tokens[:best]).strip()
    over = "".join(tokens[best:]).strip()
    return fit, over


def create_sample_pdf_bytes() -> bytes:
    """Generate a crisp sample multi-page PDF for quick testing and welcome flow."""
    doc = pymupdf.open()
    p1 = doc.new_page(width=595.28, height=841.89)
    p1.insert_text((44, 56), "Personal PDF Suite — Windows Edition", fontname="hebo", fontsize=20, color=(0.91, 0.18, 0.25))
    p1.insert_text((44, 88), "Vector In-Place Text Editing & Professional PDF Toolkit", fontname="helv", fontsize=12, color=(0.3, 0.35, 0.42))
    p1.insert_htmlbox(
        pymupdf.Rect(44, 114, 550, 260),
        "<p>Click any paragraph in <b>Edit &amp; Watermark</b> mode to modify text directly in place. "
        "Formatting such as <b>bold</b>, <i>italic</i>, <u>underline</u>, and "
        "<span style='color:#2563eb'>custom colors</span> is preserved as sharp vector text.</p>"
        "<p>If you add multiple paragraphs, lower content automatically reflows downward and cascades "
        "cleanly onto Page 2 without clipping or pixel blur.</p>",
    )
    p1.insert_text((44, 290), "Key Capabilities:", fontname="hebo", fontsize=14, color=(0.06, 0.09, 0.16))
    p1.insert_htmlbox(
        pymupdf.Rect(44, 312, 550, 460),
        "<p>• Native Qt6 High-DPI Continuous Viewer with Search &amp; Bookmarks</p>"
        "<p>• Vector Redactions, Whiteout, AES-256 Encryption &amp; Signatures</p>"
        "<p>• Visual Page Organizer, Split, Merge, Watermarks &amp; Bates Numbering</p>",
    )

    p2 = doc.new_page(width=595.28, height=841.89)
    p2.insert_text((44, 60), "Page 2 — Reflow & Multi-Page Workspace", fontname="hebo", fontsize=16, color=(0.06, 0.09, 0.16))
    p2.insert_htmlbox(
        pymupdf.Rect(44, 92, 550, 220),
        "<p>Content overflowing from Page 1 cascades cleanly above this paragraph while keeping "
        "all margins, fonts, and vector crispness intact.</p>",
    )
    data = doc.tobytes(deflate=True, garbage=3)
    doc.close()
    return data


@dataclass
class _PlacedFlowItem:
    kind: str  # "html" or "image"
    x0: float
    y0: float
    x1: float
    y1: float
    html: str = ""
    plain_text: str = ""
    font_family: str = "Calibri"
    font_size: float = 12.0
    color_hex: str = "#000000"
    line_height_ratio: float = 1.25
    image_bytes: bytes | None = None
    base_page_idx: int = -1
    base_block_id: str = ""
    base_rect: tuple[float, float, float, float] | None = None
    full_edit_html: str | None = None
    full_edit_text: str | None = None


class PDFDocumentEngine:
    """Manages base PDF bytes, live vector preview builds, text blocks, and annotations."""

    def __init__(self, pdf_bytes: bytes | None = None, name: str = "document.pdf", file_path: str | None = None):
        self.base_pdf_bytes: bytes = pdf_bytes or b""
        self.name: str = name
        self.file_path: str | None = file_path
        self.needs_password: bool = False
        self._password: str | None = None

        # Non-destructive overlay state (all coordinates in PDF points)
        self.in_place_edits: dict[int, dict[str, TextEditOp]] = {}  # page_idx -> {block_id: TextEditOp}
        self.ink_strokes: dict[int, list[InkStroke]] = {}
        self.shapes: dict[int, list[ShapeItem]] = {}
        self.markups: dict[int, list[TextMarkup]] = {}
        self.sticky_notes: dict[int, list[StickyNote]] = {}
        self.text_boxes: dict[int, list[FloatingTextBox]] = {}
        self.stamps: dict[int, list[StampItem]] = {}
        self.whiteouts: dict[int, list[WhiteoutBox]] = {}
        self.redactions: dict[int, list[RedactionBox]] = {}
        self.custom_bookmarks: list[tuple[str, int]] = []  # (title, page_idx)

        self._working_bytes_cache: bytes | None = None
        self._working_doc: pymupdf.Document | None = None
        self._text_blocks_cache: dict[int, list[TextBlock]] = {}
        self._preview_flow_blocks: dict[int, list[TextBlock]] = {}

        if self.base_pdf_bytes:
            self._open_base_check()

    def _open_base_check(self) -> None:
        doc = pymupdf.open(stream=self.base_pdf_bytes, filetype="pdf")
        self.needs_password = bool(doc.needs_pass)
        doc.close()

    def authenticate(self, password: str) -> bool:
        doc = pymupdf.open(stream=self.base_pdf_bytes, filetype="pdf")
        if not doc.needs_pass:
            doc.close()
            self.needs_password = False
            return True
        ok = bool(doc.authenticate(password))
        if ok:
            # Normalize to decrypted in-memory bytes so editing/saving works smoothly
            self.base_pdf_bytes = doc.tobytes(deflate=True)
            self._password = password
            self.needs_password = False
            self.invalidate_cache()
        doc.close()
        return ok

    def invalidate_cache(self) -> None:
        self._working_bytes_cache = None
        if self._working_doc is not None:
            self._working_doc.close()
            self._working_doc = None
        self._text_blocks_cache.clear()
        self._preview_flow_blocks.clear()

    def has_pending_edits_or_annotations(self) -> bool:
        return any(
            [
                any(v for v in self.in_place_edits.values()),
                any(v for v in self.ink_strokes.values()),
                any(v for v in self.shapes.values()),
                any(v for v in self.markups.values()),
                any(v for v in self.sticky_notes.values()),
                any(v for v in self.text_boxes.values()),
                any(v for v in self.stamps.values()),
                any(v for v in self.whiteouts.values()),
                any(v for v in self.redactions.values()),
                bool(self.custom_bookmarks),
            ]
        )

    def _get_preview_doc(self) -> pymupdf.Document:
        """
        Return the MuPDF document reflecting base PDF + any committed in-place text edits/reflows
        (Vector WYSIWYG!). Annotations/stamps/textboxes remain editable overlays until Save/Export.
        """
        if self._working_doc is not None:
            return self._working_doc
        if any(v for v in self.in_place_edits.values()):
            self._working_bytes_cache = self.build_bytes_with_text_edits(self.base_pdf_bytes)
        else:
            self._working_bytes_cache = self.base_pdf_bytes
        self._working_doc = pymupdf.open(stream=self._working_bytes_cache, filetype="pdf")
        return self._working_doc

    def get_base_doc(self) -> pymupdf.Document:
        return pymupdf.open(stream=self.base_pdf_bytes, filetype="pdf")

    @property
    def page_count(self) -> int:
        if not self.base_pdf_bytes or self.needs_password:
            return 0
        return len(self._get_preview_doc())

    def get_page_size(self, page_idx: int) -> tuple[float, float]:
        doc = self._get_preview_doc()
        p = doc[max(0, min(page_idx, len(doc) - 1))]
        return float(p.rect.width), float(p.rect.height)

    def render_page_rgba(
        self, page_idx: int, zoom: float = 1.0, rotation: int = 0
    ) -> tuple[bytes, int, int]:
        doc = self._get_preview_doc()
        page = doc[max(0, min(page_idx, len(doc) - 1))]
        mat = pymupdf.Matrix(zoom, zoom).prerotate(rotation % 360)
        pix = page.get_pixmap(matrix=mat, alpha=True)
        return pix.samples, pix.width, pix.height

    def render_thumbnail_rgba(self, page_idx: int, max_width: int = 150) -> tuple[bytes, int, int]:
        doc = self._get_preview_doc()
        page = doc[max(0, min(page_idx, len(doc) - 1))]
        zoom = max_width / max(1.0, page.rect.width)
        pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=True)
        return pix.samples, pix.width, pix.height

    def get_toc(self) -> list[tuple[int, str, int]]:
        """Return outline entries as (level, title, zero_based_page_idx)."""
        if not self.base_pdf_bytes or self.needs_password:
            return []
        doc = self._get_preview_doc()
        raw_toc = doc.get_toc(simple=True) or []
        return [(lvl, title, max(0, pno - 1)) for lvl, title, pno in raw_toc]

    def search_text(self, query: str) -> list[SearchHit]:
        """Find all matching occurrences across all pages using MuPDF search_for."""
        q = (query or "").strip()
        if not q or not self.base_pdf_bytes or self.needs_password:
            return []
        doc = self._get_preview_doc()
        hits: list[SearchHit] = []
        for pno, page in enumerate(doc):
            rects = page.search_for(q)
            for r in rects:
                hits.append(SearchHit(page_idx=pno, rect=(r.x0, r.y0, r.x1, r.y1), snippet=q))
        return hits

    def extract_text_blocks(self, page_idx: int, use_base: bool = False) -> list[TextBlock]:
        """Extract clean structural paragraph/heading blocks with healed text and rich HTML."""
        if not use_base and page_idx in self._text_blocks_cache:
            return self._text_blocks_cache[page_idx]

        # Ensure preview build has populated _preview_flow_blocks when edits exist
        if not use_base and any(v for v in self.in_place_edits.values()):
            self._get_preview_doc()
            if page_idx in self._preview_flow_blocks:
                mapped = self._preview_flow_blocks[page_idx]
                self._text_blocks_cache[page_idx] = mapped
                return mapped

        doc = self.get_base_doc() if use_base else self._get_preview_doc()
        if page_idx < 0 or page_idx >= len(doc):
            if use_base:
                doc.close()
            return []

        page = doc[page_idx]
        raw_dict = page.get_text("dict", flags=pymupdf.TEXTFLAGS_TEXT)
        if use_base:
            doc.close()

        blocks: list[TextBlock] = []
        for b_idx, blk in enumerate(raw_dict.get("blocks", [])):
            if blk.get("type") != 0:
                continue
            lines = blk.get("lines", [])
            if not lines:
                continue

            # Split block if a line inside it jumps dramatically in font size (heading vs body)
            sub_groups = self._split_block_on_heading_change(lines)
            for s_idx, group_lines in enumerate(sub_groups):
                tb = self._build_text_block(page_idx, f"p{page_idx}_b{b_idx}_{s_idx}", group_lines)
                if tb is not None:
                    blocks.append(tb)

        if not use_base:
            self._text_blocks_cache[page_idx] = blocks
        return blocks

    def _split_block_on_heading_change(self, lines: list[dict]) -> list[list[dict]]:
        def _line_text(ln: dict) -> str:
            return "".join(s.get("text", "") for s in ln.get("spans", [])).strip()

        def _line_size(ln: dict) -> float:
            sizes = [float(s.get("size", 12.0)) for s in ln.get("spans", []) if s.get("text", "").strip()]
            return max(sizes) if sizes else 12.0

        non_empty = [ln for ln in lines if _line_text(ln)]
        if len(non_empty) <= 1:
            return [non_empty] if non_empty else []

        groups: list[list[dict]] = [[non_empty[0]]]
        prev_size = _line_size(non_empty[0])
        for ln in non_empty[1:]:
            cur_size = _line_size(ln)
            if abs(cur_size - prev_size) >= 1.8:
                groups.append([ln])
            else:
                groups[-1].append(ln)
            prev_size = cur_size
        return groups

    def _build_text_block(self, page_idx: int, block_id: str, lines: list[dict]) -> TextBlock | None:
        all_spans: list[dict] = []
        x0 = y0 = float("inf")
        x1 = y1 = float("-inf")
        plain_lines: list[str] = []

        for ln in lines:
            lx0, ly0, lx1, ly1 = ln["bbox"]
            line_chars = "".join(s.get("text", "") for s in ln.get("spans", []))
            if not line_chars.strip():
                continue
            x0 = min(x0, lx0)
            y0 = min(y0, ly0)
            x1 = max(x1, lx1)
            y1 = max(y1, ly1)
            plain_lines.append(heal_pdf_text(line_chars).strip())
            for sp in ln.get("spans", []):
                if sp.get("text"):
                    all_spans.append(sp)

        if not all_spans or not plain_lines:
            return None

        # Join wrapped lines cleanly into paragraph string
        joined_plain = ""
        for part in plain_lines:
            if not joined_plain:
                joined_plain = part
            elif joined_plain.endswith(("-", "–", "—")):
                joined_plain += part
            else:
                joined_plain += " " + part

        # Compute dominant font, size, weight, italic, and color weighted by char count
        font_weights: dict[str, int] = {}
        color_weights: dict[str, int] = {}
        size_weights: dict[float, int] = {}
        bold_chars = italic_chars = total_chars = 0

        for sp in all_spans:
            t = heal_pdf_text(sp.get("text", "")).strip()
            ln_len = len(t)
            if not ln_len:
                continue
            total_chars += ln_len
            fam, is_b, is_i = resolve_pdf_font_name(sp.get("font", ""))
            flags = int(sp.get("flags", 0))
            if flags & 16:
                is_b = True
            if flags & 2:
                is_i = True
            if is_b:
                bold_chars += ln_len
            if is_i:
                italic_chars += ln_len
            font_weights[fam] = font_weights.get(fam, 0) + ln_len
            c_int = int(sp.get("color", 0))
            c_hex = f"#{c_int:06x}"
            color_weights[c_hex] = color_weights.get(c_hex, 0) + ln_len
            sz = round(float(sp.get("size", 12.0)), 1)
            size_weights[sz] = size_weights.get(sz, 0) + ln_len

        dom_font = max(font_weights, key=font_weights.get) if font_weights else "Calibri"
        dom_color = max(color_weights, key=color_weights.get) if color_weights else "#000000"
        dom_size = max(size_weights, key=size_weights.get) if size_weights else 12.0
        dom_bold = total_chars > 0 and (bold_chars / total_chars) >= 0.5
        dom_italic = total_chars > 0 and (italic_chars / total_chars) >= 0.5

        # Line spacing ratio
        steps = [
            lines[i]["bbox"][1] - lines[i - 1]["bbox"][1]
            for i in range(1, len(lines))
            if (lines[i]["bbox"][1] - lines[i - 1]["bbox"][1]) > 0
        ]
        if steps and dom_size > 0:
            med_step = sorted(steps)[len(steps) // 2]
            lh_ratio = max(1.05, min(1.85, med_step / dom_size))
        else:
            lh_ratio = 1.22

        # Build rich inline HTML preserving inline bold/italic/color runs
        html_runs: list[str] = []
        for l_idx, ln in enumerate(lines):
            line_html = []
            for sp in ln.get("spans", []):
                raw_txt = heal_pdf_text(sp.get("text", ""))
                if not raw_txt:
                    continue
                fam, is_b, is_i = resolve_pdf_font_name(sp.get("font", ""))
                flags = int(sp.get("flags", 0))
                is_b = is_b or bool(flags & 16)
                is_i = is_i or bool(flags & 2)
                c_hex = f"#{int(sp.get('color', 0)):06x}"

                frag = html_lib.escape(raw_txt)
                if is_b != dom_bold:
                    frag = f"<b>{frag}</b>" if is_b else f'<span style="font-weight:normal;">{frag}</span>'
                if is_i != dom_italic:
                    frag = f"<i>{frag}</i>" if is_i else f'<span style="font-style:normal;">{frag}</span>'
                if c_hex.lower() != dom_color.lower():
                    frag = f'<span style="color:{c_hex};">{frag}</span>'
                line_html.append(frag)
            joined_line = "".join(line_html).strip()
            if joined_line:
                if html_runs and not html_runs[-1].endswith("-"):
                    html_runs.append(" ")
                html_runs.append(joined_line)

        inner_html = "".join(html_runs)
        if dom_bold:
            inner_html = f"<b>{inner_html}</b>"
        if dom_italic:
            inner_html = f"<i>{inner_html}</i>"
        rich_html = (
            f"<div style=\"font-family:'{html_lib.escape(dom_font)}',sans-serif;"
            f'font-size:{dom_size:.1f}pt;color:{dom_color};line-height:{lh_ratio:.2f};">'
            f"<p>{inner_html}</p></div>"
        )

        return TextBlock(
            block_id=block_id,
            page_idx=page_idx,
            rect=(float(x0), float(y0), float(x1), float(y1)),
            plain_text=joined_plain,
            html=rich_html,
            font_family=dom_font,
            font_size=dom_size,
            is_bold=dom_bold,
            is_italic=dom_italic,
            color_hex=dom_color,
            line_height_ratio=lh_ratio,
            base_page_idx=page_idx,
            base_block_id=block_id,
            base_rect=(float(x0), float(y0), float(x1), float(y1)),
        )

    def _block_to_clean_html(self, blk: TextBlock) -> str:
        return blk.html

    def find_block_at(self, page_idx: int, pt_x: float, pt_y: float) -> TextBlock | None:
        """Locate the text block containing or closest to (pt_x, pt_y) in PDF points."""
        blocks = self.extract_text_blocks(page_idx)
        for blk in blocks:
            x0, y0, x1, y1 = blk.rect
            if (x0 - 6) <= pt_x <= (x1 + 6) and (y0 - 4) <= pt_y <= (y1 + 4):
                return blk
        return None

    def set_in_place_edit(self, edit: TextEditOp) -> None:
        self.in_place_edits.setdefault(edit.page_idx, {})[edit.block_id] = edit
        self.invalidate_cache()

    def remove_in_place_edit(self, page_idx: int, block_id: str) -> TextEditOp | None:
        page_map = self.in_place_edits.get(page_idx)
        if not page_map or block_id not in page_map:
            return None
        removed = page_map.pop(block_id)
        self.invalidate_cache()
        return removed

    def build_bytes_with_text_edits(self, src_bytes: bytes) -> bytes:
        """
        Apply all committed in-place text edits using true vector PDF redactions,
        rich HTML rendering (insert_htmlbox), downward column reflow, and clean
        multi-page overflow cascades.
        """
        doc = pymupdf.open(stream=src_bytes, filetype="pdf")
        css = build_mupdf_font_css()
        arch = get_windows_font_archive()
        self._preview_flow_blocks.clear()

        incoming_queue: list[_PlacedFlowItem] = []
        p_idx = 0

        while p_idx < len(doc) or incoming_queue:
            if p_idx >= len(doc):
                ref_rect = doc[-1].rect if len(doc) else pymupdf.Rect(0, 0, 595.28, 841.89)
                doc.new_page(width=ref_rect.width, height=ref_rect.height)

            page = doc[p_idx]
            page_w = float(page.rect.width)
            page_h = float(page.rect.height)
            safe_bottom = page_h - SAFE_BOTTOM_MARGIN

            page_edits = dict(self.in_place_edits.get(p_idx, {}))
            has_incoming = bool(incoming_queue)
            if not page_edits and not has_incoming:
                if p_idx < self.get_base_page_count():
                    self._preview_flow_blocks[p_idx] = self.extract_text_blocks(p_idx, use_base=True)
                p_idx += 1
                continue

            # Extract original text blocks and images on this page BEFORE mutating it
            orig_blocks = self.extract_text_blocks(p_idx, use_base=True) if p_idx < self.get_base_page_count() else []
            orig_images = page.get_image_info(xrefs=True)

            placed_items: list[_PlacedFlowItem] = []
            untouched_blocks: list[TextBlock] = []
            redact_text_rects: list[pymupdf.Rect] = []
            moved_image_payloads: list[tuple[pymupdf.Rect, bytes, pymupdf.Rect]] = []

            # Compute base incoming shift
            incoming_placed_height = 0.0
            cursor_y = SAFE_TOP_MARGIN
            next_incoming: list[_PlacedFlowItem] = []

            if has_incoming:
                for idx_in, item in enumerate(incoming_queue):
                    if item.kind == "image":
                        ih = item.y1 - item.y0
                        if cursor_y + ih <= safe_bottom:
                            placed_items.append(
                                _PlacedFlowItem(
                                    kind="image",
                                    x0=item.x0,
                                    y0=cursor_y,
                                    x1=item.x1,
                                    y1=cursor_y + ih,
                                    image_bytes=item.image_bytes,
                                )
                            )
                            cursor_y += ih + PARA_GAP
                        else:
                            next_incoming.extend(incoming_queue[idx_in:])
                            break
                    else:
                        w = max(40.0, item.x1 - item.x0)
                        h = measure_html_height(item.html, w)
                        if cursor_y + h <= safe_bottom:
                            placed_items.append(
                                _PlacedFlowItem(
                                    kind="html",
                                    x0=item.x0,
                                    y0=cursor_y,
                                    x1=item.x1,
                                    y1=cursor_y + h,
                                    html=item.html,
                                    plain_text=item.plain_text,
                                    font_family=item.font_family,
                                    font_size=item.font_size,
                                    color_hex=item.color_hex,
                                    line_height_ratio=item.line_height_ratio,
                                    base_page_idx=item.base_page_idx,
                                    base_block_id=item.base_block_id,
                                    base_rect=item.base_rect,
                                    full_edit_html=item.full_edit_html or item.html,
                                    full_edit_text=item.full_edit_text or item.plain_text,
                                )
                            )
                            cursor_y += h + PARA_GAP
                        else:
                            avail = safe_bottom - cursor_y
                            fit_h, over_h = split_html_to_fit(
                                item.html,
                                item.plain_text,
                                w,
                                avail,
                                item.font_family,
                                item.font_size,
                                item.color_hex,
                                item.line_height_ratio,
                            )
                            if fit_h:
                                fh = measure_html_height(fit_h, w)
                                placed_items.append(
                                    _PlacedFlowItem(
                                        kind="html",
                                        x0=item.x0,
                                        y0=cursor_y,
                                        x1=item.x1,
                                        y1=cursor_y + fh,
                                        html=fit_h,
                                        plain_text=item.plain_text,
                                        font_family=item.font_family,
                                        font_size=item.font_size,
                                        color_hex=item.color_hex,
                                        line_height_ratio=item.line_height_ratio,
                                        base_page_idx=item.base_page_idx,
                                        base_block_id=item.base_block_id,
                                        base_rect=item.base_rect,
                                        full_edit_html=item.full_edit_html or item.html,
                                        full_edit_text=item.full_edit_text or item.plain_text,
                                    )
                                )
                            if over_h:
                                next_incoming.append(
                                    _PlacedFlowItem(
                                        kind="html",
                                        x0=item.x0,
                                        y0=0.0,
                                        x1=item.x1,
                                        y1=0.0,
                                        html=over_h,
                                        plain_text=item.plain_text,
                                        font_family=item.font_family,
                                        font_size=item.font_size,
                                        color_hex=item.color_hex,
                                        line_height_ratio=item.line_height_ratio,
                                        base_page_idx=item.base_page_idx,
                                        base_block_id=item.base_block_id,
                                        base_rect=item.base_rect,
                                        full_edit_html=item.full_edit_html or item.html,
                                        full_edit_text=item.full_edit_text or item.plain_text,
                                    )
                                )
                            next_incoming.extend(incoming_queue[idx_in + 1 :])
                            break

                incoming_placed_height = cursor_y - SAFE_TOP_MARGIN

            # Determine how much existing page content must shift due to incoming items
            orig_min_y = min(
                [b.rect[1] for b in orig_blocks] + [img["bbox"][1] for img in orig_images] + [SAFE_TOP_MARGIN]
            )
            incoming_shift = max(0.0, (cursor_y + PARA_GAP) - orig_min_y) if (has_incoming and incoming_placed_height > 0) else 0.0

            # Measure edits and map each original block -> matched edit (if any)
            matched_edits: dict[str, TextEditOp] = {}
            new_blocks_ops: list[TextEditOp] = []
            for op in page_edits.values():
                if op.is_new_block:
                    new_blocks_ops.append(op)
                else:
                    matched_edits[op.block_id] = op

            # Build vertical shift map from edited blocks
            shift_events: list[tuple[float, float, float, float]] = []  # (orig_x0, orig_x1, orig_y1, delta_y)
            for op in matched_edits.values():
                w = max(40.0, op.target_rect[2] - op.target_rect[0])
                orig_h = max(12.0, op.orig_rect[3] - op.orig_rect[1])
                new_h = measure_html_height(op.new_html, w)
                delta = max(0.0, new_h - orig_h) if op.reflow_enabled else 0.0
                if delta > 0.5:
                    shift_events.append((op.orig_rect[0], op.orig_rect[2], op.orig_rect[3], delta))

            def _cumulative_shift(x0: float, x1: float, y0: float) -> float:
                total = incoming_shift
                for ex0, ex1, ey1, dy in shift_events:
                    if y0 >= ey1 - 4.0:
                        # Same column or wide paragraph (>50% page width)
                        wide = (ex1 - ex0) > (page_w * 0.5)
                        horiz_overlap = not (x1 < ex0 - 20.0 or x0 > ex1 + 20.0)
                        if wide or horiz_overlap:
                            total += dy
                return total

            # Assemble page items in top-to-bottom visual order
            native_items: list[tuple[float, str, object]] = []
            for blk in orig_blocks:
                native_items.append((blk.rect[1], "block", blk))
            for img_info in orig_images:
                ib = img_info["bbox"]
                native_items.append((float(ib[1]), "image", img_info))
            for op in new_blocks_ops:
                native_items.append((op.target_rect[1], "new_op", op))
            native_items.sort(key=lambda item: item[0])

            overflow_triggered = bool(next_incoming)
            for _, kind, payload in native_items:
                if kind == "block":
                    blk: TextBlock = payload
                    op = matched_edits.get(blk.block_id)
                    if op is None:
                        # Match by proximity if base block IDs differ
                        for cand_op in matched_edits.values():
                            if (
                                abs(cand_op.orig_rect[0] - blk.rect[0]) < 8
                                and abs(cand_op.orig_rect[1] - blk.rect[1]) < 8
                            ):
                                op = cand_op
                                break

                    shift = _cumulative_shift(blk.rect[0], blk.rect[2], blk.rect[1])
                    is_edited = op is not None
                    if not is_edited and shift <= 0.5 and not overflow_triggered:
                        untouched_blocks.append(blk)
                        continue  # Leave untouched block completely native on page!

                    # Either edited or shifted -> redact original position
                    redact_text_rects.append(pymupdf.Rect(blk.rect))
                    tx0 = op.target_rect[0] if op else blk.rect[0]
                    tx1 = op.target_rect[2] if op else min(page_w - 36.0, blk.rect[2] + 12.0)
                    ty0 = (op.target_rect[1] if op else blk.rect[1]) + shift
                    w = max(40.0, tx1 - tx0)
                    h_html = op.new_html if op else blk.html
                    h_plain = op.new_text if op else blk.plain_text
                    h_font = op.font_family if op else blk.font_family
                    h_size = op.font_size if op else blk.font_size
                    h_color = op.color_hex if op else blk.color_hex
                    h_ratio = op.line_height_ratio if op else blk.line_height_ratio

                    flow_item = _PlacedFlowItem(
                        kind="html",
                        x0=tx0,
                        y0=ty0,
                        x1=tx1,
                        y1=ty0 + measure_html_height(h_html, w),
                        html=h_html,
                        plain_text=h_plain,
                        font_family=h_font,
                        font_size=h_size,
                        color_hex=h_color,
                        line_height_ratio=h_ratio,
                        base_page_idx=p_idx,
                        base_block_id=blk.block_id,
                        base_rect=blk.rect,
                        full_edit_html=h_html,
                        full_edit_text=h_plain,
                    )

                    if overflow_triggered or flow_item.y0 >= safe_bottom - 14.0:
                        overflow_triggered = True
                        next_incoming.append(flow_item)
                    elif flow_item.y1 <= safe_bottom:
                        placed_items.append(flow_item)
                    else:
                        avail = safe_bottom - flow_item.y0
                        fit_h, over_h = split_html_to_fit(
                            flow_item.html,
                            flow_item.plain_text,
                            w,
                            avail,
                            h_font,
                            h_size,
                            h_color,
                            h_ratio,
                        )
                        if fit_h:
                            fh = measure_html_height(fit_h, w)
                            placed_items.append(
                                _PlacedFlowItem(
                                    kind="html",
                                    x0=tx0,
                                    y0=flow_item.y0,
                                    x1=tx1,
                                    y1=flow_item.y0 + fh,
                                    html=fit_h,
                                    plain_text=h_plain,
                                    font_family=h_font,
                                    font_size=h_size,
                                    color_hex=h_color,
                                    line_height_ratio=h_ratio,
                                    base_page_idx=p_idx,
                                    base_block_id=blk.block_id,
                                    base_rect=blk.rect,
                                    full_edit_html=h_html,
                                    full_edit_text=h_plain,
                                )
                            )
                        if over_h:
                            overflow_triggered = True
                            next_incoming.append(
                                _PlacedFlowItem(
                                    kind="html",
                                    x0=tx0,
                                    y0=0.0,
                                    x1=tx1,
                                    y1=0.0,
                                    html=over_h,
                                    plain_text=h_plain,
                                    font_family=h_font,
                                    font_size=h_size,
                                    color_hex=h_color,
                                    line_height_ratio=h_ratio,
                                    base_page_idx=p_idx,
                                    base_block_id=blk.block_id,
                                    base_rect=blk.rect,
                                    full_edit_html=h_html,
                                    full_edit_text=h_plain,
                                )
                            )

                elif kind == "image":
                    img_info = payload
                    ib = img_info["bbox"]
                    ix0, iy0, ix1, iy1 = float(ib[0]), float(ib[1]), float(ib[2]), float(ib[3])
                    shift = _cumulative_shift(ix0, ix1, iy0)
                    if shift <= 0.5 and not overflow_triggered:
                        continue
                    xref = img_info.get("xref", 0)
                    if not xref:
                        continue
                    extracted = doc.extract_image(xref)
                    img_bytes = extracted.get("image") if extracted else None
                    if not img_bytes:
                        continue
                    old_rect = pymupdf.Rect(ix0, iy0, ix1, iy1)
                    ih = iy1 - iy0
                    new_y0 = iy0 + shift
                    if overflow_triggered or (new_y0 + ih) > safe_bottom:
                        overflow_triggered = True
                        moved_image_payloads.append((old_rect, b"", old_rect))  # mark for removal only
                        next_incoming.append(
                            _PlacedFlowItem(
                                kind="image",
                                x0=ix0,
                                y0=0.0,
                                x1=ix1,
                                y1=ih,
                                image_bytes=img_bytes,
                            )
                        )
                    else:
                        new_rect = pymupdf.Rect(ix0, new_y0, ix1, new_y0 + ih)
                        moved_image_payloads.append((old_rect, img_bytes, new_rect))

                elif kind == "new_op":
                    op: TextEditOp = payload
                    w = max(40.0, op.target_rect[2] - op.target_rect[0])
                    h = measure_html_height(op.new_html, w)
                    ty0 = op.target_rect[1] + incoming_shift
                    placed_items.append(
                        _PlacedFlowItem(
                            kind="html",
                            x0=op.target_rect[0],
                            y0=ty0,
                            x1=op.target_rect[2],
                            y1=ty0 + h,
                            html=op.new_html,
                            plain_text=op.new_text,
                            font_family=op.font_family,
                            font_size=op.font_size,
                            color_hex=op.color_hex,
                            line_height_ratio=op.line_height_ratio,
                            base_page_idx=op.page_idx,
                            base_block_id=op.block_id,
                            base_rect=op.orig_rect,
                            full_edit_html=op.new_html,
                            full_edit_text=op.new_text,
                        )
                    )

            # Also redact any edited blocks that didn't match an extracted dict block directly
            for op in matched_edits.values():
                r = pymupdf.Rect(op.orig_rect)
                if not any(abs(r.y0 - rr.y0) < 2 and abs(r.x0 - rr.x0) < 2 for rr in redact_text_rects):
                    redact_text_rects.append(r)

            # 1. Remove moved images from old positions first
            if moved_image_payloads:
                for old_rect, _, _ in moved_image_payloads:
                    page.add_redact_annot(old_rect)
                page.apply_redactions(
                    images=pymupdf.PDF_REDACT_IMAGE_REMOVE,
                    graphics=pymupdf.PDF_REDACT_LINE_ART_NONE,
                )

            # 2. Redact replaced/shifted text glyphs (keeping graphics/images intact!)
            if redact_text_rects:
                for rr in redact_text_rects:
                    page.add_redact_annot(rr)
                page.apply_redactions(
                    images=pymupdf.PDF_REDACT_IMAGE_NONE,
                    graphics=pymupdf.PDF_REDACT_LINE_ART_NONE,
                )

            # 3. Re-insert shifted images that stayed on this page
            for _, img_bytes, new_rect in moved_image_payloads:
                if img_bytes:
                    page.insert_image(new_rect, stream=img_bytes)

            # 4. Draw placed rich HTML text as real vector PDF streams
            preview_list: list[TextBlock] = list(untouched_blocks)
            for item in placed_items:
                if item.kind == "image" and item.image_bytes:
                    page.insert_image(pymupdf.Rect(item.x0, item.y0, item.x1, item.y1), stream=item.image_bytes)
                elif item.kind == "html" and item.html.strip():
                    w = max(40.0, item.x1 - item.x0)
                    h = max(16.0, (item.y1 - item.y0) + 8.0)
                    target = pymupdf.Rect(item.x0, item.y0, item.x0 + w, min(page_h - 8.0, item.y0 + h))
                    page.insert_htmlbox(target, item.html, css=css, archive=arch)
                    if item.base_block_id:
                        preview_list.append(
                            TextBlock(
                                block_id=item.base_block_id,
                                page_idx=p_idx,
                                rect=(item.x0, item.y0, item.x0 + w, min(page_h - 8.0, item.y1)),
                                plain_text=item.full_edit_text or item.plain_text,
                                html=item.full_edit_html or item.html,
                                font_family=item.font_family,
                                font_size=item.font_size,
                                is_bold=False,
                                is_italic=False,
                                color_hex=item.color_hex,
                                line_height_ratio=item.line_height_ratio,
                                base_page_idx=item.base_page_idx if item.base_page_idx >= 0 else p_idx,
                                base_block_id=item.base_block_id,
                                base_rect=item.base_rect or (item.x0, item.y0, item.x1, item.y1),
                                full_edit_html=item.full_edit_html,
                                full_edit_text=item.full_edit_text,
                            )
                        )
            self._preview_flow_blocks[p_idx] = preview_list

            incoming_queue = next_incoming
            p_idx += 1

        try:
            doc.subset_fonts()
        except Exception:
            pass
        out = doc.tobytes(deflate=True, garbage=3)
        doc.close()
        return out

    def get_base_page_count(self) -> int:
        doc = pymupdf.open(stream=self.base_pdf_bytes, filetype="pdf")
        try:
            return len(doc)
        finally:
            doc.close()

    def build_final_pdf_bytes(self) -> bytes:
        """
        Bake ALL edits (in-place text edits/reflow, ink, shapes, markups, sticky notes,
        text boxes, stamps, signatures, whiteouts, true redactions, bookmarks)
        into a crisp, selectable vector PDF ready to save to disk.
        """
        working_bytes = (
            self.build_bytes_with_text_edits(self.base_pdf_bytes)
            if any(v for v in self.in_place_edits.values())
            else self.base_pdf_bytes
        )
        doc = pymupdf.open(stream=working_bytes, filetype="pdf")
        css = build_mupdf_font_css()
        arch = get_windows_font_archive()

        for page_idx in range(len(doc)):
            page = doc[page_idx]

            # 1. Permanent true redactions (remove underlying text & images in box)
            redacts = self.redactions.get(page_idx, [])
            if redacts:
                for rb in redacts:
                    page.add_redact_annot(pymupdf.Rect(rb.rect), fill=(0, 0, 0))
                page.apply_redactions(
                    images=pymupdf.PDF_REDACT_IMAGE_REMOVE,
                    graphics=pymupdf.PDF_REDACT_LINE_ART_REMOVE_IF_TOUCHED,
                )

            # 2. Whiteout rectangles
            for wb in self.whiteouts.get(page_idx, []):
                page.draw_rect(pymupdf.Rect(wb.rect), color=(1, 1, 1), fill=(1, 1, 1))

            # 3. Text markups (highlight, underline, strikethrough)
            for mk in self.markups.get(page_idx, []):
                col = _hex_to_rgb01(mk.color_hex)
                for r in mk.rects:
                    rect = pymupdf.Rect(r)
                    if mk.markup_type == "highlight":
                        page.draw_rect(rect, color=None, fill=col, fill_opacity=mk.opacity)
                    elif mk.markup_type == "underline":
                        y = rect.y1 - 1.5
                        page.draw_line((rect.x0, y), (rect.x1, y), color=col, width=1.6)
                    elif mk.markup_type == "strikethrough":
                        y = (rect.y0 + rect.y1) / 2.0
                        page.draw_line((rect.x0, y), (rect.x1, y), color=col, width=1.6)

            # 4. Ink & highlighter strokes
            for st in self.ink_strokes.get(page_idx, []):
                if len(st.points) < 2:
                    continue
                col = _hex_to_rgb01(st.color_hex)
                pts = [pymupdf.Point(px, py) for px, py in st.points]
                shape = page.new_shape()
                shape.draw_polyline(pts)
                shape.finish(
                    color=col,
                    width=st.stroke_width,
                    stroke_opacity=st.opacity,
                    lineCap=1,
                    lineJoin=1,
                )
                shape.commit()

            # 5. Geometric shapes
            for sh in self.shapes.get(page_idx, []):
                col = _hex_to_rgb01(sh.color_hex)
                r = pymupdf.Rect(sh.rect)
                shape = page.new_shape()
                if sh.shape_type == "rect":
                    shape.draw_rect(r)
                elif sh.shape_type == "ellipse":
                    shape.draw_oval(r)
                elif sh.shape_type in ("line", "arrow"):
                    p0 = pymupdf.Point(sh.rect[0], sh.rect[1])
                    p1 = pymupdf.Point(sh.rect[2], sh.rect[3])
                    shape.draw_line(p0, p1)
                    if sh.shape_type == "arrow":
                        ang = math.atan2(p1.y - p0.y, p1.x - p0.x)
                        head_len = max(8.0, sh.stroke_width * 3.2)
                        for sign in (-1, 1):
                            hx = p1.x - head_len * math.cos(ang + sign * 0.45)
                            hy = p1.y - head_len * math.sin(ang + sign * 0.45)
                            shape.draw_line(p1, pymupdf.Point(hx, hy))
                shape.finish(color=col, width=sh.stroke_width, stroke_opacity=sh.opacity)
                shape.commit()

            # 6. Floating typewriter rich text boxes
            for tb in self.text_boxes.get(page_idx, []):
                r = pymupdf.Rect(tb.rect)
                html_str = qt_html_to_clean_html(
                    tb.html,
                    default_font=tb.font_family,
                    default_size_pt=tb.font_size,
                    default_color=tb.color_hex,
                )
                page.insert_htmlbox(r, html_str, css=css, archive=arch)

            # 7. Stamps, signatures, and inserted images
            for stp in self.stamps.get(page_idx, []):
                r = pymupdf.Rect(stp.rect)
                if stp.kind == "image" and stp.image_bytes:
                    rot_val = int(round(stp.rotation / 90.0) * 90) % 360
                    page.insert_image(r, stream=stp.image_bytes, rotate=rot_val, keep_proportion=True)
                elif stp.kind == "stamp" and stp.text:
                    col = _hex_to_rgb01(stp.color_hex)
                    shape = page.new_shape()
                    shape.draw_rect(r)
                    shape.finish(color=col, width=2.5, stroke_opacity=stp.opacity)
                    shape.commit()
                    tw = pymupdf.TextWriter(page.rect)
                    font = pymupdf.Font("hebo")
                    fsize = stp.font_size
                    text_w = font.text_length(stp.text, fontsize=fsize)
                    while text_w > (r.width - 10) and fsize > 8:
                        fsize -= 1
                        text_w = font.text_length(stp.text, fontsize=fsize)
                    cx = (r.x0 + r.x1) / 2.0
                    cy = (r.y0 + r.y1) / 2.0
                    rad = math.radians(stp.rotation)
                    dx = -(text_w / 2.0) * math.cos(rad) + (fsize * 0.35) * math.sin(rad)
                    dy = (text_w / 2.0) * math.sin(rad) + (fsize * 0.35) * math.cos(rad)
                    origin = pymupdf.Point(cx + dx, cy + dy)
                    mat = pymupdf.Matrix(
                        math.cos(-rad), math.sin(-rad), -math.sin(-rad), math.cos(-rad), 0, 0
                    )
                    tw.append(origin, stp.text, font=font, fontsize=fsize)
                    tw.write_text(page, color=col, opacity=stp.opacity, morph=(origin, mat))

            # 8. Sticky notes (native PDF Text annotations)
            for note in self.sticky_notes.get(page_idx, []):
                annot = page.add_text_annot((note.x, note.y), note.text, icon="Comment")
                annot.set_info(title=note.author, content=note.text)
                annot.update()

        # Append custom bookmarks to TOC
        if self.custom_bookmarks:
            toc = doc.get_toc(simple=True) or []
            for title, p_idx in self.custom_bookmarks:
                if 0 <= p_idx < len(doc):
                    toc.append([1, title, p_idx + 1])
            doc.set_toc(toc)

        try:
            doc.subset_fonts()
        except Exception:
            pass
        out = doc.tobytes(deflate=True, garbage=3)
        doc.close()
        return out


def build_pdf_from_word_flow_html(raw_html: str, page_size: tuple[float, float] = (595.28, 841.89)) -> bytes:
    """
    Create a crisp multi-page vector PDF from continuous Word-Flow rich HTML
    using MuPDF's Story pagination engine.
    """
    import io

    clean = qt_html_to_clean_html(raw_html)
    css = build_mupdf_font_css() + "\nbody { margin: 0; }\np { margin-bottom: 6pt; }"
    arch = get_windows_font_archive()
    story = pymupdf.Story(html=clean, user_css=css, archive=arch)

    w, h = page_size
    mediabox = pymupdf.Rect(0, 0, w, h)
    content_rect = pymupdf.Rect(42, 42, w - 42, h - 42)
    out_stream = io.BytesIO()
    writer = pymupdf.DocumentWriter(out_stream)
    more = True
    pages_written = 0
    while more and pages_written < 200:
        dev = writer.begin_page(mediabox)
        more, _ = story.place(content_rect)
        story.draw(dev, None)
        writer.end_page()
        pages_written += 1
    writer.close()

    res_doc = pymupdf.open(stream=out_stream.getvalue(), filetype="pdf")
    try:
        res_doc.subset_fonts()
    except Exception:
        pass
    out = res_doc.tobytes(deflate=True, garbage=3)
    res_doc.close()
    return out
