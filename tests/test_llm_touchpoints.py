"""Two pure functions in orchestrator/llm_touchpoints.py: `_to_decision` and
`_merge_classifications`. Gap-filling tests -- not covered anywhere today.

`_to_decision` must re-apply the agentic-only guardrail in code even when the
model's RoutingResponse disagrees (the module docstring states this
invariant explicitly). `_merge_classifications` must classify every
discovered test exactly once, falling back to the heuristic layer for any
test the model omitted, and must only mark `verified=True` when the model's
layer agrees with the heuristic AND confidence clears `_CONFIDENCE_FLOOR`.
"""

from __future__ import annotations

from agenticqa.llm.prompts.classification import ClassificationResponse
from agenticqa.llm.prompts.classification import TestVerdict as _TestVerdict
from agenticqa.llm.prompts.routing import LayerVerdict, RoutingResponse
from agenticqa.models.enums import LayerId, RepoKind
from agenticqa.models.pyramid import DiscoveredTest
from agenticqa.orchestrator.llm_touchpoints import _CONFIDENCE_FLOOR, _merge_classifications, _to_decision


# ---- _to_decision -----------------------------------------------------------


def test_agentic_only_layer_forced_inapplicable_for_plain_service():
    """The model claims plain_service but marks an agentic-only layer
    (trajectory) applicable=True -- the guardrail must override it."""
    resp = RoutingResponse(
        repo_kind="plain_service",
        summary="looks like a plain API",
        layers=[
            LayerVerdict(layer="unit", applicable=True, reason="always"),
            LayerVerdict(layer="property", applicable=True, reason="always"),
            LayerVerdict(layer="trajectory", applicable=True, reason="model mistakenly said yes"),
            LayerVerdict(layer="llm_judge", applicable=True, reason="model mistakenly said yes"),
            LayerVerdict(layer="adversarial", applicable=True, reason="always"),
        ],
    )
    decision = _to_decision(resp)

    assert decision.repo_kind == RepoKind.PLAIN_SERVICE
    for layer in (LayerId.TRAJECTORY, LayerId.LLM_JUDGE):
        assert layer.agentic_only
        assert decision.layers[layer].applicable is False, f"{layer} should be forced inapplicable"
        assert "plain service" in decision.layers[layer].reason.lower()
        # The model claimed applicable=True for both; the guardrail overrode
        # it -- that must be visible (this is the signal the eval harness
        # uses to measure how often the guardrail is actually load-bearing).
        assert decision.layers[layer].guardrail_overridden is True


def test_guardrail_override_flag_is_false_when_model_already_agreed():
    """The model correctly said an agentic-only layer doesn't apply -- the
    guardrail didn't have to do anything, so `guardrail_overridden` is False."""
    resp = RoutingResponse(
        repo_kind="plain_service",
        summary="",
        layers=[
            LayerVerdict(layer="trajectory", applicable=False, reason="not agentic"),
            LayerVerdict(layer="llm_judge", applicable=False, reason="not agentic"),
        ],
    )
    decision = _to_decision(resp)
    for layer in (LayerId.TRAJECTORY, LayerId.LLM_JUDGE):
        assert decision.layers[layer].applicable is False
        assert decision.layers[layer].guardrail_overridden is False


def test_agentic_only_layer_respected_for_agentic_repo():
    resp = RoutingResponse(
        repo_kind="agentic",
        summary="agent app",
        layers=[
            LayerVerdict(layer="unit", applicable=True, reason=""),
            LayerVerdict(layer="property", applicable=True, reason=""),
            LayerVerdict(layer="trajectory", applicable=True, reason="agentic"),
            LayerVerdict(layer="llm_judge", applicable=False, reason="not needed here"),
            LayerVerdict(layer="adversarial", applicable=True, reason=""),
        ],
    )
    decision = _to_decision(resp)
    assert decision.repo_kind == RepoKind.AGENTIC
    assert decision.layers[LayerId.TRAJECTORY].applicable is True
    assert decision.layers[LayerId.LLM_JUDGE].applicable is False


def test_model_omitted_layer_biases_to_applicable():
    """A non-agentic-only layer the model didn't rule on defaults to
    applicable=True (the "bias-to-applicable" fallback)."""
    resp = RoutingResponse(
        repo_kind="plain_service",
        summary="",
        layers=[
            LayerVerdict(layer="unit", applicable=True, reason=""),
            # property, trajectory, llm_judge, adversarial all omitted.
        ],
    )
    decision = _to_decision(resp)
    assert decision.layers[LayerId.PROPERTY].applicable is True
    assert "did not rule" in decision.layers[LayerId.PROPERTY].reason
    assert decision.layers[LayerId.ADVERSARIAL].applicable is True
    # but the agentic-only guardrail still wins over the omission fallback
    assert decision.layers[LayerId.TRAJECTORY].applicable is False
    assert decision.layers[LayerId.LLM_JUDGE].applicable is False


# ---- _merge_classifications --------------------------------------------------


def _test(node_id: str) -> DiscoveredTest:
    # node ids chosen so heuristic_layer(test) is deterministic and known:
    # property-style markers/names route to PROPERTY, everything else to UNIT
    # by the default heuristic. We only rely on whatever heuristic_layer
    # actually returns, fetched at test time below.
    return DiscoveredTest(node_id=node_id, file="test_x.py", name=node_id)


def test_omitted_test_falls_back_to_heuristic_unverified():
    from agenticqa.classify import heuristic_layer

    t = _test("test_omitted")
    heuristic = heuristic_layer(t)
    resp = ClassificationResponse(tests=[])  # model returned nothing for this test

    out = _merge_classifications([t], resp)

    assert len(out) == 1
    c = out[0]
    assert c.node_id == "test_omitted"
    assert c.layer == heuristic
    assert c.heuristic_layer == heuristic
    assert c.agreement is True
    assert c.verified is False


def test_disagreement_is_unverified_even_with_high_confidence():
    from agenticqa.classify import heuristic_layer

    t = _test("test_disagree")
    heuristic = heuristic_layer(t)
    # pick a layer that differs from the heuristic call
    other = next(layer for layer in LayerId.ordered() if layer != heuristic)

    resp = ClassificationResponse(
        tests=[_TestVerdict(node_id="test_disagree", layer=other.value, confidence=0.95, rationale="model is confident")]
    )

    out = _merge_classifications([t], resp)
    assert len(out) == 1
    c = out[0]
    assert c.layer == other
    assert c.heuristic_layer == heuristic
    assert c.agreement is False
    assert c.verified is False, "verified requires agreement AND confidence >= floor"


def test_agreement_with_low_confidence_is_unverified():
    from agenticqa.classify import heuristic_layer

    t = _test("test_agree_low_conf")
    heuristic = heuristic_layer(t)
    low_conf = _CONFIDENCE_FLOOR - 0.1
    assert low_conf >= 0.0

    resp = ClassificationResponse(
        tests=[_TestVerdict(node_id="test_agree_low_conf", layer=heuristic.value, confidence=low_conf, rationale="unsure")]
    )
    out = _merge_classifications([t], resp)
    c = out[0]
    assert c.agreement is True
    assert c.verified is False


def test_agreement_with_high_confidence_is_verified():
    from agenticqa.classify import heuristic_layer

    t = _test("test_agree_high_conf")
    heuristic = heuristic_layer(t)
    high_conf = _CONFIDENCE_FLOOR

    resp = ClassificationResponse(
        tests=[_TestVerdict(node_id="test_agree_high_conf", layer=heuristic.value, confidence=high_conf, rationale="confident")]
    )
    out = _merge_classifications([t], resp)
    c = out[0]
    assert c.agreement is True
    assert c.verified is True
