"""A deterministic stand-in for an LLM judge: scores an answer against its evidence.

An answer is grounded when it cites at least one source and every checkable
claim in it (a duration, or a dollar amount) appears in the evidence it was
given. In production this scorer would be an LLM judge with a rubric; keeping
it deterministic lets the suite run with no model key.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_CLAIM_RE = re.compile(r"\$\d+(?:\.\d+)?|\d+\s*(?:days?|hours?)", re.IGNORECASE)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.lower()).strip()


@dataclass
class Grounding:
    claims: list[str]
    ungrounded: list[str]
    cited: bool

    @property
    def score(self) -> float:
        return 1.0 if not self.claims else 1 - len(self.ungrounded) / len(self.claims)

    @property
    def passes(self) -> bool:
        return self.cited and not self.ungrounded


def score_grounding(answer: str, citations: list[str], evidence: list[str]) -> Grounding:
    claims = [_norm(c) for c in _CLAIM_RE.findall(answer)]
    pool = _norm(" ".join(evidence))
    return Grounding(claims=claims, ungrounded=[c for c in claims if c not in pool], cited=bool(citations))
