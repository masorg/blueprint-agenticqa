"""In-process event bus: append to the run's log, fan out to SSE subscribers."""

from .bus import EventBus

__all__ = ["EventBus"]
