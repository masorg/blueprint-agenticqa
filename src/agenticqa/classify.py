"""Deterministic heuristic mapping of a test to a pyramid layer.

Used directly in Phase 1 (before the LLM classifier exists) and later as the
cross-check the LLM classification is compared against (`agreement`/`verified`).
The rules are intentionally simple and explainable.
"""

from __future__ import annotations

from .models.enums import LayerId
from .models.pyramid import DiscoveredTest

# Substring cues, checked against the lowercased node id + marker names.
_ADVERSARIAL_CUES = ("adversarial", "security", "fuzz", "malicious", "hostile", "injection")
_PROPERTY_CUES = ("property", "hypothesis", "invariant", "propert")
_TRAJECTORY_CUES = ("trajectory", "tool_sequence", "toolseq", "agent_trace")
_LLM_JUDGE_CUES = ("llm_judge", "llm_as_judge", "judge", "rubric")


def heuristic_layer(test: DiscoveredTest) -> LayerId:
    """Best-effort layer for a discovered test. Defaults to UNIT."""
    hay = " ".join([test.node_id.lower(), test.file.lower(), test.name.lower(), *[m.lower() for m in test.markers]])

    if any(cue in hay for cue in _ADVERSARIAL_CUES):
        return LayerId.ADVERSARIAL
    if any(cue in hay for cue in _LLM_JUDGE_CUES):
        return LayerId.LLM_JUDGE
    if any(cue in hay for cue in _TRAJECTORY_CUES):
        return LayerId.TRAJECTORY
    if any(cue in hay for cue in _PROPERTY_CUES):
        return LayerId.PROPERTY
    return LayerId.UNIT
