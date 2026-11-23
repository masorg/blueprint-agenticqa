"""Models for test results and per-layer/whole-run aggregates."""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field

from .enums import (
    ApprovalState,
    ApprovalSource,
    LayerId,
    LayerRunState,
    RunState,
    TestOutcome,
    Verdict,
)
from .pyramid import LayerPlan, RoutingDecision


def _now() -> datetime:
    return datetime.now(timezone.utc)


class TestResult(BaseModel):
    node_id: str
    outcome: TestOutcome
    duration: float = 0.0
    message: str = ""  # failure/error longrepr tail, empty on pass


class FailingTest(BaseModel):
    node_id: str
    message: str = ""


class RunnerResult(BaseModel):
    """Structured result of running one framework over a set of node ids.

    This is the carried-over contract from the original spec's tool functions:
    `{passed, failed, total, failing_tests, exit_code}`, extended with skipped,
    duration, and the full per-test list for live/aggregate views.
    """

    passed: int = 0
    failed: int = 0
    skipped: int = 0
    total: int = 0
    failing_tests: list[FailingTest] = Field(default_factory=list)
    exit_code: int = 0
    duration: float = 0.0
    results: list[TestResult] = Field(default_factory=list)
    error: str = ""  # set when the run itself failed to execute (not a test failure)

    @property
    def ok(self) -> bool:
        return self.failed == 0 and self.error == ""


class LayerRun(BaseModel):
    layer: LayerId
    state: LayerRunState = LayerRunState.PENDING
    planned_count: int = 0
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    results: list[TestResult] = Field(default_factory=list)
    error: str = ""

    def apply(self, result: RunnerResult) -> None:
        self.passed = result.passed
        self.failed = result.failed
        self.skipped = result.skipped
        self.results = result.results
        self.error = result.error
        if result.error:
            self.state = LayerRunState.ERROR
        elif result.failed > 0:
            self.state = LayerRunState.FAILED
        elif result.total == 0 or result.total == result.skipped:
            self.state = LayerRunState.SKIPPED
        else:
            self.state = LayerRunState.PASSED


class RepoRef(BaseModel):
    owner: str = ""
    name: str = ""
    clone_url: str | None = None

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}" if self.owner or self.name else "(local)"


class PRRef(BaseModel):
    number: int | None = None
    title: str = ""
    body: str = ""
    head_sha: str | None = None
    base_sha: str | None = None


class Approval(BaseModel):
    state: ApprovalState = ApprovalState.NOT_REQUIRED
    source: ApprovalSource | None = None
    actor: str = ""
    at: datetime | None = None


class SelectionInfo(BaseModel):
    """Which tests ran for this change and why (see `agenticqa.selection`)."""

    mode: str = "full"  # "change" | "full"
    reason: str = ""
    total: int = 0
    core: int = 0
    added: int = 0
    selected: int = 0
    changed: list[str] = Field(default_factory=list)
    inert: list[str] = Field(default_factory=list)
    uncovered: list[str] = Field(default_factory=list)
    out_of_scope: list[str] = Field(default_factory=list)
    weakened: list[str] = Field(default_factory=list)


class QARun(BaseModel):
    """The whole lifecycle of one QA run, persisted as the source of truth."""

    id: str
    source: str = "local"  # local | github
    repo: RepoRef = Field(default_factory=RepoRef)
    pr: PRRef = Field(default_factory=PRRef)
    local_path: str | None = None
    diff: str = ""
    env: dict[str, str] = Field(default_factory=dict)  # non-secret env for the SUT's tests
    installation_id: int | None = None  # GitHub App installation that owns this repo

    state: RunState = RunState.QUEUED
    routing: RoutingDecision | None = None
    layer_plans: list[LayerPlan] = Field(default_factory=list)
    layer_runs: dict[LayerId, LayerRun] = Field(default_factory=dict)
    triage_report: str = ""
    selection: SelectionInfo | None = None
    verdict: Verdict | None = None
    verdict_reason: str = ""
    approval: Approval = Field(default_factory=Approval)
    error: str = ""

    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)

    def touch(self) -> None:
        self.updated_at = _now()

    @property
    def category(self) -> str:
        """Coarse bucket used by the platform's list views."""
        if self.state in (
            RunState.QUEUED, RunState.ROUTING, RunState.PLANNED,
            RunState.RUNNING, RunState.TRIAGING,
        ):
            return "open"
        if self.state == RunState.AWAITING_APPROVAL:
            return "review"
        if self.state == RunState.FAILED:
            return "blocked"
        if self.state == RunState.DONE:
            return "blocked" if self.approval.state == ApprovalState.REJECTED else "passed"
        return "blocked"  # cancelled

    def layer_active(self, layer: LayerId) -> bool:
        """True if the layer counts for this run: routed as applicable, OR it
        actually received tests (e.g. via the coverage fallback). Routing is
        advisory; a layer that executed tests always counts, so a router can
        never hide a failure by marking the layer inapplicable."""
        if self.routing and self.routing.applies(layer):
            return True
        lr = self.layer_runs.get(layer)
        return bool(lr and lr.planned_count > 0)

    @property
    def totals(self) -> dict:
        p = f = s = 0
        for lr in self.layer_runs.values():
            p += lr.passed
            f += lr.failed
            s += lr.skipped
        return {"passed": p, "failed": f, "skipped": s, "total": p + f + s}
