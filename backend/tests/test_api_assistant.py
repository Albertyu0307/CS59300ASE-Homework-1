"""The LLM features of a saved paper: summary (cached), questions and answers, chat history,
and how LLM failures reach the user. The LLM runs in mock mode unless a test uses `real_llm`."""

import httpx
import pytest

from app.config import settings
from tests.conftest import LLM_URL
from tests.factories import completion

NO_TEXT = "This paper has no PDF text and no abstract, so there is nothing for the LLM to work with."


# --- GET /api/papers/{id}/summary (no LLM call) ---------------------------------------------------


def test_summary_info_before_any_summary(client, db, llm_calls):
    paper = db.add_paper()

    response = client.get(f"/api/papers/{paper.id}/summary")

    assert response.status_code == 200
    assert response.json() == {"summary": None, "based_on": "full_text", "truncated": False}
    assert llm_calls == []


def test_summary_info_for_a_paper_with_only_an_abstract(client, db):
    paper = db.add_paper(full_text=None, text_status="abstract_only")

    assert client.get(f"/api/papers/{paper.id}/summary").json()["based_on"] == "abstract"


def test_summary_info_for_a_paper_with_no_text_at_all(client, db):
    paper = db.add_paper(full_text=None, abstract=None, text_status="failed")

    assert client.get(f"/api/papers/{paper.id}/summary").json() == {"summary": None, "based_on": "none", "truncated": False}


def test_summary_info_reports_a_truncated_paper(client, db, monkeypatch):
    monkeypatch.setattr(settings, "llm_max_paper_chars", 1000)
    paper = db.add_paper(full_text="word " * 300)

    assert client.get(f"/api/papers/{paper.id}/summary").json()["truncated"] is True


# --- POST /api/papers/{id}/summary --------------------------------------------------------------


def test_summarize_generates_and_stores_a_summary(client, db, llm_calls):
    paper = db.add_paper(title="Attention Is All You Need", full_text="The Transformer uses only attention.")

    response = client.post(f"/api/papers/{paper.id}/summary")

    assert response.status_code == 200
    body = response.json()
    assert body["summary"].startswith("**Mock answer:**")
    assert body["based_on"] == "full_text"
    assert db.paper(paper.id).summary == body["summary"]
    [messages] = llm_calls
    assert "The Transformer uses only attention." in messages[0]["content"]  # the paper's full text is sent
    assert "Title: Attention Is All You Need" in messages[0]["content"]
    assert messages[-1]["content"].startswith("Summarize this paper")


def test_summary_is_cached_after_the_first_request(client, db, llm_calls):
    paper = db.add_paper()
    first = client.post(f"/api/papers/{paper.id}/summary").json()

    second = client.post(f"/api/papers/{paper.id}/summary").json()

    assert second == first
    assert len(llm_calls) == 1
    assert client.get(f"/api/papers/{paper.id}/summary").json()["summary"] == first["summary"]


def test_refresh_generates_a_new_summary(client, db, llm_calls):
    paper = db.add_paper(summary="An old summary.")

    response = client.post(f"/api/papers/{paper.id}/summary", params={"refresh": "true"})

    assert response.json()["summary"] != "An old summary."
    assert len(llm_calls) == 1


def test_summary_of_an_abstract_only_paper_tells_the_llm(client, db, llm_calls):
    paper = db.add_paper(full_text=None, abstract="Only this abstract is known.", text_status="abstract_only")

    body = client.post(f"/api/papers/{paper.id}/summary").json()

    assert body["based_on"] == "abstract"
    system = llm_calls[0][0]["content"]
    assert "Only this abstract is known." in system
    assert "Only the paper's abstract is available" in system


def test_summary_of_a_very_long_paper_sends_only_the_first_part(client, db, llm_calls, monkeypatch):
    monkeypatch.setattr(settings, "llm_max_paper_chars", 1000)
    paper = db.add_paper(full_text="START " + "x" * 2000 + " END")

    body = client.post(f"/api/papers/{paper.id}/summary").json()

    assert body["truncated"] is True
    system = llm_calls[0][0]["content"]
    assert "START" in system and "END" not in system
    assert "its text below was cut off" in system


def test_summary_of_a_paper_without_text_is_422_and_skips_the_llm(client, db, llm_calls):
    paper = db.add_paper(full_text=None, abstract=None, text_status="failed")

    response = client.post(f"/api/papers/{paper.id}/summary")

    assert response.status_code == 422
    assert response.json()["detail"] == NO_TEXT
    assert llm_calls == []


# --- POST /api/papers/{id}/ask, GET /api/papers/{id}/messages --------------------------------------


def test_ask_answers_and_records_the_exchange(client, db):
    paper = db.add_paper()

    response = client.post(f"/api/papers/{paper.id}/ask", json={"question": "  What datasets are used?\n"})

    assert response.status_code == 200
    answer = response.json()["answer"]
    assert "> What datasets are used?" in answer  # the mock echoes the question
    history = client.get(f"/api/papers/{paper.id}/messages").json()
    assert [(m["role"], m["content"]) for m in history] == [
        ("user", "What datasets are used?"),  # stored without the surrounding whitespace
        ("assistant", answer),
    ]
    assert all(set(m) == {"id", "role", "content", "created_at"} for m in history)


def test_chat_history_starts_empty(client, db):
    paper = db.add_paper()

    assert client.get(f"/api/papers/{paper.id}/messages").json() == []


def test_chat_history_is_oldest_first_and_per_paper(client, db):
    first, other = db.add_paper(), db.add_paper()
    for question in ("Q1?", "Q2?"):
        client.post(f"/api/papers/{first.id}/ask", json={"question": question})
    client.post(f"/api/papers/{other.id}/ask", json={"question": "Other?"})

    history = client.get(f"/api/papers/{first.id}/messages").json()

    assert [m["content"] for m in history if m["role"] == "user"] == ["Q1?", "Q2?"]


def test_follow_up_questions_include_the_recent_history(client, db, llm_calls):
    paper = db.add_paper()
    db.add_messages(paper.id, "Q1", "A1", "Q2", "A2", "Q3", "A3", "Q4", "A4")

    client.post(f"/api/papers/{paper.id}/ask", json={"question": "Q5"})

    [messages] = llm_calls
    assert messages[0]["role"] == "system"
    # The last 6 messages (3 questions and answers), oldest first, then the new question.
    assert [(m["role"], m["content"]) for m in messages[1:]] == [
        ("user", "Q2"), ("assistant", "A2"), ("user", "Q3"), ("assistant", "A3"),
        ("user", "Q4"), ("assistant", "A4"), ("user", "Q5"),
    ]


def test_history_of_another_paper_is_not_sent(client, db, llm_calls):
    paper, other = db.add_paper(), db.add_paper()
    db.add_messages(other.id, "Secret question", "Secret answer")

    client.post(f"/api/papers/{paper.id}/ask", json={"question": "Hello?"})

    assert [m["content"] for m in llm_calls[0][1:]] == ["Hello?"]


@pytest.mark.parametrize(
    "body",
    [{"question": ""}, {"question": "x" * 2001}, {}, {"question": None}, {"text": "wrong field"}],
    ids=["empty", "too-long", "missing", "null", "wrong-field"],
)
def test_invalid_question_is_rejected_with_422(client, db, llm_calls, body):
    paper = db.add_paper()

    assert client.post(f"/api/papers/{paper.id}/ask", json=body).status_code == 422
    assert llm_calls == []
    assert db.messages(paper.id) == []


def test_whitespace_only_question_is_rejected(client, db, llm_calls):
    paper = db.add_paper()

    response = client.post(f"/api/papers/{paper.id}/ask", json={"question": " \n\t "})

    assert response.status_code == 422
    assert response.json()["detail"] == "The question is empty."
    assert llm_calls == []


def test_question_at_the_maximum_length_is_accepted(client, db):
    paper = db.add_paper()

    assert client.post(f"/api/papers/{paper.id}/ask", json={"question": "x" * 2000}).status_code == 200


def test_question_about_a_paper_without_text_is_422(client, db):
    paper = db.add_paper(full_text=None, abstract=None, text_status="failed")

    response = client.post(f"/api/papers/{paper.id}/ask", json={"question": "Anything?"})

    assert response.status_code == 422
    assert response.json()["detail"] == NO_TEXT
    assert db.messages(paper.id) == []


@pytest.mark.parametrize(
    "method, path",
    [("get", "summary"), ("post", "summary"), ("post", "ask"), ("get", "messages")],
)
def test_llm_endpoints_of_a_missing_paper_are_404(client, method, path):
    response = client.request(method.upper(), f"/api/papers/4242/{path}", json={"question": "Hi?"})

    assert response.status_code == 404
    assert response.json()["detail"] == "Paper not found. It may have been deleted."


# --- LLM failures (the real client against a mocked provider) -------------------------------------


@pytest.mark.parametrize(
    "provider_status, provider_body, api_key, status, detail",
    [
        (429, '{"error": {"message": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}}', "test-key",
         429, "The LLM's free daily quota is used up. Try again tomorrow."),
        (429, '{"error": {"message": "slow down"}}', "test-key", 429, "LLM rate limit reached, try again shortly."),
        (401, '{"error": {"message": "API key not valid"}}', "test-key",
         503, "The LLM provider rejected the API key. Check LLM_API_KEY in backend/.env."),
        (401, '{"error": {"message": "missing key"}}', "",
         503, "The LLM provider needs an API key. Set LLM_API_KEY in backend/.env."),
        (503, '{"error": {"message": "high demand"}}', "test-key",
         502, "The LLM service is unavailable right now (HTTP 503). Try again in a minute."),
        (400, '{"error": {"message": "Invalid model name"}}', "test-key",
         502, "The LLM rejected the request (HTTP 400): Invalid model name"),
    ],
    ids=["daily-quota", "rate-limit", "bad-key", "no-key", "provider-down", "bad-request"],
)
def test_llm_failures_become_friendly_http_errors(
    client, db, http_mock, real_llm, monkeypatch, provider_status, provider_body, api_key, status, detail
):
    monkeypatch.setattr(settings, "llm_api_key", api_key)
    http_mock.post(LLM_URL).respond(provider_status, text=provider_body)
    paper = db.add_paper()

    summary = client.post(f"/api/papers/{paper.id}/summary")
    answer = client.post(f"/api/papers/{paper.id}/ask", json={"question": "Why?"})

    assert (summary.status_code, summary.json()["detail"]) == (status, detail)
    assert (answer.status_code, answer.json()["detail"]) == (status, detail)
    assert db.paper(paper.id).summary is None  # nothing cached
    assert db.messages(paper.id) == []  # nothing recorded, so the question can be retried


def test_failed_refresh_keeps_the_old_summary(client, db, http_mock, real_llm):
    """Regenerate is the one action that risks an existing summary, e.g. once the daily quota is used up."""
    http_mock.post(LLM_URL).respond(400, json={"error": {"message": "Invalid model name"}})  # 400: not retried
    paper = db.add_paper(summary="The old summary.")

    response = client.post(f"/api/papers/{paper.id}/summary", params={"refresh": "true"})

    assert response.status_code == 502
    assert db.paper(paper.id).summary == "The old summary."
    assert client.get(f"/api/papers/{paper.id}/summary").json()["summary"] == "The old summary."


def test_unconfigured_llm_is_503(client, db, real_llm, monkeypatch):
    monkeypatch.setattr(settings, "llm_base_url", "")
    paper = db.add_paper()

    response = client.post(f"/api/papers/{paper.id}/summary")

    assert response.status_code == 503
    assert "The LLM is not configured" in response.json()["detail"]


def test_real_llm_answer_is_returned_and_stored(client, db, http_mock, real_llm):
    route = http_mock.post(LLM_URL).respond(200, json=completion("<think>planning</think>The paper uses **ImageNet**."))
    paper = db.add_paper()

    response = client.post(f"/api/papers/{paper.id}/ask", json={"question": "Which dataset?"})

    assert response.json() == {"answer": "The paper uses **ImageNet**."}
    assert db.messages(paper.id)[-1].content == "The paper uses **ImageNet**."
    request = route.calls.last.request
    assert request.headers["authorization"] == "Bearer test-key"


def test_transient_llm_overload_is_retried_before_answering(client, db, http_mock, real_llm):
    http_mock.post(LLM_URL).mock(side_effect=[
        httpx.Response(503, text="overloaded"),
        httpx.Response(200, json=completion("Recovered answer.")),
    ])
    paper = db.add_paper()

    response = client.post(f"/api/papers/{paper.id}/summary")

    assert response.status_code == 200
    assert response.json()["summary"] == "Recovered answer."
    assert real_llm == [3]  # waited once before retrying
