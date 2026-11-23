import json, glob, re
from pathlib import Path
root = Path("/Users/msaleem/work/cloudera/agentic-qa")
man = json.loads((root / "docs/figures/evidence.json").read_text())
def latest(pattern):
    fs = sorted(glob.glob(str(root / "eval/reports" / pattern)))
    return str(Path(fs[-1]).relative_to(root)) if fs else None
def per_project(prefix, projects):
    out = []
    for p in projects:
        f = latest(f"{prefix}-{p}-2026*.json")
        if f: out.append(f)
    return out
projects = ["attrs","boltons","click","idna","itsdangerous","jinja","markupsafe","more-itertools","packaging","pluggy","sqlparse","toolz"]
sel = {"mutants": per_project("selection-replay", projects), "mutants_run": per_project("selection-mutants", projects),
       "field": latest("selection-field-*.json"), "aidev": latest("selection-aidev-*.json"),
       "demo": latest("selection-demo-*.json"),
       "attack": per_project("selection-attack", projects), "advice": per_project("selection-advice", projects)}
hold = json.loads((root / "eval/data/holdout-projects.json").read_text())
hp = list(hold["projects"])
def best(names):
    out = []
    for n in names:
        f = latest(f"selection-replay-{n}-2026*.json") or latest(f"selection-mutants-{n}-2026*.json")
        if f: out.append(f)
    return out
sel["holdout"] = best(hp)
sel["holdout_batch3"] = best(hold.get("batch3", []))
sel["swebench"] = [str(Path(f).relative_to(root)) for f in sorted(glob.glob(str(root / "eval/reports/selection-swebench-*.json")))]
sel["testmon"] = per_project("selection-testmon", projects + hp)
ff = sorted(glob.glob(str(root / "eval/reports/holdout-first-run/selection-mutants-validators-2026*.json")))
if ff: sel["holdout_first_run"] = [str(Path(ff[-1]).relative_to(root))]
man["selection"] = {k: v for k, v in sel.items() if v}
(root / "docs/figures/evidence.json").write_text(json.dumps(man, indent=1))
print(json.dumps(man["selection"], indent=1)[:1500])
