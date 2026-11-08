"""The handout's requirements (hw1.pdf), each followed through the whole backend.

The other test files check one endpoint or service at a time. These follow a paper through several
of them, the way a user's clicks do: a search result is saved exactly as the search returned it, the
text of its PDF is what the LLM reads, and the library is still complete after a restart. The same
workflow in the browser is in e2e/test_workflows.py; TEST_CATALOG.md maps each requirement to its tests.
"""

from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient
from sqlmodel import create_engine

from app import database
from app.main import app
from tests.factories import (
    BODY_ONLY_PHRASE,
    DEFAULT_ABSTRACT,
    DEFAULT_TITLE,
    HANDOUT_QUESTIONS,
    arxiv_entry,
    arxiv_feed,
    make_pdf,
    one_line,
    openalex_response,
    openalex_work,
)

ARXIV_PDF = "https://arxiv.org/pdf/2101.00001v2"  # arxiv_entry("2101.00001")'s PDF link
OPENALEX_PDF = "https://example.org/resnet.pdf"  # openalex_work()'s open-access copy
PDF_TITLE = " ".join(DEFAULT_TITLE)


def search_and_save(client, http_mock) -> dict:
    """Search arXiv and save the result as the Save button does; its PDF is make_pdf()."""
    http_mock.get(host="export.arxiv.org", path="/api/query").respond(
        200, text=arxiv_feed(arxiv_entry("2101.00001", title=PDF_TITLE, summary=DEFAULT_ABSTRACT))
    )
    http_mock.get(ARXIV_PDF).respond(200, content=make_pdf())
    [result] = client.get("/api/search", params={"q": "sparse attention"}).json()
    response = client.post("/api/papers", json=result)
    assert response.status_code == 201
    return response.json()


def upload(client, http_mock) -> dict:
    """Upload a PDF as the library page does. (http_mock is unused: same signature as search_and_save.)"""
    pdf = make_pdf(header="Proceedings of the Test Conference 2019")
    response = client.post("/api/papers/upload", files={"file": ("sparse-attention.pdf", pdf, "application/pdf")})
    assert response.status_code == 201
    return response.json()


# --- Requirements 1 and 2: save the results the search returned --------------------------------------


@pytest.mark.parametrize("source, pdf_url", [("auto", ARXIV_PDF), ("openalex", OPENALEX_PDF)], ids=["arxiv", "openalex"])
def test_search_result_is_saved_exactly_as_the_search_returned_it(client, http_mock, source, pdf_url):
    """The Save button posts a result just as GET /api/search returned it, from either source."""
    http_mock.get(host="export.arxiv.org", path="/api/query").respond(200, text=arxiv_feed(arxiv_entry("2101.00001")))
    http_mock.get(host="api.openalex.org", path="/works").respond(200, json=openalex_response(openalex_work()))
    http_mock.get(pdf_url).respond(200, content=make_pdf())
    [result] = client.get("/api/search", params={"q": "neural networks", "source": source}).json()
    assert all(result[field] for field in ("title", "authors", "year", "abstract", "url"))  # the handout's fields

    response = client.post("/api/papers", json=result)

    assert response.status_code == 201
    [listed] = client.get("/api/papers").json()
    assert {field: listed[field] for field in result} == result
    assert (listed["text_status"], listed["has_pdf"]) == ("full", True)
    assert client.get(f"/api/papers/{listed['id']}").json() == listed


# --- Requirement 2: everything survives a restart ----------------------------------------------------


@contextmanager
def restarted_app(data_dir, monkeypatch):
    """The app started again on the same data folder, as after stopping and restarting uvicorn."""
    engine = create_engine(f"sqlite:///{(data_dir / 'library.db').as_posix()}")
    monkeypatch.setattr(database, "engine", engine)
    try:
        with TestClient(app) as client:
            yield client
    finally:
        engine.dispose()


def test_papers_pdfs_summaries_and_questions_survive_a_restart(client, http_mock, data_dir, monkeypatch, llm_calls):
    saved, uploaded = search_and_save(client, http_mock), upload(client, http_mock)
    summary = client.post(f"/api/papers/{saved['id']}/summary").json()
    client.post(f"/api/papers/{uploaded['id']}/ask", json={"question": "What datasets are used?"})
    library = client.get("/api/papers").json()
    chat = client.get(f"/api/papers/{uploaded['id']}/messages").json()
    pdfs = {paper["id"]: client.get(f"/api/papers/{paper['id']}/pdf").content for paper in library}
    assert [paper["has_pdf"] for paper in library] == [True, True]
    client.close()
    llm_calls.clear()

    with restarted_app(data_dir, monkeypatch) as restarted:
        assert restarted.get("/api/papers").json() == library
        assert restarted.get(f"/api/papers/{saved['id']}/summary").json() == summary
        assert restarted.get(f"/api/papers/{uploaded['id']}/messages").json() == chat
        for paper_id, pdf in pdfs.items():
            assert restarted.get(f"/api/papers/{paper_id}/pdf").content == pdf
        assert llm_calls == []  # the summary comes from the database, not from a new LLM call
        restarted.post(f"/api/papers/{uploaded['id']}/ask", json={"question": "How does it compare with the baselines?"})

    # The stored text still feeds the LLM, along with the question asked before the restart.
    [follow_up] = llm_calls
    assert BODY_ONLY_PHRASE in one_line(follow_up[0]["content"])
    assert [message["content"] for message in follow_up[1:]] == [
        "What datasets are used?", chat[1]["content"], "How does it compare with the baselines?",
    ]


# --- Requirements 4 and 5: the LLM reads the paper, not just its metadata -----------------------------


@pytest.mark.parametrize("add_paper", [search_and_save, upload], ids=["saved-from-search", "uploaded"])
def test_summary_and_answers_are_based_on_the_text_of_the_papers_pdf(client, http_mock, llm_provider, add_paper):
    """Requirement 4: "based on the content of the selected paper rather than only its title or
    metadata". The phrase is only in the PDF's body, so finding it shows that the LLM got the body."""
    paper = add_paper(client, http_mock)
    assert BODY_ONLY_PHRASE not in one_line(f"{paper['title']} {paper['abstract']}")

    summary = client.post(f"/api/papers/{paper['id']}/summary").json()
    answer = client.post(f"/api/papers/{paper['id']}/ask", json={"question": "What datasets are used?"}).json()

    assert summary == {"summary": llm_provider.SUMMARY, "based_on": "full_text", "truncated": False}
    assert answer == {"answer": "An answer to: What datasets are used?"}
    assert len(llm_provider.requests) == 2
    for messages in llm_provider.requests:
        assert BODY_ONLY_PHRASE in one_line(messages[0]["content"])
        assert f"Title: {paper['title']}" in messages[0]["content"]


def test_summary_and_answers_use_only_the_selected_paper(client, db, llm_calls):
    """With several papers in the library, the LLM gets the one the user opened, and only that one."""
    other = db.add_paper(title="Protein Folding at Scale", full_text="We predict protein structures from sequences.")
    selected = db.add_paper(title="Graph Coloring Heuristics", full_text="We color sparse graphs with few colors.")

    client.post(f"/api/papers/{selected.id}/summary")
    client.post(f"/api/papers/{selected.id}/ask", json={"question": "What is the main idea?"})

    assert len(llm_calls) == 2
    for messages in llm_calls:
        prompt = "\n".join(message["content"] for message in messages)
        assert selected.title in prompt and selected.full_text in prompt
        assert other.title not in prompt and other.full_text not in prompt
    assert db.paper(other.id).summary is None
    assert db.messages(other.id) == []


# --- Requirement 5: the handout's example questions ---------------------------------------------------


@pytest.mark.parametrize("question", HANDOUT_QUESTIONS)
def test_handouts_example_question_is_answered_from_the_paper(client, db, llm_provider, question):
    paper = db.add_paper()

    response = client.post(f"/api/papers/{paper.id}/ask", json={"question": question})

    assert response.status_code == 200
    assert response.json() == {"answer": f"An answer to: {question}"}
    [messages] = llm_provider.requests
    assert messages[0]["role"] == "system" and paper.full_text in messages[0]["content"]
    assert messages[-1] == {"role": "user", "content": question}
    assert [(message.role, message.content) for message in db.messages(paper.id)] == [
        ("user", question), ("assistant", f"An answer to: {question}"),
    ]
