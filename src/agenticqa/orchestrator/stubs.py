"""Deterministic stand-ins for the three LLM touch-points (Phase 2).

These implement the Router/Classifier/Triage interfaces with explainable rules
instead of a model, so the whole pipeline - pyramid, live tallies, verdict gate,
approval - can be demoed end to end with zero LLM or network dependency. Phase 3
swaps these for real LLM-backed implementations behind the same interfaces.
"""

from __future__ import annotations

from ..classify import heuristic_layer
from ..models.enums import LayerId, RepoKind
from ..models.pyramid import (
    DiscoveredTest,
    LayerRouting,
    RoutingDecision,
    TestClassification,
)
from ..models.run import QARun

# Cues in a diff that suggest the system under test is itself agentic/LLM-based.
_AGENTIC_CUES = (
    "anthropic",
    "openai",
    "langchain",
    "langgraph",
    "llamaindex",
    "autogen",
    "crewai",
    "def agent",
    "class agent",
    "system_prompt",
    "tool_use",
    "trajectory",
)


class HeuristicRouter:
    """Reads the diff for agentic cues; defaults to a plain service.

    Layer applicability: unit / property / adversarial always apply; the two
    agentic-only layers (trajectory, LLM-as-judge) apply only when the SUT looks
    agentic. Non-applicable layers render dark in the pyramid.
    """

    async def route(self, run: QARun) -> RoutingDecision:
        haystack = (run.diff or "").lower()
        matched = [cue for cue in _AGENTIC_CUES if cue in haystack]
        is_agentic = bool(matched)
        kind = RepoKind.AGENTIC if is_agentic else RepoKind.PLAIN_SERVICE

        layers: dict[LayerId, LayerRouting] = {}
        for layer in LayerId.ordered():
            if not layer.agentic_only:
                layers[layer] = LayerRouting(
                    applicable=True, reason="Applies to every system under test."
                )
            elif is_agentic:
                layers[layer] = LayerRouting(
                    applicable=True,
                    reason=f"System under test looks agentic (cues: {', '.join(matched[:3])}).",
                )
            else:
                layers[layer] = LayerRouting(
                    applicable=False,
                    reason="Agentic-only layer; the target is a plain service.",
                )

        if is_agentic:
            summary = "Routed as an agentic system - all five layers apply."
        else:
            summary = (
                "Routed as a plain service - unit, property and adversarial layers "
                "apply; trajectory and LLM-as-judge stay dark."
            )
        return RoutingDecision(repo_kind=kind, layers=layers, summary=summary)


class HeuristicClassifier:
    """Assigns each test its heuristic layer with full confidence (self-agreeing)."""

    async def classify(
        self, run: QARun, tests: list[DiscoveredTest]
    ) -> list[TestClassification]:
        out: list[TestClassification] = []
        for test in tests:
            layer = heuristic_layer(test)
            out.append(
                TestClassification(
                    node_id=test.node_id,
                    layer=layer,
                    confidence=1.0,
                    rationale=f"Heuristic cues mapped this test to the {layer.label} layer.",
                    heuristic_layer=layer,
                    agreement=True,
                    verified=True,
                )
            )
        return out


class HeuristicTriage:
    """Builds a plain-language summary from the layer results (no model)."""

    async def triage(self, run: QARun) -> str:
        lines: list[str] = []
        total_pass = total_fail = total_skip = 0
        failing: list[str] = []

        for layer in LayerId.ordered():
            lr = run.layer_runs.get(layer)
            if lr is None:
                continue
            if not run.layer_active(layer):
                continue
            total_pass += lr.passed
            total_fail += lr.failed
            total_skip += lr.skipped
            if lr.planned_count == 0:
                lines.append(f"- **{layer.label}**: no tests classified here.")
                continue
            status = "✓ passed" if lr.failed == 0 and not lr.error else "✗ failed"
            lines.append(
                f"- **{layer.label}**: {status} - {lr.passed}/{lr.planned_count} passed"
                + (f", {lr.failed} failed" if lr.failed else "")
                + (f", {lr.skipped} skipped" if lr.skipped else "")
            )
            for r in lr.results:
                if r.outcome.value in ("failed", "error"):
                    failing.append(r.node_id)

        header = (
            f"Ran {total_pass + total_fail + total_skip} tests across the applicable "
            f"layers: **{total_pass} passed**, **{total_fail} failed**, {total_skip} skipped."
        )
        body = "\n".join(lines)
        if failing:
            tail = "\n\n**Failing tests requiring attention:**\n" + "\n".join(
                f"- `{nid}`" for nid in failing
            )
        else:
            tail = "\n\nAll applicable layers are green."
        return f"{header}\n\n{body}{tail}"


class HeuristicAdvisor:
    """Adds tests whose file or name shares a word with a changed source file (no model).

    It is a second, independent way to find related tests when the import analysis misses a link,
    for example a test that exercises the code through a subprocess or a plugin."""

    async def advise(self, run: QARun, tests: list[DiscoveredTest], core: list[str]) -> list[str]:
        import re

        stems = set()
        for path in (run.selection.changed if run.selection else []):
            name = path.rsplit("/", 1)[-1]
            if name.endswith(".py") and not name.startswith(("test_", "conftest")):
                stem = name[:-3].lower()
                if len(stem) >= 4 and stem != "__init__":
                    stems.add(stem)
        if not stems:
            return []
        out = []
        for t in tests:
            hay = re.sub(r"[^a-z0-9]+", "", f"{t.file} {t.name}".lower())
            if any(re.sub(r"[^a-z0-9]+", "", s) in hay for s in stems):
                out.append(t.node_id)
        return out
