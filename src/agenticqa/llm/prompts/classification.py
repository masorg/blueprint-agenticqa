"""Classification prompt + schema: assign each discovered test a pyramid layer.

The LLMClassifier cross-checks each returned layer against the deterministic
`heuristic_layer` to set the agreement/verified badges the UI shows; any test the
model omits falls back to its heuristic layer, so every discovered test is
classified exactly once.
"""

from __future__ import annotations

import json

from pydantic import BaseModel, Field

from ...models.pyramid import DiscoveredTest
from .routing import LayerLit


class TestVerdict(BaseModel):
    node_id: str
    layer: LayerLit
    confidence: float = Field(ge=0.0, le=1.0, description="0-1 confidence in the layer.")
    rationale: str = Field(description="One short clause on why this layer.")


class ClassificationResponse(BaseModel):
    tests: list[TestVerdict]


SYSTEM = (
    "You are the CLASSIFICATION stage of an automated software-QA pyramid. Assign "
    "each test to exactly one of five layers, using the node id, file, test name, "
    "and pytest markers as evidence.\n\n"
    "Layer definitions:\n"
    "- unit: a single function/class/endpoint in isolation. The default.\n"
    "- property: property-based / invariant tests (Hypothesis `@given`, "
    "'invariant', 'property' in the name).\n"
    "- trajectory: checks an agent's tool-call ordering or a multi-step trace.\n"
    "- llm_judge: scores output quality against a rubric, often via an LLM judge.\n"
    "- adversarial: security, safety, fuzzing, prompt-injection, hostile input.\n\n"
    "Return one entry per test id you are given, each with a 0-1 confidence and a "
    "one-clause rationale. Do not invent test ids."
)


def build_classification_prompt(tests: list[DiscoveredTest]) -> tuple[str, str]:
    """Return (system, user) messages for the classification call."""
    rows = [
        {
            "node_id": t.node_id,
            "file": t.file,
            "name": t.name,
            "markers": t.markers,
        }
        for t in tests
    ]
    user = (
        "Classify these tests. Reply with one verdict per node_id.\n\n"
        + json.dumps(rows, indent=2)
    )
    return SYSTEM, user
