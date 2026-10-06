"""API request/response shapes (not database tables; those live in models.py)."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class PaperResult(BaseModel):
    """One search result, normalized so the rest of the app doesn't care which API it came from."""

    source: Literal["arxiv", "openalex"]  # "openalex" is the search fallback when arXiv is unavailable
    external_id: str  # ID within the source: arXiv "2101.00001" (no version suffix), OpenAlex "W1837145606"
    title: str
    authors: list[str]
    year: int | None = None
    abstract: str | None = None
    url: str | None = None  # landing page
    pdf_url: str | None = None


class PaperOut(BaseModel):
    """A paper in the library. full_text and pdf_path stay server-side, which keeps lists light."""

    id: int
    source: str  # "arxiv", "openalex" or "upload"
    external_id: str | None
    title: str
    authors: list[str]
    year: int | None
    abstract: str | None
    url: str | None
    pdf_url: str | None
    text_status: str  # "full", "abstract_only" or "failed"
    created_at: datetime
