"""Title / authors / year / abstract of an uploaded PDF, from heuristics (no LLM, no network).

- Title: the largest font in the top half of page 1; the PDF's title field as the fallback.
- Authors: the PDF's author field if it holds real names, else name-like lines between the
  title and "Abstract" (emails and affiliations filtered out). The weakest heuristic.
- Year: the arXiv ID stamped in the margin, a "Proceedings ... 2019"-style header on page 1 or in the
  PDF's subject field, or the file's creation date.
- Abstract: the text between an "Abstract" heading and "Introduction"/"Keywords".

improve_with_llm() then asks the LLM for the same fields (one call); the heuristic values stay as
the fallback for anything it doesn't get right.
"""

import logging
import re
from dataclasses import dataclass, replace
from datetime import date
from pathlib import PurePath

from app.services import llm_client, prompts
from app.services.llm_client import LLMError
from app.services.pdf_processor import PdfContent, TextLine

log = logging.getLogger(__name__)


@dataclass
class PaperMetadata:
    title: str
    authors: list[str]
    year: int | None
    abstract: str | None
    arxiv_id: str | None  # arXiv stamps "arXiv:1706.03762v7 [cs.CL] 2 Aug 2023" up the margin of its PDFs


MAX_ABSTRACT_CHARS = 3000
MAX_AUTHORS = 50
# The start of the text sent to the LLM: about two pages, which hold the title, authors, abstract
# and any venue header.
LLM_INPUT_CHARS = 8000

# New-style IDs only (YYMM.NNNNN). The stamp's date is the latest version's, but the ID's YYMM
# is the original submission month, so the year comes from the ID.
_ARXIV_STAMP = re.compile(r"arXiv:(\d{4}\.\d{4,5})")
_HEADER_YEAR = re.compile(
    r"(?:Proceedings|Conference|Workshop|Symposium|Journal|Transactions|Published|Copyright|©)\D{0,80}?\b((?:19|20)\d{2})\b"
)
_ABSTRACT_START = re.compile(r"(?im)^[ \t]*abstract\b[ \t]*[.:—–-]?[ \t]*")
# The abstract ends at the next heading ("1 Introduction", "I. INTRODUCTION", "Keywords"), or at
# the footnotes / arXiv stamp at the bottom of page 1 when the introduction starts on page 2.
_ABSTRACT_END = re.compile(
    r"(?im)^[ \t]*(?:"
    r"(?:(?:\d+|[IVX]+)\.?\s*)?(?:introduction|keywords|key words|index terms|ccs concepts|acm reference format)\b"
    r"|[∗*†‡§¶]|arXiv:\d)"
)
# Lines in the author area that are affiliations, not people.
_AFFILIATION = re.compile(
    r"universit|institut|college|school|department|\bdept\b|laborator|\blabs?\b|research|\binc\b|\bcorp|\bltd\b|"
    r"gmbh|company|cent(?:er|re)|academy|faculty|hospital|google|microsoft|facebook|\bmeta\b|deepmind|openai|"
    r"amazon|\bibm\b|nvidia|brain|language|\bai\b|team|group|foundation|national|science|engineering|technolog",
    re.IGNORECASE,
)
_FOOTNOTE_MARKS = re.compile(r"[∗*†‡§¶⋆★#♯♮♭\d,]+$")
_NAME_PARTICLES = {"van", "von", "de", "der", "den", "da", "di", "del", "della", "la", "le", "du", "bin", "al", "dos", "das"}
_JUNK_TITLE = re.compile(r"^(?:untitled|microsoft word|title)\b|\.(?:pdf|docx?|tex|dvi)$", re.IGNORECASE)


def extract_metadata(content: PdfContent, filename: str) -> PaperMetadata:
    lines = sorted((line for line in content.first_page_lines if line.horizontal), key=lambda l: (round(l.y), l.x))
    title_lines = _title_lines(lines, content.first_page_height)
    arxiv_id = _arxiv_id(content.first_page_lines)

    title = _clean(" ".join(line.text for line in title_lines))
    if not _plausible_title(title):
        info_title = _clean(content.info.get("title", ""))
        title = info_title if _plausible_title(info_title) else PurePath(filename).stem or "Untitled paper"

    authors = _authors_from_info(content.info.get("author", "")) or _authors_from_lines(lines, title_lines)
    return PaperMetadata(
        title=title,
        authors=authors,
        year=_year(content, lines, arxiv_id),
        abstract=_abstract(content.text),
        arxiv_id=arxiv_id,
    )


def improve_with_llm(content: PdfContent, meta: PaperMetadata) -> PaperMetadata:
    """Ask the LLM for the metadata; each field it returns in a plausible form replaces the heuristic one.

    Keeps the heuristic values when the LLM call fails (e.g. rate-limited); mock mode returns no fields.
    One quick retry at most, since the user is waiting for the upload.
    """
    try:
        data = llm_client.chat_json(prompts.metadata_messages(content.text[:LLM_INPUT_CHARS]), retries=1)
    except LLMError as exc:
        log.warning("LLM metadata extraction failed, using the heuristics: %s", exc)
        return meta

    title = _clean(str(data.get("title") or ""))
    authors = data.get("authors")
    names = [_clean(str(name)) for name in authors] if isinstance(authors, list) else []
    names = list(dict.fromkeys(name for name in names if 0 < len(name) <= 100))  # drop blanks and duplicates
    year = data.get("year")
    year = int(year) if isinstance(year, int) or (isinstance(year, str) and year.isdigit()) else None
    abstract = _tidy_abstract(str(data.get("abstract") or ""))
    return replace(
        meta,
        title=title if _plausible_title(title) else meta.title,
        authors=names[:MAX_AUTHORS] or meta.authors,
        year=year if year and 1900 < year <= date.today().year + 1 else meta.year,
        abstract=abstract or meta.abstract,
    )


def _title_lines(lines: list[TextLine], page_height: float) -> list[TextLine]:
    """The run of consecutive largest-font lines in the top half of the page."""
    candidates = [l for l in lines if l.y < page_height / 2 and sum(c.isalpha() for c in l.text) >= 4]
    if not candidates:
        return []
    biggest = max(line.size for line in candidates)
    start = next(i for i, line in enumerate(candidates) if line.size >= biggest - 0.5)
    title = [candidates[start]]
    for line in candidates[start + 1 :]:
        if line.size < biggest - 0.5 or line.y - title[-1].y > 2 * line.size:
            break
        title.append(line)
    return title


def _plausible_title(title: str) -> bool:
    return 10 <= len(title) <= 300 and len(title.split()) >= 2 and not _JUNK_TITLE.search(title)


def _authors_from_info(author_field: str) -> list[str]:
    """The PDF's author field, if every entry in it looks like a person's name."""
    names = [_clean(part) for part in re.split(r";|,|\band\b|&", author_field) if part.strip()]
    return names[:MAX_AUTHORS] if names and all(_looks_like_name(name) for name in names) else []


def _authors_from_lines(lines: list[TextLine], title_lines: list[TextLine]) -> list[str]:
    """Name-like text between the title and the "Abstract" heading (or the first stretch below the title)."""
    if not title_lines:
        return []
    top = title_lines[-1].y
    abstract_y = next((l.y for l in lines if l.y > top and re.match(r"(?i)abstract\b", l.text)), top + 250)
    names: list[str] = []
    for line in lines:
        if not top < line.y < abstract_y or "@" in line.text or "{" in line.text or _AFFILIATION.search(line.text):
            continue
        for part in re.split(r",|;|\band\b|&|\s{2,}", line.text):
            name = _FOOTNOTE_MARKS.sub("", _clean(part)).strip()
            if _looks_like_name(name) and name not in names:
                names.append(name)
    return names[:MAX_AUTHORS]


def _looks_like_name(text: str) -> bool:
    words = text.split()
    return (
        2 <= len(words) <= 5
        and len(text) <= 60
        and all(re.fullmatch(r"[^\W\d_][\w'’.-]*", word) for word in words)
        and all(word[0].isupper() or word.lower() in _NAME_PARTICLES for word in words)
    )


def _arxiv_id(lines: list[TextLine]) -> str | None:
    """The arXiv ID from the stamp arXiv adds up the left margin (rotated text), without its version."""
    for line in lines:
        if not line.horizontal and (match := _ARXIV_STAMP.search(line.text)):
            return match.group(1)
    return None


def _year(content: PdfContent, lines: list[TextLine], arxiv_id: str | None) -> int | None:
    latest = date.today().year + 1
    if arxiv_id:
        return 2000 + int(arxiv_id[:2])
    if match := _HEADER_YEAR.search(" ".join(line.text for line in lines)):
        if 1900 < (year := int(match.group(1))) <= latest:
            return year
    # Publishers often put the venue in the subject field: "2016 IEEE Conference on Computer Vision ..."
    if match := re.search(r"\b((?:19|20)\d{2})\b", content.info.get("subject", "")):
        if 1900 < (year := int(match.group(1))) <= latest:
            return year
    # Document info dates look like "D:20190429173603Z".
    if match := re.match(r"D:((?:19|20)\d{2})", content.info.get("creationDate", "")):
        if (year := int(match.group(1))) <= latest:
            return year
    return None


def _abstract(text: str) -> str | None:
    head = text[:20_000]  # the abstract is near the start; don't scan a whole thesis
    start = _ABSTRACT_START.search(head)
    if not start:
        return None
    body = head[start.end() :]
    if end := _ABSTRACT_END.search(body):
        body = body[: end.start()]
    return _tidy_abstract(body)


def _tidy_abstract(text: str) -> str | None:
    """Whitespace normalized and capped at MAX_ABSTRACT_CHARS; None if too short to be an abstract."""
    abstract = _clean(text)
    if len(abstract) > MAX_ABSTRACT_CHARS:
        # No end marker found nearby: keep whole sentences up to the limit.
        cut = abstract.rfind(". ", 0, MAX_ABSTRACT_CHARS)
        abstract = abstract[: cut + 1] if cut > 0 else abstract[:MAX_ABSTRACT_CHARS]
    return abstract if len(abstract) >= 100 else None


def _clean(text: str) -> str:
    return " ".join(text.split())
