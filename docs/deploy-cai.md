# Deploy Agentic QA as a Cloudera AI (CAI) Application — real, end-to-end

Goal: the app runs as a **Cloudera AI Application** at a public HTTPS URL, a **GitHub
org webhook** delivers real PR events to it, it runs the pyramid and posts the
`agentic-qa/review` commit status back to the PR, and an **org/enterprise allowlist**
gates which repos it acts on. No mockups.

```
 your laptop                GitHub (org)                 Cloudera AI
 ┌──────────┐  git push   ┌──────────────┐            ┌─────────────────────────┐
 │ code repo│ ──────────▶ │ agentic-qa   │ ─clone────▶│ CAI Project             │
 └──────────┘             │ (this app)   │  (project   │  └─ Application          │
                          └──────────────┘   from git) │     cml/app_startup.sh   │
                                                        │     https://agentic-qa… │
   gated repo PR ─(pull_request webhook)───────────────▶│  ← org webhook           │
        ▲   status: agentic-qa/review  ◀────(bot token)─┤   runs pyramid, triages  │
        └── reviewer comments /approve ──(issue_comment)▶│   flips status → success │
                                                        └─────────────────────────┘
```

**Two repos, don't confuse them:** ① *this app's* repo (`agentic-qa`) — pushed to git,
cloned into the CAI project. ② the *gated* repos — the ones with PRs you want reviewed;
they need **zero files**, just the org webhook.

---

## Prerequisites (you provide — I can't do these from here)

- A **CAI workspace** with rights to create a Project and an Application, and an **admin**
  who can toggle one workspace security setting (Part 3).
- A **GitHub org** you administer (to add an org webhook + a bot user/token), and at least
  one repo in it to open a test PR against.
- A **git remote** for this app's code (e.g. `github.com/<you>/agentic-qa`).
- **Outbound network** from the CAI workspace to `github.com` + `api.github.com` (the app
  clones PR repos and calls the GitHub API). Confirm with your CAI admin.
- *(Optional)* an OpenAI key or a **Cloudera AI Inference** endpoint for model-written
  triage. Without it the app uses deterministic heuristics.

---

## Part 1 — Push this app to git

From the project root, commit and push to the app repo (NOT a gated repo):

```bash
git add -A && git commit -m "Agentic QA app"
git remote add origin https://github.com/<you>/agentic-qa.git   # if not set
git push -u origin main
```

**Do not commit `.env`** (it holds keys). All config goes in CAI env vars (Part 6).

---

## Part 2 — Create the CAI Project from git

CAI workspace → **Projects → New Project**:
- **Initial setup: Git** → paste the HTTPS (or SSH) URL of the `agentic-qa` repo. The
  project is created as a clone.
  - Private repo over HTTPS: supply a token when prompted. Over SSH: add your CAI SSH key
    to GitHub first (Project/User Settings → SSH keys).
- **Runtime:** choose an **ML Runtime with Python 3.11** (the app requires ≥3.11 — do not
  pick 3.9/3.10; 3.13 is not needed and usually not offered).

---

## Part 3 — Enable unauthenticated app access (admin, one-time)

CAI Applications sit behind CAI login by default — which would **block GitHub's webhook**
(GitHub can't log in). An admin enables the opt-in:

- **Admin → Security →** check **"Allow applications to be configured with unauthenticated access."**

This only *permits* it; you still enable it per-application (Part 4). Our endpoint stays
protected by **HMAC signature verification** and a **GitHub write-access check** on every
approval — the open port is not an open door.

---

## Part 4 — Create the Application

Project → **Applications → New Application**:

| Field | Value |
|---|---|
| **Name** | `Agentic QA` |
| **Subdomain** | `agentic-qa` → URL becomes `https://agentic-qa.<workspace-domain>` |
| **Script** | `app.py` |
| **Runtime / Kernel** | Python **3.11+** ML Runtime (3.12 is fine) |
| **Resource profile** | ≥ **2 vCPU / 4 GB** (it builds venvs and runs pytest) |
| **Enable Unauthenticated Access** | ✅ **checked** |
| **Environment variables** | set them here (Part 6) |

**Use `app.py`, not the `.sh`** — CAI runs the Application's Script with the ML
Runtime's *Python* interpreter, so the entry point must be a Python file (a shell
script won't run, and the picker only lists `.py`). `app.py` installs deps
(idempotent; set `AGENTICQA_SKIP_PIP=1` to skip on restart) and serves the app on
`CDSW_APP_PORT`, single-worker — a hard invariant (the event bus/engine/store are
in-process singletons). (`cml/app_startup.sh` remains for plain-shell/non-CAI use.)

Click **Create**, wait for **Running**, and note the URL.

---

## Part 5 — Wire GitHub (org webhook + bot token) — the primary path

**5a. Bot identity + token.** Create a **machine-user** account (or a fine-grained PAT)
with these permissions on the org's repos:

| Permission | Access | Why |
|---|---|---|
| Contents | Read | clone PR head, read the compare diff |
| Commit statuses | Read & write | the `agentic-qa/review` status |
| Pull requests / Issues | Read & write | post PR comments |

That token → `AGENTICQA_GITHUB_TOKEN`.

**5b. Organization webhook.** GitHub **org → Settings → Webhooks → Add webhook**:
- **Payload URL:** `https://agentic-qa.<workspace-domain>/api/v1/github/webhook`
- **Content type:** `application/json`
- **Secret:** a strong random string → `AGENTICQA_GITHUB_WEBHOOK_SECRET`
- **Events:** *Let me select individual events* → **Pull requests** + **Issue comments**

One org webhook covers **all repos in the org, current and future**. (Alternative:
a GitHub App — see [`docs/10-github-app-integration.md`](10-github-app-integration.md).)

> Enterprise with many orgs: repeat 5b per org (GitHub's *enterprise* "global webhooks"
> do **not** carry `pull_request`). Then gate them centrally with the allowlist below.

---

## Part 6 — Configure the Application env vars (in CAI)

Application → **Settings → Set Environment Variables** (then **Restart**):

```bash
# GitHub wiring
AGENTICQA_GITHUB_TOKEN=<bot token from 5a>
AGENTICQA_GITHUB_WEBHOOK_SECRET=<same secret as the webhook>
AGENTICQA_PUBLIC_BASE_URL=https://agentic-qa.<workspace-domain>   # makes the status "Details" link work

# The whitelist — the app only acts on these orgs/enterprises (blank = act on all)
AGENTICQA_GITHUB_ALLOWED_ORGS=<your-org>
# AGENTICQA_GITHUB_ALLOWED_ENTERPRISES=<enterprise-slug>          # for enterprise-account repos

# Real deployment: no fake dashboard data
AGENTICQA_SEED_SAMPLES=0

# Optional — model-written triage (else deterministic heuristics)
AGENTICQA_LLM_PROVIDER=openai
AGENTICQA_OPENAI_API_KEY=<key>
# or Cloudera AI Inference (OpenAI-compatible):
# AGENTICQA_LLM_PROVIDER=cloudera
# AGENTICQA_CLOUDERA_AI_ENDPOINT=https://<inference-endpoint>/v1
# AGENTICQA_CLOUDERA_AI_API_KEY=<key>
# AGENTICQA_CLOUDERA_MODEL=<model-name>
```

On boot the app logs `GitHub integration enabled (mode=pat, …)`. If token/secret are
missing it logs `disabled` and still runs the pipeline (statuses just get skipped).

---

## Part 7 — Prove it end-to-end

1. In a repo owned by the **whitelisted org**, open (or reopen) a **pull request**.
2. GitHub fires `pull_request` → the app fetches the diff, runs the pyramid, and posts the
   **`agentic-qa/review`** commit status (pending → success / failure).
3. Open `https://agentic-qa.<workspace-domain>/` and watch the run stream in the pyramid.
4. On the PR, a user with **write access** comments **`/approve`** → status flips to
   success. (`/reject` is symmetric; a read-only user's `/approve` is refused.)
5. **Allowlist check:** open a PR in an org **not** on the allowlist → the app returns
   `200 {"ignored":"not_allowlisted"}` and does nothing.
6. **Make it required (optional):** gated repo → Settings → Branches → require the
   `agentic-qa/review` status check, so the merge button waits for the gate.

**Replays:** GitHub org webhook → **Recent Deliveries → Redeliver** re-sends any event
(handy while debugging without opening new PRs).

---

## Part 8 — Updating the deployed app later

```bash
# locally
git commit -am "change"; git push
```
Then in CAI: open a **Session → Terminal** in the project, run `git pull`, and on the
**Applications** page click **Restart**. (CAI has no Git UI — use terminal git.)

---

## Gotchas to check on first deploy

- **Runtime = Python 3.11.** Not 3.9/3.10 (typing/`fromisoformat` needs ≥3.11); 3.13 not required.
- **SSE through the CAI proxy.** The live-filling pyramid streams via SSE; the app already
  sends `Cache-Control: no-cache` + `X-Accel-Buffering: no`. If the proxy still buffers,
  the run **still completes** and the dashboard catches up on refresh — but verify the live
  stream once; tell me if it buffers and I'll adjust headers/keepalive.
- **Outbound to GitHub.** Cloning PR repos + API calls need egress to `github.com` /
  `api.github.com`. If blocked, runs fail at checkout — ask your CAI admin to allow it.
- **First run is slow.** `pip install` on boot + building the first per-run venv. Give the
  app a minute after "Running" before the first PR.
- **Single worker only.** `app_startup.sh` already exec's one worker — don't add `--workers`.
- **Never commit `.env`.** Config lives in CAI env vars.

---

## Who does what

- **You (your accounts):** push to git, create the CAI project + application, get admin to
  enable unauthenticated apps, create the bot/PAT, add the org webhook, open the test PR.
- **Me (from here):** everything code-side is ready (`app_startup.sh`, webhook handler,
  allowlist, token auth, this guide). I **can verify the outbound half before you deploy** —
  give me the bot token + a throwaway test repo and I'll post a real `agentic-qa/review`
  commit status to a PR from here, proving the token/permissions work. I **cannot** create
  the CAI app, flip the admin setting, add the webhook, or open PRs for you.
