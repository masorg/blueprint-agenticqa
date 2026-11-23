"""LLM-backed Router / Classifier / Triage (Phase 3).

Each implements the same interface as its Phase-2 heuristic stub and wraps that
stub as a fallback: if the model call fails (or returns nothing usable) the
touch-point degrades to the deterministic heuristic and logs it, so a model
hiccup never fails a run. Two invariants are enforced in code, not left to the
model:

* the routing guardrail - agentic-only layers can never apply to a plain
  service, regardless of what the model returns;
* every discovered test is classified exactly once - a test the model omits
  falls back to its heuristic layer.

The final approve / needs-review verdict is *not* here - the pipeline computes
it deterministically from the layer results.
"""

from __future__ import annotations

import logging

from ..classify import heuristic_layer
from ..llm.base import LLMClient, LLMError
from ..llm.prompts import (
    SelectionResponse,
    build_selection_prompt,
    ClassificationResponse,
    RoutingResponse,
    build_classification_prompt,
    build_routing_prompt,
    build_triage_prompt,
)
from ..models.enums import LayerId, RepoKind
from ..models.pyramid import (
    DiscoveredTest,
    LayerRouting,
    RoutingDecision,
    TestClassification,
)
from ..models.run import QARun
from .interfaces import Classifier, Router, TestAdvisor, Triage
from .stubs import HeuristicAdvisor, HeuristicClassifier, HeuristicRouter, HeuristicTriage

log = logging.getLogger("agenticqa.llm")

# Below this the LLM's layer pick is shown as "unverified" in the UI (still run).
_CONFIDENCE_FLOOR = 0.6


class LLMRouter:
    """Routes via the LLM, then re-applies the agentic-only guardrail in code."""

    def __init__(self, client: LLMClient, *, fallback: Router | None = None) -> None:
        self._client = client
        self._fallback = fallback or HeuristicRouter()

    async def route(self, run: QARun) -> RoutingDecision:
        system, user = build_routing_prompt(run)
        try:
            resp = await self._client.structured(
                system=system, user=user, schema=RoutingResponse
            )
        except LLMError as exc:
            log.warning("LLM routing failed (%s); using heuristic router", exc)
            return await self._fallback.route(run)
        return _to_decision(resp)


class LLMClassifier:
    """Classifies via the LLM; cross-checks each pick against the heuristic."""

    def __init__(self, client: LLMClient, *, fallback: Classifier | None = None) -> None:
        self._client = client
        self._fallback = fallback or HeuristicClassifier()

    async def classify(
        self, run: QARun, tests: list[DiscoveredTest]
    ) -> list[TestClassification]:
        if not tests:
            return []
        system, user = build_classification_prompt(tests)
        try:
            resp = await self._client.structured(
                system=system, user=user, schema=ClassificationResponse
            )
        except LLMError as exc:
            log.warning("LLM classification failed (%s); using heuristic classifier", exc)
            return await self._fallback.classify(run, tests)
        return _merge_classifications(tests, resp)


class LLMTriage:
    """Writes the triage narrative via the LLM (verdict stays deterministic)."""

    def __init__(self, client: LLMClient, *, fallback: Triage | None = None) -> None:
        self._client = client
        self._fallback = fallback or HeuristicTriage()

    async def triage(self, run: QARun) -> str:
        system, user = build_triage_prompt(run)
        try:
            report = await self._client.complete(system=system, user=user, temperature=0.2)
        except LLMError as exc:
            log.warning("LLM triage failed (%s); using heuristic triage", exc)
            return await self._fallback.triage(run)
        if not report.strip():
            return await self._fallback.triage(run)
        return report.strip()


# ---- response → model mapping ------------------------------------------------


def _to_decision(resp: RoutingResponse) -> RoutingDecision:
    kind = RepoKind.AGENTIC if resp.repo_kind == "agentic" else RepoKind.PLAIN_SERVICE
    is_agentic = kind == RepoKind.AGENTIC
    by_layer = {v.layer: v for v in resp.layers}

    layers: dict[LayerId, LayerRouting] = {}
    for layer in LayerId.ordered():
        # Guardrail: agentic-only layers can never apply to a plain service.
        if layer.agentic_only and not is_agentic:
            raw = by_layer.get(layer.value)
            overridden = bool(raw is not None and raw.applicable)
            layers[layer] = LayerRouting(
                applicable=False,
                reason="Agentic-only layer; the target is a plain service.",
                guardrail_overridden=overridden,
            )
            continue
        v = by_layer.get(layer.value)
        if v is not None:
            layers[layer] = LayerRouting(
                applicable=bool(v.applicable),
                reason=(v.reason or "").strip(),
            )
        else:
            # Model omitted this layer; be inclusive (bias-to-applicable rule).
            layers[layer] = LayerRouting(
                applicable=True,
                reason="Included by default (the model did not rule on this layer).",
            )
    return RoutingDecision(repo_kind=kind, layers=layers, summary=(resp.summary or "").strip())


def _merge_classifications(
    tests: list[DiscoveredTest], resp: ClassificationResponse
) -> list[TestClassification]:
    verdicts = {v.node_id: v for v in resp.tests}
    out: list[TestClassification] = []
    for test in tests:
        heuristic = heuristic_layer(test)
        v = verdicts.get(test.node_id)
        if v is None:
            # Model omitted this test - fall back to its heuristic layer, flagged.
            out.append(
                TestClassification(
                    node_id=test.node_id,
                    layer=heuristic,
                    confidence=0.4,
                    rationale="Not returned by the model; fell back to the heuristic layer.",
                    heuristic_layer=heuristic,
                    agreement=True,
                    verified=False,
                )
            )
            continue
        layer = LayerId(v.layer)
        agreement = layer == heuristic
        out.append(
            TestClassification(
                node_id=test.node_id,
                layer=layer,
                confidence=v.confidence,
                rationale=(v.rationale or "").strip(),
                heuristic_layer=heuristic,
                agreement=agreement,
                verified=agreement and v.confidence >= _CONFIDENCE_FLOOR,
            )
        )
    return out


class LLMTestAdvisor:
    """Names extra tests via the LLM. Its answer is advice: the pipeline can only add it to the core."""

    def __init__(self, client: LLMClient, *, fallback: TestAdvisor | None = None) -> None:
        self._client = client
        self._fallback = fallback or HeuristicAdvisor()

    async def advise(self, run: QARun, tests: list[DiscoveredTest], core: list[str]) -> list[str]:
        system, user = build_selection_prompt(run, tests, core)
        try:
            resp = await self._client.structured(system=system, user=user, schema=SelectionResponse)
        except LLMError as exc:
            log.warning("LLM test advice failed (%s); using heuristic advisor", exc)
            return await self._fallback.advise(run, tests, core)
        return list(resp.node_ids)
