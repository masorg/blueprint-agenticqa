"""Eval report plumbing: corpus fingerprint stability and the catch-matrix grid."""

from __future__ import annotations

from agenticqa.eval.corpus import CORPUS, Scenario
from agenticqa.eval.metrics import _catch_matrix, _provenance_lines
from agenticqa.eval.provenance import corpus_fingerprint


def test_fingerprint_is_stable_and_sensitive_to_scenario_content():
    assert corpus_fingerprint(CORPUS) == corpus_fingerprint(list(CORPUS))
    changed = [Scenario(name="x", expected_verdict="auto_approve"), *CORPUS[1:]]
    assert corpus_fingerprint(changed) != corpus_fingerprint(CORPUS)


def test_catch_matrix_marks_failures_and_unrun_layers():
    scenario = {
        "scenario": "s1",
        "layers": {"states": {"unit": "failed", "property": "passed", "adversarial": "error"}},
    }
    header, _, row = _catch_matrix([scenario])
    cols = [c.strip() for c in header.strip("|").split("|")]
    cells = dict(zip(cols[1:], [c.strip() for c in row.strip("|").split("|")][1:]))
    assert cells["unit"] == "FAIL" and cells["property"] == "pass" and cells["adversarial"] == "ERR"
    assert cells["trajectory"] == "·"  # absent from states = layer never ran


def test_provenance_lines_flag_a_dirty_tree():
    text = "\n".join(_provenance_lines({"git_sha": "a" * 40, "code_dirty": True, "corpus": "full"}))
    assert "DIRTY" in text and "a" * 10 in text
    assert _provenance_lines({}) == []


# ---- the evaluator's answer key must never reach the system under test --------
#
# Found 2026-10-14: the harness passed `Scenario.notes` (which says things like
# "the one scenario where the agentic-only layers SHOULD apply") as the PR body,
# and the router prompt includes the PR body. Every LLM routing result before
# this fix was contaminated.

from pathlib import Path  # noqa: E402

from agenticqa.eval.corpus import FULL_CORPUS  # noqa: E402
from agenticqa.eval.harness import build_run  # noqa: E402
from agenticqa.llm.prompts.routing import build_routing_prompt  # noqa: E402


def test_scenario_notes_never_reach_the_routing_prompt():
    for s in FULL_CORPUS:
        assert s.notes, "every scenario documents itself"
        run = build_run(s, repo_root=Path("."), diff=s.diff, pr_title=s.pr_title, pr_body=s.pr_body)
        system, user = build_routing_prompt(run)
        assert s.notes[:60] not in user and s.notes[:60] not in system, s.name
        assert "SHOULD" not in user, s.name


# ---- a baseline that never ran must not make the invariants pass -----------------


def test_an_unusable_baseline_is_an_error_not_a_pass():
    """If the honest run could not build, every hostile run fails identically, and 'same verdict,
    same failures, same coverage' would hold trivially. That has to be 'could not run'."""
    import pytest

    from agenticqa.eval.adversaries import BaselineFailed, check_baseline

    check_baseline("needs_human_review", 18, "awaiting_approval", "")       # usable: no error
    check_baseline("auto_approve", 5, "done", "")
    with pytest.raises(BaselineFailed):
        check_baseline(None, 0, "failed", "command failed (1): uv pip install ...")
    with pytest.raises(BaselineFailed):
        check_baseline("auto_approve", 0, "done", "")                        # ran nothing
