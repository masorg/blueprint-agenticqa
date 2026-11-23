# Evaluation harness

`agenticqa eval-run` runs a small labeled benchmark corpus through the **real**
`Pipeline` (no reimplemented guardrail/verdict logic — see
`src/agenticqa/eval/harness.py`) and writes a JSON + Markdown report with the
metrics identified in the roadmap (`/Users/aamir/.claude/plans/have-a-look-at-sprightly-frog.md`,
Track B):

- **Verdict correctness** — a confusion matrix against each scenario's labeled
  expected verdict (`auto_approve` / `needs_human_review`), since the corpus
  scenarios have a ground truth, unlike a real unlabeled PR stream.
- **Routing agreement** — the LLM (or heuristic) router's applicable-layer set
  vs. an independently-computed `HeuristicRouter` read of the same run.
- **Classification agreement / verified rate** — aggregated straight from the
  `agreement`/`verified` fields `orchestrator/llm_touchpoints.py` already
  computes per test, which were being thrown away (never read by anything)
  before this harness existed.
- **Coverage safety net** — whether every discovered test actually executed
  (see "Finding" below), how many tests were demoted to a fallback layer, and
  how many times the deterministic guardrail actually had to override the
  model's raw routing pick.
- **Cost / latency** — wall-clock per scenario, plus LLM token usage when the
  configured provider is `openai`/`cloudera` (see the `last_usage` addition to
  `OpenAIClient` in `llm/openai_client.py` — purely observational, doesn't
  change request behavior).

## Usage

```bash
# Free: heuristic-only baseline (the "no-LLM" ablation).
agenticqa eval-run --provider stub

# Costs a handful of real LLM calls: whatever AGENTICQA_LLM_PROVIDER is set to.
agenticqa eval-run --provider configured
```

Reports land in `eval/reports/eval-<provider>-<timestamp>.{json,md}`.

## Design choices worth knowing about

- **The pipeline is exercised directly, not through the HTTP API** — `harness.py`
  builds a `QARun` (`source="local"`), wires a fresh in-memory `RunRepository`,
  `EventBus`, and `SubprocessVenvExecutor`, and calls `Pipeline.execute()`.
  This is the same call the `Engine` makes in production; the harness just
  skips the FastAPI/SSE layer around it.
- **Router/Classifier are wrapped in a thin recording shim**
  (`_RecordingRouter`/`_RecordingClassifier`), not reimplemented — they
  delegate every call unchanged and just remember what came back, so the eval
  numbers cannot silently drift from what the guardrail in
  `orchestrator/llm_touchpoints.py` / `orchestrator/pipeline.py` actually does.
- **Routing agreement always has a comparison point**, even when the LLM
  router is the one under test, by also calling a bare `HeuristicRouter()` on
  a deep copy of the same run.

## The benchmark corpus (N=4)

`src/agenticqa/eval/corpus.py`, all against `demo/inventory-orders-api`. Each
scenario is a DIFFERENT class of bug, chosen so a bug is only catchable by a
specific layer *by construction* — not because of which numbers happened to
be picked:

| Scenario | Bug class | Structurally catchable by | Expected verdict |
|---|---|---|---|
| `clean-baseline` | none | — | `auto_approve` |
| `stock-check-off-by-one` | business-rule off-by-one (`<`→`<=`) | unit + property | `needs_human_review` |
| `stale-stock-bound-property-only` | stock check reads a stale/cached bound instead of live remaining stock | property only — every unit test places exactly one order per item, so it structurally cannot see a bug that only appears from the *second* order onward | `needs_human_review` |
| `name-length-bound-removed-adversarial-only` | HTTP-layer input-length bound removed | adversarial only — unit/property call the domain functions directly, bypassing HTTP validation entirely | `needs_human_review` |

Full writeups (with diffs) in `demo/inventory-orders-api/bug_variant.py`.

## Finding, fix, and measured effect: the dropped-test bug

The first live-LLM pilot run (N=2 corpus, before the fix below) surfaced a
real gap: the LLM classified `tests/test_orders.py::test_add_item_then_get_item`
as `trajectory` (an agentic-only layer) while this repo routes as
`plain_service`. In `orchestrator/pipeline.py::_plan`, tests were bucketed by
classified layer first, and a bucket for a non-applicable layer was discarded
outright — so **that test was classified but never executed at all**, with no
error and no surfaced warning (only a quiet `verified: false`).

**Fixed** (`orchestrator/pipeline.py::_bucket_with_fallback`, unit-tested in
`tests/test_pipeline_guardrail.py`): a test classified into a layer this run
doesn't route to is now demoted to its heuristic layer if that one applies,
else to `UNIT` (the one layer every routing decision leaves applicable) —
never dropped. Every discovered test is guaranteed to execute *somewhere*,
regardless of a classification quirk. This directly reinforces the
project's central claim: the code, not the model, decides what actually
happens.

**Measured, not assumed** — the harness now reports `all_discovered_tests_ran`
and a `demoted_to_fallback_layer` count per scenario. Same LLM misclassifying
the same test, same repo, run before and after the fix:

| | before fix (N=2 corpus) | after fix (N=4 corpus) |
|---|---|---|
| Discovered tests | 18 | 18 |
| Tests actually executed | **17** | **18** |
| Coverage-safety-net status | not instrumented | `all_discovered_tests_ran: True`, demoted: 1/scenario (4 total) |

See `eval/reports/eval-openai-2026-09-23T15-46-23.834717+00-00.md` (before) vs.
`eval/reports/eval-openai-2026-09-23T16-12-18.511305+00-00.md` (after).

## Real-PR corpus tier (N=2): the live GitHub demo target, no CAI needed

`REAL_PR_CORPUS` (`src/agenticqa/eval/corpus.py`) points the harness at real
commits on `AgenticQAOrg/AgenticProduct` via `source="github"` — the exact
checkout path production uses (`execution/checkout.py`: `git clone` +
`fetch <sha>` + `checkout`), with each PR's real diff fetched live from its
public `.diff` URL. This needs no deployed app, no webhook, no CAI — just
network access, the same as any `git clone`.

- **`agenticproduct-pr1-regression`** — real PR #1, the off-by-one stock-check
  regression as an actual commit (no `agenticqa.yaml` hint file, so routing
  runs fully automatically against a real diff).
- **`agenticproduct-pr5-restock-agent`** — real PR #5, a tool-calling restock
  agent (33 tests spanning all 5 layers via filename cues). The one scenario
  where the agentic-only layers *should* turn on — tests the router's
  "turn it on" path, complementing every other scenario's "turn it off"
  guardrail path.

`--corpus local` (default — no network beyond installing deps) runs just the
synthetic demo-repo tier; `--corpus real-pr` runs just this tier (clones over
the network); `--corpus full` runs both (N=6).

## Guardrail-override visibility: instrumented, still 0 — now on a fair test

`LayerRouting.guardrail_overridden` (`models/pyramid.py`) is set by
`_to_decision` whenever the model claims an agentic-only layer applies to
what routed as `plain_service`, and the guardrail forces it off (tested in
`tests/test_llm_touchpoints.py`). Across all 6 scenarios — five plain-service,
one genuinely agentic — `total_guardrail_overrides` is still 0: gpt-4o
correctly identified `agenticproduct-pr5-restock-agent` as `agentic` (100%
repo-kind accuracy) and never mistakenly claimed an agentic-only layer for
any plain-service scenario. **This is a stronger, fairer null result than
before** (the corpus now actually contains an agentic case to potentially
confuse the model with) but it's still a null result, honestly reported: the
guardrail's necessity remains a structural/design argument backed by
literature showing LLM review agents *can* be steered (SEVRA-BENCH,
`docs/12-related-work.md`), not yet an empirically observed correction.
Forcing an observation would need a deliberately adversarial scenario — a
plain-service change with agentic-*sounding* red herrings (a class literally
named `Agent`, a comment mentioning `tool_use`, in code that does nothing
agentic) designed to tempt a misclassification. Flagged as concrete follow-up
work, not attempted here.

## Pilot run results (2026-09-23)

- **`--provider stub`, N=4 (local tier)**: verdict accuracy 4/4, all four bug
  classes caught by exactly the layer predicted, 22.27s, zero tokens.
- **`--provider configured` = openai/gpt-4o, N=4 (local tier)**: verdict
  accuracy 4/4, routing agreement 100%, classification agreement 94% (the
  recurring trajectory/unit disagreement below), **all 18 discovered tests
  executed in every scenario** (0 dropped, 4 demotions — the safety net
  engaging every time), 1921 tokens / 86.8s.
- **`--provider stub`, N=2 (real-PR tier)**: verdict accuracy 2/2, repo-kind
  accuracy 2/2 (correctly plain_service and agentic), 21.6s, zero tokens.
- **`--provider configured` = openai/gpt-4o, N=6 (full corpus)**: verdict
  accuracy **6/6**, routing agreement 100%, repo-kind accuracy **100%**
  (correctly identifies the one real agentic PR among five plain-service
  scenarios), classification agreement 95%, **all discovered tests executed
  in every scenario** (0 dropped, 5 demotions total — same recurring
  trajectory/unit misclassification, now observed across 5 of 6 scenarios,
  caught every time), 2961 tokens / 125.0s total.

Full detail in `eval/reports/`.

## Limitations / honest caveats

- **N=6 across two target repos** (one demo service, one real GitHub
  project) — real progress from N=2, still a pilot, not a benchmark. Growing
  further: more PRs from `AgenticProduct`, or a second real-world target repo.
- **Guardrail overrides: instrumented, still unobserved** (see above) — the
  one deliberate experiment (an adversarial-cue plain-service scenario) that
  would actually stress-test this is identified but not yet built.
- **Single LLM/model.** All "configured" numbers above are gpt-4o. Comparing
  across models/providers is unexplored.
