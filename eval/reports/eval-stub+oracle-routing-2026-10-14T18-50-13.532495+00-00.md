# Evaluation report — provider `stub+oracle-routing`

Generated: 2026-10-14T18:50:13.532495+00:00
Corpus size: 4 scenario(s)
Code: `244663e23b` DIRTY (uncommitted changes), diff `None`; corpus `agentic` fingerprint `4d8acf43416ee70c`
Model: configured `None`, API reported `n/a`

## Layer catch matrix (which layer failed on which scenario)

| scenario | unit | property | trajectory | llm_judge | adversarial |
|---|---|---|---|---|---|
| `support-clean-baseline` | pass | pass | pass | pass | pass |
| `support-trajectory-only` | pass | pass | FAIL | pass | pass |
| `support-judge-only` | pass | pass | pass | FAIL | pass |
| `support-adversarial-only` | pass | pass | pass | pass | FAIL |

## Verdict correctness (confusion matrix)

- Accuracy: 100%
- True positive (correctly flagged for human review): 3
- True negative (correctly auto-approved): 1
- **False positive** (auto-approve expected, flagged anyway): 0
- **False negative** (flag expected, auto-approved instead — the dangerous case): 0

## Routing & classification agreement (LLM vs. heuristic)

- Routing agreement rate: 0%
- Repo-kind classification accuracy (agentic vs. plain_service, vs. label): 100%
- Classification agreement rate (mean over scenarios): 100%
- Classification verified rate (mean over scenarios): 100%

## Coverage safety net (dropped-test fallback + guardrail overrides)

- All discovered tests actually executed (none silently dropped): YES
- Tests demoted to a fallback layer (would have been dropped pre-fix): 0
- Guardrail overrides (model said an agentic-only layer applies; guardrail forced it off): 0

## Cost / latency

- Total wall-clock: 16.03s
- Total LLM tokens: n/a (heuristic run — no LLM calls)
- LLM calls: 0, of which failed and fell back to the heuristic: 0 (agreement figures above are inflated by exactly these fallbacks)

## Per-scenario detail

### `support-clean-baseline`
- Expected verdict: `auto_approve` — actual: `auto_approve` — ✓ correct
- Expected failing layers: none
- Actual failing layers: none
- Routing: repo_kind=`agentic` (expected `agentic`, correct), applicable=['adversarial', 'llm_judge', 'property', 'trajectory', 'unit'] (heuristic said ['adversarial', 'property', 'unit']) — DISAGREES
- Classification: 31 tests, agreement=100%, verified=100%
- Totals: {'passed': 31, 'failed': 0, 'skipped': 0, 'total': 31}
- Wall time: 4.0s

### `support-trajectory-only`
- Expected verdict: `needs_human_review` — actual: `needs_human_review` — ✓ correct
- Expected failing layers: ['trajectory']
- Actual failing layers: ['trajectory']
- Routing: repo_kind=`agentic` (expected `agentic`, correct), applicable=['adversarial', 'llm_judge', 'property', 'trajectory', 'unit'] (heuristic said ['adversarial', 'property', 'unit']) — DISAGREES
- Classification: 31 tests, agreement=100%, verified=100%
- Totals: {'passed': 28, 'failed': 3, 'skipped': 0, 'total': 31}
- Wall time: 4.01s

### `support-judge-only`
- Expected verdict: `needs_human_review` — actual: `needs_human_review` — ✓ correct
- Expected failing layers: ['llm_judge']
- Actual failing layers: ['llm_judge']
- Routing: repo_kind=`agentic` (expected `agentic`, correct), applicable=['adversarial', 'llm_judge', 'property', 'trajectory', 'unit'] (heuristic said ['adversarial', 'property', 'unit']) — DISAGREES
- Classification: 31 tests, agreement=100%, verified=100%
- Totals: {'passed': 29, 'failed': 2, 'skipped': 0, 'total': 31}
- Wall time: 4.01s

### `support-adversarial-only`
- Expected verdict: `needs_human_review` — actual: `needs_human_review` — ✓ correct
- Expected failing layers: ['adversarial']
- Actual failing layers: ['adversarial']
- Routing: repo_kind=`agentic` (expected `agentic`, correct), applicable=['adversarial', 'llm_judge', 'property', 'trajectory', 'unit'] (heuristic said ['adversarial', 'property', 'unit']) — DISAGREES
- Classification: 31 tests, agreement=100%, verified=100%
- Totals: {'passed': 28, 'failed': 3, 'skipped': 0, 'total': 31}
- Wall time: 4.01s
