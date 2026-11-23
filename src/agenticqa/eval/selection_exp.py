"""Selection experiments: what would a plain CI run, and what does GATE run, for the same change?

Three studies, none of which looks at a model's opinion of safety:

1. **Mutants on real projects** (`mutants_on_project`). A mutant is a change whose harm is known: the
   full suite either fails on it or it does not. For each mutant we run the full suite (what a plain CI
   check does) and the selected tests (what GATE runs) and compare. The question is whether selection
   loses any failure the full suite would have shown ("missed"), and what it saves.
2. **Real agent pull requests, statically** (`field_static`). For every pull request of the field study
   we fetch the diff and the tree, compute what GATE would select, and compare it with the size of the
   suite. No test runs, so no environment can fail: this shows how much of a real suite a real change
   reaches, how often the answer is "run all" and how often "nothing reaches this change".
3. **Changed files of AIDev at scale** (`aidev_file_kinds`). The kinds of files changed by tens of
   thousands of agent pull requests: how many touch only documentation, how many touch build
   configuration (which forces a full run), and how many change Python code.

Sampling is fixed by seeds written next to the code, and every attempt is reported, including projects
that could not run.
"""

from __future__ import annotations

import asyncio
import difflib
import json
import random
import re
import statistics as st
import subprocess
import tempfile
import time
from collections import Counter, defaultdict
from pathlib import Path

from ..models.enums import LayerId
from ..selection import RepoGraph, kind_of, select_tests, static_tests, with_advice
from .mutation import apply_site, enumerate_sites, is_valid_python, wilson

SEED = 20261023
# Test-only packages that a project's pyproject does not list under a test extra or group (found by reading the
# collection errors of the first attempt; recorded here so the choice is visible).
EXTRA_PIP = {"click": ["<project>"], "blinker": ["pytest-asyncio"], "idna": ["hypothesis"], "w3lib": ["hypothesis"],
             "slugify": ["text-unidecode", "unidecode"], "transitions": ["pycodestyle", "six"], "furl": ["orderedmultidict", "six"], "pyparsing": ["railroad-diagrams", "jinja2"], "packaging": ["pretend"], "itsdangerous": ["freezegun"], "jinja": ["trio"], "attrs": ["hypothesis", "pytest-xdist"]}
SKIP_PARTS = ("test", "tests", "docs", "doc", "examples", "example", "benchmarks", "scripts", "tools",
              "build", "setup.py", "conftest.py", "__init__.py", "_vendor", "vendor", "typing_tests")


def _pip_install(python, *pkgs: str) -> list[str]:
    """Install into an environment: through uv when present (its environments carry no pip), else pip."""
    import shutil as _sh

    uv = _sh.which("uv")
    return [uv, "pip", "install", "-q", "--python", str(python), *pkgs] if uv else [str(python), "-m", "pip", "install", "-q", *pkgs]


def _is_source(path: str) -> bool:
    parts = path.split("/")
    name = parts[-1]
    if not name.endswith(".py") or name in ("setup.py", "conftest.py", "noxfile.py"):
        return False
    if name.startswith(("test_", "bench")) or name.endswith("_test.py"):
        return False
    return not any(p in SKIP_PARTS for p in parts[:-1])


def unified(path: str, old: str, new: str) -> str:
    lines = difflib.unified_diff(old.splitlines(True), new.splitlines(True), f"a/{path}", f"b/{path}")
    return f"diff --git a/{path} b/{path}\n" + "".join(lines)


def _git(cwd: Path, *args: str, timeout: int = 600) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=timeout)


def fetch_commit(clone_url: str, sha: str, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    _git(dest, "init", "-q")
    _git(dest, "remote", "add", "origin", clone_url)
    r = _git(dest, "fetch", "-q", "--depth=1", "origin", sha)
    if r.returncode:
        raise RuntimeError(f"fetch failed: {r.stderr[-300:]}")
    _git(dest, "checkout", "-q", "--force", "FETCH_HEAD")


def sample_mutants(root: Path, n: int, seed: int) -> list[tuple[str, object]]:
    """n mutation sites, round-robin over source files (so one big file cannot dominate) with a fixed seed."""
    rng = random.Random(seed)
    per_file: dict[str, list] = {}
    for p in sorted(root.rglob("*.py")):
        rel = p.relative_to(root).as_posix()
        if not _is_source(rel) or any(x in rel.split("/") for x in (".venv", "venv", "node_modules")):
            continue
        try:
            sites = enumerate_sites(p.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError, ValueError):
            continue
        if sites:
            rng.shuffle(sites)
            per_file[rel] = sites
    out: list[tuple[str, object]] = []
    files = sorted(per_file)
    rng.shuffle(files)
    while len(out) < n and any(per_file.values()):
        for f in files:
            if per_file[f] and len(out) < n:
                out.append((f, per_file[f].pop()))
    return out


async def mutants_on_project(name: str, clone_url: str, sha: str, *, n: int, seed: int = SEED,
                             progress=print) -> dict:
    """Run n mutants of a real project twice each: the whole suite, and only the tests GATE selects."""
    from ..execution.base import WorkspaceSpec
    from ..execution.subprocess_venv import SubprocessVenvExecutor
    from ..runners.pytest_runner import PytestRunner

    tmp = Path(tempfile.mkdtemp(prefix=f"sel-{name}-"))
    result: dict = {"project": name, "clone_url": clone_url, "sha": sha, "seed": seed, "status": "error",
                    "mutants": []}
    ws = None
    try:
        fetch_commit(clone_url, sha, tmp / "src")
        ws = await SubprocessVenvExecutor().prepare(WorkspaceSpec(
            source="local", local_path=str(tmp / "src"), extra_pip=EXTRA_PIP.get(name, [])))
        runner = PytestRunner()
        tests = await runner.discover(ws)
        if not tests:
            result["status"] = "no_tests"
            return result
        ids = [t.node_id for t in tests]
        t0 = time.monotonic()
        base = await runner.run(ws, ids, LayerId.UNIT)
        base_seconds = time.monotonic() - t0
        if base.error:
            result.update(status="baseline_error", detail=base.error[:300])
            return result
        base_fail = {f.node_id for f in base.failing_tests}
        graph = RepoGraph(ws.root)
        result.update(status="ran", n_tests=len(tests), baseline_failing=len(base_fail),
                      baseline_seconds=round(base_seconds, 2), python_files=len(graph.files))
        chosen = sample_mutants(ws.root, n, seed)
        for i, (rel, site) in enumerate(chosen):
            path = ws.root / rel
            original = path.read_text(encoding="utf-8")
            mutated = apply_site(original, site)
            m = {"id": f"{name}-{i:03d}", "file": rel, "operator": site.operator, "line": site.line}
            if not is_valid_python(mutated):
                m["status"] = "invalid"
                result["mutants"].append(m)
                continue
            path.write_text(mutated, encoding="utf-8")
            try:
                diff = unified(rel, original, mutated)
                sel = select_tests(ws.root, diff, tests, graph=graph, level="name")
                sel_file = select_tests(ws.root, diff, tests, graph=graph, level="file")
                sel_namefile = select_tests(ws.root, diff, tests, graph=graph, level="name", granularity="file")
                sel_nodyn = select_tests(ws.root, diff, tests, graph=graph, level="name", taint_dynamic=False)
                from types import SimpleNamespace

                from ..orchestrator.stubs import HeuristicAdvisor
                advised = await HeuristicAdvisor().advise(
                    SimpleNamespace(selection=SimpleNamespace(changed=sel.changed)), tests, sel.core)
                t1 = time.monotonic()
                full = await runner.run(ws, ids, LayerId.UNIT)
                t_full = time.monotonic() - t1
                if full.error:  # the mutant broke collection or timed out: invalid, as in the layer study
                    m.update(status="invalid", detail=full.error[:120])
                    result["mutants"].append(m)
                    continue
                new_fail = sorted({f.node_id for f in full.failing_tests} - base_fail)
                keep = set(sel.selected)
                file_keep = set(sel_file.selected)
                nodyn_keep = set(sel_nodyn.selected)
                namefile_keep = set(sel_namefile.selected)
                adv_keep = keep | (set(advised) & {t.node_id for t in tests}) if sel.mode == "change" else keep
                chosen_ids = [t.node_id for t in tests if t.node_id in keep]
                t2 = time.monotonic()
                if chosen_ids:
                    part = await runner.run(ws, chosen_ids, LayerId.UNIT)
                    sel_fail = sorted({f.node_id for f in part.failing_tests} - base_fail)
                    part_error = bool(part.error)
                else:
                    sel_fail, part_error = [], False
                t_sel = time.monotonic() - t2
                m.update(status="valid", mode=sel.mode, reason=sel.reason[:100], total=len(tests),
                         selected=len(chosen_ids), uncovered=sel.uncovered, full_failing=new_fail[:50],
                         n_full_failing=len(new_fail), selected_failing=sel_fail[:50],
                         n_selected_failing=len(sel_fail), selected_run_error=part_error,
                         seconds_full=round(t_full, 2), seconds_selected=round(t_sel, 2) if chosen_ids else 0.0,
                         file_selected=len(file_keep), file_uncovered=sel_file.uncovered, file_mode=sel_file.mode,
                         n_file_failing=sum(1 for x in new_fail if x in file_keep),
                         n_name_failing_derived=sum(1 for x in new_fail if x in keep),
                         advice_selected=len(adv_keep), n_advice_failing=sum(1 for x in new_fail if x in adv_keep),
                         namefile_selected=len(namefile_keep), namefile_uncovered=sel_namefile.uncovered,
                         n_namefile_failing=sum(1 for x in new_fail if x in namefile_keep),
                         nodyn_selected=len(nodyn_keep), nodyn_uncovered=sel_nodyn.uncovered,
                         n_nodyn_failing=sum(1 for x in new_fail if x in nodyn_keep))
            finally:
                path.write_text(original, encoding="utf-8")
            result["mutants"].append(m)
            progress(f"[{name} {i + 1}/{len(chosen)}] {m['status']:7s} selected {m.get('selected')}/{len(tests)}"
                     f" full-fail {m.get('n_full_failing')} sel-fail {m.get('n_selected_failing')}")
        return result
    except Exception as exc:  # noqa: BLE001 - a project that cannot run is a finding
        result["detail"] = f"{type(exc).__name__}: {exc}"[:400]
        return result
    finally:
        if ws is not None:
            await ws.aclose()
        import shutil

        shutil.rmtree(tmp, ignore_errors=True)


def summarize_mutants(projects: list[dict]) -> dict:
    """Detection and savings, pooled over mutants and per project, for each selection level."""
    def rate(k: int, n: int) -> dict:
        lo, hi = wilson(k, n)
        return {"k": k, "n": n, "rate": (k / n) if n else None, "ci95": [round(lo, 3), round(hi, 3)]}

    rows = []
    for p in projects:
        for m in p.get("mutants", []):
            if m.get("status") == "valid":
                rows.append({**m, "project": p["project"]})
    killed = [r for r in rows if r["n_full_failing"] > 0]
    survivors = [r for r in rows if r["n_full_failing"] == 0]

    # level -> (selected count key, failing-in-selection key, uncovered key)
    levels = {
        "file": ("file_selected", "n_file_failing", "file_uncovered"),
        "name-file": ("namefile_selected", "n_namefile_failing", "namefile_uncovered"),
        "name": ("selected", "n_selected_failing", "uncovered"),
        "name+advice": ("advice_selected", "n_advice_failing", "uncovered"),
        "name-ignoring-dynamic-loading": ("nodyn_selected", "n_nodyn_failing", "nodyn_uncovered"),
    }
    if rows and all("dyn_selected" in r for r in rows):
        levels["dynamic"] = ("dyn_selected", "n_dyn_failing", "dyn_uncovered")
    out_levels = {}
    for lvl, (sel_k, fail_k, unc_k) in levels.items():
        flag = lambda r: r[fail_k] > 0 or bool(r[unc_k])  # noqa: E731 - GATE flags it
        missed = [r for r in killed if not flag(r)]
        by_tests = [r for r in killed if r[fail_k] > 0]
        shares = [r[sel_k] / r["total"] for r in rows if r["total"]]
        out_levels[lvl] = {
            "caught_by_selected_tests": rate(len(by_tests), len(killed)),
            "missed_failures": rate(len(missed), len(killed)),
            "missed_ids": [r["id"] for r in missed],
            "extra_reviews_green_ci": rate(sum(1 for r in survivors if r[unc_k]), len(survivors)),
            "selected_share": {"median": round(st.median(shares), 4) if shares else None,
                               "mean": round(st.mean(shares), 4) if shares else None,
                               "p90": round(sorted(shares)[int(0.9 * (len(shares) - 1))], 4) if shares else None},
            "tests_saved_share": round(1 - sum(r[sel_k] for r in rows) / max(1, sum(r["total"] for r in rows)), 4),
            "all_tests_selected_share": rate(sum(1 for r in rows if r[sel_k] >= r["total"]), len(rows)),
        }
    sec_full = sum(r["seconds_full"] for r in rows)
    sec_sel = sum(r["seconds_selected"] for r in rows)
    per_project = {}
    for p in projects:
        rs = [r for r in rows if r["project"] == p["project"]]
        ks = [r for r in rs if r["n_full_failing"] > 0]
        per_project[p["project"]] = {
            "status": p.get("status"), "n_tests": p.get("n_tests"), "python_files": p.get("python_files"),
            "valid_mutants": len(rs), "killed_by_full_suite": len(ks),
            "missed_file": sum(1 for r in ks if not (r["n_file_failing"] > 0 or r["file_uncovered"])),
            "mean_share_namefile": round(st.mean([r["namefile_selected"] / r["total"] for r in rs]), 4) if rs else None,
            "mean_share_nodyn": round(st.mean([r["nodyn_selected"] / r["total"] for r in rs]), 4) if rs else None,
            "missed_name": sum(1 for r in ks if not (r["n_selected_failing"] > 0 or r["uncovered"])),
            "mean_share_file": round(st.mean([r["file_selected"] / r["total"] for r in rs]), 4) if rs else None,
            "mean_share_name": round(st.mean([r["selected"] / r["total"] for r in rs]), 4) if rs else None,
            "time_ratio": round(sum(r["seconds_selected"] for r in rs) / max(1e-9, sum(r["seconds_full"] for r in rs)), 4) if rs else None,
        }
    return {
        "n_projects": len(projects), "n_projects_ran": sum(1 for p in projects if p.get("status") == "ran"),
        "valid_mutants": len(rows), "killed_by_full_suite": len(killed), "survivors": len(survivors),
        "levels": out_levels,
        "full_run_fallback_share": rate(sum(1 for r in rows if r["mode"] == "full"), len(rows)),
        "seconds_full": round(sec_full, 1), "seconds_selected": round(sec_sel, 1),
        "time_saved_share": round(1 - sec_sel / sec_full, 4) if sec_full else None,
        "selected_run_errors": sum(1 for r in rows if r["selected_run_error"]),
        "disagreements_between_derived_and_actual": sum(
            1 for r in rows if (r["n_name_failing_derived"] > 0) != (r["n_selected_failing"] > 0)),
        "per_project": per_project,
    }


# --------------------------------------------------------------------------- #
# Real agent pull requests, statically
# --------------------------------------------------------------------------- #
def field_static_one(a: dict, workdir: Path) -> dict:
    """Fetch one PR (head and its base), diff them, and compute what GATE would select. Runs no test."""
    repo, number, n_commits = a["repo"], a["number"], a["n_commits"]
    out: dict = {k: a.get(k) for k in ("pr_id", "number", "agent", "repo", "merged", "n_commits")}
    out["status"] = "error"
    d = workdir / f"{repo.replace('/', '__')}-{number}"
    try:
        d.mkdir(parents=True, exist_ok=True)
        _git(d, "init", "-q")
        _git(d, "remote", "add", "origin", f"https://github.com/{repo}.git")
        f = _git(d, "fetch", "-q", f"--depth={n_commits + 1}", "origin", f"pull/{number}/head", timeout=900)
        if f.returncode:
            out["status"] = "fetch_failed"
            return out
        base_p = _git(d, "rev-parse", f"FETCH_HEAD~{n_commits}")
        if base_p.returncode:
            out["status"] = "no_base"
            return out
        base = base_p.stdout.strip()
        _git(d, "checkout", "-q", "--force", "FETCH_HEAD")
        diff = _git(d, "diff", "--no-color", "--no-ext-diff", base, "HEAD").stdout
        graph = RepoGraph(d)
        tests = static_tests(graph)
        sel = select_tests(d, diff, tests, graph=graph, level="name")
        sel_file = select_tests(d, diff, tests, graph=graph, level="file")
        sel_nodyn = select_tests(d, diff, tests, graph=graph, level="name", taint_dynamic=False)
        changed = sel.changed
        kinds = Counter(kind_of(c) for c in changed)
        out.update(status="ok", n_changed=len(changed), kinds=dict(kinds), n_tests=len(tests),
                   n_test_files=len({t.file for t in tests}), mode=sel.mode, reason=sel.reason[:120],
                   selected=len(sel.selected), uncovered=len(sel.uncovered), inert=len(sel.inert),
                   out_of_scope=len(sel.out_of_scope), uncovered_paths=sel.uncovered[:30],
                   out_of_scope_paths=sel.out_of_scope[:30],
                   file_selected=len(sel_file.selected), file_uncovered=len(sel_file.uncovered),
                   nodyn_selected=len(sel_nodyn.selected), nodyn_uncovered=len(sel_nodyn.uncovered),
                   python_files=len(graph.files))
        return out
    except subprocess.TimeoutExpired:
        out["status"] = "timeout"
        return out
    except Exception as exc:  # noqa: BLE001
        out["detail"] = f"{type(exc).__name__}: {exc}"[:200]
        return out
    finally:
        import shutil

        shutil.rmtree(d, ignore_errors=True)


def summarize_field(rows: list[dict]) -> dict:
    """What GATE would do with each real agent PR, from the diff alone."""
    ok = [r for r in rows if r["status"] == "ok"]
    with_tests = [r for r in ok if r["n_tests"] > 0]

    def rate(k: int, n: int) -> dict:
        lo, hi = wilson(k, n)
        return {"k": k, "n": n, "rate": (k / n) if n else None, "ci95": [round(lo, 3), round(hi, 3)]}

    def outcome(r: dict) -> str:
        if r["mode"] == "full":
            return "run all tests (configuration or unreadable change)"
        if r["uncovered"]:
            return "needs review (a changed file is reached by no test)"
        if r["selected"] == 0:
            return "no test needed (documentation or other inert files only)"
        return "run a subset"

    cats = Counter(outcome(r) for r in ok)
    subset = [r for r in with_tests if outcome(r) == "run a subset"]
    return {
        "attempted": len(rows), "analysed": len(ok), "status": dict(Counter(r["status"] for r in rows)),
        "with_tests": len(with_tests), "outcomes": dict(cats),
        "subset_selected_share": {
            "median": round(st.median([r["selected"] / r["n_tests"] for r in subset]), 4) if subset else None,
            "mean": round(st.mean([r["selected"] / r["n_tests"] for r in subset]), 4) if subset else None},
        "tests_total_all": sum(r["n_tests"] for r in with_tests),
        "tests_selected_all": sum(r["selected"] for r in with_tests),
        "tests_selected_file_level": sum(r["file_selected"] for r in with_tests),
        "tests_selected_ignoring_dynamic": sum(r["nodyn_selected"] for r in with_tests),
        "review_breakdown": {
            "no_tests": sum(1 for r in ok if r["mode"] == "change" and r["uncovered"] and r["n_tests"] == 0),
            "non_python_only": sum(1 for r in ok if r["mode"] == "change" and r["uncovered"] and r["n_tests"] > 0
                                   and r["kinds"].get("python", 0) + r["kinds"].get("conftest", 0) == 0),
            "python_unmentioned": sum(1 for r in ok if r["mode"] == "change" and r["uncovered"] and r["n_tests"] > 0
                                      and r["kinds"].get("python", 0) + r["kinds"].get("conftest", 0) > 0)},
        "pull_requests_with_tests": len(with_tests),
        "no_tests_in_repository": len(ok) - len(with_tests),
        "reviewed_for_missing_evidence": rate(sum(1 for r in ok if outcome(r).startswith("needs review")), len(ok)),
        "full_run_share": rate(sum(1 for r in ok if r["mode"] == "full"), len(ok)),
    }


# --------------------------------------------------------------------------- #
# AIDev, at scale: the kinds of files that agent pull requests change
# --------------------------------------------------------------------------- #
def aidev_file_kinds() -> dict:
    """Share of AIDev pull requests (Python repositories) by the kinds of files they change."""
    from .aidev import load_table

    repos = {r["id"]: r for r in load_table("repository", ["id", "full_name", "language"])}
    prs = {p["id"]: p for p in load_table("pull_request", ["id", "agent", "repo_id", "state"])}
    py_prs = {pid for pid, p in prs.items() if repos.get(p["repo_id"], {}).get("language") == "Python"}
    commits: dict[str, int] = {}
    for c in load_table("pr_commits", ["sha", "pr_id"]):
        if c["pr_id"] in py_prs:
            commits[c["sha"]] = c["pr_id"]
    files: dict[int, set[str]] = defaultdict(set)
    for d in load_table("pr_commit_details", ["sha", "filename"]):
        pid = commits.get(d["sha"])
        if pid is not None and d["filename"]:
            files[pid].add(d["filename"])
    per_pr = []
    for pid, fs in files.items():
        kinds = {kind_of(f) for f in fs}
        per_pr.append((pid, prs[pid]["agent"], kinds, len(fs)))
    n = len(per_pr)

    def share(pred) -> dict:
        k = sum(1 for _, _, ks, _ in per_pr if pred(ks))
        lo, hi = wilson(k, n)
        return {"k": k, "n": n, "rate": k / n if n else None, "ci95": [round(lo, 3), round(hi, 3)]}

    by_agent = {}
    for agent in sorted({a for _, a, _, _ in per_pr if a}):
        rs = [ks for _, a, ks, _ in per_pr if a == agent]
        by_agent[agent] = {
            "n": len(rs),
            "only_inert": round(sum(1 for ks in rs if ks <= {"inert"}) / len(rs), 3),
            "touches_config": round(sum(1 for ks in rs if "config" in ks) / len(rs), 3),
            "touches_python": round(sum(1 for ks in rs if ks & {"python", "conftest"}) / len(rs), 3),
        }
    return {
        "python_prs_with_file_lists": n, "python_prs_total": len(py_prs),
        "only_inert_files": share(lambda ks: ks <= {"inert"}),
        "touches_build_or_test_config": share(lambda ks: "config" in ks),
        "touches_python_source": share(lambda ks: bool(ks & {"python", "conftest"})),
        "touches_other_files": share(lambda ks: "other" in ks),
        "median_files_changed": st.median([c for *_, c in per_pr]) if per_pr else None,
        "by_agent": by_agent,
    }


def demo_services_static(repo_root: Path) -> dict:
    """What selection does on the two demo services, for every mutation site (no test is run).

    The demo services are single-module applications whose tests all import that module, so the
    expected answer is 'almost everything': a limit worth reporting."""
    from .mutation import SYSTEMS

    out = {}
    for name, system in SYSTEMS.items():
        demo = repo_root / system.demo_dir
        graph = RepoGraph(demo)
        tests = static_tests(graph)
        original = (demo / system.source_file).read_text()
        rows = []
        for site in enumerate_sites(original):
            mutated = apply_site(original, site)
            if not is_valid_python(mutated):
                continue
            for level in ("file", "name"):
                sel = select_tests(demo, unified(system.source_file, original, mutated), tests, graph=graph, level=level)
                rows.append({"level": level, "selected": len(sel.selected), "uncovered": len(sel.uncovered)})
        res = {"n_tests": len(tests), "mutants": sum(1 for r in rows if r["level"] == "name")}
        for level in ("file", "name"):
            rs = [r for r in rows if r["level"] == level]
            res[level] = {"mean_selected_share": round(st.mean(r["selected"] / len(tests) for r in rs), 4) if rs and tests else None,
                          "uncovered_changes": sum(1 for r in rs if r["uncovered"])}
        out[name] = res
    return out


# --------------------------------------------------------------------------- #
# Attack on the test advisor
# --------------------------------------------------------------------------- #
class _Advisor:
    """A test advisor with a fixed (possibly hostile) behaviour. It only ever sees what the interface gives it."""

    def __init__(self, kind: str) -> None:
        self.kind = kind

    async def advise(self, run, tests, core):
        ids = [t.node_id for t in tests]
        if self.kind == "empty":
            return []
        if self.kind == "garbage":
            return [f"nonexistent/test_{i}.py::test_{i}" for i in range(50)]
        if self.kind == "everything":
            return ids
        if self.kind == "non-core-only":
            return [i for i in ids if i not in set(core)]
        if self.kind == "duplicates":
            return (core + ids[:3]) * 5
        if self.kind == "huge":
            return [f"x{i}" for i in range(100_000)] + ids[:1]
        if self.kind == "raises":
            raise RuntimeError("model unavailable")
        if self.kind == "reversed-core":
            return [i for i in ids if i not in set(core)][::-1]
        return []


ADVISOR_KINDS = ["empty", "garbage", "everything", "non-core-only", "duplicates", "huge", "raises", "reversed-core"]


async def advisor_attack(name: str, clone_url: str, sha: str, *, n: int, seed: int = SEED, progress=print,
                         only: int | None = None, kinds: list[str] | None = None) -> dict:
    """Run the pipeline on mutants of a real project with each hostile advisor, and check Proposition 1.

    For every mutant the honest run (no advisor) fixes the core set E0 and the verdict V0. A hostile run
    breaks M1 if some core test did not run, and breaks M2 if V0 flags the change and V does not."""
    import shutil
    from pathlib import Path as _P

    from ..orchestrator.stubs import HeuristicClassifier, HeuristicRouter, HeuristicTriage
    from .corpus import Scenario
    from .harness import run_scenario

    tmp = _P(tempfile.mkdtemp(prefix=f"atk-{name}-"))
    out: dict = {"project": name, "sha": sha, "seed": seed, "cases": [], "status": "error"}
    try:
        fetch_commit(clone_url, sha, tmp / "base")
        chosen = sample_mutants(tmp / "base", n, seed)
        for i, (rel, site) in enumerate(chosen):
            if only is not None and i != only:
                continue
            original = (tmp / "base" / rel).read_text(encoding="utf-8")
            mutated = apply_site(original, site)
            if not is_valid_python(mutated):
                continue
            work = tmp / f"m{i}"
            shutil.copytree(tmp / "base", work, ignore=shutil.ignore_patterns(".git", "__pycache__"))
            (work / rel).write_text(mutated, encoding="utf-8")
            diff = unified(rel, original, mutated)
            runs = {}
            for kind in [None, *(kinds or ADVISOR_KINDS)]:
                scn = Scenario(name=f"atk-{name}-{i}-{kind}", source="local", repo_path=str(work), diff=diff,
                               extra_env={"PYTEST_ADDOPTS": "-p no:randomly"})
                r = await run_scenario(scn, router=HeuristicRouter(), classifier=HeuristicClassifier(),
                                       triage=HeuristicTriage(), repo_root=_P("/"), selection_mode="change",
                                       advisor=None if kind is None else _Advisor(kind))
                ran = {t.node_id for lr in r.run.layer_runs.values() for t in lr.results}
                timed_out = any("timed out" in (lr.error or "") for lr in r.run.layer_runs.values())
                runs[kind] = {"ran": ran, "timed_out": timed_out, "errors": [(l.value, (lr.error or "")[:160], lr.state.value, lr.planned_count, len(lr.results)) for l, lr in r.run.layer_runs.items() if lr.planned_count],
                              "seconds": round(r.wall_seconds, 1), "verdict": r.run.verdict.value if r.run.verdict else None,
                              "state": r.run.state.value, "sel": r.run.selection.model_dump() if r.run.selection else None}
            honest = runs[None]
            if honest["verdict"] is None:
                continue
            for kind in (kinds or ADVISOR_KINDS):
                h = runs[kind]
                invalid = honest["timed_out"] or h["timed_out"]  # a run cut off by the layer time limit says nothing
                m1 = not invalid and not honest["ran"] <= h["ran"]
                m2 = not invalid and honest["verdict"] == "needs_human_review" and h["verdict"] != "needs_human_review"
                out["cases"].append({"mutant": f"{name}-{i}", "advisor": kind, "core_tests": len(honest["ran"]),
                                     "ran": len(h["ran"]), "m1_violation": m1, "m2_violation": m2, "invalid_timeout": invalid,
                                     "honest_verdict": honest["verdict"], "verdict": h["verdict"],
                                     "honest_layers": honest.get("errors"), "layers": h.get("errors"),
                                     "honest_seconds": honest.get("seconds"), "seconds": h.get("seconds")})
            progress(f"[{name} {i + 1}/{len(chosen)}] honest={honest['verdict']} core={len(honest['ran'])}")
            shutil.rmtree(work, ignore_errors=True)
        out["status"] = "ran"
        return out
    except Exception as exc:  # noqa: BLE001
        out["detail"] = f"{type(exc).__name__}: {exc}"[:300]
        return out
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def summarize_attack(projects: list[dict]) -> dict:
    cases = [c for p in projects for c in p.get("cases", [])]
    by_kind = {k: {"cases": sum(1 for c in cases if c["advisor"] == k),
                   "m1": sum(1 for c in cases if c["advisor"] == k and c["m1_violation"]),
                   "m2": sum(1 for c in cases if c["advisor"] == k and c["m2_violation"])} for k in ADVISOR_KINDS}
    flagged = sum(1 for c in cases if c["honest_verdict"] == "needs_human_review")
    return {"n_cases": len(cases), "n_projects": sum(1 for p in projects if p.get("status") == "ran"),
            "n_mutants": len({c["mutant"] for c in cases}), "honest_flagged_cases": flagged,
            "invalid_timeouts": sum(1 for c in cases if c.get("invalid_timeout")),
            "m1_violations": sum(c["m1_violation"] for c in cases), "m2_violations": sum(c["m2_violation"] for c in cases),
            "upper_bound_95": wilson(0, len(cases))[1] if cases else None, "by_advisor": by_kind}


# --------------------------------------------------------------------------- #
# What a model adds as test advisor
# --------------------------------------------------------------------------- #
async def advice_study(name: str, clone_url: str, sha: str, report: dict, *, provider: str, model: str | None,
                       limit: int, progress=print) -> dict:
    """For recorded mutants, ask a real model which tests to add to the core, and compare with what failed.

    The core is recomputed (the mutants are deterministic: same seed, same sites). A model is useful as an
    advisor only if it names failing tests that the core left out, and it is costly in proportion to the
    tests it adds."""
    import shutil
    from copy import deepcopy
    from types import SimpleNamespace

    from ..config import get_settings
    from ..execution.base import WorkspaceSpec
    from ..execution.subprocess_venv import SubprocessVenvExecutor
    from ..llm.factory import build_client
    from ..models.run import PRRef, QARun, SelectionInfo
    from ..orchestrator.llm_touchpoints import LLMTestAdvisor
    from ..runners.pytest_runner import PytestRunner

    settings = get_settings().model_copy(update={"llm_provider": provider})
    if model:
        settings = settings.model_copy(update={{"openai": "openai_model", "mistral": "mistral_model"}.get(provider, "openai_model"): model})
    client = build_client(settings)
    out: dict = {"project": name, "provider": provider, "model": model, "rows": [], "status": "error"}
    if client is None:
        out["detail"] = "no client"
        return out
    tmp = Path(tempfile.mkdtemp(prefix=f"adv-{name}-"))
    ws = None
    try:
        fetch_commit(clone_url, sha, tmp / "src")
        ws = await SubprocessVenvExecutor().prepare(WorkspaceSpec(
            source="local", local_path=str(tmp / "src"), extra_pip=EXTRA_PIP.get(name, [])))
        runner = PytestRunner()
        tests = await runner.discover(ws)
        ids = {t.node_id for t in tests}
        graph = RepoGraph(ws.root)
        chosen = sample_mutants(ws.root, len(report["mutants"]), report["seed"])
        advisor = LLMTestAdvisor(client)
        used = 0
        for m, (rel, site) in zip(report["mutants"], chosen):
            if m.get("status") != "valid" or m["n_full_failing"] == 0 or used >= limit:
                continue
            original = (ws.root / rel).read_text(encoding="utf-8")
            mutated = apply_site(original, site)
            (ws.root / rel).write_text(mutated, encoding="utf-8")
            try:
                diff = unified(rel, original, mutated)
                sel = select_tests(ws.root, diff, tests, graph=graph)
            finally:
                (ws.root / rel).write_text(original, encoding="utf-8")
            run = QARun(id="adv", diff=diff, pr=PRRef(title=f"mutant {m['id']}"))
            run.selection = SelectionInfo(changed=sel.changed)
            before = dict(getattr(client, "totals", None) or {})
            t0 = time.monotonic()
            advised = await advisor.advise(run, tests, sel.core)
            secs = time.monotonic() - t0
            after = dict(getattr(client, "totals", None) or {})
            added = [a for a in dict.fromkeys(advised) if a in ids and a not in set(sel.core)]
            failing = set(m["full_failing"])
            outside = failing - set(sel.core)
            row = {"mutant": m["id"], "total": len(tests), "core": len(sel.core), "added": len(added),
                   "failing_shown": len(failing), "failing_outside_core": len(outside),
                   "named_failing_outside_core": len(outside & set(added)),
                   "tokens": sum(after.get(k, 0) - before.get(k, 0) for k in after), "seconds": round(secs, 2)}
            out["rows"].append(row)
            used += 1
            progress(f"[{name} advice {used}] core {row['core']} +{row['added']} outside-core failing {row['failing_outside_core']}")
        out["status"] = "ran"
        return out
    except Exception as exc:  # noqa: BLE001
        out["detail"] = f"{type(exc).__name__}: {exc}"[:300]
        return out
    finally:
        if ws is not None:
            await ws.aclose()
        shutil.rmtree(tmp, ignore_errors=True)


def summarize_advice(studies: list[dict]) -> dict:
    rows = [r for s in studies for r in s.get("rows", [])]
    outside = sum(r["failing_outside_core"] for r in rows)
    return {
        "n_mutants": len(rows), "models": sorted({f"{s['provider']}:{s.get('model') or 'default'}" for s in studies}),
        "mean_core_share": round(st.mean(r["core"] / r["total"] for r in rows), 4) if rows else None,
        "mean_added": round(st.mean(r["added"] for r in rows), 2) if rows else None,
        "mutants_with_additions": sum(1 for r in rows if r["added"]),
        "failing_tests_outside_core": outside,
        "named_by_model": sum(r["named_failing_outside_core"] for r in rows),
        "mean_tokens": round(st.mean(r["tokens"] for r in rows), 0) if rows else None,
        "mean_seconds": round(st.mean(r["seconds"] for r in rows), 2) if rows else None,
    }


# --------------------------------------------------------------------------- #
# Replay: the recorded full-suite outcomes of mutants against a (newer) selector
# --------------------------------------------------------------------------- #
async def replay_project(name: str, clone_url: str, sha: str, report: dict, *, progress=print) -> dict:
    """Recompute the selection of every recorded mutant with the current selector and judge it against the
    failures the full suite showed when the mutants were run. No mutant is run again.

    The mutants are deterministic (same seed, same order), so mutant i here is mutant i of the report. The
    time of a selected run is estimated from per-test durations measured on the unmodified project."""
    import shutil
    from types import SimpleNamespace

    from ..execution.base import WorkspaceSpec
    from ..execution.subprocess_venv import SubprocessVenvExecutor
    from ..orchestrator.stubs import HeuristicAdvisor
    from ..runners.pytest_runner import PytestRunner

    tmp = Path(tempfile.mkdtemp(prefix=f"replay-{name}-"))
    out: dict = {"project": name, "clone_url": clone_url, "sha": sha, "seed": report["seed"], "status": "error",
                 "mutants": [], "replay_of": report.get("project")}
    ws = None
    try:
        fetch_commit(clone_url, sha, tmp / "src")
        ws = await SubprocessVenvExecutor().prepare(WorkspaceSpec(
            source="local", local_path=str(tmp / "src"), extra_pip=EXTRA_PIP.get(name, [])))
        runner = PytestRunner()
        tests = await runner.discover(ws)
        ids = [t.node_id for t in tests]
        base = await runner.run(ws, ids, LayerId.UNIT)
        dur = {r.node_id: r.duration or 0.0 for r in base.results}
        graph = RepoGraph(ws.root)
        out.update(status="ran", n_tests=len(tests), python_files=len(graph.files),
                   baseline_failing=len(base.failing_tests), baseline_seconds=round(sum(dur.values()), 2))
        # The dynamic core: one recorded run under coverage with per-test contexts (C tracer: Python 3.14's
        # sys.monitoring tracer cannot record contexts).
        cmap = None
        cov_json = ws.root / ".agenticqa-cov.json"
        t0 = time.monotonic()
        # pytest-cov goes into the same environment the mutants ran in; a new environment could differ from it
        await ws.exec(_pip_install(ws.python, "pytest-cov"), timeout=600, cwd=ws.root)
        rc = ws.root / ".agenticqa-coveragerc"
        db = ws.root / ".agenticqa-coverage"
        # our own settings, not the project's coverage config; no report, the data file is read directly
        rc.write_text(f"[run]\nbranch = False\nsource = .\ndata_file = {db}\n")
        cov_run = await ws.exec([str(ws.python), "-m", "pytest", "-q", "-p", "no:cacheprovider", "--cov=.",
                                 f"--cov-config={rc}", "--cov-context=test", "--cov-report=",
                                 "-W", "ignore::coverage.exceptions.CoverageWarning"],
                                timeout=1800, cwd=ws.root, env=dict(getattr(ws, "_env", {}), COVERAGE_CORE="ctrace"))
        out["coverage_seconds"] = round(time.monotonic() - t0, 1)
        if db.exists() and cov_run.exit_code in (0, 1):
            from ..selection import CoverageMap

            cmap = CoverageMap.from_sqlite(db, graph, ws.root)
            out["coverage_files"] = len(cmap.files)
            out["coverage_bodies"] = len(cmap.by_body)
        else:
            out["coverage_error"] = (cov_run.stdout_tail + cov_run.stderr_tail)[-300:]
        chosen = sample_mutants(ws.root, len(report["mutants"]), report["seed"])
        total_seconds = sum(dur.get(i, 0.0) for i in ids)
        for rec, (rel, site) in zip(report["mutants"], chosen):
            m = {k: rec[k] for k in ("id", "file", "operator", "line") if k in rec}
            if rec.get("status") != "valid" or rec["file"] != rel:
                m["status"] = rec.get("status", "invalid") if rec.get("status") != "valid" else "mismatch"
                out["mutants"].append(m)
                continue
            path = ws.root / rel
            original = path.read_text(encoding="utf-8")
            mutated = apply_site(original, site)
            path.write_text(mutated, encoding="utf-8")
            try:
                diff = unified(rel, original, mutated)
                sel = select_tests(ws.root, diff, tests, graph=graph)
                sel_f = select_tests(ws.root, diff, tests, graph=graph, level="file")
                sel_nf = select_tests(ws.root, diff, tests, graph=graph, level="name", granularity="file")
                sel_nd = select_tests(ws.root, diff, tests, graph=graph, taint_dynamic=False)
                sel_dy = select_tests(ws.root, diff, tests, graph=graph, coverage=cmap) if cmap else sel
                adv = await HeuristicAdvisor().advise(
                    SimpleNamespace(selection=SimpleNamespace(changed=sel.changed)), tests, sel.core)
            finally:
                path.write_text(original, encoding="utf-8")
            failing = rec["full_failing"]
            keep = set(sel.selected)
            adv_keep = keep | ({a for a in adv} & set(ids)) if sel.mode == "change" else keep
            m.update(status="valid", mode=sel.mode, reason=sel.reason[:100], total=len(tests),
                     selected=len(keep), uncovered=sel.uncovered, full_failing=failing,
                     n_full_failing=rec["n_full_failing"], selected_failing=[x for x in failing if x in keep],
                     n_selected_failing=sum(1 for x in failing if x in keep), selected_run_error=False,
                     seconds_full=round(total_seconds, 3),
                     seconds_selected=round(sum(dur.get(i, 0.0) for i in keep), 3),
                     file_selected=len(sel_f.selected), file_uncovered=sel_f.uncovered, file_mode=sel_f.mode,
                     n_file_failing=sum(1 for x in failing if x in set(sel_f.selected)),
                     n_name_failing_derived=sum(1 for x in failing if x in keep),
                     namefile_selected=len(sel_nf.selected), namefile_uncovered=sel_nf.uncovered,
                     n_namefile_failing=sum(1 for x in failing if x in set(sel_nf.selected)),
                     nodyn_selected=len(sel_nd.selected), nodyn_uncovered=sel_nd.uncovered,
                     n_nodyn_failing=sum(1 for x in failing if x in set(sel_nd.selected)),
                     advice_selected=len(adv_keep), n_advice_failing=sum(1 for x in failing if x in adv_keep),
                     dyn_selected=len(sel_dy.selected), dyn_uncovered=sel_dy.uncovered,
                     n_dyn_failing=sum(1 for x in failing if x in set(sel_dy.selected)), dyn_available=cmap is not None,
                     seconds_dyn=round(sum(dur.get(i, 0.0) for i in set(sel_dy.selected)), 3))
            out["mutants"].append(m)
        progress(f"[{name}] replayed {len(out['mutants'])} mutants, {len(tests)} tests")
        return out
    except Exception as exc:  # noqa: BLE001
        out["detail"] = f"{type(exc).__name__}: {exc}"[:300]
        return out
    finally:
        if ws is not None:
            await ws.aclose()
        shutil.rmtree(tmp, ignore_errors=True)


# --------------------------------------------------------------------------- #
# Head to head with pytest-testmon on the same mutants
# --------------------------------------------------------------------------- #
_COUNT = re.compile(r"(\d+) (passed|failed|errors?|skipped|deselected|xfailed|xpassed)")


def parse_pytest_summary(out: str) -> dict:
    """Counts from the last summary line of a pytest run (works with -q)."""
    counts = {"passed": 0, "failed": 0, "error": 0, "skipped": 0, "deselected": 0}
    for line in reversed(out.strip().splitlines()):
        found = _COUNT.findall(line)
        if found and (" in " in line or line.strip().startswith(("=", "FAILED")) or "passed" in line or "failed" in line):
            for n, kind in found:
                counts["error" if kind.startswith("error") else kind if kind in counts else "passed"] += int(n)
            break
    return counts


async def testmon_project(name: str, clone_url: str, sha: str, report: dict, *, progress=print) -> dict:
    """Run pytest-testmon on the recorded mutants of one project and compare it with GATE on the same mutants.

    testmon first runs the whole suite once on the unmodified project to learn which tests depend on which code
    (its baseline), then, for each mutant, runs only the tests it considers affected. A mutant is *caught* by
    testmon if that run has a failure or an error."""
    import shutil

    from ..execution.base import WorkspaceSpec
    from ..execution.subprocess_venv import SubprocessVenvExecutor

    tmp = Path(tempfile.mkdtemp(prefix=f"tmon-{name}-"))
    out: dict = {"project": name, "seed": report["seed"], "status": "error", "rows": []}
    ws = None
    try:
        fetch_commit(clone_url, sha, tmp / "src")
        ws = await SubprocessVenvExecutor().prepare(WorkspaceSpec(
            source="local", local_path=str(tmp / "src"), extra_pip=EXTRA_PIP.get(name, []) + ["pytest-testmon"]))
        argv = [str(ws.python), "-m", "pytest", "--testmon", "-q", "--no-header"]
        env = dict(getattr(ws, "_env", {}), COVERAGE_CORE="ctrace")  # sys.monitoring cannot record per-test contexts
        t0 = time.monotonic()
        base = await ws.exec(argv, timeout=1500, cwd=ws.root, env=env)
        out["baseline_seconds"] = round(time.monotonic() - t0, 1)
        data = {p.name: p.read_bytes() for p in ws.root.glob(".testmondata*") if p.is_file()}
        if not data:
            out.update(status="no_testmon_data", detail=(base.stdout_tail + base.stderr_tail)[-300:])
            return out
        counts0 = parse_pytest_summary(base.stdout_tail)
        out["baseline_counts"] = counts0
        chosen = sample_mutants(ws.root, len(report["mutants"]), report["seed"])
        for rec, (rel, site) in zip(report["mutants"], chosen):
            if rec.get("status") != "valid" or rec["file"] != rel:
                continue
            path = ws.root / rel
            original = path.read_text(encoding="utf-8")
            path.write_text(apply_site(original, site), encoding="utf-8")
            try:
                for p in ws.root.glob(".testmondata*"):
                    p.unlink()
                for fname, blob in data.items():
                    (ws.root / fname).write_bytes(blob)
                t1 = time.monotonic()
                r = await ws.exec(argv, timeout=900, cwd=ws.root, env=env)
                secs = time.monotonic() - t1
            finally:
                path.write_text(original, encoding="utf-8")
            c = parse_pytest_summary(r.stdout_tail)
            run = c["passed"] + c["failed"] + c["error"] + c["skipped"]
            out["rows"].append({
                "id": rec["id"], "total": rec["total"], "killed_full": rec["n_full_failing"] > 0,
                "testmon_run": run, "testmon_failed": c["failed"] + c["error"], "testmon_seconds": round(secs, 2),
                "timed_out": r.timed_out, "gate_selected": rec["selected"],
                "gate_caught": rec["n_selected_failing"] > 0 or bool(rec["uncovered"]),
                "seconds_full": rec["seconds_full"]})
        out["status"] = "ran"
        progress(f"[{name}] testmon on {len(out['rows'])} mutants")
        return out
    except Exception as exc:  # noqa: BLE001
        out["detail"] = f"{type(exc).__name__}: {exc}"[:300]
        return out
    finally:
        if ws is not None:
            await ws.aclose()
        shutil.rmtree(tmp, ignore_errors=True)


def summarize_testmon(studies: list[dict]) -> dict:
    rows = [dict(r, project=s["project"]) for s in studies for r in s.get("rows", []) if not r["timed_out"]]
    killed = [r for r in rows if r["killed_full"]]

    def rate(k: int, n: int) -> dict:
        lo, hi = wilson(k, n)
        return {"k": k, "n": n, "rate": (k / n) if n else None, "ci95": [round(lo, 3), round(hi, 3)]}

    tm_missed = [r for r in killed if r["testmon_failed"] == 0]
    gate_missed = [r for r in killed if not r["gate_caught"]]
    return {
        "projects": sorted({r["project"] for r in rows}), "mutants": len(rows), "killed": len(killed),
        "testmon_missed": rate(len(tm_missed), len(killed)), "gate_missed": rate(len(gate_missed), len(killed)),
        "testmon_tests_share_mean": round(st.mean(min(1.0, r["testmon_run"] / r["total"]) for r in rows if r["total"]), 4) if rows else None,
        "gate_tests_share_mean": round(st.mean(r["gate_selected"] / r["total"] for r in rows if r["total"]), 4) if rows else None,
        "testmon_missed_ids": [r["id"] for r in tm_missed][:20],
        "baseline_seconds": {s["project"]: s.get("baseline_seconds") for s in studies if s.get("rows")},
        "per_project": {p: {"mutants": sum(1 for r in rows if r["project"] == p),
                            "killed": sum(1 for r in killed if r["project"] == p),
                            "testmon_missed": sum(1 for r in tm_missed if r["project"] == p),
                            "gate_missed": sum(1 for r in gate_missed if r["project"] == p),
                            "testmon_share": round(st.mean(min(1.0, r["testmon_run"] / r["total"]) for r in rows if r["project"] == p and r["total"]), 3),
                            "gate_share": round(st.mean(r["gate_selected"] / r["total"] for r in rows if r["project"] == p and r["total"]), 3)}
                        for p in sorted({r["project"] for r in rows})},
    }
