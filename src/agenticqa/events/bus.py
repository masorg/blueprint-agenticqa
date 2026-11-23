"""The single in-process pub/sub hub for run events.

Every state change goes through ``publish``: it records the event in the run's
append-only log (which assigns the monotonic per-run seq) and then fans it out
to any live SSE subscribers. Subscribers are per-connection asyncio queues;
slow/dead ones are dropped rather than allowed to block the publisher.

This is a process-wide singleton by design - the app runs single-worker
(``uvicorn --workers 1``), so one bus sees every run and every browser.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict

from ..models.events import Event
from ..store.db import RunRepository

_QUEUE_MAX = 1000


class EventBus:
    def __init__(self, repo: RunRepository) -> None:
        self._repo = repo
        self._subscribers: dict[str, set[asyncio.Queue[Event]]] = defaultdict(set)

    async def publish(self, run_id: str, type_: str, data: dict) -> Event:
        event = await self._repo.append_event(run_id, type_, data)
        for queue in list(self._subscribers.get(run_id, ())):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # Subscriber can't keep up; drop it. It will re-hydrate from the
                # snapshot + Last-Event-ID replay when it reconnects.
                self._subscribers[run_id].discard(queue)
        return event

    def subscribe(self, run_id: str) -> asyncio.Queue[Event]:
        queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=_QUEUE_MAX)
        self._subscribers[run_id].add(queue)
        return queue

    def unsubscribe(self, run_id: str, queue: asyncio.Queue[Event]) -> None:
        subs = self._subscribers.get(run_id)
        if subs:
            subs.discard(queue)
            if not subs:
                self._subscribers.pop(run_id, None)

    async def replay(self, run_id: str, after_seq: int = 0) -> list[Event]:
        """Events with seq > after_seq, for SSE reconnect (Last-Event-ID)."""
        return await self._repo.get_events(run_id, after_seq)
