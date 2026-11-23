"""GitHub App JWT auth (github/app_auth.py): JWT minting, installation-token
caching/refresh, PEM loading (raw/\\n-escaped/base64), diff assembly, the
no-credential short-circuit, and the pull_request/issue_comment HTTP surface
when the app is configured in App mode.

Ported from the former manual scripts/verify_phase5_app.py (now deleted).
"""

from __future__ import annotations

import asyncio
import base64
import tempfile
import time

import jwt  # PyJWT, present via PyGithub
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from agenticqa.config import Settings
from agenticqa.github.app_auth import GitHubAppAuth, _load_pem
from agenticqa.github.client import GitHubClient, _assemble_diff
from agenticqa.models.enums import ApprovalSource

from conftest import FakeGitHubClient, FakeGitHubService, FakeRepo, post_webhook

_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PRIV_PEM = _key.private_bytes(
    serialization.Encoding.PEM,
    serialization.PrivateFormat.PKCS8,
    serialization.NoEncryption(),
).decode()
PUB_PEM = _key.public_key().public_bytes(
    serialization.Encoding.PEM,
    serialization.PublicFormat.SubjectPublicKeyInfo,
).decode()


def _settings(**over) -> Settings:
    s = Settings()
    s.github_app_id = over.get("app_id", "")
    s.github_app_private_key = over.get("private_key", "")
    s.github_app_private_key_path = over.get("private_key_path", "")
    return s


# ---- unit -------------------------------------------------------------------


def test_app_jwt_mints_and_verifies():
    auth = GitHubAppAuth(_settings(app_id="123456", private_key=PRIV_PEM))
    assert auth.enabled
    decoded = jwt.decode(auth._app_jwt(), PUB_PEM, algorithms=["RS256"])
    assert decoded.get("iss") == "123456"
    ttl = decoded["exp"] - decoded["iat"]
    assert 0 < ttl <= 600


def test_installation_token_cached_while_fresh():
    auth = GitHubAppAuth(_settings(app_id="123456", private_key=PRIV_PEM))
    calls = {"n": 0}

    async def mint_fresh(_iid):
        calls["n"] += 1
        return f"tok-{calls['n']}", time.time() + 3600

    auth._mint_installation_token = mint_fresh
    t1 = asyncio.run(auth.installation_token(42))
    t2 = asyncio.run(auth.installation_token(42))
    assert t1 == t2 == "tok-1" and calls["n"] == 1


def test_near_expiry_token_refreshed():
    auth = GitHubAppAuth(_settings(app_id="123456", private_key=PRIV_PEM))
    calls = {"n": 0}

    async def mint_stale(_iid):
        calls["n"] += 1
        return f"tok-{calls['n']}", time.time() + 1  # inside the refresh skew

    auth._mint_installation_token = mint_stale
    asyncio.run(auth.installation_token(7))
    asyncio.run(auth.installation_token(7))
    assert calls["n"] == 2


def test_load_pem_from_path():
    with tempfile.NamedTemporaryFile("w", suffix=".pem", delete=False) as fh:
        fh.write(PRIV_PEM)
        pem_path = fh.name
    assert _load_pem(_settings(private_key_path=pem_path)) == PRIV_PEM


def test_load_pem_raw():
    assert _load_pem(_settings(private_key=PRIV_PEM)) == PRIV_PEM


def test_load_pem_base64():
    b64 = base64.b64encode(PRIV_PEM.encode()).decode()
    assert _load_pem(_settings(private_key=b64)) == PRIV_PEM


def test_load_pem_escaped_newlines():
    escaped = PRIV_PEM.replace("\n", "\\n")
    loaded = _load_pem(_settings(private_key=escaped))
    assert "BEGIN" in loaded and "\n" in loaded


def test_assemble_diff_includes_patched_skips_binary_and_truncates():
    files = [
        {"filename": "app.py", "patch": "@@ -1 +1 @@\n-x\n+y"},
        {"filename": "logo.png"},  # binary: no patch
    ]
    d = _assemble_diff(files, 200_000)
    assert "diff --git a/app.py b/app.py" in d
    assert "logo.png" not in d
    capped = _assemble_diff([{"filename": "big.py", "patch": "x" * 1000}], 100)
    assert "truncated" in capped


def test_client_disabled_with_no_credentials():
    c = GitHubClient(token="", app_auth=None)
    assert c.enabled is False
    assert asyncio.run(c.compare_diff("o/r", "b", "h")) == ""


# ---- http: pull_request + issue_comment surfaces ---------------------------


PR_PAYLOAD = {
    "action": "opened",
    "pull_request": {
        "number": 7, "title": "Add feature", "body": "desc",
        "head": {"sha": "headsha123"}, "base": {"sha": "basesha456"},
    },
    "repository": {
        "name": "inventory-orders-api",
        "full_name": "masorg/inventory-orders-api",
        "owner": {"login": "masorg"},
        "clone_url": "https://github.com/masorg/inventory-orders-api.git",
    },
    "installation": {"id": 99},
}


class _FakeEngine:
    def __init__(self) -> None:
        self.submitted: list = []
        self.resolved: list = []

    async def submit(self, run):
        self.submitted.append(run)
        return run

    async def resolve_approval(self, run_id, approved, source, actor):
        from agenticqa.models.enums import ApprovalState, RunState
        from agenticqa.models.run import Approval, QARun

        self.resolved.append((run_id, approved, source, actor))
        st = ApprovalState.APPROVED if approved else ApprovalState.REJECTED
        return QARun(id=run_id, state=RunState.DONE, approval=Approval(state=st, source=source, actor=actor))


@pytest.fixture()
def wired(client, app):
    from agenticqa.models.enums import RunState
    from agenticqa.models.run import QARun

    fake_engine = _FakeEngine()
    fake_github = FakeGitHubService(FakeGitHubClient())
    fake_repo = FakeRepo(QARun(id="await-1", state=RunState.AWAITING_APPROVAL))
    app.state.engine = fake_engine
    app.state.github = fake_github
    app.state.repo = fake_repo
    return client, fake_engine, fake_github, fake_repo


def test_ping(wired):
    client, *_ = wired
    r = post_webhook(client, "ping", {"zen": "hi"})
    assert r.status_code == 200 and r.json().get("pong") is True


def test_pull_request_opened_submits_run_with_diff(wired):
    client, fake_engine, fake_github, _ = wired
    r = post_webhook(client, "pull_request", PR_PAYLOAD)
    assert r.status_code == 200 and r.json().get("run_id"), r.text
    sub = fake_engine.submitted[-1]
    assert sub.source == "github"
    assert sub.repo.owner == "masorg" and sub.repo.name == "inventory-orders-api"
    assert sub.pr.head_sha == "headsha123" and sub.pr.base_sha == "basesha456"
    assert sub.installation_id == 99
    assert sub.diff == "diff --git a/app.py b/app.py\n@@ -1 +1 @@\n-x\n+y\n"
    assert fake_github.client.compare_calls[-1] == (
        "masorg/inventory-orders-api", "basesha456", "headsha123", 99
    )


def test_pull_request_closed_ignored(wired):
    client, *_ = wired
    r = post_webhook(client, "pull_request", dict(PR_PAYLOAD, action="closed"))
    assert r.json().get("ignored") == "closed"


def test_pull_request_missing_head_sha_incomplete(wired):
    import copy

    client, *_ = wired
    p_incomplete = copy.deepcopy(PR_PAYLOAD)
    p_incomplete["pull_request"]["head"]["sha"] = ""
    r = post_webhook(client, "pull_request", p_incomplete)
    assert r.json().get("ignored") == "incomplete"


COMMENT_PAYLOAD = {
    "action": "created",
    "issue": {"number": 7, "pull_request": {"url": "x"}},
    "comment": {"body": "/approve", "user": {"login": "alice"}},
    "repository": {
        "name": "inventory-orders-api",
        "full_name": "masorg/inventory-orders-api",
        "owner": {"login": "masorg"},
    },
    "installation": {"id": 99},
}


def test_writer_approve_resolves_via_engine(wired):
    client, fake_engine, fake_github, _ = wired
    fake_github.client.perm = "write"
    r = post_webhook(client, "issue_comment", COMMENT_PAYLOAD)
    assert r.status_code == 200, r.text
    assert fake_github.client.perm_calls[-1] == ("masorg/inventory-orders-api", "alice", 99)
    assert fake_engine.resolved[-1] == ("await-1", True, ApprovalSource.GITHUB, "alice")


def test_readonly_approve_403_and_warns(wired):
    client, _, fake_github, _ = wired
    fake_github.client.perm = "read"
    r = post_webhook(client, "issue_comment", COMMENT_PAYLOAD)
    assert r.status_code == 403
    warn = fake_github.client.comments[-1]
    assert warn[2] == 99


def test_issue_comment_no_command_ignored(wired):
    import copy

    client, *_ = wired
    no_cmd = copy.deepcopy(COMMENT_PAYLOAD)
    no_cmd["comment"]["body"] = "looks good to me"
    r = post_webhook(client, "issue_comment", no_cmd)
    assert r.json().get("ignored") == "no_command"


def test_bad_signature_precedes_everything(wired):
    client, *_ = wired
    r = post_webhook(client, "pull_request", PR_PAYLOAD, sign_ok=False)
    assert r.status_code == 401
