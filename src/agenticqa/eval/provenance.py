"""Provenance for an eval report: enough to say exactly what produced a number.

A result in the paper must be traceable to a code state, a corpus definition
and a model. ``code_dirty`` / ``code_diff_sha256`` matter because experiments
are often run from an uncommitted tree; a clean SHA must not be claimed then.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from .corpus import Scenario


def _git(repo_root: Path, *args: str) -> str | None:
    try:
        out = subprocess.run(
            ["git", *args], cwd=repo_root, capture_output=True, text=True, timeout=15, check=True
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout


def corpus_fingerprint(scenarios: list[Scenario]) -> str:
    """Stable hash of the scenario definitions, so a report states which corpus it scored."""
    payload = json.dumps([dataclasses.asdict(s) for s in scenarios], sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def collect(
    *,
    repo_root: Path,
    scenarios: list[Scenario],
    corpus_name: str,
    provider: str,
    configured_model: str | None,
    response_models: list[str],
    argv: list[str],
) -> dict:
    sha = (_git(repo_root, "rev-parse", "HEAD") or "").strip() or None
    # Report files are this run's outputs, not code: they must not read as "dirty".
    status = _git(repo_root, "status", "--porcelain", "--", ".", ":(exclude)eval/reports")
    diff = _git(repo_root, "diff", "HEAD")
    return {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "git_sha": sha,
        "code_dirty": bool(status.strip()) if status is not None else None,
        # Pins the uncommitted state too: two "dirty" runs are comparable only if this matches.
        "code_diff_sha256": hashlib.sha256(diff.encode()).hexdigest()[:16] if diff else None,
        "corpus": corpus_name,
        "corpus_fingerprint": corpus_fingerprint(scenarios),
        "n_scenarios": len(scenarios),
        "provider": provider,
        "configured_model": configured_model,
        # What the API said answered (aliases like "-latest" resolve to dated ids).
        "response_models": sorted(response_models),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "argv": argv,
    }
