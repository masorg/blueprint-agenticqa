# [Working title] Agentic QA: A Deterministic-Guardrail Architecture for LLM-Assisted Pull Request Review

*Status: first structural draft, 2026-09-23. Sections marked **[TODO]** are
gaps, not filler — see the end-of-document punch list. Everything else is
either already true of the deployed system or a direct summary of a measured
result in `docs/13-evaluation-harness.md`. Venue: not yet selected — written
to fit an industry-track format (ICSE-SEIP / FSE Industry Track shape: short
paper, systems + evaluation, practitioner-relevant).*

## Abstract

**[TODO: write last, ~150-200 words]** Draft shape: Problem (LLM-assisted PR
review is being adopted faster than it is being governed; empirical work
shows it speeds up merges without clearly improving quality, and shows
review agents can be manipulated). Approach (a five-layer test pyramid used
as a router — which layers apply is proposed by an LLM but bounded by a
deterministic guardrail — gated by a human-approval step wired natively into
the CI surface). Deployment (a real Cloudera CAI Application, validated
end-to-end against a live GitHub PR). Evaluation (a four-scenario pilot
benchmark with structurally distinct bug classes; verdict accuracy,
guardrail-override and coverage-safety-net measurements). Contribution
statement (an architecture and a deployed instance, not a new test-generation
technique).

## 1. Introduction

Pull-request review is where a change either ships or gets a second look.
As LLMs are folded into that step — writing tests, reviewing diffs,
summarizing risk — two empirical findings should worry anyone deploying this
in practice. First, across more than a million real pull requests, AI-agent
involvement in review has been shown to speed up merge decisions without
clearly improving review quality [9]. Second, LLM-based review agents can be
socially engineered into approving vulnerable code through persuasively
written PR narratives [11]. Neither finding argues against using LLMs in the
review path; both argue against trusting an LLM's own verdict as the review
path's final authority.

This paper describes Agentic QA, a system that takes that argument
literally: LLMs are used for exactly two things — deciding *which* layers of
a five-layer test pyramid apply to a given change, and writing a
human-readable narrative of what happened — while a deterministic,
non-learned guardrail computes the actual pass/fail verdict and a
human-approval step, wired into GitHub's own commit-status and comment
surface, is the only thing that can clear a flagged change. The system is
not a research prototype behind a paywall of assumptions: it runs as a
Cloudera CAI Application, has gated a real, numbered GitHub pull request
end-to-end, and its production failure modes are reported alongside its
architecture (§5).

**Contributions:**

1. An architecture that combines a layered test taxonomy used as an
   LLM-proposed, guardrail-bounded *router*, with a human-approval gate
   native to the version-control system's own surface — a combination not
   found in the surveyed literature (§2).
2. A small, structurally-motivated benchmark corpus and evaluation harness
   that measures routing/classification agreement, verdict correctness, a
   *coverage safety net* (whether a classification quirk can silently drop a
   test from execution), and guardrail-override frequency — not just pass/
   fail accuracy (§4).
3. A production deployment case study, including reported failure modes with
   root causes and generalizable lessons for teams deploying similar systems
   on managed platforms (§5).

## 2. Related Work

*(Full section: `docs/12-related-work.md`. Condensed here; expand back out
at submission time.)*

**Agentic test generation** (DPIAgent [1], ReProAgent [2], TestAgent [3],
GSE [4], Change2Task [5]) optimizes how well an agent writes a test,
typically against SWE-bench/SWT-Bench-style benchmarks. Agentic QA treats
test generation as necessary but insufficient, and does not compete on this
axis.

**Test quality as a distinct problem**: VibeCheck [6] and Jhanglani et al.
[7] both find LLM-generated tests are often shallow or flaky even when they
run; Tanaka et al. [8] show combining property-based and example-based tests
raises bug detection substantially over either alone on HumanEval. This
motivates a layered architecture directly, and is the closest prior result
to what §4 measures on Agentic QA's own corpus.

**LLM-based code review**: Zhong et al. [9] (>1M PRs: faster, not clearly
better), Kamalı et al. [10] (a vision for "supervisory operator" agentic
review), SEVRA-BENCH [11] (review agents can be socially engineered), Kumar
et al. [12] (synthetic review benchmarks overestimate real capability), and
Gao et al. [13] (most AI-co-authored PRs merge with minimal human review in
the wild). No surveyed work combines a deterministic guardrail on LLM
decision points, a human-approval gate native to the CI surface, and a
reported production deployment — the combination this paper presents.

**Property-based-testing-with-LLMs** (ChekProp [14]) and **trajectory
evaluation** (DynSTEER [15], [16]) are adjacent techniques, not
PR-gating architectures; relevant to future refinement of individual layers,
not to the architecture itself.

## 3. System Architecture

Agentic QA is a single-process FastAPI application (no message queue, no
separate worker fleet) that runs as a Cloudera CML/CAI Analytical
Application. Three properties shape every design decision below: it must
run any repository's existing test suite unmodified, it must degrade
gracefully when any LLM call fails or is disabled, and its final verdict
must never depend on an LLM call succeeding.

### 3.1 The test pyramid as a router, not a checklist

Five layers, ordered base-to-apex: **unit**, **property-based**,
**trajectory**, **LLM-as-judge**, **adversarial/safety**. The middle two are
*agentic-only*: they apply when the system under test is itself an
agentic/LLM system (does it call tools in the right order; is its output
grounded and on-policy), and stay inactive for a plain service. Which layers
apply, and which discovered test belongs to which layer, are *routing*
decisions, not fixed configuration — a router proposes a routing decision
per run (repository kind, per-layer applicability), and a classifier assigns
each discovered test to exactly one layer.

### 3.2 Two LLM touch-points, one deterministic guardrail, one deterministic gate

The router and classifier are implemented twice behind the same interface: a
deterministic heuristic (cue-based: filenames, markers, diff keywords) and an
LLM-backed implementation that falls back to the heuristic on any model
error. Two invariants are enforced in code, independent of what either
implementation returns:

- **Routing guardrail**: an agentic-only layer can never be marked
  applicable for a repository routed as a plain service, regardless of what
  the LLM's routing response claims. (`orchestrator/llm_touchpoints.py::_to_decision`)
- **Coverage safety net**: a test classified into a layer the run does not
  route to is never silently dropped — it is demoted to its heuristic layer,
  or to the always-applicable base layer, and still executed.
  (`orchestrator/pipeline.py::_bucket_with_fallback`; see §4.3 for why this
  invariant was added after the evaluation harness found it violated.)

The **verdict** — auto-approve or needs-human-review — is computed by a
third, entirely separate rule: if any applicable layer failed or errored,
the run needs human review; otherwise it auto-approves. An LLM writes the
triage narrative describing *why*, but never computes *whether*. This
separation is the system's central design commitment, and is why a model
outage degrades the system to its heuristic baseline rather than blocking
or silently mis-deciding.

### 3.3 Human approval, wired into the CI surface

A flagged run is not resolved in a side dashboard that a reviewer must
remember to visit. The app owns a GitHub commit status
(`agentic-qa/review`) directly on the PR's head commit, and a reviewer
resolves it with a plain `/approve` or `/reject` comment on the PR itself.
The comment's *text* is never trusted for authorization: the commenter's
repository permission is checked against the live GitHub API on every
resolution attempt, and only `admin`/`maintain`/`write` roles can resolve a
run. This directly targets the SEVRA-BENCH threat model [11] — a persuasive
comment body cannot approve anything; only a genuine write-access grant can.

### 3.4 Inbound integration: why an organization webhook, not an enterprise one

GitHub enterprise-level ("global") webhooks deliver only management events
(organization, repository, membership, …) — never `pull_request` or `push`.
The organization is GitHub's actual unit of PR-event delivery. Agentic QA's
primary inbound path is therefore an **organization-level webhook** paired
with a bot token (a GitHub App with per-installation tokens is supported as
an alternative for organizations wanting scoped, revocable grants). An
operator-side allowlist (by org or enterprise) sits on top of whichever
grant is used — GitHub's own permission model has no notion of
"whitelist after granting access," so the allowlist is a filter on an
already-irreducible grant, not a substitute for one.

### 3.5 Isolation and execution

Each run checks out the target commit, builds a fresh virtual environment
(cached by dependency hash to avoid repeated installs), and runs the
repository's own test framework (pytest, discovered dynamically) inside a
resource-limited subprocess with a scrubbed environment. No part of the
target repository's code runs in the application's own process.

## 4. Evaluation

Full detail, raw reports, and methodology in `docs/13-evaluation-harness.md`;
summarized here.

### 4.1 Setup

Two corpus tiers (`src/agenticqa/eval/corpus.py`), six scenarios total.

**Local tier (N=4)**, against one demo service (an Inventory & Orders REST
API), each scenario a *structurally* distinct bug class — not merely a
different injected value — chosen so that which layer catches it is a
property of the bug's nature, not of which specific numbers a test happened
to use:

| Scenario | Bug class | Catchable by (structurally) |
|---|---|---|
| clean-baseline | none | — |
| stock-check-off-by-one | business-rule boundary error | unit + property |
| stale-stock-bound | stale/cached bound vs. live state | property only (needs a *sequence* of orders; every unit test issues exactly one) |
| name-length-bound-removed | HTTP-boundary validation regression | adversarial only (unit/property call domain functions directly, bypassing HTTP validation) |

**Real-PR tier (N=2)**, against real commits on the live GitHub demo target
(`AgenticQAOrg/AgenticProduct`), via the exact `source="github"` checkout
path production uses — no deployed app, no webhook, just a `git clone` at a
pinned SHA and each PR's real diff fetched from its public `.diff` URL:

| Scenario | What it is | Expected |
|---|---|---|
| agenticproduct-pr1-regression | real PR #1, the same off-by-one as above, as an actual commit with no routing hint file | needs review, unit+property fail |
| agenticproduct-pr5-restock-agent | real PR #5, a tool-calling restock agent, 33 tests across all 5 layers | auto-approve, `repo_kind=agentic` |

The second tier's agentic scenario is the corpus's only case where the
agentic-only layers *should* activate — it tests the router's "turn it on"
path, where every other scenario tests the guardrail's "turn it off" path.

The evaluation harness (`src/agenticqa/eval/harness.py`) drives the
*production* `Pipeline` directly (not a re-implementation), wrapping the
router/classifier in a non-invasive recording shim, so reported numbers
cannot drift from what the deployed system actually does.

### 4.2 Results

Two conditions: `stub` (heuristic-only, the no-LLM ablation, free) and
`configured` (OpenAI gpt-4o, real calls), full N=6 corpus for the headline
row.

| Metric | stub (N=6) | gpt-4o (N=6) |
|---|---|---|
| Verdict accuracy | 6/6 (100%) | 6/6 (100%) |
| Bug/agentic-status caught correctly, every scenario | yes | yes |
| Routing agreement vs. heuristic | 100% | 100% |
| Repo-kind accuracy (plain_service vs. agentic, vs. label) | 100% | 100% |
| Classification agreement vs. heuristic | 100% | 95% |
| All discovered tests executed (coverage safety net) | yes | yes |
| Tests demoted to a fallback layer | 0 | 5 (1/scenario, 5 of 6) |
| Guardrail overrides observed | 0 | 0 |
| Total wall-clock | ~44s | 125.0s |
| Total tokens | 0 | 2,961 |

The stub condition is a sanity check, not a result: the "LLM" is the
heuristic, so 100% agreement is definitional. The gpt-4o condition is the
result: verdict accuracy and repo-kind accuracy both held at 100% — including
correctly identifying the one genuinely agentic scenario amid five
plain-service ones — despite a systematic ~5% classification disagreement
(the model consistently placed one specific test in an agentic-only layer on
a plain-service repository, in 5 of 6 scenarios), *and* every discovered test
still executed regardless — because of the fix in §4.3, not by chance.

### 4.3 A found-and-fixed coverage bug, measured before and after

The first pilot run (an earlier, N=2 version of this corpus) surfaced a real
defect: a test misclassified into a layer the run does not route to was
silently dropped by the pipeline's layer-planning step — classified, but
never executed, with no error and no surfaced warning. This was fixed by
demoting any such test to its heuristic layer (or the always-applicable base
layer) instead of dropping it, and the harness was instrumented to report
whether every discovered test actually ran. Re-run under identical
conditions (same model, same misclassification recurring), the after-fix
condition shows `18/18` tests executed in every scenario where the
before-fix condition showed `17/18`. This is offered as a concrete
instance of the paper's central claim: a deterministic, code-level
guarantee — not the model getting better — is what closed the gap, and the
evaluation harness is what made the gap visible in the first place.

### 4.4 Threats to validity

N=6 across two target repos (one demo service, one real GitHub project), one
model (gpt-4o), zero observed guardrail overrides on this corpus (§4.5).
These are acknowledged, not hidden — see §6.

### 4.5 Guardrail overrides: instrumented, still not observed — now on a fairer test

Adding the real agentic scenario (§4.1) made this a fair test, not just a
larger one: the corpus now contains a case that could plausibly confuse a
router into thinking a plain service is agentic, or vice versa. gpt-4o got
repo-kind right on all 6 scenarios regardless, so `guardrail_overridden`
remains 0. This is a *stronger* null result than an earlier N=4 run without
an agentic case at all, but it is still a null result: the guardrail's
necessity remains a structural/design argument (an agentic-only layer cannot
apply to a plain service, by code, regardless of what any LLM call returns),
supported by literature showing review agents can be *manipulated* [11]
rather than by an observed correction on this corpus. Forcing an observation
would need a deliberately adversarial scenario — a plain-service change with
agentic-*sounding* red herrings (a class literally named `Agent`, a comment
mentioning `tool_use`, in code that does nothing agentic) — identified as
follow-up work, not yet built.

## 5. Lessons from Production

Full detail in `docs/14-production-lessons.md`; five findings, condensed:

1. **Managed-runtime environment defaults can silently break sandboxed
   subprocesses.** Cloudera's ML Runtimes set `PIP_USER=1`, which is
   incompatible with `pip install` inside a virtualenv — invisible until a
   per-run venv install fails on that specific platform.
2. **An auth-by-default reverse proxy can silently swallow inbound
   webhooks.** A `302` redirect to a login page looks like delivery success
   to most senders (including GitHub), producing an integration that
   "looks wired up" while never actually receiving a request.
3. **A reactive framework's HTML-attribute binding is unsafe for
   case-sensitive SVG attributes.** `:viewBox` gets silently lowercased to
   `viewbox` by the DOM's attribute API, which a rendering-logic
   investigation will not find — only inspecting the live DOM attribute
   value reveals it.
4. **Webhook scope determines which events are even deliverable.**
   Enterprise-level GitHub webhooks never carry `pull_request` events,
   regardless of configuration; the organization is the smallest scope that
   does, which drove the system's primary integration design (§3.4).
5. **Idle managed infrastructure can disappear without an application-level
   error.** A CAI Application left idle for roughly three weeks stopped
   resolving its own URL (DNS failure) while every other part of the
   deployment remained untouched — a reminder that a "validated end-to-end"
   claim carries an implicit timestamp on managed platforms.

## 6. Limitations and Future Work

- **Benchmark scale.** N=6 across two repos is real progress from a single
  demo service, still a pilot, not a benchmark. `AgenticProduct` has three
  more real PRs (#2–#4) not yet added as labeled scenarios; a second
  real-world target repo would strengthen this further.
- **Guardrail overrides remain unobserved** (§4.5) — the one identified,
  not-yet-built experiment (an adversarial-cue plain-service scenario) is
  the most valuable next addition to this section specifically.
- **Single model.** All LLM-condition numbers are gpt-4o; cross-model/
  cross-provider comparison is unexplored.
- **Guardrail overrides unobserved, not unnecessary** (§4.5) — closing this
  needs either an agentic-repo scenario or a deliberately adversarial
  routing prompt in the corpus.
- **Durability.** The run store is in-memory by design (documented,
  deliberate simplification); a durable backend is scoped but not yet
  built, tracked as a product roadmap item independent of this paper.
- **No practitioner user study.** All evaluation to date is automated;
  practitioner reaction to the triage narrative and the approval-gate UX is
  unmeasured.

## 7. Conclusion

**[TODO: 1 paragraph, write after §4 is finalized with a larger corpus.]**

## References

*(Renumber against `docs/12-related-work.md` at submission time; identical
list, reproduced here for a self-contained draft.)*

1. Liu, H. et al. "DPIAgent." 2026-08.
2. Zhang, Q. et al. "ReProAgent." 2026-07.
3. Zhang, Q. et al. "TestAgent." 2026-07.
4. Yang, C. et al. "Learning Globally Reusable Skills for Coding Agents." 2026-08.
5. Qi, H. et al. "Change2Task." 2026-07.
6. Tabassum, A. et al. "VibeCheck." 2026-09.
7. Jhanglani, P. et al. "Beyond Test Presence." 2026-07.
8. Tanaka, H. et al. "Understanding the Characteristics of LLM-Generated Property-Based Tests." 2025-10.
9. Zhong, S. et al. "From Human-Centric to Agentic Code Review." 2026-07.
10. Kamalı, H. Ö. et al. "Rethinking Code Review in the Age of AI." 2026-05.
11. Melo, R. et al. "SEVRA-BENCH." 2026-06.
12. Kumar, S. P. et al. "Bigger Isn't Always Better." 2026-04.
13. Gao, H. et al. "On Autopilot?" 2026-01.
14. Etemadi, K. et al. "ChekProp." 2025-05.
15. Shi, Z. et al. "DynSTEER." 2026-09.
16. Ayyad, M. et al. "Trajectory-Aware Benchmark Subset Selection." 2026-09.

---

## Punch list to move this from draft to submittable

**Real deadlines found (2026-09-23, via each track's own CFP page — not
guessed):**

| Track | Fit | Submission deadline | Notification |
|---|---|---|---|
| ICSE-SEIP 2027 | systems + evaluation + practice, strong fit | **Fri 23 Oct 2026** (1 month out) | Fri 11 Dec 2026 |
| CAIN 2027 Industry Track (SE for AI) | thematically the tightest fit — AI-in-SE specifically | **Fri 6 Nov 2026** (~6 weeks out) | not yet checked |
| ICSE 2027 Tool Demo / Data Showcase | lower bar, live-tool angle, could run in parallel | Fri 23 Oct 2026 | Fri 11 Dec 2026 |
| MSR 2027 Industry Track | weaker fit (mining-focused community) | Fri 18 Dec 2026 | Mon 11 Jan 2027 |

Both realistic primary targets (SEIP, CAIN) are 4-6 weeks out, not "no
deadline" — this changes §1-5 below from a wishlist into a schedule.

1. **Corpus** (§6) — N=2→6 done this session (structurally distinct local
   bugs + two real `AgenticProduct` PRs, including the one agentic case).
   Remaining, in order of value: the adversarial-cue guardrail-override
   probe (§4.5) is the single most paper-relevant addition left; PRs #2-4
   as additional labeled scenarios is lower-value (same bug class as #1's
   sibling, less new signal).
2. **Pick SEIP vs. CAIN** — a real decision, not mechanical; CAIN fits the
   subject better, SEIP is the more prestigious/established venue. Needs a
   decision, not more research.
3. Write the Abstract and Conclusion last, once §1 lands.
4. Add 1-2 figures (the mechanism diagram already exists in
   `docs/16-onepager.html`, built for a different audience — adapting it for
   print is redrawing, not designing from scratch).
5. Decide on and add an author list / affiliations.
