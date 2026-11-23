# Running GATE in shadow mode on real repositories

Shadow mode runs GATE on every pull request but posts nothing: no status, no comment. Each decision is
appended to a log. After two weeks or more, `agenticqa shadow-compare` joins the log with each repository's
own CI result for the same commit, which is the evidence the paper needs.

## Settings (environment, prefix `AGENTICQA_`)

| Setting | Value |
|---|---|
| `GITHUB_MODE` | `shadow` (or `advisory`: a separate, non-required status and no comments) |
| `SHADOW_LOG_PATH` | a persistent path on the CAI project, e.g. `/home/cdsw/data/shadow-decisions.jsonl` |
| `SELECTION` | `change` |
| `GITHUB_ALLOWED_ORGS` | the organisation(s) taking part |
| `GITHUB_APP_ID`, `GITHUB_APP_PRIVATE_KEY_PATH`, `GITHUB_WEBHOOK_SECRET` | the GitHub App |
| `LLM_PROVIDER` and model settings | optional; the verdict does not use a model |

## Steps

1. Choose Python repositories with pytest suites whose maintainers agree to take part.
2. Install the GitHub App on those repositories with read access to contents, pull requests, checks and
   statuses. Shadow mode needs no write access; advisory mode needs commit-status write.
3. Deploy the app on Cloudera AI with the settings above (`cml/` has the application definition).
4. Leave it running. The log records the verdict, the selection, the layers and the timings, never code.
5. Run `agenticqa shadow-compare --log <path>` with a token that can read checks, and commit the report.

The comparison reports, per pull request: GATE's verdict, the project's CI result, the share of tests GATE
would have run, and whether GATE sent the change to a person because no test reaches it.
