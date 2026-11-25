# Personal PDF Suite — Windows Edition

Native Windows desktop PDF suite built with **Python 3.13 + PySide6 (Qt6) + PyMuPDF (MuPDF)**.

## Why This Architecture
- **True Vector In-Place Text Editing**: Uses MuPDF structural text blocks + native Qt `QTextEdit` rich-text editing + MuPDF vector redactions and `insert_htmlbox` / `Story` rendering (no `contentEditable` / `document.execCommand` / raster PNG flattening).
- **Word-Flow Mode**: Continuous multi-page Word-style document editor powered by `QTextEdit` + `pymupdf.Story`.
- **Full Desktop Feature Set**: Continuous high-DPI viewing, search, thumbnails, outline/bookmarks, pen/highlighter/shapes/markup/stamps/notes, draw/type/uploaded signature with adaptive paper removal, visual page organizer, split/merge, watermarks, Bates numbering, AES-256 encryption, true redaction, native Windows printing, PNG/text export.
- **Low Footprint**: ~80–120 MB RAM, fast local testing (`pytest` + `pytest-qt`), and cloud `.exe` builds via GitHub Actions.

## Running & Testing Locally
```cmd
python -m pip install -r requirements.txt
run.bat
```

Run automated tests (including real Qt GUI + vector PDF verification):
```cmd
pytest -v
```
