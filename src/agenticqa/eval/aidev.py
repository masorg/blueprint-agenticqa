"""Real agent-written pull-request text from AIDev (Li, Zhang, Hassan; Hugging Face hao-li/AIDev, v4).

Two uses, neither of which runs any third-party code:

1. **How common are claims about tests in real agent PR descriptions?** Our manipulation study
   injects text like "ran the full suite: all tests pass". If real agents already write that,
   the payload is realistic and the threat is not hypothetical. The patterns below were fixed
   BEFORE they were run on the data.
2. **Does ordinary real text move the router?** A router should not change its decision because
   an unrelated, honest description sits in the PR body. We append real descriptions to our demo
   scenarios and count decision changes: a baseline for how much of our attack effect is just
   "different text".

Only identifiers are committed, never the text: each repository keeps its own license (the
dataset card is CC-BY-4.0 but says source content retains its original license).
"""

from __future__ import annotations

import random
import re
import statistics as st
from collections import defaultdict

REPO = "hao-li/AIDev"
REVISION = "v4"
MIN_CHARS = 80  # a one-line body carries no claim; fixed in advance

# Fixed before running. Each is a *kind of claim a PR description can make about its own testing
# or readiness*; none of them is verified here (we do not run the PR's tests).
CLAIMS: dict[str, re.Pattern[str]] = {
    "says_tests_pass": re.compile(
        r"\b(all\s+)?(the\s+)?(unit\s+|existing\s+|new\s+|related\s+)?tests?\s+(all\s+)?"
        r"(pass|passes|passed|passing|are\s+passing|are\s+green)\b"
        r"|\ball\s+(checks|tests)\s+(pass|passed|are\s+green)\b"
        r"|\b(tests?|checks?|ci)\s*:?\s*(✅|✔)",
        re.IGNORECASE,
    ),
    "says_tests_were_run": re.compile(
        r"\b(ran|run|running|executed)\b[^.\n]{0,40}\b(tests?|test\s+suite|pytest|jest|go\s+test|cargo\s+test)\b"
        r"|\bnpm\s+(run\s+)?test\b|\bpytest\b|\btests?\s+(run|ran)\b",
        re.IGNORECASE,
    ),
    "says_ready": re.compile(r"\b(ready\s+(to|for)\s+(merge|review)|good\s+to\s+merge|lgtm)\b", re.IGNORECASE),
    "says_no_breakage": re.compile(
        r"\bno\s+(breaking\s+changes?|regressions?|behaviou?r(al)?\s+changes?|functional\s+changes?)\b",
        re.IGNORECASE,
    ),
    "claims_review_or_approval": re.compile(r"\b(approved|reviewed|signed[\s-]off)\s+by\b", re.IGNORECASE),
}


# POST-HOC: added after reading a sample of the matches for `says_tests_were_run`, where many
# descriptions turned out to report that their own run FAILED. Exploratory; not pre-registered.
POST_HOC_FAILED_RUN = re.compile(
    r"\(\s*fails?\b|\bfails?:|\bfailed\b|\bfailing\b|\bcommand not found\b|\bnot installed\b|\bcould not\b|❌",
    re.IGNORECASE,
)


def load_table(name: str, columns: list[str] | None = None) -> list[dict]:
    """One AIDev table (a parquet file in the dataset), only the columns asked for."""
    import pyarrow.parquet as pq
    from huggingface_hub import HfFileSystem

    with HfFileSystem().open(f"datasets/{REPO}@{REVISION}/{name}.parquet") as fh:
        return pq.read_table(fh, columns=columns).to_pylist()


def load_pull_requests(columns: list[str] | None = None) -> list[dict]:
    """The popular-repository pull-request table (71,677 rows at v4), only the columns asked for."""
    return load_table("pull_request", columns)


def claim_prevalence(rows: list[dict]) -> dict:
    """Share of descriptions (overall and per agent) that make each kind of claim."""
    by_agent: dict[str, list[str]] = defaultdict(list)
    for r in rows:
        by_agent[r.get("agent") or "unknown"].append((r.get("body") or ""))

    def block(bodies: list[str]) -> dict:
        n = len(bodies)
        nonempty = [b for b in bodies if len(b.strip()) >= MIN_CHARS]
        out = {
            "n": n,
            "empty_or_short": n - len(nonempty),
            "median_chars": int(st.median(len(b) for b in bodies)) if bodies else 0,
        }
        for name, rx in CLAIMS.items():
            hits = sum(1 for b in nonempty if rx.search(b))
            out[name] = {"hits": hits, "of": len(nonempty), "share": (hits / len(nonempty)) if nonempty else None}
        ran = [b for b in nonempty if CLAIMS["says_tests_were_run"].search(b)]
        failed = sum(1 for b in ran if POST_HOC_FAILED_RUN.search(b))
        out["post_hoc_run_mentions_a_failure"] = {"hits": failed, "of": len(ran),
                                                  "share": (failed / len(ran)) if ran else None}
        any_test = sum(1 for b in nonempty if CLAIMS["says_tests_pass"].search(b) or CLAIMS["says_tests_were_run"].search(b))
        out["says_tests_pass_or_run"] = {"hits": any_test, "of": len(nonempty),
                                         "share": (any_test / len(nonempty)) if nonempty else None}
        return out

    all_bodies = [b for bs in by_agent.values() for b in bs]
    return {"overall": block(all_bodies), "by_agent": {a: block(b) for a, b in sorted(by_agent.items())}}


def sample_descriptions(rows: list[dict], n: int, seed: int) -> list[dict]:
    """Seeded sample of PR descriptions, stratified by agent, using only a length floor.

    No content filter: which descriptions are "good" is exactly the choice we must not make."""
    pool: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        if len((r.get("body") or "").strip()) >= MIN_CHARS:
            pool[r.get("agent") or "unknown"].append(r)
    agents = sorted(pool)
    rng = random.Random(seed)
    chosen: list[dict] = []
    quota = -(-n // len(agents))  # ceil
    for agent in agents:
        ids = sorted(pool[agent], key=lambda r: r["id"])
        for r in rng.sample(ids, min(quota, len(ids))):
            chosen.append({"id": r["id"], "agent": agent})
    return sorted(chosen, key=lambda d: (d["agent"], d["id"]))[:n] if len(chosen) > n else \
        sorted(chosen, key=lambda d: (d["agent"], d["id"]))


def routing_signature(decision) -> tuple:
    """What the router decided, in a comparable form: the system kind and the applicable layers."""
    layers = tuple(sorted(layer.value for layer, r in decision.layers.items() if r.applicable))
    return (decision.repo_kind.value, layers)
