"""API request/response shapes (not database tables; those live in models.py)."""

from pydantic import BaseModel


class PaperResult(BaseModel):
    """One search result, normalized so the rest of the app doesn't care which API it came from."""

    source: str  # "arxiv" or "openalex" (search fallback when arXiv is unavailable)
    external_id: str  # ID within the source: arXiv "2101.00001" (no version suffix), OpenAlex "W1837145606"
    title: str
    authors: list[str]
    year: int | None = None
    abstract: str | None = None
    url: str | None = None  # landing page
    pdf_url: str | None = None
