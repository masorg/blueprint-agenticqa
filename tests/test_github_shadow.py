"""Shadow and advisory modes of the GitHub integration: what is posted and what is recorded."""

from __future__ import annotations

import json

from agenticqa.config import Settings
from agenticqa.github.service import GitHubService
from agenticqa.models.enums import RunState, Verdict
from agenticqa.models.run import PRRef, QARun, RepoRef


class _Client:
    def __init__(self):
        self.statuses, self.comments = [], []

    async def set_commit_status(self, repo, sha, state, **kw):
        self.statuses.append((repo, state, kw["context"]))
        return True

    async def create_comment(self, repo, number, body, **kw):
        self.comments.append((repo, number))
        return True


def _run():
    r = QARun(id="r1", source="github", repo=RepoRef(owner="o", name="n"), pr=PRRef(number=7, head_sha="abc"))
    r.state, r.verdict = RunState.AWAITING_APPROVAL, Verdict.NEEDS_HUMAN_REVIEW
    return r


async def test_shadow_mode_posts_nothing_and_records_the_decision(tmp_path):
    log = tmp_path / "s.jsonl"
    c = _Client()
    svc = GitHubService(c, Settings(github_mode="shadow", shadow_log_path=str(log)))
    await svc.report_pending(_run())
    await svc.report_verdict(_run())
    assert c.statuses == [] and c.comments == []
    rec = json.loads(log.read_text().splitlines()[-1])
    assert rec["repo"] == "o/n" and rec["pr"] == 7 and rec["verdict"] == "needs_human_review" and rec["mode"] == "shadow"


async def test_advisory_mode_posts_a_separate_status_and_no_comment(tmp_path):
    c = _Client()
    svc = GitHubService(c, Settings(github_mode="advisory", shadow_log_path=str(tmp_path / "s.jsonl")))
    await svc.report_verdict(_run())
    assert c.comments == [] and c.statuses and c.statuses[0][2].endswith("(advisory)")


async def test_enforce_mode_is_unchanged(tmp_path):
    c = _Client()
    svc = GitHubService(c, Settings(github_mode="enforce", shadow_log_path=str(tmp_path / "s.jsonl")))
    await svc.report_verdict(_run())
    assert c.statuses and c.comments and c.statuses[0][2] == "agentic-qa/review"
