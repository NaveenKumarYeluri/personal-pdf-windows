"""Windows font resolution and MuPDF CSS font-face archive utilities."""

from __future__ import annotations

import os
import re
from functools import lru_cache

import pymupdf

WINDOWS_FONTS_DIR = r"C:\Windows\Fonts"

# Canonical UI font families -> Windows font filenames (regular, bold, italic, bold_italic)
FONT_CATALOG: dict[str, tuple[str, str, str, str]] = {
    "Calibri": ("calibri.ttf", "calibrib.ttf", "calibrii.ttf", "calibriz.ttf"),
    "Arial": ("arial.ttf", "arialbd.ttf", "ariali.ttf", "arialbi.ttf"),
    "Times New Roman": ("times.ttf", "timesbd.ttf", "timesi.ttf", "timesbi.ttf"),
    "Cambria": ("cambria.ttc", "cambriab.ttf", "cambriai.ttf", "cambriaz.ttf"),
    "Consolas": ("consola.ttf", "consolab.ttf", "consolai.ttf", "consolaz.ttf"),
    "Courier New": ("cour.ttf", "courbd.ttf", "couri.ttf", "courbi.ttf"),
    "Georgia": ("georgia.ttf", "georgiab.ttf", "georgiai.ttf", "georgiaz.ttf"),
    "Segoe UI": ("segoeui.ttf", "segoeuib.ttf", "segoeuii.ttf", "segoeuiz.ttf"),
    "Verdana": ("verdana.ttf", "verdanab.ttf", "verdanai.ttf", "verdanaz.ttf"),
    "Tahoma": ("tahoma.ttf", "tahomabd.ttf", "tahoma.ttf", "tahomabd.ttf"),
    "Trebuchet MS": ("trebuc.ttf", "trebucbd.ttf", "trebucit.ttf", "trebucbi.ttf"),
    "Palatino Linotype": ("pala.ttf", "palab.ttf", "palai.ttf", "palabi.ttf"),
    "Comic Sans MS": ("comic.ttf", "comicbd.ttf", "comici.ttf", "comicz.ttf"),
    "Impact": ("impact.ttf", "impact.ttf", "impact.ttf", "impact.ttf"),
}

_FONT_ALIASES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"calibri|carlito", re.I), "Calibri"),
    (re.compile(r"cambria|caladea", re.I), "Cambria"),
    (re.compile(r"times|tinos|nimbusrom|serif(?<!sans-serif)", re.I), "Times New Roman"),
    (re.compile(r"consolas", re.I), "Consolas"),
    (re.compile(r"courier|cousine|mono", re.I), "Courier New"),
    (re.compile(r"georgia", re.I), "Georgia"),
    (re.compile(r"segoe", re.I), "Segoe UI"),
    (re.compile(r"verdana", re.I), "Verdana"),
    (re.compile(r"tahoma", re.I), "Tahoma"),
    (re.compile(r"trebuchet", re.I), "Trebuchet MS"),
    (re.compile(r"palatino", re.I), "Palatino Linotype"),
    (re.compile(r"comic", re.I), "Comic Sans MS"),
    (re.compile(r"impact", re.I), "Impact"),
    (re.compile(r"arial|helvetica|arimo|sans", re.I), "Arial"),
]


def available_ui_fonts() -> list[str]:
    """Return canonical font families available on this machine."""
    if not os.path.isdir(WINDOWS_FONTS_DIR):
        return list(FONT_CATALOG.keys())
    available = []
    for name, files in FONT_CATALOG.items():
        if os.path.exists(os.path.join(WINDOWS_FONTS_DIR, files[0])):
            available.append(name)
    return available or ["Arial", "Times New Roman", "Courier New"]


def resolve_pdf_font_name(raw_font_name: str) -> tuple[str, bool, bool]:
    """Normalize a PDF subset font name into (ui_family, is_bold, is_italic)."""
    name = (raw_font_name or "").strip()
    # Strip PDF 6-letter subset prefix e.g. BCDEEE+Calibri-Bold
    name = re.sub(r"^[A-Z]{6}\+", "", name)
    is_bold = bool(re.search(r"(bold|black|heavy|semibold|\bBd\b|MT-Bold)", name, re.I))
    is_italic = bool(re.search(r"(italic|oblique|\bIt\b|Inclined)", name, re.I))

    for pattern, canonical in _FONT_ALIASES:
        if pattern.search(name):
            return canonical, is_bold, is_italic
    return "Calibri", is_bold, is_italic


@lru_cache(maxsize=1)
def get_windows_font_archive() -> pymupdf.Archive | None:
    """Create a cached MuPDF Archive pointing at Windows Fonts if present."""
    if os.path.isdir(WINDOWS_FONTS_DIR):
        return pymupdf.Archive(WINDOWS_FONTS_DIR)
    return None


@lru_cache(maxsize=1)
def build_mupdf_font_css() -> str:
    """Build @font-face rules for MuPDF Story/insert_htmlbox matching Windows fonts."""
    rules: list[str] = [
        "body, p, div, span { margin: 0; padding: 0; }",
        "p { margin-bottom: 3pt; }",
    ]
    if os.path.isdir(WINDOWS_FONTS_DIR):
        for family, (reg, bold, ital, bold_ital) in FONT_CATALOG.items():
            # Windows calibri.ttf substitutes 'ti' -> U+019F (Ɵ) in MuPDF HarfBuzz PDF export;
            # map Calibri PDF export to Segoe UI so exported PDFs stay 100% searchable & clean.
            if family == "Calibri" and os.path.exists(os.path.join(WINDOWS_FONTS_DIR, "segoeui.ttf")):
                reg, bold, ital, bold_ital = ("segoeui.ttf", "segoeuib.ttf", "segoeuii.ttf", "segoeuiz.ttf")
            variants = [
                (reg, "normal", "normal"),
                (bold, "bold", "normal"),
                (ital, "normal", "italic"),
                (bold_ital, "bold", "italic"),
            ]
            for filename, weight, style in variants:
                full_path = os.path.join(WINDOWS_FONTS_DIR, filename)
                if os.path.exists(full_path):
                    rules.append(
                        f'@font-face {{ font-family: "{family}"; src: url("{filename}"); '
                        f"font-weight: {weight}; font-style: {style}; }}"
                    )
        # Provide generic fallbacks mapped to Segoe UI / Times New Roman / Consolas
        for generic, target in [
            ("sans-serif", "segoeui.ttf"),
            ("serif", "times.ttf"),
            ("monospace", "consola.ttf"),
        ]:
            if os.path.exists(os.path.join(WINDOWS_FONTS_DIR, target)):
                rules.append(f'@font-face {{ font-family: {generic}; src: url("{target}"); }}')
    return "\n".join(rules)
