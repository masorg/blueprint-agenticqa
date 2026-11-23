"""Sample runs used to populate the platform on first boot.

These are static, fully-formed QARun records (no execution) so the Overview,
Pull Requests, Review Queue and Blocked views have realistic content the moment
the app opens. The bundled demo (the "New review" button) runs for real and
animates the pyramid live; these are the surrounding history. When a durable
store replaces the in-memory one, seeding simply moves behind an "is empty" check.
"""

from __future__ import annotations

from datetime import timedelta

from ..models.enums import (
    ApprovalState,
    ApprovalSource,
    LayerId,
    LayerRunState,
    RepoKind,
    RunState,
    TestOutcome,
    Verdict,
)
from ..models.pyramid import LayerRouting, RoutingDecision
from ..models.run import Approval, LayerRun, PRRef, QARun, RepoRef, TestResult, _now


def _routing(agentic: bool) -> RoutingDecision:
    layers: dict[LayerId, LayerRouting] = {}
    for layer in LayerId.ordered():
        if not layer.agentic_only:
            layers[layer] = LayerRouting(applicable=True, reason="Applies to every system under test.")
        elif agentic:
            layers[layer] = LayerRouting(applicable=True, reason="System under test is agentic.")
        else:
            layers[layer] = LayerRouting(applicable=False, reason="Agentic-only layer; the target is a plain service.")
    summary = (
        "Routed as an agentic system - all five layers apply."
        if agentic
        else "Routed as a plain service - unit, property and adversarial layers apply."
    )
    return RoutingDecision(
        repo_kind=RepoKind.AGENTIC if agentic else RepoKind.PLAIN_SERVICE,
        layers=layers,
        summary=summary,
    )


def _layer(layer: LayerId, passed: list[str], failed: list[str], applicable: bool = True) -> LayerRun:
    results = [TestResult(node_id=n, outcome=TestOutcome.PASSED, duration=0.01) for n in passed]
    results += [
        TestResult(node_id=n, outcome=TestOutcome.FAILED, duration=0.02, message="AssertionError")
        for n in failed
    ]
    if not applicable:
        state = LayerRunState.PENDING
    elif failed:
        state = LayerRunState.FAILED
    elif not passed:
        state = LayerRunState.SKIPPED
    else:
        state = LayerRunState.PASSED
    return LayerRun(
        layer=layer,
        state=state,
        planned_count=len(passed) + len(failed),
        passed=len(passed),
        failed=len(failed),
        results=results,
    )


def _triage(totals: dict, ok: bool) -> str:
    head = (
        f"Ran {totals['total']} tests across the applicable layers: "
        f"**{totals['passed']} passed**, **{totals['failed']} failed**."
    )
    tail = "All applicable layers are green." if ok else "One or more layers failed and need review."
    return f"{head}\n\n{tail}"


def sample_runs() -> list[QARun]:
    now = _now()
    runs: list[QARun] = []

    # 1) Agentic repo, all five layers, auto-approved - showcases agentic-only layers.
    r1 = QARun(
        id="a1b2c3d4e5f6",
        source="github",
        repo=RepoRef(owner="acme", name="recommendations-agent"),
        pr=PRRef(number=214, title="Add re-ranking tool to recommender agent", head_sha="9f3ac71bce22"),
        state=RunState.DONE,
        verdict=Verdict.AUTO_APPROVE,
        approval=Approval(state=ApprovalState.NOT_REQUIRED),
        routing=_routing(agentic=True),
        layer_runs={
            LayerId.UNIT: _layer(LayerId.UNIT, ["test_rank_scores", "test_dedupe", "test_format_output", "test_cold_start"], []),
            LayerId.PROPERTY: _layer(LayerId.PROPERTY, ["test_prop_scores_monotonic", "test_prop_no_dupes"], []),
            LayerId.TRAJECTORY: _layer(LayerId.TRAJECTORY, ["test_calls_search_before_rank", "test_stops_after_answer"], []),
            LayerId.LLM_JUDGE: _layer(LayerId.LLM_JUDGE, ["test_relevance_rubric", "test_no_hallucinated_items"], []),
            LayerId.ADVERSARIAL: _layer(LayerId.ADVERSARIAL, ["test_prompt_injection", "test_pii_refusal", "test_jailbreak_resist"], []),
        },
        created_at=now - timedelta(hours=5),
        updated_at=now - timedelta(hours=5, minutes=-3),
    )
    r1.triage_report = _triage(r1.totals, ok=True)
    runs.append(r1)

    # 2) Plain service, awaiting human review (2 failures) - the Review Queue item.
    r2 = QARun(
        id="b2c3d4e5f6a1",
        source="github",
        repo=RepoRef(owner="acme", name="inventory-orders-api"),
        pr=PRRef(number=131, title="Optimize stock check in order creation", head_sha="4d1e88a0f7bb"),
        state=RunState.AWAITING_APPROVAL,
        verdict=Verdict.NEEDS_HUMAN_REVIEW,
        approval=Approval(state=ApprovalState.PENDING),
        routing=_routing(agentic=False),
        layer_runs={
            LayerId.UNIT: _layer(LayerId.UNIT, ["test_add_item", "test_get_item", "test_create_order", "test_partial_order", "test_zero_stock", "test_missing_sku"], ["test_order_exact_remaining_stock_succeeds"]),
            LayerId.PROPERTY: _layer(LayerId.PROPERTY, ["test_prop_stock_never_negative", "test_prop_order_totals", "test_prop_idempotent_reads"], ["test_property_any_valid_order_must_succeed"]),
            LayerId.TRAJECTORY: _layer(LayerId.TRAJECTORY, [], [], applicable=False),
            LayerId.LLM_JUDGE: _layer(LayerId.LLM_JUDGE, [], [], applicable=False),
            LayerId.ADVERSARIAL: _layer(LayerId.ADVERSARIAL, ["test_sql_injection_sku", "test_negative_quantity", "test_overflow_quantity", "test_unknown_item_404"], []),
        },
        created_at=now - timedelta(minutes=42),
        updated_at=now - timedelta(minutes=39),
    )
    r2.triage_report = _triage(r2.totals, ok=False)
    runs.append(r2)

    # 3) Plain service, passed then human-approved.
    r3 = QARun(
        id="c3d4e5f6a1b2",
        source="github",
        repo=RepoRef(owner="acme", name="auth-service"),
        pr=PRRef(number=97, title="Rotate session token signing key", head_sha="7bb2c4419de0"),
        state=RunState.DONE,
        verdict=Verdict.NEEDS_HUMAN_REVIEW,
        approval=Approval(state=ApprovalState.APPROVED, source=ApprovalSource.IN_APP, actor="priya", at=now - timedelta(hours=2)),
        routing=_routing(agentic=False),
        layer_runs={
            LayerId.UNIT: _layer(LayerId.UNIT, ["test_sign", "test_verify", "test_expiry", "test_rotation"], []),
            LayerId.PROPERTY: _layer(LayerId.PROPERTY, ["test_prop_roundtrip", "test_prop_tamper_detected"], []),
            LayerId.TRAJECTORY: _layer(LayerId.TRAJECTORY, [], [], applicable=False),
            LayerId.LLM_JUDGE: _layer(LayerId.LLM_JUDGE, [], [], applicable=False),
            LayerId.ADVERSARIAL: _layer(LayerId.ADVERSARIAL, ["test_replay_attack", "test_forged_signature", "test_expired_token"], []),
        },
        created_at=now - timedelta(hours=3),
        updated_at=now - timedelta(hours=2),
    )
    r3.triage_report = _triage(r3.totals, ok=True)
    runs.append(r3)

    # 4) Blocked - the run failed to execute (dependency install error).
    r4 = QARun(
        id="d4e5f6a1b2c3",
        source="github",
        repo=RepoRef(owner="acme", name="billing-api"),
        pr=PRRef(number=58, title="Upgrade payment SDK to v4", head_sha="2ac9f0e13d55"),
        state=RunState.FAILED,
        error="RuntimeError: command failed (1): pip install -r requirements.txt - could not resolve payments-sdk==4.0.0",
        routing=_routing(agentic=False),
        created_at=now - timedelta(hours=1, minutes=10),
        updated_at=now - timedelta(hours=1, minutes=8),
    )
    runs.append(r4)

    # 5) Plain service, clean auto-approve.
    r5 = QARun(
        id="e5f6a1b2c3d4",
        source="github",
        repo=RepoRef(owner="acme", name="search-service"),
        pr=PRRef(number=143, title="Add fuzzy matching to product search", head_sha="ee01b7cc9a34"),
        state=RunState.DONE,
        verdict=Verdict.AUTO_APPROVE,
        approval=Approval(state=ApprovalState.NOT_REQUIRED),
        routing=_routing(agentic=False),
        layer_runs={
            LayerId.UNIT: _layer(LayerId.UNIT, ["test_tokenize", "test_fuzzy_match", "test_rank", "test_empty_query", "test_unicode"], []),
            LayerId.PROPERTY: _layer(LayerId.PROPERTY, ["test_prop_match_symmetry", "test_prop_score_bounds"], []),
            LayerId.TRAJECTORY: _layer(LayerId.TRAJECTORY, [], [], applicable=False),
            LayerId.LLM_JUDGE: _layer(LayerId.LLM_JUDGE, [], [], applicable=False),
            LayerId.ADVERSARIAL: _layer(LayerId.ADVERSARIAL, ["test_regex_dos", "test_injection", "test_huge_input"], []),
        },
        created_at=now - timedelta(minutes=18),
        updated_at=now - timedelta(minutes=16),
    )
    r5.triage_report = _triage(r5.totals, ok=True)
    runs.append(r5)

    return runs
