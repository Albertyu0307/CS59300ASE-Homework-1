"""The handout's rules for the repository (hw1.pdf, "Submission"): no API key or other credential is
committed, the dependency and configuration files are complete, and the README's setup and run
commands match the project. The git checks are skipped outside a git checkout (e.g. a ZIP download).
"""

import ast
import importlib
import importlib.metadata
import re
import subprocess
import sys
from pathlib import Path

import pytest
from packaging.requirements import Requirement

from app.config import BACKEND_DIR, Settings
from app.main import app

REPO = BACKEND_DIR.parent

CREDENTIALS = {
    "Google API key": re.compile(r"AIza[0-9A-Za-z_-]{35}"),
    "OpenAI-style secret key": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"),
    "private key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    # A .env-style line with a value. A blank LLM_API_KEY=, a Python LLM_API_KEY="", or a mention
    # inside a sentence is fine.
    "filled-in LLM_API_KEY": re.compile(r"^[ \t]*LLM_API_KEY[ \t]*=[ \t]*[^\s#\"'`]", re.MULTILINE),
}


def credentials_in(text: str) -> list[str]:
    return [kind for kind, pattern in CREDENTIALS.items() if pattern.search(text)]


def git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, text=True, encoding="utf-8")


@pytest.fixture(scope="module")
def tracked_files() -> list[Path]:
    """Every file in the repository, as committed (not ignored ones like backend/.env)."""
    try:
        result = git("ls-files", "-z")
    except FileNotFoundError:
        pytest.skip("git is not installed")
    if result.returncode != 0:
        pytest.skip("Not a git checkout")
    return [REPO / name for name in result.stdout.split("\0") if name]


def requirement_lines(file: Path) -> list[str]:
    """The requirements in a pip requirements file, without comments and -r includes."""
    lines = (line.split("#", 1)[0].strip() for line in file.read_text(encoding="utf-8").splitlines())
    return [line for line in lines if line and not line.startswith("-")]


# --- No credentials in the repository ----------------------------------------------------------------


def test_no_credentials_are_committed(tracked_files):
    found = []
    for path in tracked_files:
        if not path.is_file():  # deleted but not yet committed
            continue
        with path.open("rb") as file:
            if b"\0" in file.read(8192):  # binary: the PDF handout, the demo video
                continue
        found += [f"{path.relative_to(REPO)}: {kind}" for kind in credentials_in(path.read_text(encoding="utf-8", errors="ignore"))]

    assert found == []


@pytest.mark.parametrize(
    "text, found",
    [
        ("key = '" + "AIza" + "x" * 35 + "'", ["Google API key"]),
        ("OPENAI_KEY=" + "sk-" + "a1B2" * 6, ["OpenAI-style secret key"]),
        ("LLM_MOCK=false\nLLM_API_KEY=abc123\n", ["filled-in LLM_API_KEY"]),
        ("LLM_API_KEY=\nLLM_MODEL=gemini\n", []),
        ('    LLM_API_KEY="",', []),
        ("In backend/.env, set `LLM_API_KEY=` to the key.", []),
    ],
    ids=["google-key", "openai-key", "filled-in-env-line", "blank-env-line", "python-empty-string", "readme-sentence"],
)
def test_credential_patterns_tell_keys_from_mentions(text, found):
    """Control for the scan above: it would find a real-looking key, and it ignores the repository's
    own mentions of LLM_API_KEY."""
    assert credentials_in(text) == found


def test_env_file_with_the_api_key_is_ignored_by_git(tracked_files):
    assert REPO / "backend" / ".env" not in tracked_files
    assert git("check-ignore", "--quiet", "backend/.env").returncode == 0  # so `git add .` skips it too


# --- Dependency and configuration files ---------------------------------------------------------------


def test_env_example_lists_every_setting_and_no_key():
    lines = (BACKEND_DIR / ".env.example").read_text(encoding="utf-8").splitlines()
    values = dict(line.split("=", 1) for line in lines if line.strip() and not line.startswith("#"))

    assert {name.lower() for name in values} == set(Settings.model_fields)
    assert values["LLM_API_KEY"] == ""


@pytest.mark.parametrize("file", ["requirements.txt", "requirements-dev.txt"])
def test_dependencies_have_pinned_versions(file):
    lines = requirement_lines(BACKEND_DIR / file)

    assert lines
    assert [line for line in lines if not re.fullmatch(r"[A-Za-z0-9][\w.-]*(\[[\w,.-]+\])?==\d[\w.+!-]*", line)] == []


def _normalized(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _installed_by(lines: list[str]) -> set[str]:
    """The distributions `pip install` gives for these requirements: them and, recursively, their
    dependencies, including those of requested extras such as uvicorn[standard]."""
    pending = [Requirement(line) for line in lines]
    done: set[tuple[str, frozenset[str]]] = set()
    while pending:
        requirement = pending.pop()
        key = (_normalized(requirement.name), frozenset(requirement.extras))
        if key in done:
            continue
        done.add(key)
        for line in importlib.metadata.requires(requirement.name) or []:
            dependency = Requirement(line)
            extras = requirement.extras or {""}
            if dependency.marker is None or any(dependency.marker.evaluate({"extra": extra}) for extra in extras):
                pending.append(dependency)
    return {name for name, _ in done}


def _third_party_imports(folder: Path) -> set[str]:
    modules = set()
    for path in folder.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                modules.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                modules.add(node.module.split(".")[0])
    return modules - set(sys.stdlib_module_names) - {"app"}


def test_requirements_txt_installs_every_package_the_app_imports():
    """A fresh `pip install -r requirements.txt` provides every module the app imports, listed there
    or as a dependency of a listed package (e.g. sqlalchemy comes with sqlmodel)."""
    installed = _installed_by(requirement_lines(BACKEND_DIR / "requirements.txt"))
    distributions = importlib.metadata.packages_distributions()
    imports = _third_party_imports(BACKEND_DIR / "app")

    assert {"fastapi", "httpx", "pymupdf"} <= imports  # the scan sees the app's imports
    assert [module for module in sorted(imports) if not {_normalized(d) for d in distributions.get(module, [])} & installed] == []


# --- README -------------------------------------------------------------------------------------------


def test_readme_setup_and_run_commands_match_the_project():
    """The commands a reader copies from the README name files and a module that exist."""
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    requirement_files = re.findall(r"pip install -r (\S+)", readme)
    env_templates = re.findall(r"cp (\S+) \.env\b", readme)
    apps = re.findall(r"uvicorn ([\w.]+):(\w+)", readme)

    assert set(requirement_files) == {"requirements.txt", "requirements-dev.txt"}
    assert all((BACKEND_DIR / name).is_file() for name in requirement_files)
    assert env_templates and all((BACKEND_DIR / name).is_file() for name in env_templates)
    assert apps and all(getattr(importlib.import_module(module), name) is app for module, name in apps)
