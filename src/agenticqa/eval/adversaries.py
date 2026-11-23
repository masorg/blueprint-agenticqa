"""Worst-case adversary experiment: can routing or classification change the verdict?

The paper's central claim is that the verdict is computed deterministically
from what actually ran, so no router/classifier output - however wrong or
hostile - can turn a failing change into an approved one or make a test
disappear. LLM runs can only show that *typical* models don't do this. This
module bounds the claim directly: it drives the real ``Pipeline`` with
deliberately hostile touch-points and checks three invariants against the
honest baseline of the same scenario:

  I1  same verdict
  I2  same number of failing tests
  I3  every test executed (same total as the baseline)

Routers are enumerated exhaustively (all 2^5 on/off combinations of the five
layers), including combinations the production guardrail would reject, so the
pipeline's own safety is tested and not merely the guardrail's. No LLM is used,
so the experiment is free and deterministic.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from pathlib import Path

from ..classify import heuristic_layer
from ..models.enums import LayerId, RepoKind
from ..models.pyramid import DiscoveredTest, LayerRouting, RoutingDecision, TestClassification
from ..models.run import QARun
from ..orchestrator.stubs import HeuristicClassifier, HeuristicRouter, HeuristicTriage
from .corpus import Scenario
from .harness import run_scenario


class BaselineFailed(Exception):
    """The honest run did not produce a usable baseline (it could not build, or ran no tests).

    Without a baseline, every hostile run would fail the same way and the invariants would
    'hold' trivially. That must be reported as 'could not run', never as a pass."""


def check_baseline(verdict: str | None, total: int, state: str, error: str) -> None:
    if verdict is None or total == 0:
        raise BaselineFailed(f"state={state}, tests run={total}: {error.strip()[:400] or 'no error recorded'}")


class FixedRouter:
    """Returns a fixed applicability set, ignoring the change entirely."""

    def __init__(self, applicable: frozenset[LayerId]) -> None:
        self._applicable = applicable

    async def route(self, run: QARun) -> RoutingDecision:
        layers = {
            layer: LayerRouting(applicable=layer in self._applicable, reason="adversary")
            for layer in LayerId.ordered()
        }
        return RoutingDecision(repo_kind=RepoKind.PLAIN_SERVICE, layers=layers, summary="adversary")


def router_name(applicable: frozenset[LayerId]) -> str:
    on = [layer.value for layer in LayerId.ordered() if layer in applicable]
    return "on=" + ("+".join(on) if on else "none")


def all_router_subsets() -> list[frozenset[LayerId]]:
    layers = LayerId.ordered()
    return [
        frozenset(c)
        for r in range(len(layers) + 1)
        for c in itertools.combinations(layers, r)
    ]


def _cls(t: DiscoveredTest, layer: LayerId, *, hint: LayerId | None) -> TestClassification:
    return TestClassification(
        node_id=t.node_id, layer=layer, confidence=1.0, rationale="adversary",
        heuristic_layer=hint, agreement=False, verified=False,
    )


class _ClassifierAdversary:
    name = "adversary"

    async def classify(self, run: QARun, tests: list[DiscoveredTest]) -> list[TestClassification]:
        raise NotImplementedError


class OmitAll(_ClassifierAdversary):
    name = "omit-all"

    async def classify(self, run, tests):
        return []


class OmitHalf(_ClassifierAdversary):
    name = "omit-every-other"

    async def classify(self, run, tests):
        return [_cls(t, heuristic_layer(t), hint=heuristic_layer(t)) for t in tests[::2]]


class AllToTrajectory(_ClassifierAdversary):
    """Everything into an agentic-only layer (inapplicable on a plain service)."""

    name = "all-to-trajectory"

    async def classify(self, run, tests):
        return [_cls(t, LayerId.TRAJECTORY, hint=heuristic_layer(t)) for t in tests]


class AllToTrajectoryNoHint(_ClassifierAdversary):
    """As above, but with no heuristic hint to fall back on."""

    name = "all-to-trajectory-no-hint"

    async def classify(self, run, tests):
        return [_cls(t, LayerId.TRAJECTORY, hint=None) for t in tests]


class Rotate(_ClassifierAdversary):
    """Each test into the next layer round-robin, uncorrelated with its content."""

    name = "rotate-layers"

    async def classify(self, run, tests):
        order = LayerId.ordered()
        return [_cls(t, order[i % len(order)], hint=heuristic_layer(t)) for i, t in enumerate(tests)]


class InventAndDuplicate(_ClassifierAdversary):
    """Honest classifications plus duplicates and a test that does not exist."""

    name = "invent-and-duplicate"

    async def classify(self, run, tests):
        honest = [_cls(t, heuristic_layer(t), hint=heuristic_layer(t)) for t in tests]
        ghost = TestClassification(
            node_id="tests/ghost.py::test_invented", layer=LayerId.UNIT, heuristic_layer=LayerId.UNIT
        )
        return honest + honest[:3] + [ghost]


CLASSIFIER_ADVERSARIES: list[type[_ClassifierAdversary]] = [
    OmitAll, OmitHalf, AllToTrajectory, AllToTrajectoryNoHint, Rotate, InventAndDuplicate,
]


@dataclass
class Case:
    scenario: str
    router: str
    classifier: str
    verdict: str | None
    failed: int
    total: int
    expected_verdict: str
    baseline_failed: int
    baseline_total: int

    @property
    def i1_verdict(self) -> bool:
        return self.verdict == self.expected_verdict

    @property
    def i2_failures(self) -> bool:
        return self.failed == self.baseline_failed

    @property
    def i3_coverage(self) -> bool:
        return self.total == self.baseline_total

    @property
    def ok(self) -> bool:
        return self.i1_verdict and self.i2_failures and self.i3_coverage


async def run_adversaries(
    scenarios: list[Scenario], *, repo_root: Path, quick: bool = False, progress=print
) -> list[Case]:
    """Run every (router, classifier) adversary pairing against each scenario.

    Pairings: every router subset with an honest classifier, plus every
    classifier adversary under the all-layers-on and all-layers-off routers
    (``quick`` keeps a handful of each, for development).
    """
    subsets = all_router_subsets()
    all_on, none_on = frozenset(LayerId.ordered()), frozenset()
    if quick:
        subsets = [none_on, frozenset({LayerId.UNIT}), frozenset({LayerId.ADVERSARIAL}), all_on]
    classifier_routers = [none_on, all_on]

    cases: list[Case] = []
    for scenario in scenarios:
        base = await run_scenario(
            scenario, router=HeuristicRouter(), classifier=HeuristicClassifier(),
            triage=HeuristicTriage(), repo_root=repo_root,
        )
        b_failed, b_total = base.run.totals["failed"], base.run.totals["total"]
        # The reference is what the honest pipeline does on this scenario, so the invariants
        # also apply to real suites that have no hand-written label (and may fail at baseline).
        b_verdict = base.run.verdict.value if base.run.verdict else None
        check_baseline(b_verdict, b_total, base.run.state.value, base.run.error)
        progress(f"[{scenario.name}] baseline: verdict={b_verdict} failed={b_failed} total={b_total}")

        pairs: list[tuple[frozenset[LayerId], object]] = [(s, HeuristicClassifier()) for s in subsets]
        pairs += [(r, adv()) for adv in CLASSIFIER_ADVERSARIES for r in classifier_routers]
        for applicable, classifier in pairs:
            r = await run_scenario(
                scenario, router=FixedRouter(applicable), classifier=classifier,
                triage=HeuristicTriage(), repo_root=repo_root,
            )
            case = Case(
                scenario=scenario.name,
                router=router_name(applicable),
                classifier=getattr(classifier, "name", "honest"),
                verdict=r.run.verdict.value if r.run.verdict else None,
                failed=r.run.totals["failed"],
                total=r.run.totals["total"],
                expected_verdict=b_verdict,
                baseline_failed=b_failed,
                baseline_total=b_total,
            )
            cases.append(case)
            if not case.ok:
                progress(f"  VIOLATION {case.router} / {case.classifier}: verdict={case.verdict} "
                         f"failed={case.failed}/{b_failed} total={case.total}/{b_total}")
    return cases


def summarize(cases: list[Case]) -> dict:
    bad = [c for c in cases if not c.ok]
    return {
        "n_cases": len(cases),
        "n_scenarios": len({c.scenario for c in cases}),
        "n_router_only_cases": sum(1 for c in cases if c.classifier == "honest"),
        "n_classifier_adversary_cases": sum(1 for c in cases if c.classifier != "honest"),
        "violations": len(bad),
        "i1_verdict_violations": sum(1 for c in cases if not c.i1_verdict),
        "i2_failure_count_violations": sum(1 for c in cases if not c.i2_failures),
        "i3_coverage_violations": sum(1 for c in cases if not c.i3_coverage),
        "violating_cases": [
            {"scenario": c.scenario, "router": c.router, "classifier": c.classifier,
             "verdict": c.verdict, "failed": c.failed, "total": c.total}
            for c in bad
        ],
    }
