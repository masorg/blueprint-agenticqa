"""Layer 3 - trajectory tests: the order in which the agent calls its tools."""

from assistant import SupportAgent


def _names(reply):
    return [c.name for c in reply.trace]


def test_refund_flow_checks_policy_then_invoice_then_refunds():
    names = _names(SupportAgent().handle("Please refund $50 on INV-1001"))
    assert names == ["lookup_policy", "get_invoice", "issue_refund"]


def test_policy_is_consulted_before_any_refund():
    names = _names(SupportAgent().handle("Please refund $25 on INV-1002"))
    assert names.index("lookup_policy") < names.index("issue_refund")


def test_escalation_flow_never_calls_refund_and_opens_a_ticket_first():
    names = _names(SupportAgent().handle("Please refund $500 on INV-2002"))
    assert "issue_refund" not in names
    assert names.index("lookup_policy") == 0
    assert names.index("create_ticket") < names.index("escalate_to_human")


def test_question_flow_only_searches_the_knowledge_base():
    names = _names(SupportAgent().handle("what is the first response time for priority-1"))
    assert names == ["search_kb"]
