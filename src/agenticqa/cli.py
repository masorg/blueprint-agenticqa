"""Command-line entry points.

Phase 1: `agenticqa run-local --path <repo>` exercises the full execution core
(checkout -> venv -> discover -> classify -> run each layer) with no web server,
no LLM, and no GitHub. This is the harness that proves the engine end-to-end.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import defaultdict
from pathlib import Path

from .classify import heuristic_layer
from .execution.base import WorkspaceSpec
from .execution.subprocess_venv import SubprocessVenvExecutor
from .models.enums import LayerId, TestOutcome
from .models.pyramid import TestClassification
from .models.run import TestResult
from .runners.registry import select_runner

_SYMBOL = {TestOutcome.PASSED: "✓", TestOutcome.FAILED: "✗", TestOutcome.SKIPPED: "‑", TestOutcome.ERROR: "✗"}


async def _run_local(path: str, bug: bool) -> int:
    executor = SubprocessVenvExecutor()
    spec = WorkspaceSpec(
        source="local",
        local_path=path,
        extra_env={"INVENTORY_ORDERS_BUG": "1"} if bug else {},
    )
    print(f"→ Preparing workspace from {path} (checkout + venv + install)…")
    workspace = await executor.prepare(spec)
    async with workspace:
        runner = await select_runner(workspace)
        if runner is None:
            print("✗ No framework runner matched this repo.", file=sys.stderr)
            return 2

        tests = await runner.discover(workspace)
        print(f"→ Discovered {len(tests)} test(s) via '{runner.name}'.\n")

        classifications = [
            TestClassification(node_id=t.node_id, layer=heuristic_layer(t), heuristic_layer=heuristic_layer(t))
            for t in tests
        ]
        by_layer: dict[LayerId, list[str]] = defaultdict(list)
        for c in classifications:
            by_layer[c.layer].append(c.node_id)

        overall_pass = overall_fail = overall_skip = 0
        any_error = False
        for layer in LayerId.ordered():
            node_ids = by_layer.get(layer, [])
            if not node_ids:
                continue
            print(f"■ {layer.label} layer - {len(node_ids)} test(s) planned")

            def _on_event(tr: TestResult) -> None:
                print(f"    {_SYMBOL[tr.outcome]} {tr.node_id}")

            result = await runner.run(workspace, node_ids, layer, _on_event)
            overall_pass += result.passed
            overall_fail += result.failed
            overall_skip += result.skipped
            if result.error:
                any_error = True
                print(f"    ! layer error: {result.error}")
            print(f"  → {result.passed} passed, {result.failed} failed, {result.skipped} skipped "
                  f"({result.duration:.2f}s)\n")

        print("─" * 60)
        print(f"TOTAL: {overall_pass} passed, {overall_fail} failed, {overall_skip} skipped")
        verdict = "needs_human_review" if (overall_fail > 0 or any_error) else "auto_approve"
        print(f"RECOMMENDATION: {verdict}")
        return 1 if verdict == "needs_human_review" else 0


# Which Settings field holds the model name for each provider (for --model).
_MODEL_FIELD = {"openai": "openai_model", "mistral": "mistral_model", "cloudera": "cloudera_model"}


async def _eval_run(
    provider: str, out_dir: str, corpus_name: str, model: str | None = None, routing: str = "provider",
    ablation: str = "none",
) -> int:
    """Run the benchmark corpus through the real pipeline and write a
    JSON + Markdown report (routing/classification agreement, verdict
    correctness, latency/cost). See docs/13-evaluation-harness.md."""
    import re

    from .config import get_settings
    from .eval.corpus import AGENTIC_CORPUS, CORPUS, FULL_CORPUS, REAL_PR_CORPUS
    from .eval.harness import run_corpus
    from .eval.injection import INJECTION_CORPUS
    from .eval.metrics import build_report, render_markdown
    from .llm.factory import build_client, resolve_target
    from .orchestrator.llm_touchpoints import LLMClassifier, LLMRouter, LLMTriage
    from .orchestrator.stubs import HeuristicClassifier, HeuristicRouter, HeuristicTriage

    corpus = {
        "local": CORPUS, "agentic": AGENTIC_CORPUS, "real-pr": REAL_PR_CORPUS, "full": FULL_CORPUS,
        "injection": INJECTION_CORPUS,
    }[corpus_name]
    if ablation != "none" and provider == "stub":
        print("✗ --ablation needs a real LLM provider.", file=sys.stderr)
        return 2
    settings = get_settings()
    if provider == "stub":
        if model:
            print("✗ --model has no meaning with --provider stub.", file=sys.stderr)
            return 2
        router, classifier, triage = HeuristicRouter(), HeuristicClassifier(), HeuristicTriage()
        provider_label = "stub"
        client, configured_model = None, None
    else:
        # Named providers pin the run to that provider regardless of .env;
        # "configured" defers to AGENTICQA_LLM_PROVIDER.
        update: dict[str, str] = {}
        if provider != "configured":
            update["llm_provider"] = provider
        effective = (update.get("llm_provider") or settings.llm_provider).lower()
        if model:
            field = _MODEL_FIELD.get(effective)
            if field is None:
                print(f"✗ --model is not supported for provider {effective!r}.", file=sys.stderr)
                return 2
            update[field] = model
        settings = settings.model_copy(update=update)

        client = build_client(settings)
        target = resolve_target(settings)
        if client is None or target is None:
            print(
                f"✗ provider {settings.llm_provider!r} has no usable client "
                "(missing API key?) - refusing to silently fall back to stub for an eval run.",
                file=sys.stderr,
            )
            return 2
        router, classifier, triage = LLMRouter(client), LLMClassifier(client), LLMTriage(client)
        # Label with the model actually called, so reports from different
        # models of one provider never collide or get misattributed.
        provider_label = re.sub(r"[^A-Za-z0-9._-]", "_", f"{target.provider}-{target.model}")
        configured_model = target.model

    if routing == "oracle":
        from .eval.routers import OracleRouter

        router = OracleRouter(corpus)
        provider_label += "+oracle-routing"

    repo_root = Path(__file__).resolve().parents[2]
    print(f"→ Running {len(corpus)} scenario(s) [corpus={corpus_name}] against provider={provider_label!r}…")
    results = await run_corpus(
        corpus, router=router, classifier=classifier, triage=triage, repo_root=repo_root
    )

    if ablation == "llm-verdict":
        from .eval.ablation import llm_verdict

        for r in results:
            r.baseline_verdict = await llm_verdict(client, r.run)

    from .eval import provenance

    report = build_report(
        results,
        provider_label=provider_label,
        provenance=provenance.collect(
            repo_root=repo_root,
            scenarios=corpus,
            corpus_name=corpus_name,
            provider=provider_label,
            configured_model=configured_model,
            response_models=list(getattr(client, "response_models", ()) or ()),
            argv=sys.argv[1:],
        ),
    )
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stamp = report["generated_at"].replace(":", "-")
    json_path = out / f"eval-{provider_label}-{stamp}.json"
    md_path = out / f"eval-{provider_label}-{stamp}.md"
    json_path.write_text(json.dumps(report, indent=2))
    md_path.write_text(render_markdown(report))

    print(render_markdown(report))
    print(f"\n→ wrote {json_path}\n→ wrote {md_path}")
    accuracy = report["verdict_confusion"]["accuracy"]
    return 0 if accuracy == 1.0 else 1


async def _llm_check(provider: str, model: str | None) -> int:
    """One cheap structured call + one free-text call against a provider/model,
    reporting which structured-output path it used and the token usage. Run it
    before a long eval matrix so a bad key/model name fails in seconds."""
    from pydantic import BaseModel

    from .config import get_settings
    from .llm.factory import build_client, resolve_target

    class _Probe(BaseModel):
        repo_kind: str
        reason: str

    settings = get_settings()
    update: dict[str, str] = {}
    if provider != "configured":
        update["llm_provider"] = provider
    effective = (update.get("llm_provider") or settings.llm_provider).lower()
    if model:
        field = _MODEL_FIELD.get(effective)
        if field is None:
            print(f"✗ --model is not supported for provider {effective!r}.", file=sys.stderr)
            return 2
        update[field] = model
    settings = settings.model_copy(update=update)

    client = build_client(settings)
    target = resolve_target(settings)
    if client is None or target is None:
        print(f"✗ provider {settings.llm_provider!r} has no usable client (missing API key?).", file=sys.stderr)
        return 2

    print(f"→ {target.provider} / {target.model} @ {target.base_url or 'default endpoint'}")
    try:
        probe = await client.structured(
            system="Classify the change. repo_kind is 'plain_service' or 'agentic'.",
            user="Diff: renamed a variable in a CRUD REST handler.",
            schema=_Probe,
        )
        path = "native structured outputs" if client._supports_parse else "JSON-mode fallback"
        print(f"✓ structured call ok via {path}: {probe.model_dump()} usage={client.last_usage}")
        text = await client.complete(system="Reply in one short sentence.", user="Say the check passed.")
        print(f"✓ free-text call ok: {text!r} usage={client.last_usage}")
    except Exception as exc:  # noqa: BLE001 - report any failure to the operator
        print(f"✗ call failed: {exc}", file=sys.stderr)
        return 1
    return 0


async def _adversary_run(out_dir: str, quick: bool, suite: str = "demo", only: str | None = None) -> int:
    """Worst-case router/classifier experiment (no LLM): the verdict and test
    coverage must not change under any hostile routing. Exit 1 on a violation."""
    from datetime import datetime, timezone

    from .eval import adversaries, provenance
    from .eval.corpus import CORPUS, REAL_SUITES

    scenarios = REAL_SUITES if suite == "real" else CORPUS
    if only:
        scenarios = [x for x in scenarios if x.name == only]
    repo_root = Path(__file__).resolve().parents[2]
    print(f"→ Adversary experiment over {len(scenarios)} scenario(s)" + (" [quick]" if quick else "") + "…")
    try:
        cases = await adversaries.run_adversaries(scenarios, repo_root=repo_root, quick=quick)
    except adversaries.BaselineFailed as exc:
        from datetime import datetime, timezone

        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).isoformat().replace(":", "-")
        path = out / f"adversary-COULD-NOT-RUN-{(only or suite)}-{stamp}.json"
        path.write_text(json.dumps({"scenario": only, "could_not_run": True, "reason": str(exc)}, indent=2))
        print(f"✗ honest baseline unusable, so no hostile cases were run: {exc}\n→ wrote {path}", file=sys.stderr)
        return 3
    summary = adversaries.summarize(cases)
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "provenance": provenance.collect(
            repo_root=repo_root, scenarios=scenarios, corpus_name=("real:" + ",".join(x.name for x in scenarios)) if suite == "real" else "local", provider="none (no LLM)",
            configured_model=None, response_models=[], argv=sys.argv[1:],
        ),
        "summary": summary,
        "cases": [vars(c) | {"ok": c.ok} for c in cases],
    }
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stamp = report["generated_at"].replace(":", "-")
    path = out / f"adversary-{stamp}.json"
    path.write_text(json.dumps(report, indent=2))
    print(json.dumps({k: v for k, v in summary.items() if k != "violating_cases"}, indent=2))
    for v in summary["violating_cases"][:20]:
        print("  VIOLATION", v)
    print(f"→ wrote {path}")
    return 1 if summary["violations"] else 0


async def _mutation_run(systems: list[str], n: int | None, seed: int, out_dir: str) -> int:
    """Run mutants of the demo services through the real pipeline (ground-truth
    routing, heuristic classifier) and report which layers kill which mutants."""
    from datetime import datetime, timezone

    from .eval import mutation, provenance
    from .eval.corpus import FULL_CORPUS

    repo_root = Path(__file__).resolve().parents[2]
    all_results: list[mutation.MutantResult] = []
    for name in systems:
        system = mutation.SYSTEMS[name]
        source = (repo_root / system.demo_dir / system.source_file).read_text()
        sites = mutation.enumerate_sites(source)
        chosen = mutation.sample_sites(sites, n, seed) if n is not None and n < len(sites) else sites
        print(f"→ {name}: {len(sites)} mutation sites, running {len(chosen)}"
              + (f" (stratified sample, seed={seed})" if len(chosen) < len(sites) else " (exhaustive)"))
        all_results += await mutation.run_mutants(system, chosen, repo_root=repo_root)

    summary = {"all": mutation.summarize(all_results)}
    for name in systems:
        summary[name] = mutation.summarize([r for r in all_results if r.system == name])
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "provenance": provenance.collect(
            repo_root=repo_root, scenarios=FULL_CORPUS, corpus_name="mutants:" + ",".join(systems),
            provider="none (no LLM; heuristic classifier, oracle routing)", configured_model=None,
            response_models=[], argv=sys.argv[1:],
        ) | {"hypothesis_seed": mutation.HYPOTHESIS_SEED, "sample_seed": seed, "n_requested": n},
        "summary": summary,
        "mutants": [
            {"system": r.system, "id": r.id, "operator": r.operator, "line": r.line,
             "description": r.description, "status": r.status, "killers": r.killers,
             "states": r.states, "failed_tests": r.failed_tests}
            for r in all_results
        ],
    }
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"mutation-{report['generated_at'].replace(':', '-')}.json"
    path.write_text(json.dumps(report, indent=2))
    for name, s in summary.items():
        print(f"\n== {name}: generated {s['n_generated']}, valid {s['n_valid']}, killed {s['n_killed']}, "
              f"survived {s['n_survived']}, invalid {s['n_invalid']}")
        for layer, d in s["per_layer"].items():
            if d["kills"]:
                print(f"   {layer:12s} kills={d['kills']:3d} unique={d['unique_kills']:3d}")
        for b, d in s["baselines"].items():
            print(f"   baseline {b:18s} {d['k']}/{d['n']} = {d['rate'] if d['rate'] is None else round(d['rate'], 3)}  CI95 {d['ci95']}")
    print(f"\n→ wrote {path}")
    return 0


def _eval_plot(manifest: str, out: str) -> int:
    """Render the paper's figures and number macros from the evidence a manifest pins."""
    try:
        from .eval import plots
    except ModuleNotFoundError as exc:  # matplotlib is an optional extra
        print(f"✗ {exc}. Install with: pip install 'agenticqa[plots]'", file=sys.stderr)
        return 2
    for path in plots.render_all(Path(manifest), Path(out)):
        print("→", path)
    return 0


def _route_set_build(workdir: str, out: str) -> int:
    """Sample the pinned real-PR routing manifest from public git history."""
    from .eval import routing_set as rs

    print(f"→ Sampling {rs.PER_REPO} commits per repo, window {rs.WINDOW[0]}..{rs.WINDOW[1]}, seed {rs.SEED}")
    samples = rs.build_manifest(Path(workdir))
    rs.save_manifest(samples, Path(out))
    by_label = {k: sum(1 for s in samples if s.label == k) for k in ("agentic", "plain_service")}
    print(f"→ {len(samples)} samples {by_label} -> {out}")
    return 0


async def _route_eval(manifest: str, workdir: str, provider: str, model: str | None, mask_repo: bool,
                      out_dir: str, concurrency: int) -> int:
    """Route every sample in a manifest with one router and score repo-kind accuracy.
    No tests are executed: this measures routing alone, from the PR text and diff."""
    import re
    from datetime import datetime, timezone

    from .config import get_settings
    from .eval import provenance
    from .eval import routing_set as rs
    from .eval.corpus import Scenario
    from .llm.factory import build_client, resolve_target
    from .models.run import PRRef, QARun, RepoRef
    from .orchestrator.llm_touchpoints import LLMRouter
    from .orchestrator.stubs import HeuristicRouter

    samples = rs.load_manifest(Path(manifest))
    settings = get_settings()
    client, label = None, "stub"
    if provider in ("stub", "manifest", "manifest+diff"):
        router = HeuristicRouter()  # also the diff half of "manifest+diff"
        label = provider
    else:
        update = {"llm_provider": provider} if provider != "configured" else {}
        if model:
            update[_MODEL_FIELD[(update.get("llm_provider") or settings.llm_provider).lower()]] = model
        settings = settings.model_copy(update=update)
        client, target = build_client(settings), resolve_target(settings)
        if client is None or target is None:
            print(f"✗ provider {settings.llm_provider!r} has no usable client (missing API key?).", file=sys.stderr)
            return 2
        router = LLMRouter(client)
        label = re.sub(r"[^A-Za-z0-9._-]", "_", f"{target.provider}-{target.model}")
    label += "-masked" if mask_repo else ""

    work = Path(workdir)
    sem = asyncio.Semaphore(concurrency)

    async def one(sample: rs.Sample) -> dict:
        async with sem:
            info = await asyncio.to_thread(rs.fetch, sample, work)
            repo = RepoRef(owner="example", name="project") if mask_repo else RepoRef(
                owner=sample.repo.split("/")[0], name=sample.repo.split("/")[1])
            run = QARun(id=f"route-{sample.sha[:10]}", source="github", repo=repo,
                        pr=PRRef(title=info["title"], body=info["body"]), diff=info["diff"])
            decision = await router.route(run)
            predicted, deps = decision.repo_kind.value, None
            if provider.startswith("manifest"):
                from .eval import manifest_signal

                sig = await asyncio.to_thread(manifest_signal.signal, sample, work)
                deps = sig["dependencies"]
                from_diff = provider == "manifest+diff" and predicted == "agentic"
                predicted = "agentic" if (deps or from_diff) else "plain_service"
            return {
                "repo": sample.repo, "sha": sample.sha, "label": sample.label, "ambiguous": sample.ambiguous,
                "predicted": predicted, "manifest_dependencies": deps,
                "agentic_layers_on": decision.applies(next(l for l in decision.layers if l.agentic_only)),
                "touches_source": rs.touches_source(info["paths"]),
                "n_files": len(info["paths"]), "diff_chars": len(info["diff"]),
            }

    print(f"→ routing {len(samples)} samples with {label} (concurrency {concurrency})…")
    rows = await asyncio.gather(*(one(s) for s in samples))
    summary = _route_summary(rows)
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(), "router": label, "masked_repo_name": mask_repo,
        "provenance": provenance.collect(
            repo_root=Path(__file__).resolve().parents[2], scenarios=[Scenario(name="routing-set")],
            corpus_name=f"routing-set:{Path(manifest).name}", provider=label,
            configured_model=None, response_models=sorted(getattr(client, "response_models", ()) or ()),
            argv=sys.argv[1:],
        ) | {"llm_calls": (client.totals if client else None)},
        "summary": summary, "samples": rows,
    }
    errors = client.totals["errors"] if client else 0
    report["valid"] = errors == 0
    report["llm_errors"] = errors
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    prefix = "routing" if errors == 0 else "INVALID-routing"
    path = out / f"{prefix}-{label}-{report['generated_at'].replace(':', '-')}.json"
    path.write_text(json.dumps(report, indent=1))
    print(json.dumps({k: summary[k] for k in ("n", "accuracy", "agentic_recall", "plain_recall")}, indent=1))
    if client:
        print("llm calls:", client.totals)
    print(f"→ wrote {path}")
    if errors:
        print(f"✗ {errors} of {client.totals['calls']} routing calls failed and fell back to the heuristic: "
              "these numbers are NOT an LLM result. Lower --concurrency or retry.", file=sys.stderr)
        return 1
    return 0


def _route_summary(rows: list[dict]) -> dict:
    from .eval.mutation import wilson

    def acc(rs: list[dict]) -> dict:
        k = sum(1 for r in rs if r["predicted"] == r["label"])
        lo, hi = wilson(k, len(rs))
        return {"k": k, "n": len(rs), "rate": (k / len(rs)) if rs else None, "ci95": [round(lo, 3), round(hi, 3)]}

    clear = [r for r in rows if not r["ambiguous"]]
    ag, pl = [r for r in rows if r["label"] == "agentic"], [r for r in rows if r["label"] == "plain_service"]
    return {
        "n": len(rows), "accuracy": acc(rows)["rate"],
        "agentic_recall": acc(ag)["rate"], "plain_recall": acc(pl)["rate"],
        "overall": acc(rows), "clear_repos_only": acc(clear),
        "agentic_clear": acc([r for r in clear if r["label"] == "agentic"]),
        "touches_source": acc([r for r in rows if r["touches_source"]]),
        "no_source_change": acc([r for r in rows if not r["touches_source"]]),
        "agentic_touches_source": acc([r for r in ag if r["touches_source"]]),
        "by_repo": {repo: acc([r for r in rows if r["repo"] == repo]) for repo in sorted({r["repo"] for r in rows})},
    }


def _aidev_claims(out_dir: str, sample_n: int, seed: int, manifest_out: str) -> int:
    """How common are claims about testing in real agent PR descriptions? Also writes the pinned
    sample of descriptions (ids only) used by `route-noise`."""
    from datetime import datetime, timezone

    from .eval import aidev, provenance
    from .eval.corpus import Scenario

    print(f"→ reading {aidev.REPO}@{aidev.REVISION} pull_request (columns: id, agent, body, state, merged_at)…")
    rows = aidev.load_pull_requests(["id", "agent", "body", "state", "merged_at"])
    prevalence = aidev.claim_prevalence(rows)
    sample = aidev.sample_descriptions(rows, sample_n, seed)
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dataset": {"repo": aidev.REPO, "revision": aidev.REVISION, "rows": len(rows),
                    "table": "pull_request (popular repositories)"},
        "patterns": {k: v.pattern for k, v in aidev.CLAIMS.items()},
        "min_chars": aidev.MIN_CHARS,
        "provenance": provenance.collect(
            repo_root=Path(__file__).resolve().parents[2], scenarios=[Scenario(name="aidev")],
            corpus_name="aidev-claims", provider="none (no LLM)", configured_model=None,
            response_models=[], argv=sys.argv[1:]),
        "prevalence": prevalence,
    }
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"aidev-claims-{report['generated_at'].replace(':', '-')}.json"
    path.write_text(json.dumps(report, indent=1))
    Path(manifest_out).parent.mkdir(parents=True, exist_ok=True)
    Path(manifest_out).write_text(json.dumps(
        {"dataset": f"{aidev.REPO}@{aidev.REVISION}", "seed": seed, "min_chars": aidev.MIN_CHARS,
         "note": "ids only; descriptions are re-read from the dataset, never redistributed", "sample": sample}, indent=1))
    o = prevalence["overall"]
    print(f"n={o['n']}  too short/empty={o['empty_or_short']}  median chars={o['median_chars']}")
    for k in list(aidev.CLAIMS) + ["says_tests_pass_or_run"]:
        print(f"  {k:28s} {o[k]['hits']:6d} / {o[k]['of']}  = {100 * o[k]['share']:.1f}%")
    print(f"→ wrote {path}\n→ wrote {manifest_out} ({len(sample)} ids)")
    return 0


async def _route_noise(manifest: str, provider: str, model: str | None, out_dir: str, concurrency: int) -> int:
    """Does ordinary real text move the router? Append real agent PR descriptions to the demo
    scenarios that have a diff and count decision changes against the scenario's own routing."""
    import re
    from datetime import datetime, timezone

    from .config import get_settings
    from .eval import aidev, provenance
    from .eval.corpus import AGENTIC_CORPUS, CORPUS
    from .eval.harness import build_run
    from .eval.mutation import wilson  # noqa: F401  (kept for symmetry with other reports)
    from .eval.routing_analysis import cluster_bootstrap
    from .llm.factory import build_client, resolve_target
    from .orchestrator.llm_touchpoints import LLMRouter

    settings = get_settings()
    update = {"llm_provider": provider}
    if model:
        update[_MODEL_FIELD[provider]] = model
    settings = settings.model_copy(update=update)
    client, target = build_client(settings), resolve_target(settings)
    if client is None or target is None:
        print(f"✗ provider {provider!r} has no usable client (missing API key?).", file=sys.stderr)
        return 2
    router = LLMRouter(client)
    label = re.sub(r"[^A-Za-z0-9._-]", "_", f"{target.provider}-{target.model}")

    ids = json.loads(Path(manifest).read_text())["sample"]
    wanted = {d["id"] for d in ids}
    bodies = {r["id"]: r for r in aidev.load_pull_requests(["id", "agent", "body"]) if r["id"] in wanted}
    scenarios = [s for s in CORPUS + AGENTIC_CORPUS if s.diff]  # a scenario with no diff is routed from text alone
    repo_root = Path(__file__).resolve().parents[2]
    sem = asyncio.Semaphore(concurrency)

    async def route(scn, body: str):
        run = build_run(scn, repo_root=repo_root, diff=scn.diff, pr_title=scn.pr_title, pr_body=body)
        async with sem:
            return aidev.routing_signature(await router.route(run))

    base = {s.name: await route(s, s.pr_body) for s in scenarios}
    print(f"→ {label}: {len(scenarios)} scenarios x {len(ids)} real descriptions = {len(scenarios) * len(ids)} routing calls…")
    tasks = [(d, s) for d in ids for s in scenarios]
    sigs = await asyncio.gather(*(route(s, (s.pr_body + "\n\n" + bodies[d["id"]]["body"]).strip()[:20000]) for d, s in tasks))

    rows = [{"description_id": d["id"], "agent": d["agent"], "scenario": s.name,
             "flipped": sig != base[s.name]} for (d, s), sig in zip(tasks, sigs)]
    by_desc: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        by_desc[r["description_id"]].append(float(r["flipped"]))
    errors = client.totals["errors"]
    flips = sum(r["flipped"] for r in rows)
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(), "router": label, "valid": errors == 0,
        "llm_errors": errors,
        "provenance": provenance.collect(
            repo_root=repo_root, scenarios=scenarios, corpus_name="route-noise", provider=label,
            configured_model=target.model, response_models=sorted(client.response_models), argv=sys.argv[1:]
        ) | {"llm_calls": client.totals},
        "summary": {
            "calls": len(rows), "flips": flips, "flip_rate": flips / len(rows),
            "cluster_by_description": cluster_bootstrap(by_desc),
            "by_scenario": {s.name: sum(r["flipped"] for r in rows if r["scenario"] == s.name) for s in scenarios},
            "by_agent": {a: sum(r["flipped"] for r in rows if r["agent"] == a) for a in sorted({r["agent"] for r in rows})},
        },
        "baseline_routing": {k: list(v) if not isinstance(v, str) else v for k, v in base.items()},
        "rows": rows,
    }
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    prefix = "route-noise" if errors == 0 else "INVALID-route-noise"
    path = out / f"{prefix}-{label}-{report['generated_at'].replace(':', '-')}.json"
    path.write_text(json.dumps(report, indent=1, default=list))
    c = report["summary"]["cluster_by_description"]
    print(f"decision changed in {flips}/{len(rows)} = {100 * flips / len(rows):.1f}%  (95% interval resampling descriptions: {c['ci95']})")
    print("llm calls:", client.totals, f"\n→ wrote {path}")
    if errors:
        print(f"✗ {errors} routing calls failed and fell back to the heuristic: NOT a valid result.", file=sys.stderr)
        return 1
    return 0


def _field_sample(out: str) -> int:
    from .eval import aidev, field_study

    print(f"→ sampling from {aidev.REPO}@{aidev.REVISION}: Python repos, closed PRs, "
          f"{field_study.MIN_COMMITS}-{field_study.MAX_COMMITS} commits, seed {field_study.SEED}")
    order = field_study.build_manifest()
    by_agent = {a: sum(1 for d in order if d["agent"] == a) for a in sorted({d["agent"] for d in order})}
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(json.dumps({
        "dataset": f"{aidev.REPO}@{aidev.REVISION}", "seed": field_study.SEED,
        "note": "ids and derived booleans only; no description text or code",
        "attempts": order}, indent=1))
    print(f"→ {len(order)} attempts {by_agent} -> {out}")
    return 0


async def _field_run(repo: str, number: int, n_commits: int, meta_json: str, out_path: str) -> int:
    """INSIDE A CONTAINER ONLY. Run the gate on one real agent PR's head and base commits."""
    import re
    import subprocess
    import tempfile
    import time

    from .eval.corpus import Scenario
    from .eval.harness import run_scenario
    from .orchestrator.stubs import HeuristicClassifier, HeuristicRouter, HeuristicTriage

    import os

    result: dict = {**json.loads(meta_json), "repo": repo, "number": number, "status": "error",
                    "python": sys.version.split()[0], "image_code_sha": os.environ.get("AGENTICQA_GIT_SHA", "unknown")}
    started = time.monotonic()
    tmp = Path(tempfile.mkdtemp(prefix="field-"))

    def git(*args: str, timeout: int = 600) -> subprocess.CompletedProcess:
        return subprocess.run(["git", *args], cwd=tmp, capture_output=True, text=True, timeout=timeout)

    def finish(status: str, **extra) -> int:
        result.update(status=status, wall_seconds=round(time.monotonic() - started, 1), **extra)
        Path(out_path).write_text(json.dumps(result, indent=1))
        print(f"{repo}#{number}: {status}")
        return 0

    async def gate(sha: str) -> dict:
        git("checkout", "-q", "--force", sha)
        scn = Scenario(name=f"field-{number}", source="local", repo_path=str(tmp), expected_repo_kind="plain_service")
        t0 = time.monotonic()
        r = await run_scenario(scn, router=HeuristicRouter(), classifier=HeuristicClassifier(),
                               triage=HeuristicTriage(), repo_root=Path("/"))
        failing = sorted(t.node_id for lr in r.run.layer_runs.values() for t in lr.results
                         if t.outcome.value in ("failed", "error"))
        return {"sha": sha, "state": r.run.state.value, "verdict": r.run.verdict.value if r.run.verdict else None,
                "completed": bool(r.run.verdict) and r.run.totals["total"] > 0, "totals": r.run.totals,
                "failing": failing, "error": r.run.error[:400],
                "layer_states": {l.value: lr.state.value for l, lr in r.run.layer_runs.items()},
                "seconds": round(time.monotonic() - t0, 1)}

    try:
        git("init", "-q")
        git("remote", "add", "origin", f"https://github.com/{repo}.git")
        fetched = git("fetch", "-q", f"--depth={n_commits + 1}", "origin", f"pull/{number}/head")
        if fetched.returncode:
            return finish("fetch_failed", detail=fetched.stderr[-300:])
        head = git("rev-parse", "FETCH_HEAD").stdout.strip()
        base_p = git("rev-parse", f"FETCH_HEAD~{n_commits}")
        base = base_p.stdout.strip() if base_p.returncode == 0 else None
        tree = git("ls-tree", "-r", "--name-only", "FETCH_HEAD").stdout.splitlines()
        if not any(re.search(r"(^|/)(tests?/|test_[^/]*\.py$|[^/]*_test\.py$)", p) for p in tree):
            return finish("no_tests", head=head)
        result["head"] = await gate(head)
        if not result["head"]["completed"]:
            collected = "could not be collected" in result["head"]["error"]
            return finish("collection_failed" if collected else "build_or_run_failed", head_sha=head)
        result["base"] = await gate(base) if base else None
        return finish("ran", head_sha=head, base_sha=base)
    except subprocess.TimeoutExpired as exc:
        return finish("git_timeout", detail=str(exc)[:200])
    except Exception as exc:  # noqa: BLE001 - every failure is a recorded finding, never a silent skip
        return finish("error", detail=f"{type(exc).__name__}: {exc}"[:400])
    finally:
        import shutil

        shutil.rmtree(tmp, ignore_errors=True)


def _field_study(manifest: str, out_dir: str, target: int, jobs: int, wall_limit: int, image: str) -> int:
    """Host driver: one throwaway container per PR, attempts in manifest order until `target` have run."""
    import concurrent.futures as cf
    import subprocess

    attempts = json.loads(Path(manifest).read_text())["attempts"]
    out = Path(out_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)
    out.chmod(0o777)  # the container user is not the host user

    def one(a: dict) -> dict:
        name = f"aqa-field-{a['order']}"
        meta = json.dumps({k: a[k] for k in ("order", "pr_id", "agent", "merged", "says_tests_pass", "n_commits")})
        cmd = ["docker", "run", "--rm", "--name", name, "--cpus", "2", "--memory", "4g", "--pids-limit", "1024",
               "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--read-only",
               "--tmpfs", "/tmp:rw,exec,size=6g", "--tmpfs", "/home/runner:rw,exec,size=2g,uid=1000,gid=1000,mode=0755",
               "-v", f"{out}:/out", image, "field-run", "--repo", a["repo"], "--pr", str(a["number"]),
               "--n-commits", str(a["n_commits"]), "--meta", meta, "--out", f"/out/{a['order']:03d}.json"]
        target_file = out / f"{a['order']:03d}.json"
        stderr_tail = ""
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=wall_limit)
            stderr_tail = (proc.stderr or proc.stdout or "")[-500:]
        except subprocess.TimeoutExpired:
            subprocess.run(["docker", "kill", name], capture_output=True)
            target_file.write_text(json.dumps({**json.loads(meta), "repo": a["repo"], "number": a["number"],
                                               "status": "wall_timeout", "wall_seconds": wall_limit}))
        if not target_file.exists():  # the container died before writing anything
            target_file.write_text(json.dumps({**json.loads(meta), "repo": a["repo"], "number": a["number"],
                                               "status": "container_failed", "detail": stderr_tail}))
        return json.loads(target_file.read_text())

    done: list[dict] = []
    todo = [a for a in attempts if not (out / f"{a['order']:03d}.json").exists()]
    done += [json.loads((out / f"{a['order']:03d}.json").read_text()) for a in attempts
             if (out / f"{a['order']:03d}.json").exists()]
    with cf.ThreadPoolExecutor(max_workers=jobs) as pool:
        pending: set = set()
        while todo or pending:
            ran = sum(1 for r in done if r["status"] == "ran")
            while todo and len(pending) < jobs and ran + len(pending) < target:
                pending.add(pool.submit(one, todo.pop(0)))
            if not pending:
                break
            finished, pending = cf.wait(pending, return_when=cf.FIRST_COMPLETED)
            for f in finished:
                r = f.result()
                done.append(r)
                print(f"[{len(done)} attempted, {sum(1 for x in done if x['status'] == 'ran')} ran] "
                      f"{r['repo']}#{r['number']} -> {r['status']}", flush=True)
    return 0


def _field_summary(results_dir: str, manifest: str, out_dir: str) -> int:
    from datetime import datetime, timezone

    from .eval import field_study, provenance
    from .eval.corpus import Scenario

    results = [json.loads(p.read_text()) for p in sorted(Path(results_dir).glob("*.json"))]
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "provenance": provenance.collect(
            repo_root=Path(__file__).resolve().parents[2], scenarios=[Scenario(name="field")],
            corpus_name="field-study", provider="none (no LLM; heuristic routing)", configured_model=None,
            response_models=[], argv=sys.argv[1:]),
        "summary": field_study.summarize(results), "results": results,
    }
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"field-study-{report['generated_at'].replace(':', '-')}.json"
    path.write_text(json.dumps(report, indent=1))
    print(json.dumps(report["summary"], indent=1)[:3000], f"\n→ wrote {path}")
    return 0


def _write_selection_report(prefix: str, corpus: str, summary: dict, results, out_dir: str) -> int:
    from datetime import datetime, timezone

    from .eval import provenance
    from .eval.corpus import Scenario

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "provenance": provenance.collect(
            repo_root=Path(__file__).resolve().parents[2], scenarios=[Scenario(name=corpus)],
            corpus_name=corpus, provider="none (no LLM)", configured_model=None,
            response_models=[], argv=sys.argv[1:]),
        "summary": summary, "results": results,
    }
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{prefix}-{report['generated_at'].replace(':', '-')}.json"
    path.write_text(json.dumps(report, indent=1))
    print(json.dumps(summary, indent=1)[:2500], f"\n-> wrote {path}")
    return 0


async def _selection_mutants(project: str, n: int, seed: int, out_dir: str) -> int:
    from .eval import selection_exp
    from .eval.corpus import REAL_SUITES

    suites = {s.name.removeprefix("real-"): (s.clone_url, s.head_sha) for s in REAL_SUITES}
    holdout = json.loads((Path(__file__).resolve().parents[2] / "eval/data/holdout-projects.json").read_text())
    for name, h in holdout["projects"].items():
        suites[name] = (f"https://github.com/{h['repo']}.git", h["sha"])
    if project not in suites:
        print(f"unknown project {project!r}; choose from {sorted(suites)}")
        return 2
    url, sha = suites[project]
    res = await selection_exp.mutants_on_project(project, url, sha, n=n, seed=seed)
    return _write_selection_report(f"selection-mutants-{project}", f"selection-mutants:{project}",
                                   selection_exp.summarize_mutants([res]), [res], out_dir)


def _selection_field(manifest: str, jobs: int, limit: int, out_dir: str) -> int:
    import tempfile
    from concurrent.futures import ThreadPoolExecutor

    from .eval import selection_exp

    attempts = json.loads(Path(manifest).read_text())["attempts"][:limit]
    work = Path(tempfile.mkdtemp(prefix="selfield-"))
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        rows = list(pool.map(lambda a: selection_exp.field_static_one(a, work), attempts))
    return _write_selection_report("selection-field", "selection-field", selection_exp.summarize_field(rows), rows, out_dir)


async def _selection_attack(project: str, n: int, seed: int, out_dir: str, only: int | None = None, kinds: str = "") -> int:
    from .eval import selection_exp
    from .eval.corpus import REAL_SUITES

    suites = {s.name.removeprefix("real-"): s for s in REAL_SUITES}
    s = suites[project]
    res = await selection_exp.advisor_attack(project, s.clone_url, s.head_sha, n=n, seed=seed, only=only,
                                             kinds=[k for k in kinds.split(",") if k] or None)
    return _write_selection_report(f"selection-attack-{project}", f"selection-attack:{project}",
                                   selection_exp.summarize_attack([res]), [res], out_dir)


async def _selection_advice(project: str, report: str, provider: str, model: str | None, limit: int, out_dir: str) -> int:
    from .eval import selection_exp
    from .eval.corpus import REAL_SUITES

    suites = {s.name.removeprefix("real-"): s for s in REAL_SUITES}
    s = suites[project]
    rep = json.loads(Path(report).read_text())["results"][0]
    res = await selection_exp.advice_study(project, s.clone_url, s.head_sha, rep, provider=provider, model=model, limit=limit)
    return _write_selection_report(f"selection-advice-{project}", f"selection-advice:{project}",
                                   selection_exp.summarize_advice([res]), [res], out_dir)


async def _selection_replay(project: str, report: str, out_dir: str) -> int:
    from .eval import selection_exp
    from .eval.corpus import REAL_SUITES

    suites = {s.name.removeprefix("real-"): (s.clone_url, s.head_sha) for s in REAL_SUITES}
    holdout = json.loads((Path(__file__).resolve().parents[2] / "eval/data/holdout-projects.json").read_text())
    for name, h in holdout["projects"].items():
        suites[name] = (f"https://github.com/{h['repo']}.git", h["sha"])
    url, sha = suites[project]
    rep = json.loads(Path(report).read_text())["results"][0]
    res = await selection_exp.replay_project(project, url, sha, rep)
    return _write_selection_report(f"selection-replay-{project}", f"selection-replay:{project}",
                                   selection_exp.summarize_mutants([res]), [res], out_dir)


def _selection_swebench(repo: str, cap: int, workdir: str, out_dir: str) -> int:
    from .eval import swebench_exp

    rows = [r for r in swebench_exp.sample_instances(swebench_exp.load_instances(), cap) if r["repo"] == repo]
    work = Path(workdir)
    work.mkdir(parents=True, exist_ok=True)
    results = swebench_exp.run_repo(repo, rows, work)
    return _write_selection_report(f"selection-swebench-{repo.replace('/', '__')}", f"selection-swebench:{repo}",
                                   swebench_exp.summarize(results), results, out_dir)


async def _selection_testmon(project: str, report: str, out_dir: str) -> int:
    from .eval import selection_exp
    from .eval.corpus import REAL_SUITES

    suites = {s.name.removeprefix("real-"): (s.clone_url, s.head_sha) for s in REAL_SUITES}
    holdout = json.loads((Path(__file__).resolve().parents[2] / "eval/data/holdout-projects.json").read_text())
    for name, h in holdout["projects"].items():
        suites[name] = (f"https://github.com/{h['repo']}.git", h["sha"])
    url, sha = suites[project]
    rep = json.loads(Path(report).read_text())["results"][0]
    res = await selection_exp.testmon_project(project, url, sha, rep)
    return _write_selection_report(f"selection-testmon-{project}", f"selection-testmon:{project}",
                                   selection_exp.summarize_testmon([res]), [res], out_dir)


def _weakened_study(out_dir: str) -> int:
    from .eval import weakened_exp

    root = Path(__file__).resolve().parents[2]
    res = weakened_exp.run_all(root / "eval/shadow-v2", root / "eval/data/field-manifest.json")
    rows = res.pop("results")
    return _write_selection_report("weakened-study", "weakened-study", res, rows, out_dir)


def _ablation_rules(out_dir: str) -> int:
    import glob

    from .eval import ablation_rules

    root = Path(__file__).resolve().parents[2]

    def latest(pattern: str) -> list[dict]:
        per: dict[str, str] = {}
        for f in sorted(glob.glob(str(root / "eval/reports" / pattern))):
            per[Path(f).name.split("-2026")[0]] = f
        return [r for f in per.values() for r in json.loads(Path(f).read_text())["results"]]

    shadow = [json.loads(p.read_text()) for p in sorted((root / "eval/shadow-v2").glob("*.json"))]
    wk = sorted(glob.glob(str(root / "eval/reports/weakened-study-*.json")))
    weakened = json.loads(Path(wk[-1]).read_text())["results"] if wk else []
    summary = {"mutants": ablation_rules.mutants(latest("selection-replay-*.json"), latest("selection-testmon-*.json")),
               "swebench": ablation_rules.swebench(latest("selection-swebench-*.json")),
               "shadow": ablation_rules.shadow(shadow, weakened)}
    return _write_selection_report("ablation-rules", "ablation-rules", summary, [], out_dir)


def _selection_demo(out_dir: str) -> int:
    from .eval import selection_exp

    summary = selection_exp.demo_services_static(Path(__file__).resolve().parents[2])
    return _write_selection_report("selection-demo", "selection-demo", summary, [], out_dir)


def _selection_aidev(out_dir: str) -> int:
    from .eval import selection_exp

    summary = selection_exp.aidev_file_kinds()
    return _write_selection_report("selection-aidev", "selection-aidev", summary, [], out_dir)


def _shadow_projects() -> dict[str, str]:
    from .eval.corpus import REAL_SUITES

    root = Path(__file__).resolve().parents[2]
    out = {s.name.removeprefix("real-"): s.clone_url.removeprefix("https://github.com/").removesuffix(".git")
           for s in REAL_SUITES}
    for name, h in json.loads((root / "eval/data/holdout-projects.json").read_text())["projects"].items():
        out[name] = h["repo"]
    return out


def _shadow_manifest(projects: str, per_project: int, out: str, extend: bool = False) -> int:
    from .eval import shadow_exp

    if extend:
        data = shadow_exp.extend_manifest(Path(out), per_project)
        print(f"-> {len(data['attempts'])} pull requests in {out} (batch 2 added)")
        return 0

    known = _shadow_projects()
    names = [p for p in projects.split(",") if p] or sorted(known)
    data = shadow_exp.write_manifest({n: known[n] for n in names}, Path(out), per_project)
    print(f"-> {len(data['attempts'])} pull requests of {len(names)} projects -> {out}")
    return 0


async def _shadow_record(repo: str, pr: int, branch: str, project: str, out_path: str) -> int:
    """INSIDE A CONTAINER ONLY."""
    from .eval import shadow_exp
    from .eval.selection_exp import EXTRA_PIP

    res = await shadow_exp.record_project(repo, branch, pr, EXTRA_PIP.get(project, []))
    res["project"] = project
    Path(out_path).write_text(json.dumps(res))
    print(f"{repo}: record {res['status']}")
    return 0


async def _shadow_pr(repo: str, number: int, branch: str, project: str, out_path: str, map_path: str | None = None) -> int:
    """INSIDE A CONTAINER ONLY."""
    from .eval import shadow_exp
    from .eval.selection_exp import EXTRA_PIP

    res = await shadow_exp.shadow_pr(repo, number, branch, EXTRA_PIP.get(project, []), map_path)
    res["project"] = project
    res["image_code_sha"] = __import__("os").environ.get("AGENTICQA_GIT_SHA", "unknown")
    Path(out_path).write_text(json.dumps(res, indent=1))
    print(f"{repo}#{number}: {res['status']}")
    return 0


def _shadow_study(manifest: str, out_dir: str, jobs: int, wall_limit: int, image: str) -> int:
    """Host driver: one throwaway container per pull request, every attempt recorded."""
    import concurrent.futures as cf
    import subprocess

    attempts = json.loads(Path(manifest).read_text())["attempts"]
    out = Path(out_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)
    out.chmod(0o777)

    def one(a: dict) -> None:
        target = out / f"{a['order']:04d}.json"
        if target.exists():
            return
        name = f"aqa-shadow-{a['order']}"
        cmd = ["docker", "run", "--rm", "--name", name, "--cpus", "2", "--memory", "4g", "--pids-limit", "1024",
               "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--read-only",
               "--tmpfs", "/tmp:rw,exec,size=8g", "--tmpfs", "/home/runner:rw,exec,size=2g,uid=1000,gid=1000,mode=0755",
               "-v", f"{out}:/out", image, "shadow-pr", "--repo", a["repo"], "--pr", str(a["number"]),
               "--branch", a["branch"], "--project", a["project"], "--out", f"/out/{target.name}"]
        tail = ""
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=wall_limit)
            tail = (proc.stderr or proc.stdout or "")[-400:]
        except subprocess.TimeoutExpired:
            subprocess.run(["docker", "kill", name], capture_output=True)
            target.write_text(json.dumps({**a, "status": "wall_timeout"}))
        if not target.exists():
            target.write_text(json.dumps({**a, "status": "container_failed", "detail": tail}))
        print(f"[{a['order']}] {a['repo']}#{a['number']}: {json.loads(target.read_text())['status']}", flush=True)

    with cf.ThreadPoolExecutor(max_workers=jobs) as pool:
        list(pool.map(one, attempts))
    return 0


def _shadow_stale(manifest: str, ran_dir: str, maps_dir: str, out_dir: str, jobs: int, wall_limit: int, image: str) -> int:
    """Host driver for the stale-recording replay: one recording per project, then every pull request that ran."""
    import concurrent.futures as cf
    import subprocess

    attempts = json.loads(Path(manifest).read_text())["attempts"]
    ran = {(r["repo"], r["number"]) for r in (json.loads(p.read_text()) for p in Path(ran_dir).glob("*.json"))
           if r.get("status") == "ran"}
    todo = [a for a in attempts if (a["repo"], a["number"]) in ran]
    maps, out = Path(maps_dir).resolve(), Path(out_dir).resolve()
    for d in (maps, out):
        d.mkdir(parents=True, exist_ok=True)
        d.chmod(0o777)
    base_cmd = ["docker", "run", "--rm", "--cpus", "2", "--memory", "4g", "--pids-limit", "1024", "--cap-drop", "ALL",
                "--security-opt", "no-new-privileges", "--read-only", "--tmpfs", "/tmp:rw,exec,size=8g",
                "--tmpfs", "/home/runner:rw,exec,size=2g,uid=1000,gid=1000,mode=0755"]
    oldest = {}
    for a in todo:  # the recording is made at the merge base of each project's oldest pull request in the sample
        if a["project"] not in oldest or a["number"] < oldest[a["project"]]["number"]:
            oldest[a["project"]] = a

    def record(a: dict) -> None:
        target = maps / f"{a['project']}.json"
        if target.exists():
            return
        cmd = base_cmd + ["-v", f"{maps}:/maps", image, "shadow-record", "--repo", a["repo"], "--pr", str(a["number"]),
                          "--branch", a["branch"], "--project", a["project"], "--out", f"/maps/{target.name}"]
        try:
            subprocess.run(cmd, capture_output=True, text=True, timeout=wall_limit)
        except subprocess.TimeoutExpired:
            pass
        if not target.exists():
            target.write_text(json.dumps({"status": "record_failed", "project": a["project"]}))
        print(f"[record] {a['project']}: {json.loads(target.read_text())['status']}", flush=True)

    def one(a: dict) -> None:
        target = out / f"{a['order']:04d}.json"
        if target.exists():
            return
        name = f"aqa-stale-{a['order']}"
        cmd = base_cmd + ["--name", name, "-v", f"{out}:/out", "-v", f"{maps}:/maps:ro", image, "shadow-pr",
                          "--repo", a["repo"], "--pr", str(a["number"]), "--branch", a["branch"], "--project", a["project"],
                          "--map", f"/maps/{a['project']}.json", "--out", f"/out/{target.name}"]
        try:
            subprocess.run(cmd, capture_output=True, text=True, timeout=wall_limit)
        except subprocess.TimeoutExpired:
            subprocess.run(["docker", "kill", name], capture_output=True)
            target.write_text(json.dumps({**a, "status": "wall_timeout"}))
        if not target.exists():
            target.write_text(json.dumps({**a, "status": "container_failed"}))
        print(f"[{a['order']}] {a['repo']}#{a['number']}: {json.loads(target.read_text())['status']}", flush=True)

    with cf.ThreadPoolExecutor(max_workers=jobs) as pool:
        list(pool.map(record, oldest.values()))
        list(pool.map(one, todo))
    return 0


def _shadow_summary(results_dir: str, out_dir: str) -> int:
    from .eval import shadow_exp

    rows = [json.loads(p.read_text()) for p in sorted(Path(results_dir).glob("*.json"))]
    return _write_selection_report("shadow-study", "shadow-study", shadow_exp.summarize(rows), rows, out_dir)



async def _shadow_compare(log_path: str, out_dir: str) -> int:
    """Join the deployment's shadow log with each repository's own CI result for the same commit."""
    from .config import get_settings
    from .github.app_auth import GitHubAppAuth
    from .github.client import GitHubClient

    settings = get_settings()
    client = GitHubClient(settings.github_token, app_auth=GitHubAppAuth(settings), api_url=settings.github_api_url)
    recs = [json.loads(line) for line in Path(log_path).read_text().splitlines() if line.strip()]
    last: dict = {}
    for r in recs:  # the latest decision per pull request and commit
        last[(r["repo"], r["pr"], r["head_sha"])] = r
    rows = []
    for (repo, pr, sha), r in sorted(last.items()):
        ci = await client.ci_conclusion(repo, sha)
        rows.append({**r, "ci": ci["conclusion"], "ci_checks": ci["checks"]})
    def agree(r):
        return (r["verdict"] == "auto_approve") == (r["ci"] == "success")
    done = [r for r in rows if r["ci"] in ("success", "failure") and r["verdict"]]
    summary = {
        "decisions": len(rows), "with_ci_result": len(done),
        "gate_review_ci_failure": sum(1 for r in done if r["verdict"] == "needs_human_review" and r["ci"] == "failure"),
        "gate_review_ci_success": sum(1 for r in done if r["verdict"] == "needs_human_review" and r["ci"] == "success"),
        "gate_approve_ci_failure": sum(1 for r in done if r["verdict"] == "auto_approve" and r["ci"] == "failure"),
        "gate_approve_ci_success": sum(1 for r in done if r["verdict"] == "auto_approve" and r["ci"] == "success"),
        "agreement": round(sum(1 for r in done if agree(r)) / len(done), 3) if done else None,
        "mean_selected_share": round(sum((r["selection"] or {}).get("selected", 0) / max(1, (r["selection"] or {}).get("total", 1))
                                         for r in rows) / len(rows), 3) if rows else None,
        "sent_to_review_for_unreached_change": sum(1 for r in rows if (r["selection"] or {}).get("uncovered")),
    }
    return _write_selection_report("deployment-shadow", "deployment-shadow", summary, rows, out_dir)



def _field_layouts(results_dir: str, out_dir: str) -> int:
    """For the field-study projects whose tests imported a package we had not installed, how do they
    declare dependencies? Reads each project's CURRENT top-level files (not the pull request's)."""
    import urllib.error
    import urllib.request
    from datetime import datetime, timezone

    from .eval import field_study

    def fetch(repo: str, path: str) -> str | None:
        try:
            with urllib.request.urlopen(f"https://raw.githubusercontent.com/{repo}/HEAD/{path}", timeout=20) as r:
                return r.read().decode("utf-8", "replace")
        except (urllib.error.URLError, TimeoutError):
            return None

    results = [json.loads(p.read_text()) for p in sorted(Path(results_dir).glob("*.json"))]
    repos = sorted({r["repo"] for r in results
                    if field_study.classify_failure(r) == "tests import a package we did not install"})
    rows = [{"repo": repo, "layout": field_study.layout_of(fetch(repo, "pyproject.toml"),
                                                           fetch(repo, "requirements.txt") is not None)}
            for repo in repos]
    tally: dict[str, int] = {}
    for r in rows:
        tally[r["layout"]] = tally.get(r["layout"], 0) + 1
    report = {"generated_at": datetime.now(timezone.utc).isoformat(),
              "method": "Top-level pyproject.toml / requirements.txt on each repository's default branch AT ANALYSIS "
                        "TIME, not at the pull request's commit, so layouts may have changed since.",
              "projects": rows, "tally": tally}
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"field-layouts-{report['generated_at'].replace(':', '-')}.json"
    path.write_text(json.dumps(report, indent=1))
    print(json.dumps(tally, indent=1), f"\n→ wrote {path}")
    return 0


def _serve(host: str | None, port: int | None) -> int:
    """Launch the FastAPI app (single worker). Honors CDSW_APP_PORT on CML."""
    import uvicorn

    from .config import get_settings

    settings = get_settings()
    bind_host = host or settings.host
    bind_port = port or settings.resolved_port()
    print(f"→ Serving Agentic QA on http://{bind_host}:{bind_port}")
    uvicorn.run("agenticqa.main:app", host=bind_host, port=bind_port, workers=1, log_level="info")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agenticqa", description="Agentic QA testing-pyramid runner")
    sub = parser.add_subparsers(dest="command", required=True)

    run_local = sub.add_parser("run-local", help="Run the pyramid against a local repo path")
    run_local.add_argument("--path", required=True, help="Path to the target repo")
    run_local.add_argument("--bug", action="store_true", help="Inject the demo bug (INVENTORY_ORDERS_BUG=1)")

    serve = sub.add_parser("serve", help="Run the web app (dashboard + API)")
    serve.add_argument("--host", default=None, help="Bind host (default from settings)")
    serve.add_argument("--port", type=int, default=None, help="Bind port (default CDSW_APP_PORT or settings)")

    eval_run = sub.add_parser(
        "eval-run", help="Run the benchmark corpus through the pipeline and write a report"
    )
    eval_run.add_argument(
        "--provider", choices=["stub", "configured", "openai", "mistral", "cloudera"], default="stub",
        help="'stub' = free heuristic baseline; 'configured' = whatever AGENTICQA_LLM_PROVIDER is set "
             "to; or name a provider to pin the run to it regardless of .env",
    )
    eval_run.add_argument(
        "--model", default=None,
        help="Override that provider's model for this run (e.g. gpt-4o-mini, mistral-small-latest)",
    )
    eval_run.add_argument("--out", default="eval/reports", help="Directory to write the report into")
    eval_run.add_argument(
        "--corpus", choices=["local", "agentic", "real-pr", "full", "injection"], default="local",
        help="'local' (default) = synthetic demo-repo scenarios only, no network beyond "
             "installing deps; 'agentic' = the Billing Support Assistant bugs; 'real-pr' = "
             "live AgenticProduct PRs only (clones over the network); 'full' = all tiers",
    )
    eval_run.add_argument(
        "--ablation", choices=["none", "llm-verdict"], default="none",
        help="'llm-verdict' also asks the model to decide approve/review itself from the same inputs, "
             "the design this architecture rejects, to compare against the deterministic verdict",
    )
    eval_run.add_argument(
        "--routing", choices=["provider", "oracle"], default="provider",
        help="'oracle' = ground-truth routing from each scenario's label, to measure what the "
             "layers catch independent of router quality",
    )

    adversary = sub.add_parser(
        "adversary-run", help="Worst-case router/classifier experiment: verdict must be invariant"
    )
    adversary.add_argument("--out", default="eval/reports", help="Directory to write the report into")
    adversary.add_argument("--quick", action="store_true", help="A handful of routers only (development)")
    adversary.add_argument("--suite", choices=["demo", "real"], default="demo",
                           help="'real' = open-source suites at pinned commits instead of the demo services")
    adversary.add_argument("--only", default=None, help="Run a single scenario by name")

    mut = sub.add_parser(
        "mutation-run", help="Layer-necessity experiment: which layers kill which mutants of the demo services"
    )
    mut.add_argument("--system", choices=["inventory", "support", "all"], default="all")
    mut.add_argument("--n", type=int, default=None, help="Stratified sample size per system (default: every site)")
    mut.add_argument("--seed", type=int, default=20261014, help="Sampling seed (only used with --n)")
    mut.add_argument("--out", default="eval/reports", help="Directory to write the report into")


    selm = sub.add_parser("selection-mutants", help="Mutants of a real project: full suite vs the tests GATE selects")
    selm.add_argument("--project", required=True)
    selm.add_argument("--n", type=int, default=30)
    selm.add_argument("--seed", type=int, default=20261023)
    selm.add_argument("--out", default="eval/reports")
    self_ = sub.add_parser("selection-field", help="What GATE would select for each real agent PR (static; runs no test)")
    self_.add_argument("--manifest", default="eval/data/field-manifest.json")
    self_.add_argument("--jobs", type=int, default=6)
    self_.add_argument("--limit", type=int, default=1000)
    self_.add_argument("--out", default="eval/reports")
    sela2 = sub.add_parser("selection-attack", help="Hostile test advisors against the pipeline on mutants of a real project")
    sela2.add_argument("--project", required=True)
    sela2.add_argument("--n", type=int, default=6)
    sela2.add_argument("--seed", type=int, default=20261023)
    sela2.add_argument("--out", default="eval/reports")
    sela2.add_argument("--only", type=int, default=None, help="run a single mutant index")
    sela2.add_argument("--kinds", default="", help="comma-separated advisor kinds")
    sadv = sub.add_parser("selection-advice", help="What a real model adds as test advisor, on recorded mutants")
    sadv.add_argument("--project", required=True)
    sadv.add_argument("--report", required=True, help="selection-mutants report of the same project")
    sadv.add_argument("--provider", default="openai")
    sadv.add_argument("--model", default=None)
    sadv.add_argument("--limit", type=int, default=15)
    sadv.add_argument("--out", default="eval/reports")
    srep = sub.add_parser("selection-replay", help="Judge the current selector against recorded mutant outcomes")
    srep.add_argument("--project", required=True)
    srep.add_argument("--report", required=True)
    srep.add_argument("--out", default="eval/reports")
    ssw = sub.add_parser("selection-swebench", help="Real defects: do the exposing tests of SWE-bench Verified get selected?")
    ssw.add_argument("--repo", required=True)
    ssw.add_argument("--cap", type=int, default=20)
    ssw.add_argument("--workdir", default="/tmp/swe")
    ssw.add_argument("--out", default="eval/reports")
    stm = sub.add_parser("selection-testmon", help="pytest-testmon on the same mutants as GATE")
    stm.add_argument("--project", required=True)
    stm.add_argument("--report", required=True)
    stm.add_argument("--out", default="eval/reports")
    swk = sub.add_parser("weakened-study", help="How often real changes remove or weaken tests (diffs only)")
    swk.add_argument("--out", default="eval/reports")
    sab = sub.add_parser("ablation-rules", help="What a plain selection gate (no U or W rule) would approve")
    sab.add_argument("--out", default="eval/reports")
    seld = sub.add_parser("selection-demo", help="Selection on the demo services' mutants (static)")
    seld.add_argument("--out", default="eval/reports")
    sela = sub.add_parser("selection-aidev", help="Kinds of files changed by AIDev pull requests, at scale")
    sela.add_argument("--out", default="eval/reports")

    shm = sub.add_parser("shadow-manifest", help="Fix the pull requests of the shadow replay before running any")
    shm.add_argument("--projects", default="")
    shm.add_argument("--per-project", type=int, default=25)
    shm.add_argument("--out", default="eval/data/shadow-manifest.json")
    shm.add_argument("--extend", action="store_true", help="add batch 2 below the lowest PR number of batch 1")
    shp = sub.add_parser("shadow-pr", help="INSIDE A CONTAINER ONLY: GATE and a full CI run on one pull request")
    shp.add_argument("--repo", required=True)
    shp.add_argument("--pr", type=int, required=True)
    shp.add_argument("--branch", required=True)
    shp.add_argument("--project", required=True)
    shp.add_argument("--out", required=True)
    shp.add_argument("--map", default=None, help="a stored coverage map in place of a fresh recording")
    shr = sub.add_parser("shadow-record", help="INSIDE A CONTAINER ONLY: record one coverage map for a project")
    shr.add_argument("--repo", required=True)
    shr.add_argument("--pr", type=int, required=True)
    shr.add_argument("--branch", required=True)
    shr.add_argument("--project", required=True)
    shr.add_argument("--out", required=True)
    sst = sub.add_parser("shadow-stale", help="Shadow replay with one stored coverage map per project")
    sst.add_argument("--manifest", default="eval/data/shadow-manifest.json")
    sst.add_argument("--ran", default="eval/shadow-v2")
    sst.add_argument("--maps", default="eval/shadow-maps")
    sst.add_argument("--out", default="eval/shadow-stale")
    sst.add_argument("--jobs", type=int, default=3)
    sst.add_argument("--wall-limit", type=int, default=3000)
    sst.add_argument("--image", default="agenticqa-shadow:latest")
    shs = sub.add_parser("shadow-study", help="Run the shadow replay, one throwaway container per pull request")
    shs.add_argument("--manifest", default="eval/data/shadow-manifest.json")
    shs.add_argument("--out", default="eval/shadow")
    shs.add_argument("--jobs", type=int, default=4)
    shs.add_argument("--wall-limit", type=int, default=2400)
    shs.add_argument("--image", default="agenticqa-field:latest")
    shc = sub.add_parser("shadow-compare", help="Deployment: compare the shadow log with each repo's own CI")
    shc.add_argument("--log", default="data/shadow-decisions.jsonl")
    shc.add_argument("--out", default="eval/reports")
    shsum = sub.add_parser("shadow-summary", help="Summarise the shadow replay")
    shsum.add_argument("--results", default="eval/shadow")
    shsum.add_argument("--out", default="eval/reports")

    plot = sub.add_parser("eval-plot", help="Render paper figures + LaTeX number macros from pinned evidence")
    plot.add_argument("--manifest", default="docs/figures/evidence.json")
    plot.add_argument("--out", default="docs/figures")

    rsb = sub.add_parser("route-set-build", help="Sample the pinned real-PR routing manifest from public git history")
    rsb.add_argument("--workdir", default="/tmp/aqa-routing-mirrors", help="Where blobless mirrors are cached")
    rsb.add_argument("--out", default="eval/data/routing-set.json")

    rev = sub.add_parser("route-eval", help="Score a router's repo-kind decisions on the real-PR routing set (no tests run)")
    rev.add_argument("--manifest", default="eval/data/routing-set.json")
    rev.add_argument("--workdir", default="/tmp/aqa-routing-mirrors")
    rev.add_argument("--provider", choices=["stub", "manifest", "manifest+diff", "configured", "openai", "mistral", "cloudera"],
                     default="stub")
    rev.add_argument("--model", default=None)
    rev.add_argument("--mask-repo", action="store_true", help="Hide the repo name from the router")
    rev.add_argument("--out", default="eval/reports")
    rev.add_argument("--concurrency", type=int, default=4)

    aid = sub.add_parser("aidev-claims", help="Count test/readiness claims in real agent PR descriptions (AIDev)")
    aid.add_argument("--out", default="eval/reports")
    aid.add_argument("--sample", type=int, default=60, help="descriptions to pin for route-noise")
    aid.add_argument("--seed", type=int, default=20261015)
    aid.add_argument("--manifest-out", default="eval/data/aidev-descriptions.json")

    rn = sub.add_parser("route-noise", help="Does ordinary real PR text move the router? (no tests run)")
    rn.add_argument("--manifest", default="eval/data/aidev-descriptions.json")
    rn.add_argument("--provider", choices=["openai", "mistral", "cloudera"], required=True)
    rn.add_argument("--model", default=None)
    rn.add_argument("--out", default="eval/reports")
    rn.add_argument("--concurrency", type=int, default=2)

    fsm = sub.add_parser("field-sample", help="Sample real agent PRs for the field study (ids only)")
    fsm.add_argument("--out", default="eval/data/field-manifest.json")
    fr = sub.add_parser("field-run", help="INSIDE A CONTAINER: run the gate on one real agent PR")
    fr.add_argument("--repo", required=True)
    fr.add_argument("--pr", type=int, required=True)
    fr.add_argument("--n-commits", type=int, required=True)
    fr.add_argument("--meta", required=True)
    fr.add_argument("--out", required=True)
    fs = sub.add_parser("field-study", help="Host driver: one throwaway container per sampled PR")
    fs.add_argument("--manifest", default="eval/data/field-manifest.json")
    fs.add_argument("--out", default=str(Path.home() / "aqa-field-results"),
                    help="must be under $HOME: Colima shares only the home directory with its VM")
    fs.add_argument("--target", type=int, default=30, help="stop once this many PRs have run")
    fs.add_argument("--jobs", type=int, default=2)
    fs.add_argument("--wall-limit", type=int, default=2400, help="seconds per container")
    fs.add_argument("--image", default="agenticqa-field")
    fl = sub.add_parser("field-layouts", help="How do the field-study projects with missing packages declare dependencies?")
    fl.add_argument("--results", default="eval/field")
    fl.add_argument("--out", default="eval/reports")
    fsu = sub.add_parser("field-summary", help="Summarise field-study result files")
    fsu.add_argument("--results", default=str(Path.home() / "aqa-field-results"))
    fsu.add_argument("--manifest", default="eval/data/field-manifest.json")
    fsu.add_argument("--out", default="eval/reports")

    llm_check = sub.add_parser("llm-check", help="Smoke-test an LLM provider/model before an eval run")
    llm_check.add_argument(
        "--provider", choices=["configured", "openai", "mistral", "cloudera"], default="configured"
    )
    llm_check.add_argument("--model", default=None, help="Override that provider's model")

    args = parser.parse_args(argv)
    if args.command == "field-sample":
        return _field_sample(args.out)
    if args.command == "field-run":
        return asyncio.run(_field_run(args.repo, args.pr, args.n_commits, args.meta, args.out))
    if args.command == "field-study":
        return _field_study(args.manifest, args.out, args.target, args.jobs, args.wall_limit, args.image)
    if args.command == "field-layouts":
        return _field_layouts(args.results, args.out)
    if args.command == "field-summary":
        return _field_summary(args.results, args.manifest, args.out)
    if args.command == "aidev-claims":
        return _aidev_claims(args.out, args.sample, args.seed, args.manifest_out)
    if args.command == "route-noise":
        return asyncio.run(_route_noise(args.manifest, args.provider, args.model, args.out, args.concurrency))
    if args.command == "route-set-build":
        return _route_set_build(args.workdir, args.out)
    if args.command == "route-eval":
        return asyncio.run(_route_eval(args.manifest, args.workdir, args.provider, args.model,
                                       args.mask_repo, args.out, args.concurrency))
    if args.command == "selection-mutants":
        return asyncio.run(_selection_mutants(args.project, args.n, args.seed, args.out))
    if args.command == "selection-field":
        return _selection_field(args.manifest, args.jobs, args.limit, args.out)
    if args.command == "selection-attack":
        return asyncio.run(_selection_attack(args.project, args.n, args.seed, args.out, args.only, args.kinds))
    if args.command == "selection-advice":
        return asyncio.run(_selection_advice(args.project, args.report, args.provider, args.model, args.limit, args.out))
    if args.command == "selection-replay":
        return asyncio.run(_selection_replay(args.project, args.report, args.out))
    if args.command == "selection-swebench":
        return _selection_swebench(args.repo, args.cap, args.workdir, args.out)
    if args.command == "selection-testmon":
        return asyncio.run(_selection_testmon(args.project, args.report, args.out))
    if args.command == "weakened-study":
        return _weakened_study(args.out)
    if args.command == "ablation-rules":
        return _ablation_rules(args.out)
    if args.command == "selection-demo":
        return _selection_demo(args.out)
    if args.command == "selection-aidev":
        return _selection_aidev(args.out)
    if args.command == "shadow-manifest":
        return _shadow_manifest(args.projects, args.per_project, args.out, args.extend)
    if args.command == "shadow-pr":
        return asyncio.run(_shadow_pr(args.repo, args.pr, args.branch, args.project, args.out, args.map))
    if args.command == "shadow-record":
        return asyncio.run(_shadow_record(args.repo, args.pr, args.branch, args.project, args.out))
    if args.command == "shadow-stale":
        return _shadow_stale(args.manifest, args.ran, args.maps, args.out, args.jobs, args.wall_limit, args.image)
    if args.command == "shadow-study":
        return _shadow_study(args.manifest, args.out, args.jobs, args.wall_limit, args.image)
    if args.command == "shadow-compare":
        return asyncio.run(_shadow_compare(args.log, args.out))
    if args.command == "shadow-summary":
        return _shadow_summary(args.results, args.out)
    if args.command == "eval-plot":
        return _eval_plot(args.manifest, args.out)
    if args.command == "mutation-run":
        systems = ["inventory", "support"] if args.system == "all" else [args.system]
        return asyncio.run(_mutation_run(systems, args.n, args.seed, args.out))
    if args.command == "adversary-run":
        return asyncio.run(_adversary_run(args.out, args.quick, args.suite, args.only))
    if args.command == "llm-check":
        return asyncio.run(_llm_check(args.provider, args.model))
    if args.command == "run-local":
        return asyncio.run(_run_local(args.path, args.bug))
    if args.command == "serve":
        return _serve(args.host, args.port)
    if args.command == "eval-run":
        return asyncio.run(_eval_run(args.provider, args.out, args.corpus, args.model, args.routing, args.ablation))
    parser.error(f"unknown command {args.command!r}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
