"""Real-PR routing set: can a router tell what kind of system a change belongs to?

Merged PRs on squash-merge repositories are single commits, so a sample is a
public (repo, commit): the diff and message come straight from git history, with
no API token and no test execution. Routing is judged from exactly what the
production router is given - the PR title/body and the diff.

Validity choices, fixed BEFORE looking at any result:
* The repository list is fixed below (agent-centric vs plain Python), with the
  borderline LLM frameworks flagged ``ambiguous`` and reported separately.
* Sampling is seeded, from a fixed date window, over all non-merge commits - no
  content filter, so no researcher degree of freedom in which diffs are "good".
* The label is the repository's kind, so commits whose diff says nothing about
  agent behaviour (docs, CI, typing) are legitimately hard; samples are
  stratified by whether the diff touches source code.
* Each sample is routed with the repo name visible and masked: a model that knows
  ``langchain-ai/langgraph`` is agentic is not reading the diff.

The manifest committed to the repo lists only (repo, sha, label, title); diffs
are re-fetched from the public repositories, not redistributed.
"""

from __future__ import annotations

import json
import random
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

WINDOW = ("2026-06-01", "2026-09-30")
SEED = 20261014
PER_REPO = 8

# (owner/name, label, ambiguous)
REPOS: list[tuple[str, str, bool]] = [
    ("langchain-ai/langgraph", "agentic", False),
    ("huggingface/smolagents", "agentic", False),
    ("openai/openai-agents-python", "agentic", False),
    ("microsoft/autogen", "agentic", False),
    ("crewAIInc/crewAI", "agentic", False),
    ("pydantic/pydantic-ai", "agentic", False),
    ("Significant-Gravitas/AutoGPT", "agentic", False),
    ("langchain-ai/langchain", "agentic", True),   # LLM framework, not an agent itself
    ("run-llama/llama_index", "agentic", True),
    ("deepset-ai/haystack", "agentic", True),
    ("psf/requests", "plain_service", False),
    ("pallets/flask", "plain_service", False),
    ("tiangolo/fastapi", "plain_service", False),
    ("encode/httpx", "plain_service", False),
    ("pytest-dev/pytest", "plain_service", False),
    ("sqlalchemy/sqlalchemy", "plain_service", False),
    ("celery/celery", "plain_service", False),
    ("urllib3/urllib3", "plain_service", False),
    ("python-attrs/attrs", "plain_service", False),
    ("pallets/click", "plain_service", False),
]

_DOC_SUFFIX = (".md", ".rst", ".txt", ".adoc")
_NON_SOURCE_PREFIX = ("docs/", "doc/", ".github/", "examples/", "tests/", "test/")
_NON_SOURCE_NAMES = ("uv.lock", "poetry.lock", "package-lock.json", "requirements.txt", "pyproject.toml",
                     "setup.cfg", "tox.ini", ".gitignore", "LICENSE", "CHANGELOG", "Makefile")


@dataclass(frozen=True)
class Sample:
    repo: str
    sha: str
    label: str  # agentic | plain_service
    ambiguous: bool
    title: str


def _git(*args: str, cwd: Path | None = None, check: bool = True) -> str:
    out = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=300, check=check)
    return out.stdout


def mirror(repo: str, workdir: Path) -> Path:
    """A blobless bare clone: history is local, file contents are fetched on demand."""
    dest = workdir / (repo.replace("/", "__") + ".git")
    if not dest.exists():
        workdir.mkdir(parents=True, exist_ok=True)
        _git("clone", "-q", "--bare", "--filter=blob:none", "--single-branch",
             f"https://github.com/{repo}.git", str(dest))
    return dest


def build_manifest(workdir: Path, *, seed: int = SEED, per_repo: int = PER_REPO,
                   window: tuple[str, str] = WINDOW, progress=print) -> list[Sample]:
    rng = random.Random(seed)
    out: list[Sample] = []
    for repo, label, ambiguous in REPOS:
        try:
            git_dir = mirror(repo, workdir)
        except subprocess.CalledProcessError as exc:
            progress(f"  ! {repo}: clone failed ({exc.stderr.strip()[:80]}); skipped")
            continue
        shas = sorted(_git("log", f"--since={window[0]}", f"--until={window[1]}", "--no-merges",
                           "--format=%H", cwd=git_dir).split())
        picked = rng.sample(shas, min(per_repo, len(shas)))
        progress(f"  {repo}: {len(shas)} commits in window, sampled {len(picked)}")
        for sha in sorted(picked):
            title = _git("show", "-s", "--format=%s", sha, cwd=git_dir).strip()
            out.append(Sample(repo, sha, label, ambiguous, title))
    return out


def save_manifest(samples: list[Sample], path: Path, *, seed: int = SEED, window=WINDOW) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(
        {"seed": seed, "window": list(window), "per_repo": PER_REPO,
         "samples": [asdict(s) for s in samples]}, indent=1))


def load_manifest(path: Path) -> list[Sample]:
    return [Sample(**s) for s in json.loads(path.read_text())["samples"]]


def fetch(sample: Sample, workdir: Path, *, max_diff_chars: int = 200_000) -> dict:
    """Message, diff and changed paths for a sample, from the (cached) mirror."""
    git_dir = mirror(sample.repo, workdir)
    msg = _git("show", "-s", "--format=%B", sample.sha, cwd=git_dir)
    title, _, body = msg.strip().partition("\n")
    diff = _git("show", "--format=", "--patch", "--no-color", sample.sha, cwd=git_dir)
    paths = [p for p in _git("show", "--format=", "--name-only", sample.sha, cwd=git_dir).splitlines() if p]
    return {"title": title.strip(), "body": body.strip(), "diff": diff[:max_diff_chars], "paths": paths}


def touches_source(paths: list[str]) -> bool:
    """True if the change edits something other than docs, CI, tests, examples or metadata."""
    for p in paths:
        base = p.rsplit("/", 1)[-1]
        if p.lower().endswith(_DOC_SUFFIX) or p.startswith(_NON_SOURCE_PREFIX) or base in _NON_SOURCE_NAMES:
            continue
        return True
    return False
