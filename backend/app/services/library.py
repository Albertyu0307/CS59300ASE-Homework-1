"""Adding papers to the library and removing them.

Search results and uploads go through the same PDF pipeline (pdf_processor.read_pdf), so every
paper with a readable PDF has full_text for the LLM (PLAN.md section 9, decision 1).
"""

import hashlib

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.models import ChatMessage, Paper
from app.schemas import PaperResult
from app.services import metadata_extractor, pdf_processor, search_client
from app.services.pdf_processor import PdfError


class AlreadySavedError(Exception):
    """A paper with the same source and external_id is already in the library."""


def save_search_result(session: Session, result: PaperResult) -> Paper:
    """Save a search result and download its PDF, so the summary can use the full text.

    The paper is saved even if the PDF can't be fetched; text_status then says why there is no full text.
    """
    _ensure_not_saved(session, result.source, result.external_id)  # before spending time on the download
    paper = Paper(**result.model_dump())
    if result.pdf_url:
        try:
            data = pdf_processor.download_pdf(result.pdf_url)
            paper.full_text = pdf_processor.read_pdf(data).text
            paper.pdf_path = pdf_processor.store_pdf(data)
            paper.text_status = "full"
        except PdfError:
            paper.text_status = "failed"
    else:
        paper.text_status = "abstract_only"
    return _insert(session, paper)


def save_upload(session: Session, filename: str, data: bytes) -> Paper:
    """Save an uploaded PDF with its text and metadata.

    Raises PdfError if the PDF can't be used, AlreadySavedError if the same paper is already saved.
    """
    content = pdf_processor.read_pdf(data)
    meta = metadata_extractor.extract_metadata(content, filename)

    # A PDF from arXiv carries its ID. Then it is that arXiv paper, with arXiv's exact metadata
    # (no heuristics, no LLM), and it dedupes against the same paper saved from search.
    arxiv = search_client.fetch_arxiv_paper(meta.arxiv_id) if meta.arxiv_id else None
    if arxiv:
        paper = Paper(**arxiv.model_dump())
        _ensure_not_saved(session, paper.source, paper.external_id)
    else:
        # Keyed by the file's hash, so uploading the same file twice is refused like a repeated save.
        external_id = hashlib.sha256(data).hexdigest()
        _ensure_not_saved(session, "upload", external_id)  # before spending an LLM call
        meta = metadata_extractor.improve_with_llm(content, meta)
        paper = Paper(
            source="upload",
            external_id=external_id,
            title=meta.title,
            authors=meta.authors,
            year=meta.year,
            abstract=meta.abstract,
        )
    paper.full_text = content.text
    paper.text_status = "full"
    paper.pdf_path = pdf_processor.store_pdf(data)
    return _insert(session, paper)


def delete_paper(session: Session, paper: Paper) -> None:
    """Remove a paper, its chat history and its stored PDF, if it has one."""
    pdf_path = paper.pdf_path
    # SQLite doesn't enforce foreign keys by default, so the messages are deleted explicitly.
    for message in session.exec(select(ChatMessage).where(ChatMessage.paper_id == paper.id)):
        session.delete(message)
    session.delete(paper)
    session.commit()
    if pdf_path:
        pdf_processor.delete_pdf(pdf_path)


def _ensure_not_saved(session: Session, source: str, external_id: str | None) -> None:
    existing = session.exec(select(Paper.id).where(Paper.source == source, Paper.external_id == external_id)).first()
    if existing is not None:
        raise AlreadySavedError


def _insert(session: Session, paper: Paper) -> Paper:
    session.add(paper)
    try:
        session.commit()
    except IntegrityError as exc:  # a concurrent request (e.g. a double click) saved it first
        session.rollback()
        if paper.pdf_path:
            pdf_processor.delete_pdf(paper.pdf_path)
        raise AlreadySavedError from exc
    session.refresh(paper)
    return paper
