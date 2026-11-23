## 4. Evaluation Design

We ask five questions.

- **RQ1, safety.** Does the selected run miss a failure that the full run shows?
- **RQ2, savings.** How many tests and how much time does selection save, and what do the file-level rule, the name-level rule, and the rule for dynamic loading cost or gain?
- **RQ3, real changes.** Does GATE select the tests that expose real defects, and what would it do with real agent-written pull requests?
- **RQ4, models.** Can a router, a classifier, or an advisor change the verdict or shrink the run, and what do models add over plain rules?
- **RQ5, layers.** Do the layers find different failures?

**What we tested on.**

- *Mutants of {{selMutProjects}} real projects.* A mutant changes one operator or constant in one source file, in one of seven ways (for example `<` to `<=`). Its harm is known, because the full suite either fails on it or does not. The projects are open-source Python libraries with pytest suites at pinned commits: {{realSuitesList}}. Packaging was dropped from the mutant study (Section 7). We took at most 30 mutants per project, drawn round-robin over the source files with a fixed seed, and dropped mutants that break test collection. Each mutant is a diff, which is the input that GATE sees.
- *Held-out projects.* To check that the selector does not only fit the projects it was developed on, we named 22 further projects in three batches and pinned each to its last non-merge commit before 2026-09-30, before running it. Batch 1 (8 projects) was run once, exposed a bug, and was rerun after the fix together with batch 2 (8 projects). Batch 3 (6 projects) was named after the fix and run once with the frozen selector. A project could be set up for only {{selHoldRan}} of the {{selHoldAttempted}}: the others failed for reasons outside the selector, such as missing build metadata, old pinned requirements, optional test dependencies, a baseline run longer than 300 s, or a test configuration that needs plugins we did not install.
- *Real defects.* SWE-bench Verified [32], which pairs real GitHub issues in {{selSweRepos}} Python projects with the patch that fixed each one and the tests that fail before the fix and pass after it. We used at most 20 instances per project, drawn with a fixed seed. Each instance becomes a change that introduces the defect: the tree before the fix with the test patch applied, and the reverse of the fix as the diff. GATE has to select an exposing test or send the change to a person. No test is run, because the exposing tests are given.
- *Two demo services* of {{invTests}} and {{supTests}} tests, with {{mutValid}} mutants, and hand-made bugs, one per layer. The second service is a small agent that calls tools. It uses a scripted planner, so its tests need no API key.
- *AIDev* [1]: the files changed by {{selAidevN}} pull requests in Python repositories, and {{fieldAttempted}} of these pull requests drawn with a fixed seed (14 per agent, closed pull requests with 1 to 20 commits) for a static selection study and a field study that runs their tests. Descriptions of {{aidevPRs}} pull requests serve to check the realism of the misleading texts. We read only text and diffs, and never run the code of a pull request outside a throwaway container.
- *{{routCommits}} public commits* ({{routRepos}} projects, 8 each, seeded) for the router study.

**Models.** gpt-4o (2024-08-06), gpt-4o-mini (2024-07-18), mistral-large-latest, and mistral-small-latest. Every report records the model name that the API returned. Structured calls use temperature 0.

**Procedure and measures for mutants.** For each mutant we run the full suite, which is what a plain CI check does, and record the tests that fail and did not fail on the unmodified project. We compute the selection from the diff and run the selected tests separately. GATE *catches* a mutant if a selected test newly fails or if no test reaches the changed file ($U(c) \neq \emptyset$). It *misses* a mutant if the full run shows a new failure and GATE does neither. We report the number of missed mutants with a 95% Wilson upper bound, $z^2/(n+z^2)$ for no miss in $n$ cases, and the share of tests selected, the share of tests saved over all mutants, and the time of the selected runs against the full runs. Because every refinement of the selector changes only which tests are selected, we judge each selector against the failures that the full suite showed, which is exact if a test's outcome does not depend on which other tests run. The first version of the selector also ran its selected tests, which checks that assumption. The time of a selected run is estimated from per-test durations measured on the unmodified project. Kill rates of layers use the exact sign test on the mutants that only one of two sets kills.

**How we kept the evaluation honest.** Every report records the code version, whether the code had uncommitted changes, a fingerprint of the test set, and the model name that the API returned. Commits from one project are not independent, so intervals over commits resample whole projects. If a model call failed during a run, the system silently used a fallback rule, so we mark such a run INVALID and never report it. All figures and numbers in the text are generated from the reports.

---

