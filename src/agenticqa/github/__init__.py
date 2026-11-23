"""GitHub integration (Phase 4).

The app - not the triggering Action - owns the ``agentic-qa/review`` commit
status, so a multi-minute run never fails a short-lived Action. ``GitHubClient``
wraps the REST calls; ``GitHubService`` maps a run's lifecycle onto commit
statuses and PR comments and verifies inbound approval webhooks. Every network
call degrades gracefully: with no token configured the calls are logged and
skipped, exactly like the LLM touch-points, so the pipeline never fails on a
GitHub hiccup.
"""

from .app_auth import GitHubAppAuth
from .client import GitHubClient
from .service import GitHubService, verify_signature

__all__ = ["GitHubAppAuth", "GitHubClient", "GitHubService", "verify_signature"]
