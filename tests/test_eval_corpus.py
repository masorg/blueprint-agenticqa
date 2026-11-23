"""Structural sanity checks on the eval corpus itself (src/agenticqa/eval/corpus.py).

Not a run of the harness (that needs network + a subprocess venv, exercised
manually via `agenticqa eval-run`) — just that the corpus data is internally
consistent, since a typo here silently produces a meaningless eval report
rather than a test failure.
"""

from __future__ import annotations

from agenticqa.eval.corpus import AGENTIC_CORPUS, CORPUS, FULL_CORPUS, REAL_PR_CORPUS, Scenario


def test_full_corpus_is_every_tier():
    assert FULL_CORPUS == CORPUS + AGENTIC_CORPUS + REAL_PR_CORPUS
    assert len(FULL_CORPUS) == len(CORPUS) + len(AGENTIC_CORPUS) + len(REAL_PR_CORPUS)


def test_agentic_tier_covers_one_bug_per_agentic_relevant_layer():
    bugs = {s.expected_failing_layers for s in AGENTIC_CORPUS if s.expected_failing_layers}
    assert bugs == {("trajectory",), ("llm_judge",), ("adversarial",)}
    assert all(s.expected_repo_kind == "agentic" for s in AGENTIC_CORPUS)


def test_agentic_scenario_paths_exist():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    for s in AGENTIC_CORPUS:
        assert (root / s.repo_path / "assistant.py").is_file(), s.name


def test_local_scenarios_are_source_local_with_a_repo_path():
    for s in CORPUS:
        assert s.source == "local", s.name
        assert s.repo_path, f"{s.name} needs a repo_path for a local scenario"
        assert not s.clone_url, f"{s.name} is local but has a clone_url set"


def test_github_scenarios_have_clone_url_and_pinned_sha():
    for s in REAL_PR_CORPUS:
        assert s.source == "github", s.name
        assert s.clone_url.startswith("https://github.com/"), s.name
        assert s.head_sha, f"{s.name} must pin an exact commit, not float on a branch tip"
        assert s.pr_number is not None, f"{s.name} needs a pr_number to fetch its real diff"


def test_scenario_names_are_unique():
    names = [s.name for s in FULL_CORPUS]
    assert len(names) == len(set(names)), "duplicate scenario name would silently overwrite a report entry"


def test_every_scenario_declares_an_expected_verdict_and_repo_kind():
    for s in FULL_CORPUS:
        assert s.expected_verdict in ("auto_approve", "needs_human_review"), s.name
        assert s.expected_repo_kind in ("plain_service", "agentic"), s.name


def test_scenario_defaults_are_local_and_plain_service():
    s = Scenario(name="x", repo_path="somewhere")
    assert s.source == "local"
    assert s.expected_repo_kind == "plain_service"
    assert s.expected_verdict == "auto_approve"
    assert s.pr_number is None
