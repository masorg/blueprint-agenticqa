"""Pick an LLMClient from settings.

Returns ``None`` for the ``stub`` provider (or when a required key is missing),
which tells ``main.py`` to keep the deterministic heuristic touch-points. Today
``openai`` is implemented (and covers Cloudera AI Inference via ``openai_base_url``);
``mistral`` rides the same OpenAI-compatible client against Mistral's endpoint;
``anthropic`` is reserved for a later provider and falls through to ``None`` for now.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from ..config import Settings
from .base import LLMClient

log = logging.getLogger("agenticqa.llm")


@dataclass(frozen=True)
class LLMTarget:
    """The endpoint a provider name resolves to. ``model`` is what actually gets
    called, so an eval report labels itself from this, never from a guess."""

    provider: str
    api_key: str
    base_url: str
    model: str


def resolve_target(settings: Settings) -> LLMTarget | None:
    """Map ``settings.llm_provider`` to its credentials/endpoint/model, or
    ``None`` if the provider is ``stub`` or unknown."""
    provider = (settings.llm_provider or "stub").lower()

    if provider == "openai":
        return LLMTarget(provider, settings.openai_api_key, settings.openai_base_url, settings.openai_model)
    if provider == "cloudera":
        # Cloudera AI Inference reuses the OpenAI wire protocol via base_url.
        return LLMTarget(
            provider,
            settings.cloudera_ai_api_key or settings.openai_api_key,
            settings.cloudera_ai_endpoint or settings.openai_base_url,
            settings.cloudera_model or settings.openai_model,
        )
    if provider == "mistral":
        return LLMTarget(provider, settings.mistral_api_key, settings.mistral_base_url, settings.mistral_model)
    return None


def build_client(settings: Settings) -> LLMClient | None:
    provider = (settings.llm_provider or "stub").lower()

    if provider == "stub":
        return None

    target = resolve_target(settings)
    if target is None:
        log.warning("Unknown llm_provider=%s; falling back to heuristic stubs.", provider)
        return None

    if not target.api_key:
        log.warning(
            "llm_provider=%s but no API key set; falling back to heuristic stubs.",
            provider,
        )
        return None

    from .openai_client import OpenAIClient

    return OpenAIClient(
        api_key=target.api_key,
        model=target.model,
        base_url=target.base_url,
        timeout=settings.llm_timeout_seconds,
        max_retries=settings.llm_max_retries,
        transport_retries=settings.llm_transport_retries,
    )
