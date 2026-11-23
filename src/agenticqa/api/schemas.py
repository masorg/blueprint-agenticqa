"""Request/response bodies for the HTTP API (distinct from internal models)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class RepoIn(BaseModel):
    owner: str = ""
    name: str = ""
    clone_url: str | None = None


class PRIn(BaseModel):
    number: int | None = None
    title: str = ""
    body: str = ""
    head_sha: str | None = None
    base_sha: str | None = None


class TriggerRequest(BaseModel):
    """Body for POST /api/v1/runs. `source=local` runs a path already on the
    app's filesystem (demo); `source=github` checks out a PR head."""

    source: str = "local"
    repo: RepoIn = Field(default_factory=RepoIn)
    pr: PRIn = Field(default_factory=PRIn)
    local_path: str | None = None
    diff: str = ""
    env: dict[str, str] = Field(default_factory=dict)


class TriggerResponse(BaseModel):
    run_id: str
    state: str
    stream_url: str
    dashboard_url: str


class ApprovalRequest(BaseModel):
    actor: str = "in-app-user"


class RunSummary(BaseModel):
    id: str
    source: str
    repo: str
    pr_number: int | None
    pr_title: str = ""
    head_sha: str | None = None
    state: str
    verdict: str | None
    category: str = "open"
    repo_kind: str = "plain_service"
    passed: int = 0
    failed: int = 0
    total: int = 0
    approval_state: str = "not_required"
    created_at: str
    updated_at: str


class Stats(BaseModel):
    open: int = 0
    review: int = 0
    blocked: int = 0
    passed: int = 0
    total: int = 0
    pass_rate: float = 0.0  # passed / (passed + blocked), as a percentage
