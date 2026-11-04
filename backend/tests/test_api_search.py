"""GET /api/search: arXiv first, OpenAlex as the fallback, parameter validation, error responses."""

import httpx
import pytest

from tests.factories import ARXIV_ERROR_FEED, arxiv_entry, arxiv_feed, openalex_response, openalex_work


@pytest.fixture
def arxiv(http_mock):
    return http_mock.get(host="export.arxiv.org", path="/api/query")


@pytest.fixture
def openalex(http_mock):
    return http_mock.get(host="api.openalex.org", path="/works")


def test_search_returns_arxiv_results_in_the_common_format(client, arxiv):
    arxiv.respond(200, text=arxiv_feed(arxiv_entry("2101.00001"), arxiv_entry("2102.00002", title="Second")))

    response = client.get("/api/search", params={"q": "graph neural networks"})

    assert response.status_code == 200
    first, second = response.json()
    assert first == {
        "source": "arxiv",
        "external_id": "2101.00001",
        "title": "Graph Neural Networks: A Review",
        "authors": ["Alice Smith", "Bob Jones"],
        "year": 2021,
        "abstract": "We review graph neural networks and their applications.",
        "url": "https://arxiv.org/abs/2101.00001v2",
        "pdf_url": "https://arxiv.org/pdf/2101.00001v2",
    }
    assert second["external_id"] == "2102.00002"


def test_query_words_are_combined_with_and(client, arxiv):
    """A plain "all:graph neural networks" would match papers containing ANY of the words."""
    arxiv.respond(200, text=arxiv_feed())

    client.get("/api/search", params={"q": "graph  neural-networks!", "limit": 5})

    params = arxiv.calls.last.request.url.params
    assert params["search_query"] == "all:graph AND all:neural AND all:networks"
    assert params["max_results"] == "5"
    assert params["start"] == "0"


def test_default_limit_is_20(client, arxiv):
    arxiv.respond(200, text=arxiv_feed())

    client.get("/api/search", params={"q": "transformers"})

    assert arxiv.calls.last.request.url.params["max_results"] == "20"


def test_openalex_source_never_contacts_arxiv(client, arxiv, openalex):
    arxiv.respond(200, text=arxiv_feed(arxiv_entry()))
    openalex.respond(200, json=openalex_response(openalex_work()))

    response = client.get("/api/search", params={"q": "residual learning", "source": "openalex", "limit": 7})

    assert response.status_code == 200
    assert [paper["source"] for paper in response.json()] == ["openalex"]
    assert not arxiv.called
    assert openalex.calls.last.request.url.params["search"] == "residual learning"
    assert openalex.calls.last.request.url.params["per-page"] == "7"


@pytest.mark.parametrize(
    "arxiv_failure",
    [
        httpx.Response(429, text="Rate exceeded."),
        httpx.Response(503),
        httpx.Response(200, text=ARXIV_ERROR_FEED),
        httpx.ConnectTimeout("timed out"),
        httpx.ConnectError("no route to host"),
    ],
    ids=["rate-limited", "server-error", "query-rejected", "timeout", "unreachable"],
)
def test_auto_falls_back_to_openalex_when_arxiv_fails(client, arxiv, openalex, arxiv_failure):
    arxiv.mock(side_effect=[arxiv_failure])
    openalex.respond(200, json=openalex_response(openalex_work()))

    response = client.get("/api/search", params={"q": "residual learning"})

    assert response.status_code == 200
    assert [paper["source"] for paper in response.json()] == ["openalex"]


def test_both_sources_failing_returns_502_with_both_reasons(client, arxiv, openalex):
    arxiv.respond(503)
    openalex.mock(side_effect=httpx.ConnectError("offline"))

    response = client.get("/api/search", params={"q": "graph"})

    assert response.status_code == 502
    assert response.json()["detail"] == (
        "arXiv returned HTTP 503. The fallback source also failed: "
        "Could not reach OpenAlex. Check your internet connection."
    )


def test_openalex_only_failure_returns_502(client, openalex):
    openalex.mock(side_effect=httpx.ReadTimeout("slow"))

    response = client.get("/api/search", params={"q": "graph", "source": "openalex"})

    assert response.status_code == 502
    assert response.json()["detail"] == "OpenAlex did not respond in time."


def test_no_matches_returns_an_empty_list(client, arxiv):
    arxiv.respond(200, text=arxiv_feed())

    response = client.get("/api/search", params={"q": "xyzzy plugh"})

    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.parametrize("source", ["auto", "openalex"])
def test_query_without_any_words_returns_nothing_without_calling_an_api(client, arxiv, openalex, source):
    response = client.get("/api/search", params={"q": "?!  ...", "source": source})

    assert response.status_code == 200
    assert response.json() == []
    assert not arxiv.called and not openalex.called


def test_repeated_search_is_answered_from_the_cache(client, arxiv):
    arxiv.respond(200, text=arxiv_feed(arxiv_entry()))

    first = client.get("/api/search", params={"q": "graph networks"}).json()
    second = client.get("/api/search", params={"q": "Graph Networks"}).json()

    assert first == second
    assert arxiv.call_count == 1


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"q": ""},
        {"q": "x" * 301},
        {"q": "graph", "limit": 0},
        {"q": "graph", "limit": 51},
        {"q": "graph", "limit": "ten"},
        {"q": "graph", "source": "google-scholar"},
    ],
    ids=["missing-q", "empty-q", "q-too-long", "limit-0", "limit-51", "limit-not-a-number", "unknown-source"],
)
def test_invalid_parameters_are_rejected_with_422(client, arxiv, params):
    response = client.get("/api/search", params=params)

    assert response.status_code == 422
    assert not arxiv.called


@pytest.mark.parametrize("params", [{"q": "x" * 300}, {"q": "graph", "limit": 1}, {"q": "graph", "limit": 50}])
def test_boundary_parameters_are_accepted(client, arxiv, params):
    arxiv.respond(200, text=arxiv_feed())

    assert client.get("/api/search", params=params).status_code == 200
