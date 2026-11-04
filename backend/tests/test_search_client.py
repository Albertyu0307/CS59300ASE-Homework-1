"""services/search_client.py: result normalization, the arXiv cache, cooldown and throttle,
OpenAlex parsing, and arXiv abs-page lookups for uploaded PDFs."""

import httpx
import pytest

from app.services import search_client
from app.services.search_client import SearchError, fetch_arxiv_paper, search_arxiv, search_openalex, search_papers
from tests.factories import ARXIV_ERROR_FEED, arxiv_abs_page, arxiv_entry, arxiv_feed, openalex_response, openalex_work


class FakeClock:
    """Stands in for the time module: monotonic() is under the test's control; sleep() advances it."""

    def __init__(self, now: float = 10_000.0):
        self.now = now
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture
def clock(monkeypatch):
    fake = FakeClock()
    monkeypatch.setattr(search_client, "time", fake)
    return fake


@pytest.fixture
def arxiv(http_mock):
    return http_mock.get(host="export.arxiv.org", path="/api/query")


@pytest.fixture
def openalex(http_mock):
    return http_mock.get(host="api.openalex.org", path="/works")


# --- arXiv results ------------------------------------------------------------------------------


def test_arxiv_entry_is_normalized(arxiv):
    arxiv.respond(200, text=arxiv_feed(arxiv_entry("2101.00001", version=3)))

    [paper] = search_arxiv("graph")

    assert paper.source == "arxiv"
    assert paper.external_id == "2101.00001"  # version suffix dropped, so v1 and v3 are the same paper
    assert paper.title == "Graph Neural Networks: A Review"  # arXiv's line breaks collapsed
    assert paper.abstract == "We review graph neural networks and their applications."
    assert paper.authors == ["Alice Smith", "Bob Jones"]
    assert paper.year == 2021
    assert paper.url == "https://arxiv.org/abs/2101.00001v3"  # http:// upgraded
    assert paper.pdf_url == "https://arxiv.org/pdf/2101.00001v3"


def test_old_style_arxiv_ids_keep_their_archive_prefix(arxiv):
    arxiv.respond(200, text=arxiv_feed(arxiv_entry("hep-th/9901001", version=1)))

    [paper] = search_arxiv("strings")

    assert paper.external_id == "hep-th/9901001"


def test_arxiv_entry_with_missing_fields(arxiv):
    entry = arxiv_entry(title="  ", summary="", authors=(), published=None, pdf=False)
    arxiv.respond(200, text=arxiv_feed(entry))

    [paper] = search_arxiv("anything")

    assert paper.title == "(untitled)"
    assert paper.abstract is None
    assert paper.authors == []
    assert paper.year is None
    assert paper.pdf_url is None


def test_arxiv_query_error_is_reported(arxiv):
    arxiv.respond(200, text=ARXIV_ERROR_FEED)

    with pytest.raises(SearchError, match="arXiv rejected the query: incorrect id format for x"):
        search_arxiv("graph")


@pytest.mark.parametrize(
    "failure, message",
    [
        (httpx.Response(429), "arXiv is temporarily limiting requests from this computer (HTTP 429)."),
        (httpx.Response(500), "arXiv returned HTTP 500."),
        (httpx.ConnectTimeout("slow"), "arXiv did not respond in time."),
        (httpx.ConnectError("offline"), "Could not reach arXiv. Check your internet connection."),
    ],
)
def test_arxiv_failures_have_user_facing_messages(arxiv, failure, message):
    arxiv.mock(side_effect=[failure])

    with pytest.raises(SearchError) as error:
        search_arxiv("graph")

    assert str(error.value) == message


# --- arXiv cache, cooldown and throttle ---------------------------------------------------------


def test_same_search_is_cached(arxiv, clock):
    arxiv.respond(200, text=arxiv_feed(arxiv_entry()))

    first = search_arxiv("graph networks")
    again = search_arxiv("Graph, networks!")  # same words, other case and punctuation

    assert again == first
    assert arxiv.call_count == 1


def test_a_different_limit_is_a_different_search(arxiv, clock):
    arxiv.respond(200, text=arxiv_feed(arxiv_entry()))

    search_arxiv("graph", limit=10)
    search_arxiv("graph", limit=20)

    assert arxiv.call_count == 2


def test_cached_results_expire(arxiv, clock):
    arxiv.respond(200, text=arxiv_feed(arxiv_entry()))
    search_arxiv("graph")

    clock.now += search_client.CACHE_SECONDS + 1
    search_arxiv("graph")

    assert arxiv.call_count == 2


def test_cache_is_emptied_when_full(arxiv, clock, monkeypatch):
    monkeypatch.setattr(search_client, "CACHE_MAX_ENTRIES", 2)
    arxiv.respond(200, text=arxiv_feed(arxiv_entry()))

    for query in ("one", "two", "three", "one"):  # "three" finds the cache full and clears it
        search_arxiv(query)

    assert arxiv.call_count == 4


def test_arxiv_is_skipped_for_a_while_after_a_failure(arxiv, clock):
    arxiv.mock(side_effect=[httpx.Response(429), httpx.Response(200, text=arxiv_feed(arxiv_entry()))])
    with pytest.raises(SearchError):
        search_arxiv("graph")

    with pytest.raises(SearchError, match="skipped for a few minutes"):
        search_arxiv("graph")
    assert arxiv.call_count == 1  # not contacted during the cooldown

    clock.now += search_client.ARXIV_COOLDOWN_SECONDS + 1
    assert len(search_arxiv("graph")) == 1
    assert arxiv.call_count == 2


def test_cached_results_are_still_served_during_the_cooldown(arxiv, clock):
    arxiv.mock(side_effect=[httpx.Response(200, text=arxiv_feed(arxiv_entry())), httpx.Response(503)])
    cached = search_arxiv("graph")
    with pytest.raises(SearchError):
        search_arxiv("other words")

    assert search_arxiv("graph") == cached


def test_failed_searches_are_not_cached(arxiv, clock):
    arxiv.mock(side_effect=[httpx.Response(500), httpx.Response(200, text=arxiv_feed(arxiv_entry()))])
    with pytest.raises(SearchError):
        search_arxiv("graph")
    clock.now += search_client.ARXIV_COOLDOWN_SECONDS + 1

    assert len(search_arxiv("graph")) == 1


def test_requests_to_arxiv_are_spaced_out(arxiv, clock, monkeypatch):
    monkeypatch.setattr(search_client, "MIN_SECONDS_BETWEEN_REQUESTS", 3.0)
    arxiv.respond(200, text=arxiv_feed())

    search_arxiv("first")
    clock.now += 1.0
    search_arxiv("second")  # 1 s after the first: waits the remaining 2 s
    clock.now += 5.0
    search_arxiv("third")  # long enough after: no wait

    assert clock.sleeps == [2.0]
    assert arxiv.call_count == 3


def test_auto_search_during_the_cooldown_goes_straight_to_openalex(arxiv, openalex, clock):
    arxiv.respond(429)
    openalex.respond(200, json=openalex_response(openalex_work()))
    search_papers("graph")

    results = search_papers("other words")

    assert arxiv.call_count == 1
    assert [paper.source for paper in results] == ["openalex"]


# --- OpenAlex -----------------------------------------------------------------------------------


def test_openalex_work_is_normalized(openalex):
    openalex.respond(200, json=openalex_response(openalex_work()))

    [paper] = search_openalex("residual")

    assert paper.source == "openalex"
    assert paper.external_id == "W1837145606"
    assert paper.title == "Deep Residual Learning"
    assert paper.authors == ["Kaiming He", "Xiangyu Zhang"]
    assert paper.year == 2016
    assert paper.abstract == "Deeper networks are hard to networks"  # rebuilt from the inverted index
    assert paper.url == "https://doi.org/10.1109/cvpr.2016.90"
    assert paper.pdf_url == "https://example.org/resnet.pdf"


def test_openalex_requests_only_the_needed_fields(openalex):
    openalex.respond(200, json=openalex_response())

    search_openalex("graph", limit=3)

    params = openalex.calls.last.request.url.params
    assert params["per-page"] == "3"
    assert set(params["select"].split(",")) >= {"id", "display_name", "abstract_inverted_index", "best_oa_location"}


@pytest.mark.parametrize(
    "fields, expected_url",
    [
        ({}, "https://doi.org/10.1109/cvpr.2016.90"),
        ({"doi": None}, "https://example.org/resnet"),
        ({"doi": None, "landing_page_url": None}, "https://openalex.org/W1837145606"),
    ],
    ids=["doi", "landing-page", "openalex-page"],
)
def test_openalex_landing_page_preference(openalex, fields, expected_url):
    openalex.respond(200, json=openalex_response(openalex_work(**fields)))

    assert search_openalex("x")[0].url == expected_url


@pytest.mark.parametrize(
    "fields, expected_pdf",
    [
        ({"primary_pdf_url": "https://example.org/primary.pdf"}, "https://example.org/resnet.pdf"),
        ({"oa_pdf_url": None, "primary_pdf_url": "https://example.org/primary.pdf"}, "https://example.org/primary.pdf"),
        ({"oa_pdf_url": None}, None),
    ],
    ids=["open-access-copy-preferred", "primary-location-fallback", "no-pdf"],
)
def test_openalex_pdf_link_preference(openalex, fields, expected_pdf):
    openalex.respond(200, json=openalex_response(openalex_work(**fields)))

    assert search_openalex("x")[0].pdf_url == expected_pdf


def test_openalex_work_with_missing_fields(openalex):
    work = openalex_work(title=None, year=None, authors=(), abstract_words={})
    work["authorships"] = [{"author": {}}, {"author": {"display_name": "Real Name"}}]
    work["primary_location"] = None
    openalex.respond(200, json=openalex_response(work))

    [paper] = search_openalex("x")

    assert paper.title == "(untitled)"
    assert paper.year is None
    assert paper.abstract is None
    assert paper.authors == ["Real Name"]


@pytest.mark.parametrize(
    "response",
    [httpx.Response(200, text="<html>maintenance</html>"), httpx.Response(200, json={"error": "nope"})],
    ids=["not-json", "no-results-key"],
)
def test_unexpected_openalex_response_is_an_error(openalex, response):
    openalex.mock(side_effect=[response])

    with pytest.raises(SearchError, match="OpenAlex returned an unexpected response."):
        search_openalex("graph")


def test_openalex_rate_limit_is_reported(openalex):
    openalex.respond(429)

    with pytest.raises(SearchError, match=r"OpenAlex is temporarily limiting requests from this computer \(HTTP 429\)"):
        search_openalex("graph")


def test_auto_search_reports_both_failures(arxiv, openalex):
    arxiv.respond(500)
    openalex.respond(502)

    with pytest.raises(SearchError) as error:
        search_papers("graph")

    assert str(error.value) == "arXiv returned HTTP 500. The fallback source also failed: OpenAlex returned HTTP 502."


# --- arXiv abs pages (uploaded PDFs that carry an arXiv ID) --------------------------------------


@pytest.fixture
def abs_page(http_mock):
    return http_mock.get("https://arxiv.org/abs/2103.01234")


def test_fetch_arxiv_paper_reads_the_citation_tags(abs_page):
    abs_page.respond(200, text=arxiv_abs_page(title="Attention & Memory", authors=("Smith, Alice", "Plato")))

    paper = fetch_arxiv_paper("2103.01234")

    assert paper.source == "arxiv"
    assert paper.external_id == "2103.01234"
    assert paper.title == "Attention & Memory"
    assert paper.authors == ["Alice Smith", "Plato"]  # "Last, First" turned around; single names kept
    assert paper.year == 2021
    assert paper.abstract == "We study sparse attention."
    assert paper.url == "https://arxiv.org/abs/2103.01234"
    assert paper.pdf_url == "https://arxiv.org/pdf/2103.01234"


def test_fetch_arxiv_paper_with_missing_optional_tags(abs_page):
    abs_page.respond(200, text=arxiv_abs_page(date=None, pdf_url=None, abstract=" "))

    paper = fetch_arxiv_paper("2103.01234")

    assert paper.year is None
    assert paper.pdf_url is None
    assert paper.abstract is None


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(404, text="not found"),
        httpx.Response(200, text="<html><head><title>arXiv</title></head></html>"),
        httpx.ConnectError("offline"),
    ],
    ids=["not-found", "no-title-tag", "unreachable"],
)
def test_fetch_arxiv_paper_returns_none_when_unavailable(abs_page, response):
    abs_page.mock(side_effect=[response])

    assert fetch_arxiv_paper("2103.01234") is None
