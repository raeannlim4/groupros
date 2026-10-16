"""Extract plain text from assignment-brief documents (PDF, Word, text)."""

from __future__ import annotations

import io

MAX_FILE_BYTES = 1_000_000
MAX_DOC_CHARS = 150_000
# A text-based page should yield at least this many characters, else we assume
# the PDF is scanned images with no text layer.
MIN_PDF_CHARS = 50
MAX_DOC_WORDS = MAX_DOC_CHARS // 6

SCANNED = (
    "This PDF looks scanned (images only), so I can't read it. "
    "Try a text-based PDF or paste the text."
)
UNSUPPORTED = "I can only read .pdf, .docx or .txt files, or pasted text."
EMPTY = "That document looks empty. Try another file or paste the text."
UNREADABLE = "I couldn't read that file. Try a different file or paste the text."


class DocumentError(Exception):
    """A problem with the document that has a specific user-facing reply."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def _extension(filename: str) -> str:
    name = (filename or "").lower().strip()
    dot = name.rfind(".")
    return name[dot:] if dot != -1 else ""


def _normalise(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text.strip()


def too_long(chars: int) -> str:
    return (
        f"That document is too long for me to read (about {chars // 6} words; "
        f"my limit is about {MAX_DOC_WORDS} words). Try splitting it into parts "
        "or pasting the key text."
    )


def _decode(data: bytes) -> str:
    try:
        return _normalise(data.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise DocumentError(UNREADABLE) from exc


def _extract_pdf(data: bytes) -> str:
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(data))
        pages = [page.extract_text() or "" for page in reader.pages]
    except DocumentError:
        raise
    except Exception as exc:
        raise DocumentError(UNREADABLE) from exc
    text = _normalise("\n".join(pages))
    if reader.pages and len(text) < MIN_PDF_CHARS:
        raise DocumentError(SCANNED)
    return text


def _extract_docx(data: bytes) -> str:
    from docx import Document as DocxDocument

    try:
        document = DocxDocument(io.BytesIO(data))
    except DocumentError:
        raise
    except Exception as exc:
        raise DocumentError(UNREADABLE) from exc
    parts = [paragraph.text for paragraph in document.paragraphs]
    for table in document.tables:
        for row in table.rows:
            cells = [cell.text.strip() for cell in row.cells]
            if any(cells):
                parts.append(" | ".join(cells))
    return _normalise("\n".join(parts))


def check_text(text: str) -> str:
    """Validate pasted text, raising DocumentError when empty or too long."""
    text = _normalise(text)
    if not text:
        raise DocumentError(EMPTY)
    if len(text) > MAX_DOC_CHARS:
        raise DocumentError(too_long(len(text)))
    return text


def extract_text(filename: str, data: bytes) -> str:
    """Return the document text, or raise DocumentError with a reply."""
    extension = _extension(filename)
    if extension == ".pdf":
        text = _extract_pdf(data)
    elif extension == ".docx":
        text = _extract_docx(data)
    elif extension == ".txt":
        text = _decode(data)
    else:
        raise DocumentError(UNSUPPORTED)
    return check_text(text)
