"""Number macros: the only way a figure/number should reach the paper's prose."""

from __future__ import annotations

import re

import pytest

plots = pytest.importorskip("agenticqa.eval.plots")  # matplotlib is an optional extra


def _base(rate):
    return {"k": 1, "n": 2, "rate": rate, "ci95": [0.1, 0.9]}


MUTATION = {"summary": {"all": {
    "paired_vs_full": {k: {"only_full": 1, "only_baseline": 0, "p_value": 0.5}
                       for k in ("unit-only", "unit+property", "unit+adversarial")},
    "n_generated": 4, "n_valid": 4, "n_killed": 3, "n_survived": 1,
    "baselines": {k: _base(0.5) for k in ("unit-only", "unit+property", "unit+adversarial", "full-pyramid")},
    "per_layer": {k: {"kills": 2, "unique_kills": 1, "unique_ids": []}
                  for k in ("unit", "property", "trajectory", "llm_judge", "adversarial")},
}}}
ADV = {"summary": {"n_cases": 176, "n_scenarios": 4, "n_router_only_cases": 128,
                   "n_classifier_adversary_cases": 48, "violations": 0, "i1_verdict_violations": 0,
                   "i2_failure_count_violations": 0, "i3_coverage_violations": 0}}


def test_macro_names_are_valid_latex_control_words():
    macros = plots.build_macros(adversary_before=ADV, adversary_after=ADV, mutation=MUTATION)
    assert macros, "expected macros"
    for name in macros:
        assert re.fullmatch(r"[A-Za-z]+", name), f"{name!r} is not a valid LaTeX macro name (letters only)"


def test_zero_violations_report_a_two_sided_upper_bound():
    macros = plots.build_macros(adversary_before=None, adversary_after=ADV, mutation=None)
    assert macros["advViolationsAfter"] == 0 and macros["advCasesAfter"] == 176
    assert macros["advUpperBoundPct"] == "2.1"   # 0 of 176 => 95% upper bound ~2.1%, not the 1.7% rule of three


def test_write_macros_emits_one_newcommand_per_entry(tmp_path):
    plots.write_macros(tmp_path / "n.tex", {"fooBar": 7, "baz": "1.5"})
    text = (tmp_path / "n.tex").read_text()
    assert "\\newcommand{\\fooBar}{7}" in text and "\\newcommand{\\baz}{1.5}" in text


def _scn(name, expected, actual, kind_ok=True):
    return {"scenario": name, "verdict_correct": True, "routing": {"repo_kind_correct": kind_ok},
            "layers": {"expected_failing": expected, "actual_failing": actual}}


def test_no_model_comparison_groups_text_variants_by_the_bug_they_came_from():
    stub = {"total_wall_seconds": 10, "scenarios": [
        _scn("bug-a", ["unit"], ["unit"]),
        _scn("bug-b", ["trajectory"], ["unit"], kind_ok=False),
        _scn("bug-b+inj-authority", ["trajectory"], ["unit"], kind_ok=False),
    ]}
    model = {"total_wall_seconds": 90, "total_llm_tokens": 5000, "scenarios": [
        _scn("bug-a", ["unit"], ["unit"]),
        _scn("bug-b", ["trajectory"], ["trajectory"]),
        _scn("bug-b+inj-authority", ["trajectory"], ["trajectory"]),
    ]}
    out = plots.no_model_comparison(stub, {"m": model})
    assert out["stub"]["layers_exact"] == 1 and out["stub"]["bugs"] == 2
    assert out["stub"]["bugs_all_layers_exact"] == 1  # two scenarios of bug-b count as ONE missed bug
    assert out["models"]["m"]["layers_exact"] == 3
    macros = plots.build_macros(adversary_before=None, adversary_after=None, mutation=None, no_model=out)
    assert macros["nmStubBugsMissed"] == 1 and macros["nmModelMissMax"] == 0
