"""Runs corpus scenarios through the REAL ``Pipeline`` - no reimplemented
guardrail or verdict logic here, so the eval numbers can't silently drift from
what production actually does. Router/Classifier are wrapped in a thin
recording shim that observes what they returned, purely for reporting; the
shim delegates every call unchanged.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from ..events.bus import EventBus
from ..execution.subprocess_venv import SubprocessVenvExecutor
from ..models.pyramid import DiscoveredTest, RoutingDecision, TestClassification
from ..models.run import PRRef, QARun, RepoRef
from ..orchestrator.interfaces import Classifier, Router, Triage
from ..orchestrator.pipeline import Pipeline
from ..orchestrator.stubs import HeuristicRouter
from ..store.db import RunRepository
from .corpus import Scenario

log = logging.getLogger("agenticqa.eval")


async def _fetch_pr_meta(clone_url: str, pr_number: int) -> tuple[str, str] | None:
    """A public PR's real (title, body) via the GitHub REST API - what a
    reviewer, and therefore the production router, actually sees. Best-effort."""
    repo_full = clone_url.removesuffix(".git").split("github.com/")[-1]
    url = f"https://api.github.com/repos/{repo_full}/pulls/{pr_number}"
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=15.0) as client:
            resp = await client.get(url, headers={"Accept": "application/vnd.github+json"})
            resp.raise_for_status()
            data = resp.json()
            return (data.get("title") or "", data.get("body") or "")
    except Exception as exc:  # noqa: BLE001 - best-effort, never fail the scenario
        log.warning("could not fetch PR metadata for %s#%s: %s", repo_full, pr_number, exc)
        return None


def build_run(
    scenario: Scenario, *, repo_root: Path, diff: str, pr_title: str, pr_body: str
) -> QARun:
    """The QARun a scenario is evaluated as. Deliberately takes the PR text as
    explicit arguments so `scenario.notes` (the answer key) cannot leak in."""
    if scenario.source == "github":
        return QARun(
            id=f"eval-{scenario.name}",
            source="github",
            repo=RepoRef(owner="", name=scenario.name, clone_url=scenario.clone_url),
            pr=PRRef(number=scenario.pr_number, title=pr_title, body=pr_body,
                      head_sha=scenario.head_sha),
            diff=diff,
            env=dict(scenario.extra_env),
        )
    return QARun(
        id=f"eval-{scenario.name}",
        source="local",
        repo=RepoRef(owner="", name=scenario.name),
        pr=PRRef(title=pr_title, body=pr_body),
        local_path=str((repo_root / scenario.repo_path).resolve()),
        diff=diff,
        env=dict(scenario.extra_env),
    )


async def _fetch_pr_diff(clone_url: str, pr_number: int) -> str:
    """A PR's diff via GitHub's public `.diff` URL - no auth needed for a
    public repo, and it's the exact text a human reviewer sees on the PR.
    Best-effort: an empty string on any failure (network, 404, etc.) rather
    than failing the whole scenario - the router tolerates an empty diff and
    considers every layer, same as it does for a webhook whose diff fetch
    failed in production."""
    repo_full = clone_url.removesuffix(".git").split("github.com/")[-1]
    url = f"https://github.com/{repo_full}/pull/{pr_number}.diff"
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=15.0) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.text
    except Exception as exc:  # noqa: BLE001 - best-effort, never fail the scenario
        log.warning("could not fetch diff for %s#%s: %s", repo_full, pr_number, exc)
        return ""


@dataclass
class _RecordingRouter:
    inner: Router
    last_decision: RoutingDecision | None = None

    async def route(self, run: QARun) -> RoutingDecision:
        decision = await self.inner.route(run)
        self.last_decision = decision
        return decision


@dataclass
class _RecordingClassifier:
    inner: Classifier
    last_classifications: list[TestClassification] = field(default_factory=list)

    async def classify(
        self, run: QARun, tests: list[DiscoveredTest]
    ) -> list[TestClassification]:
        result = await self.inner.classify(run, tests)
        self.last_classifications = result
        return result


@dataclass
class ScenarioResult:
    scenario: Scenario
    run: QARun
    heuristic_routing: RoutingDecision
    actual_routing: RoutingDecision | None
    classifications: list[TestClassification]
    wall_seconds: float
    llm_usage: dict[str, int] | None = None
    # Verdict of the "LLM decides" ablation (eval.ablation), when it was run.
    baseline_verdict: str | None = None


async def run_scenario(
    scenario: Scenario,
    *,
    router: Router,
    classifier: Classifier,
    triage: Triage,
    repo_root: Path,
    selection_mode: str = "full",
    advisor=None,
) -> ScenarioResult:
    """Execute one corpus scenario through the production ``Pipeline`` using
    the given (heuristic- or LLM-backed) touch-points, and independently
    compute the heuristic router's read of the same run - so routing agreement
    is measurable even when the LLM router is the one under test."""
    repo = RunRepository()
    await repo.connect()
    bus = EventBus(repo)
    executor = SubprocessVenvExecutor()

    rec_router = _RecordingRouter(inner=router)
    rec_classifier = _RecordingClassifier(inner=classifier)
    pipeline = Pipeline(repo, bus, executor, rec_router, rec_classifier, triage, github=None,
                        selection_mode=selection_mode, advisor=advisor)

    diff, pr_title, pr_body = scenario.diff, scenario.pr_title, scenario.pr_body
    if scenario.source == "github" and scenario.pr_number is not None:
        if not diff:
            diff = await _fetch_pr_diff(scenario.clone_url, scenario.pr_number)
        meta = await _fetch_pr_meta(scenario.clone_url, scenario.pr_number)
        if meta is not None:
            pr_title, pr_body = meta
    run = build_run(scenario, repo_root=repo_root, diff=diff, pr_title=pr_title, pr_body=pr_body)

    # A throwaway copy: the heuristic router mutates nothing, but keep the
    # comparison run separate from the one the pipeline will drive to a
    # terminal state.
    heuristic_decision = await HeuristicRouter().route(run.model_copy(deep=True))

    # One client serves router, classifier and triage, so its cumulative totals
    # (not `last_usage`, which keeps only the final call) give this scenario's cost.
    client = next(
        (c for c in (getattr(t, "_client", None) for t in (router, classifier, triage)) if c is not None),
        None,
    )
    totals_before = dict(getattr(client, "totals", None) or {})

    started = time.monotonic()
    await pipeline.execute(run)
    elapsed = time.monotonic() - started

    await repo.close()

    totals_after = getattr(client, "totals", None)
    llm_usage = (
        {k: totals_after[k] - totals_before.get(k, 0) for k in totals_after}
        if totals_after is not None
        else None
    )

    return ScenarioResult(
        scenario=scenario,
        run=run,
        heuristic_routing=heuristic_decision,
        actual_routing=rec_router.last_decision,
        classifications=rec_classifier.last_classifications,
        wall_seconds=elapsed,
        llm_usage=llm_usage,
    )


async def run_corpus(
    scenarios: list[Scenario],
    *,
    router: Router,
    classifier: Classifier,
    triage: Triage,
    repo_root: Path,
) -> list[ScenarioResult]:
    return [
        await run_scenario(
            s, router=router, classifier=classifier, triage=triage, repo_root=repo_root
        )
        for s in scenarios
    ]
