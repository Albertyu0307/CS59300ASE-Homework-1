"""LLM summary and Q&A for a saved paper.

The paper's whole extracted text goes into the prompt; there's no RAG (PLAN.md section 9, decision 2).
Texts longer than settings.llm_max_paper_chars are cut, and the response says so. Each user action
makes at most one LLM call, and a summary is generated once and then served from the database.
"""

from sqlmodel import Session, col, select

from app.config import settings
from app.models import ChatMessage, Paper
from app.schemas import SummaryOut
from app.services import llm_client, prompts

# The last 3 questions and answers go along with a new question, so follow-ups like
# "and how does it compare with the baselines?" make sense to the model.
HISTORY_MESSAGES = 6


class NoTextError(Exception):
    """The paper has neither full text nor an abstract. The message is user-facing."""

    def __init__(self) -> None:
        super().__init__("This paper has no PDF text and no abstract, so there is nothing for the LLM to work with.")


def summary_info(paper: Paper) -> SummaryOut:
    """The cached summary (None if there is none yet), without calling the LLM."""
    if not paper.full_text and not paper.abstract:
        return SummaryOut(summary=paper.summary, based_on="none", truncated=False)
    prompt = _paper_prompt(paper)
    return SummaryOut(
        summary=paper.summary,
        based_on="abstract" if prompt.abstract_only else "full_text",
        truncated=prompt.truncated,
    )


def summarize(session: Session, paper: Paper, refresh: bool = False) -> SummaryOut:
    """Generate the summary if there's none cached yet (or refresh is set), and store it.

    Raises NoTextError or llm_client.LLMError.
    """
    if paper.summary is None or refresh:
        paper.summary = llm_client.chat(prompts.summary_messages(_paper_prompt(paper)))
        session.add(paper)
        session.commit()
        session.refresh(paper)
    return summary_info(paper)


def ask(session: Session, paper: Paper, question: str) -> str:
    """Answer a question about the paper and add both to its chat history.

    Nothing is stored when the LLM call fails. Raises NoTextError or llm_client.LLMError.
    """
    question = question.strip()
    history = [{"role": m.role, "content": m.content} for m in reversed(_recent_messages(session, paper))]
    answer = llm_client.chat(prompts.question_messages(_paper_prompt(paper), history, question))
    session.add(ChatMessage(paper_id=paper.id, role="user", content=question))
    session.add(ChatMessage(paper_id=paper.id, role="assistant", content=answer))
    session.commit()
    return answer


def chat_history(session: Session, paper: Paper) -> list[ChatMessage]:
    """All of the paper's questions and answers, oldest first."""
    return list(session.exec(select(ChatMessage).where(ChatMessage.paper_id == paper.id).order_by(col(ChatMessage.id))))


def _recent_messages(session: Session, paper: Paper) -> list[ChatMessage]:
    """The paper's last HISTORY_MESSAGES chat messages, newest first."""
    query = (
        select(ChatMessage)
        .where(ChatMessage.paper_id == paper.id)
        .order_by(col(ChatMessage.id).desc())
        .limit(HISTORY_MESSAGES)
    )
    return list(session.exec(query))


def _paper_prompt(paper: Paper) -> prompts.PaperPrompt:
    """The paper as the model sees it: full text if there is any, else the abstract. Raises NoTextError."""
    text = paper.full_text or paper.abstract
    if not text:
        raise NoTextError
    limit = settings.llm_max_paper_chars
    truncated = len(text) > limit
    if truncated:
        # Cut at a paragraph break near the limit, so the text doesn't stop mid-sentence.
        cut = text.rfind("\n\n", 0, limit)
        text = text[: cut if cut > limit * 0.9 else limit]
    return prompts.PaperPrompt(
        title=paper.title,
        authors=paper.authors,
        year=paper.year,
        text=text,
        abstract_only=not paper.full_text,
        truncated=truncated,
    )

