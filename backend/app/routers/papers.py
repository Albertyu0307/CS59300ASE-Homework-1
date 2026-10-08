"""Library: save a search result, upload a PDF, list, get, delete, and serve a paper's stored PDF."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlmodel import Session, col, select

from app.database import get_session
from app.models import Paper
from app.schemas import PaperOut, PaperResult
from app.services import library, pdf_processor
from app.services.library import AlreadySavedError
from app.services.pdf_processor import MAX_PDF_BYTES, PdfError

router = APIRouter(prefix="/api", tags=["papers"])

SessionDep = Annotated[Session, Depends(get_session)]

ALREADY_SAVED = "This paper is already in your library."


# Plain defs (not async): FastAPI runs them in a worker thread, so PDF downloads and text
# extraction don't block other requests.
@router.post("/papers", status_code=201, response_model=PaperOut)
def save_paper(result: PaperResult, session: SessionDep) -> Paper:
    """Save a search result (the whole PaperResult, so the search API isn't queried again).

    Also downloads its PDF and extracts the full text, which takes a few seconds.
    """
    try:
        return library.save_search_result(session, result)
    except AlreadySavedError as exc:
        raise HTTPException(status_code=409, detail=ALREADY_SAVED) from exc


@router.post("/papers/upload", status_code=201, response_model=PaperOut)
def upload_paper(file: UploadFile, session: SessionDep) -> Paper:
    """Upload a PDF: extract its text and metadata (title, authors, year, abstract), then save it."""
    filename = file.filename or "upload.pdf"
    if not filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=415, detail="Only PDF files can be uploaded.")
    data = file.file.read(MAX_PDF_BYTES + 1)
    if len(data) > MAX_PDF_BYTES:
        raise HTTPException(status_code=413, detail=f"The PDF is larger than {MAX_PDF_BYTES // (1024 * 1024)} MB.")
    try:
        return library.save_upload(session, filename, data)
    except PdfError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except AlreadySavedError as exc:
        raise HTTPException(status_code=409, detail=ALREADY_SAVED) from exc


@router.get("/papers", response_model=list[PaperOut])
def list_papers(session: SessionDep) -> list[Paper]:
    """All saved papers, newest first."""
    return list(session.exec(select(Paper).order_by(col(Paper.id).desc())).all())


@router.get("/papers/{paper_id}", response_model=PaperOut)
def get_paper(paper_id: int, session: SessionDep) -> Paper:
    return get_paper_or_404(session, paper_id)


@router.get("/papers/{paper_id}/pdf", response_class=FileResponse)
def get_paper_pdf(paper_id: int, session: SessionDep) -> FileResponse:
    """The stored copy of the paper's PDF, shown in the browser (no download prompt)."""
    paper = get_paper_or_404(session, paper_id)
    path = pdf_processor.pdf_file(paper.pdf_path) if paper.pdf_path else None
    if path is None or not path.is_file():
        raise HTTPException(status_code=404, detail="No PDF is stored for this paper.")
    return FileResponse(path, media_type="application/pdf")


@router.delete("/papers/{paper_id}", status_code=204)
def delete_paper(paper_id: int, session: SessionDep) -> None:
    """Remove a paper and its stored PDF, if it has one."""
    library.delete_paper(session, get_paper_or_404(session, paper_id))


def get_paper_or_404(session: Session, paper_id: int) -> Paper:
    paper = session.get(Paper, paper_id)
    if paper is None:
        raise HTTPException(status_code=404, detail="Paper not found. It may have been deleted.")
    return paper
