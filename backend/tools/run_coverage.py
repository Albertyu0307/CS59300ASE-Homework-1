"""Measure the code coverage of the test suite.

From backend/, with the virtual environment activated (see README.md, "Measuring code coverage"):

    python tools/run_coverage.py                     the code and tests of this checkout
    python tools/run_coverage.py --compare <commit>  also another commit's code and tests (in a temporary
                                                     git worktree), and print the two side by side

- Backend: statement and branch coverage of app/ with coverage.py (settings in .coveragerc), over the
  whole suite. The browser tests count too, because their server runs inside the test process.
- Frontend: line and function coverage of frontend/js/*.js during the browser tests (js_coverage.py).

The results are saved in coverage-report/, which each run empties first: summary.json (the numbers
printed at the end), html/index.html (coverage.py's report, line by line), backend.json (coverage.py's
JSON report), js-v8.json (the raw V8 counts) and junit.xml (the test results). With --compare, the
other commit's results go to coverage-report/<commit>/.
"""

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ElementTree
from datetime import datetime, timezone
from pathlib import Path

import coverage

from js_coverage import summarize

TOOLS = Path(__file__).resolve().parent
BACKEND = TOOLS.parent
REPO = BACKEND.parent
RCFILE = BACKEND / ".coveragerc"
REPORT_DIR = BACKEND / "coverage-report"
# What a measurement depends on: the code under test and the tests. Changes elsewhere (e.g. to this
# script or to the README) don't make the checkout's numbers differ from its commit's.
MEASURED_PATHS = ["backend/app", "backend/tests", "backend/pytest.ini", "frontend"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter,
                                     epilog="\n\n".join(__doc__.split("\n\n")[1:]))
    parser.add_argument("--compare", metavar="COMMIT",
                        help="also measure COMMIT's code and tests, and print them next to this checkout's")
    args = parser.parse_args()

    shutil.rmtree(REPORT_DIR, ignore_errors=True)  # no results of an earlier run are left behind
    other, other_exit = measure_commit(args.compare) if args.compare else (None, 0)
    checkout, checkout_exit = measure(BACKEND, REPORT_DIR, checkout_label())
    if other:
        print_comparison(other, checkout)
    return checkout_exit or other_exit


# --- Measuring -------------------------------------------------------------------------------------


def measure(backend_dir: Path, out: Path, label: str) -> tuple[dict, int]:
    """Run the whole suite in backend_dir under coverage. Returns the summary and pytest's exit code."""
    print(f"\n=== Coverage of {label} ===\n", flush=True)
    out.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, COVERAGE_FILE=str(out / ".coverage"),
               PYTHONPATH=os.pathsep.join(filter(None, [str(TOOLS), os.environ.get("PYTHONPATH")])))
    tests = subprocess.run(
        [sys.executable, "-m", "coverage", "run", f"--rcfile={RCFILE}", "-m", "pytest", "-q", "-p", "no:cacheprovider",
         "-p", "js_coverage", f"--js-coverage={out / 'js-v8.json'}", f"--junitxml={out / 'junit.xml'}"],
        cwd=backend_dir, env=env,
    )
    if tests.returncode not in (0, 1):  # 1: some tests failed, which still gives coverage numbers
        sys.exit(f"pytest stopped with exit code {tests.returncode}; no coverage was measured.")

    def run_coverage(*args: str) -> None:
        subprocess.run([sys.executable, "-m", "coverage", *args, f"--rcfile={RCFILE}"], cwd=backend_dir, env=env, check=True)

    run_coverage("json", "-q", "-o", str(out / "backend.json"))
    run_coverage("html", "-q", "-d", str(out / "html"))
    print("\nBackend (coverage.py, statements and branches of backend/app/):\n", flush=True)
    run_coverage("report")

    frontend = summarize(out / "js-v8.json", backend_dir.parent / "frontend" / "js")
    if not any(f["functions"]["total"] for f in frontend["files"].values()):
        frontend = None  # no browser test ran (no browser found, or -m "not e2e")
    summary = {
        "label": label,
        "measured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "environment": {
            "python": platform.python_version(),
            "coverage.py": coverage.__version__,
            "os": platform.platform(),
            "browser": frontend["browser"] if frontend else None,
        },
        "tests": test_counts(out / "junit.xml"),
        "backend": backend_summary(json.loads((out / "backend.json").read_text(encoding="utf-8"))),
        "frontend": frontend,
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print_summary(summary)
    print(f"\nSaved in {out}: summary.json, html/index.html (line by line), backend.json, js-v8.json, junit.xml")
    return summary, tests.returncode


def measure_commit(commit: str) -> tuple[dict, int]:
    """measure() on another commit, checked out in a temporary git worktree (the checkout is untouched)."""
    sha = git("rev-parse", "--verify", "--short", f"{commit}^{{commit}}")
    parent = Path(tempfile.mkdtemp(prefix="coverage-worktree-"))
    worktree = parent / "repo"
    git("worktree", "add", "--detach", "--quiet", str(worktree), sha)
    try:
        return measure(worktree / "backend", REPORT_DIR / sha, sha)
    finally:
        git("worktree", "remove", "--force", str(worktree))
        shutil.rmtree(parent, ignore_errors=True)


def checkout_label() -> str:
    sha = git("rev-parse", "--short", "HEAD")
    changed = git("status", "--porcelain", "--", *MEASURED_PATHS)
    return f"{sha} + uncommitted changes" if changed else sha


def git(*args: str) -> str:
    return subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, text=True, check=True).stdout.strip()


def test_counts(junit_xml: Path) -> dict:
    counts = dict.fromkeys(("passed", "failed", "xfailed", "skipped"), 0)
    for case in ElementTree.parse(junit_xml).getroot().iter("testcase"):
        results = {child.tag: child for child in case}
        if "failure" in results or "error" in results:
            counts["failed"] += 1
        elif "skipped" in results:
            counts["xfailed" if results["skipped"].get("type") == "pytest.xfail" else "skipped"] += 1
        else:
            counts["passed"] += 1
    return {"total": sum(counts.values()), **counts}


def backend_summary(report: dict) -> dict:
    """The parts of coverage.py's JSON report that the summary uses."""
    files = {}
    for name, data in sorted(report["files"].items()):
        numbers = data["summary"]
        if numbers["num_statements"]:  # skip empty __init__.py files
            files["backend/" + name.replace("\\", "/")] = {
                "statements": {"covered": numbers["covered_lines"], "total": numbers["num_statements"]},
                "branches": {"covered": numbers["covered_branches"], "total": numbers["num_branches"]},
                "missing_lines": data["missing_lines"],
                "missing_branches": data.get("missing_branches", []),
            }
    totals = report["totals"]
    return {
        "statements": {"covered": totals["covered_lines"], "total": totals["num_statements"]},
        "branches": {"covered": totals["covered_branches"], "total": totals["num_branches"]},
        "files": files,
    }


# --- Showing the numbers ---------------------------------------------------------------------------


def percent(part: dict) -> str:
    """Like coverage.py: one decimal, and never "100.0" unless everything is covered."""
    covered, total = part["covered"], part["total"]
    if not total:
        return "--"
    text = f"{100 * covered / total:.1f}"
    return "99.9" if text == "100.0" and covered < total else text


def fraction(part: dict | None) -> str:
    return f"{part['covered']}/{part['total']} ({percent(part)}%)" if part else "n/a"


def combined(backend: dict) -> dict:
    """coverage.py's total: statements and branches together."""
    return {kind: backend["statements"][kind] + backend["branches"][kind] for kind in ("covered", "total")}


def totals(summary: dict) -> list[tuple[str, str]]:
    """(name, value) for each total, in the order they are printed."""
    tests, backend, frontend = summary["tests"], summary["backend"], summary["frontend"]
    return [
        ("Test cases", str(tests["total"])),
        ("  passed", str(tests["passed"])),
        ("  xfail (known bugs)", str(tests["xfailed"])),
        ("  failed", str(tests["failed"])),
        ("  skipped", str(tests["skipped"])),
        ("Backend statements", fraction(backend["statements"])),
        ("Backend branches", fraction(backend["branches"])),
        ("Backend statements + branches", fraction(combined(backend))),
        ("Frontend JS lines", fraction(frontend["lines"] if frontend else None)),
        ("Frontend JS functions", fraction(frontend["functions"] if frontend else None)),
    ]


def print_summary(summary: dict) -> None:
    frontend = summary["frontend"]
    if frontend:
        print(f"\nFrontend (line and function coverage of frontend/js/ during the browser tests, {frontend['browser']}):\n")
        print(f"{'File':26} {'Lines':>6} {'Miss':>5} {'Cover':>7} {'Functions':>10}   Not run")
        for name, f in frontend["files"].items():
            lines, functions = f["lines"], f["functions"]
            not_run = ", ".join([*(f"line {n}" for n in f["missing_lines"]), *f["missing_functions"]])
            print(f"{name:26} {lines['total']:6} {lines['total'] - lines['covered']:5} {percent(lines):>6}% "
                  f"{functions['covered']:>4}/{functions['total']:<5}   {not_run}")
        print(f"{'TOTAL':26} {frontend['lines']['total']:6} {frontend['lines']['total'] - frontend['lines']['covered']:5} "
              f"{percent(frontend['lines']):>6}% {frontend['functions']['covered']:>4}/{frontend['functions']['total']:<5}")
    else:
        print("\nFrontend: not measured, because no browser test ran (see README.md, \"Running the tests\").")

    print(f"\nSummary of {summary['label']}:")
    for name, value in totals(summary):
        print(f"  {name:31} {value}")


def print_comparison(other: dict, checkout: dict) -> None:
    """The totals and the per-file percentages of two measurements, side by side."""
    first, second = other["label"], "checkout"
    print(f"\n=== Comparison of {first} with this checkout ({checkout['label']}) ===\n")
    print(f"{'':31} {first:>18} {second:>18}")
    for (name, value), (_, checkout_value) in zip(totals(other), totals(checkout)):
        print(f"{name:31} {value:>18} {checkout_value:>18}")

    def files(section: str, kinds: tuple[str, str]) -> None:
        old = (other[section] or {}).get("files", {})
        new = (checkout[section] or {}).get("files", {})
        cell = lambda files, name, kind: percent(files[name][kind]) if name in files else "--"  # noqa: E731
        names = sorted(set(old) | set(new))
        width = max([len(name) for name in names] + [30])
        print(f"\n{section.capitalize() + ' file, in percent':{width}} {kinds[0].capitalize():^19} {kinds[1].capitalize():^19}")
        print(f"{'':{width}} {first:>9} {second:>9} {first:>9} {second:>9}")
        for name in names:
            print(f"{name:{width}} {cell(old, name, kinds[0]):>9} {cell(new, name, kinds[0]):>9} "
                  f"{cell(old, name, kinds[1]):>9} {cell(new, name, kinds[1]):>9}")
        total = lambda summary, kind: percent(summary[section][kind]) if summary[section] else "n/a"  # noqa: E731
        print(f"{'TOTAL':{width}} {total(other, kinds[0]):>9} {total(checkout, kinds[0]):>9} "
              f"{total(other, kinds[1]):>9} {total(checkout, kinds[1]):>9}")

    files("backend", ("statements", "branches"))
    files("frontend", ("lines", "functions"))
    print("\nA dash: the file has no branches, or isn't in that version.")


if __name__ == "__main__":
    sys.exit(main())
