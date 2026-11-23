"""In-app approval: POST /api/v1/runs/{id}/approve and /reject.

Phase 2 applies the decision directly through the engine. Phase 4 routes both
this and the GitHub webhook through a single ApprovalService so the in-app and
GitHub-native channels can never diverge.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..models.enums import ApprovalSource
from ..orchestrator.engine import Engine
from .deps import get_engine
from .schemas import ApprovalRequest

router = APIRouter(prefix="/api/v1", tags=["approval"])


@router.post("/runs/{run_id}/approve")
async def approve(
    run_id: str, body: ApprovalRequest, engine: Engine = Depends(get_engine)
) -> dict:
    run = await engine.resolve_approval(run_id, True, ApprovalSource.IN_APP, body.actor)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    return {"run_id": run.id, "state": run.state.value, "approval": run.approval.state.value}


@router.post("/runs/{run_id}/reject")
async def reject(
    run_id: str, body: ApprovalRequest, engine: Engine = Depends(get_engine)
) -> dict:
    run = await engine.resolve_approval(run_id, False, ApprovalSource.IN_APP, body.actor)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    return {"run_id": run.id, "state": run.state.value, "approval": run.approval.state.value}
