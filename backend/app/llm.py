"""The language model behind generative features, reached through any OpenAI-compatible endpoint.

    from .. import llm

    if not llm.configured():
        raise HTTPException(503, "no model configured")
    text, input_tokens, output_tokens = llm.chat([{"role": "user", "content": "..."}])

Three environment variables choose the endpoint (`LLM_BASE_URL`), the key (`LLM_API_KEY`) and
the model name (`LLM_MODEL`). Nebius Token Factory, OpenRouter, OpenAI and a local server all
speak the same chat-completions protocol, so this file never needs to change when you switch.
The key is read from settings only; it is never logged and never returned to the browser.
"""
from __future__ import annotations

from openai import OpenAI

from .config import settings

# One request may take a while on a busy endpoint, but a clinician is waiting: cap it at 20 s.
TIMEOUT_SECONDS = 20.0

_client: OpenAI | None = None


def configured() -> bool:
    """True when a key is present. Routes answer 503 instead of calling the model without one."""
    return bool(settings.llm_api_key)


def client() -> OpenAI:
    """Build the client once and reuse it; it holds a connection pool."""
    global _client
    if _client is None:
        # Retries stay at the SDK default (2 quick retries on connection errors and 5xx).
        _client = OpenAI(base_url=settings.llm_base_url, api_key=settings.llm_api_key, timeout=TIMEOUT_SECONDS)
    return _client


def chat(messages: list[dict], max_tokens: int = 600, temperature: float = 0.2) -> tuple[str, int, int]:
    """Send a chat completion and return (text, input_tokens, output_tokens).

    The token counts come from the endpoint's own `usage` block, so metering matches the bill.
    Any SDK exception (timeout, 4xx, 5xx, unreachable host) propagates; the caller records the
    failure with record_usage() and turns it into an HTTP error.
    """
    response = client().chat.completions.create(
        model=settings.llm_model,
        messages=messages,
        max_tokens=max_tokens,
        temperature=temperature,
    )
    text = (response.choices[0].message.content or "").strip()
    usage = response.usage
    input_tokens = usage.prompt_tokens if usage else 0
    output_tokens = usage.completion_tokens if usage else 0
    return text, input_tokens, output_tokens
