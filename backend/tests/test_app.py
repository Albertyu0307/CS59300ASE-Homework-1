"""The app as a whole: health check, the static frontend, startup, persistence, configuration."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect
from sqlmodel import create_engine

from app import database
from app.config import BACKEND_DIR, Settings, settings
from app.main import app
from tests.factories import search_result


def test_health_check(client):
    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.parametrize(
    "path, expected_text",
    [
        ("/", "<title>Search · AI Research Assistant</title>"),
        ("/index.html", 'id="search-form"'),
        ("/library.html", "<h1>My library</h1>"),
        ("/paper.html", 'id="ask-form"'),
    ],
)
def test_frontend_pages_are_served(client, path, expected_text):
    response = client.get(path)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert expected_text in response.text


@pytest.mark.parametrize("script", ["api.js", "ui.js", "search.js", "library.js", "paper.js"])
def test_frontend_scripts_are_served_as_javascript(client, script):
    response = client.get(f"/js/{script}")

    assert response.status_code == 200
    assert "javascript" in response.headers["content-type"]


def test_stylesheet_is_served(client):
    response = client.get("/css/style.css")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/css")


@pytest.mark.parametrize("path", ["/missing.html", "/js/missing.js", "/api/missing"])
def test_unknown_paths_return_404(client, path):
    assert client.get(path).status_code == 404


@pytest.mark.parametrize("path", ["/%2e%2e/backend/app/config.py", "/..%2f..%2fbackend%2f.env.example", "/js/%2e%2e/%2e%2e/README.md"])
def test_files_outside_the_frontend_folder_are_not_served(client, path):
    response = client.get(path)

    assert response.status_code == 404
    assert "BaseSettings" not in response.text and "LLM_API_KEY" not in response.text


def test_dot_dot_inside_the_frontend_folder_still_resolves(client):
    """Control for the test above: the server does decode "..", it just stops at frontend/."""
    assert client.get("/js/%2e%2e/index.html").status_code == 200


def test_api_routes_take_precedence_over_the_static_mount(client):
    """The static mount at "/" is registered last, so every API route is still reachable."""
    paths = client.get("/openapi.json").json()["paths"]

    assert {"/api/health", "/api/search", "/api/papers", "/api/papers/upload", "/api/papers/{paper_id}",
            "/api/papers/{paper_id}/pdf", "/api/papers/{paper_id}/summary", "/api/papers/{paper_id}/ask",
            "/api/papers/{paper_id}/messages"} <= set(paths)
    assert client.get("/api/papers").headers["content-type"] == "application/json"


def test_startup_creates_the_data_folder_and_tables(tmp_path, monkeypatch):
    fresh_dir = tmp_path / "not-created-yet"
    fresh_engine = create_engine(f"sqlite:///{(fresh_dir / 'library.db').as_posix()}")
    monkeypatch.setattr(settings, "data_dir", fresh_dir)
    monkeypatch.setattr(database, "engine", fresh_engine)

    with TestClient(app) as test_client:
        assert test_client.get("/api/papers").json() == []

    assert (fresh_dir / "library.db").is_file()
    assert {"paper", "chatmessage"} <= set(inspect(fresh_engine).get_table_names())
    fresh_engine.dispose()


def test_saved_papers_survive_a_restart(client, data_dir, monkeypatch):
    client.post("/api/papers", json=search_result(pdf_url=None))
    client.close()

    # A new process: a new engine on the same database file.
    restarted_engine = create_engine(f"sqlite:///{(data_dir / 'library.db').as_posix()}")
    monkeypatch.setattr(database, "engine", restarted_engine)
    with TestClient(app) as restarted:
        papers = restarted.get("/api/papers").json()

    assert [paper["title"] for paper in papers] == ["Graph Neural Networks: A Review"]
    restarted_engine.dispose()


def test_relative_data_dir_is_resolved_against_the_backend_folder():
    assert Settings(_env_file=None, data_dir=Path("./data")).data_path == BACKEND_DIR / "data"


def test_absolute_data_dir_is_used_as_is(tmp_path):
    assert Settings(_env_file=None, data_dir=tmp_path).data_path == tmp_path


def test_settings_default_to_mock_mode_and_gemini(monkeypatch):
    for name in ("LLM_MOCK", "LLM_BASE_URL", "LLM_MODEL", "LLM_API_KEY"):
        monkeypatch.delenv(name, raising=False)

    defaults = Settings(_env_file=None)

    assert defaults.llm_mock is True  # works out of the box with no key
    assert defaults.llm_api_key == ""
    assert "generativelanguage.googleapis.com" in defaults.llm_base_url
    assert defaults.llm_max_paper_chars == 200_000
