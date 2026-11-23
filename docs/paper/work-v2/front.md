## Abstract

AI coding agents now open pull requests faster than people can review them, so the bottleneck has moved from producing a change to merging it. Handing the merge decision to a language model is not a safe answer, because a model can be misled by the text of the change. This paper presents GATE (Gated Assurance with Tiered Evidence), a framework that decides whether a pull request can be merged on the evidence of the project's own tests. GATE extends the test pyramid with layers that are defined by the kind of failure they find, two of them for projects with agentic components. A router and a classifier, which may be language models, plan the layers for a change. The tests run in isolation, and a fixed rule computes the verdict from their outcomes, so that no model output can change it. Flagged changes go to a reviewer whose repository permission is checked, and the verdict is published as a commit status. We evaluate GATE by attacking its planning with hostile routers and classifiers, by mutation testing, and on real projects and real agent-written pull requests. The attack found two bugs in our first version. After the fixes, no attack case changed the verdict in {{advCasesAfter}} cases on two demo services or in {{realCases}} cases on {{realSuitesRan}} real projects. Misleading text changed the router's choice in {{injRoutingFlips}} of {{injRuns}} runs but never the verdict, whereas a language model that decided pass or fail approved a failing change in {{injBaselinePct}}% of misleading runs. All layers together found {{mutRateFull}}% of {{mutValid}} artificial bugs, against {{mutRateUnit}}% for unit tests alone. The verdict equals that of a plain CI check that fails when any test fails, and plain rules matched the models on it. The contribution is the structure around that verdict and its tested safety under model error.

**Index Terms:** pull-request review, test pyramid, LLM agents, software testing, AI engineering

---

## 1. Introduction

AI coding agents have changed how many pull requests are opened. The AIDev dataset holds over 456,000 pull requests from five agents, and one developer in it submitted as many pull requests in three days as in the previous three years [20]. GitHub reports 43.2 million merged pull requests per month in 2025, 23% more than in 2024, and more than one million pull requests created by its Copilot coding agent between May and September 2025 [21]. In telemetry from over 10,000 developers, teams with high AI adoption merged 98% more pull requests [22].

Producing a change is no longer the bottleneck. Merging it is. The same telemetry shows that pull requests grew by 154% in size and that review time grew by 91%, and it names human approval as the limit on throughput [22]. A vision paper on agentic code review describes review as "a growing bottleneck", because AI tools increase the volume of code that needs review [5]. Pull requests from agents are accepted less often than those from people [20]. In a manual study of agent pull requests, only 35.7% of the rejected ones reflected a clear agent failure, and 31.2% were rejected for process reasons [4]. Every one of these changes needs evidence that it is safe to merge, and producing that evidence has not become cheaper at the pace at which changes are produced.

Language models are the obvious tool for reviewing at this volume. The evidence on handing them the decision is not encouraging. In 1.02 million reviewed pull requests from 207 projects, review started by agents was decided sooner, and the speed did not come with better review quality [1]. Review agents can be talked into approving unsafe code by the text of a pull request [2]. People are not a sufficient safeguard either: reviewers approved AI-written code more often as they reviewed more of it, from 30.1% to 36.8% over 11,429 reviews [3]. The merge decision therefore needs evidence that does not depend on the author's account of the change, on a model's reading of it, or on the attention of a tired reviewer.

Tests are such evidence, and a continuous-integration check is the usual way to use them. Two gaps remain, and both become wider for agentic software. The first concerns the test pyramid. The classic pyramid separates tests by the size of the unit under test. It has no place for failures that are specific to agent systems, such as tools called in the wrong order or answers that the evidence does not support, and it does not separate failures on hostile input from other failures. The pyramid has to evolve, so that its layers are defined by the kind of failure that they find. The second gap concerns the change itself. A change has to be matched with the evidence it calls for, and the outcome has to be handled: it has to be clear which kind of failure occurred, whether a model may influence the decision, and when a person has to look. A plain check returns one red or green mark and leaves these questions open.

This paper presents GATE (Gated Assurance with Tiered Evidence), a framework that addresses both gaps. GATE is a composition. It takes test pyramids, property-based testing, required status checks, containers, and human review from existing practice, changes them where a gate with models in it needs a change, and adds the parts that connect them (Section 2). It consists of a test pyramid with five layers and a review pipeline of five stages. A router and a classifier plan the layers for a change and label every test with a layer, and either may be a language model. The tests run in isolation. A fixed rule computes the verdict from the outcomes, the verdict is published as a status on the pull request, and a flagged change goes to a reviewer, who is supported by a triage report. Models advise, and a rule decides.

The contributions are the following.

1. **A framework for gating pull requests on layered evidence (Section 3).** It gives formal definitions of the layer, the plan, and the verdict rule, and a proposition that the verdict is the same for every router and every classifier.
2. **An attack-based test of the planning stage (Section 5.1).** Hostile routers and classifiers found two bugs in the first version that let a failure through. After the fixes, nothing broke in {{advCasesAfter}} cases on two demo services and in {{realCases}} cases on {{realSuitesRan}} real projects. A control with the fixes removed shows that the attack can find the bugs.
3. **Evidence on the layered pyramid (Section 5.2).** On {{mutValid}} artificial bugs, all layers found {{mutRateFull}}%, against {{mutRateUnit}}% for unit tests alone.
4. **Evidence on what models add and how misleading text acts (Sections 5.3 to 5.6).** Seven kinds of misleading text, tried on four models, never changed the verdict. They changed the router's choice in {{injRoutingFlips}} of {{injRuns}} runs. A model that decided pass or fail approved a failing change in {{injBaselinePct}}% of these runs.
5. **A field study and a deployment (Sections 3.7 and 5.7).** On {{fieldAttempted}} real agent-written pull requests, automatic environment setup allowed {{fieldRan}} to be evaluated, and none of the failures was approved. GATE is deployed on Cloudera AI. Section 6.3 lists {{lessonsCount}} mistakes in our own evaluation.

The scope of the claims is limited in two ways. The verdict equals that of a conventional check that fails when any test fails, so GATE finds no more bugs than the tests it runs (Sections 3.5 and 6.2). The plan decides which layers are in scope and how outcomes are grouped, and every discovered test still runs. The value lies in what is built around the verdict, and in the tested safety of that construction when a model errs or is misled.

---

## 2. Related Work

**Review load and LLM reviewers.** Agent-written pull requests are numerous [20, 21] and make review a growing bottleneck [5, 22]. Zhong et al. [1] study over a million reviewed pull requests and find that agents speed up decisions without improving review quality. SEVRA-BENCH [2] shows that review agents can be manipulated by pull-request text, and Kumar et al. [6] warn that artificial benchmarks flatter models. Two studies of human reviewers [3, 4] show that people alone are not a safe backstop. GATE treats the pull-request text as something an attacker may write.

**Testing LLM and agent systems.** Two recent papers propose test pyramids for these systems, and they are the closest to this work. Badagi et al. [7] propose a five-layer pyramid based on the *scope* of what is tested, link events such as a model change to the layers to re-run, and apply pass thresholds in code to scores from LLM judges. In the parts we read, it is a strategy with examples, without measurements and without an approval step. Kohl et al. [8] apply the classic three-level pyramid to agents, record tool calls, and report two case studies. They state that a larger measurement study is needed. GATE defines its layers by *the kind of failure they find*, includes a property-based layer, and measures the design.

**Test generation and quality.** Many papers study how to make agents write tests [13 to 17]. This is a different problem, since GATE uses the tests that a project already has. Generated tests often run but are shallow or flaky [9, 10], and mixing property-based with example-based tests finds more bugs [11, 12], which is one reason for several layers. Work on judging agent trajectories [18, 19] relates to the trajectory layer.

**Where the parts come from.** GATE combines existing ideas. Table 1 states, for each part, what exists, what GATE changes, and what is new.

**Table 1.** Where each part of GATE comes from.

| Part | Existing basis | Change | New |
|---|---|---|---|
| Test pyramid | Pyramids built on the scope of what is tested [7, 8]; property-based tests find extra bugs [11, 12] | Layers are defined by the kind of failure. A property layer and two agentic layers are added | Nothing in the idea; the design is measured |
| Plan | Models used to route and classify; keyword rules | Router and classifier only advise. Each has a rule fallback, and the classifier is checked against file-name cues | The plan never removes a test |
| Execute | Virtual environments and containers | One environment per dependency set; an isolation level set by policy | Nothing in the idea; failures of environment setup are reported |
| Verdict rule | A CI check that fails when any test fails; pass thresholds on judge scores [7] | The verdict is computed over the active layers: those the router chose plus every layer that holds a test | Three guarantees with a short proof, and an attack that tries to break them |
| Pull-request status | Required status checks | The status is set again after each run and after each approval | Nothing in the idea |
| Escalation | Required reviews; LLM reviewers that can be talked into approving [2] | A comment resolves a flagged change only after the commenter's repository permission is checked | Neither a comment nor the pull-request text can grant approval |

What is new is mainly the join. The verdict is computed over the layers that ran, so a model can advise the plan and cannot change the outcome, and this property is tested by attack instead of assumed. Table 2 compares the designs. Commercial LLM review tools are closed and change often, and were not compared. The experiment in which a model decides pass or fail (Section 5.5) stands in for that kind of design.

**Table 2.** Design comparison. "Who decides" refers to pass or fail.

| Design | Who decides | Plan advised per change | Approval step | Safety tested? |
|---|---|---|---|---|
| Required CI test check | the tests | no | branch protection | not needed (no model) |
| LLM PR reviewer (as in [1, 2]) | **the model** | not applicable | none or manual | shown to be breakable [2] |
| AI Assurance Pyramid [7] | thresholds on LLM-judge scores | events trigger layers | runtime oversight only | no |
| Structural testing of agents [8] | the tests | no | no | no (2 case studies) |
| **GATE** | **the tests; a model cannot change it** | yes (layers in scope; every test still runs) | permission checked, on the pull request | **attack, real projects, control** |

---

