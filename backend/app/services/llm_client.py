"""The only module that talks to an LLM: one client for the OpenAI-style chat-completions API.

The candidate providers (OVHcloud AI Endpoints, Gemini's OpenAI-compatible endpoint, Mistral, ...)
all accept the same request, so switching providers means editing backend/.env, not code:
LLM_BASE_URL, LLM_API_KEY (blank for keyless providers) and LLM_MODEL.
LLM_MOCK=true returns canned text without calling anything, to build the UI without spending quota.

Free tiers are rate-limited, so HTTP 429 and 5xx responses are retried with backoff, and then
reported with a friendly message (PLAN.md section 4).
"""

import json
import logging
import re
import time

import httpx

from app.config import settings

log = logging.getLogger(__name__)

Message = dict[str, str]  # {"role": "system" | "user" | "assistant", "content": "..."}

# A whole paper in the prompt can take a while to answer.
TIMEOUT = httpx.Timeout(120, connect=10)
# Waits before each retry after HTTP 429 (rate limit) or 5xx (provider overloaded).
RETRY_DELAYS_SECONDS = (3, 8, 20)
# When the provider asks for a longer wait than this, give up and tell the user instead.
MAX_RETRY_WAIT_SECONDS = 30
MOCK_DELAY_SECONDS = 0.8  # long enough to see the UI's loading state

_THINKING = re.compile(r"^\s*<think>.*?</think>\s*", re.DOTALL)  # some open models think out loud
_JSON_OBJECT = re.compile(r"\{.*\}", re.DOTALL)
# Providers that say how long to wait: a Retry-After header, or Gemini's "retryDelay": "37s".
_RETRY_DELAY_IN_BODY = re.compile(r'"retryDelay"\s*:\s*"(\d+)')


class LLMError(Exception):
    """The LLM call failed. The message is user-facing."""


class LLMNotConfiguredError(LLMError):
    """LLM_BASE_URL / LLM_MODEL are missing, or the provider rejected the API key."""


class LLMRateLimitError(LLMError):
    """The provider kept answering HTTP 429."""


def chat(messages: list[Message], *, retries: int = len(RETRY_DELAYS_SECONDS)) -> str:
    """Send a conversation to the LLM and return its reply (Markdown text). Raises LLMError."""
    if settings.llm_mock:
        time.sleep(MOCK_DELAY_SECONDS)
        return _mock_reply(messages)
    return _complete(messages, retries)


def chat_json(messages: list[Message], *, retries: int = len(RETRY_DELAYS_SECONDS)) -> dict:
    """chat() for prompts that ask for a JSON object; returns it parsed. Mock mode returns {}."""
    if settings.llm_mock:
        return {}
    reply = _complete(messages, retries)
    # Models sometimes wrap the object in a ```json fence or a sentence; take the outermost braces.
    match = _JSON_OBJECT.search(reply)
    try:
        data = json.loads(match.group(0)) if match else None
    except json.JSONDecodeError:
        data = None
    if not isinstance(data, dict):
        raise LLMError("The LLM did not return valid JSON.")
    return data


def _complete(messages: list[Message], retries: int) -> str:
    if not settings.llm_base_url or not settings.llm_model:
        raise LLMNotConfiguredError(
            "The LLM is not configured. Set LLM_BASE_URL and LLM_MODEL in backend/.env, "
            "or set LLM_MOCK=true to get canned answers."
        )
    url = settings.llm_base_url.rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {settings.llm_api_key}"} if settings.llm_api_key else {}
    body = {"model": settings.llm_model, "messages": messages}

    for attempt in range(retries + 1):
        try:
            response = httpx.post(url, json=body, headers=headers, timeout=TIMEOUT)
        except httpx.TimeoutException as exc:
            raise LLMError("The LLM took too long to answer. Try again.") from exc
        except httpx.HTTPError as exc:
            raise LLMError("Could not reach the LLM service. Check your internet connection and LLM_BASE_URL.") from exc

        if response.status_code == 200:
            return _reply_text(response)
        if attempt == retries or not _retryable(response):
            break
        delay = _retry_delay(response, attempt)
        if delay is None:
            break
        log.warning("LLM returned HTTP %s; retry %d of %d in %d s", response.status_code, attempt + 1, retries, delay)
        time.sleep(delay)
    raise _error_for(response)


def _retryable(response: httpx.Response) -> bool:
    if response.status_code == 429:
        return not _daily_quota_used_up(response)  # waiting a few seconds won't help
    return response.status_code >= 500


def _retry_delay(response: httpx.Response, attempt: int) -> int | None:
    """Seconds to wait before the next attempt, or None if the provider wants too long a wait."""
    hint = response.headers.get("retry-after", "")
    if not hint.isdigit() and (match := _RETRY_DELAY_IN_BODY.search(response.text)):
        hint = match.group(1)
    if hint.isdigit():
        return int(hint) + 1 if int(hint) < MAX_RETRY_WAIT_SECONDS else None
    return RETRY_DELAYS_SECONDS[min(attempt, len(RETRY_DELAYS_SECONDS) - 1)]


def _daily_quota_used_up(response: httpx.Response) -> bool:
    # Gemini names the exhausted quota, e.g. "GenerateRequestsPerDayPerProjectPerModel-FreeTier".
    return "PerDay" in response.text


def _error_for(response: httpx.Response) -> LLMError:
    status = response.status_code
    log.warning("LLM request failed: HTTP %s %s", status, response.text[:500])
    if status == 429:
        if _daily_quota_used_up(response):
            return LLMRateLimitError("The LLM's free daily quota is used up. Try again tomorrow.")
        return LLMRateLimitError("LLM rate limit reached, try again shortly.")
    if status in (401, 403):
        if not settings.llm_api_key:
            return LLMNotConfiguredError("The LLM provider needs an API key. Set LLM_API_KEY in backend/.env.")
        return LLMNotConfiguredError("The LLM provider rejected the API key. Check LLM_API_KEY in backend/.env.")
    if status >= 500:
        return LLMError(f"The LLM service is unavailable right now (HTTP {status}). Try again in a minute.")
    return LLMError(f"The LLM rejected the request (HTTP {status}): {_provider_message(response)}")


def _provider_message(response: httpx.Response) -> str:
    """The error message in an OpenAI-style error body ({"error": {"message": ...}}), else the raw text."""
    try:
        body = response.json()
    except ValueError:
        return response.text[:300]
    if isinstance(body, list) and body:  # Gemini wraps the error object in a list
        body = body[0]
    error = body.get("error") if isinstance(body, dict) else None
    message = error.get("message") if isinstance(error, dict) else None
    return str(message or body)[:300]


def _reply_text(response: httpx.Response) -> str:
    try:
        content = response.json()["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise LLMError("The LLM returned a response in an unexpected format.") from exc
    text = _THINKING.sub("", content or "").strip()
    if not text:
        raise LLMError("The LLM returned an empty answer. Try again.")
    return text


def _mock_reply(messages: list[Message]) -> str:
    request = messages[-1]["content"].strip().splitlines()[0][:200]
    prompt_chars = sum(len(message["content"]) for message in messages)
    return (
        "**Mock answer:** `LLM_MOCK=true`, so no LLM was called.\n\n"
        f"> {request}\n\n"
        f"The prompt had {len(messages)} messages and {prompt_chars:,} characters. A real answer would cover:\n\n"
        "- the problem the paper addresses\n"
        "- the main idea of the approach\n"
        "- the datasets, baselines and results\n\n"
        "Set `LLM_MOCK=false` in `backend/.env` to use the real model."
    )
