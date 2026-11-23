"""Materialize a target repo into a workspace directory (git or local copy)."""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

from .base import WorkspaceSpec

_COPY_IGNORE = shutil.ignore_patterns(
    ".git", ".venv", "venv", "__pycache__", "*.pyc", ".pytest_cache", ".hypothesis", "node_modules"
)


async def materialize(spec: WorkspaceSpec, dest: Path) -> None:
    """Populate `dest` with the target repo's code at the requested revision."""
    dest.mkdir(parents=True, exist_ok=True)
    if spec.source == "local":
        if not spec.local_path:
            raise ValueError("local source requires local_path")
        src = Path(spec.local_path).expanduser().resolve()
        if not src.is_dir():
            raise FileNotFoundError(f"local_path is not a directory: {src}")
        # copytree into an existing dir (dirs_exist_ok) so `dest` can be pre-made.
        shutil.copytree(src, dest, ignore=_COPY_IGNORE, dirs_exist_ok=True)
        return

    if spec.source == "github":
        if not spec.clone_url:
            raise ValueError("github source requires clone_url")
        await _git(["clone", "--depth", "1", spec.clone_url, str(dest)])
        if spec.head_sha:
            # Fetch the specific sha shallowly, then check it out.
            await _git(["fetch", "--depth", "1", "origin", spec.head_sha], cwd=dest)
            await _git(["checkout", spec.head_sha], cwd=dest)
        return

    raise ValueError(f"unknown source: {spec.source!r}")


async def _git(args: list[str], cwd: Path | None = None) -> None:
    proc = await asyncio.create_subprocess_exec(
        "git", *args,
        cwd=str(cwd) if cwd else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    out, _ = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed ({proc.returncode}): {out.decode(errors='replace')[-2000:]}")
