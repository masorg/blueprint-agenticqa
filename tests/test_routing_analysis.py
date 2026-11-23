"""Routing analysis: paired comparisons and the leave-one-out repo vote."""

from __future__ import annotations

from agenticqa.eval.routing_analysis import analyze, repo_vote_predictions, split_name


def _row(repo, sha, label, pred):
    return {"repo": repo, "sha": sha, "label": label, "predicted": pred}


def test_split_name():
    assert split_name("openai-gpt-4o-masked") == ("openai-gpt-4o", True)
    assert split_name("stub") == ("stub", False)


def test_repo_vote_is_leave_one_out_and_ties_go_to_plain():
    # 3 commits of one agentic repo; the router says agentic for 2 of them.
    rows = [_row("r", "a", "agentic", "agentic"), _row("r", "b", "agentic", "agentic"),
            _row("r", "c", "agentic", "plain_service")]
    v = repo_vote_predictions(rows)
    assert v[("r", "c")] == "agentic"        # the other two say agentic -> rescued
    assert v[("r", "a")] == "plain_service"  # others: agentic, plain -> tie -> conservative default
    assert repo_vote_predictions([_row("solo", "x", "agentic", "agentic")])[("solo", "x")] == "plain_service"


def _report(router, preds):
    rows = [_row("r1", f"s{i}", "agentic", p) for i, p in enumerate(preds)]
    return {"router": router, "samples": rows}


def test_analysis_pairs_routers_on_the_same_commits_and_reports_the_name_effect():
    rep = [
        _report("stub", ["plain_service"] * 4),
        _report("m", ["agentic", "agentic", "agentic", "plain_service"]),
        _report("m-masked", ["agentic", "plain_service", "plain_service", "plain_service"]),
    ]
    out = analyze(rep)
    assert out["routers"]["m"]["per_commit"]["k"] == 3 and out["routers"]["stub"]["per_commit"]["k"] == 0
    pv = out["paired_vs_heuristic"]["m"]
    assert (pv["only_a"], pv["only_b"], pv["p_value_independent"]) == (3, 0, 0.25)
    assert out["name_effect"]["m"]["only_a"] == 2 and out["name_effect"]["m"]["only_b"] == 0
    assert out["routers"]["m-masked"]["masked"] is True


def test_cluster_bootstrap_uses_repos_as_the_unit_and_is_wider_than_commit_level():
    from agenticqa.eval.routing_analysis import cluster_bootstrap
    from agenticqa.eval.mutation import wilson

    # 6 repos x 8 commits: three repos always right, three always wrong -> strong clustering.
    by_repo = {f"r{i}": [1.0 if i < 3 else 0.0] * 8 for i in range(6)}
    out = cluster_bootstrap(by_repo)
    lo, hi = out["ci95"]
    w_lo, w_hi = wilson(24, 48)
    assert out["mean"] == 0.5 and out["n_repos"] == 6
    assert (hi - lo) > (w_hi - w_lo)                      # honest interval is wider
    assert cluster_bootstrap(by_repo) == out              # seeded: reproducible


def test_manifest_plus_diff_is_derived_as_the_or_of_the_two_runs():
    rep = [
        _report("stub", ["agentic", "plain_service", "plain_service", "plain_service"]),
        _report("manifest", ["plain_service", "agentic", "plain_service", "plain_service"]),
    ]
    out = analyze(rep)
    assert out["routers"]["manifest+diff"]["per_commit"]["k"] == 2   # commits 0 and 1 are caught by one side
    assert out["routers"]["stub"]["per_commit"]["k"] == 1 and out["routers"]["manifest"]["per_commit"]["k"] == 1
