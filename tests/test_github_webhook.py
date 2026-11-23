"""The `/api/v1/github/webhook` HTTP surface: ping, signature checks,
/approve /reject comment parsing, permission checks, and approval resolution.

Ported from the former manual scripts/verify_phase4_webhook.py (now deleted).
"""

from __future__ import annotations

import asyncio

from agenticqa.models.enums import ApprovalState, RunState, Verdict
from agenticqa.models.run import Approval, PRRef, QARun, RepoRef

from conftest import post_webhook

OWNER, NAME, NUMBER = "acme", "widget", 42


def comment_payload(body: str, login: str = "alice", is_pr: bool = True) -> dict:
    issue: dict = {"number": NUMBER}
    if is_pr:
        issue["pull_request"] = {"url": "https://api.github.com/..."}
    return {
        "action": "created",
        "issue": issue,
        "comment": {"body": body, "user": {"login": login}},
        "repository": {
            "owner": {"login": OWNER},
            "name": NAME,
            "full_name": f"{OWNER}/{NAME}",
        },
    }


def make_awaiting_run(run_id="pr42run") -> QARun:
    return QARun(
        id=run_id,
        source="github",
        repo=RepoRef(owner=OWNER, name=NAME),
        pr=PRRef(number=NUMBER, title="Add coupons", head_sha="abc1234"),
        state=RunState.AWAITING_APPROVAL,
        verdict=Verdict.NEEDS_HUMAN_REVIEW,
        approval=Approval(state=ApprovalState.PENDING),
    )


def test_ping(client):
    r = post_webhook(client, "ping", {"zen": "hi"})
    assert r.status_code == 200 and r.json().get("pong"), r.text


def test_bad_signature_rejected(client):
    r = post_webhook(client, "issue_comment", comment_payload("/approve"), sign_ok=False)
    assert r.status_code == 401


def test_missing_signature_rejected(client):
    r = post_webhook(client, "issue_comment", comment_payload("/approve"), no_sig=True)
    assert r.status_code == 401


def test_non_command_comment_ignored(client):
    r = post_webhook(client, "issue_comment", comment_payload("lgtm, nice work"))
    assert r.status_code == 200 and r.json().get("ignored") == "no_command", r.text


def test_comment_on_non_pr_issue_ignored(client):
    r = post_webhook(client, "issue_comment", comment_payload("/approve", is_pr=False))
    assert r.status_code == 200 and r.json().get("ignored") == "not_a_pr", r.text


def test_approve_flow_permission_and_resolution(client, app):
    gh = app.state.github
    calls = {"status": [], "comment": [], "perm": {"value": "write"}}

    async def fake_status(repo_full, sha, state, **kw):
        calls["status"].append((repo_full, sha, state, kw.get("description", "")))
        return True

    async def fake_comment(repo_full, num, body, **kw):
        calls["comment"].append((repo_full, num, body))
        return True

    async def fake_perm(repo_full, username, **kw):
        return calls["perm"]["value"]

    gh.client.set_commit_status = fake_status
    gh.client.create_comment = fake_comment
    gh.client.collaborator_permission = fake_perm

    # /approve before any run exists for the PR -> ignored (permission check
    # passes -- perm defaults to "write" -- but there's nothing to resolve).
    r = post_webhook(client, "issue_comment", comment_payload("/approve"))
    assert r.status_code == 200 and r.json().get("ignored") == "no_run_for_pr", r.text

    asyncio.run(app.state.repo.save_run(make_awaiting_run()))

    # non-write commenter rejected (403) + warned via comment
    calls["perm"]["value"] = "read"
    r = post_webhook(client, "issue_comment", comment_payload("/approve", login="mallory"))
    assert r.status_code == 403
    assert len(calls["comment"]) == 1, str(calls["comment"])

    # write commenter approves -> run resolves to DONE/APPROVED
    calls["perm"]["value"] = "write"
    calls["status"].clear()
    calls["comment"].clear()
    r = post_webhook(client, "issue_comment", comment_payload("/approve", login="alice"))
    body = r.json() if r.status_code == 200 else {}
    assert r.status_code == 200, r.text
    assert body.get("state") == "done"
    assert body.get("approval") == "approved"
    assert body.get("actor") == "alice"
    assert any(s[2] == "success" for s in calls["status"]), str(calls["status"])
    assert len(calls["comment"]) >= 1

    # approving again (already resolved) is a no-op resolution
    calls["status"].clear()
    r = post_webhook(client, "issue_comment", comment_payload("/approve", login="alice"))
    assert r.status_code == 200 and r.json().get("state") == "done", r.text
