"""A project whose tests cannot be loaded must not be approved as "nothing failed".

Found 2026-10-15 in the field study: a real project's tests raised ImportError at collection, our
parser skipped the ERROR line, found zero tests, and the gate answered auto_approve.
"""

from __future__ import annotations

import pytest

from agenticqa.execution.base import ExecResult
from agenticqa.runners.discovery import CollectionError, discover_pytest


class _FakeWorkspace:
    python = "python"
    root = "."

    def __init__(self, exit_code: int, stdout: str = "", stderr: str = "", timed_out: bool = False) -> None:
        self._r = ExecResult(exit_code=exit_code, stdout_tail=stdout, stderr_tail=stderr,
                             timed_out=timed_out, duration=0.1)

    async def exec(self, argv, *, timeout, cwd=None, **kw):
        return self._r


async def test_a_clean_collection_returns_the_tests():
    out = await discover_pytest(_FakeWorkspace(0, "tests/test_a.py::test_one\n\n1 test collected in 0.01s\n"))
    assert [t.node_id for t in out] == ["tests/test_a.py::test_one"]


async def test_no_tests_at_all_is_not_an_error():
    assert await discover_pytest(_FakeWorkspace(5, "\nno tests collected in 0.01s\n")) == []


async def test_an_import_error_during_collection_raises_instead_of_looking_like_no_tests():
    stdout = ("ERROR tests/test_bad.py\n\n=== short test summary info ===\n"
              "ERROR tests/test_bad.py\n!!! Interrupted: 1 error during collection !!!\n")
    with pytest.raises(CollectionError, match="could not be collected"):
        await discover_pytest(_FakeWorkspace(2, stdout))


async def test_partial_collection_failure_does_not_silently_drop_the_broken_module():
    # other modules collected fine, but running "the rest" would hide the module that failed to load
    stdout = "tests/test_ok.py::test_fine\nERROR tests/test_bad.py\n\n1 test collected, 1 error\n"
    with pytest.raises(CollectionError):
        await discover_pytest(_FakeWorkspace(2, stdout))


async def test_a_collection_timeout_is_an_error_too():
    with pytest.raises(CollectionError, match="timed out"):
        await discover_pytest(_FakeWorkspace(-1, timed_out=True))
