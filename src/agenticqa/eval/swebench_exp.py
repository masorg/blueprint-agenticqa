"""Does the selector find the tests that expose real defects? SWE-bench Verified, without running anything.

SWE-bench Verified (Jimenez et al.; human-validated subset) pairs real GitHub issues with the patch that fixed
them (`patch`), the tests that were added or changed (`test_patch`), and the tests that fail before the fix and
pass after it (`FAIL_TO_PASS`). Those tests are, by construction, the tests that a change to the fixed code
affects.

We turn each instance into the situation GATE faces: a change that *introduces* the defect. The tree is the
state before the fix with the test patch applied (so the exposing tests exist), and the change is the reverse of
the fix. A safe selector must select at least one of the exposing tests, or send the change to a person. We
compute the selection statically from the diff, so no environment is needed and no test runs.

The measure per instance: the change is *caught* if the selection has an exposing test, or the change is sent
to a person (a file no test reaches), or the whole suite runs. Otherwise it is *missed*.
"""

from __future__ import annotations

import ast
import json
import random
import re
import shutil
import statistics as st
import subprocess
import tempfile
from collections import defaultdict
from pathlib import Path

from ..selection import RepoGraph, select_tests, static_tests
from .mutation import wilson

DATASET = "princeton-nlp/SWE-bench_Verified"
SEED = 20261024
PER_REPO_CAP = 20


def load_instances() -> list[dict]:
    import pyarrow.parquet as pq
    from huggingface_hub import HfFileSystem

    with HfFileSystem().open(f"datasets/{DATASET}/data/test-00000-of-00001.parquet") as fh:
        table = pq.read_table(fh, columns=["instance_id", "repo", "base_commit", "patch", "test_patch",
                                           "FAIL_TO_PASS", "version"])
    return table.to_pylist()


def sample_instances(rows: list[dict], cap: int = PER_REPO_CAP, seed: int = SEED) -> list[dict]:
    """At most `cap` instances per repository, drawn with a fixed seed, so no repository dominates."""
    by_repo: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_repo[r["repo"]].append(r)
    rng = random.Random(seed)
    out = []
    for repo in sorted(by_repo):
        items = sorted(by_repo[repo], key=lambda r: r["instance_id"])
        rng.shuffle(items)
        out += items[:cap]
    return out


def exposing_tests(f2p: list[str]) -> list[tuple[str | None, str]]:
    """(class or None, function) of each FAIL_TO_PASS entry, for pytest ids, Django ids, and bare names."""
    out = []
    for entry in f2p:
        e = entry.strip()
        m = re.match(r"^(\w+) \(([\w.]+)\)", e)  # Django: test_x (module.Class)
        if m:
            out.append((m.group(2).split(".")[-1], m.group(1)))
            continue
        parts = [re.sub(r"\[.*$", "", p) for p in e.split("::")]
        if len(parts) >= 3:
            out.append((parts[-2], parts[-1]))
        else:
            out.append((None, parts[-1]))
    return out


def _git(cwd: Path, *args: str, timeout: int = 1800, check: bool = False) -> subprocess.CompletedProcess:
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=timeout)
    if check and r.returncode:
        raise RuntimeError(f"git {' '.join(args[:3])} failed: {r.stderr[-300:]}")
    return r


def _apply(root: Path, patch: str, name: str) -> None:
    f = root / f".{name}.patch"
    f.write_text(patch)
    r = subprocess.run(["git", "apply", "--whitespace=nowarn", str(f)], cwd=root, capture_output=True, text=True)
    f.unlink(missing_ok=True)
    if r.returncode:
        raise RuntimeError(f"cannot apply {name}: {r.stderr[-200:]}")


def ensure_clone(repo: str, workdir: Path) -> Path:
    d = workdir / repo.replace("/", "__")
    if not (d / ".git").exists():
        subprocess.run(["git", "clone", "-q", "--filter=blob:none", "--no-checkout",
                        f"https://github.com/{repo}.git", str(d)], capture_output=True, text=True, timeout=3600, check=True)
    return d


def run_instance(inst: dict, root: Path) -> dict:
    """Build the 'buggy' tree and the reverse-fix diff, select, and judge. Returns one result row."""
    row = {k: inst[k] for k in ("instance_id", "repo", "version")}
    row["status"] = "error"
    try:
        f2p = json.loads(inst["FAIL_TO_PASS"]) if isinstance(inst["FAIL_TO_PASS"], str) else list(inst["FAIL_TO_PASS"])
        _git(root, "checkout", "-q", "-f", inst["base_commit"], check=True)
        _git(root, "clean", "-q", "-fdx")
        _apply(root, inst["test_patch"], "test")
        _git(root, "add", "-A")
        _git(root, "-c", "user.name=x", "-c", "user.email=x@x", "commit", "-q", "-m", "buggy", check=True)
        buggy = _git(root, "rev-parse", "HEAD", check=True).stdout.strip()
        _apply(root, inst["patch"], "fix")
        _git(root, "add", "-A")
        _git(root, "-c", "user.name=x", "-c", "user.email=x@x", "commit", "-q", "-m", "fixed", check=True)
        fixed = _git(root, "rev-parse", "HEAD", check=True).stdout.strip()
        diff = _git(root, "diff", "--no-color", "--no-ext-diff", fixed, buggy, check=True).stdout
        _git(root, "checkout", "-q", "-f", buggy, check=True)  # the tree GATE sees: the defect is present
        test_files = set(re.findall(r"^diff --git a/(\S+) b/", inst["test_patch"], re.M))
        graph = RepoGraph(root)
        tests = static_tests(graph)
        want = exposing_tests(f2p)
        keys = defaultdict(list)
        for t in tests:
            parts = t.node_id.split("::")
            keys[(parts[-2] if len(parts) >= 3 else None, parts[-1])].append(t)
            keys[(None, parts[-1])].append(t)  # class-agnostic fallback
        targets: set[str] = set()
        for cls, fn in want:
            for t in keys.get((cls, fn), []) or keys.get((None, fn), []):
                if t.file in test_files:
                    targets.add(t.node_id)
        sel = select_tests(root, diff, tests, graph=graph)
        chosen = set(sel.selected)
        hit = targets & chosen
        src_files = [c for c in sel.changed if not re.search(r"(^|/)(tests?|testing)(/|$)|test_", c)]
        row.update(
            status="ok", n_tests=len(tests), n_changed=len(sel.changed), n_source_files=len(src_files),
            n_f2p=len(f2p), n_targets=len(targets), n_targets_selected=len(hit), selected=len(chosen),
            mode=sel.mode, reason=sel.reason[:80], uncovered=sel.uncovered[:5], out_of_scope=sel.out_of_scope[:5],
            caught=bool(hit) or bool(sel.uncovered) or sel.mode == "full",
            caught_by_test=bool(hit), caught_by_review=bool(sel.uncovered) and not hit,
            caught_by_full=sel.mode == "full" and not hit,
            file_selected=len(select_tests(root, diff, tests, graph=graph, level="file").selected),
        )
        return row
    except Exception as exc:  # noqa: BLE001
        row["detail"] = f"{type(exc).__name__}: {exc}"[:300]
        return row


def run_repo(repo: str, instances: list[dict], workdir: Path, progress=print) -> list[dict]:
    root = ensure_clone(repo, workdir)
    rows = []
    for i, inst in enumerate(instances):
        row = run_instance(inst, root)
        rows.append(row)
        progress(f"[{repo} {i + 1}/{len(instances)}] {inst['instance_id']} {row['status']} "
                 f"caught={row.get('caught')} selected={row.get('selected')}/{row.get('n_tests')}")
    return rows


def summarize(rows: list[dict]) -> dict:
    def rate(k: int, n: int) -> dict:
        lo, hi = wilson(k, n)
        return {"k": k, "n": n, "rate": (k / n) if n else None, "ci95": [round(lo, 3), round(hi, 3)]}

    ok = [r for r in rows if r["status"] == "ok"]
    usable = [r for r in ok if r["n_targets"] > 0]  # the exposing tests could be located in the syntax trees
    caught = [r for r in usable if r["caught"]]
    by_test = [r for r in usable if r["caught_by_test"]]
    shares = [r["selected"] / r["n_tests"] for r in usable if r["n_tests"] and r["mode"] == "change"]
    return {
        "attempted": len(rows), "analysed": len(ok), "usable": len(usable),
        "failed_to_analyse": len(rows) - len(ok), "exposing_tests_not_located": len(ok) - len(usable),
        "caught": rate(len(caught), len(usable)), "missed": rate(len(usable) - len(caught), len(usable)),
        "missed_ids": [r["instance_id"] for r in usable if not r["caught"]],
        "caught_by_a_selected_test": rate(len(by_test), len(usable)),
        "caught_by_review": sum(1 for r in usable if r["caught_by_review"]),
        "caught_by_full_run": sum(1 for r in usable if r["caught_by_full"]),
        "targets_selected_share": round(st.mean(r["n_targets_selected"] / r["n_targets"] for r in usable), 4) if usable else None,
        "selected_share": {"mean": round(st.mean(shares), 4) if shares else None,
                           "median": round(st.median(shares), 4) if shares else None},
        "file_level_selected_share": round(st.mean(r["file_selected"] / r["n_tests"] for r in usable if r["n_tests"]), 4) if usable else None,
        "by_repo": {repo: {"usable": sum(1 for r in usable if r["repo"] == repo),
                           "caught": sum(1 for r in usable if r["repo"] == repo and r["caught"]),
                           "mean_selected_share": round(st.mean(r["selected"] / r["n_tests"] for r in usable
                                                                if r["repo"] == repo and r["n_tests"]), 3)
                           if any(r["repo"] == repo and r["n_tests"] for r in usable) else None}
                    for repo in sorted({r["repo"] for r in rows})},
    }
