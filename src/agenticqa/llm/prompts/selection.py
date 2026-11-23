"""Selection prompt + schema: which tests, beyond the deterministic core, relate to this change?

The answer can only ADD tests. The pipeline keeps every core test whatever the model says, and ignores
ids that were not discovered, so a wrong, empty, or hostile answer cannot shrink the run.
"""

from __future__ import annotations

import json

from pydantic import BaseModel, Field

from ...models.pyramid import DiscoveredTest
from ...models.run import QARun

MAX_LISTED = 300
DIFF_LIMIT = 3000


class SelectionResponse(BaseModel):
    node_ids: list[str] = Field(description="Ids of tests, from the list given, that could exercise the change.")
    reason: str = Field(default="", description="One short sentence.")


SYSTEM = (
    "You are the SELECTION stage of a pull-request gate. A static import analysis already chose the tests "
    "that reach the changed files. Your job is to name ADDITIONAL tests that could exercise the change "
    "through a route imports do not show: a subprocess, a plugin or entry point, a configuration or data "
    "file, a prompt or tool definition, or a name that points to the changed code. Choose only from the ids "
    "given. If you are unsure, add the test; adding costs a little time, and missing one can hide a failure. "
    "Reply with an empty list when nothing else is related."
)


def build_selection_prompt(run: QARun, tests: list[DiscoveredTest], core: list[str]) -> tuple[str, str]:
    have = set(core)
    rest = [t for t in tests if t.node_id not in have]
    shown = rest[:MAX_LISTED]
    diff = (run.diff or "").strip()
    if len(diff) > DIFF_LIMIT:
        diff = diff[:DIFF_LIMIT] + "\n... (diff truncated)"
    user = "\n\n".join([
        f"Pull request: {run.pr.title}" if run.pr.number else "Local run.",
        "Changed files:\n" + "\n".join(f"- {p}" for p in (run.selection.changed if run.selection else [])[:60]),
        f"Tests already selected by import analysis: {len(core)}.",
        f"Tests not selected ({len(rest)}; first {len(shown)} shown):\n"
        + json.dumps([{"id": t.node_id} for t in shown]),
        "Diff:\n" + diff if diff else "No diff text.",
    ])
    return SYSTEM, user
