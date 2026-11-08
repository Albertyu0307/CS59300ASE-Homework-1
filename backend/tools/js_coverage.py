"""pytest plugin: line and function coverage of the frontend's JavaScript (frontend/js/*.js) during the
browser tests in tests/e2e/.

    python -m pytest -p tools.js_coverage --js-coverage=coverage-report/js-v8.json

tools/run_coverage.py loads it for you. Chromium's JavaScript engine (V8) can count how often each
function and block of a script runs ("precise coverage" in the DevTools protocol). The plugin turns
that on in every page the tests open and, when the page's browser context is closed, saves the counts
for the app's own scripts: those served under /js/ by the local test server, so the CDN libraries are
left out. summarize() then turns the saved counts into line and function coverage per file.

It hooks into Playwright itself rather than into the test fixtures, so it doesn't depend on how the
tests open their pages. Chromium-based browsers only (Playwright's Chromium, Chrome, Edge).
"""

import json
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlsplit

# Without these, Chromium may give a tab's next document a fresh renderer process (RenderDocument, the
# back/forward cache). The counts of a page that a click or a form submit navigated away from would
# then be lost before the context closes.
BROWSER_ARGS = ["--disable-features=BackForwardCache,RenderDocument", "--process-per-site"]
LOCAL_HOSTS = {"127.0.0.1", "localhost"}


def pytest_addoption(parser):
    parser.addoption(
        "--js-coverage", metavar="PATH",
        help="save the V8 coverage of frontend/js/*.js during the browser tests to PATH (JSON)",
    )


def pytest_configure(config):
    path = config.getoption("js_coverage")
    if path:
        config.pluginmanager.register(_Collector(Path(path)), "js-coverage-collector")


class _Collector:
    def __init__(self, path: Path):
        self.path = path
        self.browser = None
        self.scripts: list[dict] = []
        self._sessions: dict[int, list] = {}  # id(browser context) -> its pages' DevTools sessions
        self._originals = None

    # --- pytest hooks ----------------------------------------------------------------------------

    def pytest_sessionstart(self, session):
        try:
            from playwright.sync_api import BrowserContext, BrowserType
        except ImportError:  # Playwright not installed: the browser tests are skipped anyway
            return
        collector = self
        real_launch, real_new_page, real_close = BrowserType.launch, BrowserContext.new_page, BrowserContext.close
        self._originals = (BrowserType, BrowserContext, real_launch, real_new_page, real_close)

        def launch(self, *args, **kwargs):
            kwargs["args"] = [*(kwargs.get("args") or []), *BROWSER_ARGS]
            browser = real_launch(self, *args, **kwargs)
            collector.browser = f"{kwargs.get('channel') or self.name} {browser.version}"  # e.g. "chrome 142.0..."
            return browser

        def new_page(self, *args, **kwargs):
            page = real_new_page(self, *args, **kwargs)
            try:
                cdp = self.new_cdp_session(page)
                cdp.send("Profiler.enable")
                cdp.send("Profiler.startPreciseCoverage", {"callCount": True, "detailed": True})
            except Exception:  # not a Chromium-based browser: no coverage for this page
                return page
            collector._sessions.setdefault(id(self), []).append(cdp)
            return page

        def close(self, *args, **kwargs):
            for cdp in collector._sessions.pop(id(self), []):
                collector._take(cdp)
            return real_close(self, *args, **kwargs)

        BrowserType.launch, BrowserContext.new_page, BrowserContext.close = launch, new_page, close

    def pytest_sessionfinish(self, session):
        if self._originals:
            browser_type, browser_context, launch, new_page, close = self._originals
            browser_type.launch, browser_context.new_page, browser_context.close = launch, new_page, close
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"browser": self.browser, "scripts": self.scripts}), encoding="utf-8")

    # ---------------------------------------------------------------------------------------------

    def _take(self, cdp) -> None:
        try:
            result = cdp.send("Profiler.takePreciseCoverage")["result"]
        except Exception:  # the page crashed or was already closed
            return
        for script in result:
            url = urlsplit(script["url"])
            if url.hostname in LOCAL_HOSTS and url.path.startswith("/js/"):
                self.scripts.append({"path": url.path, "functions": script["functions"]})


# --- From the saved counts to line and function coverage -------------------------------------------


def summarize(raw_file: Path, js_dir: Path) -> dict:
    """Line and function coverage of each .js file in js_dir, from the counts the plugin saved.

    A line counts if it holds code (not blank, not only a comment); it is covered if any of its code
    ran. A function is covered if it was called at least once. A file that no test loaded has all its
    lines uncovered and no function data.
    """
    raw = json.loads(raw_file.read_text(encoding="utf-8"))
    runs = defaultdict(list)  # file name -> the function lists of every page that loaded it
    for script in raw["scripts"]:
        runs[script["path"].rsplit("/", 1)[-1]].append(script["functions"])

    files = {}
    for path in sorted(js_dir.glob("*.js")):
        # Bytes, so "\r\n" stays two characters: V8's offsets count the file as served.
        files[f"frontend/js/{path.name}"] = _file_coverage(path.read_bytes().decode("utf-8"), runs.get(path.name, []))
    totals = {
        kind: {"covered": sum(f[kind]["covered"] for f in files.values()), "total": sum(f[kind]["total"] for f in files.values())}
        for kind in ("lines", "functions")
    }
    return {"browser": raw.get("browser"), **totals, "files": files}


def _file_coverage(source: str, runs: list[list[dict]]) -> dict:
    # V8 gives offsets in UTF-16 code units; map them to indexes into the Python string.
    index_of = [i for i, char in enumerate(source) for _ in range(2 if ord(char) > 0xFFFF else 1)] + [len(source)]
    counts = [0] * len(source)
    calls: dict[int, int] = defaultdict(int)  # function start -> times called
    names: dict[int, str] = {}

    for functions in runs:
        run = [0] * len(source)
        ranges = []
        for function in functions:
            first = function["ranges"][0]  # the whole function; the others are blocks inside it
            start = index_of[min(first["startOffset"], len(index_of) - 1)]
            if not (first["startOffset"] == 0 and index_of[min(first["endOffset"], len(index_of) - 1)] >= len(source) - 1):
                calls[start] += first["count"]  # not the script's top level
                names[start] = function["functionName"] or f"(anonymous, line {source.count(chr(10), 0, start) + 1})"
            ranges += function["ranges"]
        # A nested range overrides the one around it, so apply the outer ones first.
        for r in sorted(ranges, key=lambda r: (r["startOffset"], -r["endOffset"])):
            a = index_of[min(r["startOffset"], len(index_of) - 1)]
            b = index_of[min(r["endOffset"], len(index_of) - 1)]
            run[a:b] = [r["count"]] * (b - a)
        counts = [total + count for total, count in zip(counts, run)]

    lines_total, missing_lines, position = 0, [], 0
    for number, text in enumerate(source.split("\n"), 1):
        code = [position + i for i, char in enumerate(text) if not char.isspace()]
        position += len(text) + 1
        if not code or text.lstrip().startswith(("//", "/*", "*")):
            continue
        lines_total += 1
        if not any(counts[i] > 0 for i in code):
            missing_lines.append(number)

    return {
        "lines": {"covered": lines_total - len(missing_lines), "total": lines_total},
        "functions": {"covered": sum(1 for n in calls.values() if n > 0), "total": len(calls)},
        "missing_lines": missing_lines,
        "missing_functions": sorted((names[start] for start, n in calls.items() if n == 0), key=str.lower),
    }
