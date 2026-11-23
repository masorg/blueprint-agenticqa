# GitHub App integration (alternative)

> **The primary integration is the [organization webhook + bot token](11-org-webhook-integration.md).**
> Use the GitHub App instead when you want **scoped, auto-expiring
> per-installation tokens** rather than a long-lived org PAT — the strongest
> security posture. It's the same inbound webhook contract; only the auth and the
> setup differ.

Install one App on the org and **every PR fires a webhook** to the running app —
no per-repo workflow files. The app fetches each PR's diff itself and reports
back with a short-lived per-installation token.

This is the github.com + publicly-reachable-app path. For private/VPC deploys or
where neither an App nor an org webhook can be added, use the per-repo Actions
fallback in [`github-integration/`](../github-integration/README.md) instead.

## How it flows

```
PR opened / pushed ─▶ GitHub ──(pull_request webhook, HMAC-signed)──▶ app
                                                                       │
                    app fetches diff (compare API) ◀───(installation token)
                                                                       │
       app runs the pyramid, posts agentic-qa/review commit status ──▶ PR
                                                                       │
  reviewer comments /approve ─▶ GitHub ─(issue_comment webhook)─▶ app resolves,
                                                     flips status to success ─▶ PR
```

The **app** owns the `agentic-qa/review` commit status, so a multi-minute run is
never bound by a webhook's response window. Approval comments are HMAC-verified
and the commenter's write access is checked against the API — comment text is
never trusted for authorization.

## 1. Register the App

GitHub → *Settings → Developer settings → GitHub Apps → New GitHub App*.

| Field | Value |
|---|---|
| **Webhook URL** | `https://<app-public-url>/api/v1/github/webhook` |
| **Webhook secret** | a strong random string → `AGENTICQA_GITHUB_WEBHOOK_SECRET` |
| **Homepage URL** | the app's public URL (any valid URL) |

**Repository permissions:**

| Permission | Access | Why |
|---|---|---|
| Contents | Read | clone the PR head, read the compare diff |
| Pull requests | Read & write | post PR comments |
| Commit statuses | Read & write | the `agentic-qa/review` status |
| Metadata | Read | mandatory |

**Subscribe to events:** **Pull request**, **Issue comment**.

Create the App, then **generate a private key** (downloads a `.pem`) and note the
**App ID**.

## 2. Install it

*Install App* → choose the org → **All repositories** (org-wide) or select the
repos to gate. Installing is what makes PRs in those repos start firing webhooks.

## 3. Configure the app

Set on the deployed app (CML/CAI project env vars, or `.env` locally):

```bash
AGENTICQA_GITHUB_APP_ID=123456
AGENTICQA_GITHUB_APP_PRIVATE_KEY_PATH=/path/to/agentic-qa.private-key.pem  # preferred
# or inline (\n-escaped or base64), if mounting a file isn't convenient:
# AGENTICQA_GITHUB_APP_PRIVATE_KEY=-----BEGIN...

AGENTICQA_GITHUB_WEBHOOK_SECRET=<same secret you set on the App>
AGENTICQA_PUBLIC_BASE_URL=https://<app-public-url>   # makes the check "Details" link work
# AGENTICQA_GITHUB_API_URL=https://api.github.com    # GHES: https://ghe.example.com/api/v3
```

On boot the app logs `GitHub integration enabled (mode=app, …)`. If the App id or
key is missing it falls back to `mode=pat` (a `AGENTICQA_GITHUB_TOKEN`), and with
neither it runs with GitHub reporting disabled (statuses/comments are logged and
skipped — the pipeline still runs).

## 4. Make the check required (optional but recommended)

To block merges until Agentic QA passes: target repo → *Settings → Branches →
branch protection rule* for the base branch → **Require status checks to pass** →
add **`agentic-qa/review`**. Until the run resolves (and any human approval lands)
the merge button stays disabled.

## Auth internals

- The app signs a short-lived **RS256 JWT** (`iss` = App ID, ≤10-min TTL) with the
  private key, then exchanges it at `POST /app/installations/{id}/access_tokens`
  for a ~1-hour **installation token**, cached per installation and refreshed just
  before expiry. Each webhook payload carries `installation.id`, which the app
  stores on the run so later status/comment calls use the right token.
- All of this degrades gracefully: any auth or API failure is logged and skipped,
  never raised into a run.

## Testing without a public URL

- **Outbound** (JWT → installation token → real status/comment/compare) needs only
  the App ID + private key and a real installation — fully testable locally.
- **Inbound** (GitHub actually delivering the webhook) is the only piece needing
  the public URL; once deployed, use the App's *Advanced → Recent Deliveries →
  Redeliver* to replay events.
- `tests/test_github_app_auth.py` exercises the whole webhook + auth surface
  in-process with fakes (no network, no keys) — run it with `pytest -q`.
