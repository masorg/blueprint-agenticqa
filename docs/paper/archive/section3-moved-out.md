# Moved out of Section 3 (2026-10-16)

These belong to other sections: problem setting and threat model, related work, and the introduction.

## From the old Section 3 opening (relation to existing practice, Table 2, design principles)

**How GATE relates to existing practice.** GATE is a composition. Most of its parts exist in some form: test pyramids [7, 8], property-based testing [11, 12], required CI checks, containers, and human review. We take these, change them where a gate with models in it needs it, and add what is missing. Table 2 shows, for each part, what we took, what we changed, and what is new.

**Table 2.** Where each part of GATE comes from.

| Part | Existing basis | Our change | What is new |
|---|---|---|---|
| Tiered tests | Test pyramids built on the scope of what is tested [7, 8]; property-based tests find extra bugs [11, 12] | Layers are defined by the kind of failure they find. We add a property layer and two agent-only layers (trajectory, LLM-judge) | Nothing in the idea; we measure it (Section 5.2) |
| Advised plans | Models used to route and classify; keyword rules | The router and the classifier only advise. Each has a rule fallback, and the classifier is checked against file-name cues | The plan can never remove a test (principle P2) |
| Isolated execution | Virtual environments and containers | One environment per dependency set; a throwaway container per untrusted change | Nothing in the idea; we report where it fails (Section 5.7) |
| Rule-decided verdicts | A CI check that fails when any test fails; pass thresholds on judge scores [7] | The result is computed over the active layers: those the router chose plus every layer that holds a test | Three guarantees (I1 to I3) with a short argument, and an attack that tries to break them (Section 5.1) |
| Gated merges | Required status checks on a pull request | The verdict is published as a status, set again after each run | Nothing in the idea |
| Escalation to people | Required reviews; LLM reviewers that can be talked into approving [2] | A flagged change is resolved by a comment, checked against the commenter's repository access each time | Neither a comment nor the pull-request text can grant approval |

What is new is mainly the join. The verdict is computed over the layers that actually ran, so a model can advise the plan but cannot change the outcome. We also test that claim by attack, instead of assuming it. Section 3.1 states the setting and the assumptions, and Section 3.2 walks through the procedure. Five design principles run through all the parts.

- **P1. Models advise; a rule decides.** A model may suggest a plan and write an explanation. It never sets the result.
- **P2. Never skip a test.** Every test that is found runs, whatever the plan says.
- **P3. Fail closed.** A model failure falls back to a rule. A project whose tests cannot be loaded gives an error, not an approval.
- **P4. Authority comes from identity, not text.** Approval depends on the commenter's access to the repository. It never depends on what a comment or a pull-request description says.
- **P5. Untrusted code runs in isolation.** The framework never runs a project's tests inside its own process.

## Old Section 3.1 (setting and assumptions A1 to A5)

### 3.1 Setting and assumptions

GATE judges one change $c$ to a project $P$, such as a pull request. The project has a test suite. GATE answers one question, whether $c$ is safe to merge, with a verdict $V \in \{\texttt{auto\_approve}, \texttt{needs\_human\_review}\}$. We make five assumptions.

- **A1. Tests are the evidence.** The project's tests are the only evidence about the change. GATE does not judge the tests themselves, so a change that deletes or weakens tests can pass. GATE is only as strong as the tests it runs, like a plain CI check.
- **A2. Models are fallible and can be manipulated.** A router, classifier, or note writer can be wrong, can fail, and can be steered by text in the change. We assume nothing about its answers. We do assume that it cannot change test results or the verdict rule.
- **A3. Text in the change is untrusted.** The commit message, the description, comments in the diff, and comments on the pull request may be written by an attacker [2]. They never count as evidence or as authority.
- **A4. Test code is untrusted.** The tests of a change can run any code, so they run in isolation (Section 3.6).
- **A5. A test's outcome does not depend on the layer that runs it.** There are no flaky tests and no layer-specific setup. The guarantees in Section 3.5 rely on this.

## Old Section 3.9 deployment and production text (for a deployment or evaluation section)

On CAI it runs as one application with one worker. An organisation-level GitHub webhook sends pull-request and comment events. The application fetches the diff, runs the plan, sets the status, and handles `/approve`. We tested this from start to end on a public GitHub repository. The two bugs from Section 5.1 were present in the first deployed version. [AUTHOR: confirm which version is deployed now, and whether it has the fixes from Section 3.4.]

All numbers in this paper come from demo services that we wrote and from public open-source projects. **We did not use, and did not have, any customer data.** So the CAI result shows that the system works. It is not a statistical result. Timings were measured on a laptop (macOS, Python 3.14), not on CAI. [AUTHOR: add CAI timings if you have them.]

**What went wrong in production.** These problems were not obvious on CAI, and they may happen on other managed platforms too. (The third was found while designing, before it broke anything.)

1. **Platform defaults can break a sandbox.** CAI sets `PIP_USER=1`, which does not work when installing into a virtual environment. Every run failed on CAI and none failed locally. The executor now builds its environment from an allow-list.
2. **A login proxy can swallow webhooks.** Webhook calls without a login were redirected to a login page. Senders treat this as success. The integration looked fine but received nothing. Two settings are needed: one for the workspace and one for the application.
3. **The webhook level decides which events you get.** Enterprise-level GitHub webhooks never send `pull_request` events. The organisation level is the smallest one that does. This decided our design.
4. **A web framework changed the case of an attribute.** The attribute `viewBox` became `viewbox`, which hid two of the five pyramid layers on screen. We only saw the cause in the live page.
5. **"Tested end to end" has a date.** After about three weeks without use, the application's address stopped working, with no error from the application.

On the flows we tried, the system behaved correctly in production. [AUTHOR: add anything quantitative you can share about the CAI deployment.]
