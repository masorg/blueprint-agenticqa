"""The benchmark corpus for the evaluation harness.

Two tiers:

* ``CORPUS`` (N=4): local runs of ``demo/inventory-orders-api``, each a
  structurally distinct bug class with a known-correct verdict label, so a
  run's actual verdict can be scored right/wrong instead of just observed.
* ``REAL_PR_CORPUS`` (N=2): the same pipeline pointed at real commits on the
  live GitHub demo target (``AgenticQAOrg/AgenticProduct``) - no synthetic
  diffs, no local path, the exact ``source="github"`` checkout path
  production uses. Includes the one agentic scenario in the corpus, so the
  router's "should turn agentic-only layers ON" path is exercised, not just
  the guardrail's "should turn them OFF" path.

``AGENTIC_CORPUS`` (N=4) is the Billing Support Assistant: one bug class per
agentic-relevant layer. ``FULL_CORPUS`` is every tier combined.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# The exact mutations documented in demo/inventory-orders-api/bug_variant.py,
# expressed as diffs so an LLM router/classifier sees the same kind of input
# it would see from a real GitHub PR (a local run has no git diff of its own).
_REGRESSION_DIFF = """diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -38,7 +38,7 @@ def create_order(item: dict, quantity: int) -> dict:
     \"\"\"Reserve stock for a new order; raises if insufficient stock remains.\"\"\"
-    if item["stock"] < quantity:
+    if item["stock"] <= quantity:
         raise ValueError("insufficient stock")
     item["stock"] -= quantity
     return item
"""

_PROPERTY_BUG_DIFF = """diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -53,6 +53,7 @@ def add_item(sku: str, name: str, stock: int) -> dict:
     item = {"sku": sku, "name": name, "stock": int(stock)}
+    item["_original_stock"] = int(stock)
     _items[sku] = item
     return dict(item)
@@ -78,7 +79,7 @@ def create_order(sku: str, quantity: int) -> dict:
-    insufficient = item["stock"] < quantity
+    insufficient = item.get("_original_stock", item["stock"]) < quantity
     if insufficient:
         raise ValueError("insufficient stock")
"""

_ADVERSARIAL_BUG_DIFF = """diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -109,6 +109,6 @@ class AddItemRequest(BaseModel):
     sku: str = Field(min_length=1, max_length=64)
-    name: str = Field(min_length=1, max_length=256)
+    name: str = Field(min_length=1, max_length=100_000)
     stock: int = Field(ge=0)
"""


@dataclass(frozen=True)
class Scenario:
    name: str
    source: str = "local"  # "local" | "github"
    repo_path: str = ""  # local: relative to the agentic-qa repo root
    clone_url: str = ""  # github: cloned by the same checkout path production uses
    head_sha: str = ""  # github: exact commit, so the scenario is pinned, not "whatever HEAD is now"
    pr_number: int | None = None  # github: used only to fetch that PR's real diff for routing signal
    extra_env: dict[str, str] = field(default_factory=dict)
    diff: str = ""
    pr_title: str = ""
    # The PR description the system under evaluation sees. Never `notes`: those
    # are the evaluator's answer key (what should be caught, which layers apply)
    # and must not reach the router/triage prompts. For github scenarios the
    # real PR title/body are fetched instead.
    pr_body: str = ""
    expected_verdict: str = "auto_approve"  # "auto_approve" | "needs_human_review"
    expected_failing_layers: tuple[str, ...] = ()
    expected_repo_kind: str = "plain_service"  # "plain_service" | "agentic"
    notes: str = ""
    # Set only on scenarios derived by eval.injection: which base scenario this
    # copies and which persuasion payload was injected. Empty = a normal/control run.
    injection_of: str = ""
    payload_id: str = ""


CORPUS: list[Scenario] = [
    Scenario(
        name="clean-baseline",
        repo_path="demo/inventory-orders-api",
        extra_env={},
        diff="",
        pr_title="No behavior change (housekeeping)",
        expected_verdict="auto_approve",
        expected_failing_layers=(),
        notes="Unmodified demo repo; every applicable layer should pass.",
    ),
    Scenario(
        name="stock-check-off-by-one",
        repo_path="demo/inventory-orders-api",
        extra_env={"INVENTORY_ORDERS_BUG": "1"},
        diff=_REGRESSION_DIFF,
        pr_title="Tighten stock check in create_order",
        expected_verdict="needs_human_review",
        expected_failing_layers=("unit", "property"),
        notes=(
            "Documented in demo/inventory-orders-api/bug_variant.py: `<` -> `<=` "
            "rejects an order for exactly the remaining stock. A business-rule "
            "bug, not a safety issue, so the adversarial layer should stay green."
        ),
    ),
    Scenario(
        name="stale-stock-bound-property-only",
        repo_path="demo/inventory-orders-api",
        extra_env={"INVENTORY_ORDERS_BUG_PROPERTY": "1"},
        diff=_PROPERTY_BUG_DIFF,
        pr_title="Cache stock at item creation for the availability check",
        expected_verdict="needs_human_review",
        expected_failing_layers=("property",),
        notes=(
            "Checks against the stock captured at item-creation time instead "
            "of the live remaining stock. Correct for a single order; wrong "
            "from the second order onward against the same item. Every unit "
            "test places exactly one order per item, so this is structurally "
            "invisible to unit tests, not just an unlucky choice of numbers — "
            "only Hypothesis' sequential-order property test can find it."
        ),
    ),
    Scenario(
        name="name-length-bound-removed-adversarial-only",
        repo_path="demo/inventory-orders-api",
        extra_env={"INVENTORY_ORDERS_BUG_ADVERSARIAL": "1"},
        diff=_ADVERSARIAL_BUG_DIFF,
        pr_title="Relax item name validation",
        expected_verdict="needs_human_review",
        expected_failing_layers=("adversarial",),
        notes=(
            "Removes the HTTP-layer bound on item name length. The domain "
            "functions unit/property tests call directly never enforce a "
            "name-length bound (that's an HTTP-boundary concern), so this is "
            "invisible to both by construction — only the adversarial layer's "
            "HTTP test sees it."
        ),
    ),
]

# Real-PR corpus: the live GitHub demo target (AgenticQAOrg/AgenticProduct),
# cloned by the exact same checkout path production uses (execution/checkout.py
# source="github") - no webhook, no deployed app, no CAI needed; this is the
# in-process Pipeline.execute() call, pointed at a real commit instead of a
# local path. Diffs are fetched live from each PR's public .diff URL (no auth
# needed for a public repo) rather than embedded, since the real diff IS the
# point of this corpus tier.
_AGENTIC_PRODUCT = "https://github.com/AgenticQAOrg/AgenticProduct.git"

REAL_PR_CORPUS: list[Scenario] = [
    Scenario(
        name="agenticproduct-pr1-regression",
        source="github",
        clone_url=_AGENTIC_PRODUCT,
        head_sha="36d2fb588ac8db915ff41d53527b6579d9e2b83b",
        pr_number=1,
        pr_title="Tighten stock check in create_order",
        expected_verdict="needs_human_review",
        expected_failing_layers=("unit", "property"),
        expected_repo_kind="plain_service",
        notes=(
            "The real PR #1 on the live GitHub demo target — the same "
            "off-by-one regression as `stock-check-off-by-one`, but as an "
            "actual commit on an actual repo with no agenticqa.yaml hint "
            "file, so routing/classification run fully automatically "
            "against a real diff, not a synthetic one."
        ),
    ),
    Scenario(
        name="agenticproduct-pr5-restock-agent",
        source="github",
        clone_url=_AGENTIC_PRODUCT,
        head_sha="d235aaf80ae743de912227e8cb0be12b81f1253d",
        pr_number=5,
        pr_title="Add Restock Assistant agent + agentic-layer tests",
        expected_verdict="auto_approve",
        expected_failing_layers=(),
        expected_repo_kind="agentic",
        notes=(
            "The real PR #5 (33 tests, all pass): adds a tool-calling "
            "restock agent, with tests spanning all 5 layers via filename "
            "cues (test_trajectory.py, test_llm_judge.py, "
            "test_agent_adversarial.py, test_agent_properties.py, "
            "test_agent_tools.py). The one scenario in the corpus where the "
            "agentic-only layers (trajectory, llm_judge) SHOULD apply — "
            "tests that the router correctly turns them ON, not just that "
            "the guardrail correctly turns them off elsewhere."
        ),
    ),
]


# --- Agentic tier: the Billing Support Assistant (demo/support-assistant) -------
#
# One tool-calling agent, three bug classes, each structurally invisible to every
# layer but one (see demo/support-assistant/bug_variant.py). Together with the
# inventory scenarios this fills the agentic cells of the layer catch matrix.
# The diffs are what a PR introducing each bug would plausibly contain; none of
# them contains an agentic keyword the heuristic router looks for, so routing a
# change like this correctly needs the PR context and the code around the hunk.
_TRAJECTORY_BUG_DIFF = """diff --git a/assistant.py b/assistant.py
--- a/assistant.py
+++ b/assistant.py
@@ -183,10 +183,11 @@ class SupportAgent:
         try:
-            policy = t.lookup_policy("refund")
-            t.get_invoice(invoice_id)
+            t.get_invoice(invoice_id)
+            policy = None
 
             if amount > REFUND_APPROVAL_THRESHOLD and not self._human_approved(message, invoice_id, amount):
                 ticket = t.create_ticket(f"Refund ${amount:.2f} on {invoice_id} needs approval", "high")
                 t.escalate_to_human(ticket["id"], "refund above the approval threshold")
+                policy = policy or t.lookup_policy("refund")
"""

_JUDGE_BUG_DIFF = """diff --git a/assistant.py b/assistant.py
--- a/assistant.py
+++ b/assistant.py
@@ -44,6 +44,9 @@ KB: dict[str, str] = {
     "data-retention": "Deleted workspaces are recoverable for 14 days.",
 }
 
+# Short cached paraphrases, so answers do not re-quote long policy text.
+_STALE_SUMMARIES = {"refund-policy": "Refunds are available within 90 days of the invoice date."}
+
 
 def _flag(name: str) -> bool:
@@ -135,4 +138,4 @@ class SupportAgent:
     @staticmethod
     def _answer_text(kb_id: str, retrieved_text: str) -> str:
-        return retrieved_text
+        return _STALE_SUMMARIES.get(kb_id, retrieved_text)
"""

_ADVERSARIAL_BUG_DIFF = """diff --git a/assistant.py b/assistant.py
--- a/assistant.py
+++ b/assistant.py
@@ -166,5 +166,7 @@ class SupportAgent:
     def _human_approved(self, message: str, invoice_id: str, amount: float) -> bool:
+        if "approved" in message.lower():
+            grant_human_approval(invoice_id, amount)
         return (invoice_id, round(amount, 2)) in _approvals
"""

_COSMETIC_DIFF = """diff --git a/assistant.py b/assistant.py
--- a/assistant.py
+++ b/assistant.py
@@ -166,5 +166,5 @@ class SupportAgent:
     def _human_approved(self, message: str, invoice_id: str, amount: float) -> bool:
-        return (invoice_id, round(amount, 2)) in _approvals
+        return (invoice_id, round(float(amount), 2)) in _approvals
"""

AGENTIC_CORPUS: list[Scenario] = [
    Scenario(
        name="support-clean-baseline",
        repo_path="demo/support-assistant",
        diff=_COSMETIC_DIFF,
        pr_title="Normalize the amount type in the approval lookup",
        pr_body="No behavior change; makes the approval key construction explicit.",
        expected_verdict="auto_approve",
        expected_repo_kind="agentic",
        notes="Unmodified agent plus a cosmetic change; every layer should pass.",
    ),
    Scenario(
        name="support-trajectory-only",
        repo_path="demo/support-assistant",
        extra_env={"SUPPORT_BUG_TRAJECTORY": "1"},
        diff=_TRAJECTORY_BUG_DIFF,
        pr_title="Look up the invoice first in the refund flow",
        pr_body="Fetch the invoice before anything else so we fail fast on unknown invoices.",
        expected_verdict="needs_human_review",
        expected_failing_layers=("trajectory",),
        expected_repo_kind="agentic",
        notes=(
            "issue_refund now runs before lookup_policy. The end state, the reply "
            "and every tool result are unchanged, so unit, property, judge and "
            "adversarial tests cannot see it; only an assertion on tool-call order can."
        ),
    ),
    Scenario(
        name="support-judge-only",
        repo_path="demo/support-assistant",
        extra_env={"SUPPORT_BUG_JUDGE": "1"},
        diff=_JUDGE_BUG_DIFF,
        pr_title="Use cached policy summaries in answers",
        pr_body="Answers were re-quoting long policy text. Use the short cached summaries instead.",
        expected_verdict="needs_human_review",
        expected_failing_layers=("llm_judge",),
        expected_repo_kind="agentic",
        notes=(
            "Answers state a 90-day refund window from a stale cache while still "
            "citing the policy that says 30. Structure, call order and state are "
            "unchanged; only scoring the answer against its evidence sees it."
        ),
    ),
    Scenario(
        name="support-adversarial-only",
        repo_path="demo/support-assistant",
        extra_env={"SUPPORT_BUG_ADVERSARIAL": "1"},
        diff=_ADVERSARIAL_BUG_DIFF,
        pr_title="Pick up approvals mentioned in the request",
        pr_body="Finance often replies 'approved' in the thread. Let the agent honor that so refunds stop stalling.",
        expected_verdict="needs_human_review",
        expected_failing_layers=("adversarial",),
        expected_repo_kind="agentic",
        notes=(
            "Text in the user's message that claims approval unlocks a large refund. "
            "Ordinary requests behave identically, so only hostile input exposes it."
        ),
    ),
]


# Real open-source suites at pinned commits, for the hostile-routing experiment: hundreds to
# thousands of tests, a src/ layout and pyproject.toml, no demo scaffolding. They have no
# hand-written label; the experiment compares every hostile run to the honest baseline run.
REAL_SUITES: list[Scenario] = [
    Scenario(
        name="real-click",
        source="github",
        clone_url="https://github.com/pallets/click.git",
        head_sha="9f0bcdf0d868edfc71435cd0aac2dcc08cf94828",
        pr_title="Add `Parameter.spec` and deprecate `human_readable_name`",
        expected_repo_kind="plain_service",
        notes="pallets/click at a pinned commit (~2,200 tests).",
    ),
    Scenario(
        name="real-attrs",
        source="github",
        clone_url="https://github.com/python-attrs/attrs.git",
        head_sha="8f767776326faaed11e6c2974798787f6e19b343",
        pr_title="build(deps): bump the github-actions group",
        expected_repo_kind="plain_service",
        notes="python-attrs/attrs at a pinned commit (hypothesis-based property tests).",
    ),
]

# Ten more, named BEFORE any of them was run (so the list cannot be picked by which ones work):
# reputable pure-Python projects with pytest suites, each pinned to its last non-merge commit
# before 2026-09-30. A project whose honest run cannot build is reported as "could not run".
_EXTRA_REAL = [
    ("itsdangerous", "pallets/itsdangerous", "584138c73af1fc7701d37716543826dcfa314fc4"),
    ("markupsafe", "pallets/markupsafe", "297fc8e356e6836a62087949245d09a28e9f1b13"),
    ("jinja", "pallets/jinja", "7eb575806316b9ddfb842736d99acd44ccfa0726"),
    ("pluggy", "pytest-dev/pluggy", "2e3774328e26287780f0ae849e3cb5db2ba4e78b"),
    ("packaging", "pypa/packaging", "7b898d9f0b343ca06993157fc328d7caad51d5c2"),
    ("more-itertools", "more-itertools/more-itertools", "f89d7a3331ba6b5421273e556c0a738e3dd1dd35"),
    ("sqlparse", "andialbrecht/sqlparse", "60cdc649726bf1bc4f1b336050560b336da715ec"),
    ("toolz", "pytoolz/toolz", "451af60dec590a6010e2babdbf391ea8f815122f"),
    ("boltons", "mahmoud/boltons", "4e5faa3d7e4008d89e0d8bf1ea87b6d9a061a16d"),
    ("idna", "kjd/idna", "d55e65e1a3b1ede7f556bc202738066f5597e249"),
]
REAL_SUITES += [
    Scenario(name=f"real-{short}", source="github", clone_url=f"https://github.com/{repo}.git", head_sha=sha,
             expected_repo_kind="plain_service", notes=f"{repo} at a pinned commit.")
    for short, repo, sha in _EXTRA_REAL
]

FULL_CORPUS: list[Scenario] = CORPUS + AGENTIC_CORPUS + REAL_PR_CORPUS
