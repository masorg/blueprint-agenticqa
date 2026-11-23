"""Shared fixtures for the pytest suite.

Env vars mirroring the old scripts/verify_*.py setup must be in place BEFORE
``agenticqa.config``/``agenticqa.main`` are imported anywhere, because
``agenticqa.config.get_settings()`` caches a single ``Settings()`` instance at
first call (module-level ``_settings`` global) and ``agenticqa.main`` builds
its module-level ``app`` singleton at import time via that cached settings.
Setting env vars here, at conftest module scope, runs before pytest imports
any test module that imports ``agenticqa.main``.
"""

from __future__ import annotations

import os

os.environ.setdefault("AGENTICQA_GITHUB_WEBHOOK_SECRET", "test-secret")
os.environ.setdefault("AGENTICQA_LLM_PROVIDER", "stub")
os.environ.setdefault("AGENTICQA_SEED_SAMPLES", "0")
os.environ.setdefault("AGENTICQA_APP_API_KEY", "")
os.environ.setdefault("AGENTICQA_GITHUB_TOKEN", "fake-token-enables-client")

import pytest
from fastapi.testclient import TestClient

from agenticqa.main import create_app

GITHUB_WEBHOOK_SECRET = os.environ["AGENTICQA_GITHUB_WEBHOOK_SECRET"]
FAKE_DIFF = "diff --git a/app.py b/app.py\n@@ -1 +1 @@\n-x\n+y\n"


@pytest.fixture()
def app():
    """A fresh FastAPI app instance per test (own ``app.state``)."""
    return create_app()


@pytest.fixture()
def client(app):
    """A TestClient wrapping a fresh app, with the lifespan run."""
    with TestClient(app) as c:
        yield c


# ---- shared fakes used across the GitHub-facing test modules --------------


class FakeGitHubClient:
    """Stands in for ``agenticqa.github.client.GitHubClient``: no network."""

    def __init__(self) -> None:
        self.enabled = True
        self.perm = "write"
        self.compare_calls: list = []
        self.perm_calls: list = []
        self.status_calls: list = []
        self.comments: list = []

    async def compare_diff(self, repo_full, base, head, *, installation_id=None, max_bytes=200_000):
        self.compare_calls.append((repo_full, base, head, installation_id))
        return FAKE_DIFF

    async def collaborator_permission(self, repo_full, username, *, installation_id=None):
        self.perm_calls.append((repo_full, username, installation_id))
        return self.perm

    async def set_commit_status(self, repo_full, sha, state, *, context="", description="",
                                 target_url="", installation_id=None):
        self.status_calls.append((repo_full, sha, state, context, description, target_url, installation_id))
        return True

    async def create_comment(self, repo_full, number, body, *, installation_id=None):
        self.comments.append((repo_full, number, installation_id, body))
        return True


class FakeGitHubService:
    def __init__(self, gh_client: FakeGitHubClient | None = None) -> None:
        self.client = gh_client or FakeGitHubClient()


class FakeEngine:
    """Stands in for ``orchestrator.engine.Engine``."""

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
        state = ApprovalState.APPROVED if approved else ApprovalState.REJECTED
        return QARun(id=run_id, state=RunState.DONE, approval=Approval(state=state, source=source, actor=actor))


class FakeRepo:
    """Stands in for ``store.db.RunRepository`` where only ``latest_run_for_pr``
    (or ``save_run``/``get_run`` for the seeded-run tests) is needed."""

    def __init__(self, run=None) -> None:
        self.run = run
        self.saved: list = []

    async def latest_run_for_pr(self, owner, name, number):
        return self.run

    async def save_run(self, run) -> None:
        self.saved.append(run)
        self.run = run

    async def get_run(self, run_id: str):
        return self.run if self.run is not None and self.run.id == run_id else None


def sign(body: bytes, secret: str = GITHUB_WEBHOOK_SECRET) -> str:
    import hashlib
    import hmac

    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def post_webhook(client, event: str, payload: dict, *, sign_ok: bool = True, no_sig: bool = False):
    import json

    body = json.dumps(payload).encode()
    headers = {"X-GitHub-Event": event, "Content-Type": "application/json"}
    if not no_sig:
        headers["X-Hub-Signature-256"] = sign(body) if sign_ok else "sha256=deadbeef"
    return client.post("/api/v1/github/webhook", content=body, headers=headers)
