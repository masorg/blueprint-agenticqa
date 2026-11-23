"""Layer 4 - LLM-as-judge: score answer quality (grounding + citation) against
its evidence. The judge here is the deterministic stand-in in grounding.py."""

from assistant import KB, SupportAgent

from grounding import score_grounding


def test_policy_answer_is_grounded_in_the_cited_source():
    reply = SupportAgent().handle("how long are deleted workspaces recoverable")
    g = score_grounding(reply.text, reply.citations, [KB[c] for c in reply.citations])
    assert g.passes, g


def test_refund_confirmation_is_grounded_in_policy_and_the_refunded_amount():
    reply = SupportAgent().handle("Please refund $50.00 on INV-1001")
    evidence = [KB[c] for c in reply.citations] + [f"${reply.refunded:.2f}"]
    g = score_grounding(reply.text, reply.citations, evidence)
    assert g.passes, g


def test_escalation_message_states_only_what_the_policy_says():
    reply = SupportAgent().handle("Please refund $500 on INV-2002")
    g = score_grounding(reply.text, reply.citations, [KB[c] for c in reply.citations])
    assert g.passes, g
