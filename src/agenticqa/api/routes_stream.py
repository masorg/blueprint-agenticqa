"""GET /api/v1/runs/{id}/events - Server-Sent Events, with Last-Event-ID replay.

Subscribe first, then replay the durable log from the client's last seen seq,
then stream live - deduping by seq so nothing is missed or doubled across the
subscribe/replay boundary. The stream closes once a terminal `run.done` is sent;
a run awaiting approval stays open (keepalive pings) so the human decision
arrives live.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sse_starlette.sse import EventSourceResponse

from ..events.bus import EventBus
from ..models.events import EventType
from ..store.db import RunRepository
from .deps import get_bus, get_repo

router = APIRouter(prefix="/api/v1", tags=["stream"])

# Headers that keep proxies (incl. the CML subdomain proxy) from buffering SSE.
_SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


@router.get("/runs/{run_id}/events")
async def stream_events(
    run_id: str,
    request: Request,
    last_event_id: str | None = Header(default=None),
    repo: RunRepository = Depends(get_repo),
    bus: EventBus = Depends(get_bus),
) -> EventSourceResponse:
    run = await repo.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")

    try:
        after = int(last_event_id) if last_event_id else 0
    except ValueError:
        after = 0

    async def gen():
        queue = bus.subscribe(run_id)
        sent = after
        try:
            for event in await bus.replay(run_id, after):
                if event.seq <= sent:
                    continue
                yield _frame(event.seq, event.type, event.data)
                sent = event.seq
                if event.type == EventType.RUN_DONE:
                    return

            while True:
                if await request.is_disconnected():
                    return
                event = await queue.get()
                if event.seq <= sent:
                    continue
                yield _frame(event.seq, event.type, event.data)
                sent = event.seq
                if event.type == EventType.RUN_DONE:
                    return
        finally:
            bus.unsubscribe(run_id, queue)

    return EventSourceResponse(gen(), ping=15, headers=_SSE_HEADERS)


def _frame(seq: int, type_: str, data: dict) -> dict:
    return {"id": str(seq), "event": type_, "data": json.dumps(data, default=str)}
