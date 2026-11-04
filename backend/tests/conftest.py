"""Shared fixtures. Every test runs offline, against a fresh empty library in its own temporary folder.

- The settings are pinned through environment variables BEFORE the app is imported: they are read
  at import time, and backend/.env may hold a real API key with LLM_MOCK=false. Environment
  variables take precedence over .env.
- Each test gets its own data folder (SQLite DB + stored PDFs); the app's engine is swapped for one
  pointing there, so backend/data/ is never touched.
- `http_mock` (autouse) intercepts every httpx request. A request without a matching route fails the
  test instead of reaching arXiv, OpenAlex or the LLM provider.
- Waits are removed: the mock LLM's delay and the arXiv throttle.
"""

import atexit
import os
import shutil
import tempfile
from types import SimpleNamespace

_IMPORT_TIME_DATA_DIR = tempfile.mkdtemp(prefix="ara-tests-")
atexit.register(shutil.rmtree, _IMPORT_TIME_DATA_DIR, ignore_errors=True)
os.environ.update(
    LLM_MOCK="true",
    LLM_BASE_URL="https://llm.test/v1",
    LLM_API_KEY="",
    LLM_MODEL="test-model",
    LLM_MAX_PAPER_CHARS="200000",
    DATA_DIR=_IMPORT_TIME_DATA_DIR,
)

import pytest  # noqa: E402
import respx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlmodel import Session, SQLModel, create_engine, select  # noqa: E402

from app import database  # noqa: E402
from app.config import settings  # noqa: E402
from app.main import app  # noqa: E402
from app.models import ChatMessage, Paper  # noqa: E402
from app.services import llm_client, search_client  # noqa: E402

LLM_URL = "https://llm.test/v1/chat/completions"


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    """This test's data folder (settings.data_path); stored PDFs go to data_dir / "pdfs"."""
    path = tmp_path / "data"
    path.mkdir()
    monkeypatch.setattr(settings, "data_dir", path)
    return path


@pytest.fixture(autouse=True)
def engine(data_dir, monkeypatch):
    """A fresh SQLite database for this test, used by every request the app handles."""
    test_engine = create_engine(f"sqlite:///{(data_dir / 'library.db').as_posix()}", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(test_engine)
    monkeypatch.setattr(database, "engine", test_engine)
    yield test_engine
    test_engine.dispose()


@pytest.fixture(autouse=True)
def fast_and_clean_state(monkeypatch):
    """No artificial waits, and none of the arXiv state (cache, cooldown) carried over between tests."""
    monkeypatch.setattr(llm_client, "MOCK_DELAY_SECONDS", 0)
    monkeypatch.setattr(search_client, "MIN_SECONDS_BETWEEN_REQUESTS", 0)
    monkeypatch.setattr(search_client, "_last_request_at", 0.0)
    monkeypatch.setattr(search_client, "_arxiv_unavailable_until", 0.0)
    search_client._cache.clear()
    yield
    search_client._cache.clear()


@pytest.fixture(autouse=True)
def http_mock():
    """All outgoing httpx requests. Tests add routes, e.g. http_mock.get(url).respond(200, text=...)."""
    with respx.mock(assert_all_called=False, assert_all_mocked=True) as router:
        yield router


@pytest.fixture
def client():
    """The whole app (API + static frontend) behind FastAPI's test client, startup included."""
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def sleeps(monkeypatch):
    """Replaces the LLM client's time.sleep; the list collects the requested waits (in seconds)."""
    waits: list[float] = []
    monkeypatch.setattr(llm_client, "time", SimpleNamespace(sleep=waits.append))
    return waits


@pytest.fixture
def real_llm(monkeypatch, sleeps):
    """LLM_MOCK=false with an API key: llm_client posts to LLM_URL (mock it with http_mock)."""
    monkeypatch.setattr(settings, "llm_mock", False)
    monkeypatch.setattr(settings, "llm_api_key", "test-key")
    return sleeps


@pytest.fixture
def llm_calls(monkeypatch):
    """Records the messages of every llm_client.chat() call (the mock reply is still returned)."""
    calls: list[list[dict]] = []
    real_chat = llm_client.chat

    def chat(messages, **kwargs):
        calls.append(messages)
        return real_chat(messages, **kwargs)

    monkeypatch.setattr(llm_client, "chat", chat)
    return calls


class Db:
    """Direct access to the test database, to set up rows and to check what the API stored."""

    def __init__(self, engine):
        self.engine = engine
        self._counter = 0

    def add_paper(self, **fields) -> Paper:
        self._counter += 1
        values = {
            "source": "arxiv",
            "external_id": f"2101.{self._counter:05d}",
            "title": f"Test Paper {self._counter}",
            "authors": ["Alice Smith", "Bob Jones"],
            "year": 2021,
            "abstract": "We propose a method and evaluate it on three datasets.",
            "full_text": "Introduction. We propose a method.\n\nExperiments. It beats the baseline by 3 points.",
            "text_status": "full",
        }
        values.update(fields)
        with Session(self.engine) as session:
            paper = Paper(**values)
            session.add(paper)
            session.commit()
            session.refresh(paper)
            return paper

    def add_messages(self, paper_id: int, *contents: str) -> None:
        """Alternating question / answer messages."""
        with Session(self.engine) as session:
            for i, content in enumerate(contents):
                session.add(ChatMessage(paper_id=paper_id, role="user" if i % 2 == 0 else "assistant", content=content))
            session.commit()

    def paper(self, paper_id: int) -> Paper | None:
        with Session(self.engine) as session:
            return session.get(Paper, paper_id)

    def papers(self) -> list[Paper]:
        with Session(self.engine) as session:
            return list(session.exec(select(Paper)))

    def messages(self, paper_id: int) -> list[ChatMessage]:
        with Session(self.engine) as session:
            return list(session.exec(select(ChatMessage).where(ChatMessage.paper_id == paper_id).order_by(ChatMessage.id)))


@pytest.fixture
def db(engine) -> Db:
    return Db(engine)


@pytest.fixture
def stored_pdfs(data_dir):
    """Lists the PDF files currently stored under data/pdfs/."""
    return lambda: sorted((data_dir / "pdfs").glob("*.pdf")) if (data_dir / "pdfs").exists() else []
