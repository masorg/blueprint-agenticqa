"""OpenAI-backed LLMClient.

Primary provider, and also the Cloudera AI Inference path (OpenAI-compatible) -
just set `base_url`. Structured calls prefer native structured outputs
(`chat.completions.parse` with a pydantic schema, guaranteed adherence); if the
endpoint doesn't support them (some compatible servers only do JSON mode), it
falls back to JSON mode plus pydantic validation with one corrective retry. The
synchronous SDK is run in a worker thread so the event loop keeps serving SSE.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from typing import TypeVar

from openai import OpenAI
from pydantic import BaseModel, ValidationError

from .base import LLMError

log = logging.getLogger("agenticqa.llm")

M = TypeVar("M", bound=BaseModel)


def _strip_fences(text: str) -> str:
    """Remove ```json … ``` fences a model may wrap JSON in."""
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[-1] if "\n" in t else t[3:]
        if t.rstrip().endswith("```"):
            t = t.rstrip()[:-3]
    return t.strip()


class OpenAIClient:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str = "",
        timeout: float = 40.0,
        max_retries: int = 1,
        transport_retries: int = 4,
    ) -> None:
        # The SDK retries 429/5xx/connection errors with exponential backoff (and
        # honors Retry-After). Without it a rate limit surfaces as an LLMError, the
        # touch-point falls back to its heuristic, and that is easy to mistake for
        # the model's own answer.
        kwargs: dict = {"api_key": api_key, "timeout": timeout, "max_retries": transport_retries}
        if base_url:
            kwargs["base_url"] = base_url
        self._client = OpenAI(**kwargs)
        self._model = model
        self._max_retries = max_retries
        # Optimistic: try native structured outputs once, then remember if the
        # endpoint doesn't support them and stay on the JSON-mode path.
        self._supports_parse = True
        # Token usage from the most recent call, if the endpoint reported one.
        # Purely observational (e.g. for the eval harness) - never read internally.
        self.last_usage: dict[str, int] | None = None
        # Cumulative over the client's lifetime, for the eval harness (which
        # snapshots it before/after a scenario). `last_usage` alone is wrong for
        # that: one pipeline run makes several calls and it keeps only the last.
        # `calls` counts logical structured()/complete() invocations; `errors`
        # counts those that raised LLMError (the caller then falls back to a
        # heuristic, which must not be mistaken for the model agreeing with it).
        # Tokens include every API request, retries and rejected attempts too,
        # since those are billed.
        self.totals: dict[str, int] = {
            "calls": 0, "errors": 0,
            "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
        }
        self._totals_lock = threading.Lock()
        # Model ids the API reports answering (an alias like "mistral-large-latest"
        # resolves to a dated id that can change under us - evals record it).
        self.response_models: set[str] = set()

    def _record_usage(self, comp) -> None:
        reported = getattr(comp, "model", None)
        if isinstance(reported, str) and reported:
            with self._totals_lock:
                self.response_models.add(reported)
        usage = getattr(comp, "usage", None)
        if usage is None:
            return
        self.last_usage = {
            "prompt_tokens": getattr(usage, "prompt_tokens", 0) or 0,
            "completion_tokens": getattr(usage, "completion_tokens", 0) or 0,
            "total_tokens": getattr(usage, "total_tokens", 0) or 0,
        }
        with self._totals_lock:
            for k, v in self.last_usage.items():
                self.totals[k] += v

    def _count(self, *, error: bool) -> None:
        with self._totals_lock:
            self.totals["calls"] += 1
            if error:
                self.totals["errors"] += 1

    # ---- structured ----------------------------------------------------

    async def structured(
        self, *, system: str, user: str, schema: type[M], temperature: float = 0.0
    ) -> M:
        try:
            result = await asyncio.to_thread(self._structured_sync, system, user, schema, temperature)
        except LLMError:
            self._count(error=True)
            raise
        self._count(error=False)
        return result

    def _structured_sync(self, system: str, user: str, schema: type[M], temperature: float) -> M:
        # Reset per call: a caller reading `last_usage` after this call raises
        # must see "no usage from this call" (None), never a previous call's
        # stale token counts misattributed to it.
        self.last_usage = None
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]

        if self._supports_parse:
            try:
                parse = getattr(self._client.chat.completions, "parse", None) or \
                    self._client.beta.chat.completions.parse
                comp = parse(
                    model=self._model,
                    messages=messages,
                    response_format=schema,
                    temperature=temperature,
                )
                self._record_usage(comp)
                parsed = comp.choices[0].message.parsed
                if parsed is not None:
                    return parsed
            except Exception as exc:  # noqa: BLE001 - any failure → JSON-mode fallback
                log.info("native structured output unavailable (%s); using JSON mode", exc)
                self._supports_parse = False

        # JSON-mode fallback + manual validation, with corrective retries.
        schema_hint = json.dumps(schema.model_json_schema())
        convo = messages + [
            {
                "role": "system",
                "content": (
                    "Reply with a SINGLE JSON object that validates against this JSON "
                    "Schema. No prose, no markdown fences.\n" + schema_hint
                ),
            }
        ]
        last_err: Exception | None = None
        for _ in range(self._max_retries + 1):
            try:
                comp = self._client.chat.completions.create(
                    model=self._model,
                    messages=convo,
                    temperature=temperature,
                    response_format={"type": "json_object"},
                )
                self._record_usage(comp)  # billed even if the reply fails validation below
                content = comp.choices[0].message.content or ""
                return schema.model_validate_json(_strip_fences(content))
            except (ValidationError, json.JSONDecodeError, ValueError) as exc:
                last_err = exc
                convo.append(
                    {
                        "role": "system",
                        "content": f"Your previous reply did not validate ({exc}). "
                        "Return only a valid JSON object.",
                    }
                )
            except Exception as exc:  # noqa: BLE001 - network/auth/etc.
                raise LLMError(f"OpenAI request failed: {exc}") from exc

        raise LLMError(f"structured output did not validate after retries: {last_err}")

    # ---- free text -----------------------------------------------------

    async def complete(self, *, system: str, user: str, temperature: float = 0.2) -> str:
        try:
            result = await asyncio.to_thread(self._complete_sync, system, user, temperature)
        except LLMError:
            self._count(error=True)
            raise
        self._count(error=False)
        return result

    def _complete_sync(self, system: str, user: str, temperature: float) -> str:
        self.last_usage = None  # see _structured_sync: reset per call, never stale
        try:
            comp = self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=temperature,
            )
            self._record_usage(comp)
            return (comp.choices[0].message.content or "").strip()
        except Exception as exc:  # noqa: BLE001
            raise LLMError(f"OpenAI request failed: {exc}") from exc
