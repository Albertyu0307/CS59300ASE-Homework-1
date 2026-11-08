"""The demo video's five steps (hw1.pdf) in the browser, done the way a user does them: search, save,
upload, summarize, ask. Nothing is put in the database beforehand, and pages are reloaded along the
way, because saved papers, summaries and answers must still be there after a refresh.

The LLM is a fake provider behind the real client, so these tests also check what it was sent: the
text of the paper's PDF, not just the title and abstract shown on the card.
"""

import re

import pytest

from tests.factories import BODY_ONLY_PHRASE, DEFAULT_ABSTRACT, DEFAULT_TITLE, arxiv_entry, arxiv_feed, make_pdf, one_line

expect = pytest.importorskip("playwright.sync_api").expect
pytestmark = pytest.mark.e2e

TITLE = " ".join(DEFAULT_TITLE)  # make_pdf()'s title
SUMMARY_TEXT = "A summary written by the fake provider."  # FakeLLMProvider.SUMMARY, without its heading


def assert_llm_read_the_pdf(llm_provider) -> None:
    """The summary request and the question request both carried the PDF's body text."""
    assert len(llm_provider.requests) == 2
    for messages in llm_provider.requests:
        assert BODY_ONLY_PHRASE in one_line(messages[0]["content"])


def test_search_save_summarize_and_ask(page, http_mock, llm_provider):
    http_mock.get(host="export.arxiv.org", path="/api/query").respond(200, text=arxiv_feed(
        arxiv_entry("2101.00001", title=TITLE, summary=DEFAULT_ABSTRACT),
        arxiv_entry("2102.00002", title="A Paper Left Unsaved", pdf=False),
        arxiv_entry("2103.00003", title="Another Saved Paper", pdf=False),
    ))
    http_mock.get("https://arxiv.org/pdf/2101.00001v2").respond(200, content=make_pdf())
    cards = page.locator(".paper-card")

    # 1. Search. Each result shows its title, authors, year, abstract and a link to the paper.
    page.goto("/")
    page.fill("#search-input", "sparse attention")
    page.click("button[type=submit]")
    expect(cards).to_have_count(3)
    expect(cards.nth(0).locator(".paper-title")).to_have_text(TITLE)
    expect(cards.nth(0).locator(".paper-meta")).to_have_text("Alice Smith, Bob Jones · 2021")
    expect(cards.nth(0).locator(".paper-abstract")).to_have_text(DEFAULT_ABSTRACT)
    expect(cards.nth(0).get_by_role("link", name="View on arXiv")).to_have_attribute("href", "https://arxiv.org/abs/2101.00001v2")

    # 2. Save two of the results. After a refresh, they are still marked as saved.
    for index in (0, 2):
        cards.nth(index).get_by_role("button", name="Save to library").click()
        expect(cards.nth(index).get_by_role("button", name="Saved")).to_be_disabled()
    page.reload()
    expect(cards.nth(0).get_by_role("button", name="Saved")).to_be_disabled()
    expect(cards.nth(1).get_by_role("button", name="Save to library")).to_be_enabled()
    expect(cards.nth(2).get_by_role("button", name="Saved")).to_be_disabled()

    # Browse the library: both saved papers, newest first, with the details the search showed.
    page.get_by_role("link", name="Library", exact=True).click()
    expect(page).to_have_url(re.compile(r"/library\.html$"))
    expect(cards.locator(".paper-title")).to_have_text(["Another Saved Paper", TITLE])
    expect(cards.nth(1).locator(".paper-meta")).to_have_text("Alice Smith, Bob Jones · 2021")
    expect(cards.nth(1).locator(".paper-abstract")).to_have_text(DEFAULT_ABSTRACT)

    # 4. Open the paper and summarize it.
    cards.nth(1).get_by_role("link", name=TITLE).click()
    expect(page.locator("#assistant")).to_be_visible()
    expect(page.locator(".paper-title")).to_have_text(TITLE)
    page.click("#summary-button")
    expect(page.locator("#summary")).to_contain_text(SUMMARY_TEXT)

    # 5. Ask one of the suggested questions.
    page.get_by_role("button", name="What datasets are used?").click()
    expect(page.locator(".chat-message.assistant")).to_contain_text("An answer to: What datasets are used?")
    assert_llm_read_the_pdf(llm_provider)  # the downloaded PDF's text, which the card doesn't show

    # After a refresh, the summary and the answer come back from the library without new LLM calls.
    page.reload()
    expect(page.locator("#summary")).to_contain_text(SUMMARY_TEXT)
    expect(page.locator(".chat-message")).to_have_count(2)
    assert len(llm_provider.requests) == 2


def test_upload_summarize_and_ask(page, llm_provider, db):
    card = page.locator(".paper-card")
    page.goto("/library.html")

    # 3. Upload a PDF. Its title, authors, year and abstract are read from the file and shown on the
    #    same card as a search result.
    page.set_input_files("#upload-input", files=[{
        "name": "sparse-attention.pdf",
        "mimeType": "application/pdf",
        "buffer": make_pdf(header="Proceedings of the Test Conference 2019"),
    }])
    expect(card).to_have_count(1)
    expect(card.locator(".paper-title")).to_have_text(TITLE)
    expect(card.locator(".paper-meta")).to_have_text("Alice Smith, Bob Jones · 2019")
    expect(card.locator(".paper-abstract")).to_have_text(DEFAULT_ABSTRACT)
    expect(card.locator(".badge")).to_have_text("Uploaded PDF")
    [paper] = db.papers()
    expect(card.get_by_role("link", name="PDF")).to_have_attribute("href", f"/api/papers/{paper.id}/pdf")
    assert len(llm_provider.metadata_requests) == 1

    # Still in the library after a refresh.
    page.reload()
    expect(card.locator(".paper-title")).to_have_text(TITLE)

    # 4. Open it and summarize it. An upload has no web page, so its title isn't a link there.
    card.get_by_role("link", name=TITLE).click()
    expect(page.locator("#assistant")).to_be_visible()
    expect(page.locator(".paper-title")).to_have_text(TITLE)
    expect(page.locator(".paper-title a")).to_have_count(0)
    expect(page.locator(".paper-meta")).to_have_text("Alice Smith, Bob Jones · 2019")
    page.click("#summary-button")
    expect(page.locator("#summary")).to_contain_text(SUMMARY_TEXT)

    # 5. Type a question.
    page.fill("#question", "How does this method compare with the baselines?")
    page.locator("#question").press("Enter")
    expect(page.locator(".chat-message.assistant")).to_contain_text(
        "An answer to: How does this method compare with the baselines?"
    )
    assert_llm_read_the_pdf(llm_provider)
