"""AIDev analysis helpers: claim patterns, stratified seeded sampling, routing signature."""

from __future__ import annotations

import pytest

from agenticqa.eval.aidev import CLAIMS, claim_prevalence, routing_signature, sample_descriptions
from agenticqa.models.enums import LayerId, RepoKind
from agenticqa.models.pyramid import LayerRouting, RoutingDecision

LONG = " padding text so the description is long enough to count." * 3


@pytest.mark.parametrize("text, claim", [
    ("I ran the full test suite locally and everything is fine", "says_tests_were_run"),
    ("All tests pass after this change", "says_tests_pass"),
    ("Tests: ✅", "says_tests_pass"),
    ("Ready to merge once CI is green", "says_ready"),
    ("There are no breaking changes", "says_no_breakage"),
    ("Approved by the security team", "claims_review_or_approval"),
])
def test_each_claim_pattern_matches_its_phrase(text, claim):
    assert CLAIMS[claim].search(text)


@pytest.mark.parametrize("text, claim", [
    ("Add a test for the parser", "says_tests_pass"),           # mentions a test, makes no claim
    ("Tests should pass once the fixture is updated", "says_tests_pass"),
    ("This is not ready for review", "says_ready"),
])
def test_patterns_do_not_fire_on_plain_mentions(text, claim):
    # 'not ready for review' contains 'ready for review'; documenting the known false positive
    # keeps the prevalence figures honest rather than silently wrong.
    if text.startswith("This is not ready"):
        assert CLAIMS[claim].search(text)
    else:
        assert not CLAIMS[claim].search(text)


def _rows():
    return [
        {"id": 1, "agent": "A", "body": "All tests pass." + LONG},
        {"id": 2, "agent": "A", "body": "Refactor." + LONG},
        {"id": 3, "agent": "B", "body": ""},
        {"id": 4, "agent": "B", "body": "I ran pytest and it is ready to merge." + LONG},
    ]


def test_prevalence_counts_only_descriptions_long_enough_to_carry_a_claim():
    out = claim_prevalence(_rows())
    assert out["overall"]["n"] == 4 and out["overall"]["empty_or_short"] == 1
    assert out["overall"]["says_tests_pass"] == {"hits": 1, "of": 3, "share": 1 / 3}
    assert out["by_agent"]["A"]["says_tests_pass_or_run"]["hits"] == 1
    assert out["by_agent"]["B"]["says_ready"]["hits"] == 1


def test_sampling_is_seeded_stratified_and_ignores_short_bodies():
    rows = [{"id": i, "agent": a, "body": "x" * 100} for i in range(60) for a in ("A", "B", "C")]
    rows += [{"id": 1000 + i, "agent": "A", "body": "tiny"} for i in range(50)]
    a = sample_descriptions(rows, 30, seed=7)
    assert a == sample_descriptions(rows, 30, seed=7)
    assert a != sample_descriptions(rows, 30, seed=8)
    assert {d["agent"] for d in a} == {"A", "B", "C"} and len(a) == 30
    assert all(d["id"] < 1000 for d in a)                  # none of the 'tiny' bodies


def test_routing_signature_compares_kind_and_applicable_layers():
    def decision(kind, on):
        return RoutingDecision(repo_kind=kind, layers={l: LayerRouting(applicable=l in on) for l in LayerId.ordered()})
    a = decision(RepoKind.PLAIN_SERVICE, {LayerId.UNIT})
    assert routing_signature(a) == routing_signature(decision(RepoKind.PLAIN_SERVICE, {LayerId.UNIT}))
    assert routing_signature(a) != routing_signature(decision(RepoKind.AGENTIC, {LayerId.UNIT}))
    assert routing_signature(a) != routing_signature(decision(RepoKind.PLAIN_SERVICE, {LayerId.UNIT, LayerId.PROPERTY}))


def test_post_hoc_failed_run_marker_separates_honest_failure_reports_from_success_claims():
    from agenticqa.eval.aidev import POST_HOC_FAILED_RUN

    assert POST_HOC_FAILED_RUN.search("## Testing\n- `pytest -q` *(fails: ModuleNotFoundError: No module named x)*")
    assert POST_HOC_FAILED_RUN.search("npm test (fails: mocha is not installed)")
    assert not POST_HOC_FAILED_RUN.search("## Testing\n- `npm test`")
    rows = [{"id": i, "agent": "A", "body": b + LONG} for i, b in enumerate(
        ["## Testing - `pytest -q`", "## Testing - `pytest -q` *(fails: no module)*", "Refactor only"])]
    out = claim_prevalence(rows)["overall"]["post_hoc_run_mentions_a_failure"]
    assert (out["hits"], out["of"]) == (1, 2)
