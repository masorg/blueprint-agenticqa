"""Which third-party packages a target project needs in order to run its tests.

The executor used to install only ``requirements.txt`` plus pytest, so a typical
real project (``pyproject.toml``, ``src/`` layout, test extras) failed at import.
This reads the project's declared dependencies plus its *test* extras / dependency
groups. The project itself is NOT installed: it is put on ``PYTHONPATH`` per run,
because an editable install would point a shared, cached venv at a per-run
directory that is deleted afterwards.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

# Optional-dependency / dependency-group names that conventionally hold test tooling.
TEST_KEYS = ("test", "tests", "testing", "dev")


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _expand_group(groups: dict, name: str, seen: set[str]) -> list[str]:
    """A PEP 735 dependency group, resolving ``{include-group = ...}`` entries."""
    if name in seen or name not in groups:
        return []
    seen = seen | {name}
    out: list[str] = []
    for item in groups[name] or []:
        if isinstance(item, str):
            out.append(item)
        elif isinstance(item, dict) and "include-group" in item:
            out += _expand_group(groups, item["include-group"], seen)
    return out


def project_requirements(root: Path) -> list[str]:
    """Core dependencies + test extras/groups from ``pyproject.toml`` (sorted, de-duplicated).
    Requirements that name the project itself (e.g. ``pkg[dev]``) are dropped."""
    pyproject = root / "pyproject.toml"
    if not pyproject.is_file():
        return []
    try:
        data = tomllib.loads(pyproject.read_text(errors="replace"))
    except tomllib.TOMLDecodeError:
        return []

    project = data.get("project") or {}
    reqs: list[str] = list(project.get("dependencies") or [])
    optional = project.get("optional-dependencies") or {}
    groups = data.get("dependency-groups") or {}
    for key in TEST_KEYS:
        reqs += optional.get(key) or []
        reqs += _expand_group(groups, key, set())

    own = _norm(project.get("name") or "")
    kept: set[str] = set()
    for req in reqs:
        req = req.strip()
        name = re.split(r"[\s\[<>=!~;@(]", req, maxsplit=1)[0]
        if req and not (own and _norm(name) == own):
            kept.add(req)
    return sorted(kept)
