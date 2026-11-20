"""Plain text of uploaded documents, for full-text search.

PDF and Word files are read; scanned PDFs (images only) give no text and are left empty.
A broken or unreadable file never blocks the upload.
"""

import io
import logging
from pathlib import PurePath

log = logging.getLogger(__name__)

MAX_CHARS = 400_000          # a search vector must stay below 1 MB
MAX_BYTES = 60 * 1024 * 1024


def _pdf(data):
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    if reader.is_encrypted:
        reader.decrypt("")
    parts, size = [], 0
    for page in reader.pages:
        text = page.extract_text() or ""
        parts.append(text)
        size += len(text)
        if size > MAX_CHARS:
            break
    return "\n".join(parts)


def _docx(data):
    from docx import Document

    doc = Document(io.BytesIO(data))
    parts = [p.text for p in doc.paragraphs]
    for table in doc.tables:
        for row in table.rows:
            parts.append(" | ".join(cell.text.strip() for cell in row.cells))
    return "\n".join(parts)


def extract_text(data, filename="", mime_type=None, compact=True):
    """Text of the file, or None when there is none or the type is not readable.

    ``compact`` puts it on one line (what search needs); otherwise lines and table rows are kept for reading.
    """
    if not data or len(data) > MAX_BYTES:
        return None
    suffix = PurePath(filename or "").suffix.lower()
    mime = (mime_type or "").lower()
    try:
        if suffix == ".pdf" or mime == "application/pdf":
            text = _pdf(data)
        elif suffix == ".docx" or "wordprocessingml" in mime:
            text = _docx(data)
        elif suffix in {".txt", ".md", ".csv"} or mime.startswith("text/plain"):
            text = data.decode("utf-8", errors="replace")
        else:
            return None
    except Exception as exc:  # noqa: BLE001 - damaged files are common; the document is still saved
        log.warning("Could not read the text of %s: %s", filename, exc)
        return None
    text = text.replace("\x00", " ")
    if compact:
        text = " ".join(text.split())      # one line: search does not need the layout
    else:
        text = "\n".join(line.strip() for line in text.splitlines() if line.strip())
    return text[:MAX_CHARS * (1 if compact else 3)] or None
