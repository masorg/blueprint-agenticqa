# Vertical-flavored demo: planning (not built yet)

The current demo (`demo/inventory-orders-api`) is deliberately generic — a
small, deterministic REST service chosen so the pyramid's mechanics show
clearly, with no domain complexity competing for attention. That's the right
choice for explaining the *architecture*. It's the wrong choice for an
Applied AI pitch that needs a customer to see themselves in the demo. This
doc thinks through what a second, vertical-flavored scenario should be —
scoped for a future build, not started here.

## What "vertical-flavored" needs to do

1. Look like something a real Cloudera customer actually runs, not a toy CRUD API.
2. Still produce a clean, explainable pyramid story (a bug per layer, the way
   the current demo does) — domain flavor shouldn't cost us the clarity that
   makes the current demo work.
3. Ideally lean into what's *already* Agentic QA's strongest differentiator
   (the agentic-only layers) rather than compete with it by adding yet
   another plain-CRUD example.

## Three candidates considered

### A. Data ingestion / ETL validation service

A small service validating and transforming incoming records before they
land in a warehouse table (Iceberg-flavored, matching Cloudera's core data
platform story). Candidate bugs: a schema-validation regression (adversarial),
a rounding/aggregation error in a transform (property-based), a dedup/
partitioning bug only visible across a *sequence* of ingests (property-only,
same shape as the existing `stale-stock-bound` scenario).

- **Fit**: strong for Cloudera's traditional data-engineering audience.
- **Risk**: it's still a plain service — doesn't showcase the agentic layers
  at all, so it's "the same demo, different domain nouns" rather than a new
  story.

### B. Regulated-transaction decisioning service (loan/claims)

A small "decisioning" API reflecting Cloudera's banking/insurance customer
base — compliance-flavored adversarial tests (PII leakage, a fairness-style
property check across protected-attribute-adjacent inputs).

- **Fit**: strong resonance with a regulated-industry sales motion.
- **Risk**: highest effort and highest risk of "looking wrong" to a domain
  expert if the fairness/compliance framing is even slightly off — this is
  exactly the kind of detail a knowledgeable prospect would notice and hold
  against the demo, and it's the hardest of the three to get convincingly
  right without real domain input.

### C. Agentic support/retrieval assistant (RAG + tool calls)

A small "Support Assistant" agent: retrieves from a knowledge base, then
calls tools (`create_ticket`, `escalate_to_human`, `issue_refund`). Candidate
bugs: a tool-call ordering violation — e.g., escalating before checking a
policy lookup tool (trajectory), an ungrounded/hallucinated answer that
doesn't cite retrieved context (LLM-as-judge), and an unsafe
auto-escalation that skips a required human-approval tool call entirely
(adversarial — this one lines up unusually well with the product's own
human-approval-gate thesis).

- **Fit**: this *is* the product's differentiator, not a side branch of it.
  Cloudera's Applied AI team's own remit is building and shipping AI agents
  on Cloudera infrastructure — a demo where the system under test is itself
  a Cloudera-flavored agent closes the loop: "Cloudera builds the agent,
  Agentic QA tests the agent."
- **Risk**: lowest technical risk of the three — the actual mechanics
  (tool-calling with an injectable/scripted planner, no real LLM key needed
  in the test sandbox) already exist and are proven in
  `AgenticProduct`'s `demo/restock-agent` branch (§4.1 of
  `docs/13-evaluation-harness.md`). This would be an *extension* of that
  pattern with new tool-ordering/safety bug classes, not a from-scratch build.

## Recommendation

**Option C.** It's the strongest narrative fit (leans into the differentiator
instead of diluting it), the lowest technical risk (extends a pattern already
built and validated), and it directly deepens the paper's evaluation corpus
at the same time — a bug in an *escalation* tool call that skips required
approval is a natural adversarial-layer scenario for the real-PR corpus too.
Option A is a reasonable second scenario later if the pitch lands and a
customer specifically wants a data-pipeline flavor; Option B needs real
domain-expert input before it's safe to build, not just engineering time.

## If/when this gets built

Rough shape, mirroring `demo/inventory-orders-api/bug_variant.py`'s
toggle-per-bug-class pattern:

- `demo/support-assistant/` (or add directly to `AgenticProduct` as a new PR
  branch, matching the real-PR corpus pattern) — a small agent with a
  scripted/injectable planner (no LLM key needed to run its own tests,
  matching `restock_agent.py`'s existing design).
- Three toggled bug variants: tool-order violation (trajectory-only),
  ungrounded answer (LLM-judge-only), skipped-approval auto-escalation
  (adversarial-only) — each structurally invisible to the other layers, same
  discipline as the three bugs already built into `inventory-orders-api`.
- Feeds both goals at once: a demo scene that makes the agentic differentiator
  the *hero* rather than an aside, and 3 more eval-corpus scenarios that
  specifically exercise the agentic-only layers under failure, which the
  current corpus (one all-green agentic scenario) doesn't yet do.

**Not started.** This is the plan; building it is a separate, later step.
