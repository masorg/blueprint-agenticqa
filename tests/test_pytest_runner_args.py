"""Selecting many tests without exceeding the OS argument limit.

Found 2026-10-15 running a wider set of real projects: `packaging` has so many parametrized tests
that passing every id on the command line failed with "Argument list too long" before pytest
started, so the honest run could not even complete.
"""

from __future__ import annotations

import subprocess
import sys

from agenticqa.runners.pytest_runner import ARG_BUDGET_CHARS, node_args


def test_few_ids_stay_on_the_command_line(tmp_path):
    ids = ["tests/test_a.py::test_one", "tests/test_a.py::test_two"]
    assert node_args(ids, tmp_path, "unit") == ids
    assert not list(tmp_path.glob(".agenticqa-args-*"))


def test_too_many_ids_go_into_an_argument_file_one_per_line(tmp_path):
    ids = [f"tests/test_big.py::test_case[param {i}]" for i in range(5000)]
    assert sum(len(i) + 1 for i in ids) > ARG_BUDGET_CHARS
    args = node_args(ids, tmp_path, "unit")
    assert len(args) == 1 and args[0].startswith("@")
    assert (tmp_path / ".agenticqa-args-unit.txt").read_text().splitlines() == ids


def test_pytest_reads_ids_with_spaces_from_the_argument_file(tmp_path):
    """The real behaviour, not a mock: pytest must run exactly the listed tests, spaces and all."""
    (tmp_path / "test_x.py").write_text(
        "import pytest\n"
        "@pytest.mark.parametrize('v', ['a b', 'c d', 'e f'])\n"
        "def test_p(v):\n    assert v\n"
        "def test_other():\n    assert True\n"
    )
    ids = ["test_x.py::test_p[a b]", "test_x.py::test_p[e f]"]
    args = node_args(ids, tmp_path, "unit", budget=1)          # force the file path
    out = subprocess.run([sys.executable, "-m", "pytest", *args, "-q", "-p", "no:cacheprovider"],
                         cwd=tmp_path, capture_output=True, text=True)
    assert out.returncode == 0, out.stdout + out.stderr
    assert "2 passed" in out.stdout                              # not test_other, not 'c d'


def test_report_log_with_unicode_line_separators_is_read_completely(tmp_path):
    """REGRESSION (found by the idna attack): str.splitlines() splits at U+2028 and U+0085, which json.dumps(ensure_ascii=False)
    leaves raw inside a string. One such failure message stopped the reader and dropped every later result."""
    import json

    from agenticqa.runners.pytest_runner import _drain

    def rec(node, outcome="passed", longrepr=""):
        return json.dumps({"$report_type": "TestReport", "nodeid": node, "when": "call", "outcome": outcome,
                           "duration": 0.0, "longrepr": longrepr}, ensure_ascii=False)

    log = tmp_path / "r.jsonl"
    log.write_text("\n".join([rec("a"), rec("b", "failed", "bad   char and \u0085 more"), rec("c"), rec("d")]) + "\n")
    state = {"offset": 0, "seen": set()}
    out = []
    _drain(log, state, out, None)
    assert [r.node_id for r in out] == ["a", "b", "c", "d"]


async def test_a_killed_pytest_with_partial_results_is_an_error_not_a_pass(tmp_path):
    """REGRESSION (found by the idna attack): RLIMIT_CPU killed pytest part-way; the layer was reported from the
    partial results, so a run whose recorded tests all passed would have been approved."""
    import json

    from agenticqa.execution.base import ExecResult
    from agenticqa.models.enums import LayerId
    from agenticqa.runners.pytest_runner import PytestRunner

    class _WS:
        root = tmp_path
        python = "python"

        async def exec(self, argv, **kw):
            log = tmp_path / ".agenticqa-report-unit.jsonl"
            log.write_text(json.dumps({"$report_type": "TestReport", "nodeid": "t1", "when": "call",
                                       "outcome": "passed", "duration": 0.0}) + "\n")
            return ExecResult(exit_code=-24, stdout_tail="", stderr_tail="", timed_out=False, duration=1.0)

    res = await PytestRunner().run(_WS(), ["t1", "t2", "t3"], LayerId.UNIT)
    assert res.error and "no result" in res.error
