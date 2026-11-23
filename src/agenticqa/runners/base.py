"""The FrameworkRunner interface."""

from __future__ import annotations

import abc
from collections.abc import Callable

from ..execution.base import Workspace
from ..models.enums import LayerId
from ..models.pyramid import DiscoveredTest
from ..models.run import RunnerResult, TestResult

# Called once per test as its outcome is determined (for live UI updates).
OnEvent = Callable[[TestResult], None]


class FrameworkRunner(abc.ABC):
    name: str

    @abc.abstractmethod
    async def matches(self, workspace: Workspace) -> bool:
        """Whether this runner can handle the repo in `workspace`."""

    @abc.abstractmethod
    async def discover(self, workspace: Workspace) -> list[DiscoveredTest]:
        """Enumerate the repo's tests without running them."""

    @abc.abstractmethod
    async def run(
        self,
        workspace: Workspace,
        node_ids: list[str],
        layer: LayerId,
        on_event: OnEvent | None = None,
    ) -> RunnerResult:
        """Run the given tests, emitting per-test events, return structured result."""
