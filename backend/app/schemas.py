"""API request/response shapes (not database tables; those live in models.py)."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, computed_field


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
    external_id: str | None  # for uploads, the SHA-256 of the file
    title: str
    authors: list[str]
    year: int | None
    abstract: str | None
    url: str | None
    pdf_url: str | None
    # "full": text extracted from the PDF. "abstract_only": no PDF available.
    # "failed": the PDF couldn't be downloaded or read. The last two only have the abstract.
    text_status: str
    created_at: datetime
    pdf_path: str | None = Field(default=None, exclude=True)  # read from the Paper, never sent

    @computed_field
    @property
    def has_pdf(self) -> bool:
        """A copy of the PDF is stored, served by GET /api/papers/{id}/pdf."""
        return self.pdf_path is not None


class SummaryOut(BaseModel):
    """A paper's cached summary, and what text the LLM gets to see for this paper."""

    summary: str | None  # None until one has been generated
    # "full_text": text extracted from the PDF. "abstract": no full text, so only the abstract.
    # "none": neither, so there is nothing to summarize or ask about.
    based_on: Literal["full_text", "abstract", "none"]
    truncated: bool  # the paper is too long to send whole; only its first part goes to the LLM


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)


class AnswerOut(BaseModel):
    answer: str  # Markdown


class ChatMessageOut(BaseModel):
    id: int
    role: Literal["user", "assistant"]
    content: str  # Markdown for assistant replies, plain text for questions
    created_at: datetime
