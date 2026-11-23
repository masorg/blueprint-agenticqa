"""Local executor: fresh virtualenv + subprocess per run.

Weak isolation (no network/user namespace), appropriate for trusted/internal
repos and the demo. The `Executor`/`Workspace` interface means a stronger
backend (CML Worker, container) can replace this without touching the pipeline.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import os
import shutil
import signal
import sys
import tempfile
import time
from collections.abc import AsyncIterator, Mapping, Sequence
from pathlib import Path

from .base import ExecResult, Executor, Workspace, WorkspaceSpec
from .checkout import materialize
from .deps import project_requirements
from .limits import INSTALL_TIMEOUT, MAX_OUTPUT_BYTES, preexec_limits, scrubbed_env


class SubprocessVenvExecutor(Executor):
    def __init__(self, base_dir: str | Path | None = None) -> None:
        self._base = Path(base_dir) if base_dir else Path(tempfile.gettempdir()) / "agenticqa-workspaces"
        self._base.mkdir(parents=True, exist_ok=True)
        self._cache = self._base / "venv-cache"
        self._cache.mkdir(parents=True, exist_ok=True)
        self._locks: dict[str, asyncio.Lock] = {}

    async def prepare(self, spec: WorkspaceSpec) -> Workspace:
        root = Path(tempfile.mkdtemp(prefix="ws-", dir=self._base))
        try:
            await materialize(spec, root)
            # The project under test is imported from its checkout, not installed: put the
            # repo root and its src/ layout on PYTHONPATH (the cached venv holds only deps).
            paths = [str(root)] + ([str(root / "src")] if (root / "src").is_dir() else [])
            env = dict(spec.extra_env)
            env["PYTHONPATH"] = os.pathsep.join(paths + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
            # The venv lives in a shared cache keyed by the target's requirements
            # (+ python + extra pkgs), NOT inside the per-run workspace - so a
            # repeat run of the same repo reuses it and skips the slow install.
            python = await self._venv_for(root, spec.python_version, spec.extra_pip)
            return SubprocessWorkspace(root=root, python=python, extra_env=env)
        except BaseException:
            shutil.rmtree(root, ignore_errors=True)
            raise

    def _cache_key(self, root: Path, python_version: str | None, extra_pip: list[str]) -> str:
        req = root / "requirements.txt"
        reqs = req.read_text(errors="replace") if req.exists() else ""
        h = hashlib.sha256()
        h.update((python_version or "").encode())
        h.update(reqs.encode())
        # Two projects with no requirements.txt used to hash identically and so SHARED one
        # venv (dependencies leaking between repos). Their declared deps now distinguish them.
        h.update("\n".join(project_requirements(root)).encode())
        h.update(",".join(sorted(extra_pip or [])).encode())
        return h.hexdigest()[:16]

    async def _venv_for(self, root: Path, python_version: str | None, extra_pip: list[str]) -> Path:
        """Reuse a cached venv for identical deps, else build one once and cache it."""
        key = self._cache_key(root, python_version, extra_pip)
        venv_dir = self._cache / key / ".venv"
        bindir = "Scripts" if os.name == "nt" else "bin"
        exe = "python.exe" if os.name == "nt" else "python"
        python = venv_dir / bindir / exe
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            if python.exists():
                return python  # cache hit - deps already installed
            venv_dir.parent.mkdir(parents=True, exist_ok=True)
            try:
                await self._create_venv(venv_dir, python_version)
                await self._install(root, python, extra_pip)
            except BaseException:
                shutil.rmtree(venv_dir.parent, ignore_errors=True)  # no half-built cache
                raise
            return python

    # -- venv + install ---------------------------------------------------- #
    async def _create_venv(self, venv_dir: Path, python_version: str | None) -> Path:
        uv = shutil.which("uv")
        if uv:
            args = [uv, "venv", str(venv_dir)]
            if python_version:
                args += ["--python", python_version]
            await self._run(args)
        else:
            await self._run([sys.executable, "-m", "venv", str(venv_dir)])
        bindir = "Scripts" if os.name == "nt" else "bin"
        exe = "python.exe" if os.name == "nt" else "python"
        return venv_dir / bindir / exe

    async def _install(self, root: Path, python: Path, extra_pip: list[str]) -> None:
        # "<project>" installs the checked-out project itself (non-editable), for tests that read its metadata
        pkgs = ["pytest", "pytest-reportlog", *[str(root) if x == "<project>" else x for x in (extra_pip or [])]]
        req = root / "requirements.txt"
        uv = shutil.which("uv")
        base = [uv, "pip", "install", "--python", str(python)] if uv else [str(python), "-m", "pip", "install"]
        if req.exists():
            await self._run(base + ["-r", str(req)], timeout=INSTALL_TIMEOUT)
        await self._run(base + pkgs + project_requirements(root), timeout=INSTALL_TIMEOUT)

    async def _run(self, argv: list[str], timeout: float = INSTALL_TIMEOUT) -> None:
        """Run a prepare-time command; raise with output tail on failure."""
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=scrubbed_env(),
        )
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            await proc.wait()
            raise RuntimeError(f"command timed out after {timeout}s: {' '.join(argv)}")
        if proc.returncode != 0:
            tail = out.decode(errors="replace")[-3000:]
            raise RuntimeError(f"command failed ({proc.returncode}): {' '.join(argv)}\n{tail}")


class SubprocessWorkspace(Workspace):
    def __init__(self, root: Path, python: Path, extra_env: dict[str, str] | None = None) -> None:
        self.root = root
        self.python = python
        env = scrubbed_env(extra_env)
        env["VIRTUAL_ENV"] = str(python.parent.parent)
        env["PATH"] = str(python.parent) + os.pathsep + env.get("PATH", "")
        self._env = env
        self._closed = False

    async def exec(
        self,
        argv: Sequence[str],
        *,
        timeout: float,
        env: Mapping[str, str] | None = None,
        cwd: str | Path | None = None,
        max_output_bytes: int = MAX_OUTPUT_BYTES,
    ) -> ExecResult:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(cwd or self.root),
            env=dict(env) if env is not None else self._env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            preexec_fn=preexec_limits(),
        )
        start = time.monotonic()
        timed_out = False
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            timed_out = True
            _kill(proc)
            try:
                out, err = await asyncio.wait_for(proc.communicate(), timeout=10)
            except asyncio.TimeoutError:
                out, err = b"", b""
        return ExecResult(
            exit_code=proc.returncode if proc.returncode is not None else -1,
            stdout_tail=_tail(out, max_output_bytes),
            stderr_tail=_tail(err, max_output_bytes),
            timed_out=timed_out,
            duration=time.monotonic() - start,
        )

    async def stream(
        self,
        argv: Sequence[str],
        *,
        timeout: float,
        env: Mapping[str, str] | None = None,
        cwd: str | Path | None = None,
    ) -> AsyncIterator[str]:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(cwd or self.root),
            env=dict(env) if env is not None else self._env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            preexec_fn=preexec_limits(),
        )
        deadline = time.monotonic() + timeout
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                try:
                    line = await asyncio.wait_for(proc.stdout.readline(), timeout=remaining)
                except asyncio.TimeoutError:
                    break
                if not line:
                    break
                yield line.decode(errors="replace").rstrip("\n")
            await proc.wait()
        finally:
            if proc.returncode is None:
                _kill(proc)
                with contextlib.suppress(Exception):
                    await proc.wait()

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        shutil.rmtree(self.root, ignore_errors=True)


def _kill(proc: asyncio.subprocess.Process) -> None:
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        with contextlib.suppress(ProcessLookupError):
            proc.kill()


def _tail(data: bytes, limit: int) -> str:
    text = data.decode(errors="replace")
    return text[-limit:] if len(text) > limit else text
