"""services/llm_client.py: the OpenAI-style request, mock mode, reply parsing, retries with backoff,
and the user-facing error for each kind of failure."""

import json

import httpx
import pytest

from app.config import settings
from app.services.llm_client import LLMError, LLMNotConfiguredError, LLMRateLimitError, chat, chat_json
from tests.conftest import LLM_URL
from tests.factories import completion

MESSAGES = [{"role": "system", "content": "You help."}, {"role": "user", "content": "What is attention?\nBe brief."}]


@pytest.fixture
def provider(http_mock, real_llm):
    """The provider's /chat/completions endpoint; `real_llm` turns mock mode off."""
    return http_mock.post(LLM_URL)


# --- Mock mode -----------------------------------------------------------------------------------


def test_mock_mode_returns_canned_markdown_without_any_request(monkeypatch):
    monkeypatch.setattr(settings, "llm_mock", True)

    reply = chat(MESSAGES)

    assert reply.startswith("**Mock answer:** `LLM_MOCK=true`")
    assert "> What is attention?" in reply  # first line of the request
    assert "Be brief." not in reply
    assert "2 messages" in reply


def test_mock_mode_json_is_an_empty_object(monkeypatch):
    monkeypatch.setattr(settings, "llm_mock", True)

    assert chat_json(MESSAGES) == {}


# --- The request and the reply -------------------------------------------------------------------


def test_chat_sends_an_openai_style_request(provider):
    provider.respond(200, json=completion("  Attention weighs tokens.  "))

    reply = chat(MESSAGES)

    assert reply == "Attention weighs tokens."
    request = provider.calls.last.request
    assert request.headers["authorization"] == "Bearer test-key"
    assert json.loads(request.content) == {"model": "test-model", "messages": MESSAGES}


def test_trailing_slash_in_the_base_url_is_ignored(provider, monkeypatch):
    monkeypatch.setattr(settings, "llm_base_url", "https://llm.test/v1/")
    provider.respond(200, json=completion("ok"))

    assert chat(MESSAGES) == "ok"


def test_no_authorization_header_for_keyless_providers(provider, monkeypatch):
    monkeypatch.setattr(settings, "llm_api_key", "")
    provider.respond(200, json=completion("ok"))

    chat(MESSAGES)

    assert "authorization" not in provider.calls.last.request.headers


def test_thinking_section_is_removed_from_the_reply(provider):
    provider.respond(200, json=completion("<think>\nLet me think...\n</think>\n\nThe answer."))

    assert chat(MESSAGES) == "The answer."


@pytest.mark.parametrize("content", ["", "   \n", None, "<think>only thoughts</think>"])
def test_empty_reply_is_an_error(provider, content):
    provider.respond(200, json=completion(content))

    with pytest.raises(LLMError, match="The LLM returned an empty answer. Try again."):
        chat(MESSAGES)


NON_STRING_CONTENT_BUG = pytest.mark.xfail(
    strict=True, raises=TypeError,
    reason="Known bug: reply content that isn't a string (e.g. a list of content parts) crashes the <think> regex "
    "with a TypeError, which reaches the user as a bare HTTP 500 instead of an LLMError",
)


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, json={"choices": []}),
        httpx.Response(200, json={"error": "nope"}),
        httpx.Response(200, json={"choices": [{"message": {}}]}),
        httpx.Response(200, json=["not", "an", "object"]),
        httpx.Response(200, text="<html>proxy page</html>"),
        pytest.param(httpx.Response(200, json={"choices": [{"message": {"content": [{"type": "text", "text": "Hi."}]}}]}),
                     marks=NON_STRING_CONTENT_BUG),
        pytest.param(httpx.Response(200, json={"choices": [{"message": {"content": 42}}]}), marks=NON_STRING_CONTENT_BUG),
    ],
    ids=["no-choices", "no-choices-key", "no-content", "list-body", "not-json", "content-parts-list", "content-number"],
)
def test_malformed_reply_is_an_error(provider, response):
    provider.mock(side_effect=[response])

    with pytest.raises(LLMError, match="unexpected format"):
        chat(MESSAGES)


# --- JSON replies --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "content",
    [
        '{"title": "T", "year": 2020}',
        '```json\n{"title": "T", "year": 2020}\n```',
        'Here is the metadata:\n{"title": "T", "year": 2020}\nHope this helps!',
    ],
    ids=["bare", "code-fence", "surrounded-by-text"],
)
def test_chat_json_extracts_the_object(provider, content):
    provider.respond(200, json=completion(content))

    assert chat_json(MESSAGES) == {"title": "T", "year": 2020}


@pytest.mark.parametrize("content", ["no braces here", "{not: valid json}", '["a", "list"]', "{'single': 'quotes'}"])
def test_chat_json_rejects_anything_but_a_json_object(provider, content):
    provider.respond(200, json=completion(content))

    with pytest.raises(LLMError, match="The LLM did not return valid JSON."):
        chat_json(MESSAGES)


# --- Retries -------------------------------------------------------------------------------------


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_transient_failure_is_retried_after_a_short_wait(provider, real_llm, status):
    provider.mock(side_effect=[httpx.Response(status), httpx.Response(200, json=completion("Recovered."))])

    assert chat(MESSAGES) == "Recovered."
    assert provider.call_count == 2
    assert real_llm == [3]


def test_persistent_rate_limit_gives_up_after_three_retries_with_backoff(provider, real_llm):
    provider.respond(429, text='{"error": {"message": "Resource exhausted"}}')

    with pytest.raises(LLMRateLimitError, match="LLM rate limit reached, try again shortly."):
        chat(MESSAGES)

    assert provider.call_count == 4
    assert real_llm == [3, 8, 20]


def test_persistent_server_error_is_reported_as_unavailable(provider, real_llm):
    provider.respond(500)

    with pytest.raises(LLMError) as error:
        chat(MESSAGES)

    assert type(error.value) is LLMError
    assert str(error.value) == "The LLM service is unavailable right now (HTTP 500). Try again in a minute."
    assert provider.call_count == 4


def test_used_up_daily_quota_is_not_retried(provider, real_llm):
    provider.respond(429, text='[{"error": {"details": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]}}]')

    with pytest.raises(LLMRateLimitError, match="free daily quota is used up. Try again tomorrow."):
        chat(MESSAGES)

    assert provider.call_count == 1
    assert real_llm == []


def test_retry_after_header_sets_the_wait(provider, real_llm):
    provider.mock(side_effect=[httpx.Response(429, headers={"Retry-After": "5"}), httpx.Response(200, json=completion("ok"))])

    chat(MESSAGES)

    assert real_llm == [6]  # the requested wait, plus a second of margin


def test_gemini_retry_delay_in_the_body_sets_the_wait(provider, real_llm):
    body = '[{"error": {"code": 429, "details": [{"@type": "RetryInfo", "retryDelay": "12s"}]}}]'
    provider.mock(side_effect=[httpx.Response(429, text=body), httpx.Response(200, json=completion("ok"))])

    chat(MESSAGES)

    assert real_llm == [13]


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(429, headers={"Retry-After": "120"}),
        httpx.Response(429, text='{"error": {"details": [{"retryDelay": "45s"}]}}'),
        httpx.Response(503, headers={"Retry-After": "30"}),
    ],
    ids=["header", "gemini-body", "at-the-limit"],
)
def test_too_long_requested_wait_is_not_waited_for(provider, real_llm, response):
    provider.mock(side_effect=[response])

    with pytest.raises(LLMError):
        chat(MESSAGES)

    assert provider.call_count == 1
    assert real_llm == []


def test_retries_can_be_turned_off(provider, real_llm):
    provider.respond(503)

    with pytest.raises(LLMError):
        chat(MESSAGES, retries=0)

    assert provider.call_count == 1


def test_chat_json_honours_its_retry_count(provider, real_llm):
    provider.respond(503)

    with pytest.raises(LLMError):
        chat_json(MESSAGES, retries=1)

    assert provider.call_count == 2


# --- Errors that aren't retried ------------------------------------------------------------------


@pytest.mark.parametrize("status", [401, 403])
def test_rejected_api_key(provider, status):
    provider.respond(status, json={"error": {"message": "API key not valid"}})

    with pytest.raises(LLMNotConfiguredError, match="rejected the API key. Check LLM_API_KEY"):
        chat(MESSAGES)

    assert provider.call_count == 1


def test_missing_api_key(provider, monkeypatch):
    monkeypatch.setattr(settings, "llm_api_key", "")
    provider.respond(401)

    with pytest.raises(LLMNotConfiguredError, match="needs an API key. Set LLM_API_KEY"):
        chat(MESSAGES)


@pytest.mark.parametrize(
    "response, expected",
    [
        (httpx.Response(400, json={"error": {"message": "Unknown model"}}), "Unknown model"),
        (httpx.Response(400, json=[{"error": {"message": "Gemini wraps errors in a list"}}]), "Gemini wraps errors in a list"),
        (httpx.Response(404, text="Not Found"), "Not Found"),
        (httpx.Response(422, json={"detail": "bad"}), "{'detail': 'bad'}"),
    ],
    ids=["openai-style", "gemini-style", "plain-text", "other-json"],
)
def test_rejected_request_includes_the_providers_message(provider, response, expected):
    provider.mock(side_effect=[response])

    with pytest.raises(LLMError) as error:
        chat(MESSAGES)

    assert str(error.value) == f"The LLM rejected the request (HTTP {response.status_code}): {expected}"
    assert provider.call_count == 1


def test_long_provider_message_is_shortened(provider):
    provider.respond(400, text="x" * 5000)

    with pytest.raises(LLMError) as error:
        chat(MESSAGES)

    assert len(str(error.value)) < 400


@pytest.mark.parametrize("setting", ["llm_base_url", "llm_model"])
def test_missing_configuration_fails_without_a_request(provider, monkeypatch, setting):
    monkeypatch.setattr(settings, setting, "")

    with pytest.raises(LLMNotConfiguredError, match="The LLM is not configured"):
        chat(MESSAGES)

    assert not provider.called


@pytest.mark.parametrize(
    "exception, message",
    [
        (httpx.ReadTimeout("slow"), "The LLM took too long to answer. Try again."),
        (httpx.ConnectTimeout("slow"), "The LLM took too long to answer. Try again."),
        (httpx.ConnectError("offline"), "Could not reach the LLM service. Check your internet connection and LLM_BASE_URL."),
    ],
)
def test_network_failures(provider, exception, message):
    provider.mock(side_effect=exception)

    with pytest.raises(LLMError) as error:
        chat(MESSAGES)

    assert str(error.value) == message


def test_error_types_share_one_base_class():
    """The router catches LLMError last, so every subclass must be one."""
    assert issubclass(LLMRateLimitError, LLMError)
    assert issubclass(LLMNotConfiguredError, LLMError)
