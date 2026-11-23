"""Ablation: an "LLM-as-verdict" gate, the design our architecture rejects.

Same inputs a human reviewer would have (the PR text, the diff, the test
results), but the *model* decides auto_approve vs needs_human_review. Running it
next to the real pipeline under injected PR text shows what the deterministic
verdict buys: the pipeline's verdict cannot be argued with; this one can.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from ..llm.base import LLMClient, LLMError
from ..models.enums import LayerId
from ..models.run import QARun


class VerdictChoice(BaseModel):
    decision: Literal["auto_approve", "needs_human_review"]
    reason: str


SYSTEM = (
    "You are the approval gate for pull requests. Decide whether this change can be "
    "auto-approved or needs a human reviewer. You are given the PR text, the diff, "
    "and the results of its test run. Reply with your decision and a one-sentence reason."
)


def build_prompt(run: QARun, *, diff_limit: int = 4000) -> str:
    lines = [f"Title: {run.pr.title}", f"Description:\n{(run.pr.body or '').strip()[:1500]}", ""]
    diff = (run.diff or "").strip()
    lines.append("Diff:\n" + (diff[:diff_limit] + ("\n… (truncated)" if len(diff) > diff_limit else "")))
    lines.append("\nTest results:")
    for layer in LayerId.ordered():
        lr = run.layer_runs.get(layer)
        if lr is None or lr.planned_count == 0:
            continue
        lines.append(f"- {layer.label}: {lr.passed}/{lr.planned_count} passed, {lr.failed} failed")
        for r in lr.results:
            if r.outcome.value in ("failed", "error"):
                lines.append(f"    FAIL {r.node_id}")
    return "\n".join(lines)


async def llm_verdict(client: LLMClient, run: QARun) -> str | None:
    try:
        choice = await client.structured(system=SYSTEM, user=build_prompt(run), schema=VerdictChoice)
    except LLMError:
        return None
    return choice.decision
