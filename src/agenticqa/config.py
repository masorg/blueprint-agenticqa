"""Application settings, driven by environment variables (prefix AGENTICQA_)."""

from __future__ import annotations

import os

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AGENTICQA_", env_file=".env", extra="ignore")

    # Server
    host: str = "127.0.0.1"
    port: int = 8000

    # Auth: API key required on the trigger endpoint. Empty = auth disabled (dev only).
    app_api_key: str = ""

    # Storage
    db_path: str = "data/agenticqa.db"
    workspace_dir: str | None = None  # None -> system temp

    # Orchestration
    max_concurrent_runs: int = 2
    # "change": run the tests selected from the diff (fails closed to all tests); "full": run every test.
    selection: str = "change"
    # Policy: trust that a computed import never loads a changed project file. Off (safe) by default; turning it
    # on saves more tests on projects that load modules dynamically, and is unsound if the trust is wrong.
    selection_trust_dynamic: bool = False
    # Policy: a stored coverage recording (JSON written by `shadow-record`) for the coverage-based core. Unset, or
    # unreadable, means the static core. Code added after the recording is unreached and goes to a person.
    selection_coverage_map: str | None = None

    # Seed the store with sample runs on boot so the dashboard has content to
    # show. Harmless in the demo; set AGENTICQA_SEED_SAMPLES=0 for a clean start.
    seed_samples: bool = True

    # LLM. "stub" uses deterministic heuristics (Phase 2, no keys needed);
    # "openai" is the primary real provider; "anthropic"/"cloudera" also supported.
    llm_provider: str = "stub"

    # OpenAI (primary). base_url left blank uses api.openai.com; set it to point
    # at an OpenAI-compatible endpoint (incl. Cloudera AI Inference).
    openai_api_key: str = ""
    openai_model: str = "gpt-4o"
    openai_base_url: str = ""

    # Mistral speaks the OpenAI wire protocol, so it reuses OpenAIClient.
    mistral_api_key: str = ""
    mistral_model: str = "mistral-large-latest"
    mistral_base_url: str = "https://api.mistral.ai/v1"

    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-5"
    cloudera_ai_endpoint: str = ""
    cloudera_ai_api_key: str = ""
    cloudera_model: str = ""

    # Shared LLM client tuning. timeout is per request (seconds); max_retries is
    # the number of corrective retries on the JSON-mode structured-output path.
    llm_timeout_seconds: float = 40.0
    llm_max_retries: int = 1
    # HTTP-level retries with backoff for rate limits (429) and transient 5xx. Separate
    # from llm_max_retries, which re-asks the model when its JSON fails validation.
    llm_transport_retries: int = 4

    # GitHub (Phase 4). token authenticates the app's REST calls (commit status,
    # PR comments, permission checks) - a PAT is fine for the demo. webhook_secret
    # verifies inbound /approve comments (HMAC). public_base_url makes the commit
    # status "Details" link point back at this app's run page.
    github_token: str = ""
    github_webhook_secret: str = ""
    github_api_url: str = "https://api.github.com"
    github_status_context: str = "agentic-qa/review"
    # How the integration acts on a pull request. "enforce": statuses and comments, as designed. "advisory": a
    # status under a separate context that branch protection does not require, and no comments. "shadow": nothing
    # is posted; each decision is appended to `shadow_log_path` for a later comparison with the project's own CI.
    github_mode: str = "enforce"
    shadow_log_path: str = "data/shadow-decisions.jsonl"
    public_base_url: str = ""  # e.g. https://<app>.cml... ; blank = no Details link

    # GitHub App (Phase 4b). Preferred over a PAT: install the App on the org and
    # every PR fires the webhook - no per-repo workflow files. app_id + a private
    # key enable App mode (per-installation tokens); otherwise github_token (PAT)
    # is used, and if neither is set GitHub integration is disabled (graceful).
    github_app_id: str = ""
    github_app_private_key: str = ""       # PEM contents (raw, \n-escaped, or base64)
    github_app_private_key_path: str = ""  # path to a mounted .pem (preferred on CAI)

    # Operator-side allowlist - the "whitelist an enterprise/org" control knob.
    # The org webhook (or App) may be wired broadly, but the app only acts on
    # events whose org or enterprise appears here. Comma-separated, case-
    # insensitive. Both blank = act on every delivered event (open; the default).
    # Enterprise match uses the payload's ``enterprise.slug`` (present only for
    # repos owned by orgs under an enterprise account); org match uses the org /
    # repo-owner login. An event passes if it matches EITHER list.
    github_allowed_orgs: str = ""
    github_allowed_enterprises: str = ""

    # Repos offered in the dashboard's "Review a PR" picker (comma-separated
    # owner/name). Blank = the picker accepts a typed repo.
    github_review_repos: str = ""

    @property
    def review_repos(self) -> list[str]:
        return [r.strip() for r in self.github_review_repos.split(",") if r.strip()]

    @property
    def allowed_orgs(self) -> set[str]:
        return {s.strip().lower() for s in self.github_allowed_orgs.split(",") if s.strip()}

    @property
    def allowed_enterprises(self) -> set[str]:
        return {s.strip().lower() for s in self.github_allowed_enterprises.split(",") if s.strip()}

    @property
    def github_allowlist_active(self) -> bool:
        """True once any org/enterprise is listed; otherwise the app is open."""
        return bool(self.allowed_orgs or self.allowed_enterprises)

    def resolved_port(self) -> int:
        """CML injects the app port via CDSW_APP_PORT; honor it when present."""
        return int(os.environ.get("CDSW_APP_PORT", self.port))


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
