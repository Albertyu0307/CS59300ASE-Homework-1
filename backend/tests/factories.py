"""Test data builders: real PDF files (made with PyMuPDF) and fake arXiv / OpenAlex responses."""

from xml.sax.saxutils import escape

import pymupdf

DEFAULT_TITLE = ("Sparse Attention for Long Document", "Understanding at Scale")
DEFAULT_AUTHORS = "Alice Smith1, Bob Jones2"
DEFAULT_ABSTRACT = (
    "We study sparse attention for long documents. Our method attends to a small set of "
    "learned anchor tokens, which reduces the cost of attention from quadratic to linear in "
    "the document length while keeping accuracy on long-range benchmarks."
)
DEFAULT_BODY = (
    "Transformers are strong sequence models, but full attention is expensive for long inputs. "
    "We evaluate on the LongBench and SCROLLS datasets and compare against Longformer and BigBird. "
    "Our model improves accuracy by 2.1 points while using 40 percent less memory. "
) * 3


def make_pdf(
    *,
    title: tuple[str, ...] = DEFAULT_TITLE,
    authors: str | None = DEFAULT_AUTHORS,
    affiliation: str | None = "University of Testing",
    email: str | None = "alice@example.edu",
    header: str | None = None,
    abstract: str | None = DEFAULT_ABSTRACT,
    body: str = DEFAULT_BODY,
    arxiv_stamp: str | None = None,
    metadata: dict[str, str] | None = None,
    extra_pages: tuple[str, ...] = (),
    password: str | None = None,
) -> bytes:
    """A one-column paper laid out like a real first page: small header, big title, authors,
    affiliation, email, "Abstract", "1 Introduction", body text.

    header: small text above the title, e.g. "Proceedings of the Test Conference 2019".
    arxiv_stamp: rotated text up the left margin, e.g. "arXiv:2103.01234v2 [cs.CL] 5 Mar 2021".
    extra_pages: the text of each further page.
    """
    doc = pymupdf.open()
    page = doc.new_page()  # A4: 595 x 842 points
    y = 50.0
    if header:
        page.insert_text((72, y), header, fontsize=9)
    y = 80.0
    for line in title:
        page.insert_text((72, y), line, fontsize=18)
        y += 22
    y += 6
    for text, size in ((authors, 11), (affiliation, 10), (email, 10)):
        if text:
            page.insert_text((72, y), text, fontsize=size)
            y += 15
    y += 15
    if abstract:
        page.insert_text((72, y), "Abstract", fontsize=12)
        y = _textbox(page, y + 10, abstract, height=130) + 20
    page.insert_text((72, y), "1 Introduction", fontsize=12)
    _textbox(page, y + 10, body, height=350)
    if arxiv_stamp:
        page.insert_text((30, 600), arxiv_stamp, fontsize=9, rotate=90)

    for text in extra_pages:
        _textbox(doc.new_page(), 72, text, height=650)

    doc.set_metadata(metadata or {})
    if password:
        return doc.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw=password, owner_pw=password + "-owner")
    return doc.tobytes()


def make_scanned_pdf(pages: int = 1) -> bytes:
    """Pages that are pictures of text: a drawn shape and a page number, no real text layer."""
    doc = pymupdf.open()
    for number in range(1, pages + 1):
        page = doc.new_page()
        page.draw_rect(pymupdf.Rect(72, 72, 523, 770), color=(0, 0, 0), fill=(0.9, 0.9, 0.9))
        page.insert_text((290, 810), str(number), fontsize=9)
    return doc.tobytes()


def _textbox(page: pymupdf.Page, top: float, text: str, height: float) -> float:
    """Writes wrapped text below `top` and returns the y where it ended."""
    rect = pymupdf.Rect(72, top, 523, top + height)
    spare = page.insert_textbox(rect, text, fontsize=10)
    if spare < 0:
        raise ValueError(f"Test PDF text doesn't fit its box ({-spare:.0f} pt too tall)")
    return rect.y1 - spare


# --- arXiv API (Atom XML) ----------------------------------------------------------------------


def arxiv_entry(
    arxiv_id: str = "2101.00001",
    *,
    version: int = 2,
    title: str = "Graph Neural\n      Networks: A Review",
    summary: str = "  We review graph neural networks\n  and their applications.  ",
    authors: tuple[str, ...] = ("Alice Smith", "Bob Jones"),
    published: str | None = "2021-01-05T18:00:00Z",
    pdf: bool = True,
) -> str:
    """One <entry> as arXiv returns it, with http:// links like the real API."""
    versioned = f"{arxiv_id}v{version}"
    parts = [f"<id>http://arxiv.org/abs/{versioned}</id>"]
    if published:
        parts.append(f"<published>{published}</published>")
    parts.append(f"<title>{escape(title)}</title>")
    parts.append(f"<summary>{escape(summary)}</summary>")
    parts += [f"<author><name>{escape(name)}</name></author>" for name in authors]
    parts.append(f'<link href="http://arxiv.org/abs/{versioned}" rel="alternate" type="text/html"/>')
    if pdf:
        parts.append(f'<link title="pdf" href="http://arxiv.org/pdf/{versioned}" rel="related" type="application/pdf"/>')
    return "<entry>" + "".join(parts) + "</entry>"


def arxiv_feed(*entries: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">'
        "<title>ArXiv Query</title>" + "".join(entries) + "</feed>"
    )


# How arXiv reports a malformed query: a single entry pointing at its errors page.
ARXIV_ERROR_FEED = arxiv_feed(
    "<entry><id>http://arxiv.org/api/errors#incorrect_id_format_for_x</id>"
    "<title>Error</title><summary>incorrect id format for x</summary></entry>"
)


def arxiv_abs_page(
    *,
    title: str = "Sparse Attention for Long Documents",
    authors: tuple[str, ...] = ("Smith, Alice", "Jones, Bob"),
    date: str | None = "2021/03/02",
    abstract: str = "We study sparse\n attention.",
    pdf_url: str | None = "http://arxiv.org/pdf/2103.01234",
) -> str:
    """An arXiv abs page, reduced to the citation_* meta tags fetch_arxiv_paper reads."""
    tags = [("title", title)] + [("author", author) for author in authors]
    if date:
        tags.append(("date", date))
    tags.append(("abstract", abstract))
    if pdf_url:
        tags.append(("pdf_url", pdf_url))
    meta = "\n".join(f'<meta name="citation_{name}" content="{escape(value, {chr(34): "&quot;"})}" />' for name, value in tags)
    return f"<!DOCTYPE html><html><head>{meta}</head><body>abs page</body></html>"


# --- OpenAlex API (JSON) -----------------------------------------------------------------------


def openalex_work(
    work_id: str = "W1837145606",
    *,
    title: str | None = "Deep Residual Learning",
    year: int | None = 2016,
    authors: tuple[str, ...] = ("Kaiming He", "Xiangyu Zhang"),
    abstract_words: dict[str, list[int]] | None = None,
    doi: str | None = "https://doi.org/10.1109/cvpr.2016.90",
    landing_page_url: str | None = "https://example.org/resnet",
    primary_pdf_url: str | None = None,
    oa_pdf_url: str | None = "https://example.org/resnet.pdf",
) -> dict:
    if abstract_words is None:
        abstract_words = {"Deeper": [0], "networks": [1, 5], "are": [2], "hard": [3], "to": [4]}
    return {
        "id": f"https://openalex.org/{work_id}",
        "display_name": title,
        "publication_year": year,
        "authorships": [{"author": {"display_name": name}} for name in authors],
        "abstract_inverted_index": abstract_words,
        "doi": doi,
        "primary_location": {"landing_page_url": landing_page_url, "pdf_url": primary_pdf_url},
        "best_oa_location": {"pdf_url": oa_pdf_url} if oa_pdf_url else None,
    }


def openalex_response(*works: dict) -> dict:
    return {"meta": {"count": len(works)}, "results": list(works)}


# --- The app's own API -------------------------------------------------------------------------


def search_result(**overrides) -> dict:
    """A PaperResult as GET /api/search returns it and POST /api/papers accepts it."""
    result = {
        "source": "arxiv",
        "external_id": "2101.00001",
        "title": "Graph Neural Networks: A Review",
        "authors": ["Alice Smith", "Bob Jones"],
        "year": 2021,
        "abstract": "We review graph neural networks and their applications.",
        "url": "https://arxiv.org/abs/2101.00001v2",
        "pdf_url": "https://arxiv.org/pdf/2101.00001v2",
    }
    result.update(overrides)
    return result


def completion(content: str | None) -> dict:
    """An OpenAI-style /chat/completions response body."""
    return {"choices": [{"index": 0, "message": {"role": "assistant", "content": content}}]}
