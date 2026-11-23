"""SSE event envelope and the set of event types the dashboard consumes."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from pydantic import BaseModel, Field


class EventType:
    """String constants for SSE `event:` names (kept in one place)."""

    RUN_STATE = "run.state"
    ROUTE_DECISION = "route.decision"
    PLAN_READY = "plan.ready"
    LAYER_STATE = "layer.state"
    TEST_RESULT = "test.result"
    TRIAGE_REPORT = "triage.report"
    APPROVAL_STATE = "approval.state"
    RUN_LOG = "run.log"
    RUN_DONE = "run.done"
    ERROR = "error"


class Event(BaseModel):
    seq: int
    type: str
    data: dict = Field(default_factory=dict)
    ts: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def to_sse(self) -> str:
        """Render as a Server-Sent Events frame."""
        payload = json.dumps(self.data, default=str)
        return f"id: {self.seq}\nevent: {self.type}\ndata: {payload}\n\n"
