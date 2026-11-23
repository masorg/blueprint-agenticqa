"""Ablation of GATE's two silence rules, from saved results only (nothing is run again).

A plain selection gate approves when every selected test passes. GATE also sends a change to a person when a
changed file is reached by no test (U) or when the change removes or weakens tests (W). For each data set we count
what the plain gate would have approved although the full test run failed (a missed failure that U caught), and
what it would have approved without any test showing the change safe (approvals on silence that GATE avoids).
"""

from __future__ import annotations


def mutants(projects: list[dict], testmon: list[dict] | None = None) -> dict:
    rows = [m for p in projects for m in p.get("mutants", []) if m.get("status") == "valid"]
    killed = [m for m in rows if m["n_full_failing"] > 0]
    surv = [m for m in rows if m["n_full_failing"] == 0]
    out: dict = {"mutants": len(rows), "killed": len(killed), "survivors": len(surv)}
    for tag, sel_fail, unc in (("static", "n_selected_failing", "uncovered"), ("dynamic", "n_dyn_failing", "dyn_uncovered")):
        if killed and sel_fail not in killed[0]:
            continue
        only_u = [m for m in killed if m[sel_fail] == 0 and m[unc]]
        out[tag] = {"failures_caught_only_by_U": len(only_u), "ids": [m["id"] for m in only_u][:20],
                    "silent_approvals_avoided": sum(1 for m in surv if m[unc])}
    if testmon:
        by_id = {m["id"]: m for m in rows}
        misses = [r for s in testmon for r in s.get("rows", []) if r["killed_full"] and r["testmon_failed"] == 0
                  and not r.get("timed_out")]
        out["testmon_misses"] = len(misses)
        out["testmon_misses_caught_by_U_static"] = sum(1 for r in misses if by_id.get(r["id"], {}).get("uncovered"))
        out["testmon_miss_ids"] = [r["id"] for r in misses]
    return out


def swebench(rows: list[dict]) -> dict:
    usable = [r for r in rows if r.get("status") == "ok" and r.get("n_targets", 0) > 0]
    return {"usable": len(usable), "caught_only_by_U": sum(1 for r in usable if r.get("caught_by_review")),
            "plain_gate_misses": sum(1 for r in usable if not r.get("caught_by_test") and r.get("mode") != "full")}


def shadow(rows: list[dict], weakened: list[dict] | None = None) -> dict:
    ran = [r for r in rows if r.get("status") == "ran"]
    w = {(x["repo"], x["number"]) for x in (weakened or []) if x.get("set") == "shadow" and x.get("weakened")}
    out: dict = {"ran": len(ran), "regressions": sum(1 for r in ran if r["introduced"])}
    for tag, sel, unc, mode in (("static", "introduced_selected", "uncovered", "mode"),
                                ("dynamic", "introduced_selected_dyn", "dyn_uncovered", "dyn_mode")):
        rr = [r for r in ran if tag == "static" or r.get("dyn_available")]
        reg = [r for r in rr if r["introduced"]]
        only_u = [r for r in reg if not r.get(sel) and r.get(unc) and r.get(mode) != "full"]
        out[tag] = {
            "prs": len(rr), "regressions": len(reg), "regressions_caught_only_by_U": len(only_u),
            "ids": [f"{r['repo']}#{r['number']}" for r in only_u],
            # changes the plain gate would approve with no test showing them safe
            "silent_approvals_avoided_U": sum(1 for r in rr if r.get(unc) and r.get(mode) != "full" and not r["introduced"]),
            "silent_approvals_avoided_W": sum(1 for r in rr if (r["repo"], r["number"]) in w and not r["introduced"]),
            "regressions_also_flagged_by_W": sum(1 for r in reg if (r["repo"], r["number"]) in w),
        }
    return out
