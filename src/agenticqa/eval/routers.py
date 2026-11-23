"""Routers used only by the evaluation.

``OracleRouter`` returns the ground-truth routing for each scenario (its labelled
``expected_repo_kind``). Running the corpus with it measures what the *layers*
catch independent of how well any router decides - layer necessity (E1) - while
runs with a real router measure routing quality separately.
"""

from __future__ import annotations

from ..models.enums import LayerId, RepoKind
from ..models.pyramid import LayerRouting, RoutingDecision
from ..models.run import QARun
from .corpus import Scenario


class OracleRouter:
    def __init__(self, scenarios: list[Scenario]) -> None:
        self._kind = {f"eval-{s.name}": s.expected_repo_kind for s in scenarios}

    async def route(self, run: QARun) -> RoutingDecision:
        agentic = self._kind[run.id] == "agentic"
        layers = {
            layer: LayerRouting(
                applicable=agentic or not layer.agentic_only,
                reason="ground-truth routing (evaluation oracle)",
            )
            for layer in LayerId.ordered()
        }
        return RoutingDecision(
            repo_kind=RepoKind.AGENTIC if agentic else RepoKind.PLAIN_SERVICE,
            layers=layers,
            summary="Ground-truth routing (evaluation oracle).",
        )
