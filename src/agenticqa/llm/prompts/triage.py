"""Triage prompt: a human-readable narrative of the finished run.

Free text, not structured - the LLM writes the *narrative* only. The
approve / needs-review verdict is computed deterministically downstream, so the
prompt explicitly forbids the model from stating a final decision.
"""

from __future__ import annotations

from ...models.enums import LayerId
from ...models.run import QARun

SYSTEM = (
    "You are the TRIAGE stage of an automated software-QA pyramid. Write a concise "
    "report for a human reviewer that summarises the run: which layers ran, what "
    "passed, what failed, and what the failures most likely indicate. Group by "
    "layer, call out the specific failing tests, and note any layer that ran no "
    "tests.\n\n"
    "Use GitHub-flavoured markdown: a one-line summary, then bullets. Keep it under "
    "about 180 words. Do NOT state a final approve/reject or pass/fail decision for "
    "the change - a separate deterministic gate owns that. Report the evidence, not "
    "the verdict."
)


def build_triage_prompt(run: QARun) -> tuple[str, str]:
    """Return (system, user) messages for the triage call."""
    lines: list[str] = [
        f"Repository: {run.repo.full_name}",
        f"Routed as: {run.routing.repo_kind.value if run.routing else 'unknown'}"
        + (f" - {run.routing.summary}" if run.routing and run.routing.summary else ""),
        "",
        "Layer results:",
    ]

    for layer in LayerId.ordered():
        applicable = run.layer_active(layer)
        lr = run.layer_runs.get(layer)
        if not applicable:
            lines.append(f"- {layer.label}: not applicable (dark).")
            continue
        if lr is None or lr.planned_count == 0:
            lines.append(f"- {layer.label}: applicable, but no tests were classified here.")
            continue
        detail = f"- {layer.label}: {lr.passed}/{lr.planned_count} passed"
        if lr.failed:
            detail += f", {lr.failed} failed"
        if lr.skipped:
            detail += f", {lr.skipped} skipped"
        if lr.error:
            detail += f" (layer error: {lr.error})"
        lines.append(detail)
        for r in lr.results:
            if r.outcome.value in ("failed", "error"):
                msg = (r.message or "").strip().splitlines()
                tail = msg[-1] if msg else ""
                lines.append(f"    · FAIL {r.node_id}: {tail[:200]}")

    t = run.totals
    lines.append("")
    lines.append(
        f"Totals across applicable layers: {t['passed']} passed, {t['failed']} failed, "
        f"{t['skipped']} skipped."
    )
    return SYSTEM, "\n".join(lines)
