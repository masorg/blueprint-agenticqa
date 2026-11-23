"""Dependency discovery for real projects, and venv-cache keying."""

from __future__ import annotations

from agenticqa.execution.deps import project_requirements
from agenticqa.execution.subprocess_venv import SubprocessVenvExecutor

PYPROJECT = '''\
[project]
name = "My_Pkg"
dependencies = ["colorama; platform_system == 'Windows'", "typing-extensions>=4"]
[project.optional-dependencies]
tests = ["pytest>=8", "my-pkg[extras]"]
docs = ["sphinx"]
[dependency-groups]
dev = [{include-group = "tests"}, "ruff"]
tests = ["hypothesis", "freezegun"]
'''


def _write(tmp_path, text, name="pyproject.toml"):
    (tmp_path / name).write_text(text)
    return tmp_path


def test_core_deps_test_extras_and_included_groups_are_collected_but_docs_are_not(tmp_path):
    reqs = project_requirements(_write(tmp_path, PYPROJECT))
    assert "typing-extensions>=4" in reqs and "pytest>=8" in reqs
    assert {"hypothesis", "freezegun", "ruff"} <= set(reqs)       # group include resolved
    assert "sphinx" not in reqs                                    # docs extra is not test tooling


def test_requirements_naming_the_project_itself_are_dropped(tmp_path):
    reqs = project_requirements(_write(tmp_path, PYPROJECT))
    assert not any(r.lower().replace("_", "-").startswith("my-pkg") for r in reqs)


def test_missing_or_broken_pyproject_yields_nothing(tmp_path):
    assert project_requirements(tmp_path) == []
    assert project_requirements(_write(tmp_path, "this is [not toml")) == []


def test_circular_dependency_groups_do_not_loop(tmp_path):
    toml = '[dependency-groups]\ntests = [{include-group = "dev"}, "a"]\ndev = [{include-group = "tests"}, "b"]\n'
    assert set(project_requirements(_write(tmp_path, toml))) == {"a", "b"}


def test_projects_without_requirements_txt_no_longer_share_a_venv(tmp_path):
    """REGRESSION (found 2026-10-14): the venv cache was keyed on requirements.txt alone, so
    every project without one hashed identically and shared a single venv."""
    ex = SubprocessVenvExecutor(base_dir=tmp_path / "ws")
    a, b, a2 = tmp_path / "a", tmp_path / "b", tmp_path / "a2"
    for d, dep in ((a, "click"), (b, "attrs"), (a2, "click")):
        d.mkdir()
        _write(d, f'[project]\nname = "x"\ndependencies = ["{dep}"]\n')
    key = lambda d: ex._cache_key(d, None, [])  # noqa: E731
    assert key(a) != key(b)
    assert key(a) == key(a2)
