"""GitHubService maps run lifecycle to the right commit statuses and PR
comments. Pure in-process, no network.

Ported from the former manual scripts/verify_phase4_service.py (now deleted).
"""

from __future__ import annotations

from agenticqa.config import Settings
from agenticqa.github import GitHubService
from agenticqa.models.enums import ApprovalSource, ApprovalState, LayerId, LayerRunState, RunState, Verdict
from agenticqa.models.run import Approval, LayerRun, PRRef, QARun, RepoRef


class FakeClient:
    enabled = True

    def __init__(self) -> None:
        self.statuses: list[tuple] = []
        self.comments: list[tuple] = []

    async def set_commit_status(self, repo_full, sha, state, *, context, description="",
                                 target_url="", installation_id=None):
        self.statuses.append((repo_full, sha, state, context, description, target_url))
        return True

    async def create_comment(self, repo_full, num, body, *, installation_id=None):
        self.comments.append((repo_full, num, body))
        return True


def gh_run(**over) -> QARun:
    run = QARun(
        id="run1",
        source="github",
        repo=RepoRef(owner="acme", name="widget"),
        pr=PRRef(number=7, title="x", head_sha="sha777"),
    )
    for k, v in over.items():
        setattr(run, k, v)
    return run


def _settings() -> Settings:
    return Settings(public_base_url="https://qa.example.com")


async def test_active_for_gating():
    svc = GitHubService(FakeClient(), _settings())
    assert svc.active_for(gh_run())
    assert not svc.active_for(gh_run(source="local"))
    assert not svc.active_for(gh_run(pr=PRRef(number=7)))


async def test_report_pending():
    c = FakeClient()
    svc = GitHubService(c, _settings())
    await svc.report_pending(gh_run())
    assert c.statuses and c.statuses[0][2] == "pending"
    assert c.statuses[0][5] == "https://qa.example.com/runs/run1"


async def test_report_verdict_auto_approve():
    c = FakeClient()
    svc = GitHubService(c, _settings())
    lr = LayerRun(layer=LayerId.UNIT, state=LayerRunState.PASSED, planned_count=3, passed=3)
    await svc.report_verdict(gh_run(state=RunState.DONE, verdict=Verdict.AUTO_APPROVE, layer_runs={LayerId.UNIT: lr}))
    assert c.statuses[0][2] == "success"
    assert len(c.comments) == 1 and "auto-approved" in c.comments[0][2]


async def test_report_verdict_needs_review():
    c = FakeClient()
    svc = GitHubService(c, _settings())
    lr = LayerRun(layer=LayerId.UNIT, state=LayerRunState.FAILED, planned_count=3, passed=1, failed=2)
    await svc.report_verdict(gh_run(state=RunState.AWAITING_APPROVAL, verdict=Verdict.NEEDS_HUMAN_REVIEW,
                                     layer_runs={LayerId.UNIT: lr}, triage_report="Two unit tests failed."))
    assert c.statuses[0][2] == "pending"
    assert c.comments and "/approve" in c.comments[0][2] and "needs human review" in c.comments[0][2]


async def test_report_verdict_failed_run():
    c = FakeClient()
    svc = GitHubService(c, _settings())
    await svc.report_verdict(gh_run(state=RunState.FAILED, error="boom while cloning"))
    assert c.statuses[0][2] == "error"
    assert c.comments and "run failed" in c.comments[0][2]


async def test_report_approval_approved_and_rejected():
    c = FakeClient()
    svc = GitHubService(c, _settings())
    await svc.report_approval(gh_run(approval=Approval(state=ApprovalState.APPROVED, source=ApprovalSource.GITHUB, actor="alice")))
    assert c.statuses[0][2] == "success"
    assert c.comments and "alice" in c.comments[0][2]

    c2 = FakeClient()
    svc2 = GitHubService(c2, _settings())
    await svc2.report_approval(gh_run(approval=Approval(state=ApprovalState.REJECTED, source=ApprovalSource.IN_APP, actor="bob")))
    assert c2.statuses[0][2] == "failure"


async def test_local_run_emits_nothing():
    c = FakeClient()
    svc = GitHubService(c, _settings())
    await svc.report_pending(gh_run(source="local"))
    await svc.report_verdict(gh_run(source="local", state=RunState.DONE, verdict=Verdict.AUTO_APPROVE))
    assert not c.statuses
    assert not c.comments
