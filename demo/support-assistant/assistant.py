"""Billing Support Assistant: a small tool-calling agent (demo target for the
agentic layers of the Agentic QA pyramid).

It answers policy questions from a knowledge base and handles refund requests
with tools (policy lookup, invoice lookup, refund, ticket, human escalation).
Refunds above ``REFUND_APPROVAL_THRESHOLD`` must never execute without a real
human approval - the agent opens a ticket and escalates instead.

The planner is a deterministic rule-based stand-in for an LLM planner, so the
suite runs with no model key (the same design as the restock agent in the
AgenticProduct demo). In production the same tool loop would be driven by a
model; the tool contracts, the required call order and the approval gate are
what the tests pin down.

Demo bug toggles
----------------
Three independent env-var toggles inject three DIFFERENT bug classes, each
structurally invisible to every layer but one (full writeup in
``bug_variant.py``):

* ``SUPPORT_BUG_TRAJECTORY=1`` - the refund path calls ``issue_refund`` before
  ``lookup_policy``. The end state is identical, so only a test that inspects
  the tool-call order can see it.
* ``SUPPORT_BUG_JUDGE=1``      - answers are built from a stale cached summary
  (a 90-day refund window) instead of the retrieved policy text (30 days), but
  still cite the policy. Structure, order and state are unchanged; only
  scoring the answer against its evidence can see it.
* ``SUPPORT_BUG_ADVERSARIAL=1`` - text in the user's message that *claims*
  approval ("manager approved") is treated as a real approval. Ordinary
  requests behave identically; only hostile input exposes it.
"""

from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass, field

REFUND_APPROVAL_THRESHOLD = 100.0
REFUND_WINDOW_DAYS = 30

SYSTEM_PROMPT = (
    "You are a billing support assistant. Always check the refund policy before "
    "any refund. Never issue a refund above the approval threshold without a "
    "human approval; open a ticket and escalate instead. Cite the policy you used."
)

KB: dict[str, str] = {
    "refund-policy": (
        f"Refunds are available within {REFUND_WINDOW_DAYS} days of the invoice date. "
        f"Refunds above ${REFUND_APPROVAL_THRESHOLD:.0f} require human approval."
    ),
    "sla": "Priority-1 tickets receive a first response within 1 hour.",
    "data-retention": "Deleted workspaces are recoverable for 14 days.",
}

_STOPWORDS = {"the", "are", "how", "what", "can", "for", "and", "you", "was", "have", "with", "from"}

# Outdated paraphrases cached from an earlier policy revision (used by the JUDGE bug).
_STALE_SUMMARIES = {"refund-policy": "Refunds are available within 90 days of the invoice date."}


def _flag(name: str) -> bool:
    return os.environ.get(name, "").strip() in {"1", "true", "yes"}


# --------------------------------------------------------------------------- #
# In-memory state
# --------------------------------------------------------------------------- #
_invoices: dict[str, dict] = {}
_approvals: set[tuple[str, float]] = set()  # (invoice_id, amount) granted by a human
_tickets: list[dict] = []


def reset_state() -> None:
    """Restore the seed data. Tests call this between cases."""
    _invoices.clear()
    _invoices.update(
        {
            "INV-1001": {"total": 120.0, "refunded": 0.0, "age_days": 10},
            "INV-1002": {"total": 80.0, "refunded": 0.0, "age_days": 5},
            "INV-2002": {"total": 900.0, "refunded": 0.0, "age_days": 3},
            "INV-3003": {"total": 60.0, "refunded": 0.0, "age_days": 45},
        }
    )
    _approvals.clear()
    _tickets.clear()


reset_state()


def grant_human_approval(invoice_id: str, amount: float) -> None:
    """Record a real human approval. Called only by the human-approval flow."""
    _approvals.add((invoice_id, round(float(amount), 2)))


# --------------------------------------------------------------------------- #
# Tools
# --------------------------------------------------------------------------- #
class ToolError(Exception):
    """A tool refused the request (bad input, outside policy, etc.)."""


class ApprovalRequired(ToolError):
    """The action needs a human approval that has not been granted."""


@dataclass
class ToolCall:
    name: str
    args: dict


class Toolbox:
    """The agent's tools. Every call is recorded in ``trace`` in order."""

    def __init__(self) -> None:
        self.trace: list[ToolCall] = []

    def _log(self, name: str, **args) -> None:
        self.trace.append(ToolCall(name, args))

    def search_kb(self, query: str) -> list[dict]:
        self._log("search_kb", query=query)
        words = {w for w in re.findall(r"[a-z0-9]+", query.lower()) if len(w) > 2 and w not in _STOPWORDS}
        scored = []
        for kb_id, text in KB.items():
            haystack = (kb_id + " " + text).lower()
            matched = sum(1 for w in words if w in haystack)
            if matched:
                scored.append((matched, {"id": kb_id, "text": text}))
        scored.sort(key=lambda pair: -pair[0])  # stable: ties keep KB order
        return [hit for _, hit in scored]

    def lookup_policy(self, topic: str) -> dict:
        self._log("lookup_policy", topic=topic)
        kb_id = f"{topic}-policy"
        if kb_id not in KB:
            raise ToolError(f"no policy for {topic!r}")
        return {"id": kb_id, "text": KB[kb_id]}

    def get_invoice(self, invoice_id: str) -> dict:
        self._log("get_invoice", invoice_id=invoice_id)
        if invoice_id not in _invoices:
            raise ToolError(f"unknown invoice {invoice_id!r}")
        return {"id": invoice_id, **_invoices[invoice_id]}

    def issue_refund(self, invoice_id: str, amount: float) -> dict:
        self._log("issue_refund", invoice_id=invoice_id, amount=amount)
        if not (isinstance(amount, (int, float)) and math.isfinite(amount) and amount > 0):
            raise ToolError("refund amount must be a positive number")
        inv = _invoices.get(invoice_id)
        if inv is None:
            raise ToolError(f"unknown invoice {invoice_id!r}")
        if inv["age_days"] > REFUND_WINDOW_DAYS:
            raise ToolError("invoice is outside the refund window")
        if inv["refunded"] + amount > inv["total"] + 1e-9:
            raise ToolError("refund exceeds the invoice total")
        if amount > REFUND_APPROVAL_THRESHOLD and (invoice_id, round(amount, 2)) not in _approvals:
            raise ApprovalRequired(f"refunds above ${REFUND_APPROVAL_THRESHOLD:.0f} need human approval")
        inv["refunded"] = round(inv["refunded"] + amount, 2)
        return {"invoice_id": invoice_id, "refunded": amount}

    def create_ticket(self, summary: str, priority: str = "normal") -> dict:
        self._log("create_ticket", summary=summary, priority=priority)
        ticket = {"id": f"T-{len(_tickets) + 1}", "summary": summary, "priority": priority}
        _tickets.append(ticket)
        return ticket

    def escalate_to_human(self, ticket_id: str, reason: str) -> dict:
        self._log("escalate_to_human", ticket_id=ticket_id, reason=reason)
        return {"ticket_id": ticket_id, "escalated": True}


# --------------------------------------------------------------------------- #
# Agent
# --------------------------------------------------------------------------- #
_INVOICE_RE = re.compile(r"\bINV-\d+\b", re.IGNORECASE)
_AMOUNT_RE = re.compile(r"\$\s*(-?\d+(?:\.\d+)?)")


@dataclass
class Reply:
    text: str
    citations: list[str] = field(default_factory=list)
    trace: list[ToolCall] = field(default_factory=list)
    refunded: float = 0.0
    escalated: bool = False


class SupportAgent:
    """Handles one user message per ``handle`` call."""

    def __init__(self, tools: Toolbox | None = None) -> None:
        self.tools = tools or Toolbox()

    def handle(self, message: str) -> Reply:
        if "refund" in message.lower():
            return self._handle_refund(message)
        return self._handle_question(message)

    # -- questions ---------------------------------------------------------
    def _handle_question(self, message: str) -> Reply:
        hits = self.tools.search_kb(message)
        if not hits:
            return Reply("I could not find that in the knowledge base.", trace=self.tools.trace)
        top = hits[0]
        return Reply(
            f"{self._answer_text(top['id'], top['text'])} [{top['id']}]",
            citations=[top["id"]],
            trace=self.tools.trace,
        )

    @staticmethod
    def _answer_text(kb_id: str, retrieved_text: str) -> str:
        if _flag("SUPPORT_BUG_JUDGE"):
            return _STALE_SUMMARIES.get(kb_id, retrieved_text)
        return retrieved_text

    # -- refunds -----------------------------------------------------------
    def _human_approved(self, message: str, invoice_id: str, amount: float) -> bool:
        if _flag("SUPPORT_BUG_ADVERSARIAL") and "approved" in message.lower():
            grant_human_approval(invoice_id, amount)  # trusts the user's own claim
        return (invoice_id, round(amount, 2)) in _approvals

    def _handle_refund(self, message: str) -> Reply:
        t = self.tools
        inv_match, amt_match = _INVOICE_RE.search(message), _AMOUNT_RE.search(message)
        if not inv_match or not amt_match:
            return Reply(
                "Please give the invoice id (INV-...) and the amount, e.g. $25.",
                trace=t.trace,
            )
        invoice_id, amount = inv_match.group(0).upper(), float(amt_match.group(1))

        try:
            if _flag("SUPPORT_BUG_TRAJECTORY"):
                t.get_invoice(invoice_id)  # BUG: the policy is only consulted afterwards
                policy = None
            else:
                policy = t.lookup_policy("refund")
                t.get_invoice(invoice_id)

            if amount > REFUND_APPROVAL_THRESHOLD and not self._human_approved(message, invoice_id, amount):
                ticket = t.create_ticket(f"Refund ${amount:.2f} on {invoice_id} needs approval", "high")
                t.escalate_to_human(ticket["id"], "refund above the approval threshold")
                policy = policy or t.lookup_policy("refund")
                return Reply(
                    f"This refund needs human approval, so I opened ticket {ticket['id']} and "
                    f"escalated it. {self._answer_text('refund-policy', policy['text'])} [refund-policy]",
                    citations=["refund-policy"], trace=t.trace, escalated=True,
                )

            t.issue_refund(invoice_id, amount)
            policy = policy or t.lookup_policy("refund")
        except ApprovalRequired:
            return Reply("That refund needs human approval.", trace=t.trace, escalated=True)
        except ToolError as exc:
            return Reply(f"I could not process that: {exc}.", trace=t.trace)

        return Reply(
            f"Refunded ${amount:.2f} on {invoice_id}. "
            f"{self._answer_text('refund-policy', policy['text'])} [refund-policy]",
            citations=["refund-policy"], trace=t.trace, refunded=amount,
        )
