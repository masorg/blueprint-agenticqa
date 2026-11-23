"""Mutation experiment plumbing: site enumeration, application, sampling, summary."""

from __future__ import annotations

from agenticqa.eval.mutation import (
    MutantResult, apply_site, enumerate_sites, is_valid_python, sample_sites, summarize, wilson,
)

SRC = '''\
import os

def _bug_on():
    return os.environ.get("X") == "1"

def check(stock, qty, ok):
    """Docstring with a number 42 and a < b."""
    if stock < qty and ok:
        raise ValueError("no")
    if _bug_on():
        return stock + 99
    return stock - qty if not ok else 7
'''


def _ops(sites):
    return sorted({s.operator for s in sites})


def test_every_operator_is_found_and_flag_code_and_docstrings_are_excluded():
    sites = enumerate_sites(SRC)
    assert {"cmp-flip", "bool-swap", "raise-drop", "if-negate", "not-drop", "arith-swap", "const-shift"} <= set(_ops(sites))
    lines = {s.line for s in sites}
    assert 4 not in lines          # inside _bug_on
    assert 7 not in lines          # docstring
    assert 11 not in lines and 10 not in lines  # `if _bug_on():` block is bug plumbing


def test_apply_site_changes_only_the_target_span():
    site = next(s for s in enumerate_sites(SRC) if s.operator == "cmp-flip")
    out = apply_site(SRC, site)
    assert "stock <= qty" in out and out.count("<=") == 1
    assert out.replace("<=", "<", 1) == SRC


def test_non_ascii_source_keeps_offsets_correct():
    src = "# café — note\ndef f(a, b):\n    return a < b\n"
    site = next(s for s in enumerate_sites(src) if s.operator == "cmp-flip")
    assert apply_site(src, site).endswith("return a <= b\n")


def test_invalid_mutants_are_detected():
    assert is_valid_python("x = 1")
    assert not is_valid_python("def (:")


def test_sampling_is_deterministic_and_stratified_by_operator():
    from agenticqa.eval.mutation import Site

    pool = [Site(op, i, i, i + 1, "x", "y") for op in ("cmp-flip", "const-shift", "raise-drop") for i in range(10)]
    a = sample_sites(pool, 6, seed=1)
    assert a == sample_sites(pool, 6, seed=1)             # fixed seed -> fixed draw
    assert a != sample_sites(pool, 6, seed=2)             # a different seed draws differently
    counts = {op: sum(1 for s in a if s.operator == op) for op in ("cmp-flip", "const-shift", "raise-drop")}
    assert counts == {"cmp-flip": 2, "const-shift": 2, "raise-drop": 2}   # round-robin: none dominates
    assert len(sample_sites(pool, 100, seed=1)) == 30     # asking for more returns everything


def _r(rid, states, op="cmp-flip"):
    return MutantResult("s", rid, op, 1, "d", states=states)


def test_summary_counts_unique_kills_and_excludes_invalid_mutants():
    results = [
        _r("m1", {"unit": "failed", "property": "failed", "adversarial": "passed"}),
        _r("m2", {"unit": "passed", "property": "failed", "adversarial": "passed"}),
        _r("m3", {"unit": "passed", "property": "passed", "adversarial": "failed"}),
        _r("m4", {"unit": "passed", "property": "passed", "adversarial": "passed"}),
        _r("m5", {"unit": "error"}),  # broke collection: invalid, never counted as a kill
    ]
    s = summarize(results)
    assert (s["n_generated"], s["n_valid"], s["n_invalid"], s["n_killed"], s["n_survived"]) == (5, 4, 1, 3, 1)
    assert s["per_layer"]["unit"] == {"kills": 1, "unique_kills": 0, "unique_ids": []}
    assert s["per_layer"]["property"]["unique_ids"] == ["m2"]
    assert s["per_layer"]["adversarial"]["unique_ids"] == ["m3"]
    assert s["baselines"]["unit-only"]["k"] == 1 and s["baselines"]["full-pyramid"]["k"] == 3
    assert s["survivor_ids"] == ["m4"]


def test_wilson_interval_is_sane():
    lo, hi = wilson(0, 176)
    assert lo < 1e-9 and 0.02 < hi < 0.023   # "0 of 176" => 95% upper bound ~2.1%
    lo, hi = wilson(50, 100)
    assert 0.39 < lo < 0.41 and 0.59 < hi < 0.61
    assert wilson(0, 0) == (0.0, 0.0)


def test_sign_test_matches_hand_computed_values():
    from agenticqa.eval.mutation import sign_test_p

    assert sign_test_p(0, 0) == 1.0
    assert abs(sign_test_p(8, 0) - 2 / 2**8) < 1e-12        # 0.0078
    assert abs(sign_test_p(11, 0) - 2 / 2**11) < 1e-12
    assert abs(sign_test_p(5, 5) - 1.0) < 1e-12             # perfectly balanced: no evidence
    assert 0.2 < sign_test_p(3, 7) < 0.4


def test_summary_pairs_each_baseline_against_the_full_pyramid():
    results = [
        _r("m1", {"unit": "failed", "property": "failed", "adversarial": "passed"}),
        _r("m2", {"unit": "passed", "property": "failed", "adversarial": "passed"}),
        _r("m3", {"unit": "passed", "property": "passed", "adversarial": "failed"}),
    ]
    paired = summarize(results)["paired_vs_full"]
    assert paired["unit-only"] == {"only_full": 2, "only_baseline": 0, "p_value": 0.5}
    assert paired["unit+adversarial"]["only_full"] == 1   # m2 needs the property layer
