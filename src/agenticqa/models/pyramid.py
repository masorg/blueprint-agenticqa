"""Models for test discovery, classification, and per-layer planning."""

from __future__ import annotations

from pydantic import BaseModel, Field

from .enums import LayerId, RepoKind


class DiscoveredTest(BaseModel):
    """A single test found in the target repo (framework-agnostic node id)."""

    node_id: str
    file: str = ""
    name: str = ""
    markers: list[str] = Field(default_factory=list)


class TestClassification(BaseModel):
    """A test's assigned pyramid layer, plus the confidence/verification signal.

    `heuristic_layer` is what our deterministic rules picked; `layer` is the
    final assignment (LLM in prod, heuristic in Phase 1). `agreement` is whether
    they matched; `verified` is False when confidence is low or they disagree,
    which surfaces a badge in the UI. A test is still run either way.
    """

    node_id: str
    layer: LayerId
    confidence: float = 1.0
    rationale: str = ""
    heuristic_layer: LayerId | None = None
    agreement: bool = True
    verified: bool = True


class LayerPlan(BaseModel):
    """What a single pyramid layer will do for this run."""

    layer: LayerId
    applicable: bool
    reason: str = ""
    tests: list[str] = Field(default_factory=list)  # node ids

    @property
    def planned_count(self) -> int:
        return len(self.tests)


class LayerRouting(BaseModel):
    applicable: bool
    reason: str = ""
    # True when the deterministic guardrail overrode what the model actually
    # returned for this layer (e.g. the model said an agentic-only layer
    # applies to a plain service, and the guardrail forced it off). Lets the
    # eval harness measure whether the guardrail is load-bearing rather than
    # decorative. Always False for the heuristic router (nothing to override).
    guardrail_overridden: bool = False


class RoutingDecision(BaseModel):
    """The router's read of the change: is the SUT agentic, and which layers apply."""

    repo_kind: RepoKind = RepoKind.PLAIN_SERVICE
    layers: dict[LayerId, LayerRouting] = Field(default_factory=dict)
    summary: str = ""

    def applies(self, layer: LayerId) -> bool:
        routing = self.layers.get(layer)
        return bool(routing and routing.applicable)
