"""PDF text ligature and broken ToUnicode CMap healing utilities."""

from __future__ import annotations

import re
import unicodedata

# Characters produced by broken PDF ToUnicode CMaps for ligatures (ti, tt, fi, fl, ft, etc.)
_BAD = r"[\ufffd\u0000\u001f\u007f\u019f\?]"

_REPLACEMENTS: list[tuple[re.Pattern[str], str]] = [
    # Standard alphabetic presentation forms
    (re.compile("\uFB00"), "ff"),
    (re.compile("\uFB01"), "fi"),
    (re.compile("\uFB02"), "fl"),
    (re.compile("\uFB03"), "ffi"),
    (re.compile("\uFB04"), "ffl"),
    (re.compile("\uFB05"), "ft"),
    (re.compile("\uFB06"), "st"),
    # Specific high-frequency database/technical terms (handles single or double artifact chars like parƟƟon)
    (re.compile(rf"\bImplementa\s*{_BAD}{{1,2}}on\b"), "Implementation"),
    (re.compile(rf"\bimplemen\s*ta\s*{_BAD}{{1,2}}on\b"), "implementation"),
    (re.compile(rf"\bPar{_BAD}{{1,2}}on\b"), "Partition"),
    (re.compile(rf"\bpar{_BAD}{{1,2}}on\b"), "partition"),
    (re.compile(rf"\bPar{_BAD}{{1,2}}ons\b"), "Partitions"),
    (re.compile(rf"\bpar{_BAD}{{1,2}}ons\b"), "partitions"),
    (re.compile(rf"\bpar{_BAD}{{1,2}}oned\b", re.I), "partitioned"),
    (re.compile(rf"\bpar{_BAD}{{1,2}}oning\b", re.I), "partitioning"),
    (re.compile(rf"\bmul{_BAD}{{1,2}}ple\b", re.I), "multiple"),
    (re.compile(rf"\bmul{_BAD}{{1,2}}ply\b", re.I), "multiply"),
    (re.compile(rf"\bmul{_BAD}{{1,2}}(?=[a-zA-Z])", re.I), "multi"),
    (re.compile(rf"\bspe{_BAD}{{1,2}}c\b", re.I), "specific"),
    (re.compile(rf"\bspe{_BAD}{{1,2}}cs\b", re.I), "specifics"),
    (re.compile(rf"\bspe{_BAD}{{1,2}}cally\b", re.I), "specifically"),
    (re.compile(rf"\bsuf{_BAD}{{1,2}}cent\b", re.I), "sufficient"),
    (re.compile(rf"\bef{_BAD}{{1,2}}cent\b", re.I), "efficient"),
    (re.compile(rf"\bdif{_BAD}{{1,2}}erent\b", re.I), "different"),
    (re.compile(rf"\bdif{_BAD}{{1,2}}cult\b", re.I), "difficult"),
    (re.compile(rf"\bdif{_BAD}{{1,2}}culty\b", re.I), "difficulty"),
    (re.compile(rf"\bdif{_BAD}{{1,2}}culties\b", re.I), "difficulties"),
    (re.compile(rf"\bpar{_BAD}{{1,2}}cular\b", re.I), "particular"),
    (re.compile(rf"\biden{_BAD}{{1,2}}fied\b", re.I), "identified"),
    (re.compile(rf"\biden{_BAD}{{1,2}}fier\b", re.I), "identifier"),
    (re.compile(rf"\biden{_BAD}{{1,2}}fiers\b", re.I), "identifiers"),
    (re.compile(rf"\biden{_BAD}{{1,2}}fy\b", re.I), "identify"),
    (re.compile(rf"([a-zA-Z]){_BAD}{{1,2}}(fi|fl)"), r"\1ti\2"),
    (re.compile(rf"\balloca{_BAD}{{1,2}}on\b", re.I), "allocation"),
    (re.compile(rf"\balloca{_BAD}{{1,2}}ons\b", re.I), "allocations"),
    # ft words
    (re.compile(rf"([a-zA-Z]*a){_BAD}{{1,2}}(er(?:ward|wards)?\b)", re.I), r"\1ft\2"),
    (re.compile(rf"([a-zA-Z]*o){_BAD}{{1,2}}(en\b)", re.I), r"\1ft\2"),
    (re.compile(rf"\bso{_BAD}{{1,2}}er\b", re.I), "softer"),
    (re.compile(rf"\bfa{_BAD}{{1,2}}er\b", re.I), "faster"),
    (re.compile(rf"\ble{_BAD}{{1,2}}\b", re.I), "left"),
    (re.compile(rf"\bso{_BAD}{{1,2}}\b", re.I), "soft"),
    (re.compile(rf"\bcra{_BAD}{{1,2}}ed\b", re.I), "crafted"),
    (re.compile(rf"([a-zA-Z]*){_BAD}{{1,2}}rst\b", re.I), r"\1first"),
    # Generic -tion / -tive / -ting / -tial / -ti- patterns
    (re.compile(rf"([a-zA-Z]){_BAD}{{1,2}}on\b"), r"\1tion"),
    (re.compile(rf"([a-zA-Z]){_BAD}{{1,2}}ons\b"), r"\1tions"),
    (re.compile(rf"([a-zA-Z]){_BAD}{{1,2}}onal\b"), r"\1tional"),
    (re.compile(rf"([a-zA-Z]){_BAD}{{1,2}}oned\b"), r"\1tioned"),
    (re.compile(rf"([a-zA-Z]){_BAD}{{1,2}}oning\b"), r"\1tioning"),
    (re.compile(rf"([a-zA-Z]){_BAD}{{1,2}}ve\b"), r"\1tive"),
    (re.compile(rf"([a-zA-Z]){_BAD}{{1,2}}ves\b"), r"\1tives"),
    (re.compile(rf"([a-zA-Z]){_BAD}{{1,2}}vely\b"), r"\1tively"),
    (re.compile(rf"([a-zA-Z]){_BAD}{{1,2}}vity\b"), r"\1tivity"),
    (re.compile(rf"([a-zA-Z]){_BAD}{{1,2}}ng\b"), r"\1ting"),
    (re.compile(rf"([a-zA-Z]){_BAD}{{1,2}}al\b"), r"\1tial"),
    (re.compile(rf"([a-zA-Z]){_BAD}{{1,2}}ally\b"), r"\1tially"),
    (re.compile(rf"([a-zA-Z])\s*{_BAD}{{1,2}}([aeiou])"), r"\1ti\2"),
    (re.compile(rf"([a-zA-Z]){_BAD}([a-zA-Z])"), r"\1t\2"),
]

_PUA_STRIP_RE = re.compile(r"[\ufffd\u0000\u001f\u007f\uE000-\uF8FF]")


def heal_pdf_text(text: str) -> str:
    """Repair PDF ligatures and broken CMap replacement characters in extracted text."""
    if not text:
        return text

    # Quick exit when no suspicious markers exist
    if not any(ch in text for ch in ("\ufffd", "\x00", "\x1f", "\u019f", "?")) and not re.search(
        r"[\uFB00-\uFB06\uE000-\uF8FF]", text
    ):
        return text

    cleaned = text
    # Handle cases like "Implementa Ɵon" -> "Implementation" before general rules
    cleaned = re.sub(r"\b([A-Za-z]+)a\s+\u019f{1,2}on\b", r"\1ation", cleaned)
    cleaned = re.sub(r"\b([A-Za-z]+)e\s+\u019f{1,2}on\b", r"\1etion", cleaned)

    for pattern, repl in _REPLACEMENTS:
        cleaned = pattern.sub(repl, cleaned)

    # Second pass for \uFB01/\uFB02 that may have been exposed after prefix repair
    cleaned = cleaned.replace("\uFB01", "fi").replace("\uFB02", "fl").replace("\uFB00", "ff").replace("\xa0", " ")
    cleaned = _PUA_STRIP_RE.sub("", cleaned)
    return unicodedata.normalize("NFC", cleaned)
