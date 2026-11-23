"""LLM client abstraction and providers (Phase 3).

The orchestrator's three touch-points (route / classify / triage) talk to an
``LLMClient`` - never to a vendor SDK directly. ``factory.build_client`` picks the
implementation from settings; ``openai`` is the primary provider and also covers
Cloudera AI Inference (OpenAI-compatible) via a ``base_url``.
"""

from .base import LLMClient, LLMError

__all__ = ["LLMClient", "LLMError"]
