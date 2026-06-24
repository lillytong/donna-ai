"""Return contract for the shared LLM wrapper (services/llm.py).

`CompletionResult` is what every AI surface gets back from `complete`: the model's
text plus the token usage (also logged per-call, CLAUDE.md). Usage fields are
optional because not every provider/response populates them."""

from __future__ import annotations

from pydantic import BaseModel


class TokenUsage(BaseModel):
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None


class CompletionResult(BaseModel):
    text: str
    usage: TokenUsage
