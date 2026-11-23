# Agentic QA — demo recording script

A ~4–5 minute screen-recorded walkthrough. The arc: a pull request arrives, Agentic QA
**routes** it to the testing-pyramid layers that apply, **runs** them live, **triages** the
result, and either **auto-approves** or **escalates to a human** — then the differentiator:
the same flow on an *agentic* system lights up two extra layers no ordinary CI has.

- **Audience:** engineering leaders + platform/ML teams evaluating a PR quality gate on Cloudera.
- **Runtime:** ~4–5 min. **Tone:** calm, product-led. Let the live pyramid do the talking.
- **The one thing to remember:** the code — not the LLM — decides the gate. The LLM writes the story; a deterministic guardrail computes the verdict.

---

## Run of show (at a glance)

| # | Scene | ~Time | Beat |
|---|-------|-------|------|
| 1 | Cold open — the problem | 0:20 | Why a reviewer's gut call isn't enough |
| 2 | The dashboard + the pyramid | 0:40 | Orient: five layers, two agentic-only |
| 3 | **Clean change, live** | 0:50 | New review → pyramid fills → auto-approved |
| 4 | **Regression → human gate** | 1:10 | A layer fails → triage → reviewer approves |
| 5 | **The agentic differentiator** | 0:45 | Recommender-agent PR → all 5 layers engage |
| 6 | GitHub-native + Cloudera close | 0:35 | Commit status, `/approve`, runs on CML |

---

## Pre-flight checklist (before you hit record)

1. **Fresh server with seed data** (clean, known state):
   ```bash
   cd agentic-qa
   AGENTICQA_SEED_SAMPLES=1 AGENTICQA_LLM_PROVIDER=stub \
     PYTHONPATH=src /opt/anaconda3/envs/agenticqa/bin/python \
     -m uvicorn agenticqa.main:app --host 127.0.0.1 --port 8799
   ```
   Open **http://127.0.0.1:8799/**.
2. **Triage narrative — pick one:**
   - `LLM_PROVIDER=stub` (default above): deterministic, zero cost, instant. Triage text is templated.
   - `LLM_PROVIDER=openai` + `OPENAI_API_KEY=…`: the triage report is genuinely LLM-authored (more impressive on camera). Costs a few tokens per run — fine for a recording; do at most the two live runs.
   *Either way the verdict is identical — that's the guardrail point.*
3. **Browser:** full-screen, 100–110% zoom, bookmarks bar hidden. Pick a theme and commit to it (dark reads "techy"; light reads "enterprise" — the toggle is top-right if you want to show it once).
4. **Cloudera logo** is in the sidebar footer — good, leave it visible in frame.
5. **Cursor:** move deliberately; pause ~1s after each click so cuts are clean.
6. Do a dry run once so the two live runs' timing (~10–15s each) doesn't surprise you.

> Note: the two live runs you trigger will append to the dashboard — that's fine and looks real. If you want a pristine list, restart the server between takes.

---

## Scene 1 — Cold open (≈20s)

**On screen:** Start on the Overview dashboard, already loaded. Slow, no clicking yet.

**Narration:**
> "Every pull request is a small bet: does this change work, and is it safe to ship? Usually that's a reviewer's gut call under time pressure. **Agentic QA** turns it into a living quality gate — it runs the right tests for each change, shows you exactly what happened, and only pulls a human in when it actually matters."

---

## Scene 2 — The dashboard and the pyramid (≈40s)

**On screen:**
- Gesture across the top stat cards (**In progress · Needs review · Blocked · Pass rate**).
- Move to the hero **testing pyramid**. Hover/point along the five tiers bottom → top.

**Narration:**
> "This is the testing pyramid. At the base, fast **unit** checks; above them **property-based** tests that hold invariants across generated inputs; at the apex, **adversarial and safety** probes that run on every change. And these two —" *(point to Trajectory and LLM-as-judge, the dimmed tiers)* "— only light up when the change is itself an **agentic or LLM system**: is the agent calling its tools in the right order, and is its output good? For an ordinary service they stay dark. Routing decides which layers apply — and then it runs them."

---

## Scene 3 — Clean change, live (≈50s)  ★ hero moment

**On screen:**
1. Click **New review** (top-right).
2. In the modal, leave **Clean change** selected. Click **Start review**.
3. **Stay on the run detail and let it stream.** Watch tiers go blue (running) → green (passed) and the tallies climb. Don't rush.
4. When it settles: point to the **Auto-approved** verdict and the green Layers list.

**Narration:**
> "Let's open a review on our Inventory & Orders API. Watch the pyramid — this is live, streaming from the actual test run. Unit tests pass… property tests pass… adversarial probes pass. The two agentic layers stay dark, because this is a plain service. All applicable layers are green, so Agentic QA **auto-approves** — no human needed. That's the whole point: when it's routine, it just clears."

---

## Scene 4 — Regression → human gate (≈1:10)  ★ the story

**On screen:**
1. Click **New review** → select **Regression** (*off-by-one in stock check*) → **Start review**.
2. Let it stream. A layer turns **red**. The verdict resolves to **Needs human review** and the run lands in the review gate.
3. Scroll the **triage report** — read a line aloud. Click the failing layer in the pyramid to show the failing test(s).
4. Click **Approve** (as the reviewer). Show the verdict flip to **Approved / Done**.

**Narration:**
> "Now a subtler change — an off-by-one in the stock check. Same flow, but this time a test goes red. Notice the verdict: **needs human review**. This is deliberate — the model wrote the summary you're reading, but a **deterministic rule** set the gate: if any applicable layer fails, a human decides. No talking the model into shipping a regression. Here's the triage — plain language on what broke and where. As the reviewer, I read it, agree it's worth a look, and **approve**. The gate clears, and the decision is recorded."

*(If time: mention `/reject` is symmetric and records who decided.)*

---

## Scene 5 — The agentic differentiator (≈45s)  ★ the wow

**On screen:**
1. Go to the run list (sidebar) and open **"Add re-ranking tool to recommender agent"** (the agentic run).
2. On its detail, the pyramid shows **all five tiers active** — including **Trajectory** and **LLM-as-judge**.
3. Point to those two tiers and their results.

**Narration:**
> "Here's what makes this different. This PR changes an **AI agent** — a recommender. Now the two layers that were dark come alive. **Trajectory** checks the agent takes the right tool-call path — search before write, no skipped steps. **LLM-as-judge** scores the output against a rubric — is the answer grounded, on-format, within policy? Ordinary CI has no idea how to test an agent. Agentic QA does — and it's the *same* pyramid, the *same* gate."

---

## Scene 6 — GitHub-native + Cloudera close (≈35s)

**On screen:** *(if the app is deployed at a public URL / CML, show a real PR; otherwise narrate over the dashboard and the Cloudera logo in frame.)*
- Show (or describe) the PR's **`agentic-qa/review` commit status** and a reviewer typing **`/approve`** on the PR.

**Narration:**
> "None of this asks your developers to leave GitHub. Open a pull request and Agentic QA posts a **commit status** right on it; approve or reject with a comment — the write access is checked against GitHub, never the comment text. And it all runs as a **Cloudera CML application** — one process, your models, your data, installed once across the org. From pull request to a safe, explainable decision — that's Agentic QA."

**On screen to end:** rest on the dashboard with the Cloudera logo visible. Hold 2s. Cut.

---

## Recording tips & fallbacks

- **The live runs are the star.** Don't cut away while the pyramid fills — that motion is the demo. If a run finishes too fast to narrate, start talking before you click **Start review**.
- **If a live run errors on camera:** you have the seeded runs as a safety net — open `e5f6a1b2c3d4` (auto-approved) or the failed `d4e5f6a1b2c3` (blocked) to tell the same story from static data.
- **Keep the verdict language on screen** when you say "the code decides, not the model" — it's the most credible line in the demo.
- **Two takes minimum:** one for timing, one for the keeper. Trim dead air between clicks in post.
- **Don't show** the `.env` / any API key on screen; keep the terminal out of frame once the server's up.
- Segments 3, 4, 5 are the core; if you must trim to ~3 min, drop Scene 6 to a single sentence over the dashboard.
