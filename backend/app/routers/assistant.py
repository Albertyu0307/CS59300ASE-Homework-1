"""LLM features for a saved paper: summary, questions and answers, chat history."""

from collections.abc import Iterator
from contextlib import contextmanager

from fastapi import APIRouter, HTTPException

from app.models import ChatMessage
from app.routers.papers import SessionDep, get_paper_or_404
from app.schemas import AnswerOut, AskRequest, ChatMessageOut, SummaryOut
from app.services import assistant
from app.services.assistant import NoTextError
from app.services.llm_client import LLMError, LLMNotConfiguredError, LLMRateLimitError

router = APIRouter(prefix="/api", tags=["assistant"])


@contextmanager
def _llm_errors() -> Iterator[None]:
    """Turn LLM failures into HTTP errors; the frontend shows their detail as is."""
    try:
        yield
    except NoTextError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except LLMRateLimitError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except LLMNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except LLMError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


# Plain defs (not async): FastAPI runs them in a worker thread, so a slow LLM call doesn't block
# other requests.
@router.get("/papers/{paper_id}/summary", response_model=SummaryOut)
def get_summary(paper_id: int, session: SessionDep) -> SummaryOut:
    """The cached summary (null if none yet) and what text the LLM sees for this paper. No LLM call."""
    return assistant.summary_info(get_paper_or_404(session, paper_id))


@router.post("/papers/{paper_id}/summary", response_model=SummaryOut)
def create_summary(paper_id: int, session: SessionDep, refresh: bool = False) -> SummaryOut:
    """Summarize the paper from its full text. Cached: the LLM is only called the first time,
    or again with refresh=true."""
    paper = get_paper_or_404(session, paper_id)
    with _llm_errors():
        return assistant.summarize(session, paper, refresh)


@router.post("/papers/{paper_id}/ask", response_model=AnswerOut)
def ask_question(paper_id: int, request: AskRequest, session: SessionDep) -> AnswerOut:
    """Answer a question about the paper from its full text. Both are added to the chat history."""
    paper = get_paper_or_404(session, paper_id)
    if not request.question.strip():
        raise HTTPException(status_code=422, detail="The question is empty.")
    with _llm_errors():
        return AnswerOut(answer=assistant.ask(session, paper, request.question))


@router.get("/papers/{paper_id}/messages", response_model=list[ChatMessageOut])
def list_messages(paper_id: int, session: SessionDep) -> list[ChatMessage]:
    """The paper's questions and answers, oldest first."""
    return assistant.chat_history(session, get_paper_or_404(session, paper_id))
