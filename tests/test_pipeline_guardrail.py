"""The deterministic verdict guardrail in Pipeline._triage_and_gate.

A layer that is FAILED/ERROR and *active* (routed applicable, or it actually ran
tests via the coverage fallback) must force NEEDS_HUMAN_REVIEW. A layer that is
routed out and ran nothing must not. Routing alone never hides a failure.

We drive `_triage_and_gate` directly against a hand-built `QARun` with
`layer_runs` set, using fake Router/Classifier/Triage/Executor implementations
of the Protocols in orchestrator/interfaces.py -- this is the smallest seam
that reaches the guardrail without running a full pipeline.
"""

from __future__ import annotations

from agenticqa.events.bus import EventBus
from agenticqa.models.enums import ApprovalState, LayerId, LayerRunState, RunState, Verdict
from agenticqa.models.pyramid import LayerRouting, RoutingDecision
from agenticqa.models.pyramid import TestClassification as _TestClassification
from agenticqa.models.run import LayerRun, QARun
from agenticqa.orchestrator.pipeline import Pipeline, _bucket_with_fallback, _build_layer_plans
from agenticqa.store.db import RunRepository


class _FakeTriage:
    async def triage(self, run: QARun) -> str:
        return "narrative"


def _routing(applicable: dict[LayerId, bool]) -> RoutingDecision:
    layers = {layer: LayerRouting(applicable=applicable.get(layer, True)) for layer in LayerId.ordered()}
    return RoutingDecision(layers=layers)


def _make_pipeline() -> Pipeline:
    repo = RunRepository()
    bus = EventBus(repo)
    # router/classifier/executor are never exercised by _triage_and_gate; None
    # would work at runtime since the method never calls them, but keep the
    # dataclass honest with lightweight placeholders.
    return Pipeline(repo=repo, bus=bus, executor=None, router=None, classifier=None, triage=_FakeTriage())


def _run(routing: RoutingDecision, layer_runs: dict[LayerId, LayerRun]) -> QARun:
    return QARun(id="run1", source="local", routing=routing, layer_runs=layer_runs)


async def test_failed_layer_that_ran_triggers_review_even_if_routing_marked_it_inapplicable():
    """REGRESSION (found by the worst-case-router experiment, 2026-10-13).

    The gate used to skip any layer the router marked inapplicable. But the
    coverage fallback deliberately runs tests in such a layer, so a router that
    marked unit/property/adversarial inapplicable made a run with failing tests
    auto-approve. A layer that executed tests counts, whatever routing said.
    """
    routing = _routing({LayerId.UNIT: False, LayerId.PROPERTY: False, LayerId.ADVERSARIAL: False})
    layer_runs = {
        LayerId.UNIT: LayerRun(layer=LayerId.UNIT, state=LayerRunState.FAILED, planned_count=18, passed=16, failed=2),
    }
    run = _run(routing, layer_runs)
    pipeline = _make_pipeline()

    await pipeline._triage_and_gate(run)

    assert run.verdict == Verdict.NEEDS_HUMAN_REVIEW, run.verdict
    assert run.state == RunState.AWAITING_APPROVAL, run.state


async def test_inapplicable_layer_that_never_ran_does_not_trigger_review():
    """A layer routed out that received no tests is not 'active'; whatever
    state it carries must not force review."""
    routing = _routing({LayerId.TRAJECTORY: False})
    layer_runs = {
        LayerId.UNIT: LayerRun(layer=LayerId.UNIT, state=LayerRunState.PASSED, planned_count=1, passed=1),
        LayerId.TRAJECTORY: LayerRun(layer=LayerId.TRAJECTORY, state=LayerRunState.SKIPPED, planned_count=0),
    }
    run = _run(routing, layer_runs)
    pipeline = _make_pipeline()

    await pipeline._triage_and_gate(run)

    assert run.verdict == Verdict.AUTO_APPROVE, run.verdict
    assert run.state == RunState.DONE, run.state


def test_layer_active_means_routed_or_actually_ran():
    routing = _routing({LayerId.UNIT: False, LayerId.PROPERTY: False})
    run = _run(routing, {
        LayerId.UNIT: LayerRun(layer=LayerId.UNIT, state=LayerRunState.PASSED, planned_count=3, passed=3),
        LayerId.PROPERTY: LayerRun(layer=LayerId.PROPERTY, state=LayerRunState.PENDING, planned_count=0),
    })
    assert run.layer_active(LayerId.UNIT)           # not routed, but it ran tests
    assert not run.layer_active(LayerId.PROPERTY)   # not routed, ran nothing
    assert run.layer_active(LayerId.ADVERSARIAL)    # routed applicable


def test_triage_prompt_does_not_hide_a_failing_fallback_layer():
    """The model-written narrative must not call a layer that ran and failed
    'not applicable (dark)'."""
    from agenticqa.llm.prompts.triage import build_triage_prompt

    routing = _routing({LayerId.UNIT: False})
    run = _run(routing, {
        LayerId.UNIT: LayerRun(layer=LayerId.UNIT, state=LayerRunState.FAILED, planned_count=2, passed=1, failed=1),
    })
    _, user = build_triage_prompt(run)
    unit_line = next(ln for ln in user.splitlines() if ln.startswith("- Unit"))
    assert "not applicable" not in unit_line and "1 failed" in unit_line


async def test_failed_applicable_layer_triggers_review():
    """A FAILED layer that IS applicable must force NEEDS_HUMAN_REVIEW."""
    routing = _routing({LayerId.UNIT: True})
    layer_runs = {
        LayerId.UNIT: LayerRun(layer=LayerId.UNIT, state=LayerRunState.FAILED, planned_count=1, failed=1),
    }
    run = _run(routing, layer_runs)
    pipeline = _make_pipeline()

    await pipeline._triage_and_gate(run)

    assert run.verdict == Verdict.NEEDS_HUMAN_REVIEW, run.verdict
    assert run.state == RunState.AWAITING_APPROVAL, run.state
    assert run.approval.state == ApprovalState.PENDING


async def test_error_state_also_triggers_review_when_applicable():
    routing = _routing({LayerId.PROPERTY: True})
    layer_runs = {
        LayerId.PROPERTY: LayerRun(layer=LayerId.PROPERTY, state=LayerRunState.ERROR, planned_count=1, error="boom"),
    }
    run = _run(routing, layer_runs)
    pipeline = _make_pipeline()

    await pipeline._triage_and_gate(run)

    assert run.verdict == Verdict.NEEDS_HUMAN_REVIEW
    assert run.state == RunState.AWAITING_APPROVAL


async def test_no_routing_treats_every_layer_as_applicable():
    """With run.routing is None, the `if run.routing and not applies` guard
    never fires -- every layer's failure counts."""
    layer_runs = {
        LayerId.UNIT: LayerRun(layer=LayerId.UNIT, state=LayerRunState.FAILED, planned_count=1, failed=1),
    }
    run = QARun(id="run2", source="local", routing=None, layer_runs=layer_runs)
    pipeline = _make_pipeline()

    await pipeline._triage_and_gate(run)

    assert run.verdict == Verdict.NEEDS_HUMAN_REVIEW


# ---- _bucket_with_fallback ---------------------------------------------------
#
# Real bug found by the eval harness's first live-LLM run (2026-09-23,
# docs/13-evaluation-harness.md): a test classified into a layer this run
# doesn't route to (e.g. `trajectory` on a `plain_service`) was silently
# dropped -- by_layer[layer] existed, but `_plan` never included it in any
# LayerPlan.tests because the destination layer itself was inapplicable. It
# was classified, but never executed. `_bucket_with_fallback` is the fix:
# demote such a test to its heuristic layer (or UNIT) instead of dropping it.


def _cls(node_id: str, layer: LayerId, heuristic_layer: LayerId | None = None) -> _TestClassification:
    return _TestClassification(
        node_id=node_id, layer=layer, heuristic_layer=heuristic_layer or layer
    )


def test_test_classified_into_inapplicable_layer_is_demoted_not_dropped():
    routing = _routing({LayerId.TRAJECTORY: False})  # plain_service-style routing
    classifications = [
        _cls("test_a", layer=LayerId.TRAJECTORY, heuristic_layer=LayerId.UNIT),
    ]
    by_layer, demoted = _bucket_with_fallback(classifications, routing)

    assert demoted == 1
    assert by_layer[LayerId.TRAJECTORY] == [], "must not stay in the inapplicable layer"
    assert by_layer[LayerId.UNIT] == ["test_a"], "must fall back to its heuristic layer"


def test_demotion_falls_back_to_unit_when_heuristic_layer_also_inapplicable():
    routing = _routing({LayerId.TRAJECTORY: False, LayerId.LLM_JUDGE: False})
    classifications = [
        # Both the model's pick AND the heuristic pick are agentic-only layers
        # this run doesn't route to -- must still land somewhere applicable.
        _cls("test_b", layer=LayerId.TRAJECTORY, heuristic_layer=LayerId.LLM_JUDGE),
    ]
    by_layer, demoted = _bucket_with_fallback(classifications, routing)

    assert demoted == 1
    assert by_layer[LayerId.UNIT] == ["test_b"]
    assert by_layer[LayerId.TRAJECTORY] == []
    assert by_layer[LayerId.LLM_JUDGE] == []


def test_test_in_an_applicable_layer_is_not_touched():
    routing = _routing({LayerId.UNIT: True})
    classifications = [_cls("test_c", layer=LayerId.UNIT)]
    by_layer, demoted = _bucket_with_fallback(classifications, routing)

    assert demoted == 0
    assert by_layer[LayerId.UNIT] == ["test_c"]


def test_no_routing_never_demotes():
    classifications = [_cls("test_d", layer=LayerId.TRAJECTORY)]
    by_layer, demoted = _bucket_with_fallback(classifications, routing=None)

    assert demoted == 0
    assert by_layer[LayerId.TRAJECTORY] == ["test_d"]


# ---- _build_layer_plans ------------------------------------------------------
#
# Real bug found by /code-review (2026-09-24): `_bucket_with_fallback` can
# land a demoted test on UNIT without checking whether THIS run's routing
# marked UNIT itself inapplicable (nothing guarantees UNIT is always
# applicable - only the two agentic-only layers have a guardrail). The old
# `_plan` loop then re-gated `node_ids` on that same `applicable` flag
# (`by_layer.get(layer, []) if applicable else []`), so the demoted test was
# silently dropped anyway - directly contradicting the RUN_LOG message
# claiming it was "ran... instead of skipping". `_build_layer_plans` fixes
# this: a layer that received any tests always runs them, regardless of what
# routing said about that layer.


def test_demoted_test_still_runs_even_if_fallback_layer_itself_is_inapplicable():
    # UNIT explicitly marked inapplicable (e.g. a docs-only-PR routing
    # response) while a test gets classified into TRAJECTORY, which this run
    # also doesn't route to, and has no heuristic_layer to fall back to
    # either - so `_bucket_with_fallback` lands it on UNIT regardless.
    routing = _routing({LayerId.UNIT: False, LayerId.TRAJECTORY: False})
    classifications = [_cls("test_e", layer=LayerId.TRAJECTORY, heuristic_layer=None)]
    by_layer, demoted = _bucket_with_fallback(classifications, routing)
    assert demoted == 1
    assert by_layer[LayerId.UNIT] == ["test_e"], "sanity: fallback landed on UNIT as expected"

    plans = _build_layer_plans(by_layer, routing)
    unit_plan = next(p for p in plans if p.layer == LayerId.UNIT)
    assert unit_plan.tests == ["test_e"], "the demoted test must still be planned, not dropped"
    assert unit_plan.applicable is True, "a layer holding tests must run them regardless of the raw routing flag"
    assert unit_plan.planned_count == 1


def test_layer_with_no_tests_keeps_routings_applicable_flag():
    routing = _routing({LayerId.UNIT: False})
    plans = _build_layer_plans({layer: [] for layer in LayerId.ordered()}, routing)
    unit_plan = next(p for p in plans if p.layer == LayerId.UNIT)
    assert unit_plan.applicable is False, "no tests landed here, so the routing decision stands"
    assert unit_plan.tests == []


def test_layer_applicable_by_routing_is_unaffected_by_build_layer_plans():
    routing = _routing({LayerId.UNIT: True})
    by_layer = {layer: [] for layer in LayerId.ordered()}
    by_layer[LayerId.UNIT] = ["test_f"]
    plans = _build_layer_plans(by_layer, routing)
    unit_plan = next(p for p in plans if p.layer == LayerId.UNIT)
    assert unit_plan.applicable is True
    assert unit_plan.tests == ["test_f"]


# ---- _reconcile_classifications ----------------------------------------------
#
# The pipeline must not trust the classifier to be complete (found 2026-10-13
# while building the worst-case adversary experiment): `_plan` used to plan
# only what the classifier returned, so a classifier that omitted a test
# silently dropped it. The built-in LLM classifier fills its own gaps, but the
# Classifier interface is the trust boundary, so the pipeline enforces it.

from agenticqa.models.pyramid import DiscoveredTest as _DiscoveredTest  # noqa: E402
from agenticqa.orchestrator.pipeline import _reconcile_classifications  # noqa: E402


def _t(node_id: str) -> _DiscoveredTest:
    return _DiscoveredTest(node_id=node_id, file=node_id.split("::")[0], name=node_id.split("::")[-1])


def test_reconcile_fills_in_tests_the_classifier_omitted():
    tests = [_t("tests/test_a.py::test_one"), _t("tests/test_properties.py::test_two")]
    out, added, discarded = _reconcile_classifications(tests, [])
    assert [c.node_id for c in out] == [t.node_id for t in tests]
    assert (added, discarded) == (2, 0)
    assert out[1].layer == LayerId.PROPERTY  # heuristic layer from the filename
    assert all(not c.verified for c in out)


def test_reconcile_discards_unknown_and_duplicate_classifications():
    tests = [_t("tests/test_a.py::test_one")]
    cs = [
        _cls("tests/test_a.py::test_one", LayerId.TRAJECTORY),
        _cls("tests/test_a.py::test_one", LayerId.ADVERSARIAL),  # duplicate: first wins
        _cls("tests/ghost.py::test_invented", LayerId.UNIT),     # not a discovered test
    ]
    out, added, discarded = _reconcile_classifications(tests, cs)
    assert [(c.node_id, c.layer) for c in out] == [("tests/test_a.py::test_one", LayerId.TRAJECTORY)]
    assert (added, discarded) == (0, 2)


def test_reconcile_is_a_noop_for_a_well_formed_classifier():
    tests = [_t("tests/test_a.py::test_one"), _t("tests/test_b.py::test_two")]
    cs = [_cls(t.node_id, LayerId.UNIT) for t in tests]
    out, added, discarded = _reconcile_classifications(tests, cs)
    assert out == cs and (added, discarded) == (0, 0)
