"""The LLM abstraction the orchestrator depends on.

`structured` returns a validated pydantic model; `complete` returns free text.
Implementations wrap synchronous vendor SDKs in a worker thread so the async
pipeline is never blocked. Errors surface as `LLMError`, which the LLM-backed
touch-points catch to fall back to the deterministic heuristics - a model hiccup
must never fail a run.
"""

from __future__ import annotations

from typing import Protocol, TypeVar

from pydantic import BaseModel

M = TypeVar("M", bound=BaseModel)


class LLMError(RuntimeError):
    """Raised when an LLM call fails or its output can't be validated."""


class LLMClient(Protocol):
    async def structured(
        self, *, system: str, user: str, schema: type[M], temperature: float = 0.0
    ) -> M:
        """Return an instance of `schema`, validated. Raises LLMError on failure."""
        ...

    async def complete(self, *, system: str, user: str, temperature: float = 0.2) -> str:
        """Return the model's free-text reply. Raises LLMError on failure."""
        ...
