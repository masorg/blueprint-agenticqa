"""Enumerate pytest node ids via `--collect-only`."""

from __future__ import annotations

from ..execution.base import Workspace
from ..execution.limits import DISCOVER_TIMEOUT
from ..models.pyramid import DiscoveredTest


class CollectionError(RuntimeError):
    """pytest could not load the tests (an import error, a bad fixture, a timeout...).

    This must never be read as "no tests, so nothing failed": found 2026-10-15 when a real project's
    tests failed to import and the gate reported auto_approve with zero tests run."""


def parse_collect_output(stdout: str) -> list[DiscoveredTest]:
    """Parse `pytest --collect-only -q` output into discovered tests.

    The id list is one node id per line and ends at the first blank line (a
    summary and any warnings follow). A node id is the WHOLE line: parametrized
    ids routinely contain spaces, e.g. ``test_x[Manager approved: refund $500]``,
    so splitting on whitespace truncates them and the layer then cannot find its
    own tests.
    """
    tests: list[DiscoveredTest] = []
    seen: set[str] = set()
    started = False
    for raw in stdout.splitlines():
        line = raw.strip()
        if not line:
            if started:
                break
            continue
        if "::" not in line or line.startswith(("=", "_", "warning", "ERROR")) or line in seen:
            continue
        started = True
        seen.add(line)
        file = line.split("::", 1)[0]
        name = line.rsplit("::", 1)[-1]
        tests.append(DiscoveredTest(node_id=line, file=file, name=name))
    return tests


async def discover_pytest(workspace: Workspace) -> list[DiscoveredTest]:
    argv = [str(workspace.python), "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider"]
    result = await workspace.exec(argv, timeout=DISCOVER_TIMEOUT, cwd=workspace.root)
    # pytest exit codes: 0 collected, 5 no tests collected (genuinely nothing to run). Anything else
    # (2: interrupted by collection errors, 3: internal error, 4: usage error) means tests may exist
    # that we could not load, so running "the rest" would silently drop them.
    if result.timed_out or result.exit_code not in (0, 5):
        lines = [ln.strip() for ln in (result.stdout_tail + "\n" + result.stderr_tail).splitlines()]
        detail = " | ".join(ln for ln in lines if ln.startswith(("ERROR", "E ", "ImportError", "ModuleNotFoundError")))[:400]
        reason = "timed out" if result.timed_out else f"pytest exited {result.exit_code}"
        raise CollectionError(f"tests could not be collected ({reason}): {detail or result.stdout_tail[-300:]}")
    return parse_collect_output(result.stdout_tail)
