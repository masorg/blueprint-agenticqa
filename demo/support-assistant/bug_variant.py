"""Documented reference for the three injected bugs - NOT imported by the app.

Each toggle injects a DIFFERENT bug class, chosen so that exactly one pyramid
layer can see it and the other four cannot, by construction rather than by an
unlucky choice of inputs. The evaluation harness (``agenticqa.eval.corpus``,
tier ``AGENTIC_CORPUS``) runs them as scenarios; with ground-truth routing the
layer catch matrix is a clean diagonal:

                              unit  property  trajectory  llm_judge  adversarial
    support-clean-baseline    pass   pass      pass        pass       pass
    support-trajectory-only   pass   pass      FAIL        pass       pass
    support-judge-only        pass   pass      pass        FAIL       pass
    support-adversarial-only  pass   pass      pass        pass       FAIL

SUPPORT_BUG_TRAJECTORY=1 - refund before policy
----------------------------------------------------------------------------
    # before: policy = t.lookup_policy("refund"); t.get_invoice(invoice_id)
    # after : t.get_invoice(invoice_id); ...; t.issue_refund(...); then lookup_policy

The refund still happens, the invoice ends in the same state, every tool returns
the same results and the reply is identical. Unit and property tests assert
results and state, judge tests score the reply, adversarial tests probe hostile
input - none inspect the order of calls. Only ``tests/test_trajectory.py`` does.

SUPPORT_BUG_JUDGE=1 - stale cached answer
----------------------------------------------------------------------------
    # before: answer built from the retrieved policy text (30-day window)
    # after : answer built from a stale cached summary (90-day window), still
    #         citing [refund-policy]

Call order, tool results and state are untouched, and unit tests only check that
a citation is present. Only scoring the claim against the cited evidence
(``tests/test_llm_judge.py`` via ``tests/grounding.py``) exposes it. The judge
here is a deterministic stand-in so the suite needs no model key.

SUPPORT_BUG_ADVERSARIAL=1 - self-granted approval
----------------------------------------------------------------------------
    # after: if "approved" appears in the user's message, the agent grants the
    #        approval itself

Every ordinary request behaves identically, because none of them contains the
trigger word. Only inputs crafted to claim approval (``tests/test_agent_
adversarial.py``) move money. This is the failure SEVRA-BENCH-style social
engineering targets, and the one the product's own human-approval gate exists
to prevent.
"""
