"""The paper page (paper.html?id=N): details, LLM summary, questions and answers, errors."""

import re

import pytest

from app.config import settings
from tests.conftest import LLM_URL
from tests.factories import completion

expect = pytest.importorskip("playwright.sync_api").expect
pytestmark = pytest.mark.e2e

DAILY_QUOTA = '{"error": {"message": "Quota exceeded: GenerateRequestsPerDayPerProjectPerModel-FreeTier"}}'


def open_paper(page, paper) -> None:
    page.goto(f"/paper.html?id={paper.id}")
    expect(page.locator("#assistant")).to_be_visible()


def chat_entries(page) -> list[tuple[str, str]]:
    """(who, text) for each message in the chat log."""
    return [
        (message.locator(".chat-role").inner_text(), message.locator(".chat-question, .markdown").inner_text())
        for message in page.locator(".chat-message").all()
    ]


# --- The paper and its summary -------------------------------------------------------------------


def test_paper_details_are_shown(page, db):
    paper = db.add_paper(title="Attention Is All You Need", authors=["Ashish Vaswani"], year=2017)

    open_paper(page, paper)

    expect(page.locator(".paper-title")).to_have_text("Attention Is All You Need")
    expect(page.locator(".paper-meta")).to_have_text("Ashish Vaswani · 2017")
    expect(page).to_have_title("Attention Is All You Need · AI Research Assistant")
    expect(page.locator("#summary")).to_have_text("No summary yet. Click Summarize to generate one from the paper's text.")
    expect(page.locator("#summary-button")).to_have_text("Summarize")
    expect(page.locator("#context-note")).to_be_hidden()
    expect(page.locator("#quick-questions button")).to_have_count(5)


def test_summarize_shows_and_stores_the_summary(page, db):
    paper = db.add_paper()
    open_paper(page, paper)

    page.click("#summary-button")

    expect(page.locator("#summary")).to_contain_text("Mock answer:")
    expect(page.locator("#summary-button")).to_have_text("Regenerate")
    expect(page.locator("#summary-button")).to_be_enabled()
    assert db.paper(paper.id).summary.startswith("**Mock answer:**")


def test_cached_summary_is_shown_on_load(page, db):
    paper = db.add_paper(summary="## Problem\nA cached summary.")

    open_paper(page, paper)

    expect(page.locator("#summary")).to_contain_text("A cached summary.")
    expect(page.locator("#summary-button")).to_have_text("Regenerate")


def test_regenerate_asks_for_a_fresh_summary(page, db):
    paper = db.add_paper(summary="An old summary.")
    open_paper(page, paper)

    with page.expect_request(lambda request: "/summary" in request.url and request.method == "POST") as request:
        page.click("#summary-button")

    assert request.value.url.endswith("/summary?refresh=true")
    expect(page.locator("#summary")).to_contain_text("Mock answer:")


def test_summary_failure_is_explained(page, db, http_mock, real_llm):
    http_mock.post(LLM_URL).respond(429, text=DAILY_QUOTA)
    paper = db.add_paper()
    open_paper(page, paper)

    page.click("#summary-button")

    expect(page.locator("#summary-status")).to_have_text(
        "Could not generate the summary: The LLM's free daily quota is used up. Try again tomorrow."
    )
    expect(page.locator("#summary-button")).to_be_enabled()
    expect(page.locator("#summary-button")).to_have_text("Summarize")


# --- What the LLM gets to see ---------------------------------------------------------------------


def test_abstract_only_paper_says_so(page, db):
    paper = db.add_paper(full_text=None, text_status="abstract_only")

    open_paper(page, paper)

    expect(page.locator("#context-note")).to_have_text(
        "No full text is available for this paper, so the summary and answers are based on its abstract only."
    )
    expect(page.locator(".badge.warning")).to_have_text("Abstract only: no PDF available")


def test_very_long_paper_says_only_its_start_is_used(page, db, monkeypatch):
    monkeypatch.setattr(settings, "llm_max_paper_chars", 100)
    paper = db.add_paper(full_text="word " * 100)

    open_paper(page, paper)

    expect(page.locator("#context-note")).to_contain_text("This paper is very long, so only its first part is sent to the LLM.")


def test_paper_without_any_text_offers_no_llm_features(page, db):
    paper = db.add_paper(full_text=None, abstract=None, text_status="failed")

    open_paper(page, paper)

    expect(page.locator("#context-note")).to_have_text(
        "This paper has no PDF text and no abstract, so there is nothing to summarize or ask about."
    )
    for control in ("#summary-button", "#quick-questions", "#ask-form"):
        expect(page.locator(control)).to_have_attribute("hidden", "")


@pytest.mark.xfail(strict=True, reason="Known bug: style.css gives .quick-questions display:flex, which beats the "
                   "browser's [hidden] rule; only Pico.css's [hidden]{display:none!important} hides it, so the "
                   "buttons show when the CDN is unreachable")
def test_paper_without_any_text_hides_its_controls_even_without_the_cdn(page, db):
    paper = db.add_paper(full_text=None, abstract=None, text_status="failed")

    open_paper(page, paper)

    expect(page.locator("#context-note")).to_be_visible()
    for control in ("#summary-button", "#quick-questions", "#ask-form"):
        expect(page.locator(control)).to_be_hidden(timeout=1000)


# --- Questions and answers -----------------------------------------------------------------------


def test_asking_a_typed_question(page, db):
    paper = db.add_paper()
    open_paper(page, paper)

    page.fill("#question", "What datasets are used?")
    page.get_by_role("button", name="Ask", exact=True).click()

    expect(page.locator(".chat-message")).to_have_count(2)
    assert chat_entries(page)[0] == ("You", "What datasets are used?")
    assert chat_entries(page)[1][0] == "Assistant"
    assert "> What datasets are used?" in chat_entries(page)[1][1]
    expect(page.locator("#question")).to_have_value("")
    assert [message.role for message in db.messages(paper.id)] == ["user", "assistant"]


def test_enter_sends_and_shift_enter_adds_a_line(page, db):
    paper = db.add_paper()
    open_paper(page, paper)
    question = page.locator("#question")

    question.type("First line")
    question.press("Shift+Enter")
    question.type("second line")
    expect(page.locator(".chat-message")).to_have_count(0)
    question.press("Enter")

    expect(page.locator(".chat-message")).to_have_count(2)
    assert db.messages(paper.id)[0].content == "First line\nsecond line"


def test_quick_question_buttons_ask_right_away(page, db):
    paper = db.add_paper()
    open_paper(page, paper)

    page.get_by_role("button", name="What are the major limitations?").click()

    expect(page.locator(".chat-message.user")).to_have_text(["YouWhat are the major limitations?"])
    expect(page.locator(".chat-message.assistant")).to_have_count(1)
    expect(page.locator("#quick-questions button").first).to_be_enabled()


def test_blank_question_is_not_sent(page, db):
    paper = db.add_paper()
    open_paper(page, paper)

    page.fill("#question", "   ")
    page.locator("#question").press("Enter")

    page.wait_for_timeout(300)
    expect(page.locator(".chat-message")).to_have_count(0)
    assert db.messages(paper.id) == []


def test_earlier_questions_are_shown_when_the_page_opens(page, db):
    paper = db.add_paper()
    db.add_messages(paper.id, "First question?", "First answer.", "Second question?", "Second answer.")

    open_paper(page, paper)

    expect(page.locator(".chat-message")).to_have_count(4)
    assert chat_entries(page) == [
        ("You", "First question?"), ("Assistant", "First answer."),
        ("You", "Second question?"), ("Assistant", "Second answer."),
    ]


def test_failed_question_is_kept_for_a_retry(page, db, http_mock, real_llm):
    http_mock.post(LLM_URL).respond(429, text=DAILY_QUOTA)
    paper = db.add_paper()
    open_paper(page, paper)

    page.fill("#question", "Will this work?")
    page.get_by_role("button", name="Ask", exact=True).click()

    expect(page.locator("#chat-status")).to_have_text(
        "Could not answer the question: The LLM's free daily quota is used up. Try again tomorrow."
    )
    expect(page.locator(".chat-message")).to_have_count(0)  # the unanswered question is taken back out
    expect(page.locator("#question")).to_have_value("Will this work?")
    expect(page.get_by_role("button", name="Ask", exact=True)).to_be_enabled()
    assert db.messages(paper.id) == []


# --- Bad links -----------------------------------------------------------------------------------


@pytest.mark.parametrize("query", ["", "?id=", "?id=abc", "?id=1;DROP", "?id=-1"])
def test_page_without_a_valid_id(page, query):
    page.goto(f"/paper.html{query}")

    expect(page.locator("#status")).to_have_text("No paper selected. Open one from your library.")
    expect(page.locator("#assistant")).to_be_hidden()


def test_page_of_a_deleted_paper(page):
    page.goto("/paper.html?id=987")

    expect(page.locator("#status")).to_have_text("Could not load the paper: Paper not found. It may have been deleted.")
    expect(page.locator("#status")).to_have_class(re.compile(r"\berror\b"))
    expect(page.locator("#assistant")).to_be_hidden()


# --- Markdown rendering (needs the CDN scripts) ---------------------------------------------------


def test_llm_markdown_and_math_are_rendered_and_sanitized(online_page, db, http_mock, real_llm):
    page = online_page
    http_mock.post(LLM_URL).respond(200, json=completion(
        "The loss is **cross-entropy**, $O(n^2)$ in time.\n\n"
        '<img src="x" onerror="window.hacked = true"><script>window.hacked = true</script>'
    ))
    paper = db.add_paper()
    page.goto(f"/paper.html?id={paper.id}")
    if page.evaluate("typeof window.marked === 'undefined' || typeof window.DOMPurify === 'undefined'"):
        pytest.skip("The CDN scripts could not be loaded (offline?)")

    page.fill("#question", "Which loss?")
    page.locator("#question").press("Enter")

    answer = page.locator(".chat-message.assistant .markdown")
    expect(answer.locator("strong")).to_have_text("cross-entropy")
    expect(answer.locator(".katex")).to_have_count(1)
    expect(answer.locator("script")).to_have_count(0)
    assert answer.locator("img").evaluate_all("images => images.filter(i => i.hasAttribute('onerror')).length") == 0
    assert page.evaluate("window.hacked") is None
