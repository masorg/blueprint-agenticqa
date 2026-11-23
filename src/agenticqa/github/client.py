"""Thin async GitHub REST client (httpx).

Just the calls the app needs: set a commit status, comment on a PR, read a
user's repo permission, and fetch a PR's diff via the compare API. Auth resolves
per call: an installation token (GitHub App mode) when an installation id is
known, else the static token (PAT mode), else nothing - in which case every call
logs and returns a benign value instead of raising, so a missing/expired
credential can never fail a QA run.
"""

from __future__ import annotations

import logging

import httpx

from .app_auth import GitHubAppAuth

log = logging.getLogger("agenticqa.github")


def _assemble_diff(files: list[dict], max_bytes: int) -> str:
    """Turn compare-API file entries into a unified diff string, capped."""
    parts: list[str] = []
    size = 0
    for f in files:
        patch = f.get("patch")
        if not patch:
            continue  # binary or too-large file: no patch provided
        name = f.get("filename", "")
        block = f"diff --git a/{name} b/{name}\n{patch}\n"
        if size + len(block) > max_bytes:
            parts.append(f"\n… diff truncated at {max_bytes} bytes …\n")
            break
        parts.append(block)
        size += len(block)
    return "".join(parts)


class GitHubClient:
    def __init__(
        self,
        token: str = "",
        *,
        app_auth: GitHubAppAuth | None = None,
        api_url: str = "https://api.github.com",
        timeout: float = 15.0,
    ) -> None:
        self._token = token
        self._app_auth = app_auth
        self._api = api_url.rstrip("/")
        self._timeout = timeout
        self.enabled = bool(token) or bool(app_auth and app_auth.enabled)

    async def _token_for(self, installation_id: int | None) -> str:
        """Resolve the auth token for a call: installation token, else PAT, else ''."""
        if self._app_auth and self._app_auth.enabled:
            tok = await self._app_auth.installation_token(installation_id)
            if tok:
                return tok
        return self._token

    def _headers(self, token: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    async def set_commit_status(
        self,
        repo_full: str,
        sha: str,
        state: str,  # pending | success | failure | error
        *,
        context: str,
        description: str = "",
        target_url: str = "",
        installation_id: int | None = None,
    ) -> bool:
        """POST a commit status. Returns True on success, False if skipped/failed."""
        token = await self._token_for(installation_id)
        if not token:
            log.info("github: no credential - would set %s status=%s on %s@%s",
                     context, state, repo_full, (sha or "")[:7])
            return False
        if not (repo_full and sha):
            return False
        body = {"state": state, "context": context, "description": description[:140]}
        if target_url:
            body["target_url"] = target_url
        return await self._post(f"/repos/{repo_full}/statuses/{sha}", body, token) is not None

    async def create_comment(
        self,
        repo_full: str,
        issue_number: int,
        body: str,
        *,
        installation_id: int | None = None,
    ) -> bool:
        token = await self._token_for(installation_id)
        if not token:
            log.info("github: no credential - would comment on %s#%s", repo_full, issue_number)
            return False
        if not (repo_full and issue_number):
            return False
        return await self._post(
            f"/repos/{repo_full}/issues/{issue_number}/comments", {"body": body}, token
        ) is not None

    async def collaborator_permission(
        self, repo_full: str, username: str, *, installation_id: int | None = None
    ) -> str:
        """Return 'admin' | 'maintain' | 'write' | 'triage' | 'read' | 'none'.

        'none' when it can't be determined (no credential, error) - the caller
        decides whether that's allowed. GitHub returns the effective permission.
        """
        token = await self._token_for(installation_id)
        if not (token and repo_full and username):
            return "none"
        data = await self._get(
            f"/repos/{repo_full}/collaborators/{username}/permission", token
        )
        return (data or {}).get("permission", "none")

    async def compare_diff(
        self,
        repo_full: str,
        base: str,
        head: str,
        *,
        installation_id: int | None = None,
        max_bytes: int = 200_000,
    ) -> str:
        """Fetch a PR's unified diff via the compare API. '' on failure."""
        token = await self._token_for(installation_id)
        if not (token and repo_full and base and head):
            return ""
        data = await self._get(f"/repos/{repo_full}/compare/{base}...{head}", token)
        if not data:
            return ""
        return _assemble_diff(data.get("files") or [], max_bytes)

    async def get_pull(self, repo_full: str, number: int, *, installation_id: int | None = None) -> dict | None:
        """Fetch one pull request (for UI-triggered reviews). None on failure."""
        token = await self._token_for(installation_id)
        if not (token and repo_full and number):
            return None
        return await self._get(f"/repos/{repo_full}/pulls/{number}", token)

    async def list_open_pulls(self, repo_full: str, *, installation_id: int | None = None) -> list[dict]:
        """List open PRs (newest first) for the review picker. [] on failure."""
        token = await self._token_for(installation_id)
        if not (token and repo_full):
            return []
        data = await self._get(
            f"/repos/{repo_full}/pulls?state=open&sort=updated&direction=desc&per_page=30", token
        )
        return data if isinstance(data, list) else []

    async def ci_conclusion(self, repo_full: str, sha: str, *, exclude_context: str = "agentic-qa",
                            installation_id: int | None = None) -> dict:
        """The project's own CI result for a commit: check runs and commit statuses, ignoring GATE's own."""
        token = await self._token_for(installation_id)
        if not (token and repo_full and sha):
            return {"conclusion": "unknown", "checks": []}
        checks = (await self._get(f"/repos/{repo_full}/commits/{sha}/check-runs?per_page=100", token)) or {}
        status = (await self._get(f"/repos/{repo_full}/commits/{sha}/status", token)) or {}
        items = [(c.get("name", ""), c.get("status"), c.get("conclusion")) for c in checks.get("check_runs", [])]
        items += [(s.get("context", ""), "completed", {"success": "success", "failure": "failure",
                                                     "error": "failure"}.get(s.get("state"), None))
                  for s in status.get("statuses", [])]
        items = [i for i in items if not i[0].startswith(exclude_context)]
        if not items:
            conclusion = "none"
        elif any(st != "completed" for _, st, _ in items):
            conclusion = "pending"
        elif any(c in ("failure", "timed_out", "cancelled", "action_required") for _, _, c in items):
            conclusion = "failure"
        else:
            conclusion = "success"
        return {"conclusion": conclusion, "checks": [{"name": n, "conclusion": c} for n, _, c in items][:50]}

    async def _get(self, path: str, token: str):
        url = f"{self._api}{path}"
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as c:
                r = await c.get(url, headers=self._headers(token))
            if r.status_code < 300:
                return r.json() if r.content else {}
            log.warning("GET %s -> HTTP %s: %s", path, r.status_code, r.text[:200])
        except Exception as exc:  # noqa: BLE001
            log.warning("GET %s failed: %s", path, exc)
        return None

    async def _post(self, path: str, body: dict, token: str) -> dict | None:
        url = f"{self._api}{path}"
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as c:
                r = await c.post(url, headers=self._headers(token), json=body)
            if r.status_code < 300:
                return r.json() if r.content else {}
            log.warning("POST %s -> HTTP %s: %s", path, r.status_code, r.text[:200])
        except Exception as exc:  # noqa: BLE001 - never let a GitHub call break a run
            log.warning("POST %s failed: %s", path, exc)
        return None
