"""The run engine: owns the background tasks, the concurrency cap, and the
in-app approval transition.

One asyncio task per run, gated by a semaphore so at most
``max_concurrent_runs`` prepare-and-execute at once. The engine is a process
singleton (single-worker uvicorn), so it sees every run and holds the only
authoritative task registry.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import TYPE_CHECKING

from ..events.bus import EventBus
from ..execution.base import Executor
from ..models.enums import ApprovalState, ApprovalSource, RunState
from ..models.events import EventType
from ..models.run import Approval, QARun, _now
from ..store.db import RunRepository
from .interfaces import Classifier, Router, TestAdvisor, Triage
from .pipeline import Pipeline

if TYPE_CHECKING:
    from ..github import GitHubService

log = logging.getLogger("agenticqa")


class Engine:
    def __init__(
        self,
        repo: RunRepository,
        bus: EventBus,
        executor: Executor,
        router: Router,
        classifier: Classifier,
        triage: Triage,
        max_concurrent_runs: int = 2,
        github: "GitHubService | None" = None,
        selection_mode: str = "full",
        advisor: TestAdvisor | None = None,
        trust_dynamic_loading: bool = False,
        coverage_map_path: str | None = None,
    ) -> None:
        self._trust_dynamic = trust_dynamic_loading
        self._coverage_map = coverage_map_path
        self._selection_mode = selection_mode
        self._advisor = advisor
        self._repo = repo
        self._bus = bus
        self._executor = executor
        self._github = github
        self._pipeline = Pipeline(repo, bus, executor, router, classifier, triage, github,
                                  selection_mode=selection_mode, advisor=advisor,
                                  trust_dynamic_loading=trust_dynamic_loading, coverage_map_path=coverage_map_path)
        self._sem = asyncio.Semaphore(max_concurrent_runs)
        self._tasks: dict[str, asyncio.Task] = {}

    def set_touchpoints(self, router: Router, classifier: Classifier, triage: Triage) -> None:
        """Hot-swap the LLM touch-points (used by the runtime model-config).
        Rebuilds the pipeline atomically; new runs use it, in-flight runs keep
        the pipeline they started with."""
        self._pipeline = Pipeline(
            self._repo, self._bus, self._executor, router, classifier, triage, self._github,
            selection_mode=self._selection_mode, advisor=self._advisor, trust_dynamic_loading=self._trust_dynamic,
            coverage_map_path=self._coverage_map,
        )

    @staticmethod
    def new_run_id() -> str:
        return uuid.uuid4().hex[:12]

    async def _notify_github(self, method: str, run: QARun) -> None:
        """Fire a GitHub lifecycle hook, swallowing any error."""
        if self._github is None:
            return
        try:
            await getattr(self._github, method)(run)
        except Exception as exc:  # noqa: BLE001
            log.warning("github %s hook failed for run %s: %s", method, run.id, exc)

    async def submit(self, run: QARun) -> QARun:
        """Persist a QUEUED run and launch its pipeline task. Returns immediately."""
        run.state = RunState.QUEUED
        await self._repo.save_run(run)
        await self._bus.publish(
            run.id, EventType.RUN_STATE, {"state": RunState.QUEUED.value, "verdict": None}
        )
        await self._notify_github("report_pending", run)
        task = asyncio.create_task(self._guarded(run), name=f"run:{run.id}")
        self._tasks[run.id] = task
        task.add_done_callback(lambda t, rid=run.id: self._tasks.pop(rid, None))
        return run

    async def _guarded(self, run: QARun) -> None:
        async with self._sem:
            await self._pipeline.execute(run)

    async def resolve_approval(
        self, run_id: str, approved: bool, source: ApprovalSource, actor: str
    ) -> QARun | None:
        """Apply a human decision to a run awaiting approval. Idempotent-ish:
        a run not awaiting approval is returned unchanged."""
        run = await self._repo.get_run(run_id)
        if run is None:
            return None
        if run.state != RunState.AWAITING_APPROVAL:
            return run

        state = ApprovalState.APPROVED if approved else ApprovalState.REJECTED
        run.approval = Approval(state=state, source=source, actor=actor, at=_now())
        run.state = RunState.DONE
        await self._repo.save_run(run)
        await self._bus.publish(
            run.id,
            EventType.APPROVAL_STATE,
            {
                "approval_state": state.value,
                "verdict": run.verdict.value if run.verdict else None,
                "source": source.value,
                "actor": actor,
            },
        )
        await self._bus.publish(
            run.id,
            EventType.RUN_DONE,
            {"state": run.state.value, "verdict": run.verdict.value if run.verdict else None},
        )
        await self._notify_github("report_approval", run)
        return run

    async def reconcile_on_startup(self) -> int:
        """Any run left non-terminal by a crash/redeploy is marked FAILED."""
        ids = await self._repo.non_terminal_run_ids()
        for run_id in ids:
            run = await self._repo.get_run(run_id)
            if run is None:
                continue
            run.state = RunState.FAILED
            run.error = "Interrupted by a restart before completion."
            await self._repo.save_run(run)
            await self._bus.publish(run.id, EventType.ERROR, {"error": run.error})
            await self._bus.publish(
                run.id, EventType.RUN_DONE, {"state": run.state.value, "verdict": None}
            )
        return len(ids)
