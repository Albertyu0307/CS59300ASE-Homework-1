"""Prompt templates, in one place.

Summary and Q&A share one layout (PLAN.md section 9, decision 6): the instructions and the paper
text in the system message, then the recent chat history, then the new request. The paper text
comes first and is the same for every question, so providers that cache prompt prefixes can reuse it.
"""

from dataclasses import dataclass

from app.services.llm_client import Message

SUMMARY_REQUEST = """Summarize this paper for a researcher who hasn't read it. Use these Markdown sections:

## Problem
What problem the paper addresses and why it matters.

## Approach
The main idea of the proposed method and how it works.

## Experiments and results
The datasets, the baselines, and the key quantitative results.

## Contributions
The main contributions, as a bulleted list.

## Limitations
Limitations the authors state, plus any clear ones they don't (label those as your own assessment).

Stay under about 500 words. Include concrete details from the body of the paper, such as how the \
method works and the numbers it reports, not only what the abstract says."""

_PAPER_SYSTEM = """You are a research assistant helping a reader understand one research paper. \
The paper is below, between <paper> and </paper>.

Base everything you say on the paper. When it doesn't contain the answer, say so plainly instead \
of guessing; if general background knowledge helps, label it as such. Be specific: name the \
datasets, baselines, metrics and numbers the paper reports. Answer questions concisely (a short \
paragraph or a few bullet points) unless the reader asks for more. Format your replies in Markdown; \
write math in LaTeX between single dollar signs ($O(n^2)$) or double ones for a displayed equation.
{notes}
<paper>
Title: {title}
Authors: {authors}
Year: {year}

{text}
</paper>"""

_ABSTRACT_ONLY_NOTE = (
    "\nOnly the paper's abstract is available, not its full text. When a request needs details "
    "the abstract doesn't give, say that the full text isn't available.\n"
)
_TRUNCATED_NOTE = (
    "\nThe paper is very long, so its text below was cut off partway through. When a request "
    "concerns a part that is missing, say so.\n"
)

_METADATA_SYSTEM = """You extract bibliographic metadata from the first pages of a research paper. \
Reply with only a JSON object, no other text, in this form:
{"title": "...", "authors": ["...", "..."], "year": 2024, "abstract": "..."}

- title: the paper's title, as written in the paper.
- authors: the authors' full names in order, without affiliations, emails or footnote marks.
- year: the publication year if the text states it (for example in a conference or journal \
header, a copyright line or a date), otherwise null. Ignore years in citations.
- abstract: the abstract copied word for word, without the "Abstract" heading; null if there is none."""


@dataclass
class PaperPrompt:
    """What the model is told about a paper."""

    title: str
    authors: list[str]
    year: int | None
    text: str  # the full text, or the abstract when there's no full text
    abstract_only: bool
    truncated: bool  # text was cut to settings.llm_max_paper_chars


def summary_messages(paper: PaperPrompt) -> list[Message]:
    return [_paper_system(paper), {"role": "user", "content": SUMMARY_REQUEST}]


def question_messages(paper: PaperPrompt, history: list[Message], question: str) -> list[Message]:
    """history: the most recent questions and answers about this paper, oldest first."""
    return [_paper_system(paper), *history, {"role": "user", "content": question}]


def metadata_messages(first_pages_text: str) -> list[Message]:
    return [
        {"role": "system", "content": _METADATA_SYSTEM},
        {"role": "user", "content": first_pages_text},
    ]


def _paper_system(paper: PaperPrompt) -> Message:
    notes = (_ABSTRACT_ONLY_NOTE if paper.abstract_only else "") + (_TRUNCATED_NOTE if paper.truncated else "")
    content = _PAPER_SYSTEM.format(
        notes=notes,
        title=paper.title,
        authors=", ".join(paper.authors) or "unknown",
        year=paper.year or "unknown",
        text=paper.text,
    )
    return {"role": "system", "content": content}
