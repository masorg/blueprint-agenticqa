"""GET /api/v1/runs, /api/v1/runs/{id}, /api/v1/stats - lists, snapshot, metrics."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from ..models.run import QARun
from ..store.db import RunRepository
from .deps import get_repo
from .schemas import RunSummary, Stats

router = APIRouter(prefix="/api/v1", tags=["runs"])


@router.get("/meta")
async def meta(request: Request) -> dict:
    """Runtime configuration the UI surfaces: the active LLM provider/model
    (updated live by the model-config) and the GitHub wiring mode."""
    settings = request.app.state.settings
    llm = getattr(request.app.state, "llm_meta", None) or {
        "provider": "stub", "label": "Heuristic", "model": None, "base_url": "", "mode": "heuristic"
    }
    if settings.github_app_id and (settings.github_app_private_key or settings.github_app_private_key_path):
        gh_mode = "app"
    elif settings.github_token:
        gh_mode = "pat"
    else:
        gh_mode = "disabled"
    return {
        "llm": llm,
        "github": {
            "mode": gh_mode,
            "webhook_ready": bool(settings.github_webhook_secret),
            "allowed_orgs": sorted(settings.allowed_orgs),
            "allowed_enterprises": sorted(settings.allowed_enterprises),
            "review_repos": settings.review_repos,
        },
    }


def _summary(r: QARun) -> RunSummary:
    t = r.totals
    return RunSummary(
        id=r.id,
        source=r.source,
        repo=r.repo.full_name,
        pr_number=r.pr.number,
        pr_title=r.pr.title,
        head_sha=(r.pr.head_sha[:7] if r.pr.head_sha else None),
        state=r.state.value,
        verdict=r.verdict.value if r.verdict else None,
        category=r.category,
        repo_kind=r.routing.repo_kind.value if r.routing else "plain_service",
        passed=t["passed"],
        failed=t["failed"],
        total=t["total"],
        approval_state=r.approval.state.value,
        created_at=r.created_at.isoformat(),
        updated_at=r.updated_at.isoformat(),
    )


@router.get("/runs", response_model=list[RunSummary])
async def list_runs(repo: RunRepository = Depends(get_repo)) -> list[RunSummary]:
    return [_summary(r) for r in await repo.list_runs(limit=200)]


@router.get("/stats", response_model=Stats)
async def stats(repo: RunRepository = Depends(get_repo)) -> Stats:
    runs = await repo.list_runs(limit=500)
    counts = {"open": 0, "review": 0, "blocked": 0, "passed": 0}
    for r in runs:
        counts[r.category] = counts.get(r.category, 0) + 1
    decided = counts["passed"] + counts["blocked"]
    rate = (counts["passed"] / decided * 100.0) if decided else 0.0
    return Stats(
        open=counts["open"],
        review=counts["review"],
        blocked=counts["blocked"],
        passed=counts["passed"],
        total=len(runs),
        pass_rate=round(rate, 1),
    )


@router.get("/runs/{run_id}")
async def get_run(run_id: str, repo: RunRepository = Depends(get_repo)) -> QARun:
    run = await repo.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    return run
