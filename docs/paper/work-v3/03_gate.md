## 3. The GATE Framework

GATE (Guarded Approval from Test Evidence) decides whether the tests of a project show a change safe to merge. It uses the tests that the project holds at the changed commit, whether people or agents wrote them, and applies to projects with or without agentic components, including hybrid ones. GATE has two parts (Figure 1): a *test pyramid*, which groups the tests into five layers by the class of failure they target, and a *review pipeline* of five stages:

1. **Select and plan.** A deterministic selector chooses the tests that must run. A router and a classifier, which may be language models, decide which layers the change calls for and which layer each test belongs to. A test advisor, which may also be a model, can add tests and cannot remove any.
2. **Execute.** An isolated executor runs the selected tests layer by layer.
3. **Verdict rule.** A fixed rule maps the outcomes, whether every changed file is reached by a selected test, and whether the change removes tests, to `auto_approve` or `needs_human_review`. No model takes part.
4. **Status.** The verdict is published as a commit status, which a repository can require before merging.
5. **Escalation.** A flagged change goes to a reviewer with a triage report, which a model may draft; the reviewer resolves it with `/approve` or `/reject`.

![Figure 1. Overview of GATE. A change passes through the five stages of the review pipeline from left to right. The test pyramid is drawn inside the stage that runs the tests, because the plan, the execution, and the verdict rule all work on its layers. Dashed boxes are stages in which a language model may take part, and solid boxes are fixed rules.](../figures/f0_framework.png)

Formally, GATE maps a change $c = (\Delta, m)$, a diff $\Delta$ with its description $m$ (commit message and pull-request text), of a project $P$ to a verdict $V$:
$$\begin{aligned} T &= \mathrm{discover}(P_c), & E &= \mathrm{select}(c, T), & \pi &= \mathrm{plan}(c, E),\\ o &= \mathrm{execute}(E, \pi), & V &= \mathrm{decide}(o, c). \end{aligned}$$
$P_c$ is the project after the diff, $T$ its discovered tests, and $E \subseteq T$ the selected tests. The plan $\pi = (R, \lambda)$ consists of the layers $R \subseteq L$ that the change calls for and a labelling $\lambda : E \to L$ onto the layers $L = (\ell_1, \dots, \ell_5)$ of the pyramid. Each selected test gets an outcome $o(t) \in \{\text{pass}, \text{fail}, \text{error}\}$; a test without a result, or whose run is cut short by a limit, is an error, and a layer is *bad* if one of its tests fails or errors. Algorithm 1 gives the procedure; the number before a step names its stage.

```{=latex}
\begin{algorithm}[!ht]
\caption{GATE on a change $c = (\Delta, m)$ of a project $P$}
\begin{algorithmic}[1]
\State check out $P_c$ and discover the tests $T$ \Comment{error if this fails}
\State \textbf{(1)} $E_0 \gets \textsc{Core}(\Delta, T)$ \Comment{all of $T$ if the analysis fails}
\State \textbf{(1)} $E \gets E_0 \cup (\textsc{Advise}(c, T) \cap T)$
\State \textbf{(1)} $R \gets \textsc{Router}(\Delta, m)$;\quad $\lambda_0 \gets \textsc{Classifier}(E)$
\State \textbf{(1)} $\lambda \gets \textsc{Reconcile}(\lambda_0, E, R)$
\For{each layer $\ell \in L$ in pyramid order}
  \State \textbf{(2)} execute the tests $\lambda^{-1}(\ell)$ in isolation; record $o(t)$
\EndFor
\State \textbf{(3)} $A \gets R \cup \lambda(E)$;\quad $U \gets \textsc{Unreached}(\Delta, E_0)$;\quad $W \gets \textsc{Weakened}(\Delta)$
\State \textbf{(3)} $V \gets$ \texttt{needs\_human\_review} \textbf{if} some layer in $A$ is bad \textbf{or} $U \cup W \neq \emptyset$ \textbf{else} \texttt{auto\_approve}
\State \textbf{(4)} publish $V$ as the status of $c$
\State \textbf{(5)} $n \gets \textsc{Triage}(c, o)$; post $n$ on $c$
\If{$V$ is \texttt{needs\_human\_review}}
  \State \textbf{(5)} wait for \texttt{/approve} or \texttt{/reject} from a user with write access
\EndIf
\end{algorithmic}
\end{algorithm}
```

If a model fails or returns unusable output, a rule takes its place; if the tests cannot be discovered, the run ends in `error`; if the selection cannot be computed, every test runs. None of these produces an approval that a full run would not.

*Example.* In a tool-calling support assistant, a change to the function in `planner.py` that orders tool calls selects only the two tests that mention it; the trajectory test fails, and the change goes to a reviewer with a report naming it. Had the change edited a function of `refund.py` that no test calls, nothing would fail, but $U(c)$ would hold `refund.py`, and the change would still go to a reviewer, where a plain check would be green.

### 3.1 Test Pyramid

The pyramid groups the tests of a project into five layers, each defined by the class of failure it targets rather than by the size of the unit under test (Table I). A layer is a triple of a failure class, the projects it applies to (all, or only those with agentic components), and *cues* matched against a test's path, name, and markers; a test that matches several layers goes to the one nearest the apex, and a test that matches none is a unit test. A conventional project is judged on three layers and a project with agentic components, including a hybrid one, on five. The layers structure the plan and the report. They never change which tests run, and they enter the verdict only through the rule that a failing test fails its layer.

**Table 1.** The layers of the GATE pyramid, from the base to the apex. The cues are matched against the path, name, and markers of a test.

| Layer | Failure class $\varphi$ | Applies to $\alpha$ | Cues $\psi$ |
|---|---|---|---|
| Unit | Incorrect results for individual inputs | all projects | none (default) |
| Property | Violated invariants over generated inputs or sequences of steps | all projects | property, hypothesis, invariant |
| Trajectory | Wrong tools, or tools called in the wrong order | agentic components | trajectory, tool_sequence, toolseq, agent_trace |
| LLM-judge | Answers not supported by the evidence they were given | agentic components | llm_judge, llm_as_judge, judge, rubric |
| Adversarial | Failures on invalid or hostile input and at limits | all projects | adversarial, security, fuzz, malicious, hostile, injection |

### 3.2 Selection

Selection uses no model. A test file *reaches* every file it imports, directly or transitively, and the `conftest.py` files above it. The *core* $E_0$ is the set of tests that the change can affect; the static core computes it in two steps.

1. *File level.* A test is in the core if its file is changed or reaches a changed file. This is safe but coarse: a package whose `__init__` imports every module makes every test reach every file.
2. *Name level.* For a change inside a function body, the *affected names* are the function and, transitively, every definition whose body mentions an affected name. A test function stays in the core only if it reaches the changed file and mentions an affected name, in its body, decorators, or parameters (and so its fixtures), or in code it shares with other tests (class set-up, module-level code, autouse fixtures, and pytest hooks).

Changes that cannot be expressed as names stay at file level: import-time code, decorators, non-literal defaults, class bodies, unparsable files, and functions called by a string (a dictionary filled at import time, or a prefix passed to `getattr`). Computed imports, loading by path, and started processes reach only modules named by string literals, followed at file level.

*Coverage-based core.* Where a stored recording of the suite exists, made under coverage with per-test contexts, the core of a change inside function bodies is the set of tests that executed those bodies. Three cases keep the static rules, because coverage cannot see what matters there: a body that also ran outside any test (at import or collection), a decorated function, which a decorator can cache or wrap, and code run in another process.

The selection fails closed. An empty or unreadable diff, a change to build or test configuration (`pyproject.toml`, `requirements.txt`, `pytest.ini`), and any error in the analysis select every test. Documentation, images, comments, and docstrings are *inert* and select nothing, unless the runner collects them as doctests.

*Unreached and weakened changes.* Reach is decided per changed file by the rule that built the core: a test of $E_0$ must import the file (file level), mention an affected name of its change (name level), or have executed a changed body (coverage). The changed files that no test of $E_0$ reaches form $U(c)$, the *unreached* part of the change, which no test shows safe; in a project without tests, every changed file that is not inert is unreached. A *scope policy* exempts files that do not ship, by default those under documentation, example, script, benchmark, and tooling directories and data files that no code names; they are reported but do not enter $U(c)$, so an organisation that ships such files must narrow the policy. $U(c)$ depends on the core alone, so advice cannot shrink it. A change can also pass by removing the test that would fail, so $W(c)$ records how the diff takes evidence away: test files deleted, test functions removed, skip or expected-failure markers added, assertions removed, and pytest hooks or options that rewrite or deselect results.

A *test advisor*, a model or a rule matching test names to changed files, may name further tests, for example one that reaches the change through a subprocess or a plugin. Its answer is intersected with the discovered tests and added: $E = E_0 \cup (\mathrm{advice}(c) \cap T)$.

### 3.3 Plan and Execution

A plan $\pi = (R, \lambda)$ states which layers the change calls for and which layer each selected test belongs to. It neither writes nor removes tests. The *router* produces $R$ from the diff, the description, and the repository name: it decides whether the project has agentic components (calls language models or tools, builds prompts, or plans over several steps) and which layers apply; a guard discards agentic layers for a project without such components. The *classifier* labels each selected test from its name, file, and markers. A keyword search over the diff and the cued layer replace the router and the classifier whenever a model fails or returns unusable output, or when no model is configured. A *reconcile* step then gives every selected test exactly one layer: a test that the classifier left out gets its cued layer, names outside $E$ are dropped, and a label outside $R$ is moved to the cued layer or to the unit layer. No selected test is removed at any point.

The tests of a change are part of the change under review, so an *execution policy* fixes the runner, the environment, the limits, and the isolation level. The executor builds one environment per set of declared dependencies and runs each layer in a separate process, under limits and without GATE's credentials; a stricter policy runs everything in a throwaway container with reduced privileges.

### 3.4 Verdict Rule

A layer is *active* if the router selected it or if at least one selected test is labelled with it:
$$A = R \cup \{\lambda(t) : t \in E\}.$$
The verdict rule then maps the outcomes, the unreached part of the change, and the evidence it removes to the verdict:
$$V = \begin{cases} \texttt{needs\_human\_review} & \text{if some layer in } A \text{ is bad, or } U(c) \cup W(c) \neq \emptyset,\\ \texttt{auto\_approve} & \text{otherwise.}\end{cases}$$
A change is approved only if every selected test passes, every changed file that is neither inert nor exempted by the scope policy is reached by one, and no test is taken away. A selected test that already failed before the change also flags it; GATE does not compare against the merge base. The rule is the same for every change and does not consult a model.

Let $V_0$ be the verdict that GATE would give if it ran only the core $E_0$ and activated every layer. We assume (A1) that the outcome of a test does not depend on its layer or on the other tests in the run, and (A2) that a run that does not finish yields an error.

**Proposition 1 (Models cannot weaken the verdict).** Under A1 and A2, for every router, classifier, and advisor, (M1) $E_0 \subseteq E$, (M2) if $V_0$ is `needs_human_review` then so is $V$, and (M3) the labelling $\lambda$ does not change which tests run.

*Proof.* The advice is intersected with $T$ and united with $E_0$ (M1), and the outputs of the router and the classifier never enter $E$ (M3). If a test of $E_0$ fails or errors in $V_0$, it is in $E$, fails or errors there too by A1 and A2, and makes its layer $\lambda(t) \in A$ bad. $U(c)$ and $W(c)$ depend on $\Delta$ and the core alone, so they are the same. $\square$

Because $A$ contains every layer that holds a test, a hostile router cannot hide a failure. A1 fails when tests share state, and an advisor's extra tests could then mask a failure; the attacks of Section 5.3 probe this case, and it lies outside the proof. Two further properties hold by construction. *No approval on silence:* an approved change passed every selected test, and $U(c) = W(c) = \emptyset$. *No missed failure under safe selection:* if every test whose outcome can change is in $E_0$, a newly failing test flags $c$. The second property is only as good as the dependencies behind the core: reflection, shared state, and external services can break them, and one real pull request did (toolz#632, Section 5.2). Hostile test code, a hostile environment, and the triage report are outside these guarantees.

### 3.5 Status and Escalation

GATE publishes $V$ as a commit status: `pending` while a run is in progress or waits for a reviewer, `success` when $V$ is `auto_approve` or a reviewer approves, `failure` when a reviewer rejects, and `error` when a run fails. A reviewer resolves a flagged change by commenting `/approve` or `/reject`. GATE accepts a comment only from a user whose repository role is `admin`, `maintain`, or `write`, as reported by GitHub; no text in a comment, description, or diff confers authority, and every webhook is authenticated by its signature. A *triage report* tells the reviewer which layers ran, which tests failed, and which changed files no test reaches; a model may draft it after $V$ is fixed, and a template replaces the model if it fails.

### 3.6 Implementation and Extension

The selector, router, classifier, advisor, triage writer, executor, and test runner are interfaces, and every model-backed implementation has a rule-based counterpart. The reference implementation is a Python package (`agenticqa`) with pytest as its runner, pytest-cov for recordings, and Docker for isolation (non-root, no capabilities, read-only file system, limits on CPU, memory, and processes). A command line or a GitHub integration drives it: the integration receives pull-request and comment webhooks, runs the pipeline, sets the status, and processes approvals. Settings choose the selected or the full run and name a stored coverage recording, with a fallback to the static core. An organisation adapts GATE by changing a layer, replacing a model through its interface, choosing the execution policy, and setting the approving roles. An earlier version, with the pipeline, the models, and the GitHub integration but before the selector, was deployed as an application on Cloudera AI and gated pull requests of our own test repository end to end (Section 6).

### 3.7 Use in an Organisation

Table II maps the requirements of Section 1 to GATE's mechanisms and to the evidence for them. Adoption follows three modes, which the GitHub integration implements. In *shadow* mode GATE logs its decisions for comparison with the organisation's CI, as Section 5.2 does on public history; in *advisory* mode it posts a status that is not required; in *enforce* mode the status gates the merge. The organisation sets the roles that may approve, the scope policy, and whether to use a stored recording.

**Table 2.** Requirements that we derive for an organisation adopting coding agents (Section 1), and how GATE meets them.

| Requirement | Mechanism | Evidence |
|---|---|---|
| R1 Throughput and safety of selection | Static or coverage-based selection; inert changes need no test | {{dvSameStaticPct}}% (static) and {{dvSameStoredPct}}% (stored) fewer tests on the same real pull requests; {{abPlainTotal}} selection misses caught as unreached (5.1, 5.2) |
| R2 Accountability | A fixed verdict rule; approval only by repository permission; recorded reasons and triage report; code review policy unchanged | No model takes part in the verdict (3.4); every escalation names its reason |
| R3 Safety against the change | Changes that remove or weaken tests go to a person; text never grants authority; isolated execution | {{lbWN}} test-removal flags, each reviewable: {{lbWReal}} real weakenings, {{lbWFP}} false positives, mostly one missing exemption for skips on new tests (5.2); misleading text changed no verdict (5.3) |
| R4 Model choice | Every model has a rule-based counterpart; models only add; the selector and the verdict use no model | Plain rules gave the same verdicts as four models (5.3); all safety experiments ran with no model |
| R5 Gradual adoption | Shadow, advisory, and enforce modes | The shadow replay (5.2) is the first step an organisation can run on its own history |

