"""How often real changes remove or weaken tests: the diff only, so no code of the change is run.

For every pull request of the shadow replay that ran, and for the agent-written pull requests of the field
sample, fetch the head and its base, take the diff, and apply GATE's check for removed or weakened tests.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from ..selection import diff_hunks, parse_diff, weakened_tests


def _git(cwd: Path, *a: str, timeout: int = 900) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True, timeout=timeout)


def one(repo: str, number: int, base_ref: str | None, n_commits: int | None) -> dict:
    out = {"repo": repo, "number": number, "status": "error"}
    d = Path(tempfile.mkdtemp(prefix="weak-"))
    try:
        _git(d, "init", "-q")
        _git(d, "remote", "add", "origin", f"https://github.com/{repo}.git")
        if base_ref:  # shadow replay: merge base with the default branch
            if _git(d, "fetch", "-q", "origin", f"+refs/heads/{base_ref}:refs/remotes/origin/{base_ref}",
                    f"+refs/pull/{number}/head:refs/pr").returncode:
                out["status"] = "fetch_failed"
                return out
            base = _git(d, "merge-base", f"origin/{base_ref}", "refs/pr").stdout.strip()
        else:  # agent pull request: the head walked back by its commit count
            if _git(d, "fetch", "-q", f"--depth={n_commits + 1}", "origin", f"+refs/pull/{number}/head:refs/pr").returncode:
                out["status"] = "fetch_failed"
                return out
            base = _git(d, "rev-parse", f"refs/pr~{n_commits}").stdout.strip()
        if not base:
            out["status"] = "no_base"
            return out
        diff = _git(d, "diff", "--no-color", "--no-ext-diff", base, "refs/pr").stdout
        changed = parse_diff(diff) or []
        w = weakened_tests(changed, diff_hunks(diff)) if changed else []
        out.update(status="ok", weakened=w, kinds=sorted({x.split(": ", 1)[1].split(" ")[0] if ": " in x else x for x in w}))
        return out
    except Exception as exc:  # noqa: BLE001
        out["detail"] = f"{type(exc).__name__}: {exc}"[:200]
        return out
    finally:
        shutil.rmtree(d, ignore_errors=True)


def run_all(shadow_dir: Path, field_manifest: Path, jobs: int = 6) -> dict:
    shadow = [json.loads(p.read_text()) for p in sorted(shadow_dir.glob("*.json"))]
    shadow = [r for r in shadow if r.get("status") == "ran"]
    manifest = json.loads((shadow_dir.parents[1] / "eval/data/shadow-manifest.json").read_text())["attempts"]
    branch = {(a["repo"], a["number"]): a["branch"] for a in manifest}
    agents = json.loads(field_manifest.read_text())["attempts"]
    tasks = [("shadow", r["repo"], r["number"], branch[(r["repo"], r["number"])], None,
              bool(r.get("introduced"))) for r in shadow]
    tasks += [("agent", a["repo"], a["number"], None, a["n_commits"], None) for a in agents]
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        res = list(pool.map(lambda t: {**one(t[1], t[2], t[3], t[4]), "set": t[0], "regression": t[5]}, tasks))

    def block(rows: list[dict]) -> dict:
        ok = [r for r in rows if r["status"] == "ok"]
        w = [r for r in ok if r["weakened"]]
        kinds: dict[str, int] = {}
        for r in w:
            for k in r["weakened"]:
                kk = ("test file deleted" if "deleted" in k else "test removed" if "removed" in k else
                      "skip added" if "skip" in k else "fewer assertions")
                kinds[kk] = kinds.get(kk, 0) + 1
        return {"analysed": len(ok), "attempted": len(rows), "weakened_prs": len(w),
                "share": round(len(w) / len(ok), 4) if ok else None, "by_kind": kinds}

    return {"shadow": block([r for r in res if r["set"] == "shadow"]),
            "agent": block([r for r in res if r["set"] == "agent"]), "results": res}
