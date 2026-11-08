"""The search page (index.html): searching, result cards, saving to the library, errors."""

import re

import pytest

from tests.factories import arxiv_entry, arxiv_feed, make_pdf, openalex_response, openalex_work

expect = pytest.importorskip("playwright.sync_api").expect
pytestmark = pytest.mark.e2e


@pytest.fixture
def arxiv(http_mock):
    return http_mock.get(host="export.arxiv.org", path="/api/query")


@pytest.fixture
def openalex(http_mock):
    return http_mock.get(host="api.openalex.org", path="/works")


def search(page, query: str) -> None:
    page.goto("/")
    page.fill("#search-input", query)
    page.click("button[type=submit]")


def test_footer_shows_the_backend_is_up(page):
    page.goto("/")

    expect(page.locator("#health")).to_have_text("Backend: ok")


def test_search_shows_a_card_per_result(page, arxiv):
    arxiv.respond(200, text=arxiv_feed(arxiv_entry("2101.00001"), arxiv_entry("2102.00002", title="Second Paper", pdf=False)))

    search(page, "graph neural networks")

    expect(page).to_have_url(re.compile(r"index\.html\?q=graph\+neural\+networks&source=auto$"))
    cards = page.locator(".paper-card")
    expect(cards).to_have_count(2)
    first = cards.nth(0)
    expect(first.locator(".paper-title")).to_have_text("Graph Neural Networks: A Review")
    expect(first.locator(".paper-meta")).to_have_text("Alice Smith, Bob Jones · 2021")
    expect(first.locator(".paper-abstract")).to_have_text("We review graph neural networks and their applications.")
    expect(first.get_by_role("link", name="View on arXiv")).to_have_attribute("href", "https://arxiv.org/abs/2101.00001v2")
    expect(first.get_by_role("link", name="PDF")).to_have_attribute("href", "https://arxiv.org/pdf/2101.00001v2")
    expect(cards.nth(1).get_by_role("link", name="PDF")).to_have_count(0)
    expect(page.locator("#status")).to_be_hidden()


def test_search_runs_from_the_url(page, openalex):
    openalex.respond(200, json=openalex_response(openalex_work()))

    page.goto("/index.html?q=residual+learning&source=openalex")

    expect(page.locator("#search-input")).to_have_value("residual learning")
    expect(page.locator("#search-source")).to_have_value("openalex")
    expect(page.locator(".paper-card .paper-title")).to_have_text("Deep Residual Learning")


def test_openalex_choice_is_remembered(page, arxiv, openalex):
    openalex.respond(200, json=openalex_response(openalex_work()))
    page.goto("/")
    page.select_option("#search-source", "openalex")
    page.fill("#search-input", "residual")
    page.click("button[type=submit]")
    expect(page.locator(".paper-card")).to_have_count(1)

    page.goto("/")

    expect(page.locator("#search-source")).to_have_value("openalex")
    assert not arxiv.called


def test_no_results_message(page, arxiv):
    arxiv.respond(200, text=arxiv_feed())

    search(page, "xyzzy")

    expect(page.locator("#status")).to_have_text('No papers found for "xyzzy". Try fewer or different words.')
    expect(page.locator(".paper-card")).to_have_count(0)


def test_search_failure_message(page, arxiv, openalex):
    arxiv.respond(503)
    openalex.respond(500)

    search(page, "graph")

    status = page.locator("#status")
    expect(status).to_have_class(re.compile(r"\berror\b"))
    expect(status).to_have_text(
        "Search failed: arXiv returned HTTP 503. The fallback source also failed: OpenAlex returned HTTP 500."
    )


def test_notice_when_results_come_from_the_fallback(page, arxiv, openalex):
    arxiv.respond(429)
    openalex.respond(200, json=openalex_response(openalex_work()))

    search(page, "residual")

    expect(page.locator("#status")).to_contain_text("arXiv is unavailable right now, so these results come from OpenAlex.")
    expect(page.locator(".paper-card")).to_have_count(1)


def test_saving_a_result_downloads_its_pdf(page, arxiv, http_mock, db):
    arxiv.respond(200, text=arxiv_feed(arxiv_entry("2101.00001")))
    http_mock.get("https://arxiv.org/pdf/2101.00001v2").respond(200, content=make_pdf())
    search(page, "graph")

    page.get_by_role("button", name="Save to library").click()

    expect(page.get_by_role("button", name="Saved")).to_be_disabled()
    [saved] = db.papers()
    expect(page.get_by_role("link", name="Open in library")).to_have_attribute("href", f"paper.html?id={saved.id}")
    assert saved.text_status == "full"
    expect(page.locator(".save-control .warning")).to_have_count(0)


def test_saving_a_result_without_a_pdf_warns_about_it(page, arxiv):
    arxiv.respond(200, text=arxiv_feed(arxiv_entry(pdf=False)))
    search(page, "graph")

    page.get_by_role("button", name="Save to library").click()

    expect(page.locator(".save-control .warning")).to_have_text("Abstract only: no PDF available")


def test_results_already_in_the_library_are_marked_saved(page, arxiv, db):
    saved = db.add_paper(source="arxiv", external_id="2101.00001")
    arxiv.respond(200, text=arxiv_feed(arxiv_entry("2101.00001"), arxiv_entry("2102.00002")))

    search(page, "graph")

    cards = page.locator(".paper-card")
    expect(cards.nth(0).get_by_role("button", name="Saved")).to_be_disabled()
    expect(cards.nth(0).get_by_role("link", name="Open in library")).to_have_attribute("href", f"paper.html?id={saved.id}")
    expect(cards.nth(1).get_by_role("button", name="Save to library")).to_be_enabled()


def test_failed_save_shows_the_error_and_can_be_retried(page, arxiv, db):
    arxiv.respond(200, text=arxiv_feed(arxiv_entry(pdf=False)))
    fail_once = [True]

    def first_save_fails(route):
        if route.request.method == "POST" and fail_once:
            fail_once.pop()
            route.fulfill(status=500, json={"detail": "Database is locked"})
        else:
            route.continue_()

    page.route("**/api/papers", first_save_fails)
    search(page, "graph")
    save = page.get_by_role("button", name="Save to library")

    save.click()
    expect(page.locator(".save-control .error")).to_have_text("Save failed: Database is locked")
    expect(save).to_be_enabled()
    save.click()  # the retry reaches the backend this time

    expect(page.get_by_role("button", name="Saved")).to_be_disabled()
    assert len(db.papers()) == 1


def test_save_refused_as_duplicate_counts_as_saved(page, arxiv):
    """Saved in the meantime, e.g. from another tab: the backend answers 409."""
    arxiv.respond(200, text=arxiv_feed(arxiv_entry(pdf=False)))
    page.route("**/api/papers", lambda route: route.fulfill(status=409, json={"detail": "This paper is already in your library."})
               if route.request.method == "POST" else route.continue_())
    search(page, "graph")

    page.get_by_role("button", name="Save to library").click()

    expect(page.get_by_role("button", name="Saved")).to_be_disabled()
    expect(page.get_by_role("link", name="Open in library")).to_have_attribute("href", "library.html")


def test_long_abstract_can_be_expanded_and_collapsed(page, arxiv):
    arxiv.respond(200, text=arxiv_feed(arxiv_entry(summary="A long abstract sentence. " * 20)))
    search(page, "graph")
    abstract = page.locator(".paper-abstract")
    toggle = page.get_by_role("button", name="Show more")

    expect(abstract).to_have_class(re.compile(r"\bcollapsed\b"))
    toggle.click()
    expect(abstract).not_to_have_class(re.compile(r"\bcollapsed\b"))
    page.get_by_role("button", name="Show less").click()
    expect(abstract).to_have_class(re.compile(r"\bcollapsed\b"))
    expect(page.get_by_role("button", name="Show more")).to_be_visible()


def test_short_abstract_has_no_toggle(page, arxiv):
    arxiv.respond(200, text=arxiv_feed(arxiv_entry()))

    search(page, "graph")

    expect(page.locator(".paper-card")).to_have_count(1)
    expect(page.locator(".toggle-abstract")).to_have_count(0)


def test_result_without_authors_or_year_or_abstract(page, openalex):
    openalex.respond(200, json=openalex_response(openalex_work(authors=(), year=None, abstract_words={})))

    page.goto("/index.html?q=x&source=openalex")

    expect(page.locator(".paper-meta")).to_have_text("Unknown authors · Year unknown")
    expect(page.locator(".paper-abstract")).to_have_text("No abstract available.")


def test_long_author_lists_are_shortened(page, openalex):
    authors = tuple(f"Author Number{i}" for i in range(1, 10))
    openalex.respond(200, json=openalex_response(openalex_work(authors=authors)))

    page.goto("/index.html?q=x&source=openalex")

    expect(page.locator(".paper-meta")).to_have_text(
        "Author Number1, Author Number2, Author Number3, Author Number4, Author Number5, Author Number6, "
        "et al. (9 authors) · 2016"
    )


def test_paper_data_is_shown_as_text_never_as_html(page, openalex):
    """A malicious title or abstract from a search API must not run in the page."""
    evil_title = '<img src=x onerror="window.hacked = true">Evil Title'
    evil_abstract = "<script>window.hacked = true</script>" + "<b>bold?</b> " * 30
    work = openalex_work(title=evil_title, abstract_words={evil_abstract: [0]})
    openalex.respond(200, json=openalex_response(work))

    page.goto("/index.html?q=x&source=openalex")

    expect(page.locator(".paper-title")).to_have_text(evil_title)
    expect(page.locator(".paper-card img, .paper-card script, .paper-card b")).to_have_count(0)
    assert page.evaluate("window.hacked") is None


def test_footer_says_when_the_backend_is_unreachable(page):
    page.route("**/api/health", lambda route: route.fulfill(status=503, body="Service Unavailable", content_type="text/plain"))

    page.goto("/")

    expect(page.locator("#health")).to_have_text("Backend unreachable: 503 Service Unavailable")


def test_results_are_shown_even_when_the_library_cannot_be_loaded(page, arxiv, db):
    """The library list only adds the "Saved" marks. Without it, a paper already in the library shows a
    Save button, and saving it gets a 409, which counts as saved."""
    db.add_paper(source="arxiv", external_id="2101.00001")
    arxiv.respond(200, text=arxiv_feed(arxiv_entry("2101.00001")))
    page.route("**/api/papers", lambda route: route.fulfill(status=503, body="Service Unavailable", content_type="text/plain")
               if route.request.method == "GET" else route.continue_())

    search(page, "graph")

    expect(page.locator(".paper-card")).to_have_count(1)
    expect(page.locator("#status")).to_be_hidden()
    page.get_by_role("button", name="Save to library").click()
    expect(page.get_by_role("button", name="Saved")).to_be_disabled()
    expect(page.get_by_role("link", name="Open in library")).to_have_attribute("href", "library.html")
    assert len(db.papers()) == 1


def test_openalex_result_shows_every_field_and_links_to_the_paper(page, openalex):
    """hw1.pdf asks for the title, authors, year, abstract and a link to the paper. An OpenAlex result
    links its title and "View paper" to the DOI, in a new tab, and "PDF" to the open-access copy."""
    openalex.respond(200, json=openalex_response(openalex_work()))

    page.goto("/index.html?q=residual&source=openalex")

    card = page.locator(".paper-card")
    expect(card.locator(".paper-title")).to_have_text("Deep Residual Learning")
    expect(card.locator(".paper-meta")).to_have_text("Kaiming He, Xiangyu Zhang · 2016")
    expect(card.locator(".paper-abstract")).to_have_text("Deeper networks are hard to networks")
    title_link = card.locator(".paper-title a")
    expect(title_link).to_have_attribute("href", "https://doi.org/10.1109/cvpr.2016.90")
    expect(title_link).to_have_attribute("target", "_blank")
    expect(card.get_by_role("link", name="View paper")).to_have_attribute("href", "https://doi.org/10.1109/cvpr.2016.90")
    expect(card.get_by_role("link", name="PDF")).to_have_attribute("href", "https://example.org/resnet.pdf")


def test_source_picker_works_when_local_storage_is_blocked(page, openalex):
    """Browsers that block site data make localStorage throw; the choice just isn't remembered."""
    page.add_init_script(
        "Object.defineProperty(window, 'localStorage', {"
        " get() { throw new DOMException('The operation is insecure.', 'SecurityError'); } });"
    )
    openalex.respond(200, json=openalex_response(openalex_work()))
    page.goto("/")
    assert page.evaluate("() => { try { localStorage; return false; } catch { return true; } }")  # really blocked
    expect(page.locator("#search-source")).to_have_value("auto")

    page.select_option("#search-source", "openalex")  # saving the choice throws, and is skipped
    page.fill("#search-input", "residual")
    page.click("button[type=submit]")

    expect(page.locator("#search-source")).to_have_value("openalex")  # from the URL
    expect(page.locator(".paper-card .paper-title")).to_have_text("Deep Residual Learning")
