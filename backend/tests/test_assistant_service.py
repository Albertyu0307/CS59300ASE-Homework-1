"""services/assistant.py and services/prompts.py: what the LLM is sent for a paper (full text or
abstract, cut to the length limit), and how the summary and Q&A prompts are laid out."""

import pytest

from app.config import settings
from app.models import Paper
from app.services import assistant, prompts
from app.services.assistant import NoTextError
from app.services.prompts import PaperPrompt

LIMIT = 1000


@pytest.fixture
def short_limit(monkeypatch):
    monkeypatch.setattr(settings, "llm_max_paper_chars", LIMIT)


def paper(**fields) -> Paper:
    values = {"source": "upload", "title": "A Paper", "authors": ["Alice Smith"], "year": 2021,
              "abstract": "The abstract.", "full_text": "The full text."}
    values.update(fields)
    return Paper(**values)


def paper_prompt(**fields) -> PaperPrompt:
    values = {"title": "A Paper", "authors": ["Alice Smith", "Bob Jones"], "year": 2021, "text": "Body text.",
              "abstract_only": False, "truncated": False}
    values.update(fields)
    return PaperPrompt(**values)


# --- Which text goes to the LLM ------------------------------------------------------------------


def test_full_text_is_preferred_over_the_abstract():
    prompt = assistant._paper_prompt(paper())

    assert (prompt.text, prompt.abstract_only, prompt.truncated) == ("The full text.", False, False)


@pytest.mark.parametrize("full_text", [None, ""])
def test_abstract_is_used_when_there_is_no_full_text(full_text):
    prompt = assistant._paper_prompt(paper(full_text=full_text))

    assert (prompt.text, prompt.abstract_only) == ("The abstract.", True)


@pytest.mark.parametrize("abstract", [None, ""])
def test_no_text_at_all_raises(abstract):
    with pytest.raises(NoTextError, match="nothing for the LLM to work with"):
        assistant._paper_prompt(paper(full_text=None, abstract=abstract))


def test_text_exactly_at_the_limit_is_not_cut(short_limit):
    prompt = assistant._paper_prompt(paper(full_text="a" * LIMIT))

    assert (len(prompt.text), prompt.truncated) == (LIMIT, False)


def test_long_text_is_cut_at_a_paragraph_break_near_the_limit(short_limit):
    text = "a" * 950 + "\n\n" + "b" * 500

    prompt = assistant._paper_prompt(paper(full_text=text))

    assert prompt.truncated is True
    assert prompt.text == "a" * 950


def test_long_text_without_a_nearby_paragraph_break_is_cut_at_the_limit(short_limit):
    text = "a" * 500 + "\n\n" + "b" * 1000  # the only break is far before the limit

    prompt = assistant._paper_prompt(paper(full_text=text))

    assert prompt.truncated is True
    assert len(prompt.text) == LIMIT


def test_long_abstract_is_cut_too(short_limit):
    prompt = assistant._paper_prompt(paper(full_text=None, abstract="z" * (LIMIT + 1)))

    assert (len(prompt.text), prompt.truncated, prompt.abstract_only) == (LIMIT, True, True)


def test_summary_info_never_calls_the_llm(llm_calls):
    info = assistant.summary_info(paper(summary="Cached."))

    assert (info.summary, info.based_on, info.truncated) == ("Cached.", "full_text", False)
    assert llm_calls == []


# --- Prompt layout -------------------------------------------------------------------------------


def test_summary_prompt_has_the_paper_in_the_system_message():
    system, request = prompts.summary_messages(paper_prompt())

    assert system["role"] == "system"
    assert "<paper>\nTitle: A Paper\nAuthors: Alice Smith, Bob Jones\nYear: 2021\n\nBody text.\n</paper>" in system["content"]
    assert request == {"role": "user", "content": prompts.SUMMARY_REQUEST}
    for section in ("## Problem", "## Approach", "## Experiments and results", "## Contributions", "## Limitations"):
        assert section in request["content"]


def test_unknown_authors_and_year_are_spelled_out():
    system, _ = prompts.summary_messages(paper_prompt(authors=[], year=None))

    assert "Authors: unknown\nYear: unknown" in system["content"]


@pytest.mark.parametrize(
    "abstract_only, truncated, expected_notes",
    [
        (False, False, []),
        (True, False, ["Only the paper's abstract is available"]),
        (False, True, ["its text below was cut off"]),
        (True, True, ["Only the paper's abstract is available", "its text below was cut off"]),
    ],
)
def test_notes_tell_the_llm_what_it_is_missing(abstract_only, truncated, expected_notes):
    system, _ = prompts.summary_messages(paper_prompt(abstract_only=abstract_only, truncated=truncated))

    for note in ("Only the paper's abstract is available", "its text below was cut off"):
        assert (note in system["content"]) == (note in expected_notes)


def test_paper_text_with_braces_is_inserted_verbatim():
    """Paper text is full of LaTeX ({x}, \\frac{a}{b}); it must not be treated as a template."""
    text = r"We minimize \frac{1}{n} \sum_{i} {loss} and {title} stays as is."

    system, _ = prompts.summary_messages(paper_prompt(text=text))

    assert text in system["content"]


def test_question_prompt_puts_the_history_between_the_paper_and_the_question():
    history = [{"role": "user", "content": "Q1"}, {"role": "assistant", "content": "A1"}]

    messages = prompts.question_messages(paper_prompt(), history, "Q2")

    assert messages[0]["role"] == "system" and "<paper>" in messages[0]["content"]
    assert messages[1:] == [*history, {"role": "user", "content": "Q2"}]


def test_question_and_summary_prompts_share_the_same_system_message():
    """Same prefix for every request about a paper, so providers can cache it."""
    summary_system = prompts.summary_messages(paper_prompt())[0]
    question_system = prompts.question_messages(paper_prompt(), [], "Why?")[0]

    assert summary_system == question_system


def test_metadata_prompt_asks_for_json_only():
    system, user = prompts.metadata_messages("First pages of the paper")

    assert "Reply with only a JSON object" in system["content"]
    assert user == {"role": "user", "content": "First pages of the paper"}
