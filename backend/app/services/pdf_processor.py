"""PDF handling: download, open and extract text (PyMuPDF), and store files under data/pdfs/.

This is the only module that uses PyMuPDF. It hands plain data (PdfContent) to metadata_extractor.
"""

import re
import uuid
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

import httpx
import pymupdf

from app.config import settings

MAX_PDF_BYTES = 25 * 1024 * 1024
DOWNLOAD_TIMEOUT_SECONDS = 30
# A real paper has thousands of characters per page. Far fewer means the pages are images of
# text (a scanned PDF), which would need OCR.
MIN_CHARS_PER_PAGE = 200
PDF_DIR = "pdfs"  # under settings.data_path
USER_AGENT = "ai-research-assistant/0.1 (student course project)"

# Expand ligatures (the single glyph "ﬁ" becomes "fi") so words read and match normally.
# (PyMuPDF's TEXT_DEHYPHENATE flag had no effect on real papers, so read_pdf rejoins words itself.)
_TEXT_FLAGS = pymupdf.TEXTFLAGS_TEXT & ~pymupdf.TEXT_PRESERVE_LIGATURES
_DICT_FLAGS = pymupdf.TEXTFLAGS_DICT & ~pymupdf.TEXT_PRESERVE_LIGATURES & ~pymupdf.TEXT_PRESERVE_IMAGES
# A word hyphenated across a line break: "representa-\ntion". Lowercase after the break only, so
# "Pre-\nTraining" keeps its hyphen; "pre-\ntrain" becomes "pretrain", a fair trade.
_LINE_BREAK_HYPHEN = re.compile(r"(\w)-\n([a-z])")


class PdfError(Exception):
    """The PDF could not be downloaded or read. The message is user-facing."""


@dataclass
class TextLine:
    """One line of text on a page, with the layout details the metadata heuristics use."""

    text: str
    size: float  # largest font size in the line, in points
    x: float  # left edge, in points
    y: float  # top edge, in points from the top of the page
    horizontal: bool  # False for rotated text, e.g. the arXiv ID stamped up the left margin


@dataclass
class PdfContent:
    text: str  # text of all pages, in reading order
    page_count: int
    info: dict[str, str]  # non-empty document info fields: title, author, creationDate, ...
    first_page_lines: list[TextLine]
    first_page_height: float


def read_pdf(data: bytes) -> PdfContent:
    """Open a PDF and extract its text. Raises PdfError for non-PDFs and damaged, locked or scanned files."""
    # The "%PDF-" header may be preceded by a little junk, which readers tolerate.
    if b"%PDF-" not in data[:1024]:
        raise PdfError("This file is not a PDF.")
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception as exc:  # PyMuPDF raises several exception types for broken files
        raise PdfError("The PDF could not be opened. It may be damaged.") from exc

    with doc:
        if doc.needs_pass:
            raise PdfError("The PDF is password-protected.")
        if doc.page_count == 0:
            raise PdfError("The PDF has no pages.")
        try:
            pages = [page.get_text("text", flags=_TEXT_FLAGS) for page in doc]
            first_page_lines = _lines(doc[0])
            first_page_height = doc[0].rect.height
        except Exception as exc:
            raise PdfError("The PDF could not be read. It may be damaged.") from exc
        info = {key: value.strip() for key, value in (doc.metadata or {}).items() if value and value.strip()}
        page_count = doc.page_count

    text = _LINE_BREAK_HYPHEN.sub(r"\1\2", "\n\n".join(pages).replace("\x00", "")).strip()
    if len(text) < MIN_CHARS_PER_PAGE * page_count:
        raise PdfError(
            "Almost no text could be extracted from this PDF. It is probably a scanned document "
            "(images of pages), which isn't supported."
        )
    return PdfContent(text, page_count, info, first_page_lines, first_page_height)


def download_pdf(url: str) -> bytes:
    """Download a PDF of at most MAX_PDF_BYTES. Raises PdfError. The content isn't checked here; read_pdf does that."""
    chunks: list[bytes] = []
    size = 0
    try:
        with httpx.stream(
            "GET", url, timeout=DOWNLOAD_TIMEOUT_SECONDS, follow_redirects=True, headers={"User-Agent": USER_AGENT}
        ) as response:
            response.raise_for_status()
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > MAX_PDF_BYTES:
                    raise PdfError(f"The PDF is larger than {MAX_PDF_BYTES // (1024 * 1024)} MB.")
                chunks.append(chunk)
    except httpx.TimeoutException as exc:
        raise PdfError("The PDF download timed out.") from exc
    except httpx.HTTPStatusError as exc:
        raise PdfError(f"The PDF download failed (HTTP {exc.response.status_code}).") from exc
    except httpx.HTTPError as exc:
        raise PdfError("The PDF could not be downloaded.") from exc
    return b"".join(chunks)


def store_pdf(data: bytes) -> str:
    """Save a PDF under data/pdfs/ with a unique name. Returns its path relative to settings.data_path."""
    relative_path = f"{PDF_DIR}/{uuid.uuid4().hex}.pdf"
    path = pdf_file(relative_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return relative_path


def pdf_file(relative_path: str) -> Path:
    """The absolute path of a stored PDF (Paper.pdf_path is relative to settings.data_path)."""
    return settings.data_path / relative_path


def delete_pdf(relative_path: str) -> None:
    # A file Windows refuses to delete (e.g. open in a PDF viewer) is just left behind.
    with suppress(OSError):
        pdf_file(relative_path).unlink(missing_ok=True)


def _lines(page: pymupdf.Page) -> list[TextLine]:
    lines = []
    for block in page.get_text("dict", flags=_DICT_FLAGS)["blocks"]:
        for line in block.get("lines", []):
            spans = [span for span in line["spans"] if span["text"].strip()]
            text = " ".join("".join(span["text"] for span in line["spans"]).split())
            if not spans or not text:
                continue
            dx, dy = line["dir"]
            x0, y0 = line["bbox"][0], line["bbox"][1]
            lines.append(TextLine(text, max(span["size"] for span in spans), x0, y0, dx > 0.99 and abs(dy) < 0.01))
    return lines
