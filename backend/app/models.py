"""Database tables (SQLModel). API request/response shapes live in schemas.py."""

from datetime import datetime, timezone

from sqlalchemy import JSON, Column, UniqueConstraint
from sqlmodel import Field, SQLModel


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Paper(SQLModel, table=True):
    """A paper in the library: saved from search or uploaded (schema in PLAN.md section 7)."""

    # Saving the same search result twice is refused. Uploads have no external_id, and SQLite
    # treats NULLs as distinct, so any number of uploads can coexist.
    __table_args__ = (UniqueConstraint("source", "external_id"),)

    id: int | None = Field(default=None, primary_key=True)
    source: str  # "arxiv", "openalex" or "upload"
    external_id: str | None = None  # ID within the source; None for uploads
    title: str
    authors: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))  # stored as JSON text
    year: int | None = None
    abstract: str | None = None
    url: str | None = None  # landing page
    pdf_url: str | None = None  # remote PDF
    pdf_path: str | None = None  # local copy, relative to settings.data_path (Phase 3)
    full_text: str | None = None  # extracted PDF text that feeds the LLM (Phase 3)
    text_status: str = "abstract_only"  # "full", "abstract_only" or "failed"
    summary: str | None = None  # cached LLM summary (Phase 4)
    created_at: datetime = Field(default_factory=_utc_now)
