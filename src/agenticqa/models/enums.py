"""Enumerations for the pyramid layers and the run lifecycle."""

from __future__ import annotations

from enum import Enum


class LayerId(str, Enum):
    """The five layers of the Agentic QA testing pyramid, bottom to top.

    The two middle layers are *agentic-only*: they apply when the system under
    test is itself an agentic/LLM system, and stay dark for a plain service.
    """

    UNIT = "unit"
    PROPERTY = "property"
    TRAJECTORY = "trajectory"
    LLM_JUDGE = "llm_judge"
    ADVERSARIAL = "adversarial"

    @property
    def order(self) -> int:
        """0 = base of the pyramid (widest), 4 = apex."""
        return _ORDER[self]

    @property
    def label(self) -> str:
        return _LABEL[self]

    @property
    def agentic_only(self) -> bool:
        return self in {LayerId.TRAJECTORY, LayerId.LLM_JUDGE}

    @classmethod
    def ordered(cls) -> list["LayerId"]:
        return sorted(cls, key=lambda layer: layer.order)


_ORDER: dict[LayerId, int] = {
    LayerId.UNIT: 0,
    LayerId.PROPERTY: 1,
    LayerId.TRAJECTORY: 2,
    LayerId.LLM_JUDGE: 3,
    LayerId.ADVERSARIAL: 4,
}

_LABEL: dict[LayerId, str] = {
    LayerId.UNIT: "Unit",
    LayerId.PROPERTY: "Property-based",
    LayerId.TRAJECTORY: "Trajectory",
    LayerId.LLM_JUDGE: "LLM-as-judge",
    LayerId.ADVERSARIAL: "Adversarial / Safety",
}


class RepoKind(str, Enum):
    PLAIN_SERVICE = "plain_service"
    AGENTIC = "agentic"


class RunState(str, Enum):
    QUEUED = "queued"
    ROUTING = "routing"
    PLANNED = "planned"
    RUNNING = "running"
    TRIAGING = "triaging"
    AWAITING_APPROVAL = "awaiting_approval"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def terminal(self) -> bool:
        return self in {RunState.DONE, RunState.FAILED, RunState.CANCELLED}


class LayerRunState(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    ERROR = "error"


class TestOutcome(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    ERROR = "error"


class Verdict(str, Enum):
    AUTO_APPROVE = "auto_approve"
    NEEDS_HUMAN_REVIEW = "needs_human_review"


class ApprovalState(str, Enum):
    NOT_REQUIRED = "not_required"
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class ApprovalSource(str, Enum):
    IN_APP = "in_app"
    GITHUB = "github"
