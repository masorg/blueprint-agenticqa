"""GitHub App authentication: mint a JWT, exchange it for installation tokens.

A GitHub App can't use a static PAT - it authenticates as itself with a
short-lived RS256 JWT (signed by the App's private key), then exchanges that for
a per-installation access token (~1h) to act on a given org/repo. Tokens are
cached per installation and refreshed just before expiry. Like the rest of
``github/``, every failure is swallowed (returns ``""`` + logs) so a token
hiccup never breaks a QA run.
"""

from __future__ import annotations

import base64
import logging
import time
from datetime import datetime
from pathlib import Path

import httpx
import jwt

from ..config import Settings

log = logging.getLogger("agenticqa.github")

# Mint the JWT slightly in the past (clock skew) and well under GitHub's 10-min cap.
_JWT_LEEWAY = 60
_JWT_TTL = 540  # 9 minutes
# Refresh an installation token this many seconds before it actually expires.
_REFRESH_SKEW = 60


def _load_pem(settings: Settings) -> str:
    """Resolve the App private key PEM ('' if not configured).

    Accepts a mounted ``.pem`` path (preferred on CAI), raw PEM, ``\\n``-escaped
    PEM, or base64-encoded PEM in the inline env var.
    """
    path = (settings.github_app_private_key_path or "").strip()
    if path:
        try:
            return Path(path).read_text()
        except OSError as exc:
            log.warning("could not read App private key at %s: %s", path, exc)
            return ""
    raw = settings.github_app_private_key or ""
    if not raw:
        return ""
    if "BEGIN" in raw:  # already PEM (possibly with escaped newlines)
        return raw.replace("\\n", "\n")
    try:  # otherwise assume base64-encoded PEM
        return base64.b64decode(raw).decode()
    except Exception as exc:  # noqa: BLE001
        log.warning("App private key is neither PEM nor base64: %s", exc)
        return ""


def _parse_expiry(iso: str | None) -> float:
    """Parse GitHub's ISO-8601 ``expires_at`` to an epoch; default now+1h."""
    if not iso:
        return time.time() + 3600
    try:
        return datetime.fromisoformat(iso).timestamp()
    except ValueError:
        return time.time() + 3600


class GitHubAppAuth:
    def __init__(self, settings: Settings, *, timeout: float = 15.0) -> None:
        self._app_id = (settings.github_app_id or "").strip()
        self._pem = _load_pem(settings)
        self._api = settings.github_api_url.rstrip("/")
        self._timeout = timeout
        self.enabled = bool(self._app_id and self._pem)
        # installation_id -> (token, expires_at_epoch)
        self._cache: dict[int, tuple[str, float]] = {}

    def _app_jwt(self) -> str:
        now = int(time.time())
        payload = {"iat": now - _JWT_LEEWAY, "exp": now + _JWT_TTL, "iss": self._app_id}
        return jwt.encode(payload, self._pem, algorithm="RS256")

    async def installation_token(self, installation_id: int | None) -> str:
        """Return a cached-or-fresh installation access token, or '' on failure."""
        if not (self.enabled and installation_id):
            return ""
        cached = self._cache.get(installation_id)
        if cached and cached[1] - _REFRESH_SKEW > time.time():
            return cached[0]
        token, exp = await self._mint_installation_token(installation_id)
        if token:
            self._cache[installation_id] = (token, exp)
        return token

    async def _mint_installation_token(self, installation_id: int) -> tuple[str, float]:
        url = f"{self._api}/app/installations/{installation_id}/access_tokens"
        headers = {
            "Authorization": f"Bearer {self._app_jwt()}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as c:
                r = await c.post(url, headers=headers)
            if r.status_code < 300:
                data = r.json()
                return data.get("token", ""), _parse_expiry(data.get("expires_at"))
            log.warning("installation token %s -> HTTP %s: %s",
                        installation_id, r.status_code, r.text[:200])
        except Exception as exc:  # noqa: BLE001
            log.warning("installation token mint failed for %s: %s", installation_id, exc)
        return "", 0.0
