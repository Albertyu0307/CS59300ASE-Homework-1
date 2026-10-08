"""Database tables (SQLModel). API request/response shapes live in schemas.py."""

from datetime import datetime, timezone

from sqlalchemy import JSON, Column, UniqueConstraint
from sqlmodel import Field, SQLModel


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Paper(SQLModel, table=True):
    """A paper in the library: saved from search or uploaded (schema in PLAN.md section 7)."""

    # Saving the same paper twice is refused: the same search result, or the same uploaded file.
    __table_args__ = (UniqueConstraint("source", "external_id"),)

    id: int | None = Field(default=None, primary_key=True)
    # "arxiv", "openalex" or "upload". An uploaded PDF that carries an arXiv ID is stored as "arxiv".
    source: str
    external_id: str | None = None  # ID within the source; for uploads, the SHA-256 of the file
    title: str
    authors: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))  # stored as JSON text
    year: int | None = None
    abstract: str | None = None
    url: str | None = None  # landing page
    pdf_url: str | None = None  # remote PDF
    pdf_path: str | None = None  # local copy, relative to settings.data_path ("pdfs/<uuid>.pdf")
    full_text: str | None = None  # extracted PDF text that feeds the LLM
    text_status: str = "abstract_only"  # "full", "abstract_only" or "failed"
    summary: str | None = None  # cached LLM summary (Phase 4)
    created_at: datetime = Field(default_factory=_utc_now)


class ChatMessage(SQLModel, table=True):
    """One question or answer in a paper's Q&A history."""

    id: int | None = Field(default=None, primary_key=True)
    paper_id: int = Field(foreign_key="paper.id", index=True)  # deleted along with the paper (library.delete_paper)
    role: str  # "user" or "assistant"
    content: str  # assistant replies are Markdown
    created_at: datetime = Field(default_factory=_utc_now)
