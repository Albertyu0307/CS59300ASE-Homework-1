"""Paper search: arXiv first, OpenAlex as the fallback. Both are normalized into PaperResult."""

import re
import threading
import time

import feedparser
import httpx

from app.schemas import PaperResult

ARXIV_API_URL = "https://export.arxiv.org/api/query"
OPENALEX_API_URL = "https://api.openalex.org/works"
TIMEOUT_SECONDS = 15
# arXiv asks clients to make at most one request every 3 seconds.
MIN_SECONDS_BETWEEN_REQUESTS = 3.0
# arXiv blocks an IP for 20+ minutes once it decides it sent too many requests (HTTP 429), so
# repeated searches (page refresh, back button) are answered from memory instead.
CACHE_SECONDS = 60 * 60
CACHE_MAX_ENTRIES = 200
# After arXiv fails, skip it for a while: retrying during a block can extend it, and a blocked
# request sometimes takes 30 s to be refused.
ARXIV_COOLDOWN_SECONDS = 10 * 60

_throttle_lock = threading.Lock()
_last_request_at = 0.0
_arxiv_unavailable_until = 0.0
_cache: dict[tuple[str, int], tuple[float, list[PaperResult]]] = {}  # key -> (stored_at, results)


class SearchError(Exception):
    """The search API could not be reached or returned an error. The message is user-facing."""


def search_papers(query: str, limit: int = 20) -> list[PaperResult]:
    """Search arXiv; if arXiv is unavailable, search OpenAlex instead."""
    try:
        return search_arxiv(query, limit)
    except SearchError as arxiv_error:
        try:
            return search_openalex(query, limit)
        except SearchError as openalex_error:
            raise SearchError(f"{arxiv_error} The fallback source also failed: {openalex_error}") from openalex_error


def search_arxiv(query: str, limit: int = 20) -> list[PaperResult]:
    global _arxiv_unavailable_until
    words = re.findall(r"\w+", query)
    if not words:
        return []
    # AND the words together: "all:graph neural networks" would match papers with ANY of them.
    search_query = " AND ".join(f"all:{word}" for word in words)
    params = {"search_query": search_query, "start": 0, "max_results": limit}

    key = (search_query.lower(), limit)
    cached = _cache.get(key)
    if cached and time.monotonic() - cached[0] < CACHE_SECONDS:
        return cached[1]
    if time.monotonic() < _arxiv_unavailable_until:
        raise SearchError("arXiv failed recently, so it is skipped for a few minutes.")

    _wait_for_turn()
    try:
        response = httpx.get(ARXIV_API_URL, params=params, timeout=TIMEOUT_SECONDS, follow_redirects=True)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        _arxiv_unavailable_until = time.monotonic() + ARXIV_COOLDOWN_SECONDS
        raise SearchError(_describe_http_error("arXiv", exc)) from exc

    feed = feedparser.parse(response.text)
    # arXiv reports bad queries as a single entry whose id points at its errors page.
    if feed.entries and "/api/errors" in feed.entries[0].get("id", ""):
        raise SearchError(f"arXiv rejected the query: {_clean(feed.entries[0].get('summary', ''))}")
    results = [_arxiv_entry_to_result(entry) for entry in feed.entries]

    if len(_cache) >= CACHE_MAX_ENTRIES:
        _cache.clear()
    _cache[key] = (time.monotonic(), results)
    return results


def search_openalex(query: str, limit: int = 20) -> list[PaperResult]:
    """OpenAlex: free, no key, generous limits. Covers arXiv and journals, but not every result has a PDF."""
    if not re.search(r"\w", query):
        return []
    params = {
        "search": query,
        "per-page": limit,
        "select": "id,display_name,publication_year,authorships,abstract_inverted_index,doi,primary_location,best_oa_location",
    }
    try:
        response = httpx.get(OPENALEX_API_URL, params=params, timeout=TIMEOUT_SECONDS, follow_redirects=True)
        response.raise_for_status()
        works = response.json()["results"]
    except httpx.HTTPError as exc:
        raise SearchError(_describe_http_error("OpenAlex", exc)) from exc
    except (ValueError, KeyError) as exc:
        raise SearchError("OpenAlex returned an unexpected response.") from exc
    return [_openalex_work_to_result(work) for work in works]


def _describe_http_error(source: str, exc: httpx.HTTPError) -> str:
    if isinstance(exc, httpx.TimeoutException):
        return f"{source} did not respond in time."
    if isinstance(exc, httpx.HTTPStatusError):
        if exc.response.status_code == 429:
            return f"{source} is temporarily limiting requests from this computer (HTTP 429)."
        return f"{source} returned HTTP {exc.response.status_code}."
    return f"Could not reach {source}. Check your internet connection."


def _wait_for_turn() -> None:
    """Block until at least MIN_SECONDS_BETWEEN_REQUESTS have passed since the previous arXiv request."""
    global _last_request_at
    with _throttle_lock:
        wait = _last_request_at + MIN_SECONDS_BETWEEN_REQUESTS - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last_request_at = time.monotonic()


def _arxiv_entry_to_result(entry) -> PaperResult:
    # entry.id looks like "http://arxiv.org/abs/2101.00001v2"; drop the version so v1/v2 dedupe.
    arxiv_id = re.sub(r"v\d+$", "", entry.id.split("/abs/")[-1])
    pdf_url = next((link.href for link in entry.get("links", []) if link.get("type") == "application/pdf"), None)
    published = entry.get("published", "")
    return PaperResult(
        source="arxiv",
        external_id=arxiv_id,
        title=_clean(entry.get("title", "")) or "(untitled)",
        authors=[author.name for author in entry.get("authors", []) if author.get("name")],
        year=int(published[:4]) if published[:4].isdigit() else None,
        abstract=_clean(entry.get("summary", "")) or None,
        url=_https(entry.get("link")),
        pdf_url=_https(pdf_url),
    )


def _openalex_work_to_result(work: dict) -> PaperResult:
    primary = work.get("primary_location") or {}
    best_oa = work.get("best_oa_location") or {}
    return PaperResult(
        source="openalex",
        external_id=work["id"].rsplit("/", 1)[-1],  # "https://openalex.org/W1837145606" -> "W1837145606"
        title=_clean(work.get("display_name") or "") or "(untitled)",
        authors=[a["author"]["display_name"] for a in work.get("authorships", []) if a.get("author", {}).get("display_name")],
        year=work.get("publication_year"),
        abstract=_rebuild_abstract(work.get("abstract_inverted_index")),
        url=work.get("doi") or primary.get("landing_page_url") or work["id"],
        pdf_url=best_oa.get("pdf_url") or primary.get("pdf_url"),
    )


def _rebuild_abstract(inverted_index: dict[str, list[int]] | None) -> str | None:
    """OpenAlex stores abstracts as {word: [positions]}; put the words back in order."""
    if not inverted_index:
        return None
    positions = {pos: word for word, places in inverted_index.items() for pos in places}
    return _clean(" ".join(positions[pos] for pos in sorted(positions))) or None


def _clean(text: str) -> str:
    """Collapse the line breaks and runs of spaces arXiv puts inside titles and abstracts."""
    return " ".join(text.split())


def _https(url: str | None) -> str | None:
    return url.replace("http://", "https://", 1) if url and url.startswith("http://") else url
