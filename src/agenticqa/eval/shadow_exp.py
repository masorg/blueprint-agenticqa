"""Shadow replay: GATE on the real pull-request traffic of open-source projects, without posting anything.

For each project the manifest names the most recent pull requests, fixed before any is run. For each pull
request we take its head and the merge base with the default branch, which is the change a CI check sees, and:

* run the whole suite at the merge base and at the head, which is what a plain CI check does;
* compute what GATE selects from the diff between them, and whether GATE would approve, ask for review because
  no test reaches a changed file, or run everything;
* call a test *introduced* if it fails at the head and passes at the merge base, and confirm it with two reruns
  on each side, so that flaky tests do not count.

GATE *misses* a pull request if it introduces a failing test and GATE neither selects that test nor sends the
change to a person. Pull-request heads are code by unknown authors, so this only ever runs in a throwaway
container (`sandbox/Dockerfile`), one per pull request.
"""

from __future__ import annotations

import json
import statistics as st
import subprocess
import tempfile
import time
from pathlib import Path

from .mutation import wilson

PER_PROJECT = 25


def _pip_install(python, *pkgs: str) -> list[str]:
    """Install into an environment: through uv when present (its environments carry no pip), else pip."""
    import shutil as _sh

    uv = _sh.which("uv")
    return [uv, "pip", "install", "-q", "--python", str(python), *pkgs] if uv else [str(python), "-m", "pip", "install", "-q", *pkgs]


def _git(cwd: Path, *args: str, timeout: int = 900) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=timeout)


def recent_pulls(repo: str, n: int = PER_PROJECT) -> tuple[str, list[int]]:
    """The default branch and the n highest-numbered pull requests, read with `git ls-remote` (no API)."""
    url = f"https://github.com/{repo}.git"
    head = subprocess.run(["git", "ls-remote", "--symref", url, "HEAD"], capture_output=True, text=True, timeout=300)
    branch = "main"
    for line in head.stdout.splitlines():
        if line.startswith("ref:"):
            branch = line.split()[1].removeprefix("refs/heads/")
    refs = subprocess.run(["git", "ls-remote", url, "refs/pull/*/head"], capture_output=True, text=True, timeout=600)
    nums = sorted({int(line.split("refs/pull/")[1].split("/")[0]) for line in refs.stdout.splitlines()
                   if "refs/pull/" in line}, reverse=True)
    return branch, nums[:n]


async def shadow_pr(repo: str, number: int, branch: str, extra_pip: list[str], map_path: str | None = None) -> dict:
    """INSIDE A CONTAINER ONLY. GATE and a full CI run on one pull request."""
    from ..execution.base import WorkspaceSpec
    from ..execution.subprocess_venv import SubprocessVenvExecutor
    from ..models.enums import LayerId
    from ..runners.pytest_runner import PytestRunner
    from ..selection import RepoGraph, select_tests

    out: dict = {"repo": repo, "number": number, "status": "error"}
    t_start = time.monotonic()
    tmp = Path(tempfile.mkdtemp(prefix="shadow-"))
    try:
        _git(tmp, "init", "-q")
        _git(tmp, "remote", "add", "origin", f"https://github.com/{repo}.git")
        if _git(tmp, "fetch", "-q", "--filter=blob:none", "origin", f"+refs/heads/{branch}:refs/remotes/origin/{branch}",
                f"+refs/pull/{number}/head:refs/pr").returncode:
            out["status"] = "fetch_failed"
            return out
        head = _git(tmp, "rev-parse", "refs/pr").stdout.strip()
        base = _git(tmp, "merge-base", f"origin/{branch}", "refs/pr").stdout.strip()
        if not base:
            out["status"] = "no_merge_base"
            return out
        diff = _git(tmp, "diff", "--no-color", "--no-ext-diff", base, head).stdout
        out.update(head=head, base=base, diff_bytes=len(diff))
        if not diff.strip():
            out["status"] = "empty_diff"
            return out

        runner = PytestRunner()
        executor = SubprocessVenvExecutor()

        async def full(sha: str) -> tuple[dict, object, list]:
            _git(tmp, "checkout", "-q", "--force", sha)
            ws = await executor.prepare(WorkspaceSpec(source="local", local_path=str(tmp), extra_pip=extra_pip))
            tests = await runner.discover(ws)
            t0 = time.monotonic()
            res = await runner.run(ws, [t.node_id for t in tests], LayerId.UNIT)
            return ({"failing": sorted({f.node_id for f in res.failing_tests}), "n": len(tests),
                     "error": res.error[:200], "seconds": round(time.monotonic() - t0, 1),
                     "durations": {r.node_id: r.duration for r in res.results}}, ws, tests)

        b, ws_b, _ = await full(base)
        cmap = None
        if map_path:  # a stored recording, made once for the project at an older commit
            stored = json.loads(Path(map_path).read_text())
            if stored.get("status") == "ok":
                cmap = map_from_json(stored["map"])
                out["map_base"] = stored["base"]
                out["map_age_commits"] = int(_git(tmp, "rev-list", "--count", f"{stored['base']}..{base}").stdout.strip() or -1)
            await ws_b.aclose()
        try:  # the dynamic core: one recorded run of the base under coverage with per-test contexts
            if map_path:
                raise StopIteration  # the stored map stands in for a fresh recording

            from ..selection import CoverageMap

            await ws_b.exec(_pip_install(ws_b.python, "pytest-cov"), timeout=600, cwd=ws_b.root)
            rc, db = ws_b.root / ".agenticqa-coveragerc", ws_b.root / ".agenticqa-coverage"
            rc.write_text(f"[run]\nbranch = False\nsource = .\ndata_file = {db}\n")
            t0 = time.monotonic()
            cr = await ws_b.exec([str(ws_b.python), "-m", "pytest", "-q", "-p", "no:cacheprovider", "--cov=.",
                                  f"--cov-config={rc}", "--cov-context=test", "--cov-report=",
                                  "-W", "ignore::coverage.exceptions.CoverageWarning"], timeout=1800, cwd=ws_b.root,
                                 env=dict(getattr(ws_b, "_env", {}), COVERAGE_CORE="ctrace"))
            out["coverage_seconds"] = round(time.monotonic() - t0, 1)
            if db.exists() and cr.exit_code in (0, 1):
                cmap = CoverageMap.from_sqlite(db, RepoGraph(ws_b.root), ws_b.root)
                out["coverage_files"] = len(cmap.files)
            else:
                out["coverage_error"] = f"exit {cr.exit_code}: " + (cr.stderr_tail or cr.stdout_tail)[-300:]
        except StopIteration:
            pass
        except Exception as exc:  # noqa: BLE001 - without a map the static core is used, and that is recorded
            out["coverage_error"] = f"{type(exc).__name__}: {exc}"[:200]
        if not map_path:
            await ws_b.aclose()
        h, ws_h, tests = await full(head)
        try:
            if b["error"] or h["error"]:
                out.update(status="run_error", detail=(b["error"] or h["error"]))
                return out
            suspects = sorted(set(h["failing"]) - set(b["failing"]))
            confirmed: list[str] = []
            if suspects:  # rerun twice at the head; a test that passes once is flaky, not introduced
                again = [await runner.run(ws_h, suspects, LayerId.UNIT) for _ in range(2)]
                still = set(suspects)
                for r in again:
                    still &= {f.node_id for f in r.failing_tests}
                confirmed = sorted(still)
            graph = RepoGraph(ws_h.root)
            sel = select_tests(ws_h.root, diff, tests, graph=graph)
            sel_f = select_tests(ws_h.root, diff, tests, graph=graph, level="file")
            sel_d = select_tests(ws_h.root, diff, tests, graph=graph, coverage=cmap) if cmap else sel
            keep, keep_f, keep_d = set(sel.selected), set(sel_f.selected), set(sel_d.selected)
            dur = h["durations"]
        finally:
            await ws_h.aclose()
        if confirmed:  # and it must pass at the base when run again, or it was already broken there
            _git(tmp, "checkout", "-q", "--force", base)
            ws_b2 = await executor.prepare(WorkspaceSpec(source="local", local_path=str(tmp), extra_pip=extra_pip))
            try:
                base_ids = {t.node_id for t in await runner.discover(ws_b2)}
                rerun = [x for x in confirmed if x in base_ids]
                if rerun:
                    r = await runner.run(ws_b2, rerun, LayerId.UNIT)
                    confirmed = [x for x in confirmed if x not in {f.node_id for f in r.failing_tests}]
            finally:
                await ws_b2.aclose()
        flagged = bool(sel.uncovered)
        out.update(
            status="ran", n_tests=h["n"], base_failing=len(b["failing"]), head_failing=len(h["failing"]),
            introduced=confirmed, flaky=sorted(set(suspects) - set(confirmed)) if suspects else [],
            mode=sel.mode, reason=sel.reason[:80], changed=sel.changed[:50], n_changed=len(sel.changed),
            selected=len(keep), file_selected=len(keep_f), uncovered=sel.uncovered[:20],
            dyn_available=cmap is not None, dyn_selected=len(keep_d), dyn_uncovered=sel_d.uncovered[:20],
            dyn_mode=sel_d.mode, introduced_selected_dyn=[x for x in confirmed if x in keep_d],
            seconds_dyn=round(sum(dur.get(x, 0.0) for x in keep_d), 2),
            out_of_scope=sel.out_of_scope[:20], inert=sel.inert[:20],
            introduced_selected=[x for x in confirmed if x in keep],
            gate_verdict=("needs_human_review" if (any(x in keep for x in h["failing"]) or flagged)
                          else "auto_approve"),
            ci_verdict="failure" if h["failing"] else "success",
            seconds_full=round(sum(dur.values()), 2), seconds_selected=round(sum(dur.get(x, 0.0) for x in keep), 2),
            wall_seconds=round(time.monotonic() - t_start, 1))
        return out
    except subprocess.TimeoutExpired:
        out["status"] = "git_timeout"
        return out
    except Exception as exc:  # noqa: BLE001 - every attempt is reported
        out["detail"] = f"{type(exc).__name__}: {exc}"[:400]
        return out
    finally:
        import shutil

        shutil.rmtree(tmp, ignore_errors=True)


def summarize(rows: list[dict]) -> dict:
    def rate(k: int, n: int) -> dict:
        lo, hi = wilson(k, n)
        return {"k": k, "n": n, "rate": (k / n) if n else None, "ci95": [round(lo, 3), round(hi, 3)]}

    ran = [r for r in rows if r["status"] == "ran"]
    regress = [r for r in ran if r["introduced"]]
    missed = [r for r in regress if not r["introduced_selected"] and not r["uncovered"] and r["mode"] != "full"]
    clean = [r for r in ran if not r["introduced"] and r["head_failing"] == r["base_failing"] == 0]
    review = [r for r in ran if r["uncovered"] and r["mode"] != "full"]
    shares = [r["selected"] / r["n_tests"] for r in ran if r["n_tests"]]
    tot_full = sum(r["seconds_full"] for r in ran)
    tot_sel = sum(r["seconds_selected"] for r in ran)
    dyn = [r for r in ran if r.get("dyn_available")]
    dyn_reg = [r for r in dyn if r["introduced"]]
    dyn_missed = [r for r in dyn_reg if not r["introduced_selected_dyn"] and not r["dyn_uncovered"] and r["dyn_mode"] != "full"]
    dyn_block = {
        "prs": len(dyn), "regressions": len(dyn_reg), "missed": rate(len(dyn_missed), len(dyn_reg)),
        "missed_ids": [f"{r['repo']}#{r['number']}" for r in dyn_missed],
        "tests_saved_share": round(1 - sum(r["dyn_selected"] for r in dyn) / max(1, sum(r["n_tests"] for r in dyn)), 4),
        "mean_selected_share": round(st.mean(r["dyn_selected"] / r["n_tests"] for r in dyn if r["n_tests"]), 4) if dyn else None,
        "time_saved_share": round(1 - sum(r["seconds_dyn"] for r in dyn) / max(1e-9, sum(r["seconds_full"] for r in dyn)), 4) if dyn else None,
        "review_share": rate(sum(1 for r in dyn if r["dyn_uncovered"] and r["dyn_mode"] != "full"), len(dyn)),
        "static_tests_saved_share_same_prs": round(1 - sum(r["selected"] for r in dyn) / max(1, sum(r["n_tests"] for r in dyn)), 4),
    }
    return {
        "seconds_full_total": round(sum(r["seconds_full"] for r in ran), 1),
        "coverage_seconds_total": round(sum(r.get("coverage_seconds") or 0 for r in ran), 1),
        "coverage_runs": sum(1 for r in ran if r.get("coverage_seconds")),
        "dynamic": dyn_block,
        "attempted": len(rows), "ran": len(ran), "projects_ran": len({r["repo"] for r in ran}),
        "status": {s: sum(1 for r in rows if r["status"] == s) for s in sorted({r["status"] for r in rows})},
        "prs_introducing_failures": len(regress), "introduced_tests": sum(len(r["introduced"]) for r in regress),
        "missed_prs": rate(len(missed), len(regress)), "missed_ids": [f"{r['repo']}#{r['number']}" for r in missed],
        "caught_by_selected_test": sum(1 for r in regress if r["introduced_selected"]),
        "caught_by_review": sum(1 for r in regress if not r["introduced_selected"] and r["uncovered"]),
        "caught_by_full_run": sum(1 for r in regress if not r["introduced_selected"] and r["mode"] == "full"),
        "flaky_tests_filtered": sum(len(r["flaky"]) for r in ran),
        "tests_saved_share": round(1 - sum(r["selected"] for r in ran) / max(1, sum(r["n_tests"] for r in ran)), 4),
        "file_level_tests_saved_share": round(1 - sum(r["file_selected"] for r in ran) / max(1, sum(r["n_tests"] for r in ran)), 4),
        "mean_selected_share": round(st.mean(shares), 4) if shares else None,
        "median_selected_share": round(st.median(shares), 4) if shares else None,
        "time_saved_share": round(1 - tot_sel / tot_full, 4) if tot_full else None,
        "full_run_share": rate(sum(1 for r in ran if r["mode"] == "full"), len(ran)),
        "no_test_needed": sum(1 for r in ran if r["mode"] == "change" and r["selected"] == 0 and not r["uncovered"]),
        "review_share": rate(len(review), len(ran)),
        "clean_prs_sent_to_review": rate(sum(1 for r in clean if r["uncovered"] and r["mode"] != "full"), len(clean)),
        "per_project": {repo: {"ran": sum(1 for r in ran if r["repo"] == repo),
                               "regressions": sum(1 for r in regress if r["repo"] == repo),
                               "missed": sum(1 for r in missed if r["repo"] == repo),
                               "mean_share": round(st.mean([r["selected"] / r["n_tests"] for r in ran
                                                            if r["repo"] == repo and r["n_tests"]]), 3)
                               if any(r["repo"] == repo and r["n_tests"] for r in ran) else None}
                        for repo in sorted({r["repo"] for r in rows})},
    }


def derived(fresh: list[dict], stale: list[dict], weakened: list[dict] | None = None) -> dict:
    """What the headline counts hide: which regressions tested the selector, the total load on people, the same
    pull requests under every core, and what the final selector changes against the one that ran the fresh replay."""
    key = lambda r: (r["repo"], r["number"])  # noqa: E731
    ran = [r for r in fresh if r["status"] == "ran"]
    reg = [r for r in ran if r["introduced"]]

    def kind(r: dict) -> str:
        if r["mode"] == "full":
            return "full_run"
        files = {t.split("::")[0] for t in r["introduced"]}
        if files <= set(r["changed"]):
            return "failing_test_changed_by_pr"
        if r["selected"] >= r["n_tests"]:
            return "all_tests_selected"
        return "tested_the_selector"

    kinds: dict[str, int] = {}
    for r in reg:
        kinds[kind(r)] = kinds.get(kind(r), 0) + 1
    hard = [r for r in reg if kind(r) == "tested_the_selector"]
    core_missed = [f"{r['repo']}#{r['number']}" for r in reg
                   if r["mode"] != "full" and not set(r["introduced"]) & set(r["introduced_selected"])]
    distinct = len({(r["repo"], frozenset(r["introduced"])) for r in reg})
    w = {key(x) for x in (weakened or []) if x.get("set") == "shadow" and x.get("weakened")}
    u = {key(r) for r in ran if r["uncovered"] and r["mode"] != "full"}
    failing = {key(r) for r in reg}
    escalated = u | w | failing
    by_key = {key(r): r for r in ran}
    st_ran = [r for r in stale if r["status"] == "ran"]
    both = [r for r in st_ran if r.get("dyn_available") and key(r) in by_key and by_key[key(r)].get("dyn_available")]
    same_fresh = [by_key[key(r)] for r in both]

    def saved(rows: list[dict], field: str) -> float:
        return round(1 - sum(r[field] for r in rows) / max(1, sum(r["n_tests"] for r in rows)), 4)

    pairs = [(by_key[key(r)], r) for r in st_ran if key(r) in by_key]

    def secs(rows: list[dict], field: str) -> float:
        return round(1 - sum(r[field] for r in rows) / max(1e-9, sum(r["seconds_full"] for r in rows)), 4)

    def missed(rows: list[dict], sel: str, unr: str, mode: str) -> int:
        return sum(1 for r in rows if r["introduced"] and r[mode] != "full"
                   and not set(r["introduced"]) & set(r[sel]) and not r[unr])

    no_test = [r for r in ran if r["selected"] == 0 and not r["uncovered"] and r["mode"] != "full"]
    return {
        "base_failing_prs": sum(1 for r in ran if r["base_failing"]),
        # against what a full CI run already shows: red when any test fails at the head, introduced or not
        "ci_red": sum(1 for r in ran if r["ci_verdict"] == "failure"),
        "ci_green": sum(1 for r in ran if r["ci_verdict"] != "failure"),
        "gate_review_ci_red": sum(1 for r in ran if r["ci_verdict"] == "failure"
                                  and (r["gate_verdict"] == "needs_human_review" or key(r) in w)),
        "gate_review_ci_green": sum(1 for r in ran if r["ci_verdict"] != "failure"
                                    and (r["gate_verdict"] == "needs_human_review" or key(r) in w)),
        "gate_review_any": sum(1 for r in ran if r["gate_verdict"] == "needs_human_review" or key(r) in w),
        "no_test_with_out_of_scope": sum(1 for r in no_test if r.get("out_of_scope")),
        "same_prs_detail": {"regressions": sum(1 for r in both if r["introduced"]),
                            "missed_static": missed(both, "introduced_selected", "uncovered", "mode"),
                            "missed_stored": missed(both, "introduced_selected_dyn", "dyn_uncovered", "dyn_mode"),
                            "missed_fresh": missed(same_fresh, "introduced_selected_dyn", "dyn_uncovered", "dyn_mode"),
                            "time_static": secs(both, "seconds_selected"), "time_stored": secs(both, "seconds_dyn"),
                            "time_fresh": secs(same_fresh, "seconds_dyn"),
                            "static_review": round(sum(1 for r in both if r["uncovered"] and r["mode"] != "full") / max(1, len(both)), 4)},
        "regressions": len(reg), "regression_kinds": kinds, "distinct_failure_sets": distinct,
        "tested_the_selector": [f"{r['repo']}#{r['number']}" for r in hard], "core_missed": core_missed,
        "unreached_or_weakened": {"k": len(u | w), "n": len(ran)},
        "escalated_any_reason": {"k": len(escalated), "n": len(ran)},
        "escalated_without_failure": {"k": len(escalated - failing), "n": len(ran)},
        "same_prs": {"n": len(both), "static": saved([by_key[key(r)] for r in both], "selected"),
                     "static_final": saved(both, "selected"), "stored": saved(both, "dyn_selected"),
                     "fresh": saved(same_fresh, "dyn_selected"),
                     "stored_review": round(sum(1 for r in both if r["dyn_uncovered"] and r["dyn_mode"] != "full") / max(1, len(both)), 4),
                     "fresh_review": round(sum(1 for r in same_fresh if r["dyn_uncovered"] and r["dyn_mode"] != "full") / max(1, len(both)), 4)},
        "final_selector": {"prs": len(pairs), "changed_selection": sum(1 for a, b in pairs if a["selected"] != b["selected"]),
                           "fewer_selected": sum(1 for a, b in pairs if b["selected"] < a["selected"]),
                           "saved_replay": saved([a for a, _ in pairs], "selected"), "saved_final": saved([b for _, b in pairs], "selected"),
                           "no_test_replay": sum(1 for a, _ in pairs if a["selected"] == 0 and not a["uncovered"] and a["mode"] != "full"),
                           "no_test_final": sum(1 for _, b in pairs if b["selected"] == 0 and not b["uncovered"] and b["mode"] != "full")},
    }


def extend_manifest(path: Path, per_project: int = PER_PROJECT) -> dict:
    """Add batch 2: for each project, the next `per_project` pull requests below the lowest number in batch 1."""
    data = json.loads(path.read_text())
    first = [a for a in data["attempts"] if a.get("batch", 1) == 1]
    for a in first:
        a["batch"] = 1
    lowest: dict[str, int] = {}
    for a in first:
        lowest[a["project"]] = min(lowest.get(a["project"], 10**9), a["number"])
    seen = {(a["project"], a["number"]) for a in data["attempts"]}
    for name in sorted(lowest):
        repo = next(a["repo"] for a in first if a["project"] == name)
        branch = next(a["branch"] for a in first if a["project"] == name)
        _, nums = recent_pulls(repo, 400)
        older = [n for n in nums if n < lowest[name] and (name, n) not in seen][:per_project]
        for n in older:
            data["attempts"].append({"order": len(data["attempts"]), "project": name, "repo": repo, "branch": branch,
                                     "number": n, "batch": 2})
    data["rule_batch2"] = (f"the next {per_project} pull requests of each project below the lowest number of batch 1, "
                           "read with git ls-remote; fixed before batch 2 was run")
    path.write_text(json.dumps(data, indent=1))
    return data


def write_manifest(projects: dict[str, str], out: Path, per_project: int = PER_PROJECT) -> dict:
    """projects: name -> owner/repo. Fixes the pull requests before any of them is run."""
    attempts = []
    for name, repo in sorted(projects.items()):
        branch, nums = recent_pulls(repo, per_project)
        for n in nums:
            attempts.append({"order": len(attempts), "project": name, "repo": repo, "branch": branch, "number": n})
    data = {"rule": f"the {per_project} highest-numbered pull requests of each project, read with git ls-remote",
            "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "attempts": attempts}
    out.write_text(json.dumps(data, indent=1))
    return data


# --------------------------------------------------------------------------- #
# Stale recordings: one coverage map per project, reused for all its pull requests
# --------------------------------------------------------------------------- #
def map_to_json(cmap) -> dict:
    tests = sorted({t for v in cmap.by_body.values() for t in v})
    idx = {t: i for i, t in enumerate(tests)}
    return {"tests": tests, "files": sorted(cmap.files),
            "bodies": [[f, o, n, sorted(idx[t] for t in v)] for (f, o, n), v in cmap.by_body.items()],
            "outside": [list(k) for k in sorted(cmap.outside, key=str)]}


def map_from_json(data: dict):
    from ..selection import CoverageMap

    return CoverageMap.from_json(data)


async def record_project(repo: str, branch: str, pr: int, extra_pip: list[str]) -> dict:
    """INSIDE A CONTAINER ONLY. Record coverage once, at the merge base of the given (oldest) pull request."""
    from ..execution.base import WorkspaceSpec
    from ..execution.subprocess_venv import SubprocessVenvExecutor
    from ..selection import CoverageMap, RepoGraph

    out: dict = {"repo": repo, "status": "error"}
    tmp = Path(tempfile.mkdtemp(prefix="record-"))
    try:
        _git(tmp, "init", "-q")
        _git(tmp, "remote", "add", "origin", f"https://github.com/{repo}.git")
        _git(tmp, "fetch", "-q", "--filter=blob:none", "origin", f"+refs/heads/{branch}:refs/remotes/origin/{branch}",
             f"+refs/pull/{pr}/head:refs/pr")
        base = _git(tmp, "merge-base", f"origin/{branch}", "refs/pr").stdout.strip()
        _git(tmp, "checkout", "-q", "--force", base)
        ws = await SubprocessVenvExecutor().prepare(WorkspaceSpec(source="local", local_path=str(tmp), extra_pip=extra_pip))
        try:
            await ws.exec(_pip_install(ws.python, "pytest-cov"), timeout=600, cwd=ws.root)
            rc, db = ws.root / ".agenticqa-coveragerc", ws.root / ".agenticqa-coverage"
            rc.write_text(f"[run]\nbranch = False\nsource = .\ndata_file = {db}\n")
            t0 = time.monotonic()
            cr = await ws.exec([str(ws.python), "-m", "pytest", "-q", "-p", "no:cacheprovider", "--cov=.",
                                f"--cov-config={rc}", "--cov-context=test", "--cov-report=",
                                "-W", "ignore::coverage.exceptions.CoverageWarning"], timeout=1800, cwd=ws.root,
                               env=dict(getattr(ws, "_env", {}), COVERAGE_CORE="ctrace"))
            secs = round(time.monotonic() - t0, 1)
            if not (db.exists() and cr.exit_code in (0, 1)):
                out.update(status="record_failed", detail=(cr.stderr_tail or cr.stdout_tail)[-300:])
                return out
            cmap = CoverageMap.from_sqlite(db, RepoGraph(ws.root), ws.root)
        finally:
            await ws.aclose()
        out.update(status="ok", base=base, recorded_from_pr=pr, seconds=secs, map=map_to_json(cmap))
        return out
    except Exception as exc:  # noqa: BLE001
        out["detail"] = f"{type(exc).__name__}: {exc}"[:300]
        return out
    finally:
        import shutil

        shutil.rmtree(tmp, ignore_errors=True)
