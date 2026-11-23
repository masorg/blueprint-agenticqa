# Lessons from a real CAI/CML deployment

Agentic QA has run as an actual Cloudera CAI Application, gated a real GitHub
pull request end-to-end (webhook → run → commit status → `/approve` comment
→ status flip, read back from the live GitHub API), and been deployed and
redeployed across more than one session. This document collects the
non-obvious failures encountered along the way — the kind of detail an
industry-track paper's "lessons from production" section wants, and that a
platform-engineering audience recognizes immediately because they've hit
adjacent versions of the same class of bug.

Each entry: what broke, why it was non-obvious, how it was found, and the fix.

## 1. Per-run pip install silently failing on CAI (`PIP_USER=0`)

**What broke:** every PR run failed during workspace setup — `pip install`
inside the per-run virtualenv errored out.

**Why it was non-obvious:** the executor (`execution/subprocess_venv.py`)
builds a scrubbed environment for each run's isolated venv + subprocess, and
this worked perfectly in local development. It only failed on CAI.

**Root cause:** Cloudera's ML Runtimes set `PIP_USER=1` at the container
level (a reasonable default for a shared runtime where users install into
their own site). `pip install` inside a **virtualenv** does not accept
`--user` semantics — the two are mutually exclusive — so every per-run
install failed, but only on infrastructure that sets this variable, which is
invisible unless you go looking for it.

**Fix:** the executor's scrubbed environment now explicitly forces
`PIP_USER=0` and `PYTHONNOUSERSITE=1` for every per-run venv (`execution/limits.py`).

**Generalizable lesson:** a sandboxed subprocess environment should be
built by an explicit allow-list of what it inherits, not by trusting "it
worked locally" — a managed runtime's own environment defaults are exactly
the kind of ambient state that differs silently between dev and prod.

## 2. GitHub webhooks silently swallowed by CAI's default SAML gate

**What broke:** the app was deployed at a public CAI URL, the org webhook
was configured, everything *looked* wired up — and yet no commit status or
comment ever appeared on a real PR. Confusingly, commit statuses from an
**earlier, local simulation** of the same webhook were still visible on
GitHub, which made it look like the deployed app was working when it was not.

**Why it was non-obvious:** CAI gates Applications behind SAML
authentication by default. An unauthenticated request (which is exactly
what a GitHub webhook delivery is — GitHub has no CAI session) gets a
`302` redirect to a login page, which most HTTP clients — including
GitHub's webhook delivery — treat as a `200`-with-HTML rather than a clear
failure. There is no error in the app's own logs, because the request never
reaches the app at all.

**Root cause:** two settings, both required, both off by default: the CAI
workspace admin must enable "Allow applications to be configured with
unauthenticated access," *and* the specific Application must have "Enable
Unauthenticated Access" turned on per-app.

**Fix:** both toggles enabled; re-verified via a real numbered PR (`#1`)
with signature-verified webhook delivery read back from the GitHub API.

**Generalizable lesson:** for any inbound-webhook integration on a platform
with an auth-by-default proxy layer, treat "the webhook delivery log on the
sending side shows success" and "my app actually received and processed the
request" as two separate claims that must each be independently verified —
a redirect can satisfy the first while failing the second silently.

## 3. A rendering bug that was actually a case-sensitivity routing bug

**What broke:** the live testing-pyramid visualization showed only 2 of 5
tiers on the dashboard, even though the underlying run data had all 5.

**Why it was non-obvious:** the natural hypothesis — a caching issue, or a
logic bug in which layers get computed — was wrong. The DOM, when inspected
directly, showed all 5 tiers, 5 labels, and 5 gradients present. The
rendering code was correct. The bug was one attribute.

**Root cause:** `<svg :viewBox="pyramid.viewBox">` — Alpine.js's `:attr`
binding writes through the browser's HTML attribute API, which lowercases
attribute names per the HTML parsing spec. SVG's `viewBox` is
**case-sensitive**; the browser silently set `viewbox` (no-op) instead. With
no `viewBox` applied, the SVG's implicit coordinate system rendered only the
portion of the (unscaled) content that happened to fit the visible area —
which looked exactly like "only the top of the pyramid is showing."

**Fix:** hardcode `viewBox="0 0 520 348"` directly in the markup (the value
is a constant; nothing was gained by binding it), bypassing Alpine's
attribute binding for this one case. Diagnosed by installing Playwright and
reading `getAttribute('viewBox')` on the live-rendered DOM — it returned
`null`, which was the moment the actual bug became obvious.

**Generalizable lesson:** a reactive framework's attribute-binding
convenience (`:attr=`) generally assumes HTML attributes, which are
case-insensitive; anything binding into inline SVG (`viewBox`,
`preserveAspectRatio`, `gradientUnits`, and similar camelCase SVG attributes)
should be set imperatively (`setAttribute`) or hardcoded, never bound with
the framework's HTML-attribute shorthand. When a rendering bug resists a
rendering-logic explanation, look one layer down, at how the attribute
actually reached the DOM.

## 4. Enterprise-level webhooks cannot deliver pull-request events at all

**What broke:** nothing shipped broken here — this was caught during design,
before any code was written for it, by reading GitHub's own webhook event
documentation closely rather than assuming "an enterprise webhook is just a
broader org webhook."

**The non-obvious fact:** GitHub Enterprise-level ("global") webhooks carry
only *management* events — organization, user, repository, team, member,
membership, fork, ping — and explicitly **not** `pull_request` or `push`.
There is no way to receive PR-triggering events at the enterprise level; the
organization is GitHub's actual unit for PR-event delivery.

**Decision this drove:** the primary inbound integration is an
**organization-level webhook** paired with a bot/PAT token, with a GitHub
App (per-installation tokens) documented as the alternative for
organizations that specifically want scoped, revocable per-repo grants
without a shared token. An operator-side allowlist (org/enterprise) sits on
top of whichever grant is used, since GitHub's security model has no
concept of "whitelist after the fact" — the org webhook or App install is
the one irreducible grant, and the allowlist is a filter on top of it, not a
substitute for it.

**Generalizable lesson:** when integrating with a platform's webhook system
across multiple scope levels (repo / org / enterprise), verify which events
each scope actually delivers before designing around an assumed superset —
"broader" is not always "delivers more event types."

## 5. Idle managed infrastructure goes away quietly

**What broke:** after roughly three weeks with no activity, the CAI
Application's public URL stopped resolving entirely (DNS failure, not an
HTTP error) — while everything else about the deployment (the GitHub org,
the demo repository, open PRs) remained untouched and reachable.

**Why it matters for a paper or a customer demo:** a "fully validated
end-to-end" claim has an implicit timestamp. Managed platform infrastructure
that auto-suspends idle applications means that claim needs periodic
re-verification, not one-time proof — and the failure mode (DNS resolution
failure) is different enough from an application-level error that it's easy
to misdiagnose as a networking or DNS problem on the *checking* side rather
than the deployment itself.

**Generalizable lesson:** any "live demo" or production claim built on
managed, potentially-auto-suspended infrastructure should be re-verified
immediately before it is relied on (a demo, a paper submission, a customer
call), not assumed durable from a prior session — and the re-verification
step itself (a plain reachability check) is cheap enough that it should be
routine, not exceptional.
