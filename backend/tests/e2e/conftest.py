"""Browser tests: the real frontend in a headless browser, against the app served by uvicorn.

The server runs in a background thread of the test process, so the fixtures in tests/conftest.py
still apply: each test has its own empty database, and `http_mock` answers the app's outgoing
requests (arXiv, OpenAlex, PDF downloads, the LLM).

The browser may only reach the test server. The CDN files (Pico.css, marked, KaTeX, DOMPurify) are
blocked, so these tests run offline and exercise the pages' no-CDN fallback (LLM Markdown shown as
plain text); `online_page` lets the CDN through for the one test that checks Markdown rendering.

Browser: Playwright's own Chromium (`playwright install chromium`), else an installed Chrome or Edge.
Without Playwright or a browser, these tests are skipped.
"""

import socket
import threading
import time

import pytest
import uvicorn

from app.main import app

try:
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import sync_playwright
except ImportError:  # requirements-dev.txt not installed
    sync_playwright = None

BROWSERS = ({}, {"channel": "chrome"}, {"channel": "msedge"})


@pytest.fixture(scope="session")
def live_server():
    """Base URL of the app, served by uvicorn on a free local port for the whole test session."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    # lifespan="off": the per-test database fixture creates the tables.
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="off"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        if time.monotonic() > deadline or not thread.is_alive():
            raise RuntimeError("The test server did not start.")
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=10)


@pytest.fixture(scope="session")
def browser():
    if sync_playwright is None:
        pytest.skip("Playwright is not installed: pip install -r requirements-dev.txt")
    with sync_playwright() as playwright:
        for options in BROWSERS:
            try:
                launched = playwright.chromium.launch(**options)
                break
            except PlaywrightError:
                continue
        else:
            pytest.skip("No browser for Playwright: run `playwright install chromium`")
        yield launched
        launched.close()


def _open_page(browser, base_url: str, *, offline: bool):
    context = browser.new_context(base_url=base_url)
    context.set_default_timeout(10_000)
    if offline:
        context.route(lambda url: not url.startswith(base_url), lambda route: route.abort())
    page = context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    return context, page, errors


@pytest.fixture
def page(browser, live_server):
    """A fresh browser tab (no cookies or localStorage) that can only reach the test server.
    Uncaught JavaScript errors on the page fail the test."""
    context, tab, errors = _open_page(browser, live_server, offline=True)
    yield tab
    context.close()
    assert errors == [], f"JavaScript errors on the page: {errors}"


@pytest.fixture
def online_page(browser, live_server):
    """Like `page`, but the CDN scripts may load (needs internet)."""
    context, tab, errors = _open_page(browser, live_server, offline=False)
    yield tab
    context.close()
    assert errors == [], f"JavaScript errors on the page: {errors}"
