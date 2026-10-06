"""Library CRUD: save a search result, list saved papers, get one, delete one."""

from contextlib import suppress
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, select

from app.config import settings
from app.database import get_session
from app.models import Paper
from app.schemas import PaperOut, PaperResult

router = APIRouter(prefix="/api", tags=["papers"])

SessionDep = Annotated[Session, Depends(get_session)]

ALREADY_SAVED = "This paper is already in your library."


@router.post("/papers", status_code=201, response_model=PaperOut)
def save_paper(result: PaperResult, session: SessionDep) -> Paper:
    """Save a search result (the whole PaperResult, so the search API isn't queried again)."""
    # Checked up front so nothing is downloaded for a paper that is already saved (Phase 3).
    existing = session.exec(
        select(Paper).where(Paper.source == result.source, Paper.external_id == result.external_id)
    ).first()
    if existing:
        raise HTTPException(status_code=409, detail=ALREADY_SAVED)

    paper = Paper(**result.model_dump())
    # Phase 3: download pdf_url and extract full_text here, with the same pipeline as uploads.
    session.add(paper)
    try:
        session.commit()
    except IntegrityError as exc:  # a concurrent request (e.g. a double click) saved it first
        session.rollback()
        raise HTTPException(status_code=409, detail=ALREADY_SAVED) from exc
    session.refresh(paper)
    return paper


@router.get("/papers", response_model=list[PaperOut])
def list_papers(session: SessionDep) -> list[Paper]:
    """All saved papers, newest first."""
    return list(session.exec(select(Paper).order_by(col(Paper.id).desc())).all())


@router.get("/papers/{paper_id}", response_model=PaperOut)
def get_paper(paper_id: int, session: SessionDep) -> Paper:
    return _get_or_404(session, paper_id)


@router.delete("/papers/{paper_id}", status_code=204)
def delete_paper(paper_id: int, session: SessionDep) -> None:
    """Remove a paper and its local PDF file, if it has one."""
    paper = _get_or_404(session, paper_id)
    pdf_path = paper.pdf_path
    session.delete(paper)
    session.commit()
    if pdf_path:
        # The row is gone either way; a file Windows won't delete (open in a viewer) is just left behind.
        with suppress(OSError):
            (settings.data_path / pdf_path).unlink(missing_ok=True)


def _get_or_404(session: Session, paper_id: int) -> Paper:
    paper = session.get(Paper, paper_id)
    if paper is None:
        raise HTTPException(status_code=404, detail="Paper not found. It may have been deleted.")
    return paper
