"""LiteLLM wrapper — the single LLM abstraction (CLAUDE.md). All AI surfaces call
through here so model selection (by consequence tier, DD-35), the per-call timeout,
and the mandatory log (model, tokens, latency, caller) live in one place."""

from __future__ import annotations

import asyncio
import time
from typing import Any, Literal

import litellm
import structlog

from backend.config.settings import get_settings
from backend.models.llm import CompletionResult, TokenUsage

log = structlog.get_logger()

# litellm's type stub omits RateLimitError from its exports; it is a real runtime
# attribute (re-exported from the provider SDK). Alias it once with a concrete type.
_RateLimitError: type[Exception] = litellm.RateLimitError  # type: ignore[attr-defined]

Tier = Literal["high", "medium", "low"]


class LLMRateLimitError(RuntimeError):
    """Provider rate-limited us and the single retry also failed. Callers map this
    to a clean 429 rather than letting it surface as an unhandled 500."""


async def complete(
    *,
    tier: Tier,
    messages: list[dict[str, Any]],
    caller: str,
    max_tokens: int = 1024,
    temperature: float = 0.0,
    json_response: bool = False,
) -> CompletionResult:
    """Run a completion at the given consequence tier; return its text + usage.

    The model for each tier comes from config (DD-35); temperature/max_tokens are
    the caller's (also config-sourced) and the timeout is config's — none hardcoded.
    `json_response=True` asks the model for a strict JSON object. `messages` content
    may be a plain string or a list of content blocks (e.g. a cache_control prefix)."""
    settings = get_settings()
    model = f"anthropic/{getattr(settings.models, tier)}"

    kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "timeout": settings.llm.timeout_s,
        "api_key": settings.anthropic_api_key or None,
    }
    if json_response:
        kwargs["response_format"] = {"type": "json_object"}

    async def _once() -> Any:
        return await litellm.acompletion(**kwargs)

    start = time.perf_counter()
    try:
        resp: Any = await _once()
    except _RateLimitError as exc:
        # NOTE: single retry only, respecting Retry-After when the provider sends it.
        # Full retry/backoff policy is parked — Phase 2 DEV_TODO "LLM retry/backoff".
        retry_after = getattr(exc, "retry_after", None)
        if isinstance(retry_after, int | float) and retry_after > 0:
            await asyncio.sleep(min(float(retry_after), settings.llm.timeout_s))
        try:
            resp = await _once()
        except _RateLimitError as exc2:
            raise LLMRateLimitError(str(exc2)) from exc2
    except Exception:
        resp = await _once()
    latency_ms = round((time.perf_counter() - start) * 1000, 1)

    raw_usage = getattr(resp, "usage", None)
    usage = TokenUsage(
        prompt_tokens=getattr(raw_usage, "prompt_tokens", None),
        completion_tokens=getattr(raw_usage, "completion_tokens", None),
        total_tokens=getattr(raw_usage, "total_tokens", None),
    )
    log.info(
        "llm_call",
        caller=caller,
        model=model,
        tier=tier,
        prompt_tokens=usage.prompt_tokens,
        completion_tokens=usage.completion_tokens,
        total_tokens=usage.total_tokens,
        cache_read_input_tokens=getattr(raw_usage, "cache_read_input_tokens", None),
        latency_ms=latency_ms,
    )
    text: str = resp.choices[0].message.content
    return CompletionResult(text=text, usage=usage)
