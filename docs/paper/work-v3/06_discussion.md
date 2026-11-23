## 6. Lessons from Engineering the Gate

Building and attacking GATE taught us four lessons that apply to any pipeline in which a language model sits next to a decision. Table V lists the nine fail-open defects that the experiments exposed in GATE and its harness. Each would have let a failing change pass or a missing result count as a pass, each was found by an experiment and not by reading the code, and each is fixed with a regression test.

**Table 5.** Fail-open defects found by the experiments.

| Defect | What would have happened | Found by |
|---|---|---|
| The verdict looked only at the layers the router chose | A router that switched layers off hid failing tests | Hostile routers (5.3) |
| The pipeline trusted the classifier to list every test | A classifier that omitted tests made them not run | Hostile classifiers (5.3) |
| Tests that could not be loaded looked like no tests | Approval with nothing run | Agent pull requests (5.2) |
| A package under a `src/` directory that is itself a package was not recognised | Tests of the package not selected; 4 failures missed | Held-out projects (5.1) |
| A deleted code line between comments was read as no change | The change selected nothing and was approved | Real defects (5.1) |
| Unicode line separators in a failure message split a log record | Later results dropped | Hostile advisors (5.3) |
| A CPU limit below the time limit ended runs early | Partial results reported as complete | Hostile advisors (5.3) |
| The coverage report had no per-test data, so the map was empty | Changes not marked unreached: 21 failures missed | Comparison with testmon (5.1) |
| Coverage cannot attribute import-time code or cached calls to a test | Two failures missed by the first coverage-based core | Mutants (5.1) |

**Separate advice from authority, and enforce it in the structure.** In GATE a model can only add a test to the selection, label it, or draft a report, and the verdict is a function of test outcomes alone. Safety then does not depend on how the model behaves (Section 5.3). A policy that tells a model not to approve unsafe changes is weaker than a design in which it cannot.

**Treat every missing signal as a failure.** Most defects in Table V are a missing signal read as success: a test without a result, a partial run, an empty coverage map, a change that selected nothing. The rules for unreached changes and for unfinished runs turn these cases into reviews. The same rule applies to the platform: a gate should report the delivery of each event it acts on, and a required status that never arrives must block a merge.

**Attack the harness, not only the model.** Attacks on routers, classifiers, and advisors found four of the nine defects, and the comparison with an independent tool found a fifth. Two controls made the zeros trustworthy: the attack run with our fixes removed, which finds bugs, and testmon, which registers misses.

**Make the platform observable.** Deploying an earlier version of GATE on a managed machine-learning platform (Cloudera AI) showed how a platform can hide a gate's output. GitHub webhooks never reached the application: the platform puts applications behind single sign-on and answers an unauthenticated request with a redirect to a login page, which the sender recorded as delivered, while old statuses from a local test made the deployment look alive. The runtime set `PIP_USER=1` for every process, which broke the per-run virtual environments; the executor now builds each environment from an explicit list of variables. An idle application was suspended after about three weeks, and its address stopped resolving, which looked like a network fault on the caller's side. A gate must report that its verdict was delivered, because a silent platform fault looks the same as a green check.

---

## 7. Threats to Validity and Limits

*Scope.* GATE finds no more bugs than the tests do, and it uses a full run as its reference, so a regression that no test detects is invisible to both. A change that weakens tests in a way the rule does not recognise (looser assertions, new mocks, edited fixtures) can still pass, and hostile test code is outside the guarantees. Shared state between tests and reflection that builds names from data can break the assumptions of Section 3.4, as in toolz#632. GATE does not compare failures against the merge base, so a suite that already fails escalates every change that selects the failing test.

*Internal validity.* The selector was refined on the development projects, held-out mutants also drove fixes to the coverage-based core, and half of the projects of the replay are development projects. Only batch 3 ({{selHoldFreshKilled}} failing mutants of three projects) is untouched, and only for the static core. The fresh replay used an earlier selector, before the rules for docstrings and doctest files, and applied the rule for removed tests to its diffs afterwards; the final selector, used in the stored replay, changed the selection of {{dvFinalChanged}} of {{dvFinalPRs}} pull requests, never selecting fewer tests. The labels of Section 5.2 were made with an AI assistant, and each label and its evidence are in the repository.

*External validity.* All projects are Python libraries with pytest suites. Mutants are single-site changes, which favour name-level analysis, and SWE-bench is evaluated statically. Only {{dvHard}} of the {{shRegress}} real regressions depended on the selection, so the upper bound on misses ({{shUpper}}%) is wide, and bot pull requests were not separated. Only {{selHoldRan}} of {{selHoldAttempted}} held-out projects could be set up, and {{shNotRun}} pull requests could not run, mostly in one project (transitions).

*Not studied.* We did not measure how developers respond to escalations or to the triage report; the replay posts nothing, and the platform deployment gated only our test repository. The requirements of Section 1 come from our own practice, not from a survey. The model studies use our prompts, demo services, and the models of Section 4, and times are estimated from per-test durations.

## 8. Conclusion

GATE shows that a merge gate can use test selection and language models without approving on silence. On {{selMutKilled}} + {{selHoldKilled}} failing mutants of {{shProjects}} projects, {{selSweUsable}} real defects, and {{shRegress}} regressions in {{shRan}} real pull requests it missed no failure that a full test run showed, including all {{abPlainTotal}} cases in which its selection had omitted the failing test, while pytest-testmon missed {{selTmMissK}} of {{selTmKilled}} on the same mutants. It ran {{dvSameStaticPct}}% to {{dvSameStoredPct}}% fewer tests and sent {{dvRevGreenPct}}% of the passing pull requests to a person. The rule that models propose and a deterministic rule decides, and the habit of treating a missing signal as a failure, apply to any pipeline in which a model sits beside a decision. Future work includes a study of developers' response to escalations, a survey of practitioners' requirements, and other languages.

