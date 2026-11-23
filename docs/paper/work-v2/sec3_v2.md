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

GATE has five layers (Table 3). When a test matches the cues of several layers, the layer nearest the apex is chosen, and a test that matches no cue belongs to the unit layer. The two layers with $\alpha = \textit{agentic}$ apply only to projects with agentic components, so a conventional project is judged on three layers, and a project with agentic components, including a hybrid one, on five. A layer is added by specifying one more triple.

**Table 3.** The layers of the GATE pyramid, from the base to the apex. The cues are matched against the path, name, and markers of a test.

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

