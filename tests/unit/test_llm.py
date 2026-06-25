"""Retry/backoff policy for the shared LLM wrapper (services/llm.py).

Mocks the underlying `litellm.acompletion` and `asyncio.sleep` so the tests assert
the retry *policy* (which errors retry, how many times, the backoff schedule) without
real network calls or real waiting. Settings are stubbed so the knobs (DD-35) are
controlled per-test rather than read from .env."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import litellm
import pytest
from backend.models.llm import CompletionResult
from backend.services import llm as llm_mod
from backend.services.llm import LLMRateLimitError, complete


def _fake_settings(
    *, max_retries: int = 2, backoff_base_s: float = 0.5, timeout_s: float = 30.0
) -> Any:
    return SimpleNamespace(
        models=SimpleNamespace(high="m-high", medium="m-medium", low="m-low"),
        llm=SimpleNamespace(
            llm_max_retries=max_retries,
            llm_backoff_base_s=backoff_base_s,
            timeout_s=timeout_s,
        ),
        anthropic_api_key="test-key",
    )


def _fake_response() -> Any:
    usage = SimpleNamespace(prompt_tokens=3, completion_tokens=5, total_tokens=8)
    message = SimpleNamespace(content="hello")
    return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=usage)


def _timeout() -> Exception:
    return litellm.Timeout(message="timed out", model="m-high", llm_provider="anthropic")


def _rate_limit() -> Exception:
    return litellm.RateLimitError(message="429", llm_provider="anthropic", model="m-high")


def _server_error() -> Exception:
    return litellm.InternalServerError(message="500", llm_provider="anthropic", model="m-high")


def _bad_request() -> Exception:
    return litellm.BadRequestError(message="bad", model="m-high", llm_provider="anthropic")


@pytest.fixture(autouse=True)
def _patch_env(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Stub settings, record (don't perform) sleeps. Returns the sleep-delay log."""
    monkeypatch.setattr(llm_mod, "get_settings", _fake_settings)
    sleeps: list[float] = []

    async def _fake_sleep(d: float) -> None:
        sleeps.append(d)

    monkeypatch.setattr(llm_mod.asyncio, "sleep", _fake_sleep)
    return sleeps


def _install_acompletion(
    monkeypatch: pytest.MonkeyPatch, *, raises: list[Exception], then: Any
) -> dict[str, int]:
    """Raise each queued exception in turn, then return `then`. Records call count."""
    state = {"calls": 0}
    queue = list(raises)

    async def _acompletion(**_kwargs: Any) -> Any:
        state["calls"] += 1
        if queue:
            raise queue.pop(0)
        return then

    monkeypatch.setattr(llm_mod.litellm, "acompletion", _acompletion)
    return state


async def _call() -> CompletionResult:
    return await complete(tier="high", messages=[{"role": "user", "content": "hi"}], caller="test")


async def test_transient_error_retries_then_succeeds(
    monkeypatch: pytest.MonkeyPatch, _patch_env: list[float]
) -> None:
    state = _install_acompletion(monkeypatch, raises=[_timeout()], then=_fake_response())
    result = await _call()
    assert result.text == "hello"
    assert state["calls"] == 2  # one failure + one success
    assert len(_patch_env) == 1  # backed off once


async def test_rate_limit_retries_exhausted_raises(
    monkeypatch: pytest.MonkeyPatch, _patch_env: list[float]
) -> None:
    # 3 calls = initial + 2 retries (max_retries default), all rate-limited.
    state = _install_acompletion(
        monkeypatch, raises=[_rate_limit(), _rate_limit(), _rate_limit()], then=_fake_response()
    )
    with pytest.raises(LLMRateLimitError):
        await _call()
    assert state["calls"] == 3
    assert len(_patch_env) == 2  # backed off after attempts 0 and 1, not after the last


async def test_server_error_exhausted_raises_original(
    monkeypatch: pytest.MonkeyPatch, _patch_env: list[float]
) -> None:
    _install_acompletion(
        monkeypatch,
        raises=[_server_error(), _server_error(), _server_error()],
        then=_fake_response(),
    )
    with pytest.raises(litellm.InternalServerError):
        await _call()
    assert len(_patch_env) == 2


async def test_non_transient_error_not_retried(
    monkeypatch: pytest.MonkeyPatch, _patch_env: list[float]
) -> None:
    state = _install_acompletion(monkeypatch, raises=[_bad_request()], then=_fake_response())
    with pytest.raises(litellm.BadRequestError):
        await _call()
    assert state["calls"] == 1  # surfaced immediately, no retry
    assert _patch_env == []  # no backoff


async def test_backoff_is_exponential(
    monkeypatch: pytest.MonkeyPatch, _patch_env: list[float]
) -> None:
    # backoff_base 0.5 → delays 0.5 * 2**0, 0.5 * 2**1 = [0.5, 1.0].
    _install_acompletion(
        monkeypatch, raises=[_server_error(), _server_error()], then=_fake_response()
    )
    result = await _call()
    assert result.text == "hello"
    assert _patch_env == [0.5, 1.0]


async def test_happy_path_no_sleep_first_try(
    monkeypatch: pytest.MonkeyPatch, _patch_env: list[float]
) -> None:
    state = _install_acompletion(monkeypatch, raises=[], then=_fake_response())
    result = await _call()
    assert result.text == "hello"
    assert result.usage.total_tokens == 8
    assert state["calls"] == 1
    assert _patch_env == []  # happy path never backs off
