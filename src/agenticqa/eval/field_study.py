"""Field study: what does the gate say about real pull requests written by AI agents?

Population and sampling are fixed here, before any pull request is run:

* AIDev (popular repositories), closed pull requests (merged or not) in repositories whose
  language is Python, with 1 to 20 commits;
* a seeded random order, stratified by agent (equal attempts per agent), with no content filter:
  choosing PRs that "look testable" is exactly the choice we must not make;
* attempts go down that order, and EVERY attempt is reported, including repositories that cannot
  be built: a project the gate cannot run is a finding, not missing data.

For each pull request the gate runs on the head commit AND on its base (the head walked back by
the PR's commit count), so a failure the PR introduced can be told apart from one that was already
there. This runs other people's tests, so it only ever runs inside a throwaway container
(`sandbox/Dockerfile`), never on the host.

Only identifiers and derived booleans are stored, never descriptions or code.
"""

from __future__ import annotations

import random
from collections import defaultdict

from .aidev import CLAIMS, MIN_CHARS, load_table
from .mutation import wilson

SEED = 20261015
MIN_COMMITS, MAX_COMMITS = 1, 20
PER_AGENT_ATTEMPTS = 14  # attempts per agent, in order; the run stops once enough PRs have completed


def build_manifest(seed: int = SEED, per_agent: int = PER_AGENT_ATTEMPTS) -> list[dict]:
    prs = load_table("pull_request", ["id", "number", "agent", "state", "merged_at", "repo_id", "body"])
    repos = {r["id"]: r for r in load_table("repository", ["id", "full_name", "language", "stars"])}
    counts: dict[int, int] = defaultdict(int)
    for c in load_table("pr_commits", ["pr_id"]):
        counts[c["pr_id"]] += 1

    pool: dict[str, list[dict]] = defaultdict(list)
    for p in prs:
        repo = repos.get(p["repo_id"])
        n = counts.get(p["id"], 0)
        if (repo and repo["language"] == "Python" and p["state"] != "open"
                and MIN_COMMITS <= n <= MAX_COMMITS):
            body = (p.get("body") or "")
            pool[p["agent"] or "unknown"].append({
                "pr_id": p["id"], "number": p["number"], "agent": p["agent"], "repo": repo["full_name"],
                "merged": p["merged_at"] is not None, "n_commits": n,
                # derived from the description, never the text itself
                "says_tests_pass": len(body.strip()) >= MIN_CHARS and bool(CLAIMS["says_tests_pass"].search(body)),
            })
    return order_candidates(pool, seed, per_agent)


def order_candidates(pool: dict[str, list[dict]], seed: int, per_agent: int) -> list[dict]:
    """Seeded shuffle within each agent, then interleaved by agent so any prefix is balanced."""
    rng = random.Random(seed)
    lists: dict[str, list[dict]] = {}
    for agent, items in sorted(pool.items()):
        items = sorted(items, key=lambda d: d["pr_id"])
        rng.shuffle(items)
        lists[agent] = items[:per_agent]
    order: list[dict] = []
    for i in range(per_agent):
        for agent in sorted(lists):
            if i < len(lists[agent]):
                order.append({**lists[agent][i], "order": len(order)})
    return order


def layout_of(pyproject: str | None, has_requirements: bool) -> str:
    """How a repository declares its dependencies at the top level, in terms of what our executor reads.

    The executor reads PEP 621 ``[project]`` (plus test extras and dependency groups) and one root
    ``requirements.txt``; it does not read Poetry's own tables."""
    import re

    if pyproject and re.search(r"^\[project\]", pyproject, re.M):
        return "standard [project] layout (read by the executor)"
    if pyproject and "[tool.poetry" in pyproject:
        return "Poetry only (not read by the executor)"
    if has_requirements:
        return "requirements.txt only"
    return "no [project] table, Poetry table or requirements.txt at the top"


def classify_failure(r: dict) -> str | None:
    """Why a field-study attempt did not run, in a few stable categories (None if it ran)."""
    if r["status"] == "ran":
        return None
    if r["status"] == "no_tests":
        return "the repository has no tests"
    if r["status"] in ("wall_timeout", "git_timeout", "fetch_failed", "container_failed", "error"):
        return "infrastructure problem (timeout, fetch or container failure)"
    err = ((r.get("head") or {}).get("error") or r.get("detail") or "")
    if "timed out" in err:
        return "timeout while building or collecting"
    if "No framework runner matched" in err:
        return "the test runner found no pytest-style tests"
    if "ModuleNotFoundError" in err or "ImportError" in err:
        return "tests import a package we did not install"
    if any(m in err for m in ("pip install", "uv pip", "No solution found", "Failed to build", "failed to build")):
        return "dependencies could not be installed"
    if r["status"] == "collection_failed":
        return "tests could not be collected (other)"
    return "could not build or run (other)"


def summarize(results: list[dict]) -> dict:
    """Funnel and findings from per-PR result files (see `agenticqa field-run`)."""
    def rate(k: int, n: int) -> dict:
        lo, hi = wilson(k, n)
        return {"k": k, "n": n, "rate": (k / n) if n else None, "ci95": [round(lo, 3), round(hi, 3)]}

    funnel: dict[str, int] = defaultdict(int)
    for r in results:
        funnel[r["status"]] += 1
    ran = [r for r in results if r["status"] == "ran"]
    with_base = [r for r in ran if r.get("base") and r["base"].get("completed")]

    def introduced(r: dict) -> list[str]:
        return sorted(set(r["head"]["failing"]) - set(r["base"]["failing"]))

    flagged_head = [r for r in ran if r["head"]["verdict"] == "needs_human_review"]
    new_fail = [r for r in with_base if introduced(r)]
    reasons: dict[str, int] = defaultdict(int)
    for r in results:
        why = classify_failure(r)
        if why:
            reasons[why] += 1
    out = {
        "attempted": len(results), "funnel": dict(funnel), "ran": len(ran), "with_base": len(with_base),
        "ran_share": rate(len(ran), len(results)), "distinct_repos_attempted": len({r["repo"] for r in results}),
        "distinct_repos_ran": len({r["repo"] for r in ran}), "failure_reasons": dict(reasons),
        "head_flagged": rate(len(flagged_head), len(ran)),
        "introduced_failures": rate(len(new_fail), len(with_base)),
        "by_agent": {}, "claims_vs_reality": {}, "outcome": {},
    }
    for agent in sorted({r["agent"] for r in with_base}):
        rs = [r for r in with_base if r["agent"] == agent]
        out["by_agent"][agent] = rate(sum(1 for r in rs if introduced(r)), len(rs))
    claim = [r for r in with_base if r["says_tests_pass"]]
    noclaim = [r for r in with_base if not r["says_tests_pass"]]
    out["claims_vs_reality"] = {
        "says_tests_pass_and_introduced_failures": rate(sum(1 for r in claim if introduced(r)), len(claim)),
        "no_claim_and_introduced_failures": rate(sum(1 for r in noclaim if introduced(r)), len(noclaim)),
    }
    merged = [r for r in with_base if r["merged"]]
    rejected = [r for r in with_base if not r["merged"]]
    out["outcome"] = {
        "merged_and_introduced_failures": rate(sum(1 for r in merged if introduced(r)), len(merged)),
        "not_merged_and_introduced_failures": rate(sum(1 for r in rejected if introduced(r)), len(rejected)),
    }
    return out
