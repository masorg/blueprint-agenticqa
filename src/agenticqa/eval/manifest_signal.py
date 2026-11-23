"""A repo-level routing signal: does the project depend on an LLM/agent SDK?

A single commit's diff often cannot show what kind of system it belongs to, but the
repository's dependency manifests can: an application that depends on ``openai`` or
``langgraph`` is an LLM system. This reads the manifests at the commit, so it is
deterministic, free, and needs no model.

The list of SDK names below was fixed BEFORE it was run on the routing set, and is
not tuned against it. Matching is on dependency names (``langchain-core`` counts as
``langchain``). A package's own ``name =`` line is ignored so a repository cannot
match itself, and comments, docs, examples and tests are not scanned.
"""

from __future__ import annotations

import re
import threading
from pathlib import Path

from .routing_set import Sample, _git, mirror

# Pre-registered; do not edit after looking at results. Deliberately excludes general
# ML libraries (transformers, torch, numpy), which do not make a project agentic.
LLM_DEPENDENCIES = (
    "openai", "anthropic", "langchain", "langgraph", "llama-index", "llamaindex",
    "litellm", "crewai", "autogen", "smolagents", "pydantic-ai", "google-genai",
    "google-generativeai", "mistralai", "cohere", "ollama", "groq", "haystack",
)
_MANIFEST_NAMES = ("pyproject.toml", "setup.py", "setup.cfg", "Pipfile")
_SKIP_PARTS = ("docs", "doc", "examples", "example", "test", "tests", "node_modules", "site-packages")
_MAX_DEPTH = 3
_TOKEN = re.compile(r"[a-z0-9][a-z0-9_-]*")

# A blob id identifies content exactly, so a manifest that is unchanged between commits
# (nearly all of them) is fetched and scanned once. Without this, every commit re-fetches
# every manifest one at a time from the remote.
_CACHE: dict[tuple[str, str], set[str]] = {}
_LOCK = threading.Lock()


def is_manifest(path: str) -> bool:
    parts = path.split("/")
    if len(parts) > _MAX_DEPTH + 1 or any(p in _SKIP_PARTS for p in parts[:-1]):
        return False
    base = parts[-1]
    return base in _MANIFEST_NAMES or (base.startswith("requirements") and base.endswith(".txt"))


def scan_text(text: str) -> set[str]:
    """LLM-SDK dependency names mentioned in a manifest's text."""
    found: set[str] = set()
    for raw in text.splitlines():
        line = raw.strip().lower()
        if not line or line.startswith("#") or re.match(r"(name|project|packages?)\s*[=:]", line):
            continue
        for tok in _TOKEN.findall(line):
            tok = tok.replace("_", "-")
            for dep in LLM_DEPENDENCIES:
                if tok == dep or tok.startswith(dep + "-"):
                    found.add(dep)
    return found


def signal(sample: Sample, workdir: Path) -> dict:
    """Which LLM-SDK dependencies the repo's manifests declare at this commit."""
    git_dir = mirror(sample.repo, workdir)
    listing = _git("ls-tree", "-r", sample.sha, cwd=git_dir)
    found: set[str] = set()
    files: list[str] = []
    for line in listing.splitlines():
        meta, _, path = line.partition("\t")
        if not is_manifest(path):
            continue
        blob = meta.split()[2]
        key = (sample.repo, blob)
        with _LOCK:
            hits = _CACHE.get(key)
        if hits is None:
            try:
                hits = scan_text(_git("cat-file", "-p", blob, cwd=git_dir, check=True))
            except Exception:  # noqa: BLE001 - an unreadable manifest is simply no signal
                hits = set()
            with _LOCK:
                _CACHE[key] = hits
        if hits:
            files.append(path)
            found |= hits
    return {"dependencies": sorted(found), "files": files[:5]}
