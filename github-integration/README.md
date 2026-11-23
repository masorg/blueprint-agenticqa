# Connecting repositories to Agentic QA

Three ways to wire repos to the running app, in order of preference. The first
two are **org-wide with zero per-repo files**; the third is a per-repo fallback.

## ① Organization webhook + bot token (primary)

Add **one webhook at the org level** (Pull requests + Issue comments) and a
**bot token** with repo read+write. Every PR in every repo — current and future
— fires a central webhook; the app fetches the diff itself and reports back with
the token. **The gated repos need zero files.** Full setup:
[`docs/11-org-webhook-integration.md`](../docs/11-org-webhook-integration.md).

Requires: the app reachable **inbound** from GitHub (public HTTPS for github.com,
or same-network for GHES) + org-admin to add the webhook and grant the bot.

## ② GitHub App

Same inbound contract, but auth is a scoped, auto-expiring **per-installation
token** instead of a long-lived PAT — the strongest security posture. Install one
App on the org; **the gated repos need zero files.** Full setup:
[`docs/10-github-app-integration.md`](../docs/10-github-app-integration.md).

## ③ Per-repo Actions (fallback in `workflows/`)

Use these only when a GitHub App / inbound webhook isn't practical — e.g. a
private/VPC deploy on github.com (pair them with **self-hosted runners** that can
reach the app), or a repo where you can't install an org App. Copy both files
into the target repo's `.github/workflows/`:

- **`agentic-qa.yml`** — on `pull_request`, computes the diff and POSTs a trigger
  to `$AGENTICQA_URL/api/v1/runs` with `X-API-Key`. (The org-webhook / App paths
  replace this with the `pull_request` webhook.)
- **`human-approval.yml`** — relays `/approve` · `/reject` PR comments to
  `$AGENTICQA_URL/api/v1/github/webhook`, HMAC-signed. (The org-webhook / App
  paths replace this with the `issue_comment` webhook.)

Repo secrets they expect: `AGENTICQA_URL`, `AGENTICQA_API_KEY`,
`AGENTICQA_WEBHOOK_SECRET`.

## Making the gate actually block a merge (any path)

The app posts the `agentic-qa/review` **commit status**. To make it *required*,
add it as a **required status check** in the target repo's branch-protection rule
for the base branch. Until it resolves (and any human approval lands), the merge
button stays disabled.
