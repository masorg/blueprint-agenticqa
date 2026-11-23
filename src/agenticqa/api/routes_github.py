"""Inbound GitHub webhook: POST /api/v1/github/webhook.

The single inbound entry point for the GitHub App. Two events matter:

* ``pull_request`` (opened / synchronize / reopened) - start a QA run. The
  webhook carries no diff, so the app fetches it itself via the compare API,
  then submits the run to the engine exactly like the ``/api/v1/runs`` trigger.
* ``issue_comment`` (created) - a reviewer approves or rejects an awaiting run
  by commenting **/approve** or **/reject** on the PR.

Every request is HMAC-verified against ``AGENTICQA_GITHUB_WEBHOOK_SECRET`` (a
webhook with no secret configured is refused, never trusted). For approvals the
commenter's repo permission is checked against the GitHub API - the comment text
is never trusted for authorization. Resolution goes through
``engine.resolve_approval``, the same convergence point as in-app approval,
which posts the confirmation comment and flips the commit status.
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel

from ..github import GitHubService, verify_signature
from ..models.enums import ApprovalSource
from ..models.run import PRRef, QARun, RepoRef
from ..orchestrator.engine import Engine
from .deps import get_engine, get_github, get_repo, get_settings

log = logging.getLogger("agenticqa.github")

router = APIRouter(prefix="/api/v1/github", tags=["github"])

_WRITE_ROLES = {"admin", "maintain", "write"}
_PR_ACTIONS = {"opened", "synchronize", "reopened"}


def _event_org_enterprise(payload: dict) -> tuple[str, str]:
    """Best-effort (org_login, enterprise_slug) from any webhook payload."""
    org = ((payload.get("organization") or {}).get("login")
           or ((payload.get("repository") or {}).get("owner") or {}).get("login")
           or "")
    enterprise = (payload.get("enterprise") or {}).get("slug", "") or ""
    return org.lower(), enterprise.lower()


def _allowlisted(payload: dict, settings) -> bool:
    """The operator-side gate. Open when no org/enterprise is configured;
    otherwise the event's org OR enterprise must be listed."""
    if not settings.github_allowlist_active:
        return True
    org, enterprise = _event_org_enterprise(payload)
    return org in settings.allowed_orgs or enterprise in settings.allowed_enterprises


def _parse_command(body: str) -> bool | None:
    """Return True for /approve, False for /reject, None if neither is present."""
    for line in (body or "").splitlines():
        tok = line.strip().lower()
        if tok.startswith("/approve"):
            return True
        if tok.startswith("/reject"):
            return False
    return None


@router.post("/webhook")
async def webhook(
    request: Request,
    x_github_event: str = Header(default=""),
    x_hub_signature_256: str = Header(default=""),
    engine: Engine = Depends(get_engine),
    github: GitHubService | None = Depends(get_github),
) -> dict:
    settings = get_settings(request)
    raw = await request.body()

    # Authenticity first - refuse anything we can't verify.
    if not settings.github_webhook_secret:
        raise HTTPException(status_code=503, detail="Webhook secret not configured.")
    if not verify_signature(settings.github_webhook_secret, raw, x_hub_signature_256):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Bad signature.")

    if x_github_event == "ping":
        return {"ok": True, "pong": True}

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON.")

    # Operator-side allowlist: even a broadly-wired webhook only acts on the
    # orgs/enterprises we've opted in. Silently ignored (200) when out of scope.
    if not _allowlisted(payload, settings):
        org, enterprise = _event_org_enterprise(payload)
        log.info("ignoring %s from org=%s enterprise=%s (not allowlisted)",
                 x_github_event, org or "?", enterprise or "?")
        return {"ok": True, "ignored": "not_allowlisted"}

    if x_github_event == "pull_request":
        return await _handle_pull_request(payload, engine, github)
    if x_github_event == "issue_comment":
        return await _handle_issue_comment(payload, request, engine, github)
    return {"ok": True, "ignored": x_github_event or "unknown"}


async def _handle_pull_request(
    payload: dict, engine: Engine, github: GitHubService | None
) -> dict:
    action = payload.get("action")
    if action not in _PR_ACTIONS:
        return {"ok": True, "ignored": action or "no_action"}

    pr = payload.get("pull_request") or {}
    repo = payload.get("repository") or {}
    owner = (repo.get("owner") or {}).get("login", "")
    name = repo.get("name", "")
    repo_full = repo.get("full_name") or f"{owner}/{name}"
    clone_url = repo.get("clone_url")
    number = pr.get("number")
    head_sha = (pr.get("head") or {}).get("sha")
    base_sha = (pr.get("base") or {}).get("sha")
    installation_id = (payload.get("installation") or {}).get("id")

    if not (owner and name and head_sha):
        return {"ok": True, "ignored": "incomplete"}

    run = await _submit_pr_run(
        engine, github, owner=owner, name=name, clone_url=clone_url,
        number=number, title=pr.get("title", ""), body=pr.get("body") or "",
        head_sha=head_sha, base_sha=base_sha, installation_id=installation_id,
    )
    log.info("github pull_request %s -> run %s (%s#%s)", action, run.id, repo_full, number)
    return {"ok": True, "run_id": run.id, "state": run.state.value, "pr": number}


async def _submit_pr_run(
    engine: Engine, github: GitHubService | None, *, owner: str, name: str,
    clone_url: str | None, number, title: str, body: str,
    head_sha: str | None, base_sha: str | None, installation_id: int | None = None,
) -> QARun:
    """Build a github-source QARun from PR fields (webhook OR UI review) and submit it.

    The webhook carries no diff, so we fetch it via the compare API (best-effort;
    the router tolerates an empty diff and considers every layer)."""
    repo_full = f"{owner}/{name}"
    diff = ""
    if github is not None and github.client.enabled and base_sha:
        diff = await github.client.compare_diff(repo_full, base_sha, head_sha, installation_id=installation_id)
    run = QARun(
        id=Engine.new_run_id(),
        source="github",
        repo=RepoRef(owner=owner, name=name, clone_url=clone_url),
        pr=PRRef(number=number, title=title or "", body=body or "", head_sha=head_sha, base_sha=base_sha),
        installation_id=installation_id,
        diff=diff,
    )
    await engine.submit(run)
    return run


class _ReviewRequest(BaseModel):
    repo: str      # "owner/name"
    number: int


@router.get("/pulls")
async def list_pulls(repo: str, github: GitHubService | None = Depends(get_github)) -> list[dict]:
    """Open PRs for the review picker (UI-driven, no webhook needed)."""
    if github is None or not github.client.enabled:
        raise HTTPException(status_code=503, detail="GitHub client not configured.")
    pulls = await github.client.list_open_pulls(repo)
    return [
        {"number": p.get("number"), "title": p.get("title", "") or "",
         "head_sha": ((p.get("head") or {}).get("sha") or "")[:7]}
        for p in pulls
    ]


@router.post("/review")
async def review_pr(
    req: _ReviewRequest,
    engine: Engine = Depends(get_engine),
    github: GitHubService | None = Depends(get_github),
) -> dict:
    """Trigger a review of a real open PR from the dashboard - same pipeline as
    the webhook, pull-triggered instead of push-triggered. Fast, repeatable."""
    if github is None or not github.client.enabled:
        raise HTTPException(status_code=503, detail="GitHub client not configured.")
    if "/" not in req.repo:
        raise HTTPException(status_code=400, detail="repo must be 'owner/name'.")
    pr = await github.client.get_pull(req.repo, req.number)
    if not pr:
        raise HTTPException(status_code=404, detail=f"PR {req.repo}#{req.number} not found.")
    owner, name = req.repo.split("/", 1)
    head = pr.get("head") or {}
    clone_url = ((head.get("repo") or {}).get("clone_url")) or f"https://github.com/{req.repo}.git"
    run = await _submit_pr_run(
        engine, github, owner=owner, name=name, clone_url=clone_url,
        number=req.number, title=pr.get("title", ""), body=pr.get("body") or "",
        head_sha=head.get("sha"), base_sha=(pr.get("base") or {}).get("sha"),
    )
    log.info("github review (UI) -> run %s (%s#%s)", run.id, req.repo, req.number)
    return {"ok": True, "run_id": run.id, "state": run.state.value, "pr": req.number}


async def _handle_issue_comment(
    payload: dict, request: Request, engine: Engine, github: GitHubService | None
) -> dict:
    if payload.get("action") != "created" or "comment" not in payload:
        return {"ok": True, "ignored": "action"}
    # Only PR comments matter; plain issues have no pull_request handle.
    if "pull_request" not in (payload.get("issue") or {}):
        return {"ok": True, "ignored": "not_a_pr"}

    decision = _parse_command((payload.get("comment") or {}).get("body", ""))
    if decision is None:
        return {"ok": True, "ignored": "no_command"}

    repo = payload.get("repository") or {}
    owner = (repo.get("owner") or {}).get("login", "")
    name = repo.get("name", "")
    repo_full = repo.get("full_name") or f"{owner}/{name}"
    number = (payload.get("issue") or {}).get("number")
    commenter = ((payload.get("comment") or {}).get("user") or {}).get("login", "")
    installation_id = (payload.get("installation") or {}).get("id")

    # Never trust the comment text for authorization - ask GitHub.
    if github is None or not github.client.enabled:
        raise HTTPException(status_code=503, detail="GitHub client not configured.")
    perm = await github.client.collaborator_permission(
        repo_full, commenter, installation_id=installation_id
    )
    if perm not in _WRITE_ROLES:
        log.warning("rejecting /%s from %s on %s (permission=%s)",
                    "approve" if decision else "reject", commenter, repo_full, perm)
        await github.client.create_comment(
            repo_full, number,
            f"@{commenter} - only users with write access can approve or reject this run.",
            installation_id=installation_id,
        )
        raise HTTPException(status_code=403, detail="Commenter lacks write access.")

    run_repo = get_repo(request)
    run = await run_repo.latest_run_for_pr(owner, name, number)
    if run is None:
        return {"ok": True, "ignored": "no_run_for_pr"}

    resolved = await engine.resolve_approval(run.id, decision, ApprovalSource.GITHUB, commenter)
    if resolved is None:
        return {"ok": True, "ignored": "run_gone"}
    return {
        "ok": True,
        "run_id": resolved.id,
        "state": resolved.state.value,
        "approval": resolved.approval.state.value,
        "actor": commenter,
    }
