"""`OpenAIClient.last_usage` must never leak a previous call's token counts
onto a call that itself produced none (raised, or the SDK reported no usage).

Real bug found by /code-review (2026-09-24): `last_usage` was only ever SET
on a successful path and never reset, so `eval/metrics.py`'s cost/latency
aggregation could attribute a prior successful call's tokens to a later
failed one that fell back to the heuristic path.
"""

from __future__ import annotations

import pytest

from agenticqa.llm.base import LLMError
from agenticqa.llm.openai_client import OpenAIClient


class _Usage:
    def __init__(self, prompt=10, completion=5, total=15):
        self.prompt_tokens = prompt
        self.completion_tokens = completion
        self.total_tokens = total


class _Message:
    def __init__(self, content: str):
        self.content = content


class _Choice:
    def __init__(self, content: str):
        self.message = _Message(content)


class _Completion:
    def __init__(self, content: str, usage: _Usage | None):
        self.choices = [_Choice(content)]
        self.usage = usage


def _client() -> OpenAIClient:
    # Constructing the SDK client does no network I/O - it just stores config.
    return OpenAIClient(api_key="test-key", model="gpt-4o", timeout=1, max_retries=0)


async def test_successful_call_records_usage():
    client = _client()
    client._client.chat.completions.create = lambda **kw: _Completion("hello", _Usage(total=42))

    result = await client.complete(system="s", user="u")

    assert result == "hello"
    assert client.last_usage == {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 42}


async def test_failed_call_after_a_successful_one_clears_last_usage():
    client = _client()

    client._client.chat.completions.create = lambda **kw: _Completion("hello", _Usage(total=42))
    await client.complete(system="s", user="u")
    assert client.last_usage is not None  # sanity: the first call did record usage

    def _boom(**kw):
        raise RuntimeError("network blip")

    client._client.chat.completions.create = _boom
    with pytest.raises(LLMError):
        await client.complete(system="s", user="u")

    assert client.last_usage is None, "a failed call must not report the previous call's tokens"


async def test_call_with_no_usage_reported_clears_previous_usage():
    client = _client()

    client._client.chat.completions.create = lambda **kw: _Completion("hello", _Usage(total=42))
    await client.complete(system="s", user="u")
    assert client.last_usage is not None

    # Some OpenAI-compatible endpoints omit `usage` entirely.
    client._client.chat.completions.create = lambda **kw: _Completion("world", usage=None)
    result = await client.complete(system="s", user="u")

    assert result == "world"
    assert client.last_usage is None, "no usage reported this call - must not keep the stale one"


# ---- cumulative totals (eval harness cost accounting) -----------------------
#
# `last_usage` keeps only the final call, but one pipeline run makes several
# (route, classify, triage). The harness once reported just that last call as
# the scenario's cost, undercounting tokens; `totals` is the cumulative source.


async def test_totals_accumulate_across_calls():
    client = _client()
    client._client.chat.completions.create = lambda **kw: _Completion("hi", _Usage(10, 5, 15))

    await client.complete(system="s", user="u")
    await client.complete(system="s", user="u")

    assert client.totals == {
        "calls": 2, "errors": 0, "prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30,
    }
    assert client.last_usage["total_tokens"] == 15  # last_usage is still just the final call


async def test_failed_call_is_counted_as_an_error():
    client = _client()

    def _boom(**kw):
        raise RuntimeError("network blip")

    client._client.chat.completions.create = _boom
    with pytest.raises(LLMError):
        await client.complete(system="s", user="u")

    assert client.totals["calls"] == 1 and client.totals["errors"] == 1


async def test_rejected_structured_attempt_still_counts_its_tokens():
    """A reply that fails schema validation is retried; both requests are billed."""
    from pydantic import BaseModel

    class _Out(BaseModel):
        x: int

    client = OpenAIClient(api_key="k", model="m", timeout=1, max_retries=1)
    client._supports_parse = False  # force the JSON-mode path
    replies = iter(["not json", '{"x": 1}'])
    client._client.chat.completions.create = lambda **kw: _Completion(next(replies), _Usage(10, 5, 15))

    out = await client.structured(system="s", user="u", schema=_Out)

    assert out.x == 1
    assert client.totals["calls"] == 1  # one logical call...
    assert client.totals["total_tokens"] == 30  # ...two billed requests
    assert client.totals["errors"] == 0


def test_rate_limits_are_retried_with_backoff_at_the_http_layer():
    """REGRESSION: transport retries were forced to 0, so a 429 became an LLMError, the
    touch-point silently fell back to its heuristic, and a 'model' result was really the
    heuristic's. The SDK now retries 429/5xx with backoff (honoring Retry-After)."""
    assert OpenAIClient(api_key="k", model="m", timeout=1)._client.max_retries == 4
    assert OpenAIClient(api_key="k", model="m", timeout=1, transport_retries=0)._client.max_retries == 0
