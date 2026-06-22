"""LiteLLM wrapper — the single LLM abstraction (CLAUDE.md). All AI surfaces call
through here so model selection (by consequence tier, DD-35) and the mandatory
per-call log (model, tokens, latency, caller) live in one place."""

from __future__ import annotations

import time
from typing import Any, Literal

import litellm
import structlog

from backend.config.settings import get_settings

log = structlog.get_logger()

Tier = Literal["high", "medium", "low"]


async def complete(
    *,
    tier: Tier,
    messages: list[dict[str, str]],
    caller: str,
    max_tokens: int = 1024,
) -> str:
    """Run a completion at the given consequence tier; return the message content.

    The model for each tier comes from config (DD-35) — never hardcoded here."""
    settings = get_settings()
    model = getattr(settings.models, tier)

    start = time.perf_counter()
    resp: Any = await litellm.acompletion(
        model=model,
        messages=messages,
        max_tokens=max_tokens,
        api_key=settings.anthropic_api_key or None,
    )
    latency_s = round(time.perf_counter() - start, 3)

    usage = getattr(resp, "usage", None)
    log.info(
        "llm_call",
        caller=caller,
        model=model,
        tier=tier,
        prompt_tokens=getattr(usage, "prompt_tokens", None),
        completion_tokens=getattr(usage, "completion_tokens", None),
        total_tokens=getattr(usage, "total_tokens", None),
        latency_s=latency_s,
    )
    content: str = resp.choices[0].message.content
    return content
