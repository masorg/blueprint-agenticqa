"""Layer 1 - unit tests: the tools and the agent's observable results, in isolation.

None of these assert the order of tool calls or the wording of an answer's
policy claim; they pin down results and state.
"""

import pytest

import assistant
from assistant import (
    KB, REFUND_APPROVAL_THRESHOLD, ApprovalRequired, SupportAgent, ToolError, Toolbox,
    grant_human_approval,
)


def test_search_kb_finds_the_retention_entry():
    hits = Toolbox().search_kb("how long can I recover a deleted workspace")
    assert any(h["id"] == "data-retention" for h in hits)


def test_search_kb_returns_nothing_for_unrelated_text():
    assert Toolbox().search_kb("zzzz qqqq") == []


def test_lookup_policy_returns_the_refund_policy():
    assert Toolbox().lookup_policy("refund")["id"] == "refund-policy"


def test_lookup_policy_unknown_topic_is_an_error():
    with pytest.raises(ToolError):
        Toolbox().lookup_policy("weather")


def test_small_refund_is_issued_and_recorded():
    reply = SupportAgent().handle("Please refund $50.00 on INV-1001")
    assert reply.refunded == 50.0
    assert assistant._invoices["INV-1001"]["refunded"] == 50.0
    assert "INV-1001" in reply.text


def test_refund_above_threshold_escalates_instead_of_refunding():
    reply = SupportAgent().handle("Please refund $500 on INV-2002")
    assert reply.escalated and reply.refunded == 0.0
    assert assistant._invoices["INV-2002"]["refunded"] == 0.0
    assert len(assistant._tickets) == 1


def test_refund_above_threshold_succeeds_once_a_human_approves():
    grant_human_approval("INV-2002", 500)
    reply = SupportAgent().handle("Please refund $500 on INV-2002")
    assert reply.refunded == 500.0


def test_refund_outside_the_window_is_declined():
    reply = SupportAgent().handle("Please refund $10 on INV-3003")
    assert reply.refunded == 0.0 and "could not process" in reply.text


def test_issue_refund_tool_enforces_the_approval_gate_itself():
    with pytest.raises(ApprovalRequired):
        Toolbox().issue_refund("INV-2002", REFUND_APPROVAL_THRESHOLD + 1)


def test_answer_for_a_known_topic_cites_its_source():
    reply = SupportAgent().handle("how long are deleted workspaces recoverable")
    assert reply.citations == ["data-retention"]
    assert "[data-retention]" in reply.text


def test_missing_details_asks_instead_of_acting():
    reply = SupportAgent().handle("I want a refund")
    assert reply.refunded == 0.0 and "invoice id" in reply.text.lower()
