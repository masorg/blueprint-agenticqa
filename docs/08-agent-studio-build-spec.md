# Agent Studio Build Specification — Agentic Testing Pyramid Workflow
### For: a coding agent implementing this inside Cloudera AI Agent Studio
### Status as of handoff: service code and all local tests are written, tested, and passing. Nothing has been built inside Agent Studio yet. Nothing has been deployed. No live round-trip has ever run.

This document is self-contained. It does not assume you have access to any other document, conversation, or context beyond this file and the code files it references.

---

## 0. Mission, in one paragraph

Build a 5-agent workflow in Cloudera AI Agent Studio that, given a code change (PR diff + description) to a small REST API, decides which categories of tests are relevant, runs them, and produces one synthesized report ending in a machine-readable recommendation (`auto_approve` or `needs_human_review`). This workflow will later be called from a GitHub Actions pipeline (already built, see Section 6) so that opening a real pull request triggers it automatically, with a human approval step gating merge when the recommendation says so.

---

## 1. What already exists — do not rebuild these, read and use them as-is

All files below are delivered, tested locally (outside Agent Studio), and confirmed working. Assume they are correct; your job is to make Agent Studio call them correctly, not to rewrite their logic.

| File | Contents | Verified behavior |
|---|---|---|
| `app.py` | The system under test: an in-memory Inventory & Orders REST API (FastAPI). Core functions: `add_item`, `get_item`, `create_order`, `get_order`. | 7/7 unit tests pass against this version |
| `bug_variant.py` | A documented, isolated reference showing one injected bug: the stock check in `create_order` changed from `item["stock"] < quantity` to `item["stock"] <= quantity`. Used for testing/demo purposes only — **not part of the production service**. | Confirmed: swapping this logic into `app.py` causes exactly 1 of 7 unit tests and exactly 1 of 4 property tests to fail, with all other tests (including all 7 adversarial tests) still passing |
| `test_orders.py` | Layer 1 — 7 deterministic unit tests against `app.py`'s core functions. | 7/7 pass clean; 6/7 pass + 1 named failure against the bug variant |
| `test_properties.py` | Layer 2 — 4 property-based tests using Hypothesis. Checks invariants (stock never negative, orders are atomic, non-positive quantities always rejected, any valid order must succeed) across generated inputs, not fixed examples. | 4/4 pass clean; 3/4 pass + 1 named failure (`test_property_any_valid_order_must_succeed`) against the bug variant, with Hypothesis reporting the minimal failing case |
| `test_adversarial.py` | Layer 5 — 7 tests hitting the HTTP layer with malformed/hostile input (negative quantities, huge numbers, SQL-injection-style strings, oversized payloads, type mismatches). | 7/7 pass clean **and** 7/7 still pass against the bug variant — the bug is a business-rule issue, not a safety issue, and this layer correctly does not flag it |
| `unit_test_tool.py` | A Python function `run_unit_tests(test_path: str = "test_orders.py") -> dict` that shells out to pytest and returns structured JSON: `{passed, failed, total, failing_tests: [{test, message}], exit_code, raw_stdout_tail}`. | Confirmed returns correct structured output |
| `layer_tools.py` | Two Python functions: `run_property_tests() -> dict` and `run_adversarial_tests() -> dict`, same structured-JSON contract as above (`{passed, failed, total, failing_tests, exit_code}`), running `test_properties.py` and `test_adversarial.py` respectively. | Confirmed returns correct structured output for both |
| `requirements.txt` | `fastapi, pydantic, pytest, pytest-json-report, httpx, uvicorn, hypothesis, requests` | N/A |

**Note on Layers 3 and 4 (trajectory/tool-sequence checks, LLM-as-judge):** these are deliberately excluded from this workflow. They test *agentic* systems specifically (does an agent call the right tools; is an agent's open-ended output good). `app.py` is a plain deterministic REST API with no agentic behavior of its own — those two layers do not apply to it and should not be built against it.

---

## 2. What you need to build inside Agent Studio

### 2.1 Create the workflow

Create a new Agent Studio workflow. Enable the **Manager Agent** option (Agent Studio's built-in orchestration pattern — one agent designated as delegator, others as specialists it can invoke).

### 2.2 Define 5 agents, exactly as follows

Use these backstories verbatim as a starting point. Adapt only as needed to fit Agent Studio's actual backstory/goal field format and length limits — do not change the substance of the routing rules or the recommendation logic.

---

**Agent 1: Test Strategy Manager** (the Manager Agent)

```
You are the Test Strategy Manager for the Inventory & Orders API.
You will be given a PR's diff, title, and description. Decide which
specialist agent(s) apply, based on these rules:

- If the diff touches core logic in app.py (create_order, get_item,
  add_item, get_order), invoke the Unit Test Specialist.
- If the diff touches any function with numeric or boundary logic
  (stock checks, quantity validation), also invoke the Property-Based
  Specialist.
- If the diff touches request handling, input validation, or the
  HTTP layer, also invoke the Adversarial/Safety Specialist.
- If you are unsure whether a rule applies, invoke the specialist
  anyway -- a false positive (running an unnecessary check) is much
  cheaper than a false negative (skipping a check that was needed).

Invoke all applicable specialists, wait for their structured results,
and forward everything -- unmodified -- to the Root-Cause & Triage
Agent. Do not analyze or summarize results yourself.
```

---

**Agent 2: Unit Test Specialist**

```
You are the Unit Test Specialist for the Inventory & Orders API.
When invoked, call the run_unit_tests tool and return its exact
structured output. Do not editorialize or summarize -- return the
tool's JSON result as your response, including the full
failing_tests list if anything failed.
```
Tool bound to this agent: `run_unit_tests` (from `unit_test_tool.py`).

---

**Agent 3: Property-Based Specialist**

```
You are the Property-Based Specialist for the Inventory & Orders
API. When invoked, call the run_property_tests tool and return its
exact structured output. Do not editorialize or summarize -- return
the tool's JSON result as your response, including the full
failing_tests list if anything failed.
```
Tool bound to this agent: `run_property_tests` (from `layer_tools.py`).

---

**Agent 4: Adversarial/Safety Specialist**

```
You are the Adversarial-Safety Specialist for the Inventory & Orders
API. When invoked, call the run_adversarial_tests tool and return
its exact structured output. Do not editorialize or summarize --
return the tool's JSON result as your response, including the full
failing_tests list if anything failed.
```
Tool bound to this agent: `run_adversarial_tests` (from `layer_tools.py`).

---

**Agent 5: Root-Cause & Triage Agent**

```
You are the Root-Cause & Triage Agent. You will receive structured
evidence from one or more specialist agents, plus the original PR
description, about a change to the Inventory & Orders API.

Produce ONE report for a human engineer, covering:
1. What changed (from the PR description)
2. What broke, if anything, per layer -- cite the specific failing
   test(s) and their exact error message for each specialist that
   reported a failure
3. Why it likely broke (connect failures back to the PR description
   -- was any "no behavior change intended" claim accurate?)
4. A suggested next step

End your report with a single machine-readable line in exactly this
format, based on your findings:
RECOMMENDATION: auto_approve
or
RECOMMENDATION: needs_human_review

Use needs_human_review if ANY specialist reported a failure, or if
you are not fully confident the change is safe. Use auto_approve
only if every invoked specialist reported a clean pass.

Ground every claim in the evidence you were given. Do not speculate
beyond what the specialists reported.
```
No tools bound to this agent — it only synthesizes text from what the Manager forwards it.

### 2.3 Tool registration contract

Each of the three tool functions must be registered so Agent Studio can call them. The exact registration syntax (decorator, wrapper, manifest format) depends on your Agent Studio version — consult its current Tools User Guide for the precise mechanism. Regardless of syntax, the contract each tool must satisfy is:

- **Input:** none required (each function takes zero required arguments; `run_unit_tests` has one optional argument, `test_path`, defaulting correctly).
- **Output:** a JSON-serializable dict with at minimum the keys `passed` (int), `failed` (int), `total` (int), `failing_tests` (list of objects with `test` and `message` keys), `exit_code` (int).
- **Execution environment:** the tool's execution context (sandbox/container/working directory — whatever Agent Studio uses to actually run the Python function) must have `app.py`, `test_orders.py`, `test_properties.py`, `test_adversarial.py`, and all packages in `requirements.txt` available in the same working directory, because these functions shell out to `pytest` against relative file paths. **Confirm how Agent Studio provisions the tool execution environment and ensure these files are present there** — this is an environment-setup step, not a code change.

### 2.4 Orchestration pattern

Manager → (Unit Specialist, Property-Based Specialist, Adversarial Specialist, invoked conditionally per the Manager's routing rules) → RCA Agent. This mirrors Cloudera's own published Manager/Specialist/RCA pattern (originally documented for a data-quality use case) — if your Agent Studio version has a template or example for this pattern, prefer adapting it over building the orchestration from scratch.

---

## 3. Testing protocol inside the Studio — run this exact sequence, verify against these exact expected results

**Test run 1 — clean version.** Ensure `app.py` is the clean, unmodified version (as delivered, not the bug variant). Provide the workflow with this input:
```
pr_title: "Refactor stock-check condition"
pr_description: "Tightened the stock-check condition in create_order() for clarity. No behavior change intended."
diff: (paste the actual diff — or if unavailable, restate the change: the condition in create_order changes from `if item["stock"] < quantity:` to `if item["stock"] <= quantity:`)
```
**Expected:** Manager invokes all three specialists (the described change plausibly touches core logic, boundary logic, and could affect request handling). Unit Specialist reports 7/7 passed. Property-Based Specialist reports 4/4 passed. Adversarial Specialist reports 7/7 passed. RCA Agent's report ends with `RECOMMENDATION: auto_approve`.

**Test run 2 — bug variant.** Swap in the logic from `bug_variant.py` (replace `create_order`'s stock-check condition in `app.py` with the `<=` version). Re-run the same input.
**Expected:** Unit Specialist reports 6 passed, 1 failed (`test_order_exact_remaining_stock_succeeds`). Property-Based Specialist reports 3 passed, 1 failed (`test_property_any_valid_order_must_succeed`). Adversarial Specialist still reports 7/7 passed (this is correct — do not treat this as a bug in the Adversarial Specialist). RCA Agent's report must clearly name the two specific failing tests and their error messages, and must end with `RECOMMENDATION: needs_human_review`.

**Reliability check.** Run test run 2 at least 3 times. Agents are non-deterministic — confirm the RCA Agent's `RECOMMENDATION:` line is consistent across all runs before moving on. If it isn't, tighten the RCA Agent's backstory to be more directive (e.g., make the auto_approve/needs_human_review criteria even more explicit and less open to interpretation) and re-test until it is reliable.

Restore `app.py` to the clean version once testing is complete.

---

## 4. Native Evaluations setup

Agent Studio has a built-in Evaluations feature. Use it, don't rebuild equivalent functionality:

- **Automatic metrics** (no action needed — populate on every run): check Token Usage and Latency after test run 2 above, to have real numbers on hand for a cost/performance discussion.
- **LLM-as-judge metrics** (manually triggered — click "Run LLM as a judge evals" in the Evaluations dashboard after a workflow run completes): specifically check the **Manager Delegation** score — this metric evaluates whether the Manager correctly routed to the appropriate specialist(s). Expand its Explanation Block to see the judge's reasoning. Screenshot a good result from test run 2 (where correct delegation is less trivial than the "run everything" clean-version case).

---

## 5. Deployment and the endpoint contract — resolve this, don't guess

Deploy the finished, tested workflow as a production endpoint (Agent Studio supports deploying workflows as long-running endpoints).

**Once deployed, you must determine and document the following before touching the GitHub integration (Section 6):**

1. **Is the endpoint synchronous or asynchronous?** I.e., does a POST request block and return the full result in one response, or does it return a job ID that must be polled? This is currently unknown and was not guessed at in the GitHub integration scripts — check this directly against your deployed endpoint.
2. **What is the exact request payload shape the endpoint expects?** (Field names, nesting, content-type.)
3. **What is the exact response shape?** In particular: is there a clean, separately-extractable field for the recommendation (`auto_approve` / `needs_human_review`), or does the RCA agent's full text report come back as one blob that needs the `RECOMMENDATION:` line parsed out of it with a regex or string search?
4. **How long does a typical run take, end to end** (all three specialists plus RCA synthesis)? This matters for the GitHub Actions timeout setting and for whether a live on-stage trigger is viable within a short demo window.

Once you know these four things, **update `scripts/trigger_agent_studio.py`** (delivered file, see Section 6) to match reality:
- If asynchronous, add a polling loop (POST to start, then GET/poll a status endpoint until complete, with a reasonable timeout and interval).
- Adjust the `payload` dict's keys to match what the endpoint actually expects, if different from the current guess (`pr_title`, `pr_description`, `pr_number`, `changed_files`, `diff`).
- Adjust the response parsing: the current script expects `result["recommendation"]` and `result["report"]` to exist directly. If the real response doesn't cleanly separate these, add a parsing step that extracts the `RECOMMENDATION:` line from the full report text via string search (e.g., `"RECOMMENDATION: auto_approve" in report_text` vs `"RECOMMENDATION: needs_human_review" in report_text`) and derives the `recommendation` field from that instead.

---

## 6. GitHub integration — already built, your job is to wire and verify it, not rewrite it

These files are delivered and already logic-tested (with mocked API calls — never against a real GitHub repo, since none was available at build time):

| File | Role |
|---|---|
| `.github/workflows/agentic-qa.yml` | Fires on PR open/update. Computes the diff, calls `trigger_agent_studio.py`, then `post_pr_feedback.py`. |
| `.github/workflows/human-approval.yml` | Fires on any issue/PR comment. Calls `handle_approval_comment.py`, which only acts if the comment is `/approve`. |
| `scripts/trigger_agent_studio.py` | Calls the deployed endpoint. **Needs the Section 5 fixes before first real use.** |
| `scripts/post_pr_feedback.py` | Posts the RCA report as a PR comment; sets a `agentic-qa/manager-review` commit status (`success` if auto_approve, `pending` if needs_human_review). Logic-tested: both paths confirmed to produce correct API calls. |
| `scripts/handle_approval_comment.py` | On `/approve`, checks the commenter has write/admin access via GitHub's API (never trusts the comment text alone), then flips the status check to `success`. Logic-tested: write-access accept, non-write reject, and unrelated-comment-ignored paths all confirmed correct. |

**Setup steps (not yet done by anyone, must be done once):**
1. In the target GitHub repo's settings → Secrets, add `AGENT_STUDIO_ENDPOINT_URL` and `AGENT_STUDIO_API_KEY` (values from Section 5's deployment).
2. Confirm the default `GITHUB_TOKEN` has `pull-requests: write` and `statuses: write` permissions for this repo (add an explicit `permissions:` block to both workflow YAML files if the org's default token scope is more restrictive than that).
3. Add a branch protection rule on the default branch requiring the `agentic-qa/manager-review` status check to pass before merge. Without this, the status check is purely informational and doesn't actually gate anything.

**Verification (must be done at least once, for real, before this is considered working):**
1. Open a real PR with the clean `app.py`. Confirm: the Action runs, a comment appears with the full report, status shows `success`.
2. Open a second real PR with the bug variant swapped in. Confirm: comment appears citing the two specific failing tests, status shows `pending`.
3. From an account with write access, comment `/approve` on that second PR. Confirm: a confirmation comment appears, status flips to `success`.
4. From an account WITHOUT write access (or simulate by temporarily testing the rejection path), comment `/approve`. Confirm: a rejection comment appears, status remains `pending`.

---

## 7. Definition of done

- [ ] All 5 agents created in Agent Studio with the backstories from Section 2.2
- [ ] All 3 tools registered and confirmed callable, with the service/test files present in their execution environment
- [ ] Test run 1 (clean) produces the expected results from Section 3
- [ ] Test run 2 (bug variant) produces the expected results from Section 3, reliably across 3+ runs
- [ ] `app.py` restored to clean version after testing
- [ ] Manager Delegation score checked in the native Evaluations dashboard for at least one run
- [ ] Workflow deployed as a production endpoint
- [ ] Endpoint's sync/async behavior, request shape, response shape, and typical latency documented
- [ ] `scripts/trigger_agent_studio.py` updated to match the real endpoint contract
- [ ] GitHub repo secrets and branch protection configured
- [ ] All 4 verification steps in Section 6 completed successfully against a real repo
- [ ] Typical end-to-end latency (PR open → comment appears) measured and recorded, to inform whether a live on-stage trigger is viable or whether a pre-staged/recorded demo is the safer choice

---

## 8. Explicit open questions this spec does not answer — resolve during implementation, do not guess and silently proceed

1. Agent Studio's exact tool-registration syntax for this version.
2. Whether the deployed endpoint is sync or async.
3. The endpoint's exact request/response JSON shape.
4. Whether the three specialists execute in parallel or sequentially inside the workflow (affects total latency).
5. Whether Agent Studio's tool execution environment needs files explicitly uploaded/mounted, or whether it has some other mechanism for making `app.py` and the test files available to the tool functions at runtime.

If any of these turn out to have a different answer than what this spec assumed, update this document (or flag the discrepancy back) rather than silently patching around it — later steps in this spec may depend on the assumption.
