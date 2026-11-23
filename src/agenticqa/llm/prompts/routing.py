"""Routing prompt + schema: is the SUT agentic, and which layers apply?

The response is a `RoutingResponse`; the LLMRouter maps it onto a
`RoutingDecision` and re-applies the hard guardrail (agentic-only layers can
never be applicable for a plain service) so a model slip can't widen the plan.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from ...models.run import QARun

RepoKindLit = Literal["agentic", "plain_service"]
LayerLit = Literal["unit", "property", "trajectory", "llm_judge", "adversarial"]


class LayerVerdict(BaseModel):
    layer: LayerLit
    applicable: bool
    reason: str = Field(description="One short sentence justifying the call.")


class RoutingResponse(BaseModel):
    repo_kind: RepoKindLit
    summary: str = Field(description="One or two sentences on how the change was routed.")
    layers: list[LayerVerdict]


SYSTEM = (
    "You are the ROUTING stage of an automated software-QA pyramid. Given a code "
    "change, you make two decisions.\n\n"
    "1. repo_kind - is the system under test an AGENTIC system (an LLM/agent "
    "application: it calls an LLM provider SDK, builds prompts, uses tools, plans, "
    "or reasons over multi-step trajectories), or a PLAIN_SERVICE (an ordinary API, "
    "library, or service with no LLM/agent behaviour)? Judge by what the code does, "
    "not by the repo name.\n\n"
    "2. For each of the five pyramid layers, whether it applies to this change:\n"
    "   - unit: applies to every system.\n"
    "   - property: property-based / invariant tests - applies to every system.\n"
    "   - adversarial: security, safety, fuzz, injection - applies to every system.\n"
    "   - trajectory: agent tool-call ordering / multi-step traces - AGENTIC ONLY.\n"
    "   - llm_judge: rubric / quality scoring by an LLM - AGENTIC ONLY.\n\n"
    "Rules: bias toward marking a layer applicable when unsure, EXCEPT the two "
    "agentic-only layers (trajectory, llm_judge) must be applicable=false whenever "
    "repo_kind is plain_service. Always return all five layers."
)


def build_routing_prompt(run: QARun, *, diff_limit: int = 6000) -> tuple[str, str]:
    """Return (system, user) messages for the routing call."""
    diff = (run.diff or "").strip()
    if len(diff) > diff_limit:
        diff = diff[:diff_limit] + "\n… (diff truncated)"

    parts = [
        f"Repository: {run.repo.full_name}",
        f"Pull request: #{run.pr.number} - {run.pr.title}" if run.pr.number else "Local run (no PR).",
    ]
    if run.pr.body:
        parts.append(f"PR description:\n{run.pr.body.strip()[:1500]}")
    if diff:
        parts.append(f"Unified diff:\n{diff}")
    else:
        parts.append(
            "No diff was provided. Route from the repository name and PR title alone; "
            "when the change's nature is unclear, treat it as a plain service."
        )
    return SYSTEM, "\n\n".join(parts)
