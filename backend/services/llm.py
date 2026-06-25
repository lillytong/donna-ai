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

# Transient failure types — safe to retry with exponential backoff. Timeouts and
# connection/5xx server errors are not the caller's fault; the provider may succeed
# on a fresh attempt. Resolved from litellm at import (its type stub omits these
# names, so getattr + an isinstance(type) guard keeps mypy honest and the tuple sane
# even if a future litellm renames one).
_TRANSIENT_TYPES: tuple[type[Exception], ...] = tuple(
    t
    for t in (
        getattr(litellm, "Timeout", None),
        getattr(litellm, "APIConnectionError", None),
        getattr(litellm, "ServiceUnavailableError", None),
        getattr(litellm, "InternalServerError", None),
    )
    if isinstance(t, type)
)


class LLMRateLimitError(RuntimeError):
    """Provider rate-limited us and every retry also failed. Callers map this
    to a clean 429 rather than letting it surface as an unhandled 500."""


def _is_transient(exc: BaseException) -> bool:
    """True for retryable failures: known transient types, or any error carrying a
    5xx status. NON-transient errors (bad request, auth, context-length) return False
    and are surfaced immediately. Rate limits are handled on their own path."""
    if isinstance(exc, _TRANSIENT_TYPES):
        return True
    status = getattr(exc, "status_code", None)
    return isinstance(status, int) and status >= 500


def _retry_delay(
    attempt: int, exc: BaseException, backoff_base_s: float, timeout_s: float
) -> float:
    """Exponential backoff for the given 0-based attempt, honouring a provider
    `retry_after` hint when present, capped at the per-attempt timeout."""
    delay = backoff_base_s * (2**attempt)
    retry_after = getattr(exc, "retry_after", None)
    if isinstance(retry_after, int | float) and retry_after > 0:
        delay = max(delay, float(retry_after))
    return float(min(delay, timeout_s))


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

    # Retry transient failures (timeouts, rate limits, 5xx/connection) with exponential
    # backoff; knobs are config, not hardcoded (DD-35). Non-transient errors (bad request,
    # auth, context-length) are surfaced on the first attempt. Rate limits, once retries are
    # exhausted, become LLMRateLimitError so the routes' 429 mapping stays correct.
    max_retries = settings.llm.llm_max_retries
    backoff_base_s = settings.llm.llm_backoff_base_s
    timeout_s = settings.llm.timeout_s

    start = time.perf_counter()
    attempt = 0
    while True:
        try:
            resp: Any = await _once()
            break
        except _RateLimitError as exc:
            if attempt >= max_retries:
                raise LLMRateLimitError(str(exc)) from exc
            delay = _retry_delay(attempt, exc, backoff_base_s, timeout_s)
            err_name = type(exc).__name__
        except Exception as exc:
            if not _is_transient(exc) or attempt >= max_retries:
                raise
            delay = _retry_delay(attempt, exc, backoff_base_s, timeout_s)
            err_name = type(exc).__name__
        log.warning(
            "llm_retry",
            caller=caller,
            model=model,
            tier=tier,
            attempt=attempt + 1,
            max_retries=max_retries,
            delay_s=delay,
            error=err_name,
        )
        await asyncio.sleep(delay)
        attempt += 1
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
