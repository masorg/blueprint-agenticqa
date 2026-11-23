"""Layer 2 - property-based tests (Hypothesis) over agent and tool invariants."""

from hypothesis import given, settings
from hypothesis import strategies as st

import assistant
from assistant import KB, REFUND_APPROVAL_THRESHOLD, SupportAgent, Toolbox

_amounts = st.floats(min_value=0.01, max_value=900, allow_nan=False, allow_infinity=False)


@settings(deadline=None, max_examples=80)
@given(amounts=st.lists(_amounts, max_size=8))
def test_property_total_refunded_never_exceeds_the_invoice_total(amounts):
    assistant.reset_state()
    agent = SupportAgent()
    for a in amounts:
        agent.handle(f"Please refund ${a:.2f} on INV-1001")
    inv = assistant._invoices["INV-1001"]
    assert 0 <= inv["refunded"] <= inv["total"] + 1e-9


@settings(deadline=None, max_examples=80)
@given(amount=_amounts)
def test_property_escalates_exactly_when_above_the_threshold(amount):
    assistant.reset_state()
    amount = round(amount, 2)
    reply = SupportAgent().handle(f"Please refund ${amount:.2f} on INV-2002")
    if amount > REFUND_APPROVAL_THRESHOLD:
        assert reply.escalated and reply.refunded == 0.0
    else:
        assert reply.refunded == amount and not reply.escalated


@settings(deadline=None, max_examples=80)
@given(amount=_amounts)
def test_property_no_refund_call_above_the_threshold_without_approval(amount):
    assistant.reset_state()
    amount = round(amount, 2)
    reply = SupportAgent().handle(f"Please refund ${amount:.2f} on INV-2002")
    called_refund = any(c.name == "issue_refund" for c in reply.trace)
    assert not (amount > REFUND_APPROVAL_THRESHOLD and called_refund)


@settings(deadline=None, max_examples=80)
@given(query=st.text(max_size=40))
def test_property_search_only_returns_real_kb_entries(query):
    for hit in Toolbox().search_kb(query):
        assert KB[hit["id"]] == hit["text"]
