"""Selection inside the pipeline: what runs, and how 'no test reaches this change' reaches the verdict."""

from __future__ import annotations

from types import SimpleNamespace

from agenticqa.events.bus import EventBus
from agenticqa.models.enums import LayerId, LayerRunState, RunState, Verdict
from agenticqa.models.pyramid import DiscoveredTest
from agenticqa.models.run import LayerRun, QARun, SelectionInfo
from agenticqa.orchestrator.pipeline import Pipeline
from agenticqa.orchestrator.stubs import HeuristicAdvisor
from agenticqa.selection import RepoGraph, static_tests
from agenticqa.store.db import RunRepository


class _FakeTriage:
    async def triage(self, run: QARun) -> str:
        return "narrative"


def _pipeline(**kw) -> Pipeline:
    repo = RunRepository()
    return Pipeline(repo=repo, bus=EventBus(repo), executor=None, router=None, classifier=None,
                    triage=_FakeTriage(), **kw)


def _diff(path: str) -> str:
    return f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n@@ -1 +1 @@\n-x\n+y\n"


def _project(tmp_path):
    files = {
        "src/pkg/__init__.py": "",
        "src/pkg/a.py": "def f():\n    return 1\n",
        "src/pkg/b.py": "def g():\n    return 2\n",
        "src/pkg/orphan.py": "def z():\n    return 3\n",
        "tests/test_a.py": "from pkg.a import f\n\ndef test_f():\n    assert f() == 1\n",
        "tests/test_b.py": "from pkg.b import g\n\ndef test_g():\n    assert g() == 2\n",
    }
    for rel, body in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    return SimpleNamespace(root=tmp_path), static_tests(RepoGraph(tmp_path))


async def test_change_mode_runs_only_the_selected_tests(tmp_path):
    ws, tests = _project(tmp_path)
    run = QARun(id="r", diff=_diff("src/pkg/a.py"))
    chosen = await _pipeline(selection_mode="change")._select(run, ws, tests)
    assert [t.node_id for t in chosen] == ["tests/test_a.py::test_f"]
    assert run.selection.mode == "change" and run.selection.total == 2 and run.selection.selected == 1


async def test_full_mode_and_missing_diff_run_everything(tmp_path):
    ws, tests = _project(tmp_path)
    off = QARun(id="r", diff=_diff("src/pkg/a.py"))
    assert len(await _pipeline(selection_mode="full")._select(off, ws, tests)) == 2
    nodiff = QARun(id="r2", diff="")
    assert len(await _pipeline(selection_mode="change")._select(nodiff, ws, tests)) == 2
    assert nodiff.selection.mode == "full"


async def test_advice_adds_tests_and_a_failing_or_hostile_advisor_cannot_remove_any(tmp_path):
    ws, tests = _project(tmp_path)

    class Greedy:
        async def advise(self, run, tests, core):
            return [t.node_id for t in tests]

    class Empty:
        async def advise(self, run, tests, core):
            return []

    class Boom:
        async def advise(self, run, tests, core):
            raise RuntimeError("model down")

    for advisor, expected in ((Greedy(), 2), (Empty(), 1), (Boom(), 1)):
        run = QARun(id="r", diff=_diff("src/pkg/a.py"))
        chosen = await _pipeline(selection_mode="change", advisor=advisor)._select(run, ws, tests)
        assert len(chosen) == expected
        assert "tests/test_a.py::test_f" in {t.node_id for t in chosen}


async def test_heuristic_advisor_adds_tests_named_after_the_changed_module(tmp_path):
    ws, tests = _project(tmp_path)
    run = QARun(id="r", diff=_diff("src/pkg/orphan.py"))
    run.selection = SelectionInfo(changed=["src/pkg/orphan.py"])
    extra = DiscoveredTest(node_id="tests/test_orphan_cli.py::test_cli", file="tests/test_orphan_cli.py", name="test_cli")
    assert await HeuristicAdvisor().advise(run, tests + [extra], []) == ["tests/test_orphan_cli.py::test_cli"]


async def test_a_change_no_test_reaches_is_not_auto_approved():
    run = QARun(id="r", layer_runs={LayerId.UNIT: LayerRun(layer=LayerId.UNIT, state=LayerRunState.PASSED,
                                                           planned_count=1, passed=1)})
    run.selection = SelectionInfo(mode="change", total=2, selected=1, uncovered=["src/pkg/orphan.py"])
    await _pipeline()._triage_and_gate(run)
    assert run.verdict == Verdict.NEEDS_HUMAN_REVIEW and run.state == RunState.AWAITING_APPROVAL
    assert "src/pkg/orphan.py" in run.triage_report and "no test" in run.verdict_reason


async def test_covered_change_with_passing_tests_is_auto_approved():
    run = QARun(id="r", layer_runs={LayerId.UNIT: LayerRun(layer=LayerId.UNIT, state=LayerRunState.PASSED,
                                                           planned_count=1, passed=1)})
    run.selection = SelectionInfo(mode="change", total=2, selected=1, uncovered=[])
    await _pipeline()._triage_and_gate(run)
    assert run.verdict == Verdict.AUTO_APPROVE


async def test_full_run_ignores_the_coverage_rule():
    run = QARun(id="r", layer_runs={LayerId.UNIT: LayerRun(layer=LayerId.UNIT, state=LayerRunState.PASSED,
                                                           planned_count=1, passed=1)})
    run.selection = SelectionInfo(mode="full", total=2, selected=2, uncovered=["x.py"])
    await _pipeline()._triage_and_gate(run)
    assert run.verdict == Verdict.AUTO_APPROVE


async def test_a_change_that_removes_tests_is_not_auto_approved():
    run = QARun(id="r", layer_runs={LayerId.UNIT: LayerRun(layer=LayerId.UNIT, state=LayerRunState.PASSED,
                                                           planned_count=1, passed=1)})
    run.selection = SelectionInfo(mode="change", total=2, selected=1, weakened=["tests/test_a.py: test test_x removed"])
    await _pipeline()._triage_and_gate(run)
    assert run.verdict == Verdict.NEEDS_HUMAN_REVIEW and "removed or weakened" in run.verdict_reason


async def test_a_project_without_tests_is_never_approved_on_a_source_or_config_change(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "a.py").write_text("def f():\n    return 1\n")
    ws = SimpleNamespace(root=tmp_path)
    for mode, path in (("change", "pkg/a.py"), ("change", "pyproject.toml"), ("full", "pkg/a.py")):
        run = QARun(id="r", diff=_diff(path))
        assert await _pipeline(selection_mode=mode)._select(run, ws, []) == []
        assert run.selection.uncovered == [path], (mode, path)
        await _pipeline()._triage_and_gate(run)
        assert run.verdict == Verdict.NEEDS_HUMAN_REVIEW, (mode, path)
    docs = QARun(id="d", diff=_diff("README.md"))
    await _pipeline(selection_mode="change")._select(docs, ws, [])
    assert docs.selection.uncovered == []


async def test_a_stored_coverage_recording_drives_the_core_and_a_bad_one_falls_back(tmp_path):
    import json

    ws, tests = _project(tmp_path)
    rec = {"tests": ["tests/test_b.py::test_g"], "files": ["src/pkg/a.py"],
           "bodies": [["src/pkg/a.py", None, "f", [0]]], "outside": []}
    good = tmp_path / "map.json"
    good.write_text(json.dumps(rec))
    run = QARun(id="r", diff="diff --git a/src/pkg/a.py b/src/pkg/a.py\n--- a/src/pkg/a.py\n+++ b/src/pkg/a.py\n"
                              "@@ -1,2 +1,2 @@\n def f():\n-    return 1\n+    return 9\n")
    chosen = await _pipeline(selection_mode="change", coverage_map_path=str(good))._select(run, ws, tests)
    assert [t.node_id for t in chosen] == ["tests/test_b.py::test_g"]  # the recording, not the imports
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    run2 = QARun(id="r2", diff=run.diff)
    chosen = await _pipeline(selection_mode="change", coverage_map_path=str(bad))._select(run2, ws, tests)
    assert [t.node_id for t in chosen] == ["tests/test_a.py::test_f"]  # static core
