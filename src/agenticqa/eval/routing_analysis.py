"""Analysis of the real-PR routing runs: per-router accuracy, paired comparisons
against the heuristic, the effect of hiding the repo name, and repo-level voting.

Every router saw the same pinned commits, so comparisons are *paired* (an exact
sign test on the commits where exactly one of the two is right).

Repo-level voting simulates a per-repository memory: a commit is routed by the
majority of the router's decisions on the *other* commits of the same repo
(leave-one-out, so a commit never votes on itself). The label is repo-level, so
this favours aggregation by construction, and it needs history: a brand-new repo
has none (the cold-start problem). Both caveats belong next to the number.
"""

from __future__ import annotations

import random
from collections import defaultdict

from .mutation import sign_test_p, wilson

BOOTSTRAP_RESAMPLES = 4000
BOOTSTRAP_SEED = 20261014

HEURISTIC = "stub"


def _rate(k: int, n: int) -> dict:
    lo, hi = wilson(k, n)
    return {"k": k, "n": n, "rate": (k / n) if n else None, "ci95": [round(lo, 3), round(hi, 3)]}


def cluster_bootstrap(by_repo: dict[str, list[float]], *, resamples: int = BOOTSTRAP_RESAMPLES,
                      seed: int = BOOTSTRAP_SEED) -> dict:
    """Mean of per-commit values with a 95% percentile interval from resampling whole
    REPOSITORIES. The 8 commits of a repo are not independent (a router that misses
    one smolagents commit tends to miss them all), so commit-level intervals are too
    narrow; resampling the repo as the unit is the honest one."""
    repos = sorted(by_repo)
    n_total = sum(len(v) for v in by_repo.values())
    mean = sum(sum(v) for v in by_repo.values()) / n_total
    rng = random.Random(seed)
    stats = []
    for _ in range(resamples):
        pick = [by_repo[rng.choice(repos)] for _ in repos]
        denom = sum(len(v) for v in pick)
        stats.append(sum(sum(v) for v in pick) / denom)
    stats.sort()
    lo, hi = stats[int(0.025 * resamples)], stats[int(0.975 * resamples) - 1]
    return {"mean": mean, "ci95": [round(lo, 3), round(hi, 3)], "n_repos": len(repos), "n": n_total}


def split_name(router: str) -> tuple[str, bool]:
    """'openai-gpt-4o-masked' -> ('openai-gpt-4o', True)."""
    return (router[: -len("-masked")], True) if router.endswith("-masked") else (router, False)


def repo_vote_predictions(rows: list[dict]) -> dict[tuple[str, str], str]:
    """Leave-one-out majority of the router's own decisions per repo (ties -> plain_service,
    the conservative default the product already uses)."""
    by_repo: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_repo[r["repo"]].append(r)
    out: dict[tuple[str, str], str] = {}
    for repo, rs in by_repo.items():
        for r in rs:
            others = [o["predicted"] for o in rs if o is not r]
            agentic = sum(1 for p in others if p == "agentic")
            out[(repo, r["sha"])] = "agentic" if others and agentic * 2 > len(others) else "plain_service"
    return out


def _by_repo(rows: list[dict], fn) -> dict[str, list[float]]:
    out: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        out[r["repo"]].append(float(fn(r)))
    return out


def analyze(reports: list[dict]) -> dict:
    """reports: valid route-eval reports (one per router/condition) over the same samples."""
    runs: dict[str, dict[tuple[str, str], dict]] = {}
    for rep in reports:
        runs[rep["router"]] = {(r["repo"], r["sha"]): r for r in rep["samples"]}

    # "manifest OR diff heuristic" is a deterministic combination of two committed runs, so it is
    # derived here instead of re-fetching every manifest a second time.
    if "manifest" in runs and HEURISTIC in runs:
        runs["manifest+diff"] = {
            k: {**row, "predicted": "agentic" if (row["predicted"] == "agentic"
                                                  or runs["manifest"][k]["predicted"] == "agentic")
                else "plain_service"}
            for k, row in runs[HEURISTIC].items() if k in runs["manifest"]
        }

    routers: dict[str, dict] = {}
    for name, rows in runs.items():
        lst = list(rows.values())
        ag = [r for r in lst if r["label"] == "agentic"]
        pl = [r for r in lst if r["label"] == "plain_service"]
        votes = repo_vote_predictions(lst)
        model, masked = split_name(name)
        routers[name] = {
            "model": model, "masked": masked,
            "per_commit": _rate(sum(r["predicted"] == r["label"] for r in lst), len(lst))
            | {"cluster": cluster_bootstrap(_by_repo(lst, lambda r: r["predicted"] == r["label"]))},
            "agentic_recall": _rate(sum(r["predicted"] == "agentic" for r in ag), len(ag)),
            "plain_recall": _rate(sum(r["predicted"] == "plain_service" for r in pl), len(pl)),
            "repo_vote": _rate(sum(votes[(r["repo"], r["sha"])] == r["label"] for r in lst), len(lst)),
        }

    def correct(name: str) -> dict:
        return {k: r["predicted"] == r["label"] for k, r in runs[name].items()}

    def paired(a: str, b: str) -> dict:
        A, B = correct(a), correct(b)
        keys = A.keys() & B.keys()
        x = sum(1 for k in keys if A[k] and not B[k])
        y = sum(1 for k in keys if B[k] and not A[k])
        diffs: dict[str, list[float]] = defaultdict(list)
        for k in keys:
            diffs[k[0]].append(float(A[k]) - float(B[k]))
        return {"only_a": x, "only_b": y,
                "p_value_independent": sign_test_p(x, y),  # assumes independent commits: optimistic
                "accuracy_diff": cluster_bootstrap(diffs)}  # repo-clustered: the honest interval

    vs_heuristic = {n: paired(n, HEURISTIC) for n in runs if n != HEURISTIC and HEURISTIC in runs}
    name_effect = {
        split_name(n)[0]: paired(split_name(n)[0], n)
        for n in runs if n.endswith("-masked") and split_name(n)[0] in runs
    }
    return {"n": len(next(iter(runs.values()))), "routers": routers,
            "paired_vs_heuristic": vs_heuristic, "name_effect": name_effect}
