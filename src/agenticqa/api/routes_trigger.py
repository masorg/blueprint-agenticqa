"""POST /api/v1/runs - accept a trigger, return 202 with the stream URL fast."""

from __future__ import annotations

from fastapi import APIRouter, Depends, status

from ..models.run import PRRef, QARun, RepoRef
from ..orchestrator.engine import Engine
from .deps import get_engine, require_api_key
from .schemas import TriggerRequest, TriggerResponse

router = APIRouter(prefix="/api/v1", tags=["trigger"])


@router.post(
    "/runs",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=TriggerResponse,
    dependencies=[Depends(require_api_key)],
)
async def trigger_run(body: TriggerRequest, engine: Engine = Depends(get_engine)) -> TriggerResponse:
    run = QARun(
        id=Engine.new_run_id(),
        source=body.source,
        repo=RepoRef(owner=body.repo.owner, name=body.repo.name, clone_url=body.repo.clone_url),
        pr=PRRef(
            number=body.pr.number,
            title=body.pr.title,
            body=body.pr.body,
            head_sha=body.pr.head_sha,
            base_sha=body.pr.base_sha,
        ),
        local_path=body.local_path,
        diff=body.diff,
        env=body.env,
    )
    await engine.submit(run)
    return TriggerResponse(
        run_id=run.id,
        state=run.state.value,
        stream_url=f"/api/v1/runs/{run.id}/events",
        dashboard_url=f"/runs/{run.id}",
    )
