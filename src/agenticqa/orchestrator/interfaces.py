"""The three LLM touch-points, as interfaces.

In Phase 2 these are backed by deterministic heuristic stubs (see ``stubs.py``);
in Phase 3 the same interfaces are implemented against a real LLM. Keeping them
abstract means the engine never learns which one it's talking to.
"""

from __future__ import annotations

from typing import Protocol

from ..models.pyramid import DiscoveredTest, RoutingDecision, TestClassification
from ..models.run import QARun


class Router(Protocol):
    """Decides whether the SUT is agentic and which pyramid layers apply."""

    async def route(self, run: QARun) -> RoutingDecision: ...


class Classifier(Protocol):
    """Assigns each discovered test to a pyramid layer."""

    async def classify(
        self, run: QARun, tests: list[DiscoveredTest]
    ) -> list[TestClassification]: ...


class TestAdvisor(Protocol):
    """Names tests that should run in addition to the deterministic core of the selection.

    It can only add: the pipeline keeps every core test and ignores ids that were not discovered."""

    async def advise(self, run: QARun, tests: list[DiscoveredTest], core: list[str]) -> list[str]: ...


class Triage(Protocol):
    """Turns the finished layer results into a human-readable narrative."""

    async def triage(self, run: QARun) -> str: ...
