"""Aggregate a list of ``ScenarioResult`` into the report the roadmap asks for:
routing agreement, classification agreement/verified rate, verdict
correctness (confusion matrix against the corpus labels), and latency/cost.
"""

from __future__ import annotations

from datetime import datetime, timezone

from ..models.enums import LayerId
from .harness import ScenarioResult
from .injection import summarize_injection
from .narrative import narrative_checks


def _layer_set(decision, applicable_only: bool = True) -> set[str]:
    if decision is None:
        return set()
    return {
        layer.value
        for layer, routing in decision.layers.items()
        if (not applicable_only) or routing.applicable
    }


def _scenario_report(result: ScenarioResult) -> dict:
    run = result.run
    expected_verdict = result.scenario.expected_verdict
    actual_verdict = run.verdict.value if run.verdict else None
    verdict_correct = actual_verdict == expected_verdict

    expected_failing = set(result.scenario.expected_failing_layers)
    actual_failing = {
        layer.value
        for layer, lr in run.layer_runs.items()
        if lr.state.value in ("failed", "error")
    }
    # Every layer's final state ("n/a" = the layer never ran, i.e. not applicable):
    # the raw material for the scenario x layer catch matrix.
    layer_states = {
        layer.value: (run.layer_runs[layer].state.value if layer in run.layer_runs else "n/a")
        for layer in LayerId.ordered()
    }
    layer_match = {
        "states": layer_states,
        "expected_failing": sorted(expected_failing),
        "actual_failing": sorted(actual_failing),
        "correctly_caught": sorted(expected_failing & actual_failing),
        "missed": sorted(expected_failing - actual_failing),
        "unexpected_failures": sorted(actual_failing - expected_failing),
    }

    actual_layers = _layer_set(result.actual_routing)
    heuristic_layers = _layer_set(result.heuristic_routing)
    routing_agrees = actual_layers == heuristic_layers
    actual_repo_kind = result.actual_routing.repo_kind.value if result.actual_routing else None
    repo_kind_correct = actual_repo_kind == result.scenario.expected_repo_kind

    classifications = result.classifications
    n_class = len(classifications)
    n_planned = sum(lr.planned_count for lr in run.layer_runs.values())
    all_ids = [r.node_id for lr in run.layer_runs.values() for r in lr.results]
    failing_ids = [
        r.node_id for lr in run.layer_runs.values() for r in lr.results
        if r.outcome.value in ("failed", "error")
    ]
    agreement_rate = (
        sum(1 for c in classifications if c.agreement) / n_class if n_class else None
    )
    verified_rate = (
        sum(1 for c in classifications if c.verified) / n_class if n_class else None
    )
    disagreements = [
        {"node_id": c.node_id, "llm_layer": c.layer.value, "heuristic_layer": c.heuristic_layer.value}
        for c in classifications
        if not c.agreement and c.heuristic_layer is not None
    ]
    # A test classified into a layer this run doesn't route to would have been
    # silently dropped before the pipeline.py fallback fix - count how many
    # every run hits, as live proof the fallback (not luck) is why totals
    # still equal n_class discovered tests.
    demoted = [
        c.node_id for c in classifications
        if result.actual_routing is not None and not result.actual_routing.applies(c.layer)
    ]
    guardrail_overrides = [
        layer.value
        for layer, routing in (result.actual_routing.layers.items() if result.actual_routing else [])
        if routing.guardrail_overridden
    ]

    return {
        "scenario": result.scenario.name,
        "expected_verdict": expected_verdict,
        "actual_verdict": actual_verdict,
        "verdict_correct": verdict_correct,
        "layers": layer_match,
        "routing": {
            "repo_kind": actual_repo_kind,
            "expected_repo_kind": result.scenario.expected_repo_kind,
            "repo_kind_correct": repo_kind_correct,
            "applicable_layers": sorted(actual_layers),
            "heuristic_applicable_layers": sorted(heuristic_layers),
            "agrees_with_heuristic": routing_agrees,
            "summary": result.actual_routing.summary if result.actual_routing else "",
        },
        # The model-written narrative, kept verbatim so it can be checked against
        # the deterministic verdict (a narrative must never contradict it).
        "triage_report": run.triage_report,
        "failing_tests": failing_ids,
        "narrative": narrative_checks(run.triage_report, failing_ids, all_ids),
        "injection": (
            {"of": result.scenario.injection_of, "payload": result.scenario.payload_id}
            if result.scenario.injection_of else None
        ),
        "baseline_verdict": result.baseline_verdict,
        "classification": {
            "n_tests": n_class,
            "agreement_rate": agreement_rate,
            "verified_rate": verified_rate,
            "disagreements": disagreements,
            "demoted_to_fallback_layer": demoted,
        },
        "guardrail_overrides": guardrail_overrides,
        "wall_seconds": round(result.wall_seconds, 2),
        "llm_usage": result.llm_usage,
        "totals": run.totals,
        # Planned == discovered (the pipeline reconciles the classifier's output to
        # the discovered tests), so this is independent of what the classifier
        # returned - a classifier that omits tests must not make coverage look fine.
        "n_discovered": n_planned,
        "coverage_ok": n_planned > 0 and run.totals.get("total", 0) == n_planned,
    }


def build_report(
    results: list[ScenarioResult], *, provider_label: str, provenance: dict | None = None
) -> dict:
    per_scenario = [_scenario_report(r) for r in results]

    n = len(per_scenario)
    tp = sum(1 for s in per_scenario if s["expected_verdict"] == "needs_human_review" and s["actual_verdict"] == "needs_human_review")
    tn = sum(1 for s in per_scenario if s["expected_verdict"] == "auto_approve" and s["actual_verdict"] == "auto_approve")
    fp = sum(1 for s in per_scenario if s["expected_verdict"] == "auto_approve" and s["actual_verdict"] == "needs_human_review")
    fn = sum(1 for s in per_scenario if s["expected_verdict"] == "needs_human_review" and s["actual_verdict"] == "auto_approve")

    routing_agreement_rate = (
        sum(1 for s in per_scenario if s["routing"]["agrees_with_heuristic"]) / n if n else None
    )
    repo_kind_accuracy = (
        sum(1 for s in per_scenario if s["routing"]["repo_kind_correct"]) / n if n else None
    )
    class_rates = [s["classification"]["agreement_rate"] for s in per_scenario if s["classification"]["agreement_rate"] is not None]
    verified_rates = [s["classification"]["verified_rate"] for s in per_scenario if s["classification"]["verified_rate"] is not None]

    total_tokens = sum(
        s["llm_usage"]["total_tokens"] for s in per_scenario if s.get("llm_usage")
    ) or None
    # LLM calls that raised and fell back to a heuristic. A fallback "agrees"
    # with the heuristic by construction, so without this count a model that
    # errors out would score as perfectly agreeing.
    llm_calls = sum(s["llm_usage"]["calls"] for s in per_scenario if s.get("llm_usage"))
    llm_errors = sum(s["llm_usage"]["errors"] for s in per_scenario if s.get("llm_usage"))

    total_demoted = sum(len(s["classification"]["demoted_to_fallback_layer"]) for s in per_scenario)
    total_guardrail_overrides = sum(len(s["guardrail_overrides"]) for s in per_scenario)
    coverage_failures = [s["scenario"] for s in per_scenario if not s["coverage_ok"]]

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "provider": provider_label,
        "provenance": provenance or {},
        "n_scenarios": n,
        "verdict_confusion": {
            "true_positive_needs_review": tp,
            "true_negative_auto_approve": tn,
            "false_positive_needs_review": fp,
            "false_negative_auto_approve": fn,
            "accuracy": (tp + tn) / n if n else None,
        },
        "routing_agreement_rate": routing_agreement_rate,
        "repo_kind_accuracy": repo_kind_accuracy,
        "classification_agreement_rate_mean": (sum(class_rates) / len(class_rates)) if class_rates else None,
        "classification_verified_rate_mean": (sum(verified_rates) / len(verified_rates)) if verified_rates else None,
        "total_wall_seconds": round(sum(s["wall_seconds"] for s in per_scenario), 2),
        "total_llm_tokens": total_tokens,
        "total_llm_calls": llm_calls,
        "total_llm_errors": llm_errors,
        "total_demoted_to_fallback_layer": total_demoted,
        "total_guardrail_overrides": total_guardrail_overrides,
        "coverage_failures": coverage_failures,
        "all_discovered_tests_ran": not coverage_failures,
        "injection": summarize_injection(per_scenario),
        "scenarios": per_scenario,
    }


def render_markdown(report: dict) -> str:
    lines = [
        f"# Evaluation report — provider `{report['provider']}`",
        "",
        f"Generated: {report['generated_at']}",
        f"Corpus size: {report['n_scenarios']} scenario(s)",
        *_provenance_lines(report.get("provenance") or {}),
        "",
        "## Layer catch matrix (which layer failed on which scenario)",
        "",
        *_catch_matrix(report["scenarios"]),
        "",
        "## Verdict correctness (confusion matrix)",
        "",
        f"- Accuracy: {_pct(report['verdict_confusion']['accuracy'])}",
        f"- True positive (correctly flagged for human review): {report['verdict_confusion']['true_positive_needs_review']}",
        f"- True negative (correctly auto-approved): {report['verdict_confusion']['true_negative_auto_approve']}",
        f"- **False positive** (auto-approve expected, flagged anyway): {report['verdict_confusion']['false_positive_needs_review']}",
        f"- **False negative** (flag expected, auto-approved instead — the dangerous case): {report['verdict_confusion']['false_negative_auto_approve']}",
        "",
        "## Routing & classification agreement (LLM vs. heuristic)",
        "",
        f"- Routing agreement rate: {_pct(report['routing_agreement_rate'])}",
        f"- Repo-kind classification accuracy (agentic vs. plain_service, vs. label): {_pct(report['repo_kind_accuracy'])}",
        f"- Classification agreement rate (mean over scenarios): {_pct(report['classification_agreement_rate_mean'])}",
        f"- Classification verified rate (mean over scenarios): {_pct(report['classification_verified_rate_mean'])}",
        "",
        "## Coverage safety net (dropped-test fallback + guardrail overrides)",
        "",
        f"- All discovered tests actually executed (none silently dropped): "
        f"{'YES' if report['all_discovered_tests_ran'] else 'NO — ' + str(report['coverage_failures'])}",
        f"- Tests demoted to a fallback layer (would have been dropped pre-fix): {report['total_demoted_to_fallback_layer']}",
        f"- Guardrail overrides (model said an agentic-only layer applies; guardrail forced it off): {report['total_guardrail_overrides']}",
        "",
        "## Cost / latency",
        "",
        f"- Total wall-clock: {report['total_wall_seconds']}s",
        f"- Total LLM tokens: {report['total_llm_tokens'] if report['total_llm_tokens'] is not None else 'n/a (heuristic run — no LLM calls)'}",
        f"- LLM calls: {report['total_llm_calls']}, of which failed and fell back to the heuristic: {report['total_llm_errors']}"
        " (agreement figures above are inflated by exactly these fallbacks)",
        "",
        "## Per-scenario detail",
        "",
    ]
    for s in report["scenarios"]:
        lines += [
            f"### `{s['scenario']}`",
            f"- Expected verdict: `{s['expected_verdict']}` — actual: `{s['actual_verdict']}` — {'✓ correct' if s['verdict_correct'] else '✗ WRONG'}",
            f"- Expected failing layers: {s['layers']['expected_failing'] or 'none'}",
            f"- Actual failing layers: {s['layers']['actual_failing'] or 'none'}",
        ]
        if s["layers"]["missed"]:
            lines.append(f"  - **missed**: {s['layers']['missed']}")
        if s["layers"]["unexpected_failures"]:
            lines.append(f"  - **unexpected**: {s['layers']['unexpected_failures']}")
        lines += [
            f"- Routing: repo_kind=`{s['routing']['repo_kind']}` (expected `{s['routing']['expected_repo_kind']}`, "
            f"{'correct' if s['routing']['repo_kind_correct'] else 'WRONG'}), applicable={s['routing']['applicable_layers']}"
            f" (heuristic said {s['routing']['heuristic_applicable_layers']}) — "
            f"{'agrees' if s['routing']['agrees_with_heuristic'] else 'DISAGREES'}",
            f"- Classification: {s['classification']['n_tests']} tests, "
            f"agreement={_pct(s['classification']['agreement_rate'])}, "
            f"verified={_pct(s['classification']['verified_rate'])}",
        ]
        if s["classification"]["disagreements"]:
            lines.append(f"  - disagreements: {s['classification']['disagreements']}")
        if s["classification"]["demoted_to_fallback_layer"]:
            lines.append(f"  - demoted to fallback layer (would've been dropped pre-fix): {s['classification']['demoted_to_fallback_layer']}")
        if s["guardrail_overrides"]:
            lines.append(f"  - guardrail overrode the model on: {s['guardrail_overrides']}")
        lines += [
            f"- Totals: {s['totals']}",
            f"- Wall time: {s['wall_seconds']}s"
            + (
                f", tokens: {s['llm_usage']['total_tokens']}, llm calls: {s['llm_usage']['calls']}"
                f" ({s['llm_usage']['errors']} failed)"
                if s.get("llm_usage") else ""
            ),
            "",
        ]
    return "\n".join(lines)


def _provenance_lines(p: dict) -> list[str]:
    if not p:
        return []
    dirty = "DIRTY (uncommitted changes)" if p.get("code_dirty") else "clean"
    return [
        f"Code: `{(p.get('git_sha') or 'unknown')[:10]}` {dirty}, diff `{p.get('code_diff_sha256')}`; "
        f"corpus `{p.get('corpus')}` fingerprint `{p.get('corpus_fingerprint')}`",
        f"Model: configured `{p.get('configured_model')}`, API reported `{p.get('response_models') or 'n/a'}`",
    ]


# "pending" = the layer was never applicable, so it never started.
_CELL = {
    "failed": "FAIL", "error": "ERR", "passed": "pass", "skipped": "skip",
    "pending": "·", "n/a": "·",
}


def _catch_matrix(scenarios: list[dict]) -> list[str]:
    """Scenario x layer grid. FAIL = the layer caught something; `·` = layer not run."""
    layers = [layer.value for layer in LayerId.ordered()]
    rows = ["| scenario | " + " | ".join(layers) + " |", "|---|" + "---|" * len(layers)]
    for s in scenarios:
        cells = [_CELL.get(s["layers"]["states"].get(layer, "n/a"), "?") for layer in layers]
        rows.append(f"| `{s['scenario']}` | " + " | ".join(cells) + " |")
    return rows


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x * 100:.0f}%"
