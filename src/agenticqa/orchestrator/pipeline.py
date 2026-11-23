"""The run pipeline: the ordered steps every run walks through.

    QUEUED → ROUTING → PLANNED → RUNNING → TRIAGING
             → DONE (auto_approve)
             → AWAITING_APPROVAL (needs_human_review)
    any error → FAILED

Every transition follows the same discipline: mutate the run, persist it, then
publish an event (which assigns the monotonic seq and fans out to SSE). The
verdict is computed by a **deterministic guardrail**, never by the triage
narrative: if any applicable layer failed or errored, the run needs human
review. The LLM (Phase 3) only writes the prose.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from ..classify import heuristic_layer
from ..events.bus import EventBus
from ..execution.base import Executor, Workspace, WorkspaceSpec
from ..models.enums import (
    ApprovalState,
    LayerId,
    LayerRunState,
    RunState,
    Verdict,
)
from ..models.events import EventType
from ..models.pyramid import LayerPlan, TestClassification
from ..models.run import Approval, LayerRun, QARun, SelectionInfo, TestResult
from ..runners.registry import select_runner
from ..selection import select_tests, with_advice
from ..store.db import RunRepository
from .interfaces import Classifier, Router, TestAdvisor, Triage

if TYPE_CHECKING:
    from ..github import GitHubService

log = logging.getLogger("agenticqa")


def _reconcile_classifications(tests: list, classifications: list) -> tuple[list, int, int]:
    """Make the classifier's output cover EXACTLY the discovered tests.

    The pipeline must not trust the classifier (an LLM, or anything behind the
    `Classifier` interface) to be complete or well-formed: a test it omits would
    never run, one it invents would error, and a duplicate would run twice.
    Unknown ids are discarded, the first classification per test wins, and any
    test left without one gets its heuristic layer. Returns
    (classifications in discovery order, n_added_for_missing, n_discarded).
    """
    known = {t.node_id for t in tests}
    first: dict[str, object] = {}
    discarded = 0
    for c in classifications:
        if c.node_id in known and c.node_id not in first:
            first[c.node_id] = c
        else:
            discarded += 1
    out: list = []
    added = 0
    for t in tests:
        c = first.get(t.node_id)
        if c is None:
            layer = heuristic_layer(t)
            c = TestClassification(
                node_id=t.node_id,
                layer=layer,
                confidence=0.0,
                rationale="The classifier returned nothing for this test; using its heuristic layer.",
                heuristic_layer=layer,
                agreement=True,
                verified=False,
            )
            added += 1
        out.append(c)
    return out, added, discarded


def _bucket_with_fallback(
    classifications: list, routing
) -> tuple[dict[LayerId, list[str]], int]:
    """Bucket each classified test under its layer, demoting any test whose
    classified layer this run doesn't route to (e.g. an agentic-only layer on
    a plain service) instead of dropping it. Falls back to the test's
    heuristic layer if that one is applicable here, else UNIT. UNIT is *not*
    guaranteed applicable by any guardrail (only the two agentic-only layers
    have one) - a routing response could in principle mark it inapplicable
    too. `_build_layer_plans` is what actually guarantees a demoted test
    still runs, regardless of which layer it lands in here. Returns
    (buckets, demoted_count).
    """
    by_layer: dict[LayerId, list[str]] = {layer: [] for layer in LayerId.ordered()}
    demoted = 0
    for c in classifications:
        layer = c.layer
        if routing and not routing.applies(layer):
            fallback = c.heuristic_layer
            if fallback is None or not routing.applies(fallback):
                fallback = LayerId.UNIT
            layer = fallback
            demoted += 1
        by_layer[layer].append(c.node_id)
    return by_layer, demoted


def _build_layer_plans(
    by_layer: dict[LayerId, list[str]], routing
) -> list[LayerPlan]:
    """Turn the bucketed tests into a `LayerPlan` per layer. A layer that
    received any tests - via normal classification or via
    `_bucket_with_fallback`'s demotion - always runs them, even if routing
    marked that exact layer inapplicable (e.g. a fallback landed on UNIT and
    routing also said UNIT doesn't apply): a test is never planned into a
    layer and then silently skipped anyway. `applicable` on the returned plan
    therefore means "this layer runs in this run", not just "routing said so".
    """
    plans: list[LayerPlan] = []
    for layer in LayerId.ordered():
        node_ids = by_layer.get(layer, [])
        routed_applicable = bool(routing and routing.applies(layer))
        reason = ""
        if routing and layer in routing.layers:
            reason = routing.layers[layer].reason
        applicable = routed_applicable or bool(node_ids)
        if not routed_applicable and node_ids:
            note = f"Running anyway: {len(node_ids)} test(s) landed here via the coverage fallback."
            reason = f"{reason} {note}".strip()
        plans.append(LayerPlan(layer=layer, applicable=applicable, reason=reason, tests=node_ids))
    return plans


def _untested(changed: list) -> list[str]:
    """The changed files that a run with no tests leaves without evidence: all but inert and out-of-scope files."""
    from ..selection import kind_of, out_of_scope

    return [c.path for c in changed if c.status != "deleted" and kind_of(c.path) != "inert" and not out_of_scope(c.path)]


@dataclass
class Pipeline:
    repo: RunRepository
    bus: EventBus
    executor: Executor
    router: Router
    classifier: Classifier
    triage: Triage
    github: "GitHubService | None" = None
    # "change": run the tests selected from the diff (a deterministic core, plus advice that can only
    # add). "full": run every discovered test. Anything the selection cannot account for runs all.
    selection_mode: str = "full"
    advisor: TestAdvisor | None = None
    trust_dynamic_loading: bool = False
    coverage_map_path: str | None = None  # a stored recording for the coverage-based core; None -> static core

    def _coverage(self):
        """The stored recording, or None (static core) if there is none or it cannot be read."""
        if not self.coverage_map_path:
            return None
        try:
            from ..selection import CoverageMap

            return CoverageMap.from_json(json.loads(Path(self.coverage_map_path).read_text()))
        except Exception as exc:  # noqa: BLE001 - an unusable recording falls back to the static core
            log.warning("coverage recording %s unusable, using the static core: %s", self.coverage_map_path, exc)
            return None

    async def _report(self, method: str, run: QARun) -> None:
        """Fire a GitHub lifecycle hook, swallowing any error - a GitHub hiccup
        must never fail a run (the client already degrades, this guards the rest)."""
        if self.github is None:
            return
        try:
            await getattr(self.github, method)(run)
        except Exception as exc:  # noqa: BLE001
            log.warning("github %s hook failed for run %s: %s", method, run.id, exc)

    async def _set_state(self, run: QARun, state: RunState) -> None:
        run.state = state
        await self.repo.save_run(run)
        await self.bus.publish(
            run.id,
            EventType.RUN_STATE,
            {"state": state.value, "verdict": run.verdict.value if run.verdict else None},
        )

    async def execute(self, run: QARun) -> None:
        """Drive one run to a terminal-or-awaiting state. Never raises."""
        workspace: Workspace | None = None
        try:
            await self._route(run)
            workspace = await self._checkout(run)
            runner, plans = await self._plan(run, workspace)
            await self._run_layers(run, workspace, runner, plans)
            await self._triage_and_gate(run)
        except Exception as exc:  # noqa: BLE001 - the engine boundary owns failures
            run.error = f"{type(exc).__name__}: {exc}"
            await self._set_state(run, RunState.FAILED)
            await self.bus.publish(run.id, EventType.ERROR, {"error": run.error})
            await self.bus.publish(
                run.id, EventType.RUN_DONE, {"state": run.state.value, "verdict": None}
            )
            await self._report("report_verdict", run)
        finally:
            if workspace is not None:
                await workspace.aclose()

    # ---- steps ---------------------------------------------------------

    async def _route(self, run: QARun) -> None:
        await self._set_state(run, RunState.ROUTING)
        decision = await self.router.route(run)
        run.routing = decision
        await self.repo.save_run(run)
        await self.bus.publish(
            run.id,
            EventType.ROUTE_DECISION,
            {
                "repo_kind": decision.repo_kind.value,
                "summary": decision.summary,
                "layers": {
                    layer.value: {
                        "applicable": r.applicable,
                        "reason": r.reason,
                        "agentic_only": layer.agentic_only,
                    }
                    for layer, r in decision.layers.items()
                },
            },
        )

    async def _checkout(self, run: QARun) -> Workspace:
        spec = WorkspaceSpec(
            source=run.source if run.source in ("github", "local") else "local",
            clone_url=run.repo.clone_url,
            head_sha=run.pr.head_sha,
            local_path=run.local_path,
            extra_env=dict(run.env),
        )
        await self.bus.publish(
            run.id, EventType.RUN_LOG, {"message": "Preparing workspace (checkout + venv + install)…"}
        )
        return await self.executor.prepare(spec)

    async def _plan(self, run: QARun, workspace: Workspace):
        runner = await select_runner(workspace)
        if runner is None:
            raise RuntimeError("No framework runner matched the target repository.")

        tests = await runner.discover(workspace)
        await self.bus.publish(
            run.id,
            EventType.RUN_LOG,
            {"message": f"Discovered {len(tests)} test(s) via '{runner.name}'."},
        )
        tests = await self._select(run, workspace, tests)
        classifications = await self.classifier.classify(run, tests)
        classifications, added, discarded = _reconcile_classifications(tests, classifications)
        if added or discarded:
            await self.bus.publish(
                run.id,
                EventType.RUN_LOG,
                {
                    "message": (
                        f"Classifier output did not match the discovered tests: filled in "
                        f"{added} missing, discarded {discarded} unknown/duplicate."
                    )
                },
            )

        by_layer, demoted = _bucket_with_fallback(classifications, run.routing)

        if demoted:
            await self.bus.publish(
                run.id,
                EventType.RUN_LOG,
                {
                    "message": (
                        f"{demoted} test(s) were classified into a layer this run "
                        "doesn't route to; ran them under a fallback layer instead "
                        "of skipping them."
                    )
                },
            )

        plans = _build_layer_plans(by_layer, run.routing)
        for plan in plans:
            run.layer_runs[plan.layer] = LayerRun(
                layer=plan.layer,
                state=LayerRunState.PENDING,
                planned_count=plan.planned_count,
            )

        run.layer_plans = plans
        await self._set_state(run, RunState.PLANNED)
        await self.bus.publish(
            run.id,
            EventType.PLAN_READY,
            {
                "layers": [
                    {
                        "layer": p.layer.value,
                        "label": p.layer.label,
                        "order": p.layer.order,
                        "agentic_only": p.layer.agentic_only,
                        "applicable": p.applicable,
                        "planned_count": p.planned_count,
                        "reason": p.reason,
                    }
                    for p in plans
                ]
            },
        )
        return runner, plans

    async def _select(self, run: QARun, workspace: Workspace, tests: list) -> list:
        """Decide which discovered tests run. The core is deterministic; an advisor may add tests and can
        never remove one; every failure of the analysis selects all tests."""
        total = len(tests)
        if self.selection_mode != "change":
            from ..selection import diff_hunks, parse_diff, weakened_tests

            changed = parse_diff(run.diff) or []
            run.selection = SelectionInfo(mode="full", reason="selection is off", total=total,
                                          core=total, selected=total,
                                          uncovered=_untested(changed) if total == 0 else [],
                                          weakened=weakened_tests(changed, diff_hunks(run.diff)) if changed else [])
            return tests
        coverage = self._coverage()
        sel = await asyncio.to_thread(
            select_tests, workspace.root, run.diff, tests, None, "name", not self.trust_dynamic_loading,
            "function", coverage)
        uncovered = sel.uncovered
        if total == 0:  # no test exists: nothing shows any changed file safe, whichever mode the core chose
            from ..selection import parse_diff

            uncovered = _untested(parse_diff(run.diff) or [])
        run.selection = SelectionInfo(mode=sel.mode, reason=sel.reason, total=sel.total, core=len(sel.core),
                                      selected=len(sel.selected), changed=sel.changed[:200],
                                      inert=sel.inert[:200], uncovered=uncovered[:200],
                                      out_of_scope=sel.out_of_scope[:200], weakened=sel.weakened[:200])
        if sel.mode == "change" and self.advisor is not None:
            try:
                advised = await self.advisor.advise(run, tests, sel.core)
            except Exception as exc:  # noqa: BLE001 - advice is optional; a failure leaves the core as it was
                log.warning("test advisor failed for run %s: %s", run.id, exc)
                advised = []
            sel = with_advice(sel, advised, {t.node_id for t in tests})
            run.selection.added = len(sel.added)
            run.selection.selected = len(sel.selected)
        keep = set(sel.selected)
        chosen = [t for t in tests if t.node_id in keep]
        note = (f"Selected {len(chosen)} of {total} test(s)"
                + (f" ({run.selection.core} from the change, {run.selection.added} added by advice)"
                   if sel.mode == "change" else f"; running all: {sel.reason}")
                + (f". No test reaches: {', '.join(sel.uncovered[:5])}" if sel.uncovered else "") + ".")
        await self.bus.publish(run.id, EventType.RUN_LOG, {"message": note})
        return chosen

    async def _run_layers(
        self, run: QARun, workspace: Workspace, runner, plans: list[LayerPlan]
    ) -> None:
        await self._set_state(run, RunState.RUNNING)

        for plan in plans:
            layer = plan.layer
            lr = run.layer_runs[layer]
            if not plan.applicable or plan.planned_count == 0:
                if plan.applicable:  # applicable but nothing classified here
                    lr.state = LayerRunState.SKIPPED
                await self.repo.save_run(run)
                await self._emit_layer(run, lr)
                continue

            lr.state = LayerRunState.RUNNING
            await self.repo.save_run(run)
            await self._emit_layer(run, lr)

            result = await self._run_one_layer(run, workspace, runner, plan)
            lr.apply(result)
            await self.repo.save_run(run)
            await self._emit_layer(run, lr)

    async def _run_one_layer(self, run, workspace, runner, plan):
        """Run a layer's tests, publishing each result live via a drain task."""
        queue: asyncio.Queue = asyncio.Queue()
        sentinel = object()
        running = {"passed": 0, "failed": 0, "skipped": 0}

        def on_event(tr: TestResult) -> None:
            queue.put_nowait(tr)

        async def drain() -> None:
            while True:
                item = await queue.get()
                if item is sentinel:
                    return
                tr: TestResult = item
                if tr.outcome.value == "passed":
                    running["passed"] += 1
                elif tr.outcome.value in ("failed", "error"):
                    running["failed"] += 1
                elif tr.outcome.value == "skipped":
                    running["skipped"] += 1
                await self.bus.publish(
                    run.id,
                    EventType.TEST_RESULT,
                    {
                        "layer": plan.layer.value,
                        "node_id": tr.node_id,
                        "outcome": tr.outcome.value,
                        "duration": tr.duration,
                        "message": tr.message[:2000],
                        "running": dict(running),
                        "planned_count": plan.planned_count,
                    },
                )

        drainer = asyncio.create_task(drain())
        try:
            result = await runner.run(workspace, plan.tests, plan.layer, on_event)
        finally:
            queue.put_nowait(sentinel)
            await drainer
        return result

    async def _emit_layer(self, run: QARun, lr: LayerRun) -> None:
        await self.bus.publish(
            run.id,
            EventType.LAYER_STATE,
            {
                "layer": lr.layer.value,
                "state": lr.state.value,
                "planned_count": lr.planned_count,
                "passed": lr.passed,
                "failed": lr.failed,
                "skipped": lr.skipped,
                "error": lr.error,
            },
        )

    async def _triage_and_gate(self, run: QARun) -> None:
        await self._set_state(run, RunState.TRIAGING)
        report = await self.triage.triage(run)
        run.triage_report = report
        await self.repo.save_run(run)
        await self.bus.publish(run.id, EventType.TRIAGE_REPORT, {"report": report})

        # Deterministic verdict guardrail: did any active layer fail/error?
        # "Active" is `layer_active`, not just "routed applicable": the coverage
        # fallback can run tests in a layer the router marked inapplicable, and a
        # failure there must still count - otherwise a router (or a model steering
        # it) could make a failing run auto-approve by switching layers off.
        failed = any(
            lr.state in (LayerRunState.FAILED, LayerRunState.ERROR) and run.layer_active(layer)
            for layer, lr in run.layer_runs.items()
        )

        # No evidence is not approval: a change to code that no selected test reaches is not shown to be safe.
        sel = run.selection
        # A full run covers every file it can; with no tests at all, nothing does.
        unevidenced = bool(sel and sel.uncovered and (sel.mode == "change" or sel.total == 0))
        weakened = bool(sel and sel.weakened)  # the change removes or weakens tests, in either mode
        if weakened:
            run.triage_report = (run.triage_report.rstrip()
                                 + "\n\n**This change removes or weakens tests:** "
                                 + "; ".join(sel.weakened[:20]))
        if unevidenced:
            run.triage_report = (run.triage_report.rstrip()
                                 + "\n\n**No test reaches these changed files, so nothing shows they are safe:** "
                                 + ", ".join(f"`{p}`" for p in sel.uncovered[:20]))
            await self.repo.save_run(run)
        reasons = [r for r, on in (("failing tests", failed), ("changed code that no test reaches", unevidenced),
                                   ("tests removed or weakened", weakened)) if on]
        run.verdict_reason = " and ".join(reasons)

        if failed or unevidenced or weakened:
            run.verdict = Verdict.NEEDS_HUMAN_REVIEW
            run.approval = Approval(state=ApprovalState.PENDING)
            await self._set_state(run, RunState.AWAITING_APPROVAL)
            await self.bus.publish(
                run.id,
                EventType.APPROVAL_STATE,
                {"approval_state": ApprovalState.PENDING.value, "verdict": run.verdict.value},
            )
            # Not terminal: the run waits for a human. No run.done here.
            await self._report("report_verdict", run)
        else:
            run.verdict = Verdict.AUTO_APPROVE
            run.approval = Approval(state=ApprovalState.NOT_REQUIRED)
            await self._set_state(run, RunState.DONE)
            await self.bus.publish(
                run.id,
                EventType.RUN_DONE,
                {"state": run.state.value, "verdict": run.verdict.value},
            )
            await self._report("report_verdict", run)
