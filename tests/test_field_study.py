"""Field study: seeded balanced ordering and the funnel/summary maths (no network, no containers)."""

from __future__ import annotations

from agenticqa.eval.field_study import order_candidates, summarize


def _pool():
    return {a: [{"pr_id": 1000 * i + j, "agent": a, "number": j, "repo": f"o/r{j}", "merged": bool(j % 2),
                 "n_commits": 2, "says_tests_pass": False} for j in range(20)]
            for i, a in enumerate(["A", "B", "C"], start=1)}


def test_ordering_is_seeded_and_every_prefix_is_balanced_by_agent():
    a, b = order_candidates(_pool(), 7, 10), order_candidates(_pool(), 7, 10)
    assert a == b and a != order_candidates(_pool(), 8, 10)
    assert [d["order"] for d in a] == list(range(30))
    first9 = [d["agent"] for d in a[:9]]
    assert sorted(first9) == ["A"] * 3 + ["B"] * 3 + ["C"] * 3


def _res(order, agent, status, merged, says, head_fail=(), base_fail=(), verdict="auto_approve", base_ok=True):
    r = {"order": order, "agent": agent, "merged": merged, "says_tests_pass": says, "status": status, "repo": "o/r", "number": order}
    if status == "ran":
        r["head"] = {"verdict": verdict, "failing": list(head_fail), "completed": True}
        r["base"] = {"failing": list(base_fail), "completed": True} if base_ok else None
    return r


def test_summary_separates_failures_the_pr_introduced_from_ones_already_there():
    results = [
        _res(0, "A", "ran", True, True, head_fail=["t1"], base_fail=[], verdict="needs_human_review"),   # introduced
        _res(1, "A", "ran", False, True, head_fail=["t1"], base_fail=["t1"], verdict="needs_human_review"),  # pre-existing
        _res(2, "B", "ran", True, False),
        _res(3, "B", "no_tests", True, False),
        _res(4, "B", "build_or_run_failed", False, False),
    ]
    s = summarize(results)
    assert s["attempted"] == 5 and s["funnel"] == {"ran": 3, "no_tests": 1, "build_or_run_failed": 1}
    assert s["head_flagged"]["k"] == 2 and s["head_flagged"]["n"] == 3
    assert (s["introduced_failures"]["k"], s["introduced_failures"]["n"]) == (1, 3)   # only the first PR
    assert s["claims_vs_reality"]["says_tests_pass_and_introduced_failures"] == {
        "k": 1, "n": 2, "rate": 0.5, "ci95": s["claims_vs_reality"]["says_tests_pass_and_introduced_failures"]["ci95"]}


def test_a_pr_without_a_usable_base_is_not_counted_in_the_introduced_failure_rate():
    results = [_res(0, "A", "ran", True, False, head_fail=["t"], verdict="needs_human_review", base_ok=False)]
    s = summarize(results)
    assert s["ran"] == 1 and s["with_base"] == 0 and s["introduced_failures"]["n"] == 0


def test_failure_reasons_are_classified_into_stable_categories():
    from agenticqa.eval.field_study import classify_failure

    def r(status, error=""):
        return {"status": status, "head": {"error": error}}

    assert classify_failure(r("ran")) is None
    assert classify_failure(r("no_tests")) == "the repository has no tests"
    assert classify_failure(r("collection_failed", "ModuleNotFoundError: No module named 'tomli'")) == \
        "tests import a package we did not install"
    assert classify_failure(r("build_or_run_failed", "command failed (1): uv pip install -r x ... No solution found")) == \
        "dependencies could not be installed"
    assert classify_failure(r("build_or_run_failed", "RuntimeError: No framework runner matched the target repository.")) == \
        "the test runner found no pytest-style tests"
    assert classify_failure(r("wall_timeout")).startswith("infrastructure")


def test_layout_of_names_what_the_executor_can_and_cannot_read():
    from agenticqa.eval.field_study import layout_of

    assert layout_of("[project]\nname='x'\ndependencies=['a']", False).startswith("standard")
    assert layout_of("[project]\nname='x'\n[tool.poetry]\nname='x'", False).startswith("standard")  # [project] wins
    assert layout_of("[tool.poetry]\nname='x'", False).startswith("Poetry only")
    assert layout_of(None, True) == "requirements.txt only"
    assert layout_of("[tool.ruff]\nline-length=100", False).startswith("no [project]")
    assert layout_of(None, False).startswith("no [project]")
