# Related Work

*Paper-ready draft of the Related Work section. Written in prose with numbered
citations so it can be dropped into a paper largely as-is; the "Positioning"
paragraph closing each subsection states the claim a reviewer could push back
on. Sources surveyed via the arXiv API on 2026-09-23; update this file as new
work appears or as `docs/13-evaluation-harness.md` produces results that
sharpen a claim below.*

## Agentic and LLM-driven test generation

A large body of recent work treats test generation itself as the object of
improvement, typically evaluated on SWE-bench- or SWT-Bench-style
reproduction-test benchmarks. DPIAgent [1] separates bug diagnosis from
test-writing and reports top results on SWT-Bench Verified; ReProAgent [2] is
a four-stage, tool-augmented agent for reproducing bugs from issue reports at
low per-instance cost; TestAgent [3] models planner/generator/reviewer roles
on human testing workflows to improve execution rates and coverage on Java
projects; GSE [4] evolves globally reusable "skills" that improve
bug-revealing test-generation precision and recall across multiple coding
agents; and Change2Task [5] converts merged pull requests directly into
verified, executable agent tasks (including test generation) from repository
history. Collectively, this line of work optimizes *how well an agent writes
a test*.

**Positioning.** Agentic QA does not compete on test-generation quality — that
is one narrow slice of one layer (unit and, to a lesser extent, trajectory)
inside a five-layer pyramid, not the system's contribution. The pyramid
treats "generate the right test" as necessary but insufficient, a premise the
next subsection's findings support directly.

## Test quality as a distinct, measured weakness

A second, newer line of work asks not whether an agent can produce a test,
but whether that test is *any good*. VibeCheck [6] is a multi-agent empirical
study across heterogeneous repositories evaluating commercial coding agents
(Kiro, Antigravity, Cursor), finding their generated tests are "runnable but
frequently lack strong assertions and meaningful behavioral coverage."
Jhanglani et al. [7] perform a large-scale AST-based comparison and find
agent-generated tests achieve stronger edge-case coverage than human-written
tests, but carry a materially higher risk of flakiness. Tanaka et al. [8]
show, on HumanEval, that combining LLM-generated property-based tests with
example-based tests raises bug-detection rate from 68.75% to 81.25% relative
to either alone.

**Positioning.** This is the strongest direct motivation for a layered
architecture. If LLM-generated tests are individually shallow or flaky, then
an architecture that pairs generated tests with independent property-based
and adversarial checks — and that classifies which layer a given test
belongs to, rather than simply writing more tests in one style — is a
structural response to an empirically documented problem, not a
speculative one. §5 of `docs/13-evaluation-harness.md` reproduces a version
of this claim on Agentic QA's own corpus: three structurally distinct bug
classes, each caught by exactly the layer designed to catch it and invisible
to the others by construction, not by choice of test values.

## Agentic and LLM-based code review: effective, but empirically mixed and exploitable

A third cluster studies LLM-assisted code review directly. Zhong et al. [9]
analyze over one million GitHub pull requests and find that AI-agent
involvement in review speeds up merge decisions but does not clearly improve
review *quality*. Kamalı et al. [10] present a vision paper arguing for a
five-stage agentic review framework in which the human reviewer becomes a
"supervisory operator" of one or more review agents, rather than the
reviewer of first resort. SEVRA-BENCH [11] demonstrates, empirically, that
LLM code-review agents can be socially engineered into approving vulnerable
code through persuasively written pull-request narratives. Kumar et al. [12]
show that a smaller model can outperform a larger one on code-review tasks,
and — a methodological caution relevant to any evaluation in this space —
that synthetic review benchmarks "dramatically overestimate model
capability" relative to real pull requests. Gao et al. [13] find that most
AI-co-authored pull requests from non-owner contributors are, in practice,
merged quickly with minimal or no explicit human review.

**Positioning.** This cluster identifies the gap Agentic QA occupies. No work
surveyed combines (a) a deterministic guardrail wrapped around specific LLM
decision points — routing and final verdict — rather than trusting the
model's output directly, (b) a human-approval gate wired natively into the
CI surface itself (a GitHub commit status plus an `/approve` comment, with
the commenter's write access verified against the GitHub API rather than
trusting the comment text), and (c) a real production deployment with
reported engineering failure modes. Kamalı et al.'s "supervisory operator"
framing [10] is presented as a vision; Agentic QA is offered here as a
working instance of that pattern, and SEVRA-BENCH's manipulation result [11]
is a direct empirical argument for why a deterministic guardrail — rather
than a second LLM asked to police the first — is the right shape for that
supervision to take.

## Property-based testing with LLMs, and trajectory evaluation

ChekProp [14] and related work generate property-based tests for
cyber-physical and general-purpose systems using LLM-extracted invariants,
but this work is not wired into a pull-request gating pipeline with a human
approval workflow — reinforcing that the *pyramid-as-router* framing, not any
single layer's generation technique, is the contribution being made here.
Separately, DynSTEER [15] and trajectory-aware benchmark subset selection
[16] optimize how agent trajectories are scored and at what cost; this is
relevant to a future refinement of the trajectory and LLM-as-judge layers,
not a dependency of the present architecture.

## Summary

Taken together, the surveyed work supports two claims this paper makes: that
LLM-generated tests need an independent, structurally distinct check rather
than more of the same generation (§2), and that LLM-based review needs a
deterministic supervisory boundary rather than unmediated trust in the
model's own verdict (§3). Agentic QA's contribution is the combination of
both as a single, deployed system — a five-layer test pyramid used as a
router whose applicability decisions an LLM proposes but a deterministic
guardrail bounds, gated by a human-approval step wired into the version
control system's own native surface, with production deployment experience
reported alongside the architecture rather than left implicit.

## References

1. Liu, H. et al. "DPIAgent: Divide, Protocol, Isolate for Agentic
   Reproduction Test Generation." 2026-08.
2. Zhang, Q. et al. "ReProAgent: Tool-Augmented Multi-Stage Agentic
   Generation of Bug Reproduction Tests from Issue Reports." 2026-07.
3. Zhang, Q. et al. "Multi-Agent LLM Collaboration for Unit Test Generation
   via Human-Testing-Inspired Workflows" (TestAgent). 2026-07.
4. Yang, C. et al. "Learning Globally Reusable Skills for Coding Agents."
   2026-08.
5. Qi, H. et al. "Change2Task: From Repository Changes to Executable Coding
   Agent Tasks and Environments." 2026-07.
6. Tabassum, A. et al. "VibeCheck: Assessing the Quality of LLM-Generated
   Unit Tests: A Multi-agent Empirical Study across Heterogeneous
   Repositories." 2026-09.
7. Jhanglani, P. et al. "Beyond Test Presence: Assessing the Quality and
   Robustness of Agent-Generated Tests in Open-Source Projects." 2026-07.
8. Tanaka, H. et al. "Understanding the Characteristics of LLM-Generated
   Property-Based Tests in Exploring Edge Cases." 2025-10.
9. Zhong, S., Noei, S., Adams, B., Zou, Y. "From Human-Centric to Agentic
   Code Review: The Impact of Different Generations of Generative AI
   Technology on Review Quality." 2026-07.
10. Kamalı, H. Ö., Tuna, E., Haratian, V., Tüzün, E. "Rethinking Code Review
    in the Age of AI: A Vision for Agentic Code Review." 2026-05.
11. Melo, R., Fogliato, R., Zhou, S., Thaker, P., Wu, Z. S. "SEVRA-BENCH:
    Social Engineering of Vulnerabilities in Review Agents." 2026-06.
12. Kumar, S. P., Bararia, S., Raj, K. "Bigger Isn't Always Better: A
    Comparative Evaluation of LLMs for Automated Code Review." 2026-04.
13. Gao, H., Banyongrakkul, P., Guan, H., Zahedi, M., Treude, C. "On
    Autopilot? An Empirical Study of Human-AI Teaming and Review Practices
    in Open Source." 2026-01.
14. Etemadi, K., Sirjani, M., Moghadam, M. H., Strandberg, P., Pettersson, P.
    "LLM-based Property-based Test Generation for Guardrailing Cyber-Physical
    Systems" (ChekProp). 2025-05.
15. Shi, Z. et al. "DynSTEER: Dynamic Stage-wise Trajectory Evaluation and
    Execution-time Review for Agents." 2026-09.
16. Ayyad, M., Wang, Z., Shin, J., Zou, Y., Adams, B. "Trajectory-Aware
    Benchmark Subset Selection for Cost-Efficient Software Engineering Agent
    Regression Testing." 2026-09.
