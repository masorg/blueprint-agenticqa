"""Select a FrameworkRunner for a prepared workspace."""

from __future__ import annotations

from ..execution.base import Workspace
from .base import FrameworkRunner
from .pytest_runner import PytestRunner


def default_runners() -> list[FrameworkRunner]:
    # Order matters: first match wins. Python-first today.
    return [PytestRunner()]


async def select_runner(workspace: Workspace) -> FrameworkRunner | None:
    for runner in default_runners():
        if await runner.matches(workspace):
            return runner
    return None
