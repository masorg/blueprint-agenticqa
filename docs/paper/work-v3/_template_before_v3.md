<!--
DRAFT for CAIN 2027, AI Engineering in Practice track (10+2 pages, IEEE template).
Every double-brace placeholder is filled from the committed evidence by build_draft.py
(numbers come from docs/figures/numbers.tex and eval/reports/*.json). Do not type numbers here.
Items in [AUTHOR: ...] need a decision or a fact only the authors have.
-->

# GATE: A Framework for Gated Assurance with Tiered Evidence in Pull-Request Review

**Authors:** [AUTHOR: names, affiliations, emails]

## Abstract

AI coding agents open pull requests faster than people can review them, so merging has become the bottleneck. Handing the decision to a language model is unsafe, and running the whole test suite for every change wastes the evidence that the change points to. We present GATE (Gated Assurance with Tiered Evidence), a framework that decides whether a pull request can be merged on the evidence of the project's own tests. A deterministic static analysis selects, per test function, the tests that a change can affect and falls back to the full suite whenever it is unsure. Language models may add tests and can never remove one, and a change that no test reaches goes to a person instead of being approved. The tests are organised in a pyramid whose layers are defined by the kind of failure they find, two of them for agentic components, and a fixed rule computes the verdict from their outcomes, so that no model output can change it. On {{selMutValid}} mutants of {{selMutProjectsRan}} real projects, selection missed {{selMissNameK}} of {{selMutKilled}} failures that the full suite showed and ran {{selSavedNamePct}}% fewer tests, with savings from {{selMinSavedPct}}% to {{selMaxSavedPct}}% across projects, and missed {{selHoldMissK}} of {{selHoldKilled}} on {{selHoldRan}} projects that were held out from its development. It caught {{selSweCaughtK}} of {{selSweUsable}} real defects from SWE-bench Verified. Of {{selAidevN}} real agent pull requests, {{selAidevInertPct}}% change only files that cannot affect a test and {{selAidevConfigPct}}% force a full run. Of {{selFieldAttempted}} examined in detail, GATE would run {{selFieldSavedPct}}% fewer tests and send {{selFieldReview}} to a person because no test reaches the change. Hostile routers, classifiers, and advisors weakened no verdict in {{selAtkCases}} advisor cases and {{advCasesAfter}} router and classifier cases. The verdict equals that of a plain CI check on the tests it runs, and the contribution is the selection, its safe interface to models, and the structure around the verdict.

**Index Terms:** pull-request review, test selection, test pyramid, LLM agents, AI engineering

---

## 1. Introduction

AI coding agents have changed how many pull requests are opened. The AIDev dataset holds over 456,000 pull requests from five agents, and one developer in it submitted as many pull requests in three days as in the previous three years [1]. GitHub reports 43.2 million merged pull requests per month in 2025, 23% more than in 2024, and more than one million pull requests created by its Copilot coding agent between May and September 2025 [2]. In telemetry from over 10,000 developers, teams with high AI adoption merged 98% more pull requests [3].

Producing a change is no longer the bottleneck. Merging it is. The same telemetry shows that pull requests grew by 154% in size and that review time grew by 91%, and it names human approval as the limit on throughput [3]. A vision paper on agentic code review describes review as "a growing bottleneck", because AI tools increase the volume of code that needs review [4]. Pull requests from agents are accepted less often than those from people [1]. In a manual study of agent pull requests, only 35.7% of the rejected ones reflected a clear agent failure, and 31.2% were rejected for process reasons [5]. Every one of these changes needs evidence that it is safe to merge, and producing that evidence has not become cheaper at the pace at which changes are produced.

Language models are the obvious tool for reviewing at this volume. The evidence on handing them the decision is not encouraging. In 1.02 million reviewed pull requests from 207 projects, review started by agents was decided sooner, and the speed did not come with better review quality [6]. Review agents can be talked into approving unsafe code by the text of a pull request [7]. People are not a sufficient safeguard either: reviewers approved AI-written code more often as they reviewed more of it, from 30.1% to 36.8% over 11,429 reviews [8]. The merge decision therefore needs evidence that does not depend on the author's account of the change, on a model's reading of it, or on the attention of a tired reviewer.

Tests are such evidence, and a continuous-integration check is the usual way to use them. Three gaps remain, and all three widen for agentic software. First, a check runs the whole suite for every change, whatever the change touches, although the change itself points to the tests that can be affected. Selecting them is a long-studied problem [9 to 12], but the answer of a selection tool is a list, and a list that is empty or wrong turns into a green check. Second, the classic test pyramid separates tests by the size of the unit under test. It has no place for failures that are specific to agent systems, such as tools called in the wrong order or answers that the evidence does not support, and it does not separate failures on hostile input from other failures. The pyramid has to evolve, so that its layers are defined by the kind of failure that they find. Third, the outcome has to be handled: it has to be clear which kind of failure occurred, whether a model may influence the decision, and when a person has to look. A plain check returns one red or green mark and leaves these questions open.

This paper presents GATE (Gated Assurance with Tiered Evidence), a framework that addresses the three gaps. GATE is a composition. It takes test selection, test pyramids, property-based testing, required status checks, containers, and human review from existing practice, changes them where a gate with models in it needs a change, and adds the parts that connect them (Section 2). It consists of a test pyramid with five layers and a review pipeline of five stages. A deterministic selector chooses from the diff the tests that must run and fails closed to the full suite. Models, which may be routers, classifiers, or advisors, can add tests and label them and can never remove one. The tests run in isolation. A change that no test reaches is not approved. A fixed rule computes the verdict from the outcomes, the verdict is published as a status on the pull request, and a flagged change goes to a reviewer, who is supported by a triage report. Models advise, and a rule decides.

The contributions are the following.

1. **A framework for gating pull requests on selected, layered evidence (Section 3).** It gives formal definitions of the layer, the selection, the plan, and the verdict rule, and two propositions: no router, classifier, or advisor can weaken the verdict, and under a stated assumption no failure introduced by a change is missed.
2. **A test selection for Python with a model-safe interface (Section 3.3).** A static core at file, name, and test level fails closed; models only add; a change that no test reaches goes to a person. On {{selMutValid}} mutants of {{selMutProjectsRan}} real projects it missed {{selMissNameK}} of {{selMutKilled}} failures (95% upper bound {{selMissNameUpper}}%).
3. **Measurements on real changes and held-out projects (Sections 5.1 and 5.2).** On {{selHoldRan}} projects held out from the selector's development it missed {{selHoldMissK}} of {{selHoldKilled}} failures, after the first held-out run exposed a bug that we report. On {{selSweUsable}} real defects from SWE-bench Verified it caught {{selSweCaughtK}}, and a comparison with pytest-testmon on the same mutants shows that a dynamic tool runs fewer tests ({{selTmSharePct}}% against {{selTmGateSharePct}}%) and needs a recorded baseline. On mutants the selected tests ran {{selShareMeanNamePct}}% of a suite on average. Of {{selFieldAttempted}} real agent pull requests, {{selFieldInertOnly}} need no test, {{selFieldFullRun}} force a full run, and {{selFieldReview}} change code that no test reaches, which a plain check would report as green.
4. **Attack-based tests of the model-facing parts (Section 5.3).** Hostile routers, classifiers, and advisors found two bugs in the first version that let a failure through. After the fixes, nothing broke in {{advCasesAfter}} cases on two demo services and in {{realCases}} cases on {{realSuitesRan}} real projects, and {{selAtkCases}} hostile-advisor cases broke neither guarantee. A control with the fixes removed shows that the attack can find the bugs.
5. **Evidence on layers, on what models add, and on a field study (Sections 5.4 to 5.6).** On {{mutValid}} artificial bugs, all layers found {{mutRateFull}}%, against {{mutRateUnit}}% for unit tests alone. Models matched plain rules on the verdict. Of {{fieldAttempted}} real agent-written pull requests, automatic environment setup allowed {{fieldRan}} to be evaluated, and none of the failures was approved. GATE is deployed on Cloudera AI.

The scope of the claims is limited in two ways. The verdict equals that of a conventional check on the tests that run, so GATE finds no more bugs than the tests it runs (Sections 3.5 and 6). The static selection is sound under an assumption that reflection and names read from data can break, and its savings depend on how a project is structured.

---

## 2. Related Work

**Review load and LLM reviewers.** Agent-written pull requests are numerous [1, 2] and make review a growing bottleneck [3, 4]. Zhong et al. [6] study over a million reviewed pull requests and find that agents speed up decisions without improving review quality. SEVRA-BENCH [7] shows that review agents can be manipulated by pull-request text, and Kumar et al. [13] warn that artificial benchmarks flatter models. Two studies of human reviewers [5, 8] show that people alone are not a safe backstop. GATE treats the pull-request text as something an attacker may write.

**Regression test selection.** Choosing the tests that a change can affect is a long-studied problem. Safe techniques select every test whose behaviour can change [9]. Ekstazi tracks dynamic file dependencies [10], and Legunsen et al. compare static techniques [11]. Industrial systems trade safety for cost with models trained on history. Predictive Test Selection at Facebook halves the testing infrastructure cost and still reports more than 99.9% of faulty changes [14], and Google describes continuous testing at scale [15]. For Python, NameRTS builds a graph of code names and selects all affected tests for 99.6% of commits while skipping 69.9% of test files on average [12]. pytest-testmon records dependencies at run time, and Section 5.1 compares it with GATE on the same mutants [16]. In the agent era, TDAD gives a test-impact map to the coding agent and lowers regressions from 6.08% to 1.82% on SWE-bench Verified with small open models [17], and CASPER groups the tests of an LLM system into slices and ranks the slices by their chance of regressing after a prompt or model change [18]. GATE's selector belongs to the family of static, safe selection with name-level analysis. It claims no better algorithm.

**Testing LLM and agent systems.** Two recent papers propose test pyramids for these systems. Badagi et al. [19] propose a five-layer pyramid defined by the scope of what is tested, from infrastructure to business outcome, require every prompt to be re-tested after a model change, and gate deployment with thresholds on scores from LLM judges. It is a strategy with illustrative examples and no measurement. Kohl et al. [20] apply the unit, integration, and acceptance pyramid to LLM-based agents, make tests deterministic with scripted model replies and recorded traces, report two detailed case studies, and state that a large-scale study is needed. Both gate a pipeline automatically. Neither selects tests for a change or asks a person to approve one. GATE defines its layers by *the kind of failure they find*, includes a property-based layer, and measures the design.

**Test generation and quality.** Many papers study how to make agents write tests [21 to 25]. This is a different problem, since GATE uses the tests that a project already has. Generated tests often run but are shallow or flaky [26, 27], and mixing property-based with example-based tests finds more bugs [28, 29], which is one reason for several layers. Work on judging agent trajectories [30, 31] relates to the trajectory layer.

**Where the parts come from.** GATE combines existing ideas. Table 1 states, for each part, what exists, what GATE changes, and what is new.

**Table 1.** Where each part of GATE comes from.

| Part | Existing basis | Change | New |
|---|---|---|---|
| Test pyramid | Pyramids built on the scope of what is tested [19, 20]; property-based tests find extra bugs [28, 29] | Layers are defined by the kind of failure. A property layer and two agentic layers are added | Nothing in the idea; the design is measured |
| Selection | Safe, static and dynamic test selection [9 to 12] | File-level and name-level analysis of Python imports; any doubt selects all tests | A change that no test reaches is not approved (the uncovered rule); models may add tests and never remove one |
| Plan | Models used to route and classify; keyword rules | Router and classifier only advise. Each has a rule fallback, and the classifier is checked against file-name cues | The plan acts only on the selected tests |
| Execute | Virtual environments and containers | One environment per dependency set; an isolation level set by policy | Nothing in the idea |
| Verdict rule | A CI check that fails when any test fails; pass thresholds on judge scores [19] | The verdict is computed over the selected tests and the uncovered part of the change | Two propositions with short proofs, and attacks that try to break them |
| Status and escalation | Required status checks and reviews; LLM reviewers that can be talked into approving [7] | A comment resolves a flagged change only after the commenter's repository permission is checked | Neither a comment nor the pull-request text can grant approval |

**How GATE differs.** The works above supply parts of GATE, and none supplies the whole. Test-selection techniques [9 to 12, 14 to 18] contain no model, return an empty set when nothing is affected, and leave the merge decision to a CI check that is green when nothing ran. The LLM-based works use a model to inform an agent [17] or to rank tests [18], not to extend a safe core. The pyramid papers [19, 20] provide layers, not selection or approval. LLM reviewers [6, 7] let a model decide. GATE contributes the composition and its tested safety. A deterministic core fixes the tests that must run. Models may add tests and cannot remove any (Proposition 1). A change that no test reaches goes to a person, where a plain CI check would turn green. A fixed rule turns the outcomes into a verdict that is published as a status and resolved by a person with repository permission. The evaluation measures the selection against the full suite on mutants of real projects and on real agent-written pull requests (Section 5).

---

## 3. The GATE Framework

GATE (Gated Assurance with Tiered Evidence) decides whether a change to a software project can be merged, using the project's own tests as evidence. It applies to projects with or without agentic components, including hybrid workflows in which conventional code and agent components are combined. GATE does not write tests. It uses the tests that the project holds at the changed commit, whether people or agents wrote them. GATE has two parts (Figure 1): a *test pyramid*, which organises the tests of a project into five layers that each target one class of failure, and a *review pipeline*, through which every change passes in five stages:

1. **Select and plan.** A selector chooses from the diff the tests that must run. A router and a classifier, which may be language models, decide which layers the change calls for and which layer each test belongs to. A test advisor, which may also be a model, can add tests to the selection and cannot remove any.
2. **Execute.** An isolated executor runs the selected tests layer by layer, in a separate process or in a throwaway container.
3. **Verdict rule.** A fixed rule maps the test outcomes, and the coverage of the change by tests, to one of two verdicts, `auto_approve` or `needs_human_review`. No model takes part.
4. **Pull-request status.** The verdict is published on the pull request as a commit status, which a repository can require before merging.
5. **Escalation.** A flagged change goes to a reviewer together with a triage report, which a language model may draft, and the reviewer resolves it with `/approve` or `/reject`.

The layers and the order of the stages are fixed. The selection and the plan are what vary from change to change.

![Figure 1. Overview of GATE. A change passes through the five stages of the review pipeline from left to right. The test pyramid is drawn inside the stage that runs the tests, because the plan, the execution, and the verdict rule all work on its layers. Dashed boxes are stages in which a language model may take part, and solid boxes are fixed rules.](../figures/f0_framework.png)

Formally, GATE maps a change $c$ of a project $P$ to a verdict $V$ in five steps, each of which uses the result of the one before:
$$\begin{aligned} T &= \mathrm{discover}(P_c), & E &= \mathrm{select}(c, T), & \pi &= \mathrm{plan}(c, E),\\ o &= \mathrm{execute}(E, \pi), & V &= \mathrm{decide}(o, c). \end{aligned}$$
Here $c = (\Delta, m)$ consists of a diff $\Delta$ and its description $m$, which is the commit message and the pull-request text, and $P_c = P \oplus \Delta$ is the project after the diff is applied. The set $T$ contains the tests discovered in $P_c$, and $E \subseteq T$ is the set of tests selected to run. The plan $\pi = (R, \lambda)$ has two parts: a set $R \subseteq L$ of layers that the change calls for, and a labelling $\lambda : E \to L$ that assigns every selected test to one layer of the pyramid $L = (\ell_1, \dots, \ell_5)$, ordered from the base to the apex. The function $o$ assigns each selected test $t$ an outcome $o(t) \in \{\text{pass}, \text{fail}, \text{error}\}$, and a layer is *bad* if at least one of its tests fails or errors. The verdict is $V \in \{\texttt{auto\_approve}, \texttt{needs\_human\_review}\}$. The functions $\mathrm{select}$ and $\mathrm{plan}$ are stage 1, $\mathrm{execute}$ is stage 2, and $\mathrm{decide}$ is stage 3. Stages 4 and 5 act on the result: they publish $V$ and hand a flagged change to a reviewer.

### 3.1 Workflow

A change passes through the stages in the order of their numbers. GATE first discovers the tests of the changed project, selects the tests that the change can affect, and plans (stage 1). It then executes the selected tests layer by layer (stage 2), applies the verdict rule (stage 3), publishes the verdict as a status on the pull request (stage 4), and, if the verdict asks for a review, escalates the change to a reviewer (stage 5). Algorithm 1 gives the procedure, and the number in parentheses before a step names its stage. The triage report $n$ describes the outcome and cannot change $V$.

```{=latex}
\begin{algorithm}[!ht]
\caption{GATE on a change $c = (\Delta, m)$ of a project $P$}
\begin{algorithmic}[1]
\State check out $P_c$ and discover the tests $T$ \Comment{error if this fails}
\State \textbf{(1)} $E \gets \textsc{Core}(\Delta, T)$ \Comment{all of $T$ if the analysis fails}
\State \textbf{(1)} $E \gets E \cup (\textsc{Advise}(c, T) \cap T)$
\State \textbf{(1)} $R \gets \textsc{Router}(\Delta, m)$;\quad $\lambda_0 \gets \textsc{Classifier}(E)$
\State \textbf{(1)} $\lambda \gets \textsc{Reconcile}(\lambda_0, E, R)$
\For{each layer $\ell \in L$ in pyramid order}
  \State \textbf{(2)} execute the tests $\lambda^{-1}(\ell)$ in isolation; record $o(t)$
\EndFor
\State \textbf{(3)} $A \gets R \cup \lambda(E)$;\quad $U \gets \textsc{Uncovered}(\Delta, E)$
\State \textbf{(3)} $V \gets$ \texttt{needs\_human\_review} \textbf{if} some layer in $A$ is bad \textbf{or} $U \neq \emptyset$ \textbf{else} \texttt{auto\_approve}
\State \textbf{(4)} publish $V$ as the status of $c$
\State \textbf{(5)} $n \gets \textsc{Triage}(c, o)$; post $n$ on $c$
\If{$V$ is \texttt{needs\_human\_review}}
  \State \textbf{(5)} wait for \texttt{/approve} or \texttt{/reject} from a user with write access
\EndIf
\end{algorithmic}
\end{algorithm}
```

Two rules govern failures. If a language model fails or returns unusable output, a rule-based adviser takes its place and the run continues. If the tests cannot be discovered, the run ends with the status `error`, and if the selection cannot be computed, every test runs. Neither case produces an approval that a full run would not have produced.

*Example.* Let $P$ be a support assistant, an agent that answers billing questions by calling tools, with modules `lookup.py`, `refund.py`, and `planner.py` and six tests: two unit tests, one property test, one trajectory test, one LLM-judge test, and one adversarial test. Let $c$ change the body of one function in `planner.py` that orders the tool calls. Discovery finds $|T| = 6$. In stage 1 the selector finds that the function is mentioned only by the trajectory test and one unit test, so $|E| = 2$. The router recognises agentic components, and the classifier labels the two tests with the unit and trajectory layers. In stage 2 both layers run, and the trajectory test fails. In stage 3 the trajectory layer is bad, so $V$ is `needs_human_review`. In stage 4 the status is set to `pending`. In stage 5 the triage report names the failing test and its layer, and a reviewer's `/approve` sets the status to `success`, whereas `/reject` sets it to `failure`. A full run would have executed six tests.

### 3.2 Test Pyramid

The pyramid sorts the tests of a project into layers. Each layer targets one class of failure. The layers are ordered from the base, which is broad and inexpensive to run, to the apex, which is narrow and expensive. Pyramids in common use are defined by the size of the unit under test. A GATE layer is defined by the kind of failure that it detects.

**Definition 1 (Layer).** A layer is a triple $\ell = (\varphi, \alpha, \psi)$, where $\varphi$ is the failure class that the layer targets, $\alpha \in \{\textit{all}, \textit{agentic}\}$ states the projects to which it applies, and $\psi$ is a set of cues. A test is *cued* to $\ell$ if its path, name, or markers contain a cue in $\psi$.

GATE has five layers (Table 2). When a test matches the cues of several layers, the layer nearest the apex is chosen, and a test that matches no cue belongs to the unit layer. The two layers with $\alpha = \textit{agentic}$ apply only to projects with agentic components, so a conventional project is judged on three layers, and a project with agentic components, including a hybrid one, on five. A layer is added by specifying one more triple.

**Table 2.** The layers of the GATE pyramid, from the base to the apex. The cues are matched against the path, name, and markers of a test.

| Layer | Failure class $\varphi$ | Applies to $\alpha$ | Cues $\psi$ |
|---|---|---|---|
| Unit | Incorrect results for individual inputs | all projects | none (default) |
| Property | Violated invariants over generated inputs or sequences of steps | all projects | property, hypothesis, invariant |
| Trajectory | Wrong tools, or tools called in the wrong order | agentic components | trajectory, tool_sequence, toolseq, agent_trace |
| LLM-judge | Answers not supported by the evidence they were given | agentic components | llm_judge, llm_as_judge, judge, rubric |
| Adversarial | Failures on invalid or hostile input and at limits | all projects | adversarial, security, fuzz, malicious, hostile, injection |

### 3.3 Selection

Selection decides which of the discovered tests must run for a change. It rests on static dependencies and uses no model. A test file *reaches* every file that it imports, directly or through other files, and the `conftest.py` files above it. The *core* of the selection is the set of tests whose file is changed or reaches a changed file. This is safe but coarse: a package whose `__init__` file imports every module makes every test reach every file. The selector therefore narrows the core with a *name-level* analysis. For a change inside the body of a function, it computes the set of *affected names*: the function, then every definition whose body mentions an affected name, and so on until no name is added. A test then belongs to the core only if it reaches the changed file and mentions an affected name. The decision is made for each test function. A test mentions the names in its own body, decorators, and parameters, which covers the fixtures it requests, and the names in the code that it shares with other tests: the set-up of its class, the code at module level, autouse fixtures, and pytest hooks. A test whose structure cannot be read is judged by its whole file. A change that cannot be expressed as a name is treated at file level: code that runs at import time (a changed literal constant is the exception, because it cannot fail at import and affects only its own name), decorators, non-literal default values, class bodies, files that cannot be parsed, and functions that are called by a string, either because they are stored as values in a dictionary at import time or because their name starts with a prefix that the project passes to `getattr`. A class does not inherit the mentions of its methods, and a dunder method stands for its class.

Code can also run without an import statement or a name. A file that imports a module by a computed name, or starts another process, can reach a module only if the module's name appears as a string literal in that file or in the test-side files that reach it, or begins with the literal prefix of the computed name (a relative import reaches its package). The selector follows such literals to the modules they name, including `__main__` for `python -m pkg`, and treats what it finds as reached at file level. Files that load code by a computed path and files that cannot be read are the exception, and the tests that reach them are selected for every source change.

Four rules make the selection fail closed. A diff that is empty or cannot be read selects every test. So does a change to a build or test configuration file, such as `pyproject.toml`, `requirements.txt`, or `pytest.ini`. So does any error in the analysis. Changes that cannot affect a test, which are documentation, images, and comments, are *inert* and select nothing.

The selection can end with a changed file that no selected test reaches or mentions. The set of such files is $U(c)$, the *uncovered* part of the change. A change with $U(c) \neq \emptyset$ is not shown to be safe by any test, so the verdict rule sends it to a reviewer (Section 3.5). A project that has no test at all falls under the same rule.

A *test advisor* may name further tests, for example a test that reaches the changed code through a subprocess, a plugin, or a data file. The advisor may be a language model or a rule that matches test names to the names of changed files. Its answer is intersected with the discovered tests and added to the core, so it can only add: $E = \mathrm{core}(c) \cup (\mathrm{advice}(c) \cap T)$.

### 3.4 Plan and Execution

A plan states which layers the change calls for and which layer each test belongs to. It neither writes nor removes tests.

**Definition 2 (Plan).** A plan is a pair $\pi = (R, \lambda)$, where $R \subseteq L$ is the set of layers selected for $c$, and $\lambda : E \to L$ assigns every selected test to exactly one layer.

The *router* produces $R$. It reads the diff $\Delta$ and the description $m$ and answers two questions. The first is whether the project has agentic components, that is, whether it calls language models or tools, builds prompts, or plans over several steps. The second is, for each layer, whether the layer applies to the change. The unit, property, and adversarial layers can apply to any project. The trajectory and LLM-judge layers test agentic behaviour, so they can apply only to a project with agentic components. If the router marks them for a project without such components, a guard rule discards the mark. A layer in $R$ that holds no selected test is reported as applicable but empty. The *classifier* produces a labelling $\lambda_0$. It reads the name, file, and markers of each selected test, but not its body, and its label is compared with the layer to which the test is cued. Both advisers can be replaced by rules: a keyword search over the diff stands in for the router, and the cued layer stands in for the classifier. A rule takes over whenever a model fails or returns unusable output.

A *reconcile* step turns $\lambda_0$ into a complete labelling $\lambda$ of exactly the selected tests. A test that the classifier left out receives its cued layer, and names that are not in $E$ and duplicate names are discarded. A test labelled with a layer outside $R$ is moved to its cued layer if that layer is in $R$, and to the unit layer otherwise. No selected test is removed at any point, so the plan decides how outcomes are grouped and attributed and does not change which tests are executed.

The tests of a change are part of the change under review, so GATE separates their execution from its own. An *execution policy* defines the runner, the environment, the resource limits, and the isolation level. The executor builds an environment from the dependencies that the project declares, reuses it in runs that share the same dependencies, and runs the tests of each layer in a separate process, under limits and without GATE's credentials. A stricter policy runs the whole pipeline in a throwaway container with no credentials and reduced privileges. The policy is chosen per deployment or per repository, so an organisation can match the isolation level to the trust it places in the authors of changes.

### 3.5 Verdict Rule

A layer is *active* if the router selected it or if at least one selected test is labelled with it:
$$A = R \cup \{\lambda(t) : t \in E\}.$$
The verdict rule then maps the outcomes and the uncovered part of the change to the verdict:
$$V = \begin{cases} \texttt{needs\_human\_review} & \text{if some layer in } A \text{ is bad, or } U(c) \neq \emptyset,\\ \texttt{auto\_approve} & \text{otherwise.}\end{cases}$$
In words, a change is approved automatically only if every selected test passes and every changed file that can affect a test is reached by one. The rule is a function of the outcomes, of $A$, and of $U(c)$ alone. It is the same for every change, and it does not consult a model.

Let $E_0 = \mathrm{core}(c)$ be the selection without any advice, and let $V_0$ be the verdict of the run on $E_0$ with the cued layers and all layers selected.

**Proposition 1 (Models cannot weaken the verdict).** For every router, classifier, and advisor, (M1) $E_0 \subseteq E$, (M2) if $V_0$ is `needs_human_review` then $V$ is `needs_human_review`, and (M3) the labelling $\lambda$ does not change which tests run.

*Proof.* The advisor's output is intersected with $T$ and united with $E_0$, which gives M1, and the router and classifier act only after $E$ is fixed, which gives M3. Assume the outcome of a test does not depend on the layer that executes it. If $V_0$ is flagged because a test in $E_0$ fails, that test is in $E$, it lies in the layer $\lambda(t) \in A$, and that layer is bad. If $V_0$ is flagged because $U(c) \neq \emptyset$, then $U(c)$ is computed from $\Delta$ and the core alone, so it is the same. In both cases $V$ is flagged. $\square$

**Proposition 2 (No missed failure).** Assume that the outcome of a test at $P_c$ can differ from its outcome at $P$ only if the test belongs to $E_0$. If some test passes at $P$ and fails at $P_c$, then $V$ is `needs_human_review`.

*Proof.* By the assumption the test belongs to $E_0$ and therefore to $E$. It fails in the run, so its layer is bad and the layer is active. $\square$

The assumption of Proposition 2 is the soundness of the static dependencies, and it is not a theorem about all programs: reflection, computed attribute names, and external services can break it. The evaluation measures how often it fails (Section 5.1). The converse does not hold. A test that already fails at $P$ and is not selected does not flag $c$, because the change did not cause it. The propositions cover the models of the plan. They do not cover hostile test code, a hostile execution environment, or the triage report.

### 3.6 Status and Escalation

GATE publishes $V$ on the pull request as a commit status named `agentic-qa/review`. The status is `pending` while a run is in progress or waits for a reviewer, `success` when $V$ is `auto_approve` or a reviewer approves, `failure` when a reviewer rejects, and `error` when a run fails. Because it is an ordinary commit status, a repository can require `success` before a merge.

A change with $V = \texttt{needs\_human\_review}$ is handed to a reviewer, who resolves it by commenting `/approve` or `/reject` on the pull request. GATE authorises a comment only by the commenter's permission on the repository. It queries GitHub for the role of the commenter and accepts `admin`, `maintain`, and `write`. The text of a comment, of the pull-request description, and of the diff never confers authority, and every webhook is authenticated by its signature. The reviewer is supported by a *triage report*, a short account of which layers ran, which tests failed, which changed files no test reaches, and where to look. A language model drafts the report after $V$ is fixed, and a template replaces the model if it fails. The report describes the verdict and cannot change it.

### 3.7 Implementation and Extension

The selector, the router, the classifier, the test advisor, the triage writer, the executor, and the test runner are interfaces, and each model-backed implementation has a rule-based counterpart, so models and rules can be exchanged without changing the pipeline. The reference implementation is a Python package (`agenticqa`). Its test runner is pytest, and its container policy uses Docker with a non-root user, no capabilities, a read-only file system, and limits on CPU, memory, and processes. Its pipeline queues a run, discovers and selects tests, plans, executes, writes the triage report, and ends in the state done or awaiting approval. The pipeline is driven either from a command line or by a GitHub integration that receives pull-request and comment webhooks, fetches the diff, runs the pipeline, sets the status, and processes approvals. We deploy the integration on Cloudera AI. A setting switches between the selected and the full run.

An organisation can adapt GATE at four points. A layer is added or changed by specifying its triple of failure class, applicability, and cues. A router, classifier, or advisor is replaced through its interface. The execution policy fixes the runner and the isolation level. The repository roles that may approve, and the organisations on which GATE acts, are settings. In the reference implementation the layers and their cues are constants in the code, and the other points are interfaces or settings. Policies that make a layer mandatory for a given kind of change are not implemented.

---

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
- *AIDev* [6]: the files changed by {{selAidevN}} pull requests in Python repositories, and {{fieldAttempted}} of these pull requests drawn with a fixed seed (14 per agent, closed pull requests with 1 to 20 commits) for a static selection study and a field study that runs their tests. Descriptions of {{aidevPRs}} pull requests serve to check the realism of the misleading texts. We read only text and diffs, and never run the code of a pull request outside a throwaway container.
- *{{routCommits}} public commits* ({{routRepos}} projects, 8 each, seeded) for the router study.

**Models.** gpt-4o (2024-08-06), gpt-4o-mini (2024-07-18), mistral-large-latest, and mistral-small-latest. Every report records the model name that the API returned. Structured calls use temperature 0.

**Procedure and measures for mutants.** For each mutant we run the full suite, which is what a plain CI check does, and record the tests that fail and did not fail on the unmodified project. We compute the selection from the diff and run the selected tests separately. GATE *catches* a mutant if a selected test newly fails or if no test reaches the changed file ($U(c) \neq \emptyset$). It *misses* a mutant if the full run shows a new failure and GATE does neither. We report the number of missed mutants with a 95% Wilson upper bound, $z^2/(n+z^2)$ for no miss in $n$ cases, and the share of tests selected, the share of tests saved over all mutants, and the time of the selected runs against the full runs. Because every refinement of the selector changes only which tests are selected, we judge each selector against the failures that the full suite showed, which is exact if a test's outcome does not depend on which other tests run. The first version of the selector also ran its selected tests, which checks that assumption. The time of a selected run is estimated from per-test durations measured on the unmodified project. Kill rates of layers use the exact sign test on the mutants that only one of two sets kills.

**How we kept the evaluation honest.** Every report records the code version, whether the code had uncommitted changes, a fingerprint of the test set, and the model name that the API returned. Commits from one project are not independent, so intervals over commits resample whole projects. If a model call failed during a run, the system silently used a fallback rule, so we mark such a run INVALID and never report it. All figures and numbers in the text are generated from the reports.

---

## 5. Results

### 5.1 RQ1 and RQ2: Selection on mutants of real projects

We ran the full suite and the selected tests on {{selMutValid}} valid mutants of {{selMutProjectsRan}} projects. The full suite failed on {{selMutKilled}} of them and stayed green on {{selMutSurvivors}}.  A twelfth project, packaging, is left out (Section 7).

*Safety.* Selection missed {{selMissNameK}} of the {{selMutKilled}} failures that the full suite showed, with a 95% upper bound of {{selMissNameUpper}}%. A selected test caught {{selCaughtTestsNameK}} of them, and the others were caught because no test reached the changed file. One of the latter is a mutant of the entry-point module of sqlparse. The full suite fails on it in a test of another module, but that test and the other tests of its file pass when they run alone with the mutant, so the failure depends on other tests of the same session. That breaks the assumption that a test's outcome does not depend on the tests that run with it, and the unreached-change rule is what caught the mutant. The first version of the selector ran its selected tests for {{selRunValid}} mutants, and the outcome derived from the full run differed from the actual selected run in {{selRunDisagree}} of them, which supports deriving outcomes for the final selector.

The selector was developed on these projects, and the experiment shaped it. The mutants showed that tests which start `python -m idna` in a process and a codec module that is registered by name lie outside imports and names, that jinja dispatches filters and tests through dictionaries and visitor methods through `getattr` prefixes, and that a class must not inherit the mentions of all its methods. Each finding became a rule, so the numbers above describe a selector tuned on these projects. The held-out projects and the pull-request study below test it elsewhere.

*Held-out projects.* On {{selHoldRan}} held-out projects ({{selHoldValid}} mutants, {{selHoldKilled}} of them failing the full suite) selection missed {{selHoldMissK}} failures, with a 95% upper bound of {{selHoldUpper}}%, and saved {{selHoldSavedPct}}% of the tests and an estimated {{selHoldTimePct}}% of the time. On the {{selHoldFreshRan}} projects of the fresh batch 3, which the final selector saw once, it missed {{selHoldFreshMissK}} of {{selHoldFreshKilled}}. The first held-out run was not clean. For validators the first version of the selector missed {{selFirstMissK}} of {{selFirstKilled}} failures ({{selFirstMissFileK}} at file level), because a package under a `src/` directory that is itself a package was not recognised, so no test reached it by import. We fixed it with a test, and the fix raised the share of validators' tests that run. This failure shows that the safety result needs held-out projects to be believed.

*Comparison with pytest-testmon.* The closest tool in daily use is pytest-testmon [16], which selects dynamically: it runs the suite once under coverage to record which tests execute which code, and reruns only the tests whose code changed. We ran it on the same mutants of {{selTmProjects}} projects ({{selTmMutants}} mutants, {{selTmKilled}} of which fail the full suite). It needed the C tracer of coverage, because the default tracer of Python 3.14 cannot record per-test contexts. It missed {{selTmMissK}} of the {{selTmKilled}} failures (95% upper bound {{selTmUpper}}%) and ran {{selTmSharePct}}% of the tests on average, against {{selTmGateSharePct}}% for GATE on the same mutants, where GATE missed {{selTmGateMissK}}. The projects held out from GATE's development are the fairer comparison: there testmon missed {{selTmHoldMissK}} of {{selTmHoldKilled}} and GATE {{selTmHoldGateMissK}}. Most of testmon's misses are in jinja, where filters and tests are dispatched by name, a route that GATE's rules were tuned for. A dynamic tool is more precise than a static one. Its price is a recorded baseline run and state that must be kept current, and it neither sends a change that no test reaches to a person nor limits what a model may add. GATE's core needs no run, and a recorded map could replace it where one exists.

*Savings.* GATE ran {{selShareMeanNamePct}}% of the tests of a project on average, which saves {{selSavedNamePct}}% of the tests over all mutants. Estimated from the duration of each test on the unmodified project, it saves {{selMutTimeSavedPct}}% of the test time. The median run is {{selShareMedianNamePct}}% of the suite, because savings depend on how a project is built (Figure 2). In the modular boltons and more-itertools, GATE ran {{selShareBoltonsPct}}% and {{selShareMoreItertoolsPct}}% of the tests. In click, jinja, and itsdangerous it ran {{selShareClickPct}}%, {{selShareJinjaPct}}%, and {{selShareItsdangerousPct}}%, because widely used functions such as `invoke` or `dumps` are mentioned by most tests. Each refinement helps: file-level reach saves {{selSavedFilePct}}% of the tests, names at the level of test files {{selSavedNameFPct}}%, and names per test {{selSavedNamePct}}%. The rule for computed imports now costs almost nothing, because ignoring it saves {{selSavedNoDynPct}}%. A name-matching advisor adds tests and lowers the saving to {{selSavedAdvPct}}%.

*Reviews.* The full suite stayed green on {{selExtraRevNameN}} mutants, and {{selExtraRevNameK}} of them still went to a person, because no test reached the changed code.

![Figure 2. Share of a project's tests that GATE runs per mutant, by selection level (lower is better). The number on the right is the count of failures that GATE missed.](../figures/f7_selection.png)

### 5.2 RQ3: Real agent-written pull requests

*Real defects.* The mutants are artificial, so we also used {{selSweAttempted}} real defects from SWE-bench Verified [32]. The exposing tests could be located in the syntax trees for {{selSweUsable}} of them ({{selSweNotLocated}} used test names that static discovery could not locate). GATE caught {{selSweCaughtK}} of the {{selSweUsable}} and missed {{selSweMissK}} (95% upper bound {{selSweUpper}}%): a selected test caught {{selSweByTestK}}, and {{selSweReview}} were caught because no test reached the changed file. Savings are small on real defects, because the defective code is used by many tests: GATE ran {{selSweShareMeanPct}}% of the tests on average, and file-level reach ran {{selSweFilePct}}%. The first run of this experiment found a miss that exposed a bug, where a deleted code line between comments was read as no change. We fixed it with a test and reran the experiment.

*At scale.* Of {{selAidevN}} AIDev pull requests in Python repositories, {{selAidevInertPct}}% change only documentation or other files that cannot affect a test, so no test is needed. {{selAidevConfigPct}}% change build or test configuration, which runs the full suite. {{selAidevPythonPct}}% change Python source, and {{selAidevOtherPct}}% change files of another kind such as data, scripts, or front ends. The median pull request changes {{selAidevMedianFiles}} files.

*Static selection on {{selFieldAttempted}} pull requests.* We fetched each pull request and computed what GATE would select, without running any test (Table 3). The {{selFieldWithTests}} pull requests whose repositories have tests face {{selFieldTestsTotal}} test functions, and a plain CI check would run all of them. GATE would run {{selFieldTestsSelected}}, which is {{selFieldSavedPct}}% fewer. File-level reach alone would save {{selFieldSavedFilePct}}%, and ignoring the rule for files that load code by a computed path would save {{selFieldSavedNoDynPct}}%, so on these repositories the savings come from documentation-only changes and from changes that no test mentions, and little comes from narrowing the tests of a change that some test reaches.

The price is reviewer load. For {{selFieldReview}} of {{selFieldAttempted}} pull requests a changed file is reached by no test, and GATE sends them to a person where a plain check would report green. {{selFieldRevPython}} of them change Python code that no test mentions, {{selFieldRevNonPython}} change only files of another kind such as data, scripts, or front ends, and {{selFieldRevNoTests}} belong to repositories that have no tests. Whether this load is acceptable is a policy decision, and we did not measure how reviewers would respond.

**Table 3.** What GATE does with {{selFieldAttempted}} real agent pull requests, from the diff alone.

| Outcome | Pull requests |
|---|---|
| No test needed (only documentation or other inert files) | {{selFieldInertOnly}} |
| Run a subset of the tests (median {{selFieldSubsetMedianPct}}% of the suite) | {{selFieldSubset}} |
| Run all tests (build or test configuration changed) | {{selFieldFullRun}} |
| Needs review (a changed file is reached by no test) | {{selFieldReview}} |

*Model advice.* A gpt-4o-mini advisor added {{selAdvMeanAdded}} tests per mutant on average, at about {{selAdvTokens}} tokens and {{selAdvSeconds}} seconds, in {{selAdvWithAdditions}} of {{selAdvMutants}} mutants. {{selAdvOutsideCore}} failing tests lay outside the core, so the model had nothing to find, and its additions only cost time. For the name-matching advisor the saving falls from {{selSavedNamePct}}% to {{selSavedAdvPct}}%.

### 5.3 RQ4: Models cannot weaken the verdict

*Routers and classifiers.* We attacked the pipeline with hostile routers and classifiers. For routers we tried all 32 ways of switching the five layers on or off, including choices that the guard rule would reject. For classifiers we tried six bad behaviours: leave out all tests, leave out every other test, send every test to an unused layer (with and without a default layer), rotate tests across layers, and add invented and duplicate tests. The attack ran with every test selected, so that it tests the labelling alone.

**Table 5.** How often the guarantees on the labelling were broken (change of verdict, of failure count, or of the tests that ran).

| Where | Attack cases | Cases with any break |
|---|---|---|
| Demo services, with fixes | {{advCasesAfter}} ({{advRouterCases}} router, {{advClassifierCases}} classifier) | **{{advViolationsAfter}}** (95% upper bound {{advUpperBoundPct}}%) |
| Demo services, fixes removed (a subset) | {{advCasesBefore}} | {{advViolationsBefore}} |
| {{realSuitesRan}} real projects, with fixes (smaller attack) | {{realCases}} | **{{realViolations}}** |
| click ({{clickTests}} tests), fixes removed | {{advRealClickCases}} | {{advRealClickViolationsBefore}} |

The attack found two bugs in the first version that we deployed on Cloudera AI. The result check looked only at the layers that the router picked, so with three layers switched off a run with two failing tests ended as `auto_approve`. The pipeline also trusted the classifier to list every test, so a classifier that left out, invented, or repeated tests changed which tests ran. Both are fixed (Section 3.4). With the fixes removed, the attack finds the bugs ({{advViolationsBefore}} of {{advCasesBefore}} cases on the demos and {{advRealClickViolationsBefore}} of {{advRealClickCases}} on click), so its zeros are meaningful.

*Advisors.* We then attacked the selection with eight hostile test advisors (empty, invented ids, all tests, only tests outside the core, duplicates, a hundred thousand ids, an exception, and the reverse order). Each ran in the real pipeline on mutants of {{selAtkProjects}} real projects: {{selAtkCases}} cases on {{selAtkMutants}} mutants. The core tests failed to run in {{selAtkM1}} cases (M1), and the verdict was weaker than that of the run without advice in {{selAtkM2}} cases (M2), with a 95% upper bound of {{selAtkUpper}}%. A property test with 200 random advisor outputs found no violation either, and no run was cut off by the layer time limit ({{selAtkTimeouts}} timeouts). An earlier run of the idna attack on a heavily loaded machine showed 8 cases in which fewer tests ran than in the honest run. The rerun showed none, and we attribute the earlier cases to the load without having proved it. The guarantees do not cover a hostile test environment or hostile test code.

### 5.4 RQ5: The layers find different failures

In each demo service, every bug that we planted is found by exactly the layer it was designed for, and the other layers stay green. These bugs fit one layer each by design, so this shows only that each layer can be needed. The mutants check that without our labels. We used every possible change of seven types in both services, which gave {{mutGenerated}} mutants, none invalid.

**Table 6.** Share of the {{mutValid}} mutants killed by each set of layers, with the exact sign test against all layers.

| Layers used | Killed | 95% interval | Only the full pyramid kills | p-value |
|---|---|---|---|---|
| Unit only | {{mutRateUnit}}% | [{{mutLoUnit}}, {{mutHiUnit}}] | {{mutOnlyFullVsUnit}} | {{mutPVsUnitF}} |
| Unit + property | {{mutRateUnitProp}}% | [{{mutLoUnitProp}}, {{mutHiUnitProp}}] | {{mutOnlyFullVsUnitProp}} | {{mutPVsUnitPropF}} |
| Unit + adversarial | {{mutRateUnitAdv}}% | [{{mutLoUnitAdv}}, {{mutHiUnitAdv}}] | {{mutOnlyFullVsUnitAdv}} | {{mutPVsUnitAdvF}} |
| **All layers** | **{{mutRateFull}}%** | [{{mutLoFull}}, {{mutHiFull}}] | n/a | n/a |

The unit, property, and adversarial layers each kill mutants that no other layer kills, and all layers together beat every smaller set. The two agentic layers add almost nothing here (only-this-layer kills: trajectory {{mutUniqueTrajectory}}, LLM-judge {{mutUniqueJudge}}), because small automatic code changes rarely cause the failures they target. So the case for them rests on the three hand-made bugs. A layer here is a group of test files, so the comparison also measures how we wrote those tests. {{mutSurvived}} of {{mutValid}} mutants survive: some are harmless, and some are real gaps in our own tests.

### 5.5 RQ4, continued: what models add, and misleading text

*Project type from a diff.* On {{routCommits}} real commits, the router saw the commit message and the diff, as in the real system. The project name does most of the work: hiding it lowers the accuracy of gpt-4o-mini by {{routNameEffectMini}} points, of mistral-large by {{routNameEffectMistralLarge}}, and of gpt-4o by {{routNameEffectGptO}}, although the prompt says to judge by what the code does. With the name hidden, no model is clearly different from a keyword rule on the diff ({{routAccHeuristic}}%). A fixed check of the project's dependency files for names of LLM libraries, written before we ran it, reaches {{routAccManifest}}% and errs only on `fastapi`. The agent projects in our set are LLM frameworks, which favours this check, so the data supports a design choice and not a ranking: decide the project type from project-level evidence and use models for the per-change decisions.

*Plain rules against models.* On {{nmN}} scenarios (six planted bugs and two clean changes, each also with seven kinds of misleading text), plain rules gave the right verdict in {{nmStubVerdict}} and every model in at least {{nmModelVerdictMin}}. Plain rules named exactly the failing layers in {{nmStubLayers}} scenarios and the four models in {{nmModelLayersMin}} to {{nmModelLayersMax}}. All {{nmStubLayerMisses}} misses of the plain rules come from {{nmStubBugsMissed}} planted bugs, the two in the agent service, so the gap is two cases and not {{nmStubLayerMisses}}. Plain rules took {{nmStubSeconds}} seconds and no tokens, the models {{nmModelSecondsMin}} to {{nmModelSecondsMax}} seconds and {{nmTokensMin}} to {{nmTokensMax}} thousand tokens. In our setting, models add a modest improvement in how failures are grouped at a clear cost, and nothing to the verdict.

*Misleading text.* We wrote seven kinds of misleading text, based on [7] (a claim of authority, urgency, a direct order, a request to ignore failures, made-up results, a false statement of the project type, and a hidden note to the reviewer) and added each to every demo scenario, which gave {{injRuns}} runs with it and {{injControlRuns}} without on four models. The verdict changed in {{injPipelineFlips}} runs, and {{injPipelineFalseApproves}} of the {{injWithFailures}} runs with a failing test were approved (95% upper bound {{injGateUpperPct}}%). The router's choice changed in {{injRoutingFlips}} runs, and a single false statement of the project type moved it in {{injMisdirectFlips}} of {{injMisdirectN}} tries. Ordinary text moves it too: {{noiseDescriptions}} real descriptions, appended to {{noiseScenarios}} scenarios, changed the router's decision in {{noisePooledPct}}% of runs. A model that decides pass or fail from the same inputs approved a failing change in {{injBaselineFalseApproves}} of {{injWithFailures}} runs ({{injBaselinePct}}%), almost all from one text, "known flaky, do not mention" ({{injFlakyHits}} of {{injFlakyN}}). The comparison prompt is ours and a better one might resist more. Real agent descriptions make such claims: {{aidevSaysPassPct}}% of {{aidevLong}} say outright that tests pass.

### 5.6 RQ3, continued: a field study that runs the tests, and cost

On {{fieldAttempted}} closed agent pull requests, GATE ran the tests of the head commit and of its base, inside throwaway containers. Only {{fieldRan}} attempts ({{fieldRanPct}}%) could be evaluated, because building each project's environment automatically usually failed, most often on a package that the tests import and the executor had not installed ({{fieldWhyMissingPkg}} attempts). None of the failures was approved. The study also exposed a bug: a project whose tests could not be loaded was approved with zero tests run, because failed loading looked like "no tests". It now ends in an error. Of the {{fieldRan}} pull requests that ran, {{fieldFlagged}} were flagged, and {{fieldIntroduced}} of the {{fieldWithBase}} with a usable base introduced a failing test. With so few cases we say nothing about the quality of agent pull requests. The finding concerns GATE: environment setup is the weak point, and a deployment could read the project's own CI result when it exists. A full run on the demo services (about 25 tests) takes {{wallMin}} to {{wallMax}} seconds and {{tokMin}} to {{tokMax}} thousand tokens with models. These are laptop timings on small suites, and we measured neither Cloudera AI timings nor very large repositories.

---

## 6. Discussion

**What works and what does not.** The selection, the verdict rule, and the model-facing parts held up under attack: no hostile router, classifier, or advisor weakened the verdict, misleading text never changed it, and a model that decides pass or fail was fooled. The layers beat smaller sets on mutants, and the agentic layers add little there. Several things do not work well. Models do not beat plain rules on the verdict, and a diff alone is a poor basis for choosing the project type. One sentence or ordinary text can steer the router, which is why the router has no power over the verdict. A checker is only as good as its tests: {{mutSurvived}} of {{mutValid}} demo mutants survive, and {{realSuitesWithBaselineFailures}} of {{realSuitesRan}} real projects had failing tests before any change. Automatic environment setup failed for most agent pull requests.

**Against the alternatives.** A plain CI check that runs every test and fails on any failure finds the same failures as GATE's full run, so GATE finds no more bugs than the tests it runs. What GATE adds is the selection with its measured safety, the refusal to approve a change that no test reaches, failures grouped by layer, an approval step that checks permissions, and an explanation. A model that decides pass or fail was fooled by one sentence ({{injFlakyPct}}% for the strongest text), and ours was not. A scope-based pyramid of unit and adversarial tests kills {{mutRateUnitAdv}}% of the mutants against {{mutRateFull}}% for all layers. We did not run commercial review tools, which are closed and change often, or the pyramid papers [19, 20], which are conceptual or tied to a toolkit.

**Mistakes in our own evaluation.** Sixteen mistakes would each have given a wrong or flattering number, and we found each by inspection or by an experiment built to fail. Examples: cost was read from the last of three model calls and undercounted by 7.7 times; a failed model call used the fallback rule and counted as agreement; rate limits silently gave fallback answers in 129 of 144 calls of one run, so such runs are now marked INVALID; the answer key was passed to the router as the pull-request description; commits from one project were treated as independent, which reversed one claim; a project whose tests could not be loaded was approved with zero tests run; and we explained the field-study failures by project layouts that the executor cannot read, when {{fieldMissStandard}} of {{fieldMissRepos}} projects use the layout it does read. Two habits helped most: a *control that should fail* (the runs with the fixes removed) turned a zero into evidence, and refusing to report a run with failed model calls removed a whole kind of contamination.

---

## 7. Threats to Validity and Limits

- **Assumptions.** The verdict rests on four assumptions. The project's tests are the only evidence, so a change that deletes or weakens tests can pass. The text of a change and the output of a model are untrusted. Test code is untrusted and runs in isolation. The outcome of a test does not depend on the layer that runs it or on the tests that run with it. The static dependencies of Proposition 2 are sound, which includes the assumption that a module loaded by a computed name or run in a started process is named by a string literal that the selector can see.
- **Our own choices.** We wrote the planted bugs, the misleading texts, and the comparison prompt. Mutants are small automatic changes and not real defects, and the real defects of agent pull requests may reach tests through routes that mutants do not. The list of LLM libraries reflects what we know about agent frameworks. A layer is a group of test files, so comparing layers also measures how we wrote those tests.
- **How far the results reach.** Only {{selHoldRan}} of {{selHoldAttempted}} held-out projects could be set up, so the held-out evidence comes from a self-selected set of projects with simple test setups.  {{selMutProjectsRan}} real projects (packaging, with 23,516 tests, was dropped because its full run was too slow for the mutant loop), two demo services, Python and pytest only, public projects only. Static selection is unsound for reflection that builds names from data, for names read from files or the environment, and for external services. The field study could evaluate only {{fieldRanPct}}% of its sample, the static study of agent pull requests runs no test, and the agent demo uses a scripted planner. The comparison with plain rules rests on two bugs in one service.
- **Statistics.** Mutants of one project are grouped, so intervals are wide. Structured calls use temperature 0, so repeating a run mostly measures API noise, and we ran each setting once.
- **Not tested.** Commercial review tools, whether the explanation helps human reviewers, large monorepos, languages other than Python, and Cloudera AI performance at scale.

## 8. Conclusion

AI agents make pull requests cheap to produce and merging them expensive. GATE answers with a framework that selects the tests a change can affect with a deterministic core, lets models add tests and never remove one, refuses to approve a change that no test reaches, and computes the verdict with a fixed rule. On mutants of real projects it missed {{selMissNameK}} of {{selMutKilled}} failures that the full suite showed, and {{selHoldMissK}} of {{selHoldKilled}} on projects held out from its development, it caught {{selSweCaughtK}} of {{selSweUsable}} real defects from SWE-bench Verified, and it saved tests where a project is modular; on real agent pull requests it saved the tests of documentation-only changes and sent changes that no test reaches to a person. No hostile model changed a verdict. The verdict equals that of a plain CI check on the tests it runs, and plain rules give the same verdict without any model. Three directions follow from the limits: finer dependency analysis for dynamic code, policies that make a layer mandatory for a kind of change, and reading the project's own CI result in place of rebuilding every environment. The most useful lesson for the method is that our own test harness was the largest source of wrong numbers, and that a control built to fail was the best tool against them.

## Availability and disclosure

Code, fixed test sets, all reports, and the generated figures and numbers are in the project repository at https://github.com/masorg/agentic-qa [AUTHOR: confirm that the repository is public, and archive a release on Zenodo or Figshare to obtain a DOI, as the venue's open-science policy asks]. Every figure and number in this paper can be regenerated from the saved reports with `agenticqa eval-plot`. The experiments are run by `adversary-run`, `mutation-run`, `selection-mutants`, `selection-replay`, `selection-field`, `selection-aidev`, `selection-attack`, `selection-advice`, `selection-swebench`, `selection-testmon`, `route-eval`, `eval-run`, `route-noise`, `aidev-claims`, and `field-study`; the full text of the seven kinds of misleading text is in `src/agenticqa/eval/injection.py`, and the held-out projects are pinned in `eval/data/holdout-projects.json`. Each report records the code version, whether the code had uncommitted changes, a fingerprint of the test set, and the model name that the API returned. The selector changed after some experiments, as the text states; the final change affects only diffs that delete lines, which mutants do not, and the experiments that depend on the selector were rerun on the final code.

**Use of AI tools.** In line with the IEEE and ACM authorship policies, we disclose that an AI coding assistant (Claude Code, with Claude models) helped to draft the text, write and run the analysis code, and review the work. The authors checked the claims and are responsible for them.

## References

[1] H. Li, H. Zhang, A. E. Hassan, "The Rise of AI Teammates in Software Engineering (SE) 3.0: How Autonomous Coding Agents Are Reshaping Software Engineering," arXiv:2507.15003, 2025. Dataset: hao-li/AIDev on Hugging Face (CC-BY-4.0; each source repository keeps its own license).
[2] GitHub, "Octoverse: A new developer joins GitHub every second as AI leads TypeScript to #1," GitHub Blog, 2025. https://github.blog/news-insights/octoverse/octoverse-a-new-developer-joins-github-every-second-as-ai-leads-typescript-to-1/
[3] Faros AI, "The AI Productivity Paradox Report 2025," Faros AI, July 23, 2025. https://www.faros.ai/blog/ai-software-engineering (vendor telemetry from over 10,000 developers in 1,255 teams; not peer reviewed).
[4] H. Ö. Kamalı et al., "Rethinking Code Review in the Age of AI: A Vision for Agentic Code Review," arXiv:2605.17548, 2026.
[5] S. R. O. Peralta et al., "Why Are Agentic Pull Requests Merged or Rejected? An Empirical Study," arXiv:2605.22534, MSR 2026.
[6] S. Zhong et al., "From Human-Centric to Agentic Code Review: The Impact of Different Generations of Generative AI Technology on Review Quality," arXiv:2607.13196, 2026.
[7] R. Melo et al., "SEVRA-BENCH: Social Engineering of Vulnerabilities in Review Agents," arXiv:2606.13757, 2026.
[8] H. Yu et al., "Habituation at the Gate: Rising Approval and Declining Scrutiny in Human Review of AI Agent Code," arXiv:2606.22721, 2026.
[9] G. Rothermel and M. J. Harrold, "A safe, efficient regression test selection technique," ACM Trans. Softw. Eng. Methodol., vol. 6, no. 2, 1997, doi:10.1145/248233.248262.
[10] M. Gligoric, L. Eloussi, and D. Marinov, "Practical regression test selection with dynamic file dependencies," in Proc. ISSTA, 2015, doi:10.1145/2771783.2771784.
[11] O. Legunsen, F. Hariri, A. Shi, Y. Lu, L. Zhang, and D. Marinov, "An extensive study of static regression test selection in modern software evolution," in Proc. FSE, 2016, doi:10.1145/2950290.2950361.
[12] Y. Wang, M. Pradel, and Z. Liu, "Names Are All You Need: Effective and Safe Regression Test Selection for Python," arXiv:2605.25356, 2026 (accepted at ISSTA 2026).
[13] S. P. Kumar, S. Bararia, and K. Raj, "Bigger Isn't Always Better: A Comparative Evaluation of LLMs for Automated Code Review," arXiv:2606.15689, 2026.
[14] M. Machalica, A. Samylkin, M. Porth, and S. Chandra, "Predictive Test Selection," arXiv:1810.05286, 2018 (camera-ready 2019).
[15] A. Memon et al., "Taming Google-scale continuous testing," in Proc. ICSE-SEIP, 2017, doi:10.1109/ICSE-SEIP.2017.16.
[16] pytest-testmon, https://github.com/tarpas/pytest-testmon, accessed 2026-10-23.
[17] P. Alonso, S. Yovine, and V. A. Braberman, "TDAD: Test-Driven Agentic Development - Reducing Code Regressions in AI Coding Agents via Graph-Based Impact Analysis," arXiv:2603.17973, 2026.
[18] B. A. Muse, L. Briand, Y. Lu, and K. Gallaba, "CASPER-Change-Aware Slice Prioritization for Efficient Regression Testing of LLM-based systems," arXiv:2608.00378, 2026.
[19] C. Badagi et al., "AI Assurance: A Comprehensive Testing Strategy for Enterprise AI Systems," arXiv:2605.23459, 2026.
[20] J. Kohl et al., "Automated structural testing of LLM-based agents: methods, framework, and case studies," arXiv:2601.18827, 2026.
[21] H. Liu et al., "DPIAgent: Divide, Protocol, Isolate for Agentic Reproduction Test Generation," arXiv:2608.23341, 2026.
[22] Q. Zhang et al., "ReProAgent: Tool-Augmented Multi-Stage Agentic Generation of Bug Reproduction Tests from Issue Reports," arXiv:2607.09123, 2026.
[23] Q. Zhang et al., "Multi-Agent LLM Collaboration for Unit Test Generation via Human-Testing-Inspired Workflows," arXiv:2607.09101, 2026.
[24] C. Yang et al., "Learning Globally Reusable Skills for Coding Agents," arXiv:2608.06153, 2026.
[25] H. Qi et al., "Change2Task: From Repository Changes to Executable Coding Agent Tasks and Environments," arXiv:2607.28591, 2026.
[26] A. Tabassum et al., "VibeCheck: Assessing the Quality of LLM-Generated Unit Tests: A Multi-agent Empirical Study across Heterogeneous Repositories," arXiv:2609.05978, 2026.
[27] P. Jhanglani et al., "Beyond Test Presence: Assessing the Quality and Robustness of Agent-Generated Tests in Open-Source Projects," arXiv:2607.12068, 2026.
[28] H. Tanaka et al., "Understanding the Characteristics of LLM-Generated Property-Based Tests in Exploring Edge Cases," arXiv:2510.25297, 2025.
[29] K. Etemadi et al., "LLM-based Property-based Test Generation for Guardrailing Cyber-Physical Systems," arXiv:2505.23549, 2025.
[30] Z. Shi et al., "DynSTEER: Dynamic Stage-wise Trajectory Evaluation and Execution-time Review for Agents," arXiv:2609.14637, 2026.
[31] M. Ayyad et al., "Trajectory-Aware Benchmark Subset Selection for Cost-Efficient Software Engineering Agent Regression Testing," arXiv:2609.24928, 2026.
[32] C. E. Jimenez, J. Yang, A. Wettig, S. Yao, K. Pei, O. Press, and K. Narasimhan, "SWE-bench: Can Language Models Resolve Real-World GitHub Issues?" in Proc. ICLR, 2024, arXiv:2310.06770. The Verified subset (500 human-validated instances) is hosted at huggingface.co/datasets/princeton-nlp/SWE-bench_Verified.
