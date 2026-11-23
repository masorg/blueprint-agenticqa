"""Maps a run's lifecycle onto GitHub commit statuses and PR comments.

One convergence point for the two moments GitHub cares about: when a verdict is
reached, and when a human resolves an approval (in-app *or* via a `/approve`
comment). Both go through the same status/comment calls, so the PR check and the
dashboard can't drift apart. Everything no-ops cleanly when GitHub isn't
configured or the run didn't come from GitHub.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from ..config import Settings
from ..models.enums import ApprovalState, RunState, Verdict
from ..models.run import QARun
from .client import GitHubClient

log = logging.getLogger("agenticqa.github")


def verify_signature(secret: str, body: bytes, header: str) -> bool:
    """Constant-time check of GitHub's X-Hub-Signature-256 header."""
    if not (secret and header and header.startswith("sha256=")):
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header)


class GitHubService:
    def __init__(self, client: GitHubClient, settings: Settings) -> None:
        self.client = client
        self._mode = (settings.github_mode or "enforce").lower()
        self._context = settings.github_status_context + (" (advisory)" if self._mode == "advisory" else "")
        self._base = (settings.public_base_url or "").rstrip("/")
        self._shadow_log = Path(settings.shadow_log_path)

    # ---- helpers -------------------------------------------------------

    def active_for(self, run: QARun) -> bool:
        return bool(
            run.source == "github"
            and run.repo.owner
            and run.repo.name
            and run.pr.head_sha
        )

    def _repo_full(self, run: QARun) -> str:
        return f"{run.repo.owner}/{run.repo.name}"

    def _target(self, run: QARun) -> str:
        return f"{self._base}/runs/{run.id}" if self._base else ""

    def record(self, run: QARun, event: str) -> None:
        """Append one decision to the shadow log (what GATE decided and why; no code or text of the change)."""
        sel = run.selection
        rec = {
            "at": datetime.now(timezone.utc).isoformat(), "event": event, "mode": self._mode,
            "repo": self._repo_full(run), "pr": run.pr.number, "head_sha": run.pr.head_sha,
            "state": run.state.value, "verdict": run.verdict.value if run.verdict else None,
            "verdict_reason": run.verdict_reason, "error": (run.error or "")[:200], "totals": run.totals,
            "selection": sel.model_dump() if sel else None,
            "layers": {l.value: {"state": lr.state.value, "planned": lr.planned_count, "failed": lr.failed}
                       for l, lr in run.layer_runs.items()},
            "approval": run.approval.state.value if run.approval else None,
        }
        try:
            self._shadow_log.parent.mkdir(parents=True, exist_ok=True)
            with self._shadow_log.open("a") as fh:
                fh.write(json.dumps(rec) + "\n")
        except OSError as exc:
            log.warning("shadow log write failed: %s", exc)

    async def _status(self, run: QARun, state: str, description: str) -> None:
        if self._mode == "shadow":
            return
        await self.client.set_commit_status(
            self._repo_full(run),
            run.pr.head_sha or "",
            state,
            context=self._context,
            description=description,
            target_url=self._target(run),
            installation_id=run.installation_id,
        )

    async def _comment(self, run: QARun, body: str) -> None:
        if self._mode in ("shadow", "advisory"):
            return
        if run.pr.number:
            await self.client.create_comment(
                self._repo_full(run), run.pr.number, body,
                installation_id=run.installation_id,
            )

    # ---- lifecycle reporting ------------------------------------------

    async def report_pending(self, run: QARun) -> None:
        if not self.active_for(run):
            return
        await self._status(run, "pending", "Agentic QA review started…")

    async def report_verdict(self, run: QARun) -> None:
        """Called once a verdict is reached (auto-approve, needs-review, or failed)."""
        if not self.active_for(run):
            return
        self.record(run, "verdict")
        t = run.totals
        if run.state == RunState.FAILED:
            await self._status(run, "error", f"Run failed: {run.error[:100]}")
            await self._comment(run, f"### ❌ Agentic QA - run failed\n\n{run.error}")
            return
        if run.verdict == Verdict.NEEDS_HUMAN_REVIEW:
            await self._status(
                run, "pending",
                f"Needs human review - {t['failed']} test(s) failed. Comment /approve or /reject.",
            )
            await self._comment(run, self._verdict_comment(run, needs_review=True))
        else:  # AUTO_APPROVE
            await self._status(run, "success", f"All {t['passed']} applicable test(s) passed.")
            await self._comment(run, self._verdict_comment(run, needs_review=False))

    async def report_approval(self, run: QARun) -> None:
        """Called after a human resolves an awaiting run (in-app or via comment)."""
        if not self.active_for(run):
            return
        self.record(run, "approval")
        actor = run.approval.actor or "a reviewer"
        if run.approval.state == ApprovalState.APPROVED:
            await self._status(run, "success", f"Approved by {actor}.")
            await self._comment(run, f"### ✅ Approved by **{actor}**\n\nCleared to merge.")
        elif run.approval.state == ApprovalState.REJECTED:
            await self._status(run, "failure", f"Rejected by {actor}.")
            await self._comment(run, f"### 🛑 Rejected by **{actor}**")

    # ---- comment bodies -----------------------------------------------

    def _verdict_comment(self, run: QARun, *, needs_review: bool) -> str:
        t = run.totals
        head = (
            "### ⚠️ Agentic QA - needs human review"
            if needs_review
            else "### ✅ Agentic QA - auto-approved"
        )
        lines = [head, "", f"**{t['passed']} passed · {t['failed']} failed · {t['skipped']} skipped**", ""]
        if run.triage_report:
            lines.append(run.triage_report)
        if needs_review:
            lines += ["", "---", "Reply **/approve** to merge or **/reject** to block."]
        if self._target(run):
            lines += ["", f"[View the full run →]({self._target(run)})"]
        return "\n".join(lines)
