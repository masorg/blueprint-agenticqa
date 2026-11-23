<!--
WORKING DRAFT (one section at a time) for CAIN 2027, AI Engineering in Practice track (10+2 pages, IEEE template).
Every double-brace placeholder is filled from the committed evidence by build_draft.py
(numbers come from docs/figures/numbers.tex and eval/reports/*.json). Do not type numbers here.
Items in [AUTHOR: ...] need a decision or a fact only the authors have.
-->

# GATE: A Framework for Gated Assurance with Tiered Evidence in Pull-Request Review

**Authors:** [AUTHOR: names, affiliations, emails]

*Only the agreed section is kept here. The complete earlier draft is in docs/paper/archive/.*

## 3. The GATE Framework

GATE (Gated Assurance with Tiered Evidence) decides whether a change to a software project can be merged, using the project's own tests as evidence. It applies to projects with or without agentic components, including hybrid workflows in which conventional code and agent components are combined. GATE has two parts (Figure 1): a *test pyramid*, which organises the tests of a project into five layers that each target one class of failure, and a *review pipeline*, through which every change passes in five stages:

1. **Plan.** A router and a classifier advise which layers the change calls for and which layer each test belongs to. Either may be a language model.
2. **Execute.** An isolated executor runs the tests layer by layer, in a separate process or in a throwaway container.
3. **Verdict rule.** A fixed rule maps the test outcomes to one of two verdicts, `auto_approve` or `needs_human_review`. No model takes part.
4. **Pull-request status.** The verdict is published on the pull request as a commit status, which a repository can require before merging.
5. **Escalation.** A flagged change goes to a reviewer together with a triage report, which a language model may draft, and the reviewer resolves it with `/approve` or `/reject`.

![Figure 1. Overview of GATE. A change passes through the five stages of the review pipeline from left to right. The test pyramid is drawn inside the stage that runs the tests, because the plan, the execution, and the verdict rule all work on its layers. Dashed boxes are stages in which a language model may take part, and solid boxes are fixed rules.](../figures/f0_framework.png)

Formally, GATE maps a change $c$ of a project $P$ to a verdict $V$ in four steps, each of which uses the result of the one before:
$$\begin{aligned} T &= \mathrm{discover}(P_c), & \pi &= \mathrm{plan}(c, T),\\ o &= \mathrm{execute}(T, \pi), & V &= \mathrm{decide}(o, \pi). \end{aligned}$$
Here $c = (\Delta, m)$ consists of a diff $\Delta$ and its description $m$, which is the commit message and the pull-request text, and $P_c = P \oplus \Delta$ is the project after the diff is applied. The set $T$ contains the tests discovered in $P_c$, and $o$ assigns each test $t$ an outcome $o(t) \in \{\text{pass}, \text{fail}, \text{error}\}$. The plan $\pi = (R, \lambda)$ has two parts: a set $R \subseteq L$ of layers that the change calls for, and a labelling $\lambda : T \to L$ that assigns every test to one layer of the pyramid $L = (\ell_1, \dots, \ell_5)$, ordered from the base to the apex. A layer is *bad* if at least one of its tests fails or errors, and the verdict is $V \in \{\texttt{auto\_approve}, \texttt{needs\_human\_review}\}$. The functions $\mathrm{plan}$, $\mathrm{execute}$, and $\mathrm{decide}$ are stages 1, 2, and 3. Stages 4 and 5 act on the result: they publish $V$ and hand a flagged change to a reviewer.

### 3.1 Workflow

A change passes through the stages in the order of their numbers. GATE first discovers the tests of the changed project and plans (stage 1). It then executes the tests layer by layer (stage 2), applies the verdict rule to the outcomes (stage 3), publishes the verdict as a status on the pull request (stage 4), and, if the verdict asks for a review, escalates the change to a reviewer (stage 5). Algorithm 1 gives the procedure, and the bracketed number before a step names its stage. The triage report $n$ describes the outcome and cannot change $V$.

```{=latex}
\begin{algorithm}[!ht]
\caption{GATE on a change $c = (\Delta, m)$ of a project $P$}
\begin{algorithmic}[1]
\State check out $P_c$ and discover the tests $T$ \Comment{error if this fails}
\State \textbf{[1]} $R \gets \textsc{Router}(\Delta, m)$
\State \textbf{[1]} $\lambda_0 \gets \textsc{Classifier}(T)$
\State \textbf{[1]} $\lambda \gets \textsc{Reconcile}(\lambda_0, T, R)$
\For{each layer $\ell \in L$ in pyramid order}
  \State \textbf{[2]} execute the tests $\lambda^{-1}(\ell)$ in isolation; record $o(t)$
\EndFor
\State \textbf{[3]} $A \gets R \cup \lambda(T)$ \Comment{active layers}
\State \textbf{[3]} $V \gets$ \texttt{needs\_human\_review} \textbf{if} some layer in $A$ is bad \textbf{else} \texttt{auto\_approve}
\State \textbf{[4]} publish $V$ as the status of $c$
\State \textbf{[5]} $n \gets \textsc{Triage}(c, o)$; post $n$ on $c$
\If{$V$ is \texttt{needs\_human\_review}}
  \State \textbf{[5]} wait for \texttt{/approve} or \texttt{/reject} from a user with write access
\EndIf
\end{algorithmic}
\end{algorithm}
```

Two rules govern failures. If a language model fails or returns unusable output, a rule-based adviser takes its place and the run continues. If the tests cannot be discovered, the run ends with the status `error`. Neither case produces an approval.

*Example.* Let $P$ be a support assistant, an agent that answers billing questions by calling tools, and let its suite contain six tests: two unit tests, one property test, one trajectory test, one LLM-judge test, and one adversarial test. Let $c$ make the assistant call the refund tool before the lookup tool. Discovery finds $|T| = 6$. In stage 1 the router reads $\Delta$ and $m$, recognises agentic components, and selects all five layers, and the classifier labels each test with the layer its name indicates. In stage 2 the five layers are executed, and the trajectory test fails while the other five tests pass. In stage 3 the trajectory layer is bad, so $V$ is `needs_human_review`. In stage 4 the status is set to `pending`. In stage 5 the triage report names the failing test and its layer, and a reviewer's `/approve` sets the status to `success`, whereas `/reject` sets it to `failure`.

### 3.2 Test Pyramid

The pyramid sorts the tests of a project into layers. Each layer targets one class of failure. The layers are ordered from the base, which is broad and inexpensive to run, to the apex, which is narrow and expensive. Pyramids in common use are defined by the size of the unit under test. A GATE layer is defined by the kind of failure that it detects.

**Definition 1 (Layer).** A layer is a triple $\ell = (\varphi, \alpha, \psi)$, where $\varphi$ is the failure class that the layer targets, $\alpha \in \{\textit{all}, \textit{agentic}\}$ states the projects to which it applies, and $\psi$ is a set of cues. A test is *cued* to $\ell$ if its path, name, or markers contain a cue in $\psi$.

GATE has five layers (Table 1). When a test matches the cues of several layers, the layer nearest the apex is chosen, and a test that matches no cue belongs to the unit layer. The two layers with $\alpha = \textit{agentic}$ apply only to projects with agentic components, so a conventional project is judged on three layers, and a project with agentic components, including a hybrid one, on five. A layer is added by specifying one more triple.

**Table 1.** The layers of the GATE pyramid, from the base to the apex. The cues are matched against the path, name, and markers of a test.

| Layer | Failure class $\varphi$ | Applies to $\alpha$ | Cues $\psi$ |
|---|---|---|---|
| Unit | Incorrect results for individual inputs | all projects | none (default) |
| Property | Violated invariants over generated inputs or sequences of steps | all projects | property, hypothesis, invariant |
| Trajectory | Wrong tools, or tools called in the wrong order | agentic components | trajectory, tool_sequence, toolseq, agent_trace |
| LLM-judge | Answers not supported by the evidence they were given | agentic components | llm_judge, llm_as_judge, judge, rubric |
| Adversarial | Failures on invalid or hostile input and at limits | all projects | adversarial, security, fuzz, malicious, hostile, injection |

### 3.3 Plan

A plan decides which layers the change calls for and which layer each test belongs to.

**Definition 2 (Plan).** A plan is a pair $\pi = (R, \lambda)$, where $R \subseteq L$ is the set of layers selected for $c$, and $\lambda : T \to L$ assigns every test to exactly one layer.

Two advisers produce the plan, and either may be a language model. The *router* reads $\Delta$ and $m$ and returns two things: whether the project has agentic components, and the set $R$. A consistency rule removes the agentic layers from $R$ when it does not. The *classifier* reads the name, file, and markers of each test, but not its body, and returns a labelling $\lambda_0$. The label is compared with the layer to which the test is cued, and the agreement is recorded. If a model fails, a rule-based adviser takes its place: the router falls back to a keyword search over the diff, and the classifier falls back to the cued layer.

A *reconcile* step turns $\lambda_0$ into a complete labelling $\lambda$ of exactly the discovered tests. A test that the classifier left out receives its cued layer, and names that are not in $T$ and duplicate names are discarded. A test labelled with a layer outside $R$ is moved to its cued layer if that layer is in $R$, and to the unit layer otherwise. No test is removed at any point. The plan therefore decides how outcomes are grouped and attributed, and it does not change which tests are executed.

### 3.4 Execution

The tests of a change are code written by the author of the change, so GATE runs them outside its own process. The executor checks out $P_c$ and builds a virtual environment from the dependencies that the project declares, including its test extras. Environments are reused by runs that share a dependency set. The project root and its source directory are placed on the import path. The tests of each layer run in a separate process, under resource limits and without GATE's credentials. The reference runner is pytest.

For changes whose authors are not trusted, the whole pipeline runs in a throwaway container. The container uses a non-root user, drops all capabilities and the right to gain new privileges, mounts the root file system read-only, limits CPU, memory, and the number of processes, and holds no credentials.

### 3.5 Verdict Rule

A layer is *active* if the router selected it or if at least one test is labelled with it:
$$A = R \cup \{\lambda(t) : t \in T\}.$$
The verdict rule then maps the outcomes to the verdict:
$$V = \begin{cases} \texttt{needs\_human\_review} & \text{if some layer in } A \text{ is bad},\\ \texttt{auto\_approve} & \text{otherwise.}\end{cases}$$
In words, a change is approved automatically only if every active layer has all of its tests passing; a single failing or erroring test in an active layer sends the change to a reviewer. The rule is a function of the outcomes and of $A$ alone. It is the same for every change, and it does not consult a model.

To state what the rule guarantees, let $V_0$, $F_0$, and $E_0$ denote the verdict, the set of failing tests, and the set of executed tests of an *honest run*, in which every layer is selected and every test is labelled with its cued layer. Let $F$ and $E$ be the same sets for a run with an arbitrary plan.

**Proposition 1 (Invariance).** For every router and every classifier, (I1) $V = V_0$, (I2) $|F| = |F_0|$, and (I3) $E = T$.

*Proof.* Assume that the outcome of a test does not depend on the layer that executes it. The reconcile step labels every test with exactly one layer, and every layer that holds a test is executed, so every discovered test is executed, which is I3. The outcomes are then those of the honest run, hence $F = F_0$, which is I2. A failing test $t$ lies in the layer $\lambda(t) \in A$, which makes that layer bad, and a bad layer contains a failing test. Therefore $V = \texttt{needs\_human\_review}$ if and only if $F \neq \emptyset$, for every plan and for the honest run alike, so $V = V_0$, which is I1. $\square$

The proposition covers the router and the classifier. It does not cover hostile test code, a hostile execution environment, or the triage report. A project without tests has $T = \emptyset$ and receives `auto_approve`, which the totals of the run make visible. By the proposition, $V$ coincides with the verdict of a conventional check that fails when any test fails: the models affect how results are grouped and explained, and not what the verdict is.

### 3.6 Status and Escalation

GATE publishes $V$ on the pull request as a commit status named `agentic-qa/review`. The status is `pending` while a run is in progress or waits for a reviewer, `success` when $V$ is `auto_approve` or a reviewer approves, `failure` when a reviewer rejects, and `error` when a run fails. Because it is an ordinary commit status, a repository can require `success` before a merge.

A change with $V = \texttt{needs\_human\_review}$ is handed to a reviewer, who resolves it by commenting `/approve` or `/reject` on the pull request. GATE authorises a comment only by the commenter's permission on the repository. It queries GitHub for the role of the commenter and accepts `admin`, `maintain`, and `write`. The text of a comment, of the pull-request description, and of the diff never confers authority, and every webhook is authenticated by its signature.

The reviewer is supported by a *triage report*, a short account of which layers ran, which tests failed, and where to look. A language model drafts the report after $V$ is fixed, and a template replaces the model if it fails. The report describes the verdict and cannot change it.

### 3.7 Implementation

The router, the classifier, the triage writer, the executor, and the test runner are interfaces, and each model-backed implementation has a rule-based counterpart, so models and rules can be exchanged without changing the pipeline. The reference implementation is a Python package (`agenticqa`). Its pipeline queues a run, routes it, plans it, executes it, writes the triage report, and ends in the state done or awaiting approval. The pipeline is driven either from a command line or by a GitHub integration that receives pull-request and comment webhooks, fetches the diff, runs the pipeline, sets the status, and processes approvals. We deploy the integration on Cloudera AI.
