"""Executor / Workspace interfaces for isolated execution of untrusted code."""

from __future__ import annotations

import abc
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class WorkspaceSpec:
    """Describes what to materialize before running tests.

    Exactly one source is used: a git checkout (`clone_url` + `head_sha`) or a
    local path copy (`local_path`).
    """

    source: str  # "github" | "local"
    clone_url: str | None = None
    head_sha: str | None = None
    local_path: str | None = None
    python_version: str | None = None
    extra_pip: list[str] = field(default_factory=list)
    # Non-secret env vars the target repo's tests need at runtime (merged into
    # the scrubbed subprocess env). Secrets are stripped regardless.
    extra_env: dict[str, str] = field(default_factory=dict)


@dataclass
class ExecResult:
    exit_code: int
    stdout_tail: str
    stderr_tail: str
    timed_out: bool
    duration: float


class Workspace(abc.ABC):
    """An isolated, prepared directory with a usable Python interpreter.

    Use as an async context manager; `aclose()` tears down the venv + directory.
    """

    root: Path
    python: Path  # interpreter to run the checked-out project's code/tests with

    @abc.abstractmethod
    async def exec(
        self,
        argv: Sequence[str],
        *,
        timeout: float,
        env: Mapping[str, str] | None = None,
        cwd: str | Path | None = None,
        max_output_bytes: int = 1_000_000,
    ) -> ExecResult:
        """Run argv to completion under limits; return captured tails + status."""

    @abc.abstractmethod
    def stream(
        self,
        argv: Sequence[str],
        *,
        timeout: float,
        env: Mapping[str, str] | None = None,
        cwd: str | Path | None = None,
    ) -> AsyncIterator[str]:
        """Run argv and yield stdout lines as they arrive (line-buffered)."""

    @abc.abstractmethod
    async def aclose(self) -> None:
        """Tear down the workspace. Safe to call more than once."""

    async def __aenter__(self) -> "Workspace":
        return self

    async def __aexit__(self, *exc) -> None:
        await self.aclose()


class Executor(abc.ABC):
    """Prepares a `Workspace` from a `WorkspaceSpec`."""

    @abc.abstractmethod
    async def prepare(self, spec: WorkspaceSpec) -> Workspace:
        """Check out code, create a fresh env, install deps. May take minutes."""
