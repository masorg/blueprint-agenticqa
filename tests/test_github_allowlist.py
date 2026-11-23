"""Operator-side org/enterprise allowlist + the org-webhook path.

Ported from the former manual scripts/verify_allowlist.py (now deleted).
"""

from __future__ import annotations

from agenticqa.api.routes_github import _allowlisted, _event_org_enterprise
from agenticqa.config import Settings
from agenticqa.models.run import QARun

from conftest import FakeGitHubService, post_webhook


def _settings(orgs: str = "", enterprises: str = "") -> Settings:
    s = Settings()
    s.github_allowed_orgs = orgs
    s.github_allowed_enterprises = enterprises
    return s


# ---- unit: Settings parsing -------------------------------------------------


def test_allowed_orgs_parsed_normalized():
    s = _settings(orgs=" Acme, acme-Labs ,", enterprises="Acme-Inc")
    assert s.allowed_orgs == {"acme", "acme-labs"}
    assert s.allowed_enterprises == {"acme-inc"}


def test_allowlist_active_flag():
    assert _settings(orgs="acme").github_allowlist_active is True
    assert _settings().github_allowlist_active is False


# ---- unit: org/enterprise extraction ---------------------------------------


def test_extract_org_and_enterprise_from_payload():
    org, ent = _event_org_enterprise({
        "organization": {"login": "MasOrg"},
        "repository": {"owner": {"login": "ignored"}},
        "enterprise": {"slug": "Acme-Inc"},
    })
    assert org == "masorg"
    assert ent == "acme-inc"


def test_extract_org_falls_back_to_repo_owner():
    org, ent = _event_org_enterprise({"repository": {"owner": {"login": "FallbackOrg"}}})
    assert org == "fallbackorg"
    assert ent == ""


# ---- unit: the gate itself ---------------------------------------------------


def test_allowlist_open_when_unconfigured():
    foreign = {"repository": {"owner": {"login": "otherorg"}}}
    assert _allowlisted(foreign, _settings()) is True


def test_allowlist_org_match_and_miss():
    member = {"repository": {"owner": {"login": "acme"}}}
    foreign = {"repository": {"owner": {"login": "otherorg"}}}
    assert _allowlisted(member, _settings(orgs="acme")) is True
    assert _allowlisted(foreign, _settings(orgs="acme")) is False


def test_allowlist_enterprise_match_and_miss():
    by_ent = {"repository": {"owner": {"login": "someorg"}}, "enterprise": {"slug": "acme-inc"}}
    assert _allowlisted(by_ent, _settings(enterprises="acme-inc")) is True
    assert _allowlisted(by_ent, _settings(enterprises="other-inc")) is False


def test_allowlist_either_list_satisfies():
    member = {"repository": {"owner": {"login": "acme"}}}
    assert _allowlisted(member, _settings(orgs="acme", enterprises="nope")) is True


# ---- http: allowlisted org submits, foreign org is ignored ------------------


def _pr(owner: str, *, enterprise: str | None = None, with_installation: bool = True) -> dict:
    p: dict = {
        "action": "opened",
        "pull_request": {
            "number": 7, "title": "t", "body": "b",
            "head": {"sha": "head1"}, "base": {"sha": "base1"},
        },
        "organization": {"login": owner},
        "repository": {
            "name": "repo", "full_name": f"{owner}/repo",
            "owner": {"login": owner},
            "clone_url": f"https://github.com/{owner}/repo.git",
        },
    }
    if enterprise:
        p["enterprise"] = {"slug": enterprise}
    if with_installation:
        p["installation"] = {"id": 99}
    return p


class _FakeEngine:
    def __init__(self) -> None:
        self.submitted: list[QARun] = []

    async def submit(self, run: QARun) -> QARun:
        self.submitted.append(run)
        return run


def test_allowlisted_org_submits_and_foreign_org_ignored(client, app):
    fake_engine = _FakeEngine()
    app.state.engine = fake_engine
    app.state.github = FakeGitHubService()
    settings = app.state.settings
    settings.github_allowed_orgs = "masorg"
    settings.github_allowed_enterprises = ""

    r = post_webhook(client, "pull_request", _pr("masorg"))
    assert r.status_code == 200 and r.json().get("run_id"), r.text
    n_after_allowed = len(fake_engine.submitted)

    r = post_webhook(client, "pull_request", _pr("otherorg"))
    assert r.status_code == 200
    assert r.json().get("ignored") == "not_allowlisted"
    assert len(fake_engine.submitted) == n_after_allowed


def test_enterprise_allowlist_admits_member_org(client, app):
    fake_engine = _FakeEngine()
    app.state.engine = fake_engine
    app.state.github = FakeGitHubService()
    settings = app.state.settings
    settings.github_allowed_orgs = ""
    settings.github_allowed_enterprises = "acme-inc"

    r = post_webhook(client, "pull_request", _pr("subsidiary", enterprise="acme-inc"))
    assert r.status_code == 200 and r.json().get("run_id"), r.text


def test_org_webhook_without_installation_still_flows(client, app):
    fake_engine = _FakeEngine()
    app.state.engine = fake_engine
    app.state.github = FakeGitHubService()
    settings = app.state.settings
    settings.github_allowed_orgs = ""
    settings.github_allowed_enterprises = ""

    r = post_webhook(client, "pull_request", _pr("anyorg", with_installation=False))
    assert r.status_code == 200 and r.json().get("run_id"), r.text
    sub = fake_engine.submitted[-1]
    assert sub.installation_id is None
    assert sub.diff == "diff --git a/app.py b/app.py\n@@ -1 +1 @@\n-x\n+y\n"
