"""In-memory repository for runs and their append-only event logs.

Single process: state lives in plain dicts guarded by one asyncio lock. The
public API is intentionally async and matches what a durable (e.g. SQLite)
backend would expose, so persistence can be dropped in later without touching
the orchestrator or API layers. Nothing here survives a process restart - fine
for the live demo; approval-after-restart is a Phase 5 concern.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from ..models.enums import RunState
from ..models.events import Event
from ..models.run import QARun


class RunRepository:
    def __init__(self, db_path: str | None = None) -> None:
        self._lock = asyncio.Lock()
        self._runs: dict[str, str] = {}  # run_id -> QARun JSON blob
        self._order: list[str] = []  # insertion order, newest last
        self._events: dict[str, list[Event]] = {}

    async def connect(self) -> None:  # parity with a durable backend
        return None

    async def close(self) -> None:
        return None

    # ---- runs ----------------------------------------------------------

    async def save_run(self, run: QARun) -> None:
        run.touch()
        blob = run.model_dump_json()
        async with self._lock:
            if run.id not in self._runs:
                self._order.append(run.id)
                self._events.setdefault(run.id, [])
            self._runs[run.id] = blob

    async def get_run(self, run_id: str) -> QARun | None:
        async with self._lock:
            blob = self._runs.get(run_id)
        return QARun.model_validate_json(blob) if blob else None

    async def list_runs(self, limit: int = 50) -> list[QARun]:
        async with self._lock:
            ids = list(reversed(self._order))[:limit]
            blobs = [self._runs[i] for i in ids]
        return [QARun.model_validate_json(b) for b in blobs]

    async def latest_run_for_pr(
        self, owner: str, name: str, number: int | None
    ) -> QARun | None:
        """Most recent run for a repo + PR number (webhook comment → run lookup)."""
        async with self._lock:
            ids = list(reversed(self._order))
            blobs = [self._runs[i] for i in ids]
        for b in blobs:
            run = QARun.model_validate_json(b)
            if run.repo.owner == owner and run.repo.name == name and run.pr.number == number:
                return run
        return None

    async def non_terminal_run_ids(self) -> list[str]:
        terminal = {RunState.DONE, RunState.FAILED, RunState.CANCELLED}
        async with self._lock:
            blobs = list(self._runs.values())
        out = []
        for b in blobs:
            run = QARun.model_validate_json(b)
            if run.state not in terminal:
                out.append(run.id)
        return out

    # ---- events --------------------------------------------------------

    async def append_event(self, run_id: str, type_: str, data: dict) -> Event:
        """Assign the next per-run seq and record the event, atomically."""
        async with self._lock:
            log = self._events.setdefault(run_id, [])
            seq = len(log) + 1
            event = Event(seq=seq, type=type_, data=data, ts=datetime.now(timezone.utc))
            log.append(event)
            return event

    async def get_events(self, run_id: str, after_seq: int = 0) -> list[Event]:
        async with self._lock:
            log = list(self._events.get(run_id, []))
        return [e for e in log if e.seq > after_seq]
