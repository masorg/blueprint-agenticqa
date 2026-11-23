"""pytest FrameworkRunner: runs tests with pytest-reportlog and streams results.

Live results come from tailing the reportlog JSONL file, which pytest-reportlog
writes one record per test *phase* as the run proceeds -- unlike a single
end-of-session JSON report. We emit a `TestResult` the moment each test's
outcome is determined, then parse the full log for the authoritative tally.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from ..execution.base import Workspace
from ..execution.limits import LAYER_TIMEOUT
from ..models.enums import LayerId, TestOutcome
from ..models.pyramid import DiscoveredTest
from ..models.run import FailingTest, RunnerResult, TestResult
from .base import FrameworkRunner, OnEvent
from .discovery import discover_pytest

# pytest exit codes that mean "pytest itself failed", not "a test failed".
# 0=ok, 1=tests failed, 2=interrupted, 3=internal error, 4=usage, 5=no tests.
_HARD_FAILURE_CODES = {2, 3, 4}


# Test ids go on the command line until they would not fit. Operating systems cap the total length
# of arguments plus environment (about 1 MiB on macOS), and a suite with tens of thousands of
# parametrized tests blows past it: "Argument list too long" before pytest even starts.
ARG_BUDGET_CHARS = 100_000


def node_args(node_ids: list[str], root, layer_name: str, *, budget: int = ARG_BUDGET_CHARS) -> list[str]:
    """The pytest arguments that select `node_ids`: the ids themselves when they fit, otherwise a
    single `@file` argument (pytest 8.2+, one id per line, so ids containing spaces survive)."""
    if sum(len(n) + 1 for n in node_ids) <= budget:
        return list(node_ids)
    path = root / f".agenticqa-args-{layer_name}.txt"
    path.write_text("\n".join(node_ids) + "\n")
    return [f"@{path}"]


class PytestRunner(FrameworkRunner):
    name = "pytest"

    async def matches(self, workspace: Workspace) -> bool:
        root = workspace.root
        for marker in ("pytest.ini", "tox.ini", "setup.cfg", "conftest.py"):
            if (root / marker).exists():
                return True
        pyproject = root / "pyproject.toml"
        if pyproject.exists() and "[tool.pytest" in pyproject.read_text(errors="replace"):
            return True
        # Any test file anywhere shallow.
        for pattern in ("test_*.py", "*_test.py", "tests/**/*.py", "test/**/*.py"):
            if next(root.glob(pattern), None) is not None:
                return True
        return False

    async def discover(self, workspace: Workspace) -> list[DiscoveredTest]:
        return await discover_pytest(workspace)

    async def run(
        self,
        workspace: Workspace,
        node_ids: list[str],
        layer: LayerId,
        on_event: OnEvent | None = None,
    ) -> RunnerResult:
        report_log = workspace.root / f".agenticqa-report-{layer.value}.jsonl"
        report_log.unlink(missing_ok=True)

        argv = [
            str(workspace.python), "-m", "pytest",
            *node_args(node_ids, workspace.root, layer.value),
            "-p", "no:cacheprovider",
            "--report-log", str(report_log),
            "-q",
        ]

        results: list[TestResult] = []
        state = {"offset": 0, "seen": set()}

        exec_task = asyncio.create_task(workspace.exec(argv, timeout=LAYER_TIMEOUT, cwd=workspace.root))
        while not exec_task.done():
            _drain(report_log, state, results, on_event)
            await asyncio.sleep(0.15)
        exec_result = await exec_task
        _drain(report_log, state, results, on_event)  # final flush
        # The log is complete once pytest has exited: read it again from the start, so that nothing depends on
        # where the incremental reader happened to stop.
        final_state: dict = {"offset": 0, "seen": set()}
        complete: list[TestResult] = []
        _drain(report_log, final_state, complete, None)
        if len(complete) >= len(results):
            results = complete

        passed = sum(1 for r in results if r.outcome == TestOutcome.PASSED)
        failed = sum(1 for r in results if r.outcome in (TestOutcome.FAILED, TestOutcome.ERROR))
        skipped = sum(1 for r in results if r.outcome == TestOutcome.SKIPPED)
        failing = [FailingTest(node_id=r.node_id, message=r.message) for r in results
                   if r.outcome in (TestOutcome.FAILED, TestOutcome.ERROR)]

        error = ""
        missing = len(set(node_ids)) - len({r.node_id for r in results})
        if exec_result.timed_out:
            error = f"layer timed out after {LAYER_TIMEOUT}s"
        elif exec_result.exit_code not in (0, 1, 5):
            # Killed (a resource limit, a signal) or pytest itself failed: the results are partial, so nothing passed.
            error = (f"pytest ended abnormally (exit {exec_result.exit_code}); {missing} selected tests have no result: "
                     f"{exec_result.stderr_tail[-800:] or exec_result.stdout_tail[-800:]}")
        elif missing > 0:
            # Fail closed: a selected test with no recorded outcome has not been shown to pass.
            error = f"{missing} of {len(set(node_ids))} selected tests produced no result"
        elif exec_result.exit_code in _HARD_FAILURE_CODES:
            error = f"pytest failed to run (exit {exec_result.exit_code}): {exec_result.stderr_tail[-1500:] or exec_result.stdout_tail[-1500:]}"

        return RunnerResult(
            passed=passed,
            failed=failed,
            skipped=skipped,
            total=len(results),
            failing_tests=failing,
            exit_code=exec_result.exit_code,
            duration=exec_result.duration,
            results=results,
            error=error,
        )


def _drain(path: Path, state: dict, results: list[TestResult], on_event: OnEvent | None) -> None:
    """Read new complete lines from the reportlog and emit determined outcomes."""
    if not path.exists():
        return
    # Split on "\n" only: str.splitlines() also breaks at U+2028, U+0085 and similar characters, which can occur
    # unescaped inside a JSON string (a failure message about Unicode) and would cut one record in two.
    lines = path.read_text(errors="replace").split("\n")
    i = state["offset"]
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            break  # partial trailing line; retry on next drain
        i += 1
        tr = _record_to_result(rec, state["seen"])
        if tr is not None:
            results.append(tr)
            if on_event is not None:
                on_event(tr)
    state["offset"] = i


def _record_to_result(rec: dict, seen: set) -> TestResult | None:
    if rec.get("$report_type") != "TestReport":
        return None
    node = rec.get("nodeid")
    if not node or node in seen:
        return None
    when = rec.get("when")
    outcome = rec.get("outcome")

    final: TestOutcome | None = None
    if when == "call":
        final = TestOutcome.PASSED if outcome == "passed" else TestOutcome.FAILED
    elif when == "setup":
        if outcome == "skipped":
            final = TestOutcome.SKIPPED
        elif outcome in ("failed", "error"):
            final = TestOutcome.ERROR  # fixture/collection error, never reaches call
    if final is None:
        return None

    seen.add(node)
    message = _longrepr(rec) if final in (TestOutcome.FAILED, TestOutcome.ERROR) else ""
    return TestResult(node_id=node, outcome=final, duration=float(rec.get("duration") or 0.0), message=message)


def _longrepr(rec: dict) -> str:
    lr = rec.get("longrepr")
    if isinstance(lr, str):
        return lr[-2000:]
    if isinstance(lr, dict):
        crash = lr.get("reprcrash") or {}
        if crash.get("message"):
            return str(crash["message"])[-2000:]
        return json.dumps(lr)[-2000:]
    return ""
