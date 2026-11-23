# Organization webhook + bot token (primary integration)

The primary way to connect repos to Agentic QA. Add **one webhook at the
organization level** and a **bot token** with repo access — every PR in every
repo of that org (current and future) then fires a webhook to the app, and the
app reports back with the token. **The gated repos need zero files.**

Why the org level, and not the enterprise level: GitHub's enterprise "global
webhooks" deliver only *management* events (`organization`, `repository`,
`member`, …) — **never `pull_request` or `push`**. The **organization** webhook
is the unit that carries PR and comment events for all of an org's repos. An
enterprise with several orgs repeats this per org (scriptable via the API).

For the per-installation-token, no-long-lived-PAT alternative, see the
[GitHub App path](10-github-app-integration.md). For private/VPC deploys that
can't receive an inbound webhook, see the
[per-repo Actions fallback](../github-integration/README.md).

## How it flows

```
PR opened / pushed ─▶ GitHub ──(pull_request webhook, HMAC-signed)──▶ app
                                                                       │
                    app fetches diff (compare API) ◀─────(bot token)───┤
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

## 1. Create a bot identity + token

Use a **machine user** (a dedicated GitHub account added to the org) or a
fine-grained/classic **PAT** with, across the org's repos:

| Scope | Why |
|---|---|
| Contents: **Read** | clone the PR head, read the compare diff |
| Commit statuses: **Read & write** | the `agentic-qa/review` status |
| Pull requests / Issues: **Read & write** | post PR comments |

Set it as `AGENTICQA_GITHUB_TOKEN`. (This token is broad and long-lived — that's
the trade-off vs. the App's scoped, auto-expiring per-installation tokens.)

## 2. Add the organization webhook

Org → *Settings → Webhooks → Add webhook*.

| Field | Value |
|---|---|
| **Payload URL** | `https://<app-public-url>/api/v1/github/webhook` |
| **Content type** | `application/json` |
| **Secret** | a strong random string → `AGENTICQA_GITHUB_WEBHOOK_SECRET` |
| **Events** | *Let me select individual events* → **Pull requests** + **Issue comments** |

One webhook covers all current and future repos in the org.

## 3. Configure the app

Set on the deployed app (CML/CAI project env vars, or `.env` locally):

```bash
AGENTICQA_GITHUB_TOKEN=<bot token from step 1>
AGENTICQA_GITHUB_WEBHOOK_SECRET=<same secret you set on the webhook>
AGENTICQA_PUBLIC_BASE_URL=https://<app-public-url>   # makes the check "Details" link work
# AGENTICQA_GITHUB_API_URL=https://api.github.com    # GHES: https://ghe.example.com/api/v3
```

On boot the app logs `GitHub integration enabled (mode=pat, …)`. With no token
(and no App key) it runs with GitHub reporting disabled — statuses/comments are
logged and skipped, the pipeline still runs.

## 4. Scope which orgs/enterprises the app acts on (allowlist)

Even a broadly-wired webhook only acts on the orgs/enterprises you opt in. This
is the operator-side control knob — the app filters inbound events by the
payload's org (`organization.login` / repo owner) or enterprise
(`enterprise.slug`, present for repos under an enterprise account):

```bash
AGENTICQA_GITHUB_ALLOWED_ORGS=acme,acme-labs        # comma-separated, case-insensitive
AGENTICQA_GITHUB_ALLOWED_ENTERPRISES=acme-inc       # matches enterprise-account repos
```

An event passes if its **org OR its enterprise** is listed. **Both blank = act on
every delivered event** (the default). Out-of-scope events are ignored with a
`200 {"ignored":"not_allowlisted"}` — GitHub sees a healthy webhook, the app does
nothing. This is how you enable/disable an enterprise centrally without touching
GitHub.

## 5. Make the check required (optional but recommended)

To block merges until Agentic QA passes: target repo → *Settings → Branches →
branch protection rule* for the base branch → **Require status checks to pass** →
add **`agentic-qa/review`**. Until the run resolves (and any human approval lands)
the merge button stays disabled.

## Enterprise-wide rollout

There is no single enterprise-level grant for PR events. To cover every org in an
enterprise, repeat steps 1–2 per org — scriptable via the API (create the webhook
on each org, grant the bot access). The allowlist in step 4 then lets you gate
which of those orgs the app actually acts on from one config value.

## Testing without a public URL

- **Outbound** (token → real status/comment/compare) needs only the bot token and
  is fully testable locally.
- **Inbound** (GitHub actually delivering the webhook) is the only piece needing
  the public URL; once deployed, use the webhook's *Recent Deliveries → Redeliver*
  to replay events.
- `tests/test_github_allowlist.py` exercises the allowlist + org-webhook (no-
  installation) path in-process; `tests/test_github_webhook.py` and
  `tests/test_github_app_auth.py` cover the rest of the webhook surface — all
  with fakes, no network, no keys. Run the whole suite with `pytest -q`.
