"""Data models for PDF text blocks, edits, annotations, and search hits."""

from __future__ import annotations

from dataclasses import dataclass, field
import uuid


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


@dataclass
class RectTuple:
    """Normalized or PDF-point rectangle."""

    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def width(self) -> float:
        return max(1.0, self.x1 - self.x0)

    @property
    def height(self) -> float:
        return max(1.0, self.y1 - self.y0)

    def as_tuple(self) -> tuple[float, float, float, float]:
        return (self.x0, self.y0, self.x1, self.y1)


@dataclass
class TextBlock:
    """A structural PDF paragraph/heading block extracted from MuPDF."""

    block_id: str
    page_idx: int
    rect: tuple[float, float, float, float]
    plain_text: str
    html: str
    font_family: str
    font_size: float
    is_bold: bool
    is_italic: bool
    color_hex: str
    line_height_ratio: float = 1.25
    align: str = "left"
    base_page_idx: int = -1
    base_block_id: str = ""
    base_rect: tuple[float, float, float, float] | None = None
    full_edit_html: str | None = None
    full_edit_text: str | None = None
    is_new_block: bool = False


@dataclass
class TextEditOp:
    """A committed in-place edit replacing (or adding) a PDF text block."""

    edit_id: str
    page_idx: int
    orig_rect: tuple[float, float, float, float]
    target_rect: tuple[float, float, float, float]
    original_text: str
    original_html: str
    new_text: str
    new_html: str
    font_family: str
    font_size: float
    color_hex: str
    line_height_ratio: float = 1.25
    reflow_enabled: bool = True
    is_new_block: bool = False
    block_id: str = ""


@dataclass
class InkStroke:
    """Freehand pen or highlighter stroke stored in PDF page points."""

    page_idx: int
    points: list[tuple[float, float]]
    color_hex: str
    stroke_width: float = 3.0
    opacity: float = 1.0
    is_highlighter: bool = False
    item_id: str = field(default_factory=_new_id)


@dataclass
class ShapeItem:
    """Geometric vector annotation (rect, ellipse, line, arrow)."""

    page_idx: int
    shape_type: str  # "rect", "ellipse", "line", "arrow"
    rect: tuple[float, float, float, float]  # x0, y0, x1, y1 in PDF points
    color_hex: str = "#ef4444"
    stroke_width: float = 2.5
    opacity: float = 1.0
    item_id: str = field(default_factory=_new_id)


@dataclass
class TextMarkup:
    """Highlight, underline, or strikethrough markup over selected PDF quads."""

    page_idx: int
    markup_type: str  # "highlight", "underline", "strikethrough"
    rects: list[tuple[float, float, float, float]]
    color_hex: str = "#eab308"
    opacity: float = 0.4
    item_id: str = field(default_factory=_new_id)


@dataclass
class StickyNote:
    """Comment marker stored in PDF points."""

    page_idx: int
    x: float
    y: float
    text: str = "Note"
    author: str = "Personal PDF Suite"
    color_hex: str = "#facc15"
    item_id: str = field(default_factory=_new_id)


@dataclass
class FloatingTextBox:
    """Draggable, resizable rich text box added by user."""

    page_idx: int
    rect: tuple[float, float, float, float]
    html: str
    plain_text: str
    font_family: str = "Calibri"
    font_size: float = 14.0
    color_hex: str = "#000000"
    item_id: str = field(default_factory=_new_id)


@dataclass
class StampItem:
    """Text stamp, signature image, or uploaded image overlay."""

    page_idx: int
    kind: str  # "stamp" or "image"
    rect: tuple[float, float, float, float]
    text: str = ""
    color_hex: str = "#dc2626"
    font_size: float = 18.0
    opacity: float = 0.85
    rotation: float = -8.0
    image_bytes: bytes | None = None
    locked: bool = False
    item_id: str = field(default_factory=_new_id)


@dataclass
class WhiteoutBox:
    """Opaque white masking box in PDF points."""

    page_idx: int
    rect: tuple[float, float, float, float]
    locked: bool = False
    item_id: str = field(default_factory=_new_id)


@dataclass
class RedactionBox:
    """True MuPDF redaction area in PDF points."""

    page_idx: int
    rect: tuple[float, float, float, float]
    locked: bool = False
    item_id: str = field(default_factory=_new_id)


@dataclass
class SearchHit:
    """A full-text search hit on a PDF page."""

    page_idx: int
    rect: tuple[float, float, float, float]
    snippet: str = ""
