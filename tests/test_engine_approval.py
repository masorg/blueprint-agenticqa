"""Engine.resolve_approval idempotency: approving an already-DONE run must be
a no-op that returns the run UNCHANGED -- in particular the second approver's
actor name must not overwrite the first approval's actor.

This mirrors a real production race (see memory: an in-app rejection racing an
API approval, where "first decision won").
"""

from __future__ import annotations

from agenticqa.events.bus import EventBus
from agenticqa.models.enums import ApprovalSource, ApprovalState, RunState, Verdict
from agenticqa.models.run import Approval, QARun
from agenticqa.orchestrator.engine import Engine
from agenticqa.store.db import RunRepository


class _NullExecutor:
    pass


class _NullTouchpoint:
    async def route(self, run):
        raise NotImplementedError

    async def classify(self, run, tests):
        raise NotImplementedError

    async def triage(self, run):
        raise NotImplementedError


async def _make_engine() -> tuple[Engine, RunRepository]:
    repo = RunRepository()
    bus = EventBus(repo)
    tp = _NullTouchpoint()
    engine = Engine(repo, bus, _NullExecutor(), tp, tp, tp)
    return engine, repo


async def test_second_approval_is_a_noop_and_keeps_first_actor():
    engine, repo = await _make_engine()
    run = QARun(
        id="run-race",
        state=RunState.AWAITING_APPROVAL,
        verdict=Verdict.NEEDS_HUMAN_REVIEW,
        approval=Approval(state=ApprovalState.PENDING),
    )
    await repo.save_run(run)

    first = await engine.resolve_approval("run-race", True, ApprovalSource.IN_APP, "alice")
    assert first is not None
    assert first.state == RunState.DONE
    assert first.approval.state == ApprovalState.APPROVED
    assert first.approval.actor == "alice"

    second = await engine.resolve_approval("run-race", True, ApprovalSource.GITHUB, "bob")
    assert second is not None
    assert second.state == RunState.DONE
    # unchanged: still alice's approval, not bob's -- the second call never
    # applied a new decision because the run was no longer AWAITING_APPROVAL.
    assert second.approval.actor == "alice"
    assert second.approval.source == ApprovalSource.IN_APP

    persisted = await repo.get_run("run-race")
    assert persisted is not None
    assert persisted.approval.actor == "alice"


async def test_resolve_approval_returns_none_for_unknown_run():
    engine, _repo = await _make_engine()
    result = await engine.resolve_approval("nope", True, ApprovalSource.IN_APP, "alice")
    assert result is None
