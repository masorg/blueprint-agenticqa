# Evaluation report — provider `stub+oracle-routing`

Generated: 2026-10-14T18:49:57.199136+00:00
Corpus size: 4 scenario(s)
Code: `244663e23b` DIRTY (uncommitted changes), diff `None`; corpus `local` fingerprint `f197db1cd00e1de5`
Model: configured `None`, API reported `n/a`

## Layer catch matrix (which layer failed on which scenario)

| scenario | unit | property | trajectory | llm_judge | adversarial |
|---|---|---|---|---|---|
| `clean-baseline` | pass | pass | · | · | pass |
| `stock-check-off-by-one` | FAIL | FAIL | · | · | pass |
| `stale-stock-bound-property-only` | pass | FAIL | · | · | pass |
| `name-length-bound-removed-adversarial-only` | pass | pass | · | · | FAIL |

## Verdict correctness (confusion matrix)

- Accuracy: 100%
- True positive (correctly flagged for human review): 3
- True negative (correctly auto-approved): 1
- **False positive** (auto-approve expected, flagged anyway): 0
- **False negative** (flag expected, auto-approved instead — the dangerous case): 0

## Routing & classification agreement (LLM vs. heuristic)

- Routing agreement rate: 100%
- Repo-kind classification accuracy (agentic vs. plain_service, vs. label): 100%
- Classification agreement rate (mean over scenarios): 100%
- Classification verified rate (mean over scenarios): 100%

## Coverage safety net (dropped-test fallback + guardrail overrides)

- All discovered tests actually executed (none silently dropped): YES
- Tests demoted to a fallback layer (would have been dropped pre-fix): 0
- Guardrail overrides (model said an agentic-only layer applies; guardrail forced it off): 0

## Cost / latency

- Total wall-clock: 13.15s
- Total LLM tokens: n/a (heuristic run — no LLM calls)
- LLM calls: 0, of which failed and fell back to the heuristic: 0 (agreement figures above are inflated by exactly these fallbacks)

## Per-scenario detail

### `clean-baseline`
- Expected verdict: `auto_approve` — actual: `auto_approve` — ✓ correct
- Expected failing layers: none
- Actual failing layers: none
- Routing: repo_kind=`plain_service` (expected `plain_service`, correct), applicable=['adversarial', 'property', 'unit'] (heuristic said ['adversarial', 'property', 'unit']) — agrees
- Classification: 18 tests, agreement=100%, verified=100%
- Totals: {'passed': 18, 'failed': 0, 'skipped': 0, 'total': 18}
- Wall time: 3.15s

### `stock-check-off-by-one`
- Expected verdict: `needs_human_review` — actual: `needs_human_review` — ✓ correct
- Expected failing layers: ['property', 'unit']
- Actual failing layers: ['property', 'unit']
- Routing: repo_kind=`plain_service` (expected `plain_service`, correct), applicable=['adversarial', 'property', 'unit'] (heuristic said ['adversarial', 'property', 'unit']) — agrees
- Classification: 18 tests, agreement=100%, verified=100%
- Totals: {'passed': 16, 'failed': 2, 'skipped': 0, 'total': 18}
- Wall time: 3.33s

### `stale-stock-bound-property-only`
- Expected verdict: `needs_human_review` — actual: `needs_human_review` — ✓ correct
- Expected failing layers: ['property']
- Actual failing layers: ['property']
- Routing: repo_kind=`plain_service` (expected `plain_service`, correct), applicable=['adversarial', 'property', 'unit'] (heuristic said ['adversarial', 'property', 'unit']) — agrees
- Classification: 18 tests, agreement=100%, verified=100%
- Totals: {'passed': 17, 'failed': 1, 'skipped': 0, 'total': 18}
- Wall time: 3.35s

### `name-length-bound-removed-adversarial-only`
- Expected verdict: `needs_human_review` — actual: `needs_human_review` — ✓ correct
- Expected failing layers: ['adversarial']
- Actual failing layers: ['adversarial']
- Routing: repo_kind=`plain_service` (expected `plain_service`, correct), applicable=['adversarial', 'property', 'unit'] (heuristic said ['adversarial', 'property', 'unit']) — agrees
- Classification: 18 tests, agreement=100%, verified=100%
- Totals: {'passed': 17, 'failed': 1, 'skipped': 0, 'total': 18}
- Wall time: 3.32s
